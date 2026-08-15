from __future__ import annotations

from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext
from agent.tool_contract_telemetry import build_tool_contract_event
from tests.agent.test_tool_choice_lifecycle import (
    _text_response,
    _tool_response,
    lifecycle_agent,
)


def test_tool_contract_telemetry_is_bounded_and_content_free():
    context = ToolOperationContext.create(
        ToolChoicePolicy(mode="named", name="tool_fixture"),
        operation_id="private-operation-fixture",
        otto_contract_version="v1",
    )

    event = build_tool_contract_event(
        context,
        requested_model="private-project-model-fixture",
        model_selection="explicit",
        transport="private-transport-fixture",
        echo=True,
        structured_call=True,
        terminal_code="private-error-fixture",
        retry_decision="private-retry-fixture",
        fallback_decision="private-fallback-fixture",
    )

    assert set(event) == {
        "event",
        "operation",
        "call_role",
        "requested_model",
        "model_selection",
        "policy",
        "contract",
        "transport",
        "echo",
        "structured_call",
        "post_tool",
        "terminal_code",
        "retry_decision",
        "fallback_decision",
    }
    assert event["event"] == "tool_contract"
    assert event["operation"].startswith("sha256:")
    assert event["requested_model"].startswith("sha256:")
    assert event["call_role"] == "primary"
    assert event["policy"] == "named"
    assert event["contract"] == "v1"
    assert event["transport"] == "other"
    assert event["terminal_code"] is None
    assert event["retry_decision"] == "none"
    assert event["fallback_decision"] == "not_considered"
    serialized = repr(event)
    for secret in (
        "private-operation-fixture",
        "private-project-model-fixture",
        "private-transport-fixture",
        "private-error-fixture",
        "private-retry-fixture",
        "private-fallback-fixture",
        "tool_fixture",
    ):
        assert secret not in serialized


def test_call_role_is_diagnostic_only_and_does_not_change_tool_choice():
    from agent.transports import get_transport
    import agent.transports.chat_completions  # noqa: F401

    transport = get_transport("chat_completions")
    messages = [{"role": "user", "content": "fixture"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "tool_fixture",
                "description": "sanitized fixture",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    primary = ToolOperationContext.create(
        ToolChoicePolicy(),
        call_role="primary",
        otto_contract_version="v1",
    )
    post_tool = ToolOperationContext.create(
        ToolChoicePolicy(),
        call_role="post_tool",
        otto_contract_version="v1",
    )

    primary_body = transport.build_kwargs(
        model="model-fixture",
        messages=messages,
        tools=tools,
        attempt_context=primary,
    )
    post_tool_body = transport.build_kwargs(
        model="model-fixture",
        messages=messages,
        tools=tools,
        attempt_context=post_tool,
    )

    assert primary_body == post_tool_body
    assert primary_body["tool_choice"] == "auto"


def test_conversation_emits_primary_and_post_tool_diagnostics(
    lifecycle_agent, monkeypatch
):
    agent, gateway = lifecycle_agent
    gateway.queued_responses = [_tool_response(), _text_response()]
    events = []
    monkeypatch.setattr("agent.monitoring.emitter.emit", events.append)
    context = ToolOperationContext.create(
        ToolChoicePolicy(mode="required"),
        operation_id="operation-fixture",
        otto_contract_version="v1",
    )

    result = agent.run_conversation(
        "telemetry fixture",
        conversation_history=[],
        task_id="task-fixture-telemetry",
        tool_operation_context=context,
    )

    assert result["final_response"] == "done"
    contract_events = [event for event in events if event["event"] == "tool_contract"]
    assert len(contract_events) == 2
    initial, post_tool = contract_events
    assert initial["call_role"] == "primary"
    assert initial["policy"] == "required"
    assert initial["echo"] is True
    assert initial["structured_call"] is True
    assert initial["fallback_decision"] == "blocked"
    assert post_tool["call_role"] == "post_tool"
    assert post_tool["policy"] == "auto"
    assert post_tool["echo"] is True
    assert post_tool["structured_call"] is False
    assert post_tool["post_tool"] is True
