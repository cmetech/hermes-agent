from __future__ import annotations

from dataclasses import replace
import os

import pytest

from plugins.workflow.compat import (
    ARCHON_TOOL_ALIASES,
    CompatibilityFinding,
    CompatibilityLevel,
    assess_compatibility,
)
from plugins.workflow.cli import doctor_package
from plugins.workflow.models import CompatibilityFinding as ModelCompatibilityFinding
from plugins.workflow.schema import load_workflow
from plugins.workflow.schema import HOOK_EVENTS
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.store import RunStore


def test_portable_mapped_and_unsupported_fields_are_reported(workflow_writer, tmp_path):
    path = workflow_writer(
        tmp_path,
        provider="claude",
        worktree={"enabled": True},
        requires=["github"],
        persist_sessions=True,
        nodes=[
            {
                "id": "agent",
                "prompt": "x",
                "context": "shared",
                "persist_session": True,
                "allowed_tools": ["Read", "Grep"],
                "skills": ["review"],
                "mcp": "mcp/echo.yaml",
                "hooks": {
                    "PreToolUse": [
                        {
                            "response": {
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                }
                            }
                        }
                    ]
                },
            },
            {"id": "gate", "approval": {"message": "go"}, "depends_on": ["agent"]},
        ],
    )
    report = assess_compatibility(
        load_workflow(path),
        available_tools={"read_file", "search_files"},
        available_services={"github"},
        provider_capabilities={"claude": {"hooks", "reasoning", "fallback_model"}},
        isolated_workdir=True,
        mcp_available=True,
    )

    assert report.runnable is True
    assert report.level is CompatibilityLevel.MAPPED
    by_path = {finding.path: finding for finding in report.findings}
    assert by_path["nodes[0].context"].level is CompatibilityLevel.MAPPED
    assert by_path["nodes[0].allowed_tools[0]"].message.endswith("Read -> read_file")
    assert by_path["nodes[0].hooks.PreToolUse"].level is CompatibilityLevel.MAPPED
    assert not any(finding.blocking for finding in report.findings)


def test_unknown_tool_alias_and_unsupported_hook_block_execution(
    workflow_writer, tmp_path
):
    path = workflow_writer(
        tmp_path,
        provider="codex",
        nodes=[
            {
                "id": "agent",
                "prompt": "x",
                "allowed_tools": ["UnknownTool"],
                "hooks": {
                    "Notification": [
                        {"response": {"systemMessage": "x"}},
                    ]
                },
            }
        ],
    )

    report = assess_compatibility(load_workflow(path), available_tools=set())

    assert report.level is CompatibilityLevel.UNSUPPORTED
    assert report.runnable is False
    assert {finding.path for finding in report.blocking_findings} == {
        "nodes[0].allowed_tools[0]",
        "nodes[0].hooks.Notification",
    }


def test_provider_specific_controls_require_advertised_capabilities(
    workflow_writer, tmp_path
):
    path = workflow_writer(
        tmp_path,
        provider="custom",
        modelReasoningEffort="high",
        webSearchMode="auto",
        sandbox={"enabled": True},
        nodes=[
            {
                "id": "agent",
                "prompt": "x",
                "effort": "high",
                "thinking": "adaptive",
                "maxBudgetUsd": 1,
            }
        ],
    )

    report = assess_compatibility(
        load_workflow(path),
        provider_capabilities={"custom": set()},
    )

    assert report.runnable is False
    paths = {finding.path for finding in report.blocking_findings}
    assert {
        "modelReasoningEffort",
        "webSearchMode",
        "sandbox",
        "nodes[0].effort",
        "nodes[0].thinking",
        "nodes[0].maxBudgetUsd",
    } <= paths


def test_archon_alias_table_is_data_driven_and_complete():
    assert ARCHON_TOOL_ALIASES == {
        "Agent": "workflow_agent",
        "Bash": "terminal",
        "Edit": "patch",
        "Glob": "search_files",
        "Grep": "search_files",
        "Read": "read_file",
        "Task": "workflow_agent",
        "WebFetch": "web_extract",
        "WebSearch": "web_search",
        "Write": "write_file",
    }


