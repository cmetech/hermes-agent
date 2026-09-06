"""Differential corpus uses real service projections, never preview payloads."""

import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest
from pydantic import ValidationError

from plugins.workflow.marketplace.lifecycle_models import (
    LifecycleCapabilities,
    LifecycleOperation,
    PackageState,
)
from plugins.workflow.marketplace import lifecycle_models as wire
from plugins.workflow.marketplace.operations import (
    MarketplaceOperation,
    AdmissionEvicted,
    AdmissionFound,
    LifecycleOperationPage,
    ReviewTokenResponse,
    WorkflowMarketplaceOperationRegistry,
)


ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tests/fixtures/workflow-marketplace-lifecycle-v2.json"
INSPECTION_V1 = ROOT / "tests/fixtures/workflow-marketplace-inspection-v1.json"


def test_generator_reproduces_actual_domain_projections(monkeypatch):
    # Break caught: a generator that omits service execution or emits stale bytes.
    legacy_observations = []
    get_legacy = WorkflowMarketplaceOperationRegistry.get_legacy

    def observe_legacy(registry, operation_id, *, actor):
        # Exercise the real admission-version gate; V2 starts cannot pass it.
        result = get_legacy(registry, operation_id, actor=actor)
        if result.kind == "package_detail" and result.state == "succeeded":
            legacy_observations.append(result.model_dump(mode="json", by_alias=False))
        return result

    monkeypatch.setattr(
        WorkflowMarketplaceOperationRegistry, "get_legacy", observe_legacy
    )
    path = ROOT / "scripts/generate_workflow_marketplace_lifecycle_fixtures.py"
    assert path.exists(), "lifecycle fixture generator is missing"
    spec = importlib.util.spec_from_file_location("lifecycle_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated, inspection_v1 = module.generate_corpora()
    assert generated == json.loads(CORPUS.read_text(encoding="utf-8"))
    assert module.render(inspection_v1) == INSPECTION_V1.read_text(encoding="utf-8")
    v1_operation = inspection_v1["operationCases"][0]["value"]
    assert legacy_observations == [v1_operation]
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
    assert (
        v1_operation["result"]["value"]
        == by_name["service inspect"]["value"]["result"]["value"]
    )
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


def test_v1_inspection_preserves_distribution_and_effective_workflow_digests():
    # Break caught: conflating distribution identity with workflow trust identity.
    corpus = json.loads(INSPECTION_V1.read_text(encoding="utf-8"))
    case = corpus["operationCases"][0]
    assert case["accepted"]
    operation = MarketplaceOperation.model_validate_json(
        json.dumps(case["value"]), by_alias=False, by_name=True
    )
    assert operation.kind == "package_detail" and operation.state == "succeeded"
    assert operation.result.type == "package_detail"
    detail = operation.result.value
    first, second = detail.workflows
    assert [first.workflow_name, second.workflow_name] == ["A", "B"]
    digests = [detail.package_digest, first.package_digest, second.package_digest]
    assert all(re.fullmatch(r"[a-f0-9]{64}", digest) for digest in digests)
    assert len(set(digests)) == 3  # This real A/B fixture, not a schema inequality.
    assert all(
        re.fullmatch(r"[a-f0-9]{64}", item.risk_digest) for item in detail.workflows
    )


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "changed"])
def test_generator_check_detects_v1_inspection_drift(
    tmp_path, monkeypatch, capsys, missing
):
    # Break caught: --check silently omitting the V1 artifact from its ownership.
    spec = importlib.util.spec_from_file_location(
        "inspection_fixture_drift",
        ROOT / "scripts/generate_workflow_marketplace_lifecycle_fixtures.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifact = tmp_path / INSPECTION_V1.name
    if not missing:
        artifact.write_text(
            INSPECTION_V1.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
    monkeypatch.setattr(module, "INSPECTION_V1", artifact)
    monkeypatch.setattr(sys, "argv", ["generate-fixtures", "--check"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    assert (
        f"Lifecycle generated artifact drift: {artifact.name}"
        in capsys.readouterr().err
    )


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
