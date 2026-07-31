"""Command and prompt execution through the isolated host agent facade."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from agent.plugin_agent import PluginAgentRunRequest
from agent.structured_output import (
    MAX_OUTPUT_BYTES,
    StructuredOutputError,
    StructuredOutputRequest,
    StructuredOutputStrategy,
    StructuredOutputValidatorUnavailable,
    normalize_schema,
    parse_validate_canonicalize,
    require_structured_output_validator,
)
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
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    PrimaryOutputCandidate,
    write_archon_output_exclusive,
)
from plugins.workflow.resources import (
    AuthenticatedExecutionMaterializer,
    ResourceResolver,
    VariableContext,
)
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import ArtifactRef


_MAX_REPAIR_RESPONSE_BYTES = 256_000
_MAX_REPAIR_PROMPT_CHARS = 500_000
_MAX_REPAIR_REQUEST_WIRE_BYTES = 900_000


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


def _artifact_for_bytes(
    path: Path,
    run_directory: Path,
    data: bytes,
    *,
    media_type: str,
) -> ArtifactRef:
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _aggregate_usage(
    first: Mapping[str, int | float | None],
    second: Mapping[str, int | float | None],
) -> dict[str, int | float | None]:
    aggregate: dict[str, int | float | None] = {}
    for key in sorted(set(first) | set(second)):
        values = [
            value for value in (first.get(key), second.get(key)) if value is not None
        ]
        if values and all(isinstance(value, int | float) for value in values):
            aggregate[key] = sum(values)
        else:
            aggregate[key] = second.get(key, first.get(key))
    return aggregate


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
        structured_identity = None
        if (
            context.language_profile is WorkflowLanguageProfile.ARCHON_2026_07
            and context.structured_output is not None
            and context.structured_output_decision is not None
        ):
            structured_identity = {
                "schema_fingerprint": context.structured_output.schema_fingerprint,
                "strategy": context.structured_output_decision.strategy.value,
                "adapter_version": context.structured_output_decision.adapter_version,
                "effective_provider": (
                    context.structured_output_decision.effective_provider
                ),
                "model": context.structured_output_decision.model,
                "api_mode": context.structured_output_decision.api_mode,
                "declaration_source": (
                    context.structured_output_decision.declaration_source
                ),
            }
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
        if structured_identity is not None:
            material["structured_output"] = structured_identity
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _failure(code: str, message: str) -> NodeExecutionResult:
        return NodeExecutionResult("failed", (), code, message)

    @staticmethod
    def _requested_provider(context: NodeExecutionContext) -> str | None:
        """Return the routable selector from the immutable package snapshot."""
        provider = context.node.options.get("provider") or context.workflow_options.get(
            "provider"
        )
        return provider if isinstance(provider, str) and provider else None

    @staticmethod
    def _structured_request(
        context: NodeExecutionContext,
    ) -> StructuredOutputRequest | None:
        if context.language_profile is not WorkflowLanguageProfile.ARCHON_2026_07:
            return None
        declared = context.structured_output
        decision = context.structured_output_decision
        if declared is None:
            if context.node.options.get("output_format") is not None:
                raise ValueError("admitted structured-output schema is missing")
            return None
        if decision is None:
            raise ValueError("admitted structured-output decision is missing")
        normalized = normalize_schema(_thaw(declared.canonical_schema))
        if (
            normalized.schema_fingerprint != declared.schema_fingerprint
            or decision.schema_fingerprint != declared.schema_fingerprint
            or declared.canonicalization_version != 1
        ):
            raise ValueError("admitted structured-output identity is contradictory")
        return StructuredOutputRequest(
            schema=normalized,
            strategy=decision.strategy,
            adapter_version=decision.adapter_version,
            output_bytes_limit=MAX_OUTPUT_BYTES,
            canonicalization_version=declared.canonicalization_version,
        )

    @staticmethod
    def _structured_counts(
        result,
        request: StructuredOutputRequest,
        decision,
        *,
        declaration_source: str,
        provider_limit: int,
        model_limit: int,
        require_positive: bool,
    ) -> tuple[int, int]:
        evidence = result.structured_output
        if not isinstance(evidence, Mapping):
            raise ValueError("structured output evidence is missing")
        if (
            result.provider != decision.effective_provider
            or result.model != decision.model
            or result.audit.get("api_mode") != decision.api_mode
            or evidence.get("strategy") != request.strategy.value
            or evidence.get("adapter_version") != request.adapter_version
            or evidence.get("schema_fingerprint") != request.schema.schema_fingerprint
            or evidence.get("declaration_source") != declaration_source
        ):
            raise ValueError("structured output evidence does not match admission")
        provider_attempts = evidence.get("provider_attempts")
        model_calls = evidence.get("model_calls")
        if (
            isinstance(provider_attempts, bool)
            or not isinstance(provider_attempts, int)
            or not (1 if require_positive else 0) <= provider_attempts <= provider_limit
            or isinstance(model_calls, bool)
            or not isinstance(model_calls, int)
            or not (1 if require_positive else 0) <= model_calls <= model_limit
        ):
            raise ValueError("structured output accounting is invalid")
        return provider_attempts, model_calls

    @staticmethod
    def _uncertain_effects(audit: Mapping[str, object]) -> bool:
        failure_kind = str(audit.get("failure_kind", "")).lower()
        return bool(
            audit.get("unknown_side_effect") is True
            or audit.get("outcome_unknown") is True
            or audit.get("reconciliation_required") is True
            or "unknown_side_effect" in failure_kind
            or "outcome_unknown" in failure_kind
        )

    @staticmethod
    def _repair_prompt(
        request: StructuredOutputRequest,
        invalid_response: str,
        diagnostics: str,
    ) -> str:
        return json.dumps(
            {
                "canonical_schema": json.loads(
                    request.schema.canonical_schema_bytes.decode("utf-8")
                ),
                "invalid_response": invalid_response,
                "validation_diagnostics": diagnostics,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _bounded_repair_request(
        cls,
        template: PluginAgentRunRequest,
        structured_request: StructuredOutputRequest,
        invalid_response: str,
        diagnostics: str,
    ) -> PluginAgentRunRequest | None:
        def candidate(excerpt_length: int) -> PluginAgentRunRequest | None:
            prompt = cls._repair_prompt(
                structured_request,
                invalid_response[:excerpt_length],
                diagnostics,
            )
            if len(prompt) > _MAX_REPAIR_PROMPT_CHARS:
                return None
            request = replace(template, prompt=prompt)
            encoded_wire = json.dumps(
                request.to_wire(), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded_wire) > _MAX_REPAIR_REQUEST_WIRE_BYTES:
                return None
            return request

        complete = candidate(len(invalid_response))
        if complete is not None:
            return complete
        empty = candidate(0)
        if empty is None:
            return None
        low = 0
        high = len(invalid_response)
        best = empty
        while low + 1 < high:
            middle = (low + high) // 2
            bounded = candidate(middle)
            if bounded is None:
                high = middle
            else:
                low = middle
                best = bounded
        return best

    @staticmethod
    def _write_archon_output(
        context: NodeExecutionContext,
        data: bytes,
        metadata: dict[str, object],
        *,
        is_structured: bool,
        structured_value: object | None,
        schema_fingerprint: str | None,
        canonicalization_version: int,
    ) -> NodeExecutionResult:
        node = context.node
        extension = ".json" if is_structured else ".md"
        media_type = "application/json" if is_structured else "text/plain"
        try:
            output_path = write_archon_output_exclusive(
                context.run_directory,
                node_id=node.id,
                attempt_id=context.attempt_id,
                filename=f"output{extension}",
                data=data,
            )
        except ArchonOutputIntegrityError:
            metadata["archon_terminal_failure"] = True
            metadata.pop("output", None)
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_integrity",
                error_message="Archon output could not be sealed",
                metadata=metadata,
            )
        artifact = _artifact_for_bytes(
            output_path,
            context.run_directory,
            data,
            media_type=media_type,
        )
        relative_path = output_path.relative_to(context.run_directory).as_posix()
        candidate = PrimaryOutputCandidate(
            attempt_relative_path=relative_path,
            media_type=media_type,
            size_bytes=len(data),
            sha256=artifact.sha256,
            structured_value=structured_value,
            schema_fingerprint=schema_fingerprint,
            canonicalization_version=canonicalization_version,
            output_type=(
                str(node.options["output_type"])
                if node.options.get("output_type") is not None
                else None
            ),
        )
        metadata["output"] = data.decode("utf-8")
        return NodeExecutionResult(
            "succeeded",
            (artifact,),
            metadata=metadata,
            primary_output=candidate,
        )

    @staticmethod
    def _bounded_diagnostics(value: str, limit: int = 16_384) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return value
        encoded = encoded[: max(0, limit - 3)]
        while encoded:
            try:
                return encoded.decode("utf-8") + "..."
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        return "..."[:limit]

    def _repair_or_fail(
        self,
        context: NodeExecutionContext,
        agent_runner,
        initial_request: PluginAgentRunRequest,
        initial_result,
        metadata: dict[str, object],
        structured_request: StructuredOutputRequest,
        *,
        diagnostics: str,
        first_provider_attempts: int,
        first_model_calls: int,
        granted_provider_attempts: int,
        wall_deadline: float,
    ) -> NodeExecutionResult:
        response = initial_result.final_response
        response_bytes = response.encode("utf-8")
        diagnostics = self._bounded_diagnostics(diagnostics)
        metadata.update({
            "archon_terminal_failure": True,
            "invalid_output_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "invalid_output_size_bytes": len(response_bytes),
            "validation_diagnostics": diagnostics,
        })
        metadata.pop("output", None)

        def failed(disposition: str) -> NodeExecutionResult:
            metadata["repair_disposition"] = disposition
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_invalid",
                error_message=diagnostics,
                metadata=metadata,
            )

        def conservatively_account(repair_result=None) -> None:
            if repair_result is not None:
                metadata["usage"] = _aggregate_usage(
                    initial_result.usage, repair_result.usage
                )
            total_model_calls = first_model_calls + 1
            aggregate_audit = dict(initial_result.audit)
            aggregate_audit.update({
                "provider_attempts": granted_provider_attempts,
                "model_calls": total_model_calls,
                "api_calls": total_model_calls,
                "effective_provider": decision.effective_provider,
                "model": decision.model,
                "api_mode": decision.api_mode,
                "repair_accounting": "conservative",
                "provider_attempts_exact": False,
                "model_calls_exact": False,
                "api_calls_exact": False,
            })
            metadata["audit"] = aggregate_audit
            metadata["provider_attempts"] = max(0, granted_provider_attempts - 1)

        if (
            structured_request.strategy
            is not StructuredOutputStrategy.PROMPT_JSON_SCHEMA
        ):
            return failed("ineligible_native_strategy")
        if context.outward_action:
            return failed("ineligible_outward_action")
        if self._uncertain_effects(initial_result.audit):
            return failed("ineligible_uncertain_effects")
        if context.is_cancelled is not None and context.is_cancelled():
            return failed("ineligible_cancelled")
        remaining_provider_attempts = (
            granted_provider_attempts - first_provider_attempts
        )
        if remaining_provider_attempts <= 0:
            return failed("ineligible_provider_attempts")
        if initial_request.max_iterations - first_model_calls <= 0:
            return failed("ineligible_model_iterations")
        remaining_wall = max(0.0, wall_deadline - context.monotonic())
        if remaining_wall <= 0:
            return failed("ineligible_wall_time")
        if len(response_bytes) > _MAX_REPAIR_RESPONSE_BYTES:
            return failed("ineligible_response_too_large")

        decision = context.structured_output_decision
        assert decision is not None
        repair_template = PluginAgentRunRequest(
            prompt="",
            provider=initial_request.provider,
            model=decision.model,
            context_mode="fresh",
            session_id=None,
            enabled_toolsets=(),
            allowed_tools=(),
            denied_tools=("delegate_task", "workflow_agent"),
            skills=(),
            hooks=(),
            mcp_servers=None,
            inline_agents={},
            reasoning_config=None,
            fallback_model=None,
            ephemeral_system_prompt=None,
            request_overrides={},
            structured_output=structured_request,
            max_budget_usd=None,
            sandbox_policy=None,
            approved_action_digest=None,
            workdir=context.run_directory,
            max_iterations=1,
            max_api_attempts=remaining_provider_attempts,
            idle_timeout_seconds=min(
                initial_request.idle_timeout_seconds, remaining_wall
            ),
            wall_timeout_seconds=remaining_wall,
            provider_request_timeout_seconds=min(
                initial_request.provider_request_timeout_seconds,
                remaining_wall,
            ),
            max_process_tree_rss_bytes=initial_request.max_process_tree_rss_bytes,
            max_process_tree_cpu_seconds=(initial_request.max_process_tree_cpu_seconds),
            max_descendants=initial_request.max_descendants,
            cooperative_shutdown_seconds=(initial_request.cooperative_shutdown_seconds),
            term_grace_seconds=initial_request.term_grace_seconds,
            kill_reap_grace_seconds=initial_request.kill_reap_grace_seconds,
        )
        repair_request = self._bounded_repair_request(
            repair_template,
            structured_request,
            response,
            diagnostics,
        )
        if repair_request is None:
            return failed("ineligible_repair_prompt_too_large")
        try:
            repair_result = agent_runner.run(
                repair_request,
                is_cancelled=context.is_cancelled,
            )
        except (OSError, RuntimeError, ValueError):
            conservatively_account()
            return failed("repair_failed")

        metadata["usage"] = _aggregate_usage(initial_result.usage, repair_result.usage)
        repair_counts: tuple[int, int] | None = None
        if repair_result.structured_output is not None:
            try:
                assert context.structured_output_decision is not None
                repair_counts = self._structured_counts(
                    repair_result,
                    structured_request,
                    decision,
                    declaration_source=(decision.declaration_source),
                    provider_limit=remaining_provider_attempts,
                    model_limit=1,
                    require_positive=repair_result.status == "completed",
                )
            except ValueError:
                conservatively_account(repair_result)
                return failed("repair_evidence_invalid")
        if repair_counts is None:
            conservatively_account(repair_result)
            return failed(
                "repair_cancelled"
                if repair_result.status == "cancelled"
                else "repair_failed"
            )

        total_provider_attempts = first_provider_attempts + repair_counts[0]
        total_model_calls = first_model_calls + repair_counts[1]
        aggregate_audit = dict(initial_result.audit)
        aggregate_audit.update({
            "provider_attempts": total_provider_attempts,
            "model_calls": total_model_calls,
            "api_calls": total_model_calls,
            "effective_provider": decision.effective_provider,
            "model": decision.model,
            "api_mode": decision.api_mode,
            "repair_accounting": "exact",
            "provider_attempts_exact": True,
            "model_calls_exact": True,
            "api_calls_exact": True,
        })
        metadata["audit"] = aggregate_audit
        metadata["provider_attempts"] = max(0, total_provider_attempts - 1)
        if repair_result.status != "completed":
            return failed(
                "repair_cancelled"
                if repair_result.status == "cancelled"
                else "repair_failed"
            )
        try:
            repaired = parse_validate_canonicalize(
                repair_result.final_response, structured_request
            )
        except StructuredOutputValidatorUnavailable as exc:
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_unavailable",
                error_message=str(exc),
                metadata=metadata,
            )
        except StructuredOutputError:
            return failed("repair_invalid")

        metadata["repair_disposition"] = "succeeded"
        metadata.pop("archon_terminal_failure", None)
        return self._write_archon_output(
            context,
            repaired.canonical_bytes,
            metadata,
            is_structured=True,
            structured_value=repaired.value,
            schema_fingerprint=structured_request.schema.schema_fingerprint,
            canonicalization_version=repaired.canonicalization_version,
        )

    def _prompt(self, context: NodeExecutionContext) -> str:
        node = context.node
        if node.node_type == "command":
            template = (
                ResourceResolver(
                    context.run_directory,
                    sealed_paths=context.sealed_resource_paths,
                    sealed_bytes=context.sealed_resource_bytes,
                )
                .command(str(node.value))
                .body
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
                ).text(f"node-agent-skills/{context.node.id}/{agent_id}.md")
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
        try:
            structured_request = self._structured_request(context)
        except ValueError as exc:
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_capability_drift",
                error_message=str(exc),
                metadata={"archon_terminal_failure": True},
            )
        if structured_request is not None:
            try:
                require_structured_output_validator()
            except StructuredOutputValidatorUnavailable as exc:
                return NodeExecutionResult(
                    "failed",
                    error_code="structured_output_unavailable",
                    error_message=str(exc),
                    metadata={
                        "provider_attempts": 0,
                        "archon_terminal_failure": True,
                    },
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
            request_started_at = context.monotonic()
            wall_timeout = (
                context.deadline_budget.remaining_wall(request_started_at)
                if context.deadline_budget is not None
                else float(context.timeout_seconds)
            )
            wall_deadline = (
                context.deadline_budget.wall_deadline
                if context.deadline_budget is not None
                else request_started_at + wall_timeout
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
                provider=(
                    self._requested_provider(context)
                    if structured_request is not None
                    else node.options.get("provider")
                    or context.workflow_options.get("provider")
                ),
                model=(
                    context.structured_output_decision.model
                    if structured_request is not None
                    else node.options.get("model")
                    or context.workflow_options.get("model")
                ),
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
                structured_output=structured_request,
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
        structured_counts: tuple[int, int] | None = None
        negotiation_failure = str(result.audit.get("failure_kind", ""))
        if structured_request is not None and negotiation_failure in {
            "structured_output_capability_drift",
            "structured_output_unsupported",
        }:
            try:
                if result.status != "failed":
                    raise ValueError("structured output negotiation status is invalid")
                assert context.structured_output_decision is not None
                negotiation_counts = self._structured_counts(
                    result,
                    structured_request,
                    context.structured_output_decision,
                    declaration_source=(
                        context.structured_output_decision.declaration_source
                    ),
                    provider_limit=granted_provider_attempts,
                    model_limit=request.max_iterations,
                    require_positive=False,
                )
                if negotiation_counts != (0, 0):
                    raise ValueError(
                        "structured output negotiation accounting is invalid"
                    )
            except ValueError as exc:
                metadata["negotiation_evidence"] = "invalid"
                error_message = str(exc)
                metadata["provider_attempts"] = conservative_provider_retry_count(
                    None,
                    granted_attempts=granted_provider_attempts,
                )
            else:
                metadata["negotiation_evidence"] = "corroborated"
                error_message = "structured output negotiation failed"
                metadata["provider_attempts"] = 0
            metadata["archon_terminal_failure"] = True
            return NodeExecutionResult(
                "failed",
                error_code=negotiation_failure,
                error_message=error_message,
                metadata=metadata,
            )
        if structured_request is not None and result.structured_output is not None:
            try:
                structured_counts = self._structured_counts(
                    result,
                    structured_request,
                    context.structured_output_decision,
                    declaration_source=(
                        context.structured_output_decision.declaration_source
                    ),
                    provider_limit=granted_provider_attempts,
                    model_limit=request.max_iterations,
                    require_positive=result.status == "completed",
                )
            except ValueError as exc:
                metadata["archon_terminal_failure"] = True
                return NodeExecutionResult(
                    "failed",
                    error_code="structured_output_capability_drift",
                    error_message=str(exc),
                    metadata=metadata,
                )
            metadata["provider_attempts"] = max(0, structured_counts[0] - 1)
        elif structured_request is not None and result.status == "completed":
            metadata["archon_terminal_failure"] = True
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_capability_drift",
                error_message="structured output evidence is missing",
                metadata=metadata,
            )
        else:
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
            if "provider_attempts" not in metadata:
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
        if context.language_profile is WorkflowLanguageProfile.ARCHON_2026_07:
            if structured_request is None:
                archon_result = self._write_archon_output(
                    context,
                    output.encode("utf-8"),
                    metadata,
                    is_structured=False,
                    structured_value=None,
                    schema_fingerprint=None,
                    canonicalization_version=1,
                )
            else:
                assert structured_counts is not None
                try:
                    structured_value = parse_validate_canonicalize(
                        output, structured_request
                    )
                except StructuredOutputValidatorUnavailable as exc:
                    return NodeExecutionResult(
                        "failed",
                        error_code="structured_output_unavailable",
                        error_message=str(exc),
                        metadata=metadata,
                    )
                except StructuredOutputError as exc:
                    archon_result = self._repair_or_fail(
                        context,
                        agent_runner,
                        request,
                        result,
                        metadata,
                        structured_request,
                        diagnostics=str(exc),
                        first_provider_attempts=structured_counts[0],
                        first_model_calls=structured_counts[1],
                        granted_provider_attempts=granted_provider_attempts,
                        wall_deadline=wall_deadline,
                    )
                else:
                    archon_result = self._write_archon_output(
                        context,
                        structured_value.canonical_bytes,
                        metadata,
                        is_structured=True,
                        structured_value=structured_value.value,
                        schema_fingerprint=(
                            structured_request.schema.schema_fingerprint
                        ),
                        canonicalization_version=(
                            structured_value.canonicalization_version
                        ),
                    )
                if archon_result.status != "succeeded":
                    return archon_result
            if registry_key is not None and self.session_registry is not None:
                updated = self.session_registry.compare_and_set(
                    registry_key,
                    expected_generation,
                    result.session_id,
                    fingerprint,
                )
                if not updated:
                    warnings.append("newer persistent session retained")
            return archon_result

        schema = node.options.get("output_format")
        extension = ".txt"
        if schema is not None:
            try:
                value = json.loads(output)
            except json.JSONDecodeError as exc:
                return self._failure("structured_output_invalid", str(exc))
            try:
                jsonschema = require_structured_output_validator(legacy=True)
            except StructuredOutputValidatorUnavailable as exc:
                return self._failure(
                    "structured_output_unavailable",
                    str(exc),
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
