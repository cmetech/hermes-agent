from __future__ import annotations

import importlib.util
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
import sys
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from hermes_cli.dashboard_auth.base import TokenPrincipal
import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
import plugins.workflow.api_admission as api_admission_module
from plugins.workflow.api_admission import (
    ApiAdmissionAuthority,
    ApiAdmissionError,
    start_api_run,
)
from plugins.workflow.compat import (
    CompatibilityFinding,
    CompatibilityLevel,
    CompatibilityReport,
    assess_compatibility,
)
from plugins.workflow.coordinator_store import (
    CoordinatorHealthSnapshotError,
    CoordinatorIdentity,
    CoordinatorStore,
)
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
import plugins.workflow.showcase as showcase_module
from plugins.workflow.showcase import run_showcase
from plugins.workflow.trust import (
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)
from plugins.workflow.runtime import (
    WorkflowApiLimits,
    WorkflowApiRuntime,
    WorkflowStoreRegistry,
)


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_dashboard_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _router():
    return _module().router


def _app(router, *, session=None, token=None, local_admin=None):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        if local_admin is True or (
            session is None and token is None and local_admin is None
        ):
            request.state.local_admin_authenticated = True
        elif session is not None:
            request.state.session = session
        elif token is not None:
            request.state.token_principal = token
            request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _start(store, package, key, *, scope=None):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
            operator_scope=scope,
        ),
        immutable_snapshot=prepared,
    )


def _trusted_catalog_workflow(home, workflow_writer, *, name, nodes=None, **options):
    package = load_workflow(
        workflow_writer(
            home / "workflows",
            name=name,
            nodes=nodes,
            filename=f"{name}.yaml",
            **options,
        )
    )
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        digest.sha256,
        actor="test-operator",
        risk_digest=risk.risk_digest,
    )
    return package


def _healthy_coordinator(store):
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="api-test",
            host_kind="web",
            host_instance_id="api-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader


@contextmanager
def _test_bundle_path(root: Path):
    yield root.resolve()


def _assert_no_admission_residue(store: RunStore) -> None:
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


class _LoopRunner:
    def run(self, request, **_kwargs):
        return PluginAgentRunResult(
            final_response="draft",
            session_id="api-loop-session",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def test_hidden_manifest_is_api_only():
    root = Path(__file__).parents[3] / "plugins/workflow/dashboard"
    assert '"hidden": true' in (root / "manifest.json").read_text()
    assert (root / "dist/index.js").read_text().strip() == "void 0;"


def test_router_rejects_requests_without_verified_authentication() -> None:
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/workflow")

    response = TestClient(app).get("/api/plugins/workflow/runs")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


def test_post_runs_api_admission_requires_write_and_server_derived_provenance(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="api-admission")
    store = RunStore(home)
    _healthy_coordinator(store)
    module = _module()
    reader = TokenPrincipal(
        principal="reader", provider="test", scopes=("workflow:read",)
    )
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )
    body = {
        "workflow": "api-admission",
        "values": {"subject": "safe"},
        "idempotency_key": "request-1",
        "concurrency_policy": "queue",
    }

    denied = TestClient(_app(module.router, token=reader)).post(
        "/api/plugins/workflow/runs", json=body
    )
    missing_key = TestClient(_app(module.router, token=writer)).post(
        "/api/plugins/workflow/runs",
        json={key: value for key, value in body.items() if key != "idempotency_key"},
    )
    admitted = TestClient(_app(module.router, token=writer)).post(
        "/api/plugins/workflow/runs",
        json=body,
        headers={
            "X-Hermes-Workflow-Source": "desktop",
            "X-Hermes-Principal": "forged-admin",
            "X-Hermes-Source-Instance": "forged-instance",
            "X-Hermes-Channel": "forged-channel",
            "X-Hermes-Return-Route": "forged-route",
        },
    )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "workflow_write_required"
    assert missing_key.status_code == 422
    assert admitted.status_code == 202
    assert admitted.json()["ok"] is True
    result = admitted.json()["result"]
    assert result["admission_disposition"] == "created"
    run = store.get_run_status(result["run_id"], operator_scope="service:test:writer")
    assert run["trigger"] == "api"
    assert run["execution_mode"] == "background"
    assert run["provenance"]["source"] == "api"
    assert run["provenance"]["assurance"] == "verified_adapter"
    assert run["provenance"]["actor_id"] == "service:test:writer"
    assert run["provenance"]["source_instance"] == "api:token:test"
    assert run["provenance"]["return_route"] is None
    assert "forged" not in str(run["provenance"])


def test_post_runs_authenticated_session_middleware_records_desktop_source(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="desktop-admission")
    store = RunStore(home)
    _healthy_coordinator(store)
    session = SimpleNamespace(
        provider="test",
        org_id="org",
        user_id="desktop-user",
    )
    scope = "dashboard:test:org:desktop-user"
    module = _module()
    client = TestClient(_app(module.router, session=session))

    admitted = client.post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "desktop-admission",
            "values": {},
            "idempotency_key": "desktop-request",
            "concurrency_policy": "queue",
        },
    )

    assert admitted.status_code == 202
    run_id = admitted.json()["result"]["run_id"]
    run = store.get_run_status(run_id, operator_scope=scope)
    assert run["trigger"] == "desktop"
    assert run["execution_mode"] == "background"
    assert run["provenance"]["source"] == "desktop"
    assert run["provenance"]["assurance"] == "verified_adapter"
    assert run["provenance"]["actor_id"] == scope
    assert run["provenance"]["source_instance"] == "api:session:test"


