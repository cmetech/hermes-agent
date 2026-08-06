from __future__ import annotations

from dataclasses import replace
from jsonschema import Draft202012Validator
import pytest

import plugins.workflow.language_schema as language_schema
from plugins.workflow.language_schema import definition_json_schema
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.schema import parse_workflow_source_bytes


def _parse(path, *, sidecar_bytes=None, source="project", precedence=1):
    return parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
    )


def test_literal_include_is_a_source_directive_but_not_an_executable_kind(
    tmp_path, workflow_writer
) -> None:
    """Catch source parsing or authoring metadata treating includes as runtime nodes."""
    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "build", "bash": "true"},
            {
                "id": "checks",
                "include": "reusable-checks",
                "depends_on": ["build"],
            },
        ],
    )

    root = parse_workflow_source_bytes(
        root_path,
        workflow_bytes=root_path.read_bytes(),
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="project",
        precedence=1,
    )

    assert language_schema.EXECUTABLE_NODE_TYPES == (
        "command",
        "prompt",
        "bash",
        "script",
        "loop",
        "approval",
        "cancel",
    )
    assert language_schema.COMPILE_DIRECTIVE_TYPES == ("include",)
    assert language_schema.SOURCE_NODE_TYPES == (
        *language_schema.EXECUTABLE_NODE_TYPES,
        "include",
    )
    assert [node.node_type for node in root.nodes] == ["bash", "include"]
    Draft202012Validator(
        definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)
    ).validate(
        {
            "name": "root",
            "description": "root workflow",
            "nodes": [
                {"id": "build", "bash": "true"},
                {
                    "id": "checks",
                    "include": "reusable-checks",
                    "depends_on": ["build"],
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "extra",
    (
        {"with": {"branch": "main"}},
        {"when": "$build.output == 'ready'"},
        {"timeout": 30},
        {"retry": {"max_attempts": 2}},
        {"context": "fresh"},
        {"always_run": True},
    ),
)
def test_include_rejects_every_non_structural_execution_field(
    tmp_path, workflow_writer, extra
) -> None:
    """Catch compile directives inheriting runtime node options."""
    path = workflow_writer(
        tmp_path / "invalid-field",
        name="invalid-field",
        nodes=[{"id": "child", "include": "reusable", **extra}],
    )

    with pytest.raises(WorkflowValidationError):
        parse_workflow_source_bytes(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        )


@pytest.mark.parametrize(
    "target",
    (
        "",
        "./reusable",
        "https://example.test/reusable.yaml",
        "$WORKFLOW",
        "${WORKFLOW}",
        "{{ workflow }}",
        "reusable == other",
    ),
)
def test_include_target_must_be_one_literal_portable_workflow_name(
    tmp_path, workflow_writer, target
) -> None:
    """Catch path, URL, variable, and expression resolution entering compilation."""
    path = workflow_writer(
        tmp_path / "invalid-target",
        name="invalid-target",
        nodes=[{"id": "child", "include": target}],
    )

    with pytest.raises(WorkflowValidationError):
        parse_workflow_source_bytes(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        )
    assert not Draft202012Validator(
        definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)
    ).is_valid(
        {
            "name": "invalid-target",
            "description": "invalid",
            "nodes": [{"id": "child", "include": target}],
        }
    )


def test_future_loop_group_shape_cannot_hide_an_include(tmp_path) -> None:
    """Catch recursive include syntax appearing before loop-group compilation exists."""
    workflow_bytes = (
        b"name: invalid-loop-group\n"
        b"description: invalid\n"
        b"nodes:\n"
        b"  - id: group\n"
        b"    loop_group:\n"
        b"      nodes:\n"
        b"        - id: child\n"
        b"          include: reusable\n"
    )

    with pytest.raises(WorkflowValidationError):
        parse_workflow_source_bytes(
            tmp_path / "invalid-loop-group.yaml",
            workflow_bytes=workflow_bytes,
            sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        )


def test_include_expands_depth_first_and_disappears_from_the_raw_graph(
    tmp_path, workflow_writer
) -> None:
    """Catch include directives leaking to the scheduler or losing authored order."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "build", "bash": "true"},
            {"id": "checks", "include": "reusable-checks", "depends_on": ["build"]},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="reusable-checks",
        nodes=[{"id": "lint", "bash": "true"}],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)

    expanded = expand_workflow_source(
        root, WorkflowCatalogSnapshot.capture((root, child))
    )

    assert [node.id for node in expanded.nodes] == ["build", "checks__lint"]
    assert [node.node_type for node in expanded.nodes] == ["bash", "bash"]
    assert expanded.nodes[1].depends_on == ("build",)
    assert expanded.include_aliases["checks"].entries == ("checks__lint",)
    assert expanded.include_aliases["checks"].sinks == ("checks__lint",)
    assert expanded.include_aliases["checks"].first_sink == "checks__lint"
    assert expanded.dependencies == (child,)


def test_include_wires_all_entries_sinks_and_parent_join_rule(
    tmp_path, workflow_writer
) -> None:
    """Catch parent edges or downstream include dependencies becoming single-edge joins."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "build", "bash": "true"},
            {
                "id": "checks",
                "include": "reusable-checks",
                "depends_on": ["build"],
                "trigger_rule": "all_done",
            },
            {"id": "publish", "bash": "true", "depends_on": ["checks"]},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="reusable-checks",
        nodes=[
            {"id": "prepare", "bash": "true"},
            {"id": "unit", "bash": "true", "depends_on": ["prepare"]},
            {"id": "lint", "bash": "true", "depends_on": ["prepare"]},
        ],
    )
    root = _parse(root_path)
    child = _parse(child_path)

    expanded = expand_workflow_source(
        root, WorkflowCatalogSnapshot.capture((root, child))
    )
    by_id = {node.id: node for node in expanded.nodes}

    assert tuple(by_id) == (
        "build",
        "checks__prepare",
        "checks__unit",
        "checks__lint",
        "publish",
    )
    assert by_id["checks__prepare"].depends_on == ("build",)
    assert by_id["checks__prepare"].options["trigger_rule"] == "all_done"
    assert by_id["checks__unit"].depends_on == ("checks__prepare",)
    assert by_id["checks__lint"].depends_on == ("checks__prepare",)
    assert expanded.include_aliases["checks"].entries == ("checks__prepare",)
    assert expanded.include_aliases["checks"].sinks == (
        "checks__unit",
        "checks__lint",
    )
    assert by_id["publish"].depends_on == ("checks__unit", "checks__lint")


def test_nested_and_repeated_includes_namespace_instances_but_dedupe_dependencies(
    tmp_path, workflow_writer
) -> None:
    """Catch package-global expansion state rejecting safe reuse or flattening namespaces."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "first", "include": "child"},
            {"id": "second", "include": "child", "depends_on": ["first"]},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[
            {"id": "before", "bash": "true"},
            {"id": "deep", "include": "grand", "depends_on": ["before"]},
        ],
    )
    grand_path = workflow_writer(
        tmp_path / "grand", name="grand", nodes=[{"id": "scan", "bash": "true"}]
    )
    root, child, grand = (_parse(root_path), _parse(child_path), _parse(grand_path))

    expanded = expand_workflow_source(
        root, WorkflowCatalogSnapshot.capture((root, child, grand))
    )

    assert [node.id for node in expanded.nodes] == [
        "first__before",
        "first__deep__scan",
        "second__before",
        "second__deep__scan",
    ]
    assert expanded.nodes[1].depends_on == ("first__before",)
    assert expanded.nodes[2].depends_on == ("first__deep__scan",)
    assert expanded.nodes[3].depends_on == ("second__before",)
    assert tuple(source.name for source in expanded.dependencies) == ("child", "grand")
    assert expanded.include_aliases["first__deep"].first_sink == "first__deep__scan"
    assert expanded.include_aliases["second"].sinks == ("second__deep__scan",)


def test_include_resolution_reports_not_found_ambiguity_cycle_and_depth(
    tmp_path, workflow_writer
) -> None:
    """Catch catalog and active-stack failures collapsing into missing dependencies."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root", name="root", nodes=[{"id": "use", "include": "child"}]
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[{"id": "back", "include": "root"}],
    )
    root, child = _parse(root_path), _parse(child_path)

    with pytest.raises(WorkflowValidationError) as missing:
        expand_workflow_source(root, WorkflowCatalogSnapshot.capture((root,)))
    assert missing.value.issues[0].code == "include_not_found"
    assert "root -> child" in str(missing.value)

    duplicate_path = workflow_writer(
        tmp_path / "duplicate",
        name="child",
        nodes=[{"id": "other", "bash": "true"}],
    )
    duplicate = _parse(duplicate_path)
    with pytest.raises(WorkflowValidationError) as ambiguous:
        expand_workflow_source(
            root, WorkflowCatalogSnapshot.capture((root, child, duplicate))
        )
    assert ambiguous.value.issues[0].code == "include_ambiguous"
    assert "root -> child" in str(ambiguous.value)

    with pytest.raises(WorkflowValidationError) as cycle:
        expand_workflow_source(root, WorkflowCatalogSnapshot.capture((root, child)))
    assert cycle.value.issues[0].code == "include_cycle"
    assert len(str(cycle.value)) < 1024

    sources = [root]
    previous = root
    for index, name in enumerate(("child", "two", "three", "four")):
        target = ("two", "three", "four", "leaf")[index]
        path = workflow_writer(
            tmp_path / f"depth-{name}",
            name=name,
            nodes=[{"id": f"to-{target}", "include": target}],
        )
        previous = _parse(path)
        sources.append(previous)
    leaf_path = workflow_writer(
        tmp_path / "leaf", name="leaf", nodes=[{"id": "done", "bash": "true"}]
    )
    sources.append(_parse(leaf_path))
    with pytest.raises(WorkflowValidationError) as depth:
        expand_workflow_source(root, WorkflowCatalogSnapshot.capture(sources))
    assert depth.value.issues[0].code == "include_depth_exceeded"
    assert len(str(depth.value)) < 1024


