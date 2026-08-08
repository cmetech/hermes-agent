"""Per-turn adoption of ~/.hermes/.env credential edits (#67821).

A Settings save (desktop ``PUT /api/env``, ``hermes setup``) updates .env and
the saving process's os.environ, but a live session worker keeps the
base_url/api_key captured at agent init until restart — an open chat silently
kept calling the old endpoint (e.g. a local-server key sent to
api.openai.com → opaque 401).

``AIAgent._try_refresh_env_client_credentials`` re-resolves env-sourced
credentials at the start of each conversation turn and rebuilds the client
when the user edited them. It must react only to env *edits*, never to mere
divergence from the agent's current values: credential-pool rotation and
failover legitimately move the session off the env credential, and config
``model.base_url`` has higher precedence than the env override.
"""

import json
import logging
import os
import sys
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from run_agent import AIAgent, ProviderCapabilityDriftError

DEFAULT_BASE = "https://api.openai.com/v1"
LOCAL_BASE = "http://127.0.0.1:39080"
GMI_BASE = "https://api.gmi-serving.com/v1"
REPEATED_QUERY_ENDPOINT = (
    "https://tenant.openai.azure.com/openai/deployments/review"
    "?deployment=blue&api-version=2025-04-01-preview&deployment=green"
)
REPEATED_DEFAULT_QUERY = {
    "deployment": ("blue", "green"),
    "api-version": "2025-04-01-preview",
}
_MISSING = object()


def _make_agent(
    *,
    provider="openai-api",
    base_url=DEFAULT_BASE,
    api_key="sk-old",
    api_mode="chat_completions",
):
    agent = object.__new__(AIAgent)
    agent.provider = provider
    agent.requested_provider = provider
    agent.model = "test-model"
    agent.api_mode = api_mode
    agent.base_url = base_url
    agent.api_key = api_key
    agent._client_kwargs = {"base_url": base_url, "api_key": api_key}
    agent._fallback_activated = False
    agent._replace_primary_openai_client = MagicMock(return_value=True)
    agent._reapply_route_client_config = MagicMock()
    return agent


def _sealed_route_constraint(
    *,
    provider="openai-api",
    base_url=DEFAULT_BASE,
    api_mode="chat_completions",
):
    from hermes_cli import runtime_provider as rp

    identity = rp.execution_runtime_identity(
        rp.classify_execution_runtime(
            provider=provider,
            model_config={"provider": provider, "default": "test-model"},
            provider_config={
                "api_mode": api_mode,
                "base_url": base_url,
            },
        )
    )
    return rp.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="f" * 64,
        requested_provider=provider,
        model="test-model",
        api_mode=api_mode,
        base_url=base_url,
        provider_config={},
        identity=identity,
    )


def _real_sealed_query_agent(
    *, provider="azure-foundry", endpoint=None, api_key="test-credential"
):
    endpoint = endpoint or (
        "https://tenant.openai.azure.com/openai/deployments/review"
        "?api-version=2025-04-01-preview"
    )
    constraint = _sealed_route_constraint(
        provider=provider,
        base_url=endpoint,
    )
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            provider=provider,
            requested_provider=provider,
            model="test-model",
            api_mode="chat_completions",
            base_url=endpoint,
            api_key=api_key,
            execution_route_constraint=constraint,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    return agent, constraint


def _build_real_sealed_openai_agent(
    *,
    provider: str,
    endpoint: str,
    api_key: str,
    credential_pool=None,
):
    """Build the real sealed runtime exercised by credential-recovery tests."""
    constraint = _sealed_route_constraint(provider=provider, base_url=endpoint)
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            provider=provider,
            requested_provider=provider,
            model="test-model",
            api_mode="chat_completions",
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


class _LowestConstructorBarrier:
    """Deterministic OpenAI constructor boundary with real SDK clients."""

    def __init__(
        self,
        concrete_openai,
        *,
        agent,
        expired_token: str,
        fresh_token: str,
        failures: int,
        pause_before_second: bool = False,
        before_candidate_return=None,
        failure_message: str = "deterministic SDK constructor failure",
    ) -> None:
        self._concrete_openai = concrete_openai
        self._agent = agent
        self._expired_token = expired_token
        self._fresh_token = fresh_token
        self._failures_remaining = failures
        self._pause_before_second = pause_before_second
        self._before_candidate_return = before_candidate_return
        self._failure_message = failure_message
        self.constructor_tokens: list[str] = []
        self.clients: list[object] = []
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
        token = kwargs.get("api_key")
        published_key = getattr(self._agent, "api_key", None)
        is_adoption_build = token == self._fresh_token and published_key != token
        if is_adoption_build:
            self.constructor_tokens.append(token)
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
                    json={"error": {"message": "expired", "type": "auth"}},
                )
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-sealed-recovery",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )

        kwargs["http_client"] = httpx.Client(
            transport=httpx.MockTransport(handle_request)
        )
        client = self._concrete_openai(**kwargs)
        self.clients.append(client)
        return client


def _drive_provider_error(agent):
    """Drive a real provider 401 through the complete conversation loop."""
    return agent.run_conversation("hello")


def _published_openai_state(agent):
    return {
        "client": agent.client,
        "api_key": agent.api_key,
        "base_url": agent.base_url,
        "client_kwargs": deepcopy(agent._client_kwargs),
        "credential_pool_entry_id": getattr(
            agent, "_credential_pool_entry_id", _MISSING
        ),
        "env_creds_seen": getattr(agent, "_env_creds_seen", _MISSING),
    }


def _assert_published_openai_state(agent, expected) -> None:
    assert agent.client is expected["client"]
    assert agent.api_key == expected["api_key"]
    assert agent.base_url == expected["base_url"]
    assert agent._client_kwargs == expected["client_kwargs"]
    assert (
        getattr(agent, "_credential_pool_entry_id", _MISSING)
        == expected["credential_pool_entry_id"]
    )
    assert getattr(agent, "_env_creds_seen", _MISSING) == expected["env_creds_seen"]


def _fail_first_openai_construction(monkeypatch, agent):
    import run_agent

    concrete_openai = run_agent.OpenAI
    attempts = []

    def construct(**kwargs):
        attempts.append(
            {
                "api_key": kwargs.get("api_key"),
                "base_url": str(kwargs.get("base_url") or ""),
                "default_query": deepcopy(kwargs.get("default_query")),
                "http_client": kwargs.get("http_client"),
                "published_client": agent.client,
                "published_api_key": agent.api_key,
                "published_base_url": agent.base_url,
                "published_client_kwargs": deepcopy(agent._client_kwargs),
                "published_pool_entry_id": getattr(
                    agent, "_credential_pool_entry_id", _MISSING
                ),
                "published_env_creds_seen": getattr(
                    agent, "_env_creds_seen", _MISSING
                ),
            }
        )
        if len(attempts) == 1:
            raise RuntimeError("deterministic SDK constructor failure")
        return concrete_openai(**kwargs)

    monkeypatch.setattr(run_agent, "OpenAI", construct)
    return attempts


