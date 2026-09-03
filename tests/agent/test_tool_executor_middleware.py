from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def test_message_agent_receives_provider_tool_call_id(monkeypatch):
    tool = {
        "type": "function",
        "function": {
            "name": "message_agent",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    with (
        patch("run_agent.get_tool_definitions", return_value=[tool]),
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
    observed = {}
    monkeypatch.setattr(
        "tools.bot_mode_dm.message_agent_tool",
        lambda **kwargs: observed.update(kwargs) or '{"status":"prepared"}',
    )
    call = SimpleNamespace(
        id="call-provider-1",
        function=SimpleNamespace(
            name="message_agent",
            arguments='{"target":"researcher","message":"hello"}',
        ),
    )

    agent._execute_tool_calls_sequential(
        SimpleNamespace(tool_calls=[call]), [], "task-1"
    )

    assert observed["tool_call_id"] == "call-provider-1"
