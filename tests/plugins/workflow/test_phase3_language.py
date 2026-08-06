from __future__ import annotations

import pytest

import plugins.workflow.language as workflow_language
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    normalize_workflow,
    read_language_snapshot,
)
from plugins.workflow.models import (
    WorkflowValidationError,
    WorkflowLanguageProfile,
    WorkflowLanguageSelection,
    freeze_value,
)
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.trust import build_risk_summary, compute_package_digest


def _sidecar(path, profile: str) -> None:
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        f"language_compatibility: {profile}\n", encoding="utf-8"
    )


def test_new_packages_select_the_current_normalizer_for_their_profile(
    tmp_path, workflow_writer
) -> None:
    unversioned_path = workflow_writer(tmp_path / "unversioned")
    legacy_path = workflow_writer(tmp_path / "legacy")
    _sidecar(legacy_path, "hermes-legacy")
    archon_path = workflow_writer(tmp_path / "archon")
    _sidecar(archon_path, "archon-2026-07")

    unversioned = load_workflow(unversioned_path)
    legacy = load_workflow(legacy_path)
    archon = load_workflow(archon_path)

    assert workflow_language.LATEST_NORMALIZER_VERSION == 4
    assert workflow_language.CURRENT_NORMALIZER_BY_PROFILE == {
        WorkflowLanguageProfile.HERMES_LEGACY: 2,
        WorkflowLanguageProfile.ARCHON_2026_07: 3,
    }
    assert unversioned.language.normalizer_version == 2
    assert legacy.language.normalizer_version == 2
    assert archon.language.normalizer_version == 3


def test_legacy_default_normalization_is_identical_to_explicit_v2(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "legacy",
                "bash": "true",
                "timeout": 7,
                "retry": {"max_attempts": 2, "delay_ms": 1000},
            }
        ],
    )
    loaded = load_workflow(path)
    explicit = normalize_workflow(
        loaded.source_definition,
        selection=WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        ),
        normalizer_version=2,
    )

    assert loaded.definition == explicit.definition
    assert loaded.language == explicit.metadata
    assert make_language_snapshot(loaded, "a" * 64).to_dict() == {
        "effective_profile": "hermes-legacy",
        "normalizer_version": 2,
        "normalized_definition_digest": explicit.metadata.normalized_definition_digest,
        "semantic_fingerprint": make_language_snapshot(
            loaded, "a" * 64
        ).semantic_fingerprint,
        "structured_outputs": {},
    }


@pytest.mark.parametrize("version", [1, 2])
def test_explicit_legacy_snapshot_versions_reload_without_upgrade(
    tmp_path, workflow_writer, version
) -> None:
    path = workflow_writer(tmp_path)
    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=None,
        normalizer_version=version,
    )
    value = make_language_snapshot(package, "b" * 64).to_dict()

    snapshot = read_language_snapshot(value)

    assert snapshot is not None
    assert snapshot.normalizer_version == version
    assert snapshot.to_dict() == value
    assert "node_semantics" not in value


def test_snapshot_reader_rejects_v3_for_the_legacy_profile() -> None:
    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot({
            "effective_profile": "hermes-legacy",
            "normalizer_version": 3,
            "normalized_definition_digest": "a" * 64,
            "semantic_fingerprint": "b" * 64,
            "structured_outputs": {},
            "node_semantics": {},
        })

    assert exc.value.code == "workflow_normalizer_version_unsupported"


def test_snapshot_reader_requires_exact_v3_node_semantic_entries() -> None:
    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot({
            "effective_profile": "archon-2026-07",
            "normalizer_version": 3,
            "normalized_definition_digest": "a" * 64,
            "semantic_fingerprint": "b" * 64,
            "structured_outputs": {},
            "node_semantics": {"node": {"wall_timeout_seconds": 120.0}},
        })

    assert exc.value.code == "workflow_language_snapshot_invalid"


def test_snapshot_reader_rejects_null_retry_and_non_scalar_retry_values() -> None:
    base = {
        "effective_profile": "archon-2026-07",
        "normalizer_version": 3,
        "normalized_definition_digest": "a" * 64,
        "semantic_fingerprint": "b" * 64,
        "structured_outputs": {},
    }
    invalid_entries = (
        {"node": {"retry": None}},
        {
            "node": {
                "retry": {
                    "explicit": True,
                    "requested_retries": 1,
                    "requested_total_attempts": 2,
                    "delay_ms": 3000,
                    "on_error": [],
                }
            }
        },
    )

    for node_semantics in invalid_entries:
        with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
            read_language_snapshot({**base, "node_semantics": node_semantics})
        assert exc.value.code == "workflow_language_snapshot_invalid"