@pytest.fixture
def env(monkeypatch):
    """Dict-driven stand-in for the .env/os.environ resolution chain."""
    values = {}
    import agent.credential_pool as cp

    monkeypatch.setattr(
        cp, "get_env_prefer_dotenv", lambda key: values.get(key, "")
    )
    return values


class TestAdoptsEnvEdits:
    def test_sealed_route_refreshes_key_without_adopting_env_endpoint(
        self, env, caplog
    ):
        caplog.set_level("INFO")
        agent = _make_agent(provider="gmi", base_url=GMI_BASE)
        agent._execution_route_constraint = _sealed_route_constraint(
            provider="gmi", base_url=GMI_BASE
        )
        env["GMI_API_KEY"] = "sk-new"
        env["GMI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is True
        assert agent.api_key == "sk-new"
        assert agent.base_url == GMI_BASE
        assert agent._client_kwargs == {
            "base_url": GMI_BASE,
            "api_key": "sk-new",
        }
        assert (agent.provider, agent.model, agent.api_mode) == (
            "gmi",
            "test-model",
            "chat_completions",
        )
        assert GMI_BASE not in caplog.text
        assert LOCAL_BASE not in caplog.text

    def test_boot_default_adopts_override_on_first_look(self, env):
        """The reported scenario: worker spawned before the user saved the
        override — first turn after the save must switch to the local URL."""
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"
        env["OPENAI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is True
        assert agent.base_url == LOCAL_BASE
        assert agent._client_kwargs["base_url"] == LOCAL_BASE
        agent._replace_primary_openai_client.assert_called_once_with(
            reason="env_credential_refresh"
        )

    def test_edit_between_turns_is_adopted(self, env):
        """No-op first turn, then the user saves an override → next turn
        rebuilds the client onto the new endpoint."""
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"

        assert agent._try_refresh_env_client_credentials() is False

        env["OPENAI_BASE_URL"] = LOCAL_BASE
        assert agent._try_refresh_env_client_credentials() is True
        assert agent.base_url == LOCAL_BASE

    def test_key_rotation_in_env_is_adopted(self, env):
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"

        assert agent._try_refresh_env_client_credentials() is False

        env["OPENAI_API_KEY"] = "sk-new"
        assert agent._try_refresh_env_client_credentials() is True
        assert agent.api_key == "sk-new"
        assert agent._client_kwargs["api_key"] == "sk-new"


class TestLeavesNonEnvStateAlone:
    def test_unchanged_env_is_a_noop(self, env):
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"

        assert agent._try_refresh_env_client_credentials() is False
        agent._replace_primary_openai_client.assert_not_called()

    def test_pool_rotation_is_not_stomped(self, env):
        """After the pool rotates the session onto a different key, an
        unchanged env must not flap the session back every turn."""
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"
        assert agent._try_refresh_env_client_credentials() is False

        agent.api_key = "sk-rotated-pool-entry"
        assert agent._try_refresh_env_client_credentials() is False
        assert agent.api_key == "sk-rotated-pool-entry"

    def test_custom_endpoint_wins_over_env_edit(self, env):
        """A session running on a config/pool custom endpoint (not the
        registry default, not a previously-seen env value) keeps it."""
        agent = _make_agent(base_url="https://my-proxy.corp.example/v1")
        env["OPENAI_API_KEY"] = "sk-old"
        env["OPENAI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is False
        assert agent.base_url == "https://my-proxy.corp.example/v1"

    def test_skipped_while_failed_over(self, env):
        agent = _make_agent()
        agent._fallback_activated = True
        env["OPENAI_API_KEY"] = "sk-old"
        env["OPENAI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is False

    def test_skipped_for_non_api_key_provider(self, env):
        agent = _make_agent(provider="openai-codex")
        assert agent._try_refresh_env_client_credentials() is False

    def test_skipped_for_non_chat_completions_api_mode(self, env):
        agent = _make_agent()
        agent.api_mode = "anthropic_messages"
        assert agent._try_refresh_env_client_credentials() is False

    def test_skipped_when_no_key_resolves(self, env):
        agent = _make_agent()
        env["OPENAI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is False


class TestFailedRebuildRetries:
    def test_failed_rebuild_rolls_back_and_retries_next_turn(self, env):
        """A failed client rebuild must not advance the edit baseline: the
        agent rolls back to the still-live old client's state and the same
        unchanged edit is retried on the next turn."""
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"
        assert agent._try_refresh_env_client_credentials() is False

        env["OPENAI_BASE_URL"] = LOCAL_BASE
        agent._replace_primary_openai_client.return_value = False
        assert agent._try_refresh_env_client_credentials() is False
        # Rolled back: agent state still matches the old client.
        assert agent.base_url == DEFAULT_BASE
        assert agent.api_key == "sk-old"
        assert agent._client_kwargs == {"base_url": DEFAULT_BASE, "api_key": "sk-old"}

        agent._replace_primary_openai_client.return_value = True
        assert agent._try_refresh_env_client_credentials() is True
        assert agent.base_url == LOCAL_BASE
        assert agent._client_kwargs["base_url"] == LOCAL_BASE


class TestRouteConfigRefresh:
    def test_base_url_change_recomputes_route_tls_and_headers(self, env):
        """Moving to a new endpoint must recompute route-derived TLS material
        and default headers, exactly as credential-pool rotation does."""
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"
        env["OPENAI_BASE_URL"] = LOCAL_BASE

        assert agent._try_refresh_env_client_credentials() is True
        agent._reapply_route_client_config.assert_called_once_with(route_changed=True)

    def test_key_only_change_keeps_route_config(self, env):
        agent = _make_agent()
        env["OPENAI_API_KEY"] = "sk-old"
        assert agent._try_refresh_env_client_credentials() is False

        env["OPENAI_API_KEY"] = "sk-new"
        assert agent._try_refresh_env_client_credentials() is True
        agent._reapply_route_client_config.assert_called_once_with(route_changed=False)


class TestVertexRefresh:
    def test_unsealed_refresh_retains_endpoint_derivation_behavior(self, monkeypatch):
        import agent.vertex_adapter as vertex_adapter

        refreshed_base = (
            "https://europe-west4-aiplatform.googleapis.com/v1beta1/projects/"
            "credential-project/locations/europe-west4/endpoints/openapi"
        )
        with (
            patch("run_agent.get_tool_definitions", return_value=[]),
            patch("run_agent.check_toolset_requirements", return_value={}),
        ):
            agent = AIAgent(
                provider="vertex",
                requested_provider="vertex",
                model="test-model",
                api_mode="chat_completions",
                base_url="https://old-vertex.example/v1",
                api_key="expired-token",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        old_client = agent.client
        monkeypatch.setattr(
            vertex_adapter,
            "get_vertex_config",
            lambda: ("fresh-token", refreshed_base),
        )

        try:
            assert agent._try_refresh_vertex_client_credentials() is True
            assert agent.client is not old_client
            assert agent.client.api_key == "fresh-token"
            assert agent.api_key == "fresh-token"
            assert agent.base_url == refreshed_base
            assert agent._client_kwargs["base_url"] == refreshed_base
        finally:
            agent.client.close()
            old_client.close()


def test_conversation_blocks_sealed_route_mutation_before_turn_setup(monkeypatch):
    import agent.conversation_loop as conversation_loop

    agent = _make_agent(provider="gmi", base_url=GMI_BASE)
    agent._execution_route_constraint = _sealed_route_constraint(
        provider="gmi", base_url=GMI_BASE
    )
    agent._last_compaction_in_place = False
    agent._last_compression_attempt_recorded = False
    agent._last_compression_attempt_in_place = None

    def mutate_route():
        agent.base_url = LOCAL_BASE
        agent._client_kwargs["base_url"] = LOCAL_BASE
        return True

    agent._try_refresh_env_client_credentials = mutate_route
    monkeypatch.setattr(
        conversation_loop,
        "build_turn_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("turn setup reached after sealed route mutation")
        ),
    )

    with pytest.raises(RuntimeError, match="provider_capability_drift"):
        conversation_loop.run_conversation(agent, "hello")


def test_sealed_route_malformed_runtime_endpoint_has_bounded_failure() -> None:
    agent = _make_agent(provider="gmi", base_url=GMI_BASE)
    agent._execution_route_constraint = _sealed_route_constraint(
        provider="gmi", base_url=GMI_BASE
    )
    private_endpoint = "https://[private-endpoint"
    agent._client_kwargs["base_url"] = private_endpoint

    with pytest.raises(ProviderCapabilityDriftError) as caught:
        agent._assert_execution_route_constraint()

    assert str(caught.value) == "provider_capability_drift"
    assert private_endpoint not in str(caught.value)


def test_sealed_codex_refresh_recreates_client_without_endpoint_drift(
    monkeypatch,
) -> None:
    import hermes_cli.auth as auth

    sealed_base = "https://chatgpt.com/backend-api/codex"
    agent = _make_agent(
        provider="openai-codex",
        base_url=sealed_base,
        api_key="old-token",
        api_mode="codex_responses",
    )
    agent._execution_route_constraint = _sealed_route_constraint(
        provider="openai-codex",
        base_url=sealed_base,
        api_mode="codex_responses",
    )

    def resolve_codex_runtime_credentials(**kwargs):
        if kwargs.get("force_refresh"):
            return {"api_key": "new-token", "base_url": LOCAL_BASE}
        return {"api_key": "old-token", "base_url": sealed_base}

    monkeypatch.setattr(
        auth,
        "resolve_codex_runtime_credentials",
        resolve_codex_runtime_credentials,
    )

    assert agent._try_refresh_codex_client_credentials(force=True) is True
    assert agent.api_key == "new-token"
    assert agent.base_url == sealed_base
    assert agent._client_kwargs["base_url"] == sealed_base
    agent._replace_primary_openai_client.assert_called_once_with(
        reason="openai-codex_credential_refresh"
    )


def test_real_agent_init_preserves_sealed_query_endpoint_identity() -> None:
    agent, constraint = _real_sealed_query_agent()
    try:
        assert agent.base_url == (
            "https://tenant.openai.azure.com/openai/deployments/review"
        )
        assert agent._client_kwargs["default_query"] == {
            "api-version": "2025-04-01-preview"
        }
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent.client.close()


def test_real_init_preserves_repeated_approved_query_values() -> None:
    agent, constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    try:
        assert agent.base_url == (
            "https://tenant.openai.azure.com/openai/deployments/review"
        )
        assert agent._client_kwargs["default_query"] == REPEATED_DEFAULT_QUERY
        assert agent.client.default_query == REPEATED_DEFAULT_QUERY
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent.client.close()


def test_real_recreated_client_preserves_repeated_query_and_adopts_env_key(
    env,
) -> None:
    from hermes_cli.runtime_provider import execution_endpoint_sha256

    agent, constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    old_client = agent.client
    env["AZURE_FOUNDRY_API_KEY"] = "rotated-credential"
    env["AZURE_FOUNDRY_BASE_URL"] = (
        "https://attacker.invalid/openai/deployments/review"
        "?deployment=red&api-version=2026-01-01"
    )
    try:
        assert agent._try_refresh_env_client_credentials() is True

        assert agent.api_key == "rotated-credential"
        assert agent.client is not old_client
        assert agent.client.api_key == "rotated-credential"
        assert agent._client_kwargs["default_query"] == REPEATED_DEFAULT_QUERY
        assert agent.client.default_query == REPEATED_DEFAULT_QUERY
        assert execution_endpoint_sha256(
            provider=agent.provider,
            api_mode=agent.api_mode,
            base_url=agent.client.base_url,
            default_query=agent.client.default_query,
        ) == constraint.identity.endpoint_sha256
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent.client.close()
        old_client.close()


def test_failed_drifted_env_recreation_does_not_advance_seen_credentials(
    env, monkeypatch
) -> None:
    agent, _constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    old_client = agent.client
    original_create = agent._create_openai_client
    rejected_clients = []

    def create_drifted(client_kwargs, *, reason, shared):
        drifted_kwargs = dict(client_kwargs)
        drifted_kwargs["default_query"] = {
            "deployment": ("blue", "red"),
            "api-version": "2025-04-01-preview",
        }
        client = original_create(
            drifted_kwargs, reason=reason, shared=shared
        )
        rejected_clients.append(client)
        return client

    monkeypatch.setattr(agent, "_create_openai_client", create_drifted)
    env["AZURE_FOUNDRY_API_KEY"] = "rotated-credential"

    try:
        with pytest.raises(ProviderCapabilityDriftError) as caught:
            agent._try_refresh_env_client_credentials()

        assert caught.value.failure_kind == "provider_capability_drift"
        assert not hasattr(agent, "_env_creds_seen")
        assert agent.api_key == "test-credential"
        assert agent.client is old_client
        assert len(rejected_clients) == 1
    finally:
        for client in rejected_clients:
            client.close()
        old_client.close()


def test_real_sealed_vertex_refresh_preserves_query_without_duplication(
    monkeypatch,
) -> None:
    import agent.vertex_adapter as vertex_adapter
    from hermes_cli.runtime_provider import execution_endpoint_sha256

    agent, constraint = _real_sealed_query_agent(
        provider="vertex",
        endpoint=REPEATED_QUERY_ENDPOINT,
        api_key="expired-token",
    )
    old_client = agent.client
    monkeypatch.setattr(
        vertex_adapter,
        "get_vertex_credentials",
        lambda: ("fresh-token", "credential-project"),
    )
    monkeypatch.setattr(
        vertex_adapter,
        "get_vertex_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("sealed refresh derived a Vertex endpoint")
        ),
    )
    try:
        assert agent._try_refresh_vertex_client_credentials() is True

        assert agent.client is not old_client
        assert agent.api_key == "fresh-token"
        assert agent._client_kwargs["default_query"] == REPEATED_DEFAULT_QUERY
        assert agent.client.default_query == REPEATED_DEFAULT_QUERY
        assert execution_endpoint_sha256(
            provider=agent.provider,
            api_mode=agent.api_mode,
            base_url=agent.client.base_url,
            default_query=agent.client.default_query,
        ) == constraint.identity.endpoint_sha256
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent.client.close()
        old_client.close()


def test_sealed_vertex_constructor_failure_is_atomic_and_retries_same_token(
    monkeypatch,
) -> None:
    import agent.vertex_adapter as vertex_adapter

    agent, constraint = _real_sealed_query_agent(
        provider="vertex",
        endpoint=REPEATED_QUERY_ENDPOINT,
        api_key="expired-token",
    )
    old_client = agent.client
    agent._credential_pool_entry_id = "original-entry"
    agent._env_creds_seen = (constraint.identity.endpoint_sha256, "expired-token")
    original_state = _published_openai_state(agent)
    credential_reads = []

    def get_vertex_credentials():
        credential_reads.append("fresh-token")
        return "fresh-token", "credential-project"

    monkeypatch.setattr(
        vertex_adapter,
        "get_vertex_credentials",
        get_vertex_credentials,
    )
    monkeypatch.setattr(
        vertex_adapter,
        "get_vertex_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("sealed refresh derived a Vertex endpoint")
        ),
    )
    attempts = _fail_first_openai_construction(monkeypatch, agent)

    try:
        assert agent._try_refresh_vertex_client_credentials() is False

        _assert_published_openai_state(agent, original_state)
        assert agent._is_openai_client_closed(old_client) is False
        assert attempts[0]["http_client"].is_closed is True

        assert agent._try_refresh_vertex_client_credentials() is True

        assert credential_reads == ["fresh-token", "fresh-token"]
        assert [attempt["api_key"] for attempt in attempts] == [
            "fresh-token",
            "fresh-token",
        ]
        assert all(
            attempt["published_client"] is old_client
            and attempt["published_api_key"] == original_state["api_key"]
            and attempt["published_base_url"] == original_state["base_url"]
            and attempt["published_client_kwargs"]
            == original_state["client_kwargs"]
            and attempt["published_pool_entry_id"] == "original-entry"
            and attempt["published_env_creds_seen"]
            == original_state["env_creds_seen"]
            for attempt in attempts
        )
        assert agent.client is not old_client
        assert agent.client.api_key == "fresh-token"
        assert agent.api_key == "fresh-token"
        assert agent.base_url == original_state["base_url"]
        assert agent._client_kwargs["default_query"] == REPEATED_DEFAULT_QUERY
        assert agent.client.default_query == REPEATED_DEFAULT_QUERY
        assert agent._credential_pool_entry_id == "original-entry"
        assert agent._env_creds_seen == original_state["env_creds_seen"]
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        if attempts and not attempts[0]["http_client"].is_closed:
            attempts[0]["http_client"].close()
        if agent.client is not old_client:
            agent.client.close()
        old_client.close()


def test_sealed_query_endpoint_rejects_unauthorized_default_query_change() -> None:
    agent, _constraint = _real_sealed_query_agent()
    try:
        agent._client_kwargs["default_query"] = {
            "api-version": "2026-01-01"
        }

        with pytest.raises(ProviderCapabilityDriftError) as caught:
            agent._assert_execution_route_constraint()

        assert str(caught.value) == "provider_capability_drift"
    finally:
        agent.client.close()


def test_sealed_query_endpoint_rejects_actual_sdk_query_disagreement() -> None:
    from openai import OpenAI

    agent, _constraint = _real_sealed_query_agent()
    unauthorized_client = OpenAI(
        api_key="test-credential",
        base_url=(
            "https://tenant.openai.azure.com/openai/deployments/review"
        ),
        default_query={"api-version": "2026-01-01"},
    )
    try:
        with pytest.raises(ProviderCapabilityDriftError) as caught:
            agent._assert_execution_route_constraint(unauthorized_client)

        assert str(caught.value) == "provider_capability_drift"
    finally:
        unauthorized_client.close()
        agent.client.close()


def test_repeated_approved_query_rejects_changed_value_before_transport() -> None:
    from openai import OpenAI

    agent, _constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    unauthorized_client = OpenAI(
        api_key="test-credential",
        base_url=(
            "https://tenant.openai.azure.com/openai/deployments/review"
        ),
        default_query={
            "deployment": ("blue", "red"),
            "api-version": "2025-04-01-preview",
        },
    )
    try:
        with pytest.raises(ProviderCapabilityDriftError) as caught:
            agent._assert_execution_route_constraint(unauthorized_client)

        assert str(caught.value) == "provider_capability_drift"
    finally:
        unauthorized_client.close()
        agent.client.close()


def test_sealed_pool_constructor_failure_is_atomic_and_retries_same_entry(
    monkeypatch,
) -> None:
    agent, constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    old_client = agent.client
    agent._credential_pool_entry_id = "original-entry"
    agent._env_creds_seen = (constraint.identity.endpoint_sha256, "test-credential")
    original_state = _published_openai_state(agent)
    entry = SimpleNamespace(
        id="rotated",
        runtime_api_key="rotated-credential",
        runtime_base_url=(
            "https://attacker.invalid/openai/deployments/review"
            "?api-version=2026-01-01"
        ),
    )
    attempted_entries = []

    class RetrySameCandidatePool:
        provider = "azure-foundry"

        @staticmethod
        def current():
            return SimpleNamespace(
                id="original-entry",
                runtime_api_key="test-credential",
            )

        @staticmethod
        def entries():
            return []

        @staticmethod
        def try_refresh_matching(**_kwargs):
            attempted_entries.append(entry.id)
            return entry

    agent._credential_pool = RetrySameCandidatePool()
    attempts = _fail_first_openai_construction(monkeypatch, agent)
    recovery_state = agent._begin_credential_recovery_turn()

    try:
        recovered, retry_same = agent._recover_with_credential_pool(
            status_code=401,
            has_retried_429=False,
            credential_recovery_state=recovery_state,
        )

        assert recovered is True
        assert retry_same is False
        assert attempts[0]["http_client"].is_closed is True
        assert attempted_entries == ["rotated"]
        assert [attempt["api_key"] for attempt in attempts] == [
            "rotated-credential",
            "rotated-credential",
        ]
        assert all(
            attempt["published_client"] is old_client
            and attempt["published_api_key"] == original_state["api_key"]
            and attempt["published_base_url"] == original_state["base_url"]
            and attempt["published_client_kwargs"]
            == original_state["client_kwargs"]
            and attempt["published_pool_entry_id"] == "original-entry"
            and attempt["published_env_creds_seen"]
            == original_state["env_creds_seen"]
            for attempt in attempts
        )
        assert agent.client is not old_client
        assert agent.client.api_key == "rotated-credential"
        assert agent.api_key == "rotated-credential"
        assert agent.base_url == (
            "https://tenant.openai.azure.com/openai/deployments/review"
        )
        assert agent._client_kwargs["base_url"] == agent.base_url
        assert agent._client_kwargs["default_query"] == REPEATED_DEFAULT_QUERY
        assert agent.client.default_query == REPEATED_DEFAULT_QUERY
        assert agent._credential_pool_entry_id == "rotated"
        assert agent._env_creds_seen == original_state["env_creds_seen"]
        assert recovery_state.auth_pool_refresh_counts == {
            ("azure-foundry", "rotated"): 1
        }
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        if attempts and not attempts[0]["http_client"].is_closed:
            attempts[0]["http_client"].close()
        if agent.client is not old_client:
            agent.client.close()
        old_client.close()


def test_sealed_pool_constructor_failure_retries_same_real_oauth_token_once(
    tmp_path, monkeypatch
) -> None:
    import run_agent
    from agent.credential_pool import (
        AUTH_TYPE_OAUTH,
        CredentialPool,
        PooledCredential,
    )

    endpoint = "https://route.test/v1"
    expired_token = "expired-oauth-token"
    fresh_token = "fresh-oauth-token"
    entry = PooledCredential(
        provider="anthropic",
        id="oauth-entry",
        label="OAuth entry",
        auth_type=AUTH_TYPE_OAUTH,
        priority=0,
        source="manual:test",
        access_token=expired_token,
        refresh_token="refresh-token",
        base_url=endpoint,
    )
    pool = CredentialPool("anthropic", [entry])
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="anthropic",
        endpoint=endpoint,
        api_key=expired_token,
        credential_pool=pool,
    )
    first_client = agent.client
    oauth_refresh_calls: list[str] = []

    def refresh_oauth(refresh_token, *, use_json=False):
        oauth_refresh_calls.append(refresh_token)
        assert use_json is False
        return {
            "access_token": fresh_token,
            "refresh_token": "rotated-refresh-token",
            "expires_at_ms": 9_999_999_999_000,
        }

    monkeypatch.setattr(
        "agent.anthropic_adapter.refresh_anthropic_oauth_pure",
        refresh_oauth,
    )
    barrier = _LowestConstructorBarrier(
        run_agent.OpenAI,
        agent=agent,
        expired_token=expired_token,
        fresh_token=fresh_token,
        failures=1,
    )
    monkeypatch.setattr(run_agent, "OpenAI", barrier)

    try:
        result = _drive_provider_error(agent)

        assert oauth_refresh_calls == ["refresh-token"]
        assert barrier.constructor_tokens == [fresh_token, fresh_token]
        assert result.get("failed") is not True
        assert agent.client is not first_client
    finally:
        agent.close()
        if not first_client.is_closed():
            first_client.close()


def test_sealed_vertex_constructor_failure_reads_credentials_once(
    tmp_path, monkeypatch
) -> None:
    import run_agent

    endpoint = REPEATED_QUERY_ENDPOINT
    expired_token = "expired-vertex-token"
    fresh_token = "fresh-vertex-token"
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="vertex",
        endpoint=endpoint,
        api_key=expired_token,
    )
    first_client = agent.client
    vertex_credential_reads: list[bool] = []

    def get_vertex_credentials():
        vertex_credential_reads.append(True)
        return fresh_token, "credential-project"

    monkeypatch.setattr(
        "agent.vertex_adapter.get_vertex_credentials",
        get_vertex_credentials,
    )
    barrier = _LowestConstructorBarrier(
        run_agent.OpenAI,
        agent=agent,
        expired_token=expired_token,
        fresh_token=fresh_token,
        failures=1,
    )
    monkeypatch.setattr(run_agent, "OpenAI", barrier)

    try:
        result = _drive_provider_error(agent)

        assert vertex_credential_reads == [True]
        assert barrier.constructor_tokens == [fresh_token, fresh_token]
        assert result.get("failed") is not True
        assert agent.client is not first_client
    finally:
        agent.close()
        if not first_client.is_closed():
            first_client.close()


def test_sealed_pool_two_adoption_failures_clear_candidate(
    tmp_path, monkeypatch
) -> None:
    import run_agent
    from agent.credential_pool import (
        AUTH_TYPE_OAUTH,
        CredentialPool,
        PooledCredential,
    )

    endpoint = "https://route.test/v1"
    expired_token = "expired-oauth-token"
    fresh_token = "fresh-oauth-token"
    entry = PooledCredential(
        provider="anthropic",
        id="oauth-entry",
        label="OAuth entry",
        auth_type=AUTH_TYPE_OAUTH,
        priority=0,
        source="manual:test",
        access_token=expired_token,
        refresh_token="refresh-token",
        base_url=endpoint,
    )
    pool = CredentialPool("anthropic", [entry])
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="anthropic",
        endpoint=endpoint,
        api_key=expired_token,
        credential_pool=pool,
    )
    oauth_refresh_calls: list[str] = []
    monkeypatch.setattr(
        "agent.anthropic_adapter.refresh_anthropic_oauth_pure",
        lambda refresh_token, **_kwargs: oauth_refresh_calls.append(refresh_token)
        or {
            "access_token": fresh_token,
            "refresh_token": "rotated-refresh-token",
            "expires_at_ms": 9_999_999_999_000,
        },
    )
    barrier = _LowestConstructorBarrier(
        run_agent.OpenAI,
        agent=agent,
        expired_token=expired_token,
        fresh_token=fresh_token,
        failures=2,
    )
    monkeypatch.setattr(run_agent, "OpenAI", barrier)

    try:
        result = _drive_provider_error(agent)

        assert result.get("failed") is True
        assert oauth_refresh_calls == ["refresh-token"]
        assert barrier.constructor_tokens == [fresh_token, fresh_token]
        assert getattr(agent, "_pending_sealed_credential_adoption", None) is None
    finally:
        agent.close()


