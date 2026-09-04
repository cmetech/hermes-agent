from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from plugins.workflow.marketplace.api import (
    WorkflowMarketplaceApiContext,
    create_marketplace_router,
)
from plugins.workflow.marketplace.catalog import CatalogPackage, SourceRefreshResult
from plugins.workflow.marketplace.models import (
    ExternalRequirements,
    FileDigestChange,
    InstallRequest,
    InstalledPackage,
    InstalledPackageIdentity,
    InstallReview,
    PackageInspection,
    PackageInspectionResource,
    PackageReviewAssessment,
    RemoveReview,
    RequirementChanges,
    StringSetChange,
    TrustReview,
    UpdateCheck,
    UpdateReview,
    WorkflowCompatibilityChanges,
    WorkflowMarketplaceSource,
    WorkflowRiskChanges,
    WorkflowTrustReviewItem,
)
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.service import WorkflowMarketplaceService


_DIGEST = "1" * 64
_RISK_DIGEST = "2" * 64
_REVIEW_DIGEST = "3" * 64
_COMMIT = "4" * 40
_TOKEN = "confirmation-token-value-1234567890"
_NOW = datetime(2026, 9, 4, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _Authority:
    capabilities: frozenset[str]
    authority_binding: str = "session:provider:org:raw-user-identity"

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise HTTPException(
                status_code=403,
                detail={"code": f"workflow_{capability}_required"},
            )


def _verified_operator(request: Request, _requested_scope: str | None):
    level = request.headers.get("X-Test-Authority", "none")
    capabilities = {
        "none": frozenset(),
        "read": frozenset({"read"}),
        "write": frozenset({"read", "write"}),
        "admin": frozenset({"read", "write", "admin"}),
    }[level]
    return _Authority(
        capabilities,
        authority_binding=request.headers.get(
            "X-Test-Actor", "session:provider:org:raw-user-identity"
        ),
    )


def _identity() -> InstalledPackageIdentity:
    return InstalledPackageIdentity(sourceKey="company", packageId="laptop-support")


def _requirements() -> ExternalRequirements:
    return ExternalRequirements(
        runtimes=[], tools=[], providers=[], services=[], secrets=[]
    )


def _workflow_review() -> WorkflowTrustReviewItem:
    return WorkflowTrustReviewItem(
        workflowName="laptop-diagnostic",
        definitionPath="workflows/laptop-diagnostic.yaml",
        packageDigest=_DIGEST,
        riskDigest=_RISK_DIGEST,
        trustState="untrusted",
        shellOrScriptNodes=["collect"],
        commandNodes=[],
        approvalNodes=[],
        commandResources=[],
        scriptResources=["scripts/collect.py"],
        mcpResources=[],
        mcpResourceFiles=[],
        requestedTools=[],
        requestedSkills=[],
        localMcpServers=[],
        remoteMcpServers=[],
        providers=[],
        outwardActionNodes=[],
        requiredSecrets=[],
        externalRequirements=_requirements(),
        packageResourceSet="package",
        compatibility=[],
    )


def _assessment() -> PackageReviewAssessment:
    return PackageReviewAssessment(
        packageDigest=_DIGEST,
        reviewDigest=_REVIEW_DIGEST,
        workflowNames=["laptop-diagnostic"],
        blockers=[],
        advisories=[],
        externalRequirements=_requirements(),
        packageResources=[
            "scripts/collect.py",
            "workflow-package.json",
            "workflows/laptop-diagnostic.yaml",
        ],
    )


def _install_review() -> InstallReview:
    return InstallReview(
        operation="install",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath="packages/laptop-support",
        candidateVersion="1.0.0",
        candidateDigest=_DIGEST,
        assessment=_assessment(),
        fileChanges=[
            FileDigestChange(
                path="workflow-package.json",
                kind="added",
                candidateDigest=_DIGEST,
            )
        ],
        workflowReviews=[_workflow_review()],
    )


def _installed(*, version: str = "1.0.0") -> InstalledPackage:
    return InstalledPackage(
        identity=_identity(),
        sourceName="company",
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        packagePath="packages/laptop-support",
        version=version,
        contractVersion=1,
        distributionDigest=_DIGEST,
        installedAt=_NOW,
        actor="marketplace:actor",
        workflowPaths=["workflows/laptop-diagnostic.yaml"],
        orphanedSource=False,
    )


def _empty_change() -> StringSetChange:
    return StringSetChange(added=[], removed=[])


def _update_review() -> UpdateReview:
    return UpdateReview(
        operation="update",
        result="update_available",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        oldVersion="1.0.0",
        candidateVersion="2.0.0",
        oldCommit="5" * 40,
        candidateCommit=_COMMIT,
        oldDigest="6" * 64,
        candidateDigest=_DIGEST,
        fileChanges=[],
        workflowChanges=_empty_change(),
        requirementChanges=RequirementChanges(
            runtimes=_empty_change(),
            tools=_empty_change(),
            providers=_empty_change(),
            services=_empty_change(),
            secrets=_empty_change(),
        ),
        riskChanges=WorkflowRiskChanges(added=[], removed=[]),
        compatibilityChanges=WorkflowCompatibilityChanges(added=[], removed=[]),
        assessment=_assessment(),
        workflowReviews=[_workflow_review()],
    )


def _remove_review() -> RemoveReview:
    return RemoveReview(
        operation="remove",
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        currentVersion="1.0.0",
        currentCommit=_COMMIT,
        distributionDigest=_DIGEST,
        workflowNames=["laptop-diagnostic"],
    )


def _trust_review() -> TrustReview:
    return TrustReview(
        confirmationToken=_TOKEN,
        reviewDigest=_REVIEW_DIGEST,
        identity=_identity(),
        sourceName="company",
        version="1.0.0",
        resolvedCommit=_COMMIT,
        distributionDigest=_DIGEST,
        packageResources=[
            "scripts/collect.py",
            "workflow-package.json",
            "workflows/laptop-diagnostic.yaml",
        ],
        workflows=[_workflow_review()],
    )


def _inspection() -> PackageInspection:
    return PackageInspection(
        identifier="company/laptop-support",
        identity=_identity(),
        sourceName="company",
        repositoryUrl="ssh://git@example.test/team/workflows.git",
        configuredRef="main",
        resolvedCommit=_COMMIT,
        verifiedAt=_NOW,
        verified=True,
        sourceState="fresh",
        id="laptop-support",
        version="1.0.0",
        displayName="Laptop Support",
        description="Support workflows",
        license="MIT",
        publisher="Example",
        tags=["support"],
        packagePath="packages/laptop-support",
        contractVersion=1,
        packageDigest=_DIGEST,
        workflows=[_workflow_review()],
        resources=[
            PackageInspectionResource(path="scripts/collect.py", types=["script"]),
            PackageInspectionResource(
                path="workflows/laptop-diagnostic.yaml",
                types=["workflow_definition"],
            ),
        ],
        externalRequirements=_requirements(),
        blockers=[],
        advisories=[],
        installStatus="not_installed",
        updateStatus="not_applicable",
    )


class _FakeService:
    def __init__(self):
        self.calls: list[tuple] = []
        self.sources: list[WorkflowMarketplaceSource] = []
        self.confirm_error: WorkflowMarketplaceError | None = None

    def add_source(self, source):
        self.calls.append(("add_source", source))
        self.sources.append(source)
        return source

    def list_sources(self):
        self.calls.append(("list_sources",))
        return tuple(self.sources)

    def update_source(self, name, repository_url, *, ref, enabled):
        self.calls.append(("update_source", name, repository_url, ref, enabled))
        updated = WorkflowMarketplaceSource(
            name=name, repositoryUrl=repository_url, ref=ref, enabled=enabled
        )
        self.sources = [updated if item.name == name else item for item in self.sources]
        return updated

    def set_source_enabled(self, name, enabled):
        self.calls.append(("set_source_enabled", name, enabled))
        source = next(item for item in self.sources if item.name == name)
        updated = source.model_copy(update={"enabled": enabled})
        self.sources = [updated if item.name == name else item for item in self.sources]
        return updated

    def remove_source(self, name):
        self.calls.append(("remove_source", name))
        source = next(item for item in self.sources if item.name == name)
        self.sources.remove(source)
        return source

    def refresh_source(self, name, *, cancelled):
        self.calls.append(("refresh_source", name, cancelled))
        return SourceRefreshResult(
            source_name=name,
            repository_url="ssh://git@example.test/team/workflows.git",
            state="fresh",
            resolved_commit=_COMMIT,
            verified_at=_NOW,
            package_count=1,
        )

    def search(self, query, *, source=None, limit=100):
        self.calls.append(("search", query, source, limit))
        return (
            CatalogPackage(
                identifier="company/laptop-support",
                source_name="company",
                repository_url="ssh://git@example.test/team/workflows.git",
                configured_ref="main",
                resolved_commit=_COMMIT,
                verified_at=_NOW,
                state="fresh",
                id="laptop-support",
                version="1.0.0",
                display_name="Laptop Support",
                description="Support workflows",
                license="MIT",
                publisher="Example",
                tags=("support",),
                package_path="packages/laptop-support",
                contract_version=1,
                package_digest=_DIGEST,
            ),
        )

    def inspect(self, identifier, *, cancelled):
        self.calls.append(("inspect", identifier, cancelled))
        return _inspection()

    def installed_packages(self):
        self.calls.append(("installed_packages",))
        return (_installed(),)

    def check_updates(self, identity=None, *, cancelled):
        self.calls.append(("check_updates", identity, cancelled))
        return (
            UpdateCheck(
                identity=identity or _identity(),
                status="update_available",
                installedVersion="1.0.0",
                candidateVersion="2.0.0",
            ),
        )

    def prepare_install(self, request: InstallRequest, *, actor, cancelled):
        self.calls.append(("prepare_install", request, actor, cancelled))
        return _install_review()

    def confirm_install(self, token, *, actor):
        self.calls.append(("confirm_install", token, actor))
        if self.confirm_error is not None:
            raise self.confirm_error
        return _installed()

    def prepare_update(self, identity, *, actor, cancelled):
        self.calls.append(("prepare_update", identity, actor, cancelled))
        return _update_review()

    def confirm_update(self, token, *, actor):
        self.calls.append(("confirm_update", token, actor))
        return _installed(version="2.0.0")

    def prepare_remove(self, identity, *, actor):
        self.calls.append(("prepare_remove", identity, actor))
        return _remove_review()

    def confirm_remove(self, token, *, actor):
        self.calls.append(("confirm_remove", token, actor))
        return _installed()

    def review_trust(self, identity, *, actor, workflow_name=None):
        self.calls.append(("review_trust", identity, actor, workflow_name))
        return _trust_review()

    def grant_trust(self, token, *, actor):
        self.calls.append(("grant_trust", token, actor))
        return {"laptop-diagnostic": "trusted"}

    def revoke_trust(self, identity, *, workflow_name=None):
        self.calls.append(("revoke_trust", identity, workflow_name))
        return 1


@pytest.fixture
def api(tmp_path):
    service = _FakeService()
    home = [tmp_path / "support"]
    profile = ["support"]
    context = WorkflowMarketplaceApiContext(
        service_factory=lambda _home, _profile: service,
        home_resolver=lambda: home[0],
        profile_resolver=lambda _home: profile[0],
        operation_limits={
            "max_workers": 2,
            "max_in_flight": 8,
            "max_terminal": 16,
        },
    )
    app = FastAPI()
    app.include_router(
        create_marketplace_router(_verified_operator, context=context),
        prefix="/api/plugins/workflow",
    )
    with TestClient(app) as client:
        yield client, service, context, home, profile
    context.close()


def _headers(authority: str = "admin", *, actor: str | None = None) -> dict[str, str]:
    headers = {"X-Test-Authority": authority}
    if actor is not None:
        headers["X-Test-Actor"] = actor
    return headers


def _wait_operation(
    client: TestClient,
    operation_id: str,
    *,
    authority: str = "admin",
    actor: str | None = None,
):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/plugins/workflow/marketplace/operations/{operation_id}",
            headers=_headers(authority, actor=actor),
        )
        assert response.status_code == 200, response.text
        value = response.json()
        if value["state"] in {"succeeded", "failed", "cancelled"}:
            return value
        time.sleep(0.01)
    raise AssertionError("operation did not become terminal")


