from __future__ import annotations

import ast
import base64
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path
import re
import zlib

from jsonschema import Draft202012Validator
import pytest
import yaml

import plugins.workflow.language as workflow_language
import plugins.workflow.language_schema as language_schema
from plugins.workflow.language_schema import (
    FIELD_INVENTORY,
    NODE_TYPES,
    compatibility_code_catalog,
    common_node_field_names,
    definition_field_names,
    definition_json_schema,
    loop_group_field_names,
    phase6_durable_code_catalog,
    sidecar_field_names,
    sidecar_json_schema,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import (
    COMMON_NODE_FIELDS,
    SIDECAR_FIELDS,
    TOP_LEVEL_FIELDS,
    load_workflow_snapshot,
)
from plugins.workflow.compat import assess_compatibility


def _node_property(schema: dict[str, object], node_type: str, field: str) -> dict:
    variants = schema["properties"]["nodes"]["items"]["oneOf"]
    variant = next(item for item in variants if node_type in item["required"])
    assert field in variant["properties"]
    return schema["properties"]["nodes"]["items"]["properties"][field]


def _descriptor_definition(contract: dict, descriptor: dict) -> dict:
    return contract["field_definitions"][descriptor["definition_ref"]]


def _node_kind_semantic_definition(contract: dict, descriptor: dict) -> dict:
    reference = descriptor["semantic_ref"]
    node_kind = next(
        item
        for item in contract["node_kinds"]
        if item["id"] == reference["node_kind"]
    )
    return node_kind["semantic_definitions"][reference["definition"]]


def test_archon_authoring_contract_is_bounded_and_versioned():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)

    assert contract["schema_version"] == 1
    assert contract["profile"] == "archon-2026-07"
    assert contract["normalizer_version"] == 6
    assert (
        contract["definition_schema"]["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
    )
    assert len(language_schema.canonical_contract_json(contract).encode()) <= (
        language_schema.CONTRACT_MAX_BYTES
        - language_schema.CONTRACT_RESERVED_GROWTH_BYTES
    )


def test_sidecar_assignment_schema_is_closed_and_bounded():
    schema = sidecar_json_schema(WorkflowLanguageProfile.HERMES_LEGACY)
    assignments = schema["properties"]["assignments"]
    assignment = assignments["additionalProperties"]
    valid = {
        "outward_action_nodes": ["security-review"],
        "assignments": {
            "security-review": {
                "endpoint": "hermes://local/security-reviewer",
                "interaction_policy": "deny",
                "deadline": "PT4H",
                "on_deadline": "cancel_and_fail",
            }
        },
    }

    assert set(assignment["properties"]) == {
        "endpoint",
        "interaction_policy",
        "deadline",
        "on_deadline",
    }
    assert assignment["required"] == ["endpoint"]
    assert assignment["additionalProperties"] is False
    assert assignment["properties"]["interaction_policy"]["enum"] == [
        "pause",
        "deny",
        "auto_cancel",
    ]
    assert not list(Draft202012Validator(schema).iter_errors(valid))
    invalid = json.loads(json.dumps(valid))
    invalid["assignments"]["security-review"]["credential"] = "secret"
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_explicit_v6_contract_derives_bounded_loop_group_surface():
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    contract = workflow_authoring_contract(profile, normalizer_version=6)
    kinds = {item["id"]: item for item in contract["node_kinds"]}
    node_schema = contract["definition_schema"]["properties"]["nodes"]["items"]
    group_schema = node_schema["properties"]["loop_group"]
    codes = contract["compatibility_codes"]

    assert "loop_group" in kinds
    assert set(group_schema["properties"]) == set(loop_group_field_names())
    assert group_schema["required"] == ["nodes", "until", "max_iterations"]
    assert group_schema["properties"]["nodes"]["maxItems"] == 512
    assert group_schema["properties"]["nodes"]["items"]["properties"]["command"] == {
        "$ref": "#/properties/nodes/items/properties/command"
    }
    assert set(phase6_durable_code_catalog()) <= set(codes)
    assert "loop_group" not in {
        item["id"]
        for item in workflow_authoring_contract(profile, normalizer_version=5)[
            "node_kinds"
        ]
    }
    group_kind = kinds["loop_group"]
    payload = next(
        item
        for item in group_kind["fields"]
        if item["field_path"] == "nodes[].loop_group"
    )
    definition = _descriptor_definition(contract, payload)
    assert node_schema["properties"]["loop_group"]["type"] == "object"
    assert definition["widget"] == "object"
    assert definition["examples"] == [
        {
            "nodes": [{"id": "work", "command": "run-work"}],
            "until": "done",
            "max_iterations": 3,
        }
    ]


def test_explicit_v6_contract_publishes_scoped_graph_semantics():
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    contract = workflow_authoring_contract(profile, normalizer_version=6)
    rules = {rule["kind"]: rule for rule in contract["semantic_rules"]}

    assert {
        "scoped-dag-topology-v1",
        "scoped-output-reference-v1",
        "loop-group-work-product-v1",
    } <= set(rules)
    scoped = rules["scoped-dag-topology-v1"]
    assert scoped["group_kind"] == "loop_group"
    assert scoped["body_path"] == ["loop_group", "nodes"]
    assert scoped["node_id_field"] == "id"
    assert scoped["depends_on_field"] == "depends_on"
    assert scoped["allowed_node_kinds"] == [
        "command",
        "prompt",
        "bash",
        "script",
        "loop",
        "approval",
        "cancel",
    ]
    assert scoped["forbidden_node_kinds"] == [
        "include",
        "workflow",
        "loop_group",
    ]
    assert scoped["forbidden_group_fields"] == ["retry"]
    assert scoped["group_fields"] == sorted(loop_group_field_names())
    assert scoped["required_group_fields"] == [
        "nodes",
        "until",
        "max_iterations",
    ]
    assert scoped["min_nodes"] == 1
    assert scoped["max_depth"] == 1
    assert scoped["max_nodes"] == 512
    assert scoped["max_edges"] == 4096
    assert scoped["min_iterations"] == 1
    assert scoped["max_iterations"] == 100
    assert scoped["primary_sink"] == "first-terminal-in-definition-order"
    assert scoped["validation_codes"] == {
        "topology": "loop_group_topology_invalid",
        "visibility": "loop_group_scope_invalid",
        "nesting": "loop_group_shape_invalid",
        "capacity": "loop_group_product_limit",
        "work_product": "loop_group_product_limit",
    }

    references = rules["scoped-output-reference-v1"]
    assert references["current_scope"] == {
        "applies_to": ["nodes[].loop_group.nodes[]"],
        "producer_scope": "body-sibling",
        "requires_direct_dependency": True,
    }
    assert references["outer_scope"] == {
        "applies_to": [
            "nodes[].loop_group.nodes[]",
            "nodes[].loop_group.until_bash",
            "nodes[].loop_group.gate_message",
        ],
        "producer_scope": "outer-node",
        "requires_group_dependency": True,
    }
    assert references["previous_iteration"] == {
        "applies_to": [
            "nodes[].loop_group.nodes[]",
            "nodes[].loop_group.until_bash",
        ],
        "producer_scope": "body-node",
        "prefix": "$LOOP_PREV.",
        "requires_direct_dependency": False,
        "first_iteration": {
            "known_whole_output": {
                "result": "resolved",
                "rendered_text": "",
            },
            "known_structured_path": {
                "result": "error",
                "code": "output_reference_missing",
            },
        },
    }
    assert references["unqualified_producer_resolution"] == {
        "order": ["body-sibling", "outer-node"],
        "collision_precedence": "body-sibling",
    }
    assert references["interpolation_surface_v1"] == {
        "fields": [
            {
                "field_path": "nodes[].loop_group.nodes[].when",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].prompt",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].bash",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].script",
                "template_source": "inline-or-authenticated-script-body",
                "authored_value": (
                    "reference-template-if-inline-otherwise-literal-resource-name"
                ),
                "value_discriminator": "script-inline-v1",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].loop.prompt",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].loop.until_bash",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].loop.gate_message",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].loop.command",
                "template_source": "authenticated-command-body",
                "authored_value": "literal-resource-name",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].approval.message",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].approval.on_reject.prompt"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].command",
                "template_source": "authenticated-command-body",
                "authored_value": "literal-resource-name",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].systemPrompt",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].agents.*.description"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.nodes[].agents.*.prompt",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].hooks.*[].response.systemMessage"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].hooks.*[].response.stopReason"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].hooks.*[].response."
                    "hookSpecificOutput.permissionDecisionReason"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": (
                    "nodes[].loop_group.nodes[].hooks.*[].response."
                    "hookSpecificOutput.additionalContext"
                ),
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.until_bash",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
            {
                "field_path": "nodes[].loop_group.gate_message",
                "template_source": "authored-value",
                "authored_value": "reference-template",
            },
        ],
        "value_discriminators_v1": {
            "script-inline-v1": {
                "operation": "contains-listed-codepoint",
                "codepoint_ranges": [
                    [9, 13],
                    [28, 32],
                    [133, 133],
                    [160, 160],
                    [5760, 5760],
                    [8192, 8202],
                    [8232, 8233],
                    [8239, 8239],
                    [8287, 8287],
                    [12288, 12288],
                ],
                "characters": ";(){}&|<>$`\"'",
                "match": "reference-template",
                "otherwise": "literal-resource-name",
            }
        },
        "unlisted_authored_string_fields": "literal",
    }
    assert references["semantic_ref"] == {
        "node_kind": "loop_group",
        "definition": "scoped-output-reference-v1",
    }
    reference_details = _node_kind_semantic_definition(contract, references)
    assert reference_details["group_until_bash"] == {
        "field_path": ["loop_group", "until_bash"],
        "current_scope": "all-body-nodes",
    }
    assert reference_details["companion_node_paths"] == {
        "format": "group/child",
        "field_paths": ["sidecar.outward_action_nodes[]"],
        "validation_code": "unknown_sidecar_node",
    }
    structured_details = reference_details["structured_path_constraint_v1"]
    assert structured_details["syntax_rule"] == "strict-output-reference"
    assert structured_details["producer_schema_resolution_v1"] == {
        "ordinary": ["output_format"],
        "loop_group": [
            "scoped-dag-topology-v1.primary_sink",
            "output_format",
        ],
    }
    proof = structured_details["conservative_tristate_v1"]
    assert proof["accept"] == ["possible", "unknown"]
    assert proof["reject"] == "impossible"
    assert proof["modes"] == {
        "ascii-decimal": "all(object,array)",
        "other": "object",
    }
    assert proof["strategies"] == {
        "schema": "false=impossible;true|nonmap=unknown",
        "type": "exclude-mode",
        "properties": "exact-child",
        "patternProperties": "nonempty-unknown",
        "additionalProperties": "false-impossible/schema-tail/unknown",
        "maxItems": "index>=impossible",
        "prefixItems": "index-first",
        "items": "schema-or-index",
        "additionalItems": "list-overflow",
        "allOf": "any-impossible",
        "union": ["anyOf", "oneOf", "nonempty-all-impossible"],
        "unlisted": "ignored=unknown",
    }
    assert proof["evaluation_v1"] == {
        "$ref_resolution": {
            "schema_scope": "current-schema-only",
            "when": "path-segments-remain",
            "terminal_child": "not-resolved",
        },
        "object_lookup": {
            "order": [
                "properties",
                "patternProperties",
                "additionalProperties",
            ],
            "stop_after_first_applicable": True,
            "applicability": {
                "properties": "exact-key-match",
                "patternProperties": "nonempty-map",
                "additionalProperties": "fallback",
            },
        },
        "terminal_child": {
            "when": "no-path-segments-remain",
            "false": "impossible",
            "otherwise": "possible",
            "$ref": "not-resolved",
        },
    }
    assert proof["$ref"] == (
        "local-pointer(map/array,~0/~1);false|impossible=>impossible;"
        "unresolved|nonlocal|cycle=>unknown"
    )
    assert proof["dotted_key"] == (
        "after=impossible;joined-tail=literal-key;"
        "walk=$ref(map)/combinators(any)/properties/array-items;"
        "type=capable"
    )
    assert structured_details["diagnostic_table"] == {
        "codes": {
            "L": "loop_group_scope_invalid",
            "D": "output_reference_not_declared_dependency",
            "U": "output_reference_path_unsupported",
            "F": "structured_output_field_impossible",
        },
        "cols": ["when", "semantic", "body", "until", "gate"],
        "rows": [
            [
                "dep",
                "scoped-reference-missing-dependency",
                "L",
                "D",
                "D",
            ],
            [
                "prev",
                "scoped-reference-unknown-producer",
                "L",
                "L",
                None,
            ],
            [
                "companion",
                "scoped-companion-reference-unknown-node",
                None,
                None,
                None,
            ],
            [
                "no_schema",
                "scoped-reference-producer-schema-required",
                "L",
                "L",
                "U",
            ],
            [
                "impossible",
                "scoped-reference-structured-path-impossible",
                "L",
                "L",
                "F",
            ],
            [
                "dotted",
                "scoped-reference-structured-path-impossible",
                "L",
                "L",
                "U",
            ],
        ],
    }

    work = rules["loop-group-work-product-v1"]
    assert work["limit"] == 4096
    assert work["accumulators"] == ["executions", "attempts"]
    assert work["semantic_ref"] == {
        "node_kind": "loop_group",
        "definition": "loop-group-work-product-v1",
    }
    formula = _node_kind_semantic_definition(contract, work)
    assert formula["expression_format"] == "prefix-v1"
    assert formula["expressions"] == {
        "executions": [
            "*",
            "group_iterations",
            ["sum", "body_nodes", "ordinary_loop_multiplier"],
        ],
        "attempts": [
            "*",
            "group_iterations",
            [
                "sum",
                "body_nodes",
                [
                    "*",
                    "ordinary_loop_multiplier",
                    ["+", "selected_retries", 1],
                ],
            ],
        ],
    }
    assert work["group_iterations_path"] == ["loop_group", "max_iterations"]
    assert work["ordinary_loop_multiplier_path"] == ["loop", "max_iterations"]
    assert work["retry_max_attempts_path"] == ["retry", "max_attempts"]
    assert work["approval_max_attempts_path"] == [
        "approval",
        "on_reject",
        "max_attempts",
    ]
    assert work["ordinary_loop_default_multiplier"] == 1
    assert work["command_prompt_default_retries"] == 2
    assert work["other_default_retries"] == 0
    assert work["approval_default_max_attempts"] == 3
    assert formula["retry_precedence"] == (
        "approval>retry>command|prompt>default"
    )
    loop_group_topic = next(
        topic
        for topic in contract["documentation"]["topics"]
        if topic["id"] == "durable-loop-groups"
    )
    assert loop_group_topic == {
        "id": "durable-loop-groups",
        "title": "Durable bounded loop groups",
        "description": "One immutable nested body with bounded iterations.",
        "field_paths": [
            "nodes[].loop_group.nodes",
            "nodes[].loop_group.until",
            "nodes[].loop_group.max_iterations",
            "nodes[].loop_group.fresh_context",
            "nodes[].loop_group.until_bash",
            "nodes[].loop_group.interactive",
            "nodes[].loop_group.signal_completes",
            "nodes[].loop_group.gate_message",
        ],
        "applicability": {
            "profiles": ["archon-2026-07"],
            "documents": ["definition"],
        },
        "parameters": {
            "body_depth": 1,
            "body_nodes": {"minimum": 1, "maximum": 512},
            "body_edges": {"maximum": 4096},
            "max_iterations": {"minimum": 1, "maximum": 100},
            "primary_sink": "first_terminal_in_definition_order",
            "group_fields": [
                "fresh_context",
                "gate_message",
                "interactive",
                "max_iterations",
                "nodes",
                "signal_completes",
                "until",
                "until_bash",
            ],
            "reference_scopes": {
                "current_body": "direct_sibling_dependency",
                "outer": "direct_group_dependency",
                "previous_body": "immediately_previous_iteration",
            },
            "effective_interactive_requires": [
                "workflow.interactive",
                "loop_group.interactive",
            ],
            "rejected": [
                "include",
                "nested_loop_group",
                "runtime_workflow",
                "group_retry",
                "returns",
            ],
        },
    }
    assert json.loads(json.dumps(rules, sort_keys=True)) == rules


