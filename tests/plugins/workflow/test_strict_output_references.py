from __future__ import annotations

import copy
from pathlib import Path
import hashlib
import json

import pytest

import plugins.workflow.language_schema as language_schema
from agent.structured_output import normalize_schema
from plugins.workflow import output_resolution
from plugins.workflow.resources import VariableContext
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.language_schema import (
    definition_json_schema,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.store import RunStore
from plugins.workflow.trust import compute_package_digest


def _archon(workflow_writer, root: Path, *, nodes: list[dict[str, object]]) -> Path:
    path = workflow_writer(root, name="strict-references", nodes=nodes)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return path


def _codes(exc: pytest.ExceptionInfo[WorkflowValidationError]) -> list[str]:
    return [issue.code for issue in exc.value.issues]


def test_v3_reference_iterator_uses_the_exact_ascii_grammar() -> None:
    assert hasattr(language_schema, "iter_output_references")
    template = (
        "before $producer.output after "
        "$_producer-2.output.field_name.0.child-1.12"
    )

    references = list(
        language_schema.iter_output_references(template, normalizer_version=3)
    )

    assert [(item.node_id, item.path) for item in references] == [
        ("producer", ()),
        ("_producer-2", ("field_name", "0", "child-1", "12")),
    ]
    assert [template[item.start : item.end] for item in references] == [
        "$producer.output",
        "$_producer-2.output.field_name.0.child-1.12",
    ]


@pytest.mark.parametrize(
    "reference",
    (
        "$1producer.output",
        "$producer.name.output",
        "$café.output",
        "$producer/output",
        "$producer\\output.output",
        "$producer.output.",
        "$producer.output..field",
        "$producer.output.01",
        "$producer.output[field]",
        "$producer.output-field",
        "$producer.output.1-child",
        "$producer.output.field\N{COMBINING ACUTE ACCENT}",
    ),
)
def test_v3_reference_iterator_rejects_noncanonical_reference_syntax(
    reference: str,
) -> None:
    assert hasattr(language_schema, "WorkflowReferenceSyntaxError")
    with pytest.raises(language_schema.WorkflowReferenceSyntaxError) as exc:
        list(language_schema.iter_output_references(reference, normalizer_version=3))

    assert exc.value.code == "output_reference_path_unsupported"


@pytest.mark.parametrize("ordinary", ("$HOME", "$1", "$(date)", "${HOME}"))
def test_v3_reference_iterator_leaves_ordinary_dollar_syntax_alone(
    ordinary: str,
) -> None:
    assert list(
        language_schema.iter_output_references(ordinary, normalizer_version=3)
    ) == []


@pytest.mark.parametrize(
    "node_id",
    ("1producer", "producer.name", "café", "producer/name", "producer\\name"),
)
def test_new_v3_packages_reject_node_ids_that_are_not_reference_safe(
    workflow_writer, tmp_path: Path, node_id: str
) -> None:
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(
            _archon(
                workflow_writer,
                tmp_path,
                nodes=[{"id": node_id, "bash": "true"}],
            )
        )

    assert _codes(exc) == ["archon_node_id_not_reference_safe"]


def test_v2_identifier_acceptance_is_unchanged(workflow_writer, tmp_path: Path) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[{"id": "café.dot/slash", "bash": "true"}],
    )

    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
        normalizer_version=2,
    )

    assert package.definition.nodes[0].id == "café.dot/slash"


def test_v3_generated_contract_uses_the_same_ascii_reference_grammar() -> None:
    profile = language_schema.WorkflowLanguageProfile.ARCHON_2026_07
    definition = definition_json_schema(profile)
    node_id = definition["properties"]["nodes"]["items"]["properties"]["id"]
    rules = {
        item["id"]: item
        for item in workflow_authoring_contract(profile)["semantic_rules"]
    }
    references = rules["strict-output-reference"]

    assert node_id["pattern"] == "^[A-Za-z_][A-Za-z0-9_-]*$"
    assert references["field_paths"] == [
        "nodes[].when",
        "nodes[].prompt",
        "nodes[].bash",
        "nodes[].script",
        "nodes[].command",
        "nodes[].loop.prompt",
        "nodes[].loop.until_bash",
        "nodes[].approval.message",
        "nodes[].approval.on_reject.prompt",
    ]
    assert references["parameters"]["require_direct_dependency"] is True
    assert r"\p" not in references["parameters"]["pattern"]
    assert all("café" not in example for example in references["examples"])


