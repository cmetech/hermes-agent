"""Differential corpus uses real service projections, never preview payloads."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from plugins.workflow.marketplace.lifecycle_models import (
    LifecycleCapabilities,
    LifecycleOperation,
    PackageState,
)
from plugins.workflow.marketplace import lifecycle_models as wire
from plugins.workflow.marketplace.operations import (
    AdmissionEvicted,
    AdmissionFound,
    LifecycleOperationPage,
    ReviewTokenResponse,
)


ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tests/fixtures/workflow-marketplace-lifecycle-v2.json"


def test_generator_reproduces_actual_domain_projections():
    # Break caught: a generator that omits service execution or emits stale bytes.
    path = ROOT / "scripts/generate_workflow_marketplace_lifecycle_fixtures.py"
    assert path.exists(), "lifecycle fixture generator is missing"
    spec = importlib.util.spec_from_file_location("lifecycle_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = module.generate_corpus()
    assert generated == json.loads(CORPUS.read_text(encoding="utf-8"))
    assert generated["domains"]["lifecycle_relative_path"]["maxLength"] == 1024
    assert 0xFEFF not in generated["domains"]["clean_text"]["stripCodePoints"]
    assert generated["httpErrorCodes"] == sorted(wire.LIFECYCLE_HTTP_ERROR_CODES)
    assert generated["capabilities"]["capabilities"] == list(
        wire.LIFECYCLE_CAPABILITIES
    )
    assert any(
        case["accepted"] and "FEFF" in case["name"]
        for case in generated["operationCases"]
    )
    by_name = {case["name"]: case for case in generated["operationCases"]}
    selected = by_name["service grant one A"]["value"]["result"]["value"]
    assert {item["workflow_name"]: item["state"] for item in selected["workflows"]} == {
        "A": "trusted",
        "B": "untrusted",
    }
    rollback = by_name["service rollback failed"]["value"]
    assert rollback["state"] == "failed"
    assert rollback["outcome"] == {
        "type": "recovery_required",
        "reason": "rollback_failed",
    }
    ambiguous = by_name.get("service recovery ambiguous")
    assert ambiguous is not None, (
        "actual recovery-ambiguous service projection is missing"
    )
    assert ambiguous["value"]["state"] == "failed"
    assert ambiguous["value"]["result"] is None
    assert ambiguous["value"]["outcome"] == {
        "type": "recovery_required",
        "reason": "recovery_ambiguous",
    }
    ambiguous_state = next(
        case["value"]
        for case in generated["packageStateCases"]
        if case["name"] == "service ambiguous state"
    )
    assert ambiguous_state["state"] == "unconfirmed"
    assert ambiguous_state["installed"] is None and ambiguous_state["trust"] is None
    assert "private-recovery-location" not in json.dumps(generated)
    assert "confirmation_token" not in json.dumps(generated)
    found = AdmissionFound.model_validate_json(json.dumps(generated["admissionFound"]))
    evicted = AdmissionEvicted.model_validate_json(
        json.dumps(generated["admissionEvicted"])
    )
    first = LifecycleOperationPage.model_validate_json(
        json.dumps(generated["operationPage"])
    )
    last = LifecycleOperationPage.model_validate_json(
        json.dumps(generated["operationPageFinal"])
    )
    assert found.operation.id == evicted.operation_id
    assert not first.complete and first.next_cursor
    assert last.complete and last.next_cursor is None
    assert {item.id for item in first.items + last.items} == {
        found.operation.id,
        generated["legacyOperation"]["id"],
    }


def test_checked_in_cases_follow_python_validation():
    # Break caught: fixture labels or Python relationships drifting independently.
    assert CORPUS.exists(), "backend lifecycle fixture corpus is missing"
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    for collection, model in (
        ("operationCases", LifecycleOperation),
        ("packageStateCases", PackageState),
    ):
        for case in corpus[collection]:
            if case["accepted"]:
                model.model_validate_json(json.dumps(case["value"]))
            else:
                with pytest.raises(ValidationError, match=".*"):
                    model.model_validate_json(json.dumps(case["value"]))
    models = {
        model.__name__: model
        for model in (
            LifecycleCapabilities,
            AdmissionFound,
            AdmissionEvicted,
            LifecycleOperationPage,
        )
    }
    for case in corpus["outerEnvelopeCases"]:
        model = models[case["model"]]
        if case["accepted"]:
            model.model_validate_json(json.dumps(case["value"]))
        else:
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(case["value"]))
    for case in corpus["tokenEndpointCases"]:
        value = {
            **case["value"],
            "confirmation_token": case["character"] * case["length"],
        }
        if case["accepted"]:
            ReviewTokenResponse.model_validate_json(json.dumps(value))
        else:
            with pytest.raises(ValidationError):
                ReviewTokenResponse.model_validate_json(json.dumps(value))


def test_generator_observes_isolated_authority_rule_changes(monkeypatch):
    # Break caught: hard-coded domains or transport-code lists ignoring authority.
    spec = importlib.util.spec_from_file_location(
        "lifecycle_fixtures_rules",
        ROOT / "scripts/generate_workflow_marketplace_lifecycle_fixtures.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = module.generate_types()
    monkeypatch.setattr(
        wire,
        "LIFECYCLE_HTTP_ERROR_CODES",
        wire.LIFECYCLE_HTTP_ERROR_CODES - {"marketplace_list_expired"},
    )
    assert module.generate_types() != before

    before = module.generate_types()
    original = wire.lifecycle_relative_path

    def changed(value):
        if "\u2028" in value:
            return value
        return original(value)

    monkeypatch.setattr(wire, "lifecycle_relative_path", changed)
    assert module.generate_types() != before


def test_path_descriptor_does_not_generalize_contextual_drive_prefix():
    # Break caught: a:b being misclassified as evidence that every colon is invalid.
    spec = importlib.util.spec_from_file_location(
        "lifecycle_path_facts",
        ROOT / "scripts/generate_workflow_marketplace_lifecycle_fixtures.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        ord(":")
        not in module.generate_domains()["lifecycle_relative_path"][
            "forbiddenCodePoints"
        ]
    )
