from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path
import re

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


def test_archon_authoring_contract_is_bounded_and_versioned():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)

    assert contract["schema_version"] == 1
    assert contract["profile"] == "archon-2026-07"
    assert contract["normalizer_version"] == 1
    assert (
        contract["definition_schema"]["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
    )
    assert len(json.dumps(contract).encode()) < 256_000


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
    digest_payload = {key: value for key, value in contract.items() if key != "contract_digest"}
    expected_digest = sha256(
        language_schema.canonical_contract_json(digest_payload).encode()
    ).hexdigest()

    assert contract["contract_reader_version"] == 1
    assert contract["contract_digest"] == f"sha256:{expected_digest}"
    assert contract["limits"] == {"max_document_bytes": 2 * 1024 * 1024}
    assert contract["x-hermes-provenance"]["field_authority"] == (
        "plugins.workflow.language_schema.FIELD_INVENTORY"
    )
    assert contract["x-hermes-filenames"]["companion_suffix"] == (
        ".hermes.yaml"
    )


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
        '{"é":"line one\\nline \\"two\\"","😀":"snowman ☃",'
        '"\ue000":"private-use"}'
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_contract_canonical_json_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="finite"):
        language_schema.canonical_contract_json({"value": value})


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_node_kind_descriptors_cover_each_applicable_node_field_once(profile):
    contract = workflow_authoring_contract(profile)
    descriptors = {item["id"]: item for item in contract["node_kinds"]}

    assert set(descriptors) == set(NODE_TYPES)
    for node_type, descriptor in descriptors.items():
        assert descriptor["field_path"] == f"nodes[].{node_type}"
        assert descriptor["status"] == "supported"
        fields = descriptor["fields"]
        paths = [field["field_path"] for field in fields]
        orders = [field["order"] for field in fields]
        expected_direct_paths = {
            f"nodes[].{spec.yaml_name}"
            for spec in FIELD_INVENTORY
            if spec.scope == "node" and node_type in spec.applicable_node_types
        }

        assert expected_direct_paths <= set(paths)
        assert len(paths) == len(set(paths))
        assert len(orders) == len(set(orders))
        assert all(field["description"] for field in fields)
        assert all(field["applicability"]["node_kinds"] == [node_type] for field in fields)

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


def test_condition_contract_publishes_ecmascript_unicode_grammar():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    rules = {item["id"]: item for item in contract["semantic_rules"]}
    conditions = rules["condition-expression"]
    references = rules["condition-output-reference"]
    unicode_condition = "$café.output.status == 'ready'"

    expression_pattern = conditions["parameters"]["expression_pattern"]
    assert conditions["parameters"]["expression_flags"] == "u"
    assert r"\w" not in expression_pattern
    assert r'\"' not in expression_pattern
    assert r"[\p{L}\p{N}_.:-]+" in expression_pattern
    assert r"[\p{L}\p{N}_.-]+" in expression_pattern
    assert unicode_condition in conditions["examples"]

    reference_pattern = references["parameters"]["pattern"]
    assert references["parameters"]["pattern_flags"] == "u"
    assert r"\w" not in reference_pattern
    assert r"([\p{L}\p{N}_.:-]+)" in reference_pattern
    assert r"[\p{L}\p{N}_.-]+" in reference_pattern
    assert unicode_condition in references["examples"]


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (
            "$prepare.output.status == 'ready' && $inspect.output.count >= 2",
            True,
        ),
        ("$prepare.output.status != \"blocked\"", True),
        ("$prepare.output.status ==", False),
        ("prepare.output.status == 'ready'", False),
        ("$prepare.output.status == 'ready' &&", False),
    ],
)
def test_condition_expression_descriptor_matches_the_real_loader(condition, expected):
    contract = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    rule = next(
        item for item in contract["semantic_rules"] if item["id"] == "condition-expression"
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
            annotation = schema["properties"]["nodes"]["items"]["properties"][field_name]
            assert annotation["title"] == field["label"]
            assert annotation["description"] == field["description"]
            assert annotation["x-hermes-widget"] == field["widget"]
            assert annotation["x-hermes-section"] == field["section"]


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
        field["description"] and field["examples"]
        for node_kind in contract["node_kinds"]
        for field in node_kind["fields"]
    )

    prompt = next(item for item in contract["node_kinds"] if item["id"] == "prompt")
    output_format = next(
        field
        for field in prompt["fields"]
        if field["field_path"] == "nodes[].output_format"
    )
    assert output_format["widget"] == "json-schema"


