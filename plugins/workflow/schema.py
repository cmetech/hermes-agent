"""Strict portable YAML loading and deterministic DAG validation."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
import math
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agent.structured_output import parse_exact_decimal_integer
from plugins.workflow.language import (
    ARCHON_UNKNOWN_TOP_LEVEL_FIELD_CODE,
    UNKNOWN_TOP_LEVEL_FIELD_CODE,
    WORKFLOW_NORMALIZER_VERSION,
    WorkflowLanguageCompatibilityError,
    WorkflowStructuredOutputNormalizationError,
    language_compatibility_findings,
    normalize_workflow,
    prove_output_path_impossible,
    resolve_language_profile,
)
from plugins.workflow.language_schema import (
    MAX_WORKFLOW_DOCUMENT_BYTES,
    NODE_TYPES,
    WHEN_EXPRESSION_PATTERN,
    WHEN_REFERENCE_PATTERN,
    agent_field_names,
    approval_field_names,
    approval_reject_field_names,
    common_node_field_names,
    definition_field_names,
    field_max_length,
    hook_entry_field_names,
    hook_event_names,
    hook_response_field_names,
    hook_specific_field_names,
    loop_field_names,
    retry_field_names,
    sidecar_field_names,
    structural_node_field_names,
)
from plugins.workflow.models import (
    ValidationIssue,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowPackage,
    WorkflowLanguageProfile,
    WorkflowRuntimeConfig,
    WorkflowValidationError,
    freeze_value,
)
from plugins.workflow.resources import iter_output_field_references

TRIGGER_RULES = (
    "all_success",
    "one_success",
    "none_failed_min_one_success",
    "all_done",
)


class _WorkflowSafeLoader(yaml.SafeLoader):
    """SafeLoader with local exact decimal integer construction."""


def _construct_workflow_yaml_int(loader, node):
    value = loader.construct_scalar(node).replace("_", "")
    sign = -1 if value[0] == "-" else 1
    if value[0] in "+-":
        value = value[1:]
    if value == "0":
        return 0
    if value.startswith("0b"):
        return sign * int(value[2:], 2)
    if value.startswith("0x"):
        return sign * int(value[2:], 16)
    if value[0] == "0":
        return sign * int(value, 8)
    if ":" in value:
        digits = [
            parse_exact_decimal_integer(
                part,
                max_digits=MAX_WORKFLOW_DOCUMENT_BYTES,
            )
            for part in value.split(":")
        ]
        total = 0
        base = 1
        for digit in reversed(digits):
            total += digit * base
            base *= 60
        return sign * total
    return sign * parse_exact_decimal_integer(
        value,
        max_digits=MAX_WORKFLOW_DOCUMENT_BYTES,
    )


_WorkflowSafeLoader.add_constructor(
    "tag:yaml.org,2002:int",
    _construct_workflow_yaml_int,
)
CONTEXT_VALUES = ("fresh", "shared")
SCRIPT_RUNTIMES = ("bun", "uv")
TOP_LEVEL_FIELDS = definition_field_names()
COMMON_NODE_FIELDS = common_node_field_names()
HOOK_EVENTS = hook_event_names()
HOOK_ENTRY_FIELDS = hook_entry_field_names()
HOOK_RESPONSE_FIELDS = hook_response_field_names()
HOOK_SPECIFIC_FIELDS = hook_specific_field_names()
RETRY_FIELDS = retry_field_names()
LOOP_FIELDS = loop_field_names()
APPROVAL_FIELDS = approval_field_names()
APPROVAL_REJECT_FIELDS = approval_reject_field_names()
AGENT_FIELDS = agent_field_names()
SIDECAR_FIELDS = sidecar_field_names()
_CONTROL_OR_ANSI = re.compile(r"[\x00-\x1f\x7f-\x9f]|\x1b\[")
_SAFE_NAME = re.compile(r"^[^\s/\\]+$")
_WHEN_REFERENCE = re.compile(WHEN_REFERENCE_PATTERN, re.UNICODE)
_WHEN_EXPRESSION = re.compile(WHEN_EXPRESSION_PATTERN, re.UNICODE)
_INLINE_SCRIPT_METACHAR = re.compile(r"[\s;(){}&|<>$`\"']")


def _issue(
    path: str, code: str, message: str, *, line: int | None = None
) -> ValidationIssue:
    return ValidationIssue(path=path, code=code, message=message, source_line=line)


def _fail(path: str, code: str, message: str, *, line: int | None = None) -> None:
    raise WorkflowValidationError(_issue(path, code, message, line=line))


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "expected_mapping", f"{path} must be a mapping")
    return value


def _string(
    value: Any,
    path: str,
    *,
    allow_empty: bool = False,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(path, "expected_string", f"{path} must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        _fail(
            path,
            "string_too_long",
            f"{path} exceeds the {max_length}-character limit",
        )
    return value


def _positive_number(value: Any, path: str, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(path, "expected_number", f"{path} must be a number")
    if not math.isfinite(float(value)):
        _fail(path, "invalid_bound", f"{path} must be finite")
    if value < 0 or (not allow_zero and value == 0):
        _fail(
            path,
            "invalid_bound",
            f"{path} must be {'non-negative' if allow_zero else 'positive'}",
        )


def _boolean(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        _fail(path, "expected_boolean", f"{path} must be a boolean")


def _string_list(value: Any, path: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        _fail(path, "expected_string_list", f"{path} must be a list of strings")


def _validate_thinking(value: Any, path: str) -> None:
    if isinstance(value, str):
        if value not in {"adaptive", "disabled"}:
            _fail(path, "invalid_thinking", f"{path} must be adaptive or disabled")
        return
    thinking = _mapping(value, path)
    unknown = sorted(set(thinking) - {"type", "budgetTokens"})
    if unknown or thinking.get("type") != "enabled" or "budgetTokens" not in thinking:
        _fail(
            path,
            "invalid_thinking",
            f"{path} must be adaptive, disabled, or an enabled budgetTokens object",
        )
    budget = thinking["budgetTokens"]
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        _fail(
            f"{path}.budgetTokens",
            "invalid_thinking",
            f"{path}.budgetTokens must be positive",
        )


def is_inline_script(value: str) -> bool:
    """Match the portable inline-vs-named script rule."""
    return bool(_INLINE_SCRIPT_METACHAR.search(value))


def _validate_identifier(value: Any, path: str) -> str:
    identifier = _string(value, path)
    if len(identifier) > 128 or _CONTROL_OR_ANSI.search(identifier):
        _fail(
            path,
            "invalid_identifier",
            f"{path} contains an invalid identifier character",
        )
    return identifier


def _validate_relative_resource(value: str, path: str) -> None:
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("~"):
        _fail(path, "resource_escape", f"{path} must be a contained relative path")


def _source_lines(text: str) -> tuple[dict[str, int], list[dict[str, int]]]:
    """Return one-based field lines without retaining mutable YAML nodes."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}, []
    if not isinstance(root, yaml.MappingNode):
        return {}, []
    top: dict[str, int] = {}
    node_lines: list[dict[str, int]] = []
    for key_node, value_node in root.value:
        key = str(key_node.value)
        top[key] = key_node.start_mark.line + 1
        if key == "nodes" and isinstance(value_node, yaml.SequenceNode):
            for entry in value_node.value:
                fields: dict[str, int] = {}
                if isinstance(entry, yaml.MappingNode):
                    for field_node, _ in entry.value:
                        fields[str(field_node.value)] = field_node.start_mark.line + 1
                node_lines.append(fields)
    return top, node_lines


