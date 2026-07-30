"""Behavioral contract for bounded, provider-agnostic structured output."""

from __future__ import annotations

import dataclasses
import builtins
import hashlib
import json
from collections.abc import Mapping
import sys

import pytest

import agent.structured_output as structured_output
from agent.structured_output import (
    StructuredOutputError,
    StructuredOutputSchema,
    normalize_schema,
)


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def _schema_with_description(byte_count: int) -> dict[str, object]:
    return {
        "$schema": DRAFT_2020_12,
        "description": "x" * byte_count,
    }


def test_normalize_schema_canonicalizes_equivalent_draft_2020_12_schemas() -> None:
    first = normalize_schema(
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["answer"],
        }
    )
    second = normalize_schema(
        {
            "required": ["answer"],
            "properties": {"count": {"type": "integer"}, "answer": {"type": "string"}},
            "type": "object",
        }
    )

    assert first.dialect == DRAFT_2020_12
    assert first.canonical_schema["$schema"] == DRAFT_2020_12
    assert first.canonical_schema_bytes == second.canonical_schema_bytes
    assert first.schema_fingerprint == second.schema_fingerprint


def test_normalize_schema_returns_a_deeply_immutable_value_object() -> None:
    schema = normalize_schema(
        {
            "type": "object",
            "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
        }
    )

    assert isinstance(schema, StructuredOutputSchema)
    assert isinstance(schema.canonical_schema, Mapping)
    with pytest.raises(TypeError):
        schema.canonical_schema["type"] = "array"  # type: ignore[index]
    with pytest.raises(TypeError):
        schema.canonical_schema["properties"]["answer"]["type"] = "number"  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        schema.dialect = "changed"  # type: ignore[misc]


