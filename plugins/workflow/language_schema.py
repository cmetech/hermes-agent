"""Dependency-neutral workflow field inventory and authoring schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from plugins.workflow.language import WORKFLOW_NORMALIZER_VERSION
from plugins.workflow.models import WorkflowLanguageProfile


_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_PROFILES = tuple(WorkflowLanguageProfile)


@dataclass(frozen=True, slots=True)
class FieldCompatibility:
    """One field's stable authoring status for a language profile."""

    profile: WorkflowLanguageProfile
    status: str
    code: str | None


@dataclass(frozen=True, slots=True)
class WorkflowFieldSpec:
    """Immutable syntax and compatibility authority for one YAML field."""

    scope: str
    yaml_name: str
    json_type: str
    shape: str
    structural_node_types: frozenset[str]
    applicable_node_types: frozenset[str]
    enforcement_phase: int
    compatibility: tuple[FieldCompatibility, ...]
    required: bool
    required_node_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class StructuralRequirement:
    """A JSON-Schema requirement derived from loader structure."""

    scope: str
    when_field: str
    equals: object
    required_field: str
    required_shape: str


@dataclass(frozen=True, slots=True)
class DynamicCompatibilityCode:
    """A stable loader/profile code not attached to one inventory field."""

    code: str
    profiles: frozenset[WorkflowLanguageProfile]
    status: str
    enforcement_phase: int
    fields: tuple[str, ...]


def _compatibility(
    *,
    legacy_status: str = "supported",
    legacy_code: str | None = None,
    archon_status: str = "supported",
    archon_code: str | None = None,
) -> tuple[FieldCompatibility, ...]:
    return (
        FieldCompatibility(
            WorkflowLanguageProfile.HERMES_LEGACY,
            legacy_status,
            legacy_code,
        ),
        FieldCompatibility(
            WorkflowLanguageProfile.ARCHON_2026_07,
            archon_status,
            archon_code,
        ),
    )


def _field(
    scope: str,
    yaml_name: str,
    json_type: str,
    shape: str,
    *,
    node_types: tuple[str, ...] = (),
    structural_node_types: tuple[str, ...] | None = None,
    required: bool = False,
    required_node_types: tuple[str, ...] = (),
    phase: int = 1,
    legacy_status: str = "supported",
    legacy_code: str | None = None,
    archon_status: str = "supported",
    archon_code: str | None = None,
) -> WorkflowFieldSpec:
    return WorkflowFieldSpec(
        scope=scope,
        yaml_name=yaml_name,
        json_type=json_type,
        shape=shape,
        structural_node_types=frozenset(
            node_types if structural_node_types is None else structural_node_types
        ),
        applicable_node_types=frozenset(node_types),
        enforcement_phase=phase,
        compatibility=_compatibility(
            legacy_status=legacy_status,
            legacy_code=legacy_code,
            archon_status=archon_status,
            archon_code=archon_code,
        ),
        required=required,
        required_node_types=frozenset(required_node_types),
    )


NODE_TYPES = ("command", "prompt", "bash", "script", "loop", "approval", "cancel")
_AI_NODE_TYPES = ("command", "prompt")
_NON_LOOP_NODE_TYPES = tuple(item for item in NODE_TYPES if item != "loop")


_DYNAMIC_COMPATIBILITY_CODES = (
    DynamicCompatibilityCode(
        code="workflow_language_profile_unsupported",
        profiles=frozenset(_PROFILES),
        status="blocking",
        enforcement_phase=1,
        fields=("sidecar.language_compatibility",),
    ),
    DynamicCompatibilityCode(
        code="workflow_normalizer_version_unsupported",
        profiles=frozenset(_PROFILES),
        status="blocking",
        enforcement_phase=1,
        fields=("normalizer_version",),
    ),
    DynamicCompatibilityCode(
        code="unknown_top_level_field",
        profiles=frozenset({WorkflowLanguageProfile.HERMES_LEGACY}),
        status="warning",
        enforcement_phase=1,
        fields=("*",),
    ),
    DynamicCompatibilityCode(
        code="archon_unknown_top_level_field",
        profiles=frozenset({WorkflowLanguageProfile.ARCHON_2026_07}),
        status="blocking",
        enforcement_phase=1,
        fields=("*",),
    ),
)


