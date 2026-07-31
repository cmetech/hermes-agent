from __future__ import annotations

import errno
import importlib.util
import asyncio
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import sqlite3
import sys
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from hermes_cli.dashboard_auth.base import TokenPrincipal
import pytest
import yaml

from agent.plugin_agent import PluginAgentRunner, PluginAgentRunResult
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
import plugins.workflow.api_admission as api_admission_module
from plugins.workflow.catalog_api import workflow_catalog_run_support
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
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorHealthSnapshotError,
    CoordinatorIdentity,
    CoordinatorStore,
)
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.output_resolution import ArchonOutputUnavailableError
from plugins.workflow.runner_binding import (
    background_execution_context,
    production_workflow_runner_binding,
)
from plugins.workflow.scheduled_revalidation import (
    sealed_snapshot_digest,
    verify_sealed_snapshot,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import (
    ArtifactRef,
    RunStore,
    TypedPublicationCandidate,
)
import plugins.workflow.store as store_module
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


def _published_run(
    home,
    workflow_writer,
    root,
    *,
    data: bytes,
    media_type: str,
    scope: str | None = None,
    output_type: str = "DesktopReport",
    session_id: str | None = "desktop-artifact-session",
):
    workflow = workflow_writer(
        root,
        name=f"artifact-{root.name}",
        nodes=[
            {
                "id": "produce",
                "bash": "true",
                "output_type": output_type,
            },
            {
                "id": "finish",
                "bash": "true",
                "depends_on": ["produce"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(home)
    admitted = _start(store, package, root.name, scope=scope)
    claim = store.claim_node(admitted.run_id, "produce", "desktop-api-worker")
    assert claim is not None
    suffix = "json" if media_type == "application/json" else "md"
    source = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / f"output.{suffix}"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(data)
    relative_path = source.relative_to(
        store.run_directory(admitted.run_id)
    ).as_posix()
    digest = sha256(data).hexdigest()
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(
            ArtifactRef(relative_path, media_type, len(data), digest),
        ),
        typed_publication=TypedPublicationCandidate(
            attempt_relative_path=relative_path,
            output_type=output_type,
            media_type=media_type,
            size_bytes=len(data),
            sha256=digest,
            schema_fingerprint=None,
            canonicalization_version=1,
            session_id=session_id,
        ),
    )
    artifact = next(
        item
        for item in store.get_run_status(
            admitted.run_id,
            operator_scope=scope,
        )["artifacts"]
        if "publication_id" in item
    )
    return store, admitted, artifact


def _forge_checked_typed_descriptor(
    store: RunStore,
    run_id: str,
    corruption: str,
) -> tuple[str, str]:
    projection = store.load_run(run_id)
    artifacts = deepcopy(projection["artifacts"])
    descriptor = next(
        artifact for artifact in artifacts if "publication_id" in artifact
    )
    updates: dict[str, object] = {"artifacts": artifacts}
    if corruption == "unknown_media":
        descriptor["media_type"] = "application/octet-stream"
    elif corruption == "invalid_size_type":
        descriptor["size_bytes"] = True
    elif corruption == "duplicate_publication_id":
        artifacts.append(dict(descriptor))
    elif corruption == "non_winning_attempt":
        nodes = deepcopy(projection["nodes"])
        winner = next(
            attempt
            for attempt in nodes[descriptor["node_id"]]["attempts"]
            if attempt["attempt_id"] == descriptor["attempt_id"]
        )
        winner["state"] = "failed"
        updates["nodes"] = nodes
    elif corruption == "sealed_output_type_mismatch":
        descriptor["output_type"] = "ForgedOutputType"
    elif corruption == "sealed_schema_mismatch":
        descriptor["schema_fingerprint"] = "f" * 64
    elif corruption == "legacy_unversioned":
        descriptor.pop("typed_publication_version")
    else:
        raise AssertionError(f"unknown corruption fixture: {corruption}")
    store.append_event(
        run_id,
        "forged_typed_descriptor",
        projection_updates=updates,
    )
    return descriptor["publication_id"], descriptor["content_name"]


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


def _trusted_declared_catalog_workflow(
    home,
    workflow_writer,
    *,
    name,
    inputs,
):
    workflow_path = workflow_writer(
        home / "workflows",
        name=name,
        filename=f"{name}.yaml",
    )
    workflow_path.with_name(f"{name}.hermes.yaml").write_text(
        "delivery_defaults:\n  inputs:\n"
        + "".join(
            f"    {input_name}: {definition}\n"
            for input_name, definition in inputs.items()
        ),
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
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


SCHEDULE_NOW = datetime(2098, 12, 31, 0, 0, tzinfo=timezone.utc)
SCHEDULE_AT = "2099-01-02T03:04:05Z"


def _restamp_showcase_copy(root: Path, showcase_id: str) -> None:
    catalog_path = root / "catalog.yaml"
    manifest_path = root / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["catalog_sha256"] = sha256(catalog_path.read_bytes()).hexdigest()
    manifest["packages"][showcase_id] = showcase_module._tree_digest(
        root / "packages" / showcase_id
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


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
    captured_requests: list[RunAdmissionRequest] = []
    original_start_digest = RunStore._start_digest

    def capture_start_digest(request):
        captured_requests.append(request)
        return original_start_digest(request)

    monkeypatch.setattr(RunStore, "_start_digest", staticmethod(capture_start_digest))

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
    assert run["status"] == "running"
    assert run["trigger"] == "desktop"
    assert run["execution_mode"] == "background"
    assert run["run_metadata"] == {}
    assert captured_requests[-1].run_metadata is None
    assert RunStore._start_digest_from_projection(run) == original_start_digest(
        captured_requests[-1]
    )
    assert run["provenance"]["source"] == "desktop"
    assert run["provenance"]["assurance"] == "verified_adapter"
    assert run["provenance"]["actor_id"] == scope
    assert run["provenance"]["source_instance"] == "api:session:test"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2099-01-02T03:04:05Z", "2099-01-02T03:04:05Z"),
        ("2099-01-02T04:04:05+01:00", "2099-01-02T03:04:05Z"),
        ("2099-01-01T22:34:05-04:30", "2099-01-02T03:04:05Z"),
        ("2099-01-02T04:04:05.125000+01:00", "2099-01-02T03:04:05.125000Z"),
        (
            "2099-01-02T04:04:05.123456789+01:00",
            "2099-01-02T03:04:05.123456789Z",
        ),
        (
            "2099-01-02T04:04:05.1234561000+01:00",
            "2099-01-02T03:04:05.1234561Z",
        ),
    ],
)
def test_schedule_at_parser_normalizes_aware_rfc3339_to_canonical_utc_z(
    value: str, expected: str
) -> None:
    assert (
        api_admission_module.normalize_api_schedule_at(
            value, now_utc=SCHEDULE_NOW
        )
        == expected
    )


def test_schedule_at_parser_preserves_distinct_submicrosecond_instants() -> None:
    first = api_admission_module.normalize_api_schedule_at(
        "2099-01-02T03:04:05.1234561Z", now_utc=SCHEDULE_NOW
    )
    second = api_admission_module.normalize_api_schedule_at(
        "2099-01-02T03:04:05.1234569Z", now_utc=SCHEDULE_NOW
    )

    assert first == "2099-01-02T03:04:05.1234561Z"
    assert second == "2099-01-02T03:04:05.1234569Z"
    assert first != second


def test_schedule_at_parser_translates_unrepresentable_observed_offset() -> None:
    unrepresentable_now = datetime(
        1,
        1,
        1,
        tzinfo=timezone(timedelta(hours=23, minutes=59)),
    )

    with pytest.raises(ApiAdmissionError) as error:
        api_admission_module.normalize_api_schedule_at(
            SCHEDULE_AT, now_utc=unrepresentable_now
        )

    assert error.value.code == "workflow_schedule_invalid"
    assert error.value.status_code == 422


def test_schedule_at_parser_accepts_durable_metadata_value_boundary() -> None:
    schedule_at = f"2099-01-02T03:04:05.{'1' * 491}Z"

    assert len(schedule_at) == 512
    assert (
        api_admission_module.normalize_api_schedule_at(
            schedule_at, now_utc=SCHEDULE_NOW
        )
        == schedule_at
    )


def test_post_runs_rejects_schedule_over_durable_metadata_value_boundary(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    schedule_at = f"2099-01-02T03:04:05.{'1' * 492}Z"
    assert len(schedule_at) == 513
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW)

    @contextmanager
    def forbidden_store_lease():
        pytest.fail("oversized schedule reached store construction")
        yield

    monkeypatch.setattr(module, "_store_lease", forbidden_store_lease)
    monkeypatch.setattr(
        api_admission_module,
        "_catalog_package",
        lambda *_args, **_kwargs: pytest.fail("oversized schedule loaded a package"),
    )

    response = TestClient(_app(module.router)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "must-not-load",
            "values": {},
            "idempotency_key": "oversized-schedule",
            "concurrency_policy": "queue",
            "schedule_at": schedule_at,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "workflow_schedule_invalid", "retryable": False}
    }
    assert not home.exists()
    assert not (home / "workflow-staging").exists()


@pytest.mark.parametrize(
    "schedule_at",
    [
        "",
        "not-an-instant",
        "2099-01-02T03:04:05",
        "2099-01-02T03:04:05-00:00",
        "2098-12-30T23:59:59Z",
        "2098-12-31T00:00:00Z",
        "0001-01-01T00:00:00+23:59",
        "9999-12-31T23:59:59-23:59",
        17,
        {"instant": "2099-01-02T03:04:05Z"},
        ["2099-01-02T03:04:05Z"],
        True,
    ],
)
def test_post_runs_rejects_invalid_schedule_at_before_store_or_package_work(
    tmp_path, monkeypatch, schedule_at
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW, raising=False)

    @contextmanager
    def forbidden_store_lease():
        pytest.fail("invalid schedule reached store construction")
        yield

    monkeypatch.setattr(module, "_store_lease", forbidden_store_lease)
    monkeypatch.setattr(
        api_admission_module,
        "_catalog_package",
        lambda *_args, **_kwargs: pytest.fail("invalid schedule loaded a package"),
    )

    response = TestClient(_app(module.router)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "must-not-load",
            "values": {},
            "idempotency_key": f"invalid-schedule-{schedule_at}",
            "concurrency_policy": "queue",
            "schedule_at": schedule_at,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "workflow_schedule_invalid", "retryable": False}
    }
    assert not home.exists()


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
        ("ai-extensions", 409, "workflow_compatibility_blocked"),
        ("scheduling", 409, "workflow_schedule_required"),
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
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM admission_events").fetchone()[0]
            == 0
        )


def test_future_schedule_is_queued_with_server_owned_identity_and_no_execution(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = _trusted_catalog_workflow(home, workflow_writer, name="scheduled-api")
    store = RunStore(home)
    _healthy_coordinator(store)
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW)
    monkeypatch.setattr(
        RunScheduler,
        "advance",
        lambda *_args, **_kwargs: pytest.fail("scheduled admission executed a node"),
    )

    response = TestClient(_app(module.router)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "scheduled-api",
            "values": {},
            "idempotency_key": "scheduled-api-created",
            "concurrency_policy": "allow",
            "schedule_at": "2099-01-02T04:04:05+01:00",
        },
    )

    assert response.status_code == 202
    result = response.json()["result"]
    assert result["status"] == "queued"
    run = store.get_run_status(result["run_id"])
    risk = build_risk_summary(package, assess_compatibility(package))
    execution_context = background_execution_context(
        production_workflow_runner_binding(), requires_ai=None
    )
    assert run["status"] == "queued"
    assert run["execution_mode"] == "background"
    assert run["started_at"] is None
    run_directory = store.run_directory(result["run_id"])
    assert run["run_metadata"] == {
        "catalog_source": "profile",
        "catalog_source_relative": "scheduled-api.yaml",
        "catalog_source_root": str((home / "workflows").resolve()),
        "execution_identity": execution_context.identity_digest_for(package),
        "execution_runtime_identity": execution_context.identity_digest,
        "package_digest": risk.package_digest,
        "risk_digest": risk.risk_digest,
        "schedule_at": SCHEDULE_AT,
        "sealed_definition_digest": sha256(
            (run_directory / "definition.yaml").read_bytes()
        ).hexdigest(),
        "sealed_input_digest": run["input_manifest_digest"],
        "sealed_policy_digest": run["policy_digest"],
        "sealed_snapshot_digest": sealed_snapshot_digest(run_directory),
    }
    assert all(node["state"] == "ready" for node in run["nodes"].values())
    state = run_directory / "legacy-schedule-state.txt"
    state.write_text("legacy schedule state\n", encoding="utf-8")
    verify_sealed_snapshot(run, run_directory=run_directory)
    assert state.read_text(encoding="utf-8") == "legacy schedule state\n"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, scheduled_at FROM runs WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
    assert tuple(row) == ("queued", SCHEDULE_AT)


