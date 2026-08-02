"""Contracts for isolated host-owned agents exposed to trusted plugins."""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import psutil

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
    _PluginAgentCancelled,
    _PluginAgentResourceExceeded,
    _MAX_FRAME_BYTES,
    _exchange_worker,
    _read_stream,
)
from agent.structured_output import (
    MAX_CANONICAL_SCHEMA_BYTES,
    MAX_OUTPUT_BYTES,
    StructuredOutputRequest,
    StructuredOutputSchema,
    StructuredOutputStrategy,
    normalize_schema,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tools.managed_process import ProcessResourceLimits, TerminationPolicy
from tools.registry import ToolRegistry


class _WorkerRateLimitError(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__("rate limit exceeded")
        self.response = SimpleNamespace(headers={})
        self.body = {"error": {"message": "rate limit exceeded"}}


def _worker_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="fake-model", usage=None)


def _run_real_worker_retry_cycles(
    monkeypatch,
    *,
    grant: int,
    outcomes: tuple[str, ...] = (),
    recover_primary: bool,
    fallback_model: str | None,
    structured_output: StructuredOutputRequest | None = None,
    sealed_provider_attempt_grant: bool = True,
) -> tuple[dict, list[tuple[str, str]]]:
    """Drive the real worker wrapper and real AIAgent conversation retry loop.

    Only the external provider transport and persistence are replaced. The
    production change this helper protects is removal/bypass of the worker's
    request-wide launch guard: that mutation makes the recorded provider calls
    exceed ``grant`` when the real loop resets its per-cycle retry counter.
    """
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    calls: list[tuple[str, str]] = []
    primary_system_prefix: list[tuple[str, ...]] = []

    class FakeDB:
        def update_system_prompt(self, *_args, **_kwargs):
            return None

        def create_session(self, *_args, **_kwargs):
            return None

    def fake_provider_call(agent, api_kwargs):
        reserve = getattr(agent, "_provider_attempt_reservation_callback", None)
        if reserve is not None:
            reserve()
        calls.append((agent.provider, agent.model))
        messages = api_kwargs["messages"]
        system_prefix = tuple(
            message["content"]
            for message in messages
            if message.get("role") == "system"
        )
        if agent.model == "primary-model":
            if primary_system_prefix:
                assert system_prefix == primary_system_prefix[0]
            else:
                primary_system_prefix.append(system_prefix)
        assert api_kwargs.get("tools") in (None, [])
        assert any(
            "PROVIDER-GRANT-PROMPT-BYTES" in str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user"
        )
        index = len(calls) - 1
        outcome = outcomes[index] if index < len(outcomes) else "timeout"
        if outcome == "timeout":
            raise TimeoutError("provider timed out")
        if outcome == "rate_limit":
            raise _WorkerRateLimitError()
        return _worker_response(outcome)

    fallback_client = MagicMock()
    fallback_client.api_key = "fake-key"
    fallback_client.base_url = "https://fake.invalid/v1"
    fallback_client._custom_headers = None
    fallback_client.default_headers = None
    recovery_available = [recover_primary]

    def recover_once(*_args, **_kwargs):
        if not recovery_available[0]:
            return False
        recovery_available[0] = False
        return True

    monkeypatch.setattr(worker, "_configured_model", lambda _requested: "primary-model")
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake-provider",
            "model": "primary-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "fake-key",
        },
    )
    monkeypatch.setattr(run_agent, "OpenAI", lambda **kwargs: MagicMock())
    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        run_agent, "check_toolset_requirements", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        run_agent.AIAgent, "_interruptible_api_call", fake_provider_call
    )
    monkeypatch.setattr(
        run_agent.AIAgent,
        "_try_recover_primary_transport",
        recover_once,
    )
    monkeypatch.setattr(run_agent.AIAgent, "_persist_session", lambda *args: None)
    monkeypatch.setattr(run_agent.AIAgent, "_save_trajectory", lambda *args: None)
    monkeypatch.setattr(
        run_agent.AIAgent, "_cleanup_task_resources", lambda *args: None
    )
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *args, **kwargs: (fallback_client, fallback_model or "fallback-model"),
    )
    monkeypatch.setattr(
        "hermes_cli.model_normalize.normalize_model_for_provider",
        lambda model, _provider: model,
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length", lambda *_args, **_kwargs: 200000
    )
    monkeypatch.setattr(
        "agent.conversation_loop.jittered_backoff", lambda *args, **kwargs: 0.0
    )
    monkeypatch.setattr(
        "agent.conversation_loop.adaptive_rate_limit_backoff",
        lambda *args, **kwargs: (0.0, None),
    )

    request = PluginAgentRunRequest(
        prompt="PROVIDER-GRANT-PROMPT-BYTES",
        allowed_tools=(),
        fallback_model=fallback_model,
        max_api_attempts=grant,
        structured_output=structured_output,
        sealed_provider_attempt_grant=sealed_provider_attempt_grant,
    )
    result = worker._run({"plugin_id": "test-plugin", "request": request.to_wire()})
    return result, calls


def _register(registry: ToolRegistry, name: str) -> None:
    registry.register(
        name=name,
        toolset="test",
        schema={"name": name, "description": name, "parameters": {"type": "object"}},
        handler=lambda args: name,
    )