def _consumer(surface: str, reference: str, *, depends_on: list[str]) -> dict[str, object]:
    node: dict[str, object] = {"id": "consumer", "depends_on": depends_on}
    if surface == "when":
        node.update(prompt="consume", when=f"{reference} == 'ready'")
    elif surface == "prompt":
        node["prompt"] = f"consume {reference}"
    elif surface == "bash":
        node["bash"] = f"printf '%s' '{reference}'"
    elif surface == "script":
        node.update(script=f"print('{reference}')", runtime="uv")
    elif surface == "loop.prompt":
        node["loop"] = {
            "prompt": f"consume {reference}",
            "until": "done",
            "max_iterations": 2,
        }
    elif surface == "loop.until_bash":
        node["loop"] = {
            "prompt": "consume",
            "until": "done",
            "until_bash": f"test -n '{reference}'",
            "max_iterations": 2,
        }
    elif surface == "approval.message":
        node["approval"] = {"message": f"approve {reference}"}
    elif surface == "approval.on_reject.prompt":
        node["approval"] = {
            "message": "approve",
            "on_reject": {"prompt": f"retry {reference}"},
        }
    else:  # pragma: no cover - table exhaustiveness guard
        raise AssertionError(surface)
    return node


@pytest.mark.parametrize(
    "surface",
    (
        "when",
        "prompt",
        "bash",
        "script",
        "loop.prompt",
        "loop.until_bash",
        "approval.message",
        "approval.on_reject.prompt",
    ),
)
def test_v3_requires_a_direct_dependency_on_every_inline_surface(
    workflow_writer, tmp_path: Path, surface: str
) -> None:
    nodes = [
        {"id": "producer", "prompt": "produce"},
        _consumer(surface, "$producer.output", depends_on=[]),
    ]

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(_archon(workflow_writer, tmp_path, nodes=nodes))

    assert _codes(exc) == ["output_reference_not_declared_dependency"]
    assert exc.value.issues[0].path == f"nodes[1].{surface}"


@pytest.mark.parametrize(
    ("reference", "depends_on"),
    (
        ("$producer.output", ["middle"]),
        ("$unknown.output", ["producer"]),
        ("$consumer.output", ["producer"]),
        ("$downstream.output", ["producer"]),
    ),
)
def test_v3_rejects_transitive_unknown_self_and_downstream_references(
    workflow_writer,
    tmp_path: Path,
    reference: str,
    depends_on: list[str],
) -> None:
    nodes = [
        {"id": "producer", "prompt": "produce"},
        {"id": "middle", "prompt": "middle", "depends_on": ["producer"]},
        _consumer("prompt", reference, depends_on=depends_on),
        {"id": "downstream", "prompt": "later", "depends_on": ["consumer"]},
    ]

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(_archon(workflow_writer, tmp_path, nodes=nodes))

    assert _codes(exc) == ["output_reference_not_declared_dependency"]


def test_v3_accepts_a_whole_output_from_a_direct_dependency(
    workflow_writer, tmp_path: Path
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {"id": "producer", "prompt": "produce"},
            _consumer("prompt", "$producer.output", depends_on=["producer"]),
        ],
    )

    assert load_workflow(path).definition.nodes[-1].id == "consumer"


@pytest.mark.parametrize("quote", ("'", '"'))
def test_v3_when_quoted_rhs_reference_text_remains_literal(
    workflow_writer, tmp_path: Path, quote: str
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {"type": "string"},
            },
            {
                "id": "consumer",
                "prompt": "consume",
                "depends_on": ["producer"],
                "when": (
                    f"$producer.output == {quote}$literal.output.01{quote}"
                ),
            },
        ],
    )

    package = load_workflow(path)

    references = tuple(
        language_schema.iter_when_output_references(
            str(package.definition.nodes[-1].options["when"]),
            normalizer_version=3,
        )
    )
    assert [(item.node_id, item.path) for item in references] == [
        ("producer", ())
    ]


