"""Isolated, host-owned Hermes agents for trusted plugins.

The public facade serializes policy rather than credentials. Every run starts a
fresh Python worker, so tool registry/cache/callback/environment mutations are
contained and cannot alter a long-lived parent conversation.
"""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
from pathlib import Path
import queue
import re
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from agent.structured_output import (
    DRAFT_2020_12_DIALECT,
    MAX_CANONICAL_SCHEMA_BYTES,
    MAX_OUTPUT_BYTES,
    StructuredOutputRequest,
    StructuredOutputSchema,
    StructuredOutputStrategy,
    normalize_schema,
)
from tools.managed_process import (
    ManagedProcessTree,
    ProcessIdentity,
    ProcessResourceLimits,
    TerminationPolicy,
)


PLUGIN_AGENT_MCP_IMPORT_POLICY_VERSION = 1


def plugin_agent_python_runtime_identity() -> str:
    """Credential-free identity for the exact trusted Python MCP host."""
    digest = hashlib.sha256()
    digest.update(b"hermes-plugin-agent-python-runtime-v1\0")
    for value in (
        sys.implementation.name,
        getattr(sys.implementation, "cache_tag", ""),
        sys.version,
        str(PLUGIN_AGENT_MCP_IMPORT_POLICY_VERSION),
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    with Path(sys.executable).open("rb") as interpreter:
        while chunk := interpreter.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_FRAME_BYTES = 4_000_000
_MAX_QUEUED_FRAMES = 8
_MAX_PROMPT_CHARS = 500_000
_MAX_POLICY_NAMES = 256
_MAX_ENCODED_SCHEMA_BYTES = ((MAX_CANONICAL_SCHEMA_BYTES + 2) // 3) * 4
_STRUCTURED_EVIDENCE_FIELDS = frozenset(
    {
        "provider_attempts",
        "model_calls",
        "strategy",
        "adapter_version",
        "schema_fingerprint",
        "declaration_source",
    }
)
_STRUCTURED_EVIDENCE_TEXT_LIMITS = {
    "strategy": 32,
    "schema_fingerprint": 64,
    "declaration_source": 64,
}
_PERSISTENT_SESSION_MISSING_AUDIT_FIELDS = frozenset(
    {"plugin_id", "failure_kind", "provider_attempts", "model_calls"}
)


class _PluginAgentCancelled(RuntimeError):
    """Internal control flow for caller-requested worker cancellation."""


class _PluginAgentResourceExceeded(RuntimeError):
    """Internal control flow for bounded worker-tree enforcement."""


class PluginAgentSessionMissingError(ValueError):
    """A requested persistent session was confirmed absent before provider use."""

    failure_kind = "persistent_session_missing"
    provider_attempts = 0
    model_calls = 0


class PluginAgentSessionUnavailableError(RuntimeError):
    """The parent could not determine session existence before provider use."""


class PluginAgentResultProtocolError(RuntimeError):
    """A spawned worker returned an invalid result after possible provider use."""


class _ProviderAttemptGrantExhausted(RuntimeError):
    """One shared sealed request tree has spent its provider-call authority."""

    failure_kind = "provider_attempt_grant_exhausted"
    status_code = 400


_PROVIDER_AUTHORITY_VERSION = 1
_PROVIDER_AUTHORITY_AUTHKEY_BYTES = 32
_PROVIDER_AUTHORITY_FRAME_BYTES = 1024
_PROVIDER_AUTHORITY_NONCE_BYTES = 32
_PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS = 0.5
_PROVIDER_AUTHORITY_ACCEPT_TIMEOUT_SECONDS = 0.05
_PROVIDER_AUTHORITY_MAX_CLIENTS = 8
_PROVIDER_AUTHORITY_REPLAY_WINDOW = 256


def _provider_authority_canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _provider_authority_mac(authkey: bytes, value: Mapping[str, Any]) -> str:
    return hmac.new(
        authkey, _provider_authority_canonical(value), hashlib.sha256
    ).hexdigest()


def _recv_provider_authority_frame(connection: socket.socket) -> bytes:
    header = bytearray()
    while len(header) < 4:
        chunk = connection.recv(4 - len(header))
        if not chunk:
            raise EOFError("provider authority frame ended before its header")
        header.extend(chunk)
    (size,) = struct.unpack("!I", bytes(header))
    if size < 1 or size > _PROVIDER_AUTHORITY_FRAME_BYTES:
        raise ValueError("provider authority frame size is invalid")
    body = bytearray()
    while len(body) < size:
        chunk = connection.recv(size - len(body))
        if not chunk:
            raise EOFError("provider authority frame ended before its body")
        body.extend(chunk)
    return bytes(body)


def _send_provider_authority_frame(
    connection: socket.socket, payload: Mapping[str, Any]
) -> None:
    body = _provider_authority_canonical(payload)
    if len(body) > _PROVIDER_AUTHORITY_FRAME_BYTES:
        raise ValueError("provider authority frame is too large")
    connection.sendall(struct.pack("!I", len(body)) + body)


def _validated_provider_attempt_authority(
    value: object,
) -> tuple[tuple[str, int], bytes]:
    if not isinstance(value, Mapping):
        raise ValueError("provider attempt authority must be an object")
    if set(value) != {"version", "host", "port", "authkey"}:
        raise ValueError("provider attempt authority fields are invalid")
    if value.get("version") != _PROVIDER_AUTHORITY_VERSION:
        raise ValueError("provider attempt authority version is invalid")
    host = value.get("host")
    port = value.get("port")
    token = value.get("authkey")
    if host != "127.0.0.1":
        raise ValueError("provider attempt authority host is invalid")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("provider attempt authority port is invalid")
    if not isinstance(token, str):
        raise ValueError("provider attempt authority authkey is invalid")
    try:
        authkey = base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("provider attempt authority authkey is invalid") from exc
    if len(authkey) != _PROVIDER_AUTHORITY_AUTHKEY_BYTES:
        raise ValueError("provider attempt authority authkey is invalid")
    return (host, port), authkey


def _shared_provider_attempt_request(
    descriptor: Mapping[str, Any], operation: str
) -> Mapping[str, Any]:
    address, authkey = _validated_provider_attempt_authority(descriptor)
    nonce = base64.b64encode(
        secrets.token_bytes(_PROVIDER_AUTHORITY_NONCE_BYTES)
    ).decode("ascii")
    unsigned = {
        "version": _PROVIDER_AUTHORITY_VERSION,
        "operation": operation,
        "nonce": nonce,
    }
    try:
        with socket.create_connection(
            address, timeout=_PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS
        ) as connection:
            connection.settimeout(_PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS)
            _send_provider_authority_frame(
                connection,
                {**unsigned, "mac": _provider_authority_mac(authkey, unsigned)},
            )
            response = json.loads(
                _recv_provider_authority_frame(connection).decode("ascii")
            )
    except (
        EOFError,
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise RuntimeError("sealed provider attempt authority unavailable") from exc
    if not isinstance(response, Mapping):
        raise RuntimeError("sealed provider attempt authority response is invalid")
    response = dict(response)
    response_mac = response.pop("mac", None)
    if (
        response.get("version") != _PROVIDER_AUTHORITY_VERSION
        or response.get("nonce") != nonce
        or not isinstance(response_mac, str)
        or not hmac.compare_digest(
            response_mac, _provider_authority_mac(authkey, response)
        )
    ):
        raise RuntimeError("sealed provider attempt authority response is invalid")
    response.pop("version", None)
    response.pop("nonce", None)
    return response


def _reserve_shared_provider_attempt(descriptor: Mapping[str, Any]) -> int:
    response = _shared_provider_attempt_request(descriptor, "reserve")
    count = response.get("provider_attempts")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RuntimeError("sealed provider attempt authority response is invalid")
    if response.get("reserved") is False:
        raise _ProviderAttemptGrantExhausted(
            "sealed provider attempt grant exhausted"
        )
    if response.get("reserved") is not True:
        raise RuntimeError("sealed provider attempt authority response is invalid")
    return count


def _snapshot_shared_provider_attempts(
    descriptor: Mapping[str, Any],
) -> dict[str, int | bool]:
    response = _shared_provider_attempt_request(descriptor, "snapshot")
    count = response.get("provider_attempts")
    exhausted = response.get("exhausted")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or not isinstance(exhausted, bool)
    ):
        raise RuntimeError("sealed provider attempt authority response is invalid")
    return {"provider_attempts": count, "exhausted": exhausted}


class _ProviderAttemptAuthority:
    """Authenticated request-local broker for one process-tree attempt grant."""

    def __init__(self, grant: int) -> None:
        if isinstance(grant, bool) or not isinstance(grant, int) or not 1 <= grant <= 5:
            raise ValueError("provider attempt authority grant must be between 1 and 5")
        self._grant = grant
        self._provider_attempts = 0
        self._exhausted = False
        self._state_lock = threading.Lock()
        self._authkey = secrets.token_bytes(_PROVIDER_AUTHORITY_AUTHKEY_BYTES)
        self._seen_nonces: set[str] = set()
        self._nonce_order: deque[str] = deque()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(_PROVIDER_AUTHORITY_MAX_CLIENTS * 2)
        self._listener.settimeout(_PROVIDER_AUTHORITY_ACCEPT_TIMEOUT_SECONDS)
        host, port = self._listener.getsockname()
        self.descriptor: Mapping[str, Any] = {
            "version": _PROVIDER_AUTHORITY_VERSION,
            "host": host,
            "port": port,
            "authkey": base64.b64encode(self._authkey).decode("ascii"),
        }
        self._closed = False
        self._close_event = threading.Event()
        self._clients = threading.BoundedSemaphore(_PROVIDER_AUTHORITY_MAX_CLIENTS)
        self._connections_lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._thread = threading.Thread(
            target=self._serve,
            name="provider-attempt-authority",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        while not self._close_event.is_set():
            try:
                connection, _address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._close_event.is_set():
                    return
                continue
            if not self._clients.acquire(blocking=False):
                connection.close()
                continue
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="provider-attempt-authority-client",
                daemon=True,
            )
            with self._connections_lock:
                if self._close_event.is_set():
                    self._clients.release()
                    connection.close()
                    return
                self._connections.add(connection)
                self._workers.add(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(_PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS)
            request = json.loads(
                _recv_provider_authority_frame(connection).decode("ascii")
            )
            if not isinstance(request, Mapping) or set(request) != {
                "version",
                "operation",
                "nonce",
                "mac",
            }:
                return
            request = dict(request)
            request_mac = request.pop("mac", None)
            nonce = request.get("nonce")
            operation = request.get("operation")
            if (
                request.get("version") != _PROVIDER_AUTHORITY_VERSION
                or operation not in {"reserve", "snapshot"}
                or not isinstance(nonce, str)
                or not isinstance(request_mac, str)
            ):
                return
            try:
                nonce_bytes = base64.b64decode(nonce, validate=True)
            except (binascii.Error, ValueError):
                return
            if (
                len(nonce_bytes) != _PROVIDER_AUTHORITY_NONCE_BYTES
                or not hmac.compare_digest(
                    request_mac, _provider_authority_mac(self._authkey, request)
                )
            ):
                return
            with self._state_lock:
                if nonce in self._seen_nonces:
                    return
                self._seen_nonces.add(nonce)
                self._nonce_order.append(nonce)
                if len(self._nonce_order) > _PROVIDER_AUTHORITY_REPLAY_WINDOW:
                    expired = self._nonce_order.popleft()
                    self._seen_nonces.discard(expired)
                if operation == "reserve":
                    if self._provider_attempts >= self._grant:
                        self._exhausted = True
                        response = {
                            "reserved": False,
                            "provider_attempts": self._provider_attempts,
                        }
                    else:
                        self._provider_attempts += 1
                        response = {
                            "reserved": True,
                            "provider_attempts": self._provider_attempts,
                        }
                else:
                    response = {
                        "provider_attempts": self._provider_attempts,
                        "exhausted": self._exhausted,
                    }
            signed = {
                "version": _PROVIDER_AUTHORITY_VERSION,
                "nonce": nonce,
                **response,
            }
            _send_provider_authority_frame(
                connection,
                {**signed, "mac": _provider_authority_mac(self._authkey, signed)},
            )
        except (
            EOFError,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
        ):
            pass
        finally:
            try:
                connection.close()
            finally:
                with self._connections_lock:
                    self._connections.discard(connection)
                    self._workers.discard(threading.current_thread())
                self._clients.release()

    def snapshot(self) -> dict[str, int | bool]:
        with self._state_lock:
            return {
                "provider_attempts": self._provider_attempts,
                "exhausted": self._exhausted,
            }

    def close(self) -> None:
        with self._connections_lock:
            if self._closed:
                return
            self._closed = True
            self._close_event.set()
            connections = tuple(self._connections)
        try:
            self._listener.close()
        except OSError:
            pass
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._thread.join(
            timeout=_PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS
            + _PROVIDER_AUTHORITY_ACCEPT_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + _PROVIDER_AUTHORITY_IO_TIMEOUT_SECONDS
        while True:
            with self._connections_lock:
                workers = tuple(self._workers)
            if not workers or time.monotonic() >= deadline:
                break
            for worker in workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))


@dataclass(frozen=True)
class PluginAgentRunRequest:
    prompt: str
    provider: str | None = None
    model: str | None = None
    context_mode: Literal["fresh", "shared"] = "fresh"
    session_id: str | None = None
    intended_authority_digest: str | None = None
    expected_model_visible_prefix_digest: str | None = None
    expected_runtime_identity: Mapping[str, str] | None = None
    expected_mcp_runtime_identity_digest: str | None = None
    enabled_toolsets: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    hooks: tuple[Mapping[str, Any], ...] = ()
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None
    inline_agents: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    reasoning_config: Mapping[str, Any] | None = None
    fallback_model: str | None = None
    sealed_fallback_route: Mapping[str, Any] | None = None
    ephemeral_system_prompt: str | None = None
    request_overrides: Mapping[str, Any] = field(default_factory=dict)
    structured_output: StructuredOutputRequest | None = None
    max_budget_usd: float | None = None
    _cost_budget_authority: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )
    _cost_budget_contract: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )
    sandbox_policy: Mapping[str, Any] | None = None
    approved_action_digest: str | None = None
    workdir: Path | None = None
    max_iterations: int = 90
    max_api_attempts: int = 3
    sealed_provider_attempt_grant: bool = False
    _provider_attempt_authority: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )
    idle_timeout_seconds: float = 300.0
    wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0
    absolute_wall_deadline: float | None = None
    absolute_idle_deadline: float | None = None
    absolute_provider_deadline: float | None = None
    max_process_tree_rss_bytes: int = 2048 * 1024 * 1024
    max_process_tree_cpu_seconds: float = 900.0
    max_descendants: int = 32
    cooperative_shutdown_seconds: float = 5.0
    term_grace_seconds: float = 5.0
    kill_reap_grace_seconds: float = 2.0

    def to_wire(self) -> dict[str, Any]:
        payload = {
            "prompt": self.prompt,
            "provider": self.provider,
            "model": self.model,
            "context_mode": self.context_mode,
            "session_id": self.session_id,
            "intended_authority_digest": self.intended_authority_digest,
            "expected_model_visible_prefix_digest": (
                self.expected_model_visible_prefix_digest
            ),
            "expected_runtime_identity": _wire_json(self.expected_runtime_identity),
            "expected_mcp_runtime_identity_digest": (
                self.expected_mcp_runtime_identity_digest
            ),
            "enabled_toolsets": _wire_json(self.enabled_toolsets),
            "allowed_tools": _wire_json(self.allowed_tools),
            "denied_tools": _wire_json(self.denied_tools),
            "skills": _wire_json(self.skills),
            "hooks": _wire_json(self.hooks),
            "mcp_servers": _wire_json(self.mcp_servers),
            "inline_agents": _wire_json(self.inline_agents),
            "reasoning_config": _wire_json(self.reasoning_config),
            "fallback_model": self.fallback_model,
            "sealed_fallback_route": _sealed_fallback_route_to_wire(
                self.sealed_fallback_route
            ),
            "ephemeral_system_prompt": self.ephemeral_system_prompt,
            "request_overrides": _wire_json(self.request_overrides),
            "structured_output": (
                _structured_output_to_wire(self.structured_output)
                if self.structured_output is not None
                else None
            ),
            "max_budget_usd": self.max_budget_usd,
            "sandbox_policy": _wire_json(self.sandbox_policy),
            "approved_action_digest": self.approved_action_digest,
            "workdir": str(self.workdir) if self.workdir is not None else None,
            "max_iterations": self.max_iterations,
            "max_api_attempts": self.max_api_attempts,
            "sealed_provider_attempt_grant": self.sealed_provider_attempt_grant,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "wall_timeout_seconds": self.wall_timeout_seconds,
            "provider_request_timeout_seconds": self.provider_request_timeout_seconds,
            "absolute_wall_deadline": self.absolute_wall_deadline,
            "absolute_idle_deadline": self.absolute_idle_deadline,
            "absolute_provider_deadline": self.absolute_provider_deadline,
            "max_process_tree_rss_bytes": self.max_process_tree_rss_bytes,
            "max_process_tree_cpu_seconds": self.max_process_tree_cpu_seconds,
            "max_descendants": self.max_descendants,
            "cooperative_shutdown_seconds": self.cooperative_shutdown_seconds,
            "term_grace_seconds": self.term_grace_seconds,
            "kill_reap_grace_seconds": self.kill_reap_grace_seconds,
        }
        if self._provider_attempt_authority is not None:
            payload["_provider_attempt_authority"] = _wire_json(
                self._provider_attempt_authority
            )
        if self._cost_budget_authority is not None:
            payload["_cost_budget_authority"] = _wire_json(
                self._cost_budget_authority
            )
        if self._cost_budget_contract is not None:
            payload["_cost_budget_contract"] = _wire_json(
                self._cost_budget_contract
            )
        return payload

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "PluginAgentRunRequest":
        if not isinstance(value, Mapping):
            raise ValueError("plugin-agent request must be an object")
        allowed = {
            "prompt",
            "provider",
            "model",
            "context_mode",
            "session_id",
            "intended_authority_digest",
            "expected_model_visible_prefix_digest",
            "expected_runtime_identity",
            "expected_mcp_runtime_identity_digest",
            "enabled_toolsets",
            "allowed_tools",
            "denied_tools",
            "skills",
            "hooks",
            "mcp_servers",
            "inline_agents",
            "reasoning_config",
            "fallback_model",
            "sealed_fallback_route",
            "ephemeral_system_prompt",
            "request_overrides",
            "structured_output",
            "max_budget_usd",
            "sandbox_policy",
            "approved_action_digest",
            "workdir",
            "max_iterations",
            "max_api_attempts",
            "sealed_provider_attempt_grant",
            "_provider_attempt_authority",
            "_cost_budget_authority",
            "_cost_budget_contract",
            "idle_timeout_seconds",
            "wall_timeout_seconds",
            "provider_request_timeout_seconds",
            "absolute_wall_deadline",
            "absolute_idle_deadline",
            "absolute_provider_deadline",
            "max_process_tree_rss_bytes",
            "max_process_tree_cpu_seconds",
            "max_descendants",
            "cooperative_shutdown_seconds",
            "term_grace_seconds",
            "kill_reap_grace_seconds",
        }
        _reject_unknown_fields("plugin-agent request", value, allowed)
        data = dict(value)
        for name in (
            "enabled_toolsets",
            "allowed_tools",
            "denied_tools",
            "skills",
            "hooks",
        ):
            if data.get(name) is not None:
                data[name] = tuple(data[name])
        if data.get("workdir") is not None:
            if not isinstance(data["workdir"], str):
                raise ValueError("plugin-agent request workdir must be text")
            data["workdir"] = Path(data["workdir"])
        if data.get("structured_output") is not None:
            data["structured_output"] = _structured_output_from_wire(
                data["structured_output"]
            )
        fallback = data.get("sealed_fallback_route")
        if isinstance(fallback, Mapping):
            fallback_data = dict(fallback)
            if fallback_data.get("structured_output") is not None:
                fallback_data["structured_output"] = _structured_output_from_wire(
                    fallback_data["structured_output"]
                )
            data["sealed_fallback_route"] = fallback_data
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError("plugin-agent request wire value is invalid") from exc


