from __future__ import annotations

import pytest

from agent.plugin_agent_worker import (
    _ARCHON_HOOK_EVENT_MAP,
    _compile_node_hook_resources,
    _install_node_hooks,
    _translate_hook_response,
)
from plugins.workflow.compat import MAPPED_HOOK_EVENTS, UNSUPPORTED_HOOK_EVENTS
from plugins.workflow.executors.ai import AgentNodeExecutor
from tests.plugins.workflow.test_ai_executor import (
    FakeAgentRunner,
    _archon_context,
    _context,
    _node,
)


def _response(event, **specific):
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            **specific,
        }
    }


def test_every_published_mapped_hook_has_an_explicit_worker_event():
    assert set(_ARCHON_HOOK_EVENT_MAP) == set(MAPPED_HOOK_EVENTS)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("deny", {"action": "block", "message": "policy"}),
        ("ask", {"action": "approve", "message": "policy"}),
        ("allow", None),
    ],
)
def test_pre_tool_permission_decisions_translate_exactly(decision, expected):
    response = _response(
        "PreToolUse",
        permissionDecision=decision,
        permissionDecisionReason="policy",
    )
    assert _translate_hook_response("PreToolUse", response) == expected


def test_updated_input_and_additional_context_translate_without_system_mutation():
    response = _response(
        "PreToolUse",
        updatedInput={"path": "safe.txt"},
        additionalContext="node-only context",
    )
    translated = _translate_hook_response("PreToolUse", response)
    assert translated == {
        "args": {"path": "safe.txt"},
        "context": "node-only context",
    }


@pytest.mark.parametrize("event", sorted(UNSUPPORTED_HOOK_EVENTS))
def test_unsupported_events_fail_closed(event):
    with pytest.raises(ValueError, match="unsupported"):
        _compile_node_hook_resources((
            {
                "event": event,
                "response": {"continue": True},
            },
        ))


def test_invalid_regex_malformed_response_and_event_mismatch_fail_closed():
    with pytest.raises(ValueError, match="matcher"):
        _compile_node_hook_resources((
            {
                "event": "PreToolUse",
                "matcher": "[",
                "response": {"continue": True},
            },
        ))
    with pytest.raises(ValueError, match="response"):
        _compile_node_hook_resources((
            {
                "event": "PreToolUse",
                "response": "not-a-mapping",
            },
        ))
    with pytest.raises(ValueError, match="hookEventName"):
        _translate_hook_response(
            "PreToolUse",
            _response("PostToolUse", permissionDecision="deny"),
        )


def test_matcher_and_timeout_are_bounded_and_unknown_fields_block():
    resources = _compile_node_hook_resources((
        {
            "event": "PreToolUse",
            "matcher": "^read_file$",
            "timeout": 2,
            "response": _response("PreToolUse", permissionDecision="allow"),
        },
    ))
    assert len(resources) == 1
    assert resources[0]["matcher"].fullmatch("read_file")
    with pytest.raises(ValueError, match="timeout"):
        _compile_node_hook_resources((
            {
                "event": "PreToolUse",
                "timeout": 301,
                "response": {"continue": True},
            },
        ))
    with pytest.raises(ValueError, match="unknown"):
        _compile_node_hook_resources((
            {
                "event": "PreToolUse",
                "unknown": True,
                "response": {"continue": True},
            },
        ))


def test_node_executor_sends_only_declared_hooks_to_isolated_worker(tmp_path):
    runner = FakeAgentRunner("done")
    node = _node(
        "hooked",
        "work",
        hooks={
            "PreToolUse": [
                {
                    "matcher": "^read_file$",
                    "response": _response("PreToolUse", permissionDecision="deny"),
                }
            ]
        },
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].hooks[0]["event"] == "PreToolUse"
    assert runner.requests[0].hooks[0]["matcher"] == "^read_file$"


def test_structured_repair_does_not_reinstall_original_node_hooks(tmp_path):
    runner = FakeAgentRunner("not json", '{"answer":"fixed"}')
    node = _node(
        "hooked-repair",
        "work",
        hooks={
            "PreToolUse": [
                {
                    "matcher": "^read_file$",
                    "response": _response("PreToolUse", permissionDecision="deny"),
                }
            ]
        },
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].hooks
    assert runner.requests[1].hooks == ()


def test_installed_pre_tool_hook_rewrites_then_blocks_matching_call(monkeypatch):
    class Manager:
        _hooks = {}
        _middleware = {}

    manager = Manager()
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)
    observed = _install_node_hooks((
        {
            "event": "PreToolUse",
            "matcher": "^read_file$",
            "response": _response(
                "PreToolUse",
                permissionDecision="deny",
                permissionDecisionReason="blocked",
                updatedInput={"path": "safe.txt"},
            ),
        },
    ))

    rewritten = manager._middleware["tool_request"][0](
        tool_name="read_file", args={"path": "unsafe.txt"}
    )
    directive = manager._hooks["pre_tool_call"][0](
        tool_name="read_file", args=rewritten["args"]
    )

    assert rewritten["args"] == {"path": "safe.txt"}
    assert directive == {
        "action": "block",
        "message": "blocked",
        "args": {"path": "safe.txt"},
    }
    assert [event["event"] for event in observed] == ["PreToolUse", "PreToolUse"]