def _assert_sealed_vertex_acquisition_failure_is_bounded(
    outcome, tmp_path, monkeypatch
) -> None:
    import run_agent

    endpoint = REPEATED_QUERY_ENDPOINT
    expired_token = "expired-vertex-token"
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="vertex",
        endpoint=endpoint,
        api_key=expired_token,
    )
    credential_reads: list[bool] = []

    def get_vertex_credentials():
        credential_reads.append(True)
        if outcome == "error":
            raise RuntimeError("credential source failed")
        return "", "credential-project"

    monkeypatch.setattr(
        "agent.vertex_adapter.get_vertex_credentials",
        get_vertex_credentials,
    )
    barrier = _LowestConstructorBarrier(
        run_agent.OpenAI,
        agent=agent,
        expired_token=expired_token,
        fresh_token="unused-fresh-token",
        failures=0,
    )
    monkeypatch.setattr(run_agent, "OpenAI", barrier)

    try:
        result = _drive_provider_error(agent)

        assert result.get("failed") is True
        assert credential_reads == [True]
        assert barrier.constructor_tokens == []
    finally:
        agent.close()


def test_sealed_vertex_acquisition_empty_is_bounded(tmp_path, monkeypatch) -> None:
    _assert_sealed_vertex_acquisition_failure_is_bounded(
        "empty", tmp_path, monkeypatch
    )