def test_post_runs_admits_verified_showcase_in_background_and_joins_stably(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    session = SimpleNamespace(
        provider="test",
        org_id="org",
        user_id="desktop-user",
    )
    scope = "dashboard:test:org:desktop-user"
    module = _module()
    client = TestClient(_app(module.router, session=session))
    monkeypatch.setattr(
        RunScheduler,
        "advance",
        lambda *_args, **_kwargs: pytest.fail("request path executed a workflow"),
    )
    body = {
        "workflow": "approval-gate",
        "catalog_source": "showcase",
        "values": {},
        "idempotency_key": "showcase-stable-request",
        "concurrency_policy": "allow",
    }

    first = client.post(
        "/api/plugins/workflow/runs",
        json=body,
        headers={
            "X-Hermes-Workflow-Source": "api",
            "X-Hermes-Principal": "forged-admin",
        },
    )
    duplicate = client.post("/api/plugins/workflow/runs", json=body)

    assert first.status_code == duplicate.status_code == 202
    first_result = first.json()["result"]
    duplicate_result = duplicate.json()["result"]
    assert first_result["admission_disposition"] == "created"
    assert duplicate_result["admission_disposition"] == "existing"
    assert first_result["run_id"] == duplicate_result["run_id"]
    first_status = store.get_run_status(first_result["run_id"], operator_scope=scope)
    duplicate_status = store.get_run_status(
        duplicate_result["run_id"], operator_scope=scope
    )
    assert first_status["trigger"] == "desktop"
    assert first_status["execution_mode"] == "background"
    assert first_status["provenance"]["source"] == "desktop"
    assert first_status["provenance"]["assurance"] == "verified_adapter"
    assert first_status["provenance"]["actor_id"] == scope
    assert "forged" not in str(first_status["provenance"])
    assert first_status["run_metadata"]["showcase_id"] == "approval-gate"
    assert first_status["run_metadata"]["showcase_provenance"] == "verified_bundled"
    assert len(first_status["run_metadata"]["bundle_digest"]) == 64
    assert len(first_status["run_metadata"]["risk_digest"]) == 64
    assert first_status["nodes"]["operator-approval"]["state"] == "ready"
    assert RunStore._start_digest_from_projection(first_status) == (
        RunStore._start_digest_from_projection(duplicate_status)
    )
    assert first_status["run_metadata"]["bundle_digest"] == (
        duplicate_status["run_metadata"]["bundle_digest"]
    )
    assert first_status["run_metadata"]["risk_digest"] == (
        duplicate_status["run_metadata"]["risk_digest"]
    )


@pytest.mark.parametrize(
    ("showcase_id", "status_code", "reason"),
    [
        ("laptop-diagnostic", 422, "workflow_inputs_unsupported"),
        ("ai-extensions", 409, "workflow_showcase_cli_required"),
        ("scheduling", 409, "workflow_showcase_cli_required"),
    ],
)
def test_post_runs_rederives_showcase_run_support_without_persistence(
    tmp_path, monkeypatch, showcase_id, status_code, reason
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": showcase_id,
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": f"unsupported-{showcase_id}",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {"code": reason, "retryable": False}
    }
    _assert_no_admission_residue(store)


def test_post_runs_rejects_environment_incompatible_showcase_before_persistence(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)

    def incompatible(_package):
        return CompatibilityReport(
            level=CompatibilityLevel.UNSUPPORTED,
            findings=(
                CompatibilityFinding(
                    path="environment.optional_service",
                    level=CompatibilityLevel.UNSUPPORTED,
                    message="optional service unavailable",
                    blocking=True,
                ),
            ),
            runnable=False,
        )

    monkeypatch.setattr(api_admission_module, "assess_compatibility", incompatible)
    monkeypatch.setattr(showcase_module, "assess_compatibility", incompatible)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "approval-gate",
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": "incompatible-showcase",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "workflow_compatibility_blocked",
            "retryable": False,
        }
    }
    _assert_no_admission_residue(store)


def test_post_runs_showcase_enforces_execution_preflight_without_persistence(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)

    def fail_preflight(*_args, **_kwargs):
        from plugins.workflow.trust import WorkflowTrustError

        raise WorkflowTrustError("showcase preflight rejected")

    monkeypatch.setattr(api_admission_module, "preflight_execution", fail_preflight)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "approval-gate",
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": "showcase-preflight",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "workflow_preflight_failed", "retryable": False}
    }
    _assert_no_admission_residue(store)


def test_post_runs_showcase_force_reverification_rejects_cached_bundle_mutation(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    showcase_module.load_verified_showcase_packages(read_budget=budget)
    workflow = (
        copied
        / "packages"
        / "approval-gate"
        / "workflows"
        / "approval-gate.yaml"
    )
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "approval-gate",
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": "mutated-showcase",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "workflow_showcase_verification_failed",
            "retryable": False,
        }
    }
    _assert_no_admission_residue(store)


def test_post_runs_showcase_omission_and_unhealthy_coordinator_fail_without_residue(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    client = TestClient(_app(_router()))
    body = {
        "workflow": "approval-gate",
        "values": {},
        "idempotency_key": "showcase-source-required",
        "concurrency_policy": "queue",
    }

    omitted = client.post("/api/plugins/workflow/runs", json=body)
    unavailable = client.post(
        "/api/plugins/workflow/runs",
        json={**body, "catalog_source": "showcase", "idempotency_key": "unhealthy"},
    )

    assert omitted.status_code == 404
    assert omitted.json()["detail"]["code"] == "workflow_not_found"
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "coordinator_unavailable"
    _assert_no_admission_residue(store)


def test_post_runs_same_name_user_and_showcase_target_exact_sources(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="approval-gate")
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    client = TestClient(_app(_router()))
    base = {
        "workflow": "approval-gate",
        "values": {},
        "concurrency_policy": "allow",
    }

    user = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "idempotency_key": "colliding-user"},
    )
    showcase = client.post(
        "/api/plugins/workflow/runs",
        json={
            **base,
            "catalog_source": "showcase",
            "idempotency_key": "colliding-showcase",
        },
    )

    assert user.status_code == showcase.status_code == 202
    user_status = store.get_run_status(user.json()["result"]["run_id"])
    showcase_status = store.get_run_status(showcase.json()["result"]["run_id"])
    assert user_status["run_metadata"] == {}
    assert showcase_status["run_metadata"]["showcase_id"] == "approval-gate"
    assert user_status["concurrency_key"] == "approval-gate"
    assert showcase_status["concurrency_key"] == "showcase:approval-gate"