def test_empty_include_and_final_namespace_collision_are_rejected(
    tmp_path, workflow_writer
) -> None:
    """Catch directives producing no executable work or overwriting authored IDs."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "checks__lint", "bash": "true"},
            {"id": "checks", "include": "child"},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child", name="child", nodes=[{"id": "lint", "bash": "true"}]
    )
    root, child = _parse(root_path), _parse(child_path)

    with pytest.raises(WorkflowValidationError) as collision:
        expand_workflow_source(root, WorkflowCatalogSnapshot.capture((root, child)))
    assert collision.value.issues[0].code == "include_id_collision"
    assert collision.value.issues[0].path == "nodes[0].id"
    assert (
        collision.value.issues[0].source_line == child.nodes[0].field_lines["id"]
    )

    empty_child = replace(child, nodes=())
    with pytest.raises(WorkflowValidationError) as empty:
        expand_workflow_source(
            replace(root, nodes=(root.nodes[1],)),
            WorkflowCatalogSnapshot.capture((root, empty_child)),
        )
    assert empty.value.issues[0].code == "include_empty_graph"


def test_cyclic_included_graph_fails_with_a_typed_bounded_topology_issue(
    tmp_path, workflow_writer
) -> None:
    """Catch nonempty child graphs reaching alias indexing without entries or sinks."""
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
            {"id": "first", "bash": "true", "depends_on": ["second"]},
            {"id": "second", "bash": "true", "depends_on": ["first"]},
        ],
    )
    root, child = _parse(root_path), _parse(child_path)

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            root,
            WorkflowCatalogSnapshot.capture((root, child)),
        )

    issue = exc.value.issues[0]
    assert issue.code == "include_empty_graph"
    assert issue.path == "nodes[0].include"
    assert len(issue.message) < 1024


def test_executable_limit_diagnostics_name_real_source_fields(
    tmp_path, workflow_writer
) -> None:
    """Catch executable node/edge failures being reported at nonexistent include fields."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot
    from plugins.workflow.includes import expand_workflow_source
    from plugins.workflow.models import WorkflowCompilationLimits

    path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "first", "bash": "true"},
            {"id": "second", "bash": "true", "depends_on": ["first"]},
        ],
    )
    root = _parse(path)
    catalog = WorkflowCatalogSnapshot.capture((root,))
    base = {
        "max_include_depth": 3,
        "max_dependencies": 64,
        "max_nodes": 512,
        "max_edges": 4096,
        "max_source_bytes": 2 * 1024 * 1024,
        "max_expanded_bytes": 2 * 1024 * 1024,
    }

    with pytest.raises(WorkflowValidationError) as nodes:
        expand_workflow_source(
            root,
            catalog,
            WorkflowCompilationLimits(**{**base, "max_nodes": 0}),
        )
    with pytest.raises(WorkflowValidationError) as edges:
        expand_workflow_source(
            root,
            catalog,
            WorkflowCompilationLimits(**{**base, "max_edges": 0}),
        )

    assert nodes.value.issues[0].path == "nodes[0].id"
    assert nodes.value.issues[0].source_line == root.nodes[0].field_lines["id"]
    assert edges.value.issues[0].path == "nodes[1].depends_on"
    assert (
        edges.value.issues[0].source_line
        == root.nodes[1].field_lines["depends_on"]
    )