def test_sealed_vertex_acquisition_error_is_bounded(tmp_path, monkeypatch) -> None:
    _assert_sealed_vertex_acquisition_failure_is_bounded(
        "error", tmp_path, monkeypatch
    )


def _prepare_pending_pool_recovery(
    monkeypatch,
    *,
    failures: int,
    pause_before_second: bool = False,
    endpoint: str = REPEATED_QUERY_ENDPOINT,
    before_candidate_return=None,
    failure_message: str = "deterministic SDK constructor failure",
):
    import run_agent

    expired_token = "expired-pool-token"
    fresh_token = "fresh-pool-token"
    agent, constraint = _build_real_sealed_openai_agent(
        provider="azure-foundry",
        endpoint=endpoint,
        api_key=expired_token,
    )
    current = SimpleNamespace(
        id="current-entry",
        runtime_api_key=expired_token,
        last_status=None,
    )
    candidate = SimpleNamespace(
        id="candidate-entry",
        runtime_api_key=fresh_token,
        runtime_base_url=endpoint,
        last_status=None,
    )
    source_calls: list[str] = []

    class _Pool:
        provider = "azure-foundry"

        @staticmethod
        def current():
            return current

        @staticmethod
        def entries():
            return [current, candidate]

        @staticmethod
        def try_refresh_matching(**_kwargs):
            source_calls.append("pool")
            return candidate

    agent._credential_pool = _Pool()
    agent._credential_pool_entry_id = current.id
    recovery_state = agent._begin_credential_recovery_turn()
    barrier = _LowestConstructorBarrier(
        run_agent.OpenAI,
        agent=agent,
        expired_token=expired_token,
        fresh_token=fresh_token,
        failures=failures,
        pause_before_second=pause_before_second,
        before_candidate_return=before_candidate_return,
        failure_message=failure_message,
    )
    monkeypatch.setattr(run_agent, "OpenAI", barrier)
    return agent, constraint, current, candidate, recovery_state, barrier, source_calls


