import pytest

from agent.tool_choice_policy import (
    ToolChoicePolicy,
    ToolChoicePolicyError,
    ToolOperationContext,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_fixture",
            "description": "sanitized fixture",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
MESSAGES = [{"role": "user", "content": "fixture"}]


def _context(mode, name=None):
    return ToolOperationContext.create(ToolChoicePolicy(mode=mode, name=name))


@pytest.mark.parametrize(
    ("mode", "name", "expected"),
    [
        ("required", None, "required"),
        (
            "named",
            "tool_fixture",
            {"type": "function", "function": {"name": "tool_fixture"}},
        ),
        ("none", None, "none"),
    ],
)
def test_chat_completions_tool_policy_mapping(mode, name, expected):
    from agent.transports.chat_completions import ChatCompletionsTransport

    kwargs = ChatCompletionsTransport().build_kwargs(
        "model-fixture",
        MESSAGES,
        TOOLS,
        attempt_context=_context(mode, name),
    )

    assert kwargs["tool_choice"] == expected


def test_chat_completions_auto_without_tools_omits_tool_choice():
    from agent.transports.chat_completions import ChatCompletionsTransport

    kwargs = ChatCompletionsTransport().build_kwargs(
        "model-fixture",
        MESSAGES,
        [],
        attempt_context=_context("auto"),
    )

    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_anthropic_tool_policy_mapping():
    from agent.transports.anthropic import AnthropicTransport

    transport = AnthropicTransport()

    required = transport.build_kwargs(
        "claude-sonnet-4-20250514",
        MESSAGES,
        TOOLS,
        attempt_context=_context("required"),
    )
    named = transport.build_kwargs(
        "claude-sonnet-4-20250514",
        MESSAGES,
        TOOLS,
        attempt_context=_context("named", "tool_fixture"),
    )
    none = transport.build_kwargs(
        "claude-sonnet-4-20250514",
        MESSAGES,
        TOOLS,
        attempt_context=_context("none"),
    )

    assert required["tool_choice"] == {"type": "any"}
    assert named["tool_choice"] == {"type": "tool", "name": "tool_fixture"}
    assert "tools" not in none
    assert "tool_choice" not in none


def test_responses_tool_policy_mapping_replaces_hard_coded_auto():
    from agent.transports.codex import ResponsesApiTransport

    transport = ResponsesApiTransport()
    required = transport.build_kwargs(
        "model-fixture", MESSAGES, TOOLS, attempt_context=_context("required")
    )
    named = transport.build_kwargs(
        "model-fixture",
        MESSAGES,
        TOOLS,
        attempt_context=_context("named", "tool_fixture"),
    )

    assert required["tool_choice"] == "required"
    assert named["tool_choice"] == {"type": "function", "name": "tool_fixture"}


def test_tool_policy_is_source_of_truth_over_responses_request_overrides():
    from agent.transports.codex import ResponsesApiTransport

    kwargs = ResponsesApiTransport().build_kwargs(
        "model-fixture",
        MESSAGES,
        TOOLS,
        attempt_context=_context("required"),
        request_overrides={"tool_choice": "auto"},
    )

    assert kwargs["tool_choice"] == "required"


def test_bedrock_tool_policy_mapping():
    from agent.transports.bedrock import BedrockTransport

    transport = BedrockTransport()
    required = transport.build_kwargs(
        "us.anthropic.claude-sonnet-4-6",
        MESSAGES,
        TOOLS,
        attempt_context=_context("required"),
    )
    named = transport.build_kwargs(
        "us.anthropic.claude-sonnet-4-6",
        MESSAGES,
        TOOLS,
        attempt_context=_context("named", "tool_fixture"),
    )
    none = transport.build_kwargs(
        "us.anthropic.claude-sonnet-4-6",
        MESSAGES,
        TOOLS,
        attempt_context=_context("none"),
    )

    assert required["toolConfig"]["toolChoice"] == {"any": {}}
    assert named["toolConfig"]["toolChoice"] == {
        "tool": {"name": "tool_fixture"}
    }
    assert "toolConfig" not in none


def test_tool_policy_gemini_named_uses_any_with_allowlisted_name():
    from agent.gemini_native_adapter import build_gemini_request

    request = build_gemini_request(
        messages=MESSAGES,
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "tool_fixture"}},
    )

    assert request["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["tool_fixture"],
        }
    }


@pytest.mark.parametrize(
    "context,tools",
    [
        (_context("required"), []),
        (_context("named", "missing_fixture"), TOOLS),
    ],
)
def test_tool_policy_mandatory_capability_failures_are_explicit(context, tools):
    from agent.transports.chat_completions import ChatCompletionsTransport

    with pytest.raises(ToolChoicePolicyError) as raised:
        ChatCompletionsTransport().build_kwargs(
            "model-fixture", MESSAGES, tools, attempt_context=context
        )

    assert raised.value.code == "mandatory_tool_choice_not_supported"


def test_tool_policy_rejects_mandatory_choice_for_unsupported_bedrock_model():
    from agent.transports.bedrock import BedrockTransport

    with pytest.raises(ToolChoicePolicyError) as raised:
        BedrockTransport().build_kwargs(
            "us.deepseek.r1-v1:0",
            MESSAGES,
            TOOLS,
            attempt_context=_context("required"),
        )

    assert raised.value.code == "mandatory_tool_choice_not_supported"
