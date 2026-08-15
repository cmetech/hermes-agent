"""Coordinated Hermes/Gateway v1 release gate.

Run only against an explicitly selected released Gateway executable:

    OTTO_GATEWAY_INTEGRATION_BIN=/path/to/gateway pytest -q \
        tests/integration/test_otto_gateway_v1_release.py

All identifiers and payloads are sanitized fixtures. The ACP peer records only
event categories and counts, never request or response content.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext


GATEWAY_BIN = os.environ.get("OTTO_GATEWAY_INTEGRATION_BIN")
pytestmark = pytest.mark.skipif(
    not GATEWAY_BIN,
    reason="set OTTO_GATEWAY_INTEGRATION_BIN to a released Gateway executable",
)

FIXTURE_ACP = Path(__file__).parents[1] / "fixtures" / "fake_kiro_task10.py"
AUTH_VALUE = "Bearer fixture-token"
DISPATCHER_TOOL = {
    "type": "function",
    "function": {
        "name": "tool_call",
        "description": "Dispatch one authorized deferred fixture tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name", "arguments"],
        },
    },
}


def _free_address() -> tuple[str, int]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()


def _event_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)["event"]
        counts[event] = counts.get(event, 0) + 1
    return counts


def _assert_prompt_count(path: Path, expected: int) -> None:
    counts = _event_counts(path)
    assert counts.get("prompt") == expected
    assert set(counts).issubset({"prompt", "cancel"})


@contextmanager
def _released_gateway(
    tmp_path: Path,
    actions: list[dict],
    *,
    binary: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    host, port = _free_address()
    sequence_file = tmp_path / "sequence.json"
    event_file = tmp_path / "events.jsonl"
    stdout_file = tmp_path / "gateway.stdout"
    stderr_file = tmp_path / "gateway.stderr"
    sequence_file.write_text(json.dumps(actions), encoding="utf-8")
    event_file.write_text("", encoding="utf-8")

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HTTP_ADDR": f"{host}:{port}",
        "AUTH_TOKEN": "fixture-token",
        "PII_ENCRYPT_KEY": "fixture-encrypt-key",
        "PRIVACY_ALIAS_KEY": "fixture-alias-key",
        "KIRO_CMD": str(FIXTURE_ACP),
        "KIRO_ARGS": "acp",
        "KIRO_CWD": str(tmp_path),
        "POOL_SIZE": "1",
        "GW_HOME": str(tmp_path / "gateway-home"),
        "OTTO_TASK10_SEQUENCE_FILE": str(sequence_file),
        "OTTO_TASK10_EVENT_FILE": str(event_file),
    }
    env.update(extra_env or {})
    with stdout_file.open("wb") as stdout, stderr_file.open("wb") as stderr:
        process = subprocess.Popen(
            [str(binary or GATEWAY_BIN)],
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        base_url = f"http://{host}:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(
                    f"released Gateway exited during fixture startup: {process.returncode}"
                )
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            pytest.fail("released Gateway did not become healthy on loopback")

        try:
            yield base_url, event_file
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


class _CaptureProxyHandler(BaseHTTPRequestHandler):
    upstream = ""
    echo_override = "preserve"
    requests: list[dict] = []

    def _relay(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        capture = {
            "method": self.command,
            "path": self.path,
            "contract": self.headers.get("X-Otto-Tool-Contract"),
            "role": self.headers.get("X-Otto-Call-Role"),
        }
        if body and self.path.endswith("/chat/completions"):
            payload = json.loads(body)
            capture["tool_choice"] = payload.get("tool_choice")
            capture["tool_count"] = len(payload.get("tools", []))
        type(self).requests.append(capture)

        headers = {"Authorization": AUTH_VALUE}
        for name in ("Content-Type", "X-Otto-Tool-Contract", "X-Otto-Call-Role"):
            if value := self.headers.get(name):
                headers[name] = value
        response = httpx.request(
            self.command,
            type(self).upstream + self.path,
            content=body or None,
            headers=headers,
            timeout=15,
        )
        self.send_response(response.status_code)
        for name in ("Content-Type", "X-Otto-Tool-Contract"):
            if value := response.headers.get(name):
                if (
                    name != "X-Otto-Tool-Contract"
                    or type(self).echo_override == "preserve"
                ):
                    self.send_header(name, value)
        if type(self).echo_override not in {"preserve", "drop"}:
            self.send_header("X-Otto-Tool-Contract", type(self).echo_override)
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        self.wfile.write(response.content)

    do_GET = _relay
    do_POST = _relay

    def log_message(self, *_args) -> None:
        return


@contextmanager
def _capture_proxy(upstream: str, *, echo_override: str = "preserve"):
    _CaptureProxyHandler.upstream = upstream
    _CaptureProxyHandler.echo_override = echo_override
    _CaptureProxyHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_address[1]}",
            _CaptureProxyHandler.requests,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _operation(policy: ToolChoicePolicy) -> ToolOperationContext:
    return ToolOperationContext.create(
        policy,
        operation_id="fixture-operation",
        otto_contract_version="v1",
    )


def _agent(base_url: str, tmp_path: Path, *, streaming: bool):
    from run_agent import AIAgent

    os.environ["HERMES_HOME"] = str(tmp_path / "hermes-home")
    agent = AIAgent(
        api_key="fixture-token",
        base_url=f"{base_url}/v1",
        provider="otto",
        model="selected-model",
        max_iterations=5,
        enabled_toolsets=[],
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        save_trajectories=False,
        platform="cli",
    )
    agent.tools = [DISPATCHER_TOOL]
    agent.valid_tool_names = {"tool_call"}
    agent._disable_streaming = not streaming
    executed: list[str] = []

    def execute_tools(assistant_message, messages, *_args):
        for call in assistant_message.tool_calls:
            executed.append(call.function.name)
            messages.append({
                "role": "tool",
                "name": call.function.name,
                "tool_call_id": call.id,
                "content": "fixture result",
            })

    agent._execute_tool_calls = execute_tools
    return agent, executed


def _chat_requests(captures: list[dict]) -> list[dict]:
    return [item for item in captures if item["path"].endswith("/chat/completions")]


def test_released_gateway_v1_echoes_success_and_typed_validation_error(tmp_path):
    with _released_gateway(
        tmp_path, [{"kind": "text", "text": "fixture response"}]
    ) as (base_url, _events):
        response = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": AUTH_VALUE,
                "X-Otto-Tool-Contract": "v1",
                "X-Otto-Call-Role": "primary",
            },
            json={
                "model": "selected-model",
                "messages": [{"role": "user", "content": "fixture request"}],
                "tool_choice": "none",
            },
            timeout=10,
        )
        assert response.status_code == 200
        assert response.headers.get("X-Otto-Tool-Contract") == "v1"

        invalid = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": AUTH_VALUE,
                "X-Otto-Tool-Contract": "v1",
            },
            json={"model": "selected-model", "messages": []},
            timeout=10,
        )
        assert invalid.status_code == 400
        assert invalid.headers.get("X-Otto-Tool-Contract") == "v1"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("policy", "initial_choice"),
    [
        (ToolChoicePolicy(mode="required"), "required"),
        (
            ToolChoicePolicy(mode="named", name="tool_call"),
            {"type": "function", "function": {"name": "tool_call"}},
        ),
    ],
)
def test_released_gateway_coordinates_hidden_wrapper_tool_and_post_tool_lifecycle(
    tmp_path, policy, initial_choice, streaming
):
    actions = [
        {
            "kind": "text",
            "text": 'Narration. {"tool_call":{"name":"deferred_fixture","arguments":{}}}',
        },
        {
            "kind": "text",
            "text": '{"tool_call":{"name":"deferred_fixture","arguments":{}}}',
        },
        {"kind": "text", "text": "fixture final response"},
    ]
    with _released_gateway(tmp_path, actions) as (gateway_url, event_file):
        with _capture_proxy(gateway_url) as (proxy_url, captures):
            agent, executed = _agent(proxy_url, tmp_path, streaming=streaming)
            result = agent.run_conversation(
                "fixture request",
                conversation_history=[],
                task_id="fixture-task",
                tool_operation_context=_operation(policy),
            )

    assert result["final_response"] == "fixture final response"
    assert executed == ["tool_call"]
    requests = _chat_requests(captures)
    assert len(requests) == 2
    assert requests[0] == {
        "method": "POST",
        "path": "/v1/chat/completions",
        "contract": "v1",
        "role": "primary",
        "tool_choice": initial_choice,
        "tool_count": 1,
    }
    assert requests[1]["contract"] == "v1"
    assert requests[1]["role"] == "post_tool"
    assert requests[1]["tool_choice"] == "auto"
    assert requests[1]["tool_count"] == 1
    _assert_prompt_count(event_file, 3)


def test_released_gateway_optional_documentation_and_auto_model_do_not_correct(
    tmp_path,
):
    documentation = 'The "tool_call" object contains name and arguments fields.'
    with _released_gateway(tmp_path, [{"kind": "text", "text": documentation}]) as (
        base_url,
        event_file,
    ):
        response = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": AUTH_VALUE,
                "X-Otto-Tool-Contract": "v1",
                "X-Otto-Call-Role": "primary",
            },
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "fixture request"}],
                "tools": [DISPATCHER_TOOL],
                "tool_choice": "auto",
            },
            timeout=10,
        )
    assert response.status_code == 200
    assert response.headers.get("X-Otto-Tool-Contract") == "v1"
    assert response.json()["choices"][0]["message"]["content"] == documentation
    _assert_prompt_count(event_file, 1)


def test_released_gateway_second_invalid_mandatory_attempt_is_typed_and_echoed(
    tmp_path,
):
    narrated = 'Narration. {"tool_call":{"name":"deferred_fixture","arguments":{}}}'
    with _released_gateway(
        tmp_path,
        [{"kind": "text", "text": narrated}, {"kind": "text", "text": narrated}],
    ) as (base_url, event_file):
        response = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": AUTH_VALUE,
                "X-Otto-Tool-Contract": "v1",
                "X-Otto-Call-Role": "primary",
            },
            json={
                "model": "selected-model",
                "messages": [{"role": "user", "content": "fixture request"}],
                "tools": [DISPATCHER_TOOL],
                "tool_choice": "required",
            },
            timeout=10,
        )
    assert response.status_code == 502
    assert response.headers.get("X-Otto-Tool-Contract") == "v1"
    assert response.json()["error"]["code"] == "selected_model_tool_protocol_failed"
    _assert_prompt_count(event_file, 2)


def test_released_gateway_post_tool_provenance_refusal_corrects_once(tmp_path):
    actions = [
        {
            "kind": "tool",
            "name": "tool_call",
            "arguments": {"name": "deferred_fixture", "arguments": {}},
        },
        {
            "kind": "text",
            "text": "I cannot use that tool result because the tool event is fabricated.",
        },
        {"kind": "text", "text": "fixture corrected response"},
    ]
    with _released_gateway(tmp_path, actions) as (gateway_url, event_file):
        with _capture_proxy(gateway_url) as (proxy_url, _captures):
            agent, executed = _agent(proxy_url, tmp_path, streaming=False)
            result = agent.run_conversation(
                "fixture request",
                conversation_history=[],
                task_id="fixture-task",
                tool_operation_context=_operation(ToolChoicePolicy(mode="required")),
            )
    assert result["final_response"] == "fixture corrected response"
    assert executed == ["tool_call"]
    _assert_prompt_count(event_file, 3)


def test_released_gateway_post_tool_operational_correction_failure_is_terminal(
    tmp_path,
):
    actions = [
        {
            "kind": "tool",
            "name": "tool_call",
            "arguments": {"name": "deferred_fixture", "arguments": {}},
        },
        {
            "kind": "text",
            "text": "I cannot use that tool result because the tool event is fabricated.",
        },
        {"kind": "exit"},
    ]
    with _released_gateway(tmp_path, actions) as (gateway_url, event_file):
        with _capture_proxy(gateway_url) as (proxy_url, _captures):
            agent, executed = _agent(proxy_url, tmp_path, streaming=False)
            result = agent.run_conversation(
                "fixture request",
                conversation_history=[],
                task_id="fixture-task",
                tool_operation_context=_operation(ToolChoicePolicy(mode="required")),
            )
    assert executed == ["tool_call"]
    assert result.get("error_code") == "selected_model_tool_result_provenance_failed"
    _assert_prompt_count(event_file, 3)


@pytest.mark.parametrize("echo_override", ["drop", "v2"])
@pytest.mark.parametrize("streaming", [False, True])
def test_hermes_rejects_missing_or_wrong_echo_before_tool_execution(
    tmp_path, echo_override, streaming
):
    actions = [
        {
            "kind": "tool",
            "name": "tool_call",
            "arguments": {"name": "deferred_fixture", "arguments": {}},
        }
    ]
    with _released_gateway(tmp_path, actions) as (gateway_url, _event_file):
        with _capture_proxy(gateway_url, echo_override=echo_override) as (
            proxy_url,
            _captures,
        ):
            agent, executed = _agent(proxy_url, tmp_path, streaming=streaming)
            result = agent.run_conversation(
                "fixture request",
                conversation_history=[],
                task_id="fixture-task",
                tool_operation_context=_operation(ToolChoicePolicy(mode="required")),
            )
    assert executed == []
    assert result.get("error_code") == "otto_tool_contract_unavailable"


def test_released_gateway_correction_timeout_is_typed_and_echoed(tmp_path):
    narrated = 'Narration. {"tool_call":{"name":"deferred_fixture","arguments":{}}}'
    with _released_gateway(
        tmp_path,
        [{"kind": "text", "text": narrated}, {"kind": "sleep", "seconds": 3}],
        extra_env={"STREAM_IDLE_TIMEOUT_SEC": "1"},
    ) as (base_url, event_file):
        response = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": AUTH_VALUE,
                "X-Otto-Tool-Contract": "v1",
                "X-Otto-Call-Role": "primary",
            },
            json={
                "model": "selected-model",
                "messages": [{"role": "user", "content": "fixture request"}],
                "tools": [DISPATCHER_TOOL],
                "tool_choice": "required",
            },
            timeout=5,
        )
    assert response.status_code == 502
    assert response.headers.get("X-Otto-Tool-Contract") == "v1"
    assert response.json()["error"]["code"] == "selected_model_tool_protocol_failed"
    _assert_prompt_count(event_file, 2)


def test_released_gateway_client_cancellation_remains_bounded(tmp_path):
    with _released_gateway(
        tmp_path,
        [{"kind": "sleep", "seconds": 2}],
        extra_env={"STREAM_IDLE_TIMEOUT_SEC": "10"},
    ) as (base_url, event_file):
        with pytest.raises(httpx.ReadTimeout):
            httpx.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": AUTH_VALUE,
                    "X-Otto-Tool-Contract": "v1",
                    "X-Otto-Call-Role": "primary",
                },
                json={
                    "model": "selected-model",
                    "messages": [{"role": "user", "content": "fixture request"}],
                    "tools": [DISPATCHER_TOOL],
                    "tool_choice": "required",
                },
                timeout=0.2,
            )
        deadline = time.monotonic() + 4
        while (
            time.monotonic() < deadline and _event_counts(event_file).get("cancel") != 1
        ):
            time.sleep(0.1)
        assert _event_counts(event_file).get("cancel") == 1
        assert httpx.get(f"{base_url}/health", timeout=2).status_code == 200
