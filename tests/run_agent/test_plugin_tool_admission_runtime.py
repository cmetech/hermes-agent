"""Real agent-loop propagation of host-minted plugin tool admissions."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from run_agent import AIAgent
from tools.registry import registry


TOOL_NAME = "_test_agent_admission_tool"


def _tool_defs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "agent admission probe",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                },
            },
        }
    ]


def _make_agent() -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    return agent


def _tool_call(value: int, call_id: str):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=TOOL_NAME,
            arguments=json.dumps({"value": value}),
        ),
    )


@pytest.fixture(autouse=True)
def _clean_tool():
    registry.deregister(TOOL_NAME)
    yield
    registry.deregister(TOOL_NAME)


@pytest.fixture
def plugin_handler():
    received = []
    ctx = PluginContext(
        PluginManifest(name="agent-admission", key="agent-admission", source="user"),
        PluginManager(),
    )
    ctx.register_tool(
        TOOL_NAME,
        "agent-admission",
        _tool_defs()[0]["function"],
        lambda args, **kwargs: (
            received.append((args, kwargs)) or json.dumps({"ok": True})
        ),
    )
    return received


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_agent_paths_deliver_distinct_admissions_once_after_host_gate(
    monkeypatch, plugin_handler, mode
):
    hook_calls = []
    gate_calls = []

    def hook(hook_name, **kwargs):
        if hook_name == "pre_tool_call":
            hook_calls.append((kwargs["tool_call_id"], kwargs["args"]))
            return [{"action": "approve", "message": "confirm"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: (
            gate_calls.append(args[0]) or {"approved": True, "message": None}
        ),
    )
    agent = _make_agent()
    calls = [_tool_call(1, "call-1"), _tool_call(2, "call-2")]
    message = SimpleNamespace(content="", tool_calls=calls)
    messages = []

    if mode == "sequential":
        agent._execute_tool_calls_sequential(message, messages, "task-1")
    else:
        agent._execute_tool_calls_concurrent(message, messages, "task-1")

    assert sorted(hook_calls) == [
        ("call-1", {"value": 1}),
        ("call-2", {"value": 2}),
    ]
    assert gate_calls == [TOOL_NAME, TOOL_NAME]
    assert sorted(args["value"] for args, _ in plugin_handler) == [1, 2]
    admissions = [kwargs["tool_admission"] for _, kwargs in plugin_handler]
    assert len({id(item) for item in admissions}) == 2
    assert {item.tool_call_id for item in admissions} == {"call-1", "call-2"}
    assert all(item.approved is True for item in admissions)
    assert {item.arguments_sha256 for item in admissions} == {
        __import__("hashlib").sha256(b'{"value":1}').hexdigest(),
        __import__("hashlib").sha256(b'{"value":2}').hexdigest(),
    }
    assert {entry["tool_call_id"] for entry in messages} == {"call-1", "call-2"}


def test_denied_agent_call_never_dispatches_or_leaks_to_later_call(
    monkeypatch, plugin_handler
):
    directives = iter([
        [{"action": "approve", "message": "confirm"}],
        [],
    ])

    def hook(hook_name, **kwargs):
        return next(directives) if hook_name == "pre_tool_call" else []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": False, "message": "denied"},
    )
    agent = _make_agent()

    denied_messages = []
    agent._execute_tool_calls_sequential(
        SimpleNamespace(content="", tool_calls=[_tool_call(1, "call-denied")]),
        denied_messages,
        "task-1",
    )
    later_messages = []
    agent._execute_tool_calls_sequential(
        SimpleNamespace(content="", tool_calls=[_tool_call(2, "call-later")]),
        later_messages,
        "task-1",
    )

    assert "denied" in denied_messages[0]["content"]
    assert plugin_handler == [
        (
            {"value": 2},
            {
                "task_id": "task-1",
                "tool_call_id": "call-later",
                "session_id": agent.session_id,
                "turn_id": "",
                "user_task": None,
            },
        )
    ]


def test_agent_execution_middleware_rewrite_is_bound_and_gate_runs_once(
    monkeypatch, plugin_handler
):
    hook_args = []
    gate_calls = []

    def hook(hook_name, **kwargs):
        if hook_name == "pre_tool_call":
            hook_args.append(kwargs["args"])
            return [{"action": "approve", "message": "confirm"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: (
            gate_calls.append(True) or {"approved": True, "message": None}
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        lambda name, args, next_call, **kwargs: next_call({
            **args,
            "middleware": "final",
        }),
    )
    agent = _make_agent()

    agent._execute_tool_calls_sequential(
        SimpleNamespace(content="", tool_calls=[_tool_call(1, "call-rewrite")]),
        [],
        "task-1",
    )

    expected_args = {"value": 1, "middleware": "final"}
    assert hook_args == [expected_args]
    assert gate_calls == [True]
    assert plugin_handler[0][0] == expected_args
    admission = plugin_handler[0][1]["tool_admission"]
    assert (
        admission.arguments_sha256
        == __import__("hashlib").sha256(b'{"middleware":"final","value":1}').hexdigest()
    )


def test_shared_invoke_helper_delivers_admission_after_its_execution_middleware(
    monkeypatch, plugin_handler
):
    hook_args = []
    gate_calls = []

    def hook(hook_name, **kwargs):
        if hook_name == "pre_tool_call":
            hook_args.append(kwargs["args"])
            return [{"action": "approve", "message": "confirm"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: (
            gate_calls.append(True) or {"approved": True, "message": None}
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        lambda name, args, next_call, **kwargs: next_call({
            **args,
            "helper_middleware": "final",
        }),
    )
    agent = _make_agent()

    result = agent._invoke_tool(
        TOOL_NAME,
        {"value": 3},
        "task-helper",
        tool_call_id="call-helper",
    )

    expected_args = {"value": 3, "helper_middleware": "final"}
    assert result == json.dumps({"ok": True})
    assert hook_args == [expected_args]
    assert gate_calls == [True]
    assert plugin_handler[0][0] == expected_args
    assert plugin_handler[0][1]["tool_admission"].tool_call_id == "call-helper"
