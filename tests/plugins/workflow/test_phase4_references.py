from __future__ import annotations

from pathlib import Path

import pytest

from plugins.workflow.bash_rendering import bash_output_references
from plugins.workflow.includes import rewrite_reference_tokens
from plugins.workflow.language_schema import iter_output_references
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import parse_workflow_source_bytes


def _parse(
    path: Path,
    *,
    sidecar_bytes: bytes | None = None,
    source: str = "project",
    precedence: int = 1,
):
    return parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
    )


def test_v4_rewrites_only_parsed_reference_spans() -> None:
    """Catch namespace replacement consuming paths or adjacent dollar syntax."""
    template = "Use $producer.output.value then $other.output and $$HOME"

    rewritten = rewrite_reference_tokens(
        template,
        tuple(iter_output_references(template, normalizer_version=4)),
        lambda node_id: f"checks__{node_id}",
    )

    assert rewritten == (
        "Use $checks__producer.output.value then $checks__other.output and $$HOME"
    )


def test_v4_bash_rewrite_uses_lexer_admitted_reference_spans() -> None:
    """Catch v4 bypassing Bash context admission or consuming nearby dollars."""
    template = "printf '%s %s' '$producer.output.value' '$$HOME'"

    rewritten = rewrite_reference_tokens(
        template,
        bash_output_references(template, normalizer_version=4),
        lambda node_id: f"checks__{node_id}",
    )

    assert rewritten == (
        "printf '%s %s' '$checks__producer.output.value' '$$HOME'"
    )


@pytest.mark.parametrize(
    ("surface", "template", "expected"),
    (
        (
            "command body",
            "Review $producer.output.value then $$ARGUMENTS",
            "Review $checks__producer.output.value then $$ARGUMENTS",
        ),
        (
            "named script",
            "const λ = '$producer.output.value'; // $$HOME",
            "const λ = '$checks__producer.output.value'; // $$HOME",
        ),
    ),
)
def test_v4_resource_body_rewrites_preserve_nonreference_text(
    surface: str,
    template: str,
    expected: str,
) -> None:
    """Catch authenticated resource rewrites using unrestricted replacement."""
    assert surface
    assert rewrite_reference_tokens(
        template,
        tuple(iter_output_references(template, normalizer_version=4)),
        lambda node_id: f"checks__{node_id}",
    ) == expected


