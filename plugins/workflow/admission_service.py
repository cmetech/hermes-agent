"""One immutable provider-aware assessment shared by workflow surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import AbstractSet, Mapping

from hermes_cli.provider_capabilities import CapabilityDisposition
from plugins.workflow.compilation import WorkflowCompilation
from plugins.workflow.language import supports_phase4_semantics
from plugins.workflow.models import WorkflowPackage
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowProviderAuthorityError,
)
from plugins.workflow.runner_binding import (
    ExecutionCapabilityContext,
    WorkflowRunnerBinding,
    assess_package_execution,
    background_execution_context,
    production_workflow_runner_binding,
)
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    WorkflowResourceReadBudget,
    WorkflowRiskSummary,
    compute_package_digest,
)
from plugins.workflow.compat import CompatibilityReport


class WorkflowAdmissionAssessmentError(ValueError):
    """The immutable compilation and its derived admission identities disagree."""


@dataclass(frozen=True, slots=True)
class WorkflowAdmissionAssessment:
    compilation: WorkflowCompilation
    package: WorkflowPackage
    package_digest: WorkflowPackageDigest
    execution_context: ExecutionCapabilityContext
    provider_authority: WorkflowProviderAuthority | None
    compatibility: CompatibilityReport
    risk: WorkflowRiskSummary
    execution_identity: str | None
    capability_summary: Mapping[str, object] | None
    next_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.package is not self.compilation.package:
            raise WorkflowAdmissionAssessmentError(
                "admission package differs from its compilation"
            )
        if self.capability_summary is not None:
            object.__setattr__(
                self,
                "capability_summary",
                MappingProxyType(dict(self.capability_summary)),
            )


def _capability_summary(
    authority: WorkflowProviderAuthority | None,
) -> Mapping[str, object] | None:
    if authority is None:
        return None
    providers = {route.provider for route in authority.routes.values()}
    unsupported = sum(
        item.decision.disposition is CapabilityDisposition.UNSUPPORTED
        for item in authority.obligations
    )
    degraded = sum(
        item.decision.disposition
        is CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS
        for item in authority.obligations
    )
    return {
        "schema_version": 1,
        "resolved_route_count": len(authority.routes),
        "mixed_provider": len(providers) > 1,
        "unsupported_count": unsupported,
        "degraded_count": degraded,
        "warning_codes": tuple(sorted({item.code for item in authority.warnings})),
        "authority_digest": authority.authority_digest,
    }


def assess_workflow_admission(
    compilation: WorkflowCompilation,
    execution_context: ExecutionCapabilityContext,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    isolated_workdir: bool = False,
) -> WorkflowAdmissionAssessment:
    """Resolve exactly once and derive all admission identities from that result."""
    if not isinstance(compilation, WorkflowCompilation):
        raise TypeError("compilation must be immutable workflow compilation")
    if not isinstance(execution_context, ExecutionCapabilityContext):
        raise TypeError("execution context must be immutable capability context")
    package = compilation.package
    phase4 = supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    )
    package_digest = (
        WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        )
        if phase4
        else compute_package_digest(package, read_budget=read_budget)
    )
    authority_error = None
    try:
        authority = execution_context.provider_authority(package)
    except WorkflowProviderAuthorityError as exc:
        authority = None
        authority_error = exc
    compatibility, risk = assess_package_execution(
        package,
        execution_context,
        read_budget=read_budget,
        compilation=compilation if phase4 else None,
        provider_authority=authority,
        provider_authority_error=authority_error,
        available_tools=available_tools,
        available_services=available_services,
        isolated_workdir=isolated_workdir,
    )
    if risk.package_digest != package_digest.sha256:
        raise WorkflowAdmissionAssessmentError(
            "admission risk differs from compiled package identity"
        )
    return WorkflowAdmissionAssessment(
        compilation=compilation,
        package=package,
        package_digest=package_digest,
        execution_context=execution_context,
        provider_authority=authority,
        compatibility=compatibility,
        risk=risk,
        execution_identity=(
            execution_context.identity_digest_for(
                package,
                provider_authority=authority,
            )
            if authority_error is None
            else None
        ),
        capability_summary=_capability_summary(authority),
        next_actions=("run",) if compatibility.runnable else ("doctor",),
    )


def assess_production_workflow_admission(
    compilation: WorkflowCompilation,
    *,
    requires_ai: bool | None = None,
    runner_binding: WorkflowRunnerBinding | None = None,
    read_budget: WorkflowResourceReadBudget | None = None,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    isolated_workdir: bool = False,
) -> WorkflowAdmissionAssessment:
    """Assess through the same server-owned binding used by execution."""
    binding = runner_binding or production_workflow_runner_binding()
    return assess_workflow_admission(
        compilation,
        background_execution_context(binding, requires_ai=requires_ai),
        read_budget=read_budget,
        available_tools=available_tools,
        available_services=available_services,
        isolated_workdir=isolated_workdir,
    )


__all__ = [
    "WorkflowAdmissionAssessment",
    "WorkflowAdmissionAssessmentError",
    "assess_production_workflow_admission",
    "assess_workflow_admission",
]
