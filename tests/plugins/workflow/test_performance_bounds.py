from __future__ import annotations

import time

from plugins.workflow.schema import load_workflow
from plugins.workflow.topology import project_topology


def test_thousand_node_projection_is_bounded_and_disables_mermaid(
    tmp_path, workflow_writer
) -> None:
    nodes = [
        {"id": f"node-{index:04d}", "bash": "true", **({"depends_on": [f"node-{index - 1:04d}"]} if index else {})}
        for index in range(1000)
    ]
    package = load_workflow(
        workflow_writer(tmp_path / "large", name="large", nodes=nodes)
    )
    started = time.perf_counter()
    result = project_topology(package.definition)
    elapsed = time.perf_counter() - started

    assert result.node_count == 1000
    assert result.edge_count == 999
    assert len(result.text.encode("utf-8")) <= 12 * 1024
    assert result.mermaid is None
    assert "topology_mermaid_too_many_nodes" in result.warnings
    assert elapsed < 2.0


def test_topology_injection_canaries_remain_strict_graph_grammar(
    tmp_path, workflow_writer
) -> None:
    node_id = "x%%{init:evil}%%-script-alert-1-click-style-class-quote-newline"
    package = load_workflow(
        workflow_writer(tmp_path / "canary", nodes=[{"id": node_id, "bash": "true"}])
    )
    result = project_topology(package.definition)

    assert result.mermaid is not None
    assert "%%" not in result.mermaid
    assert "<" not in result.mermaid
    assert "click " not in result.mermaid
    assert result.mermaid.splitlines()[0] == "flowchart LR"
