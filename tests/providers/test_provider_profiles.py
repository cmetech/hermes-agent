"""Tests for the provider module registry and profiles."""

import pytest

from agent.structured_output import StructuredOutputStrategy
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    classify_execution_runtime,
    resolve_structured_output_capability,
    snapshot_configured_execution_routes,
)
from providers import get_provider_profile, _REGISTRY
from providers.base import ProviderProfile, OMIT_TEMPERATURE


class TestRegistry:
    def test_discovery_populates_registry(self):
        p = get_provider_profile("nvidia")
        assert p is not None
        assert p.name == "nvidia"





class TestNvidiaProfile:
    def test_max_tokens(self):
        p = get_provider_profile("nvidia")
        assert p.default_max_tokens == 16384


    def test_base_url(self):
        p = get_provider_profile("nvidia")
        assert "nvidia.com" in p.base_url



class TestKimiProfile:
    def test_temperature_omit(self):
        p = get_provider_profile("kimi")
        assert p.fixed_temperature is OMIT_TEMPERATURE




    def test_thinking_enabled(self):
        # xor contract (fix ce4e74b3): an explicit recognized effort sends
        # reasoning_effort ONLY — never paired with extra_body.thinking.
        p = get_provider_profile("kimi")
        eb, tl = p.build_api_kwargs_extras(reasoning_config={"enabled": True, "effort": "high"})
        assert tl["reasoning_effort"] == "high"
        assert "thinking" not in eb





class TestOpenRouterProfile:
    def test_extra_body_with_prefs(self):
        p = get_provider_profile("openrouter")
        body = p.build_extra_body(provider_preferences={"allow": ["anthropic"]})
        assert body["provider"] == {"allow": ["anthropic"]}





    def test_pareto_min_coding_score_emitted_for_pareto_model(self):
        """min_coding_score → plugins block when model is openrouter/pareto-code."""
        p = get_provider_profile("openrouter")
        body = p.build_extra_body(
            model="openrouter/pareto-code",
            openrouter_min_coding_score=0.65,
        )
        assert body["plugins"] == [
            {"id": "pareto-router", "min_coding_score": 0.65}
        ]











    def test_grok_session_id_sets_cache_affinity_header(self):
        """OpenRouter + Grok model + session_id => x-grok-conv-id header."""
        p = get_provider_profile("openrouter")
        _, tl = p.build_api_kwargs_extras(
            model="x-ai/grok-4",
            session_id="sess-abc123",
        )
        assert tl["extra_headers"]["x-grok-conv-id"] == "sess-abc123"





    # --- reasoning-mandatory Anthropic effort → top-level verbosity (#43432) ---
    #
    # These models (Claude 4.6+ / fable / mythos-class) ignore
    # ``reasoning.effort`` and use adaptive thinking. OpenRouter honors the
    # requested effort on the top-level ``verbosity`` field instead (maps to
    # Anthropic ``output_config.effort``). The profile must route the existing
    # ``reasoning_config["effort"]`` there while still NEVER emitting a
    # ``reasoning`` field (which would 400 — see #42991). Gate every fixture on
    # the real predicate so this stays a behavior contract, not a name snapshot.

    @staticmethod
    def _is_mandatory(model):
        import inspect
        p = get_provider_profile("openrouter")
        mod = inspect.getmodule(type(p))
        return mod._anthropic_reasoning_is_mandatory(model)






    def test_mandatory_anthropic_verbosity_coexists_with_grok_header(self):
        """A reasoning-mandatory Anthropic model is never a Grok model, but the
        top-level dict must remain a single merged dict — verify the verbosity
        path doesn't clobber the extra_headers slot used by Grok affinity."""
        p = get_provider_profile("openrouter")
        # mandatory anthropic + effort → verbosity, no extra_headers
        _, tl = p.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "high"},
            supports_reasoning=True,
            model="anthropic/claude-fable-5",
        )
        assert tl == {"verbosity": "high"}


class TestNousProfile:
    def test_tags(self):
        from agent.portal_tags import nous_portal_tags
        p = get_provider_profile("nous")
        body = p.build_extra_body()
        assert body["tags"] == nous_portal_tags()





    def test_auth_type(self):
        p = get_provider_profile("nous")
        assert p.auth_type == "oauth_device_code"




class TestQwenProfile:






    def test_prepare_messages_protects_nested_image_url_retry_mutation(self):
        qwen = get_provider_profile("qwen-oauth")
        image_url = {"url": "data:image/png;base64,original"}
        msgs = [
            {"role": "system", "content": "Be helpful"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "see image"},
                    {"type": "image_url", "image_url": image_url},
                ],
            },
        ]

        qwen_result = qwen.prepare_messages(msgs)

        assert qwen_result[1] is not msgs[1]
        assert qwen_result[1]["content"] is not msgs[1]["content"]
        assert qwen_result[1]["content"][1] is not msgs[1]["content"][1]
        assert qwen_result[1]["content"][1]["image_url"] is not image_url

        qwen_result[1]["content"][1]["image_url"]["url"] = (
            "data:image/png;base64,shrunk"
        )
        assert msgs[1]["content"][1]["image_url"]["url"] == (
            "data:image/png;base64,original"
        )

    def test_metadata_top_level(self):
        p = get_provider_profile("qwen-oauth")
        meta = {"sessionId": "s123", "promptId": "p456"}
        eb, tl = p.build_api_kwargs_extras(qwen_session_metadata=meta)
        assert tl["metadata"] == meta
        assert "metadata" not in eb


