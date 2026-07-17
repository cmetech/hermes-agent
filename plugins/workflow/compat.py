"""Field-level portable-to-Hermes compatibility classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import AbstractSet, Mapping

from plugins.workflow.models import WorkflowPackage


class CompatibilityLevel(StrEnum):
    PORTABLE = "portable"
    MAPPED = "mapped"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CompatibilityFinding:
    path: str
    level: CompatibilityLevel
    message: str
    blocking: bool


@dataclass(frozen=True)
class CompatibilityReport:
    level: CompatibilityLevel
    findings: tuple[CompatibilityFinding, ...]
    runnable: bool

    @property
    def blocking_findings(self) -> tuple[CompatibilityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.blocking)


ARCHON_TOOL_ALIASES = {
    "Agent": "delegate_task",
    "Bash": "terminal",
    "Edit": "patch",
    "Glob": "search_files",
    "Grep": "search_files",
    "Read": "read_file",
    "Task": "delegate_task",
    "WebFetch": "web_extract",
    "WebSearch": "web_search",
    "Write": "write_file",
}

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
    "effort": "reasoning",
    "thinking": "reasoning",
    "maxBudgetUsd": "reasoning",
    "modelReasoningEffort": "reasoning",
    "webSearchMode": "web_search_mode",
    "betas": "betas",
    "sandbox": "sandbox",
}


def _finding(
    findings: list[CompatibilityFinding],
    path: str,
    level: CompatibilityLevel,
    message: str,
    *,
    blocking: bool = False,
) -> None:
    findings.append(CompatibilityFinding(path, level, message, blocking))


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
        )
    else:
        _finding(
            findings,
            path,
            CompatibilityLevel.UNSUPPORTED,
            f"provider {provider} does not advertise {required} required by {field}",
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
    findings: list[CompatibilityFinding] = []
    options = package.definition.options

    for issue in package.validation_issues:
        _finding(
            findings,
            issue.path,
            CompatibilityLevel.UNSUPPORTED,
            issue.message,
            blocking=issue.blocking,
        )

    for field in ("provider", "model", "fallbackModel"):
        if field in options:
            _finding(
                findings,
                field,
                CompatibilityLevel.MAPPED,
                f"{field} resolves through Hermes provider profiles",
            )
    if "persist_sessions" in options:
        _finding(
            findings,
            "persist_sessions",
            CompatibilityLevel.MAPPED,
            "persist_sessions maps to the workflow node-session registry",
        )
    for field in ("interactive", "tags"):
        if field in options:
            _finding(
                findings,
                field,
                CompatibilityLevel.MAPPED,
                f"{field} maps to Hermes invocation metadata",
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
                blocking=True,
            )
        else:
            _finding(
                findings,
                "worktree",
                CompatibilityLevel.MAPPED,
                "caller-supplied workdir preserves worktree isolation",
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
            )
        if node.node_type not in {"command", "prompt"}:
            for field in sorted(_AI_ONLY_FIELDS.intersection(node_options)):
                _finding(
                    findings,
                    f"{prefix}.{field}",
                    CompatibilityLevel.UNSUPPORTED,
                    f"{field} applies only to command and prompt nodes",
                    blocking=True,
                )
            continue
        if "persist_session" in node_options:
            _finding(
                findings,
                f"{prefix}.persist_session",
                CompatibilityLevel.MAPPED,
                "persist_session maps to the profile-scoped node-session registry",
            )
        for list_field in ("allowed_tools", "denied_tools"):
            for tool_index, requested in enumerate(node_options.get(list_field, ())):
                target = ARCHON_TOOL_ALIASES.get(requested)
                path = f"{prefix}.{list_field}[{tool_index}]"
                if target is None:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.UNSUPPORTED,
                        f"unknown Archon tool alias: {requested}",
                        blocking=True,
                    )
                elif tools is not None and target not in tools:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.UNSUPPORTED,
                        f"mapped Hermes tool is unavailable: {requested} -> {target}",
                        blocking=True,
                    )
                else:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.MAPPED,
                        f"tool alias maps {requested} -> {target}",
                    )
        if "skills" in node_options:
            _finding(
                findings,
                f"{prefix}.skills",
                CompatibilityLevel.MAPPED,
                "skills are snapshotted into the node user message",
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
                blocking=not mcp_available,
            )
        if "agents" in node_options:
            _finding(
                findings,
                f"{prefix}.agents",
                CompatibilityLevel.MAPPED,
                "inline agents map to bounded workflow_agent child workers",
            )
        for field in ("provider", "model", "fallbackModel"):
            if field in node_options:
                _finding(
                    findings,
                    f"{prefix}.{field}",
                    CompatibilityLevel.MAPPED,
                    f"{field} resolves through Hermes provider profiles",
                )
        for field in ("effort", "thinking", "maxBudgetUsd", "betas", "sandbox"):
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
                        blocking=True,
                    )
                else:
                    _finding(
                        findings,
                        path,
                        CompatibilityLevel.MAPPED,
                        f"{event} maps to isolated Hermes worker lifecycle",
                    )
            elif event in UNSUPPORTED_HOOK_EVENTS:
                _finding(
                    findings,
                    path,
                    CompatibilityLevel.UNSUPPORTED,
                    f"{event} has no equivalent Hermes node-worker contract",
                    blocking=True,
                )

    blocking = any(finding.blocking for finding in findings)
    if blocking or any(
        finding.level is CompatibilityLevel.UNSUPPORTED for finding in findings
    ):
        level = CompatibilityLevel.UNSUPPORTED
    elif findings:
        level = CompatibilityLevel.MAPPED
    else:
        level = CompatibilityLevel.PORTABLE
    return CompatibilityReport(
        level=level, findings=tuple(findings), runnable=not blocking
    )
