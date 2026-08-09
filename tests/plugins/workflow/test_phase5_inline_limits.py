from __future__ import annotations

from dataclasses import replace
import time

import pytest

import agent.plugin_agent as plugin_agent
from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
    _validate_request,
)
from agent.plugin_agent_worker import _build_inline_agent_handler, _run
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.models import freeze_value
from plugins.workflow.provider_authority import WorkflowCapabilityObligation
from tests.plugins.workflow.test_phase5_execution_context import _Runner, _context
from tests.plugins.workflow.test_phase5_provider_authority import _authority, _load_v5


def _with_inline_authority(context):
    authority = context.sealed_provider_authority
    primary = context.sealed_provider_route
    assert authority is not None and primary is not None
    inline = replace(
        primary,
        route_id="ask:inline_agent:reviewer",
        role="inline_agent",
        inline_agent_id="reviewer",
        requested_reference_sha256="c" * 64,
        route_fingerprint="d" * 64,
    )
    primary_obligation = authority.obligations[0]
    inline_obligation = WorkflowCapabilityObligation(
        path="nodes[0].agents.reviewer.effort",
        route_id=inline.route_id,
        decision=replace(primary_obligation.decision),
    )
    return replace(
        context,
        sealed_provider_authority=replace(
            authority,
            routes={**dict(authority.routes), inline.route_id: inline},
            obligations=(*authority.obligations, inline_obligation),
        ),
    )


def test_phase5_admission_blocks_inline_agent_unreachable_by_tool_policy(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "allowed_tools": [],
            "agents": {
                "reviewer": {"description": "Review", "prompt": "Review this"}
            },
        }],
    )
    package = _load_v5(path)

    report = assess_compatibility(
        package,
        provider_authority=_authority(package),
    )

    assert report.runnable is False
    assert ("nodes[0].agents", "tool_policy_incompatible") in {
        (finding.path, finding.code) for finding in report.blocking_findings
    }


def test_phase5_inline_agent_behind_explicit_empty_tool_policy_blocks(tmp_path):
    runner = _Runner()
    context = _with_inline_authority(_context(tmp_path))
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "allowed_tools": [],
            "agents": {
                "reviewer": {
                    "description": "Review",
                    "prompt": "Review this",
                }
            },
        }),
    )

    result = AgentNodeExecutor(runner).execute(replace(context, node=node))

    assert result.error_code == "tool_policy_incompatible"
    assert result.metadata["provider_attempts"] == 0
    assert runner.requests == []


def test_phase5_omitted_tool_allowlist_keeps_declared_inline_agent_reachable(tmp_path):
    runner = _Runner()
    context = _with_inline_authority(_context(tmp_path))
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "agents": {
                "reviewer": {
                    "description": "Review",
                    "prompt": "Review this",
                }
            },
        }),
    )

    result = AgentNodeExecutor(runner).execute(replace(context, node=node))

    assert result.status == "succeeded"
    assert len(runner.requests) == 1
    assert runner.requests[0].allowed_tools is None
    assert set(runner.requests[0].inline_agents) == {"reviewer"}
    assert runner.requests[0].inline_agents["reviewer"][
        "expected_runtime_route_fingerprint"
    ] == "d" * 64
    assert runner.requests[0].inline_agents["reviewer"][
        "expected_runtime_route_options"
    ] == {"effort": "high"}


def test_phase5_inline_agent_deny_rule_is_not_overridden(tmp_path):
    runner = _Runner()
    context = _with_inline_authority(_context(tmp_path))
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "denied_tools": ["Task"],
            "agents": {
                "reviewer": {
                    "description": "Review",
                    "prompt": "Review this",
                }
            },
        }),
    )

    result = AgentNodeExecutor(runner).execute(replace(context, node=node))

    assert result.error_code == "tool_policy_incompatible"
    assert result.metadata["provider_attempts"] == 0
    assert runner.requests == []


