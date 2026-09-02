from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

import hermes_cli.handoff.runs as runs_module
from hermes_cli.handoff.models import HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.peer import PeerHermesChannel
from hermes_cli.handoff.service import (
    AgentHandoffService,
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
)
from hermes_cli.handoff.store import HandoffStore


PEER_KEY = "peer-api-key-0123456789abcdef"


class _PeerHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    features: dict[str, object] = {}
    statuses: dict[str, dict[str, object]] = {}
    reservations: dict[str, tuple[bytes, str]] = {}
    executions = 0

    @classmethod
    def reset(cls) -> None:
        cls.requests = []
        cls.features = {
            "run_submission": True,
            "runs_idempotency": {"supported": True, "durable": True},
            "run_status": True,
            "run_stop": True,
            "run_steer": True,
            "run_approval_response": True,
            "approval_events": True,
        }
        cls.statuses = {}
        cls.reservations = {}
        cls.executions = 0

    def _reply(self, value: dict[str, object], status: int = 200) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _record(self, body: bytes = b"") -> None:
        type(self).requests.append({
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "idempotency_key": self.headers.get("Idempotency-Key"),
            "body": body,
        })

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {PEER_KEY}":
            return True
        self._reply({"error": {"message": "secret detail"}}, 401)
        return False

    def do_GET(self):
        self._record()
        if not self._authorized():
            return
        if self.path == "/p/reviewer/v1/capabilities":
            self._reply({"features": type(self).features})
            return
        prefix = "/p/reviewer/v1/runs/"
        if self.path.startswith(prefix):
            run_id = self.path.removeprefix(prefix)
            status = type(self).statuses.get(run_id)
            if status is None:
                self._reply({"error": {"message": "not found"}}, 404)
            else:
                self._reply(status)
            return
        self._reply({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._record(body)
        if not self._authorized():
            return
        if self.path == "/p/reviewer/v1/runs":
            key = self.headers.get("Idempotency-Key") or ""
            reservation = type(self).reservations.get(key)
            if reservation is not None and reservation[0] != body:
                self._reply({"error": {"message": "conflicting secret payload"}}, 409)
                return
            if reservation is None:
                run_id = f"run-{len(type(self).reservations) + 1}"
                type(self).reservations[key] = (body, run_id)
                type(self).executions += 1
                type(self).statuses[run_id] = {
                    "run_id": run_id,
                    "session_id": "remote-session-1",
                    "status": "running",
                }
            else:
                run_id = reservation[1]
            self._reply({"run_id": run_id, "status": "started"}, 202)
            return
        if self.path.endswith("/approval"):
            run_id = self.path.rsplit("/", 2)[-2]
            request = json.loads(body)
            current = type(self).statuses.get(run_id, {})
            approval = current.get("approval")
            if (
                current.get("status") != "waiting_for_approval"
                or not isinstance(approval, dict)
                or request.get("request_id") != approval.get("request_id")
            ):
                self._reply({"error": {"message": "not pending"}}, 409)
                return
            type(self).statuses[run_id] = {"run_id": run_id, "status": "running"}
            self._reply({
                "run_id": run_id,
                "request_id": request["request_id"],
                "choice": request.get("choice"),
                "resolved": 1,
            })
            return
        if self.path.endswith("/steer"):
            run_id = self.path.rsplit("/", 2)[-2]
            if type(self).statuses.get(run_id, {}).get("status") != "running":
                self._reply({"error": {"message": "not accepting"}}, 409)
                return
            self._reply({"run_id": run_id, "accepted": True})
            return
        if self.path.endswith("/stop"):
            run_id = self.path.rsplit("/", 2)[-2]
            type(self).statuses[run_id] = {"run_id": run_id, "status": "stopping"}
            self._reply(type(self).statuses[run_id])
            return
        self._reply({"error": {"message": "not found"}}, 404)

    def log_message(self, _format, *_args):
        pass


@pytest.fixture
def peer_server():
    _PeerHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PeerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _write_peer(home: Path, url: str, *, key: str = PEER_KEY) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"bot_peers:\n  spark:\n    url: {url}\n", encoding="utf-8"
    )
    (home / ".env").write_text(f"HERMES_PEER_SPARK_KEY={key}\n", encoding="utf-8")


def _spec(*, capabilities=frozenset(), prompt="Review this change.") -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://peer/spark/reviewer"),
        prompt=prompt,
        output_schema=None,
        deadline_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities=frozenset(capabilities),
    )


