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

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    def test_sealed_route_refreshes_token_without_deriving_endpoint(
        self, monkeypatch
    ):
        import agent.vertex_adapter as vertex_adapter

        sealed_base = (
            "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/"
            "config-project/locations/us-central1/endpoints/openapi"
        )
        agent = _make_agent(
            provider="vertex",
            base_url=sealed_base,
            api_key="expired-token",
        )
        agent._execution_route_constraint = _sealed_route_constraint(
            provider="vertex",
            base_url=sealed_base,
        )
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

        assert agent._try_refresh_vertex_client_credentials() is True
        assert agent.api_key == "fresh-token"
        assert agent.base_url == sealed_base
        assert agent._client_kwargs == {
            "base_url": sealed_base,
            "api_key": "fresh-token",
        }
        assert (agent.provider, agent.model, agent.api_mode) == (
            "vertex",
            "test-model",
            "chat_completions",
        )

    def test_unsealed_refresh_retains_endpoint_derivation_behavior(self, monkeypatch):
        import agent.vertex_adapter as vertex_adapter

        refreshed_base = (
            "https://europe-west4-aiplatform.googleapis.com/v1beta1/projects/"
            "credential-project/locations/europe-west4/endpoints/openapi"
        )
        agent = _make_agent(
            provider="vertex",
            base_url="https://old-vertex.example/v1",
            api_key="expired-token",
        )
        monkeypatch.setattr(
            vertex_adapter,
            "get_vertex_config",
            lambda: ("fresh-token", refreshed_base),
        )

        assert agent._try_refresh_vertex_client_credentials() is True
        assert agent.api_key == "fresh-token"
        assert agent.base_url == refreshed_base
        assert agent._client_kwargs["base_url"] == refreshed_base


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


def test_sealed_credential_rotation_updates_key_without_endpoint() -> None:
    agent = _make_agent(provider="gmi", base_url=GMI_BASE)
    agent._execution_route_constraint = _sealed_route_constraint(
        provider="gmi", base_url=GMI_BASE
    )
    entry = SimpleNamespace(
        id="rotated",
        runtime_api_key="sk-rotated",
        runtime_base_url=LOCAL_BASE,
    )

    agent._swap_credential(entry)

    assert agent.api_key == "sk-rotated"
    assert agent.base_url == GMI_BASE
    assert agent._client_kwargs["base_url"] == GMI_BASE
    agent._replace_primary_openai_client.assert_called_once_with(
        reason="credential_rotation"
    )


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


def test_sealed_query_credential_rotation_keeps_split_transport_endpoint() -> None:
    agent, constraint = _real_sealed_query_agent()
    old_client = agent.client
    agent._replace_primary_openai_client = MagicMock(return_value=True)
    entry = SimpleNamespace(
        id="rotated",
        runtime_api_key="rotated-credential",
        runtime_base_url=(
            "https://attacker.invalid/openai/deployments/review"
            "?api-version=2026-01-01"
        ),
    )
    try:
        agent._swap_credential(entry)

        assert agent.api_key == "rotated-credential"
        assert agent.base_url == (
            "https://tenant.openai.azure.com/openai/deployments/review"
        )
        assert agent._client_kwargs["base_url"] == agent.base_url
        assert agent._client_kwargs["default_query"] == {
            "api-version": "2025-04-01-preview"
        }
        assert agent._assert_execution_route_constraint() is constraint
    finally:
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