def test_compilation_normalizes_expanded_nodes_with_only_root_authority(
    tmp_path, workflow_writer
) -> None:
    """Catch child defaults or sidecars becoming active after structural expansion."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[{"id": "checks", "include": "child"}],
        provider="root-provider",
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[{"id": "lint", "bash": "true"}],
        provider="child-provider",
        interactive=True,
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(
        child_path,
        sidecar_bytes=b"language_compatibility: hermes-legacy\n",
    )

    compiled = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    assert [node.id for node in compiled.package.definition.nodes] == ["checks__lint"]
    assert [node.node_type for node in compiled.package.definition.nodes] == ["bash"]
    assert compiled.package.definition.options["provider"] == "root-provider"
    assert "interactive" not in compiled.package.definition.options
    assert compiled.package.sidecar == {"language_compatibility": "archon-2026-07"}
    assert compiled.package.language.normalizer_version == 4
    assert compiled.active_policy_bytes == b"language_compatibility: archon-2026-07\n"


def test_bounded_namespacing_does_not_ban_maximum_length_authored_ids(
    tmp_path, workflow_writer
) -> None:
    """Catch generated namespaces reusing the narrower authored-ID limit."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.includes import expand_workflow_source

    include_id = "a" * 128
    child_id = "b" * 128
    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[{"id": include_id, "include": "child"}],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[{"id": child_id, "bash": "true"}],
    )
    root = _parse(
        root_path,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse(child_path)
    catalog = WorkflowCatalogSnapshot.capture((root, child))
    expected_id = f"{include_id}__{child_id}"

    expanded = expand_workflow_source(root, catalog)
    compiled = compile_workflow(root, catalog, normalizer_version=4)

    assert expanded.nodes[0].id == expected_id
    assert compiled.package.definition.nodes[0].id == expected_id
