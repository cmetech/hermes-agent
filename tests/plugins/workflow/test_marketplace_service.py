"""Review-first workflow marketplace lifecycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse

import pytest
from pydantic import ValidationError
import yaml

from plugins.workflow.marketplace.models import (
    InstallRequest,
    InstalledPackageIdentity,
    PackageInspection,
    WorkflowMarketplaceSource,
)
from plugins.workflow.marketplace.package import (
    WorkflowMarketplaceError,
    load_repository_index,
)
from plugins.workflow.marketplace.service import WorkflowMarketplaceService
from plugins.workflow.marketplace.source_store import (
    SourceRefreshStatus,
    redact_source_refresh_message,
)
from plugins.workflow.trust import WorkflowTrustError


_DIGEST_DOMAIN = b"hermes.workflow-package.v1\0"
_DIAGNOSTIC_CORPUS = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures/workflow-marketplace-source-diagnostics.json"
    ).read_text()
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


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
        path_bytes = relative_path.encode()
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        content_digest = hashlib.sha256(content)
        digest.update(content_digest.digest())
        records.append({
            "path": relative_path,
            "sha256": content_digest.hexdigest(),
            "size": len(content),
        })
    package_digest = digest.hexdigest()
    (root / "digests.json").write_bytes(
        _json_bytes({
            "algorithm": "sha256",
            "contractVersion": 1,
            "files": records,
            "packageDigest": package_digest,
        })
    )
    return package_digest


def _write_package(
    repository: Path,
    package_id: str,
    *,
    version: str,
    workflow_names: tuple[str, ...] = ("diagnostic", "repair"),
    marker: str = "v1",
    requirements: dict[str, list[str]] | None = None,
) -> str:
    root = repository / "packages" / package_id
    if root.exists():
        shutil.rmtree(root)
    (root / "workflows").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "commands").mkdir()
    workflows = []
    for index, name in enumerate(workflow_names):
        definition = f"workflows/{name}.yaml"
        node = (
            {"id": "execute", "bash": f"printf {marker!r}"}
            if index == 0
            else {"id": "approve", "approval": {"message": "Proceed?"}}
        )
        (root / definition).write_bytes(
            yaml.safe_dump(
                {
                    "name": name,
                    "description": f"{name} workflow",
                    "nodes": [node],
                },
                sort_keys=False,
            ).encode()
        )
        workflows.append({"definition": definition})
    # These files are reviewable resources; package validation must never run them.
    (root / "scripts" / "never-run.py").write_text(
        "raise RuntimeError('package content executed during review')\n",
        encoding="utf-8",
    )
    (root / "commands" / "guide.md").write_text(
        f"# Package command {marker}\n", encoding="utf-8"
    )
    external = requirements or {
        "runtimes": ["uv"],
        "tools": ["git"],
        "providers": [],
        "services": [],
        "secrets": ["SUPPORT_TOKEN"],
    }
    manifest = {
        "schemaVersion": 1,
        "id": package_id,
        "version": version,
        "displayName": package_id.replace("-", " ").title(),
        "description": f"{package_id} package",
        "license": "MIT",
        "publisher": "example-company",
        "tags": ["support"],
        "workflows": workflows,
        "externalRequirements": external,
    }
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    return _publish(root)


def _write_index(repository: Path) -> None:
    entries = []
    for root in sorted((repository / "packages").iterdir()):
        manifest = json.loads((root / "workflow-package.json").read_bytes())
        digests = json.loads((root / "digests.json").read_bytes())
        entries.append({
            "contractVersion": 1,
            "description": manifest["description"],
            "displayName": manifest["displayName"],
            "id": manifest["id"],
            "license": manifest["license"],
            "packageDigest": digests["packageDigest"],
            "packagePath": root.relative_to(repository).as_posix(),
            "publisher": manifest["publisher"],
            "tags": manifest["tags"],
            "version": manifest["version"],
        })
    path = repository / ".well-known" / "hermes-workflows" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes({"schemaVersion": 1, "packages": entries}))


def _write_inspection_resources(repository: Path) -> None:
    root = repository / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    for member in manifest["workflows"]:
        definition = Path(member["definition"])
        companion = definition.with_name(f"{definition.stem}.hermes.yaml")
        member["companion"] = companion.as_posix()
        (root / companion).write_text(
            "language_compatibility: archon-2026-07\n", encoding="utf-8"
        )
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    (root / "workflows" / "diagnostic.yaml").write_bytes(
        yaml.safe_dump(
            {
                "name": "diagnostic",
                "description": "Command workflow",
                "nodes": [{"id": "diagnose", "command": "guide"}],
            },
            sort_keys=False,
        ).encode()
    )
    (root / "workflows" / "repair.yaml").write_bytes(
        yaml.safe_dump(
            {
                "name": "repair",
                "description": "Script and MCP workflow",
                "nodes": [
                    {
                        "id": "repair",
                        "script": "never-run.py",
                        "runtime": "uv",
                        "mcp": "mixed.yaml",
                    }
                ],
            },
            sort_keys=False,
        ).encode()
    )
    (root / "mcp").mkdir(exist_ok=True)
    (root / "mcp" / "mixed.yaml").write_text(
        "mcp_servers:\n"
        "  local_support:\n"
        "    command: python\n"
        "    args: [scripts/mcp-helper.py, scripts/a-mcp-helper.py, "
        "scripts/mcp-helper.py]\n"
        "  remote_support:\n"
        "    url: https://mcp.example.test/events\n"
        "    transport: sse\n",
        encoding="utf-8",
    )
    (root / "scripts" / "mcp-helper.py").write_text(
        "raise RuntimeError('MCP support content executed during review')\n",
        encoding="utf-8",
    )
    (root / "scripts" / "a-mcp-helper.py").write_text(
        "raise RuntimeError('MCP support content executed during review')\n",
        encoding="utf-8",
    )
    _publish(root)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip().lower()


class PublishedRepository:
    def __init__(self, work: Path, remote: Path):
        self.work = work
        self.remote = remote

    def publish(self, message: str) -> str:
        _write_index(self.work)
        _git(self.work, "add", ".")
        _git(self.work, "commit", "-m", message)
        _git(self.work, "push", "origin", "main")
        return _git(self.work, "rev-parse", "head")


@pytest.fixture
def published_repo(tmp_path: Path) -> PublishedRepository:
    work = tmp_path / "publisher"
    work.mkdir()
    _write_package(work, "laptop-support", version="1.0.0")
    _write_index(work)
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "marketplace@example.test")
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "publish v1")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(work), str(remote))
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(work, "remote", "add", "origin", str(remote))
    return PublishedRepository(work, remote)


@pytest.fixture
def service(tmp_path: Path) -> WorkflowMarketplaceService:
    return WorkflowMarketplaceService(
        tmp_path / "hermes-home",
        profile="support",
        available_runtimes=frozenset(),
        available_tools=frozenset(),
        available_secrets=frozenset(),
    )


def _add_and_refresh(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    assert service.refresh_source("company").state == "fresh"


def _install(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
):
    _add_and_refresh(service, published_repo)
    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )
    return service.confirm_install(review.confirmation_token, actor="alice")


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_registered_install_refetches_exact_bytes_and_stays_untrusted(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    _add_and_refresh(service, published_repo)
    cached_commit = service.search("")[0].resolved_commit

    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )
    stale = review.workflow_reviews[0]
    service.trust_store.trust_origin(
        stale.package_digest,
        actor="stale-install",
        risk_digest=stale.risk_digest,
        origin="marketplace:company/laptop-support",
    )
    installed = service.confirm_install(review.confirmation_token, actor="alice")

    assert review.resolved_commit == cached_commit
    assert review.operation == "install"
    assert not review.assessment.blockers
    assert {item.code for item in review.assessment.advisories} >= {
        "missing_runtime",
        "missing_tool",
        "missing_secret",
    }
    assert installed.version == "1.0.0"
    assert service.workflow_trust(installed.identity) == {
        "diagnostic": "untrusted",
        "repair": "untrusted",
    }
    assert not (service.home / "executed").exists()


def test_registered_install_uses_fresh_repository_bytes_not_cached_listing(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    _add_and_refresh(service, published_repo)
    assert service.search("")[0].version == "1.0.0"
    _write_package(published_repo.work, "laptop-support", version="2.0.0")
    current_commit = published_repo.publish("publish v2 after catalog refresh")

    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )

    assert review.candidate_version == "2.0.0"
    assert review.resolved_commit == current_commit


def test_inspect_refetches_complete_verified_detail_without_executing_content(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    _write_inspection_resources(published_repo.work)
    published_repo.publish("publish typed inspection resources")
    _add_and_refresh(service, published_repo)
    cached = service.catalog.inspect("company/laptop-support")
    root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    manifest["version"] = "2.0.0"
    manifest["displayName"] = "Laptop Support Exact"
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    candidate_digest = _publish(root)
    current_commit = published_repo.publish("publish exact inspection candidate")

    detail = service.inspect("company/laptop-support")

    assert isinstance(detail, PackageInspection)
    assert detail.identifier == "company/laptop-support"
    assert detail.identity == InstalledPackageIdentity(
        sourceKey="company", packageId="laptop-support"
    )
    assert detail.source_name == "company"
    assert detail.repository_url == published_repo.remote.as_uri()
    assert detail.configured_ref is None
    assert detail.resolved_commit == current_commit
    assert detail.resolved_commit != cached.resolved_commit
    assert detail.verified_at != cached.verified_at
    assert detail.verified is True
    assert detail.source_state == "fresh"
    assert detail.version == "2.0.0"
    assert detail.display_name == "Laptop Support Exact"
    assert detail.description == "laptop-support package"
    assert detail.license == "MIT"
    assert detail.publisher == "example-company"
    assert detail.tags == ["support"]
    assert detail.contract_version == 1
    assert detail.package_path == "packages/laptop-support"
    assert detail.package_digest == candidate_digest
    assert [item.workflow_name for item in detail.workflows] == [
        "diagnostic",
        "repair",
    ]
    resources = {item.path: item.types for item in detail.resources}
    assert resources["commands/guide.md"] == ["command"]
    assert resources["scripts/never-run.py"] == ["script"]
    assert resources["mcp/mixed.yaml"] == ["mcp"]
    assert resources["scripts/a-mcp-helper.py"] == ["mcp_resource"]
    assert resources["scripts/mcp-helper.py"] == ["mcp_resource"]
    assert resources["workflows/diagnostic.yaml"] == ["workflow_definition"]
    assert resources["workflow-package.json"] == ["other"]
    assert detail.external_requirements.runtimes == ["uv"]
    assert {item.code for item in detail.advisories} >= {
        "missing_runtime",
        "missing_tool",
        "missing_secret",
    }
    assert detail.install_status == "not_installed"
    assert detail.update_status == "not_applicable"
    assert detail.installed is None
    repair = next(item for item in detail.workflows if item.workflow_name == "repair")
    assert repair.mcp_resource_files == [
        "scripts/a-mcp-helper.py",
        "scripts/mcp-helper.py",
    ]
    assert not (service.home / "executed").exists()


def test_inspect_pairs_exact_candidate_with_current_verification_event(
    tmp_path: Path,
    published_repo: PublishedRepository,
) -> None:
    cached_at = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    inspected_at = datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc)
    clock_values = iter((cached_at, inspected_at))
    exact_service = WorkflowMarketplaceService(
        tmp_path / "exact-inspection-home",
        profile="support",
        clock=lambda: next(clock_values),
    )
    _add_and_refresh(exact_service, published_repo)
    cached = exact_service.catalog.inspect("company/laptop-support")

    detail = exact_service.inspect("company/laptop-support")

    assert cached.verified_at == "2026-09-03T10:00:00Z"
    assert detail.verified_at == "2026-09-04T15:30:00Z"
    assert detail.source_state == "fresh"
    assert detail.verified is True
    assert detail.resolved_commit == _git(published_repo.work, "rev-parse", "head")


@pytest.mark.parametrize(
    ("field", "stale_value", "expected_code"),
    [
        ("id", "different-package", "package_index_invalid"),
        ("version", "9.0.0", "package_index_invalid"),
        ("displayName", "Stale Display Name", "package_index_invalid"),
        (
            "description",
            "https://alice:index-secret@example.test/private.git?token=hidden",
            "package_index_invalid",
        ),
        ("license", "Apache-2.0", "package_index_invalid"),
        ("publisher", "different-publisher", "package_index_invalid"),
        ("tags", ["different-tag"], "package_index_invalid"),
        ("contractVersion", 2, "package_contract_unsupported"),
    ],
)
def test_registered_inspect_and_install_reject_every_stale_index_projection(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    field: str,
    stale_value: object,
    expected_code: str,
) -> None:
    _add_and_refresh(service, published_repo)
    index_path = published_repo.work / ".well-known/hermes-workflows/index.json"
    index = json.loads(index_path.read_bytes())
    if field == "id":
        package_root = published_repo.work / "packages/laptop-support"
        manifest_path = package_root / "workflow-package.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["id"] = stale_value
        manifest_path.write_bytes(_json_bytes(manifest))
        index["packages"][0]["packageDigest"] = _publish(package_root)
    else:
        index["packages"][0][field] = stale_value
    index_path.write_bytes(_json_bytes(index))
    _git(published_repo.work, "add", ".")
    _git(published_repo.work, "commit", "-m", f"publish stale {field}")
    _git(published_repo.work, "push", "origin", "main")

    for operation in (
        lambda: service.inspect("company/laptop-support"),
        lambda: service.prepare_install(
            InstallRequest(identifier="company/laptop-support"), actor="alice"
        ),
    ):
        with pytest.raises(WorkflowMarketplaceError) as error:
            operation()
        assert error.value.code == expected_code
        rendered = str(error.value)
        assert "index-secret" not in rendered
        assert "alice:" not in rendered
        assert "hidden" not in rendered
        assert "verified" not in rendered.casefold()


@pytest.mark.parametrize(
    ("unrelated_failure", "repository_error"),
    [
        ("missing", "package_index_invalid"),
        ("malformed", "package_manifest_invalid"),
    ],
)
def test_registered_selected_operations_ignore_unrelated_invalid_package(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    unrelated_failure: str,
    repository_error: str,
) -> None:
    _add_and_refresh(service, published_repo)
    unrelated = published_repo.work / "packages" / "unrelated"
    _write_package(published_repo.work, "unrelated", version="1.0.0")
    _write_index(published_repo.work)
    if unrelated_failure == "missing":
        shutil.rmtree(unrelated)
    else:
        (unrelated / "workflow-package.json").write_bytes(b"{malformed")
    _git(published_repo.work, "add", "-A")
    _git(
        published_repo.work,
        "commit",
        "-m",
        f"publish unrelated {unrelated_failure} package",
    )
    _git(published_repo.work, "push", "origin", "main")

    with pytest.raises(WorkflowMarketplaceError) as full_repository_error:
        load_repository_index(published_repo.work)
    assert full_repository_error.value.code == repository_error

    detail = service.inspect("company/laptop-support")
    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )

    assert detail.identifier == "company/laptop-support"
    assert detail.resolved_commit == _git(published_repo.work, "rev-parse", "head")
    assert review.identity == InstalledPackageIdentity(
        sourceKey="company", packageId="laptop-support"
    )
    assert review.resolved_commit == detail.resolved_commit


@pytest.mark.parametrize("catalog_state", ["absent", "failed"])
def test_registered_selected_operations_do_not_require_verified_catalog_cache(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    catalog_state: str,
) -> None:
    unrelated = published_repo.work / "packages" / "unrelated"
    _write_package(published_repo.work, "unrelated", version="1.0.0")
    _write_index(published_repo.work)
    shutil.rmtree(unrelated)
    _git(published_repo.work, "add", "-A")
    _git(published_repo.work, "commit", "-m", "publish unrelated missing package")
    _git(published_repo.work, "push", "origin", "main")
    service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    if catalog_state == "failed":
        refresh = service.refresh_source("company")
        assert refresh.state == "malformed"
        assert refresh.package_count == 0
    assert service.search("") == ()

    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )
    detail = service.inspect(" COMPANY /laptop-support")

    assert review.identity == InstalledPackageIdentity(
        sourceKey="company", packageId="laptop-support"
    )
    assert detail.identifier == "company/laptop-support"
    assert detail.resolved_commit == _git(published_repo.work, "rev-parse", "head")
    assert detail.package_digest == review.candidate_digest
    assert service.search("") == ()


def test_registered_selected_operations_use_fresh_index_when_cache_omits_package(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    _write_package(published_repo.work, "unrelated", version="1.0.0")
    _write_index(published_repo.work)
    index_path = published_repo.work / ".well-known/hermes-workflows/index.json"
    cached_index = json.loads(index_path.read_bytes())
    cached_index["packages"] = [
        item for item in cached_index["packages"] if item["id"] == "unrelated"
    ]
    index_path.write_bytes(_json_bytes(cached_index))
    _git(published_repo.work, "add", ".")
    _git(published_repo.work, "commit", "-m", "publish cache without selected")
    _git(published_repo.work, "push", "origin", "main")
    _add_and_refresh(service, published_repo)
    assert service.search("laptop") == ()

    _write_index(published_repo.work)
    current_commit = published_repo.publish("publish selected package")

    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )
    detail = service.inspect("company/laptop-support")

    assert review.resolved_commit == current_commit
    assert detail.resolved_commit == current_commit
    assert detail.version == "1.0.0"
    assert service.search("laptop") == ()


def test_inspect_uses_source_store_failures_without_catalog_cache(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    with pytest.raises(WorkflowMarketplaceError) as missing:
        service.inspect("missing/laptop-support")
    assert missing.value.code == "source_not_found"

    service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    service.set_source_enabled("company", False)
    with pytest.raises(WorkflowMarketplaceError) as disabled:
        service.inspect("company/laptop-support")
    assert disabled.value.code == "source_disabled"

    service.remove_source("company")
    with pytest.raises(WorkflowMarketplaceError) as removed:
        service.inspect("company/laptop-support")
    assert removed.value.code == "source_not_found"


@pytest.mark.parametrize("identifier", ["company", "company/pkg/extra"])
def test_inspect_rejects_invalid_registered_identifier_without_fetch(
    service: WorkflowMarketplaceService,
    identifier: str,
) -> None:
    with pytest.raises(WorkflowMarketplaceError) as error:
        service.inspect(identifier)

    assert error.value.code == "catalog_identifier_invalid"


@pytest.mark.parametrize(
    ("selected_failure", "expected_code"),
    [
        ("missing", "package_manifest_invalid"),
        ("malformed", "package_manifest_invalid"),
    ],
)
def test_registered_selected_operations_still_reject_invalid_selected_package(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    selected_failure: str,
    expected_code: str,
) -> None:
    _add_and_refresh(service, published_repo)
    selected = published_repo.work / "packages" / "laptop-support"
    _write_index(published_repo.work)
    if selected_failure == "missing":
        shutil.rmtree(selected)
    else:
        (selected / "workflow-package.json").write_bytes(b"{malformed")
    _git(published_repo.work, "add", "-A")
    _git(
        published_repo.work,
        "commit",
        "-m",
        f"publish selected {selected_failure} package",
    )
    _git(published_repo.work, "push", "origin", "main")

    for operation in (
        lambda: service.inspect("company/laptop-support"),
        lambda: service.prepare_install(
            InstallRequest(identifier="company/laptop-support"), actor="alice"
        ),
    ):
        with pytest.raises(WorkflowMarketplaceError) as error:
            operation()
        assert error.value.code == expected_code


def test_inspect_reports_current_then_update_available_without_mutation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    destination = service.installed_store.package_root(installed.identity)
    before_package = _snapshot(destination)
    before_provenance = service.installed_store.get(installed.identity)

    current = service.inspect("company/laptop-support")

    assert current.install_status == "installed"
    assert current.update_status == "current"
    assert current.installed == installed
    _write_package(published_repo.work, "laptop-support", version="2.0.0")
    published_repo.publish("publish inspect update")

    available = service.inspect("company/laptop-support")

    assert available.install_status == "installed"
    assert available.update_status == "update_available"
    assert available.version == "2.0.0"
    assert available.installed == installed
    assert _snapshot(destination) == before_package
    assert service.installed_store.get(installed.identity) == before_provenance


def test_inspect_rejects_invalid_exact_candidate_without_mutation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    destination = service.installed_store.package_root(installed.identity)
    before_package = _snapshot(destination)
    before_provenance = service.installed_store.get(installed.identity)
    root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    manifest["version"] = "2.0.0"
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    (root / "workflows" / "diagnostic.yaml").write_text(
        "name: diagnostic\nnodes: [not-a-node]\n", encoding="utf-8"
    )
    _publish(root)
    published_repo.publish("publish invalid inspect candidate")

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.inspect("company/laptop-support")

    assert error.value.code == "package_workflow_invalid"
    assert _snapshot(destination) == before_package
    assert service.installed_store.get(installed.identity) == before_provenance


def test_inspection_is_strict_bounded_and_sanitizes_candidate_metadata(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    manifest["description"] = (
        "See https://alice:supersecret@example.test/private.git?token=hidden"
    )
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    _publish(root)
    published_repo.publish("publish sensitive metadata")
    _add_and_refresh(service, published_repo)
    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"),
        actor="password actor-secret",
    )
    service.confirm_install(review.confirmation_token, actor="password actor-secret")

    detail = service.inspect("company/laptop-support")
    rendered = detail.model_dump_json()

    assert "supersecret" not in rendered
    assert "alice:" not in rendered
    assert "hidden" not in rendered
    assert "actor-secret" not in rendered
    payload = detail.model_dump(mode="json", by_alias=True)
    payload["resources"] = [payload["resources"][0]] * 513
    with pytest.raises(ValidationError):
        PackageInspection.model_validate(payload)
    payload = detail.model_dump(mode="json", by_alias=True)
    payload["workflows"][0]["mcpResourceFiles"] = ["scripts/helper.py"] * 513
    with pytest.raises(ValidationError):
        PackageInspection.model_validate(payload)
    payload = detail.model_dump(mode="json", by_alias=True)
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        PackageInspection.model_validate(payload)


def test_registered_update_rejects_manifest_identity_mismatch_without_mutation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    destination = service.installed_store.package_root(installed.identity)
    before_bytes = _snapshot(destination)
    before_provenance = service.installed_store.get(installed.identity)
    package_root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((package_root / "workflow-package.json").read_bytes())
    manifest["id"] = "different-package"
    manifest["version"] = "2.0.0"
    (package_root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    candidate_digest = _publish(package_root)
    index_path = published_repo.work / ".well-known/hermes-workflows/index.json"
    index = json.loads(index_path.read_bytes())
    index["packages"][0]["version"] = "2.0.0"
    index["packages"][0]["packageDigest"] = candidate_digest
    index_path.write_bytes(_json_bytes(index))
    _git(published_repo.work, "add", ".")
    _git(published_repo.work, "commit", "-m", "publish mismatched identity")
    _git(published_repo.work, "push", "origin", "main")
    with pytest.raises(WorkflowMarketplaceError) as error:
        service.prepare_update(installed.identity, actor="alice")

    assert error.value.code == "package_index_invalid"
    assert "different-package" not in str(error.value)
    assert _snapshot(destination) == before_bytes
    assert service.installed_store.get(installed.identity) == before_provenance


def test_source_crud_search_and_removal_leave_an_orphaned_install(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)

    assert [source.name for source in service.list_sources()] == ["company"]
    assert service.search("laptop")[0].identifier == "company/laptop-support"
    assert service.set_source_enabled("company", False).enabled is False
    assert service.set_source_enabled("company", True).enabled is True
    service.remove_source("company")

    assert service.list_sources() == ()
    orphan = service.installed_packages()[0]
    assert orphan.identity == installed.identity
    assert orphan.orphaned_source is True
    assert service.installed_store.package_root(installed.identity).is_dir()


def test_source_list_records_reconcile_persisted_refresh_and_cache_state(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    service.add_source(
        WorkflowMarketplaceSource(
            name="zeta",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    service.add_source(
        WorkflowMarketplaceSource(
            name="alpha",
            repositoryUrl=published_repo.remote.as_uri(),
            enabled=False,
        )
    )

    never_refreshed = service.list_source_records()

    assert [item.name for item in never_refreshed] == ["alpha", "zeta"]
    assert never_refreshed[0].enabled is False
    assert never_refreshed[0].refresh_state is None
    assert never_refreshed[1].attempted_at is None
    assert never_refreshed[1].resolved_commit is None
    assert never_refreshed[1].verified_at is None
    assert never_refreshed[1].verified_package_count == 0

    refreshed = service.refresh_source("zeta")
    current = service.list_source_records()[1]

    assert current.refresh_state == "fresh"
    assert current.attempted_at == refreshed.verified_at
    assert current.resolved_commit == refreshed.resolved_commit
    assert current.verified_at == refreshed.verified_at
    assert current.verified_package_count == 1
    assert current.diagnostic_code is None
    assert current.message is None

    service.update_source(
        "zeta",
        published_repo.remote.as_uri(),
        ref=refreshed.resolved_commit,
        enabled=True,
    )
    invalidated = service.list_source_records()[1]

    assert invalidated.refresh_state is None
    assert invalidated.resolved_commit is None
    assert invalidated.verified_at is None
    assert invalidated.verified_package_count == 0


def test_source_list_records_preserve_verified_cache_after_failure_and_fail_closed_on_malformed_state(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    refreshed = service.refresh_source("company")
    configured = service.list_sources()[0]
    service.catalog.source_store.record_failed_refresh(
        configured,
        WorkflowMarketplaceError(
            "source_unavailable", "token=secret /private/tmp/checkout"
        ),
        attempted_at="2026-09-04T01:00:00Z",
        state="unavailable",
    )

    stale = service.list_source_records()[0]
    assert stale.refresh_state == "stale"
    assert stale.attempted_at == "2026-09-04T01:00:00Z"
    assert stale.resolved_commit == refreshed.resolved_commit
    assert stale.verified_at == refreshed.verified_at
    assert stale.verified_package_count == 1
    assert stale.diagnostic_code == "source_unavailable"
    assert "secret" not in (stale.message or "")
    assert "/private/tmp" not in (stale.message or "")

    service.catalog.source_store.catalog_path.write_text(
        '{"schemaVersion":1,"verified":"bad"}'
    )
    with pytest.raises(WorkflowMarketplaceError):
        service.list_source_records()


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["unsafe"])
def test_refresh_diagnostics_redact_assignment_punctuation_and_encoded_local_paths_before_persistence(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    message: str,
) -> None:
    configured = service.add_source(
        WorkflowMarketplaceSource(
            name="private",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )

    service.catalog.source_store.record_failed_refresh(
        configured,
        WorkflowMarketplaceError("source_unavailable", message),
        attempted_at="2026-09-04T01:00:00Z",
        state="unavailable",
    )

    persisted = service.catalog.source_store.catalog_path.read_text()
    status = service.list_source_records()[0]
    assert message not in persisted
    assert status.message != message
    assert status.message is not None


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["safe"])
def test_refresh_diagnostics_accept_canonical_generic_and_nonlocal_slash_prose(
    message: str,
) -> None:
    status = SourceRefreshStatus(
        sourceName="company",
        state="unavailable",
        attemptedAt="2026-09-04T01:00:00Z",
        diagnosticCode="source_unavailable",
        message=message,
    )

    assert status.message == message


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["unsafe"])
def test_refresh_diagnostic_validator_rejects_shared_unsafe_corpus(
    message: str,
) -> None:
    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=message,
        )


@pytest.mark.parametrize("layers", [1, 2, 8, 9])
def test_refresh_diagnostics_reject_bounded_nested_encoded_absolute_paths(
    layers: int,
) -> None:
    message = "/root/workspace/secret.txt"
    for _ in range(layers):
        message = urllib.parse.quote(message, safe="")

    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=message,
        )


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["nested"]["unsafe"])
@pytest.mark.parametrize("layers", range(1, 9))
def test_refresh_diagnostics_reject_http_adjacent_local_identity_at_every_decode_layer(
    message: str,
    layers: int,
) -> None:
    encoded = message
    for _ in range(layers):
        encoded = urllib.parse.quote(encoded, safe="")

    assert (
        redact_source_refresh_message(encoded)
        == "workflow marketplace source refresh failed"
    )
    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=encoded,
        )


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["nested"]["safe"])
@pytest.mark.parametrize("layers", range(1, 9))
def test_refresh_diagnostics_accept_safe_url_and_prose_at_every_decode_layer(
    message: str,
    layers: int,
) -> None:
    encoded = message
    for _ in range(layers):
        encoded = urllib.parse.quote(encoded, safe="")

    assert (
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=encoded,
        ).message
        == encoded
    )


@pytest.mark.parametrize("message", _DIAGNOSTIC_CORPUS["invalidHttp"])
@pytest.mark.parametrize("layers", [0, 1, 8])
def test_refresh_diagnostics_fail_closed_for_invalid_http_at_every_boundary(
    message: str,
    layers: int,
) -> None:
    encoded = message
    for _ in range(layers):
        encoded = urllib.parse.quote(encoded, safe="")

    assert (
        redact_source_refresh_message(encoded)
        == "workflow marketplace source refresh failed"
    )
    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=encoded,
        )


@pytest.mark.parametrize("layers", [0, 1, 8])
def test_refresh_diagnostics_reject_generated_delimiter_identity_matrix(
    layers: int,
) -> None:
    for delimiter in _DIAGNOSTIC_CORPUS["matrix"]["delimiters"]:
        for identity in _DIAGNOSTIC_CORPUS["matrix"]["identities"]:
            message = f"{_DIAGNOSTIC_CORPUS['matrix']['prefix']}{delimiter}{identity}"
            for _ in range(layers):
                message = urllib.parse.quote(message, safe="")

            assert (
                redact_source_refresh_message(message)
                == "workflow marketplace source refresh failed"
            )
            with pytest.raises(ValidationError):
                SourceRefreshStatus(
                    sourceName="company",
                    state="unavailable",
                    attemptedAt="2026-09-04T01:00:00Z",
                    diagnosticCode="source_unavailable",
                    message=message,
                )


def test_refresh_diagnostic_message_enforces_its_exact_size_bound() -> None:
    common = {
        "sourceName": "company",
        "state": "unavailable",
        "attemptedAt": "2026-09-04T01:00:00Z",
        "diagnosticCode": "source_unavailable",
    }

    assert SourceRefreshStatus(**common, message="x" * 4096).message == "x" * 4096
    with pytest.raises(ValidationError):
        SourceRefreshStatus(**common, message="x" * 4097)
    assert SourceRefreshStatus(**common, message="😀" * 4096).message == "😀" * 4096
    with pytest.raises(ValidationError):
        SourceRefreshStatus(**common, message="😀" * 4097)


def test_refresh_diagnostic_scanning_is_bounded_for_maximum_record_batch() -> None:
    encoded_tail = "%41"
    for _ in range(1, 8):
        encoded_tail = urllib.parse.quote(encoded_tail, safe="")
    near_match = "/.staginq"
    message = (
        f"{'x' * (4096 - len(encoded_tail) - len(near_match))}"
        f"{near_match}{encoded_tail}"
    )
    common = {
        "state": "unavailable",
        "attemptedAt": "2026-09-04T01:00:00Z",
        "diagnosticCode": "source_unavailable",
        "message": message,
    }

    started_at = time.perf_counter()
    statuses = [
        SourceRefreshStatus(sourceName=f"source-{index:03d}", **common)
        for index in range(128)
    ]
    elapsed = time.perf_counter() - started_at

    assert all(status.message == message for status in statuses)
    assert elapsed < 5.0


def test_refresh_diagnostic_redactor_bounds_before_local_identity_scan() -> None:
    persisted_prefix = "x" * 4096

    assert (
        redact_source_refresh_message(f"{persisted_prefix};/root/operator/secret.txt")
        == persisted_prefix
    )


def test_refresh_diagnostics_canonicalize_controls_before_persistence_and_reject_raw_state(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    configured = service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    raw = " \tfatal:\r\n remote unavailable\x7f retry  "

    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=raw,
        )

    service.catalog.source_store.record_failed_refresh(
        configured,
        WorkflowMarketplaceError("source_unavailable", raw),
        attempted_at="2026-09-04T01:00:00Z",
        state="unavailable",
    )

    assert (
        service.list_source_records()[0].message
        == "source_unavailable: fatal: remote unavailable retry"
    )
    assert "\\r" not in service.catalog.source_store.catalog_path.read_text()
    assert "\\n" not in service.catalog.source_store.catalog_path.read_text()
    assert "\\t" not in service.catalog.source_store.catalog_path.read_text()


@pytest.mark.parametrize("case", _DIAGNOSTIC_CORPUS["byteOrderMark"]["raw"])
def test_refresh_diagnostics_canonicalize_byte_order_mark_before_persistence_and_reject_raw_state(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    case: dict[str, str],
) -> None:
    configured = service.add_source(
        WorkflowMarketplaceSource(
            name="company",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    message = case["message"]

    assert redact_source_refresh_message(message) == case["canonical"]
    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=message,
        )

    service.catalog.source_store.record_failed_refresh(
        configured,
        WorkflowMarketplaceError("source_unavailable", message),
        attempted_at="2026-09-04T01:00:00Z",
        state="unavailable",
    )

    persisted = service.catalog.source_store.catalog_path.read_text()
    assert "\ufeff" not in persisted
    assert "\ufeff" not in (service.list_source_records()[0].message or "")

    raw_state = json.loads(persisted)
    raw_state["statuses"][0]["message"] = message
    tampered = (json.dumps(raw_state, sort_keys=True) + "\n").encode()
    service.catalog.source_store.catalog_path.write_bytes(tampered)

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.list_source_records()

    assert error.value.code == "catalog_state_invalid"
    assert service.catalog.source_store.catalog_path.read_bytes() == tampered


@pytest.mark.parametrize("case", _DIAGNOSTIC_CORPUS["byteOrderMark"]["encoded"])
def test_refresh_diagnostics_reject_encoded_byte_order_mark(
    case: dict[str, str],
) -> None:
    message = case["message"]

    assert redact_source_refresh_message(message) == case["canonical"]
    with pytest.raises(ValidationError):
        SourceRefreshStatus(
            sourceName="company",
            state="unavailable",
            attemptedAt="2026-09-04T01:00:00Z",
            diagnosticCode="source_unavailable",
            message=message,
        )


def test_source_list_records_report_auth_failure_without_inventing_cache(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    configured = service.add_source(
        WorkflowMarketplaceSource(
            name="private",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    service.catalog.source_store.record_failed_refresh(
        configured,
        WorkflowMarketplaceError("source_authentication_failed", "credential rejected"),
        attempted_at="2026-09-04T01:00:00Z",
        state="authentication-failed",
    )

    failed = service.list_source_records()[0]
    assert failed.refresh_state == "authentication-failed"
    assert failed.resolved_commit is None
    assert failed.verified_at is None
    assert failed.verified_package_count == 0
    assert failed.diagnostic_code == "source_authentication_failed"


def test_source_edit_invalidates_cache_without_uninstalling_packages(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    before = service.installed_store.get(installed.identity)

    updated = service.update_source(
        "company",
        published_repo.remote.as_uri(),
        ref=installed.resolved_commit,
        enabled=True,
    )

    assert updated.ref == installed.resolved_commit
    assert service.search("") == ()
    assert service.installed_store.get(installed.identity) == before
    assert service.installed_store.package_root(installed.identity).is_dir()


def test_direct_install_requires_one_unambiguous_package_and_honors_exact_ref(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    first = _git(published_repo.work, "rev-parse", "head")
    _write_package(published_repo.work, "second-package", version="1.0.0")
    published_repo.publish("add second package")

    with pytest.raises(WorkflowMarketplaceError) as ambiguous:
        service.prepare_install(
            InstallRequest(identifier=published_repo.remote.as_uri()), actor="alice"
        )
    assert ambiguous.value.code == "direct_package_ambiguous"

    review = service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref=first,
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    installed = service.confirm_install(review.confirmation_token, actor="alice")

    assert review.resolved_commit == first
    assert installed.version == "1.0.0"
    assert installed.identity.source_key.startswith("direct-")
    assert published_repo.remote.as_uri() in installed.repository_url


def test_direct_update_checks_refetch_moved_branches_but_keep_exact_refs_current(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    first_commit = _git(published_repo.work, "rev-parse", "head")
    branch_review = service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref="main",
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    branch_install = service.confirm_install(
        branch_review.confirmation_token, actor="alice"
    )
    pinned_service = WorkflowMarketplaceService(
        service.home.parent / "pinned-home", profile="support"
    )
    pinned_review = pinned_service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref=first_commit,
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    pinned_install = pinned_service.confirm_install(
        pinned_review.confirmation_token, actor="alice"
    )
    _write_package(published_repo.work, "laptop-support", version="2.0.0")
    published_repo.publish("publish direct v2")

    branch_check = service.check_updates(branch_install.identity)[0]
    pinned_check = pinned_service.check_updates(pinned_install.identity)[0]

    assert branch_check.status == "update_available"
    assert branch_check.candidate_version == "2.0.0"
    assert pinned_check.status == "current"
    assert pinned_check.candidate_version == "1.0.0"


def test_direct_update_check_reports_sanitized_non_destructive_failure(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref="main",
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    installed = service.confirm_install(review.confirmation_token, actor="alice")
    before_package = _snapshot(service.installed_store.package_root(installed.identity))
    before_provenance = service.installed_store.get(installed.identity)
    trust_review = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trust_review, actor="alice")
    before_trust = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)

    def fail_fetch(*_args, **_kwargs):
        raise WorkflowMarketplaceError(
            "source_fetch_failed",
            "fetch https://alice:top-secret@example.test/private.git?token=hidden failed",
        )

    monkeypatch.setattr(service.catalog.git_fetcher, "fetch", fail_fetch)

    check = service.check_updates(installed.identity)[0]

    assert check.status == "error"
    assert check.diagnostic_code == "source_fetch_failed"
    assert check.message is not None
    assert "alice" not in check.message
    assert "top-secret" not in check.message
    assert "hidden" not in check.message
    assert (
        _snapshot(service.installed_store.package_root(installed.identity))
        == before_package
    )
    assert service.installed_store.get(installed.identity) == before_provenance
    assert service.trust_store.snapshot_read_only(max_bytes=1024 * 1024) == before_trust


def test_direct_install_aliases_have_one_service_owned_logical_target(
    service: WorkflowMarketplaceService,
) -> None:
    shorthand = service.canonical_install_target(
        InstallRequest(identifier="example/workflows", ref="main")
    )
    explicit = service.canonical_install_target(
        InstallRequest(
            identifier="https://github.com/example/workflows.git",
            ref="main",
        )
    )

    assert shorthand == explicit
    assert "https://" not in shorthand
    assert "example/workflows" not in shorthand


def test_direct_update_check_reports_invalid_candidate_without_mutation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    review = service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref="main",
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    installed = service.confirm_install(review.confirmation_token, actor="alice")
    destination = service.installed_store.package_root(installed.identity)
    before_package = _snapshot(destination)
    before_provenance = service.installed_store.get(installed.identity)
    root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    manifest["version"] = "2.0.0"
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    (root / "workflows" / "diagnostic.yaml").write_text(
        "name: diagnostic\nnodes: [not-a-node]\n", encoding="utf-8"
    )
    _publish(root)
    published_repo.publish("publish invalid direct candidate")

    check = service.check_updates(installed.identity)[0]

    assert check.status == "error"
    assert check.diagnostic_code == "package_workflow_invalid"
    assert _snapshot(destination) == before_package
    assert service.installed_store.get(installed.identity) == before_provenance


@pytest.mark.parametrize(
    ("candidate_version", "expected_code"),
    [("0.9.0", "package_version_regression"), ("1.0.0", "package_version_conflict")],
)
def test_direct_update_check_enforces_version_progression_rules(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    candidate_version: str,
    expected_code: str,
) -> None:
    review = service.prepare_install(
        InstallRequest(
            identifier=published_repo.remote.as_uri(),
            ref="main",
            packagePath="packages/laptop-support",
        ),
        actor="alice",
    )
    installed = service.confirm_install(review.confirmation_token, actor="alice")
    _write_package(
        published_repo.work,
        "laptop-support",
        version=candidate_version,
        marker="changed-without-valid-progress",
    )
    published_repo.publish("publish invalid version progression")

    check = service.check_updates(installed.identity)[0]

    assert check.status == "error"
    assert check.diagnostic_code == expected_code


def test_install_rejects_existing_identity_and_update_regressions(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    with pytest.raises(WorkflowMarketplaceError) as conflict:
        service.prepare_install(
            InstallRequest(identifier="company/laptop-support"), actor="alice"
        )
    assert conflict.value.code == "installed_package_conflict"

    _write_package(published_repo.work, "laptop-support", version="0.9.0")
    published_repo.publish("publish regression")
    with pytest.raises(WorkflowMarketplaceError) as regression:
        service.prepare_update(installed.identity, actor="alice")
    assert regression.value.code == "package_version_regression"
    assert service.installed_store.get(installed.identity).package_version == "1.0.0"


def test_unchanged_update_has_stable_result_and_does_not_create_transaction(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    before = _snapshot(service.installed_store.package_root(installed.identity))

    review = service.prepare_update(installed.identity, actor="alice")

    assert review.result == "unchanged"
    assert review.confirmation_token is None
    assert service.transactions.list_journals() == ()
    assert _snapshot(service.installed_store.package_root(installed.identity)) == before


def test_unchanged_package_at_new_repository_commit_does_not_mutate(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    before = _snapshot(service.installed_store.package_root(installed.identity))
    (published_repo.work / "README.md").write_text(
        "Repository-only documentation change.\n", encoding="utf-8"
    )
    published_repo.publish("change repository documentation")

    review = service.prepare_update(installed.identity, actor="alice")

    assert review.result == "unchanged"
    assert review.confirmation_token is None
    assert service.transactions.list_journals() == ()
    assert _snapshot(service.installed_store.package_root(installed.identity)) == before
    assert service.installed_store.get(installed.identity).resolved_commit == (
        installed.resolved_commit
    )


def test_changed_update_reports_bounded_metadata_and_revokes_only_its_origin(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    trust_review = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trust_review.confirmation_token, actor="alice")
    first_workflow = trust_review.workflows[0]
    service.trust_store.trust(
        first_workflow.package_digest,
        actor="manual-operator",
        risk_digest=first_workflow.risk_digest,
    )

    requirements = {
        "runtimes": ["python"],
        "tools": ["git", "curl"],
        "providers": ["openrouter"],
        "services": [],
        "secrets": [],
    }
    _write_package(
        published_repo.work,
        "laptop-support",
        version="2.0.0",
        workflow_names=("diagnostic", "new-workflow"),
        marker="v2",
        requirements=requirements,
    )
    published_repo.publish("publish v2")

    review = service.prepare_update(installed.identity, actor="alice")
    assert review.result == "update_available"
    assert review.old_version == "1.0.0"
    assert review.candidate_version == "2.0.0"
    assert [item.path for item in review.file_changes] == sorted(
        item.path for item in review.file_changes
    )
    assert {item.kind for item in review.file_changes} >= {"modified"}
    assert review.workflow_changes.added == ["new-workflow"]
    assert review.workflow_changes.removed == ["repair"]
    assert "python" in review.requirement_changes.runtimes.added

    updated = service.confirm_update(review.confirmation_token, actor="alice")
    assert updated.version == "2.0.0"
    assert set(service.workflow_trust(updated.identity).values()) == {"untrusted"}
    assert (
        service.trust_store.check(
            first_workflow.package_digest,
            risk_digest=first_workflow.risk_digest,
        )
        == "trusted"
    )


def test_update_review_accepts_maximum_workflow_name_with_structured_risk_changes(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    workflow_name = "w" * 128
    _write_package(
        published_repo.work,
        "laptop-support",
        version="1.0.0",
        workflow_names=(workflow_name,),
    )
    published_repo.publish("publish maximum workflow name")
    installed = _install(service, published_repo)
    _write_package(
        published_repo.work,
        "laptop-support",
        version="2.0.0",
        workflow_names=(workflow_name,),
        marker="changed-risk",
    )
    published_repo.publish("update maximum workflow name")

    review = service.prepare_update(installed.identity, actor="alice")

    assert review.result == "update_available"
    assert review.risk_changes.added[0].workflow_name == workflow_name
    assert review.risk_changes.removed[0].workflow_name == workflow_name


def test_review_uses_one_shared_package_path_table_with_exact_recoverability(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    workflow_names = tuple(f"workflow-{index:02d}" for index in range(30))
    _write_package(
        published_repo.work,
        "laptop-support",
        version="2.0.0",
        workflow_names=workflow_names,
    )
    package_root = published_repo.work / "packages" / "laptop-support"
    for index in range(80):
        (package_root / "scripts" / f"resource-{index:03d}.py").write_text(
            f"VALUE = {index}\n", encoding="utf-8"
        )
    _publish(package_root)
    published_repo.publish("publish bounded large review")
    _add_and_refresh(service, published_repo)

    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )
    payload = review.model_dump(mode="json", by_alias=True)
    expected_paths = sorted(
        record["path"]
        for record in json.loads((package_root / "digests.json").read_bytes())["files"]
    )

    assert review.assessment.package_resources == expected_paths
    assert all(
        item.package_resource_set == "package" for item in review.workflow_reviews
    )
    assert all("packageResources" not in item for item in payload["workflowReviews"])
    assert len(json.dumps(payload, separators=(",", ":")).encode()) < 100_000

    installed = service.confirm_install(review.confirmation_token, actor="alice")
    trust_review = service.review_trust(installed.identity, actor="alice")
    trust_payload = trust_review.model_dump(mode="json", by_alias=True)
    assert trust_review.package_resources == expected_paths
    assert all("packageResources" not in item for item in trust_payload["workflows"])
    assert len(json.dumps(trust_payload, separators=(",", ":")).encode()) < 100_000


def test_review_statically_discloses_actual_local_and_remote_mcp_servers(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = published_repo.work / "packages" / "laptop-support"
    diagnostic_path = package_root / "workflows" / "diagnostic.yaml"
    diagnostic_path.write_bytes(
        yaml.safe_dump(
            {
                "name": "diagnostic",
                "description": "MCP disclosure",
                "nodes": [
                    {
                        "id": "ask",
                        "prompt": "hello",
                        "mcp": "mixed.yaml",
                    }
                ],
            },
            sort_keys=False,
        ).encode()
    )
    (package_root / "mcp").mkdir(exist_ok=True)
    (package_root / "mcp" / "mixed.yaml").write_text(
        "mcp_servers:\n"
        "  local_server:\n"
        "    command: python\n"
        "    args: [scripts/never-run.py]\n"
        "  remote_server:\n"
        "    url: https://mcp.example.test/events\n"
        "    transport: sse\n",
        encoding="utf-8",
    )
    _publish(package_root)
    published_repo.publish("publish mixed MCP definitions")
    installed = _install(service, published_repo)

    def execution_forbidden(*_args, **_kwargs):
        raise AssertionError("review attempted execution or network access")

    monkeypatch.setattr(subprocess, "Popen", execution_forbidden)
    monkeypatch.setattr(socket, "create_connection", execution_forbidden)
    review = service.review_trust(installed.identity, actor="alice")
    diagnostic = next(
        item for item in review.workflows if item.workflow_name == "diagnostic"
    )

    assert diagnostic.mcp_resources == ["mcp/mixed.yaml"]
    assert diagnostic.local_mcp_servers == ["local_server"]
    assert diagnostic.remote_mcp_servers == ["remote_server"]
    assert not (service.home / "executed").exists()


def test_oversized_mcp_review_fails_stably_without_execution(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    package_root = published_repo.work / "packages" / "laptop-support"
    diagnostic_path = package_root / "workflows" / "diagnostic.yaml"
    diagnostic_path.write_bytes(
        yaml.safe_dump(
            {
                "name": "diagnostic",
                "description": "Oversized MCP",
                "nodes": [{"id": "ask", "prompt": "hello", "mcp": "oversized.yaml"}],
            },
            sort_keys=False,
        ).encode()
    )
    (package_root / "mcp").mkdir(exist_ok=True)
    (package_root / "mcp" / "oversized.yaml").write_bytes(
        b"server:\n  command: python\n  padding: " + b"x" * 256_001 + b"\n"
    )
    _publish(package_root)
    published_repo.publish("publish oversized MCP definition")
    _add_and_refresh(service, published_repo)

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.prepare_install(
            InstallRequest(identifier="company/laptop-support"), actor="alice"
        )

    assert error.value.code == "package_workflow_invalid"
    assert str(error.value) == (
        "package_workflow_invalid: package workflow assessment is invalid"
    )


@pytest.mark.parametrize(
    ("reference", "content"),
    [
        ("malformed.yaml", "mcp_servers: [\n"),
        ("missing.yaml", None),
        ("../../escape.yaml", None),
    ],
)
def test_malformed_or_escaping_mcp_resources_fail_stably(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    reference: str,
    content: str | None,
) -> None:
    package_root = published_repo.work / "packages" / "laptop-support"
    (package_root / "workflows" / "diagnostic.yaml").write_bytes(
        yaml.safe_dump(
            {
                "name": "diagnostic",
                "description": "Invalid MCP",
                "nodes": [{"id": "ask", "prompt": "hello", "mcp": reference}],
            },
            sort_keys=False,
        ).encode()
    )
    if content is not None:
        (package_root / "mcp").mkdir(exist_ok=True)
        (package_root / "mcp" / reference).write_text(content, encoding="utf-8")
    _publish(package_root)
    published_repo.publish("publish invalid MCP definition")
    _add_and_refresh(service, published_repo)

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.prepare_install(
            InstallRequest(identifier="company/laptop-support"), actor="alice"
        )

    assert error.value.code == "package_workflow_invalid"
    assert len(str(error.value)) <= 4096


def test_invalid_update_preserves_installed_bytes_provenance_and_trust(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    trust_review = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trust_review.confirmation_token, actor="alice")
    destination = service.installed_store.package_root(installed.identity)
    before = _snapshot(destination)
    before_provenance = service.installed_store.get(installed.identity)

    root = published_repo.work / "packages" / "laptop-support"
    manifest = json.loads((root / "workflow-package.json").read_bytes())
    manifest["version"] = "2.0.0"
    (root / "workflow-package.json").write_bytes(_json_bytes(manifest))
    (root / "workflows" / "diagnostic.yaml").write_text(
        "name: diagnostic\nnodes: [not-a-node]\n", encoding="utf-8"
    )
    _publish(root)
    published_repo.publish("publish invalid v2")

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.prepare_update(installed.identity, actor="alice")
    assert error.value.code == "package_workflow_invalid"
    assert _snapshot(destination) == before
    assert service.installed_store.get(installed.identity) == before_provenance
    assert set(service.workflow_trust(installed.identity).values()) == {"trusted"}


def test_trust_review_is_exact_selectable_and_stale_reviews_fail_closed(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    selected = service.review_trust(
        installed.identity,
        actor="alice",
        workflow_name="diagnostic",
    )
    assert [item.workflow_name for item in selected.workflows] == ["diagnostic"]
    assert selected.workflows[0].shell_or_script_nodes == ["execute"]
    assert selected.workflows[0].script_resources == []
    assert "scripts/never-run.py" in selected.package_resources
    assert selected.workflows[0].external_requirements.runtimes == ["uv"]
    service.grant_trust(selected.confirmation_token, actor="alice")
    assert service.workflow_trust(installed.identity) == {
        "diagnostic": "trusted",
        "repair": "untrusted",
    }

    all_review = service.review_trust(installed.identity, actor="alice")
    destination = service.installed_store.package_root(installed.identity)
    (destination / "commands" / "guide.md").write_text("changed", encoding="utf-8")
    with pytest.raises(WorkflowMarketplaceError) as stale:
        service.grant_trust(all_review.confirmation_token, actor="alice")
    assert stale.value.code in {"package_digest_mismatch", "trust_review_changed"}


def test_trust_mutations_honor_exact_cancellation_boundary(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    review = service.review_trust(installed.identity, actor="alice")
    metadata = service.confirmation_metadata(
        review.confirmation_token,
        actor="alice",
        operation="trust",
    )
    assert metadata.identity == installed.identity
    with pytest.raises(WorkflowMarketplaceError) as foreign:
        service.confirmation_metadata(
            review.confirmation_token,
            actor="mallory",
            operation="trust",
        )
    assert foreign.value.code == "confirmation_token_invalid"

    with pytest.raises(WorkflowMarketplaceError) as cancelled:
        service.grant_trust(
            review.confirmation_token,
            actor="alice",
            cancelled=lambda: False,
            enter_atomic=lambda: False,
        )
    assert cancelled.value.code == "marketplace_operation_cancelled"
    assert set(service.workflow_trust(installed.identity).values()) == {"untrusted"}

    accepted = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(accepted.confirmation_token, actor="alice")
    assert set(service.workflow_trust(installed.identity).values()) == {"trusted"}

    with pytest.raises(WorkflowMarketplaceError) as revoke_cancelled:
        service.revoke_trust(
            installed.identity,
            cancelled=lambda: True,
            enter_atomic=lambda: pytest.fail("cancel must win before trust mutation"),
        )
    assert revoke_cancelled.value.code == "marketplace_operation_cancelled"
    assert set(service.workflow_trust(installed.identity).values()) == {"trusted"}


def test_all_workflow_trust_grant_is_one_atomic_write_and_preserves_other_grants(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(service, published_repo)
    review = service.review_trust(installed.identity, actor="alice")
    first = review.workflows[0]
    service.trust_store.trust(
        first.package_digest,
        actor="manual",
        risk_digest=first.risk_digest,
    )
    service.trust_store.trust_origin(
        first.package_digest,
        actor="other",
        risk_digest=first.risk_digest,
        origin="marketplace:other/package",
    )
    original_write = service.trust_store._write
    writes = 0

    def recording_write(payload):
        nonlocal writes
        writes += 1
        return original_write(payload)

    monkeypatch.setattr(service.trust_store, "_write", recording_write)

    service.grant_trust(review, actor="alice")

    payload = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)
    assert writes == 1
    assert set(payload["records"][first.package_digest]["grants"]) >= {
        "manual",
        "marketplace:other/package",
        "marketplace:company/laptop-support",
    }


def test_failed_all_workflow_grant_is_zero_partial_and_consumes_review(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(service, published_repo)
    review = service.review_trust(installed.identity, actor="alice")
    before = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)
    original_write = service.trust_store._write
    origin = "marketplace:company/laptop-support"

    def fail_complete_origin_set(payload):
        matching = sum(
            origin in record["grants"] for record in payload["records"].values()
        )
        if matching == len(review.workflows):
            raise OSError("injected pre-commit trust failure")
        return original_write(payload)

    monkeypatch.setattr(service.trust_store, "_write", fail_complete_origin_set)

    with pytest.raises(OSError, match="pre-commit"):
        service.grant_trust(review, actor="alice")

    assert service.trust_store.snapshot_read_only(max_bytes=1024 * 1024) == before
    with pytest.raises(WorkflowMarketplaceError) as replay:
        service.grant_trust(review, actor="alice")
    assert replay.value.code == "confirmation_token_invalid"


@pytest.mark.parametrize("lifecycle", ["update", "remove"])
def test_trust_grant_serializes_validation_and_write_with_lifecycle_mutation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    lifecycle: str,
) -> None:
    installed = _install(service, published_repo)
    trust_review = service.review_trust(installed.identity, actor="alice")
    if lifecycle == "update":
        _write_package(published_repo.work, "laptop-support", version="2.0.0")
        published_repo.publish("publish concurrent update")
        mutation_review = service.prepare_update(installed.identity, actor="alice")
        mutation = lambda: service.confirm_update(
            mutation_review.confirmation_token, actor="alice"
        )
    else:
        mutation_review = service.prepare_remove(installed.identity, actor="alice")
        mutation = lambda: service.confirm_remove(
            mutation_review.confirmation_token, actor="alice"
        )
    original_builder = service.assessment_builder
    validation_started = threading.Event()
    release_validation = threading.Event()
    mutation_started = threading.Event()
    mutation_finished = threading.Event()
    failures: list[BaseException] = []

    def blocking_builder(compilation):
        validation_started.set()
        if not release_validation.wait(5):
            raise AssertionError("test did not release trust validation")
        return original_builder(compilation)

    def grant() -> None:
        try:
            service.grant_trust(trust_review, actor="alice")
        except BaseException as error:
            failures.append(error)

    def mutate() -> None:
        mutation_started.set()
        try:
            mutation()
        except BaseException as error:
            failures.append(error)
        finally:
            mutation_finished.set()

    service.assessment_builder = blocking_builder
    grant_thread = threading.Thread(target=grant)
    mutation_thread = threading.Thread(target=mutate)
    grant_thread.start()
    assert validation_started.wait(5)
    mutation_thread.start()
    assert mutation_started.wait(5)
    assert not mutation_finished.wait(0.25)
    release_validation.set()
    grant_thread.join(5)
    mutation_thread.join(5)

    assert not grant_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert failures == []
    payload = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)
    assert all(
        "marketplace:company/laptop-support" not in record["grants"]
        for record in payload["records"].values()
    )


def test_trust_grant_rejects_stale_installed_provenance_without_any_grant(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    review = service.review_trust(installed.identity, actor="alice")
    state = json.loads(service.installed_store.path.read_bytes())
    state["packages"][0]["provenance"]["resolvedCommit"] = "f" * 40
    service.installed_store.path.write_bytes(_json_bytes(state))

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.grant_trust(review, actor="alice")

    assert error.value.code == "trust_review_changed"
    assert (
        service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)["records"] == {}
    )


def test_revoke_trust_for_one_workflow_preserves_other_and_manual_grants(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    trust_review = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trust_review.confirmation_token, actor="alice")
    diagnostic = next(
        item for item in trust_review.workflows if item.workflow_name == "diagnostic"
    )
    service.trust_store.trust(
        diagnostic.package_digest,
        actor="manual",
        risk_digest=diagnostic.risk_digest,
    )

    assert service.revoke_trust(installed.identity, workflow_name="diagnostic") == 1

    assert service.workflow_trust(installed.identity) == {
        "diagnostic": "trusted",
        "repair": "trusted",
    }
    assert service.revoke_trust(installed.identity, workflow_name="diagnostic") == 0


def test_remove_preview_and_confirm_remove_only_the_selected_installation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    trust_review = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trust_review.confirmation_token, actor="alice")
    manual = trust_review.workflows[0]
    service.trust_store.trust(
        manual.package_digest,
        actor="manual",
        risk_digest=manual.risk_digest,
    )
    loose = service.home / "workflows" / "loose.yaml"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_text("name: loose\n", encoding="utf-8")

    review = service.prepare_remove(installed.identity, actor="alice")
    assert review.operation == "remove"
    assert review.current_version == "1.0.0"
    removed = service.confirm_remove(review.confirmation_token, actor="alice")

    assert removed.identity == installed.identity
    assert not service.installed_store.package_root(installed.identity).exists()
    assert loose.exists()
    assert service.list_sources()[0].name == "company"
    assert (
        service.trust_store.check(manual.package_digest, risk_digest=manual.risk_digest)
        == "trusted"
    )


def test_same_package_from_two_sources_coexists_and_removal_is_origin_scoped(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    company = _install(service, published_repo)
    company_trust = service.review_trust(company.identity, actor="alice")
    service.grant_trust(company_trust.confirmation_token, actor="alice")
    service.add_source(
        WorkflowMarketplaceSource(
            name="partner",
            repositoryUrl=published_repo.remote.as_uri(),
        )
    )
    assert service.refresh_source("partner").state == "fresh"
    partner_review = service.prepare_install(
        InstallRequest(identifier="partner/laptop-support"), actor="alice"
    )
    partner = service.confirm_install(partner_review.confirmation_token, actor="alice")
    partner_trust = service.review_trust(partner.identity, actor="alice")
    service.grant_trust(partner_trust.confirmation_token, actor="alice")

    removal = service.prepare_remove(company.identity, actor="alice")
    service.confirm_remove(removal.confirmation_token, actor="alice")

    assert [item.identity for item in service.installed_packages()] == [
        partner.identity
    ]
    assert set(service.workflow_trust(partner.identity).values()) == {"trusted"}


def test_update_trust_revocation_failure_rolls_back_bytes_and_provenance(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(service, published_repo)
    trusted = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trusted.confirmation_token, actor="alice")
    destination = service.installed_store.package_root(installed.identity)
    before = _snapshot(destination)
    provenance = service.installed_store.get(installed.identity)
    trust_before = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)
    _write_package(published_repo.work, "laptop-support", version="2.0.0")
    published_repo.publish("publish v2")
    review = service.prepare_update(installed.identity, actor="alice")

    def fail_before_commit(_payload):
        raise WorkflowTrustError("trust replacement failed")

    monkeypatch.setattr(service.trust_store, "_write", fail_before_commit)

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.confirm_update(review.confirmation_token, actor="alice")
    assert error.value.code == "transaction_rollback_completed"

    assert _snapshot(destination) == before
    assert service.installed_store.get(installed.identity) == provenance
    assert service.trust_store.snapshot_read_only(max_bytes=1024 * 1024) == trust_before
    assert set(service.workflow_trust(installed.identity).values()) == {"trusted"}


def test_remove_trust_revocation_failure_rolls_back_bytes_and_provenance(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(service, published_repo)
    trusted = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trusted.confirmation_token, actor="alice")
    destination = service.installed_store.package_root(installed.identity)
    before = _snapshot(destination)
    provenance = service.installed_store.get(installed.identity)
    trust_before = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)
    review = service.prepare_remove(installed.identity, actor="alice")

    def fail_before_commit(_payload):
        raise WorkflowTrustError("trust replacement failed")

    monkeypatch.setattr(service.trust_store, "_write", fail_before_commit)

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.confirm_remove(review.confirmation_token, actor="alice")
    assert error.value.code == "transaction_rollback_completed"

    assert _snapshot(destination) == before
    assert service.installed_store.get(installed.identity) == provenance
    assert service.trust_store.snapshot_read_only(max_bytes=1024 * 1024) == trust_before
    assert set(service.workflow_trust(installed.identity).values()) == {"trusted"}


def test_update_commit_then_raise_converges_and_preserves_unrelated_trust(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(service, published_repo)
    trusted = service.review_trust(installed.identity, actor="alice")
    service.grant_trust(trusted.confirmation_token, actor="alice")
    workflow = trusted.workflows[0]
    service.trust_store.trust(
        workflow.package_digest,
        actor="manual",
        risk_digest=workflow.risk_digest,
    )
    other_origin = "marketplace:other/laptop-support"
    service.trust_store.trust_origin(
        workflow.package_digest,
        actor="other",
        risk_digest=workflow.risk_digest,
        origin=other_origin,
    )
    _write_package(published_repo.work, "laptop-support", version="2.0.0")
    published_repo.publish("publish v2")
    review = service.prepare_update(installed.identity, actor="alice")
    original_write = service.trust_store._write

    def commit_then_raise(payload):
        original_write(payload)
        raise OSError("directory fsync failed after replacement")

    monkeypatch.setattr(service.trust_store, "_write", commit_then_raise)

    updated = service.confirm_update(review.confirmation_token, actor="alice")

    assert updated.version == "2.0.0"
    grants = service.trust_store.snapshot_read_only(max_bytes=1024 * 1024)["records"][
        workflow.package_digest
    ]["grants"]
    assert set(grants) == {"manual", other_origin}


def test_models_reject_credential_bearing_direct_install_identifiers() -> None:
    with pytest.raises(ValueError):
        InstallRequest(identifier="https://user:secret@example.test/workflows.git")


def test_service_rejects_cross_actor_confirmation(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    _add_and_refresh(service, published_repo)
    review = service.prepare_install(
        InstallRequest(identifier="company/laptop-support"), actor="alice"
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        service.confirm_install(review.confirmation_token, actor="mallory")
    assert error.value.code == "confirmation_token_invalid"


def test_trust_confirmation_is_actor_profile_scoped_and_single_use(
    service: WorkflowMarketplaceService,
    published_repo: PublishedRepository,
) -> None:
    installed = _install(service, published_repo)
    review = service.review_trust(installed.identity, actor="alice")
    other_profile = WorkflowMarketplaceService(service.home, profile="other")

    with pytest.raises(WorkflowMarketplaceError) as cross_actor:
        service.grant_trust(review.confirmation_token, actor="mallory")
    assert cross_actor.value.code == "confirmation_token_invalid"
    with pytest.raises(WorkflowMarketplaceError) as cross_profile:
        other_profile.grant_trust(review.confirmation_token, actor="alice")
    assert cross_profile.value.code == "confirmation_token_invalid"

    service.grant_trust(review.confirmation_token, actor="alice")
    with pytest.raises(WorkflowMarketplaceError) as replay:
        service.grant_trust(review.confirmation_token, actor="alice")
    assert replay.value.code == "confirmation_token_invalid"


def test_installed_identity_is_profile_local_and_stable() -> None:
    identity = InstalledPackageIdentity(sourceKey="company", packageId="support")
    assert identity.source_key == "company"
    assert identity.package_id == "support"


def test_source_model_remains_credential_free() -> None:
    source = WorkflowMarketplaceSource(
        name="company",
        repositoryUrl="git@example.test:team/workflows.git",
        ref="main",
    )
    assert source.repository_url == "git@example.test:team/workflows.git"


def test_service_clock_is_used_for_bounded_trust_review_expiry(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    service = WorkflowMarketplaceService(
        tmp_path / "home", profile="support", clock=lambda: now
    )
    assert service.profile == "support"
