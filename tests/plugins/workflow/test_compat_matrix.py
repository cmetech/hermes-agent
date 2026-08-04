from __future__ import annotations

from dataclasses import asdict, replace
import json
import os

import pytest

from plugins.workflow.compat import (
    ARCHON_TOOL_ALIASES,
    WORKFLOW_COMPATIBILITY_FINDINGS_MAX,
    CompatibilityFinding,
    CompatibilityLevel,
    CompatibilityReport,
    assess_compatibility,
)
from plugins.workflow.cli import doctor_package
from plugins.workflow.models import CompatibilityFinding as ModelCompatibilityFinding
from plugins.workflow.models import ValidationIssue
from plugins.workflow.schema import load_workflow
from plugins.workflow.schema import HOOK_EVENTS
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.store import RunStore


class _ComparisonCountingStr(str):
    comparisons = 0

    def __eq__(self, other: object) -> bool:
        type(self).comparisons += 1
        return super().__eq__(other)

    __hash__ = str.__hash__


def _mapped_finding(index: int, *, blocking: bool = False) -> CompatibilityFinding:
    return CompatibilityFinding(
        path=f"mapped[{index}]",
        level=CompatibilityLevel.MAPPED,
        message=f"mapped finding {index}",
        blocking=blocking,
        code="mapped_finding",
    )


def test_compatibility_dedup_work_is_linear_and_reserves_a_bounded_sentinel(
    workflow_writer, tmp_path
) -> None:
    package = load_workflow(workflow_writer(tmp_path))
    issue_count = 2_000
    issues = tuple(
        ValidationIssue(
            path=_ComparisonCountingStr(f"unknown[{index}]"),
            code=_ComparisonCountingStr("unknown_top_level_field"),
            message="unknown",
            blocking=index == issue_count - 1,
        )
        for index in range(issue_count)
    )
    package = replace(package, validation_issues=issues)
    _ComparisonCountingStr.comparisons = 0

    report = assess_compatibility(package, available_tools=set())

    assert _ComparisonCountingStr.comparisons <= 10 * issue_count
    assert len(report.findings) == WORKFLOW_COMPATIBILITY_FINDINGS_MAX
    sentinel = report.findings[-1]
    assert sentinel.code == "compatibility_findings_truncated"
    assert sentinel.path == "compatibility.findings"
    assert sentinel.level is CompatibilityLevel.UNSUPPORTED
    assert sentinel.blocking is True
    assert report.level is CompatibilityLevel.UNSUPPORTED
    assert report.runnable is False


def test_compatibility_bounds_author_controlled_finding_text_without_losing_code(
    workflow_writer, tmp_path
) -> None:
    package = load_workflow(workflow_writer(tmp_path))
    package = replace(
        package,
        validation_issues=(
            ValidationIssue(
                path='😀\x01"\\' * 5_000,
                code="unknown_top_level_field",
                message='😀\x01"\\' * 5_000,
                blocking=False,
            ),
        ),
    )

    finding = next(
        item
        for item in assess_compatibility(package).findings
        if item.code == "unknown_top_level_field"
    )

    assert len(json.dumps(finding.path, ensure_ascii=True).encode("utf-8")) <= 256
    assert len(json.dumps(finding.message, ensure_ascii=True).encode("utf-8")) <= 512
    assert finding.code == "unknown_top_level_field"


@pytest.mark.parametrize(
    ("unique_count", "expected_length", "expected_truncated"),
    [
        (510, 510, False),
        (511, 511, False),
        (512, 512, True),
        (513, 512, True),
    ],
)
def test_compatibility_finding_boundaries_are_exact(
    workflow_writer,
    tmp_path,
    unique_count,
    expected_length,
    expected_truncated,
) -> None:
    package = load_workflow(workflow_writer(tmp_path / str(unique_count)))
    package = replace(
        package,
        compatibility_findings=(),
        validation_issues=tuple(
            ValidationIssue(
                path=f"unknown[{index}]",
                code="unknown_top_level_field",
                message=f"unknown {index}",
                blocking=False,
            )
            for index in range(unique_count)
        ),
    )

    report = assess_compatibility(package)

    assert len(report.findings) == expected_length
    assert report.findings_truncated is expected_truncated
    assert report.finding_count == unique_count
    assert report.findings[0].path == "unknown[0]"
    if expected_truncated:
        assert report.findings[-2].path == "unknown[510]"
        assert report.findings[-1].code == "compatibility_findings_truncated"
    else:
        assert report.findings[-1].path == f"unknown[{unique_count - 1}]"


