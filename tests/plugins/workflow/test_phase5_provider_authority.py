from __future__ import annotations

from dataclasses import FrozenInstanceError
import sys

import pytest
import yaml

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.provider_authority import (
    ProviderAuthorityEnvironment,
    WorkflowProviderAuthority,
    resolve_workflow_provider_authority,
)
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    assess_package_execution,
    execution_capability_context,
)
from plugins.workflow.schema import load_workflow_snapshot
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _load_v5(path):
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")
    return load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=5,
    )


def _config(*, primary_model: str = "openai/gpt-5.4"):
    return parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
        },
        "model_tiers": {
            "small": {
                "provider": "openrouter",
                "model": "openai/gpt-4.1-mini",
            },
            "large": {
                "provider": "openrouter",
                "model": "anthropic/claude-opus-4.1",
            },
        },
        "model_aliases": {
            "primary": {
                "provider": "openrouter",
                "model": primary_model,
                "effort": "medium",
            },
            "recovery": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.5",
            },
        },
    })


def _runtime():
    return classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
        },
    )


def _environment(**overrides):
    values = {
        "session_store_available": True,
        "mcp_available": True,
        "hook_lifecycle_available": True,
        "inline_agent_available": True,
        "web_service_available": True,
        "authoritative_cost_available": False,
    }
    values.update(overrides)
    return ProviderAuthorityEnvironment(**values)


def _authority(package, *, config=None, environment=None):
    return resolve_workflow_provider_authority(
        package,
        model_config=config or _config(),
        default_runtime=_runtime(),
        environment=environment or _environment(),
    )


def test_one_snapshot_resolves_primary_fallback_and_inline_routes_with_precedence(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        provider="custom-conflict",
        model="small",
        effort="low",
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "provider": "another-conflict",
                "model": "@primary",
                "effort": "high",
                "fallbackModel": "@recovery",
                "agents": {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Review the result",
                        "model": "large",
                    }
                },
            }
        ],
    )

    authority = _authority(_load_v5(path))

    assert isinstance(authority, WorkflowProviderAuthority)
    assert set(authority.routes) == {
        "ask:primary",
        "ask:fallback",
        "ask:inline_agent:reviewer",
    }
    assert authority.routes["ask:primary"].provider == "openrouter"
    assert authority.routes["ask:primary"].model == "openai/gpt-5.4"
    assert authority.routes["ask:primary"].provider_options["effort"] == "high"
    assert authority.routes["ask:fallback"].model == ("anthropic/claude-sonnet-4.5")
    assert authority.routes["ask:inline_agent:reviewer"].model == (
        "anthropic/claude-opus-4.1"
    )
    assert {warning.code for warning in authority.warnings} == {
        "model_reference_provider_overridden"
    }
    assert not hasattr(authority, "__dict__")
    with pytest.raises(FrozenInstanceError):
        authority.authority_digest = "0" * 64  # type: ignore[misc]


def test_fallback_route_classifies_inherited_capabilities_before_execution(
    tmp_path, workflow_writer
):
    config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
        },
        "model_aliases": {
            "primary": {
                "provider": "openrouter",
                "model": "openai/gpt-5.4",
            },
            "recovery": {
                "provider": "future-provider",
                "model": "future-model",
            },
        },
    })
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "fallbackModel": "@recovery",
            "allowed_tools": [],
        }],
    )
    package = _load_v5(path)

    authority = _authority(package, config=config)
    decisions = authority.obligations_by_path["nodes[0].allowed_tools"]
    report = assess_compatibility(package, provider_authority=authority)

    assert {item.route_id for item in decisions} == {
        "ask:primary",
        "ask:fallback",
    }
    fallback = next(item for item in decisions if item.route_id == "ask:fallback")
    assert fallback.decision.disposition.value == "unsupported"
    assert fallback.decision.code == "hermes_managed_loop_required"
    assert any(
        item.path == "nodes[0].allowed_tools"
        and item.code == "hermes_managed_loop_required"
        for item in report.blocking_findings
    )


def test_provider_routes_cover_only_nodes_that_can_call_the_provider(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[
            {"id": "shell", "bash": "true"},
            {
                "id": "review",
                "approval": {
                    "message": "Approve?",
                    "on_reject": {"prompt": "Revise the result"},
                },
            },
            {"id": "gate", "approval": {"message": "Approve?"}},
            {"id": "stop", "cancel": "stop"},
            {
                "id": "refine",
                "loop": {"prompt": "Refine", "until": "DONE", "max_iterations": 2},
            },
        ],
    )

    authority = _authority(_load_v5(path))

    assert set(authority.routes) == {"review:primary", "refine:primary"}