def test_v3_preserves_dependency_cycle_as_the_topology_error(
    workflow_writer, tmp_path: Path
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "depends_on": ["consumer"],
            },
            _consumer(
                "prompt", "$producer.output", depends_on=["producer"]
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["dependency_cycle"]


def test_v3_field_reference_requires_a_declared_structured_output(
    workflow_writer, tmp_path: Path
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {"id": "producer", "prompt": "produce"},
            _consumer(
                "prompt", "$producer.output.field", depends_on=["producer"]
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["output_reference_path_unsupported"]


@pytest.mark.parametrize(
    ("schema", "reference"),
    (
        (
            {
                "type": "object",
                "properties": {"dotted.key": {"type": "string"}},
                "additionalProperties": False,
            },
            "$producer.output.dotted.key",
        ),
        (
            {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"dotted.key": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                ]
            },
            "$producer.output.dotted.key",
        ),
        (
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"dotted.key": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "$producer.output.0.dotted.key",
        ),
    ),
)
def test_v3_rejects_an_attempt_to_address_a_dotted_mapping_key(
    workflow_writer, tmp_path: Path, schema: dict[str, object], reference: str
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": schema,
            },
            _consumer(
                "prompt",
                reference,
                depends_on=["producer"],
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["output_reference_path_unsupported"]


@pytest.mark.parametrize(
    ("schema", "reference"),
    (
        (
            {"type": "array", "items": {"type": "string"}},
            "$producer.output.0",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    }
                },
                "additionalProperties": False,
            },
            "$producer.output.items.0",
        ),
        (
            {
                "type": "array",
                "prefixItems": [
                    False,
                    {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "additionalProperties": False,
                    },
                ],
                "items": False,
                "minItems": 2,
                "maxItems": 2,
            },
            "$producer.output.1.name",
        ),
        (
            {
                "anyOf": [
                    {"type": "array", "maxItems": 0},
                    {"type": "array", "items": {"type": "number"}},
                ]
            },
            "$producer.output.0",
        ),
        (
            {
                "allOf": [
                    {"type": "array"},
                    {
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "additionalProperties": False,
                        }
                    },
                ]
            },
            "$producer.output.0.name",
        ),
        (
            {
                "$defs": {
                    "rows": {
                        "type": "array",
                        "items": {"type": "integer"},
                    }
                },
                "$ref": "#/$defs/rows",
            },
            "$producer.output.0",
        ),
    ),
)
def test_v3_static_schema_feasibility_accepts_possible_sequence_indexes(
    workflow_writer,
    tmp_path: Path,
    schema: dict[str, object],
    reference: str,
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {"id": "producer", "prompt": "produce", "output_format": schema},
            _consumer("prompt", reference, depends_on=["producer"]),
        ],
    )

    assert load_workflow(path).definition.nodes[-1].id == "consumer"


@pytest.mark.parametrize(
    ("schema", "reference"),
    (
        (
            {
                "type": "object",
                "properties": {"0": {"type": "string"}},
                "additionalProperties": False,
            },
            "$producer.output.0",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "object",
                        "properties": {
                            "0": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "additionalProperties": False,
                            }
                        },
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
            "$producer.output.rows.0.name",
        ),
        (
            {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"0": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    {"type": "array", "maxItems": 0},
                ]
            },
            "$producer.output.0",
        ),
        (
            {
                "allOf": [
                    {"type": "object"},
                    {
                        "properties": {"0": {"type": "string"}},
                        "additionalProperties": False,
                    },
                ]
            },
            "$producer.output.0",
        ),
    ),
)
def test_v3_numeric_segments_accept_exact_mapping_keys_when_schema_allows_them(
    workflow_writer,
    tmp_path: Path,
    schema: dict[str, object],
    reference: str,
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {"id": "producer", "prompt": "produce", "output_format": schema},
            _consumer("prompt", reference, depends_on=["producer"]),
        ],
    )

    assert load_workflow(path).definition.nodes[-1].id == "consumer"