def test_archon_v3_normalizes_requested_timeout_and_retry_semantics(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "ai-default", "prompt": "default"},
            {
                "id": "ai-explicit",
                "prompt": "explicit",
                "idle_timeout": 1500,
                "retry": {
                    "max_attempts": 1,
                    "delay_ms": 4000,
                    "on_error": "all",
                },
            },
            {"id": "bash-default", "bash": "true"},
            {
                "id": "script-explicit",
                "script": "print('ok')",
                "runtime": "uv",
                "timeout": 2500,
                "retry": {"max_attempts": 5},
            },
            {
                "id": "approval",
                "approval": {"message": "Continue?"},
            },
        ],
    )
    _sidecar(path, "archon-2026-07")

    package = load_workflow(path)

    assert package.language.node_semantics == {
        "ai-default": {
            "retry": {
                "explicit": False,
                "requested_retries": 2,
                "requested_total_attempts": 3,
                "delay_ms": 3000,
                "on_error": "transient",
            }
        },
        "ai-explicit": {
            "idle_timeout_seconds": 1.5,
            "retry": {
                "explicit": True,
                "requested_retries": 1,
                "requested_total_attempts": 2,
                "delay_ms": 4000,
                "on_error": "all",
            },
        },
        "bash-default": {
            "wall_timeout_seconds": 120.0,
            "retry": {
                "explicit": False,
                "requested_retries": 0,
                "requested_total_attempts": 1,
                "delay_ms": 3000,
                "on_error": "transient",
            },
        },
        "script-explicit": {
            "wall_timeout_seconds": 2.5,
            "retry": {
                "explicit": True,
                "requested_retries": 5,
                "requested_total_attempts": 6,
                "delay_ms": 3000,
                "on_error": "transient",
            },
        },
    }
    snapshot = make_language_snapshot(package, "c" * 64).to_dict()
    assert snapshot["node_semantics"] == package.language.node_semantics
    assert list(snapshot["node_semantics"]) == sorted(snapshot["node_semantics"])
    assert read_language_snapshot(snapshot).to_dict() == snapshot


@pytest.mark.parametrize(
    ("node", "code"),
    [
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "retry": {"max_attempts": 1}},
            "archon_retry_node_unsupported",
        ),
        (
            {"id": "cancel", "cancel": "stop", "idle_timeout": 1000},
            "archon_idle_timeout_node_unsupported",
        ),
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "timeout": 1000},
            "archon_timeout_node_unsupported",
        ),
        (
            {
                "id": "loop",
                "loop": {"prompt": "again", "until": "done", "max_iterations": 2},
                "retry": {"max_attempts": 1},
            },
            "archon_retry_node_unsupported",
        ),
    ],
)
def test_archon_v3_rejects_inapplicable_requested_semantics(
    tmp_path, workflow_writer, node, code
) -> None:
    path = workflow_writer(tmp_path, nodes=[node])
    _sidecar(path, "archon-2026-07")

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].code == code


@pytest.mark.parametrize(
    ("node", "code"),
    [
        ({"id": "shell", "bash": "true", "timeout": True}, "archon_timeout_invalid"),
        ({"id": "shell", "bash": "true", "timeout": 0}, "archon_timeout_invalid"),
        (
            {"id": "agent", "prompt": "work", "idle_timeout": float("inf")},
            "archon_idle_timeout_invalid",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {"delay_ms": 3000}},
            "archon_retry_max_attempts_required",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"max_attempts": True}},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"future": 1}},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"on_error": []}},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": None},
            "archon_retry_invalid",
        ),
        (
            {"id": "shell", "bash": "true", "retry": None},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"max_attempts": None}},
            "archon_retry_invalid",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {"max_attempts": None}},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"delay_ms": None}},
            "archon_retry_invalid",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {"delay_ms": None}},
            "archon_retry_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"on_error": None}},
            "archon_retry_invalid",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {"on_error": None}},
            "archon_retry_invalid",
        ),
    ],
)
def test_archon_v3_rejects_invalid_requested_semantics(
    tmp_path, workflow_writer, node, code
) -> None:
    path = workflow_writer(tmp_path, nodes=[node])
    _sidecar(path, "archon-2026-07")

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].code == code


def test_trust_risk_identity_changes_for_archon_v3_but_not_legacy_v2(
    tmp_path, workflow_writer
) -> None:
    archon_path = workflow_writer(tmp_path / "archon")
    _sidecar(archon_path, "archon-2026-07")
    archon_v3 = load_workflow(archon_path)
    archon_v2 = load_workflow_snapshot(
        archon_path,
        workflow_bytes=archon_path.read_bytes(),
        sidecar_bytes=archon_path.with_name(
            f"{archon_path.stem}.hermes.yaml"
        ).read_bytes(),
        normalizer_version=2,
    )
    legacy_path = workflow_writer(tmp_path / "legacy")
    legacy_default = load_workflow(legacy_path)
    legacy_explicit = load_workflow_snapshot(
        legacy_path,
        workflow_bytes=legacy_path.read_bytes(),
        sidecar_bytes=None,
        normalizer_version=2,
    )

    archon_v3_risk = build_risk_summary(
        archon_v3, assess_compatibility(archon_v3)
    )
    archon_v2_risk = build_risk_summary(
        archon_v2, assess_compatibility(archon_v2)
    )
    legacy_default_risk = build_risk_summary(
        legacy_default, assess_compatibility(legacy_default)
    )
    legacy_explicit_risk = build_risk_summary(
        legacy_explicit, assess_compatibility(legacy_explicit)
    )

    assert compute_package_digest(archon_v3) == compute_package_digest(archon_v2)
    assert archon_v3_risk.risk_digest != archon_v2_risk.risk_digest
    assert legacy_default_risk.risk_digest == legacy_explicit_risk.risk_digest


