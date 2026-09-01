"""Consumer-neutral local handoff value objects."""

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
_SUPPORTED_CAPABILITIES = frozenset({"structured_output", "cancellation"})
_MAX_PROMPT_BYTES = 500_000
_MAX_ATTRIBUTION_ITEMS = 64
_MAX_ATTRIBUTION_BYTES = 16_384
_CREDENTIAL_KEY_PARTS = (
    "api_key", "authorization", "bearer", "credential", "password", "secret", "token",
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_IDENTIFIERS = frozenset({"session_id", "run_id", "idempotency_key", "status"})
_CHECKPOINT_SHA256 = frozenset({
    "request_sha256", "process_command_sha256", "receipt_sha256", "output_sha256",
    "stdout_sha256", "stderr_sha256",
})
_CHECKPOINT_INTEGERS = frozenset({"process_pid", "receipt_version", "exit_code", "cursor", "version"})
_CHECKPOINT_KEYS = _CHECKPOINT_IDENTIFIERS | _CHECKPOINT_SHA256 | _CHECKPOINT_INTEGERS | {"process_started_at"}
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


def _normalize_binding(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) not in (set(), {"profile", "mechanism"}):
        raise ValueError("handoff binding is invalid")
    if not value:
        return MappingProxyType({})
    profile, mechanism = value["profile"], value["mechanism"]
    if not isinstance(profile, str) or not _safe_identifier(mechanism):
        raise ValueError("handoff binding is invalid")
    try:
        validate_profile_name(profile)
    except ValueError as exc:
        raise ValueError("handoff binding is invalid") from exc
    return MappingProxyType({"profile": profile, "mechanism": mechanism})


def _normalize_checkpoint(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or not set(value) <= _CHECKPOINT_KEYS:
        raise ValueError("handoff checkpoint is invalid")
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
    return _freeze(value)


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

    @classmethod
    def parse(cls, value: str) -> "HandoffEndpoint":
        if not isinstance(value, str) or _contains_control(value) or "%" in value:
            raise ValueError("handoff endpoint must be canonical")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("handoff endpoint must be canonical") from exc
        if (
            parsed.scheme != "hermes"
            or parsed.netloc != "local"
            or "?" in value
            or "#" in value
            or not parsed.path.startswith("/")
            or parsed.path.count("/") != 1
        ):
            raise ValueError("handoff endpoint must be hermes://local/<profile>")
        profile = parsed.path[1:]
        try:
            validate_profile_name(profile)
        except ValueError as exc:
            raise ValueError("handoff endpoint profile is invalid") from exc
        return cls(canonical=f"hermes://local/{profile}", profile=profile)


@dataclass(frozen=True, slots=True)
class HandoffSpec:
    mode: Literal["task"]
    endpoint: HandoffEndpoint
    prompt: str
    output_schema: Mapping[str, object] | None
    deadline_at: datetime | None
    attribution: Mapping[str, str]
    required_capabilities: frozenset[str]

    def __post_init__(self) -> None:
        if self.mode != "task":
            raise ValueError("handoff mode must be task")
        if not isinstance(self.endpoint, HandoffEndpoint):
            raise ValueError("handoff endpoint is invalid")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("handoff prompt must not be blank")
        if len(self.prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            raise ValueError("handoff prompt exceeds byte limit")
        schema = None
        if self.output_schema is not None:
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
        object.__setattr__(self, "output_schema", schema)
        object.__setattr__(self, "deadline_at", _aware_utc(self.deadline_at, "deadline"))
        object.__setattr__(self, "attribution", _freeze(attribution))
        object.__setattr__(self, "required_capabilities", capabilities)

    @property
    def fingerprint_input(self) -> bytes:
        return canonical_json_bytes({
            "mode": self.mode,
            "endpoint": self.endpoint.canonical,
            "prompt": self.prompt,
            "output_schema": self.output_schema,
            "deadline_at": _timestamp(self.deadline_at),
            "attribution": self.attribution,
            "required_capabilities": sorted(self.required_capabilities),
        }, max_bytes=3_200_000)

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
        if self.failure_code is not None and not _safe_identifier(self.failure_code):
            raise ValueError("handoff failure code is invalid")
        object.__setattr__(self, "next_advance_at", _aware_utc(self.next_advance_at, "next advance"))