_DEFINITION_FIELDS = (
    _field("definition", "name", "string", "nonempty_string", required=True),
    _field("definition", "description", "string", "nonempty_string", required=True),
    _field("definition", "nodes", "array", "nodes", required=True),
    _field("definition", "provider", "string", "nonempty_string"),
    _field("definition", "model", "string", "nonempty_string"),
    _field("definition", "modelReasoningEffort", "string", "nonempty_string"),
    _field("definition", "webSearchMode", "string", "nonempty_string"),
    _field("definition", "interactive", "boolean", "boolean"),
    _field("definition", "requires", "array", "string_list"),
    _field("definition", "worktree", "object", "worktree"),
    _field("definition", "tags", "array", "string_list"),
    _field("definition", "persist_sessions", "boolean", "boolean"),
    _field("definition", "effort", "string", "effort"),
    _field("definition", "thinking", "string or object", "thinking"),
    _field("definition", "fallbackModel", "string", "nonempty_string"),
    _field("definition", "betas", "array", "string_list"),
    _field(
        "definition",
        "sandbox",
        "object",
        "mapping",
        phase=5,
        archon_status="blocking",
        archon_code="archon_sandbox_enforcement_unavailable",
    ),
)


_NODE_FIELDS = (
    _field(
        "node",
        "id",
        "string",
        "nonempty_string",
        node_types=NODE_TYPES,
        required_node_types=NODE_TYPES,
    ),
    *(
        _field(
            "node",
            node_type,
            "object" if node_type in {"loop", "approval"} else "string",
            f"{node_type}_payload",
            node_types=(node_type,),
            required_node_types=(node_type,),
        )
        for node_type in NODE_TYPES
    ),
    _field("node", "depends_on", "array", "string_list", node_types=NODE_TYPES),
    _field("node", "when", "string", "nonempty_string", node_types=NODE_TYPES),
    _field("node", "trigger_rule", "string", "trigger_rule", node_types=NODE_TYPES),
    _field("node", "context", "string", "context", node_types=NODE_TYPES),
    _field(
        "node",
        "idle_timeout",
        "number",
        "positive_number",
        node_types=NODE_TYPES,
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_idle_timeout_seconds",
        archon_status="blocking",
        archon_code="archon_idle_timeout_semantics_unavailable",
    ),
    _field(
        "node",
        "retry",
        "object",
        "retry",
        node_types=_NON_LOOP_NODE_TYPES,
        phase=3,
        archon_status="blocking",
        archon_code="archon_retry_semantics_unavailable",
    ),
    _field("node", "always_run", "boolean", "boolean", node_types=NODE_TYPES),
    _field(
        "node",
        "output_type",
        "string",
        "nonempty_string",
        node_types=NODE_TYPES,
        phase=2,
        legacy_status="warning",
        legacy_code="legacy_output_type_not_published",
        archon_status="blocking",
        archon_code="archon_output_type_unavailable",
    ),
    _field(
        "node",
        "persist_session",
        "boolean",
        "boolean",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "provider",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "model",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "output_format",
        "object",
        "mapping",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=2,
        legacy_status="warning",
        legacy_code="legacy_output_format_post_validation",
        archon_status="blocking",
        archon_code="archon_output_format_unavailable",
    ),
    _field(
        "node",
        "allowed_tools",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "denied_tools",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "hooks",
        "object",
        "hooks",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "mcp",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "skills",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "agents",
        "object",
        "agents",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "effort",
        "string",
        "effort",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "thinking",
        "string or object",
        "thinking",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "maxBudgetUsd",
        "number",
        "positive_number",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=5,
        archon_status="blocking",
        archon_code="archon_budget_enforcement_unavailable",
    ),
    _field(
        "node",
        "systemPrompt",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "fallbackModel",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "betas",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "sandbox",
        "object",
        "mapping",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=5,
        archon_status="blocking",
        archon_code="archon_sandbox_enforcement_unavailable",
    ),
    _field(
        "node",
        "runtime",
        "string",
        "runtime",
        node_types=("script",),
        required_node_types=("script",),
    ),
    _field("node", "deps", "array", "string_list", node_types=("script",)),
    _field(
        "node",
        "timeout",
        "number",
        "positive_number",
        node_types=("bash", "script"),
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_timeout_seconds",
        archon_status="blocking",
        archon_code="archon_timeout_semantics_unavailable",
    ),
)