def _structured_request(
    strategy: StructuredOutputStrategy = StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
    *,
    output_bytes_limit: int = 321,
) -> StructuredOutputRequest:
    return StructuredOutputRequest(
        schema=normalize_schema(
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            }
        ),
        strategy=strategy,
        adapter_version=1,
        output_bytes_limit=output_bytes_limit,
    )


def test_worker_stream_reader_is_bounded_and_stoppable_under_backpressure() -> None:
    class FloodStream:
        def __init__(self) -> None:
            self.read_sizes: list[int] = []

        def readline(self, size: int = -1) -> str:
            self.read_sizes.append(size)
            return "frame\n"

    stream = FloodStream()
    events: queue.Queue = queue.Queue(maxsize=1)
    stopped = threading.Event()
    reader = threading.Thread(
        target=_read_stream,
        args=(stream, events, "stdout"),
        kwargs={"stopped": stopped},
    )
    reader.start()
    deadline = time.monotonic() + 1
    while len(stream.read_sizes) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)

    stopped.set()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert stream.read_sizes
    assert set(stream.read_sizes) == {_MAX_FRAME_BYTES + 1}


def test_allowed_and_denied_tools_are_enforced_before_first_call() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    _register(registry, "terminal")

    with registry.scoped_names(
        allowed_names={"read_file", "terminal"},
        denied_names={"terminal"},
    ):
        assert registry.get_entry("read_file") is not None
        assert registry.get_entry("terminal") is None
        assert registry.get_all_tool_names() == ["read_file"]
        assert (
            registry.dispatch("terminal", {}) == '{"error": "Unknown tool: terminal"}'
        )

    assert registry.get_all_tool_names() == ["read_file", "terminal"]


def test_empty_allowlist_means_no_names_and_deny_is_applied_last() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")

    with registry.scoped_names(allowed_names=set()):
        assert registry.get_entry("read_file") is None
        assert registry.get_definitions({"read_file"}) == []

    with registry.scoped_names(allowed_names={"read_file"}, denied_names={"read_file"}):
        assert registry.get_entry("read_file") is None


def test_scope_generation_changes_on_enter_and_exit_and_restores_after_error() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    before = registry._generation

    with pytest.raises(RuntimeError, match="boom"):
        with registry.scoped_names(allowed_names={"read_file"}):
            assert registry._generation == before + 1
            raise RuntimeError("boom")

    assert registry._generation == before + 2
    assert registry.get_entry("read_file") is not None


def test_incompatible_overlapping_scopes_are_rejected() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")
    _register(registry, "terminal")

    with registry.scoped_names(allowed_names={"read_file"}):
        with pytest.raises(RuntimeError, match="scope"):
            with registry.scoped_names(allowed_names={"terminal"}):
                pass


def test_deferred_registration_remains_hidden_from_queries_and_dispatch() -> None:
    registry = ToolRegistry()
    _register(registry, "read_file")

    with registry.scoped_names(allowed_names={"read_file"}):
        _register(registry, "deferred_tool")
        assert registry.get_entry("deferred_tool") is None
        assert "deferred_tool" not in registry.get_all_tool_names()
        assert registry.get_definitions({"deferred_tool"}) == []
        assert "Unknown tool" in registry.dispatch("deferred_tool", {})

    assert registry.get_entry("deferred_tool") is not None


def test_request_and_result_are_immutable() -> None:
    request = PluginAgentRunRequest(prompt="hello")
    result = PluginAgentRunResult(
        final_response="done",
        session_id="session-1",
        provider="test",
        model="fake",
        status="completed",
        pending_interaction=None,
        usage={"input_tokens": 1},
        audit={"plugin_id": "test-plugin"},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.prompt = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "failed"  # type: ignore[misc]


def test_structured_request_and_result_round_trip_explicit_wire_values() -> None:
    request = PluginAgentRunRequest(
        prompt="return data",
        structured_output=_structured_request(),
        sealed_provider_attempt_grant=True,
    )
    request_wire = request.to_wire()

    assert request_wire["structured_output"]["strategy"] == "prompt_json_schema"
    assert request_wire["sealed_provider_attempt_grant"] is True
    assert isinstance(
        request_wire["structured_output"]["schema"]["canonical_schema_bytes"], str
    )
    restored = PluginAgentRunRequest.from_wire(request_wire)
    assert restored == request
    assert isinstance(restored.structured_output.strategy, StructuredOutputStrategy)
    with pytest.raises(TypeError):
        restored.structured_output.schema.canonical_schema["new"] = True

    result = PluginAgentRunResult(
        final_response='{"answer":"ok"}',
        session_id="session-1",
        provider="fake",
        model="fake-model",
        status="completed",
        pending_interaction=None,
        usage={"input_tokens": 1},
        audit={
            "provider_attempts": 1,
            "model_calls": 1,
            "strategy": "prompt_json_schema",
            "adapter_version": 1,
            "schema_fingerprint": request.structured_output.schema.schema_fingerprint,
            "declaration_source": "managed_loop_default",
        },
        structured_output={
            "provider_attempts": 1,
            "model_calls": 1,
            "strategy": "prompt_json_schema",
            "adapter_version": 1,
            "schema_fingerprint": request.structured_output.schema.schema_fingerprint,
            "declaration_source": "managed_loop_default",
        },
    )
    restored_result = PluginAgentRunResult.from_wire(result.to_wire())
    assert restored_result == result
    with pytest.raises(TypeError):
        restored_result.structured_output["strategy"] = "unsupported"


def test_protocol_v1_accepts_old_frames_without_structured_output() -> None:
    request = PluginAgentRunRequest.from_wire({"prompt": "old client"})
    result = PluginAgentRunResult.from_wire(
        {
            "final_response": "done",
            "session_id": "session-1",
            "provider": "fake",
            "model": "fake-model",
            "status": "completed",
            "pending_interaction": None,
            "usage": {},
            "audit": {},
        }
    )

    assert request.structured_output is None
    assert request.sealed_provider_attempt_grant is False
    assert result.structured_output is None


@pytest.mark.parametrize(
    "wire",
    [
        {"prompt": "x", "unknown": True},
        {
            "prompt": "x",
            "structured_output": {
                "schema": {
                    "canonical_schema": {"type": "object"},
                    "schema_fingerprint": "0" * 64,
                    "canonical_schema_bytes": "e30=",
                    "dialect": "https://json-schema.org/draft/2020-12/schema",
                    "unknown": True,
                },
                "strategy": "prompt_json_schema",
                "adapter_version": 1,
                "output_bytes_limit": 100,
                "canonicalization_version": 1,
            },
        },
    ],
)
def test_protocol_rejects_unknown_fields(wire) -> None:
    with pytest.raises(ValueError, match="unknown"):
        PluginAgentRunRequest.from_wire(wire)


@pytest.mark.parametrize(
    "overrides",
    [
        {"response_format": {"type": "json_object"}},
        {"text": {"format": {"type": "json_schema"}}},
        {"output_config": {"format": {"type": "json_schema"}}},
    ],
)
def test_structured_request_rejects_contradictory_wire_overrides_before_spawn(
    monkeypatch, overrides
) -> None:
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )

    with pytest.raises(ValueError, match="structured output.*override"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(
                prompt="x",
                structured_output=_structured_request(),
                request_overrides=overrides,
            )
        )