def _validate_retry(value: Any, path: str) -> None:
    retry = _mapping(value, path)
    unknown = sorted(set(retry) - RETRY_FIELDS)
    if unknown:
        _fail(
            path,
            "unknown_retry_field",
            f"{path} has unknown execution field: {unknown[0]}",
        )
    attempts = retry.get("max_attempts", 1)
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 5
    ):
        _fail(
            f"{path}.max_attempts",
            "invalid_retry",
            f"{path}.max_attempts must be between 1 and 5",
        )
    if "delay_ms" in retry:
        delay = retry["delay_ms"]
        if (
            isinstance(delay, bool)
            or not isinstance(delay, int)
            or not 1000 <= delay <= 60_000
        ):
            _fail(
                f"{path}.delay_ms",
                "invalid_retry",
                f"{path}.delay_ms must be between 1000 and 60000",
            )
    if "on_error" in retry and retry["on_error"] not in {"transient", "all"}:
        _fail(f"{path}.on_error", "invalid_retry", f"{path}.on_error is invalid")


def _validate_hook_fields(hooks_value: Any, path: str) -> None:
    hooks = _mapping(hooks_value, path)
    for event, entries_value in hooks.items():
        if event not in HOOK_EVENTS:
            _fail(
                f"{path}.{event}", "unknown_hook_event", f"unknown hook event {event}"
            )
        if not isinstance(entries_value, list) or not entries_value:
            _fail(
                f"{path}.{event}",
                "invalid_hook",
                f"{path}.{event} must be a non-empty list",
            )
        for index, entry_value in enumerate(entries_value):
            entry_path = f"{path}.{event}[{index}]"
            entry = _mapping(entry_value, entry_path)
            unknown_entry = sorted(set(entry) - HOOK_ENTRY_FIELDS)
            if unknown_entry:
                _fail(
                    entry_path,
                    "unknown_hook_field",
                    f"{entry_path} has unknown execution field: {unknown_entry[0]}",
                )
            response = _mapping(entry.get("response"), f"{entry_path}.response")
            unknown_response = sorted(set(response) - HOOK_RESPONSE_FIELDS)
            if unknown_response:
                _fail(
                    entry_path,
                    "unknown_hook_response",
                    f"{entry_path}.response has unknown response field: {unknown_response[0]}",
                )
            specific_value = response.get("hookSpecificOutput")
            if specific_value is not None:
                specific = _mapping(
                    specific_value, f"{entry_path}.response.hookSpecificOutput"
                )
                unknown_specific = sorted(set(specific) - HOOK_SPECIFIC_FIELDS)
                if unknown_specific:
                    _fail(
                        entry_path,
                        "unknown_hook_response",
                        f"{entry_path} has unknown response field: {unknown_specific[0]}",
                    )
                if specific.get("hookEventName") != event:
                    _fail(
                        entry_path,
                        "hook_event_mismatch",
                        f"{entry_path} hookEventName must match {event}",
                    )
                for field in (
                    "permissionDecisionReason",
                    "additionalContext",
                ):
                    if field in specific:
                        _string(
                            specific[field],
                            f"{entry_path}.response.hookSpecificOutput.{field}",
                        )
                if "permissionDecision" in specific and specific[
                    "permissionDecision"
                ] not in {
                    "deny",
                    "allow",
                    "ask",
                }:
                    _fail(
                        f"{entry_path}.response.hookSpecificOutput.permissionDecision",
                        "invalid_hook_response",
                        "permissionDecision must be deny, allow, or ask",
                    )
                if "action" in specific and specific["action"] not in {
                    "accept",
                    "decline",
                    "cancel",
                }:
                    _fail(
                        f"{entry_path}.response.hookSpecificOutput.action",
                        "invalid_hook_response",
                        "hook action must be accept, decline, or cancel",
                    )
                if "updatedInput" in specific:
                    _mapping(
                        specific["updatedInput"],
                        f"{entry_path}.response.hookSpecificOutput.updatedInput",
                    )
            for field in ("systemMessage", "stopReason"):
                if field in response:
                    _string(response[field], f"{entry_path}.response.{field}")
            for field in ("continue", "suppressOutput"):
                if field in response:
                    _boolean(response[field], f"{entry_path}.response.{field}")
            if "decision" in response and response["decision"] not in {
                "approve",
                "block",
            }:
                _fail(
                    f"{entry_path}.response.decision",
                    "invalid_hook_response",
                    "hook decision must be approve or block",
                )
            if "timeout" in entry:
                _positive_number(entry["timeout"], f"{entry_path}.timeout")