def test_marketplace_reads_require_read_and_mutations_require_admin(api) -> None:
    client, _service, _context, _home, _profile = api

    assert (
        client.get(
            "/api/plugins/workflow/marketplace/packages", headers=_headers("none")
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/plugins/workflow/marketplace/packages", headers=_headers("read")
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/plugins/workflow/marketplace/install/prepare",
            json={"identifier": "company/laptop-support"},
            headers=_headers("write"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/plugins/workflow/marketplace/install/prepare",
            json={"identifier": "company/laptop-support"},
            headers=_headers("admin"),
        ).status_code
        == 202
    )


def test_source_crud_is_strict_and_returns_credential_free_models(api) -> None:
    client, _service, _context, _home, _profile = api
    base = "/api/plugins/workflow/marketplace/sources"

    invalid = client.post(
        base,
        json={
            "name": "company",
            "repositoryUrl": "https://example.test/repo.git",
            "enabled": True,
            "unexpected": "field",
        },
        headers=_headers(),
    )
    added = client.post(
        base,
        json={
            "name": "company",
            "repositoryUrl": "https://example.test/repo.git",
            "enabled": True,
        },
        headers=_headers(),
    )
    updated = client.put(
        f"{base}/company",
        json={
            "repositoryUrl": "https://example.test/updated.git",
            "ref": "stable",
            "enabled": True,
        },
        headers=_headers(),
    )
    disabled = client.post(
        f"{base}/company/enabled",
        json={"enabled": False},
        headers=_headers(),
    )
    listed = client.get(base, headers=_headers("read"))
    removed = client.delete(f"{base}/company", headers=_headers())

    assert invalid.status_code == 422
    assert invalid.json()["detail"] == {"code": "marketplace_request_invalid"}
    assert added.status_code == 201
    assert added.json()["source"]["repository_url"] == ("https://example.test/repo.git")
    assert updated.status_code == 200
    assert updated.json()["status"] == "updated"
    assert updated.json()["source"]["ref"] == "stable"
    assert disabled.status_code == 200
    assert disabled.json()["source"]["enabled"] is False
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["sources"]] == ["company"]
    assert removed.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": 1},
        {"enabled": "false"},
        {"enabled": False, "extra": True},
    ],
)
def test_source_enable_rejects_coercion_and_unknown_fields(api, body) -> None:
    client, service, _context, _home, _profile = api
    service.sources.append(
        WorkflowMarketplaceSource(
            name="company", repositoryUrl="https://example.test/repo.git"
        )
    )

    response = client.post(
        "/api/plugins/workflow/marketplace/sources/company/enabled",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "marketplace_request_invalid"}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"enabled":true,"enabled":false}',
        b'{"enabled":NaN}',
        json.dumps({"unexpected": "x" * (65 * 1024)}).encode(),
    ],
)
def test_request_json_rejects_duplicates_nonfinite_values_and_oversize(
    api, raw
) -> None:
    client, _service, _context, _home, _profile = api

    response = client.post(
        "/api/plugins/workflow/marketplace/sources/company/enabled",
        content=raw,
        headers={**_headers(), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "marketplace_request_invalid"}
    assert "unexpected" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"confirmationToken": True},
        {"confirmationToken": "too-short"},
        {"confirmationToken": "A" * 40, "unexpected": "secret"},
    ],
)
def test_confirmation_requests_are_strict_and_do_not_echo_rejected_values(
    api, body
) -> None:
    client, _service, _context, _home, _profile = api

    response = client.post(
        "/api/plugins/workflow/marketplace/install/confirm",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "marketplace_request_invalid"}
    assert "too-short" not in response.text
    assert "secret" not in response.text


