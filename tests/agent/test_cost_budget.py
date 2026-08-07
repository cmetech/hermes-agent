from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import time

import pytest

from agent.cost_budget import (
    AuthoritativeCostBudget,
    CostBudgetExhausted,
    CostBudgetLeaseBusy,
    CostBudgetPoisoned,
    acquire_cost_lease,
    canonical_usd,
    poison_cost_lease,
    release_unstarted_cost_lease,
    settle_cost_lease,
    snapshot_cost_budget,
)
from agent.plugin_agent import PluginAgentRunRequest, _validate_request
from agent.usage_pricing import (
    AuthoritativeSettlementContract,
    CostResult,
    authoritative_cost_fact,
)


def test_canonical_usd_accepts_exact_bounded_values_without_float_rounding():
    assert canonical_usd("0.000000001") == "0.000000001"
    assert canonical_usd(Decimal("12.3400")) == "12.34"
    assert canonical_usd(1.25) == "1.25"
    assert canonical_usd(2) == "2"


def test_sealed_phase5_requires_authority_but_legacy_wire_remains_compatible():
    legacy = PluginAgentRunRequest(prompt="legacy", max_budget_usd=1)
    _validate_request(legacy)
    assert PluginAgentRunRequest.from_wire(legacy.to_wire()) == legacy

    with pytest.raises(ValueError, match="authoritative cost enforcement"):
        _validate_request(
            PluginAgentRunRequest(
                prompt="phase5",
                intended_authority_digest="a" * 64,
                max_budget_usd=1,
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        "NaN",
        "Infinity",
        "-0.01",
        "0.0000000001",
        "1000000000000",
        object(),
    ],
)
def test_canonical_usd_rejects_nonfinite_negative_overflow_and_extra_precision(value):
    with pytest.raises((TypeError, ValueError)):
        canonical_usd(value)


def _contract(**overrides):
    values = {
        "provider": "synthetic",
        "strategy": "synthetic_authoritative_v1",
        "billing_mode": "shared_credit",
        "covered_outcomes": frozenset({
            "success",
            "stream",
            "charged_error",
            "disconnect",
            "timeout",
            "cancellation",
        }),
    }
    values.update(overrides)
    return AuthoritativeSettlementContract(**values)


def test_authoritative_fact_requires_one_complete_code_owned_contract():
    fact = authoritative_cost_fact(
        {"cost": "0.123456789", "tokens": 100},
        provider="synthetic",
        model="model",
        contract=_contract(),
    )

    assert fact is not None
    assert fact.amount_usd == "0.123456789"
    assert fact.provider == "synthetic"
    assert fact.model == "model"
    assert fact.strategy == "synthetic_authoritative_v1"
    assert fact.authoritative is True


def test_estimates_arbitrary_keys_byok_spoof_and_incomplete_contract_are_not_facts():
    estimate = CostResult(
        amount_usd=Decimal("0.1"),
        status="estimated",
        source="official_docs_snapshot",
        label="~$0.10",
    )
    assert authoritative_cost_fact(
        estimate,
        provider="synthetic",
        model="model",
        contract=_contract(),
    ) is None
    assert authoritative_cost_fact(
        {"amount_usd": "0.1", "authoritative": True},
        provider="synthetic",
        model="model",
        contract=_contract(),
    ) is None
    assert authoritative_cost_fact(
        {"cost": "0.1"},
        provider="spoofed",
        model="model",
        contract=_contract(),
    ) is None
    assert authoritative_cost_fact(
        {"cost": "0.1"},
        provider="synthetic",
        model="model",
        contract=_contract(billing_mode="byok"),
    ) is None
    assert authoritative_cost_fact(
        {"cost": "0.1"},
        provider="synthetic",
        model="model",
        contract=_contract(covered_outcomes=frozenset({"success"})),
    ) is None


@pytest.mark.parametrize("cost", [True, "NaN", "-1", "0.0000000001"])
def test_complete_contract_rejects_invalid_provider_cost(cost):
    with pytest.raises((TypeError, ValueError)):
        authoritative_cost_fact(
            {"cost": cost},
            provider="synthetic",
            model="model",
            contract=_contract(),
        )


def test_one_settled_call_can_overrun_then_blocks_every_later_transport():
    authority = AuthoritativeCostBudget(
        limit_usd="1",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        lease = acquire_cost_lease(
            authority.descriptor,
            attempt_id="a" * 32,
            provider="synthetic",
            model="model",
            deadline=time.monotonic() + 1,
        )
        assert lease == "a" * 32

        evidence = settle_cost_lease(
            authority.descriptor,
            attempt_id=lease,
            provider="synthetic",
            model="model",
            strategy="synthetic_authoritative_v1",
            amount_usd="1.25",
            authoritative=True,
        )

        assert evidence == {
            "limit_usd": "1",
            "settled_cost_usd": "1.25",
            "remaining_usd": "0",
            "overage_usd": "0.25",
            "settlement_count": 1,
            "authoritative": True,
            "exhausted": True,
            "terminal": True,
            "provider": "synthetic",
            "model": "model",
            "strategy": "synthetic_authoritative_v1",
            "failure_code": "budget_exhausted",
        }
        with pytest.raises(CostBudgetExhausted):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id="b" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 1,
            )
    finally:
        authority.close()