def test_sealed_pool_ordinary_429_markers_change_only_after_adoption(
    monkeypatch,
) -> None:
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=2)
    agent._credential_pool.mark_exhausted_and_rotate = MagicMock(
        return_value=candidate
    )
    try:
        recovered, marker = agent._recover_with_credential_pool(
            status_code=429,
            has_retried_429=True,
            credential_recovery_state=recovery_state,
        )

        assert recovered is False
        assert marker is True
        assert barrier.constructor_tokens == [
            "fresh-pool-token",
            "fresh-pool-token",
        ]
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_sealed_pool_preexhausted_429_markers_change_only_after_adoption(
    monkeypatch,
) -> None:
    (
        agent,
        _constraint,
        current,
        candidate,
        recovery_state,
        barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=2)
    from agent.credential_pool import STATUS_EXHAUSTED

    current.last_status = STATUS_EXHAUSTED
    agent._credential_pool.mark_exhausted_and_rotate = MagicMock(
        return_value=candidate
    )
    try:
        recovered, marker = agent._recover_with_credential_pool(
            status_code=429,
            has_retried_429=False,
            credential_recovery_state=recovery_state,
        )

        assert recovered is False
        assert marker is False
        assert barrier.constructor_tokens == [
            "fresh-pool-token",
            "fresh-pool-token",
        ]
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_pending_candidate_accepts_reordered_repeated_query_identity(
    monkeypatch,
) -> None:
    reordered = (
        "https://tenant.openai.azure.com/openai/deployments/review"
        "?deployment=green&api-version=2025-04-01-preview&deployment=blue"
    )
    (
        agent,
        constraint,
        _current,
        candidate,
        recovery_state,
        barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=0)
    candidate.runtime_base_url = reordered
    try:
        assert agent._swap_credential(
            candidate,
            credential_recovery_state=recovery_state,
        ) is True
        assert barrier.constructor_tokens == ["fresh-pool-token"]
        assert agent._assert_execution_route_constraint(agent.client) is constraint
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


