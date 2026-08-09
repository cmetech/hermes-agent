"""Optional request-local accounting at actual provider transport launches."""

from __future__ import annotations

import threading
from typing import Any

from agent.cost_budget import CostBudgetPoisoned


_COST_STATE_LOCK = threading.RLock()


def reserve_provider_transport_attempt(agent: Any, transport: Any = None) -> None:
    """Reserve one actual provider call when a sealed caller installed a guard."""

    with _COST_STATE_LOCK:
        existing_attempt_id = getattr(
            agent, "_active_cost_budget_attempt_id", None
        )
    if existing_attempt_id is not None:
        poison = getattr(agent, "_cost_budget_poison_callback", None)
        if poison is None:
            raise RuntimeError("authoritative cost poison callback is missing")
        try:
            poison(
                existing_attempt_id,
                "authoritative_settlement_ambiguous",
            )
        finally:
            with _COST_STATE_LOCK:
                if (
                    getattr(agent, "_active_cost_budget_attempt_id", None)
                    == existing_attempt_id
                ):
                    agent._active_cost_budget_attempt_id = None
        raise CostBudgetPoisoned(
            "a second provider transport started before authoritative settlement",
            failure_code="authoritative_settlement_ambiguous",
        )

    route_assertion = getattr(agent, "_assert_execution_route_constraint", None)
    if callable(route_assertion):
        route_assertion(transport)

    cost_callback = getattr(agent, "_cost_budget_acquire_callback", None)
    cost_attempt_id = cost_callback() if cost_callback is not None else None
    if cost_attempt_id is not None:
        with _COST_STATE_LOCK:
            if getattr(agent, "_active_cost_budget_attempt_id", None) is not None:
                poison = getattr(agent, "_cost_budget_poison_callback", None)
                if poison is not None:
                    poison(
                        cost_attempt_id,
                        "authoritative_cost_local_state_conflict",
                    )
                raise RuntimeError("authoritative cost lease state is inconsistent")
            agent._active_cost_budget_attempt_id = cost_attempt_id
    callback = getattr(agent, "_provider_attempt_reservation_callback", None)
    try:
        if callback is not None:
            callback()
    except BaseException as exc:
        if cost_attempt_id is not None:
            release = getattr(
                agent, "_cost_budget_release_unstarted_callback", None
            )
            try:
                if release is None:
                    raise RuntimeError(
                        "authoritative cost lease cannot prove no transport"
                    )
                release(cost_attempt_id)
            except BaseException as release_exc:
                raise release_exc from exc
            finally:
                with _COST_STATE_LOCK:
                    agent._active_cost_budget_attempt_id = None
        raise


def settle_provider_transport_attempt(agent: Any, response_usage: object) -> object:
    """Settle the active physical transport from authoritative response usage."""

    with _COST_STATE_LOCK:
        attempt_id = getattr(agent, "_active_cost_budget_attempt_id", None)
    if attempt_id is None:
        return None
    callback = getattr(agent, "_cost_budget_settle_callback", None)
    if callback is None:
        raise RuntimeError("authoritative cost settlement callback is missing")
    try:
        return callback(attempt_id, response_usage)
    finally:
        with _COST_STATE_LOCK:
            if getattr(agent, "_active_cost_budget_attempt_id", None) == attempt_id:
                agent._active_cost_budget_attempt_id = None


def poison_provider_transport_attempt(agent: Any, failure_code: str) -> bool:
    """Poison one dispatched transport whose final billed cost is ambiguous."""

    with _COST_STATE_LOCK:
        attempt_id = getattr(agent, "_active_cost_budget_attempt_id", None)
    if attempt_id is None:
        return False
    callback = getattr(agent, "_cost_budget_poison_callback", None)
    if callback is None:
        raise RuntimeError("authoritative cost poison callback is missing")
    try:
        callback(attempt_id, failure_code)
    finally:
        with _COST_STATE_LOCK:
            if getattr(agent, "_active_cost_budget_attempt_id", None) == attempt_id:
                agent._active_cost_budget_attempt_id = None
    return True


__all__ = [
    "poison_provider_transport_attempt",
    "reserve_provider_transport_attempt",
    "settle_provider_transport_attempt",
]