def test_nested_descriptors_never_upgrade_a_deferred_parent_field():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    command = next(item for item in contract["node_kinds"] if item["id"] == "command")
    retry_fields = [
        field
        for field in command["fields"]
        if field["field_path"].startswith("nodes[].retry.")
    ]

    assert retry_fields
    assert {field["status"] for field in retry_fields} == {"deferred"}


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


def test_editor_status_distinguishes_legacy_advisories_from_archon_blockers():
    legacy = workflow_authoring_contract(WorkflowLanguageProfile.HERMES_LEGACY)
    archon = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)

    def command_field(contract, field_path):
        command = next(item for item in contract["node_kinds"] if item["id"] == "command")
        return next(field for field in command["fields"] if field["field_path"] == field_path)

    assert command_field(legacy, "nodes[].idle_timeout")["status"] == "supported"
    assert command_field(archon, "nodes[].idle_timeout")["status"] == "deferred"

    legacy_code = legacy["compatibility_codes"]["legacy_idle_timeout_seconds"]
    archon_code = archon["compatibility_codes"][
        "archon_idle_timeout_semantics_unavailable"
    ]
    assert (legacy_code["status"], legacy_code["runtime_status"], legacy_code["blocking"]) == (
        "supported",
        "warning",
        False,
    )
    assert (archon_code["status"], archon_code["runtime_status"], archon_code["blocking"]) == (
        "deferred",
        "blocking",
        True,
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

    def validate_examples(value):
        if isinstance(value, dict):
            for example in value.get("examples", []):
                Draft202012Validator(value).validate(example)
            for child in value.values():
                validate_examples(child)
        elif isinstance(value, list):
            for child in value:
                validate_examples(child)

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
) -> tuple[bool, bool]:
    schema = definition_json_schema(profile)
    schema_valid = not list(Draft202012Validator(schema).iter_errors(document))
    try:
        load_workflow_snapshot(
            "structural-parity.yaml",
            workflow_bytes=yaml.safe_dump(document, sort_keys=False).encode(),
            sidecar_bytes=(
                f"language_compatibility: {profile.value}\n".encode()
                if profile is WorkflowLanguageProfile.ARCHON_2026_07
                else None
            ),
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


_NODE_FIELD_VALUES = {
    "id": "n",
    "command": "build",
    "prompt": "do it",
    "bash": "true",
    "script": "print('ok')",
    "loop": {"prompt": "again", "until": "done", "max_iterations": 2},
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
    schema_accepts, loader_accepts = _structural_outcomes(document, profile)

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
        "output_format",
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
        finding.code == "field_not_applicable"
        and finding.path == "nodes[0].always_run"
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
    ("node_type", "field", "code", "phase"),
    [
        (
            "bash",
            "idle_timeout",
            "archon_idle_timeout_semantics_unavailable",
            3,
        ),
        ("bash", "timeout", "archon_timeout_semantics_unavailable", 3),
        ("bash", "retry", "archon_retry_semantics_unavailable", 3),
        ("prompt", "output_format", "archon_output_format_unavailable", 2),
        ("prompt", "output_type", "archon_output_type_unavailable", 2),
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
                "archon_idle_timeout_semantics_unavailable",
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
    root = Path(__file__).parents[3]
    references = (
        root / "website/docs/user-guide/features/workflow-yaml-reference.md",
        root
        / "skills/software-development/workflow-builder/references/portable-schema.md",
    )

    assert runtime_codes == expected_codes
    for profile, emitted_codes in runtime_codes.items():
        assert emitted_codes <= set(compatibility_code_catalog(profile))
        for reference in references:
            documented = reference.read_text(encoding="utf-8")
            assert not {
                code for code in emitted_codes if f"`{code}`" not in documented
            }


def test_generated_language_codes_are_covered_by_authoring_references():
    generated_codes = set().union(
        *(compatibility_code_catalog(profile) for profile in WorkflowLanguageProfile)
    )
    root = Path(__file__).parents[3]
    references = (
        root / "website/docs/user-guide/features/workflow-yaml-reference.md",
        root
        / "skills/software-development/workflow-builder/references/portable-schema.md",
    )

    for reference in references:
        documented = reference.read_text(encoding="utf-8")
        assert not {code for code in generated_codes if f"`{code}`" not in documented}


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
