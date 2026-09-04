from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import cast

import pytest
import yaml

from plugins.workflow.language import CURRENT_NORMALIZER_BY_PROFILE
from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowValidationError,
)
from plugins.workflow.output_resolution import WorkflowOutputReferenceError
from plugins.workflow.resources import VariableContext
from plugins.workflow.schema import (
    _compile_workflow_source_document,
    parse_workflow_source_bytes,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_CORPUS_CASES = 64
MAX_CORPUS_BYTES = 160_000


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


def _mapping_list(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, object]], value)


def _string_list(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return cast(list[str], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _scoped_semantic_diagnostic_codes(contract: dict[str, object]) -> set[str]:
    loop_group = next(
        item
        for item in _mapping_list(contract["node_kinds"])
        if item["id"] == "loop_group"
    )
    definitions = _mapping(loop_group["semantic_definitions"])
    scoped_reference = _mapping(definitions["scoped-output-reference-v1"])
    constraint = _mapping(scoped_reference["structured_path_constraint_v1"])
    table = _mapping(constraint["diagnostic_table"])
    columns = _string_list(table["cols"])
    semantic_index = columns.index("semantic")
    rows = table["rows"]
    assert isinstance(rows, list)
    assert all(isinstance(row, list) for row in rows)
    return {
        _text(row[semantic_index])
        for row in cast(list[list[object]], rows)
    }


def _cases(profile: WorkflowLanguageProfile) -> dict[str, dict[str, object]]:
    corpus = workflow_language_conformance(profile)
    return {
        _text(case["id"]): case for case in _mapping_list(corpus["cases"])
    }


def _parse_case(case: dict[str, object]):
    return parse_workflow_source_bytes(
        f"{_text(case['id'])}.yaml",
        workflow_bytes=_text(case["definition_yaml"]).encode("utf-8"),
        sidecar_bytes=(
            _text(case["companion_yaml"]).encode("utf-8")
            if "companion_yaml" in case
            else None
        ),
        source="conformance",
        precedence=1,
    )


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
    provenance = _mapping(first["x-hermes-provenance"])
    cases = _mapping_list(first["cases"])
    assert provenance["producer"] == "hermes-agent"
    assert provenance["command"] == (
        f"hermes workflow schema-corpus --profile {profile.value} --json"
    )
    assert 1 <= len(cases) <= MAX_CORPUS_CASES
    assert len(encoded) <= MAX_CORPUS_BYTES

    ids = [_text(case["id"]) for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["profile"] == profile.value
        assert case["normalizer_version"] == CURRENT_NORMALIZER_BY_PROFILE[profile]
        assert isinstance(case["definition_yaml"], str)
        assert case["definition_yaml"].endswith("\n")
        assert isinstance(case["valid"], bool)
        diagnostics = _mapping_list(case["diagnostics"])
        features = _string_list(case["features"])
        assert case["codes"] == [item["code"] for item in diagnostics]
        assert features == sorted(set(features))
        for diagnostic in diagnostics:
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
            scope = _text(diagnostic["scope"])
            assert scope == "root" or scope.startswith(
                "loop-group:"
            )


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_every_corpus_diagnostic_is_published_by_paired_contract(profile):
    contract = workflow_authoring_contract(profile)
    diagnostics = [
        diagnostic
        for case in _mapping_list(workflow_language_conformance(profile)["cases"])
        for diagnostic in _mapping_list(case["diagnostics"])
    ]
    compatibility_codes = _mapping(contract["compatibility_codes"])
    native_diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic["code"] == diagnostic["hermes_code"]
    ]
    portable_diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic["code"] != diagnostic["hermes_code"]
    ]
    missing_native_codes = sorted(
        {
            _text(diagnostic["hermes_code"])
            for diagnostic in native_diagnostics
            if _text(diagnostic["hermes_code"]) not in compatibility_codes
        }
    )
    semantic_codes = (
        _scoped_semantic_diagnostic_codes(contract)
        if portable_diagnostics
        else set()
    )
    missing_portable_codes = sorted(
        {
            _text(diagnostic["code"])
            for diagnostic in portable_diagnostics
            if _text(diagnostic["code"]) not in semantic_codes
        }
    )

    assert missing_native_codes == []
    assert missing_portable_codes == []

    for diagnostic in native_diagnostics:
        native_entry = _mapping(
            compatibility_codes[_text(diagnostic["hermes_code"])]
        )
        if "severity" in native_entry or "blocking" in native_entry:
            assert {"severity", "blocking"} <= set(native_entry)
            assert native_entry["severity"] == diagnostic["severity"]
            assert native_entry["blocking"] is diagnostic["blocking"]
        else:
            assert native_entry["runtime_failure"] is True
            assert diagnostic["severity"] == "error"
            assert diagnostic["blocking"] is True


def test_archon_corpus_has_stable_loop_group_cases_and_portable_codes():
    cases = _cases(WorkflowLanguageProfile.ARCHON_2026_07)

    assert cases["loop-group-minimal-valid"]["valid"] is True
    assert cases["loop-group-empty-body"]["valid"] is False
    assert cases["loop-group-current-ref-needs-dependency"]["codes"] == [
        "scoped-reference-missing-dependency"
    ]
    assert cases["loop-group-gate-ref-needs-dependency"]["codes"] == [
        "scoped-reference-missing-dependency"
    ]
    assert cases["loop-group-structured-schema-required"]["codes"] == [
        "scoped-reference-producer-schema-required"
    ]
    assert cases["loop-group-structured-path-impossible"]["codes"] == [
        "scoped-reference-structured-path-impossible"
    ]
    assert cases["loop-group-promoted-schema-valid"]["valid"] is True
    assert cases["loop-group-promoted-schema-body-impossible"]["diagnostics"] == [
        {
            "blocking": True,
            "code": "scoped-reference-structured-path-impossible",
            "document": "definition",
            "hermes_code": "loop_group_scope_invalid",
            "path": "nodes[1].loop_group.nodes[0].prompt",
            "scope": "loop-group:group",
            "severity": "error",
        }
    ]
    assert cases["loop-group-promoted-schema-gate-impossible"]["diagnostics"] == [
        {
            "blocking": True,
            "code": "scoped-reference-structured-path-impossible",
            "document": "definition",
            "hermes_code": "structured_output_field_impossible",
            "path": "nodes[1].loop_group.gate_message",
            "scope": "loop-group:group",
            "severity": "error",
        }
    ]
    assert cases["loop-group-promoted-schema-unsupported-conservative"][
        "valid"
    ] is True
    assert cases["loop-group-body-outer-collision-body-field-valid"][
        "valid"
    ] is True
    assert cases["loop-group-body-outer-collision-outer-field-impossible"][
        "codes"
    ] == ["scoped-reference-structured-path-impossible"]
    assert cases["loop-group-first-iteration-previous-output"]["valid"] is True
    assert cases["loop-group-pattern-fallback-conservative"]["valid"] is True
    assert cases["loop-group-terminal-ref-false-conservative"]["valid"] is True
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
        "loop-group-mixed-ref-needs-dependency",
        "loop-group-outer-ref-with-dependency",
        "loop-group-outer-ref-needs-dependency",
        "loop-group-loop-prev-valid",
        "loop-group-loop-prev-unknown-producer",
        "loop-group-gate-ref-needs-dependency",
        "loop-group-structured-paths-valid",
        "loop-group-structured-schema-required",
        "loop-group-structured-path-impossible",
        "loop-group-promoted-schema-valid",
        "loop-group-promoted-schema-body-impossible",
        "loop-group-promoted-schema-gate-impossible",
        "loop-group-promoted-schema-unsupported-conservative",
        "loop-group-body-outer-collision-body-field-valid",
        "loop-group-body-outer-collision-outer-field-impossible",
        "loop-group-first-iteration-previous-output",
        "loop-group-pattern-fallback-conservative",
        "loop-group-terminal-ref-false-conservative",
        "loop-group-first-terminal-primary",
        "loop-group-companion-child-reference-valid",
        "loop-group-companion-child-reference-unknown",
        "loop-group-work-product-boundary",
        "loop-group-work-one-over",
        "loop-group-unknown-field-preserved",
        "jira-defect-loop-distributed",
    } <= set(cases)


