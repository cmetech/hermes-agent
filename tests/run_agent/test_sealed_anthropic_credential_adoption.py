"""Sealed Anthropic credential adoption is one atomic client transaction."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.credential_adoption import _CredentialRefreshStatus
from run_agent import AIAgent, ProviderCapabilityDriftError


ANTHROPIC_ENDPOINT = "https://api.anthropic.com"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
EXPIRED_TOKEN = "sk-ant-api03-expired-sealed-token"
FRESH_TOKEN = "sk-ant-api03-fresh-sealed-token"


def _sealed_anthropic_constraint(
    *,
    provider: str = "anthropic",
    endpoint: str = ANTHROPIC_ENDPOINT,
):
    from hermes_cli import runtime_provider as rp

    identity = rp.execution_runtime_identity(
        rp.classify_execution_runtime(
            provider=provider,
            model_config={"provider": provider, "default": ANTHROPIC_MODEL},
            provider_config={
                "api_mode": "anthropic_messages",
                "base_url": endpoint,
            },
        )
    )
    return rp.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="a" * 64,
        requested_provider=provider,
        model=ANTHROPIC_MODEL,
        api_mode="anthropic_messages",
        base_url=endpoint,
        provider_config={},
        identity=identity,
    )


def _build_real_sealed_anthropic_agent(
    *,
    provider: str = "anthropic",
    endpoint: str = ANTHROPIC_ENDPOINT,
    api_key: str = EXPIRED_TOKEN,
    credential_pool=None,
):
    constraint = _sealed_anthropic_constraint(
        provider=provider,
        endpoint=endpoint,
    )
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            provider=provider,
            requested_provider=provider,
            model=ANTHROPIC_MODEL,
            api_mode="anthropic_messages",
            base_url=endpoint,
            api_key=api_key,
            credential_pool=credential_pool,
            execution_route_constraint=constraint,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=2,
        )
    agent._cached_system_prompt = "You are helpful."
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._disable_streaming = True
    return agent, constraint


@dataclass(frozen=True)
class _AnthropicState:
    client: Any
    anthropic_api_key: Any
    anthropic_base_url: Any
    is_anthropic_oauth: bool
    api_key: Any
    base_url: Any
    credential_pool_entry_id: Any


def _snapshot_anthropic_state(agent: AIAgent) -> _AnthropicState:
    return _AnthropicState(
        client=agent._anthropic_client,
        anthropic_api_key=agent._anthropic_api_key,
        anthropic_base_url=agent._anthropic_base_url,
        is_anthropic_oauth=agent._is_anthropic_oauth,
        api_key=agent.api_key,
        base_url=agent.base_url,
        credential_pool_entry_id=getattr(
            agent, "_credential_pool_entry_id", None
        ),
    )


def _client_is_closed(client: Any) -> bool:
    closed = getattr(client, "is_closed", False)
    return bool(closed() if callable(closed) else closed)


class _AnthropicConstructorBarrier:
    """Lowest Anthropic SDK constructor boundary returning concrete clients."""

    def __init__(
        self,
        concrete_anthropic,
        *,
        agent: AIAgent,
        expired_token: str,
        fresh_token: str,
        failures: int,
        pause_before_second: bool = False,
        before_candidate_return=None,
        failure_message: str = "deterministic Anthropic constructor failure",
    ) -> None:
        self._concrete_anthropic = concrete_anthropic
        self._agent = agent
        self._expired_token = expired_token
        self._fresh_token = fresh_token
        self._failures_remaining = failures
        self._pause_before_second = pause_before_second
        self._before_candidate_return = before_candidate_return
        self._failure_message = failure_message
        self.constructor_tokens: list[str] = []
        self.constructor_beta_headers: list[str] = []
        self.clients: list[Any] = []
        self._first_failure = threading.Event()
        self._second_attempt_waiting = threading.Event()
        self._release_second_attempt = threading.Event()

    def wait_until_first_failure(self) -> None:
        assert self._first_failure.wait(timeout=5)

    def wait_until_second_attempt(self) -> None:
        assert self._second_attempt_waiting.wait(timeout=5)

    def release_second_attempt(self) -> None:
        self._release_second_attempt.set()

    def __call__(self, **kwargs):
        token = kwargs.get("api_key") or kwargs.get("auth_token")
        published_token = getattr(self._agent, "_anthropic_api_key", None)
        is_adoption_build = (
            token == self._fresh_token and published_token != self._fresh_token
        )
        if is_adoption_build:
            self.constructor_tokens.append(token)
            self.constructor_beta_headers.append(
                str((kwargs.get("default_headers") or {}).get("anthropic-beta", ""))
            )
            if len(self.constructor_tokens) == 2 and self._pause_before_second:
                self._second_attempt_waiting.set()
                assert self._release_second_attempt.wait(timeout=5)
            if self._failures_remaining:
                self._failures_remaining -= 1
                if len(self.constructor_tokens) == 1:
                    self._first_failure.set()
                raise RuntimeError(self._failure_message)
            if self._before_candidate_return is not None:
                self._before_candidate_return()

        supplied_http_client = kwargs.get("http_client")
        if supplied_http_client is not None:
            supplied_http_client.close()

        def handle_request(_request: httpx.Request) -> httpx.Response:
            if token == self._expired_token:
                return httpx.Response(
                    401,
                    json={
                        "type": "error",
                        "error": {
                            "type": "authentication_error",
                            "message": "expired",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_sealed_recovery",
                    "type": "message",
                    "role": "assistant",
                    "model": ANTHROPIC_MODEL,
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        kwargs["http_client"] = httpx.Client(
            transport=httpx.MockTransport(handle_request)
        )
        client = self._concrete_anthropic(**kwargs)
        self.clients.append(client)
        return client


def _install_constructor_barrier(
    agent: AIAgent,
    monkeypatch,
    *,
    fresh_token: str = FRESH_TOKEN,
    failures: int,
    pause_before_second: bool = False,
    before_candidate_return=None,
    failure_message: str = "deterministic Anthropic constructor failure",
) -> _AnthropicConstructorBarrier:
    from agent import anthropic_adapter

    sdk = anthropic_adapter._get_anthropic_sdk()
    barrier = _AnthropicConstructorBarrier(
        sdk.Anthropic,
        agent=agent,
        expired_token=EXPIRED_TOKEN,
        fresh_token=fresh_token,
        failures=failures,
        pause_before_second=pause_before_second,
        before_candidate_return=before_candidate_return,
        failure_message=failure_message,
    )
    monkeypatch.setattr(sdk, "Anthropic", barrier)
    return barrier


def _drive_native_anthropic_401(agent: AIAgent):
    """Drive a concrete request-local Anthropic 401 through the real loop."""
    return agent.run_conversation("hello")


def _real_anthropic_oauth_pool(tmp_path):
    from agent.credential_pool import (
        AUTH_TYPE_OAUTH,
        CredentialPool,
        PooledCredential,
    )

    entry = PooledCredential(
        provider="anthropic",
        id="anthropic-oauth-entry",
        label="Anthropic OAuth",
        auth_type=AUTH_TYPE_OAUTH,
        priority=0,
        source=f"manual:{tmp_path.name}",
        access_token=EXPIRED_TOKEN,
        refresh_token="refresh-token",
        base_url=ANTHROPIC_ENDPOINT,
    )
    return CredentialPool("anthropic", [entry]), entry


def _drive_pool_adoption_failure(agent: AIAgent):
    recovery_state = agent._begin_credential_recovery_turn()
    try:
        return agent._recover_with_credential_pool(
            status_code=401,
            has_retried_429=False,
            credential_recovery_state=recovery_state,
        )
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)


def _capture_retry_states(monkeypatch):
    import agent.conversation_loop as conversation_loop

    real_state = conversation_loop.TurnRetryState
    states = []

    def build_state():
        state = real_state()
        states.append(state)
        return state

    monkeypatch.setattr(conversation_loop, "TurnRetryState", build_state)
    return states


class _LockOrderProbe:
    def __init__(self, agent: AIAgent) -> None:
        self.agent = agent
        self.calls = 0

    def __call__(self, *_args, **_kwargs) -> None:
        self.calls += 1
        owned = getattr(self.agent._openai_client_lock(), "_is_owned", lambda: False)
        assert owned() is False


def _prepare_pool_candidate(
    tmp_path,
    monkeypatch,
    *,
    failures: int,
    provider: str = "anthropic",
    fresh_token: str = FRESH_TOKEN,
    pause_before_second: bool = False,
    before_candidate_return=None,
):
    pool, current = _real_anthropic_oauth_pool(tmp_path)
    agent, constraint = _build_real_sealed_anthropic_agent(
        provider=provider,
        credential_pool=pool,
    )
    current.provider = provider
    current.access_token = fresh_token
    current.refresh_token = "rotated-refresh-token"
    current.base_url = ANTHROPIC_ENDPOINT
    agent._credential_pool_entry_id = "published-entry"
    state = agent._begin_credential_recovery_turn()
    barrier = _install_constructor_barrier(
        agent,
        monkeypatch,
        fresh_token=fresh_token,
        failures=failures,
        pause_before_second=pause_before_second,
        before_candidate_return=before_candidate_return,
    )
    return agent, constraint, current, state, barrier


def test_sealed_anthropic_direct_refresh_is_atomic_and_reuses_token(
    tmp_path, monkeypatch
):
    del tmp_path
    agent, constraint = _build_real_sealed_anthropic_agent()
    old = _snapshot_anthropic_state(agent)
    token_reads: list[str] = []
    close_observations: list[_AnthropicState] = []
    original_close = old.client.close

    def close_old() -> None:
        close_observations.append(_snapshot_anthropic_state(agent))
        original_close()

    old.client.close = close_old
    monkeypatch.setattr(
        "agent.anthropic_adapter.resolve_anthropic_token",
        lambda: token_reads.append(FRESH_TOKEN) or FRESH_TOKEN,
    )
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=1)

    try:
        result = _drive_native_anthropic_401(agent)

        assert token_reads == [FRESH_TOKEN]
        assert barrier.constructor_tokens == [FRESH_TOKEN, FRESH_TOKEN]
        assert result.get("failed") is not True
        assert close_observations == [
            _AnthropicState(
                client=agent._anthropic_client,
                anthropic_api_key=FRESH_TOKEN,
                anthropic_base_url=ANTHROPIC_ENDPOINT,
                is_anthropic_oauth=False,
                api_key=FRESH_TOKEN,
                base_url=ANTHROPIC_ENDPOINT,
                credential_pool_entry_id=None,
            )
        ]
        assert close_observations[0].client is not old.client
        assert _client_is_closed(old.client)
        assert agent._assert_execution_route_constraint(
            agent._anthropic_client
        ) is constraint
    finally:
        agent.close()
        if not _client_is_closed(old.client):
            original_close()


def test_sealed_anthropic_pool_failure_preserves_all_mode_fields(
    tmp_path, monkeypatch
):
    pool, _entry = _real_anthropic_oauth_pool(tmp_path)
    agent, _constraint = _build_real_sealed_anthropic_agent(
        credential_pool=pool
    )
    agent._credential_pool_entry_id = "published-entry"
    old = _snapshot_anthropic_state(agent)
    refreshes: list[str] = []
    monkeypatch.setattr(
        "agent.anthropic_adapter.refresh_anthropic_oauth_pure",
        lambda refresh_token, **_kwargs: refreshes.append(refresh_token)
        or {
            "access_token": FRESH_TOKEN,
            "refresh_token": "rotated-refresh-token",
            "expires_at_ms": 9_999_999_999_000,
        },
    )
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=2)

    try:
        assert _drive_pool_adoption_failure(agent) == (False, False)
        assert refreshes == ["refresh-token"]
        assert barrier.constructor_tokens == [FRESH_TOKEN, FRESH_TOKEN]
        assert _snapshot_anthropic_state(agent) == old
        assert not _client_is_closed(old.client)
    finally:
        agent.close()


def test_sealed_anthropic_route_drift_is_terminal_and_closes_candidate(
    tmp_path, monkeypatch
):
    agent_ref: dict[str, AIAgent] = {}

    def drift_route() -> None:
        agent_ref["agent"]._anthropic_base_url = "https://drift.invalid"

    agent, _constraint, candidate, state, barrier = _prepare_pool_candidate(
        tmp_path,
        monkeypatch,
        failures=0,
        before_candidate_return=drift_route,
    )
    agent_ref["agent"] = agent
    old = _snapshot_anthropic_state(agent)
    try:
        with pytest.raises(ProviderCapabilityDriftError):
            agent._swap_credential(
                candidate,
                credential_recovery_state=state,
            )

        assert _snapshot_anthropic_state(agent).client is old.client
        assert not _client_is_closed(old.client)
        assert len(barrier.clients) == 1
        assert _client_is_closed(barrier.clients[0])
    finally:
        agent._anthropic_base_url = ANTHROPIC_ENDPOINT
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_sealed_anthropic_cancellation_at_constructor_barrier_never_publishes(
    tmp_path, monkeypatch
):
    agent, _constraint, candidate, state, barrier = _prepare_pool_candidate(
        tmp_path,
        monkeypatch,
        failures=1,
        pause_before_second=True,
    )
    old = _snapshot_anthropic_state(agent)
    results: list[bool] = []
    errors: list[BaseException] = []

    def adopt() -> None:
        try:
            results.append(
                agent._swap_credential(candidate, credential_recovery_state=state)
            )
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    thread = threading.Thread(target=adopt)
    thread.start()
    barrier.wait_until_second_attempt()
    agent._interrupt_requested = True
    barrier.release_second_attempt()
    thread.join(timeout=5)
    try:
        assert not thread.is_alive()
        assert errors == []
        assert results == [False]
        assert _snapshot_anthropic_state(agent) == old
        assert not _client_is_closed(old.client)
    finally:
        barrier.release_second_attempt()
        agent._interrupt_requested = False
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_sealed_anthropic_second_build_failure_clears_candidate(
    tmp_path, monkeypatch
):
    agent, _constraint, candidate, state, barrier = _prepare_pool_candidate(
        tmp_path, monkeypatch, failures=2
    )
    old = _snapshot_anthropic_state(agent)
    try:
        assert agent._swap_credential(
            candidate, credential_recovery_state=state
        ) is False
        assert barrier.constructor_tokens == [FRESH_TOKEN, FRESH_TOKEN]
        assert getattr(agent, "_pending_sealed_credential_adoption", None) is None
        assert _snapshot_anthropic_state(agent) == old
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_sealed_anthropic_old_client_close_failure_keeps_new_state_and_redacts(
    tmp_path, monkeypatch, caplog
):
    canary = "RETIREMENT_EXCEPTION_TOKEN_ENDPOINT_PATH_CANARY"
    agent, constraint, candidate, state, _barrier = _prepare_pool_candidate(
        tmp_path, monkeypatch, failures=0
    )
    old_client = agent._anthropic_client
    old_client.close = MagicMock(side_effect=RuntimeError(canary))
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    try:
        assert agent._swap_credential(
            candidate, credential_recovery_state=state
        ) is True
        assert agent._anthropic_client is not old_client
        assert agent._anthropic_api_key == FRESH_TOKEN
        assert agent.api_key == FRESH_TOKEN
        assert agent._credential_pool_entry_id == candidate.id
        assert agent._assert_execution_route_constraint(
            agent._anthropic_client
        ) is constraint
        assert old_client.close.call_count == 1
        assert canary not in caplog.text
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_sealed_anthropic_third_party_pool_publication_preserves_identity(
    tmp_path, monkeypatch
):
    oauth_like = "sk-ant-oat01-third-party-token"
    agent, constraint, candidate, state, barrier = _prepare_pool_candidate(
        tmp_path,
        monkeypatch,
        failures=0,
        provider="minimax",
        fresh_token=oauth_like,
    )
    try:
        assert agent._swap_credential(
            candidate, credential_recovery_state=state
        ) is True
        assert barrier.constructor_tokens == [oauth_like]
        assert agent._anthropic_api_key == oauth_like
        assert agent.api_key == oauth_like
        assert agent._anthropic_base_url == ANTHROPIC_ENDPOINT
        assert agent.base_url == ANTHROPIC_ENDPOINT
        assert agent._credential_pool_entry_id == candidate.id
        assert agent._is_anthropic_oauth is False
        assert agent._assert_execution_route_constraint(
            agent._anthropic_client
        ) is constraint
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def _assert_sealed_anthropic_acquisition_failure_consumes_existing_guard(
    outcome, tmp_path, monkeypatch
):
    del tmp_path
    agent, _constraint = _build_real_sealed_anthropic_agent()
    states = _capture_retry_states(monkeypatch)
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=0)
    reads: list[str] = []

    def resolve():
        reads.append(outcome)
        if outcome == "error":
            raise RuntimeError("credential acquisition failed")
        return ""

    monkeypatch.setattr("agent.anthropic_adapter.resolve_anthropic_token", resolve)
    try:
        result = _drive_native_anthropic_401(agent)
        assert result.get("failed") is True
        assert reads == [outcome]
        assert barrier.constructor_tokens == []
        assert len(states) == 1
        assert states[0].anthropic_auth_retry_attempted is True
    finally:
        agent.close()


def test_sealed_anthropic_acquisition_empty_consumes_existing_guard(
    tmp_path, monkeypatch
):
    _assert_sealed_anthropic_acquisition_failure_consumes_existing_guard(
        "empty", tmp_path, monkeypatch
    )


def test_sealed_anthropic_acquisition_error_consumes_existing_guard(
    tmp_path, monkeypatch
):
    _assert_sealed_anthropic_acquisition_failure_consumes_existing_guard(
        "error", tmp_path, monkeypatch
    )


def test_sealed_anthropic_first_build_failure_then_success_sets_guard(
    tmp_path, monkeypatch
):
    del tmp_path
    agent, _constraint = _build_real_sealed_anthropic_agent()
    states = _capture_retry_states(monkeypatch)
    monkeypatch.setattr(
        "agent.anthropic_adapter.resolve_anthropic_token",
        lambda: FRESH_TOKEN,
    )
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=1)
    try:
        result = _drive_native_anthropic_401(agent)
        assert result.get("failed") is not True
        assert barrier.constructor_tokens == [FRESH_TOKEN, FRESH_TOKEN]
        assert len(states) == 1
        assert states[0].anthropic_auth_retry_attempted is True
    finally:
        agent.close()


def test_sealed_anthropic_two_build_failures_leave_success_guard_clear(
    tmp_path, monkeypatch
):
    del tmp_path
    agent, _constraint = _build_real_sealed_anthropic_agent()
    states = _capture_retry_states(monkeypatch)
    monkeypatch.setattr(
        "agent.anthropic_adapter.resolve_anthropic_token",
        lambda: FRESH_TOKEN,
    )
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=2)
    try:
        result = _drive_native_anthropic_401(agent)
        assert result.get("failed") is True
        assert barrier.constructor_tokens == [FRESH_TOKEN, FRESH_TOKEN]
        assert len(states) == 1
        assert states[0].anthropic_auth_retry_attempted is False
    finally:
        agent.close()


def test_sealed_anthropic_retirement_starts_after_client_lock_release(
    tmp_path, monkeypatch
):
    agent, _constraint, candidate, state, _barrier = _prepare_pool_candidate(
        tmp_path, monkeypatch, failures=0
    )
    probe = _LockOrderProbe(agent)
    agent._anthropic_client.close = probe
    try:
        assert agent._swap_credential(
            candidate, credential_recovery_state=state
        ) is True
        assert probe.calls == 1
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


@pytest.mark.parametrize("outcome", ["failed", "invalidated"])
def test_sealed_anthropic_failed_or_invalidated_adoption_never_retires_live_client(
    outcome, tmp_path, monkeypatch
):
    agent, _constraint, candidate, state, _barrier = _prepare_pool_candidate(
        tmp_path,
        monkeypatch,
        failures=2 if outcome == "failed" else 0,
    )
    old = _snapshot_anthropic_state(agent)
    retirements: list[bool] = []
    old.client.close = lambda: retirements.append(True)
    if outcome == "invalidated":
        agent._interrupt_requested = True
    try:
        assert agent._swap_credential(
            candidate, credential_recovery_state=state
        ) is False
        assert retirements == []
        assert _snapshot_anthropic_state(agent) == old
    finally:
        agent._interrupt_requested = False
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_sealed_anthropic_adoption_preserves_disabled_1m_beta(
    tmp_path, monkeypatch
):
    # Exercise the Azure-gated 1M beta without also introducing the Foundry
    # adapter's implicit api-version query (endpoint-query sealing is covered
    # by the Phase 5 execution-context suite).
    azure_endpoint = "https://proxy.azure.com/anthropic-proxy"
    pool, current = _real_anthropic_oauth_pool(tmp_path)
    current.access_token = FRESH_TOKEN
    current.base_url = azure_endpoint
    agent, constraint = _build_real_sealed_anthropic_agent(
        provider="azure-foundry",
        endpoint=azure_endpoint,
        credential_pool=pool,
    )
    current.provider = "azure-foundry"
    agent._oauth_1m_beta_disabled = True
    state = agent._begin_credential_recovery_turn()
    barrier = _install_constructor_barrier(agent, monkeypatch, failures=0)
    try:
        assert agent._swap_credential(
            current, credential_recovery_state=state
        ) is True
        request_client = agent._create_request_anthropic_client(reason="test")
        try:
            assert barrier.constructor_tokens == [FRESH_TOKEN]
            assert all(
                "context-1m-2025-08-07" not in betas
                for betas in barrier.constructor_beta_headers
            )
            assert agent._assert_execution_route_constraint(
                agent._anthropic_client
            ) is constraint
        finally:
            request_client.close()
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


def test_anthropic_candidate_failure_diagnostics_are_candidate_safe(
    tmp_path, monkeypatch, caplog
):
    canaries = [
        "TOKEN_CANARY",
        "ENDPOINT_CANARY",
        "DIGEST_CANARY",
        "EXCEPTION_CANARY",
        "/tmp/PATH_CANARY",
    ]
    agent, _constraint, candidate, state, _barrier = _prepare_pool_candidate(
        tmp_path,
        monkeypatch,
        failures=2,
    )
    from agent import anthropic_adapter

    sdk = anthropic_adapter._get_anthropic_sdk()
    barrier = _AnthropicConstructorBarrier(
        sdk.Anthropic,
        agent=agent,
        expired_token=EXPIRED_TOKEN,
        fresh_token=FRESH_TOKEN,
        failures=2,
        failure_message=" ".join(canaries),
    )
    monkeypatch.setattr(sdk, "Anthropic", barrier)
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    try:
        adopted = agent._swap_credential(
            candidate, credential_recovery_state=state
        )
        public = json.dumps({"adopted": adopted})
        assert adopted is False
        for canary in canaries:
            assert canary not in caplog.text + public
    finally:
        agent._end_credential_recovery_turn(state.generation)
        agent.close()


@pytest.mark.parametrize("api_mode", ["bedrock_converse", "codex_app_server"])
def test_pool_adoption_is_not_applicable_without_a_publication_row(
    api_mode, tmp_path, monkeypatch
):
    del tmp_path
    agent, _constraint = _build_real_sealed_anthropic_agent()
    state = agent._begin_credential_recovery_turn()
    entry = SimpleNamespace(
        id="unsupported-entry",
        runtime_api_key=FRESH_TOKEN,
        runtime_base_url=ANTHROPIC_ENDPOINT,
    )
    agent.api_mode = api_mode
    builds = MagicMock(side_effect=AssertionError("unsupported row built a client"))
    monkeypatch.setattr(
        "agent.anthropic_adapter.build_anthropic_client",
        builds,
    )
    try:
        assert agent._swap_credential(
            entry, credential_recovery_state=state
        ) is False
        builds.assert_not_called()
    finally:
        agent.api_mode = "anthropic_messages"
        agent._end_credential_recovery_turn(state.generation)
        agent.close()
