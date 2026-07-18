from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal

from plugins.workflow.notifications import NotificationOutbox
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