def test_unknown_top_level_fields_are_explicit_compatibility_findings(
    workflow_writer, tmp_path
):
    package = load_workflow(workflow_writer(tmp_path, futureOption=True))

    report = assess_compatibility(package)

    finding = next(item for item in report.findings if item.path == "futureOption")
    assert finding.level is CompatibilityLevel.UNSUPPORTED
    assert finding.code == "unknown_top_level_field"
    assert finding.blocking is False
    assert finding.effective_profile is package.language.effective_profile
    assert report.runnable is True


@pytest.mark.parametrize("declaration", [None, "hermes-legacy"])
def test_legacy_profile_finding_is_stable_for_default_and_explicit_declarations(
    workflow_writer, tmp_path, declaration
):
    path = workflow_writer(tmp_path / str(declaration))
    if declaration is not None:
        path.with_name(f"{path.stem}.hermes.yaml").write_text(
            f"language_compatibility: {declaration}\n", encoding="utf-8"
        )

    finding = next(
        item
        for item in assess_compatibility(load_workflow(path)).findings
        if item.code == "legacy_language_profile"
    )

    assert finding.blocking is False
    assert finding.severity == "warning"
    assert finding.effective_profile.value == "hermes-legacy"
    assert finding.migration


@pytest.mark.parametrize(
    ("profile", "node_options", "code", "blocking", "severity"),
    [
        (
            "hermes-legacy",
            {"timeout": 2},
            "legacy_timeout_seconds",
            False,
            "warning",
        ),
        (
            "hermes-legacy",
            {"retry": {"max_attempts": 2}},
            "legacy_retry_total_attempts",
            False,
            "warning",
        ),
        (
            "hermes-legacy",
            {"output_format": {"type": "object"}},
            "legacy_output_format_post_validation",
            False,
            "warning",
        ),
        (
            "hermes-legacy",
            {"output_type": "text"},
            "legacy_output_type_not_published",
            False,
            "warning",
        ),
        (
            "archon-2026-07",
            {"timeout": 2},
            "archon_timeout_semantics_unavailable",
            True,
            "error",
        ),
        (
            "archon-2026-07",
            {"retry": {"max_attempts": 2}},
            "archon_retry_semantics_unavailable",
            True,
            "error",
        ),
        (
            "archon-2026-07",
            {"output_format": {"type": "object"}},
            "archon_output_format_unavailable",
            True,
            "error",
        ),
        (
            "archon-2026-07",
            {"output_type": "text"},
            "archon_output_type_unavailable",
            True,
            "error",
        ),
        (
            "archon-2026-07",
            {"maxBudgetUsd": 1},
            "archon_budget_enforcement_unavailable",
            True,
            "error",
        ),
        (
            "archon-2026-07",
            {"sandbox": {"enabled": True}},
            "archon_sandbox_enforcement_unavailable",
            True,
            "error",
        ),
    ],
)
def test_language_profile_fields_emit_stable_findings(
    workflow_writer,
    tmp_path,
    profile,
    node_options,
    code,
    blocking,
    severity,
):
    node = (
        {"id": "agent", "bash": "true", **node_options}
        if "timeout" in node_options
        else {"id": "agent", "prompt": "x", **node_options}
    )
    path = workflow_writer(tmp_path / code, nodes=[node])
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        f"language_compatibility: {profile}\n", encoding="utf-8"
    )

    finding = next(
        item
        for item in assess_compatibility(load_workflow(path)).findings
        if item.code == code
    )

    assert finding.blocking is blocking
    assert finding.severity == severity
    assert finding.effective_profile.value == profile
    assert finding.migration


def test_archon_profile_with_enforceable_fields_remains_runnable(
    workflow_writer, tmp_path
):
    path = workflow_writer(tmp_path, nodes=[{"id": "start", "bash": "true"}])
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    report = assess_compatibility(load_workflow(path))

    assert report.runnable is True
    assert not report.blocking_findings