class TestBaseProfile:
    def test_unauthenticated_support_defaults_disabled(self):
        p = ProviderProfile(name="example")
        assert p.supports_unauthenticated is False

    def test_model_capabilities_path_defaults_disabled(self):
        p = ProviderProfile(name="example")
        assert p.model_capabilities_path == ""

    def test_structured_output_strategy_defaults_undeclared(self):
        p = ProviderProfile(name="example")
        assert p.structured_output_strategy is None

    def test_prepare_messages_passthrough(self):
        p = ProviderProfile(name="test")
        msgs = [{"role": "user", "content": "hi"}]
        assert p.prepare_messages(msgs) is msgs


    def test_build_api_kwargs_extras_empty(self):
        p = ProviderProfile(name="test")
        eb, tl = p.build_api_kwargs_extras()
        assert eb == {}
        assert tl == {}


@pytest.mark.parametrize(
    ("provider", "model_config", "provider_config", "expected_strategy"),
    [
        pytest.param(
            "openai-api",
            {
                "provider": "openai-api",
                "default": "gpt-5.4",
                "base_url": "https://api.openai.com/v1",
            },
            {"api_mode": "codex_responses", "base_url": "https://api.openai.com/v1"},
            StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            id="direct-openai-responses",
        ),
        pytest.param(
            "openai-api",
            {
                "provider": "openai-api",
                "default": "gpt-4.1",
                "base_url": "https://api.openai.com/v1",
            },
            {"api_mode": "chat_completions", "base_url": "https://api.openai.com/v1"},
            StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            id="direct-openai-chat-completions",
        ),
        pytest.param(
            "anthropic",
            {"provider": "anthropic", "default": "claude-sonnet-4-6"},
            {
                "api_mode": "anthropic_messages",
                "base_url": "https://api.anthropic.com",
            },
            StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            id="direct-anthropic-messages",
        ),
        pytest.param(
            "custom",
            {
                "provider": "custom",
                "default": "local-model",
                "base_url": "http://127.0.0.1:11434/v1",
            },
            {
                "api_mode": "chat_completions",
                "base_url": "http://127.0.0.1:11434/v1",
            },
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="custom-endpoint",
        ),
        pytest.param(
            "openrouter",
            {"provider": "openrouter", "default": "openai/gpt-5.4"},
            {
                "api_mode": "chat_completions",
                "base_url": "https://openrouter.ai/api/v1",
            },
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="aggregator",
        ),
        pytest.param(
            "community-provider",
            {"provider": "community-provider", "default": "community-model"},
            {"api_mode": "chat_completions"},
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="unknown-hermes-managed-loop",
        ),
        pytest.param(
            "openai-codex",
            {
                "provider": "openai-codex",
                "default": "gpt-5.4-codex",
                "openai_runtime": "codex_app_server",
            },
            {"api_mode": "codex_app_server"},
            StructuredOutputStrategy.UNSUPPORTED,
            id="delegated-runtime",
        ),
        pytest.param(
            "community-provider",
            {"provider": "community-provider", "default": "community-model"},
            {"api_mode": "chat_completions", "structured_output": True},
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="community-metadata-cannot-promote",
        ),
        pytest.param(
            "openai-codex",
            {"provider": "openai-codex", "default": "gpt-5.4-codex"},
            {
                "api_mode": "codex_responses",
                "base_url": "https://chatgpt.com/backend-api/codex",
            },
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="chatgpt-subscription-is-not-native",
        ),
        pytest.param(
            "openai-codex",
            {
                "provider": "openai-codex",
                "default": "gpt-5.4-codex",
                "base_url": "https://api.openai.com/v1",
            },
            {
                "api_mode": "codex_responses",
                "base_url": "https://api.openai.com/v1",
            },
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="openai-codex-cannot-promote-on-openai-host",
        ),
        pytest.param(
            "arbitrary-provider",
            {
                "provider": "arbitrary-provider",
                "default": "arbitrary-model",
                "base_url": "https://api.openai.com/v1",
            },
            {
                "api_mode": "chat_completions",
                "base_url": "https://api.openai.com/v1",
            },
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            id="arbitrary-identity-cannot-promote-on-openai-host",
        ),
    ],
)
def test_structured_output_capability_matrix_is_authority_based(
    provider,
    model_config,
    provider_config,
    expected_strategy,
):
    runtime = classify_execution_runtime(
        provider=provider,
        model_config=model_config,
        provider_config=provider_config,
    )

    decision = resolve_structured_output_capability(
        runtime,
        schema_fingerprint="a" * 64,
    )

    assert decision.strategy is expected_strategy
    assert decision.schema_fingerprint == "a" * 64
    assert decision.adapter_version == 1
    assert len(decision.rationale) <= 256