def test_retries_repair_and_children_share_cumulative_settled_budget():
    authority = AuthoritativeCostBudget(
        limit_usd="1",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        evidence = None
        for attempt_id in ("1" * 32, "2" * 32, "3" * 32):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id=attempt_id,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 1,
            )
            evidence = settle_cost_lease(
                authority.descriptor,
                attempt_id=attempt_id,
                provider="synthetic",
                model="model",
                strategy="synthetic_authoritative_v1",
                amount_usd="0.4",
                authoritative=True,
            )

        assert evidence is not None
        assert evidence["settled_cost_usd"] == "1.2"
        assert evidence["settlement_count"] == 3
        assert evidence["failure_code"] == "budget_exhausted"
        with pytest.raises(CostBudgetExhausted):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id="4" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 1,
            )
    finally:
        authority.close()


def test_duplicate_settlement_is_idempotent_but_contradiction_poisons():
    authority = AuthoritativeCostBudget(
        limit_usd="2",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        acquire_cost_lease(
            authority.descriptor,
            attempt_id="c" * 32,
            provider="synthetic",
            model="model",
            deadline=time.monotonic() + 1,
        )
        first = settle_cost_lease(
            authority.descriptor,
            attempt_id="c" * 32,
            provider="synthetic",
            model="model",
            strategy="synthetic_authoritative_v1",
            amount_usd="0.4",
            authoritative=True,
        )
        duplicate = settle_cost_lease(
            authority.descriptor,
            attempt_id="c" * 32,
            provider="synthetic",
            model="model",
            strategy="synthetic_authoritative_v1",
            amount_usd="0.40",
            authoritative=True,
        )
        assert duplicate == first
        assert duplicate["settlement_count"] == 1

        with pytest.raises(CostBudgetPoisoned):
            settle_cost_lease(
                authority.descriptor,
                attempt_id="c" * 32,
                provider="synthetic",
                model="model",
                strategy="synthetic_authoritative_v1",
                amount_usd="0.41",
                authoritative=True,
            )
        assert snapshot_cost_budget(authority.descriptor)["failure_code"] == (
            "authoritative_cost_contradiction"
        )
    finally:
        authority.close()


def test_one_inflight_lease_serializes_concurrent_callers_and_no_start_releases_it():
    authority = AuthoritativeCostBudget(
        limit_usd="2",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        first = acquire_cost_lease(
            authority.descriptor,
            attempt_id="d" * 32,
            provider="synthetic",
            model="model",
            deadline=time.monotonic() + 1,
        )

        def acquire_second():
            return acquire_cost_lease(
                authority.descriptor,
                attempt_id="e" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 2,
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(acquire_second)
            time.sleep(0.05)
            assert pending.done() is False
            release_unstarted_cost_lease(
                authority.descriptor,
                attempt_id=first,
                provider="synthetic",
                model="model",
            )
            assert pending.result(timeout=1) == "e" * 32
        release_unstarted_cost_lease(
            authority.descriptor,
            attempt_id="e" * 32,
            provider="synthetic",
            model="model",
        )
    finally:
        authority.close()


def test_cancelled_waiter_cannot_bypass_or_reopen_the_active_lease():
    authority = AuthoritativeCostBudget(
        limit_usd="2",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        active = acquire_cost_lease(
            authority.descriptor,
            attempt_id="5" * 32,
            provider="synthetic",
            model="model",
            deadline=time.monotonic() + 1,
        )
        with pytest.raises(CostBudgetLeaseBusy, match="cancelled"):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id="6" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 60,
                is_cancelled=lambda: True,
            )
        assert snapshot_cost_budget(authority.descriptor)["terminal"] is False
        poison_cost_lease(
            authority.descriptor,
            attempt_id=active,
            failure_code="authoritative_settlement_ambiguous",
        )
        with pytest.raises(CostBudgetPoisoned):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id="7" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 1,
            )
    finally:
        authority.close()


def test_ambiguous_cancellation_and_provider_spoof_poison_without_reopening():
    authority = AuthoritativeCostBudget(
        limit_usd="2",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    try:
        lease = acquire_cost_lease(
            authority.descriptor,
            attempt_id="f" * 32,
            provider="synthetic",
            model="model",
            deadline=time.monotonic() + 1,
        )
        poison_cost_lease(
            authority.descriptor,
            attempt_id=lease,
            failure_code="authoritative_settlement_ambiguous",
        )
        with pytest.raises(CostBudgetPoisoned):
            acquire_cost_lease(
                authority.descriptor,
                attempt_id="1" * 32,
                provider="synthetic",
                model="model",
                deadline=time.monotonic() + 1,
            )
        with pytest.raises(CostBudgetPoisoned):
            settle_cost_lease(
                authority.descriptor,
                attempt_id=lease,
                provider="spoofed",
                model="model",
                strategy="synthetic_authoritative_v1",
                amount_usd="0.1",
                authoritative=True,
            )
        evidence = snapshot_cost_budget(authority.descriptor)
        assert evidence["terminal"] is True
        assert evidence["failure_code"] == "authoritative_settlement_ambiguous"
        assert set(evidence) == {
            "limit_usd",
            "settled_cost_usd",
            "remaining_usd",
            "overage_usd",
            "settlement_count",
            "authoritative",
            "exhausted",
            "terminal",
            "provider",
            "model",
            "strategy",
            "failure_code",
        }
    finally:
        authority.close()