def test_doctor_preserves_finding_codes_instead_of_deriving_them_from_prose(
    workflow_writer, tmp_path
):
    package = load_workflow(workflow_writer(tmp_path))
    package = replace(
        package,
        compatibility_findings=(
            CompatibilityFinding(
                path="nodes[0].allowed_tools[0]",
                level=CompatibilityLevel.MAPPED,
                message="unknown Archon tool alias appears only as explanatory prose",
                blocking=False,
                code="stable_contract_code",
                severity="warning",
                effective_profile=package.language.effective_profile,
                migration="keep the backend-issued code",
            ),
        ),
    )

    report = doctor_package(package, hermes_home=tmp_path / "profile")

    assert any(
        finding.code == "stable_contract_code" for finding in report.findings
    )
    assert not any(finding.code == "unknown_tool_alias" for finding in report.findings)


def test_doctor_dynamic_findings_carry_the_package_effective_profile(
    workflow_writer, tmp_path
):
    package = load_workflow(workflow_writer(tmp_path))

    report = doctor_package(package, hermes_home=tmp_path / "profile")
    finding = next(
        item
        for item in report.findings
        if item.code == "effective_admission_capacity"
    )

    assert finding.effective_profile is package.language.effective_profile


def test_default_finding_severity_tracks_blocking_state():
    blocking = CompatibilityFinding(
        path="field",
        level=CompatibilityLevel.UNSUPPORTED,
        message="unsupported",
        blocking=True,
    )
    ordinary = CompatibilityFinding(
        path="field",
        level=CompatibilityLevel.MAPPED,
        message="mapped",
        blocking=False,
    )

    assert blocking.severity == "error"
    assert ordinary.severity == "info"


def test_compatibility_module_reexports_the_shared_finding_class():
    assert CompatibilityFinding is ModelCompatibilityFinding


def test_ai_only_fields_on_non_ai_nodes_block_instead_of_being_ignored(
    workflow_writer, tmp_path
):
    package = load_workflow(
        workflow_writer(
            tmp_path,
            nodes=[
                {
                    "id": "shell",
                    "bash": "true",
                    "allowed_tools": ["Read"],
                    "provider": "claude",
                }
            ],
        )
    )

    report = assess_compatibility(package)

    assert report.runnable is False
    assert {finding.path for finding in report.blocking_findings} == {
        "nodes[0].allowed_tools",
        "nodes[0].provider",
    }


def test_every_published_hook_event_is_classified(workflow_writer, tmp_path):
    hooks = {
        event: [{"response": {"systemMessage": "bounded"}}]
        for event in sorted(HOOK_EVENTS)
    }
    package = load_workflow(
        workflow_writer(
            tmp_path,
            provider="claude",
            nodes=[{"id": "agent", "prompt": "x", "hooks": hooks}],
        )
    )

    report = assess_compatibility(
        package,
        provider_capabilities={"claude": {"hooks"}},
        mcp_available=True,
    )
    hook_findings = {
        finding.path.rsplit(".", 1)[-1]: finding
        for finding in report.findings
        if ".hooks." in finding.path
    }

    assert set(hook_findings) == set(HOOK_EVENTS)
    assert {event for event, finding in hook_findings.items() if finding.blocking} == {
        "ConfigChange",
        "Notification",
        "PreCompact",
        "Stop",
        "TeammateIdle",
        "WorktreeCreate",
        "WorktreeRemove",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point boundary")
def test_windows_log_evidence_rejects_reparse_parent(
    workflow_writer, tmp_path
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="windows-reparse")
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="windows-reparse",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="windows-reparse-intent",
            concurrency_key="windows-reparse",
        ),
        immutable_snapshot=prepared,
    )
    outside = tmp_path / "outside" / "a1"
    outside.mkdir(parents=True)
    (outside / "stdout.txt").write_text("REPARSE_ESCAPE_SENTINEL")
    nodes = store.run_directory(admitted.run_id) / "nodes"
    nodes.mkdir(exist_ok=True)
    os.symlink(outside.parent, nodes / "n1", target_is_directory=True)

    page = EvidenceReader(store).query(admitted.run_id, kind="logs")

    assert "REPARSE_ESCAPE_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]
