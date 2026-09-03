"""Consumer-neutral handoff value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import math
import re
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

from agent.structured_output import canonical_json_bytes, normalize_schema
from hermes_cli.profiles import validate_profile_name


HANDOFF_PHASES = frozenset({
    "prepared", "submitted", "active", "needs_input", "cancelling",
    "indeterminate", "succeeded", "failed", "cancelled",
})
_SUPPORTED_CAPABILITIES = frozenset({
    "approval",
    "cancellation",
    "follow_up",
    "steering",
    "structured_output",
})
_CHANNEL_CAPABILITIES = frozenset({
    "approval",
    "authoritative_status",
    "cancellation",
    "durable_admission",
    "follow_up",
    "steering",
})
_REQUIRED_PEER_CAPABILITIES = frozenset({
    "authoritative_status",
    "durable_admission",
})
_APPROVAL_CHOICES = ("once", "session", "always", "deny")
_MAX_PROMPT_BYTES = 500_000
_MAX_ATTRIBUTION_ITEMS = 64
_MAX_ATTRIBUTION_BYTES = 16_384
_CREDENTIAL_KEY_PARTS = (
    "api_key", "authorization", "bearer", "credential", "password", "secret", "token",
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_SAFE_SESSION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,511}$")
_PEER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELIVERY_POLICIES = frozenset({"attention", "wake"})
_CHECKPOINT_IDENTIFIERS = frozenset({
    "approval_request_id",
    "session_id",
    "run_id",
    "idempotency_key",
    "status",
})
_CHECKPOINT_SHA256 = frozenset({
    "request_sha256", "process_command_sha256", "receipt_sha256", "output_sha256",
    "stdout_sha256", "stderr_sha256",
})
_CHECKPOINT_INTEGERS = frozenset({"process_pid", "receipt_version", "exit_code", "cursor", "version"})
_CHECKPOINT_KEYS = (
    _CHECKPOINT_IDENTIFIERS
    | _CHECKPOINT_SHA256
    | _CHECKPOINT_INTEGERS
    | {"approval_choices", "process_started_at"}
)
_MAX_FACT_INTEGER = 2**63 - 1


def _contains_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _safe_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_IDENTIFIER.fullmatch(value))


def _normalize_return_route(
    value: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("handoff return route is invalid")
    kind = value.get("kind")
    if kind == "bot":
        required = {
            "kind",
            "host_kind",
            "profile",
            "session_id",
            "tool_call_id",
            "delivery_policy",
            "hop_count",
        }
        keys = set(value)
        if keys not in (required, required | {"session_key"}):
            raise ValueError("handoff return route is invalid")
        if value.get("host_kind") not in {"gateway", "web"}:
            raise ValueError("handoff return route is invalid")
        session_key = value.get("session_key")
        if session_key is not None and (
            not isinstance(session_key, str)
            or not _SAFE_SESSION_KEY.fullmatch(session_key)
        ):
            raise ValueError("handoff return route is invalid")
        if not _safe_identifier(value.get("session_id")) or not _safe_identifier(
            value.get("tool_call_id")
        ):
            raise ValueError("handoff return route is invalid")
        if value.get("delivery_policy") not in _DELIVERY_POLICIES:
            raise ValueError("handoff return route is invalid")
        hop_count = value.get("hop_count")
        if (
            isinstance(hop_count, bool)
            or not isinstance(hop_count, int)
            or not 0 <= hop_count <= 1
        ):
            raise ValueError("handoff return route is invalid")
    elif kind == "operator":
        if set(value) != {"kind", "profile", "inbox_id"} or not _safe_identifier(
            value.get("inbox_id")
        ):
            raise ValueError("handoff return route is invalid")
    else:
        raise ValueError("handoff return route is invalid")
    profile = value.get("profile")
    if not isinstance(profile, str):
        raise ValueError("handoff return route is invalid")
    try:
        validate_profile_name(profile)
    except ValueError as exc:
        raise ValueError("handoff return route is invalid") from exc
    return _freeze(value)  # type: ignore[return-value]


def _normalize_binding(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("handoff binding is invalid")
    if not value:
        return MappingProxyType({})
    keys = set(value)
    local_keys = {"profile", "mechanism"}
    local_runs_keys = local_keys | {"capabilities"}
    peer_keys = {
        "auth_scope_sha256",
        "capabilities",
        "mechanism",
        "origin_sha256",
        "peer",
        "profile",
    }
    if keys not in (local_keys, local_runs_keys, peer_keys):
        raise ValueError("handoff binding is invalid")
    profile, mechanism = value["profile"], value["mechanism"]
    if not isinstance(profile, str) or not _safe_identifier(mechanism):
        raise ValueError("handoff binding is invalid")
    try:
        validate_profile_name(profile)
    except ValueError as exc:
        raise ValueError("handoff binding is invalid") from exc
    if keys == local_keys:
        return MappingProxyType({"profile": profile, "mechanism": mechanism})

    capabilities = value["capabilities"]
    if not isinstance(capabilities, list | tuple | set | frozenset) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ValueError("handoff binding is invalid")
    capability_set = frozenset(capabilities)
    if keys == local_runs_keys:
        if (
            mechanism != "runs"
            or not capability_set <= _CHANNEL_CAPABILITIES
            or not _REQUIRED_PEER_CAPABILITIES <= capability_set
        ):
            raise ValueError("handoff binding is invalid")
        return _freeze({
            "profile": profile,
            "mechanism": mechanism,
            "capabilities": sorted(capability_set),
        })  # type: ignore[return-value]

    peer = value["peer"]
    origin_sha256 = value["origin_sha256"]
    auth_scope_sha256 = value["auth_scope_sha256"]
    if (
        not isinstance(peer, str)
        or not _PEER_NAME.fullmatch(peer)
        or mechanism not in {"peer_dm", "peer_runs"}
        or not capability_set <= _CHANNEL_CAPABILITIES
        or (mechanism == "peer_dm" and bool(capability_set))
        or (
            mechanism == "peer_runs"
            and not _REQUIRED_PEER_CAPABILITIES <= capability_set
        )
        or not isinstance(origin_sha256, str)
        or not _SHA256.fullmatch(origin_sha256)
        or not isinstance(auth_scope_sha256, str)
        or not _SHA256.fullmatch(auth_scope_sha256)
    ):
        raise ValueError("handoff binding is invalid")
    return _freeze({
        "peer": peer,
        "profile": profile,
        "mechanism": mechanism,
        "capabilities": sorted(capability_set),
        "origin_sha256": origin_sha256,
        "auth_scope_sha256": auth_scope_sha256,
    })  # type: ignore[return-value]


def _normalize_checkpoint(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or not set(value) <= _CHECKPOINT_KEYS:
        raise ValueError("handoff checkpoint is invalid")
    normalized = dict(value)
    for key, item in value.items():
        if key in _CHECKPOINT_IDENTIFIERS and not _safe_identifier(item):
            raise ValueError("handoff checkpoint is invalid")
        if key in _CHECKPOINT_SHA256 and (not isinstance(item, str) or not _SHA256.fullmatch(item)):
            raise ValueError("handoff checkpoint is invalid")
        if key in _CHECKPOINT_INTEGERS and (
            isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= _MAX_FACT_INTEGER
        ):
            raise ValueError("handoff checkpoint is invalid")
        if key == "process_started_at" and (
            isinstance(item, bool) or not isinstance(item, int | float) or not math.isfinite(item) or item < 0
        ):
            raise ValueError("handoff checkpoint is invalid")
    has_approval_id = "approval_request_id" in value
    has_approval_choices = "approval_choices" in value
    if has_approval_id != has_approval_choices:
        raise ValueError("handoff checkpoint is invalid")
    if has_approval_choices:
        choices = value["approval_choices"]
        if (
            not isinstance(choices, list | tuple | set | frozenset)
            or not choices
            or not all(isinstance(item, str) for item in choices)
            or not frozenset(choices) <= frozenset(_APPROVAL_CHOICES)
        ):
            raise ValueError("handoff checkpoint is invalid")
        normalized["approval_choices"] = [
            choice for choice in _APPROVAL_CHOICES if choice in choices
        ]
    return _freeze(normalized)


def _normalize_terminal_result(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"text", "sha256", "media_type", "size_bytes"}:
        raise ValueError("handoff terminal result is invalid")
    text, digest, media_type, size_bytes = (
        value["text"], value["sha256"], value["media_type"], value["size_bytes"],
    )
    if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_PROMPT_BYTES:
        raise ValueError("handoff terminal result is invalid")
    encoded = text.encode("utf-8")
    if (
        not isinstance(digest, str)
        or digest != sha256(encoded).hexdigest()
        or media_type not in {"text/plain", "application/json"}
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes != len(encoded)
    ):
        raise ValueError("handoff terminal result is invalid")
    return _freeze(value)


def _aware_utc(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class HandoffEndpoint:
    canonical: str
    profile: str
    peer: str | None = None

    @property
    def kind(self) -> Literal["local", "peer"]:
        return "peer" if self.peer is not None else "local"

    @classmethod
    def parse(cls, value: str) -> "HandoffEndpoint":
        if not isinstance(value, str) or _contains_control(value) or "%" in value:
            raise ValueError("handoff endpoint must be canonical")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("handoff endpoint must be canonical") from exc
        if parsed.scheme != "hermes" or "?" in value or "#" in value:
            raise ValueError("handoff endpoint must be canonical")
        parts = parsed.path.split("/")
        peer = None
        if parsed.netloc == "local" and len(parts) == 2:
            profile = parts[1]
            canonical = f"hermes://local/{profile}"
        elif parsed.netloc == "peer" and len(parts) == 3:
            peer, profile = parts[1:]
            if not _PEER_NAME.fullmatch(peer):
                raise ValueError("handoff endpoint peer is invalid")
            canonical = f"hermes://peer/{peer}/{profile}"
        else:
            raise ValueError("handoff endpoint must be canonical")
        try:
            validate_profile_name(profile)
        except ValueError as exc:
            raise ValueError("handoff endpoint profile is invalid") from exc
        if value != canonical:
            raise ValueError("handoff endpoint must be canonical")
        return cls(canonical=canonical, profile=profile, peer=peer)


@dataclass(frozen=True, slots=True)
class HandoffSpec:
    mode: Literal["task", "conversation"]
    endpoint: HandoffEndpoint
    prompt: str
    output_schema: Mapping[str, object] | None
    deadline_at: datetime | None
    attribution: Mapping[str, str]
    required_capabilities: frozenset[str]
    return_route: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"task", "conversation"}:
            raise ValueError("handoff mode is invalid")
        if not isinstance(self.endpoint, HandoffEndpoint):
            raise ValueError("handoff endpoint is invalid")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("handoff prompt must not be blank")
        if len(self.prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            raise ValueError("handoff prompt exceeds byte limit")
        schema = None
        if self.output_schema is not None:
            if self.mode == "conversation":
                raise ValueError(
                    "conversation handoff cannot require structured output"
                )
            schema = normalize_schema(self.output_schema).canonical_schema
        if not isinstance(self.attribution, Mapping) or len(self.attribution) > _MAX_ATTRIBUTION_ITEMS:
            raise ValueError("handoff attribution is invalid")
        attribution: dict[str, str] = {}
        for key, value in self.attribution.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                or _contains_control(key)
                or _contains_control(value)
                or any(part in key.lower() for part in _CREDENTIAL_KEY_PARTS)
                or value.lower().startswith(("bearer", "basic"))
            ):
                raise ValueError("handoff attribution is unsafe")
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                raise ValueError("handoff attribution must not contain URLs")
            attribution[key] = value
        try:
            canonical_json_bytes(attribution, max_bytes=_MAX_ATTRIBUTION_BYTES)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("handoff attribution exceeds byte limit") from exc
        capabilities = frozenset(self.required_capabilities)
        if not capabilities <= _SUPPORTED_CAPABILITIES:
            raise ValueError("handoff requires unsupported capabilities")
        return_route = _normalize_return_route(self.return_route)
        if self.mode == "task" and return_route is not None:
            raise ValueError("task handoff cannot have a return route")
        object.__setattr__(self, "output_schema", schema)
        object.__setattr__(self, "deadline_at", _aware_utc(self.deadline_at, "deadline"))
        object.__setattr__(self, "attribution", _freeze(attribution))
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(self, "return_route", return_route)

    @property
    def fingerprint_input(self) -> bytes:
        payload = {
            "mode": self.mode,
            "endpoint": self.endpoint.canonical,
            "prompt": self.prompt,
            "output_schema": self.output_schema,
            "deadline_at": _timestamp(self.deadline_at),
            "attribution": self.attribution,
            "required_capabilities": sorted(self.required_capabilities),
        }
        if self.return_route is not None:
            payload["return_route"] = self.return_route
        return canonical_json_bytes(payload, max_bytes=3_200_000)

    @property
    def fingerprint(self) -> str:
        return sha256(self.fingerprint_input).hexdigest()


@dataclass(frozen=True, slots=True)
class HandoffSnapshot:
    handoff_id: str
    key_scope: str
    handoff_key: str
    spec: HandoffSpec
    spec_fingerprint: str
    phase: str
    state_version: int
    mechanism: str | None = None
    binding: Mapping[str, object] | None = None
    checkpoint: Mapping[str, object] | None = None
    next_advance_at: datetime | None = None
    submit_attempted_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    terminal_result: Mapping[str, object] | None = None
    failure_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.phase not in HANDOFF_PHASES:
            raise ValueError("handoff phase is invalid")
        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int) or self.state_version < 0:
            raise ValueError("handoff state version is invalid")
        for name in (
            "next_advance_at", "submit_attempted_at", "cancel_requested_at",
            "created_at", "updated_at",
        ):
            object.__setattr__(self, name, _aware_utc(getattr(self, name), name))
        for name in ("binding", "checkpoint", "terminal_result"):
            object.__setattr__(self, name, {
                "binding": _normalize_binding,
                "checkpoint": _normalize_checkpoint,
                "terminal_result": _normalize_terminal_result,
            }[name](getattr(self, name)))
        if self.mechanism is not None and not _safe_identifier(self.mechanism):
            raise ValueError("handoff mechanism is invalid")
        if self.failure_code is not None and not _safe_identifier(self.failure_code):
            raise ValueError("handoff failure code is invalid")


@dataclass(frozen=True, slots=True)
class ChannelObservation:
    phase: str
    checkpoint: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    binding: Mapping[str, object] | None = None
    mechanism: str | None = None
    terminal_result: Mapping[str, object] | None = None
    failure_code: str | None = None
    next_advance_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.phase not in HANDOFF_PHASES:
            raise ValueError("observation phase is invalid")
        for name, normalizer in (
            ("checkpoint", _normalize_checkpoint),
            ("binding", _normalize_binding),
            ("terminal_result", _normalize_terminal_result),
        ):
            object.__setattr__(self, name, normalizer(getattr(self, name)))
        if self.mechanism is not None and not _safe_identifier(self.mechanism):
            raise ValueError("handoff mechanism is invalid")
        if self.failure_code is not None and not _safe_identifier(self.failure_code):
            raise ValueError("handoff failure code is invalid")
        object.__setattr__(self, "next_advance_at", _aware_utc(self.next_advance_at, "next advance"))