@pytest.mark.parametrize(
    ("profile", "normalizer_version"),
    [
        (WorkflowLanguageProfile.HERMES_LEGACY, 2),
        (WorkflowLanguageProfile.ARCHON_2026_07, 5),
    ],
)
def test_profiles_without_loop_groups_omit_scoped_graph_semantics(
    profile, normalizer_version
):
    rules = workflow_authoring_contract(
        profile,
        normalizer_version=normalizer_version,
    )["semantic_rules"]

    scoped_kinds = {
        "scoped-dag-topology-v1",
        "scoped-output-reference-v1",
        "loop-group-work-product-v1",
    }
    assert scoped_kinds.isdisjoint(rule["id"] for rule in rules)
    assert all("kind" not in rule for rule in rules)


@pytest.mark.parametrize(
    ("group_options", "expected"),
    [
        pytest.param(
            {"interactive": True, "gate_message": "Continue?"},
            True,
            id="interactive-with-gate",
        ),
        pytest.param(
            {"interactive": True},
            False,
            id="interactive-requires-gate",
        ),
    ],
)
def test_explicit_v6_group_interactivity_has_schema_loader_parity(
    group_options, expected
):
    document = _workflow({
        "id": "group",
        "loop_group": {
            "nodes": [{"id": "child", "bash": "true"}],
            "until": "done",
            "max_iterations": 1,
            **group_options,
        },
    })

    assert _structural_outcomes(
        document,
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=6,
    ) == (expected, expected)


def test_phase4_loop_inventory_remains_explicitly_readable_without_changing_v1_v3_schemas():
    loop_specs = {
        spec.yaml_name: spec for spec in FIELD_INVENTORY if spec.scope == "loop"
    }

    assert loop_specs["command"].enforcement_phase == 4
    assert loop_specs["signal_completes"].enforcement_phase == 4
    assert loop_specs["signal_completes"].json_type == "boolean"
    assert loop_specs["prompt"].required is False

    schema = definition_json_schema(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )
    loop_schema = schema["properties"]["nodes"]["items"]["properties"]["loop"]
    assert "prompt" not in loop_schema.get("required", ())
    assert "command" in loop_schema["properties"]
    assert "signal_completes" in loop_schema["properties"]

    contract = workflow_authoring_contract(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )
    loop_kind = next(item for item in contract["node_kinds"] if item["id"] == "loop")
    loop_paths = {item["field_path"] for item in loop_kind["fields"]}
    assert "nodes[].loop.command" in loop_paths
    assert "nodes[].loop.signal_completes" in loop_paths
    assert schema == definition_json_schema(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )
    assert contract["node_kinds"] == language_schema.node_kind_descriptors(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )

    phase3_schema = definition_json_schema(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=3,
    )
    phase3_loop = phase3_schema["properties"]["nodes"]["items"]["properties"]["loop"]
    assert "prompt" in phase3_loop["required"]
    assert "command" not in phase3_loop["properties"]
    assert "signal_completes" not in phase3_loop["properties"]

    legacy_schema = definition_json_schema(WorkflowLanguageProfile.HERMES_LEGACY)
    legacy_kinds = language_schema.node_kind_descriptors(
        WorkflowLanguageProfile.HERMES_LEGACY
    )
    for normalizer_version in (1, 2):
        assert legacy_schema == definition_json_schema(
            WorkflowLanguageProfile.HERMES_LEGACY,
            normalizer_version=normalizer_version,
        )
        assert legacy_kinds == language_schema.node_kind_descriptors(
            WorkflowLanguageProfile.HERMES_LEGACY,
            normalizer_version=normalizer_version,
        )


