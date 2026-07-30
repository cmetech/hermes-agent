from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from agent.plugin_agent import PluginAgentRunResult
from agent.structured_output import StructuredOutputStrategy, normalize_schema
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.models import (
    DeadlineBudget,
    RunExecutionLimits,
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowStructuredOutput,
    freeze_value,
)
from plugins.workflow.resources import VariableContext


class FakeAgentRunner:
    def __init__(
        self,
        *responses: str,
        provider_attempts: tuple[int, ...] | None = None,
        model_calls: tuple[int, ...] | None = None,
        audit_rows: tuple[dict[str, object], ...] | None = None,
    ):
        self.requests = []
        self.responses = list(responses)
        self.provider_attempts = provider_attempts
        self.model_calls = model_calls
        self.audit_rows = audit_rows

    def run(self, request, **_kwargs):
        self.requests.append(request)
        index = len(self.requests) - 1
        output = self.responses.pop(0)
        audit = dict(self.audit_rows[index]) if self.audit_rows else {}
        structured_evidence = None
        if request.structured_output is not None:
            provider_attempts = (
                self.provider_attempts[index] if self.provider_attempts else 1
            )
            model_calls = self.model_calls[index] if self.model_calls else 1
            structured_evidence = {
                "provider_attempts": provider_attempts,
                "model_calls": model_calls,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            audit.setdefault("api_calls", model_calls)
            audit.setdefault("api_mode", "chat_completions")
            audit.update(structured_evidence)
        return PluginAgentRunResult(
            final_response=output,
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 4, "output_tokens": 2},
            audit={"tool_names": list(request.allowed_tools or ()), **audit},
            structured_output=structured_evidence,
        )


def _node(node_id: str, prompt: str, **options) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        node_type="prompt",
        value=prompt,
        depends_on=tuple(options.pop("depends_on", ())),
        source_index=0,
        source_line=1,
        options=freeze_value(options),
    )


def _context(tmp_path: Path, node: WorkflowNode, **kwargs) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir(parents=True, exist_ok=True)
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id=f"attempt-{node.id}",
        workflow_name="ai-demo",
        workflow_options=freeze_value({
            "provider": "fake-provider",
            "model": "fake-model",
        }),
        variable_context=VariableContext(arguments="evidence", workflow_id="run-1"),
        **kwargs,
    )


