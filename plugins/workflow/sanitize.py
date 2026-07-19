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
    if _SECRET_KEY.search(key):
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
        cleaned, truncated = sanitize_text(value, max_chars=16_384)
        if key.lower() == "transition_key" and ":gateway:" in cleaned:
            cleaned = cleaned.partition(":gateway:")[0] + ":gateway:opaque"
        if key.lower() in {"path", "source_path", "run_directory", "relative_path"}:
            cleaned = PurePath(cleaned).name
        return cleaned + ("…[TRUNCATED]" if truncated else "")
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_projection(str(value), key=key, depth=depth + 1)


__all__ = ["sanitize_evidence_bytes", "sanitize_projection", "sanitize_text"]