@dataclass(frozen=True)
class PluginAgentRunResult:
    final_response: str
    session_id: str
    provider: str
    model: str
    status: Literal["completed", "paused", "cancelled", "failed"]
    pending_interaction: Mapping[str, str] | None
    usage: Mapping[str, int | float | None]
    audit: Mapping[str, Any]
    structured_output: Mapping[str, str | int] | None = None

    def __post_init__(self) -> None:
        if self.pending_interaction is not None:
            object.__setattr__(
                self,
                "pending_interaction",
                MappingProxyType(dict(self.pending_interaction)),
            )
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        object.__setattr__(self, "audit", MappingProxyType(dict(self.audit)))
        if self.structured_output is not None:
            evidence = _validated_structured_evidence(self.structured_output)
            for name in _STRUCTURED_EVIDENCE_FIELDS:
                if self.audit.get(name) != evidence[name]:
                    raise ValueError(
                        "structured output evidence disagrees with audit field "
                        f"{name}"
                    )
            object.__setattr__(
                self, "structured_output", MappingProxyType(dict(evidence))
            )

    def to_wire(self) -> dict[str, Any]:
        return {
            "final_response": self.final_response,
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "pending_interaction": _wire_json(self.pending_interaction),
            "usage": _wire_json(self.usage),
            "audit": _wire_json(self.audit),
            "structured_output": _wire_json(self.structured_output),
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "PluginAgentRunResult":
        if not isinstance(value, Mapping):
            raise ValueError("plugin-agent result must be an object")
        allowed = {
            "final_response",
            "session_id",
            "provider",
            "model",
            "status",
            "pending_interaction",
            "usage",
            "audit",
            "structured_output",
        }
        _reject_unknown_fields("plugin-agent result", value, allowed)
        try:
            return cls(
                final_response=value["final_response"],
                session_id=value["session_id"],
                provider=value["provider"],
                model=value["model"],
                status=value["status"],
                pending_interaction=value["pending_interaction"],
                usage=value["usage"],
                audit=value["audit"],
                structured_output=value.get("structured_output"),
            )
        except KeyError as exc:
            raise ValueError(f"plugin-agent result is missing {exc.args[0]}") from exc


def _wire_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _wire_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_json(item) for item in value]
    return value