def test_ai_agent_forwards_one_stable_structured_contract_to_initialization(
    monkeypatch,
) -> None:
    import agent.agent_init as agent_init
    from run_agent import AIAgent

    captured = {}
    structured = _structured_request()

    def capture_init(agent, **kwargs):
        captured.update(kwargs)
        agent.structured_output = kwargs["structured_output"]

    monkeypatch.setattr(agent_init, "init_agent", capture_init)

    agent = AIAgent(structured_output=structured)

    assert captured["structured_output"] is structured
    assert agent.structured_output is structured


@pytest.mark.parametrize(
    "structured_request",
    [
        StructuredOutputRequest(
            schema=StructuredOutputSchema(
                canonical_schema={"type": "object"},
                schema_fingerprint="0" * 64,
                canonical_schema_bytes=b"x" * (MAX_CANONICAL_SCHEMA_BYTES + 1),
            ),
            strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            adapter_version=1,
        ),
        _structured_request(output_bytes_limit=MAX_OUTPUT_BYTES + 1),
    ],
)
def test_oversized_structured_contract_fails_before_worker_start(
    monkeypatch, structured_request
) -> None:
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )

    with pytest.raises(ValueError, match="structured output"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", structured_output=structured_request)
        )


def test_real_plugin_agent_runner_declares_request_mcp_ownership() -> None:
    assert PluginAgentRunner.starts_request_mcp is True


def test_plugin_runner_returns_usage_without_exposing_credentials(monkeypatch) -> None:
    captured: dict = {}

    def fake_exchange(payload, **kwargs):
        captured.update(payload)
        return {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "used read_file",
                "session_id": "session-1",
                "provider": "fake",
                "model": "fake-model",
                "status": "completed",
                "pending_interaction": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "audit": {"plugin_id": "test-plugin", "tool_names": ["read_file"]},
            },
        }

    monkeypatch.setattr("agent.plugin_agent._exchange_worker", fake_exchange)
    result = PluginAgentRunner("test-plugin").run(
        PluginAgentRunRequest(prompt="Use read_file once", allowed_tools=("read_file",))
    )

    assert result.status == "completed"
    assert result.session_id
    assert result.usage["input_tokens"] == 3
    assert "api_key" not in result.audit
    assert "api_key" not in captured
    assert captured["plugin_id"] == "test-plugin"


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        (None, "structured output evidence is missing"),
        (
            {
                "provider_attempts": 0,
                "model_calls": 0,
                "strategy": "prompt_json_schema",
                "adapter_version": 1,
                "schema_fingerprint": "f" * 64,
                "declaration_source": "managed_loop_default",
            },
            "structured output evidence does not match request",
        ),
    ],
    ids=("missing", "wrong-fingerprint"),
)
def test_parent_rejects_uncorrelated_structured_worker_results(
    monkeypatch, evidence, message
) -> None:
    structured = _structured_request()
    audit = {"plugin_id": "test-plugin"}
    if evidence is not None:
        audit.update(evidence)

    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "",
                "session_id": "worker-session",
                "provider": "fake",
                "model": "fake-model",
                "status": "failed",
                "pending_interaction": None,
                "usage": {},
                "audit": audit,
                "structured_output": evidence,
            },
        },
    )

    with pytest.raises(RuntimeError, match=message):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", structured_output=structured)
        )


