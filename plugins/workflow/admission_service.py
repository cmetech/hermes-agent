"""One immutable provider-aware assessment shared by workflow surfaces."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from types import MappingProxyType
from typing import AbstractSet, Mapping

import yaml

from plugins.workflow.compilation import WorkflowCompilation
from plugins.workflow.language import supports_phase4_semantics, supports_phase5_semantics
from plugins.workflow.models import WorkflowPackage
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowProviderAuthorityError,
    public_provider_capability_projection,
)
from plugins.workflow.resources import normalize_mcp_server_document
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


_PHASE5_MCP_PATH_SUFFIXES = frozenset({
    ".bash",
    ".cjs",
    ".crt",
    ".exe",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".key",
    ".mjs",
    ".pem",
    ".py",
    ".pyw",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
})
_PHASE5_MCP_PATH_OPTION = re.compile(
    r"^--?(?:config|cwd|dir|directory|entry|file|manifest|path|root|script)(?:[-_].*)?$",
    re.IGNORECASE,
)
_PHASE5_MCP_PATH_ENV = re.compile(
    r"(?:^|_)(?:CONFIG|CWD|DIR|DIRECTORY|ENTRY|FILE|MANIFEST|PATH|ROOT|SCRIPT)$",
    re.IGNORECASE,
)
_PHASE5_MCP_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _phase5_mcp_path_reference(
    value: str,
    *,
    compound: bool = False,
    path_hint: bool = False,
) -> str | None:
    option = ""
    candidate = value
    if compound and value.startswith("-") and "=" in value:
        option, candidate = value.split("=", 1)
    looks_path_like = (
        path_hint
        or "%" in candidate
        or _PHASE5_MCP_URI_SCHEME.match(candidate) is not None
        or candidate.startswith(("./", "../", ".\\", "..\\", "/", "~"))
        or "/" in candidate
        or "\\" in candidate
        or re.match(r"^[A-Za-z]:", candidate) is not None
        or any(candidate.lower().endswith(suffix) for suffix in _PHASE5_MCP_PATH_SUFFIXES)
        or bool(option and _PHASE5_MCP_PATH_OPTION.fullmatch(option))
    )
    if not looks_path_like:
        return None
    if candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def _phase5_mcp_launch_shape_is_sealed(
    server: Mapping[str, object], sealed_resources: set[str]
) -> bool:
    args = server.get("args")
    if not isinstance(args, list) or not args or any(
        not isinstance(value, str) for value in args
    ):
        return False
    for value in args:
        reference = _phase5_mcp_path_reference(value, compound=True)
        if reference is not None and reference not in sealed_resources:
            return False
    environment = server.get("env")
    if environment is not None:
        if not isinstance(environment, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in environment.items()
        ):
            return False
        for key, value in environment.items():
            reference = _phase5_mcp_path_reference(
                value,
                path_hint=_PHASE5_MCP_PATH_ENV.search(key) is not None,
            )
            if reference is not None and reference not in sealed_resources:
                return False
    return True


def _phase5_mcp_execution_preconditions(
    compilation: WorkflowCompilation,
) -> Mapping[str, bool] | None:
    package = compilation.package
    if not supports_phase5_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        return None
    resources_by_node: dict[str, set[str]] = {}
    mcp_bindings = []
    for binding in compilation.dependency_manifest.resources:
        if binding.node_id is None:
            continue
        if binding.resource_kind == "mcp_resource":
            resources = resources_by_node.setdefault(binding.node_id, set())
            resources.add(binding.source_relative_path)
            resources.add(binding.snapshot_path)
        elif binding.resource_kind == "mcp":
            mcp_bindings.append(binding)
    facts: dict[str, bool] = {}
    for binding in mcp_bindings:
        node_id = str(binding.node_id)
        supported = True
        try:
            encoded = compilation.sealed_files[binding.snapshot_path]
            document = yaml.safe_load(encoded.decode("utf-8")) or {}
            servers = normalize_mcp_server_document(
                document,
                default_name=binding.snapshot_path.rsplit("/", 1)[-1].split(".")[0],
            )
        except (KeyError, UnicodeError, ValueError, yaml.YAMLError):
            supported = False
            servers = {}
        sealed_resources = resources_by_node.get(node_id, set())
        for server in servers.values():
            command = server.get("command")
            args = server.get("args")
            entry = args[0] if isinstance(args, list) and args else None
            runtime_files = server.get("runtime_files", [])
            if (
                command != sys.executable
                or server.get("transport") != "stdio"
                or not isinstance(entry, str)
                or entry.startswith("-")
                or not entry.lower().endswith((".py", ".pyw"))
                or entry not in sealed_resources
                or not _phase5_mcp_launch_shape_is_sealed(
                    server, sealed_resources
                )
                or not isinstance(runtime_files, list)
                or any(
                    not isinstance(path, str) or path not in sealed_resources
                    for path in runtime_files
                )
            ):
                supported = False
        facts[node_id] = facts.get(node_id, True) and supported and bool(servers)
    return MappingProxyType(facts)


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
    return public_provider_capability_projection(authority)


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
        authority = execution_context.provider_authority(
            package,
            mcp_execution_preconditions=(
                _phase5_mcp_execution_preconditions(compilation)
            ),
        )
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