def _reject_unknown_fields(
    label: str, value: Mapping[str, Any], allowed: set[str] | frozenset[str]
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")


def _structured_output_to_wire(request: StructuredOutputRequest) -> dict[str, Any]:
    _validate_structured_output(request)
    schema = request.schema
    return {
        "schema": {
            "canonical_schema": _wire_json(schema.canonical_schema),
            "schema_fingerprint": schema.schema_fingerprint,
            "canonical_schema_bytes": base64.b64encode(
                schema.canonical_schema_bytes
            ).decode("ascii"),
            "dialect": schema.dialect,
        },
        "strategy": request.strategy.value,
        "adapter_version": request.adapter_version,
        "output_bytes_limit": request.output_bytes_limit,
        "canonicalization_version": request.canonicalization_version,
    }


def _sealed_fallback_route_to_wire(
    route: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if route is None:
        return None
    payload = dict(route)
    structured = payload.get("structured_output")
    if structured is not None:
        payload["structured_output"] = _structured_output_to_wire(structured)
    return _wire_json(payload)


def _structured_output_from_wire(value: object) -> StructuredOutputRequest:
    if not isinstance(value, Mapping):
        raise ValueError("structured output must be an object")
    allowed = {
        "schema",
        "strategy",
        "adapter_version",
        "output_bytes_limit",
        "canonicalization_version",
    }
    _reject_unknown_fields("structured output", value, allowed)
    schema_wire = value.get("schema")
    if not isinstance(schema_wire, Mapping):
        raise ValueError("structured output schema must be an object")
    schema_allowed = {
        "canonical_schema",
        "schema_fingerprint",
        "canonical_schema_bytes",
        "dialect",
    }
    _reject_unknown_fields("structured output schema", schema_wire, schema_allowed)
    try:
        encoded = schema_wire["canonical_schema_bytes"]
        if not isinstance(encoded, str):
            raise ValueError("structured output schema bytes must be base64 text")
        if len(encoded) > _MAX_ENCODED_SCHEMA_BYTES:
            raise ValueError(
                "structured output encoded schema exceeds the size limit"
            )
        canonical_bytes = base64.b64decode(encoded, validate=True)
        if len(canonical_bytes) > MAX_CANONICAL_SCHEMA_BYTES:
            raise ValueError("structured output schema exceeds the bytes limit")
        try:
            decoded_schema = normalize_schema(json.loads(canonical_bytes))
            supplied_schema = normalize_schema(schema_wire["canonical_schema"])
        except RecursionError as exc:
            raise ValueError("structured output schema exceeds the depth limit") from exc
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"structured output schema is invalid: {exc}") from exc
        if (
            decoded_schema.canonical_schema_bytes != canonical_bytes
            or supplied_schema.canonical_schema_bytes != canonical_bytes
            or supplied_schema.schema_fingerprint
            != decoded_schema.schema_fingerprint
            or schema_wire["schema_fingerprint"]
            != decoded_schema.schema_fingerprint
            or schema_wire["dialect"] != DRAFT_2020_12_DIALECT
        ):
            raise ValueError("structured output schema evidence is contradictory")
        request = StructuredOutputRequest(
            schema=StructuredOutputSchema(
                canonical_schema=decoded_schema.canonical_schema,
                schema_fingerprint=decoded_schema.schema_fingerprint,
                canonical_schema_bytes=canonical_bytes,
                dialect=DRAFT_2020_12_DIALECT,
            ),
            strategy=StructuredOutputStrategy(value["strategy"]),
            adapter_version=value["adapter_version"],
            output_bytes_limit=value["output_bytes_limit"],
            canonicalization_version=value["canonicalization_version"],
        )
    except KeyError as exc:
        raise ValueError(f"structured output is missing {exc.args[0]}") from exc
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("structured output"):
            raise
        raise ValueError("structured output wire value is invalid") from exc
    _validate_structured_output(request)
    return request


def _validate_structured_output(request: StructuredOutputRequest) -> None:
    if not isinstance(request, StructuredOutputRequest):
        raise ValueError("structured output must be StructuredOutputRequest")
    schema = request.schema
    if not isinstance(schema, StructuredOutputSchema):
        raise ValueError("structured output schema is invalid")
    if len(schema.canonical_schema_bytes) > MAX_CANONICAL_SCHEMA_BYTES:
        raise ValueError("structured output schema exceeds the bytes limit")
    if schema.dialect != DRAFT_2020_12_DIALECT:
        raise ValueError("structured output schema dialect is invalid")
    try:
        raw_schema = json.loads(schema.canonical_schema_bytes)
        normalized = normalize_schema(raw_schema)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"structured output schema is invalid: {exc}") from exc
    if (
        normalized.schema_fingerprint != schema.schema_fingerprint
        or normalized.canonical_schema_bytes != schema.canonical_schema_bytes
        or _wire_json(schema.canonical_schema)
        != _wire_json(normalized.canonical_schema)
    ):
        raise ValueError("structured output schema evidence is contradictory")
    if not isinstance(request.strategy, StructuredOutputStrategy):
        raise ValueError("structured output strategy is invalid")
    if type(request.adapter_version) is not int or request.adapter_version <= 0:
        raise ValueError("structured output adapter version is invalid")
    if (
        isinstance(request.output_bytes_limit, bool)
        or not isinstance(request.output_bytes_limit, int)
        or not 0 < request.output_bytes_limit <= MAX_OUTPUT_BYTES
    ):
        raise ValueError("structured output bytes limit is invalid")
    if (
        type(request.canonicalization_version) is not int
        or request.canonicalization_version != 1
    ):
        raise ValueError("structured output canonicalization version is invalid")


