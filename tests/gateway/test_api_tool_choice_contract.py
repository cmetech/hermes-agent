import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


def _app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    app.router.add_post("/v1/responses", adapter._handle_responses)
    app.router.add_post("/v1/runs", adapter._handle_runs)
    return app


def _agent_result():
    return (
        {"final_response": "ok", "messages": [], "api_calls": 1},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )


def _agent_contract_failure(code="selected_model_tool_protocol_failed"):
    return (
        {
            "final_response": "",
            "messages": [],
            "api_calls": 1,
            "completed": False,
            "failed": True,
            "error": "The selected model did not complete the required tool protocol.",
            "error_code": code,
        },
        {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
    )


def _chat_body(tool_choice=None):
    body = {
        "model": "explicit-model-fixture",
        "messages": [{"role": "user", "content": "fixture request"}],
    }
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    return body


@pytest_asyncio.fixture
async def adapter():
    instance = APIServerAdapter(PlatformConfig(enabled=True))
    try:
        yield instance
    finally:
        await instance.disconnect()


@pytest.mark.asyncio
async def test_chat_required_with_exact_v1_builds_trusted_operation_context(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body("required"),
                headers={"X-Otto-Tool-Contract": "v1"},
            )

    assert response.status == 200
    context = run_agent.call_args.kwargs["tool_operation_context"]
    assert context.policy.mode == "required"
    assert context.call_role == "primary"
    assert context.otto_contract_version == "v1"


@pytest.mark.asyncio
async def test_chat_named_outer_dispatcher_builds_named_policy(adapter):
    choice = {"type": "function", "function": {"name": "tool_call"}}
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body(choice),
                headers={"X-Otto-Tool-Contract": "v1"},
            )

    assert response.status == 200
    policy = run_agent.call_args.kwargs["tool_operation_context"].policy
    assert policy.mode == "named"
    assert policy.name == "tool_call"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["auto", "none"])
async def test_chat_accepts_nonmandatory_standard_choices(adapter, choice):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            response = await client.post(
                "/v1/chat/completions", json=_chat_body(choice)
            )

    assert response.status == 200
    context = run_agent.call_args.kwargs["tool_operation_context"]
    assert context.policy.mode == choice
    assert context.otto_contract_version is None


