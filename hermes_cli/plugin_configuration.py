"""Static, side-effect-free plugin configuration descriptors.

Plugins may point ``config_schema`` in ``plugin.yaml`` at a JSON file below
their plugin root.  This module reads that file as inert data: it never imports
plugin code and it deliberately supports no executable setup action metadata.
Malformed or unsafe descriptors are omitted (``None``) so discovery can keep
listing the plugin without trusting the descriptor.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


_MAX_DESCRIPTOR_BYTES = 64 * 1024
_MAX_FIELDS = 64
_MAX_SETUP_ACTIONS = 16
_MAX_TEXT = 4096
_MAX_LABEL = 256
_MAX_ID = 64
_MAX_PATTERN = 256
_MAX_ENUM_VALUES = 64
_MAX_PLATFORMS = 16

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
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _InvalidDescriptor("expected bounded non-empty text")
    return value


def _identifier(value: Any) -> str:
    value = _bounded_text(value, maximum=_MAX_ID)
    if _ID_PATTERN.fullmatch(value) is None:
        raise _InvalidDescriptor("invalid identifier")
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
        try:
            re.compile(pattern)
        except re.error as exc:
            raise _InvalidDescriptor("invalid regular expression") from exc

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
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
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
        documentation_url = _bounded_text(documentation_url, maximum=2048)

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
        documentation_url = _bounded_text(documentation_url, maximum=2048)
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