def _validated_structured_evidence(
    value: Mapping[str, Any],
) -> Mapping[str, str | int]:
    if not isinstance(value, Mapping):
        raise ValueError("structured output evidence must be an object")
    _reject_unknown_fields("structured output evidence", value, _STRUCTURED_EVIDENCE_FIELDS)
    missing = sorted(_STRUCTURED_EVIDENCE_FIELDS - set(value))
    if missing:
        raise ValueError(
            f"structured output evidence is missing field(s): {', '.join(missing)}"
        )
    for name in ("provider_attempts", "model_calls"):
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"structured output evidence {name} is invalid")
    adapter_version = value["adapter_version"]
    if type(adapter_version) is not int or adapter_version <= 0:
        raise ValueError("structured output evidence adapter_version is invalid")
    strategy = value["strategy"]
    if strategy not in {item.value for item in StructuredOutputStrategy}:
        raise ValueError("structured output evidence strategy is invalid")
    fingerprint = value["schema_fingerprint"]
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("structured output evidence schema fingerprint is invalid")
    for name, limit in _STRUCTURED_EVIDENCE_TEXT_LIMITS.items():
        item = value[name]
        if not isinstance(item, str) or len(item) > limit:
            raise ValueError(f"structured output evidence {name} is invalid")
    return value


def _correlate_structured_result(
    request: PluginAgentRunRequest, result: PluginAgentRunResult
) -> None:
    admitted = request.structured_output
    if result.audit.get("fallback_used") is True:
        fallback = request.sealed_fallback_route
        if (
            not isinstance(fallback, Mapping)
            or result.audit.get("fallback_context") != "fresh"
        ):
            raise RuntimeError("fallback structured output authority is missing")
        admitted = fallback.get("structured_output")
    evidence = result.structured_output
    if admitted is None:
        if evidence is not None:
            raise RuntimeError("unexpected structured output evidence")
        return
    if evidence is None:
        raise RuntimeError("structured output evidence is missing")
    failure_kind = result.audit.get("failure_kind")
    if failure_kind in {
        "structured_output_capability_drift",
        "structured_output_unsupported",
    }:
        if (
            result.status != "failed"
            or evidence["provider_attempts"] != 0
            or evidence["model_calls"] != 0
        ):
            raise RuntimeError("structured output negotiation failure is invalid")
        return
    if (
        evidence["strategy"] != admitted.strategy.value
        or evidence["adapter_version"] != admitted.adapter_version
        or evidence["schema_fingerprint"]
        != admitted.schema.schema_fingerprint
    ):
        raise RuntimeError("structured output evidence does not match request")