def test_included_nodes_rewrite_every_inline_reference_surface(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch included templates retaining child-local producer identifiers."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[{"id": "checks", "include": "child"}],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "id": "ask",
                "prompt": "λ $producer.output.value $$HOME",
                "depends_on": ["producer"],
                "when": "$producer.output.value == '$producer.output.literal'",
                "systemPrompt": "system $producer.output.value",
                "agents": {
                    "reviewer": {
                        "description": "describe $producer.output.value",
                        "prompt": "agent $producer.output.value",
                    }
                },
                "hooks": {
                    "PreToolUse": [
                        {
                            "response": {
                                "systemMessage": "system $producer.output.value",
                                "stopReason": "stop $producer.output.value",
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecisionReason": (
                                        "reason $producer.output.value"
                                    ),
                                    "additionalContext": (
                                        "context $producer.output.value"
                                    ),
                                },
                            }
                        }
                    ]
                },
            },
            {
                "id": "shell",
                "bash": "printf '%s' '$producer.output.value'",
                "depends_on": ["producer"],
            },
            {
                "id": "inline",
                "script": "print('$producer.output.value')",
                "runtime": "uv",
                "depends_on": ["producer"],
            },
            {
                "id": "repeat",
                "loop": {
                    "prompt": "loop $producer.output.value",
                    "until": "DONE",
                    "max_iterations": 2,
                    "until_bash": "test -n '$producer.output.value'",
                    "interactive": True,
                    "gate_message": "gate $producer.output.value",
                },
                "depends_on": ["producer"],
            },
            {
                "id": "approve",
                "approval": {
                    "message": "approve $producer.output.value",
                    "on_reject": {
                        "prompt": "reject $producer.output.value",
                    },
                },
                "depends_on": ["producer"],
            },
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    expanded = expand_workflow_source(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
    )
    by_id = {node.id: node for node in expanded.nodes}

    assert by_id["checks__ask"].value == (
        "λ $checks__producer.output.value $$HOME"
    )
    assert by_id["checks__ask"].options["when"] == (
        "$checks__producer.output.value == '$producer.output.literal'"
    )
    assert by_id["checks__ask"].options["systemPrompt"] == (
        "system $checks__producer.output.value"
    )
    assert by_id["checks__ask"].options["agents"]["reviewer"] == {
        "description": "describe $checks__producer.output.value",
        "prompt": "agent $checks__producer.output.value",
    }
    hook = by_id["checks__ask"].options["hooks"]["PreToolUse"][0]["response"]
    assert hook["systemMessage"] == "system $checks__producer.output.value"
    assert hook["stopReason"] == "stop $checks__producer.output.value"
    assert hook["hookSpecificOutput"]["permissionDecisionReason"] == (
        "reason $checks__producer.output.value"
    )
    assert hook["hookSpecificOutput"]["additionalContext"] == (
        "context $checks__producer.output.value"
    )
    assert by_id["checks__shell"].value == (
        "printf '%s' '$checks__producer.output.value'"
    )
    assert by_id["checks__inline"].value == (
        "print('$checks__producer.output.value')"
    )
    assert by_id["checks__repeat"].value == {
        "prompt": "loop $checks__producer.output.value",
        "until": "DONE",
        "max_iterations": 2,
        "until_bash": "test -n '$checks__producer.output.value'",
        "interactive": True,
        "gate_message": "gate $checks__producer.output.value",
    }
    assert by_id["checks__approve"].value == {
        "message": "approve $checks__producer.output.value",
        "on_reject": {"prompt": "reject $checks__producer.output.value"},
    }


