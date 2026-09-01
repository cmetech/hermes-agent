"""Profile-scoped loopback transport for durable local Hermes Runs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import socket
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request

from agent.secret_scope import build_profile_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platforms.api_server import DEFAULT_HOST, DEFAULT_PORT
from hermes_cli.auth import has_usable_secret
from hermes_cli.profiles import get_profile_dir, profiles_to_serve
from hermes_cli.urllib_security import open_credentialed_url
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot
from .service import (
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
    ChannelRetryableFailure,
    EndpointAssessment,
)


MAX_RESPONSE_BYTES = 600_000
_READ_CHUNK_BYTES = 64 * 1024
_VALIDATE_BUDGET_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class _Connection:
    base_url: str
    key: str


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self._at = monotonic() + seconds

    def remaining(self) -> float:
        remaining = self._at - monotonic()
        if remaining <= 0:
            raise TimeoutError("handoff operation budget expired")
        return remaining


def _listener_url(host: object, port: object) -> str | None:
    configured = str(host or DEFAULT_HOST).strip()
    lookup = (
        configured[1:-1]
        if configured.startswith("[") and configured.endswith("]")
        else configured
    )
    if lookup == "*":
        lookup = "127.0.0.1"
    else:
        try:
            if ipaddress.ip_address(lookup).is_unspecified:
                lookup = "127.0.0.1"
        except ValueError:
            pass
    try:
        addresses = {
            row[4][0].split("%", 1)[0]
            for row in socket.getaddrinfo(lookup, int(port), type=socket.SOCK_STREAM)
        }
        if not addresses or not all(
            ipaddress.ip_address(value).is_loopback for value in addresses
        ):
            return None
    except (OSError, TypeError, ValueError):
        return None
    rendered = f"[{lookup}]" if ":" in lookup else lookup
    return f"http://{rendered}:{int(port)}"


def _failure_code(exc: urllib.error.HTTPError) -> str:
    return f"http_{int(exc.code)}"


def _direct_local_opener(redirect_handler):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirect_handler,
    )
    opener.addheaders = []
    return opener


def _read_response(response, deadline: _Deadline) -> bytes:
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
            raise ValueError("handoff response exceeds byte limit")
        chunks.append(chunk)


def _request_json(
    connection: _Connection,
    path: str,
    deadline: _Deadline,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    data = None
    if body is not None:
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_headers = {
        "Authorization": f"Bearer {connection.key}",
        "Content-Type": "application/json",
        "User-Agent": "hermes-local-handoff",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{connection.base_url}{path}",
        data=data,
        method=method,
        headers=request_headers,
    )
    with open_credentialed_url(
        request,
        timeout=deadline.remaining(),
        opener_factory=_direct_local_opener,
    ) as response:
        raw = _read_response(response, deadline)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("handoff endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("handoff endpoint returned non-object JSON")
    return value


def _checkpoint(snapshot: HandoffSnapshot, **changes: object) -> dict[str, object]:
    values = dict(snapshot.checkpoint or {})
    values.update(changes)
    return values


class LocalHermesChannel:
    """Duck-typed handoff channel backed by the local multiplex API server."""

    def _connection(self, profile: str) -> tuple[_Connection | None, str | None]:
        default_home = get_profile_dir("default")
        token = set_hermes_home_override(default_home)
        try:
            config = load_gateway_config()
        finally:
            reset_hermes_home_override(token)

        if not config.multiplex_profiles:
            return None, "multiplex_required"
        api_config = config.platforms.get(Platform.API_SERVER)
        if api_config is None or not api_config.enabled:
            return None, "api_server_disabled"

        served = dict(
            profiles_to_serve(
                multiplex=True,
                profile_allowlist=config.multiplex_profile_allowlist,
            )
        )
        target_home = served.get(profile)
        if target_home is None:
            return None, "profile_not_served"

        key = (
            build_profile_secret_scope(target_home).get("API_SERVER_KEY") or ""
        ).strip()
        if not key:
            return None, "api_server_key_missing"
        if not has_usable_secret(key, min_length=16):
            return None, "api_server_key_weak"

        extra = api_config.extra or {}
        base = _listener_url(
            extra.get("host", DEFAULT_HOST), extra.get("port", DEFAULT_PORT)
        )
        if base is None:
            return None, "listener_not_loopback"
        prefix = urllib.parse.quote(profile, safe="")
        return _Connection(f"{base}/p/{prefix}", key), None

    @staticmethod
    def _durability_failure(capabilities: dict[str, object]) -> str | None:
        features = capabilities.get("features")
        contract = (
            features.get("runs_idempotency") if isinstance(features, dict) else None
        )
        if (
            not isinstance(contract, dict)
            or contract.get("supported") is not True
            or contract.get("durable") is not True
        ):
            return "runs_not_durable"
        return None

    def _assess(
        self, endpoint: HandoffEndpoint, deadline: _Deadline
    ) -> tuple[_Connection | None, str | None]:
        connection, failure = self._connection(endpoint.profile)
        if connection is None:
            return None, failure
        try:
            capabilities = _request_json(connection, "/v1/capabilities", deadline)
        except urllib.error.HTTPError as exc:
            return None, _failure_code(exc)
        except (OSError, TimeoutError, ValueError):
            return None, "endpoint_unavailable"
        return (
            (connection, None)
            if (failure := self._durability_failure(capabilities)) is None
            else (None, failure)
        )

    def validate_endpoint(
        self, endpoint: HandoffEndpoint, _initiator: str
    ) -> EndpointAssessment:
        _connection, failure = self._assess(
            endpoint, _Deadline(_VALIDATE_BUDGET_SECONDS)
        )
        return EndpointAssessment(
            endpoint=endpoint,
            available=failure is None,
            mechanism="runs" if failure is None else None,
            failure_code=failure,
        )

    def _find_session(
        self, connection: _Connection, title: str, deadline: _Deadline
    ) -> str | None:
        query = urllib.parse.urlencode({
            "limit": 200,
            "title": title,
            "include_hidden": 1,
        })
        listing = _request_json(connection, f"/api/sessions?{query}", deadline)
        rows = listing.get("data")
        if not isinstance(rows, list):
            raise ValueError("handoff session listing is invalid")
        for row in rows:
            if isinstance(row, dict) and (row.get("title") or "").strip() == title:
                session_id = str(row.get("id") or "")
                return session_id or None
        return None

    def _ensure_session(
        self, connection: _Connection, handoff_id: str, deadline: _Deadline
    ) -> str:
        title = f"Handoff: {handoff_id}"
        existing = self._find_session(connection, title, deadline)
        if existing:
            return existing
        try:
            created = _request_json(
                connection,
                "/api/sessions",
                deadline,
                method="POST",
                body={"title": title, "source": "handoff"},
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            existing = self._find_session(connection, title, deadline)
            if existing:
                return existing
            raise
        session = created.get("session")
        session_id = str(session.get("id") or "") if isinstance(session, dict) else ""
        if not session_id:
            raise ValueError("handoff session creation returned no id")
        return session_id

    def bind(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        deadline = _Deadline(budget_seconds)
        connection, failure = self._assess(snapshot.spec.endpoint, deadline)
        if connection is None:
            return ChannelObservation(phase="prepared", failure_code=failure)
        try:
            session_id = self._ensure_session(connection, snapshot.handoff_id, deadline)
        except urllib.error.HTTPError as exc:
            return ChannelObservation(phase="prepared", failure_code=_failure_code(exc))
        except (OSError, TimeoutError, ValueError):
            return ChannelObservation(
                phase="prepared", failure_code="endpoint_unavailable"
            )
        return ChannelObservation(
            phase="prepared",
            mechanism="runs",
            binding={"profile": snapshot.spec.endpoint.profile, "mechanism": "runs"},
            checkpoint={"session_id": session_id},
        )

    def _bound_connection(self, snapshot: HandoffSnapshot) -> _Connection:
        profile = str((snapshot.binding or {}).get("profile") or "")
        connection, failure = self._connection(profile)
        if connection is None:
            raise ChannelIndeterminate(failure or "endpoint_unavailable")
        return connection

    def _submit(
        self, snapshot: HandoffSnapshot, deadline: _Deadline
    ) -> ChannelObservation:
        connection = self._bound_connection(snapshot)
        session_id = str((snapshot.checkpoint or {}).get("session_id") or "")
        if not session_id:
            raise ChannelIndeterminate("session_missing")
        idempotency_key = f"handoff-{snapshot.handoff_id}"
        body = {"input": snapshot.spec.prompt, "session_id": session_id}
        try:
            response = _request_json(
                connection,
                "/v1/runs",
                deadline,
                method="POST",
                body=body,
                headers={"Idempotency-Key": idempotency_key},
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise ChannelDefinitelyNotAccepted("idempotency_conflict") from exc
            if 400 <= exc.code < 500:
                raise ChannelDefinitelyNotAccepted(_failure_code(exc)) from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        run_id = str(response.get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        return ChannelObservation(
            phase="submitted",
            checkpoint=_checkpoint(
                snapshot,
                session_id=session_id,
                run_id=run_id,
                idempotency_key=idempotency_key,
                status=str(response.get("status") or "queued"),
            ),
        )

    def submit(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        return self._submit(snapshot, _Deadline(budget_seconds))

    @staticmethod
    def _terminal_result(output: object) -> dict[str, object]:
        text = (
            output
            if isinstance(output, str)
            else json.dumps(output, sort_keys=True, separators=(",", ":"))
        )
        encoded = text.encode("utf-8")
        return {
            "text": text,
            "sha256": sha256(encoded).hexdigest(),
            "media_type": "text/plain"
            if isinstance(output, str)
            else "application/json",
            "size_bytes": len(encoded),
        }

    def _observe(
        self,
        snapshot: HandoffSnapshot,
        deadline: _Deadline,
        *,
        cancelling: bool = False,
    ) -> ChannelObservation:
        connection = self._bound_connection(snapshot)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            response = _request_json(
                connection,
                f"/v1/runs/{urllib.parse.quote(run_id, safe='')}",
                deadline,
            )
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise ChannelRetryableFailure() from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelRetryableFailure() from exc

        status = str(response.get("status") or "unknown")
        checkpoint = _checkpoint(snapshot, run_id=run_id, status=status)
        if status == "completed":
            try:
                result = self._terminal_result(response.get("output", ""))
                return ChannelObservation(
                    phase="succeeded", checkpoint=checkpoint, terminal_result=result
                )
            except (TypeError, UnicodeError, ValueError) as exc:
                raise ChannelIndeterminate() from exc
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

    def reconcile(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        deadline = _Deadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("run_id"):
            return self._observe(
                snapshot,
                deadline,
                cancelling=snapshot.cancel_requested_at is not None,
            )
        return self._submit(snapshot, deadline)

    def observe(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        return self._observe(snapshot, _Deadline(budget_seconds))

    def cancel(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        deadline = _Deadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("status") == "stopping":
            return self._observe(snapshot, deadline, cancelling=True)
        connection = self._bound_connection(snapshot)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            _request_json(
                connection,
                f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stop",
                deadline,
                method="POST",
                body={},
            )
        except urllib.error.HTTPError as exc:
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        return ChannelObservation(
            phase="cancelling",
            checkpoint=_checkpoint(snapshot, run_id=run_id, status="stopping"),
        )


__all__ = ["LocalHermesChannel", "MAX_RESPONSE_BYTES"]
