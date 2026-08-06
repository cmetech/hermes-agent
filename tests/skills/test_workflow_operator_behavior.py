from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import pytest
import yaml

from plugins.workflow.cli import register_cli
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.machine_contract import operator_command_contract
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import preflight_showcase
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workflow_writer():
    def write(root: Path, *, name: str, nodes: list[dict[str, object]]) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.yaml"
        path.write_text(
            yaml.safe_dump({"name": name, "description": name, "nodes": nodes}),
            encoding="utf-8",
        )
        return path

    return write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def _render(template: list[str], **values: object) -> list[str]:
    return [part.format_map(values) for part in template]


def _invoke(parser, capsys, common: list[str], argv: list[str]):
    args = parser.parse_args([*common, *argv])
    exit_code = args.func(args)
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def test_published_contract_constructs_real_commands_without_duplicates(
    tmp_path, workflow_writer, capsys
) -> None:
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="operator-contract",
        nodes=[
            {"id": "gate", "approval": {"message": "Continue?"}},
            {"id": "finish", "bash": "true", "depends_on": ["gate"]},
        ],
    )
    profile = tmp_path / "profile"
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    contract = operator_command_contract()
    commands = contract["commands"]
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]
    values = {
        "workflow_name": "operator-contract",
        "intent_key": "one-semantic-intent",
        "trigger_source": "chat",
    }

    exit_code, started = _invoke(
        parser,
        capsys,
        common,
        _render(commands["run_foreground"], **values),
    )
    assert exit_code == 0
    assert started["ok"] is True
    paused = started["result"]
    assert paused["status"] == "paused"

    decision_values = {
        "run_id": paused["run_id"],
        "interaction_id": paused["pending_interaction"]["interaction_id"],
        "state_version": paused["state_version"],
    }
    exit_code, approved = _invoke(
        parser,
        capsys,
        common,
        _render(commands["approve"], **decision_values),
    )
    assert exit_code == 0
    assert approved["result"]["run_status"] == "succeeded"

    exit_code, replay = _invoke(
        parser,
        capsys,
        common,
        _render(commands["run_foreground"], **values),
    )
    assert exit_code == 0
    assert replay["result"]["run_id"] == paused["run_id"]
    assert replay["result"]["admission_disposition"] == "existing"

    exit_code, unavailable = _invoke(
        parser,
        capsys,
        common,
        _render(commands["run_background"], **values),
    )
    assert exit_code == 6
    assert unavailable["error"]["code"] == "coordinator_unavailable"


def test_showcase_preflight_publishes_the_same_operator_contract(tmp_path) -> None:
    preflight = preflight_showcase("laptop-diagnostic", hermes_home=tmp_path)
    assert preflight["command_contract"] == operator_command_contract()
    assert [item["name"] for item in preflight["input_requirements"]] == [
        "evidence",
        "symptom",
    ]


def test_operator_contract_publishes_complete_exit_code_table() -> None:
    assert operator_command_contract()["exit_codes"] == {
        "success": 0,
        "invocation": 2,
        "not_found": 3,
        "authorization": 4,
        "conflict": 5,
        "coordinator_unavailable": 6,
        "blocking_finding": 7,
        "action_failed": 8,
        "internal": 70,
    }


def test_operator_phase3_guidance_uses_the_dynamic_catalog_authority() -> None:
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    topics = {item["id"]: item for item in contract["documentation"]["topics"]}

    stable_codes = topics["stable-codes"]
    assert stable_codes["code_source"] == "compatibility_codes"
    assert "codes" not in stable_codes
    recovery = topics["persistent-session-recovery"]
    assert recovery["operator_surfaces"] == [
        "workflow doctor",
        "Run Inspector recovery evidence",
    ]


def test_operator_explicit_v4_guidance_keeps_backend_actions_authoritative() -> None:
    """Retrieve the staged interaction contract without activating Archon v4."""
    current = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    phase4 = workflow_authoring_contract(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )
    topics = {item["id"]: item for item in phase4["documentation"]["topics"]}
    parameters = topics["ordinary-loops-and-includes"]["parameters"]

    assert current["normalizer_version"] == 3
    assert phase4["normalizer_version"] == 4
    assert parameters["signal_confirmation_actions"] == {
        "before_final_iteration": ["approve", "provide-input", "cancel"],
        "final_iteration": ["approve", "cancel"],
    }
    assert parameters["wire_actions_reused"] is True
    assert parameters["approval_reexecutes_provider"] is False


def test_operator_skills_are_structured_and_defer_syntax_to_runtime() -> None:
    skill_paths = (
        REPO_ROOT / "skills/productivity/workflow/SKILL.md",
        REPO_ROOT / "skills/productivity/workflow-showcase/SKILL.md",
    )
    for path in skill_paths:
        body = path.read_text(encoding="utf-8")
        frontmatter, content = body.split("---\n", 2)[1:]
        metadata = yaml.safe_load(frontmatter)
        assert metadata["description"].startswith("Use when")
        assert "<objective>" in content
        assert "<quick_start>" in content
        assert "<success_criteria>" in content
        assert not re.search(r"^#{1,6} ", content, flags=re.MULTILINE)
        assert "command_contract" in body

    showcase_root = REPO_ROOT / "skills/productivity/workflow-showcase"
    for relative in (
        "workflows/explain-showcase.md",
        "workflows/run-showcase.md",
        "workflows/resume-and-report.md",
        "workflows/reset-and-cleanup.md",
    ):
        content = (showcase_root / relative).read_text(encoding="utf-8")
        assert "<required_reading>" in content
        assert "<process>" in content
        assert "<success_criteria>" in content
        assert not re.search(r"^#{1,6} ", content, flags=re.MULTILINE)
