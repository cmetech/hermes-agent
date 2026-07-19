"""Single bounded sanitizer for workflow operator projections."""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Mapping


_SECRET_KEY = re.compile(
    r"(?i)(secret|password|token|authorization|api[_-]?key|credential|reasoning|prompt|return[_-]?route)"
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PORTABLE_INPUT_INVALID = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_DEVICE_NAME = re.compile(
    r"(?i)^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$"
)
_PORTABLE_COMPONENT_MAX_UNITS = 255
_TEXT_INPUT_SUFFIX = ".txt"
_PROJECTION_MAX_CHARS = 16_384
_TRUNCATION_SUFFIX = "…[TRUNCATED]"


def workflow_input_name_is_portable(name: object, *, max_length: int = 128) -> bool:
    """Return whether a name is one portable filename segment on every host OS."""
    if not isinstance(name, str):
        return False
    component = name + _TEXT_INPUT_SUFFIX
    try:
        utf8_units = len(component.encode("utf-8"))
        utf16_units = len(component.encode("utf-16-le")) // 2
    except UnicodeError:
        return False
    return (
        bool(name.strip())
        and len(name) <= max_length
        and utf8_units <= _PORTABLE_COMPONENT_MAX_UNITS
        and utf16_units <= _PORTABLE_COMPONENT_MAX_UNITS
        and name not in {".", ".."}
        and not name.endswith((".", " "))
        and _PORTABLE_INPUT_INVALID.search(name) is None
        and _CONTROL.search(name) is None
        and _WINDOWS_DEVICE_NAME.fullmatch(name) is None
    )


def workflow_input_names_are_portable(names: object) -> bool:
    """Reject non-portable names and case-insensitive component collisions."""
    try:
        iterator = iter(names)
    except TypeError:
        return False
    seen: set[str] = set()
    for name in iterator:
        if not workflow_input_name_is_portable(name):
            return False
        folded = name.casefold()
        if folded in seen:
            return False
        seen.add(folded)
    return True


def projection_key_is_secret(key: str) -> bool:
    """Return whether a projection key names operator-sensitive content."""
    return bool(_SECRET_KEY.search(key))


def sanitize_text(value: str, *, max_chars: int = 16_384) -> tuple[str, bool]:
    cleaned = _CONTROL.sub("�", _ANSI.sub("", value))
    if len(cleaned) <= max_chars:
        return cleaned, False
    return cleaned[:max_chars], True


def sanitize_evidence_bytes(
    value: bytes, *, max_chars: int = 16_384
) -> tuple[str, bool]:
    """Decode and sanitize untrusted evidence through the shared text policy."""
    return sanitize_text(value.decode("utf-8", errors="replace"), max_chars=max_chars)


def sanitize_projection(value: object, *, key: str = "", depth: int = 0) -> object:
    if depth > 12:
        return "[TRUNCATED_DEPTH]"
    if projection_key_is_secret(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child): sanitize_projection(item, key=str(child), depth=depth + 1)
            for child, item in list(value.items())[:200]
            if str(child).lower()
            not in {"operator_scope_digest", "idempotency_key_digest"}
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_projection(item, key=key, depth=depth + 1) for item in value[:200]
        ]
    if isinstance(value, str):
        cleaned, truncated = sanitize_text(value, max_chars=_PROJECTION_MAX_CHARS)
        if key.lower() == "transition_key" and ":gateway:" in cleaned:
            cleaned = cleaned.partition(":gateway:")[0] + ":gateway:opaque"
        if key.lower() in {"path", "source_path", "run_directory", "relative_path"}:
            cleaned = PurePath(cleaned).name
        if truncated:
            cleaned = cleaned[: _PROJECTION_MAX_CHARS - len(_TRUNCATION_SUFFIX)]
            return cleaned + _TRUNCATION_SUFFIX
        return cleaned
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_projection(str(value), key=key, depth=depth + 1)


__all__ = [
    "projection_key_is_secret",
    "sanitize_evidence_bytes",
    "sanitize_projection",
    "sanitize_text",
    "workflow_input_name_is_portable",
    "workflow_input_names_are_portable",
]
