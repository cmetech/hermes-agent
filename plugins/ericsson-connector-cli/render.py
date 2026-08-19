"""Stable, bounded output normalization for Ericsson connector commands."""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "ericsson.connector-cli/v1"
MAX_DEPTH = 16
MAX_ITEMS = 10_000
MAX_STRING = 256 * 1024
MAX_WARNINGS = 100
MAX_META_ITEMS = 100
MAX_HUMAN_ROWS = 50
MAX_HUMAN_LINE = 512
MAX_HUMAN_BYTES = 32 * 1024

_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_ESCAPE = re.compile(r"\x1b(?:.|$)", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "admission",
        "authority",
        "authorization",
        "bearer",
        "certificate",
        "cookie",
        "config",
        "configuration",
        "credential",
        "credentials",
        "invocation",
        "password",
        "pat",
        "secret",
        "token",
    }
)
_SENSITIVE_COMPACT_EDGES = _SENSITIVE_KEY_WORDS - {"pat"}
_SENSITIVE_COMPACT_MARKERS = frozenset(
    {
        "accesskey",
        "apikey",
        "clientkey",
        "privatekey",
        "secretkey",
    }
)


class InvalidProviderResult(ValueError):
    """Raised when a provider result cannot enter the public CLI contract."""


def sanitize_terminal(value: str) -> str:
    """Remove terminal control sequences and non-printing control bytes."""
    value = _OSC.sub("", value)
    value = _CSI.sub("", value)
    value = _ESCAPE.sub("", value)
    return _CONTROL.sub("", value)


def _sensitive_key(key: str) -> bool:
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", expanded)
    words = {
        word for word in re.split(r"[^a-z0-9]+", expanded.casefold()) if word
    }
    compact = re.sub(r"[^a-z0-9]", "", expanded.casefold())
    return (
        bool(words & _SENSITIVE_KEY_WORDS)
        or any(marker in compact for marker in _SENSITIVE_COMPACT_MARKERS)
        or any(
            compact.startswith(marker) or compact.endswith(marker)
            for marker in _SENSITIVE_COMPACT_EDGES
        )
    )


def _safe_value(
    value: Any,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
    ancestors: set[int] | None = None,
) -> Any:
    if depth > MAX_DEPTH:
        raise InvalidProviderResult()
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_ITEMS:
        raise InvalidProviderResult()
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise InvalidProviderResult()
        return value
    if type(value) is str:
        if len(value) > MAX_STRING:
            raise InvalidProviderResult()
        return sanitize_terminal(value)
    if type(value) not in {list, dict}:
        raise InvalidProviderResult()
    if ancestors is None:
        ancestors = set()
    identity = id(value)
    if identity in ancestors:
        raise InvalidProviderResult()
    ancestors.add(identity)
    try:
        if type(value) is list:
            return [
                _safe_value(
                    item,
                    depth=depth + 1,
                    counter=counter,
                    ancestors=ancestors,
                )
                for item in value
            ]
        copied = {}
        for key, item in value.items():
            if type(key) is not str or _sensitive_key(key):
                raise InvalidProviderResult()
            copied[key] = _safe_value(
                item,
                depth=depth + 1,
                counter=counter,
                ancestors=ancestors,
            )
        return copied
    finally:
        ancestors.remove(identity)


