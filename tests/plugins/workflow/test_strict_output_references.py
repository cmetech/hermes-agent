from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

import plugins.workflow.language_schema as language_schema
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
