from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext


TOOL = {
    "type": "function",
    "function": {
        "name": "tool_fixture",
        "description": "sanitized fixture",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _tool_response():
    return {
        "id": "response-fixture-tool",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-fixture",
                            "type": "function",
                            "function": {
                                "name": "tool_fixture",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _text_response():
    return {
        "id": "response-fixture-final",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
    }


class _GatewayHandler(BaseHTTPRequestHandler):
    requests = []
    queued_responses = []

    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": "model-fixture"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode())
        type(self).requests.append(
            {
                "payload": payload,
                "contract": self.headers.get("X-Otto-Tool-Contract"),
                "role": self.headers.get("X-Otto-Call-Role"),
            }
        )
        response = type(self).queued_responses.pop(0)
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Otto-Tool-Contract", "v1")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.fixture
def lifecycle_agent(tmp_path, monkeypatch):
    from run_agent import AIAgent

    _GatewayHandler.requests = []
    _GatewayHandler.queued_responses = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))

    agent = AIAgent(
        api_key="fixture-key",
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        provider="otto",
        model="model-fixture",
        max_iterations=4,
        enabled_toolsets=[],
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        save_trajectories=False,
        platform="cli",
    )
    agent.tools = [TOOL]
    agent.valid_tool_names = {"tool_fixture"}
    agent._disable_streaming = True

    def execute_tools(assistant_message, messages, *_args):
        for call in assistant_message.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "name": call.function.name,
                    "tool_call_id": call.id,
                    "content": "result-fixture",
                }
            )

    agent._execute_tool_calls = execute_tools
    try:
        yield agent, _GatewayHandler
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("policy", "expected_choice"),
    [
        (ToolChoicePolicy(mode="required"), "required"),
        (
            ToolChoicePolicy(mode="named", name="tool_fixture"),
            {"type": "function", "function": {"name": "tool_fixture"}},
        ),
    ],
)
def test_conversation_loop_tool_policy_initial_and_post_tool_lifecycle(
    lifecycle_agent, policy, expected_choice
):
    agent, gateway = lifecycle_agent
    gateway.queued_responses = [_tool_response(), _text_response()]
    context = ToolOperationContext.create(
        policy,
        operation_id="operation-fixture",
        otto_contract_version="v1",
    )

    result = agent.run_conversation(
        "use the fixture tool",
        conversation_history=[],
        task_id="task-fixture",
        tool_operation_context=context,
    )

    assert result["final_response"] == "done"
    assert len(gateway.requests) == 2
    initial, post_tool = gateway.requests
    assert initial["payload"]["tool_choice"] == expected_choice
    assert (initial["contract"], initial["role"]) == ("v1", "primary")
    assert post_tool["payload"]["tool_choice"] == "auto"
    assert (post_tool["contract"], post_tool["role"]) == ("v1", "post_tool")
    assert initial["payload"]["messages"][0] == post_tool["payload"]["messages"][0]
    assert initial["payload"]["tools"] == post_tool["payload"]["tools"]
    assert "tool_operation_context" not in agent.__dict__


def test_conversation_loop_tool_policy_network_retry_reuses_same_context(
    lifecycle_agent, monkeypatch
):
    agent, _gateway = lifecycle_agent
    contexts = []
    attempts = []
    original_builder = agent._build_api_kwargs

    def capture_builder(*args, **kwargs):
        contexts.append(kwargs.get("attempt_context"))
        return original_builder(*args, **kwargs)

    def flaky_call(_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("sanitized fixture failure")
        return type(
            "ResponseFixture",
            (),
            {
                "choices": [
                    type(
                        "ChoiceFixture",
                        (),
                        {
                            "message": type(
                                "MessageFixture",
                                (),
                                {"role": "assistant", "content": "done", "tool_calls": None},
                            )(),
                            "finish_reason": "stop",
                        },
                    )()
                ],
                "usage": None,
            },
        )()

    agent._build_api_kwargs = capture_builder
    agent._interruptible_api_call = flaky_call
    monkeypatch.setattr("agent.conversation_loop.time.sleep", lambda *_args: None)
    context = ToolOperationContext.create(ToolChoicePolicy(mode="required"))

    result = agent.run_conversation(
        "retry fixture",
        conversation_history=[],
        task_id="task-fixture-retry",
        tool_operation_context=context,
    )

    assert result["final_response"] == "done"
    assert len(contexts) >= 2
    assert contexts[0] is context
    assert contexts[1] is context
    assert "tool_operation_context" not in agent.__dict__


def test_conversation_loop_tool_policy_fallback_is_rejected_for_mandatory_operation():
    from agent.conversation_loop import _tool_policy_allows_fallback

    required = ToolOperationContext.create(ToolChoicePolicy(mode="required"))
    post_tool = required.after_structured_tool_call()

    assert _tool_policy_allows_fallback(required) is False
    assert _tool_policy_allows_fallback(post_tool) is True
    assert _tool_policy_allows_fallback(None) is True


def test_conversation_loop_tool_policy_cancellation_leaves_no_agent_state(
    lifecycle_agent,
):
    agent, _gateway = lifecycle_agent

    def cancel(_kwargs):
        raise InterruptedError("sanitized fixture cancellation")

    agent._interruptible_api_call = cancel
    context = ToolOperationContext.create(ToolChoicePolicy(mode="required"))

    result = agent.run_conversation(
        "cancel fixture",
        conversation_history=[],
        task_id="task-fixture-cancel",
        tool_operation_context=context,
    )

    assert result.get("interrupted") is True
    assert "tool_operation_context" not in agent.__dict__


def test_conversation_loop_tool_policy_max_iterations_leaves_no_agent_state(
    lifecycle_agent,
):
    agent, gateway = lifecycle_agent
    agent.max_iterations = 1
    gateway.queued_responses = [_tool_response()]
    context = ToolOperationContext.create(
        ToolChoicePolicy(mode="required"),
        otto_contract_version="v1",
    )

    result = agent.run_conversation(
        "budget fixture",
        conversation_history=[],
        task_id="task-fixture-budget",
        tool_operation_context=context,
    )

    assert result.get("completed") is False
    assert "tool_operation_context" not in agent.__dict__


def test_conversation_loop_tool_policy_concurrent_builds_are_isolated(lifecycle_agent):
    agent, _gateway = lifecycle_agent
    barrier = threading.Barrier(2)
    seen = {}

    def build(label, context):
        barrier.wait()
        kwargs = agent._build_api_kwargs(
            [{"role": "user", "content": label}],
            tools_for_api=[TOOL],
            attempt_context=context,
        )
        seen[label] = kwargs["tool_choice"]

    required = ToolOperationContext.create(ToolChoicePolicy(mode="required"))
    named = ToolOperationContext.create(
        ToolChoicePolicy(mode="named", name="tool_fixture")
    )
    threads = [
        threading.Thread(target=build, args=("required", required)),
        threading.Thread(target=build, args=("named", named)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {
        "required": "required",
        "named": {"type": "function", "function": {"name": "tool_fixture"}},
    }
    assert "tool_operation_context" not in agent.__dict__