def _archon_context(
    tmp_path: Path,
    node: WorkflowNode,
    *,
    strategy: StructuredOutputStrategy = StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
    outward_action: bool = False,
    decision_provider: str = "fake-provider",
    decision_model: str = "fake-model",
    decision_api_mode: str = "chat_completions",
    **kwargs,
) -> NodeExecutionContext:
    def thaw(value):
        if isinstance(value, dict) or hasattr(value, "items"):
            return {str(key): thaw(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [thaw(item) for item in value]
        return value

    normalized = normalize_schema(thaw(node.options["output_format"]))
    return _context(
        tmp_path,
        node,
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        structured_output=WorkflowStructuredOutput(
            canonical_schema=normalized.canonical_schema,
            schema_fingerprint=normalized.schema_fingerprint,
        ),
        structured_output_decision=StructuredOutputCapabilityDecision(
            strategy=strategy,
            effective_provider=decision_provider,
            model=decision_model,
            api_mode=decision_api_mode,
            declaration_source="test",
            adapter_version=1,
            schema_fingerprint=normalized.schema_fingerprint,
            rationale="test decision",
        ),
        outward_action=outward_action,
        **kwargs,
    )


def _archon_text_context(
    tmp_path: Path, node: WorkflowNode, **kwargs
) -> NodeExecutionContext:
    return _context(
        tmp_path,
        node,
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        **kwargs,
    )


def _safe_output_component(kind: str, value: str) -> str:
    return f"{kind}-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def test_fresh_prompt_uses_host_runner_and_validates_structured_output(tmp_path):
    runner = FakeAgentRunner('{"answer":"ok","count":2}')
    node = _node(
        "analyze",
        "Analyze $ARGUMENTS",
        context="fresh",
        allowed_tools=["read_file"],
        output_format={
            "type": "object",
            "required": ["answer", "count"],
            "properties": {
                "answer": {"type": "string"},
                "count": {"type": "integer"},
            },
        },
    )

    result = AgentNodeExecutor(runner, profile_name="default").execute(
        _context(tmp_path, node)
    )

    assert result.status == "succeeded"
    assert runner.requests[0].prompt == "Analyze evidence"
    assert runner.requests[0].context_mode == "fresh"
    assert runner.requests[0].allowed_tools == ("read_file",)
    assert result.metadata["session_id"] == "session-1"
    assert result.metadata["cache_fingerprint"]
    output = tmp_path / "run" / result.artifacts[0].relative_path
    assert output.read_text() == '{"answer":"ok","count":2}'


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_snapshotted_skill_uses_authenticated_bytes_without_reopening_source(
    tmp_path, mutation
):
    runner = FakeAgentRunner("done")
    node = _node("analyze", "Analyze", skills=["reports"])
    skill = tmp_path / "run" / "node-skills" / "analyze.md"
    skill.parent.mkdir(parents=True)
    authenticated = b"AUTHENTICATED SKILL"
    skill.write_bytes(authenticated)
    context = _context(
        tmp_path,
        node,
        sealed_resource_paths=frozenset({"node-skills/analyze.md"}),
        sealed_resource_bytes={"node-skills/analyze.md": authenticated},
    )
    if mutation == "delete":
        skill.unlink()
    elif mutation == "rename":
        skill.rename(skill.with_suffix(".gone"))
    else:
        skill.write_text("FORGED SKILL", encoding="utf-8")

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].prompt == "AUTHENTICATED SKILL\n\nAnalyze"


def test_invalid_structured_output_fails_without_repairing_prose(tmp_path):
    runner = FakeAgentRunner('Result: {"answer":"ok"}')
    node = _node(
        "analyze",
        "Analyze",
        output_format={"type": "object", "required": ["answer"]},
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"
    assert not result.artifacts


@pytest.mark.parametrize(
    "provider_output",
    [
        '{"b":[true,null],"a":1}',
        '  { "a" : 1, "b" : [ true, null ] }\n',
    ],
)
def test_archon_structured_output_writes_one_canonical_primary_candidate(
    tmp_path, provider_output
):
    runner = FakeAgentRunner(provider_output)
    node = _node(
        "canonical",
        "Produce data",
        output_type="analysis-result",
        output_format={
            "type": "object",
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "array"},
            },
        },
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].structured_output is not None
    assert result.primary_output is not None
    assert result.primary_output.output_type == "analysis-result"
    assert result.primary_output.media_type == "application/json"
    assert result.primary_output.structured_value == {"a": 1, "b": (True, None)}
    assert result.primary_output.schema_fingerprint == (
        runner.requests[0].structured_output.schema.schema_fingerprint
    )
    output = tmp_path / "run" / result.primary_output.attempt_relative_path
    assert output.read_bytes() == b'{"a":1,"b":[true,null]}'
    assert result.artifacts[0].sha256 == result.primary_output.sha256


@pytest.mark.parametrize(
    "node_id",
    [".", "..", "nested/name", "nested\\name", "/absolute/node"],
    ids=("dot", "dotdot", "slash", "backslash", "absolute"),
)
def test_archon_output_uses_contained_deterministic_components(tmp_path, node_id):
    runner = FakeAgentRunner("{}")
    node = _node(node_id, "Produce data", output_format={"type": "object"})
    context = _archon_context(tmp_path, node)

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.primary_output is not None
    expected = (
        Path("nodes")
        / _safe_output_component("node", node_id)
        / _safe_output_component("attempt", context.attempt_id)
        / "output.json"
    )
    assert result.primary_output.attempt_relative_path == expected.as_posix()
    output = context.run_directory / result.primary_output.attempt_relative_path
    assert output.resolve().is_relative_to(context.run_directory.resolve())


def test_archon_output_rejects_symlinked_nodes_directory(tmp_path):
    runner = FakeAgentRunner("{}")
    node = _node("symlink-root", "Produce data", output_format={"type": "object"})
    context = _archon_context(tmp_path, node)
    outside = tmp_path / "outside"
    outside.mkdir()
    (context.run_directory / "nodes").symlink_to(outside, target_is_directory=True)

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "structured_output_integrity"
    assert result.metadata["archon_terminal_failure"] is True
    assert not tuple(outside.iterdir())


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd,
    reason="descriptor-relative race injection requires dir_fd support",
)
@pytest.mark.parametrize("collision", ["symlink", "directory", "fifo"])
def test_archon_output_exclusive_write_rejects_injected_nonregular_collision(
    tmp_path, monkeypatch, collision
):
    if collision == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    runner = FakeAgentRunner("{}")
    node = _node("raced", "Produce data", output_format={"type": "object"})
    context = _archon_context(tmp_path, node)
    attempt_component = _safe_output_component("attempt", context.attempt_id)
    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    original_mkdir = os.mkdir

    def injecting_mkdir(path, mode=0o777, *, dir_fd=None):
        original_mkdir(path, mode, dir_fd=dir_fd)
        if path != attempt_component or dir_fd is None:
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        attempt_descriptor = os.open(path, flags, dir_fd=dir_fd)
        try:
            if collision == "symlink":
                os.symlink(outside, "output.json", dir_fd=attempt_descriptor)
            elif collision == "directory":
                original_mkdir("output.json", dir_fd=attempt_descriptor)
            else:
                os.mkfifo("output.json", dir_fd=attempt_descriptor)
        finally:
            os.close(attempt_descriptor)

    monkeypatch.setattr(os, "mkdir", injecting_mkdir)

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "structured_output_integrity"
    assert result.metadata["archon_terminal_failure"] is True
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_archon_text_candidate_and_artifact_share_plain_text_media_type(tmp_path):
    runner = FakeAgentRunner("plain response")
    node = _node("plain", "Produce text")

    result = AgentNodeExecutor(runner).execute(_archon_text_context(tmp_path, node))

    assert result.status == "succeeded"
    assert result.primary_output is not None
    assert result.primary_output.media_type == "text/plain"
    assert result.artifacts[0].media_type == result.primary_output.media_type