@pytest.mark.parametrize(
    ("admitted_strategy", "failure_kind"),
    [
        (
            StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            "structured_output_capability_drift",
        ),
        (
            StructuredOutputStrategy.UNSUPPORTED,
            "structured_output_unsupported",
        ),
    ],
    ids=("capability-drift", "unsupported"),
)
def test_public_runner_returns_typed_structured_negotiation_failures(
    monkeypatch, admitted_strategy, failure_kind
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    monkeypatch.setattr(
        run_agent,
        "AIAgent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent constructed")),
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda payload, **kwargs: {
            "protocol_version": 1,
            "type": "result",
            "result": worker._run(payload),
        },
    )

    result = PluginAgentRunner("test-plugin").run(
        PluginAgentRunRequest(
            prompt="x",
            allowed_tools=(),
            structured_output=_structured_request(admitted_strategy),
        )
    )

    assert result.status == "failed"
    assert result.audit["failure_kind"] == failure_kind
    assert result.structured_output is not None
    assert result.structured_output["provider_attempts"] == 0
    assert result.structured_output["model_calls"] == 0
    assert result.structured_output["strategy"] == "prompt_json_schema"


@pytest.mark.parametrize(
    ("status", "provider_attempts", "model_calls"),
    [("completed", 0, 0), ("failed", 1, 0), ("failed", 0, 1)],
    ids=("wrong-status", "nonzero-provider-attempts", "nonzero-model-calls"),
)
def test_parent_rejects_malformed_typed_structured_failure(
    monkeypatch, status, provider_attempts, model_calls
) -> None:
    admitted = _structured_request()
    evidence = {
        "provider_attempts": provider_attempts,
        "model_calls": model_calls,
        "strategy": "prompt_json_schema",
        "adapter_version": 1,
        "schema_fingerprint": admitted.schema.schema_fingerprint,
        "declaration_source": "managed_loop_default",
    }
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "",
                "session_id": "",
                "provider": "fake",
                "model": "fake-model",
                "status": status,
                "pending_interaction": None,
                "usage": {},
                "audit": {
                    "plugin_id": "test-plugin",
                    "failure_kind": "structured_output_capability_drift",
                    **evidence,
                },
                "structured_output": evidence,
            },
        },
    )

    with pytest.raises(RuntimeError, match="structured output negotiation failure"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", structured_output=admitted)
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("adapter_version", True),
        ("adapter_version", 0),
        ("adapter_version", "1"),
        ("canonicalization_version", True),
        ("canonicalization_version", 0),
        ("canonicalization_version", "1"),
    ],
)
def test_structured_request_wire_requires_exact_positive_integer_versions(
    field, invalid
) -> None:
    wire = PluginAgentRunRequest(
        prompt="x", structured_output=_structured_request()
    ).to_wire()
    wire["structured_output"][field] = invalid

    with pytest.raises(ValueError, match="structured output.*version"):
        PluginAgentRunRequest.from_wire(wire)


@pytest.mark.parametrize("invalid", [True, 0, "1"])
def test_structured_result_evidence_requires_positive_integer_adapter_version(
    invalid
) -> None:
    request = _structured_request()
    evidence = {
        "provider_attempts": 0,
        "model_calls": 0,
        "strategy": "prompt_json_schema",
        "adapter_version": invalid,
        "schema_fingerprint": request.schema.schema_fingerprint,
        "declaration_source": "managed_loop_default",
    }

    with pytest.raises(ValueError, match="structured output evidence adapter_version"):
        PluginAgentRunResult.from_wire(
            {
                "final_response": "",
                "session_id": "worker-session",
                "provider": "fake",
                "model": "fake-model",
                "status": "failed",
                "pending_interaction": None,
                "usage": {},
                "audit": dict(evidence),
                "structured_output": evidence,
            }
        )


def test_direct_worker_rejects_overdeep_wire_schema_without_recursion_error() -> None:
    import agent.plugin_agent_worker as worker

    nested = {}
    for _ in range(1_500):
        nested = {"allOf": [nested]}
    wire = PluginAgentRunRequest(
        prompt="x", structured_output=_structured_request()
    ).to_wire()
    wire["structured_output"]["schema"]["canonical_schema"] = nested

    with pytest.raises(ValueError, match="depth"):
        worker._run({"plugin_id": "test-plugin", "request": wire})


