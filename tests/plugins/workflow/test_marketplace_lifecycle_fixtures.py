"""Differential corpus uses real service projections, never preview payloads."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from plugins.workflow.marketplace.lifecycle_models import (
    LifecycleOperation,
    PackageState,
)
from plugins.workflow.marketplace.operations import (
    AdmissionEvicted,
    AdmissionFound,
    LifecycleOperationPage,
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