def test_explicit_v4_authoring_contract_exposes_current_loop_fields():
    contract = workflow_authoring_contract(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )

    assert contract["normalizer_version"] == 4
    loop_schema = contract["definition_schema"]["properties"]["nodes"]["items"][
        "properties"
    ]["loop"]
    assert {"command", "signal_completes"} <= set(loop_schema["properties"])
    loop_kind = next(item for item in contract["node_kinds"] if item["id"] == "loop")
    loop_paths = {item["field_path"] for item in loop_kind["fields"]}
    assert {
        "nodes[].loop.command",
        "nodes[].loop.signal_completes",
    } <= loop_paths


def test_explicit_v4_contract_relates_compile_only_includes_and_loop_choices():
    """Catch v4 syntax leaking into v3 or an include becoming executable."""
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    current = workflow_authoring_contract(profile, normalizer_version=4)
    phase4 = workflow_authoring_contract(profile, normalizer_version=4)

    current_items = current["definition_schema"]["properties"]["nodes"]["items"]
    phase3_items = workflow_authoring_contract(
        profile,
        normalizer_version=3,
    )["definition_schema"]["properties"]["nodes"]["items"]
    phase4_variants = phase4["definition_schema"]["properties"]["nodes"]["items"][
        "oneOf"
    ]
    assert current["normalizer_version"] == 4
    assert phase4["normalizer_version"] == 4
    assert current_items == phase4["definition_schema"]["properties"]["nodes"]["items"]
    assert "include" in current_items["properties"]
    assert any("include" in variant["required"] for variant in current_items["oneOf"])
    assert "include" not in phase3_items["properties"]
    assert not any(
        "include" in variant["required"] for variant in phase3_items["oneOf"]
    )
    include = next(
        variant for variant in phase4_variants if "include" in variant["required"]
    )
    assert set(include["properties"]) == {
        "id",
        "include",
        "depends_on",
        "trigger_rule",
    }
    assert "include" not in {item["id"] for item in phase4["node_kinds"]}
    assert "include" not in NODE_TYPES

    loop = phase4["definition_schema"]["properties"]["nodes"]["items"]["properties"][
        "loop"
    ]
    choices = {
        (
            tuple(choice["required"]),
            tuple(choice["not"]["required"]),
        )
        for choice in loop["oneOf"]
    }
    assert choices == {
        (("prompt",), ("command",)),
        (("command",), ("prompt",)),
    }


def test_explicit_v4_contract_documents_signal_interactions_and_stable_codes():
    """Catch generated v4 contracts omitting operational semantics or failures."""
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    current = workflow_authoring_contract(profile, normalizer_version=4)
    phase3 = workflow_authoring_contract(profile, normalizer_version=3)
    phase4 = workflow_authoring_contract(profile, normalizer_version=4)
    current_topics = {item["id"]: item for item in current["documentation"]["topics"]}
    phase3_topics = {item["id"]: item for item in phase3["documentation"]["topics"]}
    phase4_topics = {item["id"]: item for item in phase4["documentation"]["topics"]}

    assert "ordinary-loops-and-includes" not in phase3_topics
    assert current_topics == phase4_topics
    topic = phase4_topics["ordinary-loops-and-includes"]
    assert topic["parameters"]["include_mode"] == "compile_only"
    assert topic["parameters"]["loop_prompt_sources"] == {
        "cardinality": "exactly_one",
        "fields": ["prompt", "command"],
    }
    assert topic["parameters"]["effective_interactive_requires"] == [
        "workflow.interactive",
        "loop.interactive",
    ]
    assert topic["parameters"]["signal_completes_defaults"] == {
        "effective_interactive": False,
        "otherwise": True,
    }
    assert topic["parameters"]["signal_confirmation_actions"] == {
        "before_final_iteration": ["approve", "provide-input", "cancel"],
        "final_iteration": ["approve", "cancel"],
    }

    registered = set(language_schema.phase4_durable_code_catalog())
    assert registered
    assert registered.isdisjoint(phase3["compatibility_codes"])
    assert registered <= set(current["compatibility_codes"])
    assert registered <= set(phase4["compatibility_codes"])
    assert not any(
        entry.get("blocking") and entry.get("enforcement_phase") == 4
        for entry in phase4["compatibility_codes"].values()
    )


@pytest.mark.parametrize(
    "projection",
    [
        pytest.param(definition_json_schema, id="definition-schema"),
        pytest.param(language_schema.node_kind_descriptors, id="node-kinds"),
        pytest.param(language_schema.semantic_rule_descriptors, id="semantic-rules"),
        pytest.param(workflow_authoring_contract, id="authoring-contract"),
    ],
)
def test_versioned_authoring_projections_reject_impossible_profile_pair(
    projection,
):
    with pytest.raises(workflow_language.WorkflowLanguageCompatibilityError) as raised:
        projection(
            WorkflowLanguageProfile.HERMES_LEGACY,
            normalizer_version=4,
        )

    assert raised.value.code == "workflow_normalizer_version_unsupported"


@pytest.mark.parametrize("normalizer_version", [1, 2])
def test_legacy_authoring_contract_preserves_supported_versions(
    normalizer_version,
):
    contract = workflow_authoring_contract(
        WorkflowLanguageProfile.HERMES_LEGACY,
        normalizer_version=normalizer_version,
    )

    assert contract["normalizer_version"] == normalizer_version
    loop_schema = contract["definition_schema"]["properties"]["nodes"]["items"][
        "properties"
    ]["loop"]
    assert "prompt" in loop_schema["required"]
    assert "command" not in loop_schema["properties"]
    loop_kind = next(item for item in contract["node_kinds"] if item["id"] == "loop")
    assert "nodes[].loop.command" not in {
        item["field_path"] for item in loop_kind["fields"]
    }


def test_legacy_contract_only_adds_declared_rejections_to_merge_base():
    fixture_path = (
        Path(__file__).with_name("fixtures")
        / "legacy-contract-c1dc7a23.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["merge_base"] == (
        "c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d"
    )
    golden = zlib.decompress(
        base64.b85decode(
            "".join(fixture["canonical_zlib_base85"].splitlines())
        )
    )
    assert len(golden) == fixture["canonical_bytes"]
    assert sha256(golden).hexdigest() == fixture["canonical_sha256"]

    golden_contract = json.loads(golden)
    current = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    expected = {
        "artifacts_version_unsupported": ["nodes[].artifacts"],
        "loop_group_version_unsupported": ["nodes[].loop_group"],
    }
    current_codes = current["compatibility_codes"]
    assert isinstance(current_codes, dict)
    for code, fields in expected.items():
        entry = current_codes.pop(code)
        assert {
            "blocking": entry["blocking"],
            "enforcement_phase": entry["enforcement_phase"],
            "fields": entry["fields"],
            "runtime_status": entry["runtime_status"],
            "severity": entry["severity"],
            "status": entry["status"],
        } == {
            "blocking": True,
            "enforcement_phase": 6,
            "fields": fields,
            "runtime_status": "blocking",
            "severity": "error",
            "status": "deferred",
        }

    assert current.pop("contract_digest") != golden_contract.pop("contract_digest")
    assert current == golden_contract


def test_phase6_semantic_rules_retain_kind_projection():
    rules = language_schema.semantic_rule_descriptors(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=6,
    )

    assert rules
    assert all(rule["kind"] == rule["id"] for rule in rules)


def test_archon_contract_reserves_growth_headroom_and_section_budgets():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)

    limits = contract["limits"]
    assert len(language_schema.canonical_contract_json(contract).encode()) <= (
        limits["max_contract_bytes"] - limits["reserved_growth_bytes"]
    )
    for section, maximum in limits["section_max_bytes"].items():
        assert (
            len(language_schema.canonical_contract_json(contract[section]).encode())
            <= maximum
        )


def _padded_contract_value(value, target_bytes):
    baseline = len(language_schema.canonical_contract_json(value).encode())
    with_empty_padding = {**value, "x-task9-padding": ""}
    overhead = (
        len(language_schema.canonical_contract_json(with_empty_padding).encode())
        - baseline
    )
    return {**value, "x-task9-padding": "x" * (target_bytes - baseline - overhead)}


def test_contract_total_bound_accepts_the_exact_boundary_and_rejects_overflow():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    boundary = _padded_contract_value(
        contract,
        language_schema.CONTRACT_MAX_BYTES
        - language_schema.CONTRACT_RESERVED_GROWTH_BYTES,
    )

    language_schema._require_contract_bounds(boundary)
    with pytest.raises(ValueError, match="contract exceeds"):
        language_schema._require_contract_bounds({
            **boundary,
            "x-task9-padding": f"{boundary['x-task9-padding']}x",
        })


@pytest.mark.parametrize(
    "section",
    tuple(language_schema.CONTRACT_SECTION_MAX_BYTES),
)
def test_contract_section_bounds_accept_exact_boundaries_and_reject_overflow(section):
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    boundary_section = _padded_contract_value(
        {}, language_schema.CONTRACT_SECTION_MAX_BYTES[section]
    )
    boundary = {
        **contract,
        **{
            bounded_section: {}
            for bounded_section in language_schema.CONTRACT_SECTION_MAX_BYTES
        },
        section: boundary_section,
    }

    language_schema._require_contract_bounds(boundary)
    with pytest.raises(ValueError, match=f"{section} exceeds"):
        language_schema._require_contract_bounds({
            **boundary,
            section: {
                **boundary_section,
                "x-task9-padding": f"{boundary_section['x-task9-padding']}x",
            },
        })


def test_serialized_editor_contract_resolves_every_field_definition_without_python():
    wire = json.loads(
        json.dumps(workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07))
    )

    definitions = wire["field_definitions"]
    referenced = set()
    for node_kind in wire["node_kinds"]:
        for descriptor in node_kind["fields"]:
            definition_id = descriptor["definition_ref"]
            assert definition_id.count(".") >= 1
            assert definition_id in definitions
            definition = definitions[definition_id]
            assert definition["description"]
            assert definition["examples"]
            referenced.add(definition_id)

    assert referenced
    assert len(definitions) == len(set(definitions))
    assert {
        "node.timeout",
        "hook_entry.timeout",
        "retry.max_attempts",
        "approval_reject.max_attempts",
    } <= set(definitions)


