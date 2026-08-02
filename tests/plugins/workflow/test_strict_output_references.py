from __future__ import annotations

from pathlib import Path

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
    "schema",
    (
        {
            "type": "object",
            "properties": {"dotted.key": {"type": "string"}},
            "additionalProperties": False,
        },
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
    ),
)
def test_v3_rejects_an_attempt_to_address_a_dotted_mapping_key(
    workflow_writer, tmp_path: Path, schema: dict[str, object]
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
                "prompt", "$producer.output.dotted.key", depends_on=["producer"]
            ),
        ],
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert _codes(exc) == ["output_reference_path_unsupported"]


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