def test_direct_worker_preflights_oversized_base64_before_decode() -> None:
    import agent.plugin_agent_worker as worker

    wire = PluginAgentRunRequest(
        prompt="x", structured_output=_structured_request()
    ).to_wire()
    maximum_encoded = ((MAX_CANONICAL_SCHEMA_BYTES + 2) // 3) * 4
    wire["structured_output"]["schema"]["canonical_schema_bytes"] = (
        "A" * (maximum_encoded + 4)
    )

    with pytest.raises(ValueError, match="encoded schema.*limit"):
        worker._run({"plugin_id": "test-plugin", "request": wire})


@pytest.mark.parametrize(
    ("run_request", "message"),
    [
        (PluginAgentRunRequest(prompt=""), "prompt"),
        (PluginAgentRunRequest(prompt="x", max_iterations=0), "max_iterations"),
        (PluginAgentRunRequest(prompt="x", max_iterations=1.5), "max_iterations"),
        (PluginAgentRunRequest(prompt="x", max_api_attempts=0), "API attempts"),
        (
            PluginAgentRunRequest(prompt="x", cooperative_shutdown_seconds=0),
            "cooperative shutdown",
        ),
        (PluginAgentRunRequest(prompt="x", idle_timeout_seconds=0), "idle"),
        (PluginAgentRunRequest(prompt="x", idle_timeout_seconds=float("nan")), "idle"),
        (PluginAgentRunRequest(prompt="x", wall_timeout_seconds=float("inf")), "wall"),
        (PluginAgentRunRequest(prompt="x", wall_timeout_seconds=-1), "wall"),
        (PluginAgentRunRequest(prompt="x", max_descendants=-1), "descendants"),
        (
            PluginAgentRunRequest(
                prompt="x", idle_timeout_seconds=10, wall_timeout_seconds=5
            ),
            "idle",
        ),
        (
            PluginAgentRunRequest(
                prompt="x",
                idle_timeout_seconds=5,
                provider_request_timeout_seconds=20,
                wall_timeout_seconds=10,
            ),
            "provider",
        ),
    ],
)
def test_invalid_requests_fail_before_worker_start(
    monkeypatch, run_request, message
) -> None:
    started = False

    def should_not_start(*args, **kwargs):
        nonlocal started
        started = True
        raise AssertionError("worker started")

    monkeypatch.setattr("agent.plugin_agent._exchange_worker", should_not_start)

    with pytest.raises(ValueError, match=message):
        PluginAgentRunner("test-plugin").run(run_request)
    assert started is False


def test_invalid_workdir_and_shared_session_fail_before_worker_start(
    monkeypatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x")
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )

    with pytest.raises(ValueError, match="workdir"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", workdir=file_path)
        )
    with pytest.raises(ValueError, match="session_id"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", context_mode="shared", session_id=None)
        )


@pytest.mark.parametrize("field", ["provider", "model"])
def test_provider_and_model_overrides_are_fail_closed_before_worker(
    monkeypatch, field: str
) -> None:
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )
    request = PluginAgentRunRequest(prompt="x", **{field: "untrusted-override"})
    with pytest.raises(PermissionError, match=field):
        PluginAgentRunner("test-plugin").run(request)


def test_parent_workdir_and_environment_are_unchanged(monkeypatch) -> None:
    cwd = os.getcwd()
    env = dict(os.environ)

    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda payload, **kwargs: {
            "protocol_version": 1,
            "type": "result",
            "result": {
                "final_response": "done",
                "session_id": "s",
                "provider": "fake",
                "model": "fake",
                "status": "completed",
                "pending_interaction": None,
                "usage": {},
                "audit": {},
            },
        },
    )

    PluginAgentRunner("test-plugin").run(PluginAgentRunRequest(prompt="x"))

    assert os.getcwd() == cwd
    assert dict(os.environ) == env


def test_plugin_context_agent_is_lazy_and_bound_to_manifest_key() -> None:
    manifest = PluginManifest(
        name="bare-name", source="test", key="workflow/test-plugin"
    )
    ctx = PluginContext(manifest, PluginManager())

    first = ctx.agent
    second = ctx.agent

    assert first is second
    assert isinstance(first, PluginAgentRunner)
    assert first.plugin_id == "workflow/test-plugin"


def test_real_workers_are_process_isolated_and_unknown_tools_fail_before_billing() -> (
    None
):
    import model_tools
    from tools.registry import registry

    cwd = os.getcwd()
    env = dict(os.environ)
    generation = registry._generation
    resolved_names = list(model_tools._last_resolved_tool_names)

    def run(name: str) -> PluginAgentRunResult:
        return PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(
                prompt="must not reach a provider",
                allowed_tools=(name,),
                idle_timeout_seconds=15,
                wall_timeout_seconds=30,
                provider_request_timeout_seconds=10,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("unknown_worker_a", "unknown_worker_b")))

    assert all(result.status == "failed" for result in results)
    assert all(result.audit["failure_kind"] == "ValueError" for result in results)
    assert all("unknown tool" in result.audit["error"] for result in results)
    assert registry._generation == generation
    assert model_tools._last_resolved_tool_names == resolved_names
    assert os.getcwd() == cwd
    assert dict(os.environ) == env


def test_worker_installs_fail_closed_dangerous_approval(monkeypatch) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent
    from tools.terminal_tool import _get_approval_callback

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "worker-session"
            self.provider = "fake"
            self.model = "fake"
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0

        def run_conversation(self, prompt, conversation_history=None):
            assert self._api_max_retries == 2
            callback = _get_approval_callback()
            assert callback is not None
            assert callback("rm -rf /tmp/example", "dangerous") == "deny"
            return {"final_response": "denied", "api_calls": 0}

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {"provider": "fake", "base_url": "", "api_key": "secret"},
    )
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)

    result = worker._run({
        "plugin_id": "test-plugin",
        "request": dataclasses.asdict(
            PluginAgentRunRequest(
                prompt="attempt dangerous command",
                allowed_tools=(),
                max_api_attempts=2,
            )
        ),
    })

    assert result["status"] == "paused"
    assert result["pending_interaction"]["kind"] == "approval"
    assert "action_digest" in result["pending_interaction"]
    assert "api_key" not in result["audit"]