def test_phase3_archon_fields_are_implemented_and_legacy_guidance_is_exact(
    tmp_path, workflow_writer
) -> None:
    archon_path = workflow_writer(
        tmp_path / "archon",
        nodes=[
            {
                "id": "agent",
                "prompt": "work",
                "idle_timeout": 1500,
                "retry": {"max_attempts": 2},
            },
            {"id": "shell", "bash": "true", "timeout": 120000},
        ],
    )
    _sidecar(archon_path, "archon-2026-07")
    legacy_path = workflow_writer(
        tmp_path / "legacy",
        nodes=[
            {
                "id": "single-shell",
                "bash": "true",
                "timeout": 120,
                "retry": {"max_attempts": 1},
                "output_format": {"type": "object"},
                "output_type": "report",
            },
            {
                "id": "retried-shell",
                "bash": "true",
                "retry": {"max_attempts": 3},
            },
            {
                "id": "single-agent",
                "prompt": "choose",
                "retry": {"max_attempts": 1},
            },
        ],
    )

    archon_codes = {
        finding.code for finding in load_workflow(archon_path).compatibility_findings
    }
    legacy_findings = load_workflow(legacy_path).compatibility_findings
    by_path = {finding.path: finding for finding in legacy_findings}

    assert not {
        "archon_timeout_semantics_unavailable",
        "archon_idle_timeout_semantics_unavailable",
        "archon_retry_semantics_unavailable",
    } & archon_codes
    assert "1,000" in by_path["nodes[0].timeout"].migration
    assert "omit retry" in by_path["nodes[0].retry.max_attempts"].migration
    assert "N - 1" in by_path["nodes[1].retry.max_attempts"].migration
    assert "cannot migrate" in by_path["nodes[2].retry.max_attempts"].migration
    assert "three total attempts" in by_path["nodes[2].retry.max_attempts"].migration

    profile_guidance = by_path["sidecar.language_compatibility"].migration
    assert "directly to depends_on" in profile_guidance
    assert "output_format" in profile_guidance
    assert ".field" in profile_guidance
    assert "structured scalar decision value" in profile_guidance
    assert "32,768" in profile_guidance
    assert "pathname" in profile_guidance

    output_format_guidance = by_path["nodes[0].output_format"].migration
    output_type_guidance = by_path["nodes[0].output_type"].migration
    assert "Phase 2" not in output_format_guidance
    assert "Phase 2" not in output_type_guidance
    assert "before using .field" in output_format_guidance
    assert "output_format" in output_type_guidance


@pytest.mark.parametrize(
    ("node", "total_attempts"),
    [
        ({"id": "approval", "approval": {"message": "Continue?"}}, 1),
        ({"id": "approval", "approval": {"message": "Continue?"}}, 2),
        ({"id": "cancel", "cancel": "Stop safely"}, 1),
        ({"id": "cancel", "cancel": "Stop safely"}, 2),
    ],
)
def test_legacy_retry_guidance_rejects_inapplicable_archon_node_kinds(
    tmp_path, workflow_writer, node, total_attempts
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                **node,
                "retry": {"max_attempts": total_attempts},
            }
        ],
    )

    finding = next(
        item
        for item in load_workflow(path).compatibility_findings
        if item.code == "legacy_retry_total_attempts"
    )

    assert "cannot migrate under Archon v3" in finding.migration
    assert "remove the retry block or redesign" in finding.migration
    assert "N - 1" not in finding.migration
    assert "omit retry" not in finding.migration


@pytest.mark.parametrize(
    "node",
    [
        {"id": "shell", "bash": "true"},
        {
            "id": "loop",
            "loop": {"prompt": "again", "until": "done", "max_iterations": 2},
        },
    ],
)
def test_legacy_idle_timeout_guidance_rejects_inapplicable_archon_node_kinds(
    tmp_path, workflow_writer, node
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                **node,
                "idle_timeout": 10,
            }
        ],
    )

    finding = next(
        item
        for item in load_workflow(path).compatibility_findings
        if item.code == "legacy_idle_timeout_seconds"
    )

    assert "cannot migrate under Archon v3" in finding.migration
    assert "remove idle_timeout or redesign" in finding.migration
    assert "Multiply" not in finding.migration