def test_explicit_unsupported_declaration_forbids_prompt_adaptation():
    runtime = ExecutionRuntimeCapabilities(
        api_mode="chat_completions",
        hermes_managed_tool_loop=True,
        effective_provider="locked-provider",
        model="locked-model",
        base_url_trust_class="unknown",
        declared_structured_output_strategy="unsupported",
        structured_output_declaration_source="provider_profile",
    )

    decision = resolve_structured_output_capability(
        runtime,
        schema_fingerprint="b" * 64,
    )

    assert decision.strategy is StructuredOutputStrategy.UNSUPPORTED
    assert decision.declaration_source == "explicit_unsupported"


def test_explicit_unsupported_profile_wins_even_on_openai_host(monkeypatch):
    import providers

    original_get = providers.get_provider_profile

    def profile_for(name):
        if name == "locked-openai-host":
            return ProviderProfile(
                name=name,
                api_mode="chat_completions",
                base_url="https://api.openai.com/v1",
                structured_output_strategy="unsupported",
            )
        return original_get(name)

    monkeypatch.setattr(providers, "get_provider_profile", profile_for)

    runtime = classify_execution_runtime(
        provider="locked-openai-host",
        model_config={
            "provider": "locked-openai-host",
            "default": "locked-model",
            "base_url": "https://api.openai.com/v1",
        },
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://api.openai.com/v1",
        },
    )
    decision = resolve_structured_output_capability(
        runtime,
        schema_fingerprint="c" * 64,
    )

    assert decision.strategy is StructuredOutputStrategy.UNSUPPORTED
    assert decision.declaration_source == "explicit_unsupported"


@pytest.mark.parametrize("provider", ("claude", "claude-oauth", "claude-code"))
def test_anthropic_aliases_use_canonical_native_declaration(provider):
    runtime = classify_execution_runtime(
        provider=provider,
        model_config={"provider": provider, "default": "claude-sonnet-4-6"},
        provider_config={
            "api_mode": "anthropic_messages",
            "base_url": "https://api.anthropic.com",
        },
    )
    decision = resolve_structured_output_capability(
        runtime,
        schema_fingerprint="d" * 64,
    )

    assert runtime.effective_provider == "anthropic"
    assert runtime.base_url_trust_class == "trusted_direct"
    assert decision.strategy is StructuredOutputStrategy.NATIVE_JSON_SCHEMA


@pytest.mark.parametrize(
    ("configured_url", "expected_url", "forbidden"),
    [
        pytest.param(
            "https://alice:password@proxy.example.test/anthropic",
            "https://proxy.example.test/anthropic",
            ("alice", "password"),
            id="userinfo",
        ),
        pytest.param(
            (
                "https://community.example.test/v1?token=supersecret"
                "&region=us-east-1&api-version=2026-07-01#fragmentsecret"
            ),
            (
                "https://community.example.test/v1?api-version=2026-07-01"
                "&region=us-east-1&token"
            ),
            ("supersecret", "fragmentsecret"),
            id="query-values-and-fragment",
        ),
    ],
)
def test_configured_route_snapshot_contains_only_non_secret_url_evidence(
    configured_url,
    expected_url,
    forbidden,
):
    routes = snapshot_configured_execution_routes(
        {
            "providers": {
                "private-route": {
                    "api": configured_url,
                    "transport": "chat_completions",
                }
            }
        }
    )

    route = routes["private-route"]
    serialized = repr(routes)

    assert route.provider_config["base_url"] == expected_url
    assert all(secret not in serialized for secret in forbidden)


def test_configured_route_snapshot_is_query_order_deterministic():
    first = snapshot_configured_execution_routes(
        {
            "providers": {
                "private-route": {
                    "api": (
                        "https://community.example.test/v1?region=us-east-1"
                        "&token=first-token&api-version=2026-07-01"
                    ),
                    "transport": "chat_completions",
                }
            }
        }
    )["private-route"]
    second = snapshot_configured_execution_routes(
        {
            "providers": {
                "private-route": {
                    "api": (
                        "https://community.example.test/v1?api-version=2026-07-01"
                        "&token=second-token&region=us-east-1"
                    ),
                    "transport": "chat_completions",
                }
            }
        }
    )["private-route"]

    assert first == second
    assert first.provider_config["base_url"] == (
        "https://community.example.test/v1?api-version=2026-07-01"
        "&region=us-east-1&token"
    )


def test_configured_route_snapshot_rejects_unclassified_query_values():
    routes = snapshot_configured_execution_routes(
        {
            "providers": {
                "private-route": {
                    "api": (
                        "https://community.example.test/v1"
                        "?feature-mode=opaque-secret"
                    ),
                    "transport": "chat_completions",
                }
            }
        }
    )

    route = routes["private-route"]
    serialized = repr(routes)

    assert route.route_evidence_error == "unclassified_query_parameter"
    assert "base_url" not in route.provider_config
    assert "opaque-secret" not in serialized
