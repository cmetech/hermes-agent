"""Field-level portable-to-Hermes compatibility classification."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, AbstractSet, Iterable, Literal, Mapping, Protocol

from plugins.workflow.language_schema import NODE_TYPES, inapplicable_node_fields
from plugins.workflow.language import supports_phase4_semantics, supports_phase5_semantics
from plugins.workflow.models import (
    CompatibilityFinding,
    CompatibilityLevel,
    WorkflowPackage,
)

if TYPE_CHECKING:
    from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
    from plugins.workflow.provider_authority import WorkflowProviderAuthority
    from plugins.workflow.trust import WorkflowRiskSummary


WORKFLOW_COMPATIBILITY_FINDINGS_MAX = 512
WORKFLOW_COMPATIBILITY_PAYLOAD_MAX_BYTES = 1024 * 1024
_WORKFLOW_COMPATIBILITY_PATH_JSON_MAX_BYTES = 256
_WORKFLOW_COMPATIBILITY_MESSAGE_JSON_MAX_BYTES = 512
_WORKFLOW_COMPATIBILITY_TRUNCATION_CODE = "compatibility_findings_truncated"
_WORKFLOW_COMPATIBILITY_TRUNCATION_PATH = "compatibility.findings"
_WORKFLOW_COMPATIBILITY_TRUNCATION_PREFIX = "Compatibility findings truncated: "
_WORKFLOW_COMPATIBILITY_TEXT_SUFFIX = "…[TRUNCATED]"


@dataclass(frozen=True)
class CompatibilityReport:
    level: CompatibilityLevel
    findings: tuple[CompatibilityFinding, ...]
    runnable: bool

    def __post_init__(self) -> None:
        bounded = _bounded_compatibility_findings(self.findings)
        if bounded != self.findings:
            object.__setattr__(self, "findings", bounded)

    @property
    def findings_truncated(self) -> bool:
        return bool(
            self.findings and _truncation_omitted_count(self.findings[-1]) is not None
        )

    @property
    def finding_count(self) -> int:
        if not self.findings_truncated:
            return len(self.findings)
        omitted = _truncation_omitted_count(self.findings[-1])
        return len(self.findings) - 1 + (omitted or 0)

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


def _json_string_character_bytes(character: str) -> int:
    """Return the bytes used by one character in an ensure-ASCII JSON string."""
    codepoint = ord(character)
    if character in {'"', "\\"}:
        return 2
    if codepoint < 0x20:
        return 6
    if codepoint < 0x80:
        return 1
    if codepoint <= 0xFFFF:
        return 6
    return 12


def _json_string_bytes(value: str) -> int:
    return 2 + sum(_json_string_character_bytes(character) for character in value)


def _author_text_with_suffix(
    value: str,
    *,
    max_json_bytes: int,
    suffix: str,
) -> str:
    remaining = max_json_bytes - _json_string_bytes(suffix)
    retained: list[str] = []
    for character in value:
        encoded_bytes = _json_string_character_bytes(character)
        if encoded_bytes > remaining:
            break
        retained.append(character)
        remaining -= encoded_bytes
    return "".join(retained) + suffix


def _bounded_author_text(
    value: str,
    *,
    max_json_bytes: int,
    collision_safe: bool = False,
) -> str:
    """Bound one author string by its conservative serialized JSON byte size."""
    if _json_string_bytes(value) <= max_json_bytes:
        return value

    suffix = _WORKFLOW_COMPATIBILITY_TEXT_SUFFIX
    if collision_safe:
        digest = hashlib.sha256(
            value.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        suffix = f"…[TRUNCATED:{digest}]"
    return _author_text_with_suffix(
        value,
        max_json_bytes=max_json_bytes,
        suffix=suffix,
    )


def _disambiguated_author_path(value: str, collision_index: int) -> str:
    digest = hashlib.sha256(
        f"{collision_index}\0{value}".encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return _author_text_with_suffix(
        value,
        max_json_bytes=_WORKFLOW_COMPATIBILITY_PATH_JSON_MAX_BYTES,
        suffix=f"…[COLLISION:{collision_index}:{digest}]",
    )


@dataclass(frozen=True)
class _OmittedCompatibilityState:
    level: CompatibilityLevel
    blocking: bool


def _truncation_omitted_count(finding: CompatibilityFinding) -> int | None:
    if (
        finding.code != _WORKFLOW_COMPATIBILITY_TRUNCATION_CODE
        or finding.path != _WORKFLOW_COMPATIBILITY_TRUNCATION_PATH
        or not finding.message.startswith(_WORKFLOW_COMPATIBILITY_TRUNCATION_PREFIX)
    ):
        return None
    count, separator, _remainder = finding.message[
        len(_WORKFLOW_COMPATIBILITY_TRUNCATION_PREFIX) :
    ].partition(" ")
    if not separator or not count.isascii() or not count.isdigit():
        return None
    return int(count)


class _FindingAccumulator:
    """Retain first occurrences in order while bounding the public report."""

    def __init__(
        self,
        initial: Iterable[CompatibilityFinding] = (),
        *,
        deduplicate_sources: bool = True,
    ) -> None:
        self._findings: list[CompatibilityFinding] = []
        self._seen: set[tuple[str, str]] = set()
        self._public_keys: set[tuple[str, str]] = set()
        self._deduplicate_sources = deduplicate_sources
        self._omitted = 0
        self._omitted_blocking = False
        self._omitted_level = CompatibilityLevel.PORTABLE
        self._sentinel_effective_profile = None
        for finding in initial:
            self.add(finding)

    def _merge_omitted_state(
        self, *, count: int, level: CompatibilityLevel, blocking: bool
    ) -> None:
        states: list[_OmittedCompatibilityState] = []
        if self._omitted:
            states.append(
                _OmittedCompatibilityState(
                    level=self._omitted_level,
                    blocking=self._omitted_blocking,
                )
            )
        states.append(_OmittedCompatibilityState(level=level, blocking=blocking))
        self._omitted_level, _runnable = derive_compatibility_report_state(states)
        self._omitted += count
        self._omitted_blocking |= blocking

    def add(self, finding: CompatibilityFinding) -> None:
        omitted = _truncation_omitted_count(finding)
        if omitted is not None:
            self._merge_omitted_state(
                count=omitted,
                level=finding.level,
                blocking=finding.blocking,
            )
            self._sentinel_effective_profile = finding.effective_profile
            return

        source_key = (finding.code, finding.path)
        if self._deduplicate_sources:
            if source_key in self._seen:
                return
            self._seen.add(source_key)
        if len(self._findings) >= WORKFLOW_COMPATIBILITY_FINDINGS_MAX - 1:
            self._merge_omitted_state(
                count=1,
                level=finding.level,
                blocking=finding.blocking,
            )
            return
        public_path = _bounded_author_text(
            finding.path,
            max_json_bytes=_WORKFLOW_COMPATIBILITY_PATH_JSON_MAX_BYTES,
            collision_safe=True,
        )
        public_key = (finding.code, public_path)
        if public_key in self._public_keys:
            for collision_index in range(1, WORKFLOW_COMPATIBILITY_FINDINGS_MAX + 1):
                candidate = _disambiguated_author_path(finding.path, collision_index)
                public_key = (finding.code, candidate)
                if public_key not in self._public_keys:
                    public_path = candidate
                    break
            else:  # pragma: no cover - retained rows are bounded below candidates
                raise RuntimeError("compatibility public path space exhausted")
        bounded = replace(
            finding,
            path=public_path,
            message=_bounded_author_text(
                finding.message,
                max_json_bytes=_WORKFLOW_COMPATIBILITY_MESSAGE_JSON_MAX_BYTES,
            ),
        )
        self._findings.append(bounded)
        self._public_keys.add(public_key)

    def finish(self, *, effective_profile=None) -> tuple[CompatibilityFinding, ...]:
        findings = list(self._findings)
        if self._omitted:
            omitted = min(self._omitted, 999_999_999)
            findings.append(
                CompatibilityFinding(
                    path=_WORKFLOW_COMPATIBILITY_TRUNCATION_PATH,
                    level=self._omitted_level,
                    message=(
                        f"{_WORKFLOW_COMPATIBILITY_TRUNCATION_PREFIX}{omitted} "
                        f"omitted; aggregate level {self._omitted_level.value}"
                    ),
                    blocking=self._omitted_blocking,
                    code=_WORKFLOW_COMPATIBILITY_TRUNCATION_CODE,
                    severity="error" if self._omitted_blocking else "warning",
                    effective_profile=(
                        effective_profile or self._sentinel_effective_profile
                    ),
                )
            )
        if effective_profile is not None:
            findings = [
                replace(finding, effective_profile=effective_profile)
                for finding in findings
            ]
        return tuple(findings)


def _bounded_compatibility_findings(
    findings: Iterable[CompatibilityFinding],
) -> tuple[CompatibilityFinding, ...]:
    return _FindingAccumulator(findings, deduplicate_sources=False).finish()


def _finding(
    findings: _FindingAccumulator,
    path: str,
    level: CompatibilityLevel,
    message: str,
    *,
    code: str,
    blocking: bool = False,
    severity: str | None = None,
) -> None:
    findings.add(
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
    findings: _FindingAccumulator,
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


def _add_provider_authority_findings(
    findings: _FindingAccumulator,
    authority: "WorkflowProviderAuthority",
) -> None:
    """Project sealed v5 provider decisions into admission findings."""
    from hermes_cli.provider_capabilities import CapabilityDisposition

    for obligation in authority.obligations:
        decision = obligation.decision
        unsupported = decision.disposition is CapabilityDisposition.UNSUPPORTED
        _finding(
            findings,
            obligation.path,
            (
                CompatibilityLevel.UNSUPPORTED
                if unsupported
                else CompatibilityLevel.MAPPED
            ),
            decision.rationale,
            code=decision.code,
            blocking=unsupported,
            severity="error" if unsupported else "warning",
        )
    for warning in authority.warnings:
        _finding(
            findings,
            warning.path,
            CompatibilityLevel.MAPPED,
            warning.rationale,
            code=warning.code,
            severity="warning",
        )


def assess_compatibility(
    package: WorkflowPackage,
    *,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
    structured_output_decisions: Mapping[
        str, "StructuredOutputCapabilityDecision"
    ] | None = None,
    provider_authority: "WorkflowProviderAuthority | None" = None,
) -> CompatibilityReport:
    """Classify every declared field that requires a Hermes mapping."""
    tools = available_tools
    services = available_services
    capabilities = provider_capabilities or {}
    findings = _FindingAccumulator(package.compatibility_findings)
    options = package.definition.options
    phase5 = supports_phase5_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    )

    if phase5:
        if provider_authority is None:
            _finding(
                findings,
                "provider_authority",
                CompatibilityLevel.UNSUPPORTED,
                "normalizer-v5 admission requires one sealed provider authority",
                code="provider_authority_missing",
                blocking=True,
            )
        else:
            _add_provider_authority_findings(findings, provider_authority)

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
    for field in (() if phase5 else _PROVIDER_FIELDS):
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
        structured_output = package.language.structured_outputs.get(node.id)
        if (
            not phase5
            and structured_output is not None
            and structured_output_decisions is not None
        ):
            decision = structured_output_decisions.get(node.id)
            if decision is None:
                decision = structured_output_decisions.get(
                    structured_output.schema_fingerprint
                )
            unsupported = decision is None or decision.strategy.value == "unsupported"
            _finding(
                findings,
                f"{prefix}.output_format",
                (
                    CompatibilityLevel.UNSUPPORTED
                    if unsupported
                    else CompatibilityLevel.MAPPED
                ),
                (
                    "configured runtime cannot honor the structured-output contract"
                    if unsupported
                    else (
                        "structured output uses sealed strategy "
                        f"{decision.strategy.value}"
                    )
                ),
                code=(
                    "structured_output_strategy_unsupported"
                    if unsupported
                    else "structured_output_strategy_resolved"
                ),
                blocking=unsupported,
            )
        if node_options.get("context") == "shared":
            _finding(
                findings,
                f"{prefix}.context",
                CompatibilityLevel.MAPPED,
                "shared context resumes only a cache-fingerprint-compatible predecessor",
                code="shared_context_fingerprint",
            )
        inapplicable = inapplicable_node_fields(node.node_type)
        for field in sorted(inapplicable.keys() & node_options.keys()):
            applicable_types = " and ".join(
                candidate
                for candidate in NODE_TYPES
                if candidate in inapplicable[field]
            )
            _finding(
                findings,
                f"{prefix}.{field}",
                CompatibilityLevel.UNSUPPORTED,
                f"{field} applies only to {applicable_types} nodes",
                code="field_not_applicable",
                blocking=True,
            )
        if (
            node.node_type == "loop"
            and supports_phase4_semantics(
                package.language.effective_profile,
                package.language.normalizer_version,
            )
        ):
            loop_semantics = package.language.node_semantics.get(node.id, {}).get(
                "loop"
            )
            if isinstance(loop_semantics, Mapping):
                prompt_source = str(loop_semantics.get("prompt_source", ""))
                _finding(
                    findings,
                    f"{prefix}.loop.{prompt_source}",
                    CompatibilityLevel.MAPPED,
                    (
                        "ordinary loop prompt resolves through its immutable "
                        f"{prompt_source} source"
                    ),
                    code="phase4_loop_prompt_sealed",
                )
                if (
                    loop_semantics.get("effective_interactive") is True
                    and loop_semantics.get("signal_completes") is False
                ):
                    _finding(
                        findings,
                        f"{prefix}.loop.signal_completes",
                        CompatibilityLevel.MAPPED,
                        "signal completion pauses for a backend-authored confirmation",
                        code="phase4_signal_confirmation",
                    )
        if node.node_type not in {"command", "prompt"}:
            continue
        if "persist_session" in node_options and not phase5:
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
        if "agents" in node_options and phase5:
            explicit_allowed = node_options.get("allowed_tools")
            denied = node_options.get("denied_tools", ())

            def resolves_to_inline_agent(value: object) -> bool:
                try:
                    return resolve_tool_name(value) == "workflow_agent"
                except (TypeError, ValueError):
                    return False

            unreachable = (
                explicit_allowed is not None
                and not any(resolves_to_inline_agent(value) for value in explicit_allowed)
            ) or any(resolves_to_inline_agent(value) for value in denied)
            if unreachable:
                _finding(
                    findings,
                    f"{prefix}.agents",
                    CompatibilityLevel.UNSUPPORTED,
                    "inline agents are unreachable under the declared tool policy",
                    code="tool_policy_incompatible",
                    blocking=True,
                )
        if "skills" in node_options and not phase5:
            _finding(
                findings,
                f"{prefix}.skills",
                CompatibilityLevel.MAPPED,
                "skills are snapshotted into the node user message",
                code="skill_snapshot",
            )
        if "mcp" in node_options and not phase5:
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
        if "agents" in node_options and not phase5:
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
        for field in (() if phase5 else (
            "effort",
            "thinking",
            "maxBudgetUsd",
            "fallbackModel",
            "betas",
            "sandbox",
        )):
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
        for event in (() if phase5 else node_options.get("hooks", {})):
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

    bounded_findings = findings.finish(
        effective_profile=package.language.effective_profile
    )
    level, runnable = derive_compatibility_report_state(bounded_findings)
    return CompatibilityReport(
        level=level, findings=bounded_findings, runnable=runnable
    )
