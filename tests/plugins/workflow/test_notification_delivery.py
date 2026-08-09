from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal
from hermes_cli.plugin_invocation import DeliveryReceipt, PluginInvocationContext

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
from gateway.plugin_delivery import GatewayPluginDeliveryPort
from gateway.config import Platform
from gateway.session import SessionSource


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_notification_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _app(
    router,
    *,
    scopes=("workflow:admin",),
    principal="desktop-client",
    local_admin: bool = False,
):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        if local_admin:
            request.state.local_admin_authenticated = True
        else:
            request.state.token_principal = TokenPrincipal(
                principal=principal,
                provider="test",
                scopes=tuple(scopes),
            )
            request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _terminal_run(
    store,
    tmp_path,
    workflow_writer,
    *,
    name: str,
    operator_scope: str | None = None,
) -> str:
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
            operator_scope=operator_scope,
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    return admitted.run_id


def test_desktop_delivery_is_operator_scoped_with_unrestricted_local_admin(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "operator-scoped-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    scopes = {
        "alice": "service:test:alice",
        "bob": "service:test:bob",
    }
    run_ids = {
        principal: _terminal_run(
            store,
            tmp_path,
            workflow_writer,
            name=f"delivery-{principal}",
            operator_scope=scope,
        )
        for principal, scope in scopes.items()
    }
    outbox = NotificationOutbox(store)
    notification_ids = {
        principal: [
            outbox.record(
                run_id=run_ids[principal],
                kind=kind,
                destination="desktop",
                transition_version=index,
                payload={},
                now=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            for index, kind in enumerate(
                ("approval_required", "input_required", "cancellation"),
                start=1,
            )
        ]
        for principal in scopes
    }
    for principal in scopes:
        leased = outbox.lease(
            destination="desktop",
            owner_id=f"stale-{principal}",
            now=datetime(2020, 1, 1, tzinfo=timezone.utc),
            lease_seconds=1,
            limit=3,
            operator_scope=scopes[principal],
        )
        assert {item["notification_id"] for item in leased} == set(
            notification_ids[principal]
        )

    module = _module()
    alice = TestClient(
        _app(module.router, scopes=("workflow:delivery",), principal="alice")
    )
    bob = TestClient(
        _app(module.router, scopes=("workflow:delivery",), principal="bob")
    )

    alice_lease = alice.get(
        "/api/plugins/workflow/notifications/lease?client_id=alice-client"
    )
    assert alice_lease.status_code == 200
    assert {
        item["notification_id"] for item in alice_lease.json()["items"]
    } == set(notification_ids["alice"])
    with store._connect() as connection:
        bob_rows = connection.execute(
            "SELECT state, lease_owner FROM workflow_notification_outbox "
            "WHERE run_id=? AND notification_id IN (?, ?, ?) "
            "ORDER BY notification_id",
            (run_ids["bob"], *notification_ids["bob"]),
        ).fetchall()
    assert {(row["state"], row["lease_owner"]) for row in bob_rows} == {
        ("leased", "stale-bob")
    }

    nonexistent = alice.post(
        "/api/plugins/workflow/notifications/missing-notification/ack",
        json={"client_id": "alice-client"},
    )
    for suffix, foreign_id in zip(
        ("ack", "fail", "dismiss"), notification_ids["bob"], strict=True
    ):
        foreign = alice.post(
            f"/api/plugins/workflow/notifications/{foreign_id}/{suffix}",
            json={"client_id": "alice-client"},
        )
        assert foreign.status_code == nonexistent.status_code == 404
        assert foreign.json() == nonexistent.json() == {
            "detail": {"code": "notification_not_found"}
        }

    for suffix, own_id in zip(
        ("ack", "fail", "dismiss"), notification_ids["alice"], strict=True
    ):
        response = alice.post(
            f"/api/plugins/workflow/notifications/{own_id}/{suffix}",
            json={"client_id": "alice-client"},
        )
        assert response.status_code == 200

    bob_lease = bob.get(
        "/api/plugins/workflow/notifications/lease?client_id=bob-client"
    )
    assert bob_lease.status_code == 200
    assert {item["notification_id"] for item in bob_lease.json()["items"]} == set(
        notification_ids["bob"]
    )
    bob_ack = bob.post(
        f"/api/plugins/workflow/notifications/{notification_ids['bob'][0]}/ack",
        json={"client_id": "bob-client"},
    )
    assert bob_ack.status_code == 200

    unrestricted_ids = {
        outbox.record(
            run_id=run_ids[principal],
            kind="completion",
            destination="desktop",
            transition_version=99,
            payload={"status": "succeeded"},
        )
        for principal in scopes
    }
    local_admin = TestClient(_app(module.router, local_admin=True))
    unrestricted = local_admin.get(
        "/api/plugins/workflow/notifications/lease?client_id=local-admin"
    )
    assert unrestricted.status_code == 200
    assert unrestricted_ids <= {
        item["notification_id"] for item in unrestricted.json()["items"]
    }
    for notification_id in unrestricted_ids:
        receipt = local_admin.post(
            f"/api/plugins/workflow/notifications/{notification_id}/ack",
            json={"client_id": "local-admin"},
        )
        assert receipt.status_code == 200


@pytest.mark.parametrize("outbox_state", ["pending", "leased", "dead"])
def test_pruning_preserves_unexpired_route_referenced_by_gateway_outbox(
    tmp_path, outbox_state
) -> None:
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    port = GatewayPluginDeliveryPort(
        tmp_path,
        profile="default",
        sender=lambda _route, _text: DeliveryReceipt(status="delivered"),
        now=lambda: now,
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="user-1",
        profile="default",
    )
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability=None,
    )
    capability = port.mint_return_route(source, invocation)
    store = RunStore(tmp_path)
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id=f"route-{outbox_state}",
        kind="completion",
        destination=f"gateway:{capability}",
        transition_version=1,
        payload={"status": "succeeded"},
        now=now,
    )
    if outbox_state in {"leased", "dead"}:
        assert outbox.lease_gateway(owner_id="gateway", now=now, limit=1)
    if outbox_state == "dead":
        assert outbox.terminal_fail(
            notification_id,
            owner_id="gateway",
            error="permanent",
            now=now,
        )

    assert port.prune_expired(limit=100) == {"receipts": 0, "routes": 0}
    assert (
        port.deliver(capability, "still authorized", f"delivery-{outbox_state}").status
        == "delivered"
    )
    with store._connect() as connection:
        destination = connection.execute(
            "SELECT destination FROM workflow_notification_outbox "
            "WHERE notification_id=?",
            (notification_id,),
        ).fetchone()[0]
    assert destination == f"gateway:{capability}"


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
    client = TestClient(_app(_module().router, local_admin=True))

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


