"""Manifest-aware workflow discovery behavior."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
import yaml

from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.discovery import clear_discovery_cache, discover_workflows
from plugins.workflow.marketplace.discovery import enumerate_workflow_candidates
from plugins.workflow.marketplace.models import (
    InstalledPackageIdentity,
    InstalledPackageProvenance,
)
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.provenance import InstalledPackageStore
from plugins.workflow.models import (
    WorkflowMarketplaceBinding,
    WorkflowValidationError,
)
from plugins.workflow.schema import parse_workflow_source_bytes


FIXTURE_PACKAGES = (
    Path(__file__).parent / "fixtures" / "marketplace" / "repository" / "packages"
)
_DIGEST_DOMAIN = b"hermes.workflow-package.v1\0"


def _workflow_bytes(name: str) -> bytes:
    return yaml.safe_dump(
        {
            "name": name,
            "description": f"{name} workflow",
            "nodes": [{"id": "start", "bash": "true"}],
        },
        sort_keys=False,
    ).encode("utf-8")


def _publish(root: Path) -> str:
    files = [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.relative_to(root).as_posix() != "digests.json"
    ]
    digest = hashlib.sha256(_DIGEST_DOMAIN)
    records: list[dict[str, object]] = []
    for relative_path, content in sorted(files):
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        file_digest = hashlib.sha256(content)
        digest.update(file_digest.digest())
        records.append({
            "path": relative_path,
            "sha256": file_digest.hexdigest(),
            "size": len(content),
        })
    package_digest = digest.hexdigest()
    (root / "digests.json").write_text(
        json.dumps(
            {
                "algorithm": "sha256",
                "contractVersion": 1,
                "files": records,
                "packageDigest": package_digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return package_digest


def _install_package(
    destination: Path,
    fixture_name: str,
    workflow_names: tuple[str, ...],
) -> tuple[Path, str]:
    root = shutil.copytree(FIXTURE_PACKAGES / fixture_name, destination)
    manifest = json.loads((root / "workflow-package.json").read_text(encoding="utf-8"))
    members = manifest["workflows"]
    assert len(members) == len(workflow_names)
    for member, name in zip(members, workflow_names, strict=True):
        (root / member["definition"]).write_bytes(_workflow_bytes(name))
        if "companion" in member:
            (root / member["companion"]).write_text(
                "language_compatibility: archon-2026-07\n",
                encoding="utf-8",
            )
    return root, _publish(root)


def _binding(
    *,
    installation_key: str,
    source_name: str,
    package_id: str,
    package_version: str,
    workflow_relative_path: str,
    distribution_digest: str,
) -> WorkflowMarketplaceBinding:
    return WorkflowMarketplaceBinding(
        installation_key=installation_key,
        source_name=source_name,
        package_id=package_id,
        package_version=package_version,
        workflow_relative_path=workflow_relative_path,
        distribution_digest=distribution_digest,
    )


def test_package_discovers_only_manifest_definition_yaml(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    package_root, _digest = _install_package(
        profile / "workflows" / "marketplace" / "company" / "support",
        "laptop-support",
        ("laptop-diagnostic", "collect-support-bundle"),
    )
    (package_root / "mcp" / "server.yaml").parent.mkdir(exist_ok=True)
    (package_root / "mcp" / "server.yaml").write_text(
        "command: server\n", encoding="utf-8"
    )
    (package_root / "fixtures" / "bad.yaml").write_text(
        "not: a workflow\n", encoding="utf-8"
    )
    (package_root / "workflows" / "undeclared.yaml").write_bytes(
        _workflow_bytes("undeclared")
    )
    _publish(package_root)

    packages = discover_workflows(tmp_path / "repo", profile, tmp_path / "home")

    assert [item.definition.name for item in packages] == [
        "collect-support-bundle",
        "laptop-diagnostic",
    ]
    assert {item.root for item in packages} == {package_root.resolve()}


def test_explicit_package_root_discovers_declared_members_only(tmp_path: Path) -> None:
    package_root, _digest = _install_package(
        tmp_path / "external-package",
        "inbox-productivity",
        ("inbox-triage",),
    )
    (package_root / "fixtures" / "ignored.yaml").parent.mkdir(exist_ok=True)
    (package_root / "fixtures" / "ignored.yaml").write_text(
        "not: a workflow\n", encoding="utf-8"
    )
    _publish(package_root)

    packages = discover_workflows(
        tmp_path / "repo",
        tmp_path / "profile",
        tmp_path / "home",
        explicit_path=package_root,
    )

    assert [item.definition.name for item in packages] == ["inbox-triage"]
    assert packages[0].source == "explicit"
    assert packages[0].root == package_root.resolve()


def test_explicit_package_with_dangling_manifest_link_fails_closed(
    tmp_path: Path,
) -> None:
    package_root, _digest = _install_package(
        tmp_path / "external-package",
        "inbox-productivity",
        ("must-not-leak",),
    )
    manifest_path = package_root / "workflow-package.json"
    manifest_path.unlink()
    manifest_path.symlink_to(tmp_path / "missing-manifest.json")

    with pytest.raises(WorkflowMarketplaceError) as error:
        discover_workflows(
            tmp_path / "repo",
            tmp_path / "profile",
            tmp_path / "home",
            explicit_path=package_root,
        )

    assert error.value.code == "package_symlink_unsupported"


def test_sibling_packages_and_loose_workflows_are_all_discovered(
    tmp_path: Path, workflow_writer
) -> None:
    profile = tmp_path / "profile"
    package_parent = profile / "workflows" / "marketplace" / "company"
    _install_package(
        package_parent / "support",
        "laptop-support",
        ("laptop-diagnostic", "collect-support-bundle"),
    )
    _install_package(
        package_parent / "inbox",
        "inbox-productivity",
        ("inbox-triage",),
    )
    workflow_writer(profile / "workflows" / "loose", name="ordinary-loose")

    packages = discover_workflows(tmp_path / "repo", profile, tmp_path / "home")

    assert [item.definition.name for item in packages] == [
        "collect-support-bundle",
        "inbox-triage",
        "laptop-diagnostic",
        "ordinary-loose",
    ]
    loose = next(item for item in packages if item.definition.name == "ordinary-loose")
    assert loose.marketplace_binding is None


def test_package_definition_duplicate_names_keep_existing_error_semantics(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    _install_package(
        profile / "workflows" / "marketplace" / "company" / "support",
        "laptop-support",
        ("duplicate", "duplicate"),
    )

    with pytest.raises(WorkflowValidationError) as error:
        discover_workflows(tmp_path / "repo", profile, tmp_path / "home")

    assert error.value.issues[0].code == "duplicate_workflow_name"


def test_invalid_package_manifest_fails_closed_instead_of_exposing_inner_yaml(
    tmp_path: Path,
) -> None:
    root, _digest = _install_package(
        tmp_path / "packages" / "invalid",
        "inbox-productivity",
        ("must-not-leak",),
    )
    (root / "workflow-package.json").write_text("{", encoding="utf-8")

    with pytest.raises(WorkflowMarketplaceError) as error:
        enumerate_workflow_candidates(tmp_path / "packages")

    assert error.value.code == "package_manifest_invalid"


def test_case_variant_package_marker_fails_closed_instead_of_exposing_inner_yaml(
    tmp_path: Path,
) -> None:
    root, _digest = _install_package(
        tmp_path / "packages" / "invalid",
        "inbox-productivity",
        ("must-not-leak",),
    )
    manifest_path = root / "workflow-package.json"
    temporary_path = root / "manifest-temporary.json"
    manifest_path.rename(temporary_path)
    temporary_path.rename(root / "Workflow-Package.Json")
    _publish(root)

    with pytest.raises(WorkflowMarketplaceError) as error:
        enumerate_workflow_candidates(tmp_path / "packages")

    assert error.value.code == "package_manifest_invalid"


def test_explicit_case_variant_package_marker_fails_closed_on_case_sensitive_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _digest = _install_package(
        tmp_path / "invalid",
        "inbox-productivity",
        ("must-not-leak",),
    )
    manifest_path = root / "workflow-package.json"
    temporary_path = root / "manifest-temporary.json"
    manifest_path.rename(temporary_path)
    temporary_path.rename(root / "Workflow-Package.Json")
    _publish(root)
    monkeypatch.setattr(os.path, "lexists", lambda _path: False)

    with pytest.raises(WorkflowMarketplaceError) as error:
        discover_workflows(
            tmp_path / "repo",
            tmp_path / "profile",
            tmp_path / "home",
            explicit_path=root,
        )

    assert error.value.code == "package_manifest_invalid"


def test_nested_package_roots_fail_closed(tmp_path: Path) -> None:
    outer, _digest = _install_package(
        tmp_path / "packages" / "outer",
        "laptop-support",
        ("outer-one", "outer-two"),
    )
    _install_package(
        outer / "nested" / "inner",
        "inbox-productivity",
        ("inner",),
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        enumerate_workflow_candidates(tmp_path / "packages")

    assert error.value.code == "package_root_nested"


def test_binding_flows_through_candidate_source_compilation_and_package_root(
    tmp_path: Path,
) -> None:
    package_root, digest = _install_package(
        tmp_path / "packages" / "inbox",
        "inbox-productivity",
        ("inbox-triage",),
    )
    manifest_path = package_root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_definition = package_root / manifest["workflows"][0]["definition"]
    new_relative = "definitions/triage.yml"
    new_definition = package_root / new_relative
    new_definition.parent.mkdir()
    old_definition.rename(new_definition)
    manifest["workflows"][0]["definition"] = new_relative
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    digest = _publish(package_root)
    expected = _binding(
        installation_key="company/inbox-productivity",
        source_name="company",
        package_id="inbox-productivity",
        package_version="2.0.0",
        workflow_relative_path=new_relative,
        distribution_digest=digest,
    )
    calls: list[tuple[Path, str]] = []

    def resolve(root: Path, relative_path: str) -> WorkflowMarketplaceBinding:
        calls.append((root, relative_path))
        return expected

    candidates = enumerate_workflow_candidates(
        tmp_path / "packages", binding_resolver=resolve
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.marketplace_binding == expected
    assert calls == [(package_root.resolve(), new_relative)]
    assert candidate.definition_bytes is not None
    source = parse_workflow_source_bytes(
        candidate.workflow_path,
        workflow_bytes=candidate.definition_bytes,
        sidecar_bytes=candidate.sidecar_bytes,
        source="profile",
        precedence=2,
        package_root=candidate.package_root,
        sidecar_path=candidate.sidecar_path,
        marketplace_binding=candidate.marketplace_binding,
    )
    assert source.root == package_root.resolve()
    assert source.definition_location == new_relative
    assert source.marketplace_binding == expected
    compiled = compile_workflow(source, WorkflowCatalogSnapshot.capture((source,)))
    assert compiled.package.marketplace_binding == expected


def test_discovery_cache_never_reuses_a_package_across_marketplace_bindings(
    tmp_path: Path,
) -> None:
    clear_discovery_cache()
    profile = tmp_path / "profile"
    package_root, digest = _install_package(
        profile / "workflows" / "marketplace" / "company" / "inbox",
        "inbox-productivity",
        ("inbox-triage",),
    )
    first_binding = _binding(
        installation_key="company/inbox-productivity",
        source_name="company",
        package_id="inbox-productivity",
        package_version="2.0.0",
        workflow_relative_path="workflows/triage.yaml",
        distribution_digest=digest,
    )
    second_binding = _binding(
        installation_key="mirror/inbox-productivity",
        source_name="mirror",
        package_id="inbox-productivity",
        package_version="2.0.0",
        workflow_relative_path="workflows/triage.yaml",
        distribution_digest=digest,
    )

    first = discover_workflows(
        tmp_path / "repo",
        profile,
        tmp_path / "home",
        binding_resolver=lambda root, path: first_binding,
    )[0]
    repeated = discover_workflows(
        tmp_path / "repo",
        profile,
        tmp_path / "home",
        binding_resolver=lambda root, path: first_binding,
    )[0]
    second = discover_workflows(
        tmp_path / "repo",
        profile,
        tmp_path / "home",
        binding_resolver=lambda root, path: second_binding,
    )[0]

    assert first.root == package_root.resolve()
    assert repeated is first
    assert second is not first
    assert first.marketplace_binding == first_binding
    assert second.marketplace_binding == second_binding
    clear_discovery_cache()


def test_profile_discovery_uses_exact_installed_provenance_by_default(
    tmp_path: Path,
) -> None:
    clear_discovery_cache()
    profile = tmp_path / "profile"
    package_root, digest = _install_package(
        profile / "workflows" / "marketplace" / "company" / "inbox-productivity",
        "inbox-productivity",
        ("profile-marketplace",),
    )
    project_root, _project_digest = _install_package(
        tmp_path / "repo" / ".hermes" / "workflows" / "project-package",
        "inbox-productivity",
        ("project-package",),
    )
    identity = InstalledPackageIdentity(
        sourceKey="company",
        packageId="inbox-productivity",
    )
    provenance = InstalledPackageProvenance.model_validate({
        "schemaVersion": 1,
        "identity": identity.model_dump(mode="json", by_alias=True),
        "sourceName": "company",
        "repositoryUrl": "https://github.com/example/workflows.git",
        "configuredRef": "main",
        "resolvedCommit": "a" * 40,
        "packagePath": "packages/inbox-productivity",
        "packageVersion": "2.0.0",
        "contractVersion": 1,
        "distributionDigest": digest,
        "installedAt": "2026-09-03T12:00:00Z",
        "actor": "operator",
        "workflowPaths": ["workflows/triage.yaml"],
    })
    store = InstalledPackageStore(profile)
    store.put(provenance)

    discovered = discover_workflows(tmp_path / "repo", profile, tmp_path / "home")
    by_name = {item.definition.name: item for item in discovered}

    assert by_name["profile-marketplace"].root == package_root.resolve()
    assert by_name["profile-marketplace"].marketplace_binding is not None
    assert (
        by_name["profile-marketplace"].marketplace_binding.installation_key
        == "company/inbox-productivity"
    )
    assert by_name["project-package"].root == project_root.resolve()
    assert by_name["project-package"].marketplace_binding is None

    store.put(provenance.model_copy(update={"distribution_digest": "0" * 64}))
    clear_discovery_cache()
    tampered = discover_workflows(tmp_path / "repo", profile, tmp_path / "home")
    assert (
        next(
            item for item in tampered if item.definition.name == "profile-marketplace"
        ).marketplace_binding
        is None
    )
    clear_discovery_cache()