def test_v3_numeric_mapping_key_still_exposes_an_unaddressable_dotted_child(
    workflow_writer, tmp_path: Path
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "properties": {
                        "0": {
                            "type": "object",
                            "properties": {"dotted.key": {"type": "string"}},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
            },
            _consumer(
                "prompt",
                "$producer.output.0.dotted.key",
                depends_on=["producer"],
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["output_reference_path_unsupported"]


def test_v3_numeric_segment_rejects_a_closed_object_without_the_exact_key(
    workflow_writer, tmp_path: Path
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "properties": {"other": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            _consumer(
                "prompt", "$producer.output.0", depends_on=["producer"]
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["structured_output_field_impossible"]


def test_v3_when_reference_iterator_yields_absolute_offsets_before_late_syntax_error() -> None:
    expression = (
        "  $first.output.value == 'ready' && "
        "$second.output.0 >= 2 || $broken.output."
    )
    references = language_schema.iter_when_output_references(
        expression, normalizer_version=3
    )

    first = next(references)
    second = next(references)

    assert expression[first.start : first.end] == "$first.output.value"
    assert expression[second.start : second.end] == "$second.output.0"
    with pytest.raises(language_schema.WorkflowReferenceSyntaxError):
        next(references)


@pytest.mark.parametrize(
    ("schema", "reference"),
    (
        (
            {"type": "array", "maxItems": 0, "items": {"type": "string"}},
            "$producer.output.0",
        ),
        (
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": False,
            },
            "$producer.output.1",
        ),
        (
            {"type": "array", "prefixItems": [False], "items": True},
            "$producer.output.0",
        ),
        (
            {
                "allOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"maxItems": 0},
                ]
            },
            "$producer.output.0",
        ),
        (
            {
                "anyOf": [
                    {"type": "array", "maxItems": 0},
                    {"type": "object", "additionalProperties": False},
                ]
            },
            "$producer.output.0",
        ),
    ),
)
def test_v3_static_schema_feasibility_rejects_proven_impossible_indexes(
    workflow_writer,
    tmp_path: Path,
    schema: dict[str, object],
    reference: str,
) -> None:
    path = _archon(
        workflow_writer,
        tmp_path,
        nodes=[
            {"id": "producer", "prompt": "produce", "output_format": schema},
            _consumer("prompt", reference, depends_on=["producer"]),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["structured_output_field_impossible"]


def test_authenticated_command_body_is_scanned_before_snapshot_promotion(
    workflow_writer, tmp_path: Path
) -> None:
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "consume.md").write_text(
        "consume $producer.output\n", encoding="utf-8"
    )
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "producer", "prompt": "produce"},
                {"id": "consumer", "command": "consume"},
            ],
        )
    )

    with pytest.raises(WorkflowValidationError) as exc:
        RunStore(tmp_path / "home").prepare_run_snapshot(package)

    assert _codes(exc) == ["output_reference_not_declared_dependency"]


def test_authenticated_command_body_accepts_a_direct_dependency(
    workflow_writer, tmp_path: Path
) -> None:
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "consume.md").write_text(
        "consume $producer.output\n", encoding="utf-8"
    )
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "producer", "prompt": "produce"},
                {
                    "id": "consumer",
                    "command": "consume",
                    "depends_on": ["producer"],
                },
            ],
        )
    )

    assert compute_package_digest(package).sha256


def test_authenticated_command_body_rejects_malformed_hyphen_continuation(
    workflow_writer, tmp_path: Path
) -> None:
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "consume.md").write_text(
        "consume $producer.output-field\n", encoding="utf-8"
    )
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "producer", "prompt": "produce"},
                {
                    "id": "consumer",
                    "command": "consume",
                    "depends_on": ["producer"],
                },
            ],
        )
    )

    with pytest.raises(WorkflowValidationError) as exc:
        compute_package_digest(package)

    assert _codes(exc) == ["output_reference_path_unsupported"]


def test_recognized_reference_in_authenticated_named_script_is_blocking(
    workflow_writer, tmp_path: Path
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "consume.py"
    script.write_bytes(b"print('$producer.output')\n")
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "producer", "prompt": "produce"},
                {
                    "id": "consumer",
                    "script": "consume.py",
                    "runtime": "uv",
                    "depends_on": ["producer"],
                },
            ],
        )
    )

    with pytest.raises(WorkflowValidationError) as exc:
        compute_package_digest(package)

    assert _codes(exc) == ["named_script_output_reference_unsupported"]
    assert script.read_bytes() == b"print('$producer.output')\n"


def test_named_script_keeps_ordinary_dollar_syntax_and_reference_free_bytes(
    workflow_writer, tmp_path: Path
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "consume.py"
    script.write_bytes(b"print('$HOME', '$1', '${VALUE}')\n")
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "consumer", "script": "consume.py", "runtime": "uv"},
            ],
        )
    )

    assert compute_package_digest(package).sha256
    assert script.read_bytes() == b"print('$HOME', '$1', '${VALUE}')\n"


@pytest.mark.parametrize(
    "body",
    (
        b"$producer.output $bad.output.\n",
        b"$bad.output. $producer.output\n",
        b"\xff before $producer.output after \xfe\n",
    ),
)
def test_named_script_scan_never_loses_a_valid_reference_around_other_bytes(
    workflow_writer, tmp_path: Path, body: bytes
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "consume.py").write_bytes(body)
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[
                {"id": "producer", "prompt": "produce"},
                {
                    "id": "consumer",
                    "script": "consume.py",
                    "runtime": "uv",
                    "depends_on": ["producer"],
                },
            ],
        )
    )

    with pytest.raises(WorkflowValidationError) as exc:
        compute_package_digest(package)

    assert _codes(exc) == ["named_script_output_reference_unsupported"]


