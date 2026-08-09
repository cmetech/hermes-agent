from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hermes_cli.dashboard_auth.base import TokenPrincipal
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.store import RunStore


_CANARY = "PROMPT_COMMAND_PROVIDER_PAYLOAD_FEEDBACK_CANARY_20260808"
_NOW = datetime(2026, 8, 8, 18, tzinfo=timezone.utc)


class _MustNotBeVisited:
    def __str__(self) -> str:
        raise AssertionError("unknown notification values must not be traversed")


def _strict_json(raw: str) -> object:
    return json.loads(
        raw,
        parse_constant=lambda value: (_ for _ in ()).throw(
            AssertionError(f"non-finite JSON constant survived: {value}")
        ),
    )


def _notification_api_module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    name = "workflow_notification_closed_contract_api"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _authenticated_app(router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.token_principal = TokenPrincipal(
            principal="desktop-contract",
            provider="test",
            scopes=("workflow:delivery",),
        )
        request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _closed_transition_payload(*, state_version: int) -> dict[str, object]:
    return {
        "payload_type": "workflow_transition",
        "workflow": "deploy",
        "status": "failed",
        "event_type": "run_failed",
        "node_id": "build",
        "interaction": {
            "type": "reconcile",
            "interaction_id": "reconcile-1",
        },
        "code": "provider_capability_drift",
        "mismatched_fields": ["endpoint_sha256"],
        "state_version": state_version,
        "next_actions": ["status", "events", "resume", "retry", "abandon"],
    }


def test_notification_write_codec_never_traverses_or_copies_unknown_values(
    tmp_path,
) -> None:
    store = RunStore(tmp_path / "home")
    outbox = NotificationOutbox(store)
    cycle: dict[str, object] = {"canary": _CANARY}
    cycle["cycle"] = cycle
    deep: object = _CANARY
    for _ in range(20):
        deep = [deep]
    wide = {f"field-{index}": _CANARY for index in range(250)}

    notification_id = outbox.record(
        run_id="run-closed-write",
        kind="failure",
        destination="desktop",
        transition_version=7,
        payload={
            "workflow": "deploy",
            "status": "failed",
            "event_type": "run_failed",
            "node_id": "build",
            "interaction": {
                "type": "reconcile",
                "interaction_id": "reconcile-1",
                "message": _CANARY,
            },
            "code": "provider_capability_drift",
            "mismatched_fields": ["endpoint_sha256", "arbitrary-field"],
            "unknown_object": _MustNotBeVisited(),
            "unknown_set": {_CANARY},
            "unknown_cycle": cycle,
            "unknown_deep": deep,
            "unknown_wide": wide,
            "unknown_nan": float("nan"),
            _CANARY: "value-under-tainted-key",
            "credential_url": f"https://user:pass@example.test/{_CANARY}",
            "temporary_path": f"/private/tmp/{_CANARY}",
        },
        now=_NOW,
    )

    with store._connect() as connection:
        outbox_raw = str(
            connection.execute(
                "SELECT payload_json FROM workflow_notification_outbox "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchone()["payload_json"]
        )
        fact_raw = str(
            connection.execute(
                "SELECT payload_json FROM workflow_notification_facts "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchone()["payload_json"]
        )

    assert _strict_json(outbox_raw) == _closed_transition_payload(state_version=7)
    assert _strict_json(fact_raw) == _closed_transition_payload(state_version=7)
    assert _CANARY not in outbox_raw + fact_raw


def test_notification_read_codec_replaces_direct_legacy_rows_with_safe_recovery(
    tmp_path,
) -> None:
    store = RunStore(tmp_path / "home")
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id="run-legacy-row",
        kind="failure",
        destination="desktop",
        transition_version=9,
        payload={"status": "failed"},
        now=_NOW,
    )
    legacy = json.dumps(
        {
            "payload_type": "made_up",
            "status": "made_up",
            "code": "made_up_but_safe",
            "next_actions": ["status", "made-up-action", "cancel"],
            "nested": [{"prompt": _CANARY}],
        }
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_notification_outbox SET kind=?, payload_json=? "
            "WHERE notification_id=?",
            (f"kind-{_CANARY}", legacy, notification_id),
        )
        connection.execute(
            "UPDATE workflow_notification_facts SET kind=?, payload_json=? "
            "WHERE notification_id=?",
            (f"kind-{_CANARY}", legacy, notification_id),
        )

    leased = outbox.lease(
        destination="desktop",
        owner_id="electron-contract",
        now=_NOW,
    )
    history = outbox.history(run_id="run-legacy-row")
    expected_payload = {
        "payload_type": "projection_recovery",
        "code": "notification_projection_invalid",
        "state_version": 9,
        "next_actions": ["status", "cancel"],
    }

    assert leased[0]["kind"] == "reconciliation_required"
    assert leased[0]["payload"] == expected_payload
    assert history[0]["kind"] == "reconciliation_required"
    assert history[0]["payload"] == expected_payload
    assert _CANARY not in json.dumps({"lease": leased, "history": history})


@pytest.mark.parametrize(
    ("ambiguous", "expected_actions"),
    (
        (
            '{"payload_type":"made_up","payload_type":"workflow_transition",'
            '"status":"failed","state_version":1,"next_actions":["status"]}',
            [],
        ),
        (
            '{"payload_type":"workflow_transition","status":"failed",'
            '"state_version":true,"next_actions":["status"]}',
            ["status"],
        ),
    ),
    ids=("duplicate-key", "boolean-version"),
)
def test_notification_read_codec_rejects_duplicate_keys_and_scalar_type_confusion(
    tmp_path,
    ambiguous: str,
    expected_actions: list[str],
) -> None:
    store = RunStore(tmp_path / "home")
    outbox = NotificationOutbox(store)
    notification_id = outbox.record(
        run_id="run-ambiguous-row",
        kind="failure",
        destination="desktop",
        transition_version=1,
        payload={"status": "failed"},
        now=_NOW,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_notification_outbox SET payload_json=? "
            "WHERE notification_id=?",
            (ambiguous, notification_id),
        )
        connection.execute(
            "UPDATE workflow_notification_facts SET payload_json=? "
            "WHERE notification_id=?",
            (ambiguous, notification_id),
        )

    leased = outbox.lease(
        destination="desktop",
        owner_id="electron-ambiguous",
        now=_NOW,
    )
    history = outbox.history(run_id="run-ambiguous-row")

    for item in (leased[0], history[0]):
        assert item["kind"] == "reconciliation_required"
        assert item["payload"] == {
            "payload_type": "projection_recovery",
            "code": "notification_projection_invalid",
            "state_version": 1,
            "next_actions": expected_actions,
        }


def test_notification_rest_lease_returns_the_closed_backend_projection(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    outbox = NotificationOutbox(RunStore(home))
    outbox.record(
        run_id="run-rest-closed",
        kind="failure",
        destination="desktop",
        transition_version=3,
        payload={
            "workflow": "deploy",
            "status": "failed",
            "event_type": "run_failed",
            "code": "provider_capability_drift",
            "mismatched_fields": ["endpoint_sha256"],
            "unknown": {"provider_response": _CANARY},
        },
        now=_NOW,
    )
    module = _notification_api_module()
    client = TestClient(_authenticated_app(module.router))

    response = client.get(
        "/api/plugins/workflow/notifications/lease?client_id=electron-contract"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["items"][0]["payload"] == {
        "payload_type": "workflow_transition",
        "workflow": "deploy",
        "status": "failed",
        "event_type": "run_failed",
        "code": "provider_capability_drift",
        "mismatched_fields": ["endpoint_sha256"],
        "state_version": 3,
        "next_actions": ["status", "events", "resume", "retry", "abandon"],
    }
    assert _CANARY not in response.text
    module.WorkflowNotificationPageProjection.model_validate(body)
    invalid_body = json.loads(json.dumps(body))
    invalid_body["items"][0]["payload"]["provider_response"] = _CANARY
    with pytest.raises(ValidationError):
        module.WorkflowNotificationPageProjection.model_validate(invalid_body)
    operation = _authenticated_app(module.router).openapi()["paths"][
        "/api/plugins/workflow/notifications/lease"
    ]["get"]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def test_gateway_delivery_keeps_capability_private_and_sends_only_closed_payload(
    tmp_path,
) -> None:
    store = RunStore(tmp_path / "home")
    outbox = NotificationOutbox(store)
    capability = f"gateway-capability-{_CANARY}"
    outbox.record(
        run_id="run-gateway-closed",
        kind="completion",
        destination=f"gateway:{capability}",
        transition_version=2,
        payload={
            "workflow": "deploy",
            "status": "succeeded",
            "event_type": "run_succeeded",
            "unknown": {"prompt": _CANARY},
        },
        now=_NOW,
    )
    leased = outbox.lease_gateway(owner_id="gateway-contract", now=_NOW)

    assert leased[0]["destination"] == "gateway:opaque"
    assert leased[0]["delivery_capability"] == capability
    assert capability not in json.dumps(
        {key: value for key, value in leased[0].items() if key != "delivery_capability"}
    )

    # Return the lease, then drive the real coordinator projection boundary.
    assert outbox.fail(
        leased[0]["notification_id"],
        owner_id="gateway-contract",
        error="transient",
        now=_NOW,
    )
    deliveries: list[tuple[str, str, str]] = []

    class _DeliveryPort:
        def deliver(self, route: str, text: str, key: str):
            deliveries.append((route, text, key))
            return SimpleNamespace(status="delivered", detail=None)

    delivered = WorkflowCoordinatorService._deliver_gateway_notifications(
        outbox,
        _DeliveryPort(),
        owner_id="gateway-contract-2",
    )

    assert delivered == 1
    assert deliveries[0][0] == capability
    assert _strict_json(deliveries[0][1]) == {
        "payload_type": "workflow_transition",
        "workflow": "deploy",
        "status": "succeeded",
        "event_type": "run_succeeded",
        "state_version": 2,
        "next_actions": ["status", "events", "archive"],
    }
    assert _CANARY not in deliveries[0][1]


def test_notification_admin_scope_is_a_bounded_logical_identifier(tmp_path) -> None:
    outbox = NotificationOutbox(RunStore(tmp_path / "home"))

    assert outbox._authority_scope("service:test:desktop-client") == (
        "service:test:desktop-client"
    )
    with pytest.raises(ValueError, match="logical identifier"):
        outbox._authority_scope(f"operator supplied feedback {_CANARY}")