def test_first_iteration_previous_output_case_matches_runtime_resolution():
    case = _cases(WorkflowLanguageProfile.ARCHON_2026_07)[
        "loop-group-first-iteration-previous-output"
    ]
    expected = _mapping(_mapping(case["projection"])["first_iteration"])
    variables = VariableContext(
        previous_body_outputs={"producer": None},
        normalizer_version=6,
    )

    whole = variables.previous_output_reference("producer")
    assert {
        "result": "resolved",
        "rendered_text": whole.rendered_text,
    } == expected["known_whole_output"]
    with pytest.raises(WorkflowOutputReferenceError) as exc_info:
        variables.previous_output_reference("producer", ("status",))
    assert {
        "result": "error",
        "code": exc_info.value.code,
    } == expected["known_structured_path"]


def _public_contract_path_outcome(
    policy: Mapping[str, object], schema: object, path: tuple[str, ...]
) -> str:
    evaluation = _mapping(policy["evaluation_v1"])
    ref_policy = _mapping(evaluation["$ref_resolution"])
    lookup = _mapping(evaluation["object_lookup"])
    terminal = _mapping(evaluation["terminal_child"])
    strategies = _mapping(policy["strategies"])
    assert ref_policy == {
        "schema_scope": "current-schema-only",
        "when": "path-segments-remain",
        "terminal_child": "not-resolved",
    }
    assert lookup["stop_after_first_applicable"] is True
    assert terminal["when"] == "no-path-segments-remain"
    assert terminal["$ref"] == "not-resolved"

    def resolve_local(root: object, reference: object) -> object:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return None
        current = root
        for encoded in reference[2:].split("/"):
            segment = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping) and segment in current:
                current = current[segment]
            else:
                return None
        return current

    def terminal_outcome(child: object) -> str:
        return _text(terminal["false"] if child is False else terminal["otherwise"])

    def visit(
        current: object,
        remaining: tuple[str, ...],
        root: object,
        resolving: frozenset[str],
    ) -> str:
        if current is False:
            return "impossible"
        if current is True or not isinstance(current, Mapping):
            return "unknown"
        reference = current.get("$ref")
        if isinstance(reference, str) and reference not in resolving:
            target = resolve_local(root, reference)
            if target is False:
                return "impossible"
            if target is not None:
                outcome = visit(
                    target,
                    remaining,
                    root,
                    resolving | frozenset({reference}),
                )
                if outcome == "impossible":
                    return outcome

        segment, *tail = remaining
        applicability = _mapping(lookup["applicability"])
        for keyword in _string_list(lookup["order"]):
            if keyword == "properties":
                assert applicability[keyword] == "exact-key-match"
                properties = current.get(keyword)
                if not isinstance(properties, Mapping) or segment not in properties:
                    continue
                child = properties[segment]
                return (
                    visit(child, tuple(tail), root, resolving)
                    if tail
                    else terminal_outcome(child)
                )
            if keyword == "patternProperties":
                assert applicability[keyword] == "nonempty-map"
                patterns = current.get(keyword)
                if isinstance(patterns, Mapping) and patterns:
                    assert strategies[keyword] == "nonempty-unknown"
                    return "unknown"
                continue
            assert keyword == "additionalProperties"
            assert applicability[keyword] == "fallback"
            additional = current.get(keyword, True)
            if additional is False:
                return "impossible"
            if tail and isinstance(additional, Mapping):
                return visit(additional, tuple(tail), root, resolving)
            return "unknown"
        raise AssertionError("published object lookup has no terminal fallback")

    assert path
    return visit(schema, path, schema, frozenset())