def test_authenticated_scheduling_showcase_accepts_a_future_schedule(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW)
    monkeypatch.setattr(
        RunScheduler,
        "advance",
        lambda *_args, **_kwargs: pytest.fail("showcase admission executed a node"),
    )

    response = TestClient(_app(module.router)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "scheduling",
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": "scheduled-showcase",
            "concurrency_policy": "allow",
            "schedule_at": SCHEDULE_AT,
        },
    )

    assert response.status_code == 202
    run = store.get_run_status(response.json()["result"]["run_id"])
    assert run["status"] == "queued"
    assert run["run_metadata"]["showcase_id"] == "scheduling"
    assert run["run_metadata"]["catalog_source"] == "showcase"
    assert run["run_metadata"]["schedule_at"] == SCHEDULE_AT


def test_server_run_support_receives_future_schedule_for_package_general_admission(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="future-user-package")
    store = RunStore(home)
    _healthy_coordinator(store)
    observed: list[str | None] = []
    original = workflow_catalog_run_support

    def recording_support(package, **kwargs):
        observed.append(kwargs.get("schedule_at"))
        return original(package, **kwargs)

    monkeypatch.setattr(
        "plugins.workflow.catalog_api.workflow_catalog_run_support",
        recording_support,
    )

    result = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="future-user-package",
        values={},
        idempotency_key="future-user-package",
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="schedule-policy-test",
            namespace="schedule-policy-test",
            operator_scope=None,
            source_instance="desktop:test",
            assurance="local_admin_claim",
            trigger_source="desktop",
        ),
        schedule_at=SCHEDULE_AT,
        schedule_now_utc=SCHEDULE_NOW,
    )

    assert result["status"] == "queued"
    assert observed == [SCHEDULE_AT]