def _correlate_persistent_session_result(
    plugin_id: str,
    request: PluginAgentRunRequest,
    result: PluginAgentRunResult,
) -> bool:
    if result.audit.get("failure_kind") != "persistent_session_missing":
        return False
    audit = result.audit
    valid = (
        request.context_mode == "shared"
        and result.status == "failed"
        and result.final_response == ""
        and result.session_id == ""
        and result.provider == ""
        and result.model == ""
        and result.pending_interaction is None
        and not result.usage
        and result.structured_output is None
        and set(audit) == _PERSISTENT_SESSION_MISSING_AUDIT_FIELDS
        and audit.get("plugin_id") == plugin_id
        and type(audit.get("provider_attempts")) is int
        and audit.get("provider_attempts") == 0
        and type(audit.get("model_calls")) is int
        and audit.get("model_calls") == 0
    )
    if not valid:
        raise RuntimeError("persistent session missing result is invalid")
    return True


def _validate_name_list(label: str, values: tuple[str, ...] | None) -> None:
    if values is None:
        return
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple or None")
    if len(values) > _MAX_POLICY_NAMES:
        raise ValueError(f"{label} exceeds {_MAX_POLICY_NAMES} names")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty strings")


def _validate_request(request: PluginAgentRunRequest) -> None:
    if not isinstance(request, PluginAgentRunRequest):
        raise TypeError("request must be PluginAgentRunRequest")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ValueError("prompt must be non-empty")
    if len(request.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError("prompt exceeds the plugin-agent size limit")
    if request.context_mode not in {"fresh", "shared"}:
        raise ValueError("context_mode must be fresh or shared")
    if request.context_mode == "shared" and (
        not isinstance(request.session_id, str) or not request.session_id.strip()
    ):
        raise ValueError("shared context requires session_id")
    for label, value in (
        ("intended authority", request.intended_authority_digest),
        (
            "expected model-visible prefix",
            request.expected_model_visible_prefix_digest,
        ),
        (
            "expected MCP runtime identity",
            request.expected_mcp_runtime_identity_digest,
        ),
    ):
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"{label} digest must be lowercase SHA-256")
    if (
        request.expected_model_visible_prefix_digest is not None
        and request.intended_authority_digest is None
    ):
        raise ValueError(
            "expected model-visible prefix requires sealed intended authority"
        )
    if request.expected_runtime_identity is not None:
        expected_runtime_fields = {
            "provider",
            "model",
            "api_mode",
            "base_url_trust_class",
            "registration_provenance_digest",
        }
        if (
            not isinstance(request.expected_runtime_identity, Mapping)
            or set(request.expected_runtime_identity) != expected_runtime_fields
            or any(
                not isinstance(request.expected_runtime_identity[field], str)
                or not request.expected_runtime_identity[field]
                for field in expected_runtime_fields
            )
            or re.fullmatch(
                r"[0-9a-f]{64}",
                request.expected_runtime_identity[
                    "registration_provenance_digest"
                ],
            )
            is None
        ):
            raise ValueError("expected runtime identity is malformed")
        if request.intended_authority_digest is None:
            raise ValueError(
                "expected runtime identity requires sealed intended authority"
            )
    if (
        request.expected_mcp_runtime_identity_digest is not None
        and request.intended_authority_digest is None
    ):
        raise ValueError(
            "expected MCP runtime identity requires sealed intended authority"
        )
    if (
        not isinstance(request.max_iterations, int)
        or isinstance(request.max_iterations, bool)
        or request.max_iterations <= 0
    ):
        raise ValueError("max_iterations must be a positive integer")
    if (
        not isinstance(request.max_api_attempts, int)
        or isinstance(request.max_api_attempts, bool)
        or not 1 <= request.max_api_attempts <= 5
    ):
        raise ValueError("max API attempts must be between 1 and 5")
    if not isinstance(request.sealed_provider_attempt_grant, bool):
        raise ValueError("sealed provider attempt grant must be boolean")
    if request._provider_attempt_authority is not None:
        if not request.sealed_provider_attempt_grant:
            raise ValueError(
                "provider attempt authority requires a sealed provider grant"
            )
        _validated_provider_attempt_authority(request._provider_attempt_authority)
    for label, value in (("provider", request.provider), ("model", request.model)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} must be a non-empty string or None")
    if request.approved_action_digest is not None and not re.fullmatch(
        r"[0-9a-f]{64}", request.approved_action_digest
    ):
        raise ValueError(
            "approved action digest must be 64 lowercase hexadecimal characters"
        )
    for label, value in (
        ("idle_timeout_seconds", request.idle_timeout_seconds),
        ("wall_timeout_seconds", request.wall_timeout_seconds),
        ("provider_request_timeout_seconds", request.provider_request_timeout_seconds),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive")
    if request.idle_timeout_seconds > request.wall_timeout_seconds:
        raise ValueError("idle timeout cannot exceed wall timeout")
    if request.provider_request_timeout_seconds > request.wall_timeout_seconds:
        raise ValueError("provider request timeout cannot exceed wall timeout")
    for label, value in (
        ("absolute_wall_deadline", request.absolute_wall_deadline),
        ("absolute_idle_deadline", request.absolute_idle_deadline),
        ("absolute_provider_deadline", request.absolute_provider_deadline),
    ):
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive or None")
    if request.absolute_wall_deadline is not None:
        for label, value in (
            ("absolute idle deadline", request.absolute_idle_deadline),
            ("absolute provider deadline", request.absolute_provider_deadline),
        ):
            if value is not None and value > request.absolute_wall_deadline:
                raise ValueError(f"{label} cannot exceed absolute wall deadline")
    for label, value in (
        ("cooperative shutdown", request.cooperative_shutdown_seconds),
        ("TERM grace", request.term_grace_seconds),
        ("KILL/reap grace", request.kill_reap_grace_seconds),
    ):
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive")
    try:
        ProcessResourceLimits(
            max_rss_bytes=request.max_process_tree_rss_bytes,
            max_cpu_seconds=request.max_process_tree_cpu_seconds,
            max_descendants=request.max_descendants,
        )
    except ValueError as exc:
        raise ValueError(f"plugin-agent resource limits are invalid: {exc}") from exc
    for label, values in (
        ("enabled_toolsets", request.enabled_toolsets),
        ("allowed_tools", request.allowed_tools),
        ("denied_tools", request.denied_tools),
        ("skills", request.skills),
    ):
        _validate_name_list(label, values)
    if not isinstance(request.hooks, tuple) or len(request.hooks) > 128:
        raise ValueError("hooks must be a tuple with at most 128 entries")
    for hook in request.hooks:
        if not isinstance(hook, Mapping):
            raise ValueError("every hook must be a mapping")
    try:
        if len(json.dumps(request.hooks, default=str).encode("utf-8")) > 256_000:
            raise ValueError("hooks exceed the plugin-agent size limit")
    except (TypeError, ValueError) as exc:
        raise ValueError("hooks must be JSON serializable") from exc
    if request.mcp_servers is not None:
        if (
            not isinstance(request.mcp_servers, Mapping)
            or len(request.mcp_servers) > 32
        ):
            raise ValueError("mcp_servers must contain at most 32 server mappings")
        if any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(config, Mapping)
            for name, config in request.mcp_servers.items()
        ):
            raise ValueError("mcp_servers must map non-empty names to mappings")
        try:
            encoded_mcp = json.dumps(request.mcp_servers, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("mcp_servers must be JSON serializable") from exc
        if len(encoded_mcp) > 256_000:
            raise ValueError("mcp_servers exceed the plugin-agent size limit")
    if (
        not isinstance(request.inline_agents, Mapping)
        or len(request.inline_agents) > 16
    ):
        raise ValueError("inline_agents must contain at most 16 definitions")
    for agent_id, definition in request.inline_agents.items():
        if not isinstance(agent_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", agent_id
        ):
            raise ValueError("inline agent ids must be kebab-case")
        if not isinstance(definition, Mapping):
            raise ValueError(f"inline agent {agent_id} must be a mapping")
        turns = definition.get("max_iterations", 90)
        if (
            isinstance(turns, bool)
            or not isinstance(turns, int)
            or not 1 <= turns <= 90
        ):
            raise ValueError("inline agent max_iterations must be between 1 and 90")
    for label, value in (
        ("reasoning_config", request.reasoning_config),
        ("request_overrides", request.request_overrides),
        ("sandbox_policy", request.sandbox_policy),
    ):
        if value is not None and not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a mapping or None")
    if request.fallback_model is not None and (
        not isinstance(request.fallback_model, str)
        or not request.fallback_model.strip()
    ):
        raise ValueError("fallback_model must be a non-empty string or None")
    if request.sealed_fallback_route is not None:
        fallback = request.sealed_fallback_route
        required = {
            "provider",
            "model",
            "context_mode",
            "expected_runtime_identity",
            "reasoning_config",
            "request_overrides",
            "structured_output",
        }
        if (
            not isinstance(fallback, Mapping)
            or set(fallback) != required
            or fallback.get("context_mode") != "fresh"
            or not isinstance(fallback.get("provider"), str)
            or not fallback.get("provider")
            or not isinstance(fallback.get("model"), str)
            or not fallback.get("model")
            or not isinstance(fallback.get("reasoning_config"), Mapping)
            or not isinstance(fallback.get("request_overrides"), Mapping)
            or (
                fallback.get("structured_output") is not None
                and not isinstance(
                    fallback.get("structured_output"), StructuredOutputRequest
                )
            )
            or request.intended_authority_digest is None
            or request.sealed_provider_attempt_grant is not True
            or request.fallback_model is not None
        ):
            raise ValueError("sealed fallback route is malformed")
        fallback_structured = fallback.get("structured_output")
        if (fallback_structured is None) != (request.structured_output is None):
            raise ValueError("sealed fallback route is malformed")
        if fallback_structured is not None and request.structured_output is not None:
            _validate_structured_output(fallback_structured)
            if (
                fallback_structured.schema.schema_fingerprint
                != request.structured_output.schema.schema_fingerprint
                or fallback_structured.output_bytes_limit
                != request.structured_output.output_bytes_limit
                or fallback_structured.canonicalization_version
                != request.structured_output.canonicalization_version
            ):
                raise ValueError("sealed fallback route is malformed")
        identity = fallback.get("expected_runtime_identity")
        expected_runtime_fields = {
            "provider",
            "model",
            "api_mode",
            "base_url_trust_class",
            "registration_provenance_digest",
        }
        if (
            not isinstance(identity, Mapping)
            or set(identity) != expected_runtime_fields
            or identity.get("provider") != fallback.get("provider")
            or identity.get("model") != fallback.get("model")
            or any(
                not isinstance(identity.get(field), str) or not identity.get(field)
                for field in expected_runtime_fields
            )
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(identity.get("registration_provenance_digest", "")),
            )
            is None
        ):
            raise ValueError("sealed fallback route is malformed")
    if request.ephemeral_system_prompt is not None and not isinstance(
        request.ephemeral_system_prompt, str
    ):
        raise ValueError("ephemeral_system_prompt must be text or None")
    if request.max_budget_usd is not None and (
        isinstance(request.max_budget_usd, bool)
        or not isinstance(request.max_budget_usd, int | float)
        or not math.isfinite(request.max_budget_usd)
        or request.max_budget_usd <= 0
    ):
        raise ValueError("max_budget_usd must be finite and positive")
    cost_authority_present = request._cost_budget_authority is not None
    cost_contract_present = request._cost_budget_contract is not None
    if cost_authority_present != cost_contract_present:
        raise ValueError("authoritative cost authority and contract must be paired")
    if (
        request.max_budget_usd is not None
        and request.intended_authority_digest is not None
        and not cost_authority_present
    ):
        raise ValueError("max_budget_usd requires authoritative cost enforcement")
    if cost_authority_present:
        if request.max_budget_usd is None:
            raise ValueError("authoritative cost authority requires max_budget_usd")
        from agent.cost_budget import validate_cost_budget_authority_descriptor
        from agent.usage_pricing import AuthoritativeSettlementContract

        validate_cost_budget_authority_descriptor(request._cost_budget_authority)
        contract = request._cost_budget_contract
        if not isinstance(contract, Mapping) or set(contract) != {
            "provider",
            "strategy",
            "billing_mode",
            "covered_outcomes",
        }:
            raise ValueError("authoritative cost contract is invalid")
        covered = contract.get("covered_outcomes")
        if not isinstance(covered, (list, tuple)):
            raise ValueError("authoritative cost contract is invalid")
        parsed_contract = AuthoritativeSettlementContract(
            provider=contract.get("provider"),
            strategy=contract.get("strategy"),
            billing_mode=contract.get("billing_mode"),
            covered_outcomes=frozenset(covered),
        )
        if not parsed_contract.complete:
            raise ValueError("authoritative cost contract is incomplete")
    if request.structured_output is not None:
        _validate_structured_output(request.structured_output)
        overrides = request.request_overrides
        if "response_format" in overrides or (
            isinstance(overrides.get("text"), Mapping)
            and "format" in overrides["text"]
        ) or (
            isinstance(overrides.get("output_config"), Mapping)
            and "format" in overrides["output_config"]
        ):
            raise ValueError(
                "structured output cannot be combined with a provider format override"
            )
    if request.workdir is not None:
        path = Path(request.workdir).expanduser()
        if not path.is_dir():
            raise ValueError("workdir must be an existing directory")


