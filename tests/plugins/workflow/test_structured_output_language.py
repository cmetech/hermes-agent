"""Workflow-language structured-output normalization contracts."""

from __future__ import annotations

import pytest

from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import load_workflow


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