def test_schedule_identity_joins_equivalent_offsets_conflicts_on_change_and_recovers(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="scheduled-identity")
    store = RunStore(home)
    _healthy_coordinator(store)
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW)
    captured_requests: list[RunAdmissionRequest] = []
    original_start_digest = RunStore._start_digest

    def capture_start_digest(request):
        captured_requests.append(request)
        return original_start_digest(request)

    monkeypatch.setattr(RunStore, "_start_digest", staticmethod(capture_start_digest))
    client = TestClient(_app(module.router))
    base = {
        "workflow": "scheduled-identity",
        "values": {},
        "idempotency_key": "same-schedule-key",
        "concurrency_policy": "allow",
    }

    exact_schedule = "2099-01-02T03:04:05.1234561Z"
    created = client.post(
        "/api/plugins/workflow/runs", json={**base, "schedule_at": exact_schedule}
    )
    existing = client.post(
        "/api/plugins/workflow/runs",
        json={
            **base,
            "schedule_at": "2099-01-02T04:04:05.1234561000+01:00",
        },
    )
    conflict = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "schedule_at": "2099-01-02T03:04:05.1234569Z"},
    )

    assert created.status_code == existing.status_code == 202
    assert created.json()["result"]["admission_disposition"] == "queued"
    assert existing.json()["result"]["admission_disposition"] == "existing"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    run_id = created.json()["result"]["run_id"]
    directory = store.run_directory(run_id)
    (directory / "run.json").unlink()
    recovered = store.load_run(run_id)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT start_digest, scheduled_at FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
    stored_digest = row["start_digest"]
    assert row["scheduled_at"] == exact_schedule
    assert recovered["run_metadata"]["schedule_at"] == exact_schedule
    assert captured_requests[0].run_metadata == captured_requests[1].run_metadata
    assert captured_requests[0].run_metadata != captured_requests[-1].run_metadata
    assert original_start_digest(captured_requests[0]) == stored_digest
    assert RunStore._start_digest_from_projection(recovered) == stored_digest


def test_run_list_and_detail_expose_only_public_schedule_projection(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = _trusted_catalog_workflow(
        home, workflow_writer, name="scheduled-redaction"
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    module = _module()
    monkeypatch.setattr(module, "_schedule_now_utc", lambda: SCHEDULE_NOW)
    client = TestClient(_app(module.router))

    created = client.post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "scheduled-redaction",
            "values": {},
            "idempotency_key": "scheduled-redaction",
            "concurrency_policy": "allow",
            "schedule_at": SCHEDULE_AT,
        },
    )
    assert created.status_code == 202
    run_id = created.json()["result"]["run_id"]
    internal = store.get_run_status(run_id)
    assert internal["run_metadata"]["risk_digest"]
    assert internal["run_metadata"]["catalog_source"] == "profile"

    detail = client.get(f"/api/plugins/workflow/runs/{run_id}").json()
    listed = next(
        item
        for item in client.get("/api/plugins/workflow/runs?view=all").json()["runs"]
        if item["run_id"] == run_id
    )
    for projection in (detail, listed):
        assert projection["schedule_at"] == SCHEDULE_AT
        assert projection["presentation_state"] == "scheduled_wait"
        assert "run_metadata" not in projection
        encoded = json.dumps(projection, sort_keys=True)
        assert internal["run_metadata"]["risk_digest"] not in encoded
        assert "catalog_source" not in encoded

    snapshot = store.prepare_run_snapshot(package)
    canaries = {
        "api_token": "SECRET-CANARY",
        "caller_metadata": "CALLER-CANARY",
        "local_path": "/private/operator/CANARY.txt",
        "trust_internal": "TRUST-CANARY",
    }
    direct = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key="scheduled-redaction-canaries",
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
            execution_mode="background",
            run_metadata={"schedule_at": SCHEDULE_AT, **canaries},
        ),
        immutable_snapshot=snapshot,
    )
    assert direct.run_id is not None
    canary_detail = client.get(
        f"/api/plugins/workflow/runs/{direct.run_id}"
    ).content
    assert all(value.encode() not in canary_detail for value in canaries.values())
    assert b"run_metadata" not in canary_detail


def test_run_list_uses_one_injected_clock_for_status_and_public_projection(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="scheduled-list-clock")
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    run_id = _start_scheduled_mutation_run(store, package, key="list-clock")
    second_run_id = _start_scheduled_mutation_run(
        store, package, key="list-clock-second"
    )
    due = datetime.fromisoformat(SCHEDULE_AT.replace("Z", "+00:00"))
    before_due = due - timedelta(microseconds=1)
    after_due = due + timedelta(microseconds=1)
    store._lease_clock = lambda: LeaseClockSample(after_due, 100.0, "list-clock")
    module = _module()
    samples = 0
    request_now = before_due

    def request_clock() -> datetime:
        nonlocal samples
        samples += 1
        return request_now

    @contextmanager
    def store_lease():
        yield store

    monkeypatch.setattr(module, "_schedule_now_utc", request_clock)
    monkeypatch.setattr(module, "_store_lease", store_lease)

    response = TestClient(_app(module.router)).get(
        "/api/plugins/workflow/runs?view=all&limit=1"
    )

    assert response.status_code == 200
    first_page = response.json()
    item = first_page["runs"][0]
    assert samples == 1
    assert item["presentation_state"] == "scheduled_wait"
    assert item["blocking_reason"] == "scheduled_wait"
    assert item["queue_position"] is None
    assert first_page["next_cursor"] is not None

    request_now = after_due
    second_response = TestClient(_app(module.router)).get(
        "/api/plugins/workflow/runs?view=all&limit=1"
        f"&cursor={first_page['next_cursor']}"
    )

    assert second_response.status_code == 200
    second_item = second_response.json()["runs"][0]
    assert samples == 1
    assert {item["run_id"], second_item["run_id"]} == {run_id, second_run_id}
    assert second_item["presentation_state"] == "scheduled_wait"
    assert second_item["blocking_reason"] == "scheduled_wait"
    assert second_item["queue_position"] is None


def _scheduled_mutation_canaries() -> dict[str, str]:
    return {
        "catalog_source": "CATALOG-CANARY",
        "risk_digest": "RISK-CANARY",
        "bundle_digest": "BUNDLE-CANARY",
        "entitlement_digest": "ENTITLEMENT-CANARY",
        "local_path": "/private/operator/PATH-CANARY.txt",
        "api_token": "TOKEN-CANARY",
        "caller_metadata": "CALLER-CANARY",
    }


def _start_scheduled_mutation_run(store, package, *, key: str):
    snapshot = store.prepare_run_snapshot(package)
    result = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
            execution_mode="background",
            run_metadata={"schedule_at": SCHEDULE_AT, **_scheduled_mutation_canaries()},
        ),
        immutable_snapshot=snapshot,
    )
    assert result.run_id is not None
    return result.run_id


def _scheduled_due_boundary_run(home, workflow_writer, *, name: str):
    due = datetime.fromisoformat(SCHEDULE_AT.replace("Z", "+00:00"))
    before_due = due - timedelta(microseconds=1)
    after_due = due + timedelta(microseconds=1)
    wall = [before_due]

    def lease_clock() -> LeaseClockSample:
        return LeaseClockSample(wall[0], 100.0, f"{name}-clock")

    store = RunStore(home, lease_clock=lease_clock)
    coordinator = CoordinatorStore(store.database, clock=lease_clock)
    identity = CoordinatorIdentity(
        owner_id=f"{name}-leader",
        host_kind="web",
        host_instance_id=f"{name}-leader",
        pid=1,
        process_start_time=None,
    )
    acquired = coordinator.try_acquire(
        identity,
        now=before_due,
        lease_seconds=60,
    )
    assert acquired.is_leader
    package = load_workflow(workflow_writer(home / "package", name=name))
    run_id = _start_scheduled_mutation_run(store, package, key=name)
    wall[0] = after_due
    return store, run_id, before_due


