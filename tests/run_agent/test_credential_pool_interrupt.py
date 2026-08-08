"""Regression test for #26145: credential pool rotation after interrupt-resume.

When has_retried_429 is lost (user cancels between 429s), the pool should
still rotate if the current credential is already marked exhausted.
"""
import threading
from unittest.mock import MagicMock, patch

from agent.credential_pool import PooledCredential, STATUS_EXHAUSTED
from agent.error_classifier import FailoverReason


def _make_entry(idx, **overrides):
    defaults = dict(
        provider="test-provider",
        id=f"cred-{idx}",
        label=f"Credential {idx}",
        auth_type="api_key",
        priority=idx,
        source="manual",
        access_token=f"key-{idx}",
    )
    defaults.update(overrides)
    return PooledCredential(**defaults)


def _make_pool(entries):
    pool = MagicMock()
    pool.entries = MagicMock(return_value=entries)
    pool.current.return_value = entries[0]
    # Must be set explicitly — MagicMock.provider returns a truthy
    # child mock, which would trigger the provider-mismatch guard.
    pool.provider = ""
    return pool


def test_rotate_immediately_when_credential_already_exhausted():
    """If current credential has last_status='exhausted', rotate on first 429
    instead of retrying (Option A fix for #26145)."""
    entries = [_make_entry(0, last_status=STATUS_EXHAUSTED, last_error_code=429), _make_entry(1)]
    pool = _make_pool(entries)
    pool.mark_exhausted_and_rotate.return_value = entries[1]

    from run_agent import AIAgent
    with patch("run_agent.get_tool_definitions", return_value=[]),          patch("run_agent.check_toolset_requirements", return_value={}),          patch("run_agent.OpenAI"):
        agent = MagicMock(spec=AIAgent)
        agent._credential_pool = pool
        agent._swap_credential = MagicMock()
        recovered, retried = AIAgent._recover_with_credential_pool(
            agent,
            status_code=429,
            has_retried_429=False,  # Key: False on first 429 after interrupt
            classified_reason=FailoverReason.rate_limit,
        )

    assert recovered is True
    assert retried is False
    pool.mark_exhausted_and_rotate.assert_called_once()
    agent._swap_credential.assert_called_once_with(entries[1])




def test_rotate_on_second_429_when_not_exhausted():
    """When credential is active and this is the second 429, rotate (existing behavior)."""
    entries = [_make_entry(0, last_status=None), _make_entry(1)]
    pool = _make_pool(entries)
    pool.mark_exhausted_and_rotate.return_value = entries[1]

    from run_agent import AIAgent
    with patch("run_agent.get_tool_definitions", return_value=[]),          patch("run_agent.check_toolset_requirements", return_value={}),          patch("run_agent.OpenAI"):
        agent = MagicMock(spec=AIAgent)
        agent._credential_pool = pool
        agent._swap_credential = MagicMock()
        recovered, retried = AIAgent._recover_with_credential_pool(
            agent,
            status_code=429,
            has_retried_429=True,  # Second 429
            classified_reason=FailoverReason.rate_limit,
        )

    assert recovered is True
    assert retried is False
    pool.mark_exhausted_and_rotate.assert_called_once()


def _start_paused_sealed_adoption(monkeypatch):
    from tests.run_agent.test_env_credential_turn_refresh import (
        _prepare_pending_pool_recovery,
    )

    prepared = _prepare_pending_pool_recovery(
        monkeypatch,
        failures=1,
        pause_before_second=True,
    )
    agent, _constraint, _current, candidate, recovery_state, barrier, _calls = prepared
    results: list[bool] = []
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            results.append(
                agent._swap_credential(
                    candidate,
                    credential_recovery_state=recovery_state,
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    thread = threading.Thread(target=recover)
    thread.start()
    barrier.wait_until_second_attempt()
    return prepared, thread, results, errors


def test_sealed_candidate_cancellation_before_publication_wins(monkeypatch):
    (
        prepared,
        recovery_thread,
        results,
        errors,
    ) = _start_paused_sealed_adoption(monkeypatch)
    agent, _constraint, _current, _candidate, recovery_state, barrier, _calls = prepared
    old_client = agent.client
    interrupt_thread = threading.Thread(
        target=lambda: agent.interrupt(hard_cancel=True)
    )
    interrupt_thread.start()
    barrier.release_second_attempt()
    recovery_thread.join(timeout=5)
    interrupt_thread.join(timeout=5)

    try:
        assert not recovery_thread.is_alive()
        assert not interrupt_thread.is_alive()
        assert errors == []
        assert results == [False]
        assert agent.client is old_client
        assert getattr(agent, "_pending_sealed_credential_adoption", None) is None
        assert getattr(agent, "_credential_recovery_active_generation", None) is None
    finally:
        barrier.release_second_attempt()
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_sealed_candidate_publication_before_cancellation_stays_committed(
    monkeypatch,
):
    (
        prepared,
        recovery_thread,
        results,
        errors,
    ) = _start_paused_sealed_adoption(monkeypatch)
    agent, _constraint, _current, _candidate, recovery_state, barrier, _calls = prepared
    old_client = agent.client
    barrier.release_second_attempt()
    recovery_thread.join(timeout=5)
    agent.interrupt(hard_cancel=True)

    try:
        assert not recovery_thread.is_alive()
        assert errors == []
        assert results == [True]
        assert agent.client is not old_client
        assert getattr(agent, "_pending_sealed_credential_adoption", None) is None
        assert getattr(agent, "_credential_recovery_active_generation", None) is None
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()
