"""Field-level portable-to-Hermes compatibility classification."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, AbstractSet, Iterable, Literal, Mapping, Protocol

from plugins.workflow.models import (
    CompatibilityFinding,
    CompatibilityLevel,
    WorkflowPackage,
)
from plugins.workflow.language import (
    WORKFLOW_LANGUAGE_FINDINGS_PER_NODE_MAX,
    WORKFLOW_LANGUAGE_PACKAGE_FINDINGS_MAX,
)
from plugins.workflow.projection_limits import (
    WORKFLOW_DEFINITION_MAX_BYTES,
    WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS,
    WORKFLOW_DEFINITION_MAX_NODES,
)

if TYPE_CHECKING:
    from plugins.workflow.trust import WorkflowRiskSummary


@dataclass(frozen=True)
class CompatibilityReport:
    level: CompatibilityLevel
    findings: tuple[CompatibilityFinding, ...]
    runnable: bool

    @property
    def blocking_findings(self) -> tuple[CompatibilityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.blocking)


class WorkflowCompatibilityBlockedError(RuntimeError):
    """Raised when a compatibility assessment cannot be admitted to run."""

    code = "workflow_compatibility_blocked"

    def __init__(self, report: CompatibilityReport) -> None:
        self.report = report
        super().__init__("workflow compatibility has blocking findings")


def require_runnable(report: CompatibilityReport) -> CompatibilityReport:
    """Return a runnable report or refuse admission with its authoritative state."""
    if not report.runnable:
        raise WorkflowCompatibilityBlockedError(report)
    return report


class _CompatibilityStateFinding(Protocol):
    level: CompatibilityLevel | str
    blocking: bool


def derive_compatibility_report_state(
    findings: Iterable[_CompatibilityStateFinding],
) -> tuple[CompatibilityLevel, bool]:
    """Derive the authoritative report level and runnable bit from findings."""
    materialized = tuple(findings)
    blocking = any(finding.blocking for finding in materialized)
    if blocking or any(
        finding.level == CompatibilityLevel.UNSUPPORTED for finding in materialized
    ):
        level = CompatibilityLevel.UNSUPPORTED
    elif materialized:
        level = CompatibilityLevel.MAPPED
    else:
        level = CompatibilityLevel.PORTABLE
    return level, not blocking


@dataclass(frozen=True)
class InputRequirement:
    name: str
    kind: Literal["text", "file", "directory", "json"]
    required: bool
    max_bytes: int | None


@dataclass(frozen=True)
class DoctorReport:
    package: str
    workflow: str
    runnable: bool
    package_digest: str
    trust_state: Literal["trusted", "untrusted"]
    risk_summary: "WorkflowRiskSummary"
    input_requirements: tuple[InputRequirement, ...]
    concurrency_policy: Literal["queue", "allow", "forbid"]
    findings: tuple[CompatibilityFinding, ...]
    resolved_commands: tuple[str, ...]
    resolved_scripts: tuple[str, ...]
    resolved_mcp_servers: tuple[str, ...]
    resolved_skills: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON representation used by the CLI and tests."""
        return asdict(self)


ARCHON_TOOL_ALIASES = {
    "Agent": "workflow_agent",
    "Bash": "terminal",
    "Edit": "patch",
    "Glob": "search_files",
    "Grep": "search_files",
    "Read": "read_file",
    "Task": "workflow_agent",
    "WebFetch": "web_extract",
    "WebSearch": "web_search",
    "Write": "write_file",
}