def test_run_detail_uses_one_clock_for_status_and_public_projection(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, run_id, before_due = _scheduled_due_boundary_run(
        home, workflow_writer, name="detail-boundary"
    )
    module = _module()
    samples = 0
    status_clocks = []
    original_get_run_status = store.get_run_status

    def request_clock() -> datetime:
        nonlocal samples
        samples += 1
        return before_due

    def tracked_get_run_status(tracked_run_id, **kwargs):
        status_clocks.append(kwargs.get("now"))
        return original_get_run_status(tracked_run_id, **kwargs)

    @contextmanager
    def store_lease():
        yield store

    monkeypatch.setattr(store, "get_run_status", tracked_get_run_status)
    monkeypatch.setattr(module, "_schedule_now_utc", request_clock)
    monkeypatch.setattr(module, "_store_lease", store_lease)

    response = TestClient(_app(module.router)).get(
        f"/api/plugins/workflow/runs/{run_id}"
    )

    assert response.status_code == 200
    assert samples == 1
    assert status_clocks == [before_due]
    assert response.json()["presentation_state"] == "scheduled_wait"
    assert response.json()["blocking_reason"] == "scheduled_wait"


@pytest.mark.parametrize(
    ("action", "expected_version_delta", "expected_code", "waiting"),
    [
        ("cancel", 0, None, False),
        ("cancel", -1, "stale_state", True),
        ("archive", 0, "invalid_transition", True),
    ],
)
def test_scheduled_mutation_response_uses_one_clock_across_due_boundary(
    tmp_path,
    monkeypatch,
    workflow_writer,
    action,
    expected_version_delta,
    expected_code,
    waiting,
) -> None:
    home = tmp_path / f"home-{action}-{expected_code or 'success'}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, run_id, before_due = _scheduled_due_boundary_run(
        home,
        workflow_writer,
        name=f"mutation-boundary-{action}-{expected_code or 'success'}",
    )
    current = store.get_run_status(run_id, now=before_due)
    module = _module()
    samples = 0
    status_clocks = []
    original_get_run_status = store.get_run_status

    def request_clock() -> datetime:
        nonlocal samples
        samples += 1
        return before_due

    def tracked_get_run_status(tracked_run_id, **kwargs):
        status_clocks.append(kwargs.get("now"))
        return original_get_run_status(tracked_run_id, **kwargs)

    @contextmanager
    def store_lease():
        yield store

    monkeypatch.setattr(store, "get_run_status", tracked_get_run_status)
    monkeypatch.setattr(module, "_schedule_now_utc", request_clock)
    monkeypatch.setattr(module, "_store_lease", store_lease)

    response = TestClient(_app(module.router)).post(
        f"/api/plugins/workflow/runs/{run_id}/{action}",
        json={
            "expected_version": current["state_version"] + expected_version_delta
        },
    )

    assert samples == 1
    assert status_clocks
    assert set(status_clocks) == {before_due}
    if expected_code is None:
        assert response.status_code == 200
        projection = response.json()
    else:
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == expected_code
        projection = response.json()["detail"]["current"]
    _assert_public_scheduled_mutation_projection(projection, waiting=waiting)
    if waiting:
        assert projection["blocking_reason"] == "scheduled_wait"


def _assert_public_scheduled_mutation_projection(
    projection: dict[str, object], *, waiting: bool
) -> None:
    assert projection["schedule_at"] == SCHEDULE_AT
    if waiting:
        assert projection["presentation_state"] == "scheduled_wait"
    else:
        assert "presentation_state" not in projection
    assert "run_metadata" not in projection
    encoded = json.dumps(projection, sort_keys=True)
    assert all(value not in encoded for value in _scheduled_mutation_canaries().values())


