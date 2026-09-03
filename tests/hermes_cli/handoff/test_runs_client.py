from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import urllib.request

import pytest

import hermes_cli.handoff.runs as runs_module
from hermes_cli.handoff.models import HandoffEndpoint, HandoffSnapshot, HandoffSpec
from hermes_cli.handoff.runs import (
    MAX_RESPONSE_BYTES,
    RunsClient,
    RunsConnection,
    RunsDeadline,
    observation_from_status,
)
from hermes_cli.urllib_security import SafeCredentialRedirectHandler


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int) -> bytes:
        return self.read1(size)

    def read1(self, size: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def _client() -> RunsClient:
    return RunsClient(
        RunsConnection("https://peer.example.test/p/reviewer", "peer-secret"),
        RunsDeadline(2),
    )


def _snapshot() -> HandoffSnapshot:
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review this change.",
        output_schema=None,
        deadline_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        attribution={"workflow": "release-check"},
        required_capabilities=frozenset(),
    )
    return HandoffSnapshot(
        handoff_id="handoff-1",
        key_scope="workflow/run-1",
        handoff_key="node/review",
        spec=spec,
        spec_fingerprint=spec.fingerprint,
        phase="submitted",
        state_version=2,
        mechanism="runs",
        binding={"profile": "reviewer", "mechanism": "runs"},
        checkpoint={"run_id": "run-1", "status": "started"},
    )


def test_submit_retries_with_identical_canonical_body_and_bounded_key(monkeypatch):
    requests = []

    def open_request(request, **_kwargs):
        requests.append(request)
        return _Response(b'{"run_id":"run-1","status":"started"}')

    monkeypatch.setattr(runs_module, "open_credentialed_url", open_request)
    for _ in range(2):
        response = _client().submit(
            handoff_id="abc-123",
            prompt="Review this.",
            session_id="session-1",
        )
        assert response["run_id"] == "run-1"

    assert [request.data for request in requests] == [
        b'{"input":"Review this.","session_id":"session-1"}',
        b'{"input":"Review this.","session_id":"session-1"}',
    ]
    assert [request.get_header("Idempotency-key") for request in requests] == [
        "handoff-abc-123",
        "handoff-abc-123",
    ]
    with pytest.raises(ValueError, match="idempotency key is invalid"):
        _client().submit(handoff_id="x" * 250, prompt="x")


def test_canonical_bot_chat_session_is_reused_or_created_with_valid_id(monkeypatch):
    requests = []
    responses = iter([
        _Response(b'{"data":[]}'),
        _Response(b'{"session":{"id":"session-1"}}'),
        _Response(b'{"data":[{"id":"session-1","title":"Bot Chat"}]}'),
    ])

    def open_request(request, **_kwargs):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(runs_module, "open_credentialed_url", open_request)
    created = _client().ensure_session("Bot Chat", source="bot_handoff")
    reused = _client().ensure_session("Bot Chat", source="bot_handoff")

    assert created == reused == "session-1"
    assert requests[0].full_url.endswith(
        "/api/sessions?limit=200&title=Bot+Chat&include_hidden=1"
    )
    assert requests[1].data == b'{"source":"bot_handoff","title":"Bot Chat"}'
    assert requests[1].get_method() == "POST"


def test_session_resolution_rejects_invalid_listing_and_session_ids(monkeypatch):
    responses = iter([
        _Response(b'{"data":"not-a-list"}'),
        _Response(b'{"data":[{"id":"bad/id","title":"Bot Chat"}]}'),
    ])
    monkeypatch.setattr(
        runs_module,
        "open_credentialed_url",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(ValueError, match="session listing"):
        _client().find_session("Bot Chat")
    with pytest.raises(ValueError, match="session_id"):
        _client().find_session("Bot Chat")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-json", "Runs endpoint returned invalid JSON"),
        (b"[]", "Runs endpoint returned non-object JSON"),
        (b"x" * (MAX_RESPONSE_BYTES + 1), "Runs response exceeds byte limit"),
    ],
    ids=("malformed", "non-object", "oversized"),
)
def test_response_body_is_bounded_and_object_json_only(monkeypatch, payload, message):
    monkeypatch.setattr(
        runs_module,
        "open_credentialed_url",
        lambda *_args, **_kwargs: _Response(payload),
    )
    with pytest.raises(ValueError, match=message):
        _client().request_json("/v1/capabilities")


