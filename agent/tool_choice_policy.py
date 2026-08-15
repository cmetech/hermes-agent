"""Immutable, request-scoped tool-choice policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal
from uuid import uuid4


ToolChoiceMode = Literal["auto", "required", "named", "none"]
ToolCallRole = Literal[
    "primary",
    "post_tool",
    "correction",
    "title",
    "compression",
    "auxiliary",
]

_VALID_MODES = frozenset({"auto", "required", "named", "none"})
_VALID_CALL_ROLES = frozenset(
    {"primary", "post_tool", "correction", "title", "compression", "auxiliary"}
)


class ToolChoicePolicyError(ValueError):
    """A stable, safe tool-policy validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid(message: str = "Invalid tool choice.") -> ToolChoicePolicyError:
    return ToolChoicePolicyError("invalid_tool_choice", message)


@dataclass(frozen=True)
class ToolChoicePolicy:
    mode: ToolChoiceMode = "auto"
    name: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise _invalid()
        if self.mode == "named":
            if not isinstance(self.name, str) or not self.name.strip():
                raise _invalid("A named tool choice requires a tool name.")
        elif self.name is not None:
            raise _invalid("Only a named tool choice may include a tool name.")

    def for_post_tool(self) -> "ToolChoicePolicy":
        return ToolChoicePolicy(mode="auto")


@dataclass(frozen=True)
class ToolOperationContext:
    operation_id: str
    policy: ToolChoicePolicy
    call_role: ToolCallRole = "primary"
    otto_contract_version: Literal["v1"] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id:
            raise ValueError("operation_id must be non-empty")
        if self.call_role not in _VALID_CALL_ROLES:
            raise ValueError("call_role is not allowlisted")
        if self.otto_contract_version not in {None, "v1"}:
            raise ValueError("unsupported OTTO tool contract version")

    @classmethod
    def create(
        cls,
        policy: ToolChoicePolicy | None = None,
        *,
        operation_id: str | None = None,
        call_role: ToolCallRole = "primary",
        otto_contract_version: Literal["v1"] | None = None,
    ) -> "ToolOperationContext":
        return cls(
            operation_id=operation_id or uuid4().hex,
            policy=policy or ToolChoicePolicy(),
            call_role=call_role,
            otto_contract_version=otto_contract_version,
        )

    def for_retry(self) -> "ToolOperationContext":
        return self

    def after_structured_tool_call(self) -> "ToolOperationContext":
        return replace(
            self,
            policy=self.policy.for_post_tool(),
            call_role="post_tool",
        )

    def clear(self) -> None:
        return None


def parse_tool_choice(
    value: object,
    *,
    effective_tool_names: Iterable[str],
) -> ToolChoicePolicy:
    """Normalize a supported native choice against the effective tool catalog."""

    policy = parse_tool_choice_request(value)
    return validate_tool_choice_policy(policy, effective_tool_names)


def parse_tool_choice_request(value: object) -> ToolChoicePolicy:
    """Parse a native request shape before the agent catalog exists."""

    if value is None or value == "auto":
        return ToolChoicePolicy()
    if value == "none":
        return ToolChoicePolicy(mode="none")
    if value == "required":
        return ToolChoicePolicy(mode="required")

    name = _named_choice_name(value)
    if name is None:
        raise _invalid()
    return ToolChoicePolicy(mode="named", name=name)


def validate_tool_choice_policy(
    policy: ToolChoicePolicy,
    effective_tool_names: Iterable[str],
) -> ToolChoicePolicy:
    """Validate mandatory policy after the effective catalog is known."""

    names = frozenset(effective_tool_names)
    if policy.mode == "required" and not names:
        raise ToolChoicePolicyError(
            "mandatory_tool_choice_not_supported",
            "Mandatory tool choice requires an available tool.",
        )
    if policy.mode == "named" and policy.name not in names:
        raise _invalid()
    return policy


def _named_choice_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None

    choice_type = value.get("type")
    if choice_type == "function":
        function = value.get("function")
        if isinstance(function, dict):
            name = function.get("name")
        else:
            name = value.get("name")
    elif choice_type == "tool":
        name = value.get("name")
    else:
        return None

    return name.strip() if isinstance(name, str) and name.strip() else None