@pytest.mark.parametrize("normalizer_version", (1, 2))
@pytest.mark.parametrize(
    "body",
    (b"\xff---\nnot: parsed\n", b"---\n- not\n- a\n- mapping\n---\nbody\n"),
)
def test_pre_v3_archon_command_digest_preserves_raw_command_bytes(
    workflow_writer, tmp_path: Path, normalizer_version: int, body: bytes
) -> None:
    root = tmp_path / f"v{normalizer_version}"
    (root / "commands").mkdir(parents=True)
    command = root / "commands" / "consume.md"
    command.write_bytes(body)
    path = _archon(
        workflow_writer,
        root,
        nodes=[{"id": "consumer", "command": "consume"}],
    )
    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
        normalizer_version=normalizer_version,
    )

    first = compute_package_digest(package)
    command.write_bytes(b"changed\n" + body)
    second = compute_package_digest(package)

    assert first.covered_relative_paths == (
        "commands/consume.md",
        "example.hermes.yaml",
        "example.yaml",
    )
    assert first.sha256 != second.sha256
    assert all(len(value) == 64 for value in (first.sha256, second.sha256))


@pytest.mark.parametrize(
    "body",
    (b"\xff---\nnot: parsed\n", b"---\n- not\n- a\n- mapping\n---\nbody\n"),
)
def test_legacy_command_digest_preserves_raw_command_bytes(
    workflow_writer, tmp_path: Path, body: bytes
) -> None:
    (tmp_path / "commands").mkdir()
    command = tmp_path / "commands" / "consume.md"
    command.write_bytes(body)
    package = load_workflow(
        workflow_writer(
            tmp_path,
            name="legacy-command-bytes",
            nodes=[{"id": "consumer", "command": "consume"}],
        )
    )

    observed = compute_package_digest(package)

    digest = hashlib.sha256()
    for relative in observed.covered_relative_paths:
        data = (package.root / relative).read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    assert observed.sha256 == digest.hexdigest()


@pytest.mark.parametrize(
    "body",
    (b"\xff", b"---\n- not\n- a\n- mapping\n---\nbody\n", b"---\nunclosed\n"),
)
def test_v3_invalid_authenticated_command_has_a_bounded_stable_error(
    workflow_writer, tmp_path: Path, body: bytes
) -> None:
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "consume.md").write_bytes(body)
    package = load_workflow(
        _archon(
            workflow_writer,
            tmp_path,
            nodes=[{"id": "consumer", "command": "consume"}],
        )
    )

    with pytest.raises(WorkflowValidationError) as exc:
        compute_package_digest(package)

    assert _codes(exc) == ["invalid_command_resource"]
    assert exc.value.issues[0].path == "nodes[0].command"
    assert len(exc.value.issues[0].message.encode("utf-8")) <= 128


def _resolved_output(
    value: object,
    *,
    structured: bool = True,
    node_id: str = "producer",
) -> object:
    if structured:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        schema_fingerprint = "3" * 64
        media_type = "application/json"
    else:
        assert isinstance(value, str)
        canonical = value.encode("utf-8")
        schema_fingerprint = None
        media_type = "text/markdown; charset=utf-8"
    return output_resolution.ResolvedNodeOutput(
        canonical_bytes=canonical,
        value=value,
        text=canonical.decode("utf-8"),
        media_type=media_type,
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id=node_id,
        attempt_id="attempt-winner",
        publication_id="a" * 32,
        schema_fingerprint=schema_fingerprint,
        canonicalization_version=1,
    )


@pytest.mark.parametrize(
    ("value", "rendered"),
    (
        ("plain", "plain"),
        (None, "null"),
        (True, "true"),
        (3, "3"),
        ([{"z": 1}], '[{"z":1}]'),
        ({"z": 1, "a": False}, '{"a":false,"z":1}'),
    ),
)
def test_v3_resolver_returns_immutable_typed_and_rendered_facets(
    value: object, rendered: str
) -> None:
    result = output_resolution.resolve_output_reference(
        _resolved_output(value), node_id="producer", path=()
    )

    if isinstance(value, list):
        assert json.loads(result.rendered_text) == value
    else:
        assert result.typed_value == value
    assert result.rendered_text == rendered
    with pytest.raises((AttributeError, TypeError)):
        result.rendered_text = "changed"


def test_v3_schemaless_json_looking_text_never_becomes_structured() -> None:
    output = _resolved_output('{"answer":42}', structured=False)

    whole = output_resolution.resolve_output_reference(
        output, node_id="producer", path=()
    )

    assert whole.typed_value == '{"answer":42}'
    assert whole.rendered_text == '{"answer":42}'
    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        output_resolution.resolve_output_reference(
            output, node_id="producer", path=("answer",)
        )
    assert (exc.value.code, exc.value.node_id, exc.value.path) == (
        "output_reference_not_structured",
        "producer",
        ("answer",),
    )