def _service(home: Path, *, capabilities=frozenset(), prompt="Review this change."):
    channel = PeerHermesChannel(home)
    service = AgentHandoffService(
        store=HandoffStore(home / "handoffs.db"), channel=channel
    )
    snapshot = service.create(
        _spec(capabilities=capabilities, prompt=prompt),
        "workflow/run-1",
        handoff_key="node/review",
    )
    return service, channel, snapshot


def test_validation_uses_registered_authenticated_profile_route(tmp_path, peer_server):
    _write_peer(tmp_path, peer_server)
    service, _channel, snapshot = _service(tmp_path)

    assessment = service.validate_endpoint(snapshot.spec.endpoint, "workflow/run-1")

    assert assessment.available is True
    assert assessment.mechanism == "peer_runs"
    assert assessment.capabilities == frozenset({
        "approval",
        "authoritative_status",
        "cancellation",
        "durable_admission",
        "follow_up",
        "steering",
    })
    assert _PeerHandler.requests == [{
        "method": "GET",
        "path": "/p/reviewer/v1/capabilities",
        "authorization": f"Bearer {PEER_KEY}",
        "idempotency_key": None,
        "body": b"",
    }]


def test_unknown_peer_missing_credential_and_rejected_auth_fail_closed(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    channel = PeerHermesChannel(tmp_path)
    missing = HandoffEndpoint.parse("hermes://peer/missing/reviewer")
    assert channel.validate_endpoint(missing, "workflow/run-1").failure_code == (
        "peer_not_found"
    )

    _write_peer(tmp_path, peer_server, key="short")
    endpoint = HandoffEndpoint.parse("hermes://peer/spark/reviewer")
    assert channel.validate_endpoint(endpoint, "workflow/run-1").failure_code == (
        "peer_auth_unavailable"
    )

    _write_peer(tmp_path, peer_server, key="wrong-key-0123456789")
    assert channel.validate_endpoint(endpoint, "workflow/run-1").failure_code == (
        "peer_auth_rejected"
    )


def test_bind_seals_only_capabilities_and_nonsecret_scope_digests(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path, capabilities={"approval"})

    bound = service.advance(created.handoff_id).snapshot

    assert bound.phase == "prepared"
    assert bound.mechanism == "peer_runs"
    assert dict(bound.binding or {}) == {
        "peer": "spark",
        "profile": "reviewer",
        "mechanism": "peer_runs",
        "capabilities": (
            "approval",
            "authoritative_status",
            "cancellation",
            "durable_admission",
            "follow_up",
            "steering",
        ),
        "origin_sha256": bound.binding["origin_sha256"],
        "auth_scope_sha256": bound.binding["auth_scope_sha256"],
    }
    persisted = json.dumps({"binding": dict(bound.binding), "checkpoint": dict(bound.checkpoint)})
    assert PEER_KEY not in persisted
    assert peer_server not in persisted


@pytest.mark.parametrize(
    ("feature", "required", "failure"),
    [
        ("runs_idempotency", frozenset(), "runs_not_durable"),
        ("run_status", frozenset(), "run_status_unavailable"),
        ("run_stop", frozenset(), "capability_mismatch"),
        ("run_approval_response", frozenset({"approval"}), "capability_mismatch"),
        ("run_steer", frozenset({"steering"}), "capability_mismatch"),
    ],
)
def test_bind_fails_before_submission_when_required_capability_is_missing(
    tmp_path, peer_server, feature, required, failure
):
    _write_peer(tmp_path, peer_server)
    if feature == "runs_idempotency":
        _PeerHandler.features[feature] = {"supported": True, "durable": False}
    else:
        _PeerHandler.features[feature] = False
    service, _channel, created = _service(tmp_path, capabilities=required)

    bound = service.advance(created.handoff_id).snapshot

    assert bound.phase == "failed"
    assert bound.failure_code == failure
    assert all(request["path"] != "/p/reviewer/v1/runs" for request in _PeerHandler.requests)


def test_submit_polls_session_and_stops_by_remote_run_id(tmp_path, peer_server):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path)
    bound = service.advance(created.handoff_id).snapshot
    submitted = service.advance(created.handoff_id).snapshot

    assert submitted.checkpoint == {
        "idempotency_key": f"handoff-{created.handoff_id}",
        "run_id": "run-1",
        "status": "started",
    }
    post = next(request for request in _PeerHandler.requests if request["method"] == "POST")
    assert post["idempotency_key"] == f"handoff-{created.handoff_id}"
    assert post["body"] == b'{"input":"Review this change."}'

    observed = service.advance(created.handoff_id).snapshot
    assert observed.phase == "active"
    assert observed.checkpoint["session_id"] == "remote-session-1"

    service.command(created.handoff_id, "cancel", command_id="cancel-1", actor="workflow")
    stopping = service.advance(created.handoff_id).snapshot
    assert stopping.phase == "cancelling"
    assert stopping.checkpoint["run_id"] == "run-1"
    assert bound.binding == submitted.binding == observed.binding == stopping.binding


