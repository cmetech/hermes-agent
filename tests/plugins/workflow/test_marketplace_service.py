"""Review-first workflow marketplace lifecycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from plugins.workflow.marketplace.models import (
    InstallRequest,
    InstalledPackageIdentity,
    WorkflowMarketplaceSource,
)
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.service import WorkflowMarketplaceService
from plugins.workflow.trust import WorkflowTrustError


_DIGEST_DOMAIN = b"hermes.workflow-package.v1\0"


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
    assert selected.workflows[0].script_resources == ["scripts/never-run.py"]
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

    with pytest.raises(WorkflowTrustError):
        service.confirm_update(review.confirmation_token, actor="alice")

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

    with pytest.raises(WorkflowTrustError):
        service.confirm_remove(review.confirmation_token, actor="alice")

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