def test_approval_rework_blocks_options_its_executor_cannot_apply(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "review",
            "approval": {
                "message": "Approve?",
                "on_reject": {"prompt": "Revise the result"},
            },
            "persist_session": True,
            "allowed_tools": [],
            "hooks": {
                "PreToolUse": [{"response": {"continue": True}}],
            },
            "mcp": "local",
            "skills": ["review"],
            "agents": {
                "reviewer": {
                    "description": "Review",
                    "prompt": "Review the result",
                },
            },
        }],
    )
    (tmp_path / "mcp").mkdir()
    (tmp_path / "servers").mkdir()
    (tmp_path / "mcp/local.yaml").write_text(
        yaml.safe_dump({
            "command": sys.executable,
            "args": ["servers/main.py"],
        }),
        encoding="utf-8",
    )
    (tmp_path / "servers/main.py").write_text("print('sealed')\n", encoding="utf-8")

    package = _load_v5(path)
    authority = _authority(package)
    report = assess_compatibility(package, provider_authority=authority)

    decisions = {
        item.path: item.decision.disposition.value
        for item in authority.obligations
    }
    assert decisions["nodes[0].allowed_tools"] == "hermes_adapter"
    for path_key in (
        "nodes[0].persist_session",
        "nodes[0].mcp",
        "nodes[0].skills",
        "nodes[0].agents",
    ):
        assert decisions[path_key] == "unsupported"
        assert any(item.path == path_key for item in report.blocking_findings)
    hook_paths = [path_key for path_key in decisions if path_key.startswith("nodes[0].hooks")]
    assert hook_paths
    assert all(decisions[path_key] == "unsupported" for path_key in hook_paths)
    assert all(
        any(item.path == path_key for item in report.blocking_findings)
        for path_key in hook_paths
    )


@pytest.mark.parametrize(
    ("event", "response"),
    [
        ("SessionStart", {"systemMessage": "ignored"}),
        ("PostToolUse", {"continue": False}),
        ("UserPromptSubmit", {"decision": "block"}),
        (
            "UserPromptSubmit",
            {
                "systemMessage": "first",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "second",
                },
            },
        ),
        ("PreToolUse", {"systemMessage": "ignored"}),
        ("PreToolUse", {"stopReason": "ignored"}),
        ("PreToolUse", {"continue": True, "stopReason": "ignored"}),
        ("PreToolUse", {"decision": "approve", "stopReason": "ignored"}),
        (
            "PreToolUse",
            {
                "stopReason": "ignored",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            },
        ),
        (
            "PreToolUse",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecisionReason": "ignored",
                },
            },
        ),
        (
            "PreToolUse",
            {
                "stopReason": "silently dropped",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "preferred",
                },
            },
        ),
    ],
)
def test_hook_response_operations_block_when_the_runtime_event_ignores_them(
    tmp_path, workflow_writer, event, response
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "hooks": {event: [{"response": response}]},
        }],
    )
    package = _load_v5(path)
    authority = _authority(package)

    decision = authority.obligations_by_path[f"nodes[0].hooks.{event}[0]"][0]
    report = assess_compatibility(package, provider_authority=authority)

    assert decision.decision.disposition.value == "unsupported"
    assert any(
        item.path == f"nodes[0].hooks.{event}[0]"
        for item in report.blocking_findings
    )


@pytest.mark.parametrize(
    "response",
    [
        {"continue": True},
        {"decision": "approve"},
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            },
        },
    ],
)
def test_pre_tool_exact_allow_responses_use_normal_dispatch(
    tmp_path, workflow_writer, response
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "hooks": {"PreToolUse": [{"response": response}]},
        }],
    )
    package = _load_v5(path)
    authority = _authority(package)
    decision = authority.obligations_by_path["nodes[0].hooks.PreToolUse[0]"][0]
    report = assess_compatibility(package, provider_authority=authority)

    assert decision.decision.disposition.value == "hermes_adapter"
    assert not any(
        item.path == "nodes[0].hooks.PreToolUse[0]"
        for item in report.blocking_findings
    )


def test_user_prompt_context_is_the_only_non_tool_hook_response_currently_consumed(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "hooks": {
                "UserPromptSubmit": [{"response": {"systemMessage": "bounded"}}],
            },
        }],
    )

    authority = _authority(_load_v5(path))
    decision = authority.obligations_by_path[
        "nodes[0].hooks.UserPromptSubmit[0]"
    ][0]

    assert decision.decision.disposition.value == "hermes_adapter"


