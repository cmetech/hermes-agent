"""Profile-local workflow marketplace source state contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.source_store import WorkflowSourceStore


def test_source_store_never_persists_credentials(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)

    with pytest.raises(WorkflowMarketplaceError, match="source_credentials_forbidden"):
        store.add(
            "private",
            "https://alice:secret@example.test/team/repo.git?token=also-secret",
        )

    assert not store.path.exists()


def test_source_names_and_repository_identity_are_canonicalized(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)

    source = store.add(
        " Company_Workflows ",
        "https://github.com/example/workflows/tree/main/catalog",
        ref="release",
    )

    assert source.name == "company_workflows"
    assert source.repository_url == ("https://github.com/example/workflows.git#catalog")
    assert source.ref == "release"
    assert store.get("COMPANY_WORKFLOWS") == source
    persisted = store.path.read_text(encoding="utf-8")
    assert "tree/main" not in persisted
    assert "secret" not in persisted


def test_source_names_are_unique_after_canonicalization(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("Company", "file:///tmp/company.git")

    with pytest.raises(WorkflowMarketplaceError, match="source_already_exists"):
        store.add(" company ", "file:///tmp/mirror.git")

    assert [source.name for source in store.list_sources()] == ["company"]


def test_source_store_is_profile_local_versioned_and_private(tmp_path: Path) -> None:
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first = WorkflowSourceStore(first_home)
    second = WorkflowSourceStore(second_home)

    first.add("company", "file:///tmp/company.git", ref="main")

    assert second.list_sources() == ()
    raw = json.loads(first.path.read_bytes())
    assert raw == {
        "schemaVersion": 1,
        "sources": [
            {
                "enabled": True,
                "name": "company",
                "ref": "main",
                "repositoryUrl": "file:///tmp/company.git",
            }
        ],
    }
    if os.name != "nt":
        assert first.root.stat().st_mode & 0o777 == 0o700
        assert first.root.parent.stat().st_mode & 0o777 == 0o700
        assert first.path.stat().st_mode & 0o777 == 0o600
        assert first.lock_path.stat().st_mode & 0o777 == 0o600


def test_source_store_supports_disable_remove_without_touching_other_sources(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("alpha", "file:///tmp/alpha.git")
    store.add("beta", "file:///tmp/beta.git")

    disabled = store.set_enabled("alpha", False)
    removed = store.remove("beta")

    assert disabled.enabled is False
    assert removed.name == "beta"
    assert store.list_sources() == (disabled,)
    with pytest.raises(WorkflowMarketplaceError, match="source_not_found"):
        store.get("beta")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":1,"sources":[],"unknown":true}\n',
        b'{"schemaVersion":2,"sources":[]}\n',
        b'{"schemaVersion":1,"sources":[],"sources":[]}\n',
        b'{"schemaVersion":1,"sources":NaN}\n',
    ],
)
def test_malformed_source_state_fails_closed_without_rewrite(
    tmp_path: Path,
    payload: bytes,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(payload)
    before = store.path.read_bytes()

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.read_bytes() == before


def test_oversized_source_state_is_rejected_before_json_parsing(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    payload = b"{" + b" " * (store.max_source_state_bytes + 1)
    store.path.write_bytes(payload)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_size_limit"):
        store.list_sources()

    assert store.path.read_bytes() == payload


def test_symlinked_state_root_is_rejected_without_writing_the_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "home"
    marketplace = home / "marketplace"
    marketplace.mkdir(parents=True)
    try:
        (marketplace / "workflows").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")
    store = WorkflowSourceStore(home)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.add("company", "file:///tmp/company.git")

    assert list(outside.iterdir()) == []


def test_remove_does_not_change_sources_when_catalog_state_is_corrupt(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("company", "file:///tmp/company.git")
    before = store.path.read_bytes()
    store.catalog_path.write_bytes(b'{"schemaVersion":1,"verified":"bad"}\n')

    with pytest.raises(WorkflowMarketplaceError, match="catalog_state_invalid"):
        store.remove("company")

    assert store.path.read_bytes() == before


def test_broken_source_state_symlink_is_corruption_not_empty_state(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    try:
        store.path.symlink_to(tmp_path / "missing-state.json")
    except OSError:
        pytest.skip("host does not permit file symlinks")

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.is_symlink()


def test_tampered_source_state_cannot_introduce_ssh_password(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    payload = {
        "schemaVersion": 1,
        "sources": [
            {
                "enabled": True,
                "name": "company",
                "ref": None,
                "repositoryUrl": (
                    "ssh://git:persisted-secret@example.test/team/repo.git"
                ),
            }
        ],
    }
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
    store.path.write_bytes(encoded)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.read_bytes() == encoded
