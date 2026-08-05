from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.anthropic_adapter import create_anthropic_message
from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
from agent.codex_runtime import run_codex_app_server_turn, run_codex_stream
from agent.provider_attempts import reserve_provider_transport_attempt
from run_agent import AIAgent


ROOT = Path(__file__).parents[2]


def test_chat_completion_reserves_before_transport() -> None:
    events: list[str] = []

    def create(**_kwargs):
        events.append("transport")
        return "response"

    agent = SimpleNamespace(
        api_mode="chat_completions",
        provider="openrouter",
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = _dispatch_nonstreaming_api_request(
        agent,
        {"model": "test"},
        make_client=lambda *_args, **_kwargs: client,
    )

    assert result == "response"
    assert events == ["reserve", "transport"]


def test_anthropic_stream_fallback_reserves_each_transport() -> None:
    events: list[str] = []

    class Messages:
        def stream(self, **_kwargs):
            events.append("stream")
            raise RuntimeError("stream not supported")

        def create(self, **_kwargs):
            events.append("create")
            return "response"

    result = create_anthropic_message(
        SimpleNamespace(messages=Messages()),
        {"model": "test"},
        before_transport=lambda: events.append("reserve"),
    )

    assert result == "response"
    assert events == ["reserve", "stream", "reserve", "create"]


def test_agent_anthropic_bridge_installs_the_reservation_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_create(_client, _kwargs, **options):
        options["before_transport"]()
        events.append("transport")
        return "response"

    monkeypatch.setattr(
        "agent.anthropic_adapter.create_anthropic_message", fake_create
    )
    agent = SimpleNamespace(
        api_mode="anthropic_messages",
        _anthropic_client=object(),
        _disable_streaming=False,
        log_prefix="",
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
        _try_refresh_anthropic_client_credentials=lambda: None,
        _capture_anthropic_response_headers=lambda *_a, **_k: None,
    )

    result = AIAgent._anthropic_messages_create(agent, {"model": "test"})

    assert result == "response"
    assert events == ["reserve", "transport"]


def test_codex_responses_reserves_before_transport() -> None:
    events: list[str] = []
    response = SimpleNamespace(output=[])

    def create(**_kwargs):
        events.append("transport")
        return response

    agent = SimpleNamespace(
        _interrupt_requested=False,
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
        responses=SimpleNamespace(create=create),
    )
    client = SimpleNamespace(responses=agent.responses)

    assert run_codex_stream(agent, {"model": "test"}, client=client) is response
    assert events == ["reserve", "transport"]


def test_codex_app_server_reserves_before_transport() -> None:
    events: list[str] = []

    class Session:
        def run_turn(self, **_kwargs):
            events.append("transport")
            raise RuntimeError("stop after launch")

        def close(self):
            return None

    agent = SimpleNamespace(
        _codex_session=Session(),
        _interrupt_requested=False,
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
    )

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[{"role": "user", "content": "hello"}],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert result["error"] == "stop after launch"
    assert events == ["reserve", "transport"]


def _call_counts(path: str, call_name: str) -> dict[str, int]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != call_name:
            continue
        parent: ast.AST | None = node
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        assert isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
        counts[parent.name] = counts.get(parent.name, 0) + 1
    return counts


def test_all_provider_transport_launch_sites_remain_reserved() -> None:
    """Fail the merge gate if upstream reconciliation drops a launch seam."""

    assert _call_counts(
        "agent/chat_completion_helpers.py", "reserve_provider_transport_attempt"
    ) == {
        "_dispatch_nonstreaming_api_request": 3,
        # v0.20.0 moved each launch into a per-attempt opener/callback that
        # the managed Relay layer invokes; the reservation moved with it so
        # every physical retry still receives a distinct reservation.
        "_reserved_summary_create": 1,
        "_reserved_summary_retry_create": 1,
        "_open_bedrock_stream": 2,
        "_open_stream": 1,
        "_open_anthropic_stream": 1,
    }
    assert _call_counts("agent/anthropic_adapter.py", "before_transport") == {
        "create_anthropic_message": 2,
    }
    assert _call_counts(
        "agent/codex_runtime.py", "reserve_provider_transport_attempt"
    ) == {
        "run_codex_app_server_turn": 1,
        "_open_codex_stream": 1,
    }
    assert _call_counts(
        "run_agent.py", "reserve_provider_transport_attempt"
    ) == {"_anthropic_messages_create": 1}


def test_reservation_helper_is_optional_but_never_swallows_refusal() -> None:
    reserve_provider_transport_attempt(SimpleNamespace())

    def refuse() -> None:
        raise RuntimeError("provider grant exhausted")

    agent = SimpleNamespace(_provider_attempt_reservation_callback=refuse)
    with pytest.raises(RuntimeError, match="provider grant exhausted"):
        reserve_provider_transport_attempt(agent)