def test_v3_node_resolution_keeps_json_looking_schemaless_bytes_as_text(
    tmp_path: Path,
) -> None:
    canonical = b'{"answer":42}'
    path = tmp_path / "output.md"
    path.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    candidate = output_resolution.PrimaryOutputCandidate(
        attempt_relative_path="output.md",
        media_type="text/plain",
        size_bytes=len(canonical),
        sha256=digest,
        structured_value=None,
        schema_fingerprint=None,
        canonicalization_version=1,
        output_type=None,
    )

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="producer",
        attempt_id="attempt-winner",
        descriptor={
            "node_id": "producer",
            "attempt_id": "attempt-winner",
            "relative_path": "output.md",
            "media_type": "text/plain",
            "size_bytes": len(canonical),
            "sha256": digest,
        },
        candidate=candidate,
        strict=True,
    )
    reference = output_resolution.resolve_output_reference(
        resolved, node_id="producer"
    )

    assert reference.typed_value == canonical.decode("utf-8")
    assert reference.rendered_text == canonical.decode("utf-8")


@pytest.mark.parametrize(
    "text",
    ('{"answer":42}', '["answer"]', "42", "true", "null"),
)
def test_v3_candidate_less_node_resolution_keeps_schemaless_bytes_as_text(
    tmp_path: Path,
    text: str,
) -> None:
    canonical = text.encode("utf-8")
    path = tmp_path / "stdout.txt"
    path.write_bytes(canonical)
    descriptor = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "relative_path": "stdout.txt",
        "media_type": "application/json",
        "size_bytes": len(canonical),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="producer",
        attempt_id="attempt-winner",
        descriptor=descriptor,
        strict=True,
    )
    reference = output_resolution.resolve_output_reference(
        resolved, node_id="producer"
    )

    assert reference.typed_value == text
    assert reference.rendered_text == text
    assert resolved.schema_fingerprint is None


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ('{"answer":42}', {"answer": 42}),
        ('["answer"]', ("answer",)),
        ("42", 42),
        ("true", True),
        ("null", None),
    ),
)
def test_v2_candidate_less_node_resolution_retains_json_reparse_adapter(
    tmp_path: Path,
    text: str,
    expected: object,
) -> None:
    canonical = text.encode("utf-8")
    path = tmp_path / "stdout.txt"
    path.write_bytes(canonical)

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="producer",
        attempt_id="attempt-winner",
        descriptor={
            "node_id": "producer",
            "attempt_id": "attempt-winner",
            "relative_path": "stdout.txt",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        },
        strict=False,
    )

    assert resolved.value == expected


def test_v3_resolver_traverses_exact_mapping_keys_and_canonical_indexes() -> None:
    output = _resolved_output(
        {"items": [{"count": 3}], "01": "mapping-key", "zero": ["first"]}
    )

    count = output_resolution.resolve_output_reference(
        output, node_id="producer", path=("items", "0", "count")
    )
    exact = output_resolution.resolve_output_reference(
        output, node_id="producer", path=("01",)
    )

    assert (count.typed_value, count.rendered_text) == (3, "3")
    assert (exact.typed_value, exact.rendered_text) == ("mapping-key", "mapping-key")

    for path, expected in (
        (("missing",), "output_reference_field_missing"),
        (("items", "1"), "output_reference_field_missing"),
        (("items", "01"), "output_reference_path_type"),
        (("items", "zero"), "output_reference_path_type"),
        (("items", "0", "count", "child"), "output_reference_path_type"),
    ):
        with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
            output_resolution.resolve_output_reference(
                output, node_id="producer", path=path
            )
        assert exc.value.code == expected
        assert exc.value.path == path


def test_v3_missing_output_has_a_bounded_typed_failure() -> None:
    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        output_resolution.resolve_output_reference(
            None, node_id="producer", path=("answer",)
        )

    assert exc.value.code == "output_reference_missing"
    assert exc.value.node_id == "producer"
    assert exc.value.path == ("answer",)
    assert len(str(exc.value).encode("utf-8")) <= 256


def test_v3_nonfinite_structured_value_fails_as_reference_integrity() -> None:
    output = output_resolution.ResolvedNodeOutput(
        canonical_bytes=b"1e999",
        value=float("inf"),
        text="1e999",
        media_type="application/json",
        sha256=hashlib.sha256(b"1e999").hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="a" * 32,
        schema_fingerprint="3" * 64,
        canonicalization_version=1,
    )

    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        output_resolution.resolve_output_reference(output, node_id="producer")

    assert exc.value.code == "output_reference_integrity"


def test_v3_variable_context_uses_strict_resolver_without_empty_fallback() -> None:
    variables = VariableContext(
        node_outputs={"producer": _resolved_output({"answer": "ready"})},
        normalizer_version=3,
    )

    assert variables.render_prompt("$producer.output.answer") == "ready"
    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        variables.render_prompt("$producer.output.missing")
    assert exc.value.code == "output_reference_field_missing"