def test_compatibility_deduplicates_before_and_after_the_retention_boundary(
    workflow_writer, tmp_path
) -> None:
    package = load_workflow(workflow_writer(tmp_path))
    issues = [
        ValidationIssue(
            path="unknown[0]",
            code="unknown_top_level_field",
            message="first occurrence",
            blocking=False,
        ),
        ValidationIssue(
            path="unknown[0]",
            code="unknown_top_level_field",
            message="duplicate before cap",
            blocking=False,
        ),
        *(
            ValidationIssue(
                path=f"unknown[{index}]",
                code="unknown_top_level_field",
                message=f"unknown {index}",
                blocking=False,
            )
            for index in range(1, 513)
        ),
        ValidationIssue(
            path="unknown[512]",
            code="unknown_top_level_field",
            message="duplicate after cap",
            blocking=False,
        ),
    ]
    package = replace(
        package,
        compatibility_findings=(),
        validation_issues=tuple(issues),
    )

    report = assess_compatibility(package)

    assert report.finding_count == 513
    assert report.findings[0].message == "first occurrence"
    assert report.findings[-1].message.startswith(
        "Compatibility findings truncated: 2 omitted;"
    )


def test_bounded_paths_keep_public_code_path_keys_unique(
    workflow_writer, tmp_path
) -> None:
    package = load_workflow(workflow_writer(tmp_path))
    shared_prefix = "nodes[0]." + "p" * 2_000
    package = replace(
        package,
        compatibility_findings=(),
        validation_issues=tuple(
            ValidationIssue(
                path=shared_prefix + suffix,
                code="unknown_top_level_field",
                message="unknown",
                blocking=False,
            )
            for suffix in ("alpha", "beta")
        ),
    )

    report = assess_compatibility(package)
    public_keys = [(finding.code, finding.path) for finding in report.findings]

    assert len(public_keys) == 2
    assert len(set(public_keys)) == 2
    assert [finding.code for finding in report.findings] == [
        "unknown_top_level_field",
        "unknown_top_level_field",
    ]


def test_authored_generated_path_collisions_survive_normalization_and_append(
    workflow_writer, tmp_path
) -> None:
    long_path = "p" * 2_000 + "alpha"
    probe = load_workflow(
        workflow_writer(
            tmp_path / "probe",
            name="probe-generated-path",
            **{long_path: "x"},
        )
    )
    generated_literal = next(
        finding.path
        for finding in assess_compatibility(probe).findings
        if finding.code == "unknown_top_level_field"
    )
    package = load_workflow(
        workflow_writer(
            tmp_path / "collision",
            name="authored-generated-path-collision",
            **{long_path: "x", generated_literal: "x"},
        )
    )

    report = assess_compatibility(package)
    unknown = [
        finding
        for finding in report.findings
        if finding.code == "unknown_top_level_field"
    ]
    repeated = assess_compatibility(package)

    assert len(unknown) == 2
    assert report.finding_count == 3
    assert unknown[0].path == generated_literal
    assert len({(finding.code, finding.path) for finding in unknown}) == 2
    assert all(
        len(json.dumps(finding.path, ensure_ascii=True).encode("utf-8")) <= 256
        for finding in unknown
    )
    assert [finding.path for finding in repeated.findings] == [
        finding.path for finding in report.findings
    ]

    normalized = CompatibilityReport(
        level=report.level,
        findings=report.findings,
        runnable=report.runnable,
    )
    authored_reserved_path = replace(
        unknown[1],
        path=unknown[1].path,
        message="literal authored collision-resolution path",
    )
    wrapped = CompatibilityReport(
        level=report.level,
        findings=(*normalized.findings, authored_reserved_path),
        runnable=report.runnable,
    )
    wrapped_again = CompatibilityReport(
        level=wrapped.level,
        findings=wrapped.findings,
        runnable=wrapped.runnable,
    )
    wrapped_unknown = [
        finding
        for finding in wrapped.findings
        if finding.code == "unknown_top_level_field"
    ]

    assert normalized.finding_count == report.finding_count
    assert [finding.path for finding in normalized.findings] == [
        finding.path for finding in report.findings
    ]
    assert wrapped.finding_count == 4
    assert len(wrapped_unknown) == 3
    assert len({(finding.code, finding.path) for finding in wrapped_unknown}) == 3
    assert [finding.path for finding in wrapped_unknown[:2]] == [
        finding.path for finding in unknown
    ]
    assert [finding.path for finding in wrapped_again.findings] == [
        finding.path for finding in wrapped.findings
    ]

    equal_copy = replace(unknown[0])
    assert equal_copy == unknown[0]
    assert hash(equal_copy) == hash(unknown[0])
    original_values = (unknown[0], equal_copy)
    substituted_values = (equal_copy, replace(equal_copy))
    assert original_values == substituted_values
    assert hash(original_values) == hash(substituted_values)
    normalized_with_original = CompatibilityReport(
        level=report.level,
        findings=original_values,
        runnable=report.runnable,
    )
    normalized_with_equal_copy = CompatibilityReport(
        level=report.level,
        findings=substituted_values,
        runnable=report.runnable,
    )
    assert normalized_with_original.findings == normalized_with_equal_copy.findings
    assert (
        normalized_with_original.finding_count
        == normalized_with_equal_copy.finding_count
        == 2
    )

    message_replaced = replace(unknown[0], message="message-only replacement")
    replaced_report = CompatibilityReport(
        level=report.level,
        findings=tuple(
            message_replaced if finding is unknown[0] else finding
            for finding in report.findings
        ),
        runnable=report.runnable,
    )
    assert replaced_report.finding_count == report.finding_count
    assert [(finding.code, finding.path) for finding in replaced_report.findings] == [
        (finding.code, finding.path) for finding in report.findings
    ]

    for finding in wrapped_unknown:
        assert vars(finding) == asdict(finding)
        assert repr(finding).startswith("CompatibilityFinding(")
        assert repr(finding).count("=") == len(asdict(finding))


