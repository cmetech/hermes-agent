from __future__ import annotations

from pathlib import Path

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, validate_package
from plugins.workflow.store import RunStore


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "software-development" / "workflow-builder"
FIXTURES = ROOT / "tests" / "plugins" / "workflow" / "fixtures" / "builder"


def test_builder_skill_encodes_safe_whole_package_authoring_contract() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    for requirement in (
        "one decision at a time",
        "plain language",
        "commands/",
        "scripts/",
        "mcp/",
        "Archon",
        "approval",
        "fresh",
        "queue",
        "PRODUCT_CLI workflow doctor",
        "package digest",
        "explicit confirmation",
        "Never write trust",
        "Never call a model or connect to MCP during doctor",
    ):
        assert requirement.lower() in text.lower()

    doctor_position = text.index("PRODUCT_CLI workflow doctor")
    run_position = text.index("PRODUCT_CLI workflow run")
    assert doctor_position < run_position
    assert "Ericsson" not in text


def test_builder_references_cover_portable_shape_and_authoring_gate() -> None:
    schema = (SKILL / "references" / "portable-schema.md").read_text(
        encoding="utf-8"
    )
    checklist = (SKILL / "references" / "authoring-checklist.md").read_text(
        encoding="utf-8"
    )

    for field in ("nodes", "depends_on", "allowed_tools", "hooks", "agents"):
        assert f"`{field}`" in schema
    for resource in ("workflows/", "commands/", "scripts/", "mcp/"):
        assert resource in schema
    for gate in (
        "immutable input",
        "overlap",
        "outward",
        "resource ceilings",
        "doctor",
        "trust",
    ):
        assert gate in checklist.lower()


def test_builder_contract_fixtures_are_valid_portable_packages() -> None:
    workflows = sorted(
        path
        for path in FIXTURES.glob("*/workflows/*.yaml")
        if not path.name.endswith(".hermes.yaml")
    )
    assert [path.parent.parent.name for path in workflows] == [
        "approval-rework",
        "minimal-on-demand",
        "scheduled-report",
    ]
    for workflow in workflows:
        package = load_workflow(workflow)
        assert not any(issue.blocking for issue in validate_package(package))


def test_builder_minimal_fixture_runs_offline_through_ordinary_store(
    tmp_path: Path,
) -> None:
    workflow = FIXTURES / "minimal-on-demand" / "workflows" / "minimal.yaml"
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    snapshot = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="test",
            idempotency_key="builder-minimal-offline",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=snapshot,
    )

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert all(node["state"] == "succeeded" for node in result["nodes"].values())