def test_v3_resolver_requires_publication_path_and_full_schema_identity(
    tmp_path: Path,
) -> None:
    canonical = b'{"answer":"ready"}'
    forged = tmp_path / "nodes" / "producer" / "attempt-winner" / "output.json"
    forged.parent.mkdir(parents=True)
    forged.write_bytes(canonical)
    candidate = output_resolution.PrimaryOutputCandidate(
        attempt_relative_path=forged.relative_to(tmp_path).as_posix(),
        media_type="application/json",
        size_bytes=len(canonical),
        sha256=hashlib.sha256(canonical).hexdigest(),
        structured_value={"answer": "ready"},
        schema_fingerprint="3" * 64,
        canonicalization_version=1,
        output_type="Answer",
    )
    descriptor = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "relative_path": candidate.attempt_relative_path,
        "media_type": "application/json",
        "size_bytes": len(canonical),
        "sha256": candidate.sha256,
        "publication_id": "a" * 32,
        "content_name": "content.json",
        "schema_fingerprint": candidate.schema_fingerprint,
        "canonicalization_version": 1,
        "output_type": "Answer",
    }

    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        output_resolution.resolve_node_output(
            run_directory=tmp_path,
            node_id="producer",
            attempt_id="attempt-winner",
            descriptor=descriptor,
            candidate=candidate,
            publication_id="a" * 32,
            strict=True,
        )

    assert exc.value.code == "output_reference_integrity"


def _strict_publication_projection(tmp_path: Path) -> dict[str, object]:
    canonical = b'{"answer":"ready"}'
    publication_id = "a" * 32
    content = tmp_path / "publications" / publication_id / "content.json"
    content.parent.mkdir(parents=True)
    content.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    normalized_schema = normalize_schema({"type": "object"})
    schema_fingerprint = normalized_schema.schema_fingerprint
    candidate = {
        "attempt_relative_path": "nodes/producer/attempt-winner/output.json",
        "media_type": "application/json",
        "size_bytes": len(canonical),
        "sha256": digest,
        "schema_fingerprint": schema_fingerprint,
        "canonicalization_version": 1,
        "output_type": "Answer",
    }
    return {
        "run_id": "run-1",
        "language": {
            "effective_profile": "archon-2026-07",
            "normalizer_version": 3,
            "normalized_definition_digest": "1" * 64,
            "semantic_fingerprint": "2" * 64,
            "structured_outputs": {
                "producer": {
                    "canonical_schema": dict(normalized_schema.canonical_schema),
                    "schema_fingerprint": schema_fingerprint,
                    "canonicalization_version": 1,
                }
            },
            "node_semantics": {},
        },
        "nodes": {
            "producer": {
                "type": "prompt",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                    "metadata": {"primary_output_candidate": candidate},
                }],
            }
        },
        "artifacts": [{
            "node_id": "producer",
            "attempt_id": "attempt-winner",
            "relative_path": candidate["attempt_relative_path"],
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": digest,
            "publication_id": publication_id,
            "content_name": "content.json",
            "schema_fingerprint": schema_fingerprint,
            "canonicalization_version": 1,
            "output_type": "Answer",
        }],
    }


@pytest.mark.parametrize(
    "missing_field",
    (
        "publication_id",
        "content_name",
        "schema_fingerprint",
        "canonicalization_version",
        "output_type",
    ),
)
def test_v3_strict_publication_requires_complete_descriptor_identity(
    tmp_path: Path,
    missing_field: str,
) -> None:
    projection = _strict_publication_projection(tmp_path)
    descriptor = projection["artifacts"][0]
    publication_id = descriptor["publication_id"]
    descriptor.pop(missing_field)
    candidate = output_resolution.primary_output_candidate_from_identity(
        projection["nodes"]["producer"]["attempts"][0]["metadata"][
            "primary_output_candidate"
        ]
    )

    with pytest.raises(output_resolution.WorkflowOutputReferenceError) as exc:
        output_resolution.resolve_node_output(
            run_directory=tmp_path,
            node_id="producer",
            attempt_id="attempt-winner",
            descriptor=descriptor,
            candidate=candidate,
            publication_id=publication_id,
            strict=True,
        )

    assert exc.value.code == "output_reference_integrity"