@pytest.mark.parametrize(
    ("provider_output", "case"),
    [
        ("I refuse to provide JSON", "refusal"),
        ('{"answer":', "truncation"),
        ('Result: {"answer":"ok"}', "prose"),
        ('```json\n{"answer":"ok"}\n```', "fence"),
        ('{"answer":"ok"} {"answer":"again"}', "multiple-values"),
        ('{"answer":NaN}', "non-finite"),
        ('"' + ("x" * 500_001) + '"', "oversize"),
    ],
    ids=(
        "refusal",
        "truncation",
        "prose",
        "fence",
        "multiple-values",
        "non-finite",
        "oversize",
    ),
)
@pytest.mark.parametrize(
    "strategy",
    (
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
        StructuredOutputStrategy.NATIVE_JSON_MODE,
    ),
    ids=("native-schema", "native-json"),
)
def test_archon_native_invalid_output_is_terminal_and_never_repaired(
    tmp_path, provider_output, case, strategy
):
    runner = FakeAgentRunner(provider_output)
    node = _node(
        "native-invalid",
        "Produce data",
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(
            tmp_path,
            node,
            strategy=strategy,
        )
    )

    assert result.status == "failed", case
    assert result.error_code == "structured_output_invalid"
    assert result.metadata["archon_terminal_failure"] is True
    assert result.metadata["repair_disposition"] == "ineligible_native_strategy"
    assert len(runner.requests) == 1
    assert provider_output not in repr(result.metadata)


def test_prompt_adapter_repairs_once_in_a_fresh_action_free_request(tmp_path):
    runner = FakeAgentRunner(
        '{"answer":"wrong wrapper"} trailing',
        '{"answer":"fixed"}',
        provider_attempts=(1, 1),
        model_calls=(2, 1),
    )
    node = _node(
        "repairable",
        "ORIGINAL TASK MUST NOT ENTER REPAIR",
        allowed_tools=["read_file"],
        fallbackModel="fallback-model",
        systemPrompt="original system prompt",
        agents={
            "reviewer": {
                "description": "review",
                "prompt": "review it",
            }
        },
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(tmp_path, node, max_provider_attempts=3)
    )

    assert result.status == "succeeded"
    assert len(runner.requests) == 2
    repair = runner.requests[1]
    assert repair.context_mode == "fresh"
    assert repair.session_id is None
    assert repair.allowed_tools == ()
    assert repair.enabled_toolsets == ()
    assert "delegate_task" in repair.denied_tools
    assert repair.hooks == ()
    assert repair.mcp_servers is None
    assert repair.skills == ()
    assert repair.inline_agents == {}
    assert repair.fallback_model is None
    assert repair.ephemeral_system_prompt is None
    assert repair.max_iterations == 1
    assert repair.max_api_attempts == 2
    assert repair.reasoning_config is None
    assert repair.request_overrides == {}
    assert repair.sandbox_policy is None
    assert repair.max_budget_usd is None
    assert repair.approved_action_digest is None
    repair_payload = json.loads(repair.prompt)
    assert set(repair_payload) == {
        "canonical_schema",
        "invalid_response",
        "validation_diagnostics",
    }
    assert repair_payload["invalid_response"] == ('{"answer":"wrong wrapper"} trailing')
    assert "ORIGINAL TASK MUST NOT ENTER REPAIR" not in repair.prompt
    assert "original system prompt" not in repair.prompt
    assert "wrong wrapper" in repair.prompt
    assert '"required":["answer"]' in repair.prompt
    assert "response contains trailing non-JSON content" in repair.prompt
    assert result.metadata["usage"] == {"input_tokens": 8, "output_tokens": 4}
    assert result.metadata["audit"]["provider_attempts"] == 2
    assert result.metadata["audit"]["model_calls"] == 3
    assert result.metadata["audit"]["api_calls"] == 3
    assert result.metadata["provider_attempts"] == 1
    assert result.metadata["audit"]["repair_accounting"] == "exact"
    assert result.metadata["audit"]["provider_attempts_exact"] is True
    assert result.metadata["audit"]["model_calls_exact"] is True
    assert result.metadata["audit"]["api_calls_exact"] is True
    assert result.metadata["repair_disposition"] == "succeeded"
    output = tmp_path / "run" / result.primary_output.attempt_relative_path
    assert output.read_bytes() == b'{"answer":"fixed"}'


