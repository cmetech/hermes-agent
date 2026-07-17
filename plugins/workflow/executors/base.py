"""Typed executor boundary used by the workflow scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any, BinaryIO, Callable, Mapping, Protocol

from plugins.workflow.models import DeadlineBudget, WorkflowNode
from plugins.workflow.store import ArtifactRef
from tools.managed_process import (
    ProcessIdentity,
    ProcessResourceLimits,
    TerminationPolicy,
)


@dataclass(frozen=True)
class NodeExecutionContext:
    run_id: str
    run_directory: Path
    node: WorkflowNode
    attempt_id: str
    timeout_seconds: float = 120.0
    max_output_bytes: int = 1024 * 1024
    is_cancelled: Callable[[], bool] | None = None
    workflow_name: str = ""
    workflow_options: Mapping[str, Any] = field(default_factory=dict)
    variable_context: Any = None
    predecessor_results: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    node_state: Mapping[str, object] = field(default_factory=dict)
    operator_scope: str = "local"
    resource_limits: ProcessResourceLimits = field(
        default_factory=ProcessResourceLimits
    )
    deadline_budget: DeadlineBudget | None = None
    max_provider_attempts: int = 5
    cancellation_reason: Callable[[], str | None] | None = None
    record_iteration: (
        Callable[[tuple[ArtifactRef, ...], Mapping[str, object]], None] | None
    ) = None
    process_started: Callable[[ProcessIdentity], bool] | None = None
    process_stopped: Callable[[ProcessIdentity, bool], None] | None = None
    monotonic: Callable[[], float] = time.monotonic
    termination_policy: TerminationPolicy = field(
        default_factory=lambda: TerminationPolicy(
            cooperative_grace_seconds=5,
            term_grace_seconds=5,
            kill_grace_seconds=2,
            wait_timeout_seconds=2,
        )
    )


@dataclass(frozen=True)
class NodeExecutionResult:
    status: str
    artifacts: tuple[ArtifactRef, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class BoundedProcessOutput:
    """File-backed subprocess output without inherited-pipe reader threads."""

    def __init__(self, stdout_path: Path, stderr_path: Path, *, limit: int) -> None:
        if limit <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.limit = limit
        self.stdout: BinaryIO = stdout_path.open("wb", buffering=0)
        try:
            self.stderr: BinaryIO = stderr_path.open("wb", buffering=0)
        except BaseException:
            self.stdout.close()
            raise
        self._observed_bytes = 0

    def poll(self) -> tuple[bool, bool]:
        """Return ``(limit_exceeded, output_grew)`` since the prior poll."""
        try:
            size = self.stdout_path.stat().st_size + self.stderr_path.stat().st_size
        except FileNotFoundError:
            size = 0
        grew = size > self._observed_bytes
        self._observed_bytes = max(self._observed_bytes, size)
        return size > self.limit, grew

    def close(self) -> bool:
        self.stdout.close()
        self.stderr.close()
        stdout_size = self.stdout_path.stat().st_size
        stderr_size = self.stderr_path.stat().st_size
        exceeded = stdout_size + stderr_size > self.limit
        stdout_keep = min(stdout_size, self.limit)
        stderr_keep = min(stderr_size, self.limit - stdout_keep)
        if stdout_size != stdout_keep:
            with self.stdout_path.open("r+b") as output:
                output.truncate(stdout_keep)
        if stderr_size != stderr_keep:
            with self.stderr_path.open("r+b") as output:
                output.truncate(stderr_keep)
        return exceeded


def process_tree_active(tree: Any) -> bool:
    """Report whether the owned child or its POSIX process group is still live."""
    if tree.process.poll() is None:
        return True
    if os.name == "nt":  # pragma: no cover - Windows lifecycle CI path
        return False
    group_id = tree.identity.group_id
    if group_id is None or group_id <= 0 or group_id == os.getpgrp():
        return False
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class NodeExecutor(Protocol):
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult: ...


__all__ = [
    "BoundedProcessOutput",
    "NodeExecutionContext",
    "NodeExecutionResult",
    "NodeExecutor",
    "process_tree_active",
]