_RETRY_FIELDS = (
    _field(
        "retry",
        "max_attempts",
        "integer",
        "retry_attempts",
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_retry_total_attempts",
    ),
    _field("retry", "on_error", "string", "retry_error"),
    _field("retry", "delay_ms", "integer", "retry_delay"),
)
_LOOP_FIELDS = (
    _field("loop", "prompt", "string", "nonempty_string", required=True),
    _field("loop", "until", "string", "nonempty_string", required=True),
    _field("loop", "max_iterations", "integer", "loop_iterations", required=True),
    _field("loop", "fresh_context", "any", "any"),
    _field("loop", "until_bash", "any", "any"),
    _field("loop", "interactive", "any", "any"),
    _field("loop", "gate_message", "any", "any"),
)
_APPROVAL_FIELDS = (
    _field("approval", "message", "string", "nonempty_string", required=True),
    _field("approval", "capture_response", "any", "any"),
    _field("approval", "on_reject", "object", "approval_reject"),
)
_APPROVAL_REJECT_FIELDS = (
    _field("approval_reject", "prompt", "string", "nonempty_string", required=True),
    _field("approval_reject", "max_attempts", "integer", "approval_attempts"),
)
_AGENT_FIELDS = tuple(
    _field(
        "agent",
        name,
        json_type,
        shape,
        required=name in {"description", "prompt"},
    )
    for name, json_type, shape in (
        ("description", "string", "nonempty_string"),
        ("prompt", "string", "nonempty_string"),
        ("model", "string", "nonempty_string"),
        ("tools", "array", "string_list"),
        ("disallowedTools", "array", "string_list"),
        ("skills", "array", "string_list"),
        ("maxTurns", "integer", "positive_integer"),
    )
)


_HOOK_EVENT_NAMES = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "Setup",
    "TeammateIdle",
    "TaskCompleted",
    "Elicitation",
    "ElicitationResult",
    "InstructionsLoaded",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
)
_HOOK_EVENT_FIELDS = tuple(
    _field("hook_event", event, "array", "hook_entries", node_types=_AI_NODE_TYPES)
    for event in _HOOK_EVENT_NAMES
)
_HOOK_ENTRY_FIELDS = (
    _field("hook_entry", "matcher", "any", "any"),
    _field("hook_entry", "response", "object", "hook_response", required=True),
    _field("hook_entry", "timeout", "number", "positive_number"),
)
_HOOK_RESPONSE_FIELDS = tuple(
    _field("hook_response", name, json_type, shape)
    for name, json_type, shape in (
        ("hookSpecificOutput", "object or null", "nullable_hook_specific"),
        ("systemMessage", "string", "nonempty_string"),
        ("continue", "boolean", "boolean"),
        ("decision", "string", "hook_decision"),
        ("stopReason", "string", "nonempty_string"),
        ("suppressOutput", "boolean", "boolean"),
    )
)
_HOOK_SPECIFIC_FIELDS = tuple(
    _field(
        "hook_specific",
        name,
        json_type,
        shape,
        required=name == "hookEventName",
    )
    for name, json_type, shape in (
        ("hookEventName", "string", "hook_event_name"),
        ("permissionDecision", "string", "permission_decision"),
        ("permissionDecisionReason", "string", "nonempty_string"),
        ("updatedInput", "object", "mapping"),
        ("additionalContext", "string", "nonempty_string"),
        ("updatedMCPToolOutput", "any", "any"),
        ("action", "string", "hook_action"),
        ("content", "any", "any"),
    )
)


_SIDECAR_FIELDS = tuple(
    _field(
        "sidecar",
        name,
        json_type,
        shape,
        legacy_status=("warning" if name == "language_compatibility" else "supported"),
        legacy_code=(
            "legacy_language_profile" if name == "language_compatibility" else None
        ),
    )
    for name, json_type, shape in (
        ("language_compatibility", "string", "language_profile"),
        ("delivery_defaults", "object", "mapping"),
        ("required_services", "array", "string_list"),
        ("retention", "object", "mapping"),
        ("tags", "array", "string_list"),
        ("outward_action_nodes", "array", "string_list"),
        ("outward_action_policy", "string", "nonempty_string"),
        ("execution_environment", "string", "execution_environment"),
        ("overlap_policy", "string", "overlap_policy"),
        ("pause_lane_policy", "string", "pause_lane_policy"),
        ("concurrency_key", "string", "nonempty_string"),
        ("limits", "object", "mapping"),
        ("resource_limits", "object", "mapping"),
        ("required_secrets", "array", "string_list"),
        ("scheduling", "object", "mapping"),
    )
)


