from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal
from hermes_cli.plugin_invocation import PluginInvocationContext

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.gateway_command import workflow_gateway_command
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_notification_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _app(router, *, scopes=("workflow:admin",), principal="desktop-client"):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.token_principal = TokenPrincipal(
            principal=principal,
            provider="test",
            scopes=tuple(scopes),
        )
        request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _terminal_run(store, tmp_path, workflow_writer, *, name: str) -> str:
    package = load_workflow(workflow_writer(tmp_path / name, name=name))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    return admitted.run_id


def test_desktop_delivery_is_leased_until_explicit_electron_ack(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="run-api",
        kind="approval_required",
        destination="desktop",
        transition_version=2,
        payload={"workflow": "review", "interaction_id": "gate"},
    )
    client = TestClient(_app(_module().router))

    lease = client.get(
        "/api/plugins/workflow/notifications/lease?client_id=electron-stable"
    )
    assert lease.status_code == 200
    assert lease.json()["items"][0]["notification_id"] == notification_id
    assert client.get(
        "/api/plugins/workflow/notifications/lease?client_id=other"
    ).json()["items"] == []
    wrong = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/ack",
        json={"client_id": "other"},
    )
    assert wrong.status_code == 409
    ack = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/ack",
        json={"client_id": "electron-stable"},
    )
    assert ack.status_code == 200
    assert outbox.history(run_id="run-api")[0]["state"] == "delivered"


def test_dismissal_records_presentation_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="run-dismiss",
        kind="failure",
        destination="desktop",
        transition_version=3,
        payload={},
    )
    client = TestClient(_app(_module().router))
    client.get("/api/plugins/workflow/notifications/lease?client_id=electron")

    response = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/dismiss",
        json={"client_id": "electron"},
    )

    assert response.status_code == 200
    fact = outbox.pending_attention(run_id="run-dismiss")[0]
    assert fact["state"] == "leased"
    assert fact["dismissed_at"] is not None


def test_delivery_scope_can_lease_its_bound_desktop_projection(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="run-delivery",
        kind="approval_required",
        destination="desktop",
        transition_version=1,
        payload={},
    )

    response = TestClient(
        _app(_module().router, scopes=("workflow:delivery",))
    ).get("/api/plugins/workflow/notifications/lease?client_id=electron")

    assert response.status_code == 200
    assert response.json()["items"][0]["notification_id"] == notification_id


