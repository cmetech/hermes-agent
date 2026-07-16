"""Decision-table tests for verified provider model selection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hermes_cli.model_capabilities import (
    CAPABILITY_KEYS,
    ModelCapabilityCatalog,
    VerifiedModelCapability,
)
from providers.base import ProviderProfile

from hermes_cli.model_eligibility import (
    ModelEligibility,
    evaluate_model_eligibility,
    validate_provider_model_selection,
)


def _evaluate(
    *,
    model: str = "model-a",
    usage: str = "main",
    capability_contract: bool = True,
    catalog_status: str = "ready",
    is_live: bool = True,
    selection_mode: str = "explicit",
    verified: dict[str, str] | None = None,
    exact_existing_assignment: bool = False,
) -> ModelEligibility:
    return evaluate_model_eligibility(
        capability_contract=capability_contract,
        catalog_status=catalog_status,
        model=model,
        usage=usage,
        is_live=is_live,
        selection_mode=selection_mode,
        verified=verified
        if verified is not None
        else {key: "supported" for key in CAPABILITY_KEYS},
        exact_existing_assignment=exact_existing_assignment,
    )


@pytest.mark.parametrize(
    ("usage", "required"),
    [
        ("main", ("completion", "tools")),
        ("fallback", ("completion", "tools")),
        ("auxiliary", ("completion",)),
        ("vision", ("completion", "vision")),
        ("moa-reference", ("completion",)),
        ("moa-aggregator", ("completion", "tools")),
    ],
)
def test_each_usage_accepts_exactly_its_supported_requirements(usage, required):
    verified = {key: "unknown" for key in CAPABILITY_KEYS}
    for capability in required:
        verified[capability] = "supported"

    decision = _evaluate(usage=usage, verified=verified)

    assert decision == ModelEligibility(
        eligible=True,
        reason="eligible",
        message="This model is eligible for the requested assignment.",
    )


@pytest.mark.parametrize(
    ("usage", "missing_capability"),
    [
        ("main", "completion"),
        ("main", "tools"),
        ("fallback", "completion"),
        ("fallback", "tools"),
        ("auxiliary", "completion"),
        ("vision", "completion"),
        ("vision", "vision"),
        ("moa-reference", "completion"),
        ("moa-aggregator", "completion"),
        ("moa-aggregator", "tools"),
    ],
)
@pytest.mark.parametrize("state", ["unsupported", "unknown"])
def test_required_capability_states_have_stable_distinct_reasons(
    usage, missing_capability, state
):
    verified = {key: "supported" for key in CAPABILITY_KEYS}
    verified[missing_capability] = state

    decision = _evaluate(usage=usage, verified=verified)

    assert decision.eligible is False
    assert decision.reason == f"{missing_capability}-{state}"
    assert decision.message
    assert decision.grandfathered is False


def test_main_auto_is_the_sole_automatic_routing_exception():
    decision = _evaluate(
        model="auto",
        usage="main",
        catalog_status="gateway-upgrade-required",
        is_live=True,
        selection_mode="automatic",
        verified=None,
    )

    assert decision == ModelEligibility(
        eligible=True,
        reason="automatic-routing",
        message="Automatic routing is allowed for the main model.",
    )


@pytest.mark.parametrize(
    "usage",
    ["fallback", "auxiliary", "vision", "moa-reference", "moa-aggregator"],
)
def test_auto_is_rejected_for_every_non_main_usage(usage):
    decision = _evaluate(
        model="auto",
        usage=usage,
        selection_mode="automatic",
    )

    assert decision == ModelEligibility(
        eligible=False,
        reason="automatic-not-allowed",
        message="Automatic routing is only allowed for the main model.",
    )


@pytest.mark.parametrize(
    "usage",
    ["fallback", "auxiliary", "vision", "moa-reference", "moa-aggregator"],
)
def test_non_main_auto_is_rejected_even_with_explicit_supported_metadata(usage):
    decision = _evaluate(
        model="auto",
        usage=usage,
        selection_mode="explicit",
        verified={key: "supported" for key in CAPABILITY_KEYS},
    )

    assert decision == ModelEligibility(
        eligible=False,
        reason="automatic-not-allowed",
        message="Automatic routing is only allowed for the main model.",
    )


def test_other_automatic_models_are_not_the_main_auto_exception():
    decision = _evaluate(
        model="automatic-alias",
        usage="main",
        selection_mode="automatic",
    )

    assert decision.reason == "automatic-not-allowed"
    assert decision.eligible is False


def test_absent_live_model_is_rejected_before_capability_checks():
    decision = _evaluate(
        is_live=False,
        verified={key: "supported" for key in CAPABILITY_KEYS},
    )

    assert decision == ModelEligibility(
        eligible=False,
        reason="model-not-live",
        message="This model is not present in the provider's live model list.",
    )


@pytest.mark.parametrize(
    "status",
    [
        "authentication-required",
        "gateway-upgrade-required",
        "gateway-unreachable",
        "catalog-empty",
        "capability-response-invalid",
        "unknown",
    ],
)
def test_non_ready_catalog_status_is_the_stable_failure_reason(status):
    decision = _evaluate(catalog_status=status)

    assert decision.eligible is False
    assert decision.reason == status
    assert decision.message
    assert decision.grandfathered is False


@pytest.mark.parametrize(
    "status",
    [
        "authentication-required",
        "gateway-upgrade-required",
        "gateway-unreachable",
        "catalog-empty",
        "capability-response-invalid",
        "unknown",
    ],
)
@pytest.mark.parametrize("exact_existing_assignment", [False, True])
def test_non_ready_catalog_status_precedes_absent_live_model(
    status, exact_existing_assignment
):
    decision = _evaluate(
        catalog_status=status,
        is_live=False,
        exact_existing_assignment=exact_existing_assignment,
    )

    assert decision.reason == status
    assert decision.eligible is exact_existing_assignment
    assert decision.grandfathered is exact_existing_assignment
    if exact_existing_assignment:
        assert decision.message.startswith(
            "The existing assignment was preserved"
        )


def test_provider_without_capability_contract_remains_legacy_compatible():
    decision = _evaluate(
        capability_contract=False,
        catalog_status="gateway-unreachable",
        is_live=False,
        selection_mode="automatic",
        verified=None,
    )

    assert decision == ModelEligibility(
        eligible=True,
        reason="legacy-compatible",
        message="This provider does not require verified capability filtering.",
    )


@pytest.mark.parametrize("reasoning", ["supported", "unsupported", "unknown"])
def test_reasoning_never_changes_basic_eligibility(reasoning):
    verified = {key: "supported" for key in CAPABILITY_KEYS}
    verified["reasoning"] = reasoning

    assert _evaluate(usage="main", verified=verified).eligible is True


def test_exact_existing_invalid_assignment_is_preserved_with_warning():
    decision = _evaluate(
        verified={
            "completion": "supported",
            "tools": "unknown",
            "vision": "unknown",
            "reasoning": "supported",
        },
        exact_existing_assignment=True,
    )

    assert decision.eligible is True
    assert decision.reason == "tools-unknown"
    assert decision.grandfathered is True
    assert decision.message.startswith("The existing assignment was preserved")


def test_same_invalid_model_is_rejected_when_it_is_a_changed_assignment():
    decision = _evaluate(
        verified={
            "completion": "supported",
            "tools": "unknown",
            "vision": "unknown",
            "reasoning": "supported",
        },
        exact_existing_assignment=False,
    )

    assert decision.eligible is False
    assert decision.reason == "tools-unknown"
    assert decision.grandfathered is False


def test_model_eligibility_decisions_are_immutable():
    decision = _evaluate()

    with pytest.raises(FrozenInstanceError):
        decision.eligible = False  # type: ignore[misc]


def _verified(
    model_id: str,
    *,
    selection_mode: str = "explicit",
    capabilities: dict[str, str] | None = None,
) -> VerifiedModelCapability:
    return VerifiedModelCapability(
        id=model_id,
        name=model_id,
        selection_mode=selection_mode,
        capabilities=capabilities
        if capabilities is not None
        else {key: "supported" for key in CAPABILITY_KEYS},
        evidence={},
    )


def test_validation_bypasses_network_for_provider_without_contract(monkeypatch):
    profile = ProviderProfile(name="legacy")
    calls = []
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.get_provider_profile",
        lambda provider: calls.append(("profile", provider)) or profile,
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.resolve_api_key_provider_credentials",
        lambda provider: pytest.fail("credentials should not be resolved"),
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.fetch_model_capability_catalog",
        lambda *args, **kwargs: pytest.fail("catalog should not be fetched"),
    )

    decision = validate_provider_model_selection("legacy", "anything", "main")

    assert decision.reason == "legacy-compatible"
    assert decision.eligible is True
    assert calls == [("profile", "legacy")]


def test_validation_returns_legacy_compatibility_for_unknown_profile(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.get_provider_profile",
        lambda provider: None,
    )

    decision = validate_provider_model_selection("unprofiled", "model-a", "main")

    assert decision.reason == "legacy-compatible"
    assert decision.eligible is True


def test_validation_fetches_fresh_live_and_capability_data_then_joins_exact_ids(
    monkeypatch,
):
    profile = ProviderProfile(
        name="gateway",
        base_url="https://default.invalid/v1",
        model_capabilities_path="model-capabilities",
    )
    calls = []

    def fetch_models(*, api_key, base_url):
        calls.append(("models", api_key, base_url))
        return ["MODEL-A", "model-a"]

    profile.fetch_models = fetch_models  # type: ignore[method-assign]
    catalog = ModelCapabilityCatalog(
        status="ready",
        models={
            "model-a": _verified(
                "model-a",
                capabilities={
                    "completion": "supported",
                    "tools": "unknown",
                    "vision": "supported",
                    "reasoning": "supported",
                },
            )
        },
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.get_provider_profile",
        lambda provider: calls.append(("profile", provider)) or profile,
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.resolve_api_key_provider_credentials",
        lambda provider: calls.append(("credentials", provider))
        or {
            "api_key": "resolved-key",
            "base_url": "https://effective.invalid/v1",
        },
    )

    def fetch_catalog(provider, *, api_key, base_url, force_refresh):
        calls.append(
            ("capabilities", provider, api_key, base_url, force_refresh)
        )
        return catalog

    monkeypatch.setattr(
        "hermes_cli.model_eligibility.fetch_model_capability_catalog",
        fetch_catalog,
    )

    exact = validate_provider_model_selection(
        "gateway",
        "model-a",
        "vision",
    )
    wrong_case = validate_provider_model_selection(
        "gateway",
        "Model-A",
        "vision",
    )

    assert exact.eligible is True
    assert wrong_case.reason == "model-not-live"
    assert calls == [
        ("profile", "gateway"),
        ("credentials", "gateway"),
        ("models", "resolved-key", "https://effective.invalid/v1"),
        (
            "capabilities",
            "gateway",
            "resolved-key",
            "https://effective.invalid/v1",
            True,
        ),
        ("profile", "gateway"),
        ("credentials", "gateway"),
        ("models", "resolved-key", "https://effective.invalid/v1"),
        (
            "capabilities",
            "gateway",
            "resolved-key",
            "https://effective.invalid/v1",
            True,
        ),
    ]


def test_validation_uses_catalog_status_and_preserves_exact_invalid_noop(
    monkeypatch,
):
    profile = ProviderProfile(
        name="gateway",
        base_url="https://gateway.invalid/v1",
        model_capabilities_path="model-capabilities",
    )
    profile.fetch_models = lambda **kwargs: ["model-a"]  # type: ignore[method-assign]
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.get_provider_profile",
        lambda provider: profile,
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.resolve_api_key_provider_credentials",
        lambda provider: {"api_key": "", "base_url": profile.base_url},
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.fetch_model_capability_catalog",
        lambda *args, **kwargs: ModelCapabilityCatalog(
            status="gateway-upgrade-required",
            models={},
        ),
    )

    decision = validate_provider_model_selection(
        "gateway",
        "model-a",
        "main",
        exact_existing_assignment=True,
    )

    assert decision.eligible is True
    assert decision.grandfathered is True
    assert decision.reason == "gateway-upgrade-required"


def test_validation_treats_failed_live_fetch_as_model_not_live(monkeypatch):
    profile = ProviderProfile(
        name="gateway",
        base_url="https://gateway.invalid/v1",
        model_capabilities_path="model-capabilities",
    )
    profile.fetch_models = lambda **kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.get_provider_profile",
        lambda provider: profile,
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.resolve_api_key_provider_credentials",
        lambda provider: {"api_key": "", "base_url": profile.base_url},
    )
    monkeypatch.setattr(
        "hermes_cli.model_eligibility.fetch_model_capability_catalog",
        lambda *args, **kwargs: ModelCapabilityCatalog(
            status="ready",
            models={"model-a": _verified("model-a")},
        ),
    )

    decision = validate_provider_model_selection(
        "gateway",
        "model-a",
        "main",
    )

    assert decision.reason == "model-not-live"
    assert decision.eligible is False
