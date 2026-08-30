"""Typed executor boundary used by the workflow scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Any, BinaryIO, Callable, Mapping, Protocol

from agent.structured_output import (
    StructuredOutputError,
    StructuredOutputRequest,
    StructuredOutputStrategy,
    StructuredOutputValue,
    normalize_schema,
    parse_validate_canonicalize,
)
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.models import (
    DeadlineBudget,
    RunExecutionLimits,
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowStructuredOutput,
)
from plugins.workflow.output_resolution import (
    PrimaryOutputCandidate,
    ResolvedOutputReference,
)
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowResolvedProviderRoute,
)
from plugins.workflow.sessions import (
    PersistentSessionRecoverySelection,
    SessionRegistryUpdateCandidate,
)
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
    max_artifact_bytes: int = 16 * 1024 * 1024
    is_cancelled: Callable[[], bool] | None = None
    workflow_name: str = ""
    workflow_options: Mapping[str, Any] = field(default_factory=dict)
    variable_context: Any = None
    predecessor_results: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    node_state: Mapping[str, object] = field(default_factory=dict)
    operator_scope: str = "local"
    ai_entitlement: AIEntitlementResolution = field(
        default_factory=lambda: AIEntitlementResolution("real")
    )
    execution_limits: RunExecutionLimits | None = None
    resource_limits: ProcessResourceLimits = field(
        default_factory=ProcessResourceLimits
    )
    deadline_budget: DeadlineBudget | None = None
    max_provider_attempts: int = 5
    cancellation_reason: Callable[[], str | None] | None = None
    record_iteration: (
        Callable[[tuple[ArtifactRef, ...], Mapping[str, object]], None] | None
    ) = None
    spawn_intent: Callable[[str], bool] | None = None
    spawn_failed: Callable[[str, str], bool] | None = None
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
    sealed_resource_paths: frozenset[str] | None = None
    sealed_resource_bytes: Mapping[str, bytes] | None = None
    language_profile: WorkflowLanguageProfile = WorkflowLanguageProfile.HERMES_LEGACY
    normalizer_version: int = 2
    sealed_provider_route: WorkflowResolvedProviderRoute | None = None
    sealed_provider_authority: WorkflowProviderAuthority | None = None
    intended_authority_digest: str | None = None
    shared_context_compatibility_digest: str | None = None
    expected_model_visible_prefix_digest: str | None = None
    sealed_mcp_runtime_identity_digest: str | None = None
    structured_output: WorkflowStructuredOutput | None = None
    structured_output_decision: StructuredOutputCapabilityDecision | None = None
    outward_action: bool = False
    output_resolver: (
        Callable[[str, tuple[str, ...]], ResolvedOutputReference] | None
    ) = None
    sealed_attempt_timeout: bool = False
    record_session_recovery_selection: (
        Callable[[PersistentSessionRecoverySelection], bool] | None
    ) = None
    provider_dispatch: Callable[[str], bool] | None = None
    provider_start_delivered: Callable[[str], bool] | None = None
    provider_execute_received: Callable[[str], bool] | None = None
    provider_execute_release: Callable[[str], bool] | None = None
    record_loop_decision: (Callable[[Mapping[str, object]], None] | None) = None
    max_model_iterations: int = 90
    attempt_directory: Path | None = None
    publication_directory: Path | None = None

    @property
    def effective_attempt_directory(self) -> Path:
        return self.attempt_directory or (
            self.run_directory / "nodes" / self.node.id / self.attempt_id
        )

    @property
    def effective_publication_directory(self) -> Path:
        return self.publication_directory or self.run_directory / "artifacts"


@dataclass(frozen=True)
class NodeExecutionResult:
    status: str
    artifacts: tuple[ArtifactRef, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    primary_output: PrimaryOutputCandidate | None = None
    session_registry_update: SessionRegistryUpdateCandidate | None = None
    session_registry_authority: SessionRegistryUpdateCandidate | None = None
    session_recovery_outcome: str | None = None


def publication_tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture one no-follow publication identity, including empty directories."""

    def identity(observed: os.stat_result) -> tuple[int, ...]:
        return (
            observed.st_mode,
            observed.st_dev,
            observed.st_ino,
            observed.st_nlink,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        )

    root_before = root.lstat()
    if stat.S_ISLNK(root_before.st_mode) or not stat.S_ISDIR(root_before.st_mode):
        raise ValueError("publication root is not a regular directory")
    snapshot: dict[str, tuple[object, ...]] = {
        ".": ("directory", *identity(root_before))
    }
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode):
            raise ValueError("publication tree contains a symlink")
        if stat.S_ISDIR(before.st_mode):
            after = path.lstat()
            if identity(before) != identity(after):
                raise ValueError("publication directory changed during snapshot")
            snapshot[relative] = ("directory", *identity(after))
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("publication tree contains an unsafe entry")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ValueError("publication file is not regular")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            opened_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = path.lstat()
        if not (
            identity(before) == identity(opened) == identity(opened_after) == identity(after)
        ):
            raise ValueError("publication file changed during snapshot")
        snapshot[relative] = ("file", *identity(after), digest.hexdigest())
    root_after = root.lstat()
    if identity(root_before) != identity(root_after):
        raise ValueError("publication root changed during snapshot")
    return snapshot


