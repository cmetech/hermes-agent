from __future__ import annotations

from pathlib import Path

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.provenance import TriggerProvenance
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
    schema_words = " ".join(schema.lower().split())
    checklist_words = " ".join(checklist.lower().split())

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

    for phase3_contract in (
        "120,000 ms",
        "retries after the initial attempt",
        "direct dependency",
        "32,768-byte",
        "confirmed missing cross-run session",
        "compatibility_codes",
    ):
        assert phase3_contract.lower() in schema.lower()
    assert "blocked pending Phase 3" not in schema
    assert "normalizer v4 is not the current archon default" in schema_words

    assert "Archon timeout and retry fields are supported" in checklist
    assert "normalizer v4 is not the current archon default" in checklist_words


def test_builder_references_retrieve_complete_explicit_v4_authoring_guidance() -> None:
    """Pressure-test the references for a root/include/confirmed-loop request."""
    schema = (SKILL / "references" / "portable-schema.md").read_text(
        encoding="utf-8"
    ).lower()
    checklist = (SKILL / "references" / "authoring-checklist.md").read_text(
        encoding="utf-8"
    ).lower()
    guidance = f"{schema}\n{checklist}"
    schema_words = " ".join(schema.split())

    for required in (
        "normalizer_version=4",
        "compile-only",
        "root companion",
        "ignored child",
        "depth 3",
        "64 distinct",
        "512 executable",
        "4,096",
        "first sink",
        "signal_completes",
        "provide-input",
        "final iteration",
        "sealed",
        "source deletion",
    ):
        assert required in guidance

    assert "live child workflow" not in guidance
    assert "loop_group" in guidance
    assert schema_words.index(
        "normalizer v4 is not the current archon default"
    ) < schema_words.index("review warnings and all stable include codes before trust")


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
    intent_key = "builder-minimal-offline"
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=intent_key,
            concurrency_key=package.definition.name,
            provenance=TriggerProvenance.local_admin_claim(
                source="cli",
                intent_key=intent_key,
                source_instance="workflow-builder-test",
            ),
        ),
        immutable_snapshot=snapshot,
    )

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert all(node["state"] == "succeeded" for node in result["nodes"].values())
    assert result["provenance"]["source"] == "cli"
    assert result["provenance"]["assurance"] == "local_admin_claim"
