from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.workflow.language import CURRENT_NORMALIZER_BY_PROFILE
from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.schema import (
    _compile_workflow_source_document,
    parse_workflow_source_bytes,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_CORPUS_CASES = 64
MAX_CORPUS_BYTES = 160_000


def _cases(profile: WorkflowLanguageProfile) -> dict[str, dict[str, object]]:
    corpus = workflow_language_conformance(profile)
    return {case["id"]: case for case in corpus["cases"]}


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_conformance_envelope_is_versioned_bounded_and_deterministic(profile):
    first = workflow_language_conformance(profile)
    second = workflow_language_conformance(profile)
    encoded = json.dumps(
        first,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert first == second
    assert first["format_version"] == 1
    assert first["profile"] == profile.value
    assert first["normalizer_version"] == CURRENT_NORMALIZER_BY_PROFILE[profile]
    assert first["contract"] == {
        "schema_version": 1,
        "contract_reader_version": workflow_authoring_contract(profile)[
            "contract_reader_version"
        ],
        "contract_digest": workflow_authoring_contract(profile)["contract_digest"],
        "normalizer": "plugins.workflow.language.normalize_workflow",
        "validator": "plugins.workflow.schema._compile_workflow_source_document",
    }
    assert first["x-hermes-provenance"]["producer"] == "hermes-agent"
    assert first["x-hermes-provenance"]["command"] == (
        f"hermes workflow schema-corpus --profile {profile.value} --json"
    )
    assert 1 <= len(first["cases"]) <= MAX_CORPUS_CASES
    assert len(encoded) <= MAX_CORPUS_BYTES

    ids = [case["id"] for case in first["cases"]]
    assert len(ids) == len(set(ids))
    for case in first["cases"]:
        assert case["profile"] == profile.value
        assert case["normalizer_version"] == CURRENT_NORMALIZER_BY_PROFILE[profile]
        assert isinstance(case["definition_yaml"], str)
        assert case["definition_yaml"].endswith("\n")
        assert isinstance(case["valid"], bool)
        assert case["codes"] == [item["code"] for item in case["diagnostics"]]
        assert case["features"] == sorted(set(case["features"]))
        for diagnostic in case["diagnostics"]:
            assert set(diagnostic) == {
                "blocking",
                "code",
                "document",
                "hermes_code",
                "path",
                "scope",
                "severity",
            }
            assert diagnostic["document"] in {"definition", "companion"}
            assert diagnostic["scope"] == "root" or diagnostic["scope"].startswith(
                "loop-group:"
            )


def test_archon_corpus_has_stable_loop_group_cases_and_portable_codes():
    cases = _cases(WorkflowLanguageProfile.ARCHON_2026_07)

    assert cases["loop-group-minimal-valid"]["valid"] is True
    assert cases["loop-group-empty-body"]["valid"] is False
    assert cases["loop-group-current-ref-needs-dependency"]["codes"] == [
        "scoped-reference-missing-dependency"
    ]
    assert {
        "loop-group-all-body-kinds-valid",
        "loop-group-empty-body",
        "loop-group-duplicate-id",
        "loop-group-missing-dependency",
        "loop-group-self-edge",
        "loop-group-cycle",
        "loop-group-too-many-nodes",
        "loop-group-too-many-edges",
        "loop-group-forbidden-include",
        "loop-group-forbidden-workflow",
        "loop-group-forbidden-nested-group",
        "loop-group-forbidden-retry",
        "loop-group-current-ref-with-dependency",
        "loop-group-current-ref-needs-dependency",
        "loop-group-outer-ref-with-dependency",
        "loop-group-outer-ref-needs-dependency",
        "loop-group-loop-prev-valid",
        "loop-group-loop-prev-unknown-producer",
        "loop-group-first-terminal-primary",
        "loop-group-companion-child-reference-valid",
        "loop-group-companion-child-reference-unknown",
        "loop-group-work-product-boundary",
        "loop-group-work-product-over-boundary",
        "jira-defect-loop-distributed",
    } <= set(cases)


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_corpus_covers_supported_node_kinds_and_field_families(profile):
    corpus = workflow_language_conformance(profile)
    feature_tags = {
        feature for case in corpus["cases"] for feature in case["features"]
    }
    contract = workflow_authoring_contract(profile)

    assert {f"node-kind:{item['id']}" for item in contract["node_kinds"]} <= (
        feature_tags
    )
    expected_families = {
        f"field-family:{field_id.partition('.')[0].replace('_', '-')}"
        for field_id in contract["field_definitions"]
    }
    expected_families.update({"field-family:definition", "field-family:sidecar"})
    assert expected_families <= feature_tags


def _authority_outcome(case: dict[str, object]):
    source = parse_workflow_source_bytes(
        f"{case['id']}.yaml",
        workflow_bytes=case["definition_yaml"].encode("utf-8"),
        sidecar_bytes=(
            case["companion_yaml"].encode("utf-8")
            if "companion_yaml" in case
            else None
        ),
        source="conformance",
        precedence=1,
    )
    return _compile_workflow_source_document(
        source,
        normalizer_version=case["normalizer_version"],
    )


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_every_case_agrees_with_hermes_parser_normalizer_and_diagnostics(profile):
    for case in workflow_language_conformance(profile)["cases"]:
        try:
            package = _authority_outcome(case)
        except WorkflowValidationError as exc:
            actual = exc.issues
            actual_valid = False
        else:
            actual = package.validation_issues
            actual_valid = not any(issue.blocking for issue in actual)

        expected = case["diagnostics"]
        assert actual_valid is case["valid"], case["id"]
        assert [issue.code for issue in actual] == [
            item["hermes_code"] for item in expected
        ], case["id"]
        assert [issue.path for issue in actual] == [
            item["path"] for item in expected
        ], case["id"]
        assert [issue.severity for issue in actual] == [
            item["severity"] for item in expected
        ], case["id"]
        assert [issue.blocking for issue in actual] == [
            item["blocking"] for item in expected
        ], case["id"]


def test_projection_facts_are_produced_by_the_normalized_workflow():
    cases = _cases(WorkflowLanguageProfile.ARCHON_2026_07)
    multiple_terminals = cases["loop-group-first-terminal-primary"]
    package = _authority_outcome(multiple_terminals)
    group = package.definition.nodes[0]

    assert multiple_terminals["projection"] == {
        "group_id": group.id,
        "primary_sink": package.language.node_semantics[group.id]["loop_group"][
            "primary_sink"
        ],
        "scoped_node_ids": [
            f"{group.id}/{child.id}" for child in group.value["nodes"]
        ],
    }


def test_distributed_jira_case_is_exact_and_provenance_tagged():
    case = _cases(WorkflowLanguageProfile.ARCHON_2026_07)[
        "jira-defect-loop-distributed"
    ]
    definition = (
        REPO_ROOT
        / "capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml"
    )
    companion = definition.with_name("jira-defect-loop.hermes.yaml")

    assert case["definition_yaml"].encode("utf-8") == definition.read_bytes()
    assert case["companion_yaml"].encode("utf-8") == companion.read_bytes()
    assert case["provenance"] == {
        "kind": "distributed-workflow-package",
        "definition": (
            "capabilities/workflow-packages/ericsson/workflows/"
            "jira-defect-loop.yaml"
        ),
        "companion": (
            "capabilities/workflow-packages/ericsson/workflows/"
            "jira-defect-loop.hermes.yaml"
        ),
    }


def test_legacy_corpus_rejects_v6_syntax_and_preserves_unknown_fields():
    cases = _cases(WorkflowLanguageProfile.HERMES_LEGACY)

    assert cases["legacy-loop-group-version-rejected"]["valid"] is False
    assert cases["legacy-artifacts-version-rejected"]["valid"] is False
    assert cases["legacy-unknown-top-level-preserved"]["valid"] is True
    assert cases["legacy-unknown-top-level-preserved"]["diagnostics"] == [
        {
            "blocking": False,
            "code": "unknown_top_level_field",
            "document": "definition",
            "hermes_code": "unknown_top_level_field",
            "path": "future_editor_field",
            "scope": "root",
            "severity": "warning",
        }
    ]