@pytest.mark.parametrize(
    "repair_outcome",
    ["timeout", "resource_exception", "cancelled", "resource_result"],
)
def test_archon_evidence_free_repair_uses_bounded_conservative_accounting(
    tmp_path, repair_outcome
):
    class EvidenceFreeRepairRunner(FakeAgentRunner):
        def run(self, request, **kwargs):
            if not self.requests:
                return super().run(request, **kwargs)
            self.requests.append(request)
            if repair_outcome == "timeout":
                raise TimeoutError("repair timed out")
            if repair_outcome == "resource_exception":
                raise RuntimeError("repair resource limit")
            status = "cancelled" if repair_outcome == "cancelled" else "failed"
            return PluginAgentRunResult(
                final_response="",
                session_id="repair-session",
                provider="fake-provider",
                model="fake-model",
                status=status,
                pending_interaction=None,
                usage={"input_tokens": 7, "output_tokens": 3},
                audit={"failure_kind": repair_outcome},
                structured_output=None,
            )

    runner = EvidenceFreeRepairRunner("not json")
    node = _node(
        "evidence-free-repair",
        "Produce data",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(tmp_path, node, max_provider_attempts=3)
    )

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"
    assert result.metadata["provider_attempts"] == 2
    assert result.metadata["audit"]["provider_attempts"] == 3
    assert result.metadata["audit"]["model_calls"] == 2
    assert result.metadata["audit"]["api_calls"] == 2
    assert result.metadata["audit"]["repair_accounting"] == "conservative"
    assert result.metadata["audit"]["provider_attempts_exact"] is False
    assert result.metadata["audit"]["model_calls_exact"] is False
    assert result.metadata["audit"]["api_calls_exact"] is False
    expected_usage = (
        {"input_tokens": 4, "output_tokens": 2}
        if repair_outcome in {"timeout", "resource_exception"}
        else {"input_tokens": 11, "output_tokens": 5}
    )
    assert result.metadata["usage"] == expected_usage


@pytest.mark.parametrize(
    ("requested_provider", "canonical_provider"),
    [
        ("openai-api", "openai"),
        ("custom:corp-route", "custom"),
    ],
    ids=("openai-api", "named-custom"),
)
def test_archon_initial_and_repair_keep_immutable_routable_provider_selector(
    tmp_path, requested_provider, canonical_provider
):
    class CanonicalRouteRunner:
        def __init__(self):
            self.requests = []
            self.responses = ["not json", "{}"]

        def run(self, request, **_kwargs):
            self.requests.append(request)
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response=self.responses.pop(0),
                session_id="session",
                provider=canonical_provider,
                model="route-model",
                status="completed",
                pending_interaction=None,
                usage={"input_tokens": 1, "output_tokens": 1},
                audit={**evidence, "api_calls": 1, "api_mode": "chat_completions"},
                structured_output=evidence,
            )

    runner = CanonicalRouteRunner()
    node = _node(
        "routable-selector",
        "work",
        provider=requested_provider,
        model="route-model",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(
            tmp_path,
            node,
            decision_provider=canonical_provider,
            decision_model="route-model",
            max_provider_attempts=2,
        )
    )

    assert result.status == "succeeded"
    assert result.metadata["repair_disposition"] == "succeeded"
    assert [request.provider for request in runner.requests] == [
        requested_provider,
        requested_provider,
    ]
    assert result.metadata["provider"] == canonical_provider
    assert result.metadata["audit"]["effective_provider"] == canonical_provider


def test_legacy_initial_request_keeps_declared_provider_selector(tmp_path):
    runner = FakeAgentRunner("done")
    node = _node(
        "legacy-selector",
        "work",
        provider="custom:legacy-route",
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].provider == "custom:legacy-route"