def test_api_admission_authority_preserves_legacy_positional_return_route() -> None:
    authority = ApiAdmissionAuthority(
        "verified-principal",
        "api:verified-principal",
        None,
        "api:token:test",
        "verified_adapter",
        "opaque-return-route",
    )

    assert authority.return_route == "opaque-return-route"
    assert authority.trigger_source == "api"


@pytest.mark.parametrize("source", ["chat", "background_agent"])
def test_api_admission_rejects_non_rest_sources_without_persistence(
    tmp_path, monkeypatch, workflow_writer, source
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="invalid-api-source")
    store = RunStore(home)
    _healthy_coordinator(store)
    authority = ApiAdmissionAuthority(
        principal="service:test:writer",
        namespace="api:service:test:writer",
        operator_scope="service:test:writer",
        source_instance="api:token:test",
        assurance="verified_adapter",
        trigger_source=source,
    )

    with pytest.raises(
        ValueError,
        match="authenticated API source must be api or desktop",
    ):
        start_api_run(
            store,
            hermes_home=home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="invalid-api-source",
            values={},
            idempotency_key=f"invalid-{source}",
            concurrency_policy="queue",
            authority=authority,
        )

    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


def test_post_runs_api_admission_requires_healthy_coordinator_without_run(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="api-no-coordinator")
    store = RunStore(home)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    response = TestClient(_app(_router(), token=writer)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-no-coordinator",
            "values": {},
            "idempotency_key": "no-coordinator",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "coordinator_unavailable"
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


@pytest.mark.parametrize(
    "health_error",
    [
        CoordinatorHealthSnapshotError("snapshot unavailable"),
        sqlite3.OperationalError("database unavailable"),
    ],
)
def test_post_runs_api_admission_wraps_coordinator_health_errors_without_residue(
    tmp_path, monkeypatch, workflow_writer, health_error
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="api-health-error")
    store = RunStore(home)
    _healthy_coordinator(store)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    def unavailable_health(_self, *, now):
        del now
        raise health_error

    monkeypatch.setattr(CoordinatorStore, "health", unavailable_health)

    response = TestClient(
        _app(_router(), token=writer), raise_server_exceptions=False
    ).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-health-error",
            "values": {},
            "idempotency_key": "health-error",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "coordinator_unavailable",
        "retryable": True,
    }
    _assert_no_admission_residue(store)


