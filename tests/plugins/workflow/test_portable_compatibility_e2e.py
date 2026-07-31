from __future__ import annotations

from plugins.workflow.compat import assess_compatibility
from plugins.workflow.language_schema import NODE_TYPES
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
            {
                "id": "script",
                "script": "print('ok')",
                "runtime": "uv",
                "depends_on": ["prepare"],
            },
            {
                "id": "review",
                "approval": {"message": "Review", "capture_response": True},
                "depends_on": ["script"],
            },
            {
                "id": "cancel",
                "cancel": "bounded cancellation",
                "depends_on": ["review"],
                "when": "$script.output == 'cancel'",
            },
        ],
    )
    package = load_workflow(workflow)
    report = assess_compatibility(package, available_tools=frozenset())

    assert package.definition.name == "portable-contract"
    assert report.runnable
    assert [node.node_type for node in package.definition.nodes] == [
        "bash",
        "script",
        "approval",
        "cancel",
    ]
    showcase_catalog = load_showcase_catalog()
    assert set(showcase_catalog) == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }
    assert all(
        showcase_catalog[name].verified_bundled_provenance
        for name in ("approval-gate", "laptop-diagnostic", "resilience")
    )
    offline = run_showcase("resilience", hermes_home=tmp_path / "home", symptom="retry")
    assert offline["status"] == "succeeded"


def test_mcp_and_skills_stay_ai_node_options_in_the_archon_contract(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "extensions",
        name="archon-extension-shape",
        nodes=[
            {
                "id": "prompt-node",
                "prompt": "inspect",
                "mcp": "echo.yaml",
                "skills": ["ascii-art"],
            },
            {
                "id": "command-node",
                "command": "inspect",
                "mcp": "echo.yaml",
                "skills": ["ascii-art"],
                "depends_on": ["prompt-node"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    package = load_workflow(workflow)

    assert [node.node_type for node in package.definition.nodes] == [
        "prompt",
        "command",
    ]
    for node in package.definition.nodes:
        assert node.options["mcp"] == "echo.yaml"
        assert node.options["skills"] == ("ascii-art",)
    assert {"mcp", "skills"}.isdisjoint(NODE_TYPES)