def test_worker_consumes_exact_approval_digest_once(monkeypatch) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent
    from tools.terminal_tool import _get_approval_callback

    command = "rm -rf /tmp/example"
    description = "dangerous"
    safe = {"command": command, "description": description}
    digest = hashlib.sha256(
        json.dumps(["approval", safe], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "worker-session"
            self.provider = "fake"
            self.model = "fake"
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0

        def run_conversation(self, prompt, conversation_history=None):
            callback = _get_approval_callback()
            assert callback is not None
            assert callback(command, description) == "once"
            assert callback(command, description) == "deny"
            return {"final_response": "one-shot", "api_calls": 0}

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {"provider": "fake", "base_url": "", "api_key": "secret"},
    )
    emitted = []
    monkeypatch.setattr(
        worker, "_emit", lambda kind, **payload: emitted.append((kind, payload))
    )

    result = worker._run({
        "plugin_id": "test-plugin",
        "request": dataclasses.asdict(
            PluginAgentRunRequest(
                prompt="attempt dangerous command",
                allowed_tools=(),
                approved_action_digest=digest,
            )
        ),
    })

    assert result["status"] == "paused"
    assert result["pending_interaction"]["action_digest"] == digest
    assert [kind for kind, _payload in emitted].count("interaction") == 1


def test_prompt_structured_output_adapts_only_initial_user_message(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    captured = {}
    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier response"},
    ]

    class FakeDB:
        def get_session(self, session_id):
            return {"id": session_id}

        def get_messages_as_conversation(self, session_id):
            assert session_id == "shared-session"
            return history

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.session_id = kwargs["session_id"]
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 3
            self.session_output_tokens = 2
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0

        def run_conversation(self, prompt, conversation_history=None):
            captured["prompt"] = prompt
            captured["history"] = conversation_history
            return {"final_response": '{"answer":"ok"}', "api_calls": 1}

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)
    structured = _structured_request()
    request = PluginAgentRunRequest(
        prompt="Return the answer",
        context_mode="shared",
        session_id="shared-session",
        allowed_tools=(),
        ephemeral_system_prompt="SYSTEM-BYTES-STAY-STABLE",
        structured_output=structured,
    )

    result = worker._run(
        {"plugin_id": "test-plugin", "request": request.to_wire()}
    )

    prompt = captured["prompt"]
    assert prompt.startswith("Return the answer\n\n")
    assert prompt.count("<hermes_structured_output") == 1
    assert prompt.count("</hermes_structured_output>") == 1
    assert structured.schema.canonical_schema_bytes.decode("utf-8") in prompt
    assert len(prompt.encode("utf-8")) <= (
        len(request.prompt.encode("utf-8"))
        + MAX_CANONICAL_SCHEMA_BYTES
        + 512
    )
    assert captured["history"] is history
    assert history == [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier response"},
    ]
    assert captured["init"]["ephemeral_system_prompt"] == "SYSTEM-BYTES-STAY-STABLE"
    assert captured["init"]["structured_output"] == structured
    assert result["status"] == "completed"
    assert result["structured_output"] == {
        "provider_attempts": 1,
        "model_calls": 1,
        "strategy": "prompt_json_schema",
        "adapter_version": 1,
        "schema_fingerprint": structured.schema.schema_fingerprint,
        "declaration_source": "managed_loop_default",
    }
    assert result["audit"]["provider_attempts"] == 1
    assert result["audit"]["model_calls"] == 1
    assert result["audit"]["api_mode"] == "chat_completions"


def test_structured_capability_drift_returns_zero_attempt_evidence_before_agent(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    monkeypatch.setattr(
        run_agent,
        "AIAgent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent constructed")),
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    admitted = _structured_request(StructuredOutputStrategy.NATIVE_JSON_SCHEMA)

    result = worker._run(
        {
            "plugin_id": "test-plugin",
            "request": PluginAgentRunRequest(
                prompt="x", allowed_tools=(), structured_output=admitted
            ).to_wire(),
        }
    )

    assert result["status"] == "failed"
    assert result["audit"]["failure_kind"] == "structured_output_capability_drift"
    assert result["audit"]["provider_attempts"] == 0
    assert result["audit"]["model_calls"] == 0
    assert result["audit"]["api_mode"] == "chat_completions"
    assert result["structured_output"] == {
        "provider_attempts": 0,
        "model_calls": 0,
        "strategy": "prompt_json_schema",
        "adapter_version": 1,
        "schema_fingerprint": admitted.schema.schema_fingerprint,
        "declaration_source": "managed_loop_default",
    }


def test_worker_rejects_mismatched_resolved_decision_fingerprint(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    original_resolver = runtime_provider.resolve_structured_output_capability

    def mismatched_fingerprint(*args, **kwargs):
        decision = original_resolver(*args, **kwargs)
        return dataclasses.replace(decision, schema_fingerprint="f" * 64)

    monkeypatch.setattr(
        run_agent,
        "AIAgent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent constructed")),
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_structured_output_capability",
        mismatched_fingerprint,
    )
    admitted = _structured_request()

    result = worker._run(
        {
            "plugin_id": "test-plugin",
            "request": PluginAgentRunRequest(
                prompt="x", allowed_tools=(), structured_output=admitted
            ).to_wire(),
        }
    )

    assert result["status"] == "failed"
    assert result["audit"]["failure_kind"] == "structured_output_capability_drift"
    assert result["audit"]["provider_attempts"] == 0
    assert result["audit"]["model_calls"] == 0
    assert result["structured_output"]["schema_fingerprint"] == "f" * 64


def test_unsupported_structured_request_fails_before_agent_or_provider(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import run_agent

    monkeypatch.setattr(
        run_agent,
        "AIAgent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent constructed")),
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    unsupported = _structured_request(StructuredOutputStrategy.UNSUPPORTED)

    result = worker._run(
        {
            "plugin_id": "test-plugin",
            "request": PluginAgentRunRequest(
                prompt="x", allowed_tools=(), structured_output=unsupported
            ).to_wire(),
        }
    )

    assert result["status"] == "failed"
    assert result["audit"]["failure_kind"] == "structured_output_unsupported"
    assert result["audit"]["provider_attempts"] == 0
    assert result["audit"]["model_calls"] == 0