def test_direct_api_admission_rejects_incompatible_workflow_before_persistence(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="api-incompatible-direct",
        nodes=[
            {
                "id": "shell",
                "bash": "true",
                "allowed_tools": ["Read"],
            }
        ],
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    original_assess = api_admission_module.assess_compatibility
    assessments = 0

    def counted_assess(package):
        nonlocal assessments
        assessments += 1
        return original_assess(package)

    monkeypatch.setattr(api_admission_module, "assess_compatibility", counted_assess)

    with pytest.raises(ApiAdmissionError) as caught:
        start_api_run(
            store,
            hermes_home=home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="api-incompatible-direct",
            values={},
            idempotency_key="incompatible-direct",
            concurrency_policy="queue",
            authority=ApiAdmissionAuthority(
                principal="desktop:test",
                namespace="desktop:test",
                operator_scope=None,
                source_instance="api:session:test",
                assurance="verified_adapter",
                trigger_source="desktop",
            ),
        )

    assert caught.value.code == "workflow_compatibility_blocked"
    assert caught.value.status_code == 409
    assert caught.value.retryable is False
    assert assessments == 1
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


def test_post_runs_rejects_incompatible_workflow_without_run_or_execution(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="api-incompatible-http",
        nodes=[
            {
                "id": "shell",
                "bash": "true",
                "allowed_tools": ["Read"],
            }
        ],
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    response = TestClient(_app(_router(), token=writer)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-incompatible-http",
            "values": {},
            "idempotency_key": "incompatible-http",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "workflow_compatibility_blocked",
            "retryable": False,
        }
    }
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


def test_post_runs_api_admission_joins_identical_and_conflicts_on_changed_input(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="api-idempotent")
    store = RunStore(home)
    _healthy_coordinator(store)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )
    client = TestClient(_app(_router(), token=writer))
    body = {
        "workflow": "api-idempotent",
        "values": {"subject": "same"},
        "idempotency_key": "stable-request",
        "concurrency_policy": "allow",
    }

    first = client.post("/api/plugins/workflow/runs", json=body)
    duplicate = client.post("/api/plugins/workflow/runs", json=body)
    conflict = client.post(
        "/api/plugins/workflow/runs",
        json={**body, "values": {"subject": "changed"}},
    )

    assert first.status_code == duplicate.status_code == 202
    assert first.json()["result"]["run_id"] == duplicate.json()["result"]["run_id"]
    assert first.json()["result"]["admission_disposition"] == "created"
    assert duplicate.json()["result"]["admission_disposition"] == "existing"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_api_admission_mutations_persist_authenticated_actor_and_channel(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    scope = "service:test:writer"
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    approval = load_workflow(
        workflow_writer(
            tmp_path / "approval",
            name="api-approval",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    store = RunStore(home)
    approval_run = _start(store, approval, "api-approval", scope=scope)
    approval_paused = RunScheduler(store).advance(approval_run.run_id)
    approval_interaction = approval_paused["nodes"]["review"]["pending_interaction"][
        "interaction_id"
    ]
    client = TestClient(_app(_router(), token=writer))
    approved = client.post(
        f"/api/plugins/workflow/runs/{approval_run.run_id}/approve",
        json={
            "expected_version": approval_paused["state_version"],
            "interaction_id": approval_interaction,
            "comment": "approved",
        },
        headers={"X-Hermes-Channel": "forged-channel"},
    )

    rejection_run = _start(store, approval, "api-rejection", scope=scope)
    rejection_paused = RunScheduler(store).advance(rejection_run.run_id)
    rejection_interaction = rejection_paused["nodes"]["review"]["pending_interaction"][
        "interaction_id"
    ]
    rejected = client.post(
        f"/api/plugins/workflow/runs/{rejection_run.run_id}/reject",
        json={
            "expected_version": rejection_paused["state_version"],
            "interaction_id": rejection_interaction,
            "reason": "rejected",
        },
    )

    loop = load_workflow(
        workflow_writer(
            tmp_path / "loop",
            name="api-loop",
            interactive=True,
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Refine",
                        "until": "DONE",
                        "max_iterations": 2,
                        "interactive": True,
                        "gate_message": "Review",
                    },
                }
            ],
        )
    )
    loop_run = _start(store, loop, "api-loop", scope=scope)
    loop_paused = RunScheduler(store, agent_runner=_LoopRunner()).advance(
        loop_run.run_id
    )
    loop_interaction = loop_paused["nodes"]["iterate"]["pending_interaction"][
        "interaction_id"
    ]
    provided = client.post(
        f"/api/plugins/workflow/runs/{loop_run.run_id}/provide-input",
        json={
            "expected_version": loop_paused["state_version"],
            "interaction_id": loop_interaction,
            "value": "tighten evidence",
        },
    )

    assert approved.status_code == rejected.status_code == provided.status_code == 200
    approval_event = next(
        event
        for event in store.tail_events(approval_run.run_id)
        if event["event_type"] == "interaction_approved"
    )
    input_event = next(
        event
        for event in store.tail_events(loop_run.run_id)
        if event["event_type"] == "loop_input_provided"
    )
    rejection_event = next(
        event
        for event in store.tail_events(rejection_run.run_id)
        if event["event_type"] == "interaction_rejected"
    )
    for event in (approval_event, rejection_event, input_event):
        assert event["payload"]["actor"] == scope
        assert event["payload"]["channel"] == "api:test"
        assert "forged" not in str(event["payload"])


def test_post_runs_api_admission_rejects_unbounded_or_caller_auth_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    client = TestClient(_app(_router()))
    base = {
        "workflow": "bounded",
        "idempotency_key": "bounded",
        "concurrency_policy": "queue",
    }

    oversized = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "values": {"input": "x" * (64 * 1024 + 1)}},
    )
    caller_identity = client.post(
        "/api/plugins/workflow/runs",
        json={
            **base,
            "values": {},
            "principal": "caller-admin",
            "source": "cron",
            "return_route": "caller-route",
        },
    )
    unsafe_names = [
        "foo/bar",
        "foo\\bar",
        "mode\x1b[31m",
        "api_token",
        "CON",
        "nul.txt",
        "COM1",
        "foo:bar",
        "a?b",
        "a*b",
        "<x>",
        "a|b",
        "trailing.",
        "trailing ",
        "😀" * 64,
        "COM¹",
        "COM².txt",
        "com³",
        "LPT¹",
        "lpt².log",
        "LPT³",
    ]
    unsafe_values = [
        client.post(
            "/api/plugins/workflow/runs",
            json={**base, "values": {name: "value"}},
        )
        for name in unsafe_names
    ]
    colliding_values = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "values": {"Mode": "first", "mode": "second"}},
    )

    assert oversized.status_code == 422
    assert caller_identity.status_code == 422
    assert [response.status_code for response in unsafe_values] == [422] * len(
        unsafe_names
    )
    assert colliding_values.status_code == 422


def test_post_runs_applies_catalog_resource_bounds_before_admission(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    commands = home / "commands"
    commands.mkdir(parents=True)
    (commands / "oversized.md").write_bytes(b"x" * (1024 * 1024 + 1))
    oversized = _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="api-oversized-resource",
        nodes=[{"id": "oversized", "command": "oversized"}],
    )
    aggregate_nodes = []
    for index in range(9):
        resource_name = f"aggregate-{index}"
        (commands / f"{resource_name}.md").write_bytes(b"x" * 1024 * 1024)
        aggregate_nodes.append({"id": resource_name, "command": resource_name})
    aggregate = _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="api-aggregate-resource",
        nodes=aggregate_nodes,
    )
    assert compute_package_digest(oversized).sha256
    assert compute_package_digest(aggregate).sha256
    store = RunStore(home)
    _healthy_coordinator(store)
    client = TestClient(_app(_router()))

    responses = [
        client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": workflow,
                "values": {},
                "idempotency_key": f"resource-bound-{index}",
                "concurrency_policy": "queue",
            },
        )
        for index, workflow in enumerate(
            ("api-oversized-resource", "api-aggregate-resource")
        )
    ]

    assert [response.status_code for response in responses] == [503, 503]
    assert [response.json() for response in responses] == [
        {
            "detail": {
                "code": "workflow_catalog_capacity",
                "retryable": True,
            }
        },
        {
            "detail": {
                "code": "workflow_catalog_capacity",
                "retryable": True,
            }
        },
    ]
    assert list(store.runs_root.iterdir()) == []
    assert list(store.staging_root.iterdir()) == []