class _LockOrderProbe:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.calls = 0

    def __call__(self, *_args, **_kwargs) -> None:
        self.calls += 1
        owned = getattr(self.agent._openai_client_lock(), "_is_owned", lambda: False)
        assert owned() is False


def test_sealed_openai_retirement_starts_after_client_lock_release(
    monkeypatch,
) -> None:
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        _barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=0)
    probe = _LockOrderProbe(agent)
    monkeypatch.setattr(agent, "_retire_shared_openai_client", probe)
    try:
        assert agent._swap_credential(
            candidate,
            credential_recovery_state=recovery_state,
        ) is True
        assert probe.calls == 1
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


@pytest.mark.parametrize("outcome", ["failed", "invalidated"])
def test_sealed_openai_failed_or_invalidated_adoption_never_retires_live_client(
    outcome, monkeypatch
) -> None:
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        _barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(
        monkeypatch, failures=2 if outcome == "failed" else 0
    )
    old_client = agent.client
    retirements: list[object] = []
    monkeypatch.setattr(
        agent,
        "_retire_shared_openai_client",
        lambda client, **_kwargs: retirements.append(client),
    )
    if outcome == "invalidated":
        agent._interrupt_requested = True
    try:
        assert agent._swap_credential(
            candidate,
            credential_recovery_state=recovery_state,
        ) is False
        assert retirements == []
        assert agent.client is old_client
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_sealed_candidate_build_does_not_consume_provider_or_budget_ledgers(
    monkeypatch,
) -> None:
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        _barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=1)
    provider_attempts: list[bool] = []
    cost_attempts: list[bool] = []
    agent._provider_attempt_reservation_callback = lambda: provider_attempts.append(True)
    agent._cost_budget_acquire_callback = lambda: cost_attempts.append(True)
    budget_before = agent.iteration_budget.remaining
    try:
        assert agent._swap_credential(
            candidate,
            credential_recovery_state=recovery_state,
        ) is True
        assert provider_attempts == []
        assert cost_attempts == []
        assert agent.iteration_budget.remaining == budget_before
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


@pytest.mark.parametrize("later_source", ["vertex", "anthropic", "nous", "codex"])
def test_pending_pool_candidate_precedes_every_later_source(
    later_source,
    monkeypatch,
) -> None:
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        barrier,
        source_calls,
    ) = _prepare_pending_pool_recovery(monkeypatch, failures=1)
    later_source_calls: list[str] = []
    try:
        recovered, _marker = agent._recover_with_credential_pool(
            status_code=401,
            has_retried_429=False,
            credential_recovery_state=recovery_state,
        )
        if not recovered:
            later_source_calls.append(later_source)

        assert recovered is True
        assert source_calls == ["pool"]
        assert barrier.constructor_tokens == [
            "fresh-pool-token",
            "fresh-pool-token",
        ]
        assert later_source_calls == []
        assert agent._credential_pool_entry_id == candidate.id
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def _assert_sealed_candidate_canaries_absent(caplog, public_result, canaries):
    combined = caplog.text + json.dumps(public_result, sort_keys=True)
    for canary in canaries:
        assert canary not in combined