def test_include_output_alias_selects_ordered_first_sink_and_typed_path(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch include outputs selecting a set, last sink, or untyped projection."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "root-alias",
        name="root-alias",
        nodes=[
            {"id": "checks", "include": "child-alias"},
            {
                "id": "publish",
                "prompt": "publish $checks.output.value",
                "depends_on": ["checks"],
            },
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child-alias",
        name="child-alias",
        nodes=[
            {
                "id": "lint",
                "prompt": "lint",
                "output_format": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
            {"id": "unit", "bash": "true"},
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    compiled = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )
    publish = compiled.package.definition.nodes[-1]

    assert publish.depends_on == ("checks__lint", "checks__unit")
    assert publish.value == "publish $checks__lint.output.value"


@pytest.mark.parametrize(
    ("reference", "scope"),
    (
        ("$checks.lint.output", "root"),
        ("$outside.output", "child"),
    ),
)
def test_include_references_cannot_address_deep_children_or_escape(
    tmp_path: Path,
    workflow_writer,
    reference: str,
    scope: str,
) -> None:
    """Catch include references navigating below aliases or outside a child graph."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    child_nodes: list[dict[str, object]] = [
        {"id": "lint", "prompt": "lint"},
    ]
    root_nodes: list[dict[str, object]] = [
        {"id": "checks", "include": "child-invalid"},
    ]
    if scope == "root":
        root_nodes.append(
            {
                "id": "publish",
                "prompt": reference,
                "depends_on": ["checks"],
            }
        )
    else:
        child_nodes.append(
            {
                "id": "consumer",
                "prompt": reference,
                "depends_on": ["lint"],
            }
        )
    root_path = workflow_writer(
        tmp_path / f"root-invalid-{scope}",
        name=f"root-invalid-{scope}",
        nodes=root_nodes,
    )
    child_path = workflow_writer(
        tmp_path / f"child-invalid-{scope}",
        name="child-invalid",
        nodes=child_nodes,
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
            normalizer_version=4,
        )

    assert exc.value.issues[0].code == "include_reference_invalid"


def test_v4_root_unknown_reference_keeps_inherited_dependency_diagnostic(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch include escape enforcement reclassifying a root graph typo."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "root-unknown",
        name="root-unknown",
        nodes=[{"id": "consumer", "prompt": "$outside.output"}],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root,)),
            normalizer_version=4,
        )

    assert exc.value.issues[0].code == "output_reference_not_declared_dependency"


def test_expanded_and_normalized_nodes_preserve_logical_origin(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch namespace expansion replacing authored source identity with final order."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "origin-root",
        name="origin-root",
        nodes=[
            {"id": "before", "bash": "true"},
            {"id": "checks", "include": "origin-child"},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "origin-child",
        name="origin-child",
        filename="child.yaml",
        nodes=[
            {"id": "first", "bash": "true"},
            {"id": "lint", "prompt": "lint", "depends_on": ["first"]},
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="project",
        precedence=1,
    )
    child = _parse(child_path, source="profile", precedence=2)
    catalog = WorkflowCatalogSnapshot.capture((root, child))

    expanded = expand_workflow_source(root, catalog)
    compiled = compile_workflow(root, catalog, normalizer_version=4)
    source_node = expanded.nodes[-1]
    final_node = compiled.package.definition.nodes[-1]

    assert source_node.source_index == 1
    assert source_node.source_line == child.nodes[1].source_line
    assert source_node.origin == final_node.origin
    assert final_node.source_index == 1
    assert final_node.source_line == child.nodes[1].source_line
    assert final_node.origin is not None
    assert final_node.origin.include_instance_path == ("checks",)
    assert final_node.origin.package_key == "profile:origin-child"
    assert final_node.origin.workflow_name == "origin-child"
    assert final_node.origin.catalog_source == "profile"
    assert final_node.origin.precedence == 2
    assert final_node.origin.definition_location == "child.yaml"
    assert final_node.origin.source_index == 1
    assert final_node.origin.source_line == child.nodes[1].source_line
    assert final_node.origin.expanded_node_id == "checks__lint"


def test_included_reference_diagnostic_uses_bounded_logical_provenance(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch child reference failures losing the include site or leaking host paths."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "diagnostic-root",
        name="diagnostic-root",
        nodes=[{"id": "checks", "include": "diagnostic-child"}],
    )
    child_path = workflow_writer(
        tmp_path / "diagnostic-child",
        name="diagnostic-child",
        filename="child.yaml",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consumer",
                "prompt": "consume $café.output",
                "depends_on": ["producer"],
            },
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
            normalizer_version=4,
        )

    issue = exc.value.issues[0]
    assert issue.code == "output_reference_path_unsupported"
    assert issue.path == "include[checks]/child.yaml:nodes[1].prompt"
    assert issue.source_line == child.nodes[1].field_lines["prompt"]
    assert str(tmp_path) not in issue.path
    assert str(tmp_path) not in issue.message


def test_root_sidecar_include_reference_fans_out_and_child_sidecar_stays_ignored(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch root policy selecting only a sink or importing child policy authority."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "sidecar-root",
        name="sidecar-root",
        nodes=[
            {"id": "build", "bash": "true"},
            {"id": "checks", "include": "sidecar-child"},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "sidecar-child",
        name="sidecar-child",
        nodes=[
            {"id": "lint", "bash": "true"},
            {"id": "unit", "prompt": "unit"},
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=(
            b"language_compatibility: archon-2026-07\n"
            b"outward_action_nodes:\n"
            b"  - build\n"
            b"  - checks\n"
        ),
    )
    child = _parse(
        child_path,
        sidecar_bytes=b"outward_action_nodes:\n  - child-only-unknown\n",
    )

    compiled = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    assert compiled.package.sidecar["outward_action_nodes"] == (
        "build",
        "checks__lint",
        "checks__unit",
    )


def test_root_sidecar_cannot_address_one_deep_child(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch final expanded IDs accidentally becoming root sidecar authoring syntax."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "sidecar-deep-root",
        name="sidecar-deep-root",
        nodes=[{"id": "checks", "include": "sidecar-deep-child"}],
    )
    child_path = workflow_writer(
        tmp_path / "sidecar-deep-child",
        name="sidecar-deep-child",
        nodes=[{"id": "lint", "bash": "true"}],
    )
    root = _parse(
        root_path,
        sidecar_bytes=(
            b"language_compatibility: archon-2026-07\n"
            b"outward_action_nodes:\n"
            b"  - checks__lint\n"
        ),
    )
    child = _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
            normalizer_version=4,
        )

    assert exc.value.issues[0].code == "unknown_sidecar_node"


@pytest.mark.parametrize("surface", ("gate_message", "agent", "hook"))
def test_rewritten_extension_templates_reuse_final_typed_path_validation(
    tmp_path: Path,
    workflow_writer,
    surface: str,
) -> None:
    """Catch rewritten extension fields bypassing inherited impossible-path checks."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    consumer: dict[str, object]
    if surface == "gate_message":
        consumer = {
            "id": "consumer",
            "loop": {
                "prompt": "loop",
                "until": "DONE",
                "max_iterations": 2,
                "interactive": True,
                "gate_message": "gate $producer.output.missing",
            },
            "depends_on": ["producer"],
        }
    elif surface == "agent":
        consumer = {
            "id": "consumer",
            "prompt": "consume",
            "agents": {
                "reviewer": {
                    "description": "review",
                    "prompt": "agent $producer.output.missing",
                }
            },
            "depends_on": ["producer"],
        }
    else:
        consumer = {
            "id": "consumer",
            "prompt": "consume",
            "hooks": {
                "PreToolUse": [
                    {
                        "response": {
                            "systemMessage": "hook $producer.output.missing",
                        }
                    }
                ]
            },
            "depends_on": ["producer"],
        }
    root_path = workflow_writer(
        tmp_path / f"extension-root-{surface}",
        name=f"extension-root-{surface}",
        nodes=[{"id": "checks", "include": f"extension-child-{surface}"}],
    )
    child_path = workflow_writer(
        tmp_path / f"extension-child-{surface}",
        name=f"extension-child-{surface}",
        filename="child.yaml",
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            consumer,
        ],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
            normalizer_version=4,
        )

    assert exc.value.issues[0].code == "structured_output_field_impossible"
    assert exc.value.issues[0].path.startswith("include[checks]/child.yaml:")


@pytest.mark.parametrize("surface", ("agent", "hook"))
def test_extension_template_escape_reports_bounded_include_diagnostic(
    tmp_path: Path,
    workflow_writer,
    surface: str,
) -> None:
    """Catch nested template rewrites leaking internal exceptions or host paths."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    options: dict[str, object]
    expected_suffix: str
    if surface == "agent":
        options = {
            "agents": {
                "reviewer": {
                    "description": "review",
                    "prompt": "agent $outside.output",
                }
            }
        }
        expected_suffix = ".agents.reviewer.prompt"
    else:
        options = {
            "hooks": {
                "PreToolUse": [
                    {
                        "response": {
                            "systemMessage": "hook $outside.output",
                        }
                    }
                ]
            }
        }
        expected_suffix = ".hooks.PreToolUse[0].response.systemMessage"
    root_path = workflow_writer(
        tmp_path / f"escape-root-{surface}",
        name=f"escape-root-{surface}",
        nodes=[{"id": "checks", "include": f"escape-child-{surface}"}],
    )
    child_path = workflow_writer(
        tmp_path / f"escape-child-{surface}",
        name=f"escape-child-{surface}",
        filename="child.yaml",
        nodes=[{"id": "consumer", "prompt": "consume", **options}],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
            normalizer_version=4,
        )

    issue = exc.value.issues[0]
    assert issue.code == "include_reference_invalid"
    assert issue.path == f"include[checks]/child.yaml:nodes[0]{expected_suffix}"
    assert str(tmp_path) not in issue.path
    assert str(tmp_path) not in issue.message