def test_delivery_scope_cannot_ack_another_bound_destination(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="run-gateway",
        kind="approval_required",
        destination="gateway:verified-route",
        transition_version=1,
        payload={},
    )
    assert outbox.lease(
        destination="gateway:verified-route",
        owner_id="client-controlled-owner",
        lease_seconds=30,
        limit=1,
    )

    response = TestClient(
        _app(
            _module().router,
            scopes=("workflow:delivery", "workflow:admin"),
        )
    ).post(
        f"/api/plugins/workflow/notifications/{notification_id}/ack",
        json={"client_id": "client-controlled-owner"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_delivery_scope_mismatch"
    assert outbox.history(run_id="run-gateway")[0]["state"] == "leased"


def test_dead_letter_admin_retry_ack_clears_cleanup_dependency(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    run_id = _terminal_run(store, tmp_path, workflow_writer, name="dead-retry")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    notification_id = outbox.record(
        run_id=run_id,
        kind="failure",
        destination="desktop",
        transition_version=999,
        payload={"error": "projection failed"},
        now=now,
    )
    for attempt in range(8):
        attempt_at = now + timedelta(minutes=10 * attempt)
        assert outbox.lease(
            destination="desktop",
            owner_id="electron",
            now=attempt_at,
            limit=1,
        )[0]["notification_id"] == notification_id
        assert outbox.fail(
            notification_id,
            owner_id="electron",
            error=f"failure-{attempt}",
            now=attempt_at,
        )
    assert outbox.pending_attention(run_id=run_id)[0]["state"] == "dead"
    assert store.cleanup_runs(older_than=timedelta(0))["confirmation_token"] is None

    module = _module()
    denied = TestClient(
        _app(module.router, scopes=("workflow:delivery",))
    ).post(f"/api/plugins/workflow/notifications/{notification_id}/retry")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "workflow_admin_required"

    client = TestClient(_app(module.router))
    retried = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["outcome"] == "requeued"
    lease = client.get(
        "/api/plugins/workflow/notifications/lease?client_id=electron-retry"
    )
    assert lease.status_code == 200
    assert lease.json()["items"][0]["notification_id"] == notification_id
    ack = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/ack",
        json={"client_id": "electron-retry"},
    )
    assert ack.status_code == 200

    preview = store.cleanup_runs(older_than=timedelta(0))
    assert preview["confirmation_token"]
    decisions = [
        item["payload"]
        for item in outbox.history(run_id=run_id)
        if item["payload"].get("decision")
    ]
    assert [item["decision"] for item in decisions[:2]] == [
        "dead_letter_retried",
        "terminal_dead_letter",
    ]
    assert decisions[0]["authority_scope"] == "service:test:desktop-client"


def test_notification_prune_requires_admin_authority(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    now = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    notification_id = outbox.record(
        run_id="run-prune-api",
        kind="completion",
        destination="desktop",
        transition_version=1,
        payload={},
        now=now,
    )
    assert outbox.lease(
        destination="desktop", owner_id="electron", now=now, limit=1
    )
    assert outbox.ack(notification_id, owner_id="electron", now=now)
    module = _module()

    denied = TestClient(
        _app(module.router, scopes=("workflow:delivery",))
    ).post(
        "/api/plugins/workflow/notifications/prune",
        json={"older_than": "0d", "limit": 20},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "workflow_admin_required"

    allowed = TestClient(_app(module.router)).post(
        "/api/plugins/workflow/notifications/prune",
        json={"older_than": "0d", "limit": 20},
    )
    assert allowed.status_code == 200
    assert allowed.json()["pruned"] == 1


def test_verified_gateway_route_is_projected_and_delivered_by_coordinator(
    tmp_path, workflow_writer
) -> None:
    capability = "opaque-server-capability"
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    assert CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="gateway-test",
            host_kind="gateway",
            host_instance_id="gateway-test",
            pid=1,
            process_start_time=None,
        ),
        now=now,
        lease_seconds=60,
    ).is_leader
    package = load_workflow(workflow_writer(tmp_path / "gateway", name="gateway-run"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="gateway-run",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="chat",
            idempotency_key="gateway-run",
            idempotency_namespace="gateway:default:telegram:user-1",
            concurrency_key="gateway-run",
            execution_mode="background",
            provenance=TriggerProvenance(
                source="chat",
                assurance="verified_adapter",
                intent_key="gateway-run",
                source_instance="gateway:telegram",
                actor_id="gateway:telegram:user-1",
                return_route=capability,
            ),
        ),
        immutable_snapshot=prepared,
    )
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id=admitted.run_id,
        kind="completion",
        destination="desktop",
        transition_version=99,
        payload={"workflow": "gateway-run", "status": "succeeded"},
    )
    deliveries = []

    class Port:
        def deliver(self, route_capability, text, idempotency_key):
            deliveries.append((route_capability, text, idempotency_key))
            from hermes_cli.plugin_invocation import DeliveryReceipt

            return DeliveryReceipt(status="delivered", transport_id="message-1")

    delivered = WorkflowCoordinatorService._deliver_gateway_notifications(
        outbox,
        Port(),
        owner_id="coordinator:1",
    )

    assert delivered == 1
    assert deliveries[0][0] == capability
    assert deliveries[0][2] != notification_id
    gateway_history = [
        item
        for item in outbox.history(run_id=admitted.run_id)
        if item["destination"].startswith("gateway:")
    ]
    assert gateway_history[0]["state"] == "delivered"


def test_authenticated_gateway_command_starts_real_background_run(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    workflow_path = workflow_writer(tmp_path / "package", name="gateway-real")
    package = load_workflow(workflow_path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        digest.sha256,
        actor="test-operator",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    assert CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="gateway-test",
            host_kind="gateway",
            host_instance_id="gateway-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    ).is_leader
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )

    response = json.loads(
        workflow_gateway_command(
            f"run {shlex.quote(str(workflow_path))} "
            "--idempotency-key stable-request",
            invocation,
            hermes_home=home,
            workdir=tmp_path,
        )
    )

    assert response["ok"] is True
    run = store.get_run_status(
        response["result"]["run_id"],
        operator_scope=invocation.operator_scope,
    )
    assert run["execution_mode"] == "background"
    assert run["provenance"]["assurance"] == "verified_adapter"
    assert run["provenance"]["actor_id"] == invocation.principal
    assert run["provenance"]["return_route"] == "opaque-capability"
