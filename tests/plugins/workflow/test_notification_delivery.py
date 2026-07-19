from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


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