def _agent_override_allowed(
    plugin_id: str,
    kind: str,
    value: str | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> bool:
    if not value:
        return True
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        entry = (((config or {}).get("plugins") or {}).get("entries") or {}).get(
            plugin_id, {}
        )
        policy = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(policy, dict) or not policy.get(
            f"allow_{kind}_override", False
        ):
            return False
        allowed = policy.get(f"allowed_{kind}s")
        return not isinstance(allowed, list) or "*" in allowed or value in allowed
    except Exception:
        return False


def _request_payload(plugin_id: str, request: PluginAgentRunRequest) -> dict[str, Any]:
    body = request.to_wire()
    body["workdir"] = (
        str(Path(request.workdir).expanduser().resolve()) if request.workdir else None
    )
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "type": "run",
        "plugin_id": plugin_id,
        "provider_start_handshake": {"required": True},
        "request": body,
    }


def _read_stream(
    stream,
    events: queue.Queue,
    label: str,
    *,
    stopped: threading.Event,
) -> None:
    try:
        while not stopped.is_set():
            line = stream.readline(_MAX_FRAME_BYTES + 1)
            if line == "":
                break
            while not stopped.is_set():
                try:
                    events.put((label, line), timeout=0.05)
                    break
                except queue.Full:
                    continue
    except (OSError, ValueError):
        pass
    finally:
        if not stopped.is_set():
            try:
                events.put((f"{label}_eof", ""), timeout=0.05)
            except queue.Full:
                pass