@pytest.mark.parametrize(
    ("context_changes", "runner_kwargs", "expected_disposition"),
    [
        ({"outward_action": True}, {}, "ineligible_outward_action"),
        ({"is_cancelled": lambda: True}, {}, "ineligible_cancelled"),
        ({"max_provider_attempts": 1}, {}, "ineligible_provider_attempts"),
        ({}, {"model_calls": (90,)}, "ineligible_model_iterations"),
        (
            {},
            {"audit_rows": ({"unknown_side_effect": True},)},
            "ineligible_uncertain_effects",
        ),
    ],
)
def test_prompt_adapter_skips_repair_when_safety_or_attempt_allowance_is_gone(
    tmp_path, context_changes, runner_kwargs, expected_disposition
):
    runner = FakeAgentRunner("not json", **runner_kwargs)
    node = _node(
        "not-repairable",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(tmp_path, node, **context_changes)
    )

    assert result.error_code == "structured_output_invalid"
    assert result.metadata["repair_disposition"] == expected_disposition
    assert len(runner.requests) == 1


def test_prompt_adapter_skips_repair_when_invalid_excerpt_cannot_be_bounded(tmp_path):
    response = "x" * 256_001
    runner = FakeAgentRunner(response)
    node = _node(
        "too-large-to-repair",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.error_code == "structured_output_invalid"
    assert result.metadata["repair_disposition"] == "ineligible_response_too_large"
    assert len(runner.requests) == 1
    assert response not in repr(result.metadata)


def test_prompt_adapter_bounds_fully_serialized_repair_with_maximum_schema(
    tmp_path,
):
    invalid = "\x00" * 80_000
    runner = FakeAgentRunner(invalid, "{}")
    node = _node(
        "escaped-repair-envelope",
        "work",
        output_format={
            "type": "object",
            "description": "s" * 65_000,
        },
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "succeeded"
    assert len(runner.requests) == 2
    repair = runner.requests[1]
    encoded_wire = json.dumps(
        repair.to_wire(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert len(repair.prompt) <= 500_000
    assert len(encoded_wire) <= 900_000
    excerpt = json.loads(repair.prompt)["invalid_response"]
    assert excerpt != invalid
    assert invalid.startswith(excerpt)
    assert result.metadata["invalid_output_size_bytes"] == len(invalid.encode("utf-8"))
    assert (
        result.metadata["invalid_output_sha256"]
        == hashlib.sha256(invalid.encode("utf-8")).hexdigest()
    )


def test_prompt_adapter_skips_repair_after_shared_wall_deadline_expires(tmp_path):
    runner = FakeAgentRunner("not json")
    node = _node(
        "wall-exhausted",
        "work",
        output_format={"type": "object"},
    )
    times = iter((10.0, 31.0))
    budget = DeadlineBudget.create(
        now=10.0,
        wall_seconds=20.0,
        idle_seconds=8.0,
        provider_seconds=6.0,
    )

    result = AgentNodeExecutor(runner).execute(
        _archon_context(
            tmp_path,
            node,
            deadline_budget=budget,
            monotonic=lambda: next(times),
        )
    )

    assert result.error_code == "structured_output_invalid"
    assert result.metadata["repair_disposition"] == "ineligible_wall_time"
    assert len(runner.requests) == 1


def test_prompt_adapter_makes_only_one_repair_when_repair_is_still_invalid(tmp_path):
    runner = FakeAgentRunner("not json", "still not json")
    node = _node(
        "repair-once",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.error_code == "structured_output_invalid"
    assert result.metadata["repair_disposition"] == "repair_invalid"
    assert result.metadata["audit"]["provider_attempts"] == 2
    assert result.metadata["audit"]["model_calls"] == 2
    assert len(runner.requests) == 2
    assert "still not json" not in repr(result.metadata)


def test_archon_rejects_structured_audit_identity_that_differs_from_admission(
    tmp_path,
):
    class MismatchedAuditRunner:
        def __init__(self):
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": StructuredOutputStrategy.NATIVE_JSON_SCHEMA.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response="{}",
                session_id="session",
                provider="fake-provider",
                model="fake-model",
                status="completed",
                pending_interaction=None,
                usage={},
                audit=evidence,
                structured_output=evidence,
            )

    runner = MismatchedAuditRunner()
    node = _node(
        "audit-drift",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "failed"
    assert result.error_code == "structured_output_capability_drift"
    assert result.metadata["archon_terminal_failure"] is True
    assert len(runner.requests) == 1
    assert not (tmp_path / "run" / "nodes").exists()


@pytest.mark.parametrize(
    ("returned_provider", "returned_model", "returned_api_mode"),
    [
        ("wrong-provider", "fake-model", "chat_completions"),
        ("fake-provider", "wrong-model", "chat_completions"),
        ("fake-provider", "fake-model", "codex_responses"),
    ],
    ids=("provider", "model", "api-mode"),
)
def test_archon_rejects_runtime_identity_that_differs_from_sealed_admission(
    tmp_path,
    returned_provider,
    returned_model,
    returned_api_mode,
):
    class MismatchedRouteRunner:
        def run(self, request, **_kwargs):
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response="{}",
                session_id="session",
                provider=returned_provider,
                model=returned_model,
                status="completed",
                pending_interaction=None,
                usage={},
                audit={**evidence, "api_calls": 1, "api_mode": returned_api_mode},
                structured_output=evidence,
            )

    node = _node(
        "route-drift",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(MismatchedRouteRunner()).execute(
        _archon_context(tmp_path, node)
    )

    assert result.status == "failed"
    assert result.error_code == "structured_output_capability_drift"
    assert result.metadata["archon_terminal_failure"] is True
    assert not (tmp_path / "run" / "nodes").exists()


@pytest.mark.parametrize(
    ("provider_attempts", "model_calls"),
    [(0, 1), (1, 0)],
    ids=("zero-provider-attempts", "zero-model-calls"),
)
def test_archon_completed_output_requires_positive_exact_accounting(
    tmp_path, provider_attempts, model_calls
):
    runner = FakeAgentRunner(
        "{}",
        provider_attempts=(provider_attempts,),
        model_calls=(model_calls,),
    )
    node = _node(
        "zero-accounting",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "failed"
    assert result.error_code == "structured_output_capability_drift"
    assert result.metadata["archon_terminal_failure"] is True
    assert not (tmp_path / "run" / "nodes").exists()


def test_archon_typed_negotiation_failure_preserves_zero_attempt_accounting(tmp_path):
    class NegotiationFailureRunner:
        def run(self, request, **_kwargs):
            evidence = {
                "provider_attempts": 0,
                "model_calls": 0,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake-provider",
                model="fake-model",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    **evidence,
                    "api_mode": "chat_completions",
                    "failure_kind": "structured_output_capability_drift",
                },
                structured_output=evidence,
            )

    node = _node(
        "negotiation-failure",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(NegotiationFailureRunner()).execute(
        _archon_context(tmp_path, node, max_provider_attempts=3)
    )

    assert result.status == "failed"
    assert result.error_code == "structured_output_capability_drift"
    assert result.metadata["provider_attempts"] == 0
    assert result.metadata["archon_terminal_failure"] is True
    assert result.metadata["negotiation_evidence"] == "corroborated"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("provider", "spoof-provider"),
        ("model", "spoof-model"),
        ("api_mode", "responses"),
        ("strategy", StructuredOutputStrategy.NATIVE_JSON_SCHEMA.value),
        ("adapter_version", 99),
        ("schema_fingerprint", "0" * 64),
        ("declaration_source", "spoof-source"),
        ("evidence", None),
    ],
)
def test_archon_zero_count_negotiation_failure_requires_sealed_identity_evidence(
    tmp_path, mutation, value
):
    class NegotiationFailureRunner:
        def run(self, request, **_kwargs):
            evidence = {
                "provider_attempts": 0,
                "model_calls": 0,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            provider = "fake-provider"
            model = "fake-model"
            api_mode = "chat_completions"
            if mutation == "provider":
                provider = value
            elif mutation == "model":
                model = value
            elif mutation == "api_mode":
                api_mode = value
            elif mutation in evidence:
                evidence[mutation] = value
            structured_evidence = evidence if mutation != "evidence" else None
            audit = {
                **(evidence if structured_evidence is not None else {}),
                "api_mode": api_mode,
                "failure_kind": "structured_output_capability_drift",
            }
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=provider,
                model=model,
                status="failed",
                pending_interaction=None,
                usage={},
                audit=audit,
                structured_output=structured_evidence,
            )

    node = _node(
        "spoofed-negotiation",
        "work",
        output_format={"type": "object"},
    )

    result = AgentNodeExecutor(NegotiationFailureRunner()).execute(
        _archon_context(tmp_path, node, max_provider_attempts=3)
    )

    assert result.status == "failed"
    assert result.error_code == "structured_output_capability_drift"
    assert result.metadata["provider_attempts"] == 2
    assert result.metadata["archon_terminal_failure"] is True
    assert result.metadata["negotiation_evidence"] == "invalid"


def test_archon_cache_fingerprint_includes_sealed_schema_identity(tmp_path):
    runner = FakeAgentRunner('{"value":1}', '{"value":"one"}')
    integer_node = _node(
        "same-node",
        "work",
        output_format={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        },
    )
    string_node = _node(
        "same-node",
        "work",
        output_format={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    )

    integer_result = AgentNodeExecutor(runner).execute(
        _archon_context(tmp_path / "integer", integer_node)
    )
    string_result = AgentNodeExecutor(runner).execute(
        _archon_context(tmp_path / "string", string_node)
    )

    assert (
        integer_result.metadata["cache_fingerprint"]
        != string_result.metadata["cache_fingerprint"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effective_provider", "other-provider"),
        ("model", "other-model"),
        ("api_mode", "responses"),
        ("declaration_source", "other-source"),
    ],
)
def test_archon_cache_fingerprint_includes_sealed_effective_route(
    tmp_path, field, value
):
    node = _node(
        "sealed-route-fingerprint",
        "work",
        output_format={"type": "object"},
    )
    context = _archon_context(tmp_path, node)
    decision = context.structured_output_decision
    assert decision is not None
    mutated = replace(
        context, structured_output_decision=replace(decision, **{field: value})
    )
    executor = AgentNodeExecutor(FakeAgentRunner())

    assert executor._fingerprint(context) != executor._fingerprint(mutated)


def test_legacy_cache_fingerprint_preserves_pre_archon_known_material(tmp_path):
    node = _node(
        "legacy-fingerprint",
        "work",
        allowed_tools=["read_file"],
    )

    fingerprint = AgentNodeExecutor(FakeAgentRunner())._fingerprint(
        _context(tmp_path, node)
    )

    assert (
        fingerprint
        == "0e03d8d5cb2c039c376b70a633f22ae228e3e6860df5dc90b864cd1f9f9db514"
    )


def test_shared_context_resumes_only_one_compatible_predecessor(tmp_path):
    runner = FakeAgentRunner("first", "second")
    executor = AgentNodeExecutor(runner, profile_name="default")
    first = _node("first", "First", allowed_tools=["read_file"])
    first_result = executor.execute(_context(tmp_path, first))
    second = _node(
        "second",
        "Second",
        context="shared",
        allowed_tools=["read_file"],
        depends_on=["first"],
    )

    second_result = executor.execute(
        _context(
            tmp_path,
            second,
            predecessor_results={"first": first_result.metadata},
        )
    )

    assert second_result.status == "succeeded"
    assert runner.requests[1].context_mode == "shared"
    assert runner.requests[1].session_id == "session-1"


def test_shared_context_rejects_ambiguous_or_cache_incompatible_join(tmp_path):
    runner = FakeAgentRunner("unused")
    executor = AgentNodeExecutor(runner)
    node = _node(
        "join",
        "Join",
        context="shared",
        allowed_tools=["read_file"],
        depends_on=["left", "right"],
    )
    ambiguous = executor.execute(
        _context(
            tmp_path,
            node,
            predecessor_results={
                "left": {"session_id": "a", "cache_fingerprint": "x"},
                "right": {"session_id": "b", "cache_fingerprint": "x"},
            },
        )
    )
    mismatch_node = _node(
        "second",
        "Second",
        context="shared",
        allowed_tools=["terminal"],
        depends_on=["first"],
    )
    mismatch = executor.execute(
        _context(
            tmp_path,
            mismatch_node,
            predecessor_results={
                "first": {"session_id": "a", "cache_fingerprint": "wrong"}
            },
        )
    )

    assert ambiguous.error_code == "context_ambiguous"
    assert mismatch.error_code == "context_incompatible"
    assert "fresh" in mismatch.error_message
    assert runner.requests == []


def test_resource_failure_is_typed_and_never_hidden_as_agent_failure(tmp_path):
    class ResourceRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    "failure_kind": "resource_limit",
                    "resource_code": "rss_limit",
                },
            )

    result = AgentNodeExecutor(ResourceRunner()).execute(
        _context(tmp_path, _node("bounded", "work"))
    )

    assert result.status == "failed"
    assert result.error_code == "resource_limit"


def test_required_package_mcp_failure_keeps_exact_node_error(tmp_path):
    class PackageMCPRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "package_mcp_unavailable"},
            )

    result = AgentNodeExecutor(PackageMCPRunner()).execute(
        _context(tmp_path, _node("required-mcp", "work"))
    )

    assert result.status == "failed"
    assert result.error_code == "package_mcp_unavailable"


def test_ai_request_receives_remaining_absolute_deadline_and_retry_budget(tmp_path):
    runner = FakeAgentRunner("done")
    budget = DeadlineBudget.create(
        now=10,
        wall_seconds=20,
        idle_seconds=8,
        provider_seconds=6,
    )

    result = AgentNodeExecutor(runner).execute(
        _context(
            tmp_path,
            _node("bounded", "work"),
            deadline_budget=budget,
            monotonic=lambda: 14,
            max_provider_attempts=2,
        )
    )

    assert result.status == "succeeded"
    assert runner.requests[0].wall_timeout_seconds == 16
    assert runner.requests[0].idle_timeout_seconds == 8
    assert runner.requests[0].provider_request_timeout_seconds == 6
    assert runner.requests[0].max_api_attempts == 2
    assert runner.requests[0].cooperative_shutdown_seconds == 5
    assert runner.requests[0].term_grace_seconds == 5
    assert runner.requests[0].kill_reap_grace_seconds == 2


def test_ai_request_maps_every_run_execution_limit_exactly(tmp_path):
    runner = FakeAgentRunner("done")
    limits = RunExecutionLimits(
        max_parallel_nodes=2,
        max_total_workers=3,
        ai_idle_timeout_seconds=11,
        ai_wall_timeout_seconds=37,
        provider_request_timeout_seconds=7,
        combined_retries=4,
        subprocess_timeout_seconds=19,
        process_tree_rss_bytes=128 * 1024 * 1024,
        process_tree_cpu_seconds=13,
        max_descendants=3,
        cooperative_shutdown_seconds=1.5,
        term_grace_seconds=2.5,
        kill_reap_grace_seconds=3.5,
    )
    budget = DeadlineBudget.create(
        now=10,
        wall_seconds=limits.ai_wall_timeout_seconds,
        idle_seconds=limits.ai_idle_timeout_seconds,
        provider_seconds=limits.provider_request_timeout_seconds,
    )

    result = AgentNodeExecutor(runner).execute(
        _context(
            tmp_path,
            _node("bounded-exactly", "work"),
            execution_limits=limits,
            deadline_budget=budget,
            monotonic=lambda: 10,
        )
    )

    assert result.status == "succeeded"
    request = runner.requests[0]
    assert request.idle_timeout_seconds == limits.ai_idle_timeout_seconds
    assert request.wall_timeout_seconds == limits.ai_wall_timeout_seconds
    assert (
        request.provider_request_timeout_seconds
        == limits.provider_request_timeout_seconds
    )
    assert request.max_api_attempts == limits.combined_retries
    assert request.max_process_tree_rss_bytes == limits.process_tree_rss_bytes
    assert request.max_process_tree_cpu_seconds == limits.process_tree_cpu_seconds
    assert request.max_descendants == limits.max_descendants
    assert request.cooperative_shutdown_seconds == limits.cooperative_shutdown_seconds
    assert request.term_grace_seconds == limits.term_grace_seconds
    assert request.kill_reap_grace_seconds == limits.kill_reap_grace_seconds
    assert request.max_iterations == 90


def test_provider_failure_charges_granted_internal_retry_allowance(tmp_path):
    class TimeoutRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "provider_timeout"},
            )

    result = AgentNodeExecutor(TimeoutRunner()).execute(
        _context(
            tmp_path,
            _node("timeout", "work"),
            max_provider_attempts=1,
        )
    )

    assert result.error_code == "provider_timeout"
    assert result.metadata["provider_attempts"] == 0


