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


def test_output_type_preserves_the_durable_metadata_boundary(
    workflow_writer, tmp_path
):
    prefix = "MixedCase/分析/"
    output_type = prefix + ("Ω" * (16_384 - len(prefix)))
    accepted = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path / "accepted",
            nodes=[{
                "id": "producer",
                "prompt": "Return a report",
                "output_type": output_type,
            }],
        )
    )

    assert accepted.definition.nodes[0].options["output_type"] == output_type

    rejected = _archon_workflow(
        workflow_writer,
        tmp_path / "rejected",
        nodes=[{
            "id": "producer",
            "prompt": "Return a report",
            "output_type": output_type + "x",
        }],
    )
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(rejected)

    assert exc.value.issues[0].path == "nodes[0].output_type"
    assert exc.value.issues[0].code == "string_too_long"


@pytest.mark.parametrize("output_type", ("", " \t "))
def test_output_type_keeps_nonempty_direct_field_diagnostic(
    workflow_writer, tmp_path, output_type
):
    path = _archon_workflow(
        workflow_writer,
        tmp_path,
        nodes=[{
            "id": "producer",
            "prompt": "Return a report",
            "output_type": output_type,
        }],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].path == "nodes[0].output_type"
    assert exc.value.issues[0].code == "expected_string"


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
        {"type": 7},
        {"type": "object", "required": "x"},
        {"type": "number", "minimum": "zero"},
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


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("minimum", -(10**1_000)),
        ("maximum", 10**1_000),
        ("multipleOf", 10**1_000),
    ],
)
def test_archon_output_format_preserves_arbitrary_size_integer_number_keywords(
    workflow_writer, tmp_path, keyword, value
):
    package = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path,
            nodes=[{
                "id": "producer",
                "prompt": "Return a report",
                "output_format": {"type": "number", keyword: value},
            }],
        )
    )

    assert package.definition.nodes[0].options["output_format"][keyword] == value


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


def _interpolated_consumer(surface: str) -> dict[str, object]:
    reference = "$producer.output.missing"
    consumer: dict[str, object] = {"id": "consumer", "depends_on": ["producer"]}
    if surface == "when":
        consumer.update(prompt="Consume", when=f"{reference} != ''")
    elif surface == "bash":
        consumer["bash"] = f"printf '%s' '{reference}'"
    elif surface == "prompt":
        consumer["prompt"] = f"Consume {reference}"
    elif surface == "script":
        consumer.update(script=f"print('{reference}')", runtime="uv")
    elif surface == "loop.prompt":
        consumer["loop"] = {
            "prompt": f"Revise {reference}",
            "until": "DONE",
            "max_iterations": 2,
        }
    elif surface == "loop.until_bash":
        consumer["loop"] = {
            "prompt": "Revise",
            "until": "DONE",
            "until_bash": f"test -n '{reference}'",
            "max_iterations": 2,
        }
    elif surface == "approval.message":
        consumer["approval"] = {"message": f"Approve {reference}"}
    elif surface == "approval.on_reject.prompt":
        consumer["approval"] = {
            "message": "Approve",
            "on_reject": {"prompt": f"Revise {reference}"},
        }
    else:  # pragma: no cover - table exhaustiveness guard
        raise AssertionError(surface)
    return consumer


@pytest.mark.parametrize(
    ("surface", "expected_path"),
    (
        ("when", "nodes[1].when"),
        ("bash", "nodes[1].bash"),
        ("prompt", "nodes[1].prompt"),
        ("script", "nodes[1].script"),
        ("loop.prompt", "nodes[1].loop.prompt"),
        ("loop.until_bash", "nodes[1].loop.until_bash"),
        ("approval.message", "nodes[1].approval.message"),
        (
            "approval.on_reject.prompt",
            "nodes[1].approval.on_reject.prompt",
        ),
    ),
)
def test_impossible_output_field_is_rejected_on_every_interpolated_surface(
    workflow_writer, tmp_path, surface, expected_path
) -> None:
    producer = {
        "id": "producer",
        "prompt": "Produce",
        "output_format": {
            "type": "object",
            "properties": {"present": {"type": "string"}},
            "additionalProperties": False,
        },
    }

    with pytest.raises(WorkflowValidationError) as exc_info:
        load_workflow(
            _archon_workflow(
                workflow_writer,
                tmp_path,
                nodes=[producer, _interpolated_consumer(surface)],
            )
        )

    assert [(issue.path, issue.code) for issue in exc_info.value.issues] == [
        (expected_path, "structured_output_field_impossible")
    ]


@pytest.mark.parametrize(
    ("producer", "reference"),
    (
        (
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {
                    "type": "object",
                    "properties": {"optional": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "$producer.output.optional",
        ),
        (
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {"type": "object", "additionalProperties": True},
            },
            "$producer.output.missing",
        ),
        (
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {
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
            },
            "$producer.output.missing",
        ),
        ({"id": "producer", "prompt": "Produce"}, "$producer.output.missing"),
        ({"id": "producer", "prompt": "Produce"}, "$unknown.output.missing"),
        ({"id": "producer", "prompt": "Produce"}, "$producer.output"),
    ),
)
def test_prompt_reference_keeps_conservative_phase_two_admission(
    workflow_writer, tmp_path, producer, reference
) -> None:
    package = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path,
            nodes=[producer, {"id": "consumer", "prompt": f"Use {reference}"}],
        )
    )

    assert package.definition.nodes[-1].id == "consumer"


@pytest.mark.parametrize(
    "consumer",
    (
        {
            "id": "consumer",
            "script": "helper.py",
            "runtime": "uv",
            "systemPrompt": "$producer.output.missing",
        },
        {"id": "consumer", "command": "$producer.output.missing"},
        {
            "id": "consumer",
            "loop": {
                "prompt": "Revise",
                "until": "$producer.output.missing",
                "max_iterations": 2,
            },
        },
        {
            "id": "consumer",
            "loop": {
                "prompt": "Revise",
                "until": "DONE",
                "max_iterations": 2,
                "interactive": True,
                "gate_message": "$producer.output.missing",
            },
        },
        {"id": "consumer", "cancel": "$producer.output.missing"},
        {
            "id": "consumer",
            "prompt": "Consume",
            "skills": ["$producer.output.missing"],
        },
        {
            "id": "consumer",
            "prompt": "Consume",
            "mcp": "$producer.output.missing",
        },
        {
            "id": "consumer",
            "prompt": "Consume",
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "$producer.output.missing",
                        "response": {"continue": True},
                    }
                ]
            },
        },
        {
            "id": "consumer",
            "prompt": "Consume",
            "model": "$producer.output.missing",
        },
    ),
)
def test_non_interpolated_fields_are_not_static_reference_surfaces(
    workflow_writer, tmp_path, consumer
) -> None:
    producer = {
        "id": "producer",
        "prompt": "Produce",
        "output_format": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }

    package = load_workflow(
        _archon_workflow(
            workflow_writer,
            tmp_path,
            nodes=[producer, consumer],
        )
    )

    assert package.definition.nodes[-1].id == "consumer"
