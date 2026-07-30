"""Server-owned workflow runner and execution capability bindings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Literal, Mapping

from hermes_cli.config import get_compatible_custom_providers, read_raw_config
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    StructuredOutputCapabilityDecision,
    classify_execution_runtime,
    resolve_structured_output_capability,
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

    def _runtime_capabilities_for_node(
        self,
        package: "WorkflowPackage",
        node_id: str,
    ) -> ExecutionRuntimeCapabilities:
        """Classify the provider route the executor will use for one node."""
        node = next(node for node in package.definition.nodes if node.id == node_id)
        workflow_provider = package.definition.options.get("provider")
        node_provider = node.options.get("provider")
        configured_provider = node_provider or workflow_provider
        configured_model = node.options.get("model") or package.definition.options.get(
            "model"
        )
        if not isinstance(configured_provider, str) or not configured_provider.strip():
            if not isinstance(configured_model, str) or not configured_model.strip():
                return self.runtime_capabilities
            return ExecutionRuntimeCapabilities(
                api_mode=self.runtime_capabilities.api_mode,
                hermes_managed_tool_loop=(
                    self.runtime_capabilities.hermes_managed_tool_loop
                ),
                effective_provider=self.runtime_capabilities.effective_provider,
                model=configured_model.strip(),
                base_url_trust_class=(
                    self.runtime_capabilities.base_url_trust_class
                ),
                declared_structured_output_strategy=(
                    self.runtime_capabilities.declared_structured_output_strategy
                ),
                structured_output_declaration_source=(
                    self.runtime_capabilities.structured_output_declaration_source
                ),
            )

        provider = configured_provider.strip()
        model = configured_model.strip() if isinstance(configured_model, str) else ""
        return classify_execution_runtime(
            provider=provider,
            model_config={"provider": provider, "default": model},
            target_model=model or None,
        )

    def structured_output_decisions(
        self,
        package: "WorkflowPackage",
    ) -> Mapping[str, StructuredOutputCapabilityDecision]:
        """Return immutable per-node decisions for sealed Archon schemas."""
        decisions: dict[str, StructuredOutputCapabilityDecision] = {}
        for node_id, output in sorted(package.language.structured_outputs.items()):
            runtime_capabilities = self._runtime_capabilities_for_node(
                package, node_id
            )
            decisions[node_id] = resolve_structured_output_capability(
                runtime_capabilities,
                schema_fingerprint=output.schema_fingerprint,
            )
        return MappingProxyType(decisions)

    def structured_output_identity_material(
        self,
        package: "WorkflowPackage",
    ) -> tuple[dict[str, object], ...]:
        """Return canonical schema-free material for every sealed decision."""
        return tuple(
            {
                "node_id": node_id,
                "strategy": decision.strategy.value,
                "effective_provider": decision.effective_provider,
                "model": decision.model,
                "api_mode": decision.api_mode,
                "declaration_source": decision.declaration_source,
                "adapter_version": decision.adapter_version,
                "schema_fingerprint": decision.schema_fingerprint,
                "rationale": decision.rationale,
            }
            for node_id, decision in self.structured_output_decisions(package).items()
        )

    def structured_output_run_metadata(
        self,
        package: "WorkflowPackage",
    ) -> dict[str, str]:
        """Serialize complete decisions as bounded immutable run metadata rows."""
        metadata: dict[str, str] = {}
        for node_id, decision in self.structured_output_decisions(package).items():
            key = (
                "structured_output_decision."
                + hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
            )
            metadata[key] = json.dumps(
                {
                    "strategy": decision.strategy.value,
                    "effective_provider": decision.effective_provider,
                    "model": decision.model,
                    "api_mode": decision.api_mode,
                    "declaration_source": decision.declaration_source,
                    "adapter_version": decision.adapter_version,
                    "schema_fingerprint": decision.schema_fingerprint,
                    "rationale": decision.rationale,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return metadata

    @property
    def _structured_output_runtime_identity(self) -> dict[str, object]:
        decision = resolve_structured_output_capability(
            self.runtime_capabilities,
            schema_fingerprint="0" * 64,
        )
        return {
            "adapter_version": decision.adapter_version,
            "api_mode": decision.api_mode,
            "declaration_source": decision.declaration_source,
            "effective_provider": decision.effective_provider,
            "model": decision.model,
            "rationale": decision.rationale,
            "schema_fingerprint": decision.schema_fingerprint,
            "strategy": decision.strategy.value,
        }

    def _identity_digest(
        self,
        structured_output_decisions: object,
    ) -> str:
        """Hash shared execution authority plus structured-output decisions."""
        material = json.dumps(
            {
                "entitlement": self.entitlement.value,
                "entitlement_error": self.entitlement.error_code,
                "mcp_available": self.mcp_available,
                "runner_starts_request_mcp": (
                    self.runner_capabilities.starts_request_mcp
                ),
                "runtime_api_mode": self.runtime_capabilities.api_mode,
                "runtime_hermes_managed_tool_loop": (
                    self.runtime_capabilities.hermes_managed_tool_loop
                ),
                "runtime_provider": self.runtime_capabilities.effective_provider,
                "runtime_model": self.runtime_capabilities.model,
                "runtime_base_url_trust_class": (
                    self.runtime_capabilities.base_url_trust_class
                ),
                "runtime_declared_structured_output_strategy": (
                    self.runtime_capabilities.declared_structured_output_strategy
                ),
                "structured_output_decisions": structured_output_decisions,
                "surface": self.surface,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @property
    def identity_digest(self) -> str:
        """Return the package-independent runtime identity for early checks."""
        return self._identity_digest((self._structured_output_runtime_identity,))

    def identity_digest_for(self, package: "WorkflowPackage") -> str:
        """Return the identity sealing complete actual per-node decisions."""
        return self._identity_digest(self.structured_output_identity_material(package))


@dataclass(frozen=True, slots=True)
class WorkflowRunnerBinding:
    real_runner: object
    deterministic_runner: object
    real_capabilities: RunnerCapabilities
    deterministic_capabilities: RunnerCapabilities
    runtime_capabilities: ExecutionRuntimeCapabilities
    runtime_capabilities_provider: (
        Callable[[], ExecutionRuntimeCapabilities] | None
    ) = None

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

    def _runtime_capabilities_for_context(self) -> ExecutionRuntimeCapabilities:
        if self.runtime_capabilities_provider is not None:
            return self.runtime_capabilities_provider()
        return self.runtime_capabilities

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
            runtime_capabilities=self._runtime_capabilities_for_context(),
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


def _production_runtime_capabilities() -> ExecutionRuntimeCapabilities:
    config = read_raw_config()
    provider, model_config, provider_config = _configured_provider_metadata(config)
    return classify_execution_runtime(
        provider=provider,
        model_config=model_config,
        provider_config=provider_config,
    )


def production_workflow_runner_binding() -> WorkflowRunnerBinding:
    """Construct the immutable server-owned production runner declaration."""
    from agent.plugin_agent import PluginAgentRunner

    real_runner = PluginAgentRunner(plugin_id="workflow")
    deterministic_runner = entitled_agent_runner(
        AIEntitlementResolution("deterministic"),
        real_runner,
    )
    runtime_capabilities = _production_runtime_capabilities()
    return WorkflowRunnerBinding(
        real_runner=real_runner,
        deterministic_runner=deterministic_runner,
        real_capabilities=RunnerCapabilities(
            starts_request_mcp=bool(
                getattr(type(real_runner), "starts_request_mcp", False)
            )
        ),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=runtime_capabilities,
        runtime_capabilities_provider=_production_runtime_capabilities,
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
        structured_output_decisions=context.structured_output_decisions(package),
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
                    effective_profile=package.language.effective_profile,
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