def _validate_agents(value: Any, path: str) -> None:
    agents = _mapping(value, path)
    for agent_id, raw_agent in agents.items():
        agent_path = f"{path}.{agent_id}"
        if not isinstance(agent_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", agent_id
        ):
            _fail(
                agent_path,
                "invalid_agent_id",
                f"{agent_path} must use a kebab-case agent id",
            )
        agent = _mapping(raw_agent, agent_path)
        unknown = sorted(set(agent) - AGENT_FIELDS)
        if unknown:
            _fail(
                agent_path,
                "unknown_agent_field",
                f"{agent_path} has unknown execution field: {unknown[0]}",
            )
        _string(agent.get("description"), f"{agent_path}.description")
        _string(agent.get("prompt"), f"{agent_path}.prompt")
        if "model" in agent:
            _string(agent["model"], f"{agent_path}.model")
        for field in ("tools", "disallowedTools", "skills"):
            if field in agent:
                _string_list(agent[field], f"{agent_path}.{field}")
        if "maxTurns" in agent:
            turns = agent["maxTurns"]
            if isinstance(turns, bool) or not isinstance(turns, int) or turns <= 0:
                _fail(
                    f"{agent_path}.maxTurns",
                    "invalid_agent",
                    f"{agent_path}.maxTurns must be positive",
                )


def _validate_declared_options(node: Mapping[str, Any], path: str) -> None:
    for field in ("always_run", "persist_session"):
        if field in node:
            _boolean(node[field], f"{path}.{field}")
    if "output_type" in node:
        _string(
            node["output_type"],
            f"{path}.output_type",
            max_length=field_max_length("node", "output_type"),
        )
    for field in ("provider", "model", "systemPrompt", "fallbackModel"):
        if field in node:
            _string(node[field], f"{path}.{field}")
    if "output_format" in node:
        _mapping(node["output_format"], f"{path}.output_format")
    for field in ("allowed_tools", "denied_tools", "skills", "betas"):
        if field in node:
            _string_list(node[field], f"{path}.{field}")
    if "agents" in node:
        _validate_agents(node["agents"], f"{path}.agents")
    if "effort" in node and node["effort"] not in {"low", "medium", "high", "max"}:
        _fail(f"{path}.effort", "invalid_effort", f"{path}.effort is invalid")
    if "thinking" in node:
        _validate_thinking(node["thinking"], f"{path}.thinking")
    if "maxBudgetUsd" in node:
        _positive_number(node["maxBudgetUsd"], f"{path}.maxBudgetUsd")
    if "sandbox" in node:
        _mapping(node["sandbox"], f"{path}.sandbox")
    if "mcp" in node:
        mcp = _string(node["mcp"], f"{path}.mcp")
        _validate_relative_resource(mcp, f"{path}.mcp")