def test_desktop_projection_failure_retains_fixed_fallback_reason(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "projection-fallback-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="projection-fallback-run",
        kind="completion",
        destination="desktop",
        transition_version=1,
        payload={"status": "succeeded"},
    )
    client = TestClient(_app(_module().router, local_admin=True))
    lease = client.get(
        "/api/plugins/workflow/notifications/lease?client_id=electron-projection"
    )
    assert lease.status_code == 200
    assert lease.json()["items"][0]["notification_id"] == notification_id

    failed = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/fail",
        json={"client_id": "electron-projection"},
    )

    assert failed.status_code == 200
    assert failed.json()["outcome"] == "retry_scheduled"
    history = outbox.history(run_id="projection-fallback-run")
    assert history[0]["last_error"] == "projection_failed"


def test_desktop_projection_failure_normalizes_free_form_detail(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "projection-free-form-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    notification_id = outbox.record(
        run_id="projection-free-form-run",
        kind="completion",
        destination="desktop",
        transition_version=1,
        payload={"status": "succeeded"},
    )
    canary = "private desktop provider session /path history"
    client = TestClient(_app(_module().router, local_admin=True))
    lease = client.get(
        "/api/plugins/workflow/notifications/lease?client_id=electron-free-form"
    )
    assert lease.status_code == 200
    assert lease.json()["items"][0]["notification_id"] == notification_id

    failed = client.post(
        f"/api/plugins/workflow/notifications/{notification_id}/fail",
        json={"client_id": "electron-free-form", "error": canary},
    )

    assert failed.status_code == 200
    history = outbox.history(run_id="projection-free-form-run")
    assert history[0]["last_error"] == "notification delivery failed"
    assert canary not in json.dumps(history, sort_keys=True)


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
    client = TestClient(_app(_module().router, local_admin=True))
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
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    run_id = _terminal_run(
        store,
        tmp_path,
        workflow_writer,
        name="run-delivery",
        operator_scope="service:test:desktop-client",
    )
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id=run_id,
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
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    run_id = _terminal_run(
        store,
        tmp_path,
        workflow_writer,
        name="run-gateway",
        operator_scope="service:test:desktop-client",
    )
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id=run_id,
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
    assert outbox.history(run_id=run_id)[0]["state"] == "leased"