def test_post_runs_applies_catalog_projection_bounds_before_admission(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="api-projection-capacity",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(513)
        ],
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-projection-capacity",
            "values": {},
            "idempotency_key": "projection-capacity",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert list(store.runs_root.iterdir()) == []
    assert list(store.staging_root.iterdir()) == []


def test_post_runs_discards_snapshot_that_does_not_match_trusted_package(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="api-package-change")
    store = RunStore(home)
    _healthy_coordinator(store)
    original_prepare = RunStore.prepare_run_snapshot

    def mismatched_prepare(self, *args, **kwargs):
        prepared = original_prepare(self, *args, **kwargs)
        return replace(prepared, definition_digest="0" * 64)

    monkeypatch.setattr(RunStore, "prepare_run_snapshot", mismatched_prepare)
    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-package-change",
            "values": {},
            "idempotency_key": "package-change",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "workflow_package_changed", "retryable": False}
    }
    assert list(store.runs_root.iterdir()) == []
    assert list(store.staging_root.iterdir()) == []


def test_catalog_detail_and_admission_agree_after_cross_entry_resource_reads(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    commands = home / "commands"
    commands.mkdir(parents=True)

    def command_nodes(prefix: str, count: int):
        nodes = []
        for index in range(count):
            name = f"{prefix}-{index}"
            (commands / f"{name}.md").write_bytes(b"x" * 1024 * 1024)
            nodes.append({"id": name, "command": name})
        return nodes

    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="cross-entry-alpha",
        nodes=command_nodes("alpha", 7),
    )
    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name="cross-entry-beta",
        nodes=command_nodes("beta", 2),
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    client = TestClient(_app(_router()))

    catalog = client.get("/api/plugins/workflow/workflows")
    detail = client.get("/api/plugins/workflow/workflows/cross-entry-beta")
    admitted = client.post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "cross-entry-beta",
            "values": {},
            "idempotency_key": "cross-entry-beta",
            "concurrency_policy": "queue",
        },
    )

    assert catalog.status_code == 200
    assert catalog.json()["truncated"] is False
    assert [
        item["name"]
        for item in catalog.json()["items"]
        if item.get("source") != "showcase"
    ] == [
        "cross-entry-alpha",
        "cross-entry-beta",
    ]
    assert all("error" not in item for item in catalog.json()["items"])
    assert detail.status_code == 200
    assert admitted.status_code == 202
    assert admitted.json()["result"]["admission_disposition"] == "created"


@pytest.mark.parametrize("mutation", ["delete", "symlink"])
def test_post_runs_snapshots_only_sealed_trusted_resource_bytes(
    tmp_path, monkeypatch, workflow_writer, mutation
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    commands = home / "commands"
    commands.mkdir(parents=True)
    resource = commands / "sealed.md"
    resource.write_text("TRUSTED_RESOURCE", encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_text("UNTRUSTED_EXTERNAL_RESOURCE", encoding="utf-8")
    _trusted_catalog_workflow(
        home,
        workflow_writer,
        name=f"sealed-resource-{mutation}",
        nodes=[{"id": "sealed", "command": "sealed"}],
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    original_read = WorkflowResourceReadBudget.read
    canonical_resource = resource.resolve()
    trusted_reads = 0

    def mutate_after_trusted_read(self, path):
        nonlocal trusted_reads
        data = original_read(self, path)
        if path == canonical_resource:
            trusted_reads += 1
            if trusted_reads == 2:
                resource.unlink()
                if mutation == "symlink":
                    try:
                        resource.symlink_to(external)
                    except OSError:
                        pytest.skip("symlinks unavailable")
        return data

    monkeypatch.setattr(WorkflowResourceReadBudget, "read", mutate_after_trusted_read)
    response = TestClient(_app(_router()), raise_server_exceptions=False).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": f"sealed-resource-{mutation}",
            "values": {},
            "idempotency_key": f"sealed-resource-{mutation}",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 202
    run_id = response.json()["result"]["run_id"]
    captured = store.run_directory(run_id) / "commands" / "sealed.md"
    assert captured.read_text(encoding="utf-8") == "TRUSTED_RESOURCE"
    assert external.read_text(encoding="utf-8") == "UNTRUSTED_EXTERNAL_RESOURCE"


def test_runs_are_bounded_cursor_paginated_and_scope_authorized(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="desktop"))
    store = RunStore(home)
    first = _start(store, package, "one", scope="alice/conversation")
    _start(store, package, "two", scope="bob/conversation")
    client = TestClient(_app(_router()))

    page = client.get(
        "/api/plugins/workflow/runs?limit=1",
        headers={"X-Hermes-Operator-Scope": "alice/conversation"},
    )
    assert page.status_code == 200
    body = page.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["run_id"] == first.run_id
    assert "operator_scope_digest" not in str(body)
    detail = client.get(
        f"/api/plugins/workflow/runs/{first.run_id}",
        headers={"X-Hermes-Operator-Scope": "bob/conversation"},
    )
    assert detail.status_code == 404


def test_runs_pagination_traverses_more_than_200_filtered_rows_without_gaps(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="pagination"))
    store = RunStore(
        home,
        max_executing_runs=300,
        max_nonterminal_runs=300,
        max_start_requests_per_minute=300,
    )
    history = _start(store, package, "history-old")
    RunScheduler(store).advance(history.run_id)
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET updated_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
            (history.run_id,),
        )
    archived = _start(store, package, "archived-old")
    archived_status = RunScheduler(store).advance(archived.run_id)
    store.archive_run(
        archived.run_id,
        expected_state_version=archived_status["state_version"],
    )
    board_ids = {
        _start(store, package, f"board-{index:03d}").run_id for index in range(250)
    }
    client = TestClient(_app(_router()))

    def traverse(view: str) -> list[str]:
        cursor = None
        seen: list[str] = []
        while True:
            query = f"view={view}&limit=37"
            if cursor is not None:
                query += f"&cursor={cursor}"
            response = client.get(f"/api/plugins/workflow/runs?{query}")
            assert response.status_code == 200
            page = response.json()
            seen.extend(run["run_id"] for run in page["runs"])
            cursor = page["next_cursor"]
            if cursor is None:
                return seen

    board = traverse("board")
    history_rows = traverse("history")
    archive_rows = traverse("archive")

    assert len(board) == len(set(board)) == 250
    assert set(board) == board_ids
    assert history_rows == [history.run_id]
    assert archive_rows == [archived.run_id]


def test_events_cursor_and_stale_action_conflict(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="events"))
    store = RunStore(home)
    run = _start(store, package, "one")
    client = TestClient(_app(_router()))

    events = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}/events?after=0&limit=999"
    )
    assert events.status_code == 200
    assert len(events.json()["events"]) <= 200
    assert events.json()["next_cursor"] >= 1

    stale = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/cancel",
        json={"expected_version": -1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_state"

    evidence = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}/evidence?kind=timeline"
    )
    assert evidence.status_code == 200
    assert evidence.json()["kind"] == "timeline"
    assert evidence.json()["items"][0]["event_type"] == "run_admitted"


@pytest.mark.parametrize(
    ("action", "interaction_id", "extra"),
    [
        ("approve", None, {}),
        ("reject", "", {"reason": "no"}),
        ("provide-input", None, {"value": "feedback"}),
        ("reconcile", "", {"outcome": "confirmed-failed"}),
    ],
)
def test_null_interaction_is_rejected_before_desktop_mutation(
    tmp_path,
    monkeypatch,
    workflow_writer,
    action,
    interaction_id,
    extra,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"desktop-{action}",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    store = RunStore(home)
    run = _start(store, package, action)
    paused = RunScheduler(store).advance(run.run_id)
    payload = {
        "expected_version": paused["state_version"],
        "interaction_id": interaction_id,
        **extra,
    }

    response = TestClient(_app(_router())).post(
        f"/api/plugins/workflow/runs/{run.run_id}/{action}", json=payload
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interaction_id_required"
    assert store.load_run(run.run_id)["state_version"] == paused["state_version"]


def test_archive_restore_views_and_explicit_cleanup_api(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="lifecycle"))
    store = RunStore(home)
    run = _start(store, package, "lifecycle")
    RunScheduler(store).advance(run.run_id)
    client = TestClient(_app(_router()))
    current = client.get(f"/api/plugins/workflow/runs/{run.run_id}").json()

    archived = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/archive",
        json={"expected_version": current["state_version"]},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "succeeded"
    assert archived.json()["next_actions"] == ["status", "events", "restore"]
    assert client.get("/api/plugins/workflow/runs?view=board").json()["runs"] == []
    assert [
        item["run_id"]
        for item in client.get("/api/plugins/workflow/runs?view=archive").json()[
            "runs"
        ]
    ] == [run.run_id]

    restored = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/restore",
        json={"expected_version": archived.json()["state_version"]},
    )
    assert restored.status_code == 200
    assert [
        item["run_id"]
        for item in client.get("/api/plugins/workflow/runs?view=history").json()[
            "runs"
        ]
    ] == [run.run_id]

    preview = client.get("/api/plugins/workflow/cleanup/preview?older_than=0d")
    assert preview.status_code == 200
    token = preview.json()["confirmation_token"]
    executed = client.post(
        "/api/plugins/workflow/cleanup/execute",
        json={"older_than": "0d", "confirmation_token": token},
    )
    assert executed.status_code == 200
    assert executed.json()["run_ids"] == [run.run_id]
    history = client.get("/api/plugins/workflow/cleanup/history")
    assert history.status_code == 200
    assert history.json()["items"][0]["run_id"] == run.run_id
    assert "source_path" not in str(history.json())