def test_scheduled_mutation_success_uses_public_run_projection(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="scheduled-mutation-success")
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    run_id = _start_scheduled_mutation_run(store, package, key="success")
    current = store.get_run_status(run_id)
    run_directory = store.run_directory(run_id)
    journal_before = (run_directory / "events.jsonl").read_bytes()

    client = TestClient(_app(_router()))
    response = client.post(
        f"/api/plugins/workflow/runs/{run_id}/cancel",
        json={"expected_version": current["state_version"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    _assert_public_scheduled_mutation_projection(response.json(), waiting=False)
    assert run_directory.is_dir()
    assert (run_directory / "run.json").is_file()
    journal_after = (run_directory / "events.jsonl").read_bytes()
    assert journal_after.startswith(journal_before)
    assert b'"event_type":"run_cancelled"' in journal_after

    restarted = RunStore(home)
    after_due = datetime(2100, 1, 1, tzinfo=timezone.utc)
    scheduled, _cursor, _exhausted = restarted.scheduled_coordinator_candidates(
        after=None,
        now=after_due,
    )
    assert run_id not in {str(item["run_id"]) for item in scheduled}
    restarted_scheduler = RunScheduler(restarted, utcnow=lambda: after_due)
    try:
        assert restarted_scheduler.advance(run_id)["status"] == "cancelled"
    finally:
        restarted_scheduler.shutdown(deadline_seconds=1)
    with restarted._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 0
        )

    observed_at = datetime.now(timezone.utc)
    coordinator = CoordinatorStore(restarted.database)
    lease = coordinator.observe(now=observed_at)
    assert lease is not None
    identity = CoordinatorIdentity(
        owner_id="api-test",
        host_kind="web",
        host_instance_id="api-test",
        pid=1,
        process_start_time=None,
    )
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(host_kind="web", host_instance_id="api-test"),
        hermes_home=home,
        utcnow=lambda: observed_at,
    )
    scheduler = MagicMock()
    service._sweep_once(
        restarted,
        coordinator,
        identity,
        lease.epoch,
        scheduler,
    )
    scheduler.submit.assert_not_called()
    assert coordinator.pending_wakes(
        identity,
        epoch=lease.epoch,
        now=observed_at,
        limit=100,
    ) == ()
    with restarted._connect() as connection:
        wake_outcomes = {
            row["outcome"]
            for row in connection.execute(
                "SELECT outcome FROM coordinator_wakes WHERE run_id=?", (run_id,)
            )
        }
    assert wake_outcomes == {"not_actionable"}

    cleanup = client.get("/api/plugins/workflow/cleanup/preview?older_than=0d")
    assert cleanup.status_code == 200
    assert run_id in cleanup.json()["run_ids"]
    assert run_directory.is_dir()


@pytest.mark.parametrize(
    ("action", "expected_version", "code"),
    [
        ("cancel", -1, "stale_state"),
        ("archive", None, "invalid_transition"),
    ],
)
def test_scheduled_mutation_errors_use_public_current_projection(
    tmp_path, monkeypatch, workflow_writer, action, expected_version, code
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(tmp_path / "package", name=f"scheduled-{code}")
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    run_id = _start_scheduled_mutation_run(store, package, key=code)
    current = store.get_run_status(run_id)
    if expected_version is None:
        expected_version = current["state_version"]

    response = TestClient(_app(_router())).post(
        f"/api/plugins/workflow/runs/{run_id}/{action}",
        json={"expected_version": expected_version},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    projection = response.json()["detail"]["current"]
    _assert_public_scheduled_mutation_projection(projection, waiting=True)


def test_post_runs_translates_authenticated_laptop_inputs_and_stages_fixture(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    symptom = "fictional startup delay"

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "laptop-diagnostic",
            "catalog_source": "showcase",
            "values": {"symptom": symptom},
            "idempotency_key": "laptop-authenticated-inputs",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 202
    run_id = response.json()["result"]["run_id"]
    run_directory = store.run_directory(run_id)
    manifest = json.loads((run_directory / "inputs.json").read_text())
    assert set(manifest) == {"arguments", "evidence"}
    assert symptom not in json.dumps(manifest)
    assert (run_directory / manifest["arguments"]["relative_path"]).read_text() == (
        symptom
    )
    assert (
        run_directory / manifest["evidence"]["relative_path"]
    ).read_bytes() == (
        Path(showcase_module.__file__).with_name("showcases")
        / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
    ).read_bytes()


def test_post_runs_uses_once_verified_fixture_bytes_after_source_mutation(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    fixture = copied / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
    authenticated = fixture.read_bytes()
    authenticated_digest = sha256(authenticated).hexdigest()
    mutated = b'{"MUTATED_AFTER_VERIFICATION":true}\n'
    observed_verified_inputs = None
    original_prepare = RunStore.prepare_run_snapshot

    def mutate_before_snapshot(self, package, *args, **kwargs):
        nonlocal observed_verified_inputs
        observed_verified_inputs = kwargs.get("verified_inputs")
        fixture.write_bytes(mutated)
        return original_prepare(self, package, *args, **kwargs)

    monkeypatch.setattr(RunStore, "prepare_run_snapshot", mutate_before_snapshot)
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router()), raise_server_exceptions=False).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "laptop-diagnostic",
            "catalog_source": "showcase",
            "values": {"symptom": "fictional slow startup"},
            "idempotency_key": "laptop-read-once",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 202
    assert observed_verified_inputs == {
        "evidence": (authenticated, authenticated_digest)
    }
    run_directory = store.run_directory(response.json()["result"]["run_id"])
    manifest = json.loads((run_directory / "inputs.json").read_text())
    sealed_fixture = run_directory / manifest["evidence"]["relative_path"]
    assert sealed_fixture.read_bytes() == authenticated
    assert manifest["evidence"]["sha256"] == authenticated_digest
    assert mutated not in sealed_fixture.read_bytes()
    assert fixture.read_bytes() == mutated


@pytest.mark.parametrize("reserved_name", ["arguments", "evidence"])
def test_post_runs_refuses_authenticated_internal_or_fixture_owned_names(
    tmp_path, monkeypatch, reserved_name
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    canary = "RESERVED-INPUT-CANARY"

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "laptop-diagnostic",
            "catalog_source": "showcase",
            "values": {reserved_name: canary},
            "idempotency_key": f"reserved-{reserved_name}",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "workflow_inputs_invalid"
    assert canary not in response.text
    _assert_no_admission_residue(store)


def test_post_runs_legacy_flat_workflow_still_accepts_arguments(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_catalog_workflow(home, workflow_writer, name="legacy-arguments")
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "legacy-arguments",
            "values": {"arguments": "ordinary portable input"},
            "idempotency_key": "legacy-arguments",
            "concurrency_policy": "allow",
        },
    )

    assert response.status_code == 202


def test_post_runs_authenticated_input_identity_joins_and_conflicts(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    client = TestClient(_app(_router()))
    body = {
        "workflow": "laptop-diagnostic",
        "catalog_source": "showcase",
        "values": {"symptom": "same fictional symptom"},
        "idempotency_key": "laptop-input-identity",
        "concurrency_policy": "queue",
    }

    created = client.post("/api/plugins/workflow/runs", json=body)
    joined = client.post("/api/plugins/workflow/runs", json=body)
    changed = client.post(
        "/api/plugins/workflow/runs",
        json={**body, "values": {"symptom": "changed fictional symptom"}},
    )

    assert created.status_code == joined.status_code == 202
    assert created.json()["result"]["admission_disposition"] == "created"
    assert joined.json()["result"]["admission_disposition"] == "existing"
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_conflict"


def test_post_runs_changed_authenticated_fixture_identity_conflicts_without_raw_data(
    tmp_path, monkeypatch, caplog
) -> None:
    home = tmp_path / "home"
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)
    client = TestClient(_app(_router()))
    symptom_canary = "SYMPTOM-CANARY-4f81e6"
    fixture_canary = "FIXTURE-CANARY-9a2d73"
    body = {
        "workflow": "laptop-diagnostic",
        "catalog_source": "showcase",
        "values": {"symptom": symptom_canary},
        "idempotency_key": "laptop-fixture-identity",
        "concurrency_policy": "queue",
    }
    captured_requests = []
    original_start_digest = RunStore._start_digest

    def capture_start_digest(request):
        captured_requests.append(request)
        return original_start_digest(request)

    monkeypatch.setattr(RunStore, "_start_digest", staticmethod(capture_start_digest))
    caplog.set_level(logging.DEBUG)
    created = client.post("/api/plugins/workflow/runs", json=body)
    assert created.status_code == 202
    run_id = created.json()["result"]["run_id"]
    fixture = copied / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
    fixture.write_text(json.dumps({"fixture": fixture_canary}) + "\n")
    _restamp_showcase_copy(copied, "laptop-diagnostic")
    showcase_module._clear_verified_showcase_cache_for_tests()

    conflict = client.post("/api/plugins/workflow/runs", json=body)

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert len(captured_requests) >= 2
    assert captured_requests[0].input_manifest_digest != (
        captured_requests[-1].input_manifest_digest
    )
    start_identity = json.dumps(
        {
            "inputs": captured_requests[-1].input_manifest_digest,
            "run_metadata": captured_requests[-1].run_metadata,
        },
        sort_keys=True,
    )
    status = store.get_run_status(run_id)
    durable_events = json.dumps(store.tail_events(run_id), sort_keys=True)
    responses = [
        created.content,
        conflict.content,
        client.get("/api/plugins/workflow/runs").content,
        client.get(f"/api/plugins/workflow/runs/{run_id}").content,
        client.get(f"/api/plugins/workflow/runs/{run_id}/events").content,
        client.get(
            f"/api/plugins/workflow/runs/{run_id}/evidence",
            params={"kind": "timeline"},
        ).content,
        start_identity.encode(),
        json.dumps(status["run_metadata"], sort_keys=True).encode(),
        durable_events.encode(),
        "\n".join(record.getMessage() for record in caplog.records).encode(),
    ]
    for canary in (symptom_canary.encode(), fixture_canary.encode()):
        assert all(canary not in response for response in responses)
    manifest = json.loads(
        (store.run_directory(run_id) / "inputs.json").read_text(encoding="utf-8")
    )
    assert set(manifest) == {"arguments", "evidence"}
    assert all(
        set(record) == {
            "relative_path",
            "size_bytes",
            "media_type",
            "sha256",
        }
        for record in manifest.values()
    )
    assert status["input_manifest_digest"] == captured_requests[0].input_manifest_digest


def test_post_runs_maps_shared_compatibility_refusal_to_conflict_before_persistence(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    _healthy_coordinator(store)

    def incompatible(package, _context, *, read_budget=None):
        compatibility = CompatibilityReport(
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
        return compatibility, build_risk_summary(
            package,
            compatibility,
            read_budget=read_budget,
        )

    monkeypatch.setattr(
        api_admission_module,
        "assess_package_execution",
        incompatible,
    )

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


@pytest.mark.parametrize("tampered_resource", ["catalog", "fixture"])
def test_post_runs_tampered_authenticated_input_bundle_fails_without_residue(
    tmp_path, monkeypatch, tampered_resource
) -> None:
    home = tmp_path / "home"
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    if tampered_resource == "catalog":
        resource = copied / "catalog.yaml"
        resource.write_text(
            resource.read_text(encoding="utf-8") + "tampered: true\n",
            encoding="utf-8",
        )
    else:
        resource = (
            copied
            / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
        )
        resource.write_text('{"tampered":true}\n', encoding="utf-8")
    store = RunStore(home)
    _healthy_coordinator(store)

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "laptop-diagnostic",
            "catalog_source": "showcase",
            "values": {"symptom": "fictional symptom"},
            "idempotency_key": f"tampered-{tampered_resource}",
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
    original_assess = api_admission_module.assess_package_execution
    assessments = 0

    def counted_assess(package, context, *, read_budget=None):
        nonlocal assessments
        assessments += 1
        return original_assess(package, context, read_budget=read_budget)

    monkeypatch.setattr(
        api_admission_module,
        "assess_package_execution",
        counted_assess,
    )

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


@pytest.mark.parametrize(
    ("values", "expected_code"),
    [
        ({}, "workflow_input_required"),
        (
            {"subject": "present", "undeclared": "rejected"},
            "workflow_inputs_invalid",
        ),
        ({"subject": "x" * 33}, "workflow_input_too_large"),
    ],
)
def test_post_runs_declared_inputs_return_typed_errors_without_residue(
    tmp_path, monkeypatch, workflow_writer, values, expected_code
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_declared_catalog_workflow(
        home,
        workflow_writer,
        name="api-declared",
        inputs={
            "subject": "{type: string, required: true, max_bytes: 32}",
        },
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    response = TestClient(_app(_router(), token=writer)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-declared",
            "values": values,
            "idempotency_key": "declared-request",
            "concurrency_policy": "allow",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": expected_code, "retryable": False}
    }
    _assert_no_admission_residue(store)


def test_post_runs_accepts_declared_text_input(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _trusted_declared_catalog_workflow(
        home,
        workflow_writer,
        name="api-declared-text",
        inputs={
            "subject": "{kind: text, required: true, max_bytes: 32}",
        },
    )
    store = RunStore(home)
    _healthy_coordinator(store)
    writer = TokenPrincipal(
        principal="writer", provider="test", scopes=("workflow:write",)
    )

    response = TestClient(_app(_router(), token=writer)).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-declared-text",
            "values": {"subject": "safe text"},
            "idempotency_key": "declared-text-request",
            "concurrency_policy": "allow",
        },
    )

    assert response.status_code == 202
    assert response.json()["result"]["admission_disposition"] == "created"
    assert "safe text" not in response.text


def test_declared_input_reservations_are_scoped_to_verified_showcases(
    tmp_path, workflow_writer
) -> None:
    workflow_path = workflow_writer(tmp_path / "package", name="declared-scope")
    workflow_path.with_name("example.hermes.yaml").write_text(
        """delivery_defaults:
  inputs:
    arguments: {kind: text, required: true, max_bytes: 32}
""",
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)

    ordinary = api_admission_module.validate_declared_api_values(
        package,
        {"arguments": "safe"},
    )
    with pytest.raises(ApiAdmissionError) as reserved:
        api_admission_module.validate_declared_api_values(
            package,
            {"arguments": "safe"},
            verified_value_bindings={"subject": "arguments"},
        )

    assert ordinary == {"arguments": "safe"}
    assert reserved.value.code == "workflow_inputs_invalid"
    assert "safe" not in str(reserved.value)


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


def test_post_runs_relocates_only_byte_caps_to_typed_endpoint_validation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    client = TestClient(_app(_router()))
    base = {
        "workflow": "bounded",
        "idempotency_key": "bounded",
        "concurrency_policy": "queue",
    }
    at_cap_values = {
        f"input-{index}": "x" * (4089 if index < 10 else 4088)
        for index in range(64)
    }
    aggregate_values = dict(at_cap_values)
    aggregate_values["input-0"] += "x"
    assert sum(
        len(name.encode("utf-8")) + len(value.encode("utf-8"))
        for name, value in at_cap_values.items()
    ) == 256 * 1024

    per_value = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "values": {"input": "x" * (70 * 1024)}},
    )
    aggregate = client.post(
        "/api/plugins/workflow/runs",
        json={
            **base,
            "values": aggregate_values,
        },
    )
    at_aggregate_cap = client.post(
        "/api/plugins/workflow/runs",
        json={
            **base,
            "values": at_cap_values,
        },
    )
    unrelated_schema = client.post(
        "/api/plugins/workflow/runs",
        json={**base, "workflow": ["not", "text"], "values": {}},
    )

    for response in (per_value, aggregate):
        assert response.status_code == 422
        assert response.json() == {
            "detail": {
                "code": "workflow_input_too_large",
                "retryable": False,
            }
        }
    assert unrelated_schema.status_code == 422
    assert isinstance(unrelated_schema.json()["detail"], list)
    assert at_aggregate_cap.status_code == 404
    assert at_aggregate_cap.json()["detail"]["code"] == "workflow_not_found"


@pytest.mark.parametrize(
    "values_json",
    [
        b'{"input":"\\ud800"}',
        b'{"\\ud800":"value"}',
        b'"\\ud800"',
        b'["\\ud800"]',
        b'{"input":{"nested":"\\ud800"}}',
    ],
    ids=("scalar", "key", "values-string", "values-list", "nested-value"),
)
def test_post_runs_rejects_unpaired_surrogate_shapes_as_ordinary_schema_error(
    tmp_path, monkeypatch, values_json
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    response = TestClient(
        _app(_router()), raise_server_exceptions=False
    ).post(
        "/api/plugins/workflow/runs",
        content=(
            b'{"workflow":"bounded","values":'
            + values_json
            + b',"idempotency_key":"bounded","concurrency_policy":"queue"}'
        ),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert "workflow_input_too_large" not in response.text
    assert r"\ud800" not in response.text


@pytest.mark.parametrize(
    "failure_path",
    (
        "blank-workflow",
        "blank-idempotency",
        "too-many-values",
        "non-portable-name",
        "colliding-names",
        "unrepresentable-name",
    ),
)
def test_post_runs_redacts_values_from_structural_validation_errors(
    tmp_path, monkeypatch, failure_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    canary = "REV7-CANARY-6f0d8a2c9b41"
    payload = {
        "workflow": "bounded",
        "values": {"input": canary},
        "idempotency_key": "bounded",
        "concurrency_policy": "queue",
    }
    if failure_path == "blank-workflow":
        payload["workflow"] = "   "
    elif failure_path == "blank-idempotency":
        payload["idempotency_key"] = "   "
    elif failure_path == "too-many-values":
        payload["values"] = {
            f"input-{index}": f"{canary}-{index}" for index in range(65)
        }
    elif failure_path == "non-portable-name":
        payload["values"] = {"foo/bar": canary}
    elif failure_path == "colliding-names":
        payload["values"] = {"Mode": f"{canary}-1", "mode": f"{canary}-2"}
    elif failure_path == "unrepresentable-name":
        payload["values"] = {"api_token": canary}

    response = TestClient(
        _app(_router()), raise_server_exceptions=False
    ).post("/api/plugins/workflow/runs", json=payload)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert canary not in response.text
    assert "workflow_input_too_large" not in response.text


def test_post_runs_preserves_omitted_values_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    response = TestClient(_app(_router())).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "default-values",
            "idempotency_key": "default-values",
            "concurrency_policy": "queue",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_not_found"


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


@pytest.mark.parametrize("endpoint", ["preview", "download"])
def test_artifact_endpoints_require_verified_authentication(endpoint) -> None:
    client = TestClient(_app(_router(), local_admin=False))

    response = client.get(
        "/api/plugins/workflow/runs/run-id/artifacts/"
        f"{'a' * 32}/{endpoint}"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {"code": "authentication_required"}
    }


def test_runs_list_never_opens_real_artifact_bodies(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home-list-metadata"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / "list-metadata",
        data=b"REAL_LIST_PUBLICATION_BODY",
        media_type="text/markdown; charset=utf-8",
    )
    publication_path = (
        f"publications/{artifact['publication_id']}/{artifact['content_name']}"
    )
    real_read = store_module._read_descriptor_relative

    def reject_publication_body(directory, relative_path, *, size_bytes):
        if str(relative_path) == publication_path:
            pytest.fail("GET runs must not open publication bodies")
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        reject_publication_body,
    )

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/runs?view=all"
    )

    assert response.status_code == 200
    assert any(
        run["run_id"] == admitted.run_id
        for run in response.json()["runs"]
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "unknown_media",
        "invalid_size_type",
        "duplicate_publication_id",
        "non_winning_attempt",
        "sealed_output_type_mismatch",
        "sealed_schema_mismatch",
        "legacy_unversioned",
    ],
)
def test_runs_list_rejects_corrupt_checked_typed_metadata_without_body_reads(
    tmp_path,
    monkeypatch,
    workflow_writer,
    corruption,
) -> None:
    home = tmp_path / f"home-list-corrupt-{corruption}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, admitted, _artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"list-corrupt-{corruption}",
        data=b"CORRUPT_LIST_PUBLICATION_BODY",
        media_type="text/markdown; charset=utf-8",
    )
    publication_id, content_name = _forge_checked_typed_descriptor(
        store,
        admitted.run_id,
        corruption,
    )
    publication_path = f"publications/{publication_id}/{content_name}"
    real_read = store_module._read_descriptor_relative

    def reject_publication_body(directory, relative_path, *, size_bytes):
        if str(relative_path) == publication_path:
            pytest.fail(
                "run-list metadata validation must not open publication bodies"
            )
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        reject_publication_body,
    )

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/runs?view=all"
    )

    assert response.status_code == 200
    listed = next(
        run
        for run in response.json()["runs"]
        if run["run_id"] == admitted.run_id
    )
    assert listed["status_authoritative"] is False
    assert listed["health"] == "storage_degraded"
    assert "typed_publication_integrity" in store._active_run_repair_reasons(
        admitted.run_id
    )


def test_text_artifact_preview_is_bounded_and_download_streams_verified_bytes(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    data = ("é" * 40_000).encode("utf-8")
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / "text-preview",
        data=data,
        media_type="text/markdown; charset=utf-8",
    )
    publication_id = artifact["publication_id"]
    base = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{publication_id}"
    )

    with TestClient(_app(_router())) as client:
        preview = client.get(f"{base}/preview")
        download = client.get(f"{base}/download")

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["publication_id"] == publication_id
    assert payload["media_type"] == "text/markdown; charset=utf-8"
    assert payload["size_bytes"] == len(data)
    assert payload["bytes_returned"] == 65_536
    assert payload["truncated"] is True
    assert isinstance(payload["content"], str)
    assert len(payload["content"].encode("utf-8")) <= 65_539
    assert len(preview.content) < 70_000
    assert download.status_code == 200
    assert download.content == data
    assert download.headers["content-type"] == "text/markdown; charset=utf-8"
    assert download.headers["content-length"] == str(len(data))
    assert download.headers["content-disposition"] == (
        f'attachment; filename="{publication_id}-content.md"'
    )