def test_pre_tool_response_requires_one_effective_consumed_operation(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "hooks": {
                "PreToolUse": [{
                    "response": {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "blocked",
                            "updatedInput": {"path": "safe.txt"},
                        },
                    },
                }],
            },
        }],
    )

    authority = _authority(_load_v5(path))
    decision = authority.obligations_by_path["nodes[0].hooks.PreToolUse[0]"][0]

    assert decision.decision.disposition.value == "hermes_adapter"


@pytest.mark.parametrize(
    "event",
    [
        "SubagentStart",
        "SubagentStop",
        "TaskCompleted",
        "Elicitation",
        "ElicitationResult",
        "PermissionRequest",
        "Setup",
        "InstructionsLoaded",
    ],
)
def test_hook_events_without_exact_worker_semantics_block(
    tmp_path, workflow_writer, event
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[{
            "id": "ask",
            "prompt": "hello",
            "hooks": {event: [{"response": {"continue": True}}]},
        }],
    )
    package = _load_v5(path)
    authority = _authority(package)

    decision = authority.obligations_by_path[f"nodes[0].hooks.{event}[0]"][0]
    report = assess_compatibility(package, provider_authority=authority)

    assert decision.decision.disposition.value == "unsupported"
    assert any(item.path == f"nodes[0].hooks.{event}[0]" for item in report.blocking_findings)


def test_every_accepted_provider_dependent_path_has_one_matrix_decision(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        modelReasoningEffort="high",
        webSearchMode="hermes_tool",
        fallbackModel="@recovery",
        betas=["preview"],
        sandbox={"enabled": True},
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "persist_session": True,
                "allowed_tools": [],
                "denied_tools": ["Bash"],
                "skills": ["review"],
                "mcp": "mcp/local.yaml",
                "agents": {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Review",
                    }
                },
                "hooks": {"PreToolUse": [{"response": {"continue": True}}]},
                "effort": "high",
                "thinking": "adaptive",
                "maxBudgetUsd": 2,
                "output_format": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            }
        ],
    )
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "local.yaml").write_text(
        "echo:\n  command: python\n", encoding="utf-8"
    )

    authority = _authority(_load_v5(path))

    assert set(authority.obligations_by_path) == {
        "modelReasoningEffort",
        "webSearchMode",
        "fallbackModel",
        "betas",
        "sandbox",
        "nodes[0].persist_session",
        "nodes[0].allowed_tools",
        "nodes[0].denied_tools",
        "nodes[0].skills",
        "nodes[0].mcp",
        "nodes[0].agents",
        "nodes[0].hooks.PreToolUse[0]",
        "nodes[0].effort",
        "nodes[0].thinking",
        "nodes[0].maxBudgetUsd",
        "nodes[0].output_format",
    }
    decision_keys = [
        (
            obligation.path,
            obligation.route_id,
            obligation.decision.feature,
            obligation.decision.option,
        )
        for obligation in authority.obligations
    ]
    assert len(decision_keys) == len(set(decision_keys))
    assert all(authority.obligations_by_path.values())
    allowed = authority.obligations_by_path["nodes[0].allowed_tools"][0]
    assert allowed.decision.requested_semantics["explicit_empty"] is True
    for inherited_path in (
        "nodes[0].persist_session",
        "nodes[0].allowed_tools",
        "nodes[0].denied_tools",
        "nodes[0].skills",
        "nodes[0].mcp",
        "nodes[0].agents",
        "nodes[0].hooks.PreToolUse[0]",
        "nodes[0].output_format",
    ):
        assert {
            item.route_id
            for item in authority.obligations_by_path[inherited_path]
        } == {"ask:primary", "ask:fallback"}
    for shared_authority_path in ("nodes[0].maxBudgetUsd", "sandbox"):
        assert {
            item.route_id
            for item in authority.obligations_by_path[shared_authority_path]
        } == {
            "ask:primary",
            "ask:fallback",
            "ask:inline_agent:reviewer",
        }


def test_legacy_capability_sets_cannot_promote_v5_budget_or_sandbox(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        sandbox={"enabled": True},
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "maxBudgetUsd": 1,
            }
        ],
    )
    package = _load_v5(path)
    authority = _authority(package)

    report = assess_compatibility(
        package,
        provider_capabilities={"openrouter": {"budget", "sandbox", "reasoning_effort"}},
        provider_authority=authority,
    )

    assert report.runnable is False
    assert {(finding.path, finding.code) for finding in report.blocking_findings} >= {
        ("nodes[0].maxBudgetUsd", "authoritative_cost_unavailable"),
        ("sandbox", "provider_native_sandbox_unavailable"),
    }