def test_remote_session_cannot_mint_admin_through_cleanup_binding_header(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    session = SimpleNamespace(provider="test", org_id="org", user_id="user-1")
    client = TestClient(_app(_router(), session=session))

    response = client.get(
        "/api/plugins/workflow/cleanup/preview?older_than=0d",
        headers={
            "X-Hermes-Operator-Scope": "dashboard:test:org:user-1:admin"
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_admin_required"


def test_cleanup_binding_minted_for_one_principal_fails_for_another(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    scope = "service:test:admin-a"
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cleanup-binding")
    )
    store = RunStore(home)
    run = _start(store, package, "cleanup-binding", scope=scope)
    RunScheduler(store).advance(run.run_id)
    module = _module()
    admin_a = TokenPrincipal(
        principal="admin-a", provider="test", scopes=("workflow:admin",)
    )
    admin_b = TokenPrincipal(
        principal="admin-b", provider="test", scopes=("workflow:admin",)
    )
    preview = TestClient(_app(module.router, token=admin_a)).get(
        "/api/plugins/workflow/cleanup/preview?older_than=0d"
    )
    assert preview.status_code == 200

    executed = TestClient(_app(module.router, token=admin_b)).post(
        "/api/plugins/workflow/cleanup/execute",
        json={
            "older_than": "0d",
            "confirmation_token": preview.json()["confirmation_token"],
        },
    )

    assert executed.status_code == 409
    assert executed.json()["detail"]["code"] == "cleanup_confirmation_invalid"
    assert store.run_directory(run.run_id).is_dir()


def test_events_long_poll_returns_when_a_new_event_arrives(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    package = load_workflow(workflow_writer(tmp_path / "package", name="long-poll"))
    store = RunStore(home)
    run = _start(store, package, "long-poll")

    def append_event():
        time.sleep(0.05)
        store.append_event(run.run_id, "progress", {"step": 1})

    thread = threading.Thread(target=append_event)
    thread.start()
    response = TestClient(_app(module.router)).get(
        f"/api/plugins/workflow/runs/{run.run_id}/events?after=1&wait_seconds=1"
    )
    thread.join()

    assert response.status_code == 200
    assert response.json()["events"][0]["sequence"] == 2


def test_attention_includes_real_workflow_approval_interactions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    started = run_showcase(
        "laptop-diagnostic", hermes_home=home, symptom="fictional attention"
    )
    client = TestClient(_app(_router()))
    response = client.get("/api/plugins/workflow/attention")

    assert response.status_code == 200
    item = next(
        item for item in response.json()["items"] if item["run_id"] == started["run_id"]
    )
    assert item["interaction"]["type"] == "workflow_approval"
    detail = client.get(f"/api/plugins/workflow/runs/{started['run_id']}").json()
    assert (
        detail["pending_interaction"]["interaction_id"]
        == item["interaction"]["interaction_id"]
    )
    assert detail["pending_interaction"]["node_id"] == item["node_id"]
    assert detail["next_actions"] == [
        "status",
        "events",
        "approve",
        "reject",
        "cancel",
    ]


def test_attention_returns_action_metadata_for_every_operator_attention_kind(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home, max_executing_runs=20, max_nonterminal_runs=20)

    approval_package = load_workflow(
        workflow_writer(
            tmp_path / "approval-attention",
            name="approval-attention",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    approval = _start(store, approval_package, "approval-attention")
    RunScheduler(store).advance(approval.run_id)

    loop_package = load_workflow(
        workflow_writer(
            tmp_path / "input-attention",
            name="input-attention",
            interactive=True,
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Refine",
                        "until": "DONE",
                        "max_iterations": 2,
                        "interactive": True,
                        "gate_message": "Provide feedback",
                    },
                }
            ],
        )
    )
    loop = _start(store, loop_package, "input-attention")
    RunScheduler(store, agent_runner=_LoopRunner()).advance(loop.run_id)

    failed_package = load_workflow(
        workflow_writer(
            tmp_path / "failure-attention",
            name="failure-attention",
            nodes=[{"id": "fail", "bash": "exit 7"}],
        )
    )
    failed = _start(store, failed_package, "failure-attention")
    RunScheduler(store).advance(failed.run_id)

    stalled_package = load_workflow(
        workflow_writer(tmp_path / "stalled-attention", name="stalled-attention")
    )
    stalled_snapshot = store.prepare_run_snapshot(stalled_package)
    stalled = store.start_run(
        RunAdmissionRequest(
            workflow_name=stalled_package.definition.name,
            definition_digest=stalled_snapshot.definition_digest,
            policy_digest=stalled_snapshot.policy_digest,
            input_manifest_digest=stalled_snapshot.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key="stalled-attention",
            concurrency_key="stalled-attention",
            concurrency_policy="allow",
            foreground_owner_id="expired-owner",
            foreground_lease_seconds=0.001,
        ),
        immutable_snapshot=stalled_snapshot,
    )
    time.sleep(0.01)

    reconcile_package = load_workflow(
        workflow_writer(
            tmp_path / "reconcile-attention", name="reconcile-attention"
        )
    )
    reconcile = _start(store, reconcile_package, "reconcile-attention")
    claim = store.claim_node(reconcile.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="paused",
        error_code="outcome_uncertain",
        metadata={"pending_interaction": "reconcile"},
    )

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/attention?limit=100"
    )

    assert response.status_code == 200
    by_run = {item["run_id"]: item for item in response.json()["items"]}
    expected = {
        approval.run_id: ("workflow_approval", "approve"),
        loop.run_id: ("loop_input", "provide-input"),
        failed.run_id: ("failure", "retry"),
        stalled.run_id: ("stalled", "cancel"),
        reconcile.run_id: ("reconcile", "reconcile"),
    }
    assert set(expected) <= set(by_run)
    for run_id, (kind, action) in expected.items():
        item = by_run[run_id]
        assert item["kind"] == kind
        assert item["origin"] == "desktop"
        assert item["cause"]
        assert action in item["next_actions"]
        assert item["state_version"] >= 1
        assert item["updated_at"]


def _approval_attention_runs(
    home: Path,
    package,
    *,
    count: int,
    scope: str | None = None,
) -> list[str]:
    store = RunStore(
        home,
        max_executing_runs=max(4, count),
        max_nonterminal_runs=max(200, count + 1),
        max_start_requests_per_minute=max(60, count + 1),
    )
    run_ids = []
    for index in range(count):
        admitted = _start(
            store,
            package,
            f"attention-page-{index:03d}",
            scope=scope,
        )
        RunScheduler(store).advance(admitted.run_id)
        run_ids.append(admitted.run_id)
    return run_ids


def test_attention_is_newest_first(tmp_path, monkeypatch, workflow_writer) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(
            tmp_path / "newest-attention",
            name="newest-attention",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    run_ids = _approval_attention_runs(home, package, count=3)

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/attention?limit=3"
    )

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == list(
        reversed(run_ids)
    )


def test_attention_surfaces_run_scoped_notification_repair_damage(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="repair-attention")
    )
    admitted = _start(store, package, "repair-attention")
    journal = store.run_directory(admitted.run_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":999')
    assert NotificationOutbox(store).reconcile_journal(limit_runs=1) == 0

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/attention?limit=10"
    )

    assert response.status_code == 200
    item = next(
        item
        for item in response.json()["items"]
        if item["run_id"] == admitted.run_id
    )
    assert item["kind"] == "stalled"
    assert item["health"] == "storage_degraded"
    assert item["cause"] == "notification_reconciliation_unverified"
    assert "events.jsonl" not in response.text


def test_corrupted_run_rejects_mutation_with_typed_error(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    package = load_workflow(
        workflow_writer(
            tmp_path / "corrupt-mutation",
            name="corrupt-mutation",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    admitted = _start(store, package, "corrupt-mutation")
    RunScheduler(store).advance(admitted.run_id)
    current = store.get_run_status(admitted.run_id)
    interaction = current["pending_interaction"]
    assert interaction["type"] == "workflow_approval"
    run_directory = store.run_directory(admitted.run_id)
    journal = run_directory / "events.jsonl"
    frames = journal.read_bytes().splitlines(keepends=True)
    assert len(frames) > 1
    corrupted_journal = frames[0] + b"{not-json}\n" + b"".join(frames[1:])
    journal.write_bytes(corrupted_journal)
    (run_directory / "run.json").unlink()
    client = TestClient(_app(_router()), raise_server_exceptions=False)

    response = client.post(
        f"/api/plugins/workflow/runs/{admitted.run_id}/approve",
        json={
            "expected_version": current["state_version"],
            "interaction_id": interaction["interaction_id"],
            "comment": "approved",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_evidence_uncorroborated"
    assert journal.read_bytes() == corrupted_journal
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert store._active_run_repair_reasons(admitted.run_id) == (
        "run_evidence_uncorroborated",
    )
    attention = client.get("/api/plugins/workflow/attention?limit=10")
    item = next(
        item
        for item in attention.json()["items"]
        if item["run_id"] == admitted.run_id
    )
    assert item["cause"] == "run_evidence_uncorroborated"


def test_attention_cursor_traverses_more_than_100_tied_items_and_is_scope_bound(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    fixed = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr("plugins.workflow.store._utc_now", lambda: fixed)
    package = load_workflow(
        workflow_writer(
            tmp_path / "paged-attention",
            name="paged-attention",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    expected = set(
        _approval_attention_runs(
            home,
            package,
            count=105,
            scope="alice/conversation",
        )
    )
    client = TestClient(_app(_router()))
    cursor = None
    seen: list[str] = []
    cursors: set[str] = set()

    while True:
        query = "limit=37"
        if cursor is not None:
            query += f"&cursor={cursor}"
        response = client.get(
            f"/api/plugins/workflow/attention?{query}",
            headers={"X-Hermes-Operator-Scope": "alice/conversation"},
        )
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["run_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors
        cursors.add(cursor)

    assert len(seen) == len(set(seen)) == 105
    assert set(seen) == expected
    assert len(cursors) == 2

    denied = client.get(
        f"/api/plugins/workflow/attention?limit=37&cursor={next(iter(cursors))}",
        headers={"X-Hermes-Operator-Scope": "bob/conversation"},
    )
    assert denied.status_code == 410
    assert denied.json()["detail"]["code"] == "cursor_expired"


def test_forged_scope_header_cannot_expand_verified_session(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="verified"))
    store = RunStore(home)
    scope = "dashboard:test:org:user-1"
    run = _start(store, package, "verified", scope=scope)
    session = SimpleNamespace(provider="test", org_id="org", user_id="user-1")
    client = TestClient(_app(_router(), session=session))

    allowed = client.get(f"/api/plugins/workflow/runs/{run.run_id}")
    forged = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}",
        headers={"X-Hermes-Operator-Scope": "dashboard:test:org:user-2"},
    )

    assert allowed.status_code == 200
    assert forged.status_code == 403
    assert forged.json()["detail"]["code"] == "operator_scope_not_authorized"


def test_seventeenth_event_wait_is_refused_while_status_remains_responsive(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="capacity"))
    store = RunStore(home)
    run = _start(store, package, "capacity")
    module = _module()
    app = _app(module.router)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            path = (
                f"/api/plugins/workflow/runs/{run.run_id}/events"
                "?after=1&wait_seconds=0.2"
            )
            waits = [asyncio.create_task(client.get(path)) for _ in range(17)]
            await asyncio.sleep(0.03)
            status = await client.get(f"/api/plugins/workflow/runs/{run.run_id}")
            responses = await asyncio.gather(*waits)
            return status, responses

    status, responses = asyncio.run(exercise())

    assert status.status_code == 200
    assert sum(response.status_code == 429 for response in responses) == 1
    refused = next(response for response in responses if response.status_code == 429)
    assert refused.json()["detail"]["code"] == "event_wait_capacity"


def test_repeated_api_reads_initialize_one_store_for_the_profile(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    runtime = WorkflowApiRuntime(WorkflowApiLimits())
    created = []

    def factory(path):
        created.append(path)
        return RunStore(path)

    runtime.stores = WorkflowStoreRegistry(max_profiles=8, store_factory=factory)
    module._RUNTIME = runtime
    client = TestClient(_app(module.router))

    assert client.get("/api/plugins/workflow/runs").status_code == 200
    assert client.get("/api/plugins/workflow/runs").status_code == 200

    assert created == [home.resolve()]
    assert runtime.stores.snapshot()["profiles"] == 1
    runtime.close()
