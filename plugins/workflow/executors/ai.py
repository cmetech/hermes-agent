"""Command and prompt execution through the isolated host agent facade."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from agent.plugin_agent import PluginAgentRunRequest
from plugins.workflow.compat import resolve_tool_name
from plugins.workflow.entitlement import (
    AIExecutionIntegrityError,
    entitled_agent_runner,
)
from plugins.workflow.executors.base import (
    NodeExecutionContext,
    NodeExecutionResult,
    conservative_provider_retry_count,
    validated_provider_retry_count,
)
from plugins.workflow.resources import (
    AuthenticatedExecutionMaterializer,
    ResourceResolver,
    VariableContext,
)
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import ArtifactRef


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


def _artifact(path: Path, run_directory: Path) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type="application/json" if path.suffix == ".json" else "text/plain",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


class AgentNodeExecutor:
    def __init__(
        self,
        agent_runner,
        *,
        session_registry: NodeSessionRegistry | None = None,
        profile_name: str = "default",
        deterministic_runner=None,
    ) -> None:
        self.agent_runner = agent_runner
        self.deterministic_runner = deterministic_runner
        self.session_registry = session_registry
        self.profile_name = profile_name

    def _fingerprint(self, context: NodeExecutionContext) -> str:
        node = context.node
        workflow = context.workflow_options
        material = {
            "provider": node.options.get("provider") or workflow.get("provider"),
            "model": node.options.get("model") or workflow.get("model"),
            "allowed_tools": list(node.options.get("allowed_tools", ())),
            "denied_tools": list(node.options.get("denied_tools", ())),
            "mcp": node.options.get("mcp"),
            "profile": self.profile_name,
            "reasoning": {
                key: node.options.get(key, workflow.get(key))
                for key in (
                    "effort",
                    "thinking",
                    "systemPrompt",
                    "fallbackModel",
                    "betas",
                    "sandbox",
                )
            },
        }
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _failure(code: str, message: str) -> NodeExecutionResult:
        return NodeExecutionResult("failed", (), code, message)

    def _prompt(self, context: NodeExecutionContext) -> str:
        node = context.node
        if node.node_type == "command":
            template = (
                ResourceResolver(
                    context.run_directory,
                    sealed_paths=context.sealed_resource_paths,
                    sealed_bytes=context.sealed_resource_bytes,
                ).command(str(node.value)).body
            )
        else:
            template = str(node.value)
        variables = context.variable_context
        if not isinstance(variables, VariableContext):
            variables = VariableContext(workflow_id=context.run_id)
        prompt = variables.render_prompt(template)
        if node.options.get("skills"):
            skill_text = ResourceResolver(
                context.run_directory,
                sealed_paths=context.sealed_resource_paths,
                sealed_bytes=context.sealed_resource_bytes,
            ).text(f"node-skills/{node.id}.md")
            prompt = f"{skill_text}\n\n{prompt}"
        return prompt

    @staticmethod
    def _inline_agents(context: NodeExecutionContext) -> dict[str, dict[str, object]]:
        definitions: dict[str, dict[str, object]] = {}
        for agent_id, raw in context.node.options.get("agents", {}).items():
            allowed = (
                [resolve_tool_name(name) for name in raw["tools"]]
                if "tools" in raw
                else None
            )
            denied = [
                resolve_tool_name(name) for name in raw.get("disallowedTools", ())
            ]
            for forbidden in ("delegate_task",):
                if forbidden not in denied:
                    denied.append(forbidden)
            instructions = ""
            if raw.get("skills"):
                instructions = ResourceResolver(
                    context.run_directory,
                    sealed_paths=context.sealed_resource_paths,
                    sealed_bytes=context.sealed_resource_bytes,
                ).text(
                    f"node-agent-skills/{context.node.id}/{agent_id}.md"
                )
            definitions[str(agent_id)] = {
                "description": str(raw["description"]),
                "prompt": str(raw["prompt"]),
                "model": raw.get("model"),
                "allowed_tools": allowed,
                "denied_tools": denied,
                "instructions": instructions,
                "max_iterations": min(int(raw.get("maxTurns", 90)), 90),
            }
        return definitions

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        node = context.node
        if node.node_type not in {"command", "prompt"}:
            return self._failure("unsupported_ai_node", node.node_type)
        materializer: AuthenticatedExecutionMaterializer | None = None
        try:
            agent_runner = entitled_agent_runner(
                context.ai_entitlement,
                self.agent_runner,
                self.deterministic_runner,
            )
        except AIExecutionIntegrityError as exc:
            return self._failure("execution_integrity", str(exc))
        if agent_runner is None:
            return self._failure(
                "agent_runner_unavailable", "real agent execution is unavailable"
            )
        fingerprint = self._fingerprint(context)
        explicit_context = node.options.get("context")
        context_mode = "fresh"
        session_id = None
        warnings: list[str] = []
        registry_key = None
        expected_generation = 0

        if explicit_context == "shared":
            predecessors = [
                context.predecessor_results.get(dependency)
                for dependency in node.depends_on
                if context.predecessor_results.get(dependency) is not None
            ]
            if len(predecessors) != 1:
                return self._failure(
                    "context_ambiguous",
                    "shared context requires exactly one completed predecessor; use fresh",
                )
            predecessor = predecessors[0]
            if predecessor.get("cache_fingerprint") != fingerprint:
                return self._failure(
                    "context_incompatible",
                    "shared context cache fingerprint changed; use fresh context",
                )
            session_id = str(predecessor.get("session_id") or "")
            if not session_id:
                return self._failure(
                    "context_missing_session",
                    "shared predecessor has no resumable session; use fresh context",
                )
            context_mode = "shared"
        else:
            persist = bool(
                node.options.get(
                    "persist_session",
                    context.workflow_options.get("persist_sessions", False),
                )
            )
            if (
                persist
                and context.ai_entitlement.value == "real"
                and explicit_context != "fresh"
                and self.session_registry is not None
            ):
                provider = str(
                    node.options.get("provider")
                    or context.workflow_options.get("provider")
                    or "default"
                )
                registry_key = NodeSessionKey(
                    context.workflow_name,
                    node.id,
                    context.operator_scope,
                    provider,
                    self.profile_name,
                )
                record = self.session_registry.get(registry_key)
                if record is not None:
                    expected_generation = record.generation
                    if record.cache_fingerprint == fingerprint:
                        context_mode = "shared"
                        session_id = record.session_id
                    else:
                        warnings.append(
                            "stale persistent session replaced after cache change"
                        )

        try:
            execution_limits = context.execution_limits
            granted_provider_attempts = min(
                context.max_provider_attempts,
                (
                    execution_limits.combined_retries
                    if execution_limits is not None
                    else context.max_provider_attempts
                ),
            )
            wall_timeout = (
                context.deadline_budget.remaining_wall(context.monotonic())
                if context.deadline_budget is not None
                else float(context.timeout_seconds)
            )
            idle_timeout = min(
                context.deadline_budget.idle_seconds
                if context.deadline_budget is not None
                else float(node.options.get("idle_timeout", 300)),
                wall_timeout,
            )
            provider_timeout = min(
                context.deadline_budget.provider_seconds
                if context.deadline_budget is not None
                else 300.0,
                wall_timeout,
            )
            allowed_tools = (
                tuple(resolve_tool_name(name) for name in node.options["allowed_tools"])
                if "allowed_tools" in node.options
                else None
            )
            denied_tools = tuple(
                resolve_tool_name(name) for name in node.options.get("denied_tools", ())
            )
            hooks = tuple(
                {"event": event, **_thaw(entry)}
                for event, entries in node.options.get("hooks", {}).items()
                for entry in entries
            )
            if "mcp" in node.options and context.sealed_resource_bytes is not None:
                materializer = AuthenticatedExecutionMaterializer()
            mcp_servers = (
                ResourceResolver(
                    context.run_directory,
                    sealed_paths=context.sealed_resource_paths,
                    sealed_bytes=context.sealed_resource_bytes,
                ).mcp_servers(
                    str(node.options["mcp"]),
                    materializer=materializer,
                )
                if "mcp" in node.options
                else None
            )
            inline_agents = self._inline_agents(context)
            denied_set = list(denied_tools)
            if "delegate_task" not in denied_set:
                denied_set.append("delegate_task")
            denied_tools = tuple(denied_set)
            if (
                inline_agents
                and allowed_tools is not None
                and "workflow_agent" not in allowed_tools
            ):
                allowed_tools = (*allowed_tools, "workflow_agent")
            effort = (
                node.options.get("effort")
                or context.workflow_options.get("modelReasoningEffort")
                or context.workflow_options.get("effort")
            )
            thinking = node.options.get("thinking") or context.workflow_options.get(
                "thinking"
            )
            reasoning_config = {
                key: value
                for key, value in {
                    "effort": effort,
                    "thinking": _thaw(thinking),
                }.items()
                if value is not None
            }
            fallback_model = node.options.get(
                "fallbackModel", context.workflow_options.get("fallbackModel")
            )
            request_overrides = {}
            betas = node.options.get("betas", context.workflow_options.get("betas"))
            web_mode = context.workflow_options.get("webSearchMode")
            if betas is not None:
                request_overrides["betas"] = _thaw(betas)
            if web_mode is not None:
                request_overrides["web_search_mode"] = web_mode
            request = PluginAgentRunRequest(
                prompt=self._prompt(context),
                provider=node.options.get("provider")
                or context.workflow_options.get("provider"),
                model=node.options.get("model")
                or context.workflow_options.get("model"),
                context_mode=context_mode,
                session_id=session_id,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                skills=(),
                hooks=hooks,
                mcp_servers=mcp_servers,
                inline_agents=inline_agents,
                reasoning_config=reasoning_config or None,
                fallback_model=str(fallback_model) if fallback_model else None,
                ephemeral_system_prompt=node.options.get("systemPrompt"),
                request_overrides=request_overrides,
                max_budget_usd=node.options.get(
                    "maxBudgetUsd", context.workflow_options.get("maxBudgetUsd")
                ),
                sandbox_policy=_thaw(
                    node.options.get("sandbox", context.workflow_options.get("sandbox"))
                ),
                approved_action_digest=(
                    str(context.node_state["approved_action_digest"])
                    if context.node_state.get("approved_action_digest")
                    else None
                ),
                workdir=context.run_directory,
                max_iterations=90,
                max_api_attempts=granted_provider_attempts,
                idle_timeout_seconds=idle_timeout,
                wall_timeout_seconds=wall_timeout,
                provider_request_timeout_seconds=provider_timeout,
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
            result = agent_runner.run(
                request,
                is_cancelled=context.is_cancelled,
            )
        except PermissionError as exc:
            return self._failure("authorization", str(exc))
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
        except ValueError as exc:
            return self._failure("validation", str(exc))
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
        finally:
            if materializer is not None:
                materializer.cleanup()

        metadata: dict[str, object] = {
            "session_id": result.session_id,
            "provider": result.provider,
            "model": result.model,
            "usage": dict(result.usage),
            "audit": dict(result.audit),
            "cache_fingerprint": fingerprint,
            "warnings": warnings,
        }
        provider_attempts = validated_provider_retry_count(
            result.audit.get("provider_attempts"),
            granted_attempts=granted_provider_attempts,
        )
        if provider_attempts is not None:
            metadata["provider_attempts"] = provider_attempts
        if result.status == "paused":
            metadata["pending_interaction"] = dict(result.pending_interaction or {})
            return NodeExecutionResult("paused", metadata=metadata)
        if result.status == "cancelled":
            reason = (
                context.cancellation_reason()
                if context.cancellation_reason is not None
                else "cancelled"
            )
            return NodeExecutionResult(
                "interrupted" if reason == "shutdown" else "cancelled",
                error_code=reason or "cancelled",
                metadata=metadata,
            )
        if result.status != "completed":
            failure_kind = str(result.audit.get("failure_kind", "")).lower()
            if failure_kind == "package_mcp_unavailable":
                error_code = "package_mcp_unavailable"
            elif (
                "unknown_side_effect" in failure_kind
                or "outcome_unknown" in failure_kind
            ):
                error_code = "unknown_side_effect"
            elif "timeout" in failure_kind or "stall" in failure_kind:
                error_code = "provider_timeout"
            elif "resource_limit" in failure_kind:
                error_code = "resource_limit"
            elif "rate" in failure_kind and "limit" in failure_kind:
                error_code = "rate_limit"
            elif "credit" in failure_kind:
                error_code = "credit_exhausted"
            elif "authentic" in failure_kind:
                error_code = "authentication"
            elif "permission" in failure_kind or "authoriz" in failure_kind:
                error_code = "authorization"
            elif "value" in failure_kind or "validation" in failure_kind:
                error_code = "validation"
            elif any(part in failure_kind for part in ("network", "connection", "eof")):
                error_code = "network_disconnect"
            else:
                error_code = "agent_failed"
            if provider_attempts is None:
                # The host loop does not currently expose its exact retry
                # counter. Charge the full granted retry allowance so the
                # workflow and provider layers can never multiply attempts.
                metadata["provider_attempts"] = conservative_provider_retry_count(
                    result.audit.get("provider_attempts"),
                    granted_attempts=granted_provider_attempts,
                )
            return NodeExecutionResult(
                "failed",
                (),
                error_code,
                "isolated agent execution failed",
                metadata,
            )

        output = result.final_response
        schema = node.options.get("output_format")
        extension = ".txt"
        if schema is not None:
            try:
                value = json.loads(output)
            except json.JSONDecodeError as exc:
                return self._failure("structured_output_invalid", str(exc))
            try:
                import jsonschema
            except ImportError:
                return self._failure(
                    "structured_output_unavailable",
                    "jsonschema is required; install the Hermes mcp or all extra",
                )
            try:
                jsonschema.validate(value, _thaw(schema))
            except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
                return self._failure("structured_output_invalid", exc.message)
            extension = ".json"

        attempt = context.run_directory / "nodes" / node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        output_path = attempt / f"output{extension}"
        output_path.write_text(output, encoding="utf-8")
        artifact = _artifact(output_path, context.run_directory)
        metadata["output"] = output
        if registry_key is not None and self.session_registry is not None:
            updated = self.session_registry.compare_and_set(
                registry_key,
                expected_generation,
                result.session_id,
                fingerprint,
            )
            if not updated:
                warnings.append("newer persistent session retained")
        return NodeExecutionResult("succeeded", (artifact,), metadata=metadata)


__all__ = ["AgentNodeExecutor"]