def test_artifact_preview_and_download_accept_producer_metadata_boundary(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home-metadata-boundary"
    monkeypatch.setenv("HERMES_HOME", str(home))
    output_type = "O" * 16_384
    session_id = "S" * 16_384
    body = b"BOUNDARY_BODY_MUST_ONLY_APPEAR_IN_EXPLICIT_CONTENT_RESPONSES"
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / "metadata-boundary",
        data=body,
        media_type="text/markdown; charset=utf-8",
        output_type=output_type,
        session_id=session_id,
    )
    base = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{artifact['publication_id']}"
    )

    with TestClient(_app(_router())) as client:
        preview = client.get(f"{base}/preview")
        download = client.get(f"{base}/download")
        evidence = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/evidence",
            params={"kind": "artifacts"},
        )

    assert preview.status_code == 200
    assert preview.json()["content"] == body.decode()
    assert len(preview.content) < 1_024
    assert download.status_code == 200
    assert download.content == body
    assert evidence.status_code == 200
    item = next(
        value
        for value in evidence.json()["items"]
        if value.get("publication_id") == artifact["publication_id"]
    )
    assert item["output_type"] == output_type
    assert item["session_id"] == session_id
    assert body not in evidence.content
    assert len(evidence.content) < 40_000


@pytest.mark.parametrize("endpoint", ["preview", "download"])
@pytest.mark.parametrize("fault_target", ["content", "sealed_definition"])
def test_artifact_endpoints_preserve_retryable_publication_unavailability(
    tmp_path,
    monkeypatch,
    workflow_writer,
    endpoint,
    fault_target,
) -> None:
    home = tmp_path / f"home-unavailable-{endpoint}-{fault_target}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"unavailable-{endpoint}-{fault_target}",
        data=b"retryable publication",
        media_type="text/markdown; charset=utf-8",
    )
    publication_path = (
        f"publications/{artifact['publication_id']}/{artifact['content_name']}"
    )
    target = (
        publication_path
        if fault_target == "content"
        else "definition.yaml"
    )
    fault_active = True
    real_read = store_module._read_descriptor_relative

    def transient_read_failure(directory, relative_path, *, size_bytes):
        if fault_active and str(relative_path) == target:
            raise ArchonOutputUnavailableError(
                "injected transient publication read failure"
            )
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        transient_read_failure,
    )
    base = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{artifact['publication_id']}"
    )

    with TestClient(
        _app(_router()),
        raise_server_exceptions=False,
    ) as client:
        unavailable = client.get(f"{base}/{endpoint}")
        fault_active = False
        recovered = client.get(f"{base}/{endpoint}")

    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": {
            "code": "artifact_temporarily_unavailable",
            "retryable": True,
        }
    }
    assert "typed_publication_integrity" not in (
        store._active_run_repair_reasons(admitted.run_id)
    )
    assert recovered.status_code == 200