def test_public_contract_interpreter_accepts_ambiguous_schema_corpus_cases():
    cases = _cases(WorkflowLanguageProfile.ARCHON_2026_07)
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    rule = next(
        item
        for item in _mapping_list(contract["semantic_rules"])
        if item["id"] == "scoped-output-reference-v1"
    )
    semantic_ref = _mapping(rule["semantic_ref"])
    kind = next(
        item
        for item in _mapping_list(contract["node_kinds"])
        if item["id"] == semantic_ref["node_kind"]
    )
    definitions = _mapping(kind["semantic_definitions"])
    definition = _mapping(definitions[_text(semantic_ref["definition"])])
    constraint = _mapping(definition["structured_path_constraint_v1"])
    policy = _mapping(constraint["conservative_tristate_v1"])

    for case_id in (
        "loop-group-pattern-fallback-conservative",
        "loop-group-terminal-ref-false-conservative",
    ):
        case = cases[case_id]
        authored = _mapping(yaml.safe_load(_text(case["definition_yaml"])))
        proof = _mapping(_mapping(case["projection"])["public_contract_proof"])
        authored_nodes = _mapping_list(authored["nodes"])
        nested_nodes = [
            child
            for node in authored_nodes
            if isinstance(node.get("loop_group"), Mapping)
            for child in _mapping_list(
                _mapping(node["loop_group"])["nodes"]
            )
        ]
        producer = next(
            node
            for node in [*authored_nodes, *nested_nodes]
            if node["id"] == proof["producer_id"]
        )
        outcome = _public_contract_path_outcome(
            policy,
            producer["output_format"],
            tuple(_string_list(proof["path"])),
        )

        assert case["valid"] is True
        assert outcome == proof["outcome"]
        assert outcome in _string_list(policy["accept"])


