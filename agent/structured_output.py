"""Bounded, immutable structured-output primitives shared by provider adapters.

This module deliberately has no provider dependency and imports ``jsonschema``
only while validating a completed response.  Workflows can therefore normalize
and fingerprint schemas on lean installs, while a request that needs instance
validation fails closed when the optional dependency is absent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any


DRAFT_2020_12_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_CANONICAL_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 4_096
MAX_SCHEMA_PROPERTIES = 1_024
MAX_LOCAL_REFS = 256
MAX_REGEX_BYTES = 1_024
MAX_TOTAL_REGEX_BYTES = 16_384
MAX_ENUM_VALUES = 1_024
MAX_OUTPUT_BYTES = 500_000
MAX_VALIDATION_DIAGNOSTIC_BYTES = 16_384
STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE = (
    "jsonschema is required; install the Hermes mcp or all extra"
)
STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE = "structured-output schema is invalid"

_INTEGER_BOUND_KEYWORDS = frozenset({
    "maxContains",
    "maxItems",
    "maxLength",
    "maxProperties",
    "minContains",
    "minItems",
    "minLength",
    "minProperties",
})
_SCOPE_CHANGING_KEYWORDS = frozenset({"$anchor", "$dynamicAnchor", "$id"})
_SCHEMA_VALUE_KEYWORDS = frozenset({
    "additionalItems",
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})
_SCHEMA_MAP_KEYWORDS = frozenset({
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_GENERIC_CONTEXT = "generic"
_SCHEMA_CONTEXT = "schema"
_SCHEMA_MAP_CONTEXT = "schema_map"
_SCHEMA_ARRAY_CONTEXT = "schema_array"
_JSON_SCHEMA_TYPES = frozenset({
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
})
_NUMBER_BOUND_KEYWORDS = frozenset({
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maximum",
    "minimum",
    "multipleOf",
})


class StructuredOutputError(ValueError):
    """A bounded structured-output contract could not be satisfied."""


class StructuredOutputValidatorUnavailable(StructuredOutputError):
    """The optional jsonschema validator is required for this request."""


class StructuredOutputSchemaInvalid(StructuredOutputError):
    """A schema fails the bounded Draft 2020-12 contract."""


class StructuredOutputStrategy(str, Enum):
    NATIVE_JSON_SCHEMA = "native_json_schema"
    NATIVE_JSON_MODE = "native_json_mode"
    PROMPT_JSON_SCHEMA = "prompt_json_schema"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class StructuredOutputSchema:
    canonical_schema: Mapping[str, object]
    schema_fingerprint: str
    canonical_schema_bytes: bytes
    dialect: str = DRAFT_2020_12_DIALECT

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "canonical_schema", _freeze_json(self.canonical_schema)
        )


@dataclass(frozen=True, slots=True)
class StructuredOutputRequest:
    schema: StructuredOutputSchema
    strategy: StructuredOutputStrategy
    adapter_version: int
    output_bytes_limit: int = 500_000
    canonicalization_version: int = 1


@dataclass(frozen=True, slots=True)
class StructuredOutputValue:
    value: object
    canonical_bytes: bytes
    sha256: str
    media_type: str = "application/json"
    canonicalization_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value))


def normalize_schema(schema: Mapping[str, object]) -> StructuredOutputSchema:
    """Normalize a bounded Draft 2020-12 schema into immutable canonical JSON."""
    if not isinstance(schema, Mapping):
        raise StructuredOutputError("structured-output schema must be an object")

    source = dict(schema)
    declared_dialect = source.get("$schema", DRAFT_2020_12_DIALECT)
    if declared_dialect != DRAFT_2020_12_DIALECT:
        raise StructuredOutputError("structured-output schema must use Draft 2020-12")
    source["$schema"] = DRAFT_2020_12_DIALECT

    canonical = _copy_and_validate_schema(source)
    try:
        canonical_bytes = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StructuredOutputError("schema must contain JSON values") from exc
    if len(canonical_bytes) > MAX_CANONICAL_SCHEMA_BYTES:
        raise StructuredOutputError("schema exceeds canonical schema bytes limit")

    return StructuredOutputSchema(
        canonical_schema=canonical,
        schema_fingerprint=hashlib.sha256(canonical_bytes).hexdigest(),
        canonical_schema_bytes=canonical_bytes,
    )


def _copy_and_validate_schema(source: dict[str, object]) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[object, object, int, tuple[object, ...], str]] = [
        (source, root, 1, (), _SCHEMA_CONTEXT)
    ]
    node_count = 0
    property_count = 0
    ref_count = 0
    regex_bytes = 0
    refs: list[tuple[tuple[object, ...], str]] = []

    while stack:
        current, copied, depth, path, context = stack.pop()
        node_count += 1
        if node_count > MAX_SCHEMA_NODES:
            raise StructuredOutputError("schema exceeds traversed nodes limit")
        if depth > MAX_SCHEMA_DEPTH:
            raise StructuredOutputError("schema exceeds depth limit")
        if isinstance(current, Mapping):
            assert isinstance(copied, dict)
            for key, value in current.items():
                if not isinstance(key, str):
                    raise StructuredOutputError("schema object keys must be strings")
                child_path = path + (key,)
                child_context = _GENERIC_CONTEXT
                if context == _SCHEMA_CONTEXT:
                    child_context, property_count, ref_count, regex_bytes = (
                        _validate_schema_keyword(
                            key,
                            value,
                            child_path,
                            refs,
                            property_count,
                            ref_count,
                            regex_bytes,
                        )
                    )
                elif context == _SCHEMA_MAP_CONTEXT:
                    child_context = _SCHEMA_CONTEXT
                copied[key] = _copy_schema_value(
                    value, stack, depth, child_path, child_context
                )
        else:
            assert isinstance(current, list)
            assert isinstance(copied, list)
            for index, value in enumerate(current):
                child_path = path + (index,)
                child_context = (
                    _SCHEMA_CONTEXT
                    if context == _SCHEMA_ARRAY_CONTEXT
                    else _GENERIC_CONTEXT
                )
                copied.append(
                    _copy_schema_value(value, stack, depth, child_path, child_context)
                )

    _validate_local_refs(source, refs)
    return root


def _copy_schema_value(
    value: object,
    stack: list[tuple[object, object, int, tuple[object, ...], str]],
    depth: int,
    path: tuple[object, ...],
    context: str,
) -> object:
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        stack.append((value, copied, depth + 1, path, context))
        return copied
    if isinstance(value, list):
        copied_list: list[object] = []
        stack.append((value, copied_list, depth + 1, path, context))
        return copied_list
    if isinstance(value, float) and not math.isfinite(value):
        raise StructuredOutputError("schema numbers must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise StructuredOutputError("schema must contain JSON values")


def _validate_schema_keyword(
    key: str,
    value: object,
    path: tuple[object, ...],
    refs: list[tuple[tuple[object, ...], str]],
    property_count: int,
    ref_count: int,
    regex_bytes: int,
) -> tuple[str, int, int, int]:
    if key == "type":
        valid = (
            isinstance(value, str)
            and value in _JSON_SCHEMA_TYPES
            or isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, str) and item in _JSON_SCHEMA_TYPES for item in value
            )
            and len(set(value)) == len(value)
        )
        if not valid:
            raise StructuredOutputSchemaInvalid(
                STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE
            )
    if key == "required" and (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise StructuredOutputSchemaInvalid(STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE)
    if key in _NUMBER_BOUND_KEYWORDS and (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or (
            isinstance(value, int | float)
            and math.isfinite(value)
            and key == "multipleOf"
            and value <= 0
        )
    ):
        raise StructuredOutputSchemaInvalid(STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE)
    if key in _SCOPE_CHANGING_KEYWORDS:
        raise StructuredOutputError(f"schema {key} changes resolution scope")
    if key == "$dynamicRef":
        raise StructuredOutputError("schema $dynamicRef is unsupported")
    if key in _INTEGER_BOUND_KEYWORDS and (
        isinstance(value, bool) or not isinstance(value, int)
    ):
        raise StructuredOutputError(f"schema {key} must be an integer")
    if key in _INTEGER_BOUND_KEYWORDS and value < 0:
        raise StructuredOutputSchemaInvalid(STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE)
    if key == "properties":
        if not isinstance(value, Mapping):
            raise StructuredOutputError("schema properties must be an object")
        property_count += len(value)
        if property_count > MAX_SCHEMA_PROPERTIES:
            raise StructuredOutputError("schema exceeds properties limit")
    if key == "enum":
        if not isinstance(value, list):
            raise StructuredOutputError("schema enum must be an array")
        if len(value) > MAX_ENUM_VALUES:
            raise StructuredOutputError("schema exceeds enum values limit")
    if key == "$ref":
        if not isinstance(value, str):
            raise StructuredOutputError("schema $ref must be a string")
        ref_count += 1
        if ref_count > MAX_LOCAL_REFS:
            raise StructuredOutputError("schema exceeds local refs limit")
        refs.append((path, value))
    if key == "pattern":
        regex_bytes = _validate_regex(value, regex_bytes)
    if key == "patternProperties":
        if not isinstance(value, Mapping):
            raise StructuredOutputError("schema patternProperties must be an object")
        for pattern in value:
            regex_bytes = _validate_regex(pattern, regex_bytes)
    return _schema_child_context(key), property_count, ref_count, regex_bytes


def _schema_child_context(key: str) -> str:
    if key in _SCHEMA_VALUE_KEYWORDS:
        return _SCHEMA_CONTEXT
    if key in _SCHEMA_MAP_KEYWORDS:
        return _SCHEMA_MAP_CONTEXT
    if key in _SCHEMA_ARRAY_KEYWORDS:
        return _SCHEMA_ARRAY_CONTEXT
    return _GENERIC_CONTEXT


def _validate_regex(value: object, total_bytes: int) -> int:
    if not isinstance(value, str):
        raise StructuredOutputError("schema regex must be a string")
    value_bytes = len(value.encode("utf-8"))
    if (
        value_bytes > MAX_REGEX_BYTES
        or total_bytes + value_bytes > MAX_TOTAL_REGEX_BYTES
    ):
        raise StructuredOutputError("schema exceeds regex bytes limit")
    try:
        re.compile(value)
    except re.error as exc:
        raise StructuredOutputError("schema contains an invalid regex") from exc
    return total_bytes + value_bytes


def _validate_local_refs(
    root: Mapping[str, object], refs: list[tuple[tuple[object, ...], str]]
) -> None:
    edges: dict[str, set[str]] = {}
    for path, reference in refs:
        target = _resolve_local_definition(root, reference)
        source_definition = _definition_for_path(path)
        target_definition = target[0]
        if source_definition is not None:
            edges.setdefault(source_definition, set()).add(target_definition)
    _reject_cyclic_definitions(edges)


def _resolve_local_definition(
    root: Mapping[str, object], reference: str
) -> tuple[str, ...]:
    if not reference.startswith("#/$defs/"):
        raise StructuredOutputError(
            "schema references must be local JSON Pointers below $defs"
        )
    encoded_segments = reference[len("#/") :].split("/")
    segments = tuple(_decode_pointer_segment(segment) for segment in encoded_segments)
    current: object = root
    for segment in segments:
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif (
            isinstance(current, list)
            and segment.isdigit()
            and int(segment) < len(current)
        ):
            current = current[int(segment)]
        else:
            raise StructuredOutputError("schema contains an unresolved local ref")
    return segments[1:]


def _decode_pointer_segment(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def _definition_for_path(path: tuple[object, ...]) -> str | None:
    if len(path) >= 2 and path[0] == "$defs" and isinstance(path[1], str):
        return path[1]
    return None


def _reject_cyclic_definitions(edges: Mapping[str, set[str]]) -> None:
    states: dict[str, int] = {}
    for start in edges:
        if states.get(start, 0):
            continue
        states[start] = 1
        stack: list[tuple[str, Any]] = [(start, iter(edges.get(start, ())))]
        while stack:
            name, children = stack[-1]
            try:
                child = next(children)
            except StopIteration:
                states[name] = 2
                stack.pop()
                continue
            state = states.get(child, 0)
            if state == 1:
                raise StructuredOutputError("schema contains cyclic local refs")
            if state == 0 and child in edges:
                states[child] = 1
                stack.append((child, iter(edges[child])))


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze_json(item) for key, item in value.items()
        })
    if isinstance(value, list) or isinstance(value, tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def require_structured_output_validator(
    schema: Mapping[str, object] | None = None,
    *,
    legacy: bool = False,
) -> Any:
    """Return the required optional validator API or fail with install guidance."""
    try:
        import jsonschema
    except ImportError as exc:
        raise StructuredOutputValidatorUnavailable(
            STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
        ) from exc
    if legacy:
        validator = getattr(jsonschema, "validate", None)
        if not callable(validator) or any(
            not isinstance(getattr(jsonschema, name, None), type)
            or not issubclass(getattr(jsonschema, name), Exception)
            for name in ("SchemaError", "ValidationError")
        ):
            raise StructuredOutputValidatorUnavailable(
                STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
            )
        return jsonschema

    validator_builder = getattr(jsonschema, "Draft202012Validator", None)
    check_schema = getattr(validator_builder, "check_schema", None)
    schema_error = getattr(jsonschema, "SchemaError", None)
    if (
        not callable(validator_builder)
        or not callable(check_schema)
        or not isinstance(schema_error, type)
        or not issubclass(schema_error, Exception)
    ):
        raise StructuredOutputValidatorUnavailable(
            STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
        )
    canonical_schema = _thaw_json(schema) if schema is not None else {}
    try:
        check_schema(canonical_schema)
    except schema_error as exc:
        raise StructuredOutputSchemaInvalid(
            STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE
        ) from exc
    except (AttributeError, ImportError, TypeError) as exc:
        raise StructuredOutputValidatorUnavailable(
            STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
        ) from exc
    try:
        validator = validator_builder(canonical_schema)
    except (AttributeError, ImportError, TypeError) as exc:
        raise StructuredOutputValidatorUnavailable(
            STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
        ) from exc
    if not callable(getattr(validator, "iter_errors", None)):
        raise StructuredOutputValidatorUnavailable(
            STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE
        )
    return validator


def parse_validate_canonicalize(
    response: str,
    request: StructuredOutputRequest,
    *,
    validator: Any | None = None,
) -> StructuredOutputValue:
    """Parse one JSON value, validate it, and return its canonical JSON bytes."""
    if not isinstance(response, str):
        raise StructuredOutputError("structured output response must be text")
    if not isinstance(request, StructuredOutputRequest):
        raise StructuredOutputError("structured output request is invalid")
    if (
        isinstance(request.output_bytes_limit, bool)
        or not isinstance(request.output_bytes_limit, int)
        or request.output_bytes_limit <= 0
    ):
        raise StructuredOutputError("structured output byte limit is invalid")

    output_limit = min(request.output_bytes_limit, MAX_OUTPUT_BYTES)
    if len(response.encode("utf-8")) > output_limit:
        raise StructuredOutputError("output exceeds bytes limit")

    start = 0
    while start < len(response) and response[start] in " \t\r\n":
        start += 1
    try:
        value, end = json.JSONDecoder(
            parse_constant=_reject_nonfinite_constant
        ).raw_decode(response, start)
    except (json.JSONDecodeError, ValueError) as exc:
        raise StructuredOutputError("response is not one complete JSON value") from exc
    if response[end:].strip(" \t\r\n"):
        raise StructuredOutputError("response contains trailing non-JSON content")
    _reject_nonfinite_value(value)

    if validator is None:
        validator = require_structured_output_validator(request.schema.canonical_schema)
    errors = list(validator.iter_errors(value))
    if errors:
        raise StructuredOutputError(validation_summary(errors))
    try:
        canonical_bytes = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StructuredOutputError("response is not JSON-canonicalizable") from exc

    return StructuredOutputValue(
        value=value,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def validation_summary(
    errors: Iterable[object], *, limit_bytes: int = MAX_VALIDATION_DIAGNOSTIC_BYTES
) -> str:
    """Return a deterministic, UTF-8-bounded validator diagnostic."""
    if (
        isinstance(limit_bytes, bool)
        or not isinstance(limit_bytes, int)
        or limit_bytes <= 0
    ):
        raise ValueError("validation diagnostic byte limit must be a positive integer")
    messages = []
    for error in errors:
        if isinstance(error, str):
            messages.append(error)
        else:
            messages.append(_validation_error_metadata(error))
    summary = "\n".join(sorted(messages))
    encoded = summary.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return summary
    marker = b"..."
    truncated = encoded[: max(0, limit_bytes - len(marker))]
    while truncated:
        try:
            return truncated.decode("utf-8") + marker.decode("ascii")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return marker[:limit_bytes].decode("ascii")


def _validation_error_metadata(error: object) -> str:
    validator = getattr(error, "validator", None)
    if validator is None:
        return error.__class__.__name__
    return f"validation failed ({validator})"


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _reject_nonfinite_value(value: object) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise StructuredOutputError("response contains a non-finite JSON number")
        if isinstance(current, Mapping):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


__all__ = [
    "DRAFT_2020_12_DIALECT",
    "MAX_CANONICAL_SCHEMA_BYTES",
    "MAX_ENUM_VALUES",
    "MAX_LOCAL_REFS",
    "MAX_OUTPUT_BYTES",
    "MAX_REGEX_BYTES",
    "MAX_SCHEMA_DEPTH",
    "MAX_SCHEMA_NODES",
    "MAX_SCHEMA_PROPERTIES",
    "MAX_TOTAL_REGEX_BYTES",
    "MAX_VALIDATION_DIAGNOSTIC_BYTES",
    "STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE",
    "STRUCTURED_OUTPUT_SCHEMA_INVALID_MESSAGE",
    "StructuredOutputError",
    "StructuredOutputRequest",
    "StructuredOutputSchema",
    "StructuredOutputStrategy",
    "StructuredOutputSchemaInvalid",
    "StructuredOutputValidatorUnavailable",
    "StructuredOutputValue",
    "normalize_schema",
    "parse_validate_canonicalize",
    "require_structured_output_validator",
    "validation_summary",
]