FIELD_INVENTORY = (
    *_DEFINITION_FIELDS,
    *_NODE_FIELDS,
    *_RETRY_FIELDS,
    *_LOOP_FIELDS,
    *_APPROVAL_FIELDS,
    *_APPROVAL_REJECT_FIELDS,
    *_AGENT_FIELDS,
    *_HOOK_EVENT_FIELDS,
    *_HOOK_ENTRY_FIELDS,
    *_HOOK_RESPONSE_FIELDS,
    *_HOOK_SPECIFIC_FIELDS,
    *_SIDECAR_FIELDS,
)

STRUCTURAL_REQUIREMENTS = (
    StructuralRequirement(
        scope="loop",
        when_field="interactive",
        equals=True,
        required_field="gate_message",
        required_shape="json_truthy",
    ),
)


def _field_names(scope: str) -> frozenset[str]:
    return frozenset(spec.yaml_name for spec in FIELD_INVENTORY if spec.scope == scope)


def definition_field_names() -> frozenset[str]:
    return _field_names("definition")


def common_node_field_names() -> frozenset[str]:
    return _field_names("node")


def structural_node_field_names(node_type: str) -> frozenset[str]:
    """Return node fields the loader and JSON Schema accept structurally."""
    if node_type not in NODE_TYPES:
        raise ValueError(f"unsupported workflow node type: {node_type}")
    return frozenset(
        spec.yaml_name
        for spec in _specs("node")
        if node_type in spec.structural_node_types
    )


def sidecar_field_names() -> frozenset[str]:
    return _field_names("sidecar")


def retry_field_names() -> frozenset[str]:
    return _field_names("retry")


def loop_field_names() -> frozenset[str]:
    return _field_names("loop")


def approval_field_names() -> frozenset[str]:
    return _field_names("approval")


def approval_reject_field_names() -> frozenset[str]:
    return _field_names("approval_reject")


def agent_field_names() -> frozenset[str]:
    return _field_names("agent")


def hook_event_names() -> frozenset[str]:
    return _field_names("hook_event")


def hook_entry_field_names() -> frozenset[str]:
    return _field_names("hook_entry")


def hook_response_field_names() -> frozenset[str]:
    return _field_names("hook_response")


def hook_specific_field_names() -> frozenset[str]:
    return _field_names("hook_specific")


def _specs(scope: str) -> tuple[WorkflowFieldSpec, ...]:
    return tuple(spec for spec in FIELD_INVENTORY if spec.scope == scope)


def _profile(value: WorkflowLanguageProfile) -> WorkflowLanguageProfile:
    if not isinstance(value, WorkflowLanguageProfile):
        raise TypeError("profile must be a WorkflowLanguageProfile")
    return value


def _field_status(
    spec: WorkflowFieldSpec, profile: WorkflowLanguageProfile
) -> FieldCompatibility:
    return next(item for item in spec.compatibility if item.profile is profile)


def _json_truthy_schema() -> dict[str, Any]:
    """Describe exactly the JSON values accepted by Python truth testing."""
    return {
        "oneOf": [
            {"const": True},
            {"type": "number", "not": {"const": 0}},
            {"type": "string", "minLength": 1},
            {"type": "array", "minItems": 1},
            {"type": "object", "minProperties": 1},
        ]
    }