def test_structured_provider_exception_keeps_exact_bounded_attempt_evidence(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "worker-session"
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._api_call_count = 0

        def _interruptible_api_call(self, _kwargs):
            raise RuntimeError("bounded-provider-failure")

        def run_conversation(self, prompt, conversation_history=None):
            self._api_call_count = 1
            self._interruptible_api_call({})

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "fake",
            "model": "fake-model",
            "api_mode": "chat_completions",
            "base_url": "https://fake.invalid/v1",
            "api_key": "secret",
        },
    )
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)
    structured = _structured_request()

    result = worker._run(
        {
            "plugin_id": "test-plugin",
            "request": PluginAgentRunRequest(
                prompt="x", allowed_tools=(), structured_output=structured
            ).to_wire(),
        }
    )

    assert result["status"] == "failed"
    assert result["audit"]["failure_kind"] == "RuntimeError"
    assert result["audit"]["provider_attempts"] == 1
    assert result["audit"]["model_calls"] == 1
    assert result["structured_output"]["schema_fingerprint"] == (
        structured.schema.schema_fingerprint
    )


@pytest.mark.parametrize("grant", range(1, 6))
def test_worker_sealed_provider_grant_caps_recovery_and_fallback_cycles(
    monkeypatch, grant
) -> None:
    from plugins.workflow.executors.base import validated_provider_total_call_count
    from plugins.workflow.models import RetryLedgerGrant

    result, calls = _run_real_worker_retry_cycles(
        monkeypatch,
        grant=grant,
        recover_primary=True,
        fallback_model="fallback-model",
    )

    assert len(calls) == grant
    assert result["status"] == "failed"
    assert result["audit"]["provider_attempts"] == grant
    assert result["audit"]["failure_kind"] == "provider_attempt_grant_exhausted"
    assert all(provider == "fake-provider" for provider, _model in calls)
    assert calls[0] == ("fake-provider", "primary-model")
    assert {model for _provider, model in calls} <= {
        "primary-model",
        "fallback-model",
    }
    if grant >= 3:
        assert ("fake-provider", "fallback-model") in calls
    assert "PROVIDER-GRANT-PROMPT-BYTES" not in repr(result["audit"])
    additional = validated_provider_total_call_count(
        result["audit"]["provider_attempts"], granted_attempts=grant
    )
    assert additional == grant - 1
    ledger = RetryLedgerGrant(
        explicit=True,
        requested_retries=grant - 1,
        requested_total_attempts=grant,
        effective_total_attempts=grant,
        delay_ms=1000,
        on_error="all",
        capped=False,
        retry_consumed=0,
    )
    charge = ledger.charge(additional, provider_attempts_exact=True)
    assert charge.charged_attempts == grant
    assert charge.retry_consumed == grant
    assert charge.remaining_attempts == 0
    assert charge.provider_attempts_exact is True


def test_worker_codex_delegation_reserves_once_per_transport(monkeypatch) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    calls = 0

    class FakeDB:
        pass

    class FakeCodexAgent:
        def __init__(self, **kwargs):
            self.session_id = "codex-session"
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._api_call_count = 0
            self._interrupt_requested = False

        def _interruptible_api_call(self, _kwargs):
            nonlocal calls
            reserve = getattr(
                self, "_provider_attempt_reservation_callback", None
            )
            if reserve is not None:
                reserve()
            calls += 1
            return _worker_response("codex completed")

        def _interruptible_streaming_api_call(self, kwargs):
            return self._interruptible_api_call(kwargs)

        def run_conversation(self, _prompt, conversation_history=None):
            self._interruptible_streaming_api_call({"input": "hello"})
            self._api_call_count = 1
            return {
                "failed": False,
                "api_calls": 1,
                "final_response": "codex completed",
            }

    monkeypatch.setattr(run_agent, "AIAgent", FakeCodexAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(worker, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "api_mode": "codex_responses",
            "base_url": "https://fake.invalid/codex",
            "api_key": "secret",
        },
    )

    result = worker._run(
        {
            "plugin_id": "test-plugin",
            "request": PluginAgentRunRequest(
                prompt="hello",
                allowed_tools=(),
                max_api_attempts=1,
                sealed_provider_attempt_grant=True,
            ).to_wire(),
        }
    )

    assert calls == 1
    assert result["status"] == "completed"
    assert result["audit"]["provider_attempts"] == 1
    assert "failure_kind" not in result["audit"]


def test_worker_fallback_calls_draw_from_same_sealed_provider_grant(
    monkeypatch,
) -> None:
    result, calls = _run_real_worker_retry_cycles(
        monkeypatch,
        grant=3,
        outcomes=("rate_limit", "timeout", "timeout"),
        recover_primary=False,
        fallback_model="fallback-model",
    )

    assert calls == [
        ("fake-provider", "primary-model"),
        ("fake-provider", "fallback-model"),
        ("fake-provider", "fallback-model"),
    ]
    assert result["status"] == "failed"
    assert result["audit"]["provider_attempts"] == 3
    assert result["audit"]["failure_kind"] == "provider_attempt_grant_exhausted"


