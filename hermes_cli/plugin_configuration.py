"""Static, side-effect-free plugin configuration descriptors.

Plugins may point ``config_schema`` in ``plugin.yaml`` at a JSON file below
their plugin root.  This module reads that file as inert data: it never imports
plugin code and it deliberately supports no executable setup action metadata.
Malformed or unsafe descriptors are omitted (``None``) so discovery can keep
listing the plugin without trusting the descriptor.
"""

from __future__ import annotations

import contextvars
import json
import hashlib
import math
import os
import re
import stat
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

from hermes_cli import secret_keystore


_MAX_DESCRIPTOR_BYTES = 64 * 1024
_MAX_FIELDS = 64
_MAX_SETUP_ACTIONS = 16
_MAX_TEXT = 4096
_MAX_LABEL = 256
_MAX_ID = 64
_MAX_PATTERN = 256
_MAX_ENUM_VALUES = 64
_MAX_PLATFORMS = 16
_MAX_CONNECTOR_INVENTORY = 256
_MAX_CONNECTOR_RUNTIME_PLUGINS = 256
_MAX_CONNECTOR_TOOLS = 4096
_MAX_CONNECTOR_INVENTORY_VISITS = 4096

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_PLATFORM_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FORMATS = frozenset({"path", "url"})
_FIELD_TYPES = frozenset({"boolean", "integer", "number", "string"})
_TOP_LEVEL_KEYS = frozenset({"version", "fields", "setup_actions"})
_FIELD_KEYS = frozenset({
    "id",
    "label",
    "type",
    "storage",
    "required",
    "help",
    "documentation_url",
    "default",
    "advanced",
    "platforms",
    "visible_when",
    "validation",
    "readiness",
})
_VALIDATION_KEYS = frozenset({
    "min_length",
    "max_length",
    "pattern",
    "enum",
    "minimum",
    "maximum",
    "format",
})
_VISIBLE_WHEN_KEYS = frozenset({"field", "equals"})
_SETUP_ACTION_KEYS = frozenset({
    "id",
    "label",
    "help",
    "interactive",
    "documentation_url",
})
_SAFE_PATTERN_ESCAPES = frozenset(r"\.^$[]-*+?{}()|/dDsSwW")


class FieldStorage(str, Enum):
    """Where a host stores a field value."""

    SETTING = "setting"
    SECRET = "secret"


