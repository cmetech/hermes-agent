"""Immutable public contracts for portable workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def freeze_value(value: Any) -> Any:
    """Recursively freeze parsed YAML without changing scalar values."""
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): freeze_value(item) for key, item in value.items()
        })
    if isinstance(value, list | tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    severity: str = "error"
    blocking: bool = True
    source_line: int | None = None


class WorkflowValidationError(ValueError):
    """Raised when a portable package cannot be normalized safely."""

    def __init__(
        self,
        issues: ValidationIssue | tuple[ValidationIssue, ...] | list[ValidationIssue],
    ):
        if isinstance(issues, ValidationIssue):
            normalized = (issues,)
        else:
            normalized = tuple(issues)
        self.issues = normalized
        super().__init__("; ".join(issue.message for issue in normalized))


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    node_type: str
    value: Any
    depends_on: tuple[str, ...]
    source_index: int
    source_line: int | None
    options: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    nodes: tuple[WorkflowNode, ...]
    options: Mapping[str, Any]
    source_path: Path


@dataclass(frozen=True)
class WorkflowPackage:
    definition: WorkflowDefinition
    root: Path
    workflow_path: Path
    sidecar_path: Path | None
    sidecar: Mapping[str, Any]
    source: str
    precedence: int
    validation_issues: tuple[ValidationIssue, ...] = ()