def _validate_node_type(node: Mapping[str, Any], node_type: str, path: str) -> None:
    value = node[node_type]
    if node_type in {"command", "prompt", "bash", "script", "cancel"}:
        _string(value, f"{path}.{node_type}")
    if node_type == "command" and not _SAFE_NAME.match(value):
        _validate_relative_resource(value, f"{path}.command")
    if node_type == "script":
        runtime = node.get("runtime")
        if runtime not in SCRIPT_RUNTIMES:
            _fail(
                f"{path}.runtime",
                "invalid_runtime",
                f"{path}.runtime must be bun or uv",
            )
        if isinstance(value, str) and not is_inline_script(value):
            _validate_relative_resource(value, f"{path}.script")
        deps = node.get("deps", [])
        if not isinstance(deps, list) or any(
            not isinstance(dep, str) or not dep for dep in deps
        ):
            _fail(
                f"{path}.deps",
                "invalid_deps",
                f"{path}.deps must be a list of dependency strings",
            )
    if node_type == "loop":
        loop = _mapping(value, f"{path}.loop")
        unknown = sorted(set(loop) - LOOP_FIELDS)
        if unknown:
            _fail(
                f"{path}.loop",
                "unknown_loop_field",
                f"{path}.loop has unknown execution field: {unknown[0]}",
            )
        _string(loop.get("prompt"), f"{path}.loop.prompt")
        _string(loop.get("until"), f"{path}.loop.until")
        iterations = loop.get("max_iterations")
        if (
            isinstance(iterations, bool)
            or not isinstance(iterations, int)
            or not 1 <= iterations <= 100
        ):
            _fail(
                f"{path}.loop.max_iterations",
                "invalid_loop",
                f"{path}.loop.max_iterations must be between 1 and 100",
            )
        if loop.get("interactive") is True and not loop.get("gate_message"):
            _fail(
                f"{path}.loop.gate_message",
                "invalid_loop",
                f"{path}.loop.gate_message is required when interactive",
            )
    if node_type == "approval":
        approval = _mapping(value, f"{path}.approval")
        unknown = sorted(set(approval) - APPROVAL_FIELDS)
        if unknown:
            _fail(
                f"{path}.approval",
                "unknown_approval_field",
                f"{path}.approval has unknown execution field: {unknown[0]}",
            )
        _string(approval.get("message"), f"{path}.approval.message")
        if "on_reject" in approval:
            on_reject = _mapping(approval["on_reject"], f"{path}.approval.on_reject")
            if set(on_reject) - APPROVAL_REJECT_FIELDS:
                _fail(
                    f"{path}.approval.on_reject",
                    "unknown_approval_field",
                    f"{path}.approval.on_reject has unknown execution field",
                )
            _string(on_reject.get("prompt"), f"{path}.approval.on_reject.prompt")
            attempts = on_reject.get("max_attempts", 3)
            if (
                isinstance(attempts, bool)
                or not isinstance(attempts, int)
                or not 1 <= attempts <= 10
            ):
                _fail(
                    f"{path}.approval.on_reject.max_attempts",
                    "invalid_approval",
                    f"{path}.approval.on_reject.max_attempts must be between 1 and 10",
                )