def test_field_definition_catalog_rejects_duplicate_scope_ids(monkeypatch):
    monkeypatch.setattr(
        language_schema,
        "FIELD_INVENTORY",
        (*FIELD_INVENTORY, FIELD_INVENTORY[0]),
    )

    with pytest.raises(RuntimeError, match="definition.name"):
        language_schema.field_definition_catalog(WorkflowLanguageProfile.ARCHON_2026_07)


def test_editor_projection_version_makes_v1_rejection_and_v2_resolution_explicit():
    wire = json.loads(
        json.dumps(workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07))
    )

    assert wire["contract_reader_version"] == 2
    assert wire["editor_projection_version"] == 2
    assert wire["contract_reader_version"] > 1  # A v1 reader must reject this envelope.
    first = wire["node_kinds"][0]["fields"][0]
    assert wire["field_definitions"][first["definition_ref"]]["description"]


def test_archon_semantic_fields_retain_useful_human_descriptions():
    schema = definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)

    assert _node_property(schema, "bash", "timeout")["description"] == (
        "Timeout for this workflow node."
    )
    assert _node_property(schema, "bash", "bash")["description"] == (
        "Bash for this workflow node."
    )


def test_archon_contract_describes_phase3_authoring_semantics_from_inventory():
    """Catch the generated contract falling back to obsolete Phase 2 guidance."""
    schema = definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)

    assert _node_property(schema, "bash", "timeout")["x-hermes-semantics"] == {
        "unit": "milliseconds",
        "omitted": 120_000,
        "scope": "attempt",
    }
    assert _node_property(schema, "prompt", "idle_timeout")["x-hermes-semantics"] == {
        "unit": "milliseconds",
        "omitted": "sealed_ai_idle",
        "scope": "attempt",
    }
    retry = _node_property(schema, "prompt", "retry")
    assert retry["properties"]["max_attempts"]["x-hermes-semantics"] == {
        "counts": "retries_after_initial",
        "omitted_ai": 2,
        "omitted_deterministic": 0,
    }
    assert _node_property(schema, "bash", "depends_on")["x-hermes-semantics"] == {
        "output_references": "direct_only"
    }
    assert _node_property(schema, "prompt", "when")["x-hermes-semantics"] == {
        "operands": "typed_scalar",
        "false": "skip",
        "errors": "fail_pre_execution",
    }

    assert _node_property(schema, "bash", "bash")["x-hermes-semantics"] == {
        "inline_utf8_bytes": 32_768,
        "rendered_command_utf8_bytes": 96 * 1024,
        "spill_value_utf8_bytes": 500_000,
        "spill_files": 64,
        "spill_total_utf8_bytes": 2_000_000,
        "large_values": "contents",
        "contexts": {
            "unquoted_token": "substitute",
            "double_quoted_token": "substitute",
            "single_quoted_token": "safe_quote_boundary",
            "escaped_or_comment": "literal",
        },
        "unsupported_context": "fail",
    }
    assert _node_property(schema, "prompt", "persist_session")[
        "x-hermes-semantics"
    ] == {"confirmed_cross_run_missing": "one_fresh_execution"}

    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    kinds = {item["id"]: item for item in contract["node_kinds"]}

    for kind, field_path, schema_field in (
        ("bash", "nodes[].timeout", "timeout"),
        ("prompt", "nodes[].idle_timeout", "idle_timeout"),
        ("bash", "nodes[].depends_on", "depends_on"),
        ("prompt", "nodes[].when", "when"),
        ("bash", "nodes[].bash", "bash"),
        ("prompt", "nodes[].persist_session", "persist_session"),
    ):
        descriptor = next(
            item for item in kinds[kind]["fields"] if item["field_path"] == field_path
        )
        schema_metadata = _node_property(schema, kind, schema_field)
        definition = _descriptor_definition(contract, descriptor)
        assert definition["semantics"] == schema_metadata["x-hermes-semantics"]
        if "x-hermes-unit" in schema_metadata:
            assert definition["unit"] == schema_metadata["x-hermes-unit"]

    retry_descriptor = next(
        item
        for item in kinds["prompt"]["fields"]
        if item["field_path"] == "nodes[].retry.max_attempts"
    )
    retry_definition = _descriptor_definition(contract, retry_descriptor)
    assert (
        retry_definition["semantics"]
        == retry["properties"]["max_attempts"]["x-hermes-semantics"]
    )
    assert retry_definition["unit"] == "count"


def test_legacy_editor_descriptors_keep_units_without_v3_semantics():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    kinds = {item["id"]: item for item in contract["node_kinds"]}

    timeout = next(
        item
        for item in kinds["bash"]["fields"]
        if item["field_path"] == "nodes[].timeout"
    )
    retry = next(
        item
        for item in kinds["prompt"]["fields"]
        if item["field_path"] == "nodes[].retry.max_attempts"
    )
    timeout_definition = _descriptor_definition(contract, timeout)
    retry_definition = _descriptor_definition(contract, retry)
    assert timeout_definition["unit"] == "seconds"
    assert retry_definition["unit"] == "count"
    assert "semantics" not in timeout_definition
    assert "semantics" not in retry_definition
    assert (
        language_schema.resolve_field_semantics(
            WorkflowLanguageProfile.HERMES_LEGACY,
            "node.timeout",
        )
        is None
    )


