from __future__ import annotations

import json
from pathlib import Path

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.language import (
    CURRENT_NORMALIZER_BY_PROFILE,
    SUPPORTED_NORMALIZER_VERSIONS,
)
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, validate_package
from plugins.workflow.store import RunStore


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "software-development" / "workflow-builder"
FIXTURES = ROOT / "tests" / "plugins" / "workflow" / "fixtures" / "builder"
VERSION_SELECTION_MARKER = "<!-- workflow-language-version-selection -->"
GUIDANCE_PATHS = (
    ROOT / "website" / "docs" / "user-guide" / "features" / "workflows.md",
    ROOT
    / "website"
    / "docs"
    / "user-guide"
    / "features"
    / "workflow-yaml-reference.md",
    SKILL / "references" / "portable-schema.md",
    SKILL / "references" / "authoring-checklist.md",
)


def _version_selection_from_guidance(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    marked = text.split(VERSION_SELECTION_MARKER, maxsplit=1)[1]
    payload = marked.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return json.loads(payload)


def _runtime_version_selection() -> dict[str, object]:
    current = {
        profile.value: version
        for profile, version in CURRENT_NORMALIZER_BY_PROFILE.items()
    }
    for profile, version in CURRENT_NORMALIZER_BY_PROFILE.items():
        assert workflow_authoring_contract(profile)["normalizer_version"] == version
    for version in SUPPORTED_NORMALIZER_VERSIONS:
        assert workflow_authoring_contract(
            WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=version,
        )["normalizer_version"] == version
    return {
        "current_normalizer_by_profile": current,
        "supported_normalizer_versions": sorted(SUPPORTED_NORMALIZER_VERSIONS),
    }


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

    assert "Archon timeout and retry fields are supported" in checklist


def test_operator_guidance_matches_generated_normalizer_selection() -> None:
    expected = _runtime_version_selection()

    assert {
        path.relative_to(ROOT).as_posix(): _version_selection_from_guidance(path)
        for path in GUIDANCE_PATHS
    } == {path.relative_to(ROOT).as_posix(): expected for path in GUIDANCE_PATHS}


def test_builder_references_retrieve_complete_v4_authoring_guidance() -> None:
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
        "current_normalizer_by_profile",
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
        VERSION_SELECTION_MARKER
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
