"""Typed executor boundary used by the workflow scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from plugins.workflow.models import WorkflowNode
from plugins.workflow.store import ArtifactRef


@dataclass(frozen=True)
class NodeExecutionContext:
    run_id: str
    run_directory: Path
    node: WorkflowNode
    attempt_id: str
    timeout_seconds: float = 120.0
    max_output_bytes: int = 1024 * 1024
    is_cancelled: Callable[[], bool] | None = None


@dataclass(frozen=True)
class NodeExecutionResult:
    status: str
    artifacts: tuple[ArtifactRef, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


class NodeExecutor(Protocol):
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult: ...