@pytest.mark.parametrize("endpoint", ["preview", "download"])
@pytest.mark.parametrize(
    "error_number",
    [
        pytest.param(getattr(errno, name), id=name)
        for name in (
            "EAGAIN",
            "EIO",
            "EMFILE",
            "ENOMEM",
            "ENFILE",
            "ESTALE",
        )
        if hasattr(errno, name)
    ],
)
def test_artifact_endpoints_preserve_retryable_sealed_definition_lstat_errors(
    tmp_path,
    monkeypatch,
    workflow_writer,
    endpoint,
    error_number,
) -> None:
    home = tmp_path / f"home-lstat-{endpoint}-{error_number}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"lstat-{endpoint}-{error_number}",
        data=b"retryable sealed definition stat",
        media_type="text/markdown; charset=utf-8",
    )
    definition = store.run_directory(admitted.run_id) / "definition.yaml"
    fault_active = True
    real_lstat = Path.lstat

    def transient_definition_lstat(path):
        if fault_active and path == definition:
            raise OSError(
                error_number,
                "injected transient sealed-definition stat failure",
            )
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", transient_definition_lstat)
    base = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{artifact['publication_id']}"
    )

    with TestClient(
        _app(_router()),
        raise_server_exceptions=False,
    ) as client:
        unavailable = client.get(f"{base}/{endpoint}")
        fault_active = False
        recovered = client.get(f"{base}/{endpoint}")

    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": {
            "code": "artifact_temporarily_unavailable",
            "retryable": True,
        }
    }
    assert len(unavailable.content) < 256
    assert "typed_publication_integrity" not in (
        store._active_run_repair_reasons(admitted.run_id)
    )
    assert recovered.status_code == 200


