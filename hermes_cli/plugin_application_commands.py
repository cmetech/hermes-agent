"""Owner-bound, model-independent commands shared between trusted plugins."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

__all__ = [
    "ApplicationCommandHandler",
    "PluginApplicationCommandDenied",
    "PluginApplicationCommandError",
    "PluginApplicationCommandExecutionError",
    "PluginApplicationCommandInvalid",
    "PluginApplicationCommandInvocation",
    "PluginApplicationCommandMode",
    "PluginApplicationCommandRegistrationError",
    "PluginApplicationCommandUnavailable",
]

_APPLICATION_COMMAND_MINT_KEY = object()
_MAX_ARGUMENT_BYTES = 1_048_576
_MAX_RESULT_BYTES = 1_048_576
_MAX_ID_LENGTH = 128
_IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class PluginApplicationCommandError(RuntimeError):
    """Base class for stable, safe application-command failures."""

    category = "transient"
    _safe_message = "plugin application command failed"

    def __init__(self) -> None:
        super().__init__(self._safe_message)
        self.category = type(self).category


class PluginApplicationCommandRegistrationError(PluginApplicationCommandError):
    category = "registration"
    _safe_message = "plugin application command registration failed"


class PluginApplicationCommandUnavailable(PluginApplicationCommandError):
    category = "unavailable"
    _safe_message = "plugin application command provider is unavailable"


class PluginApplicationCommandDenied(PluginApplicationCommandError):
    category = "permission"
    _safe_message = "plugin application command invocation was denied"


class PluginApplicationCommandInvalid(PluginApplicationCommandError):
    category = "invalid_input"
    _safe_message = "plugin application command input is invalid"


class PluginApplicationCommandExecutionError(PluginApplicationCommandError):
    category = "transient"
    _safe_message = "plugin application command execution failed"


class PluginApplicationCommandMode(str, Enum):
    READ = "read"
    DRY_RUN = "dry_run"
    CONFIRM = "confirm"


def _parse_mode(value: object) -> PluginApplicationCommandMode:
    if isinstance(value, bool):
        raise PluginApplicationCommandInvalid()
    if type(value) is PluginApplicationCommandMode:
        return value
    if not isinstance(value, str):
        raise PluginApplicationCommandInvalid()
    try:
        return PluginApplicationCommandMode(value)
    except ValueError:
        raise PluginApplicationCommandInvalid() from None


def _validate_identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise PluginApplicationCommandInvalid()
    return value


def _json_native_copy(value: Any, error_type: type[PluginApplicationCommandError]) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_type()
        return value
    if isinstance(value, list):
        return [_json_native_copy(item, error_type) for item in value]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise error_type()
            copied[key] = _json_native_copy(item, error_type)
        return copied
    raise error_type()


def _canonical_json_copy(
    mapping: object,
    maximum: int,
    *,
    error_type: type[PluginApplicationCommandError],
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(mapping, Mapping):
        raise error_type()
    try:
        copied = _json_native_copy(mapping, error_type)
        encoded = json.dumps(
            copied,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > maximum:
            raise error_type()
        decoded = json.loads(encoded)
    except PluginApplicationCommandError:
        raise
    except (RecursionError, TypeError, ValueError, UnicodeError):
        raise error_type() from None
    if not isinstance(decoded, dict):
        raise error_type()
    return decoded, encoded


def _canonical_arguments(arguments: object) -> tuple[dict[str, Any], str]:
    copied, encoded = _canonical_json_copy(
        arguments,
        _MAX_ARGUMENT_BYTES,
        error_type=PluginApplicationCommandInvalid,
    )
    return copied, hashlib.sha256(encoded).hexdigest()


def _validate_result_mapping(result: object) -> dict[str, Any]:
    copied, _encoded = _canonical_json_copy(
        result,
        _MAX_RESULT_BYTES,
        error_type=PluginApplicationCommandExecutionError,
    )
    return copied


class PluginApplicationCommandInvocation:
    """Immutable, single-use authority minted by the Hermes host."""

    __slots__ = (
        "provider_id",
        "caller_id",
        "operation",
        "arguments",
        "mode",
        "invocation_id",
        "arguments_sha256",
        "profile_fingerprint",
        "_activate_once",
        "_close_once",
        "_is_active",
    )

    def __init__(
        self,
        *,
        provider_id: str,
        caller_id: str,
        operation: str,
        arguments: Mapping[str, Any],
        mode: PluginApplicationCommandMode | str,
        invocation_id: str,
        profile_fingerprint: str,
        _registration_token: object | None = None,
        _mint_key: object | None = None,
    ) -> None:
        if _mint_key is not _APPLICATION_COMMAND_MINT_KEY:
            raise TypeError(
                "PluginApplicationCommandInvocation instances are minted by Hermes"
            )
        provider_id = _validate_identifier(provider_id)
        caller_id = _validate_identifier(caller_id)
        operation = _validate_identifier(operation)
        parsed_mode = _parse_mode(mode)
        if (
            not isinstance(invocation_id, str)
            or not invocation_id
            or len(invocation_id) > _MAX_ID_LENGTH
        ):
            raise PluginApplicationCommandInvalid()
        if (
            not isinstance(profile_fingerprint, str)
            or not profile_fingerprint
            or len(profile_fingerprint) > _MAX_ID_LENGTH
        ):
            raise PluginApplicationCommandInvalid()
        copied_arguments, arguments_sha256 = _canonical_arguments(arguments)
        state_lock = threading.Lock()
        state = "minted"

        def activate_once(registration_token: object) -> bool:
            nonlocal state
            if registration_token is not _registration_token:
                return False
            with state_lock:
                if state != "minted":
                    return False
                state = "active"
                return True

        def close_once(registration_token: object) -> bool:
            nonlocal state
            if registration_token is not _registration_token:
                return False
            with state_lock:
                if state == "closed":
                    return False
                state = "closed"
                return True

        def is_active() -> bool:
            with state_lock:
                return state == "active"

        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "caller_id", caller_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "arguments", MappingProxyType(copied_arguments))
        object.__setattr__(self, "mode", parsed_mode)
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "arguments_sha256", arguments_sha256)
        object.__setattr__(self, "profile_fingerprint", profile_fingerprint)
        object.__setattr__(self, "_activate_once", activate_once)
        object.__setattr__(self, "_close_once", close_once)
        object.__setattr__(self, "_is_active", is_active)

    @property
    def active(self) -> bool:
        return self._is_active()

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("PluginApplicationCommandInvocation is immutable")

    def __repr__(self) -> str:
        return (
            "PluginApplicationCommandInvocation("
            f"provider_id={self.provider_id!r}, caller_id={self.caller_id!r}, "
            f"operation={self.operation!r}, mode={self.mode.value!r}, "
            f"invocation_id={self.invocation_id!r}, "
            f"arguments_sha256={self.arguments_sha256!r}, "
            f"profile_fingerprint={self.profile_fingerprint!r}, "
            f"active={self.active!r})"
        )


ApplicationCommandHandler = Callable[
    [PluginApplicationCommandInvocation], Mapping[str, Any]
]


@dataclass(frozen=True, slots=True)
class _ApplicationCommandRegistration:
    provider_id: str
    operations: Mapping[str, str]
    allowed_callers: frozenset[str]
    handler: ApplicationCommandHandler
    _registration_token: object


def _build_registration(
    *,
    provider_id: object,
    operations: object,
    allowed_callers: object,
    handler: object,
) -> _ApplicationCommandRegistration:
    try:
        validated_provider = _validate_identifier(provider_id)
        if not isinstance(operations, Mapping) or not operations:
            raise PluginApplicationCommandInvalid()
        validated_operations: dict[str, str] = {}
        for operation, classification in operations.items():
            validated_operation = _validate_identifier(operation)
            if not isinstance(classification, str) or classification not in {
                "read",
                "write",
            }:
                raise PluginApplicationCommandInvalid()
            validated_operations[validated_operation] = classification
        if (
            isinstance(allowed_callers, (str, bytes))
            or not isinstance(allowed_callers, Collection)
            or not allowed_callers
        ):
            raise PluginApplicationCommandInvalid()
        validated_callers = frozenset(
            _validate_identifier(caller) for caller in allowed_callers
        )
        if not callable(handler):
            raise PluginApplicationCommandInvalid()
    except PluginApplicationCommandInvalid:
        raise PluginApplicationCommandRegistrationError() from None
    return _ApplicationCommandRegistration(
        provider_id=validated_provider,
        operations=MappingProxyType(validated_operations),
        allowed_callers=validated_callers,
        handler=handler,
        _registration_token=object(),
    )


def _mint_invocation(
    *,
    provider_id: str,
    caller_id: str,
    operation: str,
    arguments: Mapping[str, Any],
    mode: PluginApplicationCommandMode | str,
    invocation_id: str,
    profile_fingerprint: str,
    registration_token: object,
) -> PluginApplicationCommandInvocation:
    return PluginApplicationCommandInvocation(
        provider_id=provider_id,
        caller_id=caller_id,
        operation=operation,
        arguments=arguments,
        mode=mode,
        invocation_id=invocation_id,
        profile_fingerprint=profile_fingerprint,
        _registration_token=registration_token,
        _mint_key=_APPLICATION_COMMAND_MINT_KEY,
    )


def _mint_invocation_for_test(**values: Any) -> PluginApplicationCommandInvocation:
    """Private seam for value-object tests; never used by plugin code."""
    return _mint_invocation(registration_token=object(), **values)