def _exchange_worker_once(
    payload: dict[str, Any],
    *,
    workdir: Path | None,
    idle_timeout_seconds: float,
    wall_timeout_seconds: float,
    worker_argv: list[str] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    resource_limits: ProcessResourceLimits | None = None,
    termination_policy: TerminationPolicy | None = None,
    spawn_intent: Callable[[str], bool] | None = None,
    spawn_failed: Callable[[str, str], bool] | None = None,
    process_started: Callable[[ProcessIdentity], bool] | None = None,
    provider_dispatch: Callable[[str], bool] | None = None,
    provider_start_delivered: Callable[[str], bool] | None = None,
    provider_execute_received: Callable[[str], bool] | None = None,
    provider_execute_release: Callable[[str], bool] | None = None,
    process_stopped: Callable[[ProcessIdentity, bool], None] | None = None,
) -> dict[str, Any]:
    executor_nonce = secrets.token_hex(16)
    handshake = payload.get("provider_start_handshake")
    handshake_required = (
        isinstance(handshake, Mapping) and handshake.get("required") is True
    )
    if handshake_required:
        payload = {
            **payload,
            "provider_start_handshake": {
                "required": True,
                "executor_nonce": executor_nonce,
            },
        }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("plugin-agent request frame is too large")

    package_root = Path(__file__).resolve().parent.parent
    default_worker_argv = [
        sys.executable,
        "-c",
        (
            "import runpy,sys;sys.path.insert(0,sys.argv[1]);"
            "runpy.run_module('agent.plugin_agent_worker',run_name='__main__')"
        ),
        str(package_root),
    ]
    if spawn_intent is not None and not spawn_intent(executor_nonce):
        raise _PluginAgentCancelled("plugin-agent spawn intent was rejected")
    try:
        tree = ManagedProcessTree.spawn(
            worker_argv or default_worker_argv,
            policy=termination_policy
            or TerminationPolicy(
                cooperative_grace_seconds=5.0,
                term_grace_seconds=5.0,
                kill_grace_seconds=2.0,
                wait_timeout_seconds=2.0,
            ),
            cwd=str(workdir) if workdir else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except BaseException as exc:
        if spawn_failed is not None:
            try:
                spawn_failed(executor_nonce, type(exc).__name__)
            except BaseException:
                pass
        raise
    process_registered = False
    if process_started is not None:
        try:
            process_registered = bool(process_started(tree.identity))
        except BaseException:
            tree.close()
            raise
        if not process_registered:
            tree.close()
            raise _PluginAgentCancelled(
                "plugin-agent process registration was rejected"
            )
    assert tree.process.stdin is not None
    assert tree.process.stdout is not None
    assert tree.process.stderr is not None
    events: queue.Queue = queue.Queue(maxsize=_MAX_QUEUED_FRAMES)
    stopped = threading.Event()
    stdout_reader = threading.Thread(
        target=_read_stream,
        args=(tree.process.stdout, events, "stdout"),
        kwargs={"stopped": stopped},
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=_read_stream,
        args=(tree.process.stderr, events, "stderr"),
        kwargs={"stopped": stopped},
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()
    tree.process.stdin.write(encoded + "\n")
    tree.process.stdin.flush()

    started = last_activity = time.monotonic()
    stderr_tail = ""
    provider_authorized = False
    provider_delivered = False
    execute_received_recorded = False
    provider_released = False
    limits = resource_limits or ProcessResourceLimits()
    try:
        while True:
            now = time.monotonic()
            if (
                not provider_released
                and is_cancelled is not None
                and is_cancelled()
            ):
                raise _PluginAgentCancelled("plugin-agent run cancelled")
            violation = tree.resource_violation(limits)
            if violation is not None:
                raise _PluginAgentResourceExceeded(violation)
            if now - started >= wall_timeout_seconds:
                raise TimeoutError("plugin-agent wall timeout")
            if now - last_activity >= idle_timeout_seconds:
                raise TimeoutError("plugin-agent idle timeout")
            wait_for = min(
                0.2,
                wall_timeout_seconds - (now - started),
                idle_timeout_seconds - (now - last_activity),
            )
            try:
                label, line = events.get(timeout=max(wait_for, 0.01))
            except queue.Empty:
                if tree.process.poll() is not None and not stdout_reader.is_alive():
                    # stderr may contain provider diagnostics with credential
                    # fragments. Keep it bounded for host diagnostics, but do
                    # not surface it through the plugin-facing exception.
                    raise RuntimeError("plugin-agent worker exited without a result")
                continue
            if label == "stderr":
                stderr_tail = (stderr_tail + line)[-_MAX_FRAME_BYTES:]
                continue
            if label != "stdout":
                continue
            if len(line.encode("utf-8")) > _MAX_FRAME_BYTES:
                raise RuntimeError("plugin-agent response frame is too large")
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("plugin-agent emitted invalid JSON") from exc
            if frame.get("protocol_version") != _PROTOCOL_VERSION:
                raise RuntimeError("plugin-agent protocol version mismatch")
            if frame.get("type") == "provider_ready":
                if (
                    not handshake_required
                    or provider_authorized
                    or frame.get("executor_nonce") != executor_nonce
                ):
                    raise RuntimeError(
                        "plugin-agent provider handshake is invalid"
                    )
                if is_cancelled is not None and is_cancelled():
                    raise _PluginAgentCancelled("plugin-agent run cancelled")
                if provider_dispatch is not None and not provider_dispatch(
                    executor_nonce
                ):
                    raise _PluginAgentCancelled(
                        "plugin-agent provider dispatch was rejected"
                    )
                control = json.dumps(
                    {
                        "protocol_version": _PROTOCOL_VERSION,
                        "type": "provider_start",
                        "executor_nonce": executor_nonce,
                    },
                    separators=(",", ":"),
                )
                tree.process.stdin.write(control + "\n")
                tree.process.stdin.flush()
                provider_authorized = True
                last_activity = time.monotonic()
                continue
            if frame.get("type") == "provider_start_received":
                if (
                    not handshake_required
                    or not provider_authorized
                    or provider_delivered
                    or frame.get("executor_nonce") != executor_nonce
                ):
                    raise RuntimeError(
                        "plugin-agent provider delivery handshake is invalid"
                    )
                if is_cancelled is not None and is_cancelled():
                    raise _PluginAgentCancelled("plugin-agent run cancelled")
                if (
                    provider_start_delivered is not None
                    and not provider_start_delivered(executor_nonce)
                ):
                    raise _PluginAgentCancelled(
                        "plugin-agent provider delivery was rejected"
                    )
                execute = json.dumps(
                    {
                        "protocol_version": _PROTOCOL_VERSION,
                        "type": "provider_execute",
                        "executor_nonce": executor_nonce,
                    },
                    separators=(",", ":"),
                )
                tree.process.stdin.write(execute + "\n")
                tree.process.stdin.flush()
                provider_delivered = True
                last_activity = time.monotonic()
                continue
            if frame.get("type") == "provider_execute_received":
                if (
                    not handshake_required
                    or not provider_delivered
                    or execute_received_recorded
                    or provider_released
                    or frame.get("executor_nonce") != executor_nonce
                ):
                    raise RuntimeError(
                        "plugin-agent provider execute handshake is invalid"
                    )
                if is_cancelled is not None and is_cancelled():
                    raise _PluginAgentCancelled("plugin-agent run cancelled")
                if (
                    provider_execute_received is not None
                    and not provider_execute_received(executor_nonce)
                ):
                    raise _PluginAgentCancelled(
                        "plugin-agent provider execute receipt was rejected"
                    )
                execute_received_recorded = True
                if is_cancelled is not None and is_cancelled():
                    raise _PluginAgentCancelled("plugin-agent run cancelled")
                if (
                    provider_execute_release is not None
                    and not provider_execute_release(executor_nonce)
                ):
                    raise _PluginAgentCancelled(
                        "plugin-agent provider execute release was rejected"
                    )
                release = json.dumps(
                    {
                        "protocol_version": _PROTOCOL_VERSION,
                        "type": "provider_execute_release",
                        "executor_nonce": executor_nonce,
                    },
                    separators=(",", ":"),
                )
                tree.process.stdin.write(release + "\n")
                tree.process.stdin.flush()
                provider_released = True
                last_activity = time.monotonic()
                continue
            if frame.get("type") == "result":
                if (
                    handshake_required
                    and provider_authorized
                    and not provider_released
                ):
                    raise RuntimeError(
                        "plugin-agent provider handshake ended before release"
                    )
                return frame
            if frame.get("type") not in {"progress", "interaction"}:
                raise RuntimeError("plugin-agent emitted an unknown frame type")
            last_activity = time.monotonic()
    finally:
        stopped.set()
        try:
            tree.process.stdin.close()
        except Exception:
            pass
        cleaned = False
        try:
            tree.close()
            cleaned = tree.reaped
        finally:
            if process_registered and process_stopped is not None:
                try:
                    process_stopped(tree.identity, cleaned)
                except BaseException:
                    pass
        stdout_reader.join(timeout=1.0)
        stderr_reader.join(timeout=1.0)


def _exchange_worker(
    payload: dict[str, Any],
    *,
    workdir: Path | None,
    idle_timeout_seconds: float,
    wall_timeout_seconds: float,
    worker_argv: list[str] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    resource_limits: ProcessResourceLimits | None = None,
    termination_policy: TerminationPolicy | None = None,
    spawn_intent: Callable[[str], bool] | None = None,
    spawn_failed: Callable[[str, str], bool] | None = None,
    process_started: Callable[[ProcessIdentity], bool] | None = None,
    provider_dispatch: Callable[[str], bool] | None = None,
    provider_start_delivered: Callable[[str], bool] | None = None,
    provider_execute_received: Callable[[str], bool] | None = None,
    provider_execute_release: Callable[[str], bool] | None = None,
    process_stopped: Callable[[ProcessIdentity, bool], None] | None = None,
) -> dict[str, Any]:
    """Exchange one frame while owning any top-level sealed grant broker."""

    owned_authority: _ProviderAttemptAuthority | None = None
    request = payload.get("request")
    if (
        isinstance(request, dict)
        and request.get("sealed_provider_attempt_grant") is True
        and bool(
            request.get("inline_agents") or request.get("sealed_fallback_route")
        )
        and request.get("_provider_attempt_authority") is None
    ):
        grant = request.get("max_api_attempts")
        owned_authority = _ProviderAttemptAuthority(grant)
        payload = {
            **payload,
            "request": {
                **request,
                "_provider_attempt_authority": dict(owned_authority.descriptor),
            },
        }
    try:
        return _exchange_worker_once(
            payload,
            workdir=workdir,
            idle_timeout_seconds=idle_timeout_seconds,
            wall_timeout_seconds=wall_timeout_seconds,
            worker_argv=worker_argv,
            is_cancelled=is_cancelled,
            resource_limits=resource_limits,
            termination_policy=termination_policy,
            spawn_intent=spawn_intent,
            spawn_failed=spawn_failed,
            process_started=process_started,
            provider_dispatch=provider_dispatch,
            provider_start_delivered=provider_start_delivered,
            provider_execute_received=provider_execute_received,
            provider_execute_release=provider_execute_release,
            process_stopped=process_stopped,
        )
    finally:
        if owned_authority is not None:
            owned_authority.close()


class PluginAgentRunner:
    """Plugin-bound facade that never exposes a live agent or credentials."""

    starts_request_mcp = True

    def __init__(self, plugin_id: str) -> None:
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("plugin_id must be non-empty")
        self.plugin_id = plugin_id.strip()

    def run(
        self,
        request: PluginAgentRunRequest,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        spawn_intent: Callable[[str], bool] | None = None,
        spawn_failed: Callable[[str, str], bool] | None = None,
        process_started: Callable[[ProcessIdentity], bool] | None = None,
        provider_dispatch: Callable[[str], bool] | None = None,
        provider_start_delivered: Callable[[str], bool] | None = None,
        provider_execute_received: Callable[[str], bool] | None = None,
        provider_execute_release: Callable[[str], bool] | None = None,
        process_stopped: Callable[[ProcessIdentity, bool], None] | None = None,
    ) -> PluginAgentRunResult:
        _validate_request(request)
        if not _agent_override_allowed(self.plugin_id, "provider", request.provider):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override provider")
        if not _agent_override_allowed(self.plugin_id, "model", request.model):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override model")
        if request.context_mode == "shared":
            from hermes_state import SessionDB

            try:
                session_db = SessionDB()
                try:
                    session = session_db.get_session(request.session_id)
                finally:
                    session_db.close()
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                raise PluginAgentSessionUnavailableError(
                    "persistent plugin-agent session could not be read"
                ) from exc
            if session is None:
                raise PluginAgentSessionMissingError(
                    "persistent plugin-agent session is missing"
                )

        now = time.monotonic()
        remaining_wall = request.wall_timeout_seconds
        remaining_idle = request.idle_timeout_seconds
        if request.absolute_wall_deadline is not None:
            remaining_wall = min(
                remaining_wall,
                request.absolute_wall_deadline - now,
            )
        if request.absolute_idle_deadline is not None:
            remaining_idle = min(
                remaining_idle,
                request.absolute_idle_deadline - now,
            )
        if remaining_wall <= 0:
            raise TimeoutError("plugin-agent absolute wall deadline expired")
        if remaining_idle <= 0:
            raise TimeoutError("plugin-agent absolute idle deadline expired")
        if (
            request.absolute_provider_deadline is not None
            and now >= request.absolute_provider_deadline
        ):
            raise TimeoutError("plugin-agent absolute provider deadline expired")

        payload = _request_payload(self.plugin_id, request)
        try:
            frame = _exchange_worker(
                payload,
                workdir=Path(request.workdir).expanduser().resolve()
                if request.workdir
                else None,
                idle_timeout_seconds=min(remaining_idle, remaining_wall),
                wall_timeout_seconds=remaining_wall,
                is_cancelled=is_cancelled,
                resource_limits=ProcessResourceLimits(
                    max_rss_bytes=request.max_process_tree_rss_bytes,
                    max_cpu_seconds=request.max_process_tree_cpu_seconds,
                    max_descendants=request.max_descendants,
                ),
                termination_policy=TerminationPolicy(
                    cooperative_grace_seconds=request.cooperative_shutdown_seconds,
                    term_grace_seconds=request.term_grace_seconds,
                    kill_grace_seconds=request.kill_reap_grace_seconds,
                    wait_timeout_seconds=request.kill_reap_grace_seconds,
                ),
                spawn_intent=spawn_intent,
                spawn_failed=spawn_failed,
                process_started=process_started,
                provider_dispatch=provider_dispatch,
                provider_start_delivered=provider_start_delivered,
                provider_execute_received=provider_execute_received,
                provider_execute_release=provider_execute_release,
                process_stopped=process_stopped,
            )
            result = frame.get("result")
            try:
                if not isinstance(result, dict):
                    raise ValueError("plugin-agent result payload is missing")
                parsed_result = PluginAgentRunResult.from_wire(result)
                if not _correlate_persistent_session_result(
                    self.plugin_id, request, parsed_result
                ):
                    _correlate_structured_result(request, parsed_result)
            except (TypeError, ValueError, RuntimeError) as exc:
                raise PluginAgentResultProtocolError(str(exc)) from exc
            return parsed_result
        except TimeoutError as exc:
            kind = "idle_timeout" if "idle" in str(exc) else "wall_timeout"
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"plugin_id": self.plugin_id, "failure_kind": kind},
            )
        except _PluginAgentCancelled:
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={"plugin_id": self.plugin_id, "failure_kind": "cancelled"},
            )
        except _PluginAgentResourceExceeded as exc:
            return PluginAgentRunResult(
                final_response="",
                session_id=request.session_id or "",
                provider=request.provider or "",
                model=request.model or "",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    "plugin_id": self.plugin_id,
                    "failure_kind": "resource_limit",
                    "resource_code": str(exc),
                },
            )


__all__ = [
    "PluginAgentResultProtocolError",
    "PluginAgentRunRequest",
    "PluginAgentRunResult",
    "PluginAgentRunner",
    "PluginAgentSessionMissingError",
    "PluginAgentSessionUnavailableError",
]
