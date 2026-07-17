from __future__ import annotations

from plugins.workflow.compat import assess_compatibility
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import load_showcase_catalog, run_showcase


def test_archon_shape_and_installed_offline_showcases_need_no_yaml_rewrite(
    tmp_path, workflow_writer
) -> None:
    root = tmp_path / "portable"
    workflow = workflow_writer(
        root,
        name="portable-contract",
        persist_sessions=True,
        variables={"focus": "synthetic"},
        nodes=[
            {"id": "prepare", "bash": "printf ready", "timeout": 5},
            {"id": "script", "script": "print('ok')", "runtime": "uv", "depends_on": ["prepare"]},
            {"id": "review", "approval": {"message": "Review", "capture_response": True}, "depends_on": ["script"]},
            {"id": "cancel", "cancel": "bounded cancellation", "depends_on": ["review"], "when": "$script.output == 'cancel'"},
        ],
    )
    package = load_workflow(workflow)
    report = assess_compatibility(package, available_tools=frozenset())

    assert package.definition.name == "portable-contract"
    assert report.runnable
    assert [node.node_type for node in package.definition.nodes] == ["bash", "script", "approval", "cancel"]
    assert len(load_showcase_catalog()) == 4
    offline = run_showcase("resilience", hermes_home=tmp_path / "home", symptom="retry")
    assert offline["status"] == "succeeded"