def test_archon_contract_documentation_derives_stable_codes_and_phase_boundaries():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    topics = {item["id"]: item for item in contract["documentation"]["topics"]}

    assert topics["stable-codes"]["code_source"] == "compatibility_codes"
    assert "codes" not in topics["stable-codes"]
    assert topics["persistent-session-recovery"]["operator_surfaces"] == [
        "workflow doctor",
        "Run Inspector recovery evidence",
    ]
    assert topics["extension-options"] == {
        "id": "extension-options",
        "parameters": {
            "mcp_skills": "options_not_node_kinds",
            "loops_includes_phase": 4,
        },
    }
    legacy_topics = {
        item["id"]: item
        for item in workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)[
            "documentation"
        ]["topics"]
    }
    assert "extension-options" not in legacy_topics


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_authoring_contract_generation_is_byte_deterministic(profile):
    first = json.dumps(
        workflow_authoring_contract(profile),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    second = json.dumps(
        workflow_authoring_contract(profile),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert first == second


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_authoring_contract_publishes_a_self_verifying_editor_envelope(profile):
    contract = workflow_authoring_contract(profile)
    digest_payload = {
        key: value for key, value in contract.items() if key != "contract_digest"
    }
    expected_digest = sha256(
        language_schema.canonical_contract_json(digest_payload).encode()
    ).hexdigest()

    assert contract["contract_reader_version"] == 2
    assert contract["editor_projection_version"] == 2
    assert contract["contract_digest"] == f"sha256:{expected_digest}"
    assert contract["limits"] == {
        "max_document_bytes": 2 * 1024 * 1024,
        "max_contract_bytes": 288_000,
        "reserved_growth_bytes": 4_000,
        "section_max_bytes": {
            "definition_schema": 160_000,
            "node_kinds": 72_000,
            "compatibility_codes": 19_000,
        },
    }
    assert contract["x-hermes-provenance"]["field_authority"] == (
        "plugins.workflow.language_schema.FIELD_INVENTORY"
    )
    assert contract["x-hermes-filenames"]["companion_suffix"] == (".hermes.yaml")


def test_contract_canonical_json_matches_javascript_number_and_sorting_rules():
    payload = {
        "z": [1.0, {"negative_zero": -0.0, "small": 1e-7, "threshold": 1e-6}],
        "large_fixed": 1e20,
        "large_exponent": 1e21,
        "a": {"z": True, "a": None},
    }

    assert language_schema.canonical_contract_json(payload) == (
        '{"a":{"a":null,"z":true},"large_exponent":1e+21,'
        '"large_fixed":100000000000000000000,'
        '"z":[1,{"negative_zero":0,"small":1e-7,"threshold":0.000001}]}'
    )


def test_contract_canonical_json_matches_javascript_unicode_rules():
    payload = {
        "\ue000": "private-use",
        "😀": "snowman ☃",
        "é": 'line one\nline "two"',
    }

    assert language_schema.canonical_contract_json(payload) == (
        '{"é":"line one\\nline \\"two\\"","😀":"snowman ☃","\ue000":"private-use"}'
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_contract_canonical_json_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="finite"):
        language_schema.canonical_contract_json({"value": value})


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_node_kind_descriptors_cover_each_applicable_node_field_once(profile):
    contract = workflow_authoring_contract(profile)
    descriptors = {item["id"]: item for item in contract["node_kinds"]}

    expected_node_types = (
        set(language_schema.EXECUTABLE_NODE_TYPES)
        if profile is WorkflowLanguageProfile.ARCHON_2026_07
        else set(NODE_TYPES)
    )
    assert set(descriptors) == expected_node_types
    for node_type, descriptor in descriptors.items():
        assert descriptor["field_path"] == f"nodes[].{node_type}"
        assert descriptor["status"] == "supported"
        fields = descriptor["fields"]
        paths = [field["field_path"] for field in fields]
        orders = [field["order"] for field in fields]
        expected_direct_paths = {
            f"nodes[].{spec.yaml_name}"
            for spec in FIELD_INVENTORY
            if spec.scope == "node"
            and node_type in spec.applicable_node_types
            and spec.enforcement_phase <= contract["normalizer_version"]
            and not (
                profile is WorkflowLanguageProfile.ARCHON_2026_07
                and (
                    (
                        spec.yaml_name == "idle_timeout"
                        and node_type not in {"command", "prompt"}
                    )
                    or (
                        spec.yaml_name == "retry"
                        and node_type not in {"command", "prompt", "bash", "script"}
                    )
                )
            )
        }

        assert expected_direct_paths <= set(paths)
        assert len(paths) == len(set(paths))
        assert len(orders) == len(set(orders))
        assert all(
            _descriptor_definition(contract, field)["description"] for field in fields
        )
        assert all(
            field["applicability"]["node_kinds"] == [node_type] for field in fields
        )

    assert "nodes[].retry.max_attempts" in {
        field["field_path"] for field in descriptors["command"]["fields"]
    }
    assert "nodes[].retry.max_attempts" not in {
        field["field_path"] for field in descriptors["loop"]["fields"]
    }
    assert "nodes[].loop.max_iterations" in {
        field["field_path"] for field in descriptors["loop"]["fields"]
    }
    assert "nodes[].approval.on_reject.prompt" in {
        field["field_path"] for field in descriptors["approval"]["fields"]
    }
    assert "nodes[].agents.*.prompt" in {
        field["field_path"] for field in descriptors["prompt"]["fields"]
    }


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_authoring_contract_publishes_live_dag_and_condition_reference_rules(profile):
    rules = {
        item["id"]: item
        for item in workflow_authoring_contract(profile)["semantic_rules"]
    }
    topology = rules["dag-topology"]
    references = rules["condition-output-reference"]

    assert topology["status"] == "supported"
    assert topology["parameters"] == {
        "nodes_path": "nodes",
        "id_field": "id",
        "dependencies_field": "depends_on",
        "acyclic": True,
        "unique_ids": True,
    }
    assert references["status"] == "supported"
    assert references["field_paths"] == ["nodes[].when"]
    assert references["parameters"]["syntax"] == "$ID.output(.path)*"
    assert references["parameters"]["node_id_capture_group"] == 1
    assert references["parameters"]["require_upstream"] is True
    conditions = rules["condition-expression"]
    assert conditions["status"] == "supported"
    assert conditions["field_paths"] == ["nodes[].when"]
    assert "pattern" not in conditions["parameters"]
    assert "syntax" not in conditions["parameters"]
    assert isinstance(conditions["parameters"]["expression_pattern"], str)


def test_explicit_v4_strict_output_rule_adds_only_v4_loop_template_paths():
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    current_rule_list = workflow_authoring_contract(
        profile,
        normalizer_version=4,
    )["semantic_rules"]
    current_rules = {item["id"]: item for item in current_rule_list}
    phase3_rule_list = workflow_authoring_contract(
        profile,
        normalizer_version=3,
    )["semantic_rules"]
    phase3_rules = {item["id"]: item for item in phase3_rule_list}
    current_paths = set(current_rules["strict-output-reference"]["field_paths"])
    phase3_paths = set(phase3_rules["strict-output-reference"]["field_paths"])

    assert current_paths - phase3_paths == {
        "nodes[].loop.command",
        "nodes[].loop.gate_message",
    }
    assert {
        "nodes[].loop.command",
        "nodes[].loop.gate_message",
    }.isdisjoint(phase3_paths)
    assert (
        language_schema.semantic_rule_descriptors(
            profile,
            normalizer_version=4,
        )
        == current_rule_list
    )
    assert (
        language_schema.semantic_rule_descriptors(
            profile,
            normalizer_version=3,
        )
        == phase3_rule_list
    )

    legacy_default = language_schema.semantic_rule_descriptors(
        WorkflowLanguageProfile.HERMES_LEGACY
    )
    assert (
        language_schema.semantic_rule_descriptors(
            WorkflowLanguageProfile.HERMES_LEGACY,
            normalizer_version=1,
        )
        == legacy_default
    )
    assert (
        language_schema.semantic_rule_descriptors(
            WorkflowLanguageProfile.HERMES_LEGACY,
            normalizer_version=2,
        )
        == legacy_default
    )


def test_condition_contract_publishes_ecmascript_unicode_grammar():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    rules = {item["id"]: item for item in contract["semantic_rules"]}
    conditions = rules["condition-expression"]
    references = rules["condition-output-reference"]
    unicode_condition = "$café.output.status == 'ready'"

    expression_pattern = conditions["parameters"]["expression_pattern"]
    assert conditions["parameters"]["expression_flags"] == "u"
    assert r"\w" not in expression_pattern
    assert r"\"" not in expression_pattern
    assert r"[\p{L}\p{N}_.:-]+" in expression_pattern
    assert r"[\p{L}\p{N}_.-]+" in expression_pattern
    assert unicode_condition in conditions["examples"]

    reference_pattern = references["parameters"]["pattern"]
    assert references["parameters"]["pattern_flags"] == "u"
    assert r"\w" not in reference_pattern
    assert r"([\p{L}\p{N}_.:-]+)" in reference_pattern
    assert r"[\p{L}\p{N}_.-]+" in reference_pattern
    assert unicode_condition in references["examples"]


def test_archon_condition_contract_projects_runtime_bounds_and_typed_rules():
    """Catch backend authoring metadata drifting from the bounded v3 evaluator."""
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    condition = next(
        item
        for item in contract["semantic_rules"]
        if item["id"] == "condition-expression"
    )
    parameters = condition["parameters"]

    assert parameters["limits"] == {
        "max_utf8_bytes": language_schema.ARCHON_V3_CONDITION_MAX_BYTES,
        "max_tokens": language_schema.ARCHON_V3_CONDITION_MAX_TOKENS,
        "max_parser_call_depth": language_schema.ARCHON_V3_CONDITION_MAX_NESTING,
    }
    assert parameters["comparison_operators"] == list(
        language_schema.ARCHON_V3_CONDITION_COMPARISON_OPERATORS
    )
    assert parameters["logical_operators"] == list(
        language_schema.ARCHON_V3_CONDITION_LOGICAL_OPERATORS
    )
    assert parameters["precedence"] == [
        {
            "operators": ["&&"],
            "associativity": "left",
            "higher_than": ["||"],
        },
        {
            "operators": ["||"],
            "associativity": "left",
            "higher_than": [],
        },
    ]
    assert parameters["evaluation"] == {
        "order": "left_to_right",
        "short_circuit": True,
    }
    assert parameters["typed_operand_modes"] == {
        "quoted_equality": "exact_string_only",
        "unquoted_decimal_equality": "canonical_finite_number_only",
        "ordered_lhs": [
            "canonical_finite_number",
            "schemaless_whole_decimal_text",
        ],
        "ordered_rhs": ["unquoted_decimal", "quoted_decimal"],
        "structured_strings_coerce_to_number": False,
    }
    assert len(language_schema.canonical_contract_json(contract).encode()) <= (
        language_schema.CONTRACT_MAX_BYTES
        - language_schema.CONTRACT_RESERVED_GROWTH_BYTES
    )


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (
            "$prepare.output.status == 'ready' && $inspect.output.count >= 2",
            True,
        ),
        ('$prepare.output.status != "blocked"', True),
        ("$prepare.output.status ==", False),
        ("prepare.output.status == 'ready'", False),
        ("$prepare.output.status == 'ready' &&", False),
    ],
)
def test_condition_expression_descriptor_matches_the_real_loader(condition, expected):
    contract = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    rule = next(
        item
        for item in contract["semantic_rules"]
        if item["id"] == "condition-expression"
    )
    assert rule["parameters"]["expression_flags"] == "u"
    pattern = re.compile(language_schema.WHEN_EXPRESSION_PATTERN)
    document = {
        "name": "condition-contract",
        "description": "contract and loader agree",
        "nodes": [
            {"id": "prepare", "bash": "true"},
            {"id": "inspect", "bash": "true"},
            {
                "id": "target",
                "bash": "true",
                "depends_on": ["prepare", "inspect"],
                "when": condition,
            },
        ],
    }

    assert (pattern.fullmatch(condition) is not None) is expected
    schema_accepts, loader_accepts = _structural_outcomes(document)
    assert schema_accepts is True
    assert loader_accepts is expected


def test_loader_keeps_python_unicode_condition_identifiers():
    document = {
        "name": "unicode-condition",
        "description": "Unicode node and output identifiers remain loader-valid",
        "nodes": [
            {"id": "café", "bash": "true"},
            {
                "id": "target",
                "bash": "true",
                "depends_on": ["café"],
                "when": "$café.output.résumé == 'ready'",
            },
        ],
    }

    assert _structural_outcomes(document) == (True, True)


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_editor_descriptors_and_schema_annotations_share_inventory_metadata(profile):
    contract = workflow_authoring_contract(profile)
    node_descriptors = {item["id"]: item for item in contract["node_kinds"]}
    schema = contract["definition_schema"]

    for node_type, descriptor in node_descriptors.items():
        direct = {
            field["field_path"].removeprefix("nodes[]."): field
            for field in descriptor["fields"]
            if field["field_path"].count(".") == 1
        }
        variant = next(
            item
            for item in schema["properties"]["nodes"]["items"]["oneOf"]
            if node_type in item["required"]
        )
        for field_name, field in direct.items():
            assert field_name in variant["properties"]
            annotation = schema["properties"]["nodes"]["items"]["properties"][
                field_name
            ]
            definition = _descriptor_definition(contract, field)
            assert annotation["title"] == definition["label"]
            assert annotation["description"] == definition["description"]
            assert annotation["x-hermes-widget"] == definition["widget"]
            assert annotation["x-hermes-section"] == definition["section"]


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_editor_compatibility_and_documentation_are_complete(profile):
    contract = workflow_authoring_contract(profile)

    assert contract["documentation"]["topics"]
    assert contract["documentation"]["examples"]
    assert all(
        descriptor["status"] in {"supported", "deferred", "deprecated"}
        and descriptor["description"]
        for descriptor in contract["compatibility_codes"].values()
    )
    assert all(
        _descriptor_definition(contract, field)["description"]
        and _descriptor_definition(contract, field)["examples"]
        for node_kind in contract["node_kinds"]
        for field in node_kind["fields"]
    )

    prompt = next(item for item in contract["node_kinds"] if item["id"] == "prompt")
    output_format = next(
        field
        for field in prompt["fields"]
        if field["field_path"] == "nodes[].output_format"
    )
    assert _descriptor_definition(contract, output_format)["widget"] == "json-schema"


