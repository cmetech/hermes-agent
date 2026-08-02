"""Isolated, host-owned Hermes agents for trusted plugins.

The public facade serializes policy rather than credentials. Every run starts a
fresh Python worker, so tool registry/cache/callback/environment mutations are
contained and cannot alter a long-lived parent conversation.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import json
import math
from multiprocessing.connection import AuthenticationError, Client, Listener
from pathlib import Path
import queue
import re
import secrets
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
    ProcessResourceLimits,
    TerminationPolicy,
)


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


class _PluginAgentCancelled(RuntimeError):
    """Internal control flow for caller-requested worker cancellation."""


class _PluginAgentResourceExceeded(RuntimeError):
    """Internal control flow for bounded worker-tree enforcement."""


class _ProviderAttemptGrantExhausted(RuntimeError):
    """One shared sealed request tree has spent its provider-call authority."""

    failure_kind = "provider_attempt_grant_exhausted"
    status_code = 400


_PROVIDER_AUTHORITY_VERSION = 1
_PROVIDER_AUTHORITY_AUTHKEY_BYTES = 32
_PROVIDER_AUTHORITY_FRAME_BYTES = 1024


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
    try:
        connection = Client(address, family="AF_INET", authkey=authkey)
        try:
            connection.send_bytes(
                json.dumps(
                    {"operation": operation}, separators=(",", ":")
                ).encode("ascii")
            )
            response = json.loads(
                connection.recv_bytes(_PROVIDER_AUTHORITY_FRAME_BYTES)
            )
        finally:
            connection.close()
    except (
        AuthenticationError,
        EOFError,
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError,
    ) as exc:
        raise RuntimeError("sealed provider attempt authority unavailable") from exc
    if not isinstance(response, Mapping):
        raise RuntimeError("sealed provider attempt authority response is invalid")
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
        self._shutdown_nonce = secrets.token_hex(32)
        self._listener = Listener(
            ("127.0.0.1", 0), family="AF_INET", authkey=self._authkey
        )
        host, port = self._listener.address
        self.descriptor: Mapping[str, Any] = {
            "version": _PROVIDER_AUTHORITY_VERSION,
            "host": host,
            "port": port,
            "authkey": base64.b64encode(self._authkey).decode("ascii"),
        }
        self._closed = False
        self._thread = threading.Thread(
            target=self._serve,
            name="provider-attempt-authority",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                connection = self._listener.accept()
            except (AuthenticationError, OSError):
                if self._closed:
                    return
                continue
            try:
                request = json.loads(
                    connection.recv_bytes(_PROVIDER_AUTHORITY_FRAME_BYTES)
                )
                operation = request.get("operation") if isinstance(request, Mapping) else None
                if operation == "reserve":
                    with self._state_lock:
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
                elif operation == "snapshot":
                    response = self.snapshot()
                elif (
                    operation == "shutdown"
                    and request.get("nonce") == self._shutdown_nonce
                ):
                    connection.send_bytes(b'{"closed":true}')
                    return
                else:
                    response = {"error": "invalid operation"}
                connection.send_bytes(
                    json.dumps(response, separators=(",", ":")).encode("ascii")
                )
            except (EOFError, json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
            finally:
                connection.close()

    def snapshot(self) -> dict[str, int | bool]:
        with self._state_lock:
            return {
                "provider_attempts": self._provider_attempts,
                "exhausted": self._exhausted,
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            connection = Client(
                self._listener.address, family="AF_INET", authkey=self._authkey
            )
            try:
                connection.send_bytes(
                    json.dumps(
                        {"operation": "shutdown", "nonce": self._shutdown_nonce},
                        separators=(",", ":"),
                    ).encode("ascii")
                )
                connection.recv_bytes(_PROVIDER_AUTHORITY_FRAME_BYTES)
            finally:
                connection.close()
        except (AuthenticationError, EOFError, OSError):
            pass
        self._listener.close()
        self._thread.join(timeout=2.0)


@dataclass(frozen=True)
class PluginAgentRunRequest:
    prompt: str
    provider: str | None = None
    model: str | None = None
    context_mode: Literal["fresh", "shared"] = "fresh"
    session_id: str | None = None
    enabled_toolsets: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    hooks: tuple[Mapping[str, Any], ...] = ()
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None
    inline_agents: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    reasoning_config: Mapping[str, Any] | None = None
    fallback_model: str | None = None
    ephemeral_system_prompt: str | None = None
    request_overrides: Mapping[str, Any] = field(default_factory=dict)
    structured_output: StructuredOutputRequest | None = None
    max_budget_usd: float | None = None
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
            "enabled_toolsets": _wire_json(self.enabled_toolsets),
            "allowed_tools": _wire_json(self.allowed_tools),
            "denied_tools": _wire_json(self.denied_tools),
            "skills": _wire_json(self.skills),
            "hooks": _wire_json(self.hooks),
            "mcp_servers": _wire_json(self.mcp_servers),
            "inline_agents": _wire_json(self.inline_agents),
            "reasoning_config": _wire_json(self.reasoning_config),
            "fallback_model": self.fallback_model,
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
            "enabled_toolsets",
            "allowed_tools",
            "denied_tools",
            "skills",
            "hooks",
            "mcp_servers",
            "inline_agents",
            "reasoning_config",
            "fallback_model",
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
            "idle_timeout_seconds",
            "wall_timeout_seconds",
            "provider_request_timeout_seconds",
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
) -> dict[str, Any]:
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
    limits = resource_limits or ProcessResourceLimits()
    try:
        while True:
            now = time.monotonic()
            if is_cancelled is not None and is_cancelled():
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
            if frame.get("type") == "result":
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
        tree.close()
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
) -> dict[str, Any]:
    """Exchange one frame while owning any top-level sealed grant broker."""

    owned_authority: _ProviderAttemptAuthority | None = None
    request = payload.get("request")
    if (
        isinstance(request, dict)
        and request.get("sealed_provider_attempt_grant") is True
        and bool(request.get("inline_agents"))
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
    ) -> PluginAgentRunResult:
        _validate_request(request)
        if not _agent_override_allowed(self.plugin_id, "provider", request.provider):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override provider")
        if not _agent_override_allowed(self.plugin_id, "model", request.model):
            raise PermissionError(f"plugin {self.plugin_id!r} cannot override model")
        if request.context_mode == "shared":
            from hermes_state import SessionDB

            session_db = SessionDB()
            try:
                if session_db.get_session(request.session_id) is None:
                    raise ValueError(
                        "session_id does not identify an existing Hermes session"
                    )
            finally:
                session_db.close()

        payload = _request_payload(self.plugin_id, request)
        try:
            frame = _exchange_worker(
                payload,
                workdir=Path(request.workdir).expanduser().resolve()
                if request.workdir
                else None,
                idle_timeout_seconds=request.idle_timeout_seconds,
                wall_timeout_seconds=request.wall_timeout_seconds,
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
            )
            result = frame.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("plugin-agent result payload is missing")
            parsed_result = PluginAgentRunResult.from_wire(result)
            _correlate_structured_result(request, parsed_result)
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


__all__ = ["PluginAgentRunRequest", "PluginAgentRunResult", "PluginAgentRunner"]
