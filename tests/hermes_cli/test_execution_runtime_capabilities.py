from __future__ import annotations

import socket
import subprocess

import pytest

import hermes_cli.runtime_provider as runtime_provider


@pytest.mark.parametrize(
    ("runtime", "expected_mode", "expected_managed"),
    [
        ({"provider": "openai", "api_mode": "chat_completions"}, "chat_completions", True),
        ({"provider": "openai-codex", "api_mode": "codex_responses"}, "codex_responses", True),
        ({"provider": "anthropic", "api_mode": "anthropic_messages"}, "anthropic_messages", True),
        (
            {
                "provider": "custom",
                "api_mode": "chat_completions",
                "base_url": "http://127.0.0.1:9000/v1",
                "source": "custom_provider:otto-loop24-gateway",
            },
            "chat_completions",
            True,
        ),
        ({"provider": "copilot-acp", "api_mode": "chat_completions"}, "chat_completions", True),
        ({"provider": "openai", "api_mode": "codex_app_server"}, "codex_app_server", False),
        ({"provider": "future", "api_mode": "future_tool_loop"}, "future_tool_loop", False),
        ({"provider": "broken"}, "", False),
        ({"provider": "broken", "api_mode": None}, "", False),
        ({"provider": "broken", "api_mode": {"mode": "chat_completions"}}, "", False),
    ],
)
def test_execution_runtime_capabilities_are_pure_and_match_resolved_runtime(
    monkeypatch,
    runtime,
    expected_mode,
    expected_managed,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("runtime capability classification performed I/O")

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    prospective = runtime_provider.classify_execution_runtime(
        runtime.get("api_mode")
    )
    resolved = runtime_provider.classify_resolved_execution_runtime(runtime)

    assert prospective == resolved
    assert prospective.api_mode == expected_mode
    assert prospective.hermes_managed_tool_loop is expected_managed