def _normalize_node(raw: Any, index: int, lines: dict[str, int]) -> WorkflowNode:
    path = f"nodes[{index}]"
    node = _mapping(raw, path)
    if "kind" in node:
        legacy_kind = node.get("kind")
        replacement = (
            f"replace `kind: {legacy_kind}` with the `{legacy_kind}: ...` node field"
            if isinstance(legacy_kind, str) and legacy_kind
            else "replace the legacy `kind` field with one supported node-type field"
        )
        _fail(
            f"{path}.kind",
            "legacy_kind_schema",
            f"legacy workflow node schema is unsupported; {replacement}",
            line=lines.get("kind"),
        )
    unknown = sorted(set(node) - COMMON_NODE_FIELDS)
    if unknown:
        _fail(
            path,
            "unknown_node_field",
            f"{path} has unknown execution field: {unknown[0]}",
            line=lines.get(unknown[0]),
        )
    node_id = _validate_identifier(node.get("id"), f"{path}.id")
    present_types = [field for field in NODE_TYPES if field in node]
    if len(present_types) != 1:
        _fail(path, "node_type_one_of", f"{path} must define exactly one node type")
    node_type = present_types[0]
    structurally_invalid = sorted(set(node) - structural_node_field_names(node_type))
    if structurally_invalid:
        field = structurally_invalid[0]
        _fail(
            f"{path}.{field}",
            "invalid_type_field",
            f"{path}.{field} is not structurally valid for {node_type} nodes",
            line=lines.get(field),
        )
    _validate_node_type(node, node_type, path)
    _validate_declared_options(node, path)
    depends = node.get("depends_on", [])
    if not isinstance(depends, list) or any(
        not isinstance(item, str) or not item for item in depends
    ):
        _fail(
            f"{path}.depends_on",
            "invalid_dependencies",
            f"{path}.depends_on must be a list of identifiers",
        )
    for dependency in depends:
        _validate_identifier(dependency, f"{path}.depends_on")
    trigger = node.get("trigger_rule", "all_success")
    if trigger not in TRIGGER_RULES:
        _fail(
            f"{path}.trigger_rule",
            "invalid_trigger_rule",
            f"{path}.trigger_rule is invalid",
        )
    if "context" in node and node["context"] not in CONTEXT_VALUES:
        _fail(
            f"{path}.context",
            "invalid_context",
            f"{path}.context must be fresh or shared",
        )
    for timeout_name in ("idle_timeout", "timeout"):
        if timeout_name in node:
            _positive_number(node[timeout_name], f"{path}.{timeout_name}")
    if "retry" in node:
        _validate_retry(node["retry"], f"{path}.retry")
    if "hooks" in node:
        _validate_hook_fields(node["hooks"], f"{path}.hooks")
    if "when" in node:
        when = _string(node["when"], f"{path}.when")
        if not _WHEN_EXPRESSION.fullmatch(when):
            _fail(
                f"{path}.when",
                "malformed_condition",
                f"{path}.when is a statically malformed condition",
            )
    options = {
        key: value
        for key, value in node.items()
        if key not in {"id", node_type, "depends_on"}
    }
    return WorkflowNode(
        id=node_id,
        node_type=node_type,
        value=freeze_value(node[node_type]),
        depends_on=tuple(depends),
        source_index=index,
        source_line=lines.get("id"),
        options=freeze_value(options),
    )