def test_public_path_collision_resolution_is_deep_and_idempotent() -> None:
    colliding = tuple(
        CompatibilityFinding(
            path="authored-reserved-path",
            level=CompatibilityLevel.MAPPED,
            message=f"ordered collision {index}",
            blocking=False,
            code="mapped_finding",
        )
        for index in range(511)
    )

    first = CompatibilityReport(
        level=CompatibilityLevel.MAPPED,
        findings=colliding,
        runnable=True,
    )
    second = CompatibilityReport(
        level=first.level,
        findings=first.findings,
        runnable=first.runnable,
    )
    third = CompatibilityReport(
        level=second.level,
        findings=second.findings,
        runnable=second.runnable,
    )

    assert first.finding_count == 511
    assert first.findings_truncated is False
    assert len({(finding.code, finding.path) for finding in first.findings}) == 511
    assert first.findings[0].message == "ordered collision 0"
    assert first.findings[-1].message == "ordered collision 510"
    assert all(
        len(json.dumps(finding.path, ensure_ascii=True).encode("utf-8")) <= 256
        for finding in first.findings
    )
    assert tuple(asdict(finding) for finding in second.findings) == tuple(
        asdict(finding) for finding in first.findings
    )
    assert tuple(asdict(finding) for finding in third.findings) == tuple(
        asdict(finding) for finding in first.findings
    )


def test_omitted_mapped_blocker_forces_unsupported_sentinel() -> None:
    report = CompatibilityReport(
        level=CompatibilityLevel.UNSUPPORTED,
        findings=tuple(
            _mapped_finding(index, blocking=index == 511) for index in range(512)
        ),
        runnable=False,
    )

    sentinel = report.findings[-1]
    assert sentinel.code == "compatibility_findings_truncated"
    assert sentinel.level is CompatibilityLevel.UNSUPPORTED
    assert sentinel.blocking is True
    assert report.level is CompatibilityLevel.UNSUPPORTED
    assert report.runnable is False


def test_wrapper_append_merges_into_an_existing_truncation_sentinel() -> None:
    initial = CompatibilityReport(
        level=CompatibilityLevel.MAPPED,
        findings=tuple(_mapped_finding(index) for index in range(512)),
        runnable=True,
    )

    wrapped = CompatibilityReport(
        level=CompatibilityLevel.UNSUPPORTED,
        findings=(*initial.findings, _mapped_finding(512, blocking=True)),
        runnable=False,
    )

    assert len(wrapped.findings) == WORKFLOW_COMPATIBILITY_FINDINGS_MAX
    assert wrapped.finding_count == 513
    sentinel = wrapped.findings[-1]
    assert sentinel.code == "compatibility_findings_truncated"
    assert sentinel.level is CompatibilityLevel.UNSUPPORTED
    assert sentinel.blocking is True
    assert sentinel.message.startswith("Compatibility findings truncated: 2 omitted;")


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
            {"idle_timeout": 2},
            "legacy_idle_timeout_seconds",
            False,
            "warning",
        ),
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
        if {"timeout", "idle_timeout"}.intersection(node_options)
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