def resolve_tool_name(name: str) -> str:
    """Resolve a published Archon alias without guessing unknown aliases."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool name must be a non-empty string")
    normalized = name.strip()
    target = ARCHON_TOOL_ALIASES.get(normalized)
    if target is not None:
        return target
    if normalized[:1].isupper():
        raise ValueError(f"unknown Archon tool alias: {normalized}")
    return normalized


_AI_ONLY_FIELDS = frozenset({
    "persist_session",
    "provider",
    "model",
    "output_format",
    "allowed_tools",
    "denied_tools",
    "hooks",
    "mcp",
    "skills",
    "agents",
    "effort",
    "thinking",
    "maxBudgetUsd",
    "systemPrompt",
    "fallbackModel",
    "betas",
    "sandbox",
})

MAPPED_HOOK_EVENTS = frozenset({
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "Setup",
    "Elicitation",
    "ElicitationResult",
    "InstructionsLoaded",
    "TaskCompleted",
})
UNSUPPORTED_HOOK_EVENTS = frozenset({
    "Notification",
    "Stop",
    "PreCompact",
    "TeammateIdle",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
})
_PROVIDER_FIELDS = {
    "effort": "reasoning_effort",
    "thinking": "thinking",
    "maxBudgetUsd": "budget",
    "modelReasoningEffort": "reasoning_effort",
    "webSearchMode": "web_execution",
    "fallbackModel": "fallback_model",
    "betas": "betas",
    "sandbox": "sandbox",
}

# A detail response is admitted only after the complete definition projection
# succeeds. These ceilings therefore bound every compatibility producer loop:
# two tool lists can each emit one finding per projected list member, hook
# findings are one per supported event, and all remaining fields are singular.
# The byte ceiling safely bounds legacy unknown top-level keys because each
# issue requires a distinct key represented in that same complete projection.
_SINGULAR_COMMAND_FINDING_FIELDS = frozenset({
    "persist_session",
    "skills",
    "mcp",
    "agents",
    "provider",
    "model",
    "effort",
    "thinking",
    "maxBudgetUsd",
    "fallbackModel",
    "betas",
    "sandbox",
    "systemPrompt",
})
WORKFLOW_COMPATIBILITY_FINDINGS_PER_NODE_MAX = (
    WORKFLOW_LANGUAGE_FINDINGS_PER_NODE_MAX
    + 1  # context
    + len(_AI_ONLY_FIELDS)
    + 2 * WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS  # allowed_tools + denied_tools
    + len(_SINGULAR_COMMAND_FINDING_FIELDS)
    + len(MAPPED_HOOK_EVENTS | UNSUPPORTED_HOOK_EVENTS)
)
WORKFLOW_COMPATIBILITY_PACKAGE_FINDINGS_MAX = (
    WORKFLOW_DEFINITION_MAX_BYTES  # non-fatal unknown top-level issues
    + WORKFLOW_LANGUAGE_PACKAGE_FINDINGS_MAX
    + 2  # provider + model
    + 1  # persist_sessions
    + 2  # interactive + tags
    + WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS  # requires
    + 1  # worktree
    + len(_PROVIDER_FIELDS)
    + 1  # execution-environment finding added by the runner binding
)
WORKFLOW_COMPATIBILITY_FINDINGS_MAX = (
    WORKFLOW_DEFINITION_MAX_NODES
    * WORKFLOW_COMPATIBILITY_FINDINGS_PER_NODE_MAX
    + WORKFLOW_COMPATIBILITY_PACKAGE_FINDINGS_MAX
)


def _finding(
    findings: list[CompatibilityFinding],
    path: str,
    level: CompatibilityLevel,
    message: str,
    *,
    code: str,
    blocking: bool = False,
    severity: str | None = None,
) -> None:
    if any(finding.code == code and finding.path == path for finding in findings):
        return
    findings.append(
        CompatibilityFinding(
            path=path,
            level=level,
            message=message,
            blocking=blocking,
            code=code,
            severity=severity or ("error" if blocking else "info"),
        )
    )


def _provider_for(package: WorkflowPackage, node_index: int | None = None) -> str:
    if node_index is not None:
        node_provider = package.definition.nodes[node_index].options.get("provider")
        if isinstance(node_provider, str) and node_provider:
            return node_provider
    provider = package.definition.options.get("provider")
    return provider if isinstance(provider, str) and provider else "default"


def _check_provider_field(
    findings: list[CompatibilityFinding],
    *,
    path: str,
    field: str,
    provider: str,
    capabilities: Mapping[str, AbstractSet[str]],
) -> None:
    required = _PROVIDER_FIELDS[field]
    advertised = capabilities.get(provider, frozenset())
    if required in advertised:
        _finding(
            findings,
            path,
            CompatibilityLevel.MAPPED,
            f"{field} maps through {provider} capability {required}",
            code="provider_field_mapped",
        )
    else:
        _finding(
            findings,
            path,
            CompatibilityLevel.UNSUPPORTED,
            f"provider {provider} does not advertise {required} required by {field}",
            code="provider_field_unsupported",
            blocking=True,
        )


def assess_compatibility(
    package: WorkflowPackage,
    *,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
) -> CompatibilityReport:
    """Classify every declared field that requires a Hermes mapping."""
    tools = available_tools
    services = available_services
    capabilities = provider_capabilities or {}
    findings = list(package.compatibility_findings)
    options = package.definition.options

    for issue in package.validation_issues:
        _finding(
            findings,
            issue.path,
            CompatibilityLevel.UNSUPPORTED,
            issue.message,
            code=issue.code,
            blocking=issue.blocking,
            severity=issue.severity,
        )

    for field in ("provider", "model"):
        if field in options:
            _finding(
                findings,
                field,
                CompatibilityLevel.MAPPED,
                f"{field} resolves through Hermes provider profiles",
                code="provider_profile_resolution",
            )
    if "persist_sessions" in options:
        _finding(
            findings,
            "persist_sessions",
            CompatibilityLevel.MAPPED,
            "persist_sessions maps to the workflow node-session registry",
            code="persistent_session_fingerprint",
        )
    for field in ("interactive", "tags"):
        if field in options:
            _finding(
                findings,
                field,
                CompatibilityLevel.MAPPED,
                f"{field} maps to Hermes invocation metadata",
                code="invocation_metadata",
            )
    if "requires" in options:
        for index, service in enumerate(options["requires"]):
            missing = services is not None and service not in services
            _finding(
                findings,
                f"requires[{index}]",
                CompatibilityLevel.UNSUPPORTED
                if missing
                else CompatibilityLevel.MAPPED,
                f"required service {service} {'is not configured' if missing else 'will be checked during preflight'}",
                code="required_service",
                blocking=missing,
            )
    if "worktree" in options:
        enabled = bool(options["worktree"].get("enabled", False))
        if enabled and not isolated_workdir:
            _finding(
                findings,
                "worktree.enabled",
                CompatibilityLevel.UNSUPPORTED,
                "workflow requires an explicitly supplied isolated workdir",
                code="worktree_requirement",
                blocking=True,
            )
        else:
            _finding(
                findings,
                "worktree",
                CompatibilityLevel.MAPPED,
                "caller-supplied workdir preserves worktree isolation",
                code="worktree_requirement",
            )
    for field in _PROVIDER_FIELDS:
        if field in options:
            _check_provider_field(
                findings,
                path=field,
                field=field,
                provider=_provider_for(package),
                capabilities=capabilities,
            )

    for index, node in enumerate(package.definition.nodes):
        prefix = f"nodes[{index}]"
        node_options = node.options
        if node_options.get("context") == "shared":
            _finding(
                findings,
                f"{prefix}.context",
                CompatibilityLevel.MAPPED,
                "shared context resumes only a cache-fingerprint-compatible predecessor",
                code="shared_context_fingerprint",
            )
        if node.node_type not in {"command", "prompt"}:
            for field in sorted(_AI_ONLY_FIELDS.intersection(node_options)):
                _finding(
                    findings,
                    f"{prefix}.{field}",
                    CompatibilityLevel.UNSUPPORTED,
                    f"{field} applies only to command and prompt nodes",
                    code="field_not_applicable",
                    blocking=True,
                )
            continue
        if "persist_session" in node_options:
            _finding(
                findings,
                f"{prefix}.persist_session",
                CompatibilityLevel.MAPPED,
                "persist_session maps to the profile-scoped node-session registry",
                code="persistent_session_fingerprint",
            )
        for list_field in ("allowed_tools", "denied_tools"):
            for tool_index, requested in enumerate(node_options.get(list_field, ())):
                path = f"{prefix}.{list_field}[{tool_index}]"
                try:
                    target = resolve_tool_name(requested)
                except ValueError:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.UNSUPPORTED,
                        f"unknown Archon tool alias: {requested}",
                        code="unknown_tool_alias",
                        blocking=True,
                    )
                    continue
                if tools is not None and target not in tools:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.UNSUPPORTED,
                        f"mapped Hermes tool is unavailable: {requested} -> {target}",
                        code="unavailable_tool",
                        blocking=True,
                    )
                else:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.MAPPED,
                        f"tool alias maps {requested} -> {target}",
                        code="tool_alias_mapped",
                    )
        if "skills" in node_options:
            _finding(
                findings,
                f"{prefix}.skills",
                CompatibilityLevel.MAPPED,
                "skills are snapshotted into the node user message",
                code="skill_snapshot",
            )
        if "mcp" in node_options:
            _finding(
                findings,
                f"{prefix}.mcp",
                CompatibilityLevel.MAPPED
                if mcp_available
                else CompatibilityLevel.UNSUPPORTED,
                "MCP servers start only inside the isolated node worker"
                if mcp_available
                else "Hermes MCP support is not available",
                code="mcp_isolation",
                blocking=not mcp_available,
            )
        if "agents" in node_options:
            _finding(
                findings,
                f"{prefix}.agents",
                CompatibilityLevel.MAPPED,
                "inline agents map to bounded workflow_agent child workers",
                code="inline_agent_bounds",
            )
        for field in ("provider", "model"):
            if field in node_options:
                _finding(
                    findings,
                    f"{prefix}.{field}",
                    CompatibilityLevel.MAPPED,
                    f"{field} resolves through Hermes provider profiles",
                    code="provider_profile_resolution",
                )
        for field in (
            "effort",
            "thinking",
            "maxBudgetUsd",
            "fallbackModel",
            "betas",
            "sandbox",
        ):
            if field in node_options:
                _check_provider_field(
                    findings,
                    path=f"{prefix}.{field}",
                    field=field,
                    provider=_provider_for(package, index),
                    capabilities=capabilities,
                )
        if "systemPrompt" in node_options:
            shared = node_options.get("context") == "shared"
            _finding(
                findings,
                f"{prefix}.systemPrompt",
                CompatibilityLevel.UNSUPPORTED if shared else CompatibilityLevel.MAPPED,
                "systemPrompt cannot change inside a shared cached session"
                if shared
                else "systemPrompt is fixed at fresh worker creation",
                code="system_prompt_context",
                blocking=shared,
            )
        for event in node_options.get("hooks", {}):
            path = f"{prefix}.hooks.{event}"
            if event in MAPPED_HOOK_EVENTS:
                if event in {"Elicitation", "ElicitationResult"} and not mcp_available:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.UNSUPPORTED,
                        f"{event} requires MCP support",
                        code="hook_unsupported",
                        blocking=True,
                    )
                else:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.MAPPED,
                        f"{event} maps to isolated Hermes worker lifecycle",
                        code="hook_mapped",
                    )
            elif event in UNSUPPORTED_HOOK_EVENTS:
                _finding(
                    findings,
                    path,
                    CompatibilityLevel.UNSUPPORTED,
                    f"{event} has no equivalent Hermes node-worker contract",
                    code="hook_unsupported",
                    blocking=True,
                )

    findings = [
        replace(
            finding,
            effective_profile=package.language.effective_profile,
        )
        for finding in findings
    ]
    level, runnable = derive_compatibility_report_state(findings)
    return CompatibilityReport(
        level=level, findings=tuple(findings), runnable=runnable
    )
