from __future__ import annotations

from typing import Iterator

import pytest

import providers
from providers import ProviderProfile

from acp_adapter.server import HermesACPAgent
from hermes_cli.auth import resolve_provider
from hermes_cli.models import parse_model_input
from hermes_cli.provider_aliases import (
    PUBLIC_PROVIDER_COMPATIBILITY_ALIASES,
    resolve_provider_selector,
)
from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    WorkflowProviderFeature,
    resolve_provider_capability,
)
from hermes_cli.providers import resolve_provider_full
from hermes_cli.runtime_provider import (
    _canonical_execution_provider,
    classify_execution_runtime,
)


@pytest.fixture(autouse=True)
def preserve_provider_registry() -> Iterator[None]:
    providers.list_providers()
    registry = dict(providers._REGISTRY)
    aliases = dict(providers._ALIASES)
    registrations = dict(providers._REGISTRATIONS)
    collisions = list(providers._REGISTRATION_COLLISIONS)
    provider_list_cache = providers._PROVIDER_LIST_CACHE
    discovered = providers._discovered

    yield

    providers._REGISTRY.clear()
    providers._REGISTRY.update(registry)
    providers._ALIASES.clear()
    providers._ALIASES.update(aliases)
    providers._REGISTRATIONS.clear()
    providers._REGISTRATIONS.update(registrations)
    providers._REGISTRATION_COLLISIONS.clear()
    providers._REGISTRATION_COLLISIONS.extend(collisions)
    providers._PROVIDER_LIST_CACHE = provider_list_cache
    providers._discovered = discovered


def _provider_token_cases() -> tuple[tuple[str, str], ...]:
    tokens = (
        set(providers._REGISTRY)
        | set(providers._ALIASES)
        | set(PUBLIC_PROVIDER_COMPATIBILITY_ALIASES)
    )
    cases = []
    for token in sorted(tokens):
        profile = providers.get_provider_profile(token)
        expected = (
            profile.name
            if profile is not None
            else PUBLIC_PROVIDER_COMPATIBILITY_ALIASES.get(token, token)
        )
        cases.append((token, expected))
    return tuple(cases)


def _register_at_origin(profile: ProviderProfile, origin: str) -> None:
    context = providers._RegistrationContext(
        origin_kind=origin,
        distribution_id=f"{origin}-provider-token-convergence",
        distribution_version="1",
        package_root=None,
    )
    token = providers._REGISTRATION_CONTEXT.set(context)
    try:
        providers.register_provider(profile)
    finally:
        providers._REGISTRATION_CONTEXT.reset(token)


def test_every_registered_or_public_token_has_one_provider_selector_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.models_dev as models_dev

    monkeypatch.setattr(models_dev, "get_provider_info", lambda *_args, **_kwargs: None)

    failures: list[tuple[str, str, str, object]] = []
    for token, expected in _provider_token_cases():
        surfaces = {
            "auth": resolve_provider(token),
            "runtime": _canonical_execution_provider(token),
            "full": (
                resolved.id
                if (resolved := resolve_provider_full(token, {}, {})) is not None
                else None
            ),
            "parser": parse_model_input(
                f"{token}:provider-token-probe", "fallback-provider"
            )[0],
            "acp": HermesACPAgent._resolve_model_selection(
                f"{token}:provider-token-probe", "fallback-provider"
            )[0],
        }
        failures.extend(
            (token, expected, surface, actual)
            for surface, actual in surfaces.items()
            if actual != expected
        )

    assert failures == []


def test_provider_selector_reports_its_finite_authority_source() -> None:
    for token, expected in _provider_token_cases():
        resolution = resolve_provider_selector(token)
        registry_profile = providers.get_provider_profile(token)

        assert resolution.provider == expected
        assert resolution.source == (
            "registry" if registry_profile is not None else "public_compatibility"
        )


def test_provider_selector_never_hides_registry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_token: str):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(providers, "get_provider_profile", unavailable)

    with pytest.raises(RuntimeError, match="registry unavailable"):
        resolve_provider_selector("openrouter")


