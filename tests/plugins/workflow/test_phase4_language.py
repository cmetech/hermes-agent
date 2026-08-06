from __future__ import annotations

import pytest

from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    read_language_snapshot,
    supports_phase3_semantics,
    supports_phase4_semantics,
    supports_structured_outputs,
)
from plugins.workflow.execution_semantics import build_phase3_execution_semantics
from plugins.workflow.models import RunExecutionLimits, WorkflowLanguageProfile
from plugins.workflow.schema import load_workflow, load_workflow_snapshot


@pytest.mark.parametrize(
    ("version", "structured", "phase3", "phase4"),
    [
        (1, False, False, False),
        (2, True, False, False),
        (3, True, True, False),
        (4, True, True, True),
    ],
)
def test_archon_capabilities_are_cumulative(version, structured, phase3, phase4):
    """Catch a new language version dropping an inherited capability."""
    profile = WorkflowLanguageProfile.ARCHON_2026_07

    assert supports_structured_outputs(profile, version) is structured
    assert supports_phase3_semantics(profile, version) is phase3
    assert supports_phase4_semantics(profile, version) is phase4


@pytest.mark.parametrize("profile", (None, "hermes-legacy"))
def test_explicit_v4_is_rejected_for_unversioned_and_legacy_workflows(
    tmp_path, workflow_writer, profile
) -> None:
    """Catch a non-Archon workflow being admitted with v4 capabilities."""
    path = workflow_writer(tmp_path / str(profile or "unversioned"))
    sidecar = None
    if profile is not None:
        sidecar_path = path.with_name(f"{path.stem}.hermes.yaml")
        sidecar_path.write_text(
            f"language_compatibility: {profile}\n", encoding="utf-8"
        )
        sidecar = sidecar_path.read_bytes()

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        load_workflow_snapshot(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=sidecar,
            normalizer_version=4,
        )

    assert exc.value.code == "workflow_normalizer_version_unsupported"


def test_current_v4_preserves_explicit_v3_normalized_behavior(
    tmp_path, workflow_writer
) -> None:
    """Catch current Archon admission bypassing or dropping sealed v3 semantics."""
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "producer",
                "prompt": "Produce a result",
                "output_format": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
            },
            {
                "id": "shell",
                "bash": "printf '%s' $producer.output.status",
                "depends_on": ["producer"],
                "timeout": 2_500,
                "retry": {"max_attempts": 1, "delay_ms": 4_000},
            },
        ],
    )
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")

    v3 = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=3,
    )
    current = load_workflow(path)

    assert current.language.normalizer_version == 4
    assert current.definition == v3.definition
    assert current.language.structured_outputs == v3.language.structured_outputs
    assert current.language.node_semantics == v3.language.node_semantics
    assert current.language.node_semantics["shell"] == {
        "wall_timeout_seconds": 2.5,
        "retry": {
            "explicit": True,
            "requested_retries": 1,
            "requested_total_attempts": 2,
            "delay_ms": 4_000,
            "on_error": "transient",
        },
    }

    limits = RunExecutionLimits(
        ai_idle_timeout_seconds=90.0,
        ai_wall_timeout_seconds=120.0,
        provider_request_timeout_seconds=60.0,
        subprocess_timeout_seconds=30.0,
        combined_retries=3,
    )
    v3_execution = build_phase3_execution_semantics(v3, limits).to_dict()
    v4_execution = build_phase3_execution_semantics(current, limits).to_dict()
    assert v3_execution.pop("normalizer_version") == 3
    assert v4_execution.pop("normalizer_version") == 4
    assert v4_execution == v3_execution

    v3_snapshot = make_language_snapshot(v3, "a" * 64).to_dict()
    v4_snapshot = make_language_snapshot(current, "a" * 64).to_dict()
    assert read_language_snapshot(v4_snapshot).to_dict() == v4_snapshot
    for field in (
        "normalizer_version",
        "normalized_definition_digest",
        "semantic_fingerprint",
    ):
        v3_snapshot.pop(field)
        v4_snapshot.pop(field)
    assert v4_snapshot == v3_snapshot
