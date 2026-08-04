from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from agent.structured_output import canonical_json_bytes
import plugins.workflow.language as language_module
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
from plugins.workflow.output_resolution import ResolvedNodeOutput
from plugins.workflow.scheduler import ConditionEvaluationError, evaluate_condition
from plugins.workflow.schema import load_workflow


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
    ordinary_document = language_module._json_safe({
        "profile": selection.effective_profile.value,
        "normalizer_version": 1,
        "definition": {
            "name": first.definition.name,
            "description": first.definition.description,
            "nodes": [
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "value": node.value,
                    "depends_on": list(node.depends_on),
                    "options": node.options,
                }
                for node in first.definition.nodes
            ],
            "options": first.definition.options,
        },
    })
    shared_encoding = canonical_json_bytes(
        ordinary_document,
        max_bytes=1_000_000,
    )
    stdlib_encoding = json.dumps(
        ordinary_document,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert shared_encoding == stdlib_encoding
    assert first.metadata.normalized_definition_digest == hashlib.sha256(
        shared_encoding
    ).hexdigest()


def test_normalized_digest_preserves_exact_integer_beyond_runtime_digit_limit(
    definition,
):
    maximum = 10**4_999
    definition = replace(
        definition,
        nodes=(
            replace(
                definition.nodes[0],
                node_type="prompt",
                value="Return an exact integer",
                options=freeze_value({
                    "output_format": {
                        "type": "number",
                        "maximum": maximum,
                    }
                }),
            ),
        ),
    )
    selection = WorkflowLanguageSelection(
        declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
    )

    first = normalize_workflow(definition, selection=selection, normalizer_version=2)
    second = normalize_workflow(definition, selection=selection, normalizer_version=2)

    assert first.metadata.normalized_definition_digest == (
        second.metadata.normalized_definition_digest
    )
    assert first.metadata.structured_outputs["start"].canonical_schema[
        "maximum"
    ] == maximum


def test_version_one_keeps_archon_structured_output_identity(definition):
    definition = replace(
        definition,
        nodes=(
            replace(
                definition.nodes[0],
                node_type="prompt",
                value="Return JSON",
                options=freeze_value({"output_format": {"type": "object"}}),
            ),
        ),
    )
    selection = WorkflowLanguageSelection(
        declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
    )

    normalized = normalize_workflow(
        definition, selection=selection, normalizer_version=1
    )

    assert normalized.definition is definition
    assert normalized.metadata.structured_outputs == {}


def test_legacy_structured_output_findings_remain_unchanged(workflow_writer, tmp_path):
    package = load_workflow(
        workflow_writer(
            tmp_path,
            nodes=[
                {
                    "id": "prompt",
                    "prompt": "Return JSON",
                    "output_format": {"type": "object"},
                    "output_type": "report",
                }
            ],
        )
    )

    assert {finding.code for finding in package.compatibility_findings} >= {
        "legacy_output_format_post_validation",
        "legacy_output_type_not_published",
    }


def test_phase2_resolved_output_keeps_condition_compatibility_adapters():
    canonical = b'{"count":"2","flag":"yes"}'
    output = ResolvedNodeOutput(
        canonical_bytes=canonical,
        value={"count": "2", "flag": "yes"},
        text=canonical.decode("utf-8"),
        media_type="application/json",
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id="collect",
        attempt_id="attempt-winner",
        publication_id=None,
    )
    outputs = {"collect": output}

    assert not evaluate_condition("$collect.output.count == 2", outputs)
    assert evaluate_condition(
        "$collect.output.flag == 'yes' || "
        "$collect.output.count == 1 && $collect.output.count == 0",
        outputs,
    )
    with pytest.raises(ConditionEvaluationError, match="missing output path"):
        evaluate_condition("$collect.output.missing == 'x'", outputs)


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


def test_legacy_yaml_native_scalars_normalize_deterministically_across_paths(tmp_path):
    workflow = """\
name: legacy-scalars
description: Legacy YAML scalar contract
nodes:
  - id: start
    bash: \"true\"
ignored_null: null
ignored_boolean: true
ignored_integer: 42
ignored_float: 1.25
ignored_string: plain text
ignored_date: 2026-07-25
ignored_timestamp: 2026-07-25T12:34:56+00:00
ignored_binary: !!binary |
  AAEC
"""
    installed_path = tmp_path / "installed" / "legacy.yaml"
    sealed_path = tmp_path / "sealed" / "definition.yaml"
    installed_path.parent.mkdir()
    sealed_path.parent.mkdir()
    installed_path.write_text(workflow, encoding="utf-8")
    sealed_path.write_text(workflow, encoding="utf-8")

    installed = load_workflow(installed_path)
    sealed = load_workflow(sealed_path)
    selection = resolve_language_profile({})

    installed_normalized = normalize_workflow(
        installed.definition,
        selection=selection,
        normalizer_version=1,
    )
    sealed_normalized = normalize_workflow(
        sealed.definition,
        selection=selection,
        normalizer_version=1,
    )

    assert (
        installed_normalized.metadata.normalized_definition_digest
        == sealed_normalized.metadata.normalized_definition_digest
    )


def test_nonfinite_yaml_floats_normalize_without_nonstandard_json_numbers(tmp_path):
    digests = {}
    selection = resolve_language_profile({})
    for label, scalar in (("nan", ".nan"), ("positive", ".inf"), ("negative", "-.inf")):
        workflow = f"""\
name: nonfinite-{label}
description: Non-finite scalar fixture
nodes:
  - id: start
    bash: \"true\"
ignored_float: {scalar}
"""
        installed_path = tmp_path / label / "installed.yaml"
        sealed_path = tmp_path / label / "sealed" / "definition.yaml"
        installed_path.parent.mkdir()
        sealed_path.parent.mkdir()
        installed_path.write_text(workflow, encoding="utf-8")
        sealed_path.write_text(workflow, encoding="utf-8")

        installed = normalize_workflow(
            load_workflow(installed_path).definition,
            selection=selection,
            normalizer_version=1,
        )
        sealed = normalize_workflow(
            load_workflow(sealed_path).definition,
            selection=selection,
            normalizer_version=1,
        )

        assert (
            installed.metadata.normalized_definition_digest
            == sealed.metadata.normalized_definition_digest
        )
        digests[label] = installed.metadata.normalized_definition_digest

    assert len(set(digests.values())) == 3


def test_legacy_mapping_cannot_impersonate_a_nonfinite_scalar_marker(tmp_path):
    mapping_workflow = """\
name: marker-collision
description: Typed value graph fixture
nodes:
  - id: start
    bash: \"true\"
ignored_value:
  __yaml_scalar__: nonfinite-float
  kind: nan
  sign: positive
"""
    nan_workflow = """\
name: marker-collision
description: Typed value graph fixture
nodes:
  - id: start
    bash: \"true\"
ignored_value: .nan
"""
    installed_path = tmp_path / "installed" / "mapping.yaml"
    sealed_path = tmp_path / "sealed" / "definition.yaml"
    nan_path = tmp_path / "nan.yaml"
    installed_path.parent.mkdir()
    sealed_path.parent.mkdir()
    installed_path.write_text(mapping_workflow, encoding="utf-8")
    sealed_path.write_text(mapping_workflow, encoding="utf-8")
    nan_path.write_text(nan_workflow, encoding="utf-8")
    selection = resolve_language_profile({})

    installed_digest = normalize_workflow(
        load_workflow(installed_path).definition,
        selection=selection,
        normalizer_version=1,
    ).metadata.normalized_definition_digest
    sealed_digest = normalize_workflow(
        load_workflow(sealed_path).definition,
        selection=selection,
        normalizer_version=1,
    ).metadata.normalized_definition_digest
    nan_digest = normalize_workflow(
        load_workflow(nan_path).definition,
        selection=selection,
        normalizer_version=1,
    ).metadata.normalized_definition_digest

    assert installed_digest == sealed_digest
    assert installed_digest != nan_digest
