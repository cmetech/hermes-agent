from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    bind_semantic_fingerprint,
    normalize_workflow,
    resolve_language_profile,
)
from plugins.workflow.models import (
    WorkflowDefinition,
    WorkflowLanguageProfile,
    WorkflowLanguageSelection,
    WorkflowNode,
    freeze_value,
)


@pytest.fixture
def definition(tmp_path):
    return WorkflowDefinition(
        name="language-contract",
        description="Language contract fixture",
        nodes=(
            WorkflowNode(
                id="start",
                node_type="bash",
                value="true",
                depends_on=(),
                source_index=0,
                source_line=4,
                options=freeze_value({}),
            ),
        ),
        options=freeze_value({}),
        source_path=tmp_path / "definition.yaml",
    )


def test_absent_language_declaration_resolves_to_legacy():
    selection = resolve_language_profile({})

    assert selection.declared_profile is None
    assert selection.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY


def test_archon_profile_normalization_is_deterministic(definition):
    selection = WorkflowLanguageSelection(
        declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
    )

    first = normalize_workflow(
        definition,
        selection=selection,
        normalizer_version=1,
    )
    second = normalize_workflow(
        definition,
        selection=selection,
        normalizer_version=1,
    )

    assert first.definition == second.definition
    assert (
        first.metadata.normalized_definition_digest
        == second.metadata.normalized_definition_digest
    )
    assert len(first.metadata.normalized_definition_digest) == 64


def test_unknown_normalizer_version_fails_closed(definition):
    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        normalize_workflow(
            definition,
            selection=WorkflowLanguageSelection(
                declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
                effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            ),
            normalizer_version=99,
        )

    assert exc.value.code == "workflow_normalizer_version_unsupported"


def test_semantic_fingerprint_binds_package_and_normalized_definition(definition):
    result = normalize_workflow(
        definition,
        selection=WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        ),
        normalizer_version=1,
    )

    left = bind_semantic_fingerprint("a" * 64, result.metadata)

    assert left != bind_semantic_fingerprint("b" * 64, result.metadata)
    assert len(left) == 64


def test_normalized_digest_excludes_source_location_and_diagnostics(definition):
    left = replace(
        definition,
        source_path=Path("/installed/workflows/example.yaml"),
        nodes=tuple(
            replace(node, source_index=index, source_line=10 + index)
            for index, node in enumerate(definition.nodes)
        ),
    )
    right = replace(
        definition,
        source_path=Path("/sealed/runs/abc/definition.yaml"),
        nodes=tuple(
            replace(node, source_index=100 + index, source_line=900 + index)
            for index, node in enumerate(definition.nodes)
        ),
    )
    selection = WorkflowLanguageSelection(
        declared_profile=None,
        effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
    )

    left_digest = normalize_workflow(
        left, selection=selection, normalizer_version=1
    ).metadata.normalized_definition_digest
    right_digest = normalize_workflow(
        right, selection=selection, normalizer_version=1
    ).metadata.normalized_definition_digest

    assert left_digest == right_digest