def test_archon_work_one_over_uses_exact_4097_authority_value():
    case = _cases(WorkflowLanguageProfile.ARCHON_2026_07)[
        "loop-group-work-one-over"
    ]

    with pytest.raises(WorkflowValidationError) as exc_info:
        _authority_outcome(case)

    assert _mapping(case["projection"])["child_attempts"] == 4_097
    assert "work bound 4097 exceeds ceiling 4096" in exc_info.value.issues[0].message


def test_archon_unknown_loop_group_field_remains_in_authored_source():
    case = _cases(WorkflowLanguageProfile.ARCHON_2026_07)[
        "loop-group-unknown-field-preserved"
    ]
    source = _parse_case(case)

    assert source.definition_bytes == _text(case["definition_yaml"]).encode("utf-8")
    assert source.nodes[0].value["future_editor_field"] == {
        "preserve": "exactly"
    }
    assert case["valid"] is False
    assert case["codes"] == ["loop_group_shape_invalid"]


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_corpus_covers_supported_node_kinds_and_field_families(profile):
    corpus = workflow_language_conformance(profile)
    feature_tags = {
        feature
        for case in _mapping_list(corpus["cases"])
        for feature in _string_list(case["features"])
    }
    contract = workflow_authoring_contract(profile)

    node_kinds = _mapping_list(contract["node_kinds"])
    assert {f"node-kind:{item['id']}" for item in node_kinds} <= feature_tags
    expected_families = {
        f"field-family:{field_id.partition('.')[0].replace('_', '-')}"
        for field_id in _mapping(contract["field_definitions"])
    }
    expected_families.update({"field-family:definition", "field-family:sidecar"})
    assert expected_families <= feature_tags


def _authority_outcome(case: dict[str, object]):
    source = _parse_case(case)
    return _compile_workflow_source_document(
        source,
        normalizer_version=_integer(case["normalizer_version"]),
    )


def _authored_document_and_scope(
    case: dict[str, object], issue
) -> tuple[str, str]:
    source = _parse_case(case)
    document = "companion" if issue.path.startswith("sidecar.") else "definition"
    group_match = re.match(r"nodes\[(\d+)]\.loop_group(?:\.|$)", issue.path)
    if (
        group_match is not None
        and case["profile"] == WorkflowLanguageProfile.ARCHON_2026_07.value
    ):
        group = source.nodes[int(group_match.group(1))]
        return document, f"loop-group:{group.id}"
    if document == "companion" and issue.code == "unknown_sidecar_node":
        reference = source.sidecar["outward_action_nodes"][0]
        return document, f"loop-group:{reference.split('/', maxsplit=1)[0]}"
    return document, "root"