def test_provider_failure_charges_run_scoped_retry_allowance(tmp_path):
    class TimeoutRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "provider_timeout"},
            )

    limits = RunExecutionLimits(combined_retries=2)
    result = AgentNodeExecutor(TimeoutRunner()).execute(
        _context(
            tmp_path,
            _node("timeout-run-limit", "work"),
            execution_limits=limits,
        )
    )

    assert result.error_code == "provider_timeout"
    assert result.metadata["provider_attempts"] == 1


@pytest.mark.parametrize("reported", [-1, 3, 99, True, "1"])
def test_malformed_provider_attempt_count_is_charged_conservatively(
    tmp_path, reported
) -> None:
    class MalformedAuditRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    "failure_kind": "provider_timeout",
                    "provider_attempts": reported,
                },
            )

    result = AgentNodeExecutor(MalformedAuditRunner()).execute(
        _context(
            tmp_path,
            _node("malformed-provider-attempts", "work"),
            execution_limits=RunExecutionLimits(combined_retries=3),
        )
    )

    assert result.error_code == "provider_timeout"
    assert result.metadata["provider_attempts"] == 2


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (OSError("provider connection failed"), "network_error"),
        (RuntimeError("agent host failed"), "agent_execution_failed"),
    ],
)
def test_retryable_runner_exception_charges_unknown_provider_attempts(
    tmp_path, failure, expected_code
) -> None:
    class FailingRunner:
        def run(self, _request, **_kwargs):
            raise failure

    result = AgentNodeExecutor(FailingRunner()).execute(
        _context(
            tmp_path,
            _node(
                "runner-exception",
                "work",
                retry={"max_attempts": 3, "on_error": "all"},
            ),
            execution_limits=RunExecutionLimits(combined_retries=3),
        )
    )

    assert result.error_code == expected_code
    assert result.metadata["provider_attempts"] == 2