@pytest.mark.asyncio
async def test_chat_required_without_v1_remains_direct_provider_policy(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            response = await client.post(
                "/v1/chat/completions", json=_chat_body("required")
            )

    assert response.status == 200
    context = run_agent.call_args.kwargs["tool_operation_context"]
    assert context.policy.mode == "required"
    assert context.otto_contract_version is None


@pytest.mark.asyncio
async def test_invalid_tool_choice_shape_fails_before_agent_dispatch(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(adapter, "_run_agent", new=AsyncMock()) as run_agent:
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body({"type": "function", "function": {}}),
            )
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "invalid_tool_choice"
    run_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_named_tool_fails_after_effective_catalog_is_known(adapter):
    fake_agent = MagicMock()
    fake_agent.valid_tool_names = {"tool_call"}
    fake_agent.tools = [
        {"type": "function", "function": {"name": "tool_call", "parameters": {}}}
    ]
    choice = {
        "type": "function",
        "function": {"name": "unavailable_tool_fixture"},
    }

    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=fake_agent):
            response = await client.post(
                "/v1/chat/completions", json=_chat_body(choice)
            )
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "mandatory_tool_choice_not_supported"
    fake_agent.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_nonempty_unknown_contract_version_fails_closed(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(adapter, "_run_agent", new=AsyncMock()) as run_agent:
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body("required"),
                headers={"X-Otto-Tool-Contract": "v2"},
            )
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "unsupported_tool_contract_version"
    run_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_contract_headers_fail_before_agent_dispatch(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(adapter, "_run_agent", new=AsyncMock()) as run_agent:
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body("required"),
                headers=[
                    ("X-Otto-Tool-Contract", "v1"),
                    ("X-Otto-Tool-Contract", "v2"),
                ],
            )
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "unsupported_tool_contract_version"
    run_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_chat_request_without_policy_preserves_legacy_context(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            first = await client.post(
                "/v1/chat/completions",
                json=_chat_body("required"),
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            second = await client.post(
                "/v1/chat/completions", json=_chat_body()
            )

    assert first.status == second.status == 200
    first_context = run_agent.await_args_list[0].kwargs["tool_operation_context"]
    second_context = run_agent.await_args_list[1].kwargs["tool_operation_context"]
    assert first_context.policy.mode == "required"
    assert second_context is None


@pytest.mark.asyncio
async def test_chat_null_tool_choice_is_legacy_unless_v1_is_requested(adapter):
    body = {
        "model": "explicit-model-fixture",
        "messages": [{"role": "user", "content": "fixture request"}],
        "tool_choice": None,
    }
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            legacy = await client.post("/v1/chat/completions", json=body)
            coordinated = await client.post(
                "/v1/chat/completions",
                json=body,
                headers={"X-Otto-Tool-Contract": "v1"},
            )

    assert legacy.status == coordinated.status == 200
    legacy_context = run_agent.await_args_list[0].kwargs["tool_operation_context"]
    coordinated_context = run_agent.await_args_list[1].kwargs["tool_operation_context"]
    assert legacy_context is None
    assert coordinated_context.policy.mode == "auto"
    assert coordinated_context.otto_contract_version == "v1"


@pytest.mark.asyncio
async def test_responses_required_v1_threads_operation_context(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            response = await client.post(
                "/v1/responses",
                json={
                    "model": "explicit-model-fixture",
                    "input": "fixture request",
                    "tool_choice": "required",
                },
                headers={"X-Otto-Tool-Contract": "v1"},
            )

    assert response.status == 200
    context = run_agent.call_args.kwargs["tool_operation_context"]
    assert context.policy.mode == "required"
    assert context.otto_contract_version == "v1"


@pytest.mark.asyncio
async def test_idempotency_distinguishes_tool_choice_and_contract_version(adapter):
    key = "tool-contract-idempotency-fixture"
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter, "_run_agent", new=AsyncMock(return_value=_agent_result())
        ) as run_agent:
            first = await client.post(
                "/v1/responses",
                json={"input": "fixture", "tool_choice": "required"},
                headers={
                    "Idempotency-Key": key,
                    "X-Otto-Tool-Contract": "v1",
                },
            )
            second = await client.post(
                "/v1/responses",
                json={"input": "fixture", "tool_choice": "auto"},
                headers={"Idempotency-Key": key},
            )

    assert first.status == second.status == 200
    assert run_agent.await_count == 2


@pytest.mark.asyncio
async def test_runs_executor_passes_context_to_conversation(adapter):
    captured = {}
    fake_agent = MagicMock()
    fake_agent.valid_tool_names = {"tool_call"}
    fake_agent.tools = [
        {"type": "function", "function": {"name": "tool_call", "parameters": {}}}
    ]

    def _run_conversation(**kwargs):
        captured.update(kwargs)
        return {"final_response": "ok", "messages": [], "api_calls": 1}

    fake_agent.run_conversation.side_effect = _run_conversation
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(adapter, "_create_agent", return_value=fake_agent):
            response = await client.post(
                "/v1/runs",
                json={"input": "fixture request", "tool_choice": "required"},
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            assert response.status == 202
            await asyncio.gather(*tuple(adapter._background_tasks))

    context = captured["tool_operation_context"]
    assert context.policy.mode == "required"
    assert context.otto_contract_version == "v1"


@pytest.mark.asyncio
async def test_chat_contract_failure_preserves_native_error_code(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(return_value=_agent_contract_failure()),
        ):
            response = await client.post(
                "/v1/chat/completions",
                json=_chat_body("required"),
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            payload = await response.json()

    assert response.status == 502
    assert payload["error"]["code"] == "selected_model_tool_protocol_failed"
    assert payload["error"]["message"] == (
        "The selected model did not complete the required tool protocol."
    )


@pytest.mark.asyncio
async def test_responses_contract_failure_preserves_native_error_code(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(return_value=_agent_contract_failure()),
        ):
            response = await client.post(
                "/v1/responses",
                json={
                    "model": "explicit-model-fixture",
                    "input": "fixture request",
                    "tool_choice": "required",
                },
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            payload = await response.json()

    assert response.status == 502
    assert payload["error"]["code"] == "selected_model_tool_protocol_failed"


@pytest.mark.asyncio
async def test_streaming_contract_failure_emits_error_before_assistant_delta(adapter):
    body = _chat_body("required")
    body["stream"] = True
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(return_value=_agent_contract_failure()),
        ):
            response = await client.post(
                "/v1/chat/completions",
                json=body,
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            wire = await response.text()

    assert '"code": "selected_model_tool_protocol_failed"' in wire
    assert '"content"' not in wire


@pytest.mark.asyncio
async def test_streaming_responses_contract_failure_is_protocol_native(adapter):
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(return_value=_agent_contract_failure()),
        ):
            response = await client.post(
                "/v1/responses",
                json={
                    "model": "explicit-model-fixture",
                    "input": "fixture request",
                    "tool_choice": "required",
                    "stream": True,
                },
                headers={"X-Otto-Tool-Contract": "v1"},
            )
            wire = await response.text()

    assert 'event: response.failed' in wire
    events = [
        json.loads(line.removeprefix("data: "))
        for line in wire.splitlines()
        if line.startswith("data: ")
    ]
    failed = next(event for event in events if event["type"] == "response.failed")
    assert failed["response"]["error"]["code"] == (
        "selected_model_tool_protocol_failed"
    )
    assert 'event: response.output_text.delta' not in wire


@pytest.mark.asyncio
async def test_streaming_chat_policy_error_preserves_typed_code(adapter):
    from agent.tool_choice_policy import ToolChoicePolicyError

    body = _chat_body(
        {
            "type": "function",
            "function": {"name": "unavailable_tool_fixture"},
        }
    )
    body["stream"] = True
    policy_error = ToolChoicePolicyError(
        "mandatory_tool_choice_not_supported",
        "The selected tool is unavailable for this request.",
    )

    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(side_effect=policy_error),
        ):
            response = await client.post("/v1/chat/completions", json=body)
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "mandatory_tool_choice_not_supported"


@pytest.mark.asyncio
async def test_streaming_responses_policy_error_preserves_typed_code(adapter):
    from agent.tool_choice_policy import ToolChoicePolicyError

    policy_error = ToolChoicePolicyError(
        "mandatory_tool_choice_not_supported",
        "The selected tool is unavailable for this request.",
    )
    async with TestClient(TestServer(_app(adapter))) as client:
        with patch.object(
            adapter,
            "_run_agent",
            new=AsyncMock(side_effect=policy_error),
        ):
            response = await client.post(
                "/v1/responses",
                json={
                    "model": "explicit-model-fixture",
                    "input": "fixture request",
                    "tool_choice": {
                        "type": "function",
                        "name": "unavailable_tool_fixture",
                    },
                    "stream": True,
                },
            )
            payload = await response.json()

    assert response.status == 400
    assert payload["error"]["code"] == "mandatory_tool_choice_not_supported"