@pytest.mark.parametrize(
    ("data", "expected_content", "expected_bytes", "truncated"),
    [
        (b'{"answer":42}', {"answer": 42}, 13, False),
        (
            json.dumps(
                {"payload": "x" * 70_000},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            None,
            0,
            True,
        ),
    ],
)
def test_json_artifact_preview_is_complete_or_omitted(
    tmp_path,
    monkeypatch,
    workflow_writer,
    data,
    expected_content,
    expected_bytes,
    truncated,
) -> None:
    home = tmp_path / f"home-{len(data)}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"json-preview-{len(data)}",
        data=data,
        media_type="application/json",
    )

    with TestClient(_app(_router())) as client:
        response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
            f"{artifact['publication_id']}/preview"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == expected_content
    assert payload["bytes_returned"] == expected_bytes
    assert payload["size_bytes"] == len(data)
    assert payload["truncated"] is truncated
    assert len(response.content) < 70_000


def test_json_artifact_preview_rejects_noncanonical_nonfinite_content(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home-nonfinite-json"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / "json-preview-nonfinite",
        data=b'{"value":NaN}',
        media_type="application/json",
    )

    with TestClient(
        _app(_router()),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
            f"{artifact['publication_id']}/preview"
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "typed_publication_integrity"


@pytest.mark.parametrize(
    "data",
    [
        b'{"value":1e10000}',
        b"[" * 1_200 + b"0" + b"]" * 1_200,
        b'{"a":1,"a":2}',
        b'{ "b":2, "a":1 }',
    ],
    ids=[
        "overflowing-number",
        "excessive-nesting",
        "duplicate-keys",
        "noncanonical-bytes",
    ],
)
def test_json_artifact_preview_rejects_noncanonical_or_unsafe_json(
    tmp_path,
    monkeypatch,
    workflow_writer,
    data,
) -> None:
    home = tmp_path / f"home-invalid-json-{sha256(data).hexdigest()[:8]}"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"invalid-json-{sha256(data).hexdigest()[:8]}",
        data=data,
        media_type="application/json",
    )

    with TestClient(
        _app(_router()),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
            f"{artifact['publication_id']}/preview"
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "typed_publication_integrity"}
    }
    assert len(response.content) < 256


@pytest.mark.parametrize(
    ("publication_id", "expected_code"),
    [
        ("f" * 32, "artifact_not_found"),
        ("..%2Fmetadata.json", None),
    ],
)
def test_artifact_endpoints_reject_unknown_and_path_like_ids(
    tmp_path,
    monkeypatch,
    workflow_writer,
    publication_id,
    expected_code,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, admitted, _artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / f"unknown-id-{publication_id[0]}",
        data=b"body",
        media_type="text/markdown; charset=utf-8",
    )

    with TestClient(_app(_router())) as client:
        response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
            f"{publication_id}/preview"
        )

    assert response.status_code == 404
    if expected_code is not None:
        assert response.json()["detail"]["code"] == expected_code


def test_artifact_lookup_enforces_operator_scope_and_profile(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "owner-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    owner_scope = "service:test:owner"
    _store, admitted, artifact = _published_run(
        home,
        workflow_writer,
        tmp_path / "owned-artifact",
        data=b"owned",
        media_type="text/markdown; charset=utf-8",
        scope=owner_scope,
    )
    reader = TokenPrincipal(
        principal="other",
        provider="test",
        scopes=("workflow:read",),
    )
    path = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{artifact['publication_id']}/preview"
    )

    with TestClient(_app(_router(), token=reader)) as client:
        wrong_scope = client.get(path)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-home"))
    with TestClient(_app(_router())) as client:
        wrong_profile = client.get(path)

    assert wrong_scope.status_code == 404
    assert wrong_scope.json()["detail"]["code"] == "run_not_found"
    assert wrong_profile.status_code == 404
    assert wrong_profile.json()["detail"]["code"] == "run_not_found"


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


def test_desktop_rejects_laptop_showcase_without_real_ai_under_offline_mode(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    real_runner_calls = 0

    def forbidden_real_run(*_args, **_kwargs):
        nonlocal real_runner_calls
        real_runner_calls += 1
        raise AssertionError("Desktop showcase rejection selected real AI")

    monkeypatch.setattr(PluginAgentRunner, "run", forbidden_real_run)
    started = run_showcase(
        "laptop-diagnostic",
        hermes_home=home,
        symptom="fictional Desktop rejection",
    )
    pending = started["pending_interaction"]
    client = TestClient(_app(_router()))

    rejected = client.post(
        f"/api/plugins/workflow/runs/{started['run_id']}/reject",
        json={
            "expected_version": started["state_version"],
            "interaction_id": pending["interaction_id"],
            "reason": "keep the fictional plan manual",
        },
    )

    assert rejected.status_code == 200
    store = RunStore(home)
    reworked = RunScheduler(
        store,
        agent_runner=PluginAgentRunner(plugin_id="workflow"),
    ).advance(started["run_id"])
    assert reworked["status"] == "paused"
    assert reworked["nodes"]["review-plan"]["approval_rework_attempts"] == 1
    assert real_runner_calls == 0


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


def test_resume_authenticates_always_run_before_desktop_mutation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(
            tmp_path / "resume-package",
            name="desktop-resume-authentication",
            nodes=[
                {"id": "cached", "bash": "true"},
                {
                    "id": "fail",
                    "bash": "false",
                    "depends_on": ["cached"],
                },
            ],
        )
    )
    store = RunStore(home)
    admitted = _start(store, package, "desktop-resume-authentication")
    scheduler = RunScheduler(store)
    try:
        assert scheduler.advance(admitted.run_id)["status"] == "failed"
    finally:
        scheduler.shutdown(deadline_seconds=2)
    before = store.get_run_status(admitted.run_id)
    definition_path = store.run_directory(admitted.run_id) / "definition.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["nodes"][0]["always_run"] = True
    definition_path.write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )
    module = _module()

    with TestClient(_app(module.router)) as client:
        response = client.post(
            f"/api/plugins/workflow/runs/{admitted.run_id}/resume",
            json={"expected_version": before["state_version"]},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "workflow_snapshot_integrity_mismatch"
    )
    assert store.get_run_status(admitted.run_id) == before


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