def _validate_graph(nodes: tuple[WorkflowNode, ...]) -> None:
    issues: list[ValidationIssue] = []
    by_id: dict[str, WorkflowNode] = {}
    for node in nodes:
        if node.id in by_id:
            issues.append(
                _issue(
                    f"nodes[{node.source_index}].id",
                    "duplicate_node_id",
                    f"duplicate node id: {node.id}",
                    line=node.source_line,
                )
            )
        by_id[node.id] = node
    for node in nodes:
        for dependency in node.depends_on:
            if dependency not in by_id:
                issues.append(
                    _issue(
                        f"nodes[{node.source_index}].depends_on",
                        "missing_dependency",
                        f"missing dependency {dependency} for node {node.id}",
                        line=node.source_line,
                    )
                )
        when = node.options.get("when")
        if isinstance(when, str):
            for reference in _WHEN_REFERENCE.findall(when):
                if reference not in by_id:
                    issues.append(
                        _issue(
                            f"nodes[{node.source_index}].when",
                            "missing_condition_reference",
                            f"missing condition reference {reference} for node {node.id}",
                            line=node.source_line,
                        )
                    )
    if issues:
        raise WorkflowValidationError(issues)
    indegree = {node.id: len(set(node.depends_on)) for node in nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        for dependency in set(node.depends_on):
            outgoing[dependency].append(node.id)
    ready = deque(node.id for node in nodes if indegree[node.id] == 0)
    visited = 0
    while ready:
        current = ready.popleft()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        _fail("nodes", "dependency_cycle", "workflow dependency graph contains a cycle")
    ancestors: dict[str, set[str]] = {}

    def collect_ancestors(node_id: str) -> set[str]:
        if node_id in ancestors:
            return ancestors[node_id]
        collected: set[str] = set()
        for dependency in by_id[node_id].depends_on:
            collected.add(dependency)
            collected.update(collect_ancestors(dependency))
        ancestors[node_id] = collected
        return collected

    upstream_issues: list[ValidationIssue] = []
    for node in nodes:
        when = node.options.get("when")
        if not isinstance(when, str):
            continue
        upstream = collect_ancestors(node.id)
        for reference in _WHEN_REFERENCE.findall(when):
            if reference in by_id and reference not in upstream:
                upstream_issues.append(
                    _issue(
                        f"nodes[{node.source_index}].when",
                        "condition_reference_not_upstream",
                        f"condition reference {reference} is not upstream of node {node.id}",
                        line=node.source_line,
                    )
                )
    if upstream_issues:
        raise WorkflowValidationError(upstream_issues)


def _validate_structured_output_field_references(
    nodes: tuple[WorkflowNode, ...],
    structured_outputs: Mapping[str, object],
    *,
    command_bodies: Mapping[str, str] | None = None,
) -> None:
    """Reject only field paths every normalized producer branch excludes."""
    issues: list[ValidationIssue] = []
    for node in nodes:
        for surface_path, template in _interpolated_node_templates(
            node, command_bodies=command_bodies
        ):
            for producer_id, path_parts in iter_output_field_references(template):
                output = structured_outputs.get(producer_id)
                if output is None:
                    continue
                schema = getattr(output, "canonical_schema", None)
                if isinstance(schema, Mapping) and prove_output_path_impossible(
                    schema, path_parts
                ):
                    issues.append(
                        _issue(
                            surface_path,
                            "structured_output_field_impossible",
                            f"structured output field {'.'.join(path_parts)} is impossible for node {producer_id}",
                            line=node.source_line,
                        )
                    )
    if issues:
        raise WorkflowValidationError(tuple(issues))


def _interpolated_node_templates(
    node: WorkflowNode,
    *,
    command_bodies: Mapping[str, str] | None,
) -> Iterable[tuple[str, str]]:
    """Yield only fields rendered by the Phase 2 runtime variable adapter."""
    prefix = f"nodes[{node.source_index}]"
    when = node.options.get("when")
    if isinstance(when, str):
        yield f"{prefix}.when", when
    if node.node_type in {"bash", "prompt"} and isinstance(node.value, str):
        yield f"{prefix}.{node.node_type}", node.value
    elif (
        node.node_type == "script"
        and isinstance(node.value, str)
        and is_inline_script(node.value)
    ):
        yield f"{prefix}.script", node.value
    elif node.node_type == "loop" and isinstance(node.value, Mapping):
        for field in ("prompt", "until_bash"):
            value = node.value.get(field)
            if isinstance(value, str):
                yield f"{prefix}.loop.{field}", value
    elif node.node_type == "approval" and isinstance(node.value, Mapping):
        message = node.value.get("message")
        if isinstance(message, str):
            yield f"{prefix}.approval.message", message
        on_reject = node.value.get("on_reject")
        if isinstance(on_reject, Mapping):
            prompt = on_reject.get("prompt")
            if isinstance(prompt, str):
                yield f"{prefix}.approval.on_reject.prompt", prompt
    elif node.node_type == "command" and command_bodies is not None:
        body = command_bodies.get(node.id)
        if isinstance(body, str):
            yield f"{prefix}.command", body


def validate_authenticated_command_references(
    package: WorkflowPackage,
    command_bodies: Mapping[str, str],
) -> None:
    """Validate command bodies already read from authenticated snapshot bytes."""
    if package.language.effective_profile is not WorkflowLanguageProfile.ARCHON_2026_07:
        return
    _validate_structured_output_field_references(
        package.definition.nodes,
        package.language.structured_outputs,
        command_bodies=command_bodies,
    )


def _parse_sidecar(
    sidecar_path: Path,
    data: bytes,
) -> tuple[Path, Mapping[str, Any]]:
    try:
        raw = yaml.load(data.decode("utf-8"), Loader=_WorkflowSafeLoader) or {}
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        _fail("sidecar", "invalid_sidecar", f"invalid workflow sidecar: {exc}")
    sidecar = _mapping(raw, "sidecar")
    forbidden = {
        key
        for key in sidecar
        if "trust" in str(key).lower()
        or str(key).lower() in {"nodes", "steps", "depends_on"}
    }
    if forbidden:
        _fail(
            "sidecar",
            "sidecar_authority",
            f"workflow sidecar cannot set trust or graph topology: {sorted(forbidden)[0]}",
        )
    unknown = sorted(set(sidecar) - SIDECAR_FIELDS)
    if unknown:
        _fail(
            "sidecar",
            "unknown_sidecar_field",
            f"workflow sidecar has unknown execution field: {unknown[0]}",
        )
    limits = sidecar.get("limits", {})
    resources = sidecar.get("resource_limits", {})
    if not isinstance(limits, Mapping) or not isinstance(resources, Mapping):
        _fail(
            "sidecar.limits",
            "invalid_sidecar",
            "workflow sidecar limits and resource_limits must be mappings",
        )
    try:
        WorkflowRuntimeConfig.from_mapping(
            sidecar_limits=limits,
            sidecar_resources=resources,
        )
    except (TypeError, ValueError) as exc:
        _fail("sidecar.limits", "invalid_sidecar", str(exc))
    outward = sidecar.get("outward_action_nodes", [])
    if not isinstance(outward, list) or any(
        not isinstance(item, str) for item in outward
    ):
        _fail(
            "sidecar.outward_action_nodes",
            "invalid_sidecar",
            "outward_action_nodes must be a list of node identifiers",
        )
    if "required_secrets" in sidecar:
        secrets = sidecar["required_secrets"]
        if not isinstance(secrets, list) or any(
            not isinstance(item, str) or not item for item in secrets
        ):
            _fail(
                "sidecar.required_secrets",
                "invalid_sidecar",
                "required_secrets must name secrets without values",
            )
    if sidecar.get("execution_environment") not in {
        None,
        "trusted_local",
        "isolated_backend_required",
    }:
        _fail(
            "sidecar.execution_environment",
            "invalid_sidecar",
            "execution_environment must be trusted_local or isolated_backend_required",
        )
    pause_lane_policy = sidecar.get("pause_lane_policy", "hold")
    if pause_lane_policy not in {"hold", "release"}:
        _fail(
            "sidecar.pause_lane_policy",
            "invalid_sidecar",
            "pause_lane_policy must be hold or release",
        )
    if (
        "pause_lane_policy" in sidecar
        and sidecar.get("overlap_policy", "queue") != "queue"
    ):
        _fail(
            "sidecar.pause_lane_policy",
            "invalid_sidecar",
            "pause_lane_policy requires queue overlap_policy",
        )
    return sidecar_path, freeze_value(sidecar)


def _validate_sidecar_node_references(
    sidecar: Mapping[str, Any], node_ids: frozenset[str]
) -> None:
    for node_id in sidecar.get("outward_action_nodes", ()):
        if node_id not in node_ids:
            _fail(
                "sidecar.outward_action_nodes",
                "unknown_sidecar_node",
                f"outward_action_nodes references unknown node: {node_id}",
            )


def _load_sidecar(path: Path) -> tuple[Path | None, Mapping[str, Any]]:
    sidecar_path = path.with_name(f"{path.stem}.hermes.yaml")
    if not sidecar_path.is_file():
        return None, freeze_value({})
    try:
        data = sidecar_path.read_bytes()
    except OSError as exc:
        _fail("sidecar", "invalid_sidecar", f"invalid workflow sidecar: {exc}")
    return _parse_sidecar(sidecar_path, data)


def _package_root(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == "workflows":
            return parent.parent
    return path.parent


def _validate_workflow_options(document: Mapping[str, Any]) -> None:
    for field in (
        "provider",
        "model",
        "modelReasoningEffort",
        "webSearchMode",
        "fallbackModel",
    ):
        if field in document:
            _string(document[field], field)
    for field in ("interactive", "persist_sessions"):
        if field in document:
            _boolean(document[field], field)
    for field in ("requires", "tags", "betas"):
        if field in document:
            _string_list(document[field], field)
    if "worktree" in document:
        worktree = _mapping(document["worktree"], "worktree")
        unknown = sorted(set(worktree) - {"enabled"})
        if unknown or "enabled" not in worktree:
            _fail("worktree", "invalid_worktree", "worktree must contain only enabled")
        _boolean(worktree["enabled"], "worktree.enabled")
    if "effort" in document and document["effort"] not in {
        "low",
        "medium",
        "high",
        "max",
    }:
        _fail("effort", "invalid_effort", "effort is invalid")
    if "thinking" in document:
        _validate_thinking(document["thinking"], "thinking")
    if "sandbox" in document:
        _mapping(document["sandbox"], "sandbox")


_READ_SIDECAR_FROM_DISK = object()


def _load_workflow_bytes(
    workflow_path: Path,
    data: bytes,
    *,
    sidecar_bytes: bytes | None | object,
    source: str,
    precedence: int,
    normalizer_version: int = WORKFLOW_NORMALIZER_VERSION,
) -> WorkflowPackage:
    if len(data) > MAX_WORKFLOW_DOCUMENT_BYTES:
        _fail(
            "path",
            "workflow_too_large",
            "workflow YAML exceeds the 2 MiB validation limit",
        )
    try:
        text = data.decode("utf-8")
        raw = yaml.load(text, Loader=_WorkflowSafeLoader)
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        _fail("document", "invalid_yaml", f"invalid workflow YAML: {exc}")
    document = _mapping(raw, "document")
    top_lines, node_lines = _source_lines(text)
    if "steps" in document:
        _fail(
            "steps",
            "removed_steps",
            "steps has been removed; use nodes",
            line=top_lines.get("steps"),
        )
    forbidden = {"trust", "trusted", "package_trusted"}.intersection(document)
    if forbidden:
        _fail(
            str(next(iter(forbidden))),
            "self_trust",
            "workflow package cannot declare trust",
        )
    if sidecar_bytes is _READ_SIDECAR_FROM_DISK:
        sidecar_path, sidecar = _load_sidecar(workflow_path)
    elif sidecar_bytes is None:
        sidecar_path, sidecar = None, freeze_value({})
    else:
        assert isinstance(sidecar_bytes, bytes)
        sidecar_path, sidecar = _parse_sidecar(
            workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml"),
            sidecar_bytes,
        )
    try:
        selection = resolve_language_profile(sidecar)
    except WorkflowLanguageCompatibilityError as exc:
        _fail("sidecar.language_compatibility", exc.code, str(exc))
    unknown_top = sorted(set(document) - TOP_LEVEL_FIELDS)
    if (
        unknown_top
        and selection.effective_profile is WorkflowLanguageProfile.ARCHON_2026_07
    ):
        raise WorkflowValidationError(
            tuple(
                ValidationIssue(
                    path=field,
                    code=ARCHON_UNKNOWN_TOP_LEVEL_FIELD_CODE,
                    message=f"Archon profile does not support top-level field: {field}",
                    source_line=top_lines.get(field),
                )
                for field in unknown_top
            )
        )
    issues = tuple(
        ValidationIssue(
            path=field,
            code=UNKNOWN_TOP_LEVEL_FIELD_CODE,
            message=f"unknown top-level field: {field}",
            severity="warning",
            blocking=False,
            source_line=top_lines.get(field),
        )
        for field in unknown_top
    )
    name = _validate_identifier(document.get("name"), "name")
    if not _SAFE_NAME.fullmatch(name):
        _fail(
            "name",
            "invalid_workflow_name",
            "name must be a portable identifier without path separators",
        )
    description = _string(document.get("description"), "description")
    _validate_workflow_options(document)
    raw_nodes = document.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        _fail("nodes", "invalid_nodes", "nodes must be a non-empty list")
    nodes = tuple(
        _normalize_node(
            node, index, node_lines[index] if index < len(node_lines) else {}
        )
        for index, node in enumerate(raw_nodes)
    )
    _validate_graph(nodes)
    options = {
        key: value
        for key, value in document.items()
        if key not in {"name", "description", "nodes"}
    }
    definition = WorkflowDefinition(
        name=name,
        description=description,
        nodes=nodes,
        options=freeze_value(options),
        source_path=workflow_path,
    )
    node_ids = frozenset(node.id for node in nodes)
    _validate_sidecar_node_references(sidecar, node_ids)
    try:
        normalized = normalize_workflow(
            definition,
            selection=selection,
            normalizer_version=normalizer_version,
        )
    except WorkflowStructuredOutputNormalizationError as exc:
        _fail(
            f"nodes[{exc.source_index}].output_format",
            "invalid_output_format",
            "output_format is not a valid bounded structured-output schema",
        )
    _validate_structured_output_field_references(
        normalized.definition.nodes, normalized.metadata.structured_outputs
    )
    return WorkflowPackage(
        source_definition=definition,
        definition=normalized.definition,
        root=_package_root(workflow_path),
        workflow_path=workflow_path,
        sidecar_path=sidecar_path,
        sidecar=sidecar,
        source=source,
        precedence=precedence,
        language=normalized.metadata,
        compatibility_findings=language_compatibility_findings(
            definition, normalized.metadata
        ),
        validation_issues=issues,
    )


def load_workflow(
    path: str | Path, *, source: str = "explicit", precedence: int = 0
) -> WorkflowPackage:
    """Load a portable workflow into immutable, validated contracts."""
    workflow_path = Path(path).expanduser().resolve(strict=True)
    if not workflow_path.is_file() or workflow_path.suffix.lower() not in {
        ".yaml",
        ".yml",
    }:
        _fail("path", "invalid_workflow_path", "workflow path must be a YAML file")
    return _load_workflow_bytes(
        workflow_path,
        workflow_path.read_bytes(),
        sidecar_bytes=_READ_SIDECAR_FROM_DISK,
        source=source,
        precedence=precedence,
    )


def load_workflow_snapshot(
    path: str | Path,
    *,
    workflow_bytes: bytes,
    sidecar_bytes: bytes | None,
    source: str = "explicit",
    precedence: int = 0,
    normalizer_version: int = WORKFLOW_NORMALIZER_VERSION,
) -> WorkflowPackage:
    """Parse caller-authenticated bytes without reopening definition files."""
    workflow_path = Path(path).expanduser().absolute()
    if workflow_path.suffix.lower() not in {".yaml", ".yml"}:
        _fail("path", "invalid_workflow_path", "workflow path must be a YAML file")
    return _load_workflow_bytes(
        workflow_path,
        workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
        normalizer_version=normalizer_version,
    )


def validate_package(package: WorkflowPackage) -> tuple[ValidationIssue, ...]:
    """Return deterministic non-fatal package diagnostics.

    Blocking schema and graph problems are raised by :func:`load_workflow`.
    """
    return package.validation_issues
