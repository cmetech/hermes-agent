from copy import deepcopy
from types import SimpleNamespace


class _CapturingTransport:
    def __init__(self):
        self.params = None

    def build_kwargs(self, **params):
        self.params = params
        return params


def _anthropic_agent(transport, tools):
    return SimpleNamespace(
        api_mode="anthropic_messages",
        tools=tools,
        model="model-fixture",
        max_tokens=1024,
        reasoning_config=None,
        context_compressor=None,
        request_overrides={},
        provider="direct-fixture",
        structured_output=None,
        _is_anthropic_oauth=False,
        _get_transport=lambda: transport,
        _prepare_anthropic_messages_for_api=lambda messages: messages,
        _anthropic_preserve_dots=lambda: False,
    )


def test_tool_policy_builder_passes_context_without_mutating_agent_tools():
    from agent.chat_completion_helpers import build_api_kwargs
    from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext

    tools = [
        {
            "type": "function",
            "function": {"name": "tool_fixture", "parameters": {"type": "object"}},
        }
    ]
    original = deepcopy(tools)
    transport = _CapturingTransport()
    agent = _anthropic_agent(transport, tools)
    context = ToolOperationContext.create(ToolChoicePolicy(mode="required"))

    build_api_kwargs(agent, [{"role": "user", "content": "fixture"}], attempt_context=context)

    assert transport.params["attempt_context"] is context
    assert agent.tools == original


def test_build_api_kwargs_has_no_tool_policy_when_attempt_context_is_omitted():
    from agent.chat_completion_helpers import build_api_kwargs

    transport = _CapturingTransport()
    agent = _anthropic_agent(transport, [])

    build_api_kwargs(agent, [{"role": "user", "content": "fixture"}])

    assert transport.params.get("attempt_context") is None


def test_raw_response_tool_contract_builder_emits_exact_otto_headers():
    from agent.chat_completion_helpers import build_api_kwargs
    from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext

    transport = _CapturingTransport()
    agent = _anthropic_agent(transport, [])
    agent.provider = "otto"
    context = ToolOperationContext.create(
        ToolChoicePolicy(mode="none"),
        otto_contract_version="v1",
    )

    kwargs = build_api_kwargs(
        agent,
        [{"role": "user", "content": "fixture"}],
        attempt_context=context,
    )

    assert kwargs["extra_headers"] == {
        "X-Otto-Tool-Contract": "v1",
        "X-Otto-Call-Role": "primary",
    }
