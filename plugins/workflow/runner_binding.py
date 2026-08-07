"""Server-owned workflow runner and execution capability bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import TYPE_CHECKING, AbstractSet, Callable, Literal, Mapping

from hermes_cli.config import read_raw_config
from hermes_cli import managed_scope
from hermes_cli.runtime_provider import (
    ConfiguredExecutionRoute,
    ExecutionRuntimeCapabilities,
    StructuredOutputCapabilityDecision,
    classify_configured_execution_route,
    classify_execution_runtime,
    resolve_structured_output_capability,
    snapshot_configured_execution_routes,
)
from hermes_cli.workflow_model_resolution import (
    WorkflowModelConfigSnapshot,
    parse_workflow_model_config,
)
from plugins.workflow.entitlement import (
    AIEntitlementResolution,
    entitled_agent_runner,
)

if TYPE_CHECKING:
    from plugins.workflow.compilation import WorkflowCompilation
    from plugins.workflow.compat import CompatibilityReport
    from plugins.workflow.models import WorkflowPackage
    from plugins.workflow.provider_authority import (
        ProviderAuthorityEnvironment,
        WorkflowProviderAuthority,
        WorkflowProviderAuthorityError,
    )
    from plugins.workflow.trust import (
        WorkflowResourceReadBudget,
        WorkflowRiskSummary,
    )


_RUN_METADATA_VALUE_MAX_CHARS = 512
_STRUCTURED_OUTPUT_METADATA_FIELD_LIMITS = MappingProxyType(
    {
        "strategy": 32,
        "effective_provider": 64,
        "model": 192,
        "api_mode": 64,
        "declaration_source": 64,
        "schema_fingerprint": 64,
        "rationale": 256,
    }
)


class StructuredOutputMetadataCapacityError(ValueError):
    """A sealed decision cannot fit the persistent run metadata contract."""

    def __init__(self, node_id: str, detail: str) -> None:
        super().__init__(detail)
        self.node_id = node_id


def _structured_output_metadata_row(
    node_id: str,
    decision: StructuredOutputCapabilityDecision,
) -> str:
    row: dict[str, object] = {
        "strategy": decision.strategy.value,
        "effective_provider": decision.effective_provider,
        "model": decision.model,
        "api_mode": decision.api_mode,
        "declaration_source": decision.declaration_source,
        "adapter_version": decision.adapter_version,
        "schema_fingerprint": decision.schema_fingerprint,
        "rationale": decision.rationale,
    }
    for field_name, maximum in _STRUCTURED_OUTPUT_METADATA_FIELD_LIMITS.items():
        value = row[field_name]
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > maximum:
            raise StructuredOutputMetadataCapacityError(
                node_id,
                f"{field_name} exceeds its run metadata limit",
            )
    serialized = json.dumps(row, sort_keys=True, separators=(",", ":"))
    if len(serialized) > _RUN_METADATA_VALUE_MAX_CHARS:
        raise StructuredOutputMetadataCapacityError(
            node_id,
            "structured output decision exceeds the run metadata row limit",
        )
    return serialized


@dataclass(frozen=True, slots=True)
class RunnerCapabilities:
    starts_request_mcp: bool


@dataclass(frozen=True, slots=True)
class ExecutionProviderSnapshot:
    runtime_capabilities: ExecutionRuntimeCapabilities
    configured_provider_routes: Mapping[str, ConfiguredExecutionRoute]
    model_config_snapshot: WorkflowModelConfigSnapshot | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configured_provider_routes",
            MappingProxyType(dict(self.configured_provider_routes)),
        )


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityContext:
    surface: str
    entitlement: AIEntitlementResolution
    runner_capabilities: RunnerCapabilities
    runtime_capabilities: ExecutionRuntimeCapabilities
    mcp_available: bool
    configured_provider_routes: Mapping[str, ConfiguredExecutionRoute] = field(
        default_factory=lambda: MappingProxyType({})
    )
    model_config_snapshot: WorkflowModelConfigSnapshot | None = None
    provider_authority_environment: "ProviderAuthorityEnvironment | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configured_provider_routes",
            MappingProxyType(dict(self.configured_provider_routes)),
        )

    def _runtime_capabilities_for_node(
        self,
        package: "WorkflowPackage",
        node_id: str,
    ) -> tuple[ExecutionRuntimeCapabilities, ConfiguredExecutionRoute | None]:
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
                return self.runtime_capabilities, None
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
            ), None

        provider = configured_provider.strip()
        model = configured_model.strip() if isinstance(configured_model, str) else ""
        configured_route = self.configured_provider_routes.get(provider.casefold())
        if configured_route is not None:
            return (
                classify_configured_execution_route(
                    configured_route,
                    target_model=model or None,
                ),
                configured_route,
            )
        return (
            classify_execution_runtime(
                provider=provider,
                model_config={"provider": provider, "default": model},
                target_model=model or None,
            ),
            None,
        )

    @staticmethod
    def _provider_route_fingerprint(
        runtime_capabilities: ExecutionRuntimeCapabilities,
        configured_route: ConfiguredExecutionRoute | None,
    ) -> str:
        route_material = {
            "api_mode": runtime_capabilities.api_mode,
            "base_url_trust_class": runtime_capabilities.base_url_trust_class,
            "declared_structured_output_strategy": (
                runtime_capabilities.declared_structured_output_strategy
            ),
            "declaration_source": (
                runtime_capabilities.structured_output_declaration_source
            ),
            "effective_provider": runtime_capabilities.effective_provider,
            "configured_route": (
                {
                    "requested_provider": configured_route.requested_provider,
                    "effective_provider": configured_route.effective_provider,
                    "model_config": dict(configured_route.model_config),
                    "provider_config": dict(configured_route.provider_config),
                    "route_evidence_error": (
                        configured_route.route_evidence_error
                    ),
                }
                if configured_route is not None
                else None
            ),
        }
        encoded = json.dumps(
            route_material, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def structured_output_decisions(
        self,
        package: "WorkflowPackage",
    ) -> Mapping[str, StructuredOutputCapabilityDecision]:
        """Return immutable per-node decisions for sealed Archon schemas."""
        decisions: dict[str, StructuredOutputCapabilityDecision] = {}
        for node_id, output in sorted(package.language.structured_outputs.items()):
            runtime_capabilities, _configured_route = (
                self._runtime_capabilities_for_node(package, node_id)
            )
            decisions[node_id] = resolve_structured_output_capability(
                runtime_capabilities,
                schema_fingerprint=output.schema_fingerprint,
            )
        return MappingProxyType(decisions)

    def provider_authority(
        self,
        package: "WorkflowPackage",
        *,
        mcp_execution_preconditions: Mapping[str, bool] | None = None,
    ) -> "WorkflowProviderAuthority | None":
        """Resolve the single v5 model/capability authority for a package."""
        from plugins.workflow.language import supports_phase5_semantics
        from plugins.workflow.provider_authority import (
            ProviderAuthorityEnvironment,
            resolve_workflow_provider_authority,
        )

        if not supports_phase5_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        ):
            return None
        if self.model_config_snapshot is None:
            return None
        environment = self.provider_authority_environment
        if environment is None:
            environment = ProviderAuthorityEnvironment(
                session_store_available=True,
                mcp_available=self.mcp_available,
                hook_lifecycle_available=True,
                inline_agent_available=(self.entitlement.value == "real"),
                web_service_available=True,
                authoritative_cost_available=False,
            )
        return resolve_workflow_provider_authority(
            package,
            model_config=self.model_config_snapshot,
            default_runtime=self.runtime_capabilities,
            environment=environment,
            mcp_execution_preconditions=mcp_execution_preconditions,
        )

    def structured_output_identity_material(
        self,
        package: "WorkflowPackage",
    ) -> tuple[dict[str, object], ...]:
        """Return canonical schema-free material for every sealed decision."""
        material: list[dict[str, object]] = []
        for node_id, decision in self.structured_output_decisions(package).items():
            runtime_capabilities, configured_route = (
                self._runtime_capabilities_for_node(package, node_id)
            )
            material.append({
                "node_id": node_id,
                "strategy": decision.strategy.value,
                "effective_provider": decision.effective_provider,
                "model": decision.model,
                "api_mode": decision.api_mode,
                "declaration_source": decision.declaration_source,
                "adapter_version": decision.adapter_version,
                "schema_fingerprint": decision.schema_fingerprint,
                "rationale": decision.rationale,
                "provider_route_fingerprint": self._provider_route_fingerprint(
                    runtime_capabilities,
                    configured_route,
                ),
            })
        return tuple(material)

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
            metadata[key] = _structured_output_metadata_row(node_id, decision)
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
        *,
        provider_authority_digest: str | None = None,
    ) -> str:
        """Hash shared execution authority plus structured-output decisions."""
        identity_material = {
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
        }
        if provider_authority_digest is not None:
            identity_material["provider_authority_digest"] = (
                provider_authority_digest
            )
        material = json.dumps(
            identity_material,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @property
    def identity_digest(self) -> str:
        """Return the package-independent runtime identity for early checks."""
        return self._identity_digest((self._structured_output_runtime_identity,))

    def identity_digest_for(
        self,
        package: "WorkflowPackage",
        *,
        provider_authority: "WorkflowProviderAuthority | None" = None,
    ) -> str:
        """Return the identity sealing complete actual per-node decisions."""
        authority = provider_authority or self.provider_authority(package)
        return self._identity_digest(
            self.structured_output_identity_material(package),
            provider_authority_digest=(
                authority.authority_digest if authority is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class WorkflowRunnerBinding:
    real_runner: object
    deterministic_runner: object
    real_capabilities: RunnerCapabilities
    deterministic_capabilities: RunnerCapabilities
    runtime_capabilities: ExecutionRuntimeCapabilities
    configured_provider_routes: Mapping[str, ConfiguredExecutionRoute] = field(
        default_factory=lambda: MappingProxyType({})
    )
    model_config_snapshot: WorkflowModelConfigSnapshot | None = None
    runtime_capabilities_provider: (
        Callable[[], ExecutionRuntimeCapabilities | ExecutionProviderSnapshot] | None
    ) = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configured_provider_routes",
            MappingProxyType(dict(self.configured_provider_routes)),
        )

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

    def _provider_snapshot_for_context(self) -> ExecutionProviderSnapshot:
        if self.runtime_capabilities_provider is not None:
            refreshed = self.runtime_capabilities_provider()
            if isinstance(refreshed, ExecutionProviderSnapshot):
                return refreshed
            return ExecutionProviderSnapshot(
                refreshed,
                self.configured_provider_routes,
                self.model_config_snapshot,
            )
        return ExecutionProviderSnapshot(
            self.runtime_capabilities,
            self.configured_provider_routes,
            self.model_config_snapshot,
        )

    def execution_context(
        self,
        *,
        surface: Literal["background", "cli"],
        entitlement: AIEntitlementResolution,
    ) -> ExecutionCapabilityContext:
        provider_snapshot = self._provider_snapshot_for_context()
        return execution_capability_context(
            surface=surface,
            entitlement=entitlement,
            runner_capabilities=self.capabilities_for(entitlement),
            runtime_capabilities=provider_snapshot.runtime_capabilities,
            configured_provider_routes=provider_snapshot.configured_provider_routes,
            model_config_snapshot=provider_snapshot.model_config_snapshot,
        )


def execution_capability_context(
    *,
    surface: Literal["background", "cli"],
    entitlement: AIEntitlementResolution,
    runner_capabilities: RunnerCapabilities,
    runtime_capabilities: ExecutionRuntimeCapabilities,
    configured_provider_routes: Mapping[str, ConfiguredExecutionRoute] | None = None,
    model_config_snapshot: WorkflowModelConfigSnapshot | None = None,
    provider_authority_environment: "ProviderAuthorityEnvironment | None" = None,
) -> ExecutionCapabilityContext:
    return ExecutionCapabilityContext(
        surface=surface,
        entitlement=entitlement,
        runner_capabilities=runner_capabilities,
        runtime_capabilities=runtime_capabilities,
        configured_provider_routes=configured_provider_routes or MappingProxyType({}),
        model_config_snapshot=model_config_snapshot,
        provider_authority_environment=provider_authority_environment,
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
    return provider, model_config, {}


def _production_provider_snapshot() -> ExecutionProviderSnapshot:
    config = read_raw_config()
    model_config_snapshot = parse_workflow_model_config(
        config,
        managed_config=managed_scope.load_managed_config(),
    )
    configured_provider_routes = snapshot_configured_execution_routes(config)
    provider, model_config, provider_config = _configured_provider_metadata(config)
    configured_route = (
        configured_provider_routes.get(provider.strip().casefold())
        if isinstance(provider, str)
        else None
    )
    target_model = (
        str(model_config.get("default") or model_config.get("model") or "").strip()
        if isinstance(model_config, Mapping)
        else ""
    )
    runtime_capabilities = (
        classify_configured_execution_route(
            configured_route,
            target_model=target_model or None,
        )
        if configured_route is not None
        else classify_execution_runtime(
            provider=provider,
            model_config=model_config,
            provider_config=provider_config,
        )
    )
    return ExecutionProviderSnapshot(
        runtime_capabilities=runtime_capabilities,
        configured_provider_routes=configured_provider_routes,
        model_config_snapshot=model_config_snapshot,
    )


def _production_runtime_capabilities() -> ExecutionRuntimeCapabilities:
    return _production_provider_snapshot().runtime_capabilities


def production_workflow_runner_binding() -> WorkflowRunnerBinding:
    """Construct the immutable server-owned production runner declaration."""
    from agent.plugin_agent import PluginAgentRunner

    real_runner = PluginAgentRunner(plugin_id="workflow")
    deterministic_runner = entitled_agent_runner(
        AIEntitlementResolution("deterministic"),
        real_runner,
    )
    provider_snapshot = _production_provider_snapshot()
    return WorkflowRunnerBinding(
        real_runner=real_runner,
        deterministic_runner=deterministic_runner,
        real_capabilities=RunnerCapabilities(
            starts_request_mcp=bool(
                getattr(type(real_runner), "starts_request_mcp", False)
            )
        ),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=provider_snapshot.runtime_capabilities,
        configured_provider_routes=provider_snapshot.configured_provider_routes,
        model_config_snapshot=provider_snapshot.model_config_snapshot,
        runtime_capabilities_provider=_production_provider_snapshot,
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
    compilation: "WorkflowCompilation | None" = None,
    provider_authority: "WorkflowProviderAuthority | None" = None,
    provider_authority_error: "WorkflowProviderAuthorityError | None" = None,
    available_tools: "AbstractSet[str] | None" = None,
    available_services: "AbstractSet[str] | None" = None,
    isolated_workdir: bool = False,
) -> tuple["CompatibilityReport", "WorkflowRiskSummary"]:
    """Recompute compatibility then risk under one immutable context."""
    from plugins.workflow.compat import (
        CompatibilityFinding,
        CompatibilityLevel,
        CompatibilityReport,
        assess_compatibility,
    )
    from plugins.workflow.trust import build_risk_summary

    if provider_authority_error is None:
        provider_authority = provider_authority or context.provider_authority(package)
    compatibility = assess_compatibility(
        package,
        available_tools=available_tools,
        available_services=available_services,
        isolated_workdir=isolated_workdir,
        mcp_available=context.mcp_available,
        structured_output_decisions=context.structured_output_decisions(package),
        provider_authority=provider_authority,
    )
    if provider_authority_error is not None:
        compatibility = CompatibilityReport(
            level=CompatibilityLevel.UNSUPPORTED,
            findings=(
                *(
                    finding
                    for finding in compatibility.findings
                    if finding.code != "provider_authority_missing"
                ),
                CompatibilityFinding(
                    path=provider_authority_error.path,
                    level=CompatibilityLevel.UNSUPPORTED,
                    message=str(provider_authority_error),
                    blocking=True,
                    code=provider_authority_error.code,
                    effective_profile=package.language.effective_profile,
                ),
            ),
            runnable=False,
        )
    try:
        context.structured_output_run_metadata(package)
    except StructuredOutputMetadataCapacityError as exc:
        compatibility = CompatibilityReport(
            level=CompatibilityLevel.UNSUPPORTED,
            findings=(
                *compatibility.findings,
                CompatibilityFinding(
                    path=f"nodes[{exc.node_id!r}].output_format",
                    level=CompatibilityLevel.UNSUPPORTED,
                    message=(
                        "structured output decision exceeds persistent run "
                        "metadata limits"
                    ),
                    blocking=True,
                    code="structured_output_metadata_too_large",
                    effective_profile=package.language.effective_profile,
                ),
            ),
            runnable=False,
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
        compilation=compilation,
        provider_authority_digest=(
            provider_authority.authority_digest
            if provider_authority is not None
            else None
        ),
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