def _authored_kind_and_family_features(
    case: dict[str, object],
) -> set[str]:
    definition = _mapping(yaml.safe_load(_text(case["definition_yaml"])))
    companion = _mapping(
        yaml.safe_load(_text(case.get("companion_yaml", "{}"))) or {}
    )
    kinds = {
        _text(item["id"])
        for known_profile in WorkflowLanguageProfile
        for item in _mapping_list(
            workflow_authoring_contract(known_profile)["node_kinds"]
        )
    }
    nodes: list[Mapping[str, object]] = []
    pending = _mapping_list(definition.get("nodes", []))
    while pending:
        node = pending.pop(0)
        nodes.append(node)
        group = node.get("loop_group")
        if isinstance(group, Mapping):
            group_mapping = cast(Mapping[str, object], group)
            pending.extend(_mapping_list(group_mapping.get("nodes", [])))

    features = {
        f"node-kind:{kind}"
        for node in nodes
        for kind in kinds
        if kind in node
    }
    if nodes:
        features.add("field-family:node")
    if set(definition) - {"name", "description", "nodes"}:
        features.add("field-family:definition")
    if set(companion) - {"language_compatibility"}:
        features.add("field-family:sidecar")
    for node in nodes:
        if "retry" in node:
            features.add("field-family:retry")
        if "loop" in node:
            features.add("field-family:loop")
        if "loop_group" in node:
            features.add("field-family:loop-group")
        approval = node.get("approval")
        if isinstance(approval, Mapping):
            approval_mapping = cast(Mapping[str, object], approval)
            features.add("field-family:approval")
            if isinstance(approval_mapping.get("on_reject"), Mapping):
                features.add("field-family:approval-reject")
        if isinstance(node.get("agents"), Mapping):
            features.add("field-family:agent")
        hooks = node.get("hooks")
        if isinstance(hooks, Mapping):
            hooks_mapping = cast(Mapping[str, object], hooks)
            features.add("field-family:hook-event")
            entries = [
                entry
                for values in hooks_mapping.values()
                for entry in _mapping_list(values)
            ]
            if entries:
                features.add("field-family:hook-entry")
            responses = [
                cast(Mapping[str, object], entry["response"])
                for entry in entries
                if isinstance(entry.get("response"), Mapping)
            ]
            if responses:
                features.add("field-family:hook-response")
            if any(
                isinstance(response.get("hookSpecificOutput"), Mapping)
                for response in responses
            ):
                features.add("field-family:hook-specific")
    return features


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_kind_and_field_family_tags_are_present_in_authored_yaml(profile):
    cases = _mapping_list(workflow_language_conformance(profile)["cases"])
    for case in cases:
        declared = {
            feature
            for feature in _string_list(case["features"])
            if feature.startswith(("node-kind:", "field-family:"))
        }
        assert declared <= _authored_kind_and_family_features(case), case["id"]


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_every_case_agrees_with_hermes_parser_normalizer_and_diagnostics(profile):
    cases = _mapping_list(workflow_language_conformance(profile)["cases"])
    for case in cases:
        try:
            package = _authority_outcome(case)
        except WorkflowValidationError as exc:
            actual = exc.issues
            actual_valid = False
        else:
            actual = package.validation_issues
            actual_valid = not any(issue.blocking for issue in actual)

        expected = _mapping_list(case["diagnostics"])
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
        assert [
            getattr(issue, "semantic_code", issue.code) for issue in actual
        ] == [item["code"] for item in expected], case["id"]
        assert [
            _authored_document_and_scope(case, issue) for issue in actual
        ] == [
            (item["document"], item["scope"]) for item in expected
        ], case["id"]


def test_projection_facts_are_produced_by_the_normalized_workflow():
    cases = _cases(WorkflowLanguageProfile.ARCHON_2026_07)
    multiple_terminals = cases["loop-group-first-terminal-primary"]
    package = _authority_outcome(multiple_terminals)
    group = package.definition.nodes[0]
    children = group.value["nodes"]
    assert isinstance(children, tuple)
    assert all(isinstance(child, WorkflowNode) for child in children)
    typed_children = cast(tuple[WorkflowNode, ...], children)

    assert multiple_terminals["projection"] == {
        "group_id": group.id,
        "primary_sink": package.language.node_semantics[group.id]["loop_group"][
            "primary_sink"
        ],
        "scoped_node_ids": [
            f"{group.id}/{child.id}" for child in typed_children
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

    assert _text(case["definition_yaml"]).encode("utf-8") == definition.read_bytes()
    assert _text(case["companion_yaml"]).encode("utf-8") == companion.read_bytes()
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