def test_legacy_plugin_agent_retains_recovery_and_fallback_cycles(monkeypatch) -> None:
    result, calls = _run_real_worker_retry_cycles(
        monkeypatch,
        grant=2,
        outcomes=("timeout", "timeout", "rate_limit", "legacy fallback completed"),
        recover_primary=True,
        fallback_model="fallback-model",
        sealed_provider_attempt_grant=False,
    )

    assert calls == [
        ("fake-provider", "primary-model"),
        ("fake-provider", "primary-model"),
        ("fake-provider", "fallback-model"),
        ("fake-provider", "fallback-model"),
    ]
    assert result["status"] == "completed"
    assert result["final_response"] == "legacy fallback completed"
    assert result["audit"]["provider_attempts"] == 4
    assert "failure_kind" not in result["audit"]


def test_structured_repair_worker_cannot_exceed_its_residual_provider_grant(
    monkeypatch,
) -> None:
    structured = _structured_request()
    result, calls = _run_real_worker_retry_cycles(
        monkeypatch,
        grant=2,
        recover_primary=True,
        fallback_model=None,
        structured_output=structured,
    )

    assert len(calls) == 2
    assert result["status"] == "failed"
    assert result["audit"]["provider_attempts"] == 2
    assert result["audit"]["failure_kind"] == "provider_attempt_grant_exhausted"
    assert result["structured_output"] == {
        "provider_attempts": 2,
        "model_calls": 1,
        "strategy": "prompt_json_schema",
        "adapter_version": 1,
        "schema_fingerprint": structured.schema.schema_fingerprint,
        "declaration_source": "managed_loop_default",
    }


def test_cancelled_worker_launches_no_provider_call(monkeypatch) -> None:
    import agent.plugin_agent_worker as worker

    worker._cancel_event.set()
    try:
        result, calls = _run_real_worker_retry_cycles(
            monkeypatch,
            grant=3,
            recover_primary=True,
            fallback_model="fallback-model",
        )
    finally:
        worker._cancel_event.clear()

    assert calls == []
    assert result["audit"]["provider_attempts"] == 0


def test_approval_digest_is_validated_before_worker_start(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.plugin_agent._exchange_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("started")),
    )

    with pytest.raises(ValueError, match="approved action digest"):
        PluginAgentRunner("test-plugin").run(
            PluginAgentRunRequest(prompt="x", approved_action_digest="not-a-digest")
        )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_timeout_terminates_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "descendant.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "sys.stdin.readline();"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    with pytest.raises(TimeoutError, match="idle timeout"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=0.3,
            wall_timeout_seconds=3,
            worker_argv=[sys.executable, "-c", code],
        )

    deadline = time.monotonic() + 3
    descendant_pid = int(pid_file.read_text())
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(descendant_pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"worker descendant {descendant_pid} survived timeout cleanup")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_cancellation_closes_lifeline_and_terminates_descendants(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "cancelled-descendant.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "sys.stdin.readline();"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    cancelled = threading.Event()

    def cancel_after_spawn() -> None:
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancelled.set()

    threading.Thread(target=cancel_after_spawn, daemon=True).start()
    with pytest.raises(_PluginAgentCancelled):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=5,
            wall_timeout_seconds=10,
            worker_argv=[sys.executable, "-c", code],
            is_cancelled=cancelled.is_set,
        )

    descendant_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            proc = psutil.Process(descendant_pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"worker descendant {descendant_pid} survived cancellation")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descendant probe")
@pytest.mark.live_system_guard_bypass
def test_worker_resource_limit_terminates_descendants(tmp_path: Path) -> None:
    code = (
        "import subprocess,sys,time;sys.stdin.readline();"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "time.sleep(60)"
    )
    with pytest.raises(_PluginAgentResourceExceeded, match="descendant_limit"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=tmp_path,
            idle_timeout_seconds=5,
            wall_timeout_seconds=10,
            worker_argv=[sys.executable, "-c", code],
            resource_limits=ProcessResourceLimits(max_descendants=0),
        )


def test_worker_stderr_is_never_exposed_to_plugin() -> None:
    secret = "sk-test-secret-should-not-escape"
    code = (
        "import sys;sys.stdin.readline();"
        f"sys.stderr.write({secret!r});sys.stderr.flush();"
        "sys.stdout.write('not-json\\n');sys.stdout.flush()"
    )
    with pytest.raises(RuntimeError, match="invalid JSON") as exc_info:
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=None,
            idle_timeout_seconds=10,
            wall_timeout_seconds=20,
            worker_argv=[sys.executable, "-c", code],
        )

    assert secret not in str(exc_info.value)


@pytest.mark.live_system_guard_bypass
def test_worker_stderr_does_not_reset_semantic_idle_deadline() -> None:
    code = (
        "import sys,time;sys.stdin.readline();"
        "\nwhile True:"
        "\n sys.stderr.write('diagnostic\\n');sys.stderr.flush();time.sleep(0.03)"
    )
    with pytest.raises(TimeoutError, match="idle timeout"):
        _exchange_worker(
            {"protocol_version": 1, "type": "run"},
            workdir=None,
            idle_timeout_seconds=0.2,
            wall_timeout_seconds=30,
            worker_argv=[sys.executable, "-c", code],
            termination_policy=TerminationPolicy(
                cooperative_grace_seconds=0.05,
                term_grace_seconds=0.1,
                kill_grace_seconds=0.2,
                wait_timeout_seconds=0.2,
            ),
        )