@dataclass(frozen=True)
class ValidationConstraints:
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    enum: tuple[Any, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    format: str | None = None


@dataclass(frozen=True)
class VisibilityCondition:
    field: str
    equals: str | int | float | bool | None


@dataclass(frozen=True)
class ReadinessContribution:
    enabled: bool = False


@dataclass(frozen=True)
class PluginConfigurationField:
    id: str
    label: str
    type: str
    storage: FieldStorage
    required: bool = False
    help: str | None = None
    documentation_url: str | None = None
    default: Any = None
    has_default: bool = False
    advanced: bool = False
    platforms: tuple[str, ...] = ()
    visible_when: VisibilityCondition | None = None
    validation: ValidationConstraints = ValidationConstraints()
    readiness: ReadinessContribution = ReadinessContribution()


@dataclass(frozen=True)
class SetupActionMetadata:
    id: str
    label: str
    help: str | None = None
    interactive: bool = False
    documentation_url: str | None = None


@dataclass(frozen=True)
class PluginConfigurationDescriptor:
    version: int
    fields: tuple[PluginConfigurationField, ...]
    setup_actions: tuple[SetupActionMetadata, ...] = ()


class _InvalidDescriptor(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidDescriptor(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bounded_text(value: Any, *, maximum: int = _MAX_TEXT) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise _InvalidDescriptor("expected bounded non-empty text")
    return value


def _identifier(value: Any) -> str:
    value = _bounded_text(value, maximum=_MAX_ID)
    if _ID_PATTERN.fullmatch(value) is None:
        raise _InvalidDescriptor("invalid identifier")
    return value


def _safe_http_url(value: str) -> bool:
    if "\\" in value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or 0 < port < 65536)
    )


def _documentation_url(value: Any) -> str:
    value = _bounded_text(value, maximum=2048)
    if not _safe_http_url(value):
        raise _InvalidDescriptor("documentation URL must use HTTP(S) with authority")
    return value


def _exact_keys(data: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if any(not isinstance(key, str) or key not in allowed for key in data):
        raise _InvalidDescriptor("unknown or unsafe key")


def _json_scalar(value: Any) -> bool:
    return value is None or type(value) in {str, int, float, bool}


def _value_matches_type(value: Any, field_type: str) -> bool:
    if field_type == "string":
        return type(value) is str and len(value) <= _MAX_TEXT
    if field_type == "integer":
        return type(value) is int
    if field_type == "number":
        return type(value) in {int, float} and math.isfinite(value)
    if field_type == "boolean":
        return type(value) is bool
    return False


def _validate_fixed_width_pattern(pattern: str) -> None:
    """Accept only regex syntax whose match cost is linear in input length.

    V1 patterns may use optional outer anchors, literals, single-character
    categories/escapes, ``.``, and character classes. Repetition, grouping,
    alternation, lookaround, and backreferences are deliberately unavailable.
    Every accepted atom therefore consumes at most one character, so the
    existing bounded pattern/input sizes bound ``re.search`` work.
    """

    index = 0
    if pattern.startswith("^"):
        index = 1
    end = len(pattern)
    if pattern.endswith("$"):
        preceding_backslashes = 0
        while (
            end - preceding_backslashes > 1
            and pattern[end - preceding_backslashes - 2] == "\\"
        ):
            preceding_backslashes += 1
        if preceding_backslashes % 2 == 0:
            end -= 1
    while index < end:
        character = pattern[index]
        if character == "\\":
            index += 1
            if index >= end or pattern[index] not in _SAFE_PATTERN_ESCAPES:
                raise _InvalidDescriptor("unsafe pattern escape")
        elif character == "[":
            index += 1
            class_atoms = 0
            if index < end and pattern[index] == "^":
                index += 1
            while index < end and pattern[index] != "]":
                if pattern[index] == "[":
                    raise _InvalidDescriptor("nested character class")
                if pattern[index] == "\\":
                    index += 1
                    if index >= end or pattern[index] not in _SAFE_PATTERN_ESCAPES:
                        raise _InvalidDescriptor("unsafe character-class escape")
                class_atoms += 1
                index += 1
            if index >= end or pattern[index] != "]" or class_atoms == 0:
                raise _InvalidDescriptor("unterminated or empty character class")
        elif character in "*+?{}()|^$":
            raise _InvalidDescriptor("pattern uses variable or branching syntax")
        index += 1

    try:
        re.compile(pattern)
    except re.error as exc:
        raise _InvalidDescriptor("invalid fixed-width pattern") from exc


def _parse_validation(raw: Any, field_type: str) -> ValidationConstraints:
    if raw is None:
        return ValidationConstraints()
    if not isinstance(raw, dict):
        raise _InvalidDescriptor("validation must be an object")
    _exact_keys(raw, _VALIDATION_KEYS)

    string_only = {"min_length", "max_length", "pattern", "format"}
    number_only = {"minimum", "maximum"}
    if field_type != "string" and string_only.intersection(raw):
        raise _InvalidDescriptor("string validation used for non-string field")
    if field_type not in {"integer", "number"} and number_only.intersection(raw):
        raise _InvalidDescriptor("numeric validation used for non-numeric field")

    min_length = raw.get("min_length")
    max_length = raw.get("max_length")
    for value in (min_length, max_length):
        if value is not None and (
            type(value) is not int or not 0 <= value <= _MAX_TEXT
        ):
            raise _InvalidDescriptor("invalid string length constraint")
    if min_length is not None and max_length is not None and min_length > max_length:
        raise _InvalidDescriptor("inverted string length constraint")

    pattern = raw.get("pattern")
    if pattern is not None:
        pattern = _bounded_text(pattern, maximum=_MAX_PATTERN)
        _validate_fixed_width_pattern(pattern)

    field_format = raw.get("format")
    if field_format is not None and field_format not in _FORMATS:
        raise _InvalidDescriptor("unknown field format")

    minimum = raw.get("minimum")
    maximum = raw.get("maximum")
    for value in (minimum, maximum):
        if value is not None and (
            type(value) not in {int, float} or not math.isfinite(value)
        ):
            raise _InvalidDescriptor("invalid numeric bound")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise _InvalidDescriptor("inverted numeric bounds")

    enum_raw = raw.get("enum", [])
    if not isinstance(enum_raw, list) or len(enum_raw) > _MAX_ENUM_VALUES:
        raise _InvalidDescriptor("invalid enum")
    enum: list[Any] = []
    for value in enum_raw:
        if not _value_matches_type(value, field_type) or value in enum:
            raise _InvalidDescriptor("invalid or duplicate enum value")
        enum.append(value)

    return ValidationConstraints(
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        enum=tuple(enum),
        minimum=minimum,
        maximum=maximum,
        format=field_format,
    )


def _default_satisfies(
    value: Any, field_type: str, rules: ValidationConstraints
) -> bool:
    if not _value_matches_type(value, field_type):
        return False
    if rules.enum and value not in rules.enum:
        return False
    if field_type == "string":
        if rules.min_length is not None and len(value) < rules.min_length:
            return False
        if rules.max_length is not None and len(value) > rules.max_length:
            return False
        if rules.pattern is not None and re.search(rules.pattern, value) is None:
            return False
        if rules.format == "url":
            if not _safe_http_url(value):
                return False
        if rules.format == "path" and "\x00" in value:
            return False
    if field_type in {"integer", "number"}:
        if rules.minimum is not None and value < rules.minimum:
            return False
        if rules.maximum is not None and value > rules.maximum:
            return False
    return True


def _parse_field(raw: Any) -> PluginConfigurationField:
    if not isinstance(raw, dict):
        raise _InvalidDescriptor("field must be an object")
    _exact_keys(raw, _FIELD_KEYS)
    field_id = _identifier(raw.get("id"))
    label = _bounded_text(raw.get("label"), maximum=_MAX_LABEL)
    field_type = raw.get("type")
    if field_type not in _FIELD_TYPES:
        raise _InvalidDescriptor("unknown field type")
    try:
        storage = FieldStorage(raw.get("storage"))
    except (TypeError, ValueError) as exc:
        raise _InvalidDescriptor("unknown field storage") from exc

    required = raw.get("required", False)
    advanced = raw.get("advanced", False)
    readiness = raw.get("readiness", False)
    if any(type(value) is not bool for value in (required, advanced, readiness)):
        raise _InvalidDescriptor("field flags must be booleans")

    help_text = raw.get("help")
    if help_text is not None:
        help_text = _bounded_text(help_text)
    documentation_url = raw.get("documentation_url")
    if documentation_url is not None:
        documentation_url = _documentation_url(documentation_url)

    platforms_raw = raw.get("platforms", [])
    if not isinstance(platforms_raw, list) or len(platforms_raw) > _MAX_PLATFORMS:
        raise _InvalidDescriptor("invalid platforms")
    platforms: list[str] = []
    for platform in platforms_raw:
        if (
            not isinstance(platform, str)
            or _PLATFORM_PATTERN.fullmatch(platform) is None
        ):
            raise _InvalidDescriptor("invalid platform")
        if platform in platforms:
            raise _InvalidDescriptor("duplicate platform")
        platforms.append(platform)

    visible_when = None
    raw_visibility = raw.get("visible_when")
    if raw_visibility is not None:
        if not isinstance(raw_visibility, dict):
            raise _InvalidDescriptor("visible_when must be an object")
        _exact_keys(raw_visibility, _VISIBLE_WHEN_KEYS)
        if set(raw_visibility) != _VISIBLE_WHEN_KEYS:
            raise _InvalidDescriptor("incomplete visible_when")
        equals = raw_visibility["equals"]
        if not _json_scalar(equals) or (
            isinstance(equals, str) and len(equals) > _MAX_TEXT
        ):
            raise _InvalidDescriptor("visible_when equals must be a bounded scalar")
        visible_when = VisibilityCondition(
            field=_identifier(raw_visibility["field"]), equals=equals
        )

    validation = _parse_validation(raw.get("validation"), field_type)
    has_default = "default" in raw
    default = raw.get("default")
    if has_default:
        if storage is FieldStorage.SECRET:
            raise _InvalidDescriptor("secret fields cannot declare defaults")
        if not _default_satisfies(default, field_type, validation):
            raise _InvalidDescriptor("default does not satisfy field validation")

    return PluginConfigurationField(
        id=field_id,
        label=label,
        type=field_type,
        storage=storage,
        required=required,
        help=help_text,
        documentation_url=documentation_url,
        default=default,
        has_default=has_default,
        advanced=advanced,
        platforms=tuple(platforms),
        visible_when=visible_when,
        validation=validation,
        readiness=ReadinessContribution(readiness),
    )


def _parse_setup_action(raw: Any) -> SetupActionMetadata:
    if not isinstance(raw, dict):
        raise _InvalidDescriptor("setup action must be an object")
    _exact_keys(raw, _SETUP_ACTION_KEYS)
    if "id" not in raw or "label" not in raw:
        raise _InvalidDescriptor("incomplete setup action")
    interactive = raw.get("interactive", False)
    if type(interactive) is not bool:
        raise _InvalidDescriptor("interactive must be a boolean")
    help_text = raw.get("help")
    if help_text is not None:
        help_text = _bounded_text(help_text)
    documentation_url = raw.get("documentation_url")
    if documentation_url is not None:
        documentation_url = _documentation_url(documentation_url)
    return SetupActionMetadata(
        id=_identifier(raw["id"]),
        label=_bounded_text(raw["label"], maximum=_MAX_LABEL),
        help=help_text,
        interactive=interactive,
        documentation_url=documentation_url,
    )


def _parse_descriptor(raw: Any) -> PluginConfigurationDescriptor:
    if not isinstance(raw, dict):
        raise _InvalidDescriptor("descriptor must be an object")
    _exact_keys(raw, _TOP_LEVEL_KEYS)
    if raw.get("version") != 1 or type(raw.get("version")) is not int:
        raise _InvalidDescriptor("unsupported descriptor version")
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or len(raw_fields) > _MAX_FIELDS:
        raise _InvalidDescriptor("invalid field list")
    fields = tuple(_parse_field(item) for item in raw_fields)
    field_ids = [field.id for field in fields]
    if len(field_ids) != len(set(field_ids)):
        raise _InvalidDescriptor("duplicate field id")
    by_id = {field.id: field for field in fields}
    for field in fields:
        condition = field.visible_when
        if condition is None:
            continue
        dependency = by_id.get(condition.field)
        if dependency is None or (
            condition.equals is not None
            and not _value_matches_type(condition.equals, dependency.type)
        ):
            raise _InvalidDescriptor("invalid visible_when dependency")

    raw_actions = raw.get("setup_actions", [])
    if not isinstance(raw_actions, list) or len(raw_actions) > _MAX_SETUP_ACTIONS:
        raise _InvalidDescriptor("invalid setup action list")
    actions = tuple(_parse_setup_action(item) for item in raw_actions)
    action_ids = [action.id for action in actions]
    if len(action_ids) != len(set(action_ids)):
        raise _InvalidDescriptor("duplicate setup action id")
    return PluginConfigurationDescriptor(
        version=1, fields=fields, setup_actions=actions
    )


def _safe_descriptor_path(plugin_root: Path, reference: Any) -> Path | None:
    if not isinstance(reference, str) or not reference or len(reference) > 512:
        return None
    relative = Path(reference)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        return None
    try:
        root = plugin_root.resolve(strict=True)
        if plugin_root.is_symlink() or not root.is_dir():
            return None
        candidate = plugin_root / relative
        current = plugin_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _read_regular_file(path: Path) -> bytes | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DESCRIPTOR_BYTES:
            return None
        chunks: list[bytes] = []
        remaining = _MAX_DESCRIPTOR_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        return content if len(content) <= _MAX_DESCRIPTOR_BYTES else None
    except OSError:
        return None
    finally:
        os.close(descriptor)


def load_plugin_configuration(
    plugin_root: Path | str, reference: Any
) -> PluginConfigurationDescriptor | None:
    """Load a referenced v1 descriptor, returning ``None`` on any violation."""

    path = _safe_descriptor_path(Path(plugin_root), reference)
    if path is None:
        return None
    content = _read_regular_file(path)
    if content is None:
        return None
    try:
        raw = json.loads(
            content.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
        return _parse_descriptor(raw)
    except (UnicodeDecodeError, ValueError, OverflowError, RecursionError):
        return None


def _validation_projection(rules: ValidationConstraints) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in (
        "min_length",
        "max_length",
        "pattern",
        "minimum",
        "maximum",
        "format",
    ):
        value = getattr(rules, key)
        if value is not None:
            projected[key] = value
    if rules.enum:
        projected["enum"] = list(rules.enum)
    return projected


def _field_projection(field: PluginConfigurationField) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "id": field.id,
        "label": field.label,
        "type": field.type,
        "storage": field.storage.value,
        "required": field.required,
        "advanced": field.advanced,
        "readiness": field.readiness.enabled,
    }
    if field.help is not None:
        projected["help"] = field.help
    if field.documentation_url is not None:
        projected["documentation_url"] = field.documentation_url
    if field.has_default:
        projected["default"] = field.default
    if field.platforms:
        projected["platforms"] = list(field.platforms)
    if field.visible_when is not None:
        projected["visible_when"] = {
            "field": field.visible_when.field,
            "equals": field.visible_when.equals,
        }
    validation = _validation_projection(field.validation)
    if validation:
        projected["validation"] = validation
    return projected


def project_plugin_configuration(
    descriptor: PluginConfigurationDescriptor,
    *,
    settings: Mapping[str, Any] | None = None,
    secrets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a catalog-safe descriptor/status projection.

    Setting values are included when supplied by the caller. Secret values are
    never returned; only an ``is_set`` boolean is projected.
    """

    settings = settings or {}
    secrets = secrets or {}
    fields: list[dict[str, Any]] = []
    for field in descriptor.fields:
        projected = _field_projection(field)
        if field.storage is FieldStorage.SECRET:
            secret = secrets.get(field.id)
            projected["is_set"] = secret is not None and secret != ""
        elif field.id in settings:
            projected["value"] = settings[field.id]
        fields.append(projected)

    result: dict[str, Any] = {"version": descriptor.version, "fields": fields}
    if descriptor.setup_actions:
        actions: list[dict[str, Any]] = []
        for action in descriptor.setup_actions:
            projected_action: dict[str, Any] = {
                "id": action.id,
                "label": action.label,
                "interactive": action.interactive,
            }
            if action.help is not None:
                projected_action["help"] = action.help
            if action.documentation_url is not None:
                projected_action["documentation_url"] = action.documentation_url
            actions.append(projected_action)
        result["setup_actions"] = actions
    return result


class PluginConfigurationError(ValueError):
    """Stable caller-facing error for connector configuration operations."""


class PluginRuntimeConfiguration:
    """Immutable resolved values exposed only to their owning plugin context."""

    __slots__ = ("__setting_lookup", "__secret_lookup")

    def __init__(
        self, setting_values: Mapping[str, Any], secret_values: Mapping[str, Any]
    ) -> None:
        def lookup(values: Mapping[str, Any]):
            snapshot = MappingProxyType(dict(values))

            def resolve(field_id: str) -> Any:
                if not isinstance(field_id, str) or field_id not in snapshot:
                    raise PluginConfigurationError(
                        "plugin configuration value unavailable"
                    )
                return snapshot[field_id]

            return resolve

        object.__setattr__(
            self,
            "_PluginRuntimeConfiguration__setting_lookup",
            lookup(setting_values),
        )
        object.__setattr__(
            self,
            "_PluginRuntimeConfiguration__secret_lookup",
            lookup(secret_values),
        )

    def __getattribute__(self, name: str) -> Any:
        if name in {"setting", "secret"}:
            return object.__getattribute__(self, name)
        raise AttributeError("plugin runtime configuration member unavailable")

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("plugin runtime configuration member unavailable")

    def __dir__(self) -> list[str]:
        return ["setting", "secret"]

    def __repr__(self) -> str:
        return "PluginRuntimeConfiguration(<redacted>)"

    def setting(self, field_id: str) -> str | int | float | bool:
        """Return one descriptor-authorized, currently resolved setting."""

        lookup = object.__getattribute__(
            self, "_PluginRuntimeConfiguration__setting_lookup"
        )
        return lookup(field_id)

    def secret(self, field_id: str) -> str | int | float | bool:
        """Return one descriptor-authorized, currently resolved secret."""

        lookup = object.__getattribute__(
            self, "_PluginRuntimeConfiguration__secret_lookup"
        )
        return lookup(field_id)


@dataclass(frozen=True)
class SetupActionContext:
    """Bounded host-owned context passed to one explicitly invoked action."""

    plugin_id: str
    configuration: Mapping[str, Any]
    _cancel_event: threading.Event

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()


@dataclass
class _SetupActionRun:
    run_id: str
    plugin_id: str
    action: str
    deadline: float
    cancel_event: threading.Event
    profile_id: str
    status: str = "queued"
    result: Mapping[str, Any] | None = None
    error: str | None = None
    timer: threading.Timer | None = None


_MAX_ACTION_TIMEOUT = 300.0
_MAX_ACTION_RESULT_BYTES = 64 * 1024
_MAX_ACTION_TEXT = 4096
_MAX_ACTION_COLLECTION = 128
_MAX_ACTION_DEPTH = 8
_MAX_ACTION_NODES = 4096
_MAX_ACTION_WORKERS = 8
_MAX_ACTION_RUNS = 128
_MAX_READINESS_WORKERS = 4
_READINESS_TIMEOUT = 0.2
_SENSITIVE_RESULT_PARTS = frozenset({
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
})


@dataclass
class _ProjectionBudget:
    """Shared traversal budget that prevents oversized intermediate results."""

    nodes: int = 0
    encoded_bytes: int = 0

    def add_node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_ACTION_NODES:
            raise PluginConfigurationError("setup action output exceeded node limit")

    def add_json_bytes(self, value: Any) -> None:
        self.add_bytes(len(json.dumps(value, ensure_ascii=False).encode("utf-8")))

    def add_bytes(self, amount: int) -> None:
        self.encoded_bytes += amount
        if self.encoded_bytes > _MAX_ACTION_RESULT_BYTES:
            raise PluginConfigurationError("setup action output exceeded byte limit")


def _secret_storage_key(plugin_id: str, field_id: str) -> str:
    """Return a collision-resistant env key without accepting caller key names."""

    identity = f"{plugin_id}\0{field_id}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:32].upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", field_id.upper()).strip("_")[:32]
    return f"HERMES_PLUGIN_{digest}_{slug or 'SECRET'}"


def _bounded_public_value(
    value: Any,
    *,
    depth: int = 0,
    key: str = "",
    redactions: tuple[str, ...] = (),
    budget: _ProjectionBudget | None = None,
) -> Any:
    if budget is None:
        budget = _ProjectionBudget()
    budget.add_node()
    if any(part in key.lower() for part in _SENSITIVE_RESULT_PARTS):
        budget.add_json_bytes("[redacted]")
        return "[redacted]"
    if depth > _MAX_ACTION_DEPTH:
        raise PluginConfigurationError("setup action output exceeded depth limit")
    if value is None or type(value) in {bool, int}:
        budget.add_json_bytes(value)
        return value
    if type(value) is float:
        projected_number = value if math.isfinite(value) else None
        budget.add_json_bytes(projected_number)
        return projected_number
    if isinstance(value, str):
        if len(value) > _MAX_ACTION_TEXT:
            raise PluginConfigurationError("setup action text exceeded limit")
        projected = value
        for secret in redactions:
            projected = projected.replace(secret, "[redacted]")
        budget.add_json_bytes(projected)
        return projected
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        budget.add_bytes(2)  # opening and closing braces
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= _MAX_ACTION_COLLECTION:
                raise PluginConfigurationError("setup action collection exceeded limit")
            if not isinstance(item_key, str) or len(item_key) > 128:
                raise PluginConfigurationError(
                    "setup action result keys must be bounded strings"
                )
            safe_key = item_key
            for secret in redactions:
                safe_key = safe_key.replace(secret, "[redacted]")
            budget.add_json_bytes(safe_key)
            # Match json.dumps()'s default ``: `` / ``, `` separators. Count
            # a comma for every item (including the last) to stay conservative.
            budget.add_bytes(4)
            projected[safe_key] = _bounded_public_value(
                item_value,
                depth=depth + 1,
                key=safe_key,
                redactions=redactions,
                budget=budget,
            )
        return projected
    if isinstance(value, (list, tuple)):
        projected_items = []
        budget.add_bytes(2)  # opening and closing brackets
        for index, item in enumerate(value):
            if index >= _MAX_ACTION_COLLECTION:
                raise PluginConfigurationError("setup action collection exceeded limit")
            # Match json.dumps()'s default ``, `` separator, conservatively
            # counting it for the final item as well.
            budget.add_bytes(2)
            projected_items.append(
                _bounded_public_value(
                    item,
                    depth=depth + 1,
                    redactions=redactions,
                    budget=budget,
                )
            )
        return projected_items
    raise PluginConfigurationError("setup action result must be JSON-native")


def _validate_value(field: PluginConfigurationField, value: Any) -> None:
    if not _default_satisfies(value, field.type, field.validation):
        raise PluginConfigurationError(f"invalid value for field '{field.id}'")


class PluginConfigurationService:
    """Profile-scoped, descriptor-authorized connector configuration service."""

    def __init__(self, manager: Any = None):
        self._manager = manager
        self._runs: dict[str, _SetupActionRun] = {}
        self._runs_lock = threading.RLock()
        self._worker_slots = threading.BoundedSemaphore(_MAX_ACTION_WORKERS)
        self._readiness_slots = threading.BoundedSemaphore(_MAX_READINESS_WORKERS)

    def _plugin_manager(self):
        if self._manager is not None:
            return self._manager
        from hermes_cli.plugins import get_plugin_manager

        return get_plugin_manager()

    def _inventory(self):
        if self._manager is not None:
            return self._manager.loaded_plugins()
        from hermes_cli.plugins import LoadedPlugin

        return [
            LoadedPlugin(manifest=manifest)
            for manifest in self._plugin_manager().static_plugin_inventory()
        ]

    def _loaded(self, plugin_id: str):
        inventory = self._inventory()
        loaded = next(
            (
                item
                for item in inventory
                if (item.manifest.key or item.manifest.name) == plugin_id
            ),
            None,
        )
        if loaded is None:
            matches = [item for item in inventory if item.manifest.name == plugin_id]
            if len(matches) == 1:
                loaded = matches[0]
        if loaded is None or loaded.manifest.configuration is None:
            raise PluginConfigurationError("plugin configuration unavailable")
        return loaded

    def inventory(self, platform: str | None = None) -> list[dict[str, Any]]:
        """Project all configurable descriptors in the active profile."""
        plugin_ids = sorted(
            loaded.manifest.key or loaded.manifest.name
            for loaded in self._inventory()
            if loaded.manifest.configuration is not None
        )
        return [self.detail(plugin_id, platform=platform) for plugin_id in plugin_ids]

    def _registrations(self, plugin_id: str) -> dict[str, dict[str, Any]]:
        if self._manager is not None:
            # Explicitly injected managers are a test/embedding seam and do
            # not participate in the process-global discovery lifecycle.
            return dict(getattr(self._manager, "_setup_actions", {}).get(plugin_id, {}))
        return self._plugin_manager().setup_action_registrations(plugin_id)

    @staticmethod
    def _fields(loaded) -> dict[str, PluginConfigurationField]:
        return {field.id: field for field in loaded.manifest.configuration.fields}

    @staticmethod
    def _profile_id() -> str:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home().resolve())

    @staticmethod
    def _is_enabled(loaded) -> bool:
        """Resolve enablement from the active profile without re-importing code."""
        from hermes_cli.config import get_config_path, load_config_readonly

        manifest = loaded.manifest
        lookup_key = manifest.key or manifest.name
        config = load_config_readonly(config_path=get_config_path())
        plugins = config.get("plugins")
        if not isinstance(plugins, Mapping):
            plugins = {}
        disabled = plugins.get("disabled", [])
        if isinstance(disabled, list) and (
            lookup_key in disabled or manifest.name in disabled
        ):
            return False
        if manifest.kind == "exclusive":
            return False
        if manifest.kind == "model-provider":
            return True
        if manifest.source == "bundled" and manifest.kind in {"backend", "platform"}:
            return True
        enabled = plugins.get("enabled")
        return isinstance(enabled, list) and (
            lookup_key in enabled or manifest.name in enabled
        )

    @staticmethod
    def _settings(plugin_id: str) -> dict[str, Any]:
        from hermes_cli.config import get_config_path, load_config_readonly

        config = load_config_readonly(config_path=get_config_path())
        plugins = config.get("plugins")
        if not isinstance(plugins, Mapping):
            return {}
        entries = plugins.get("entries")
        if not isinstance(entries, Mapping):
            return {}
        entry = entries.get(plugin_id)
        if not isinstance(entry, Mapping):
            return {}
        settings = entry.get("settings")
        return dict(settings) if isinstance(settings, Mapping) else {}

    def _resolved(self, plugin_id: str, loaded) -> tuple[dict[str, Any], set[str]]:
        stored = self._settings(plugin_id)
        secret_values = self._profile_secret_values()
        resolved: dict[str, Any] = {}
        invalid: set[str] = set()
        for field in loaded.manifest.configuration.fields:
            present = False
            value = None
            if field.storage is FieldStorage.SECRET:
                storage_key = _secret_storage_key(plugin_id, field.id)
                value = secret_keystore.resolve_secret(
                    storage_key,
                    legacy_value=secret_values.get(storage_key),
                )
                if value not in {None, ""}:
                    present = True
            elif field.id in stored:
                value = stored[field.id]
                present = True
            elif field.has_default:
                value = field.default
                present = True
            if present:
                if _default_satisfies(value, field.type, field.validation):
                    resolved[field.id] = value
                else:
                    invalid.add(field.id)
        return resolved, invalid

    @staticmethod
    def _profile_secret_values() -> dict[str, str]:
        """Merge credential authorities without consulting process-global env.

        Lowest to highest precedence is the current profile file, its hydrated
        external-secret snapshot, the installed context-local secret scope,
        then administrator-managed env. A scope miss therefore observes a
        newly persisted profile value, while an explicit scoped/external or
        managed authority continues to override plaintext profile storage.
        """
        from agent.secret_scope import current_secret_scope
        from hermes_cli import env_loader, managed_scope
        from hermes_cli.config import load_env
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
        values = load_env()
        try:
            values.update(env_loader.get_secret_source_values(home))
        except Exception:
            pass
        scope = current_secret_scope()
        if scope is not None:
            values.update(scope)
        try:
            values.update(managed_scope.load_managed_env())
        except Exception:
            pass
        return {
            key: value
            for key, value in values.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def detail(self, plugin_id: str, platform: str | None = None) -> dict[str, Any]:
        loaded = self._loaded(plugin_id)
        canonical_id = loaded.manifest.key or loaded.manifest.name
        resolved, _invalid = self._resolved(canonical_id, loaded)
        enabled = self._is_enabled(loaded)
        descriptor = loaded.manifest.configuration
        settings = {
            field.id: resolved[field.id]
            for field in descriptor.fields
            if field.storage is FieldStorage.SETTING and field.id in resolved
        }
        secrets = {
            field.id: True
            for field in descriptor.fields
            if field.storage is FieldStorage.SECRET and field.id in resolved
        }
        result = project_plugin_configuration(
            descriptor, settings=settings, secrets=secrets
        )
        result.update({
            "plugin_id": canonical_id,
            "enabled": enabled,
            "readiness": self.readiness(canonical_id, platform=platform),
        })
        registrations = self._registrations(canonical_id)
        for action in result.get("setup_actions", []):
            action["available"] = bool(enabled and action["id"] in registrations)
        return result

    def update(
        self,
        plugin_id: str,
        *,
        settings: Mapping[str, Any] | None = None,
        secrets: Mapping[str, Any] | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        loaded = self._loaded(plugin_id)
        canonical_id = loaded.manifest.key or loaded.manifest.name
        fields = self._fields(loaded)
        settings = settings or {}
        secrets = secrets or {}
        overlap = set(settings).intersection(secrets)
        if overlap:
            raise PluginConfigurationError(
                "fields cannot appear in both settings and secrets: "
                + ", ".join(sorted(overlap))
            )
        for values, expected in (
            (settings, FieldStorage.SETTING),
            (secrets, FieldStorage.SECRET),
        ):
            for field_id, value in values.items():
                field = fields.get(field_id)
                if field is None:
                    raise PluginConfigurationError(f"unknown field '{field_id}'")
                if field.storage is not expected:
                    raise PluginConfigurationError(
                        f"field '{field_id}' has incompatible storage"
                    )
                _validate_value(field, value)

        if settings:
            from hermes_cli.config import (
                ConfigurationPersistenceError,
                load_config,
                save_config,
            )

            config = load_config()
            plugins = config.setdefault("plugins", {})
            if not isinstance(plugins, dict):
                plugins = {}
                config["plugins"] = plugins
            entries = plugins.setdefault("entries", {})
            if not isinstance(entries, dict):
                entries = {}
                plugins["entries"] = entries
            entry = entries.setdefault(canonical_id, {})
            if not isinstance(entry, dict):
                entry = {}
                entries[canonical_id] = entry
            stored = entry.setdefault("settings", {})
            if not isinstance(stored, dict):
                stored = {}
                entry["settings"] = stored
            stored.update(settings)
            try:
                save_config(
                    config,
                    preserve_keys={
                        ("plugins", "entries", canonical_id, "settings", field_id)
                        for field_id in settings
                    },
                    strict=True,
                )
            except ConfigurationPersistenceError as exc:
                raise PluginConfigurationError(
                    "plugin configuration could not be persisted"
                ) from exc

        if secrets:
            try:
                # Store in the OS keystore (or its encrypted-file fallback),
                # never in .env. Batch persistence avoids a partially saved
                # multi-field connector when one later secret is refused.
                secret_keystore.set_secrets(
                    {
                        _secret_storage_key(canonical_id, field_id): value
                        for field_id, value in secrets.items()
                    }
                )
            except secret_keystore.KeystoreError as exc:
                raise PluginConfigurationError(
                    "plugin configuration could not be persisted"
                ) from exc
        return self.detail(canonical_id, platform=platform)

    def clear_secret(
        self, plugin_id: str, field_id: str, *, platform: str | None = None
    ) -> dict[str, Any]:
        loaded = self._loaded(plugin_id)
        canonical_id = loaded.manifest.key or loaded.manifest.name
        field = self._fields(loaded).get(field_id)
        if field is None:
            raise PluginConfigurationError(f"unknown field '{field_id}'")
        if field.storage is not FieldStorage.SECRET:
            raise PluginConfigurationError(
                f"field '{field_id}' has incompatible storage"
            )
        storage_key = _secret_storage_key(canonical_id, field_id)
        try:
            secret_keystore.delete_secret(storage_key)
        except secret_keystore.KeystoreError as exc:
            raise PluginConfigurationError(
                "plugin configuration could not be persisted"
            ) from exc
        return self.detail(canonical_id, platform=platform)

    def readiness(self, plugin_id: str, platform: str | None = None) -> dict[str, Any]:
        loaded = self._loaded(plugin_id)
        canonical_id = loaded.manifest.key or loaded.manifest.name
        return self._readiness_for_loaded(
            loaded,
            self._registrations(canonical_id),
            platform=platform,
        )

    def _readiness_for_loaded(
        self,
        loaded,
        registrations: Mapping[str, Mapping[str, Any]],
        *,
        platform: str | None = None,
    ) -> dict[str, Any]:
        """Project readiness from an already bounded static inventory item."""

        canonical_id = loaded.manifest.key or loaded.manifest.name
        if not self._is_enabled(loaded):
            return {
                "plugin_id": canonical_id,
                "ready": False,
                "reasons": ["plugin_not_enabled"],
            }
        resolved, invalid = self._resolved(canonical_id, loaded)
        reasons: list[str] = []
        for field in loaded.manifest.configuration.fields:
            if field.platforms and (
                platform is None or platform not in field.platforms
            ):
                continue
            if field.id in invalid:
                reasons.append(f"invalid_configuration:{field.id}")
                continue
            if not field.required or not field.readiness.enabled:
                continue
            if field.visible_when is not None and (
                field.visible_when.field in invalid
                or resolved.get(field.visible_when.field) != field.visible_when.equals
            ):
                continue
            value = resolved.get(field.id)
            if value in {None, ""}:
                reason = (
                    "authentication_required"
                    if field.storage is FieldStorage.SECRET
                    else "configuration_required"
                )
                reasons.append(f"{reason}:{field.id}")
        for action_id, registration in registrations.items():
            readiness = registration.get("readiness")
            if readiness is None:
                continue
            ready = self._bounded_readiness(readiness, resolved)
            if ready is not True:
                reasons.append(f"setup_required:{action_id}")
        return {
            "plugin_id": canonical_id,
            "ready": not reasons,
            "reasons": reasons,
        }

    def _bounded_readiness(self, callback, configuration: Mapping[str, Any]) -> bool:
        if not self._readiness_slots.acquire(blocking=False):
            return False
        completed = threading.Event()
        result = [False]
        context = contextvars.copy_context()

        def invoke() -> None:
            try:
                result[0] = callback(MappingProxyType(dict(configuration))) is True
            except Exception:
                result[0] = False
            finally:
                self._readiness_slots.release()
                completed.set()

        worker = threading.Thread(
            target=lambda: context.run(invoke),
            daemon=True,
            name="plugin-setup-readiness",
        )
        try:
            worker.start()
        except BaseException:
            self._readiness_slots.release()
            return False
        if not completed.wait(_READINESS_TIMEOUT):
            return False
        return result[0]

    def start_action(
        self,
        plugin_id: str,
        action_id: str,
        *,
        unattended: bool = False,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= _MAX_ACTION_TIMEOUT
        ):
            raise PluginConfigurationError("deadline must be between 0 and 300 seconds")
        loaded = self._loaded(plugin_id)
        canonical_id = loaded.manifest.key or loaded.manifest.name
        registrations = self._registrations(canonical_id)
        registration = registrations.get(action_id)
        if not self._is_enabled(loaded) or registration is None:
            raise PluginConfigurationError("setup action unavailable")
        metadata = next(
            (
                action
                for action in loaded.manifest.configuration.setup_actions
                if action.id == action_id
            ),
            None,
        )
        if metadata is None:
            raise PluginConfigurationError("setup action unavailable")
        if unattended and metadata.interactive:
            raise PluginConfigurationError(
                "interactive setup action cannot run unattended"
            )
        profile_id = self._profile_id()
        configuration, _invalid = self._resolved(canonical_id, loaded)
        redactions = tuple(
            str(configuration[field.id])
            for field in loaded.manifest.configuration.fields
            if field.storage is FieldStorage.SECRET
            and field.id in configuration
            and configuration[field.id] not in {None, ""}
        )
        run = _SetupActionRun(
            run_id=uuid.uuid4().hex,
            plugin_id=canonical_id,
            action=action_id,
            deadline=time.monotonic() + float(timeout_seconds),
            cancel_event=threading.Event(),
            profile_id=profile_id,
        )
        context = contextvars.copy_context()
        worker = threading.Thread(
            target=lambda: context.run(
                self._execute_action,
                run,
                registration["handler"],
                MappingProxyType(dict(configuration)),
                redactions,
            ),
            daemon=True,
            name=f"plugin-setup-{canonical_id[:24]}",
        )
        timer = threading.Timer(
            float(timeout_seconds), self._timeout_run, args=(run.run_id,)
        )
        timer.daemon = True
        run.timer = timer
        if not self._worker_slots.acquire(blocking=False):
            raise PluginConfigurationError("setup action worker capacity exhausted")
        with self._runs_lock:
            if not self._make_run_capacity_locked():
                self._worker_slots.release()
                raise PluginConfigurationError("setup action run capacity exhausted")
            self._runs[run.run_id] = run
        try:
            timer.start()
            worker.start()
        except BaseException:
            timer.cancel()
            with self._runs_lock:
                self._runs.pop(run.run_id, None)
            self._worker_slots.release()
            raise
        return self._public_run(run)

    def _make_run_capacity_locked(self) -> bool:
        while len(self._runs) >= _MAX_ACTION_RUNS:
            terminal_id = next(
                (
                    run_id
                    for run_id, record in self._runs.items()
                    if record.status not in {"queued", "running"}
                ),
                None,
            )
            if terminal_id is None:
                return False
            record = self._runs.pop(terminal_id)
            if record.timer is not None:
                record.timer.cancel()
                record.timer = None
        return True

    @staticmethod
    def _finish_run_locked(
        run: _SetupActionRun,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if run.status not in {"queued", "running"}:
            return
        run.status = status
        run.result = result
        run.error = error
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    def _timeout_run(self, run_id: str) -> None:
        with self._runs_lock:
            run = self._runs.get(run_id)
            if run is None or run.status not in {"queued", "running"}:
                return
            run.cancel_event.set()
            self._finish_run_locked(
                run, "timed_out", error="setup action deadline exceeded"
            )

    def _execute_action(self, run, handler, configuration, redactions) -> None:
        try:
            with self._runs_lock:
                if run.status != "queued":
                    return
                run.status = "running"
            value = handler(
                SetupActionContext(
                    plugin_id=run.plugin_id,
                    configuration=configuration,
                    _cancel_event=run.cancel_event,
                )
            )
            if not isinstance(value, Mapping):
                raise PluginConfigurationError("setup action result must be an object")
            public = _bounded_public_value(value, redactions=redactions)
            encoded = json.dumps(public, ensure_ascii=False).encode("utf-8")
            if len(encoded) > _MAX_ACTION_RESULT_BYTES:
                raise PluginConfigurationError("setup action output exceeded limit")
            with self._runs_lock:
                if run.status in {"cancelled", "timed_out"}:
                    return
                self._finish_run_locked(run, "succeeded", result=public)
        except Exception:
            with self._runs_lock:
                if run.status in {"cancelled", "timed_out"}:
                    return
                # Plugin exceptions may embed credentials or remote payloads.
                # Keep the public diagnostic stable and credential-free.
                self._finish_run_locked(run, "failed", error="setup action failed")
        finally:
            self._worker_slots.release()

    def _expire(self, run: _SetupActionRun) -> None:
        if run.status in {"queued", "running"} and time.monotonic() >= run.deadline:
            run.cancel_event.set()
            self._finish_run_locked(
                run, "timed_out", error="setup action deadline exceeded"
            )

    def _require_run_profile(self, run: _SetupActionRun) -> None:
        if run.profile_id != self._profile_id():
            raise PluginConfigurationError(
                "setup action run belongs to a different profile"
            )

    def action_status(self, run_id: str) -> dict[str, Any]:
        with self._runs_lock:
            run = self._runs.get(run_id)
            if run is None:
                raise PluginConfigurationError("setup action run not found")
            self._require_run_profile(run)
            self._expire(run)
            return self._public_run(run)

    def cancel_action(self, run_id: str) -> dict[str, Any]:
        with self._runs_lock:
            run = self._runs.get(run_id)
            if run is None:
                raise PluginConfigurationError("setup action run not found")
            self._require_run_profile(run)
            if run.status in {"queued", "running"}:
                run.cancel_event.set()
                self._finish_run_locked(run, "cancelled")
            return self._public_run(run)

    @staticmethod
    def _public_run(run: _SetupActionRun) -> dict[str, Any]:
        result: dict[str, Any] = {
            "run_id": run.run_id,
            "plugin_id": run.plugin_id,
            "action": run.action,
            "status": run.status,
        }
        if run.result is not None:
            result["result"] = run.result
        if run.error is not None:
            result["error"] = run.error
        return result


def _runtime_configuration_for_context(
    manifest: Any, manager: Any
) -> PluginRuntimeConfiguration:
    """Resolve one context's own values from the current discovered generation."""

    unavailable = "plugin runtime configuration unavailable"
    try:
        profile_id = PluginConfigurationService._profile_id()
        plugin_id = manifest.key or manifest.name
        loaded = manager._plugins.get(plugin_id)
        if (
            manager._discovered is not True
            or manager._discovery_profile_id != profile_id
            or loaded is None
            or loaded.manifest is not manifest
            or loaded.module is None
            or loaded.enabled is not True
            or loaded.error is not None
            or manifest.configuration is None
        ):
            raise PluginConfigurationError(unavailable)

        service = PluginConfigurationService(manager)
        if not service._is_enabled(loaded):
            raise PluginConfigurationError(unavailable)
        resolved, invalid = service._resolved(plugin_id, loaded)
        if invalid:
            raise PluginConfigurationError(unavailable)

        settings: dict[str, Any] = {}
        secrets: dict[str, Any] = {}
        for field in manifest.configuration.fields:
            if field.id not in resolved:
                continue
            target = secrets if field.storage is FieldStorage.SECRET else settings
            target[field.id] = resolved[field.id]
        return PluginRuntimeConfiguration(settings, secrets)
    except PluginConfigurationError:
        raise
    except Exception:
        raise PluginConfigurationError(unavailable) from None


_configuration_service: PluginConfigurationService | None = None


def get_plugin_configuration_service() -> PluginConfigurationService:
    global _configuration_service
    if _configuration_service is None:
        _configuration_service = PluginConfigurationService()
    return _configuration_service


@dataclass(frozen=True, slots=True)
class ConnectorCapabilitySnapshot:
    """Credential-free connector facts for one active profile generation."""

    ready_services: frozenset[str]
    available_tools: frozenset[str]
    fingerprint: str
    _service_fingerprints: tuple[tuple[str, str], ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def scoped_fingerprint(
        self,
        required_services: frozenset[str],
        required_tools: frozenset[str],
    ) -> str:
        """Bind only one workflow's connector service and tool identities."""

        if (
            len(required_services) > _MAX_FIELDS
            or len(required_tools) > _MAX_FIELDS * 8
            or any(
                not isinstance(value, str) or not value or len(value) > _MAX_TEXT
                for value in (*required_services, *required_tools)
            )
        ):
            raise PluginConfigurationError("connector capability scope exceeded limit")
        by_service = dict(self._service_fingerprints)
        encoded = json.dumps(
            {
                "schema_version": 1,
                "services": [
                    [service_id, by_service.get(service_id, "")]
                    for service_id in sorted(required_services)
                ],
                "tools": sorted(required_tools),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _connector_profile_scope(profile: str | None):
    requested = (profile or "").strip()
    if not requested or requested.lower() == "current":
        yield
        return

    from hermes_cli.profiles import (
        get_profile_dir,
        normalize_profile_name,
        profile_exists,
    )
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    canonical = normalize_profile_name(requested)
    if not profile_exists(canonical):
        raise FileNotFoundError(f"profile does not exist: {canonical}")
    token = set_hermes_home_override(str(get_profile_dir(canonical)))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _configuration_value_identity(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _connector_capacity_exceeded_snapshot() -> ConnectorCapabilitySnapshot:
    fingerprint = hashlib.sha256(
        b'{"schema_version":1,"state":"capacity_exceeded"}'
    ).hexdigest()
    return ConnectorCapabilitySnapshot(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint=fingerprint,
    )


def connector_capability_snapshot(
    profile: str | None = None,
) -> ConnectorCapabilitySnapshot:
    """Return immutable admission facts without importing disabled plugin code.

    Static descriptors establish the configurable service inventory. Runtime
    registrations are accepted only from the plugin-manager generation that
    belongs to the selected profile. Non-secret values contribute only their
    one-way canonical identity; credentials contribute presence booleans.
    """

    with _connector_profile_scope(profile):
        from hermes_cli.plugins import (
            LoadedPlugin,
            PluginStaticInventoryCapacityError,
            get_plugin_manager,
        )
        from tools.registry import registry

        manager = get_plugin_manager()
        service = get_plugin_configuration_service()
        profile_id = service._profile_id()
        generation_current = (
            getattr(manager, "_discovery_profile_id", None) == profile_id
        )
        registered_tool_names = registry.get_all_tool_names()
        try:
            static_inventory = manager.static_plugin_inventory(
                max_visits=_MAX_CONNECTOR_INVENTORY_VISITS
            )
        except PluginStaticInventoryCapacityError:
            return _connector_capacity_exceeded_snapshot()
        runtime_inventory = manager.loaded_plugins() if generation_current else ()
        if (
            len(registered_tool_names) > _MAX_CONNECTOR_TOOLS
            or len(static_inventory) > _MAX_CONNECTOR_INVENTORY
            or len(runtime_inventory) > _MAX_CONNECTOR_RUNTIME_PLUGINS
        ):
            return _connector_capacity_exceeded_snapshot()
        registered_tools = frozenset(registered_tool_names)
        plugin_tool_names = frozenset(
            name
            for name in getattr(manager, "_plugin_tool_names", ())
            if isinstance(name, str)
        )
        available_tools = set(
            registered_tools
            if generation_current
            else registered_tools.difference(plugin_tool_names)
        )
        runtime_by_id = {
            loaded.manifest.key or loaded.manifest.name: loaded
            for loaded in runtime_inventory
        }

        ready_services: set[str] = set()
        identities: list[dict[str, object]] = []
        manifests = sorted(
            (
                manifest
                for manifest in static_inventory
                if manifest.configuration is not None
            ),
            key=lambda manifest: manifest.key or manifest.name,
        )
        for manifest in manifests:
            plugin_id = manifest.key or manifest.name
            descriptor_loaded = LoadedPlugin(manifest=manifest)
            enabled = service._is_enabled(descriptor_loaded)
            resolved, invalid = service._resolved(plugin_id, descriptor_loaded)
            runtime_loaded = runtime_by_id.get(plugin_id)
            runtime_ready = bool(
                runtime_loaded is not None
                and getattr(runtime_loaded, "enabled", False)
                and getattr(runtime_loaded, "error", None) is None
            )
            registered_for_plugin = sorted(
                name
                for name in (
                    getattr(runtime_loaded, "tools_registered", ())
                    if runtime_loaded is not None
                    else ()
                )
                if isinstance(name, str) and name in registered_tools
            )
            readiness = (
                service._readiness_for_loaded(
                    descriptor_loaded,
                    manager.setup_action_registrations(plugin_id),
                )
                if enabled and runtime_ready
                else {"ready": False}
            )
            ready = readiness.get("ready") is True
            if ready:
                ready_services.add(plugin_id)

            settings_identity: dict[str, str] = {}
            secret_presence: dict[str, bool] = {}
            for field in manifest.configuration.fields:
                if field.storage is FieldStorage.SECRET:
                    secret_presence[field.id] = field.id in resolved
                elif field.id in resolved:
                    settings_identity[field.id] = _configuration_value_identity(
                        resolved[field.id]
                    )
            identities.append({
                "plugin_id": plugin_id,
                "enabled": enabled,
                "runtime_current": runtime_ready,
                "ready": ready,
                "invalid_fields": sorted(invalid),
                "settings": settings_identity,
                "secrets": secret_presence,
                "registered_tools": registered_for_plugin,
            })

        service_fingerprints = tuple(
            (
                str(identity["plugin_id"]),
                hashlib.sha256(
                    json.dumps(
                        identity,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest(),
            )
            for identity in identities
        )
        fingerprint_bytes = json.dumps(
            {"schema_version": 1, "plugins": identities},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return ConnectorCapabilitySnapshot(
            ready_services=frozenset(ready_services),
            available_tools=frozenset(available_tools),
            fingerprint=hashlib.sha256(fingerprint_bytes).hexdigest(),
            _service_fingerprints=service_fingerprints,
        )
