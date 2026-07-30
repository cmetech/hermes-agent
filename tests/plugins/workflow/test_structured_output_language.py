"""Workflow-language structured-output normalization contracts."""

from __future__ import annotations

import pytest

from agent.structured_output import normalize_schema
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    read_language_snapshot,
)
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import load_workflow, load_workflow_snapshot


def _archon_workflow(workflow_writer, tmp_path, *, nodes):
    path = workflow_writer(
        tmp_path,
        name="structured-output-language",
        nodes=nodes,
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return path


def test_archon_normalizes_output_format_and_accepts_output_type(
    workflow_writer, tmp_path
):
    package = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path,
            nodes=[
                {
                    "id": "producer",
                    "prompt": "Return a report",
                    "output_type": "report",
                    "output_format": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                    },
                }
            ],
        )
    )

    assert package.language.normalizer_version == 2
    assert package.definition.nodes[0].options["output_format"] == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"answer": {"type": "string"}},
    }
    assert set(package.language.structured_outputs) == {"producer"}
    assert not {
        "archon_output_format_unavailable",
        "archon_output_type_unavailable",
    }.intersection(finding.code for finding in package.compatibility_findings)


def test_version_one_archon_package_keeps_structured_output_unavailability(
    workflow_writer, tmp_path
):
    path = _archon_workflow(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a report",
                "output_type": "report",
                "output_format": {"type": "object"},
            }
        ],
    )
    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
        normalizer_version=1,
    )

    assert package.language.structured_outputs == {}
    assert {
        finding.code for finding in package.compatibility_findings
    } >= {
        "archon_output_format_unavailable",
        "archon_output_type_unavailable",
    }


@pytest.mark.parametrize("version", (True, 1.0, "1", None, 2))
def test_v2_snapshot_rejects_noncanonicalization_versions(version):
    schema = normalize_schema({"type": "object"})
    structured_output = {
        "canonical_schema": dict(schema.canonical_schema),
        "schema_fingerprint": schema.schema_fingerprint,
        "canonicalization_version": version,
    }
    if version is None:
        structured_output.pop("canonicalization_version")
    value = {
        "effective_profile": "archon-2026-07",
        "normalizer_version": 2,
        "normalized_definition_digest": "a" * 64,
        "semantic_fingerprint": "b" * 64,
        "structured_outputs": {"producer": structured_output},
    }

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot(value)

    assert exc.value.code == "workflow_language_snapshot_invalid"


@pytest.mark.parametrize(
    "schema",
    [
        {"$schema": "https://example.invalid/not-draft-2020-12"},
        {"description": "x" * 70_000},
    ],
)
def test_archon_invalid_output_format_is_a_coded_workflow_validation_error(
    workflow_writer, tmp_path, schema
):
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(
            _archon_workflow(
                workflow_writer,
                tmp_path,
                nodes=[
                    {
                        "id": "producer",
                        "prompt": "Return a report",
                        "output_format": schema,
                    }
                ],
            )
        )

    assert [(issue.path, issue.code) for issue in exc.value.issues] == [
        ("nodes[0].output_format", "invalid_output_format")
    ]


def _consumer_nodes(schema, path="missing", *, depends_on=True):
    consumer = {
        "id": "consumer",
        "prompt": "Use the result",
        "when": f"$producer.output.{path} != ''",
    }
    if depends_on:
        consumer["depends_on"] = ["producer"]
    producer = {"id": "producer", "prompt": "Produce"}
    if schema is not None:
        producer["output_format"] = schema
    return [producer, consumer]


@pytest.mark.parametrize(
    ("schema", "path"),
    [
        (
            {
                "type": "object",
                "properties": {"present": {"type": "string"}},
                "additionalProperties": False,
            },
            "missing",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "present": {
                        "type": "object",
                        "properties": {"known": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
            "present.missing",
        ),
        (
            {
                "$defs": {
                    "report": {
                        "type": "object",
                        "properties": {"present": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
                "$ref": "#/$defs/report",
            },
            "missing",
        ),
    ],
)
def test_closed_structured_output_rejects_impossible_field_reference(
    workflow_writer, tmp_path, schema, path
):
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(
            _archon_workflow(
                workflow_writer,
                tmp_path,
                nodes=_consumer_nodes(schema, path),
            )
        )

    assert [issue.code for issue in exc.value.issues] == [
        "structured_output_field_impossible"
    ]


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"optional": {"type": "string"}},
            "additionalProperties": False,
        },
        {"type": "object", "additionalProperties": True},
        {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"missing": {"type": "string"}},
                    "additionalProperties": False,
                },
            ]
        },
        {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"missing": {"type": "string"}},
                    "additionalProperties": False,
                },
            ]
        },
        None,
    ],
)
def test_possible_structured_output_field_reference_is_not_rejected(
    workflow_writer, tmp_path, schema
):
    path = (
        "optional"
        if schema and "optional" in schema.get("properties", {})
        else "missing"
    )

    package = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path,
            nodes=_consumer_nodes(schema, path),
        )
    )

    assert package.definition.nodes[-1].id == "consumer"


def test_non_dependency_output_reference_keeps_graph_rejection(
    workflow_writer, tmp_path
):
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(
            _archon_workflow(
                workflow_writer,
                tmp_path,
                nodes=_consumer_nodes(
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    depends_on=False,
                ),
            )
        )

    assert [issue.code for issue in exc.value.issues] == [
        "condition_reference_not_upstream"
    ]