def test_nested_retry_descriptors_follow_the_supported_archon_v3_parent():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    command = next(item for item in contract["node_kinds"] if item["id"] == "command")
    retry_fields = [
        field
        for field in command["fields"]
        if field["field_path"].startswith("nodes[].retry.")
    ]

    assert retry_fields
    assert {field["status"] for field in retry_fields} == {"supported"}


def test_nested_descriptor_keeps_supported_child_under_warning_parent():
    profile = WorkflowLanguageProfile.HERMES_LEGACY
    parent = next(
        spec
        for spec in FIELD_INVENTORY
        if spec.scope == "node" and spec.yaml_name == "retry"
    )
    child = next(spec for spec in FIELD_INVENTORY if spec.scope == "retry")
    warning_parent = replace(
        parent,
        compatibility=tuple(
            replace(item, status="warning", code="test_warning_parent")
            if item.profile is profile
            else item
            for item in parent.compatibility
        ),
    )

    descriptor = language_schema._field_descriptor(
        child,
        profile,
        "command",
        f"nodes[].retry.{child.yaml_name}",
        parent_spec=warning_parent,
    )

    assert descriptor["status"] == "supported"


def test_editor_status_distinguishes_legacy_advisories_from_archon_v3_support():
    legacy = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    archon = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)

    def command_field(contract, field_path):
        command = next(
            item for item in contract["node_kinds"] if item["id"] == "command"
        )
        return next(
            field for field in command["fields"] if field["field_path"] == field_path
        )

    assert command_field(legacy, "nodes[].idle_timeout")["status"] == "supported"
    assert command_field(archon, "nodes[].idle_timeout")["status"] == "supported"

    legacy_code = legacy["compatibility_codes"]["legacy_idle_timeout_seconds"]
    assert (
        legacy_code["status"],
        legacy_code["runtime_status"],
        legacy_code["blocking"],
    ) == (
        "supported",
        "warning",
        False,
    )
    assert (
        "archon_idle_timeout_semantics_unavailable" not in archon["compatibility_codes"]
    )


def test_schema_publishes_loader_defaults_and_identifier_pattern():
    schema = definition_json_schema(WorkflowLanguageProfile.HERMES_LEGACY)
    node = schema["properties"]["nodes"]["items"]["properties"]

    assert node["depends_on"]["default"] == []
    assert node["trigger_rule"]["default"] == "all_success"
    assert node["id"]["pattern"] == r"^[^\s/\\]+$"


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_every_published_schema_example_validates_against_its_field(profile):
    contract = workflow_authoring_contract(profile)

    def validate_examples(root):
        validator = Draft202012Validator(root)

        def visit(value):
            if isinstance(value, dict):
                for example in value.get("examples", []):
                    validator.evolve(schema=value).validate(example)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(root)

    validate_examples(contract["definition_schema"])
    validate_examples(contract["sidecar_schema"])


def test_parser_field_sets_come_from_authoring_inventory():
    assert TOP_LEVEL_FIELDS == definition_field_names()
    assert COMMON_NODE_FIELDS == common_node_field_names()
    assert SIDECAR_FIELDS == sidecar_field_names()


def test_field_inventory_is_immutable():
    assert isinstance(FIELD_INVENTORY, tuple)
    with pytest.raises(FrozenInstanceError):
        FIELD_INVENTORY[0].yaml_name = "changed"
    structured_example = next(
        spec.examples[0]
        for spec in FIELD_INVENTORY
        if isinstance(spec.examples[0], Mapping)
    )
    with pytest.raises(TypeError):
        structured_example["changed"] = True


def test_node_payload_inventory_declares_truthful_json_types():
    payloads = {
        spec.yaml_name: spec
        for spec in FIELD_INVENTORY
        if spec.scope == "node"
        and spec.yaml_name
        in {
            "command",
            "prompt",
            "bash",
            "script",
            "loop",
            "approval",
            "cancel",
        }
    }
    assert {name: spec.json_type for name, spec in payloads.items()} == {
        "command": "string",
        "prompt": "string",
        "bash": "string",
        "script": "string",
        "loop": "object",
        "approval": "object",
        "cancel": "string",
    }


def test_structural_requirements_are_inventory_metadata():
    required = {
        (spec.scope, spec.yaml_name, tuple(sorted(spec.required_node_types)))
        for spec in FIELD_INVENTORY
        if spec.required or spec.required_node_types
    }

    assert ("definition", "nodes", ()) in required
    assert ("node", "runtime", ("script",)) in required
    assert ("loop", "gate_message", ()) not in required
    assert ("hook_entry", "response", ()) in required
    assert ("hook_specific", "hookEventName", ()) in required


def _structural_outcomes(
    document: dict[str, object],
    profile: WorkflowLanguageProfile = WorkflowLanguageProfile.HERMES_LEGACY,
    *,
    normalizer_version: int | None = None,
    path: str | Path = "structural-parity.yaml",
) -> tuple[bool, bool]:
    schema = definition_json_schema(
        profile,
        normalizer_version=normalizer_version,
    )
    schema_valid = not list(Draft202012Validator(schema).iter_errors(document))
    try:
        load_workflow_snapshot(
            path,
            workflow_bytes=yaml.safe_dump(document, sort_keys=False).encode(),
            sidecar_bytes=(
                f"language_compatibility: {profile.value}\n".encode()
                if profile is WorkflowLanguageProfile.ARCHON_2026_07
                else None
            ),
            normalizer_version=normalizer_version,
        )
    except WorkflowValidationError:
        loader_valid = False
    else:
        loader_valid = True
    return schema_valid, loader_valid


def _workflow(node: dict[str, object]) -> dict[str, object]:
    return {
        "name": "structural-parity",
        "description": "schema and loader agree",
        "nodes": [node],
    }


@pytest.mark.parametrize(
    ("node_type", "normalizer_version", "nested", "expected"),
    [
        pytest.param("command", 6, False, True, id="v6-command-opt-out"),
        pytest.param("prompt", 6, False, True, id="v6-prompt-opt-out"),
        pytest.param("bash", 6, False, False, id="v6-bash-still-requires-retry"),
        pytest.param("script", 6, False, False, id="v6-script-still-requires-retry"),
        pytest.param("command", 5, False, False, id="v5-command-unchanged"),
        pytest.param("prompt", 5, False, False, id="v5-prompt-unchanged"),
        pytest.param("command", 6, True, True, id="v6-body-command-opt-out"),
        pytest.param("prompt", 6, True, True, id="v6-body-prompt-opt-out"),
    ],
)
def test_v6_ai_retry_opt_out_has_schema_loader_version_and_node_parity(
    tmp_path, node_type, normalizer_version, nested, expected
):
    child = {
        "id": "child" if nested else "n",
        node_type: "print('ok')" if node_type == "script" else "run",
        "retry": {"max_attempts": 0},
    }
    if node_type == "script":
        child["runtime"] = "uv"
    if node_type == "command":
        commands = tmp_path / "commands"
        commands.mkdir()
        (commands / "run.md").write_text("fixture command\n", encoding="utf-8")
    document = (
        _workflow({
            "id": "group",
            "loop_group": {
                "nodes": [child],
                "until": "done",
                "max_iterations": 2,
            },
        })
        if nested
        else _workflow(child)
    )

    assert _structural_outcomes(
        document,
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=normalizer_version,
        path=tmp_path / "structural-parity.yaml",
    ) == (expected, expected)


_NODE_FIELD_VALUES = {
    "id": "n",
    "command": "build",
    "prompt": "do it",
    "bash": "true",
    "script": "print('ok')",
    "loop": {"prompt": "again", "until": "done", "max_iterations": 2},
    "loop_group": {
        "nodes": [{"id": "child", "command": "run"}],
        "until": "done",
        "max_iterations": 2,
    },
    "approval": {"message": "continue?"},
    "cancel": "stop",
    "depends_on": [],
    "when": "$source.output == 'ok'",
    "trigger_rule": "all_done",
    "context": "fresh",
    "idle_timeout": 1,
    "retry": {"max_attempts": 2, "delay_ms": 1000, "on_error": "all"},
    "always_run": True,
    "output_type": "text",
    "persist_session": True,
    "provider": "claude",
    "model": "sonnet",
    "output_format": {"type": "object"},
    "allowed_tools": ["Read"],
    "denied_tools": ["Bash"],
    "hooks": {},
    "mcp": "mcp/server.yaml",
    "skills": ["review"],
    "agents": {"reviewer": {"description": "review", "prompt": "check"}},
    "effort": "high",
    "thinking": "adaptive",
    "maxBudgetUsd": 1,
    "maxTurns": 2,
    "tool_call_contract": {
        "name": "fetch_items",
        "arguments": {"max_results": 25},
        "result": {
            "items_path": "items",
            "select": ["key"],
            "output_items_path": "tickets",
            "output_count_path": "count",
            "output_status_path": "status",
            "empty_status": "empty",
            "nonempty_status": "ready",
            "max_items": 25,
        },
    },
    "artifacts": False,
    "systemPrompt": "be careful",
    "fallbackModel": "fallback",
    "betas": ["feature"],
    "sandbox": {"enabled": True},
    "runtime": "uv",
    "deps": ["pydantic>=2"],
    "timeout": 1,
}
_NODE_FIELD_SPECS = {
    spec.yaml_name: spec for spec in FIELD_INVENTORY if spec.scope == "node"
}


def _node_with_field(node_type: str, field: str) -> dict[str, object]:
    payload = _NODE_FIELD_VALUES[node_type]
    node = {"id": "n", node_type: payload}
    if node_type == "script":
        node["runtime"] = "uv"
    node[field] = _NODE_FIELD_VALUES[field]
    if field == "when":
        node["depends_on"] = ["source"]
    return node


