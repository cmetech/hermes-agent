from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from plugins.workflow.language_schema import (
    FIELD_INVENTORY,
    common_node_field_names,
    definition_field_names,
    definition_json_schema,
    sidecar_field_names,
    sidecar_json_schema,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.schema import COMMON_NODE_FIELDS, SIDECAR_FIELDS, TOP_LEVEL_FIELDS


def _node_property(schema: dict[str, object], node_type: str, field: str) -> dict:
    variants = schema["properties"]["nodes"]["items"]["oneOf"]
    variant = next(item for item in variants if node_type in item["required"])
    return variant["properties"][field]


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


def test_parser_field_sets_come_from_authoring_inventory():
    assert TOP_LEVEL_FIELDS == definition_field_names()
    assert COMMON_NODE_FIELDS == common_node_field_names()
    assert SIDECAR_FIELDS == sidecar_field_names()


def test_field_inventory_is_immutable():
    assert isinstance(FIELD_INVENTORY, tuple)
    with pytest.raises(FrozenInstanceError):
        FIELD_INVENTORY[0].yaml_name = "changed"


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


@pytest.mark.parametrize(
    ("node_type", "field", "code", "phase"),
    [
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
        "dataclasses",
        "typing",
        "plugins.workflow.language",
        "plugins.workflow.models",
    }
    assert "plugins.workflow.schema" not in imported_modules