def _schema_for_shape(
    shape: str,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    if shape == "any":
        return {}
    if shape == "json_truthy":
        return _json_truthy_schema()
    if shape == "string":
        return {"type": "string"}
    if shape == "nonempty_string":
        return {"type": "string", "minLength": 1}
    if shape == "boolean":
        return {"type": "boolean"}
    if shape == "positive_number":
        return {"type": "number", "exclusiveMinimum": 0}
    if shape == "positive_integer":
        return {"type": "integer", "minimum": 1}
    if shape == "string_list":
        return {"type": "array", "items": {"type": "string", "minLength": 1}}
    if shape == "mapping":
        return {"type": "object"}
    if shape == "worktree":
        return {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        }
    if shape == "effort":
        return {"type": "string", "enum": ["low", "medium", "high", "max"]}
    if shape == "thinking":
        return {
            "oneOf": [
                {"type": "string", "enum": ["adaptive", "disabled"]},
                {
                    "type": "object",
                    "properties": {
                        "type": {"const": "enabled"},
                        "budgetTokens": {"type": "integer", "minimum": 1},
                    },
                    "required": ["type", "budgetTokens"],
                    "additionalProperties": False,
                },
            ]
        }
    if shape == "trigger_rule":
        return {
            "type": "string",
            "enum": [
                "all_success",
                "one_success",
                "none_failed_min_one_success",
                "all_done",
            ],
        }
    if shape == "context":
        return {"type": "string", "enum": ["fresh", "shared"]}
    if shape == "runtime":
        return {"type": "string", "enum": ["bun", "uv"]}
    if shape == "retry_attempts":
        return {"type": "integer", "minimum": 1, "maximum": 5}
    if shape == "retry_delay":
        return {"type": "integer", "minimum": 1000, "maximum": 60_000}
    if shape == "retry_error":
        return {"type": "string", "enum": ["transient", "all"]}
    if shape == "loop_iterations":
        return {"type": "integer", "minimum": 1, "maximum": 100}
    if shape == "approval_attempts":
        return {"type": "integer", "minimum": 1, "maximum": 10}
    if shape == "hook_decision":
        return {"type": "string", "enum": ["approve", "block"]}
    if shape == "permission_decision":
        return {"type": "string", "enum": ["deny", "allow", "ask"]}
    if shape == "hook_action":
        return {"type": "string", "enum": ["accept", "decline", "cancel"]}
    if shape == "hook_event_name":
        if hook_event is not None:
            return {"const": hook_event}
        return {"type": "string", "enum": list(_HOOK_EVENT_NAMES)}
    if shape == "language_profile":
        return {"type": "string", "enum": [item.value for item in _PROFILES]}
    if shape == "execution_environment":
        return {
            "type": "string",
            "enum": ["trusted_local", "isolated_backend_required"],
        }
    if shape == "overlap_policy":
        return {"type": "string", "enum": ["queue", "allow", "forbid"]}
    if shape == "pause_lane_policy":
        return {"type": "string", "enum": ["hold", "release"]}
    if shape == "retry":
        return _object_schema("retry", profile)
    if shape == "hooks":
        return _object_schema("hook_event", profile)
    if shape == "hook_entries":
        return {
            "type": "array",
            "minItems": 1,
            "items": _object_schema("hook_entry", profile, hook_event=hook_event),
        }
    if shape == "hook_response":
        return _object_schema("hook_response", profile, hook_event=hook_event)
    if shape == "hook_specific":
        return _object_schema("hook_specific", profile, hook_event=hook_event)
    if shape == "nullable_hook_specific":
        return {
            "oneOf": [
                {"type": "null"},
                _object_schema("hook_specific", profile, hook_event=hook_event),
            ]
        }
    if shape == "agents":
        return {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
            "additionalProperties": _object_schema("agent", profile),
        }
    if shape == "nodes":
        return _nodes_schema(profile)
    if shape == "loop_payload":
        return _object_schema("loop", profile)
    if shape == "approval_payload":
        return _object_schema("approval", profile)
    if shape == "approval_reject":
        return _object_schema("approval_reject", profile)
    if shape.endswith("_payload"):
        return {"type": "string", "minLength": 1}
    raise ValueError(f"unknown workflow field shape: {shape}")


def _field_schema(
    spec: WorkflowFieldSpec,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    result = _schema_for_shape(spec.shape, profile, hook_event=hook_event)
    status = _field_status(spec, profile)
    if status.status != "supported":
        result["x-hermes-status"] = status.status
        result["x-hermes-compatibility-code"] = status.code
        result["x-hermes-enforcement-phase"] = spec.enforcement_phase
    return result


def _object_schema(
    scope: str,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    specs = _specs(scope)
    result: dict[str, Any] = {
        "type": "object",
        "properties": {
            spec.yaml_name: _field_schema(
                spec,
                profile,
                hook_event=(spec.yaml_name if scope == "hook_event" else hook_event),
            )
            for spec in specs
        },
        "additionalProperties": False,
    }
    required = tuple(spec.yaml_name for spec in specs if spec.required)
    if required:
        result["required"] = list(required)
    conditions = tuple(item for item in STRUCTURAL_REQUIREMENTS if item.scope == scope)
    if conditions:
        result["allOf"] = [
            {
                "if": {
                    "properties": {
                        item.when_field: {"const": item.equals},
                    },
                    "required": [item.when_field],
                },
                "then": {
                    "required": [item.required_field],
                    "properties": {
                        item.required_field: _schema_for_shape(
                            item.required_shape, profile
                        )
                    },
                },
            }
            for item in conditions
        ]
    return result


def _nodes_schema(profile: WorkflowLanguageProfile) -> dict[str, Any]:
    specs = _specs("node")
    union_properties = {spec.yaml_name: _field_schema(spec, profile) for spec in specs}
    variants = []
    for node_type in NODE_TYPES:
        properties = {
            spec.yaml_name: _field_schema(spec, profile)
            for spec in specs
            if node_type in spec.structural_node_types
        }
        variants.append({
            "type": "object",
            "properties": properties,
            "required": [
                spec.yaml_name
                for spec in specs
                if node_type in spec.required_node_types
            ],
            "additionalProperties": False,
        })
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": union_properties,
            "oneOf": variants,
            "additionalProperties": False,
        },
    }


def definition_json_schema(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return the deterministic definition authoring schema for ``profile``."""
    selected = _profile(profile)
    return {
        "$schema": _DRAFT_2020_12,
        "$id": f"https://hermes.local/workflow/{selected.value}/definition.schema.json",
        "title": f"Hermes workflow definition ({selected.value})",
        "description": (
            "Structural authoring schema. The workflow loader remains authoritative "
            "for graph references, resource paths, provider capabilities, and runtime "
            "compatibility checks that are not structural JSON constraints."
        ),
        "type": "object",
        "properties": {
            spec.yaml_name: _field_schema(spec, selected)
            for spec in _specs("definition")
        },
        "required": [spec.yaml_name for spec in _specs("definition") if spec.required],
        "additionalProperties": (selected is WorkflowLanguageProfile.HERMES_LEGACY),
    }


def sidecar_json_schema(profile: WorkflowLanguageProfile) -> dict[str, object]:
    """Return the strict Hermes companion-file authoring schema."""
    selected = _profile(profile)
    schema = _object_schema("sidecar", selected)
    return {
        "$schema": _DRAFT_2020_12,
        "$id": f"https://hermes.local/workflow/{selected.value}/companion.schema.json",
        "title": f"Hermes workflow companion ({selected.value})",
        **schema,
    }


def _contract_path(spec: WorkflowFieldSpec) -> str:
    if spec.scope == "definition":
        return spec.yaml_name
    if spec.scope == "node":
        return f"nodes[].{spec.yaml_name}"
    if spec.scope == "sidecar":
        return f"sidecar.{spec.yaml_name}"
    if spec.scope == "retry":
        return f"nodes[].retry.{spec.yaml_name}"
    return f"{spec.scope}.{spec.yaml_name}"


def compatibility_code_catalog(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return stable profile-specific codes referenced by schema annotations."""
    selected = _profile(profile)
    grouped: dict[str, dict[str, object]] = {}
    for spec in FIELD_INVENTORY:
        status = _field_status(spec, selected)
        if status.code is None or status.status == "supported":
            continue
        entry = grouped.setdefault(
            status.code,
            {
                "status": status.status,
                "severity": "error" if status.status == "blocking" else "warning",
                "blocking": status.status == "blocking",
                "enforcement_phase": spec.enforcement_phase,
                "fields": [],
            },
        )
        fields = entry["fields"]
        assert isinstance(fields, list)
        fields.append(_contract_path(spec))
    for spec in _DYNAMIC_COMPATIBILITY_CODES:
        if selected not in spec.profiles:
            continue
        grouped[spec.code] = {
            "status": spec.status,
            "severity": "error" if spec.status == "blocking" else "warning",
            "blocking": spec.status == "blocking",
            "enforcement_phase": spec.enforcement_phase,
            "fields": list(spec.fields),
        }
    return {
        code: {**entry, "fields": sorted(entry["fields"])}
        for code, entry in sorted(grouped.items())
    }


def workflow_authoring_contract(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return one bounded, side-effect-free workflow authoring contract."""
    selected = _profile(profile)
    return {
        "schema_version": 1,
        "profile": selected.value,
        "normalizer_version": WORKFLOW_NORMALIZER_VERSION,
        "definition_schema": definition_json_schema(selected),
        "sidecar_schema": sidecar_json_schema(selected),
        "compatibility_codes": compatibility_code_catalog(selected),
    }