def test_alias_change_alters_authority_risk_and_risk_bound_trust(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path / "workflow",
        model="@primary",
        nodes=[{"id": "ask", "prompt": "hello", "effort": "high"}],
    )
    package = _load_v5(path)
    first = _authority(package, config=_config(primary_model="openai/gpt-5.4"))
    second = _authority(package, config=_config(primary_model="openai/gpt-5.5"))
    first_report = assess_compatibility(package, provider_authority=first)
    second_report = assess_compatibility(package, provider_authority=second)
    first_risk = build_risk_summary(
        package,
        first_report,
        provider_authority_digest=first.authority_digest,
    )
    second_risk = build_risk_summary(
        package,
        second_report,
        provider_authority_digest=second.authority_digest,
    )
    package_digest = compute_package_digest(package).sha256
    trust = WorkflowTrustStore(tmp_path / "home")
    trust.trust(
        package_digest,
        actor="test",
        risk_digest=first_risk.risk_digest,
    )

    assert first.authority_digest != second.authority_digest
    assert first_risk.risk_digest != second_risk.risk_digest
    assert trust.check(package_digest, risk_digest=first_risk.risk_digest) == "trusted"
    assert (
        trust.check(package_digest, risk_digest=second_risk.risk_digest) == "untrusted"
    )


def test_normalized_equivalent_tool_aliases_keep_provider_authority_identity(
    tmp_path, workflow_writer
):
    def package(root, tool):
        return _load_v5(
            workflow_writer(
                root,
                name="tool-alias",
                model="@primary",
                nodes=[
                    {
                        "id": "ask",
                        "prompt": "hello",
                        "allowed_tools": [tool],
                    }
                ],
            )
        )

    alias = _authority(package(tmp_path / "alias", "Read"))
    canonical = _authority(package(tmp_path / "canonical", "read_file"))

    assert alias.authority_digest == canonical.authority_digest


def test_unsupported_matrix_decision_blocks_with_stable_path_and_code(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="@primary",
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "hooks": {"Notification": [{"response": {"suppressOutput": True}}]},
            }
        ],
    )
    package = _load_v5(path)
    authority = _authority(package)

    report = assess_compatibility(package, provider_authority=authority)

    finding = next(
        item
        for item in report.blocking_findings
        if item.path == "nodes[0].hooks.Notification[0]"
    )
    assert finding.code == "hooks_precondition_unsatisfied"
    assert report.runnable is False


def test_execution_context_binds_authority_into_identity_compatibility_and_risk(
    tmp_path, workflow_writer
):
    package = _load_v5(
        workflow_writer(
            tmp_path,
            model="@primary",
            nodes=[{"id": "ask", "prompt": "hello", "effort": "high"}],
        )
    )

    def context(model):
        return execution_capability_context(
            surface="background",
            entitlement=AIEntitlementResolution("real"),
            runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
            runtime_capabilities=_runtime(),
            model_config_snapshot=_config(primary_model=model),
        )

    first = context("openai/gpt-5.4")
    second = context("openai/gpt-5.5")
    authority = first.provider_authority(package)
    compatibility, risk = assess_package_execution(package, first)

    assert authority is not None
    assert compatibility.runnable is True
    assert first.identity_digest_for(package) != second.identity_digest_for(package)
    assert (
        risk.risk_digest
        == build_risk_summary(
            package,
            compatibility,
            provider_authority_digest=authority.authority_digest,
        ).risk_digest
    )


def test_configured_provider_alias_may_classify_to_a_distinct_effective_provider(
    tmp_path, workflow_writer
):
    package = _load_v5(
        workflow_writer(
            tmp_path,
            model="@primary",
            nodes=[{"id": "ask", "prompt": "hello"}],
        )
    )
    config = parse_workflow_model_config({
        "model": {
            "provider": "openai-api",
            "default": "gpt-5.4",
            "base_url": "https://api.openai.com/v1",
            "api_mode": "chat_completions",
        },
        "model_aliases": {
            "primary": {
                "provider": "openai-api",
                "model": "gpt-5.4",
                "base_url": "https://api.openai.com/v1",
            }
        },
    })
    runtime = classify_execution_runtime(
        provider="openai-api",
        model_config={"provider": "openai-api", "default": "gpt-5.4"},
        provider_config={"base_url": "https://api.openai.com/v1"},
    )

    authority = resolve_workflow_provider_authority(
        package,
        model_config=config,
        default_runtime=runtime,
        environment=_environment(),
    )

    assert authority.routes["ask:primary"].provider == "openai-api"