def test_normalize_schema_accepts_exactly_65536_canonical_schema_bytes() -> None:
    empty = _schema_with_description(0)
    fixed_bytes = len(
        json.dumps(empty, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    schema = normalize_schema(_schema_with_description(65_536 - fixed_bytes))

    assert len(schema.canonical_schema_bytes) == 65_536


def test_normalize_schema_rejects_more_than_65536_canonical_schema_bytes() -> None:
    empty = _schema_with_description(0)
    fixed_bytes = len(
        json.dumps(empty, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )

    with pytest.raises(StructuredOutputError, match="schema.*bytes"):
        normalize_schema(_schema_with_description(65_537 - fixed_bytes))


def test_normalize_schema_accepts_schema_depth_32_and_rejects_33() -> None:
    accepted: dict[str, object] = {"type": "null"}
    for _ in range(31):
        accepted = {"items": accepted}
    rejected: dict[str, object] = {"type": "null"}
    for _ in range(32):
        rejected = {"items": rejected}

    normalize_schema(accepted)
    with pytest.raises(StructuredOutputError, match="depth"):
        normalize_schema(rejected)


def test_normalize_schema_accepts_4096_schema_nodes_and_rejects_4097() -> None:
    normalize_schema({"allOf": [{} for _ in range(4_095)]})

    with pytest.raises(StructuredOutputError, match="nodes"):
        normalize_schema({"allOf": [{} for _ in range(4_096)]})


def test_normalize_schema_accepts_1024_properties_and_rejects_1025() -> None:
    properties = {f"field_{index}": {"type": "string"} for index in range(1_024)}
    normalize_schema({"type": "object", "properties": properties})

    properties["one_too_many"] = {"type": "string"}
    with pytest.raises(StructuredOutputError, match="properties"):
        normalize_schema({"type": "object", "properties": properties})


def test_normalize_schema_accepts_256_local_refs_and_rejects_257() -> None:
    definitions = {f"item_{index}": {"type": "string"} for index in range(257)}
    accepted = {
        "$defs": definitions,
        "allOf": [{"$ref": f"#/$defs/item_{index}"} for index in range(256)],
    }
    rejected = {
        "$defs": definitions,
        "allOf": [{"$ref": f"#/$defs/item_{index}"} for index in range(257)],
    }

    normalize_schema(accepted)
    with pytest.raises(StructuredOutputError, match="refs"):
        normalize_schema(rejected)


def test_normalize_schema_enforces_regex_byte_and_total_regex_bounds() -> None:
    normalize_schema({"type": "string", "pattern": "a" * 1_024})
    with pytest.raises(StructuredOutputError, match="regex"):
        normalize_schema({"type": "string", "pattern": "a" * 1_025})

    properties = {
        f"field_{index}": {"type": "string", "pattern": "a" * 1_024}
        for index in range(16)
    }
    normalize_schema({"type": "object", "properties": properties})
    properties["one_too_many"] = {"type": "string", "pattern": "a"}
    with pytest.raises(StructuredOutputError, match="regex"):
        normalize_schema({"type": "object", "properties": properties})


def test_normalize_schema_compiles_pattern_and_pattern_property_regexes() -> None:
    with pytest.raises(StructuredOutputError, match="regex"):
        normalize_schema({"type": "string", "pattern": "["})
    with pytest.raises(StructuredOutputError, match="regex"):
        normalize_schema({"patternProperties": {"[": {"type": "string"}}})


def test_normalize_schema_accepts_1024_enum_values_and_rejects_1025() -> None:
    normalize_schema({"enum": list(range(1_024))})

    with pytest.raises(StructuredOutputError, match="enum"):
        normalize_schema({"enum": list(range(1_025))})


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_normalize_schema_rejects_nonfinite_numbers(number: float) -> None:
    with pytest.raises(StructuredOutputError, match="finite"):
        normalize_schema({"minimum": number})


@pytest.mark.parametrize("keyword", ["maxItems", "maxLength", "minProperties"])
def test_normalize_schema_rejects_booleans_for_integer_bounds(keyword: str) -> None:
    with pytest.raises(StructuredOutputError, match="integer"):
        normalize_schema({keyword: True})


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.test/schema"},
        {"$ref": "#/$defs/missing", "$defs": {}},
        {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"},
        {"$dynamicRef": "#/$defs/value", "$defs": {"value": {"type": "string"}}},
        {"$id": "https://example.test/schema", "type": "string"},
        {"$anchor": "value", "type": "string"},
        {"$dynamicAnchor": "value", "type": "string"},
    ],
)
def test_normalize_schema_rejects_nonlocal_or_scope_changing_references(
    schema: dict[str, object],
) -> None:
    with pytest.raises(StructuredOutputError, match="(?i)ref|scope|anchor|\$id"):
        normalize_schema(schema)


def _request(schema: Mapping[str, object] | None = None) -> object:
    return structured_output.StructuredOutputRequest(
        schema=normalize_schema(schema or {"type": "object"}),
        strategy=structured_output.StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
        adapter_version=1,
    )


def test_parse_validate_canonicalize_requires_one_complete_json_value() -> None:
    request = _request()
    invalid_responses = [
        "Here is the result: {}",
        "```json\n{}\n```",
        "{} {}",
        "{} trailing",
        "NaN",
        "Infinity",
        "I cannot comply with that request.",
        '{"answer":',
    ]

    for response in invalid_responses:
        with pytest.raises(StructuredOutputError):
            structured_output.parse_validate_canonicalize(response, request)


def test_parse_validate_canonicalize_rejects_outputs_over_500000_bytes() -> None:
    request = _request({"type": "string"})

    with pytest.raises(StructuredOutputError, match="output.*bytes"):
        structured_output.parse_validate_canonicalize('"' + "x" * 500_001 + '"', request)


def test_parse_validate_canonicalize_validates_then_canonicalizes_response() -> None:
    request = _request(
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["answer", "count"],
            "additionalProperties": False,
        }
    )

    value = structured_output.parse_validate_canonicalize(
        ' \n {"count":1,"answer":"é"}\t', request
    )

    assert isinstance(value, structured_output.StructuredOutputValue)
    assert value.canonical_bytes == b'{"answer":"\xc3\xa9","count":1}'
    assert value.sha256 == hashlib.sha256(value.canonical_bytes).hexdigest()
    assert value.value == {"answer": "é", "count": 1}
    with pytest.raises(TypeError):
        value.value["answer"] = "changed"  # type: ignore[index]


def test_parse_validate_canonicalize_reports_schema_validation_failures() -> None:
    request = _request({"type": "object", "required": ["answer"]})

    with pytest.raises(StructuredOutputError, match="answer"):
        structured_output.parse_validate_canonicalize("{}", request)


def test_validation_summary_is_deterministic_and_utf8_bounded() -> None:
    assert structured_output.validation_summary(["zeta", "alpha"]) == "alpha\nzeta"

    summary = structured_output.validation_summary(["é" * 10_000])
    assert len(summary.encode("utf-8")) <= 16_384


def test_validator_is_required_only_when_validation_is_requested(monkeypatch) -> None:
    request = _request()
    original_import = builtins.__import__

    def missing_jsonschema(name: str, *args: object, **kwargs: object) -> object:
        if name == "jsonschema":
            raise ModuleNotFoundError("No module named 'jsonschema'")
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "jsonschema", raising=False)
    monkeypatch.setattr(builtins, "__import__", missing_jsonschema)

    assert normalize_schema({"type": "object"}).dialect == DRAFT_2020_12
    with pytest.raises(structured_output.StructuredOutputValidatorUnavailable):
        structured_output.parse_validate_canonicalize("{}", request)
