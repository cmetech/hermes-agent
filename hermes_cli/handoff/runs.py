"""Bounded HTTP primitives for authenticated Hermes Runs endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from time import monotonic
import urllib.parse
import urllib.request

from hermes_cli.urllib_security import open_credentialed_url

from .models import ChannelObservation, HandoffSnapshot


MAX_RESPONSE_BYTES = 600_000
MAX_RESULT_BYTES = 500_000
_READ_CHUNK_BYTES = 64 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")


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