def test_transport_disables_proxies_and_uses_safe_redirect_policy(monkeypatch):
    captured = []

    class _Opener:
        addheaders = [("Unsafe", "late-secret")]

        def open(self, _request, *, timeout):
            assert timeout > 0
            return _Response(b"{}")

    def build_opener(*handlers):
        captured.extend(handlers)
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    _client().request_json("/v1/capabilities")

    proxy = next(handler for handler in captured if isinstance(handler, urllib.request.ProxyHandler))
    redirect = next(
        handler for handler in captured if isinstance(handler, SafeCredentialRedirectHandler)
    )
    assert proxy.proxies == {}
    assert redirect._original_origin == ("https", "peer.example.test", 443)


def test_redirect_preserves_same_origin_bearer_and_strips_cross_origin_headers():
    request = urllib.request.Request(
        "https://peer.example.test/p/reviewer/v1/capabilities",
        headers={
            "Authorization": "Bearer peer-secret",
            "Cookie": "session=secret",
            "X-Private": "secret",
            "Accept": "application/json",
            "User-Agent": "hermes-local-handoff",
        },
    )
    handler = SafeCredentialRedirectHandler(request.full_url)
    same = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://peer.example.test/p/reviewer/v1/capabilities/",
    )
    cross = handler.redirect_request(
        same,
        None,
        302,
        "Found",
        {},
        "https://other.example.test/v1/capabilities",
    )
    same_headers = {name.lower() for name, _value in same.header_items()}
    cross_headers = {name.lower() for name, _value in cross.header_items()}
    assert {"authorization", "cookie", "x-private"} <= same_headers
    assert cross_headers == {"accept", "user-agent"}


@pytest.mark.parametrize(
    ("status", "phase", "failure_code"),
    [
        ("queued", "submitted", None),
        ("started", "submitted", None),
        ("running", "active", None),
        ("waiting_for_approval", "needs_input", None),
        ("failed", "failed", "remote_failed"),
        ("cancelled", "cancelled", None),
        ("interrupted", "indeterminate", "run_interrupted"),
        ("unknown", "indeterminate", "run_status_unknown"),
    ],
)
def test_status_mapping_is_stable(status, phase, failure_code):
    observation = observation_from_status(
        _snapshot(), {"run_id": "run-1", "status": status}
    )
    assert observation.phase == phase
    assert observation.failure_code == failure_code


def test_status_carries_only_bounded_references_and_terminal_result():
    completed = observation_from_status(
        _snapshot(),
        {
            "run_id": "run-1",
            "session_id": "session-1",
            "status": "completed",
            "output": {"answer": "done"},
            "error": "Bearer remote-secret /private/path",
        },
    )
    assert completed.checkpoint == {
        "run_id": "run-1",
        "session_id": "session-1",
        "status": "completed",
    }
    assert completed.terminal_result == {
        "text": '{"answer":"done"}',
        "sha256": "83497274cc7affcc460bca7452c14d5e72eaa019a33055df2bc39cd9a5202774",
        "media_type": "application/json",
        "size_bytes": 17,
    }
    assert "remote-secret" not in json.dumps(dict(completed.checkpoint))

    with pytest.raises(ValueError, match="Run session_id is invalid"):
        observation_from_status(
            _snapshot(),
            {"run_id": "run-1", "session_id": "x" * 257, "status": "running"},
        )
    with pytest.raises(ValueError, match="Run output exceeds byte limit"):
        observation_from_status(
            _snapshot(),
            {"run_id": "run-1", "status": "completed", "output": "x" * 500_001},
        )


def test_control_methods_use_exact_run_routes_and_bodies(monkeypatch):
    requests = []

    def open_request(request, **_kwargs):
        requests.append(request)
        return _Response(b"{}")

    monkeypatch.setattr(runs_module, "open_credentialed_url", open_request)
    client = _client()
    client.approve("run-1", request_id="approval-1", choice="once")
    client.steer("run-1", "Tighten the conclusion.")
    client.stop("run-1")

    assert [(request.full_url, request.data) for request in requests] == [
        (
            "https://peer.example.test/p/reviewer/v1/runs/run-1/approval",
            b'{"choice":"once","request_id":"approval-1"}',
        ),
        (
            "https://peer.example.test/p/reviewer/v1/runs/run-1/steer",
            b'{"input":"Tighten the conclusion."}',
        ),
        (
            "https://peer.example.test/p/reviewer/v1/runs/run-1/stop",
            b"{}",
        ),
    ]
