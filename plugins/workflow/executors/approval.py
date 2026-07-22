"""Durable workflow approval gate and bounded rejection rework executor."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from agent.plugin_agent import PluginAgentRunRequest
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
from plugins.workflow.resources import VariableContext
from plugins.workflow.store import ArtifactRef


def _artifact(path: Path, run_directory: Path) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type="text/plain",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


class ApprovalExecutor:
    def __init__(self, agent_runner=None) -> None:
        self.agent_runner = agent_runner

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
            message = variables.render_prompt(message)
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
        rework = context.node_state.get("approval_rework")
        if not isinstance(rework, Mapping):
            return self._gate_result(context)
        if self.agent_runner is None:
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
        prompt = variables.render_prompt(str(on_reject["prompt"]))
        wall_timeout = (
            context.deadline_budget.remaining_wall(context.monotonic())
            if context.deadline_budget is not None
            else context.timeout_seconds
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
        request = PluginAgentRunRequest(
            prompt=prompt,
            provider=context.workflow_options.get("provider"),
            model=context.workflow_options.get("model"),
            workdir=context.run_directory,
            max_iterations=90,
            max_api_attempts=(
                execution_limits.combined_retries
                if execution_limits is not None
                else context.max_provider_attempts
            ),
            idle_timeout_seconds=idle_timeout,
            wall_timeout_seconds=wall_timeout,
            provider_request_timeout_seconds=provider_timeout,
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
        result = self.agent_runner.run(request, is_cancelled=context.is_cancelled)
        if result.status == "paused":
            return NodeExecutionResult(
                "paused",
                metadata={
                    "pending_interaction": dict(result.pending_interaction or {})
                },
            )
        if result.status == "cancelled":
            return NodeExecutionResult("cancelled", error_code="cancelled")
        if result.status != "completed":
            return NodeExecutionResult(
                "failed",
                error_code="approval_rework_failed",
                error_message="isolated approval rework failed",
            )
        attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        output = attempt / "rework-output.txt"
        output.write_text(result.final_response, encoding="utf-8")
        attempts = int(context.node_state.get("approval_rework_attempts", 0)) + 1
        return self._gate_result(
            context,
            artifacts=(_artifact(output, context.run_directory),),
            rework_attempts=attempts,
        )


__all__ = ["ApprovalExecutor"]
