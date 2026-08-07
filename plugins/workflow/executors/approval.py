"""Durable workflow approval gate and bounded rejection rework executor."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from agent.plugin_agent import PluginAgentRunRequest
from hermes_cli.provider_capabilities import encode_provider_option_transport
from plugins.workflow.compat import resolve_tool_name
from plugins.workflow.entitlement import (
    AIExecutionIntegrityError,
    entitled_agent_runner,
)
from plugins.workflow.executors.base import (
    NodeExecutionContext,
    NodeExecutionResult,
    conservative_provider_retry_count,
    sealed_provider_request_for_launch,
)
from plugins.workflow.language import supports_phase5_semantics
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.store import ArtifactRef


def _artifact(path: Path, run_directory: Path) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type="text/plain",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _phase5_execution_identity_failure(message: str) -> NodeExecutionResult:
    return NodeExecutionResult(
        "failed",
        error_code="provider_capability_drift",
        error_message=message,
        metadata={
            "provider_attempts": 0,
            "provider_attempts_exact": True,
            "known_no_effect": True,
            "archon_terminal_failure": True,
        },
    )


class ApprovalExecutor:
    def __init__(self, agent_runner=None, *, deterministic_runner=None) -> None:
        self.agent_runner = agent_runner
        self.deterministic_runner = deterministic_runner

    @staticmethod
    def _gate_result(
        context: NodeExecutionContext,
        *,
        artifacts=(),
        rework_attempts: int | None = None,
    ):
        approval = context.node.value
        generation = int(context.node_state.get("approval_generation", 0)) + 1
        message = str(approval["message"])
        variables = context.variable_context
        if isinstance(variables, VariableContext):
            renderer = substitution_renderer(
                variables,
                direct_dependencies=context.node.depends_on,
                output_resolver=context.output_resolver,
            )
            message = renderer.render_prompt(message)
        identity = hashlib.sha256(
            f"{context.run_id}\0{context.node.id}\0{generation}\0{message}".encode()
        ).hexdigest()
        return NodeExecutionResult(
            "paused",
            tuple(artifacts),
            metadata={
                "pending_interaction": {
                    "type": "workflow_approval",
                    "interaction_id": identity,
                    "generation": generation,
                    "message": message,
                },
                "approval_generation": generation,
                "approval_rework_attempts": int(
                    context.node_state.get("approval_rework_attempts", 0)
                    if rework_attempts is None
                    else rework_attempts
                ),
            },
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        if context.node.node_type != "approval":
            return NodeExecutionResult(
                "failed",
                error_code="unsupported_approval_node",
                error_message=context.node.node_type,
            )
        phase5 = supports_phase5_semantics(
            context.language_profile,
            context.normalizer_version,
        )
        rework = context.node_state.get("approval_rework")
        if not isinstance(rework, Mapping):
            return self._gate_result(context)
        sealed_route = context.sealed_provider_route
        sealed_authority = context.sealed_provider_authority
        intended_authority_digest = context.intended_authority_digest
        if phase5 and (
            sealed_route is None
            or sealed_authority is None
            or sealed_authority.routes.get(f"{context.node.id}:primary")
            != sealed_route
            or sealed_route.node_id != context.node.id
            or sealed_route.role != "primary"
            or not isinstance(intended_authority_digest, str)
            or len(intended_authority_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in intended_authority_digest
            )
        ):
            return _phase5_execution_identity_failure(
                "sealed approval rework authority is incomplete"
            )
        try:
            agent_runner = entitled_agent_runner(
                context.ai_entitlement,
                self.agent_runner,
                self.deterministic_runner,
            )
        except AIExecutionIntegrityError as exc:
            return NodeExecutionResult(
                "failed",
                error_code="execution_integrity",
                error_message=str(exc),
            )
        if agent_runner is None:
            return NodeExecutionResult(
                "failed",
                error_code="approval_rework_unavailable",
                error_message="approval rejection rework requires an agent runner",
            )
        on_reject = context.node.value.get("on_reject")
        if not isinstance(on_reject, Mapping):
            return NodeExecutionResult(
                "failed",
                error_code="approval_rework_invalid",
                error_message="approval on_reject policy is missing",
            )
        variables = context.variable_context
        if not isinstance(variables, VariableContext):
            variables = VariableContext(workflow_id=context.run_id)
        variables = replace(variables, rejection_reason=str(rework.get("reason") or ""))
        renderer = substitution_renderer(
            variables,
            direct_dependencies=context.node.depends_on,
            output_resolver=context.output_resolver,
        )
        prompt = renderer.render_prompt(str(on_reject["prompt"]))
        request_started_at = context.monotonic()
        wall_timeout = (
            context.deadline_budget.remaining_wall(request_started_at)
            if context.deadline_budget is not None
            else context.timeout_seconds
        )
        if context.sealed_attempt_timeout and wall_timeout <= 0:
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                error_message="workflow attempt deadline expired",
                metadata={"provider_attempts": 0},
            )
        wall_deadline = (
            context.deadline_budget.wall_deadline
            if context.deadline_budget is not None
            else request_started_at + wall_timeout
        )
        execution_limits = context.execution_limits
        idle_timeout = min(
            wall_timeout,
            (
                execution_limits.ai_idle_timeout_seconds
                if execution_limits is not None
                else (
                    context.deadline_budget.idle_seconds
                    if context.deadline_budget is not None
                    else 300
                )
            ),
        )
        provider_timeout = min(
            wall_timeout,
            (
                execution_limits.provider_request_timeout_seconds
                if execution_limits is not None
                else (
                    context.deadline_budget.provider_seconds
                    if context.deadline_budget is not None
                    else 300
                )
            ),
        )
        granted_provider_attempts = min(
            context.max_provider_attempts,
            (
                execution_limits.combined_retries
                if execution_limits is not None
                else context.max_provider_attempts
            ),
        )
        reasoning_config = None
        request_overrides: Mapping[str, Any] = {}
        sealed_fallback_route = None
        if phase5:
            assert sealed_route is not None
            assert sealed_authority is not None
            try:
                transport = encode_provider_option_transport(
                    sealed_route,
                    sealed_authority.obligations,
                )
            except ValueError as exc:
                return _phase5_execution_identity_failure(str(exc))
            reasoning_config = dict(transport.reasoning_config) or None
            request_overrides = _thaw(transport.request_overrides)
            fallback_reference = context.node.options.get(
                "fallbackModel", context.workflow_options.get("fallbackModel")
            )
            if fallback_reference is not None:
                fallback_route = sealed_authority.routes.get(
                    f"{context.node.id}:fallback"
                )
                if fallback_route is None:
                    return _phase5_execution_identity_failure(
                        "sealed approval fallback route is missing"
                    )
                try:
                    fallback_transport = encode_provider_option_transport(
                        fallback_route,
                        sealed_authority.obligations,
                    )
                except ValueError as exc:
                    return _phase5_execution_identity_failure(str(exc))
                sealed_fallback_route = {
                    "provider": fallback_route.provider,
                    "model": fallback_route.model,
                    "context_mode": "fresh",
                    "expected_runtime_identity": {
                        "provider": fallback_route.effective_provider,
                        "model": fallback_route.model,
                        "api_mode": fallback_route.api_mode,
                        "base_url_trust_class": fallback_route.base_url_trust_class,
                        "registration_provenance_digest": (
                            fallback_route.registration_provenance_digest
                        ),
                    },
                    "reasoning_config": dict(fallback_transport.reasoning_config),
                    "request_overrides": _thaw(
                        fallback_transport.request_overrides
                    ),
                    "structured_output": None,
                }
        request = PluginAgentRunRequest(
            prompt=prompt,
            provider=(
                sealed_route.provider
                if phase5
                else context.workflow_options.get("provider")
            ),
            model=(
                sealed_route.model
                if phase5
                else context.workflow_options.get("model")
            ),
            intended_authority_digest=(
                intended_authority_digest if phase5 else None
            ),
            expected_runtime_identity=(
                {
                    "provider": sealed_route.effective_provider,
                    "model": sealed_route.model,
                    "api_mode": sealed_route.api_mode,
                    "base_url_trust_class": sealed_route.base_url_trust_class,
                    "registration_provenance_digest": (
                        sealed_route.registration_provenance_digest
                    ),
                }
                if phase5
                else None
            ),
            reasoning_config=reasoning_config,
            request_overrides=request_overrides,
            sealed_fallback_route=sealed_fallback_route,
            allowed_tools=(
                tuple(
                    resolve_tool_name(name)
                    for name in context.node.options["allowed_tools"]
                )
                if phase5 and "allowed_tools" in context.node.options
                else None
            ),
            denied_tools=(
                tuple(
                    resolve_tool_name(name)
                    for name in context.node.options.get("denied_tools", ())
                )
                if phase5
                else ()
            ),
            ephemeral_system_prompt=(
                context.node.options.get("systemPrompt") if phase5 else None
            ),
            workdir=context.run_directory,
            max_iterations=90,
            max_api_attempts=granted_provider_attempts,
            sealed_provider_attempt_grant=phase5,
            idle_timeout_seconds=idle_timeout,
            wall_timeout_seconds=wall_timeout,
            provider_request_timeout_seconds=provider_timeout,
            absolute_wall_deadline=(wall_deadline if phase5 else None),
            absolute_idle_deadline=(
                min(
                    wall_deadline,
                    (
                        context.deadline_budget.last_semantic_progress
                        if context.deadline_budget is not None
                        else request_started_at
                    )
                    + idle_timeout,
                )
                if phase5
                else None
            ),
            absolute_provider_deadline=(
                min(wall_deadline, request_started_at + provider_timeout)
                if phase5
                else None
            ),
            approved_action_digest=(
                str(context.node_state["approved_action_digest"])
                if context.node_state.get("approved_action_digest")
                else None
            ),
            max_process_tree_rss_bytes=(
                execution_limits.process_tree_rss_bytes
                if execution_limits is not None
                else context.resource_limits.max_rss_bytes
            ),
            max_process_tree_cpu_seconds=(
                execution_limits.process_tree_cpu_seconds
                if execution_limits is not None
                else context.resource_limits.max_cpu_seconds
            ),
            max_descendants=(
                execution_limits.max_descendants
                if execution_limits is not None
                else context.resource_limits.max_descendants
            ),
            cooperative_shutdown_seconds=(
                execution_limits.cooperative_shutdown_seconds
                if execution_limits is not None
                else context.termination_policy.cooperative_grace_seconds
            ),
            term_grace_seconds=(
                execution_limits.term_grace_seconds
                if execution_limits is not None
                else context.termination_policy.term_grace_seconds
            ),
            kill_reap_grace_seconds=(
                execution_limits.kill_reap_grace_seconds
                if execution_limits is not None
                else context.termination_policy.kill_grace_seconds
            ),
        )
        request = sealed_provider_request_for_launch(context, request)
        if request is None:
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                error_message="workflow attempt deadline expired",
                metadata={"provider_attempts": 0},
            )
        if phase5 and context.is_cancelled is not None and context.is_cancelled():
            return NodeExecutionResult(
                "cancelled",
                error_code="cancelled",
                metadata={"provider_attempts": 0},
            )
        launch_kwargs = {"is_cancelled": context.is_cancelled}
        if phase5 and getattr(agent_runner, "starts_request_mcp", False):
            launch_kwargs.update({
                name: callback
                for name, callback in {
                    "spawn_intent": context.spawn_intent,
                    "spawn_failed": context.spawn_failed,
                    "process_started": context.process_started,
                    "provider_dispatch": context.provider_dispatch,
                    "provider_start_delivered": context.provider_start_delivered,
                    "provider_execute_received": context.provider_execute_received,
                    "provider_execute_release": context.provider_execute_release,
                    "process_stopped": context.process_stopped,
                }.items()
                if callback is not None
            })
        try:
            result = agent_runner.run(request, **launch_kwargs)
        except PermissionError as exc:
            return NodeExecutionResult(
                "failed",
                error_code="authorization",
                error_message=str(exc),
            )
        except OSError as exc:
            return NodeExecutionResult(
                "failed",
                error_code="network_error",
                error_message=str(exc),
                metadata={
                    "provider_attempts": conservative_provider_retry_count(
                        None,
                        granted_attempts=granted_provider_attempts,
                    )
                },
            )
        except RuntimeError as exc:
            return NodeExecutionResult(
                "failed",
                error_code="agent_execution_failed",
                error_message=str(exc),
                metadata={
                    "provider_attempts": conservative_provider_retry_count(
                        None,
                        granted_attempts=granted_provider_attempts,
                    )
                },
            )
        if result.status == "cancelled":
            return NodeExecutionResult("cancelled", error_code="cancelled")
        execution_identity: dict[str, str] = {}
        if phase5:
            observed_intended = result.audit.get("intended_authority_digest")
            observed_prefix = result.audit.get("model_visible_prefix_digest")
            if (
                observed_intended != intended_authority_digest
                or not isinstance(observed_prefix, str)
                or len(observed_prefix) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in observed_prefix
                )
            ):
                return NodeExecutionResult(
                    "failed",
                    error_code="provider_capability_drift",
                    error_message="worker execution identity evidence is invalid",
                    metadata={
                        "provider_attempts": conservative_provider_retry_count(
                            result.audit.get("provider_attempts"),
                            granted_attempts=granted_provider_attempts,
                        ),
                        "archon_terminal_failure": True,
                    },
                )
            execution_identity = {
                "intended_authority_digest": observed_intended,
                "model_visible_prefix_digest": observed_prefix,
            }
        if result.status == "paused":
            return NodeExecutionResult(
                "paused",
                metadata={
                    "pending_interaction": dict(result.pending_interaction or {}),
                    **execution_identity,
                },
            )
        if result.status != "completed":
            metadata: dict[str, object] = {
                "audit": dict(result.audit),
                **execution_identity,
            }
            provider_attempts = conservative_provider_retry_count(
                result.audit.get("provider_attempts"),
                granted_attempts=granted_provider_attempts,
            )
            metadata["provider_attempts"] = provider_attempts
            return NodeExecutionResult(
                "failed",
                error_code="approval_rework_failed",
                error_message="isolated approval rework failed",
                metadata=metadata,
            )
        attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        output = attempt / "rework-output.txt"
        output.write_text(result.final_response, encoding="utf-8")
        attempts = int(context.node_state.get("approval_rework_attempts", 0)) + 1
        gate_result = self._gate_result(
            context,
            artifacts=(_artifact(output, context.run_directory),),
            rework_attempts=attempts,
        )
        if not execution_identity:
            return gate_result
        return replace(
            gate_result,
            metadata={**dict(gate_result.metadata), **execution_identity},
        )


__all__ = ["ApprovalExecutor"]
