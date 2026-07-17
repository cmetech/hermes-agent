from __future__ import annotations

from plugins.workflow.compat import resolve_tool_name
from plugins.workflow.executors.ai import AgentNodeExecutor

from tests.plugins.workflow.test_ai_executor import FakeAgentRunner, _context, _node


def test_archon_aliases_and_raw_hermes_names_resolve_deterministically():
    assert resolve_tool_name("Read") == "read_file"
    assert resolve_tool_name("Bash") == "terminal"
    assert resolve_tool_name("read_file") == "read_file"
    assert resolve_tool_name("Agent") == "workflow_agent"
    assert resolve_tool_name("Task") == "workflow_agent"


def test_empty_allowlist_stays_empty_and_deny_is_mapped_after_allow(tmp_path):
    runner = FakeAgentRunner("done")
    node = _node(
        "scoped",
        "work",
        allowed_tools=[],
        denied_tools=["Bash"],
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].allowed_tools == ()
    assert runner.requests[0].denied_tools == ("terminal", "delegate_task")


def test_allowed_and_denied_aliases_are_both_enforced_by_worker_request(tmp_path):
    runner = FakeAgentRunner("done")
    node = _node(
        "scoped",
        "work",
        allowed_tools=["Read", "Bash"],
        denied_tools=["Bash"],
    )

    AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert runner.requests[0].allowed_tools == ("read_file", "terminal")
    assert runner.requests[0].denied_tools == ("terminal", "delegate_task")


def test_unknown_tool_name_fails_before_runner_or_billing(tmp_path):
    runner = FakeAgentRunner("unused")
    node = _node("scoped", "work", allowed_tools=["NotARealTool"])

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "failed"
    assert result.error_code == "validation"
    assert runner.requests == []
