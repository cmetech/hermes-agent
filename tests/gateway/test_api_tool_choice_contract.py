import asyncio
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
    assert payload["error"]["code"] == "invalid_tool_choice"
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
async def test_next_chat_request_gets_fresh_auto_no_v1_context(adapter):
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
    assert first_context.operation_id != second_context.operation_id
    assert second_context.policy.mode == "auto"
    assert second_context.otto_contract_version is None


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