def test_phase5_worker_rejects_forged_unreachable_inline_agent_before_provider(
    monkeypatch,
):
    import hermes_cli.runtime_provider as runtime_provider

    runtime = {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-credential",
    }
    expected_identity = runtime_provider.execution_runtime_identity(
        runtime_provider.classify_resolved_execution_runtime(runtime)
    )
    identity = expected_identity.to_dict()

    constraint = runtime_provider.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="d" * 64,
        requested_provider="openrouter",
        model="openai/gpt-5.4",
        api_mode="chat_completions",
        base_url=runtime["base_url"],
        provider_config={},
        identity=expected_identity,
    )

    monkeypatch.setattr(
        runtime_provider,
        "select_credential_free_execution_route",
        lambda *_args, **_kwargs: constraint,
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: runtime,
    )
    request = PluginAgentRunRequest(
        prompt="parent",
        provider="openrouter",
        model="openai/gpt-5.4",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=identity,
        expected_runtime_route_fingerprint="d" * 64,
        expected_runtime_route_options={},
        allowed_tools=(),
        inline_agents={
            "reviewer": {
                "prompt": "Review",
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "max_iterations": 2,
            }
        },
    )

    result = _run({"plugin_id": "workflow", "request": request.to_wire()})

    assert result["status"] == "failed"
    assert result["audit"] == {
        "plugin_id": "workflow",
        "failure_kind": "tool_policy_incompatible",
        "provider_attempts": 0,
        "model_calls": 0,
        "known_no_effect": True,
        "intended_authority_digest": "a" * 64,
    }


def test_inline_child_inherits_absolute_deadlines_limits_and_cancellation(tmp_path):
    captured = []
    cancelled = []

    class Runner:
        def run(self, request, **kwargs):
            captured.append(request)
            cancelled.append(kwargs["is_cancelled"]())
            return PluginAgentRunResult(
                final_response="done",
                session_id="child",
                provider="provider",
                model="model",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    now = time.monotonic()
    parent_identity = {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "api_mode": "chat_completions",
        "base_url_trust_class": "trusted_direct",
        "endpoint_sha256": "1" * 64,
        "registration_provenance_digest": "2" * 64,
    }
    child_identity = {
        **parent_identity,
        "model": "anthropic/claude-sonnet-4.6",
        "endpoint_sha256": "3" * 64,
        "registration_provenance_digest": "4" * 64,
    }
    parent = PluginAgentRunRequest(
        prompt="parent",
        provider="openrouter",
        model="openai/gpt-5.4",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=parent_identity,
        expected_runtime_route_fingerprint="b" * 64,
        expected_runtime_route_options={},
        workdir=tmp_path,
        max_api_attempts=4,
        sealed_provider_attempt_grant=True,
        absolute_wall_deadline=now + 60,
        absolute_idle_deadline=now + 20,
        absolute_provider_deadline=now + 10,
        max_process_tree_rss_bytes=123_456,
        max_process_tree_cpu_seconds=12.5,
        max_descendants=3,
    )
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            "reviewer": {
                "prompt": "Review",
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "intended_authority_digest": "c" * 64,
                "expected_runtime_identity": child_identity,
                "expected_runtime_route_fingerprint": "d" * 64,
                "expected_runtime_route_options": {},
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "max_iterations": 2,
            }
        },
        workdir=tmp_path,
        parent_request=parent,
        runner_factory=lambda _plugin_id: Runner(),
        emit_progress=lambda **_kwargs: None,
        pause=lambda _descriptor: None,
        is_cancelled=lambda: True,
    )

    result = handler({"agent_id": "reviewer", "task": "check"})

    assert result["status"] == "completed"
    assert cancelled == [True]
    child = captured[0]
    assert child.absolute_wall_deadline == parent.absolute_wall_deadline
    assert child.absolute_idle_deadline == parent.absolute_idle_deadline
    assert child.absolute_provider_deadline == parent.absolute_provider_deadline
    assert child.max_process_tree_rss_bytes == 123_456
    assert child.max_process_tree_cpu_seconds == 12.5
    assert child.max_descendants == 2
    assert child.intended_authority_digest == "c" * 64
    assert child.expected_runtime_identity == child_identity
    _validate_request(child)


def test_expired_absolute_provider_deadline_blocks_before_worker_spawn(monkeypatch):
    spawned = False

    def fail_if_spawned(*_args, **_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("worker must not start")

    monkeypatch.setattr(plugin_agent, "_exchange_worker", fail_if_spawned)
    now = time.monotonic()
    request = PluginAgentRunRequest(
        prompt="parent",
        absolute_wall_deadline=now + 60,
        absolute_idle_deadline=now + 20,
        absolute_provider_deadline=now - 1,
    )

    with pytest.raises(TimeoutError, match="provider"):
        PluginAgentRunner("workflow").run(request)

    assert spawned is False
