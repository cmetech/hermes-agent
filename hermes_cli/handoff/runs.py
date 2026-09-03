"""Bounded HTTP primitives for authenticated Hermes Runs endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request

from hermes_cli.urllib_security import open_credentialed_url

from .models import ChannelObservation, HandoffSnapshot


MAX_RESPONSE_BYTES = 600_000
MAX_RESULT_BYTES = 500_000
_READ_CHUNK_BYTES = 64 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_APPROVAL_CHOICES = ("once", "session", "always", "deny")


@dataclass(frozen=True, slots=True)
class RunsConnection:
    base_url: str
    key: str | None = None


class RunsDeadline:
    def __init__(self, seconds: float) -> None:
        self._at = monotonic() + seconds

    def remaining(self) -> float:
        remaining = self._at - monotonic()
        if remaining <= 0:
            raise TimeoutError("handoff operation budget expired")
        return remaining


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"Run {name} is invalid")
    return value


def advertised_capabilities(
    document: dict[str, object],
) -> tuple[frozenset[str], str | None]:
    features = document.get("features")
    if not isinstance(features, dict) or features.get("run_submission") is not True:
        return frozenset(), "run_submission_unavailable"
    idempotency = features.get("runs_idempotency")
    if (
        not isinstance(idempotency, dict)
        or idempotency.get("supported") is not True
        or idempotency.get("durable") is not True
    ):
        return frozenset(), "runs_not_durable"
    if features.get("run_status") is not True:
        return frozenset(), "run_status_unavailable"
    capabilities = {"authoritative_status", "durable_admission"}
    if features.get("run_stop") is True:
        capabilities.add("cancellation")
    if (
        features.get("run_approval_response") is True
        and features.get("approval_events") is True
    ):
        capabilities.add("approval")
    if features.get("run_steer") is True:
        capabilities.update({"steering", "follow_up"})
    return frozenset(capabilities), None


def _direct_opener(redirect_handler):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirect_handler,
    )
    opener.addheaders = []
    return opener


def _read_response(response, deadline: RunsDeadline) -> bytes:
    read = getattr(response, "read1", None) or response.read
    chunks = []
    total = 0
    while True:
        remaining = deadline.remaining()
        sock = getattr(
            getattr(getattr(response, "fp", None), "raw", None), "_sock", None
        )
        if sock is not None:
            try:
                sock.settimeout(remaining)
            except (OSError, ValueError):
                pass
        chunk = read(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - total))
        deadline.remaining()
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("Runs response exceeds byte limit")
        chunks.append(chunk)


class RunsClient:
    """One-operation client for an already resolved Hermes API base URL."""

    def __init__(self, connection: RunsConnection, deadline: RunsDeadline) -> None:
        self.connection = connection
        self.deadline = deadline

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        data = None
        if body is not None:
            data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        request_headers = {
            "Content-Type": "application/json",
            "User-Agent": "hermes-local-handoff",
        }
        if self.connection.key:
            request_headers["Authorization"] = f"Bearer {self.connection.key}"
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            f"{self.connection.base_url.rstrip('/')}{path}",
            data=data,
            method=method,
            headers=request_headers,
        )
        with open_credentialed_url(
            request,
            timeout=self.deadline.remaining(),
            opener_factory=_direct_opener,
        ) as response:
            raw = _read_response(response, self.deadline)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Runs endpoint returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Runs endpoint returned non-object JSON")
        return value

    def find_session(self, title: str) -> str | None:
        if (
            not isinstance(title, str)
            or not title
            or title != title.strip()
            or len(title.encode("utf-8")) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in title)
        ):
            raise ValueError("session title is invalid")
        query = urllib.parse.urlencode({
            "limit": 200,
            "title": title,
            "include_hidden": 1,
        })
        listing = self.request_json(f"/api/sessions?{query}")
        rows = listing.get("data")
        if not isinstance(rows, list):
            raise ValueError("session listing is invalid")
        for row in rows:
            if isinstance(row, dict) and row.get("title") == title:
                return _identifier(row.get("id"), "session_id")
        return None

    def ensure_session(self, title: str, *, source: str) -> str:
        source = _identifier(source, "source")
        existing = self.find_session(title)
        if existing is not None:
            return existing
        try:
            created = self.request_json(
                "/api/sessions",
                method="POST",
                body={"title": title, "source": source},
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 400 and (existing := self.find_session(title)) is not None:
                return existing
            raise
        session = created.get("session")
        if not isinstance(session, dict):
            session = created
        return _identifier(
            session.get("id") or session.get("session_id"), "session_id"
        )

    def chat(self, session_id: str, message: str) -> dict[str, object]:
        session_id = _identifier(session_id, "session_id")
        response = self.request_json(
            f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/chat",
            method="POST",
            body={"message": message},
        )
        actual = response.get("session_id")
        if actual is not None:
            _identifier(actual, "session_id")
        return response

    def submit(
        self,
        *,
        handoff_id: str,
        prompt: str,
        session_id: str | None = None,
    ) -> dict[str, object]:
        key = f"handoff-{handoff_id}"
        if not _SAFE_IDENTIFIER.fullmatch(key):
            raise ValueError("Runs idempotency key is invalid")
        body: dict[str, object] = {"input": prompt}
        if session_id is not None:
            body["session_id"] = _identifier(session_id, "session_id")
        response = self.request_json(
            "/v1/runs",
            method="POST",
            body=body,
            headers={"Idempotency-Key": key},
        )
        run_id = _identifier(response.get("run_id"), "run_id")
        return {
            "run_id": run_id,
            "status": str(response.get("status") or "queued"),
            "idempotency_key": key,
        }

    def status(self, run_id: str) -> dict[str, object]:
        expected = _identifier(run_id, "run_id")
        response = self.request_json(
            f"/v1/runs/{urllib.parse.quote(expected, safe='')}"
        )
        actual = response.get("run_id")
        if actual is not None and _identifier(actual, "run_id") != expected:
            raise ValueError("Run run_id does not match request")
        allowed = {"run_id", "session_id", "status", "output", "approval"}
        return {key: value for key, value in response.items() if key in allowed}

    def approve(self, run_id: str, *, request_id: str, choice: str) -> dict[str, object]:
        run_id = _identifier(run_id, "run_id")
        request_id = _identifier(request_id, "approval request_id")
        return self.request_json(
            f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/approval",
            method="POST",
            body={"choice": choice, "request_id": request_id},
        )

    def steer(self, run_id: str, text: str) -> dict[str, object]:
        run_id = _identifier(run_id, "run_id")
        return self.request_json(
            f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/steer",
            method="POST",
            body={"input": text},
        )

    def stop(self, run_id: str) -> dict[str, object]:
        run_id = _identifier(run_id, "run_id")
        return self.request_json(
            f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stop",
            method="POST",
            body={},
        )


def terminal_result(output: object) -> dict[str, object]:
    text = (
        output
        if isinstance(output, str)
        else json.dumps(output, sort_keys=True, separators=(",", ":"))
    )
    encoded = text.encode()
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError("Run output exceeds byte limit")
    return {
        "text": text,
        "sha256": sha256(encoded).hexdigest(),
        "media_type": "text/plain" if isinstance(output, str) else "application/json",
        "size_bytes": len(encoded),
    }


def observation_from_status(
    snapshot: HandoffSnapshot,
    response: dict[str, object],
    *,
    cancelling: bool = False,
) -> ChannelObservation:
    checkpoint = dict(snapshot.checkpoint or {})
    expected_run_id = _identifier(checkpoint.get("run_id"), "run_id")
    actual_run_id = response.get("run_id")
    if actual_run_id is not None and _identifier(actual_run_id, "run_id") != expected_run_id:
        raise ValueError("Run run_id does not match request")
    checkpoint["run_id"] = expected_run_id
    if response.get("session_id") is not None:
        checkpoint["session_id"] = _identifier(response["session_id"], "session_id")
    status = str(response.get("status") or "unknown")
    if not _SAFE_IDENTIFIER.fullmatch(status):
        raise ValueError("Run status is invalid")
    checkpoint["status"] = status
    if status == "waiting_for_approval" and response.get("approval") is not None:
        approval = response.get("approval")
        if not isinstance(approval, dict):
            raise ValueError("Run approval facts are missing")
        choices = approval.get("choices")
        if not isinstance(choices, list | tuple):
            raise ValueError("Run approval choices are invalid")
        normalized = [choice for choice in _APPROVAL_CHOICES if choice in choices]
        checkpoint.update({
            "approval_request_id": _identifier(
                approval.get("request_id"), "approval request_id"
            ),
            "approval_choices": normalized,
        })
        if not normalized:
            raise ValueError("Run approval facts are invalid")
    else:
        checkpoint.pop("approval_request_id", None)
        checkpoint.pop("approval_choices", None)

    if status == "completed":
        return ChannelObservation(
            phase="succeeded",
            checkpoint=checkpoint,
            terminal_result=terminal_result(response.get("output", "")),
        )
    if status == "failed":
        return ChannelObservation(
            phase="failed", checkpoint=checkpoint, failure_code="remote_failed"
        )
    if status == "cancelled":
        return ChannelObservation(phase="cancelled", checkpoint=checkpoint)
    if status == "interrupted":
        return ChannelObservation(
            phase="indeterminate",
            checkpoint=checkpoint,
            failure_code="run_interrupted",
        )
    if cancelling or status == "stopping":
        return ChannelObservation(phase="cancelling", checkpoint=checkpoint)
    phase = {
        "queued": "submitted",
        "started": "submitted",
        "running": "active",
        "waiting_for_approval": "needs_input",
    }.get(status)
    if phase is None:
        return ChannelObservation(
            phase="indeterminate",
            checkpoint=checkpoint,
            failure_code="run_status_unknown",
        )
    return ChannelObservation(phase=phase, checkpoint=checkpoint)


def deliver_run_command(
    client: RunsClient, snapshot: HandoffSnapshot, command
) -> tuple[str, str | None]:
    run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
    if not run_id or command.kind not in {"respond", "steer", "message"}:
        return "indeterminate", "command_delivery_indeterminate"

    if command.delivery_state == "attempted":
        if command.kind == "respond":
            return _reconcile_approval(client, run_id, command)
        try:
            client.status(run_id)
        except (urllib.error.HTTPError, OSError, TimeoutError, ValueError):
            pass
        return "indeterminate", "guidance_delivery_indeterminate"

    if command.kind == "respond":
        try:
            response = client.approve(
                run_id,
                request_id=str(command.payload["request_id"]),
                choice=str(command.payload["choice"]),
            )
        except (urllib.error.HTTPError, OSError, TimeoutError, ValueError):
            return _reconcile_approval(client, run_id, command)
        if (
            response.get("request_id") != command.payload["request_id"]
            or response.get("choice") != command.payload["choice"]
        ):
            return _reconcile_approval(client, run_id, command)
        return "delivered", None

    try:
        response = client.steer(run_id, str(command.payload["text"]))
    except (urllib.error.HTTPError, OSError, TimeoutError, ValueError):
        return "indeterminate", "guidance_delivery_indeterminate"
    if response.get("accepted") is not True:
        return "indeterminate", "guidance_delivery_indeterminate"
    return "delivered", None


def _reconcile_approval(
    client: RunsClient, run_id: str, command
) -> tuple[str, str | None]:
    try:
        response = client.status(run_id)
    except (urllib.error.HTTPError, OSError, TimeoutError, ValueError):
        return "indeterminate", "approval_response_indeterminate"
    approval = response.get("approval")
    still_pending = (
        response.get("status") == "waiting_for_approval"
        and isinstance(approval, dict)
        and approval.get("request_id") == command.payload["request_id"]
    )
    return (
        ("indeterminate", "approval_response_indeterminate")
        if still_pending
        else ("delivered", None)
    )
