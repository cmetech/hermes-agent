"""Server-owned workflow runner and execution capability bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping

from hermes_cli.config import get_compatible_custom_providers, read_raw_config
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    classify_execution_runtime,
)
from plugins.workflow.entitlement import (
    AIEntitlementResolution,
    entitled_agent_runner,
)

if TYPE_CHECKING:
    from plugins.workflow.compat import CompatibilityReport
    from plugins.workflow.models import WorkflowPackage
    from plugins.workflow.trust import (
        WorkflowResourceReadBudget,
        WorkflowRiskSummary,
    )


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    starts_request_mcp: bool


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityContext:
    surface: str
    entitlement: AIEntitlementResolution
    runner_capabilities: RunnerCapabilities
    runtime_capabilities: ExecutionRuntimeCapabilities
    mcp_available: bool


@dataclass(frozen=True, slots=True)
class WorkflowRunnerBinding:
    real_runner: object
    deterministic_runner: object
    real_capabilities: RunnerCapabilities
    deterministic_capabilities: RunnerCapabilities
    runtime_capabilities: ExecutionRuntimeCapabilities

    def runner_for(self, entitlement: AIEntitlementResolution) -> object:
        return (
            self.real_runner
            if entitlement.value == "real"
            else self.deterministic_runner
        )

    def capabilities_for(
        self, entitlement: AIEntitlementResolution
    ) -> RunnerCapabilities:
        return (
            self.real_capabilities
            if entitlement.value == "real"
            else self.deterministic_capabilities
        )

    def execution_context(
        self,
        *,
        surface: Literal["background", "cli"],
        entitlement: AIEntitlementResolution,
    ) -> ExecutionCapabilityContext:
        return execution_capability_context(
            surface=surface,
            entitlement=entitlement,
            runner_capabilities=self.capabilities_for(entitlement),
            runtime_capabilities=self.runtime_capabilities,
        )


def execution_capability_context(
    *,
    surface: Literal["background", "cli"],
    entitlement: AIEntitlementResolution,
    runner_capabilities: RunnerCapabilities,
    runtime_capabilities: ExecutionRuntimeCapabilities,
) -> ExecutionCapabilityContext:
    return ExecutionCapabilityContext(
        surface=surface,
        entitlement=entitlement,
        runner_capabilities=runner_capabilities,
        runtime_capabilities=runtime_capabilities,
        mcp_available=(
            surface == "background"
            and entitlement.value == "real"
            and entitlement.error_code is None
            and runner_capabilities.starts_request_mcp
            and runtime_capabilities.hermes_managed_tool_loop
        ),
    )


def _configured_provider_metadata(
    config: Mapping[str, object],
) -> tuple[object, object, object]:
    model_config = config.get("model", {})
    if not isinstance(model_config, Mapping):
        return "", model_config, {}
    provider = model_config.get("provider", "")
    provider_config: Mapping[str, object] = {}
    if isinstance(provider, str) and provider.strip():
        normalized = provider.strip().casefold()
        for candidate in get_compatible_custom_providers(dict(config)):
            name = candidate.get("name") if isinstance(candidate, Mapping) else None
            if isinstance(name, str) and name.strip().casefold() == normalized:
                provider_config = candidate
                break
    return provider, model_config, provider_config


def production_workflow_runner_binding() -> WorkflowRunnerBinding:
    """Construct the immutable server-owned production runner declaration."""
    from agent.plugin_agent import PluginAgentRunner

    config = read_raw_config()
    provider, model_config, provider_config = _configured_provider_metadata(config)
    real_runner = PluginAgentRunner(plugin_id="workflow")
    deterministic_runner = entitled_agent_runner(
        AIEntitlementResolution("deterministic"),
        real_runner,
    )
    return WorkflowRunnerBinding(
        real_runner=real_runner,
        deterministic_runner=deterministic_runner,
        real_capabilities=RunnerCapabilities(
            starts_request_mcp=bool(
                getattr(type(real_runner), "starts_request_mcp", False)
            )
        ),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=classify_execution_runtime(
            provider=provider,
            model_config=model_config,
            provider_config=provider_config,
        ),
    )


def background_execution_context(
    binding: WorkflowRunnerBinding,
    *,
    requires_ai: bool | None,
) -> ExecutionCapabilityContext:
    """Derive the server-owned background context for one package projection."""
    entitlement = AIEntitlementResolution(
        "real" if requires_ai is None or requires_ai else "deterministic"
    )
    return binding.execution_context(
        surface="background",
        entitlement=entitlement,
    )


def assess_package_execution(
    package: "WorkflowPackage",
    context: ExecutionCapabilityContext,
    *,
    read_budget: "WorkflowResourceReadBudget | None" = None,
) -> tuple["CompatibilityReport", "WorkflowRiskSummary"]:
    """Recompute compatibility then risk under one immutable context."""
    from plugins.workflow.compat import (
        CompatibilityFinding,
        CompatibilityLevel,
        CompatibilityReport,
        assess_compatibility,
    )
    from plugins.workflow.trust import build_risk_summary

    compatibility = assess_compatibility(
        package,
        mcp_available=context.mcp_available,
    )
    if package.sidecar.get("execution_environment", "trusted_local") != (
        "trusted_local"
    ):
        compatibility = CompatibilityReport(
            level=CompatibilityLevel.UNSUPPORTED,
            findings=(
                *compatibility.findings,
                CompatibilityFinding(
                    path="sidecar.execution_environment",
                    level=CompatibilityLevel.UNSUPPORTED,
                    message="workflow requires a configured isolated backend",
                    blocking=True,
                    code="execution_environment_unavailable",
                ),
            ),
            runnable=False,
        )
    risk = build_risk_summary(
        package,
        compatibility,
        read_budget=read_budget,
    )
    return compatibility, risk


__all__ = [
    "ExecutionCapabilityContext",
    "RunnerCapabilities",
    "WorkflowRunnerBinding",
    "assess_package_execution",
    "background_execution_context",
    "execution_capability_context",
    "production_workflow_runner_binding",
]
