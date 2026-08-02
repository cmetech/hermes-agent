from __future__ import annotations

import json
from dataclasses import replace

import pytest

import plugins.workflow.language_schema as language_schema
from plugins.workflow.execution_semantics import (
    WorkflowExecutionSemanticsError,
    build_phase3_execution_semantics,
    read_phase3_execution_semantics,
)
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    verify_language_snapshot,
)
from plugins.workflow.language_schema import compatibility_code_catalog
from plugins.workflow.models import (
    RunExecutionLimits,
    WorkflowLanguageProfile,
    WorkflowValidationError,
)
from plugins.workflow.schema import load_workflow


def test_phase3_durable_code_metadata_is_unique_bounded_and_versioned() -> None:
    codes = [item.code for item in language_schema.PHASE3_DURABLE_CODES]

    assert len(codes) == len(set(codes))
    assert all(
        item.profiles == frozenset({WorkflowLanguageProfile.ARCHON_2026_07})
        and item.normalizer_versions == frozenset({3})
        for item in language_schema.PHASE3_DURABLE_CODES
    )
    assert len(
        json.dumps([item.to_dict() for item in language_schema.PHASE3_DURABLE_CODES])
    ) < 8_192


_DURABLE_BEHAVIOR_CASES = (
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "timeout": 1},
            "archon_timeout_node_unsupported",
        ),
        (
            {"id": "cancel", "cancel": "stop", "idle_timeout": 1},
            "archon_idle_timeout_node_unsupported",
        ),
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "retry": {}},
            "archon_retry_node_unsupported",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {}},
            "archon_retry_max_attempts_required",
        ),
        (
            {"id": "shell", "bash": "true", "timeout": 0},
            "archon_timeout_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "idle_timeout": False},
            "archon_idle_timeout_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"future": 1}},
            "archon_retry_invalid",
        ),
)


@pytest.mark.parametrize(
    ("node", "expected"),
    _DURABLE_BEHAVIOR_CASES,
)
def test_phase3_catalog_metadata_matches_real_normalization_failure(
    tmp_path, workflow_writer, node, expected
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[node],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    emitted = exc.value.issues[0].code
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    assert emitted == expected
    assert catalog[emitted]["area"] == "normalization"
    assert catalog[emitted]["normalizer_versions"] == [3]
    assert catalog[emitted]["runtime_failure"] is True
    assert catalog[emitted]["evidence"] is False


def test_every_task1_normalization_behavior_has_catalog_metadata() -> None:
    catalog_codes = {item.code for item in language_schema.PHASE3_DURABLE_CODES}
    behavior_codes = {expected for _node, expected in _DURABLE_BEHAVIOR_CASES}

    assert behavior_codes <= catalog_codes


def test_task2_snapshot_mismatch_codes_have_additive_catalog_metadata() -> None:
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    for code in (
        "workflow_language_snapshot_mismatch",
        "workflow_execution_semantics_mismatch",
    ):
        assert catalog[code]["area"] == "normalization"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False


def test_task2_catalog_codes_are_emitted_by_real_snapshot_verifiers(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "agent", "prompt": "work"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    language_snapshot = make_language_snapshot(package, "a" * 64)
    changed_language = replace(
        language_snapshot,
        normalized_definition_digest="b" * 64,
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as language_exc:
        verify_language_snapshot(package, "a" * 64, changed_language)

    semantics = build_phase3_execution_semantics(
        package, RunExecutionLimits()
    ).to_dict()
    semantics["nodes"]["agent"]["retry"]["effective_total_attempts"] = 1
    with pytest.raises(WorkflowExecutionSemanticsError) as execution_exc:
        read_phase3_execution_semantics(semantics, package=package)

    assert {
        language_exc.value.code,
        execution_exc.value.code,
    } == {
        "workflow_language_snapshot_mismatch",
        "workflow_execution_semantics_mismatch",
    }