def test_package_search_has_bounded_strict_pagination(api) -> None:
    client, service, _context, _home, _profile = api

    response = client.get(
        "/api/plugins/workflow/marketplace/packages?q=laptop&offset=0&limit=20",
        headers=_headers("read"),
    )
    bad_limit = client.get(
        "/api/plugins/workflow/marketplace/packages?q=laptop&limit=true",
        headers=_headers("read"),
    )
    unknown = client.get(
        "/api/plugins/workflow/marketplace/packages?q=laptop&surprise=yes",
        headers=_headers("read"),
    )
    repeated = client.get(
        "/api/plugins/workflow/marketplace/packages?q=one&q=two",
        headers=_headers("read"),
    )
    long_query = client.get(
        f"/api/plugins/workflow/marketplace/packages?q={'x' * 257}",
        headers=_headers("read"),
    )
    final_bounded_page = client.get(
        "/api/plugins/workflow/marketplace/packages?offset=199&limit=1",
        headers=_headers("read"),
    )

    assert response.status_code == 200
    assert response.json()["profile"] == "support"
    assert response.json()["items"][0]["identifier"] == ("company/laptop-support")
    assert ("search", "laptop", None, 21) in service.calls
    assert bad_limit.status_code == 422
    assert unknown.status_code == 422
    assert repeated.status_code == 422
    assert long_query.status_code == 422
    assert final_bounded_page.status_code == 200