class StructuredProcessOutputIntegrityError(ValueError):
    """A sealed deterministic output declaration changed after admission."""


def validate_structured_process_output(
    context: NodeExecutionContext,
    response: str,
) -> StructuredOutputValue | None:
    """Validate one v6 script/bash stdout value against its sealed schema."""
    if (
        context.language_profile is not WorkflowLanguageProfile.ARCHON_2026_07
        or context.normalizer_version < 6
    ):
        return None
    declared = context.structured_output
    if declared is None:
        if context.node.options.get("output_format") is not None:
            raise StructuredProcessOutputIntegrityError(
                "admitted structured-output schema is missing"
            )
        return None

    def thaw(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): thaw(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [thaw(item) for item in value]
        return value

    try:
        normalized = normalize_schema(thaw(declared.canonical_schema))
    except StructuredOutputError as exc:
        raise StructuredProcessOutputIntegrityError(
            "admitted structured-output schema is invalid"
        ) from exc
    if (
        normalized.schema_fingerprint != declared.schema_fingerprint
        or declared.canonicalization_version != 1
    ):
        raise StructuredProcessOutputIntegrityError(
            "admitted structured-output identity is contradictory"
        )
    return parse_validate_canonicalize(
        response,
        StructuredOutputRequest(
            schema=normalized,
            strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            adapter_version=1,
            output_bytes_limit=context.max_output_bytes,
            canonicalization_version=declared.canonicalization_version,
        ),
    )


def pretransport_zero_metadata(
    *,
    phase5: bool,
    exact_for_legacy: bool = False,
) -> dict[str, object]:
    """Describe a provider launch that provably stopped before transport."""
    metadata: dict[str, object] = {"provider_attempts": 0}
    if phase5 or exact_for_legacy:
        metadata["provider_attempts_exact"] = True
    if phase5:
        metadata["known_no_effect"] = True
    return metadata


def sealed_provider_request_for_launch(
    context: NodeExecutionContext,
    request: Any,
) -> Any | None:
    """Intersect a v3 provider handoff with the latest absolute attempt budget."""
    if not context.sealed_attempt_timeout:
        return request
    if context.deadline_budget is None:
        raise RuntimeError("sealed provider launch requires an attempt deadline")
    remaining = context.deadline_budget.remaining_wall(context.monotonic())
    if remaining <= 0:
        return None
    return replace(
        request,
        wall_timeout_seconds=remaining,
        idle_timeout_seconds=min(
            request.idle_timeout_seconds,
            context.deadline_budget.idle_seconds,
            remaining,
        ),
        provider_request_timeout_seconds=min(
            request.provider_request_timeout_seconds,
            context.deadline_budget.provider_seconds,
            remaining,
        ),
    )


def validated_provider_retry_count(
    value: object,
    *,
    granted_attempts: int,
) -> int | None:
    """Return an exact internal retry count only when it fits the grant."""
    if (
        isinstance(granted_attempts, bool)
        or not isinstance(granted_attempts, int)
        or granted_attempts <= 0
    ):
        raise ValueError("granted provider attempts must be a positive integer")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < granted_attempts
    ):
        return value
    return None


def validated_provider_total_call_count(
    value: object,
    *,
    granted_attempts: int,
) -> int | None:
    """Convert an exact total provider-call count to additional calls once."""
    if (
        isinstance(granted_attempts, bool)
        or not isinstance(granted_attempts, int)
        or granted_attempts <= 0
    ):
        raise ValueError("granted provider attempts must be a positive integer")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= granted_attempts
    ):
        return value - 1
    return None


def conservative_provider_retry_count(
    value: object,
    *,
    granted_attempts: int,
) -> int:
    """Return an exact internal retry count or conservatively charge the grant."""
    exact = validated_provider_retry_count(value, granted_attempts=granted_attempts)
    return exact if exact is not None else granted_attempts - 1


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
    """Report live descendants, failing closed when tree proof is unavailable."""
    return bool(tree.tree_active())


class NodeExecutor(Protocol):
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult: ...


__all__ = [
    "BoundedProcessOutput",
    "NodeExecutionContext",
    "NodeExecutionResult",
    "NodeExecutor",
    "conservative_provider_retry_count",
    "process_tree_active",
    "validated_provider_retry_count",
]