def test_lost_submit_response_replays_same_key_without_duplicate_execution(
    tmp_path, peer_server, monkeypatch
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path)
    service.advance(created.handoff_id)
    original = runs_module.open_credentialed_url
    lose_once = True

    def lose_response(request, **kwargs):
        nonlocal lose_once
        response = original(request, **kwargs)
        if lose_once and request.full_url.endswith("/v1/runs"):
            lose_once = False
            response.read()
            response.close()
            raise TimeoutError("accepted response lost")
        return response

    monkeypatch.setattr(runs_module, "open_credentialed_url", lose_response)
    ambiguous = service.advance(created.handoff_id).snapshot
    recovered = service.advance(created.handoff_id).snapshot

    assert ambiguous.phase == "indeterminate"
    assert recovered.checkpoint["run_id"] == "run-1"
    assert _PeerHandler.executions == 1
    run_posts = [r for r in _PeerHandler.requests if r["path"].endswith("/v1/runs")]
    assert len(run_posts) == 2
    assert run_posts[0]["idempotency_key"] == run_posts[1]["idempotency_key"]
    assert run_posts[0]["body"] == run_posts[1]["body"]


def test_same_key_replays_run_and_changed_payload_is_definitive_conflict(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    service, channel, created = _service(tmp_path)
    bound = service.advance(created.handoff_id).snapshot
    submitted = service.advance(created.handoff_id).snapshot

    replayed = channel.reconcile(
        replace(submitted, phase="indeterminate", checkpoint=bound.checkpoint),
        budget_seconds=2,
    )
    assert replayed.checkpoint["run_id"] == submitted.checkpoint["run_id"]
    with pytest.raises(ChannelDefinitelyNotAccepted) as error:
        channel.reconcile(
            replace(
                submitted,
                phase="indeterminate",
                spec=_spec(prompt="Changed payload."),
                checkpoint=bound.checkpoint,
            ),
            budget_seconds=2,
        )
    assert error.value.failure_code == "idempotency_key_conflict"
    assert _PeerHandler.executions == 1


@pytest.mark.parametrize("change", ["url", "key"])
def test_bound_scope_change_is_indeterminate_before_network_io(
    tmp_path, peer_server, change
):
    _write_peer(tmp_path, peer_server)
    service, channel, created = _service(tmp_path)
    service.advance(created.handoff_id)
    submitted = service.advance(created.handoff_id).snapshot
    before = len(_PeerHandler.requests)
    if change == "url":
        _write_peer(tmp_path, "http://127.0.0.1:9")
    else:
        _write_peer(tmp_path, peer_server, key="rotated-key-0123456789")

    with pytest.raises(ChannelIndeterminate):
        channel.observe(submitted, budget_seconds=2)
    assert len(_PeerHandler.requests) == before


def _waiting_for_approval(service, created):
    service.advance(created.handoff_id)
    submitted = service.advance(created.handoff_id).snapshot
    run_id = submitted.checkpoint["run_id"]
    _PeerHandler.statuses[run_id] = {
        "run_id": run_id,
        "session_id": "remote-session-1",
        "status": "waiting_for_approval",
        "approval": {
            "request_id": "approval-1",
            "choices": ["once", "deny"],
            "command": "Bearer private-command-detail",
        },
    }
    return service.advance(created.handoff_id).snapshot


def test_exact_approval_response_uses_sealed_request_and_advertised_choice(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path, capabilities={"approval"})
    waiting = _waiting_for_approval(service, created)

    assert waiting.phase == "needs_input"
    assert waiting.checkpoint["approval_request_id"] == "approval-1"
    assert waiting.checkpoint["approval_choices"] == ("once", "deny")
    assert "private-command" not in json.dumps(dict(waiting.checkpoint))
    service.command(
        waiting.handoff_id,
        "respond",
        command_id="respond-1",
        actor="workflow",
        request_id="approval-1",
        choice="once",
    )

    delivered = service.advance(waiting.handoff_id)

    assert delivered.operation == "deliver_command"
    assert service.store.get_command(waiting.handoff_id, "respond-1").delivery_state == (
        "delivered"
    )
    approval_posts = [
        request for request in _PeerHandler.requests if request["path"].endswith("/approval")
    ]
    assert [request["body"] for request in approval_posts] == [
        b'{"choice":"once","request_id":"approval-1"}'
    ]


def test_steer_and_correlated_message_share_remote_route_but_not_local_kind(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path)
    service.advance(created.handoff_id)
    service.advance(created.handoff_id)
    active = service.advance(created.handoff_id).snapshot
    service.command(
        active.handoff_id,
        "steer",
        command_id="steer-1",
        actor="workflow",
        text="Tighten the conclusion.",
    )
    service.advance(active.handoff_id)
    service.command(
        active.handoff_id,
        "message",
        command_id="message-1",
        actor="workflow",
        text="Check the follow-up.",
        correlation_id="follow-up-1",
    )
    service.advance(active.handoff_id)

    steer_posts = [
        request for request in _PeerHandler.requests if request["path"].endswith("/steer")
    ]
    assert [request["body"] for request in steer_posts] == [
        b'{"input":"Tighten the conclusion."}',
        b'{"input":"Check the follow-up."}',
    ]
    assert service.store.get_command(active.handoff_id, "steer-1").kind == "steer"
    assert service.store.get_command(active.handoff_id, "message-1").kind == "message"


def test_lost_approval_response_reconciles_by_status_without_resending(
    tmp_path, peer_server, monkeypatch
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path, capabilities={"approval"})
    waiting = _waiting_for_approval(service, created)
    service.command(
        waiting.handoff_id,
        "respond",
        command_id="respond-1",
        actor="workflow",
        request_id="approval-1",
        choice="once",
    )
    original = runs_module.open_credentialed_url
    lose_once = True

    def lose_response(request, **kwargs):
        nonlocal lose_once
        response = original(request, **kwargs)
        if lose_once and request.full_url.endswith("/approval"):
            lose_once = False
            response.read()
            response.close()
            raise TimeoutError("approval response lost")
        return response

    monkeypatch.setattr(runs_module, "open_credentialed_url", lose_response)
    service.advance(waiting.handoff_id)

    assert service.store.get_command(waiting.handoff_id, "respond-1").delivery_state == (
        "delivered"
    )
    assert len([r for r in _PeerHandler.requests if r["path"].endswith("/approval")]) == 1


@pytest.mark.parametrize("kind", ["steer", "message"])
def test_lost_guidance_response_is_indeterminate_and_never_resent(
    tmp_path, peer_server, monkeypatch, kind
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path)
    service.advance(created.handoff_id)
    service.advance(created.handoff_id)
    active = service.advance(created.handoff_id).snapshot
    kwargs = {"text": "Check this."}
    if kind == "message":
        kwargs["correlation_id"] = "follow-up-1"
    service.command(
        active.handoff_id,
        kind,
        command_id=f"{kind}-1",
        actor="workflow",
        **kwargs,
    )
    original = runs_module.open_credentialed_url
    lose_once = True

    def lose_response(request, **request_kwargs):
        nonlocal lose_once
        response = original(request, **request_kwargs)
        if lose_once and request.full_url.endswith("/steer"):
            lose_once = False
            response.read()
            response.close()
            raise TimeoutError("steer response lost")
        return response

    monkeypatch.setattr(runs_module, "open_credentialed_url", lose_response)
    service.advance(active.handoff_id)
    first_count = len([r for r in _PeerHandler.requests if r["path"].endswith("/steer")])
    service.advance(active.handoff_id)

    assert service.store.get_command(active.handoff_id, f"{kind}-1").delivery_state == (
        "indeterminate"
    )
    assert len([r for r in _PeerHandler.requests if r["path"].endswith("/steer")]) == first_count


def test_restart_with_attempted_response_performs_status_only(
    tmp_path, peer_server
):
    _write_peer(tmp_path, peer_server)
    service, _channel, created = _service(tmp_path, capabilities={"approval"})
    waiting = _waiting_for_approval(service, created)
    service.command(
        waiting.handoff_id,
        "respond",
        command_id="respond-1",
        actor="workflow",
        request_id="approval-1",
        choice="once",
    )
    lease = service.store.claim_advance(
        waiting.handoff_id,
        "crashed-worker",
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    assert lease is not None
    assert service.store.claim_delivery_command(lease).delivery_state == "pending"
    service.store.release_advance(lease, next_advance_at=None)
    before = len([r for r in _PeerHandler.requests if r["path"].endswith("/approval")])

    service.advance(waiting.handoff_id)

    assert len([r for r in _PeerHandler.requests if r["path"].endswith("/approval")]) == before
    assert service.store.get_command(waiting.handoff_id, "respond-1").delivery_state == (
        "indeterminate"
    )
