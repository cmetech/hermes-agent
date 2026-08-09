from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
from typing import Any

import pytest
import requests

import hermes_cli.models as models
import hermes_cli.runtime_provider as runtime_provider
import tools.lazy_deps as lazy_deps


@pytest.mark.parametrize(
    ("metadata", "expected_mode", "expected_managed"),
    [
        pytest.param(
            {
                "provider": "nous",
                "model_config": {"provider": "nous", "default": "Hermes-4"},
                "provider_config": {},
            },
            "chat_completions",
            True,
            id="ordinary-chat-completions",
        ),
        pytest.param(
            {
                "provider": "openai-codex",
                "model_config": {
                    "provider": "openai-codex",
                    "default": "gpt-5.3-codex",
                },
                "provider_config": {},
            },
            "codex_responses",
            True,
            id="codex-responses",
        ),
        pytest.param(
            {
                "provider": "anthropic",
                "model_config": {
                    "provider": "anthropic",
                    "default": "claude-opus-4-6",
                },
                "provider_config": {},
            },
            "anthropic_messages",
            True,
            id="anthropic-messages",
        ),
        pytest.param(
            {
                "provider": "custom",
                "model_config": {
                    "provider": "otto-loop24-gateway",
                    "default": "otto-loop24",
                },
                "provider_config": {
                    "name": "otto-loop24-gateway",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "api_mode": "chat_completions",
                },
            },
            "chat_completions",
            True,
            id="otto-loop24-custom-http",
        ),
        pytest.param(
            {
                "provider": "copilot-acp",
                "model_config": {
                    "provider": "copilot-acp",
                    "default": "github-copilot/gpt-5.4",
                },
                "provider_config": {},
            },
            "chat_completions",
            True,
            id="copilot-acp",
        ),
        pytest.param(
            {
                "provider": "openai-codex",
                "model_config": {
                    "provider": "openai-codex",
                    "default": "gpt-5.3-codex",
                    "openai_runtime": "codex_app_server",
                },
                "provider_config": {},
            },
            "codex_app_server",
            False,
            id="codex-app-server",
        ),
        pytest.param(
            {
                "provider": "opencode-zen",
                "model_config": {
                    "provider": "opencode-zen",
                    "default": "gpt-5.3-codex",
                },
                "provider_config": {},
            },
            "codex_responses",
            True,
            id="model-specific-opencode-responses",
        ),
        pytest.param(
            {
                "provider": "opencode-go",
                "model_config": {
                    "provider": "opencode-go",
                    "default": "qwen3.7-max",
                },
                "provider_config": {},
            },
            "anthropic_messages",
            True,
            id="model-specific-opencode-messages",
        ),
        pytest.param(
            {
                "provider": "azure-foundry",
                "model_config": {
                    "provider": "azure-foundry",
                    "default": "gpt-5.3-codex",
                },
                "provider_config": {
                    "base_url": "https://example.services.ai.azure.com/api/projects/p",
                },
            },
            "codex_responses",
            True,
            id="model-specific-azure-foundry-responses",
        ),
        pytest.param(
            {
                "provider": "copilot",
                "model_config": {
                    "provider": "copilot",
                    "default": "gpt-5.4",
                },
                "provider_config": {},
            },
            "codex_responses",
            True,
            id="model-specific-copilot-responses",
        ),
        pytest.param(
            {
                "provider": "future-provider",
                "model_config": {"provider": "future-provider"},
                "provider_config": {"api_mode": " future_tool_loop "},
            },
            "future_tool_loop",
            False,
            id="unknown-mode-fails-closed",
        ),
        pytest.param(
            {
                "provider": "future-provider",
                "model_config": {"provider": "future-provider"},
                "provider_config": {},
            },
            "",
            False,
            id="unknown-provider-fails-closed",
        ),
        pytest.param(
            {
                "provider": "nous",
                "model_config": {"provider": "nous"},
                "provider_config": {"api_mode": {"mode": "chat_completions"}},
            },
            "",
            False,
            id="malformed-mode-fails-closed",
        ),
        pytest.param(
            {
                "provider": "nous",
                "model_config": ["not", "a", "mapping"],
                "provider_config": {},
            },
            "",
            False,
            id="malformed-model-config-fails-closed",
        ),
        pytest.param(
            {
                "provider": {"name": "nous"},
                "model_config": {"provider": "nous"},
                "provider_config": {},
            },
            "",
            False,
            id="malformed-provider-fails-closed",
        ),
    ],
)
def test_prospective_execution_runtime_capabilities_are_pure(
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, Any],
    expected_mode: str,
    expected_managed: bool,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime capability classification performed I/O")

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", forbidden)
    monkeypatch.setattr(runtime_provider, "_get_model_config", forbidden)
    monkeypatch.setattr(runtime_provider, "load_config", forbidden)
    monkeypatch.setattr(models, "fetch_github_model_catalog", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    prospective = runtime_provider.classify_execution_runtime(**metadata)

    assert prospective.api_mode == expected_mode
    assert prospective.hermes_managed_tool_loop is expected_managed


@pytest.mark.parametrize(
    ("model", "expected_mode"),
    [
        pytest.param(
            "global.anthropic.claude-sonnet-4-6",
            "anthropic_messages",
            id="claude-bedrock",
        ),
        pytest.param(
            "us.amazon.nova-pro-v1:0",
            "bedrock_converse",
            id="nova-bedrock-converse",
        ),
    ],
)
def test_bedrock_classification_is_fresh_pure_managed_and_matches_resolved(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    expected_mode: str,
) -> None:
    ensure_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Bedrock runtime classification performed I/O")

    def record_ensure(
        feature: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        ensure_calls.append((feature, args, kwargs))

    # Exercise a genuinely fresh predicate import and prove the classifier does
    # not reach the side-effectful Bedrock adapter/lazy dependency installer.
    monkeypatch.delitem(sys.modules, "agent.model_metadata", raising=False)
    monkeypatch.delitem(sys.modules, "agent.bedrock_adapter", raising=False)
    monkeypatch.setattr(lazy_deps, "ensure", record_ensure)
    monkeypatch.setattr(runtime_provider, "_get_model_config", forbidden)
    monkeypatch.setattr(runtime_provider, "load_config", forbidden)
    monkeypatch.setattr(runtime_provider, "resolve_provider", forbidden)
    monkeypatch.setattr(runtime_provider, "load_pool", forbidden)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_api_key_provider_credentials",
        forbidden,
    )
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    prospective = runtime_provider.classify_execution_runtime(
        provider="bedrock",
        model_config={"provider": "bedrock", "default": model},
        provider_config={},
    )
    resolved = runtime_provider.classify_resolved_execution_runtime(
        {"provider": "bedrock", "api_mode": expected_mode},
        target_model=model,
    )

    assert prospective == resolved
    assert prospective.api_mode == expected_mode
    assert prospective.hermes_managed_tool_loop is True
    assert ensure_calls == []
    assert "agent.bedrock_adapter" not in sys.modules


def test_bedrock_classification_is_pure_in_a_fresh_process() -> None:
    probe = textwrap.dedent(
        """
        import socket
        import subprocess
        import sys

        import requests
        import tools.lazy_deps as lazy_deps

        side_effects = []

        def record_ensure(*args, **kwargs):
            side_effects.append(("ensure", args, kwargs))
            raise AssertionError("lazy installation attempted")

        def forbidden(*args, **kwargs):
            raise AssertionError("runtime classification performed I/O")

        lazy_deps.ensure = record_ensure
        subprocess.Popen = forbidden
        socket.create_connection = forbidden
        requests.sessions.Session.request = forbidden

        import hermes_cli.runtime_provider as runtime_provider

        runtime_provider._get_model_config = forbidden
        runtime_provider.load_config = forbidden
        runtime_provider.resolve_provider = forbidden
        runtime_provider.load_pool = forbidden
        runtime_provider.resolve_api_key_provider_credentials = forbidden

        fixtures = (
            ("global.anthropic.claude-sonnet-4-6", "anthropic_messages"),
            ("us.amazon.nova-pro-v1:0", "bedrock_converse"),
        )
        for model, expected_mode in fixtures:
            prospective = runtime_provider.classify_execution_runtime(
                provider="bedrock",
                model_config={"provider": "bedrock", "default": model},
                provider_config={},
            )
            resolved = runtime_provider.classify_resolved_execution_runtime(
                {"provider": "bedrock", "api_mode": expected_mode},
                target_model=model,
            )
            assert prospective == resolved
            assert prospective.hermes_managed_tool_loop is True

        assert side_effects == []
        assert "agent.bedrock_adapter" not in sys.modules
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


@pytest.mark.parametrize(
    (
        "provider",
        "model_config",
        "provider_config",
        "resolve_kwargs",
        "expected_mode",
    ),
    [
        pytest.param(
            "nous",
            {"provider": "nous", "default": "Hermes-4"},
            {},
            {
                "requested": "nous",
                "explicit_api_key": "test-nous-key",
                "explicit_base_url": "https://inference.example/v1",
            },
            "chat_completions",
            id="ordinary-chat-completions",
        ),
        pytest.param(
            "openai-codex",
            {"provider": "openai-codex", "default": "gpt-5.3-codex"},
            {},
            {
                "requested": "openai-codex",
                "explicit_api_key": "test-codex-key",
                "explicit_base_url": "https://chatgpt.com/backend-api/codex",
            },
            "codex_responses",
            id="codex-responses",
        ),
        pytest.param(
            "anthropic",
            {"provider": "anthropic", "default": "claude-opus-4-6"},
            {},
            {
                "requested": "anthropic",
                "explicit_api_key": "test-anthropic-key",
                "explicit_base_url": "https://api.anthropic.com",
            },
            "anthropic_messages",
            id="anthropic-messages",
        ),
        pytest.param(
            "custom",
            {"provider": "otto-loop24-gateway", "default": "otto-loop24"},
            {
                "name": "otto-loop24-gateway",
                "base_url": "http://127.0.0.1:9000/v1",
                "api_key": "test-loop24-key",
                "api_mode": "chat_completions",
            },
            {"requested": "otto-loop24-gateway"},
            "chat_completions",
            id="otto-loop24-custom-http",
        ),
        pytest.param(
            "copilot-acp",
            {"provider": "copilot-acp", "default": "github-copilot/gpt-5.4"},
            {},
            {"requested": "copilot-acp"},
            "chat_completions",
            id="copilot-acp",
        ),
        pytest.param(
            "openai-codex",
            {
                "provider": "openai-codex",
                "default": "gpt-5.3-codex",
                "openai_runtime": "codex_app_server",
            },
            {},
            {
                "requested": "openai-codex",
                "explicit_api_key": "test-codex-key",
                "explicit_base_url": "https://chatgpt.com/backend-api/codex",
            },
            "codex_app_server",
            id="codex-app-server",
        ),
    ],
)
def test_prospective_classifier_matches_actual_effective_runtime(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model_config: dict[str, Any],
    provider_config: dict[str, Any],
    resolve_kwargs: dict[str, Any],
    expected_mode: str,
) -> None:
    config: dict[str, Any] = {"model": model_config}
    if provider_config:
        config["custom_providers"] = [provider_config]

    monkeypatch.setattr(runtime_provider, "load_config", lambda: config)
    monkeypatch.setattr(runtime_provider, "load_pool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_provider.auth_mod,
        "get_provider_auth_state",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_external_process_provider_credentials",
        lambda *_args, **_kwargs: {
            "base_url": "http://127.0.0.1:43123/v1",
            "api_key": "test-acp-key",
            "command": "copilot",
            "args": ["--acp"],
            "source": "test-fixture",
        },
    )

    prospective = runtime_provider.classify_execution_runtime(
        provider=provider,
        model_config=model_config,
        provider_config=provider_config,
    )
    resolved = runtime_provider.resolve_runtime_provider(**resolve_kwargs)

    assert resolved["api_mode"] == expected_mode
    assert prospective == runtime_provider.classify_resolved_execution_runtime(
        resolved,
        target_model=model_config["default"],
    )


def test_runtime_resolver_wrapper_changes_only_app_server_api_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_config = {
        "provider": "openai-codex",
        "default": "gpt-5.3-codex",
        "openai_runtime": "codex_app_server",
    }
    runtime: dict[str, Any] = {
        "provider": "openai-codex",
        "api_mode": "codex_responses",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_key": "opaque-test-key",
        "source": "test-fixture",
        "requested_provider": "openai-codex",
        "nested": {"preserve": ["every", "value"]},
    }
    non_capability_fields = {
        key: value for key, value in runtime.items() if key != "api_mode"
    }

    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: model_config)
    monkeypatch.setattr(
        runtime_provider,
        "_resolve_runtime_provider_unclassified",
        lambda **_kwargs: runtime,
    )

    resolved = runtime_provider.resolve_runtime_provider(requested="openai-codex")

    assert resolved is runtime
    assert resolved["api_mode"] == "codex_app_server"
    assert {
        key: value for key, value in resolved.items() if key != "api_mode"
    } == non_capability_fields


@pytest.mark.parametrize(
    "runtime",
    [
        {"provider": "broken"},
        {"provider": "broken", "api_mode": None},
        {"provider": "broken", "api_mode": {"mode": "chat_completions"}},
        ["not", "a", "runtime", "mapping"],
    ],
)
def test_resolved_runtime_classifier_fails_closed_for_malformed_runtime(
    runtime: object,
) -> None:
    classified = runtime_provider.classify_resolved_execution_runtime(runtime)

    assert classified.api_mode == ""
    assert classified.hermes_managed_tool_loop is False
    assert classified.effective_provider == (
        "broken" if isinstance(runtime, dict) else ""
    )


@pytest.mark.parametrize(
    "provider",
    ["custom", "custom:openai-api", "openai"],
)
def test_custom_and_alias_routes_do_not_borrow_native_openai_trust(provider: str) -> None:
    classified = runtime_provider.classify_execution_runtime(
        provider=provider,
        model_config={
            "provider": provider,
            "default": "gpt-5.4",
        },
        provider_config={
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
        },
    )

    assert classified.base_url_trust_class != "trusted_direct"
    assert classified.declared_structured_output_strategy is None