def _document_with_field(node_type: str, field: str) -> dict[str, object]:
    node = _node_with_field(node_type, field)
    nodes: list[dict[str, object]] = []
    if field == "when":
        nodes.append({"id": "source", "bash": "true"})
    nodes.append(node)
    return {
        "name": "structural-parity",
        "description": "schema and loader agree",
        "nodes": nodes,
    }


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
@pytest.mark.parametrize("node_type", NODE_TYPES)
@pytest.mark.parametrize("field", tuple(_NODE_FIELD_SPECS))
def test_every_node_field_has_schema_loader_and_compatibility_parity(
    profile, node_type, field
):
    document = _document_with_field(node_type, field)
    normalizer_version = 3 if profile is WorkflowLanguageProfile.ARCHON_2026_07 else 2
    schema_accepts, loader_accepts = _structural_outcomes(
        document,
        profile,
        normalizer_version=normalizer_version,
    )

    assert schema_accepts is loader_accepts
    if not loader_accepts:
        return

    package = load_workflow_snapshot(
        "structural-parity.yaml",
        workflow_bytes=yaml.safe_dump(document, sort_keys=False).encode(),
        sidecar_bytes=(
            f"language_compatibility: {profile.value}\n".encode()
            if profile is WorkflowLanguageProfile.ARCHON_2026_07
            else None
        ),
        normalizer_version=normalizer_version,
    )
    path = f"nodes[{len(document['nodes']) - 1}].{field}"
    field_not_applicable = any(
        finding.code == "field_not_applicable" and finding.path == path
        for finding in assess_compatibility(package).findings
    )
    expected_not_applicable = (
        field in package.definition.nodes[-1].options
        and node_type not in _NODE_FIELD_SPECS[field].applicable_node_types
    )

    assert field_not_applicable is expected_not_applicable


def test_node_field_structural_and_compatibility_sets_are_distinct_and_exact():
    payload_fields = set(NODE_TYPES)
    ai_fields = {
        "persist_session",
        "provider",
        "model",
        "allowed_tools",
        "denied_tools",
        "hooks",
        "mcp",
        "skills",
        "agents",
        "effort",
        "thinking",
        "maxBudgetUsd",
        "systemPrompt",
        "fallbackModel",
        "betas",
        "sandbox",
    }

    for field in payload_fields:
        assert _NODE_FIELD_SPECS[field].structural_node_types == {field}
    assert _NODE_FIELD_SPECS["timeout"].structural_node_types == {
        "bash",
        "script",
    }
    assert _NODE_FIELD_SPECS["retry"].structural_node_types == set(NODE_TYPES) - {
        "loop"
    }
    for field in ai_fields:
        spec = _NODE_FIELD_SPECS[field]
        assert spec.structural_node_types == set(NODE_TYPES)
        assert spec.applicable_node_types == {"command", "prompt"}
    assert _NODE_FIELD_SPECS["output_format"].structural_node_types == set(
        NODE_TYPES
    )
    assert _NODE_FIELD_SPECS["output_format"].applicable_node_types == {
        "command",
        "prompt",
        "bash",
        "script",
    }


def test_compatibility_applicability_consumes_the_live_field_inventory(monkeypatch):
    revised_inventory = tuple(
        replace(
            spec,
            applicable_node_types=spec.applicable_node_types - {"bash"},
        )
        if spec.scope == "node" and spec.yaml_name == "always_run"
        else spec
        for spec in FIELD_INVENTORY
    )
    monkeypatch.setattr(language_schema, "FIELD_INVENTORY", revised_inventory)
    package = load_workflow_snapshot(
        "inventory-authority.yaml",
        workflow_bytes=yaml.safe_dump(
            _workflow({"id": "n", "bash": "true", "always_run": True}),
            sort_keys=False,
        ).encode(),
        sidecar_bytes=None,
    )

    findings = assess_compatibility(package).findings

    assert any(
        finding.code == "field_not_applicable" and finding.path == "nodes[0].always_run"
        for finding in findings
    )


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param({"id": "n", "command": "build"}, True, id="command-valid"),
        pytest.param({"id": "n", "command": 1}, False, id="command-invalid"),
        pytest.param({"id": "n", "prompt": "do it"}, True, id="prompt-valid"),
        pytest.param({"id": "n", "prompt": []}, False, id="prompt-invalid"),
        pytest.param({"id": "n", "bash": "true"}, True, id="bash-valid"),
        pytest.param({"id": "n", "bash": {}}, False, id="bash-invalid"),
        pytest.param(
            {"id": "n", "script": "print('ok')", "runtime": "bun"},
            True,
            id="script-valid",
        ),
        pytest.param(
            {"id": "n", "script": "print('no runtime')"},
            False,
            id="script-runtime-required",
        ),
        pytest.param(
            {
                "id": "n",
                "loop": {"prompt": "again", "until": "done", "max_iterations": 2},
            },
            True,
            id="loop-valid",
        ),
        pytest.param(
            {"id": "n", "loop": {"prompt": "again", "max_iterations": 2}},
            False,
            id="loop-invalid",
        ),
        pytest.param(
            {"id": "n", "approval": {"message": "continue?"}},
            True,
            id="approval-valid",
        ),
        pytest.param(
            {"id": "n", "approval": {"capture_response": True}},
            False,
            id="approval-invalid",
        ),
        pytest.param({"id": "n", "cancel": "stop"}, True, id="cancel-valid"),
        pytest.param({"id": "n", "cancel": None}, False, id="cancel-invalid"),
    ],
)
def test_every_node_kind_has_schema_loader_structural_parity(node, expected):
    assert _structural_outcomes(_workflow(node)) == (expected, expected)


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(
            {
                "id": "n",
                "loop": {
                    "prompt": "again",
                    "until": "done",
                    "max_iterations": 2,
                    "interactive": True,
                    "gate_message": "approve next iteration",
                },
            },
            True,
            id="interactive-loop-valid",
        ),
        pytest.param(
            {
                "id": "n",
                "loop": {
                    "prompt": "again",
                    "until": "done",
                    "max_iterations": 2,
                    "interactive": True,
                },
            },
            False,
            id="interactive-loop-requires-gate-message",
        ),
        pytest.param(
            {
                "id": "n",
                "bash": "false",
                "retry": {"max_attempts": 2, "delay_ms": 1000, "on_error": "all"},
            },
            True,
            id="retry-valid",
        ),
        pytest.param(
            {"id": "n", "bash": "false", "retry": {"max_attempts": 0}},
            False,
            id="retry-invalid",
        ),
        pytest.param(
            {
                "id": "n",
                "approval": {
                    "message": "continue?",
                    "capture_response": {"loader": "accepts-untyped-value"},
                    "on_reject": {"prompt": "try again", "max_attempts": 2},
                },
            },
            True,
            id="approval-reject-valid",
        ),
        pytest.param(
            {
                "id": "n",
                "approval": {"message": "continue?", "on_reject": {"max_attempts": 2}},
            },
            False,
            id="approval-reject-invalid",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "delegate",
                "agents": {"reviewer": {"description": "review", "prompt": "check"}},
            },
            True,
            id="agent-valid",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "delegate",
                "agents": {"reviewer": {"description": "review"}},
            },
            False,
            id="agent-invalid",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "hook",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": 7,
                            "response": {
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecision": "allow",
                                }
                            },
                        }
                    ]
                },
            },
            True,
            id="hook-valid-integer-matcher",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "hook",
                "hooks": {"PreToolUse": [{"response": {"hookSpecificOutput": None}}]},
            },
            True,
            id="hook-specific-output-allows-explicit-null",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "hook",
                "hooks": {
                    "PreToolUse": [
                        {
                            "response": {
                                "hookSpecificOutput": {"hookEventName": "PostToolUse"}
                            }
                        }
                    ]
                },
            },
            False,
            id="hook-event-mismatch",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "hook",
                "hooks": {
                    "PreToolUse": [
                        {"response": {"hookSpecificOutput": {"content": "missing"}}}
                    ]
                },
            },
            False,
            id="hook-event-name-required",
        ),
        pytest.param(
            {
                "id": "n",
                "prompt": "hook",
                "hooks": {"PreToolUse": [{"matcher": "tool"}]},
            },
            False,
            id="hook-response-required",
        ),
    ],
)
def test_nested_shapes_have_schema_loader_structural_parity(node, expected):
    assert _structural_outcomes(_workflow(node)) == (expected, expected)


@pytest.mark.parametrize(
    ("gate_message", "expected"),
    [
        pytest.param(1, True, id="truthy-number"),
        pytest.param(["next"], True, id="truthy-list"),
        pytest.param({"message": "next"}, True, id="truthy-object"),
        pytest.param(True, True, id="truthy-boolean"),
        pytest.param(None, False, id="falsey-null"),
        pytest.param("", False, id="falsey-string"),
        pytest.param([], False, id="falsey-list"),
        pytest.param({}, False, id="falsey-object"),
        pytest.param(False, False, id="falsey-boolean"),
        pytest.param(0, False, id="falsey-number"),
    ],
)
def test_interactive_gate_message_json_truthiness_matches_loader(
    gate_message, expected
):
    document = _workflow({
        "id": "n",
        "loop": {
            "prompt": "again",
            "until": "done",
            "max_iterations": 2,
            "interactive": True,
            "gate_message": gate_message,
        },
    })

    assert _structural_outcomes(document) == (expected, expected)