def test_package_detail_installed_and_update_check_have_strict_success_models(
    api,
) -> None:
    client, service, _context, _home, _profile = api

    detail_started = client.get(
        "/api/plugins/workflow/marketplace/packages/company/laptop-support",
        headers=_headers("read"),
    )
    detail = _wait_operation(client, detail_started.json()["id"], authority="read")
    installed = client.get(
        "/api/plugins/workflow/marketplace/installed", headers=_headers("read")
    )
    checks_started = client.post(
        "/api/plugins/workflow/marketplace/updates/check",
        json={},
        headers=_headers(),
    )
    checks = _wait_operation(client, checks_started.json()["id"])

    assert detail_started.status_code == 202
    assert detail["state"] == "succeeded"
    assert detail["result"]["type"] == "package_detail"
    assert detail["result"]["value"]["verified"] is True
    assert installed.status_code == 200
    assert installed.json()["packages"][0]["identity"] == {
        "source_key": "company",
        "package_id": "laptop-support",
    }
    assert checks_started.status_code == 202
    assert checks["state"] == "succeeded"
    assert checks["result"]["type"] == "update_checks"
    assert checks["result"]["value"]["checks"][0]["status"] == ("update_available")
    assert any(call[0] == "inspect" for call in service.calls)


def test_update_check_rejects_a_service_projection_with_unknown_fields(api) -> None:
    client, service, _context, _home, _profile = api

    service.check_updates = lambda identity=None, *, cancelled: (
        {
            "identity": {
                "sourceKey": "company",
                "packageId": "laptop-support",
            },
            "status": "current",
            "installedVersion": "1.0.0",
            "candidateVersion": "1.0.0",
            "unexpected": "must-not-cross-the-api-boundary",
        },
    )
    started = client.post(
        "/api/plugins/workflow/marketplace/updates/check",
        json={},
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    assert terminal["state"] == "failed"
    assert terminal["result"] is None
    assert terminal["error"] == {
        "code": "marketplace_operation_failed",
        "message": "Workflow marketplace operation failed.",
    }
    assert "unexpected" not in json.dumps(terminal)


def test_prepare_returns_token_only_inside_actor_scoped_prepared_result(api) -> None:
    client, service, _context, _home, _profile = api

    started = client.post(
        "/api/plugins/workflow/marketplace/install/prepare",
        json={"identifier": "company/laptop-support"},
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    assert started.status_code == 202
    assert "confirmation" not in json.dumps(started.json()).casefold()
    assert terminal["state"] == "succeeded"
    assert terminal["result"]["type"] == "install_review"
    assert terminal["result"]["value"]["confirmation_token"] == _TOKEN
    call = next(item for item in service.calls if item[0] == "prepare_install")
    assert call[2].startswith("marketplace:")
    assert "raw-user-identity" not in call[2]
    assert callable(call[3])


def test_confirmation_failure_never_echoes_the_raw_token(api) -> None:
    client, service, _context, _home, _profile = api
    secret_token = "A" * 40
    service.confirm_error = WorkflowMarketplaceError(
        "confirmation_token_invalid",
        f"expired token {secret_token} at /tmp/.quarantine",
    )

    started = client.post(
        "/api/plugins/workflow/marketplace/install/confirm",
        json={"confirmationToken": secret_token},
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    encoded = json.dumps(terminal, sort_keys=True)
    assert terminal["state"] == "failed"
    assert terminal["error"]["code"] == "confirmation_token_invalid"
    assert secret_token not in encoded
    assert "/tmp" not in encoded


@pytest.mark.parametrize(
    ("path", "body", "result_type", "call_name"),
    [
        (
            "/install/confirm",
            {"confirmationToken": _TOKEN},
            "installed_package",
            "confirm_install",
        ),
        (
            "/update/prepare",
            {"sourceKey": "company", "packageId": "laptop-support"},
            "update_review",
            "prepare_update",
        ),
        (
            "/update/confirm",
            {"confirmationToken": _TOKEN},
            "updated_package",
            "confirm_update",
        ),
        (
            "/remove/prepare",
            {"sourceKey": "company", "packageId": "laptop-support"},
            "remove_review",
            "prepare_remove",
        ),
        (
            "/remove/confirm",
            {"confirmationToken": _TOKEN},
            "removed_package",
            "confirm_remove",
        ),
        (
            "/trust/review",
            {
                "identity": {
                    "sourceKey": "company",
                    "packageId": "laptop-support",
                },
                "workflowName": "laptop-diagnostic",
            },
            "trust_review",
            "review_trust",
        ),
        (
            "/trust/grant",
            {"confirmationToken": _TOKEN},
            "trust_grant",
            "grant_trust",
        ),
        (
            "/trust/revoke",
            {
                "identity": {
                    "sourceKey": "company",
                    "packageId": "laptop-support",
                }
            },
            "trust_revoke",
            "revoke_trust",
        ),
    ],
)
def test_lifecycle_route_groups_are_thin_background_adapters(
    api, path, body, result_type, call_name
) -> None:
    client, service, _context, _home, _profile = api

    started = client.post(
        f"/api/plugins/workflow/marketplace{path}",
        json=body,
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    assert started.status_code == 202
    assert terminal["state"] == "succeeded"
    assert terminal["result"]["type"] == result_type
    assert any(call[0] == call_name for call in service.calls)


def test_operation_lookup_and_cancel_do_not_cross_actor_or_profile_scope(api) -> None:
    client, _service, _context, home, profile = api
    started = client.post(
        "/api/plugins/workflow/marketplace/sources/company/refresh",
        headers=_headers(),
    )
    operation_id = started.json()["id"]
    _wait_operation(client, operation_id)

    profile[0] = "other"
    home[0] = home[0].parent / "other"
    other_profile = client.get(
        f"/api/plugins/workflow/marketplace/operations/{operation_id}",
        headers=_headers(),
    )
    profile[0] = "support"
    home[0] = home[0].parent / "support"

    assert other_profile.status_code == 404
    assert other_profile.json()["detail"] == {"code": "marketplace_operation_not_found"}


def test_operation_lookup_and_cancel_are_indistinguishable_across_actors(api) -> None:
    client, _service, _context, _home, _profile = api
    started = client.post(
        "/api/plugins/workflow/marketplace/sources/company/refresh",
        headers=_headers(actor="operator-a"),
    )
    operation_id = started.json()["id"]
    _wait_operation(client, operation_id, actor="operator-a")

    lookup = client.get(
        f"/api/plugins/workflow/marketplace/operations/{operation_id}",
        headers=_headers(actor="operator-b"),
    )
    cancel = client.post(
        f"/api/plugins/workflow/marketplace/operations/{operation_id}/cancel",
        headers=_headers(actor="operator-b"),
    )
    listed = client.get(
        "/api/plugins/workflow/marketplace/operations",
        headers=_headers("read", actor="operator-b"),
    )

    for response in (lookup, cancel):
        assert response.status_code == 404
        assert response.json()["detail"] == {"code": "marketplace_operation_not_found"}
    assert listed.status_code == 200
    assert listed.json()["operations"] == []


def test_cancel_during_refresh_git_phase_is_result_free(api) -> None:
    client, service, _context, _home, _profile = api
    entered = threading.Event()

    def blocking_refresh(name, *, cancelled):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not cancelled():
            time.sleep(0.005)
        return SourceRefreshResult(
            source_name=name,
            repository_url="https://example.test/repo.git",
            state="cancelled",
            resolved_commit=None,
            verified_at=None,
            package_count=0,
        )

    service.refresh_source = blocking_refresh
    started = client.post(
        "/api/plugins/workflow/marketplace/sources/company/refresh",
        headers=_headers(),
    )
    assert entered.wait(timeout=2)

    cancelled = client.post(
        f"/api/plugins/workflow/marketplace/operations/{started.json()['id']}/cancel",
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    assert cancelled.status_code == 200
    assert terminal["state"] == "cancelled"
    assert terminal["result"] is None


def test_refresh_auth_failure_projection_redacts_credentials_and_local_paths(
    api,
) -> None:
    client, service, _context, _home, _profile = api

    def failed_refresh(name, *, cancelled):
        return SourceRefreshResult(
            source_name=name,
            repository_url="https://example.test/repo.git",
            state="authentication-failed",
            resolved_commit=None,
            verified_at=None,
            package_count=0,
            diagnostic_code="source_authentication_failed",
            message=(
                "https://user:secret@example.test/private "
                "/private/tmp/marketplace/.staging access_token=secret"
            ),
        )

    service.refresh_source = failed_refresh
    started = client.post(
        "/api/plugins/workflow/marketplace/sources/company/refresh",
        headers=_headers(),
    )
    terminal = _wait_operation(client, started.json()["id"])

    encoded = json.dumps(terminal, sort_keys=True)
    assert terminal["state"] == "succeeded"
    assert terminal["result"]["value"]["state"] == "authentication-failed"
    assert "secret" not in encoded
    assert "/private/tmp" not in encoded


def test_prepare_reports_progress_and_enforces_same_profile_single_flight(api) -> None:
    client, service, _context, _home, _profile = api
    entered = threading.Event()
    release = threading.Event()

    def blocking_prepare(request, *, actor, cancelled):
        entered.set()
        release.wait(timeout=5)
        return _install_review()

    service.prepare_install = blocking_prepare
    first = client.post(
        "/api/plugins/workflow/marketplace/install/prepare",
        json={"identifier": "company/laptop-support"},
        headers=_headers(),
    )
    assert entered.wait(timeout=2)
    running = client.get(
        f"/api/plugins/workflow/marketplace/operations/{first.json()['id']}",
        headers=_headers(),
    )
    conflict = client.post(
        "/api/plugins/workflow/marketplace/install/prepare",
        json={"identifier": "company/laptop-support"},
        headers=_headers(),
    )
    release.set()
    terminal = _wait_operation(client, first.json()["id"])

    assert running.json()["state"] == "running"
    assert running.json()["phase"] == "fetching"
    assert running.json()["progress"] == 10
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"code": "marketplace_operation_conflict"}
    assert terminal["state"] == "succeeded"


def test_background_operation_reservation_capacity_returns_429(api) -> None:
    client, service, _context, _home, _profile = api
    entered = threading.Event()
    release = threading.Event()

    def blocking_prepare(request, *, actor, cancelled):
        entered.set()
        release.wait(timeout=5)
        return _install_review()

    service.prepare_install = blocking_prepare
    started = [
        client.post(
            "/api/plugins/workflow/marketplace/install/prepare",
            json={"identifier": f"company/package-{index}"},
            headers=_headers(),
        )
        for index in range(8)
    ]
    assert entered.wait(timeout=2)
    rejected = client.post(
        "/api/plugins/workflow/marketplace/install/prepare",
        json={"identifier": "company/over-capacity"},
        headers=_headers(),
    )
    release.set()

    assert all(response.status_code == 202 for response in started)
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == {"code": "marketplace_operation_capacity"}


def test_cancel_before_transaction_worker_start_never_enters_confirm(tmp_path) -> None:
    service = _FakeService()
    entered = threading.Event()
    release = threading.Event()

    def blocking_refresh(name, *, cancelled):
        entered.set()
        release.wait(timeout=5)
        return SourceRefreshResult(
            source_name=name,
            repository_url="https://example.test/repo.git",
            state="fresh",
            resolved_commit=_COMMIT,
            verified_at=_NOW,
            package_count=1,
        )

    service.refresh_source = blocking_refresh  # ty: ignore[invalid-assignment]
    context = WorkflowMarketplaceApiContext(
        service_factory=lambda _home, _profile: service,
        home_resolver=lambda: tmp_path / "home",
        profile_resolver=lambda _home: "support",
        operation_limits={
            "max_workers": 1,
            "max_in_flight": 2,
            "max_terminal": 4,
        },
    )
    app = FastAPI()
    app.include_router(
        create_marketplace_router(_verified_operator, context=context),
        prefix="/api/plugins/workflow",
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/plugins/workflow/marketplace/sources/company/refresh",
                headers=_headers(),
            )
            assert entered.wait(timeout=2)
            queued = client.post(
                "/api/plugins/workflow/marketplace/install/confirm",
                json={"confirmationToken": _TOKEN},
                headers=_headers(),
            )
            cancelled = client.post(
                "/api/plugins/workflow/marketplace/operations/"
                f"{queued.json()['id']}/cancel",
                headers=_headers(),
            )
            release.set()
            first_terminal = _wait_operation(client, first.json()["id"])

        assert queued.status_code == 202
        assert cancelled.json()["state"] == "cancelled"
        assert first_terminal["state"] == "succeeded"
        assert not any(call[0] == "confirm_install" for call in service.calls)
    finally:
        release.set()
        context.close()


def test_cancel_during_atomic_swap_reports_committed_success(api) -> None:
    client, service, _context, _home, _profile = api
    entered = threading.Event()
    release = threading.Event()

    def blocking_confirm(token, *, actor):
        entered.set()
        release.wait(timeout=5)
        return _installed()

    service.confirm_install = blocking_confirm
    started = client.post(
        "/api/plugins/workflow/marketplace/install/confirm",
        json={"confirmationToken": _TOKEN},
        headers=_headers(),
    )
    assert entered.wait(timeout=2)
    during = client.post(
        f"/api/plugins/workflow/marketplace/operations/{started.json()['id']}/cancel",
        headers=_headers(),
    )
    release.set()
    terminal = _wait_operation(client, started.json()["id"])

    assert during.json()["state"] == "running"
    assert during.json()["phase"] == "committing"
    assert terminal["state"] == "succeeded"
    assert terminal["result"]["type"] == "installed_package"


def test_sync_service_errors_use_stable_redacted_envelopes(api) -> None:
    client, service, _context, _home, _profile = api

    def fail(_source):
        raise WorkflowMarketplaceError(
            "source_authentication_failed",
            "https://user:secret@example.test/private access_token=secret",
        )

    service.add_source = fail
    response = client.post(
        "/api/plugins/workflow/marketplace/sources",
        json={
            "name": "company",
            "repositoryUrl": "https://example.test/repo.git",
            "enabled": True,
        },
        headers=_headers(),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "source_authentication_failed",
        "message": "Workflow marketplace request failed.",
    }
    assert "secret" not in response.text


def test_unexpected_sync_exceptions_use_a_stable_redacted_json_envelope(api) -> None:
    client, service, _context, _home, _profile = api

    def fail():
        raise RuntimeError(
            "https://user:secret@example.test/private /tmp/.staging token=secret"
        )

    service.list_sources = fail
    response = client.get(
        "/api/plugins/workflow/marketplace/sources",
        headers=_headers("read"),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "marketplace_internal_error",
        "message": "Workflow marketplace request failed.",
    }
    assert "secret" not in response.text
    assert "/tmp" not in response.text


def test_real_service_source_crud_and_empty_search_follow_active_home(tmp_path) -> None:
    context = WorkflowMarketplaceApiContext(
        home_resolver=lambda: tmp_path / "profile",
        profile_resolver=lambda _home: "support",
    )
    app = FastAPI()
    app.include_router(
        create_marketplace_router(_verified_operator, context=context),
        prefix="/api/plugins/workflow",
    )
    try:
        with TestClient(app) as client:
            added = client.post(
                "/api/plugins/workflow/marketplace/sources",
                json={
                    "name": "company",
                    "repositoryUrl": "https://example.test/team/workflows.git",
                    "ref": "main",
                    "enabled": True,
                },
                headers=_headers(),
            )
            sources = client.get(
                "/api/plugins/workflow/marketplace/sources",
                headers=_headers("read"),
            )
            search = client.get(
                "/api/plugins/workflow/marketplace/packages?q=support",
                headers=_headers("read"),
            )

        assert added.status_code == 201
        assert sources.json()["sources"][0]["name"] == "company"
        assert search.status_code == 200
        assert search.json()["items"] == []
        persisted = tmp_path / "profile" / "marketplace" / "workflows" / "sources.json"
        assert persisted.is_file()
        assert isinstance(
            context.service_for_current_profile(), WorkflowMarketplaceService
        )
    finally:
        context.close()