def _bounded_text(value: object, *, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise InvalidProviderResult()
    cleaned = sanitize_terminal(value)
    cleaned = re.sub(r"[\r\n\t]+", " ", cleaned).strip()
    if not cleaned:
        raise InvalidProviderResult()
    return cleaned


def _base_envelope(*, connector: str, operation: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "connector": connector,
        "operation": operation,
        "mode": mode,
    }


def success_envelope(
    *,
    connector: str,
    operation: str,
    mode: str,
    data: Any,
    warnings: Any = None,
    meta: Any = None,
) -> dict[str, Any]:
    safe_data = _safe_value(data)
    safe_warnings = _safe_value([] if warnings is None else warnings)
    safe_meta = _safe_value({} if meta is None else meta)
    if type(safe_warnings) is not list or len(safe_warnings) > MAX_WARNINGS:
        raise InvalidProviderResult()
    if type(safe_meta) is not dict or len(safe_meta) > MAX_META_ITEMS:
        raise InvalidProviderResult()
    envelope = _base_envelope(
        connector=connector, operation=operation, mode=mode
    )
    envelope.update(
        {
            "ok": True,
            "data": safe_data,
            "warnings": safe_warnings,
            "meta": safe_meta,
        }
    )
    return envelope


def error_envelope(
    *,
    connector: str,
    operation: str,
    mode: str,
    category: str,
    message: str,
    remediation: str | None = None,
) -> dict[str, Any]:
    if type(category) is not str or _CATEGORY.fullmatch(category) is None:
        raise InvalidProviderResult()
    error = {
        "category": category,
        "message": _bounded_text(message, maximum=512),
    }
    if remediation is not None:
        error["remediation"] = _bounded_text(remediation, maximum=1024)
    envelope = _base_envelope(
        connector=connector, operation=operation, mode=mode
    )
    envelope["error"] = error
    return envelope


def malformed_envelope(
    *, connector: str, operation: str, mode: str
) -> dict[str, Any]:
    return error_envelope(
        connector=connector,
        operation=operation,
        mode=mode,
        category="transient",
        message="Connector returned an invalid response.",
        remediation="Retry the command after the connector is available.",
    )


_MISSING = object()


def _diagnostic_value(
    provider: Mapping[str, Any],
    data: dict[str, Any],
    name: str,
    expected_type: type,
    default: Any,
) -> Any:
    explicit = provider[name] if name in provider else _MISSING
    legacy = data.pop(name) if name in data else _MISSING
    if explicit is _MISSING and legacy is _MISSING:
        return default
    safe_explicit = (
        _MISSING if explicit is _MISSING else _safe_value(explicit)
    )
    safe_legacy = _MISSING if legacy is _MISSING else _safe_value(legacy)
    if (
        safe_explicit is not _MISSING
        and type(safe_explicit) is not expected_type
    ):
        raise InvalidProviderResult()
    if (
        safe_legacy is not _MISSING
        and type(safe_legacy) is not expected_type
    ):
        raise InvalidProviderResult()
    if (
        safe_explicit is not _MISSING
        and safe_legacy is not _MISSING
        and safe_explicit != safe_legacy
    ):
        raise InvalidProviderResult()
    return safe_explicit if safe_explicit is not _MISSING else safe_legacy


def _success_components(
    provider: Mapping[str, Any],
) -> tuple[Any, list[Any], dict[str, Any]]:
    data = provider["result"]
    if type(data) is dict:
        data = dict(data)
        warnings = _diagnostic_value(provider, data, "warnings", list, [])
        meta = _diagnostic_value(provider, data, "meta", dict, {})
    else:
        warnings = _diagnostic_value(provider, {}, "warnings", list, [])
        meta = _diagnostic_value(provider, {}, "meta", dict, {})
    return data, warnings, meta


def normalize_provider_result(
    result: object, *, connector: str, operation: str, mode: str
) -> tuple[dict[str, Any], int]:
    """Project one provider envelope into the stable public result contract."""
    try:
        if not isinstance(result, Mapping) or type(result.get("success")) is not bool:
            raise InvalidProviderResult()
        if result["success"]:
            if "result" not in result:
                raise InvalidProviderResult()
            data, warnings, meta = _success_components(result)
            envelope = success_envelope(
                connector=connector,
                operation=operation,
                mode=mode,
                data=data,
                warnings=warnings,
                meta=meta,
            )
            return envelope, 0
        error = result.get("error")
        if not isinstance(error, Mapping):
            raise InvalidProviderResult()
        envelope = error_envelope(
            connector=connector,
            operation=operation,
            mode=mode,
            category=error.get("category"),
            message=error.get("message"),
            remediation=error.get("remediation"),
        )
        category = envelope["error"]["category"]
        if category == "invalid_input":
            return envelope, 2
        if category in {
            "invalid_configuration",
            "authentication",
            "readiness",
            "unavailable",
            "unsupported",
            "not_configured",
        }:
            return envelope, 3
        if category == "write_ambiguous":
            return envelope, 5
        return envelope, 4
    except Exception:
        # The provider boundary is hostile: no malformed accessor or custom
        # object may escape as a traceback or object representation.
        return malformed_envelope(
            connector=connector, operation=operation, mode=mode
        ), 4


def _compact(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _bounded_line(value: Any) -> str:
    text = sanitize_terminal(_compact(value)).replace("\r", "")
    if len(text) > MAX_HUMAN_LINE:
        return text[: MAX_HUMAN_LINE - 1] + "…"
    return text


def _human_rows(data: Any) -> tuple[list[str], bool]:
    if type(data) is list:
        source = data
    elif type(data) is dict:
        source = [{key: data[key]} for key in sorted(data)]
    else:
        source = [data]
    truncated = len(source) > MAX_HUMAN_ROWS
    lines = [_bounded_line(item) for item in source[:MAX_HUMAN_ROWS]]
    used = 0
    bounded = []
    for line in lines:
        encoded = (line + "\n").encode("utf-8")
        if used + len(encoded) > MAX_HUMAN_BYTES:
            truncated = True
            break
        bounded.append(line)
        used += len(encoded)
    return bounded, truncated


def emit(envelope: Mapping[str, Any], *, json_mode: bool) -> None:
    """Emit one already-normalized envelope without decorative output."""
    if json_mode:
        sys.stdout.write(_compact(envelope) + "\n")
        return
    if envelope["ok"]:
        lines, truncated = _human_rows(envelope["data"])
        for line in lines:
            sys.stdout.write(line + "\n")
        for warning in envelope["warnings"]:
            sys.stderr.write(f"Warning: {_bounded_line(warning)}\n")
        if envelope["meta"]:
            sys.stderr.write(f"Metadata: {_bounded_line(envelope['meta'])}\n")
        if truncated:
            sys.stderr.write("Output truncated to the terminal display limit.\n")
        return
    error = envelope["error"]
    sys.stderr.write(error["message"] + "\n")
    remediation = error.get("remediation")
    if remediation:
        sys.stderr.write(f"Remediation: {remediation}\n")


__all__ = [
    "InvalidProviderResult",
    "SCHEMA_VERSION",
    "emit",
    "error_envelope",
    "malformed_envelope",
    "normalize_provider_result",
    "sanitize_terminal",
    "success_envelope",
]