def test_dead_letter_admin_retry_ack_clears_cleanup_dependency(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    run_id = _terminal_run(
        store,
        tmp_path,
        workflow_writer,
        name="dead-retry",
        operator_scope="service:test:desktop-client",
    )
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

    allowed = TestClient(_app(module.router, local_admin=True)).post(
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


def test_concurrent_gateway_drainers_honor_outbox_lease(tmp_path) -> None:
    store = RunStore(tmp_path / "home")
    outbox_a = NotificationOutbox(store)
    outbox_b = NotificationOutbox(RunStore(tmp_path / "home"))
    run_id = "concurrent-drainers"
    notification_id = outbox_a.record(
        run_id=run_id,
        kind="completion",
        destination="gateway:opaque-capability",
        transition_version=1,
        payload={"status": "succeeded"},
    )
    leased = threading.Event()
    release = threading.Event()
    sender_calls: list[str] = []
    drained_counts: list[int] = []

    class Port:
        def deliver(self, route_capability, text, idempotency_key):
            assert route_capability == "opaque-capability"
            sender_calls.append(notification_id)
            leased.set()
            assert release.wait(timeout=5)
            return DeliveryReceipt(status="delivered", transport_id="message-1")

    def drain_a() -> None:
        drained_counts.append(
            WorkflowCoordinatorService._deliver_gateway_notifications(
                outbox_a,
                Port(),
                owner_id="drainer-a",
            )
        )

    thread = threading.Thread(target=drain_a)
    thread.start()
    assert leased.wait(timeout=5)
    drained_counts.append(
        WorkflowCoordinatorService._deliver_gateway_notifications(
            outbox_b,
            Port(),
            owner_id="drainer-b",
        )
    )
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert sorted(drained_counts) == [0, 1]
    assert sender_calls == [notification_id]
    history = outbox_a.history(run_id=run_id)
    assert len(history) == 1
    assert history[0]["state"] == "delivered"


def test_read_apis_never_expose_gateway_return_route_capability(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    capability = "live-seven-day-bearer-capability"
    store = RunStore(home)
    assert CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="gateway-read-test",
            host_kind="gateway",
            host_instance_id="gateway-read-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    ).is_leader
    package = load_workflow(
        workflow_writer(tmp_path / "read-api", name="gateway-read-api")
    )
    snapshot = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="chat",
            idempotency_key="gateway-read-api",
            concurrency_key=package.definition.name,
            execution_mode="background",
            operator_scope="service:test:desktop-client",
            provenance=TriggerProvenance(
                source="chat",
                assurance="verified_adapter",
                intent_key="gateway-read-api",
                source_instance="gateway:telegram",
                actor_id="gateway:telegram:user-1",
                return_route=capability,
            ),
        ),
        immutable_snapshot=snapshot,
    )
    outbox = NotificationOutbox(store)
    outbox.record(
        run_id=admitted.run_id,
        kind="completion",
        destination="desktop",
        transition_version=1,
        payload={"status": "succeeded"},
    )
    stored_gateway = next(
        item
        for item in outbox.history(run_id=admitted.run_id)
        if item["destination"] == "gateway:opaque"
    )
    assert capability not in stored_gateway["transition_key"]
    client = TestClient(_app(_module().router))

    run_response = client.get(f"/api/plugins/workflow/runs/{admitted.run_id}")
    notification_response = client.get(
        f"/api/plugins/workflow/runs/{admitted.run_id}/evidence",
        params={"kind": "notifications"},
    )

    assert run_response.status_code == 200
    assert notification_response.status_code == 200
    assert capability not in run_response.text
    assert capability not in notification_response.text
    assert "return_route" not in run_response.json()["provenance"]
    notification_items = notification_response.json()["items"]
    assert notification_items
    assert all(
        set(item)
        == {
            "item_type",
            "notification_id",
            "kind",
            "state",
            "transition_version",
        }
        for item in notification_items
    )


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


def test_authenticated_gateway_explicit_phase4_run_seals_profile_include(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    from types import MappingProxyType, SimpleNamespace

    import plugins.workflow.language as language_module
    from plugins.workflow.cli import _resolve_compilation
    from plugins.workflow.models import WorkflowLanguageProfile

    monkeypatch.setattr(
        language_module,
        "CURRENT_NORMALIZER_BY_PROFILE",
        MappingProxyType({
            WorkflowLanguageProfile.HERMES_LEGACY: 2,
            WorkflowLanguageProfile.ARCHON_2026_07: 4,
        }),
    )
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    root = workflow_writer(
        tmp_path / "explicit",
        name="gateway-explicit-root",
        filename="gateway-explicit-root.yaml",
        nodes=[{"id": "child", "include": "gateway-explicit-child"}],
    )
    root.with_name("gateway-explicit-root.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    workflow_writer(
        home / "workflows",
        name="gateway-explicit-child",
        filename="gateway-explicit-child.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    compilation = _resolve_compilation(
        SimpleNamespace(workdir=workdir, hermes_home=home),
        str(root),
    )
    risk = build_risk_summary(
        compilation.package,
        assess_compatibility(compilation.package),
        compilation=compilation,
    )
    WorkflowTrustStore(home).trust(
        compilation.composite_digest,
        actor="test-operator",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    assert CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="gateway-phase4-test",
            host_kind="gateway",
            host_instance_id="gateway-phase4-test",
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
            f"run {shlex.quote(str(root))} --idempotency-key phase4-explicit",
            invocation,
            hermes_home=home,
            workdir=workdir,
        )
    )

    assert response["ok"] is True
    run = store.get_run_status(
        response["result"]["run_id"],
        operator_scope=invocation.operator_scope,
    )
    assert run["snapshot_format_version"] == 2
    assert run["definition_digest"] == compilation.composite_digest


def test_gateway_run_refuses_declared_archon_incompatibility_before_persistence(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    workflow_path = workflow_writer(
        tmp_path / "package",
        name="gateway-archon-blocked",
        nodes=[{"id": "start", "prompt": "x", "effort": "high"}],
    )
    workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
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
            "--idempotency-key archon-refusal",
            invocation,
            hermes_home=home,
            workdir=tmp_path,
        )
    )

    assert response["ok"] is False
    assert response["error"] == "workflow_compatibility_blocked"
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []
