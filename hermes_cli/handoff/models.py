"""Consumer-neutral local handoff value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
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


def _contains_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _unsafe_fact_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    collapsed = normalized.replace("_", "")
    return (
        any(part in normalized or part.replace("_", "") in collapsed for part in _CREDENTIAL_KEY_PARTS)
        or "header" in normalized
        or ("error" in normalized and not normalized.endswith("_code"))
    )


def _unsafe_fact_value(value: str) -> bool:
    if value.lower().startswith(("bearer", "basic", "~", "/", "\\\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    return bool(parsed.scheme or parsed.netloc)


def _validate_durable_facts(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _unsafe_fact_key(key):
                raise ValueError("handoff durable facts are unsafe")
            _validate_durable_facts(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _validate_durable_facts(item)
    elif isinstance(value, str) and _unsafe_fact_value(value):
        raise ValueError("handoff durable facts are unsafe")


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
            value = getattr(self, name)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"{name} must be a mapping")
            if value is not None:
                _validate_durable_facts(value)
            object.__setattr__(self, name, _freeze(value) if value is not None else None)


@dataclass(frozen=True, slots=True)
class ChannelObservation:
    phase: str
    checkpoint: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    binding: Mapping[str, object] | None = None
    mechanism: str | None = None
    terminal_result: Mapping[str, object] | None = None
    failure_code: str | None = None
    safe_data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    next_advance_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.phase not in HANDOFF_PHASES:
            raise ValueError("observation phase is invalid")
        for name in ("checkpoint", "binding", "terminal_result", "safe_data"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Mapping):
                raise ValueError(f"{name} must be a mapping")
            if value is not None:
                _validate_durable_facts(value)
            object.__setattr__(self, name, _freeze(value) if value is not None else None)
        object.__setattr__(self, "next_advance_at", _aware_utc(self.next_advance_at, "next advance"))