@pytest.mark.parametrize(
    ("loop_options", "expected"),
    [
        pytest.param(
            {"interactive": True, "gate_message": "Confirm"},
            True,
            id="interactive-valid",
        ),
        pytest.param(
            {"interactive": False},
            True,
            id="noninteractive-valid",
        ),
        pytest.param(
            {"interactive": "yes", "gate_message": "Confirm"},
            False,
            id="interactive-must-be-boolean",
        ),
        pytest.param(
            {"interactive": True},
            False,
            id="interactive-requires-gate",
        ),
        pytest.param(
            {"interactive": True, "gate_message": "   "},
            False,
            id="gate-must-be-nonblank",
        ),
        pytest.param(
            {"interactive": False, "gate_message": 1},
            False,
            id="authored-gate-must-be-string",
        ),
        pytest.param(
            {"signal_completes": "yes"},
            False,
            id="signal-completes-must-be-boolean",
        ),
    ],
)
def test_explicit_v4_loop_schema_matches_admission_validation(loop_options, expected):
    document = _workflow({
        "id": "n",
        "loop": {
            "prompt": "again",
            "until": "done",
            "max_iterations": 2,
            **loop_options,
        },
    })

    assert _structural_outcomes(
        document,
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    ) == (expected, expected)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        pytest.param("prompt", "   ", False, id="prompt-whitespace"),
        pytest.param("prompt", "Refine", True, id="prompt-valid"),
        pytest.param("command", "   ", False, id="command-whitespace"),
        pytest.param("command", "refine", True, id="command-valid"),
        pytest.param("until", "   ", False, id="until-whitespace"),
        pytest.param("until", "DONE", True, id="until-valid"),
    ],
)
def test_explicit_v4_loop_text_schema_matches_admission(
    tmp_path,
    field,
    value,
    expected,
):
    loop = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
    }
    if field == "command":
        loop.pop("prompt")
    loop[field] = value
    document = _workflow({"id": "n", "loop": loop})
    schema = definition_json_schema(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=4,
    )
    schema_valid = not list(Draft202012Validator(schema).iter_errors(document))
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text("Refine safely.\n", encoding="utf-8")

    try:
        load_workflow_snapshot(
            tmp_path / "workflow.yaml",
            workflow_bytes=yaml.safe_dump(document, sort_keys=False).encode(),
            sidecar_bytes=b"language_compatibility: archon-2026-07\n",
            normalizer_version=4,
        )
    except WorkflowValidationError:
        loader_valid = False
    else:
        loader_valid = True

    assert (schema_valid, loader_valid) == (expected, expected)


@pytest.mark.parametrize(
    ("node_type", "field", "code", "phase"),
    [
        ("prompt", "maxBudgetUsd", "archon_budget_enforcement_unavailable", 5),
        ("prompt", "sandbox", "archon_sandbox_enforcement_unavailable", 5),
    ],
)
def test_archon_deferred_fields_publish_blocking_codes(node_type, field, code, phase):
    schema = definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)

    field_schema = _node_property(schema, node_type, field)
    assert field_schema["x-hermes-status"] == "blocking"
    assert field_schema["x-hermes-compatibility-code"] == code
    assert field_schema["x-hermes-enforcement-phase"] == phase
    assert schema["additionalProperties"] is False
    assert schema["properties"]["nodes"]["items"]["additionalProperties"] is False


@pytest.mark.parametrize("field", ("output_format", "output_type"))
def test_archon_structured_output_fields_publish_supported_contracts(field):
    schema = definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)
    field_schema = _node_property(schema, "prompt", field)
    codes = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    assert field_schema["x-hermes-status"] == "supported"
    assert "x-hermes-compatibility-code" not in field_schema
    assert "archon_output_format_unavailable" not in codes
    assert "archon_output_type_unavailable" not in codes


def test_output_type_schema_publishes_the_direct_durable_metadata_boundary():
    schema = definition_json_schema(WorkflowLanguageProfile.ARCHON_2026_07)
    field_schema = _node_property(schema, "prompt", "output_type")
    prefix = "MixedCase/分析/"
    boundary = prefix + ("Ω" * (16_384 - len(prefix)))

    assert field_schema["minLength"] == 1
    assert field_schema["maxLength"] == 16_384
    assert field_schema["pattern"] == r"\S"
    Draft202012Validator(schema).validate(
        _workflow({"id": "producer", "prompt": "produce", "output_type": boundary})
    )

    for invalid in (boundary + "x", "", " \t "):
        errors = Draft202012Validator(schema).iter_errors(
            _workflow({
                "id": "producer",
                "prompt": "produce",
                "output_type": invalid,
            })
        )
        assert any(
            list(error.absolute_path) == ["nodes", 0, "output_type"] for error in errors
        )


@pytest.mark.parametrize(
    ("node_type", "field_path", "code"),
    [
        (
            "bash",
            ("idle_timeout",),
            "legacy_idle_timeout_seconds",
        ),
        ("bash", ("timeout",), "legacy_timeout_seconds"),
        (
            "bash",
            ("retry", "properties", "max_attempts"),
            "legacy_retry_total_attempts",
        ),
        (
            "prompt",
            ("output_format",),
            "legacy_output_format_post_validation",
        ),
        ("prompt", ("output_type",), "legacy_output_type_not_published"),
    ],
)
def test_legacy_deferred_fields_publish_warning_codes(node_type, field_path, code):
    schema = definition_json_schema(WorkflowLanguageProfile.HERMES_LEGACY)

    field_schema = _node_property(schema, node_type, field_path[0])
    for segment in field_path[1:]:
        field_schema = field_schema[segment]
    assert field_schema["x-hermes-status"] == "warning"
    assert field_schema["x-hermes-compatibility-code"] == code
    assert schema["additionalProperties"] is True


def test_companion_schema_has_strict_profile_enum_and_field_parity():
    for profile in WorkflowLanguageProfile:
        schema = sidecar_json_schema(profile)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        assert frozenset(schema["properties"]) == SIDECAR_FIELDS
        assert schema["properties"]["language_compatibility"]["enum"] == [
            "hermes-legacy",
            "archon-2026-07",
        ]


@pytest.mark.parametrize(
    ("profile", "profile_specific_codes"),
    [
        (
            WorkflowLanguageProfile.HERMES_LEGACY,
            {
                "legacy_language_profile",
                "legacy_idle_timeout_seconds",
                "unknown_top_level_field",
            },
        ),
        (
            WorkflowLanguageProfile.ARCHON_2026_07,
            {
                "archon_idle_timeout_node_unsupported",
                "archon_retry_node_unsupported",
                "archon_unknown_top_level_field",
            },
        ),
    ],
)
def test_compatibility_code_catalog_covers_inventory_and_dynamic_loader_codes(
    profile, profile_specific_codes
):
    catalog = compatibility_code_catalog(profile)
    inventory_codes = {
        item.code
        for spec in FIELD_INVENTORY
        for item in spec.compatibility
        if item.profile is profile and item.status != "supported" and item.code
    }

    assert inventory_codes <= set(catalog)
    assert profile_specific_codes <= set(catalog)
    assert {
        "workflow_language_profile_unsupported",
        "workflow_normalizer_version_unsupported",
    } <= set(catalog)
    assert len(json.dumps(catalog, sort_keys=True).encode()) < 32_000


def test_dynamic_catalog_codes_are_emitted_by_real_runtime_paths():
    workflow_bytes = yaml.safe_dump(
        {
            "name": "runtime-codes",
            "description": "exercise real dynamic language code paths",
            "nodes": [{"id": "n", "bash": "true"}],
        },
        sort_keys=False,
    ).encode()
    with pytest.raises(WorkflowValidationError) as unsupported_profile:
        load_workflow_snapshot(
            "unsupported-profile.yaml",
            workflow_bytes=workflow_bytes,
            sidecar_bytes=b"language_compatibility: future-profile\n",
        )
    with pytest.raises(
        workflow_language.WorkflowLanguageCompatibilityError
    ) as unsupported_normalizer:
        load_workflow_snapshot(
            "unsupported-normalizer.yaml",
            workflow_bytes=workflow_bytes,
            sidecar_bytes=None,
            normalizer_version=99,
        )

    legacy = load_workflow_snapshot(
        "legacy-unknown.yaml",
        workflow_bytes=workflow_bytes + b"future_field: true\n",
        sidecar_bytes=None,
    )
    with pytest.raises(WorkflowValidationError) as archon_unknown:
        load_workflow_snapshot(
            "archon-unknown.yaml",
            workflow_bytes=workflow_bytes + b"future_field: true\n",
            sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        )

    common_runtime_codes = {
        unsupported_profile.value.issues[0].code,
        unsupported_normalizer.value.code,
    }
    runtime_codes = {
        WorkflowLanguageProfile.HERMES_LEGACY: common_runtime_codes
        | {legacy.validation_issues[0].code},
        WorkflowLanguageProfile.ARCHON_2026_07: common_runtime_codes
        | {archon_unknown.value.issues[0].code},
    }
    expected_codes = {
        profile: {
            spec.code
            for spec in workflow_language.DYNAMIC_LANGUAGE_COMPATIBILITY_CODES
            if profile in spec.profiles
        }
        for profile in WorkflowLanguageProfile
    }
    assert runtime_codes == expected_codes
    for profile, emitted_codes in runtime_codes.items():
        assert emitted_codes <= set(compatibility_code_catalog(profile))


def test_generated_language_codes_are_derived_in_authoring_contracts():
    root = Path(__file__).parents[3]
    references = (
        root / "website/docs/user-guide/features/workflow-yaml-reference.md",
        root
        / "skills/software-development/workflow-builder/references/portable-schema.md",
    )

    for profile in WorkflowLanguageProfile:
        contract = workflow_authoring_contract(profile)
        topic = next(
            item
            for item in contract["documentation"]["topics"]
            if item["id"] == "stable-codes"
        )
        assert topic["code_source"] == "compatibility_codes"
        assert "codes" not in topic

    for reference in references:
        documented = reference.read_text(encoding="utf-8")
        assert "compatibility_codes" in documented
        assert "second exhaustive code list" in documented or reference.name == (
            "portable-schema.md"
        )


def test_authoring_contract_excludes_secret_values_and_runtime_data(
    monkeypatch, tmp_path
):
    secret = "schema-must-not-read-this-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.chdir(tmp_path)

    encoded = json.dumps(
        workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07),
        sort_keys=True,
    )

    assert secret not in encoded
    assert str(tmp_path) not in encoded
    assert "run_id" not in encoded
    assert "semantic_fingerprint" not in encoded


def test_language_schema_dependency_direction_stays_neutral():
    source = (
        Path(__file__).parents[3] / "plugins" / "workflow" / "language_schema.py"
    ).read_text(encoding="utf-8")
    imported_modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "hashlib",
        "json",
        "types",
        "typing",
        "plugins.workflow.language",
        "plugins.workflow.models",
    }
    assert "plugins.workflow.schema" not in imported_modules