def test_sealed_candidate_redaction_second_adoption_failure_keeps_live_client(
    monkeypatch,
    caplog,
) -> None:
    canaries = [
        "TOKEN_CANARY_RETRY",
        "ENDPOINT_CANARY_RETRY",
        "EXCEPTION_CANARY_RETRY",
        "DIGEST_CANARY_RETRY",
        "/tmp/PATH_CANARY_RETRY",
    ]
    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        _barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(
        monkeypatch,
        failures=2,
        failure_message=" ".join(canaries),
    )
    old_client = agent.client
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    try:
        adopted = agent._swap_credential(
            candidate,
            credential_recovery_state=recovery_state,
        )
        public_result = {
            "adopted": adopted,
            "client_unchanged": agent.client is old_client,
        }

        assert adopted is False
        assert agent.client is old_client
        assert old_client.is_closed() is False
        _assert_sealed_candidate_canaries_absent(
            caplog, public_result, canaries
        )
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_sealed_candidate_redaction_nested_create_failure_closes_owned_transport(
    monkeypatch,
    caplog,
) -> None:
    import run_agent

    canaries = [
        "TOKEN_CANARY_CREATE",
        "ENDPOINT_CANARY_CREATE",
        "EXCEPTION_CANARY_CREATE",
        "DIGEST_CANARY_CREATE",
        "/tmp/PATH_CANARY_CREATE",
    ]
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="azure-foundry",
        endpoint=REPEATED_QUERY_ENDPOINT,
        api_key="published-key",
    )

    class _OwnedTransport:
        closed = False

        def close(self):
            self.closed = True
            raise RuntimeError(" ".join(canaries))

    owned_transport = _OwnedTransport()
    monkeypatch.setattr(
        agent,
        "_build_keepalive_http_client",
        lambda *_args, **_kwargs: owned_transport,
    )
    monkeypatch.setattr(
        run_agent,
        "OpenAI",
        MagicMock(side_effect=RuntimeError(" ".join(canaries))),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    public_result = {}
    try:
        with pytest.raises(RuntimeError):
            agent._create_openai_client(
                {
                    "api_key": canaries[0],
                    "base_url": "https://route.test/v1",
                },
                reason="sealed_credential_adoption",
                shared=True,
                candidate_safe=True,
            )
        public_result = {"error_type": "RuntimeError"}

        assert owned_transport.closed is True
        _assert_sealed_candidate_canaries_absent(
            caplog, public_result, canaries
        )
    finally:
        agent.close()


def test_sealed_candidate_redaction_constructor_leaves_caller_transport_owned(
    monkeypatch,
    caplog,
) -> None:
    import run_agent

    canaries = ["TOKEN_CANARY_CALLER", "EXCEPTION_CANARY_CALLER"]
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="azure-foundry",
        endpoint=REPEATED_QUERY_ENDPOINT,
        api_key="published-key",
    )

    class _CallerTransport:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    caller_transport = _CallerTransport()
    monkeypatch.setattr(
        run_agent,
        "OpenAI",
        MagicMock(side_effect=RuntimeError(" ".join(canaries))),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    try:
        with pytest.raises(RuntimeError):
            agent._create_openai_client(
                {
                    "api_key": canaries[0],
                    "base_url": "https://route.test/v1",
                    "http_client": caller_transport,
                },
                reason="sealed_credential_adoption",
                shared=True,
                candidate_safe=True,
            )

        assert caller_transport.close_calls == 0
        _assert_sealed_candidate_canaries_absent(
            caplog, {"error_type": "RuntimeError"}, canaries
        )
    finally:
        agent.close()


def test_sealed_candidate_redaction_nested_close_and_retirement_failures(
    monkeypatch,
    caplog,
) -> None:
    canaries = [
        "ENDPOINT_CANARY_CLOSE",
        "EXCEPTION_CANARY_CLOSE",
        "DIGEST_CANARY_CLOSE",
        "/tmp/PATH_CANARY_CLOSE",
    ]
    agent, _constraint = _build_real_sealed_openai_agent(
        provider="azure-foundry",
        endpoint=REPEATED_QUERY_ENDPOINT,
        api_key="published-key",
    )

    class _RejectedClient:
        close_calls = 0

        def close(self):
            self.close_calls += 1
            raise RuntimeError(" ".join(canaries))

    rejected = _RejectedClient()
    agent.base_url = "https://route.test/ENDPOINT_CANARY_CLOSE"
    monkeypatch.setattr(agent, "_force_close_tcp_sockets", lambda _client: 0)
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    agent._close_openai_client(
        rejected,
        reason="rejected:sealed_build_failure",
        shared=False,
        candidate_safe=True,
    )
    monkeypatch.setattr(
        agent,
        "_force_close_tcp_sockets",
        MagicMock(side_effect=RuntimeError(" ".join(canaries))),
    )
    agent._retire_shared_openai_client(
        rejected,
        reason="replace:sealed_credential_adoption",
        candidate_safe=True,
    )

    try:
        assert rejected.close_calls == 1
        _assert_sealed_candidate_canaries_absent(
            caplog, {"retirement": "best_effort"}, canaries
        )
    finally:
        agent.base_url = REPEATED_QUERY_ENDPOINT
        monkeypatch.setattr(agent, "_force_close_tcp_sockets", lambda _client: 0)
        agent.close()


def test_sealed_candidate_redaction_route_drift_closes_rejected_client(
    monkeypatch,
    caplog,
) -> None:
    canaries = [
        "ENDPOINT_CANARY_DRIFT",
        "DIGEST_CANARY_DRIFT",
        "/tmp/PATH_CANARY_DRIFT",
    ]
    agent_ref: dict[str, object] = {}

    def drift_route():
        agent_ref["agent"].base_url = (
            "https://drift.invalid/tmp/PATH_CANARY_DRIFT"
            "?api-version=ENDPOINT_CANARY_DRIFT"
            "&deployment=DIGEST_CANARY_DRIFT"
        )

    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(
        monkeypatch,
        failures=0,
        before_candidate_return=drift_route,
    )
    agent_ref["agent"] = agent
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    try:
        with pytest.raises(ProviderCapabilityDriftError):
            agent._swap_credential(
                candidate,
                credential_recovery_state=recovery_state,
            )
        assert len(barrier.clients) == 1
        assert barrier.clients[0].is_closed() is True
        _assert_sealed_candidate_canaries_absent(
            caplog,
            {"error": "provider_capability_drift"},
            canaries,
        )
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_sealed_candidate_route_drift_is_terminal_without_fallback(
    monkeypatch,
) -> None:
    agent_ref: dict[str, object] = {}

    def drift_route():
        agent_ref["agent"].base_url = "https://drift.invalid/v1"

    (
        agent,
        _constraint,
        _current,
        candidate,
        recovery_state,
        _barrier,
        _source_calls,
    ) = _prepare_pending_pool_recovery(
        monkeypatch,
        failures=0,
        before_candidate_return=drift_route,
    )
    agent_ref["agent"] = agent
    fallback_calls: list[bool] = []
    monkeypatch.setattr(
        agent,
        "_try_activate_fallback",
        lambda *_args, **_kwargs: fallback_calls.append(True) or True,
    )
    try:
        with pytest.raises(ProviderCapabilityDriftError):
            agent._swap_credential(
                candidate,
                credential_recovery_state=recovery_state,
            )
        assert fallback_calls == []
        assert getattr(agent, "_pending_sealed_credential_adoption", None) is None
    finally:
        agent._end_credential_recovery_turn(recovery_state.generation)
        agent.close()


def test_drifted_concrete_replacement_is_closed_and_never_published() -> None:
    import httpx

    agent, _constraint = _real_sealed_query_agent(
        endpoint=REPEATED_QUERY_ENDPOINT
    )
    old_client = agent.client
    agent._credential_pool_entry_id = "original-entry"
    original_state = _published_openai_state(agent)
    candidate_http_client = httpx.Client()
    candidate_kwargs = deepcopy(agent._client_kwargs)
    candidate_kwargs.update(
        {
            "api_key": "private-candidate-token",
            "default_query": {
                "deployment": ("blue", "red"),
                "api-version": "2025-04-01-preview",
            },
            "http_client": candidate_http_client,
        }
    )

    try:
        with pytest.raises(ProviderCapabilityDriftError) as caught:
            agent._replace_primary_openai_client(
                reason="drifted_candidate_test",
                client_kwargs=candidate_kwargs,
                api_key="private-candidate-token",
                base_url=agent.base_url,
                credential_pool_entry_id="candidate-entry",
            )

        assert str(caught.value) == "provider_capability_drift"
        assert "private-candidate-token" not in str(caught.value)
        assert REPEATED_QUERY_ENDPOINT not in str(caught.value)
        _assert_published_openai_state(agent, original_state)
        assert candidate_http_client.is_closed is True
        assert agent._is_openai_client_closed(old_client) is False
    finally:
        if not candidate_http_client.is_closed:
            candidate_http_client.close()
        old_client.close()


def test_conversation_route_mutation_at_reservation_is_terminal(
    monkeypatch,
) -> None:
    endpoint = "https://api.gmi-serving.com/v1"
    constraint = _sealed_route_constraint(provider="gmi", base_url=endpoint)
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            provider="gmi",
            requested_provider="gmi",
            model="test-model",
            api_mode="chat_completions",
            base_url=endpoint,
            api_key="test-credential",
            execution_route_constraint=constraint,
            fallback_model={"provider": "openrouter", "model": "fallback"},
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent._cached_system_prompt = "You are helpful."
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._disable_streaming = True
    agent._try_refresh_env_client_credentials = lambda: False
    transport_calls: list[bool] = []
    request_client_builds: list[bool] = []
    fallback_calls: list[bool] = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=None,
    )
    request_client = SimpleNamespace(
        base_url=endpoint,
        default_query={},
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: transport_calls.append(True) or response
            )
        ),
    )

    def mutate_after_turn_checks(*, reason, api_kwargs=None):
        request_client_builds.append(True)
        agent._client_kwargs["base_url"] = "https://changed.invalid/v1"
        return request_client

    agent._create_request_openai_client = mutate_after_turn_checks
    agent._try_activate_fallback = (
        lambda *args, **kwargs: fallback_calls.append(True) or True
    )
    monkeypatch.setattr(agent, "_persist_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_save_trajectory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent, "_cleanup_task_resources", lambda *_args, **_kwargs: None
    )

    with pytest.raises(ProviderCapabilityDriftError) as caught:
        agent.run_conversation("hello")

    assert str(caught.value) == "provider_capability_drift"
    assert request_client_builds == [True]
    assert transport_calls == []
    assert fallback_calls == []


