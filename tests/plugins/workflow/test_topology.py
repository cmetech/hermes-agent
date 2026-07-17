from __future__ import annotations

from plugins.workflow.schema import load_workflow
from plugins.workflow.topology import project_topology, sanitize_topology_label


def _load(workflow_writer, root, nodes, *, description="fixture"):
    return load_workflow(
        workflow_writer(root, nodes=nodes, description=description)
    ).definition


def test_sequential_fanout_fanin_and_disconnected_projection(workflow_writer, tmp_path):
    definition = _load(
        workflow_writer,
        tmp_path,
        [
            {"id": "collect", "bash": "true"},
            {"id": "orphan", "prompt": "x"},
            {"id": "security", "prompt": "x", "depends_on": ["collect"]},
            {"id": "commercial", "prompt": "x", "depends_on": ["collect"]},
            {
                "id": "approval",
                "approval": {"message": "go"},
                "depends_on": ["security", "commercial"],
            },
            {"id": "send", "command": "send", "depends_on": ["approval"]},
        ],
    )

    projection = project_topology(definition)

    assert projection.node_count == 6
    assert projection.edge_count == 5
    assert (
        projection.text
        == "[collect, orphan] -> [security, commercial] -> approval -> send"
    )
    assert projection.mermaid is not None
    assert projection.mermaid.startswith("flowchart LR\n")
    assert 'n0["collect (bash)"]' in projection.mermaid
    assert "n0 --> n2" in projection.mermaid
    assert "```" not in projection.mermaid
    assert projection.warnings == ()
    assert project_topology(definition) == projection


def test_adversarial_bodies_never_enter_projection(workflow_writer, tmp_path):
    canary = 'SECRET_CANARY %%{init:evil}%% click n0 href classDef x `<b>` https://evil.invalid [x] "\nnext'
    definition = _load(
        workflow_writer,
        tmp_path,
        [
            {"id": "safe", "prompt": canary},
            {"id": "next", "bash": "true", "depends_on": ["safe"]},
        ],
        description=canary,
    )

    projection = project_topology(definition)

    assert "SECRET_CANARY" not in projection.text
    assert projection.mermaid is not None
    for forbidden in ("SECRET_CANARY", "%%{", "click ", "classDef", "<b>", "https://"):
        assert forbidden not in projection.mermaid


def test_label_sanitizer_is_bounded_and_cannot_emit_directives():
    unsafe = 'alpha\n%%{init:x}%% click classDef `<b>` https://x [x] "' + "z" * 100
    label, truncated = sanitize_topology_label(unsafe)

    assert truncated is True
    assert len(label) == 80
    assert label.endswith("…")
    assert "\n" not in label
    assert '"' not in label
    for forbidden in ("%%{", "`", "<", ">", "[", "]"):
        assert forbidden not in label


def test_mermaid_100_node_boundary_and_stable_source_order(workflow_writer, tmp_path):
    nodes = [
        {
            "id": f"node-{index:03d}",
            "bash": "true",
            **({"depends_on": [f"node-{index - 1:03d}"]} if index else {}),
        }
        for index in range(100)
    ]
    definition = _load(workflow_writer, tmp_path, nodes)

    projection = project_topology(definition)

    assert projection.mermaid is not None
    assert 'n99["node-099 (bash)"]' in projection.mermaid
    assert projection.mermaid.count(" --> ") == 99


def test_mermaid_disables_above_node_limit_but_text_remains(workflow_writer, tmp_path):
    definition = _load(
        workflow_writer,
        tmp_path,
        [{"id": f"n-{index:03d}", "bash": "true"} for index in range(101)],
    )

    projection = project_topology(definition)

    assert projection.text
    assert projection.mermaid is None
    assert projection.warnings == ("topology_mermaid_too_many_nodes",)


def test_mermaid_disables_above_edge_limit(workflow_writer, tmp_path):
    nodes = []
    for index in range(22):
        nodes.append({
            "id": f"n-{index:02d}",
            "bash": "true",
            **(
                {"depends_on": [f"n-{source:02d}" for source in range(index)]}
                if index
                else {}
            ),
        })
    projection = project_topology(_load(workflow_writer, tmp_path, nodes))

    assert projection.edge_count == 231
    assert projection.mermaid is None
    assert projection.warnings == ("topology_mermaid_too_many_edges",)


def test_projection_reports_label_truncation(workflow_writer, tmp_path):
    identifier = "n" * 100
    projection = project_topology(
        _load(workflow_writer, tmp_path, [{"id": identifier, "bash": "true"}])
    )

    assert projection.mermaid is not None
    assert projection.warnings == ("topology_label_truncated",)
    assert "…" in projection.mermaid


def test_large_text_projection_is_utf8_bounded_with_deterministic_warnings(
    workflow_writer, tmp_path
):
    nodes = [
        {"id": f"{index:03d}-" + "界" * 100, "bash": "true"} for index in range(200)
    ]

    first = project_topology(_load(workflow_writer, tmp_path, nodes))
    second = project_topology(_load(workflow_writer, tmp_path / "again", nodes))

    assert len(first.text.encode("utf-8")) <= 12 * 1024
    assert first.text.endswith("[topology omitted: 200 nodes, 0 edges]")
    assert first.warnings == (
        "topology_text_truncated",
        "topology_label_truncated",
        "topology_mermaid_too_many_nodes",
    )
    assert first == second
