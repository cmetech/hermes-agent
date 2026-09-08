"""Installed workflow marketplace provenance contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from plugins.workflow.marketplace.models import (
    InstalledPackageIdentity,
    InstalledPackageProvenance,
)
from plugins.workflow.marketplace.package import (
    WorkflowMarketplaceError,
    load_distribution,
)
from plugins.workflow.marketplace.provenance import (
    InstalledPackageStore,
    direct_source_key,
)


FIXTURE_PACKAGES = (
    Path(__file__).parent / "fixtures" / "marketplace" / "repository" / "packages"
)


def _installed(
    home: Path,
    *,
    source_key: str = "company",
    source_name: str = "company",
    package_id: str = "laptop-support",
) -> tuple[Path, InstalledPackageProvenance]:
    identity = InstalledPackageIdentity(
        sourceKey=source_key,
        packageId=package_id,
    )
    store = InstalledPackageStore(home)
    destination = store.package_root(identity)
    destination.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_PACKAGES / "laptop-support", destination)
    distribution = load_distribution(destination)
    provenance = InstalledPackageProvenance.model_validate({
        "schemaVersion": 1,
        "identity": identity.model_dump(mode="json", by_alias=True),
        "sourceName": source_name,
        "repositoryUrl": "https://github.com/example/workflows.git",
        "configuredRef": "release",
        "resolvedCommit": "a" * 40,
        "packagePath": "packages/laptop-support",
        "packageVersion": distribution.manifest.version,
        "contractVersion": 1,
        "distributionDigest": distribution.digest,
        "installedAt": "2026-09-03T12:00:00Z",
        "actor": "operator",
        "workflowPaths": [
            member.definition for member in distribution.manifest.workflows
        ],
    })
    store.put(provenance)
    return destination, provenance


def test_two_sources_with_same_package_id_have_distinct_installed_identity(
    tmp_path: Path,
) -> None:
    first_root, first = _installed(tmp_path, source_key="company")
    second_root, second = _installed(
        tmp_path,
        source_key="mirror",
        source_name="mirror",
    )
    store = InstalledPackageStore(tmp_path)

    assert first_root != second_root
    assert store.get(first.identity) == first
    assert store.get(second.identity) == second
    assert store.list_installed() == (first, second)


def test_direct_source_key_is_stable_normalized_and_credential_free() -> None:
    browser = direct_source_key("https://github.com/acme/workflows/tree/main/catalog")
    canonical = direct_source_key("https://github.com/acme/workflows.git#catalog")

    assert browser == canonical
    assert browser.startswith("direct-")
    assert len(browser) <= 128
    with pytest.raises(WorkflowMarketplaceError) as error:
        direct_source_key("https://alice:secret@github.com/acme/workflows.git")
    assert error.value.code == "source_credentials_forbidden"
    assert "secret" not in str(error.value)
    assert "alice" not in str(error.value)


def test_provenance_is_versioned_private_and_records_exact_destination(
    tmp_path: Path,
) -> None:
    destination, provenance = _installed(tmp_path)
    store = InstalledPackageStore(tmp_path)

    raw = json.loads(store.path.read_bytes())
    assert raw["schemaVersion"] == 1
    assert raw["packages"][0]["destination"] == str(destination.resolve())
    assert raw["packages"][0]["provenance"] == provenance.model_dump(
        mode="json", by_alias=True
    )
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":2,"packages":[]}\n',
        b'{"schemaVersion":1,"packages":[],"unknown":true}\n',
        b'{"schemaVersion":1,"packages":[],"packages":[]}\n',
        b'{"schemaVersion":1,"packages":NaN}\n',
    ],
)
def test_corrupt_provenance_fails_closed_without_rewrite(
    tmp_path: Path,
    payload: bytes,
) -> None:
    store = InstalledPackageStore(tmp_path)
    store.root.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        store.root.parent.chmod(0o700)
        store.root.chmod(0o700)
    store.path.write_bytes(payload)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.list_installed()

    assert error.value.code == "provenance_state_invalid"
    assert store.path.read_bytes() == payload


def test_oversized_provenance_fails_closed_without_rewrite(tmp_path: Path) -> None:
    store = InstalledPackageStore(tmp_path)
    store.max_state_bytes = 32
    store.root.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        store.root.parent.chmod(0o700)
        store.root.chmod(0o700)
    payload = b"x" * 33
    store.path.write_bytes(payload)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.list_installed()

    assert error.value.code == "provenance_state_size_limit"
    assert store.path.read_bytes() == payload


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_read_only_provenance_fails_closed_without_rewrite(tmp_path: Path) -> None:
    _root, _provenance = _installed(tmp_path)
    store = InstalledPackageStore(tmp_path)
    before = store.path.read_bytes()
    store.path.chmod(0o400)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.put(store.list_installed()[0])

    assert error.value.code == "provenance_state_write_failed"
    assert store.path.read_bytes() == before
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o400


def test_symlinked_provenance_is_rejected_without_touching_target(
    tmp_path: Path,
) -> None:
    store = InstalledPackageStore(tmp_path)
    store.root.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        store.root.parent.chmod(0o700)
        store.root.chmod(0o700)
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    try:
        store.path.symlink_to(target)
    except OSError:
        pytest.skip("host does not permit file symlinks")

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.list_installed()

    assert error.value.code == "provenance_state_invalid"
    assert target.read_text(encoding="utf-8") == "outside"


def test_only_exact_untampered_installed_provenance_creates_binding(
    tmp_path: Path,
) -> None:
    root, provenance = _installed(tmp_path)
    store = InstalledPackageStore(tmp_path)
    relative_path = provenance.workflow_paths[0]

    binding = store.binding_for_workflow(root, relative_path)

    assert binding is not None
    assert binding.installation_key == "company/laptop-support"
    assert binding.source_name == "company"
    assert binding.package_id == "laptop-support"
    assert binding.distribution_digest == provenance.distribution_digest
    assert store.binding_for_workflow(root.parent, relative_path) is None
    assert store.binding_for_workflow(root, "workflows/undeclared.yaml") is None

    (root / "fixtures" / "sample.json").write_bytes(b"tampered")
    assert store.binding_for_workflow(root, relative_path) is None


def test_binding_rejects_provenance_whose_destination_was_tampered(
    tmp_path: Path,
) -> None:
    root, provenance = _installed(tmp_path)
    store = InstalledPackageStore(tmp_path)
    state = json.loads(store.path.read_bytes())
    state["packages"][0]["destination"] = str(root.parent / "other")
    store.path.write_text(json.dumps(state), encoding="utf-8")

    assert store.binding_for_workflow(root, provenance.workflow_paths[0]) is None