def test_v3_schemaless_publication_accepts_complete_identity_without_candidate(
    tmp_path: Path,
) -> None:
    canonical = b"approved"
    publication_id = "a" * 32
    content = tmp_path / "publications" / publication_id / "content.md"
    content.parent.mkdir(parents=True)
    content.write_bytes(canonical)

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="review",
        attempt_id="attempt-winner",
        descriptor={
            "node_id": "review",
            "attempt_id": "attempt-winner",
            "relative_path": "nodes/review/attempt-winner/output.md",
            "media_type": "text/markdown; charset=utf-8",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "publication_id": publication_id,
            "content_name": "content.md",
            "schema_fingerprint": None,
            "canonicalization_version": 1,
            "output_type": "ApprovalDecision",
        },
        publication_id=publication_id,
        strict=True,
    )

    assert resolved.value == "approved"
    assert resolved.text == "approved"
    assert resolved.schema_fingerprint is None


@pytest.mark.parametrize(
    ("field", "changed"),
    (
        ("content_name", "content.md"),
        ("schema_fingerprint", "4" * 64),
        ("canonicalization_version", 2),
        ("output_type", "ChangedAnswer"),
    ),
)
def test_v3_warm_cache_revalidates_every_publication_descriptor_identity_field(
    tmp_path: Path,
    field: str,
    changed: object,
) -> None:
    projection = _strict_publication_projection(tmp_path)
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)["producer"]
    drifted = copy.deepcopy(projection)
    drifted["artifacts"][0][field] = changed
    second = scheduler._output_values(drifted, tmp_path)["producer"]

    assert isinstance(first, output_resolution.ResolvedNodeOutput)
    assert isinstance(second, output_resolution.WorkflowOutputReferenceError)
    assert second.code == "output_reference_integrity"


def test_v3_missing_winning_candidate_descriptor_is_integrity_not_missing(
    tmp_path: Path,
) -> None:
    projection = _strict_publication_projection(tmp_path)
    projection["artifacts"] = []

    output = RunScheduler.__new__(RunScheduler)._output_values(projection, tmp_path)

    assert isinstance(output["producer"], output_resolution.WorkflowOutputReferenceError)
    assert output["producer"].code == "output_reference_integrity"


def test_v3_integrity_failure_does_not_poison_good_publication_cache(
    tmp_path: Path,
) -> None:
    good = _strict_publication_projection(tmp_path)
    bad = copy.deepcopy(good)
    bad["artifacts"][0]["content_name"] = "content.md"
    scheduler = RunScheduler.__new__(RunScheduler)

    rejected = scheduler._output_values(bad, tmp_path)["producer"]
    resolved = scheduler._output_values(good, tmp_path)["producer"]

    assert isinstance(rejected, output_resolution.WorkflowOutputReferenceError)
    assert rejected.code == "output_reference_integrity"
    assert isinstance(resolved, output_resolution.ResolvedNodeOutput)
    assert resolved.value == {"answer": "ready"}


def test_v3_scheduler_rejects_ambiguous_successful_winning_attempts(
    tmp_path: Path,
) -> None:
    normalized_schema = normalize_schema({"type": "object"})
    artifacts: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    for suffix, answer in (("old", "stale"), ("new", "winner")):
        attempt_id = f"attempt-{suffix}"
        data = json.dumps(
            {"answer": answer}, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        path = tmp_path / "nodes" / "producer" / attempt_id / "output.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        identity = {
            "attempt_relative_path": path.relative_to(tmp_path).as_posix(),
            "media_type": "application/json",
            "size_bytes": len(data),
            "sha256": digest,
            "schema_fingerprint": normalized_schema.schema_fingerprint,
            "canonicalization_version": 1,
            "output_type": "Answer",
        }
        attempts.append({
            "attempt_id": attempt_id,
            "state": "succeeded",
            "metadata": {"primary_output_candidate": identity},
        })
        artifacts.append({
            "node_id": "producer",
            "attempt_id": attempt_id,
            "relative_path": identity["attempt_relative_path"],
            "media_type": identity["media_type"],
            "size_bytes": identity["size_bytes"],
            "sha256": identity["sha256"],
        })
    projection = {
        "run_id": "run-1",
        "language": {
            "effective_profile": "archon-2026-07",
            "normalizer_version": 3,
            "normalized_definition_digest": "1" * 64,
            "semantic_fingerprint": "2" * 64,
            "structured_outputs": {
                "producer": {
                    "canonical_schema": dict(normalized_schema.canonical_schema),
                    "schema_fingerprint": normalized_schema.schema_fingerprint,
                    "canonicalization_version": 1,
                }
            },
            "node_semantics": {},
        },
        "nodes": {
            "producer": {
                "type": "prompt",
                "attempts": attempts,
            }
        },
        "artifacts": artifacts,
    }

    outputs = RunScheduler.__new__(RunScheduler)._output_values(projection, tmp_path)

    assert isinstance(
        outputs["producer"], output_resolution.WorkflowOutputReferenceError
    )
    assert outputs["producer"].code == "output_reference_integrity"