CUSTOM_BASE = "https://api.longcat.example/openai/v1"


@pytest.fixture
def named_custom_provider(monkeypatch):
    """Register a named custom provider (config `providers.longcat` block)."""
    block = {"name": "longcat", "base_url": CUSTOM_BASE, "key_env": "LONGCAT_API_KEY"}
    import hermes_cli.runtime_provider as rp

    monkeypatch.setattr(
        rp,
        "_get_named_custom_provider",
        lambda requested: block if requested == "longcat" else None,
    )
    return block


class TestNamedCustomProviders:
    """#67935: named custom providers resolve to provider="custom" with no
    PROVIDER_REGISTRY entry — their `key_env` credential must refresh too."""

    def _make_custom_agent(self, *, api_key="no-key-required"):
        agent = _make_agent(provider="custom", base_url=CUSTOM_BASE, api_key=api_key)
        agent.requested_provider = "longcat"
        return agent

    def test_key_added_mid_session_is_adopted(self, env, named_custom_provider):
        """The #67935 repro: long-lived worker spawned before the key_env var
        was written to .env — the first turn after the save must pick it up."""
        agent = self._make_custom_agent()
        env["LONGCAT_API_KEY"] = "lc-fresh"

        assert agent._try_refresh_env_client_credentials() is True
        assert agent.api_key == "lc-fresh"
        assert agent._client_kwargs["api_key"] == "lc-fresh"
        agent._replace_primary_openai_client.assert_called_once_with(
            reason="env_credential_refresh"
        )

    def test_key_rotation_between_turns_is_adopted(self, env, named_custom_provider):
        agent = self._make_custom_agent(api_key="lc-old")
        env["LONGCAT_API_KEY"] = "lc-old"
        assert agent._try_refresh_env_client_credentials() is False

        env["LONGCAT_API_KEY"] = "lc-new"
        assert agent._try_refresh_env_client_credentials() is True
        assert agent.api_key == "lc-new"

    def test_unchanged_env_is_a_noop(self, env, named_custom_provider):
        agent = self._make_custom_agent(api_key="lc-old")
        env["LONGCAT_API_KEY"] = "lc-old"

        assert agent._try_refresh_env_client_credentials() is False
        agent._replace_primary_openai_client.assert_not_called()

    def test_skipped_without_key_env(self, env, named_custom_provider):
        """Inline `api_key` / pool-backed entries have no env-sourced
        credential to watch."""
        named_custom_provider.pop("key_env")
        agent = self._make_custom_agent()
        env["LONGCAT_API_KEY"] = "lc-fresh"

        assert agent._try_refresh_env_client_credentials() is False

    def test_skipped_for_unknown_custom_provider(self, env, named_custom_provider):
        agent = self._make_custom_agent()
        agent.requested_provider = "someone-else"
        env["LONGCAT_API_KEY"] = "lc-fresh"

        assert agent._try_refresh_env_client_credentials() is False
