from __future__ import annotations

from plugins.workflow.bash_rendering import render_v3_bash
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.language_schema import NODE_TYPES, workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile
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
        nodes=[
            {
                "id": "prepare",
                "bash": "printf ready",
                "timeout": 120_000,
                "retry": {"max_attempts": 1},
            },
            {
                "id": "script",
                "script": "print('ok')",
                "runtime": "uv",
                "depends_on": ["prepare"],
                "when": "$prepare.output == 'ready'",
            },
            {
                "id": "review",
                "approval": {"message": "Review", "capture_response": True},
                "depends_on": ["script"],
            },
            {
                "id": "cancel",
                "cancel": "bounded cancellation",
                "depends_on": ["review", "script"],
                "when": "$script.output == 'cancel'",
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    report = assess_compatibility(package, available_tools=frozenset())

    assert package.definition.name == "portable-contract"
    assert (
        package.language.effective_profile
        is WorkflowLanguageProfile.ARCHON_2026_07
    )
    assert package.language.normalizer_version == 3
    prepare = package.definition.nodes[0]
    assert prepare.options["timeout"] == 120_000
    assert prepare.options["retry"]["max_attempts"] == 1
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


def test_official_archon_bash_boundary_and_recovery_contract_are_discoverable(
    tmp_path,
) -> None:
    """Exercise representative v3 boundary behavior from the public contract."""
    template = 'printf \'%s\' "$producer.output"'
    start = template.index("$producer.output")
    end = start + len("$producer.output")

    inline = render_v3_bash(
        template,
        [(start, end, "x" * 32_768)],
        spill_directory=tmp_path / "inline",
    )
    spilled = render_v3_bash(
        template,
        [(start, end, "x" * 32_769)],
        spill_directory=tmp_path / "spilled",
    )
    try:
        assert inline.spill_count == 0
        assert spilled.spill_count == 1
        assert spilled.spill_total_bytes == 32_769
    finally:
        inline.close()
        spilled.close()

    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    topics = {item["id"]: item for item in contract["documentation"]["topics"]}
    assert topics["persistent-session-recovery"]["operator_surfaces"] == [
        "workflow doctor",
        "Run Inspector recovery evidence",
    ]


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