@pytest.mark.parametrize(
    ("token", "expected"),
    (
        ("minimax-global", "minimax-oauth"),
        ("minimax-portal", "minimax-oauth"),
        ("qwen", "qwen-oauth"),
        ("codex", "openai-codex"),
        ("claude-oauth", "anthropic"),
        ("or", "openrouter"),
    ),
)
def test_reported_provider_tokens_converge_at_full_parser_and_acp(
    token: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.models_dev as models_dev

    monkeypatch.setattr(models_dev, "get_provider_info", lambda *_args, **_kwargs: None)

    resolved = resolve_provider_full(token, {}, {})
    assert resolved is not None
    assert resolved.id == expected
    assert parse_model_input(f"{token}:probe-model", "openrouter") == (
        expected,
        "probe-model",
    )
    assert HermesACPAgent._resolve_model_selection(
        f"{token}:probe-model", "openrouter"
    ) == (expected, "probe-model")


@pytest.mark.parametrize("registration_order", (("bundled", "user"), ("user", "bundled")))
def test_provider_selector_follows_registry_collision_winner_in_any_import_order(
    registration_order: tuple[str, str],
) -> None:
    providers._REGISTRY.clear()
    providers._ALIASES.clear()
    providers._REGISTRATIONS.clear()
    providers._REGISTRATION_COLLISIONS.clear()
    providers._PROVIDER_LIST_CACHE = None
    providers._discovered = True
    registrations = {
        "bundled": (
            ProviderProfile(name="anthropic", aliases=("claude",)),
            "bundled",
        ),
        "user": (ProviderProfile(name="claude"), "user_plugin"),
    }
    for key in registration_order:
        profile, origin = registrations[key]
        _register_at_origin(profile, origin)

    assert providers.get_provider_profile("claude").name == "claude"
    assert resolve_provider("claude") == "claude"
    assert _canonical_execution_provider("claude") == "claude"
    assert resolve_provider_full("claude", {}, {}).id == "claude"
    assert parse_model_input("claude:probe-model", "openrouter") == (
        "claude",
        "probe-model",
    )
    assert HermesACPAgent._resolve_model_selection(
        "claude:probe-model", "openrouter"
    ) == ("claude", "probe-model")


def test_raw_config_provider_keeps_priority_over_registry_and_compatibility() -> None:
    configured = {
        "claude": {
            "base_url": "https://configured-claude.example/v1",
            "api_key_env": "CONFIGURED_CLAUDE_API_KEY",
        }
    }

    resolved = resolve_provider_full("claude", configured, {})

    assert resolved is not None
    assert resolved.id == "claude"
    assert resolved.base_url == "https://configured-claude.example/v1"
    assert resolved.source == "user-config"


def test_unknown_provider_and_unregistered_auto_remain_unresolved_selectors() -> None:
    assert _canonical_execution_provider("phase5-unknown-provider") == (
        "phase5-unknown-provider"
    )
    assert parse_model_input(
        "phase5-unknown-provider:model-with-colon", "openrouter"
    ) == ("openrouter", "phase5-unknown-provider:model-with-colon")
    assert _canonical_execution_provider("auto") == "auto"


def test_nonbundled_plugin_claiming_auto_remains_fail_closed_for_native_authority() -> None:
    _register_at_origin(
        ProviderProfile(
            name="auto",
            base_url="https://user-auto.example/v1",
            workflow_capabilities={
                "cost_budgets": {"disposition": "native"},
                "provider_native_sandbox": {"disposition": "native"},
            },
        ),
        "user_plugin",
    )

    runtime = classify_execution_runtime(
        provider="auto",
        model_config={"provider": "auto", "default": "probe-model"},
        provider_config={"api_mode": "chat_completions"},
    )

    assert runtime.effective_provider == "auto"
    assert runtime.registration_origin_kind == "user_plugin"
    assert runtime.base_url_trust_class == "unknown"
    for feature in (
        WorkflowProviderFeature.COST_BUDGETS,
        WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
    ):
        decision = resolve_provider_capability(
            runtime,
            feature=feature,
            option=None,
            requested_semantics={},
        )
        assert decision.disposition is CapabilityDisposition.UNSUPPORTED
