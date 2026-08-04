"""Bounded durable scheduling for portable workflow DAGs."""

from __future__ import annotations

import ast
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import sys
import threading
import time
import uuid
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from agent.structured_output import StructuredOutputStrategy
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.bash_rendering import bash_output_references
from plugins.workflow.conditions import (
    WorkflowConditionError,
    evaluate_v3_condition,
)
from plugins.workflow.entitlement import AIEntitlementResolution, derive_ai_entitlement
from plugins.workflow.execution_semantics import (
    Phase3ExecutionSemantics,
    WorkflowExecutionSemanticsError,
    read_phase3_execution_semantics,
)
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.executors.cancel import CancelExecutor
from plugins.workflow.executors.loop import LoopExecutor
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.locks import WorkflowLockTimeout
from plugins.workflow.language import (
    WORKFLOW_NORMALIZER_VERSION,
    WorkflowLanguageCompatibilityError,
    read_language_snapshot,
    verify_language_snapshot,
)
from plugins.workflow.language_schema import iter_output_references
from plugins.workflow.models import (
    DeadlineBudget,
    ExecutionFence,
    RetryLedgerGrant,
    RetryPolicy,
    RunExecutionLimits,
    TerminalJournalReserve,
    WorkflowNode,
    WorkflowLanguageProfile,
    WorkflowPackage,
    WorkflowRuntimeConfig,
    WorkflowValidationError,
)
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    ArchonOutputUnavailableError,
    PRIMARY_OUTPUT_CANDIDATE_METADATA_KEY,
    PrimaryOutputCandidate,
    ResolvedNodeOutput,
    ResolvedOutputReference,
    WorkflowOutputReferenceError,
    primary_output_candidate_from_identity,
    primary_output_candidate_identity,
    output_publication_identity,
    resolve_legacy_output_values,
    resolve_node_output,
    resolve_output_reference,
    resolved_output_publication_identity,
)
from plugins.workflow.resources import ResourceResolver, VariableContext
from plugins.workflow.schema import (
    is_inline_script,
    load_workflow_snapshot,
    validate_authenticated_command_references,
)
from plugins.workflow.sessions import NodeSessionRegistry
from plugins.workflow.store import (
    ArtifactRef,
    NodeClaim,
    RunStore,
    StorageQuotaError,
    TypedPublicationCandidate,
)
from plugins.workflow.trust import (
    WORKFLOW_RESOURCE_MAX_FILE_BYTES,
    WORKFLOW_RESOURCE_MAX_FILES,
    WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
)
from tools.managed_process import ProcessResourceLimits, TerminationPolicy


_TERMINAL_NODE_STATES = {"succeeded", "failed", "skipped", "cancelled", "interrupted"}
_CLAUSE = re.compile(
    r"^\s*\$(?P<node>[\w.:-]+)\.output(?P<path>(?:\.[\w.-]+)*)\s*"
    r"(?P<operator>==|!=|<=|>=|<|>)\s*"
    r"(?P<right>'[^']*'|\"[^\"]*\"|-?(?:\d+(?:\.\d*)?|\.\d+))\s*$",
    re.UNICODE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _StrictReferenceSnapshot:
    """Immutable pre-claim output authority carried into one executor claim."""

    outputs: Mapping[
        str, ResolvedNodeOutput | WorkflowOutputReferenceError
    ]
    references: Mapping[tuple[str, tuple[str, ...]], ResolvedOutputReference]

    def resolve(
        self, node_id: str, path: tuple[str, ...]
    ) -> ResolvedOutputReference:
        try:
            return self.references[(node_id, tuple(path))]
        except KeyError as exc:
            raise WorkflowOutputReferenceError(
                "output_reference_integrity", node_id, tuple(path)
            ) from exc
_LEGACY_PACKAGE_PATHS = 4096
_LEGACY_PACKAGE_PATH_CHARS = 512
_OUTPUT_RESOLUTION_CACHE_MAX_BYTES = 16 * 1024 * 1024
# Conservatively covers cache-table slots, the OrderedDict node, the LRU
# composite key, the stored weight integer, and allocator slack.
_OUTPUT_CACHE_ENTRY_OVERHEAD = 1024
_OUTPUT_CACHE_MISS = object()
_LEGACY_NON_PACKAGE_FILES = frozenset(
    {
        ".lock",
        ".snapshot-owner.json",
        "events.jsonl",
        "inputs.json",
        "resources.json",
        "run.json",
    }
)
_LEGACY_NON_PACKAGE_ROOTS = frozenset(
    {"artifacts", "inputs", "node-agent-skills", "node-skills", "nodes"}
)


def _cache_text_weight(value: str) -> int:
    return sys.getsizeof(value)


def _cache_value_weight(value: object, seen: set[int] | None = None) -> int:
    """Conservatively estimate retained immutable JSON memory."""
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    weight = sys.getsizeof(value)
    if isinstance(value, Mapping):
        if isinstance(value, MappingProxyType):
            # The proxy retains a separate, otherwise invisible backing dict.
            # A shallow copy exposes equivalent table allocation/capacity.
            weight += sys.getsizeof(dict(value))
        return weight + sum(
            _cache_value_weight(key, seen) + _cache_value_weight(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, tuple | list):
        return weight + sum(_cache_value_weight(item, seen) for item in value)
    return weight


def _cache_key_weight(key: tuple[object, ...]) -> int:
    return _cache_value_weight(key)


def _resolved_output_weight(
    key: tuple[object, ...], value: ResolvedNodeOutput | None
) -> int:
    weight = _OUTPUT_CACHE_ENTRY_OVERHEAD + _cache_key_weight(key)
    if value is None:
        return weight
    return (
        weight
        + sys.getsizeof(value)
        + sys.getsizeof(value.canonical_bytes)
        + _cache_text_weight(value.text)
        + _cache_value_weight(value.value)
        + _cache_text_weight(value.media_type)
        + _cache_text_weight(value.sha256)
        + _cache_text_weight(value.node_id)
        + _cache_text_weight(value.attempt_id)
        + (
            _cache_text_weight(value.publication_id)
            if value.publication_id is not None
            else 0
        )
        + (
            _cache_text_weight(value.schema_fingerprint)
            if value.schema_fingerprint is not None
            else 0
        )
    )


def _primary_candidate_weight(
    key: tuple[str, str, str], candidate: PrimaryOutputCandidate
) -> int:
    return (
        _OUTPUT_CACHE_ENTRY_OVERHEAD
        + _cache_key_weight(key)
        + sys.getsizeof(candidate)
        + candidate.size_bytes
        + _cache_value_weight(candidate.structured_value)
        + _cache_text_weight(candidate.attempt_relative_path)
        + _cache_text_weight(candidate.media_type)
        + _cache_text_weight(candidate.sha256)
        + (
            _cache_text_weight(candidate.schema_fingerprint)
            if candidate.schema_fingerprint is not None
            else 0
        )
        + (
            _cache_text_weight(candidate.output_type)
            if candidate.output_type is not None
            else 0
        )
    )

_SEALED_STRUCTURED_DECISION_FIELDS = frozenset({
    "strategy",
    "effective_provider",
    "model",
    "api_mode",
    "declaration_source",
    "adapter_version",
    "schema_fingerprint",
    "rationale",
})


class ConditionEvaluationError(ValueError):
    """A validated condition could not be evaluated against typed output."""


class SealedStructuredOutputDecisionError(ValueError):
    """An admitted Archon structured-output decision lost integrity."""


class FailureClass(Enum):
    TRANSIENT = "transient"
    FATAL = "fatal"
    UNKNOWN_ERROR = "unknown_error"
    UNKNOWN_OUTCOME = "unknown_outcome"
    CANCELLED = "cancelled"
    RECONCILE = "reconcile"
    EXHAUSTED = "exhausted"


_TRANSIENT_FAILURES = {
    "provider_timeout",
    "provider_stall",
    "network_disconnect",
    "network_error",
    "rate_limit",
    "service_unavailable",
    "timeout",
}
_FATAL_FAILURES = {
    "authentication",
    "authorization",
    "credit_exhausted",
    "validation",
    "invalid_request",
    "output_limit",
    "resource_limit",
    "cleanup_failed",
    "execution_integrity",
    "structured_output_integrity",
    "structured_output_capability_drift",
    "workflow_execution_semantics_mismatch",
    "workflow_language_snapshot_invalid",
}


def classify_failure(
    error_code: str | None,
    *,
    workflow_attempt: int = 1,
    provider_attempts: int = 0,
    maximum: int = 5,
    known_no_effect: bool | None = None,
    outward_action: bool = False,
) -> FailureClass:
    """Map executor failures to a closed retry taxonomy."""
    code = (error_code or "").lower()
    if code in {"cancelled", "shutdown", "interrupted"}:
        return FailureClass.CANCELLED
    if code in _FATAL_FAILURES:
        return FailureClass.FATAL
    if outward_action:
        return FailureClass.UNKNOWN_OUTCOME
    if code in {"unknown_side_effect", "outcome_unknown"}:
        return (
            FailureClass.RECONCILE
            if known_no_effect is None
            else FailureClass.UNKNOWN_OUTCOME
        )
    if (
        known_no_effect is False
        and code not in _TRANSIENT_FAILURES
        and code != "process_exit"
    ):
        return FailureClass.UNKNOWN_OUTCOME
    if workflow_attempt + provider_attempts >= maximum:
        return FailureClass.EXHAUSTED
    if code in _TRANSIENT_FAILURES:
        return FailureClass.TRANSIENT
    if code == "process_exit" and known_no_effect is True:
        return FailureClass.TRANSIENT
    if known_no_effect is True:
        return FailureClass.UNKNOWN_ERROR
    if known_no_effect is False:
        return FailureClass.UNKNOWN_OUTCOME
    return FailureClass.FATAL


def compute_retry_delay(
    policy: RetryPolicy,
    attempt: int,
    *,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Return capped exponential delay with deterministic injectable jitter."""
    if attempt <= 0:
        raise ValueError("attempt must be positive")
    factor = min(max(float(jitter()), 0.0), 1.0)
    base = (policy.delay_ms / 1000.0) * (2 ** (attempt - 1))
    return min(60.0, base * (0.5 + factor))


def _sealed_structured_output_decision(
    projection: Mapping[str, object],
    node_id: str,
    schema_fingerprint: str,
) -> StructuredOutputCapabilityDecision:
    metadata = projection.get("run_metadata")
    key = (
        "structured_output_decision."
        + hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
    )
    raw = metadata.get(key) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str):
        raise SealedStructuredOutputDecisionError(
            "sealed structured-output decision is missing"
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SealedStructuredOutputDecisionError(
            "sealed structured-output decision is malformed"
        ) from exc
    if not isinstance(value, Mapping) or set(value) != _SEALED_STRUCTURED_DECISION_FIELDS:
        raise SealedStructuredOutputDecisionError(
            "sealed structured-output decision is malformed"
        )
    try:
        decision = StructuredOutputCapabilityDecision(
            strategy=StructuredOutputStrategy(value["strategy"]),
            effective_provider=value["effective_provider"],
            model=value["model"],
            api_mode=value["api_mode"],
            declaration_source=value["declaration_source"],
            adapter_version=value["adapter_version"],
            schema_fingerprint=value["schema_fingerprint"],
            rationale=value["rationale"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SealedStructuredOutputDecisionError(
            "sealed structured-output decision is malformed"
        ) from exc
    if (
        decision.schema_fingerprint != schema_fingerprint
        or type(decision.adapter_version) is not int
        or decision.adapter_version <= 0
        or any(
            not isinstance(item, str)
            for item in (
                decision.effective_provider,
                decision.model,
                decision.api_mode,
                decision.declaration_source,
                decision.rationale,
            )
        )
    ):
        raise SealedStructuredOutputDecisionError(
            "sealed structured-output decision is contradictory"
        )
    return decision


def evaluate_trigger_rule(rule: str, dependency_states: Iterable[str]) -> bool | None:
    states = tuple(dependency_states)
    if any(state not in _TERMINAL_NODE_STATES for state in states):
        return None
    succeeded = states.count("succeeded")
    failed = any(state in {"failed", "cancelled", "interrupted"} for state in states)
    if rule == "all_success":
        return all(state == "succeeded" for state in states)
    if rule == "one_success":
        return succeeded >= 1
    if rule == "none_failed_min_one_success":
        return not failed and succeeded >= 1
    if rule == "all_done":
        return True
    raise ValueError(f"unknown trigger rule: {rule}")


def _output_value(outputs: Mapping[str, object], node_id: str, path: str) -> object:
    if node_id not in outputs:
        raise ConditionEvaluationError(f"missing output for {node_id}")
    value = outputs[node_id]
    if isinstance(value, ResolvedNodeOutput):
        # Phase 2 deliberately retains current coercion, missing-field, and
        # comparison behavior; Phase 3 replaces this compatibility adapter.
        value = value.value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    for component in filter(None, path.split(".")):
        if not isinstance(value, Mapping) or component not in value:
            raise ConditionEvaluationError(f"missing output path {node_id}.{component}")
        value = value[component]
    return value


def _evaluate_clause(clause: str, outputs: Mapping[str, object]) -> bool:
    match = _CLAUSE.fullmatch(clause)
    if match is None:
        raise ConditionEvaluationError("statically malformed condition clause")
    left = _output_value(outputs, match.group("node"), match.group("path").lstrip("."))
    right_text = match.group("right")
    right = (
        ast.literal_eval(right_text)
        if right_text[0] in {'"', "'"}
        else float(right_text)
    )
    operator = match.group("operator")
    if isinstance(left, float) and not math.isfinite(left):
        raise ConditionEvaluationError("comparison requires finite numeric output")
    if operator in {"<", "<=", ">", ">="}:
        if isinstance(left, bool) or not isinstance(left, int | float):
            raise ConditionEvaluationError("ordered comparison requires numeric output")
        left = float(left)
        if not math.isfinite(left) or not math.isfinite(float(right)):
            raise ConditionEvaluationError(
                "ordered comparison requires finite numeric output"
            )
        operations = {
            "<": lambda: left < right,
            "<=": lambda: left <= right,
            ">": lambda: left > right,
            ">=": lambda: left >= right,
        }
        return operations[operator]()
    return left == right if operator == "==" else left != right


def evaluate_condition(expression: str, outputs: Mapping[str, object]) -> bool:
    """Evaluate Archon conditions with ``&&`` precedence over ``||``."""
    or_groups = re.split(r"\s*\|\|\s*", expression)
    if not or_groups:
        raise ConditionEvaluationError("empty condition")
    return any(
        all(
            _evaluate_clause(clause, outputs) for clause in re.split(r"\s*&&\s*", group)
        )
        for group in or_groups
    )


class RunScheduler:
    def __init__(
        self,
        store: RunStore,
        *,
        owner_id: str | None = None,
        execution_owner_id: str | None = None,
        execution_owner_epoch: int | None = None,
        execution_fence: ExecutionFence | None = None,
        agent_runner=None,
        runner_binding=None,
        session_registry: NodeSessionRegistry | None = None,
        profile_name: str = "default",
        max_parallel_nodes: int = 4,
        heartbeat_seconds: float = 5.0,
        lease_seconds: float = 30.0,
        ai_idle_timeout_seconds: float = 300.0,
        ai_wall_timeout_seconds: float = 1800.0,
        provider_request_timeout_seconds: float = 300.0,
        subprocess_timeout_seconds: float = 120.0,
        default_max_attempts: int = 5,
        cooperative_shutdown_seconds: float = 5.0,
        term_grace_seconds: float = 5.0,
        kill_reap_grace_seconds: float = 2.0,
        resource_limits: ProcessResourceLimits | None = None,
        utcnow: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if max_parallel_nodes <= 0 or heartbeat_seconds <= 0 or lease_seconds <= 0:
            raise ValueError(
                "parallelism, heartbeat, and lease values must be positive"
            )
        if heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat must be shorter than the lease")
        for name, value in (
            ("AI idle timeout", ai_idle_timeout_seconds),
            ("AI wall timeout", ai_wall_timeout_seconds),
            ("provider timeout", provider_request_timeout_seconds),
            ("subprocess timeout", subprocess_timeout_seconds),
            ("cooperative shutdown", cooperative_shutdown_seconds),
            ("TERM grace", term_grace_seconds),
            ("KILL/reap grace", kill_reap_grace_seconds),
        ):
            if (
                not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive and finite")
        if ai_idle_timeout_seconds > ai_wall_timeout_seconds:
            raise ValueError("AI idle timeout cannot exceed AI wall timeout")
        if provider_request_timeout_seconds > ai_wall_timeout_seconds:
            raise ValueError("provider timeout cannot exceed AI wall timeout")
        if (
            not isinstance(default_max_attempts, int)
            or not 1 <= default_max_attempts <= 5
        ):
            raise ValueError("default retry attempts must be between 1 and 5")
        if runner_binding is not None:
            if agent_runner is not None and agent_runner is not runner_binding.real_runner:
                raise ValueError("runner_binding and agent_runner disagree")
            agent_runner = runner_binding.real_runner
        self.runner_binding = runner_binding
        deterministic_runner = (
            runner_binding.deterministic_runner
            if runner_binding is not None
            else None
        )
        self.store = store
        self.session_registry = session_registry
        if self.session_registry is None and agent_runner is not None:
            self.session_registry = NodeSessionRegistry(store.hermes_home)
        self.owner_id = owner_id or f"scheduler-{os.getpid()}-{uuid.uuid4().hex}"
        if (execution_owner_id is None) != (execution_owner_epoch is None):
            raise ValueError(
                "execution owner ID and epoch must be provided together"
            )
        self.execution_owner_id = execution_owner_id
        self.execution_owner_epoch = execution_owner_epoch
        self.execution_fence = execution_fence
        self.max_parallel_nodes = min(max_parallel_nodes, store.limits["workers"])
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.lease_seconds = float(lease_seconds)
        self.ai_idle_timeout_seconds = float(ai_idle_timeout_seconds)
        self.ai_wall_timeout_seconds = float(ai_wall_timeout_seconds)
        self.provider_request_timeout_seconds = float(provider_request_timeout_seconds)
        self.subprocess_timeout_seconds = float(subprocess_timeout_seconds)
        self.default_max_attempts = default_max_attempts
        self.termination_policy = TerminationPolicy(
            cooperative_grace_seconds=cooperative_shutdown_seconds,
            term_grace_seconds=term_grace_seconds,
            kill_grace_seconds=kill_reap_grace_seconds,
            wait_timeout_seconds=kill_reap_grace_seconds,
        )
        self.shutdown_deadline_seconds = (
            cooperative_shutdown_seconds + term_grace_seconds + kill_reap_grace_seconds
        )
        self.resource_limits = resource_limits or ProcessResourceLimits()
        self.profile_execution_limits = RunExecutionLimits.resolve(
            WorkflowRuntimeConfig(
                max_parallel_nodes=self.max_parallel_nodes,
                max_total_workers=store.limits["workers"],
                ai_idle_timeout_seconds=self.ai_idle_timeout_seconds,
                ai_wall_timeout_seconds=self.ai_wall_timeout_seconds,
                provider_request_timeout_seconds=self.provider_request_timeout_seconds,
                subprocess_timeout_seconds=self.subprocess_timeout_seconds,
                combined_retries=self.default_max_attempts,
                process_tree_rss_bytes=self.resource_limits.max_rss_bytes,
                process_tree_cpu_seconds=self.resource_limits.max_cpu_seconds,
                max_descendants=self.resource_limits.max_descendants,
                cooperative_shutdown_seconds=cooperative_shutdown_seconds,
                term_grace_seconds=term_grace_seconds,
                kill_reap_grace_seconds=kill_reap_grace_seconds,
            )
        )
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._jitter = jitter
        self._shutdown = threading.Event()
        self._activity = threading.Condition()
        self._active_runs: set[str] = set()
        self._submitted_runs: set[str] = set()
        self._active_executions = 0
        self._output_resolution_lock = threading.RLock()
        self._resolved_output_cache: dict[tuple[object, ...], ResolvedNodeOutput | None] = {}
        self._primary_output_candidates: dict[
            tuple[str, str, str], PrimaryOutputCandidate
        ] = {}
        self._output_resolution_cache_lru: OrderedDict[
            tuple[str, tuple[object, ...]], int
        ] = OrderedDict()
        self._output_resolution_cache_bytes = 0
        self._output_resolution_cache_max_bytes = _OUTPUT_RESOLUTION_CACHE_MAX_BYTES
        self._submission_pool = ThreadPoolExecutor(
            max_workers=self.max_parallel_nodes,
            thread_name_prefix="workflow-run",
        )
        self.executors = {
            "bash": BashExecutor(),
            "script": ScriptExecutor(),
            "cancel": CancelExecutor(),
            "approval": ApprovalExecutor(
                agent_runner,
                deterministic_runner=deterministic_runner,
            ),
        }
        if agent_runner is not None:
            ai_executor = AgentNodeExecutor(
                agent_runner,
                session_registry=self.session_registry,
                profile_name=profile_name,
                deterministic_runner=deterministic_runner,
            )
            self.executors.update({
                "command": ai_executor,
                "prompt": ai_executor,
                "loop": LoopExecutor(
                    agent_runner,
                    deterministic_runner=deterministic_runner,
                ),
            })

    def _reconcile_session_registry_update(self, run_id: str) -> bool:
        pending = self.store.pending_session_registry_update(run_id)
        if pending is None:
            return False
        if self.session_registry is None:
            self.session_registry = NodeSessionRegistry(self.store.hermes_home)
        candidate, retry_count, next_at = pending
        projection = self.store.load_run(run_id)
        if projection.get("status") == "recovery_pending":
            return False
        now = self._utcnow()
        if next_at is not None and datetime.fromisoformat(next_at) > now:
            return False
        try:
            outcome = self.session_registry.compare_and_set_or_observe(
                candidate.key,
                expected_generation=candidate.expected_generation,
                session_id=candidate.new_session_id,
                cache_fingerprint=candidate.cache_fingerprint,
            )
        except Exception:
            if retry_count < 5:
                self.store.defer_session_registry_update(
                    run_id,
                    candidate,
                    now=now,
                )
            return False
        self.store.resolve_session_registry_update(
            run_id,
            candidate,
            outcome=outcome,
        )
        return True

    def _renew_execution_owner(self, run_id: str) -> bool:
        if self.execution_fence is not None:
            try:
                with self.store._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self.store.assert_execution_fence(
                        connection, self.execution_fence
                    )
                    connection.commit()
                return True
            except RuntimeError:
                return False
        if self.execution_owner_id is None:
            return True
        return self.store.renew_foreground_execution(
            run_id,
            owner_id=self.execution_owner_id,
            epoch=self.execution_owner_epoch,
            now=self._utcnow(),
            lease_seconds=self.lease_seconds,
        )

    def _foreground_claim_token(
        self, projection: Mapping[str, object]
    ) -> tuple[str | None, int | None]:
        if self.execution_owner_id is not None:
            return self.execution_owner_id, self.execution_owner_epoch
        if (
            self.execution_fence is not None
            or projection.get("execution_mode") != "foreground"
        ):
            return None, None
        owner_id = projection.get("foreground_owner_id")
        epoch = projection.get("foreground_epoch")
        if (
            not isinstance(owner_id, str)
            or not owner_id
            or not isinstance(epoch, int)
        ):
            return None, None
        return owner_id, epoch

    def _is_execution_fence_loss(self, exc: RuntimeError) -> bool:
        return self.execution_fence is not None and "execution fence" in str(exc)

    @property
    def active_run_count(self) -> int:
        """Return the scheduler-local active-run count for lifecycle diagnostics."""
        with self._activity:
            return len(self._active_runs)

    @staticmethod
    def _read_text(path: Path, *, limit: int = 500_000) -> str:
        data = path.read_bytes()
        if len(data) > limit:
            raise ValueError(f"workflow value exceeds {limit} bytes: {path}")
        return data.decode("utf-8")

    def _output_values(
        self,
        projection: Mapping[str, object],
        run_directory: Path,
        *,
        node_ids: Iterable[str] | None = None,
    ) -> dict[str, object]:
        snapshot = read_language_snapshot(projection.get("language"))
        if (
            snapshot is None
            or snapshot.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY
        ):
            legacy_outputs = resolve_legacy_output_values(
                projection,
                run_directory,
                read_text=self._read_text,
            )
            if node_ids is None:
                return legacy_outputs
            requested = frozenset(node_ids)
            return {
                node_id: value
                for node_id, value in legacy_outputs.items()
                if node_id in requested
            }

        strict_v3 = snapshot.normalizer_version == 3

        artifacts = projection.get("artifacts", [])
        nodes = projection.get("nodes", {})
        if not isinstance(artifacts, list) or not isinstance(nodes, Mapping):
            return {}
        self._ensure_output_resolution_state()
        run_id = str(projection.get("run_id", ""))
        root_identity = str(run_directory.resolve())
        outputs: dict[str, object] = {}
        node_items = (
            nodes.items()
            if node_ids is None
            else (
                (node_id, nodes.get(node_id))
                for node_id in dict.fromkeys(node_ids)
            )
        )
        for node_id, node_state in node_items:
            if not isinstance(node_id, str) or not isinstance(node_state, Mapping):
                continue
            attempts = node_state.get("attempts", [])
            successful_attempts = tuple(
                attempt
                for attempt in attempts
                if isinstance(attempt, Mapping)
                and attempt.get("state") == "succeeded"
                and isinstance(attempt.get("attempt_id"), str)
            )
            if strict_v3 and len(successful_attempts) > 1:
                outputs[node_id] = WorkflowOutputReferenceError(
                    "output_reference_integrity", node_id
                )
                continue
            winning_attempt_state = (
                successful_attempts[-1] if successful_attempts else None
            )
            if winning_attempt_state is None:
                continue
            winning_attempt = str(winning_attempt_state["attempt_id"])
            candidate_key = (run_id, node_id, winning_attempt)
            node_type = str(node_state.get("type", ""))
            requires_candidate = node_type in {"command", "prompt"}
            raw_metadata = winning_attempt_state.get("metadata")
            raw_candidate = (
                raw_metadata.get(PRIMARY_OUTPUT_CANDIDATE_METADATA_KEY)
                if isinstance(raw_metadata, Mapping)
                else None
            )
            candidate: PrimaryOutputCandidate | None = None
            if raw_candidate is not None:
                try:
                    retained_candidate = primary_output_candidate_from_identity(
                        raw_candidate
                    )
                except ArchonOutputIntegrityError:
                    if strict_v3:
                        outputs[node_id] = WorkflowOutputReferenceError(
                            "output_reference_integrity", node_id
                        )
                    continue
                with self._output_resolution_lock:
                    live_candidate = self._cached_primary_output_candidate(
                        candidate_key
                    )
                if live_candidate is not None:
                    if primary_output_candidate_identity(live_candidate) != dict(
                        raw_candidate
                    ):
                        if strict_v3:
                            outputs[node_id] = WorkflowOutputReferenceError(
                                "output_reference_integrity", node_id
                            )
                        continue
                    candidate = live_candidate
                else:
                    candidate = retained_candidate
            elif requires_candidate:
                if strict_v3:
                    outputs[node_id] = WorkflowOutputReferenceError(
                        "output_reference_integrity", node_id
                    )
                continue
            candidates = [
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and artifact.get("node_id") == node_id
                and artifact.get("attempt_id") == winning_attempt
                and Path(str(artifact.get("relative_path", ""))).name.startswith(
                    ("output.", "stdout.")
                )
            ]
            canonical = [
                artifact
                for artifact in candidates
                if Path(str(artifact.get("relative_path", ""))).name.startswith(
                    "output."
                )
            ]
            if requires_candidate:
                attempt_local = [
                    artifact
                    for artifact in canonical
                    if candidate is not None
                    and artifact.get("relative_path")
                    == candidate.attempt_relative_path
                ]
                publications = [
                    artifact
                    for artifact in candidates
                    if isinstance(artifact.get("publication_id"), str)
                ]
                if strict_v3 and candidate is not None and candidate.output_type is not None:
                    if len(publications) != 1:
                        outputs[node_id] = WorkflowOutputReferenceError(
                            "output_reference_integrity", node_id
                        )
                        continue
                    descriptor = publications[0]
                else:
                    descriptor = attempt_local[-1] if attempt_local else None
            elif node_type in {"bash", "script"}:
                descriptor = (canonical or candidates)[-1] if candidates else None
            else:
                descriptor = canonical[-1] if canonical else None
            if descriptor is None:
                if strict_v3 and candidate is not None:
                    outputs[node_id] = WorkflowOutputReferenceError(
                        "output_reference_integrity", node_id
                    )
                continue
            expected_structured = snapshot.structured_outputs.get(node_id)
            if strict_v3 and (
                (expected_structured is None and candidate is not None
                 and candidate.schema_fingerprint is not None)
                or (
                    expected_structured is not None
                    and (
                        candidate is None
                        or candidate.schema_fingerprint
                        != expected_structured.schema_fingerprint
                    )
                )
            ):
                outputs[node_id] = WorkflowOutputReferenceError(
                    "output_reference_integrity", node_id
                )
                continue
            publication_id = descriptor.get("publication_id")
            producer_identity = None
            if strict_v3:
                try:
                    producer_identity = output_publication_identity(
                        node_id=node_id,
                        attempt_id=winning_attempt,
                        descriptor=descriptor,
                        candidate=candidate,
                    )
                except ValueError:
                    outputs[node_id] = WorkflowOutputReferenceError(
                        "output_reference_integrity", node_id
                    )
                    continue
            resolution_key = (
                root_identity,
                run_id,
                node_id,
                winning_attempt,
                descriptor.get("relative_path"),
                descriptor.get("media_type"),
                descriptor.get("size_bytes"),
                descriptor.get("sha256"),
                publication_id if isinstance(publication_id, str) else None,
                descriptor.get("content_name"),
                descriptor.get("schema_fingerprint"),
                descriptor.get("canonicalization_version"),
                descriptor.get("output_type"),
                *(
                    (
                        candidate.attempt_relative_path,
                        candidate.media_type,
                        candidate.size_bytes,
                        candidate.sha256,
                        candidate.schema_fingerprint,
                        candidate.canonicalization_version,
                        candidate.output_type,
                    )
                    if candidate is not None
                    else (None,) * 7
                ),
            )
            with self._output_resolution_lock:
                resolved = self._cached_resolved_output(resolution_key)
                if resolved is _OUTPUT_CACHE_MISS:
                    try:
                        resolved = resolve_node_output(
                            run_directory=run_directory,
                            node_id=node_id,
                            attempt_id=winning_attempt,
                            descriptor=descriptor,
                            candidate=candidate,
                            publication_id=(
                                publication_id
                                if isinstance(publication_id, str)
                                else None
                            ),
                            strict=strict_v3,
                        )
                    except WorkflowOutputReferenceError as exc:
                        if strict_v3:
                            outputs[node_id] = exc
                            continue
                        resolved = None
                    except ArchonOutputIntegrityError:
                        # Preserve Phase 2 missing-output outcomes. Phase 3
                        # makes integrity and missing references strict.
                        if strict_v3:
                            assert producer_identity is not None
                            outputs[node_id] = WorkflowOutputReferenceError(
                                "output_reference_integrity", node_id
                            )
                            continue
                        resolved = None
                    except ArchonOutputUnavailableError:
                        # Host-level read availability is retryable. Do not
                        # turn one transient failure into a stable cache miss.
                        if strict_v3:
                            outputs[node_id] = WorkflowOutputReferenceError(
                                "output_reference_temporarily_unavailable",
                                node_id,
                                producer_identity=producer_identity,
                            )
                        continue
                    if resolved is not None or not strict_v3:
                        self._cache_resolved_output(resolution_key, resolved)
            if resolved is not None:
                outputs[node_id] = resolved
        return outputs

    def _ensure_output_resolution_state(self) -> None:
        if hasattr(self, "_output_resolution_lock"):
            return
        self._output_resolution_lock = threading.RLock()
        self._resolved_output_cache = {}
        self._primary_output_candidates = {}
        self._output_resolution_cache_lru = OrderedDict()
        self._output_resolution_cache_bytes = 0
        self._output_resolution_cache_max_bytes = _OUTPUT_RESOLUTION_CACHE_MAX_BYTES

    def _bound_output_resolution_caches(self) -> None:
        while (
            self._output_resolution_cache_bytes
            > self._output_resolution_cache_max_bytes
            and self._output_resolution_cache_lru
        ):
            (kind, key), weight = self._output_resolution_cache_lru.popitem(
                last=False
            )
            self._output_resolution_cache_bytes -= weight
            if kind == "resolved":
                self._resolved_output_cache.pop(key, None)
            else:
                self._primary_output_candidates.pop(key, None)

    def _track_output_cache_entry(
        self,
        kind: str,
        key: tuple[object, ...],
        weight: int,
    ) -> None:
        cache_key = (kind, key)
        previous = self._output_resolution_cache_lru.pop(cache_key, 0)
        self._output_resolution_cache_bytes -= previous
        self._output_resolution_cache_lru[cache_key] = weight
        self._output_resolution_cache_bytes += weight
        self._bound_output_resolution_caches()

    def _remove_output_cache_entry(
        self, kind: str, key: tuple[object, ...]
    ) -> None:
        weight = self._output_resolution_cache_lru.pop((kind, key), 0)
        self._output_resolution_cache_bytes -= weight
        if kind == "resolved":
            self._resolved_output_cache.pop(key, None)
        else:
            self._primary_output_candidates.pop(key, None)

    def _purge_run_output_cache(self, run_id: str) -> None:
        self._ensure_output_resolution_state()
        with self._output_resolution_lock:
            for key in tuple(self._resolved_output_cache):
                if key[1] == run_id:
                    self._remove_output_cache_entry("resolved", key)
            for key in tuple(self._primary_output_candidates):
                if key[0] == run_id:
                    self._remove_output_cache_entry("candidate", key)

    def _purge_attempt_output_cache(self, claim: NodeClaim) -> None:
        candidate_key = (claim.run_id, claim.node_id, claim.attempt_id)
        self._remove_output_cache_entry("candidate", candidate_key)
        for key in tuple(self._resolved_output_cache):
            if key[1:4] == candidate_key:
                self._remove_output_cache_entry("resolved", key)

    def _purge_terminal_run_output_cache(self, run_id: str) -> None:
        try:
            terminal = self.store.load_run(run_id).get("status") in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }
        except (KeyError, OSError, RuntimeError, ValueError):
            return
        if not terminal:
            return
        with self._activity:
            if run_id in self._active_runs or run_id in self._submitted_runs:
                return
            self._purge_run_output_cache(run_id)

    def _touch_output_cache_entry(
        self, kind: str, key: tuple[object, ...]
    ) -> None:
        cache_key = (kind, key)
        if cache_key in self._output_resolution_cache_lru:
            self._output_resolution_cache_lru.move_to_end(cache_key)

    def _cached_resolved_output(
        self, key: tuple[object, ...]
    ) -> object:
        if key not in self._resolved_output_cache:
            return _OUTPUT_CACHE_MISS
        self._touch_output_cache_entry("resolved", key)
        return self._resolved_output_cache[key]

    def _cache_resolved_output(
        self,
        key: tuple[object, ...],
        resolved: ResolvedNodeOutput | None,
    ) -> None:
        self._resolved_output_cache[key] = resolved
        self._track_output_cache_entry(
            "resolved", key, _resolved_output_weight(key, resolved)
        )

    def _cached_primary_output_candidate(
        self, key: tuple[str, str, str]
    ) -> PrimaryOutputCandidate | None:
        candidate = self._primary_output_candidates.get(key)
        if candidate is not None:
            self._touch_output_cache_entry("candidate", key)
        return candidate

    def _cache_primary_output_candidate(
        self,
        key: tuple[str, str, str],
        candidate: PrimaryOutputCandidate,
    ) -> None:
        self._primary_output_candidates[key] = candidate
        self._track_output_cache_entry(
            "candidate", key, _primary_candidate_weight(key, candidate)
        )

    def _variables(
        self,
        projection: dict[str, object],
        run_directory: Path,
        *,
        sealed_resource_paths: frozenset[str] | None = None,
        sealed_resource_bytes: Mapping[str, bytes] | None = None,
        output_node_ids: Iterable[str] | None = None,
        resolved_outputs: Mapping[
            str, ResolvedNodeOutput | WorkflowOutputReferenceError
        ] | None = None,
    ):
        arguments = ""
        if sealed_resource_bytes is not None:
            resolver = ResourceResolver(
                run_directory,
                sealed_paths=sealed_resource_paths,
                sealed_bytes=sealed_resource_bytes,
            )
            manifest = json.loads(resolver.text("inputs.json"))
            entry = manifest.get("arguments")
            if isinstance(entry, dict):
                data = resolver.read_bytes(str(entry["relative_path"]))
                if len(data) > 500_000:
                    raise ValueError("workflow argument value exceeds 500000 bytes")
                arguments = data.decode("utf-8")
        else:
            manifest_path = run_directory / "inputs.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry = manifest.get("arguments")
                if isinstance(entry, dict):
                    arguments = self._read_text(
                        run_directory / entry["relative_path"]
                    )
        raw_outputs = (
            resolved_outputs
            if resolved_outputs is not None
            else self._output_values(
                projection,
                run_directory,
                node_ids=output_node_ids,
            )
        )
        outputs = {
            node: value
            if isinstance(value, str | ResolvedNodeOutput | WorkflowOutputReferenceError)
            else json.dumps(value, sort_keys=True)
            for node, value in raw_outputs.items()
        }
        language_snapshot = read_language_snapshot(projection.get("language"))
        return VariableContext(
            arguments=arguments,
            user_message=arguments,
            artifacts_dir=run_directory / "artifacts",
            workflow_id=str(projection["run_id"]),
            base_branch="base",
            docs_dir=run_directory / "docs",
            node_outputs=outputs,
            normalizer_version=(
                language_snapshot.normalizer_version
                if language_snapshot is not None
                else WORKFLOW_NORMALIZER_VERSION
            ),
        )

    @staticmethod
    def _predecessor_results(
        projection: Mapping[str, object],
        dependencies: Iterable[str],
        outputs: Mapping[str, object],
    ) -> dict[str, dict[str, object]]:
        results: dict[str, dict[str, object]] = {}
        nodes = projection.get("nodes", {})
        if not isinstance(nodes, Mapping):
            return results
        for dependency in dependencies:
            state = nodes.get(dependency)
            if not isinstance(state, Mapping):
                continue
            evidence = {
                field: state[field]
                for field in ("session_id", "cache_fingerprint")
                if field in state
            }
            output = outputs.get(dependency)
            if isinstance(output, ResolvedNodeOutput):
                evidence["output_evidence"] = MappingProxyType({
                    "media_type": output.media_type,
                    "size_bytes": len(output.canonical_bytes),
                    "sha256": output.sha256,
                    "node_id": output.node_id,
                    "attempt_id": output.attempt_id,
                    "publication_id": output.publication_id,
                })
            results[dependency] = evidence
        return results

    def _cancelled(self, run_id: str) -> bool:
        if self._shutdown.is_set():
            return True
        try:
            projection = self.store.load_run(run_id)
            return projection.get("desired_status") == "cancelled" or projection[
                "status"
            ] in {
                "cancelled",
                "abandoned",
                "interrupted",
            }
        except KeyError:
            return True

    def _cancellation_reason(self, run_id: str) -> str | None:
        if self._shutdown.is_set():
            return "shutdown"
        try:
            projection = self.store.load_run(run_id)
        except KeyError:
            return "interrupted"
        if projection.get("desired_status") == "cancelled":
            return "cancelled"
        status = projection["status"]
        return status if status in {"cancelled", "abandoned", "interrupted"} else None

    def _resolve_graph(self, run_id: str, nodes: Iterable[WorkflowNode]) -> None:
        while True:
            projection = self.store.load_run(run_id)
            run_directory = self.store.run_directory(run_id)
            language_snapshot = read_language_snapshot(projection.get("language"))
            strict_v3 = (
                language_snapshot is not None
                and language_snapshot.effective_profile
                is WorkflowLanguageProfile.ARCHON_2026_07
                and language_snapshot.normalizer_version == 3
            )
            outputs = (
                None
                if strict_v3
                else self._output_values(projection, run_directory)
            )

            def resolve_condition_output(node_id: str) -> object:
                output = self._output_values(
                    projection,
                    run_directory,
                    node_ids=(node_id,),
                ).get(node_id)
                if isinstance(output, ResolvedNodeOutput):
                    resolved_identities[node_id] = (
                        resolved_output_publication_identity(output)
                    )
                return output

            transitions: dict[str, tuple[str, str | None]] = {}
            condition_transitions: dict[str, tuple[str, str, str]] = {}
            for node in nodes:
                state = projection["nodes"][node.id]["state"]
                if state != "pending":
                    continue
                dependency_states = [
                    projection["nodes"][dependency]["state"]
                    for dependency in node.depends_on
                ]
                triggered = evaluate_trigger_rule(
                    str(node.options.get("trigger_rule", "all_success")),
                    dependency_states,
                )
                if triggered is None:
                    continue
                if not triggered:
                    transitions[node.id] = ("skipped", "trigger_rule_not_satisfied")
                    continue
                condition = node.options.get("when")
                if isinstance(condition, str):
                    if strict_v3:
                        if not self._revalidate_retained_output_resolution(
                            run_id,
                            node,
                            projection,
                            condition=True,
                        ):
                            continue
                        resolved_identities: dict[str, dict[str, object]] = {}
                        try:
                            matches = evaluate_v3_condition(
                                condition, resolve_condition_output
                            )
                        except (WorkflowConditionError, WorkflowOutputReferenceError) as exc:
                            if (
                                isinstance(exc, WorkflowOutputReferenceError)
                                and exc.code
                                == "output_reference_temporarily_unavailable"
                                and exc.producer_identity is not None
                            ):
                                current = self.store.load_run(run_id)["nodes"][node.id]
                                retained = current.get(
                                    "resolution_producer_identity"
                                )
                                if (
                                    isinstance(retained, Mapping)
                                    and retained != exc.producer_identity
                                    and any(
                                        retained == identity
                                        for identity in resolved_identities.values()
                                    )
                                ):
                                    self.store.clear_output_resolution(
                                        run_id,
                                        node.id,
                                        producer_identity=retained,
                                    )
                                self.store.defer_output_resolution(
                                    run_id,
                                    node.id,
                                    producer_identity=exc.producer_identity,
                                    now=self._utcnow(),
                                )
                                continue
                            condition_transitions[node.id] = (
                                "failed",
                                exc.code,
                                str(exc),
                            )
                            continue
                        current = self.store.load_run(run_id)["nodes"][node.id]
                        retained = current.get("resolution_producer_identity")
                        if isinstance(retained, Mapping):
                            self.store.clear_output_resolution(
                                run_id,
                                node.id,
                                producer_identity=retained,
                            )
                        if not matches:
                            condition_transitions[node.id] = (
                                "skipped",
                                "condition_false",
                                "condition evaluated false",
                            )
                            continue
                    else:
                        try:
                            assert outputs is not None
                            if not evaluate_condition(condition, outputs):
                                transitions[node.id] = ("skipped", "condition_false")
                                continue
                        except ConditionEvaluationError as exc:
                            transitions[node.id] = (
                                "skipped",
                                f"condition_evaluation_failed: {exc}",
                            )
                            continue
                transitions[node.id] = ("ready", None)
            if not transitions and not condition_transitions:
                self.store.finalize_if_complete(run_id)
                return
            for node_id, (state, code, message) in condition_transitions.items():
                self.store.transition_v3_condition_node(
                    run_id,
                    node_id,
                    state=state,
                    code=code,
                    message=message,
                )
            self.store.transition_pending_nodes(run_id, transitions)

    @staticmethod
    def _strict_reference_templates(
        node: WorkflowNode,
        *,
        run_directory: Path,
        sealed_resource_paths: frozenset[str] | None,
        sealed_resource_bytes: Mapping[str, bytes] | None,
    ) -> tuple[str, ...]:
        """Return authenticated non-condition templates without rendering them."""
        templates: list[str] = []
        if node.node_type in {"bash", "prompt"} and isinstance(node.value, str):
            templates.append(node.value)
        elif (
            node.node_type == "script"
            and isinstance(node.value, str)
            and is_inline_script(node.value)
        ):
            templates.append(node.value)
        elif node.node_type == "loop" and isinstance(node.value, Mapping):
            templates.extend(
                value
                for field in ("prompt", "until_bash")
                if isinstance((value := node.value.get(field)), str)
            )
        elif node.node_type == "approval" and isinstance(node.value, Mapping):
            message = node.value.get("message")
            if isinstance(message, str):
                templates.append(message)
            on_reject = node.value.get("on_reject")
            if isinstance(on_reject, Mapping):
                prompt = on_reject.get("prompt")
                if isinstance(prompt, str):
                    templates.append(prompt)
        elif (
            node.node_type == "command"
            and sealed_resource_paths is not None
            and sealed_resource_bytes is not None
        ):
            resolver = ResourceResolver(
                run_directory,
                sealed_paths=sealed_resource_paths,
                sealed_bytes=sealed_resource_bytes,
            )
            templates.append(resolver.command(str(node.value)).body)
        return tuple(templates)

    def _revalidate_retained_output_resolution(
        self,
        run_id: str,
        node: WorkflowNode,
        projection: Mapping[str, object],
        *,
        condition: bool,
    ) -> bool | ResolvedNodeOutput:
        """Fence a due wake to its retained producer before any other read."""
        current = self.store.load_run(run_id)["nodes"][node.id]
        retained = current.get("resolution_producer_identity")
        if not isinstance(retained, Mapping):
            return True
        producer_id = retained.get("node_id")
        if not isinstance(producer_id, str) or not producer_id:
            self.store.transition_v3_reference_node(
                run_id,
                node.id,
                code="output_reference_integrity",
                message="output reference producer identity is invalid",
            )
            return False

        output = self._output_values(
            projection,
            self.store.run_directory(run_id),
            node_ids=(producer_id,),
        ).get(producer_id)
        error = (
            output
            if isinstance(output, WorkflowOutputReferenceError)
            else None
        )
        if error is None and not isinstance(output, ResolvedNodeOutput):
            error = WorkflowOutputReferenceError(
                "output_reference_missing", producer_id
            )
        if error is not None:
            if (
                error.code == "output_reference_temporarily_unavailable"
                and error.producer_identity is not None
            ):
                self.store.defer_output_resolution(
                    run_id,
                    node.id,
                    producer_identity=error.producer_identity,
                    now=self._utcnow(),
                )
            elif condition:
                self.store.transition_v3_condition_node(
                    run_id,
                    node.id,
                    state="failed",
                    code=error.code,
                    message=str(error),
                )
            else:
                self.store.transition_v3_reference_node(
                    run_id,
                    node.id,
                    code=error.code,
                    message=str(error),
                )
            return False

        assert isinstance(output, ResolvedNodeOutput)
        self.store.clear_output_resolution(
            run_id,
            node.id,
            producer_identity=resolved_output_publication_identity(output),
        )
        refreshed = self.store.load_run(run_id)["nodes"][node.id]
        if (
            refreshed.get("state") in {"pending", "ready"}
            and "resolution_producer_identity" not in refreshed
        ):
            return output
        return False

    def _preflight_strict_node_references(
        self,
        run_id: str,
        node: WorkflowNode,
        package: WorkflowPackage,
        projection: Mapping[str, object],
        *,
        sealed_resource_paths: frozenset[str] | None,
        sealed_resource_bytes: Mapping[str, bytes] | None,
    ) -> bool | _StrictReferenceSnapshot:
        """Resolve v3 references before claim without rendering Task 7 consumers."""
        if (
            package.language.effective_profile
            is not WorkflowLanguageProfile.ARCHON_2026_07
            or package.language.normalizer_version != 3
        ):
            return True
        retained_output = self._revalidate_retained_output_resolution(
            run_id,
            node,
            projection,
            condition=False,
        )
        if not retained_output:
            return False
        run_directory = self.store.run_directory(run_id)
        reference_keys: list[tuple[str, tuple[str, ...]]] = []
        for template in self._strict_reference_templates(
            node,
            run_directory=run_directory,
            sealed_resource_paths=sealed_resource_paths,
            sealed_resource_bytes=sealed_resource_bytes,
        ):
            template_references = (
                bash_output_references(template)
                if node.node_type == "bash"
                else tuple(iter_output_references(template, normalizer_version=3))
            )
            reference_keys.extend(
                (reference.node_id, reference.path) for reference in template_references
            )
        references = tuple(dict.fromkeys(reference_keys))
        retained_node_id = (
            retained_output.node_id
            if isinstance(retained_output, ResolvedNodeOutput)
            else None
        )
        remaining_dependencies = tuple(
            dependency
            for dependency in node.depends_on
            if dependency != retained_node_id
        )
        outputs = (
            self._output_values(
                projection,
                run_directory,
                node_ids=remaining_dependencies,
            )
            if remaining_dependencies
            else {}
        )
        if isinstance(retained_output, ResolvedNodeOutput):
            outputs[retained_output.node_id] = retained_output
        resolved_identities: list[dict[str, object]] = []
        resolved_references: dict[
            tuple[str, tuple[str, ...]], ResolvedOutputReference
        ] = {}
        try:
            for producer_id, path in references:
                output = outputs.get(producer_id)
                if isinstance(output, WorkflowOutputReferenceError):
                    raise output
                resolved_references[(producer_id, path)] = resolve_output_reference(
                    output if isinstance(output, ResolvedNodeOutput) else None,
                    node_id=producer_id,
                    path=path,
                )
                assert isinstance(output, ResolvedNodeOutput)
                resolved_identities.append(
                    resolved_output_publication_identity(output)
                )
        except WorkflowOutputReferenceError as exc:
            if (
                exc.code == "output_reference_temporarily_unavailable"
                and exc.producer_identity is not None
            ):
                current = self.store.load_run(run_id)["nodes"][node.id]
                retained = current.get("resolution_producer_identity")
                if (
                    isinstance(retained, Mapping)
                    and retained != exc.producer_identity
                    and any(retained == identity for identity in resolved_identities)
                ):
                    self.store.clear_output_resolution(
                        run_id,
                        node.id,
                        producer_identity=retained,
                    )
                self.store.defer_output_resolution(
                    run_id,
                    node.id,
                    producer_identity=exc.producer_identity,
                    now=self._utcnow(),
                )
            else:
                self.store.transition_v3_reference_node(
                    run_id,
                    node.id,
                    code=exc.code,
                    message=str(exc),
                )
            return False

        current = self.store.load_run(run_id)["nodes"][node.id]
        retained = current.get("resolution_producer_identity")
        if isinstance(retained, Mapping):
            if any(retained == identity for identity in resolved_identities):
                self.store.clear_output_resolution(
                    run_id,
                    node.id,
                    producer_identity=retained,
                )
            else:
                self.store.transition_v3_reference_node(
                    run_id,
                    node.id,
                    code="output_reference_integrity",
                    message="output reference producer identity changed during resolution",
                )
                return False
        return _StrictReferenceSnapshot(
            outputs=MappingProxyType(dict(outputs)),
            references=MappingProxyType(resolved_references),
        )

    def _load_verified_run_package(
        self,
        run_id: str,
        *,
        read_budget: WorkflowResourceReadBudget | None = None,
    ) -> tuple[WorkflowPackage, frozenset[str], Mapping[str, bytes]]:
        run_directory = self.store.run_directory(run_id)
        definition = run_directory / "definition.yaml"
        policy = run_directory / "policy.yaml"
        projection = self.store.load_run(run_id)
        resources_path = run_directory / "resources.json"
        read_budget = read_budget or WorkflowResourceReadBudget(
            max_file_bytes=WORKFLOW_RESOURCE_MAX_FILE_BYTES,
            max_total_bytes=WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
            max_files=WORKFLOW_RESOURCE_MAX_FILES,
        )

        def integrity_error(message: str) -> WorkflowLanguageCompatibilityError:
            return WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                message,
            )

        initial_paths = ["definition.yaml", "resources.json"]
        if policy.is_file() or policy.is_symlink():
            initial_paths.append("policy.yaml")
        initial_bytes = self._stable_snapshot_bytes(
            run_directory,
            initial_paths,
            read_budget=read_budget,
            legacy_capacity=projection.get("snapshot_format_version") is None,
        )
        definition_bytes = initial_bytes["definition.yaml"]
        policy_bytes = initial_bytes.get("policy.yaml", b"{}\n")
        resources_bytes = initial_bytes["resources.json"]

        expected_policy_digest = projection.get("policy_digest")
        expected_resources_digest = projection.get("input_manifest_digest")
        if (
            not isinstance(expected_policy_digest, str)
            or _SHA256.fullmatch(expected_policy_digest) is None
            or not isinstance(expected_resources_digest, str)
            or _SHA256.fullmatch(expected_resources_digest) is None
            or hashlib.sha256(policy_bytes).hexdigest() != expected_policy_digest
            or hashlib.sha256(resources_bytes).hexdigest()
            != expected_resources_digest
        ):
            raise integrity_error("sealed workflow snapshot digest changed")

        try:
            resources = json.loads(resources_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise integrity_error("sealed workflow resources are malformed") from exc
        if not isinstance(resources, Mapping):
            raise integrity_error("sealed workflow resources must be a mapping")
        projected_snapshot = read_language_snapshot(projection.get("language"))
        snapshot = read_language_snapshot(resources.get("language"))
        if projected_snapshot != snapshot:
            raise integrity_error(
                "projected workflow language identity differs from sealed resources"
            )

        projected_snapshot_digest = projection.get("sealed_snapshot_digest")
        verified_sealed_paths: frozenset[str] | None = None
        verified_sealed_bytes: dict[str, bytes] | None = None
        if snapshot is not None:
            if (
                not isinstance(projected_snapshot_digest, str)
                or _SHA256.fullmatch(projected_snapshot_digest) is None
            ):
                raise integrity_error("sealed workflow tree identity is missing")
            from plugins.workflow.scheduled_revalidation import (
                ScheduledRunRevalidationError,
                read_sealed_snapshot_paths,
                sealed_snapshot_digest,
            )

            try:
                sealed_paths = read_sealed_snapshot_paths(
                    resources.get("sealed_paths")
                )
                # Validate the exact tree shape (including mutable-root symlinks and
                # unsealed precedence shadows) before binding the authoritative bytes.
                sealed_snapshot_digest(
                    run_directory,
                    relative_paths=sealed_paths,
                    read_budget=read_budget,
                    allow_unsealed_regular_files=(
                        snapshot.effective_profile
                        is WorkflowLanguageProfile.HERMES_LEGACY
                    ),
                )
                verified_sealed_bytes = self._stable_snapshot_bytes(
                    run_directory,
                    sealed_paths,
                    read_budget=read_budget,
                )
            except (ScheduledRunRevalidationError, WorkflowResourceCapacityError) as exc:
                raise integrity_error("sealed workflow tree is unreadable") from exc
            actual_snapshot_digest = self._snapshot_digest_from_bytes(
                verified_sealed_bytes
            )
            if actual_snapshot_digest != projected_snapshot_digest:
                raise integrity_error("sealed workflow tree identity changed")
            verified_sealed_paths = frozenset(sealed_paths)
            if (
                verified_sealed_bytes.get("definition.yaml") != definition_bytes
                or verified_sealed_bytes.get("resources.json") != resources_bytes
                or (
                    policy.is_file()
                    and verified_sealed_bytes.get("policy.yaml") != policy_bytes
                )
            ):
                raise integrity_error(
                    "sealed workflow snapshot changed during authentication"
                )
        elif (
            projected_snapshot_digest is not None
            or projection.get("snapshot_format_version") is not None
            or resources.get("sealed_paths") is not None
        ):
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_missing",
                "new workflow snapshot is missing admitted language metadata",
            )

        legacy_snapshot_verified = False
        legacy_direct_paths: frozenset[str] = frozenset()
        legacy_preparsed_paths: frozenset[str] | None = None
        legacy_preparsed_bytes: Mapping[str, bytes] | None = None
        legacy_workflow_identity: str | None = None
        expected_package_digest = projection.get("definition_digest")
        if (
            not isinstance(expected_package_digest, str)
            or _SHA256.fullmatch(expected_package_digest) is None
        ):
            raise integrity_error("trusted workflow package identity is missing")
        if snapshot is None:
            (
                legacy_snapshot_verified,
                legacy_direct_paths,
            ) = self._verify_legacy_journaled_seals(
                projection,
                run_directory=run_directory,
                definition_bytes=definition_bytes,
                policy_bytes=policy_bytes,
                resources_bytes=resources_bytes,
                policy_present=policy.is_file(),
            )
            legacy_preparsed_paths = self._legacy_raw_package_paths(run_directory)
            legacy_preparsed_bytes = self._legacy_raw_package_bytes(
                run_directory,
                legacy_preparsed_paths,
                read_budget=read_budget,
            )
            legacy_auxiliary_bytes = self._legacy_auxiliary_bytes(
                run_directory,
                resources,
                resources_bytes,
                read_budget=read_budget,
            )
            verified_sealed_bytes = {
                **legacy_preparsed_bytes,
                **legacy_auxiliary_bytes,
            }
            if (
                legacy_preparsed_bytes.get("definition.yaml") != definition_bytes
                or (
                    policy.is_file()
                    and legacy_preparsed_bytes.get("policy.yaml") != policy_bytes
                )
            ):
                raise integrity_error(
                    "pre-language workflow package changed during authentication"
                )
            metadata = projection.get("run_metadata")
            admitted_whole_digest = (
                metadata.get("sealed_snapshot_digest")
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                admitted_whole_digest is not None
                and self._snapshot_digest_from_bytes(verified_sealed_bytes)
                != admitted_whole_digest
            ):
                raise integrity_error(
                    "pre-language workflow snapshot changed during authentication"
                )
            legacy_workflow_identity = self._verify_legacy_preparse_identity(
                projection,
                run_directory=run_directory,
                expected_package_digest=expected_package_digest,
                package_paths=legacy_preparsed_paths,
                package_bytes=legacy_preparsed_bytes,
                snapshot_verified=legacy_snapshot_verified,
                directly_verified_paths=legacy_direct_paths,
            )

        package = load_workflow_snapshot(
            definition,
            workflow_bytes=definition_bytes,
            sidecar_bytes=policy_bytes if policy.is_file() else None,
            normalizer_version=(
                snapshot.normalizer_version
                if snapshot is not None
                else WORKFLOW_NORMALIZER_VERSION
            ),
        )
        if snapshot is None and (
            package.language.effective_profile
            is not WorkflowLanguageProfile.HERMES_LEGACY
        ):
            raise WorkflowLanguageCompatibilityError(
                "workflow_language_snapshot_missing",
                "declared Archon workflow is missing admitted language metadata",
            )
        if snapshot is not None:
            verify_language_snapshot(
                package,
                expected_package_digest,
                snapshot,
            )
            if snapshot.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY:
                self._legacy_package_with_valid_resource_precedence(
                    package,
                    run_directory=run_directory,
                )
        else:
            package = replace(
                package,
                root=run_directory,
                workflow_path=definition,
                sidecar_path=policy if policy.is_file() else None,
            )
            verified_sealed_paths = self._legacy_resource_paths(
                package,
                run_directory=run_directory,
                authenticated_bytes=legacy_preparsed_bytes,
            )
            self._verify_legacy_postparse_identity(
                package,
                projection=projection,
                run_directory=run_directory,
                expected_package_digest=expected_package_digest,
                sealed_paths=verified_sealed_paths,
                preparsed_paths=legacy_preparsed_paths,
                workflow_identity=legacy_workflow_identity,
                authenticated_bytes=legacy_preparsed_bytes,
            )
            verified_sealed_paths = frozenset(verified_sealed_bytes)
        if verified_sealed_paths is None:
            raise integrity_error("verified workflow resource identity is missing")
        if verified_sealed_bytes is None:
            raise integrity_error("verified workflow resource bytes are missing")
        if (
            package.language.effective_profile
            is WorkflowLanguageProfile.ARCHON_2026_07
        ):
            resolver = ResourceResolver(
                run_directory,
                sealed_paths=verified_sealed_paths,
                sealed_bytes=verified_sealed_bytes,
            )
            command_bodies = {
                node.id: resolver.command(str(node.value)).body
                for node in package.definition.nodes
                if node.node_type == "command"
            }
            validate_authenticated_command_references(package, command_bodies)
        return (
            package,
            verified_sealed_paths,
            MappingProxyType(dict(verified_sealed_bytes)),
        )

    def _load_run_package(self, run_id: str) -> WorkflowPackage:
        """Compatibility wrapper returning only the verified workflow package."""
        package, _sealed_paths, _sealed_bytes = self._load_verified_run_package(run_id)
        return package

    def verified_always_run_nodes(self, run_id: str) -> frozenset[str]:
        """Return resume policy only after authenticating the sealed package."""
        package, _sealed_paths, _sealed_bytes = self._load_verified_run_package(run_id)
        return frozenset(
            node.id
            for node in package.definition.nodes
            if bool(node.options.get("always_run"))
        )

    @staticmethod
    def _legacy_resource_paths(
        package: WorkflowPackage,
        *,
        run_directory: Path,
        authenticated_bytes: Mapping[str, bytes],
    ) -> frozenset[str]:
        """Bind legacy resolution while rejecting ambiguous shadow candidates."""
        sealed_package = RunScheduler._legacy_package_with_valid_resource_precedence(
            package,
            run_directory=run_directory,
        )

        from plugins.workflow.trust import (
            WorkflowResourceReadBudget,
            compute_package_digest,
        )

        return frozenset(
            compute_package_digest(
                sealed_package,
                read_budget=WorkflowResourceReadBudget.from_authenticated(
                    run_directory, authenticated_bytes
                ),
            ).covered_relative_paths
        )

    @staticmethod
    def _legacy_package_with_valid_resource_precedence(
        package: WorkflowPackage,
        *,
        run_directory: Path,
    ) -> WorkflowPackage:
        """Reject live legacy paths that could shadow authenticated resources."""
        sealed_package = replace(package, root=run_directory)

        def reject_ambiguous(candidates: tuple[Path, ...], kind: str) -> None:
            existing = tuple(
                path
                for path in dict.fromkeys(candidates)
                if path.exists() or path.is_symlink()
            )
            if len(existing) > 1:
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    f"legacy workflow has ambiguous {kind} resources",
                )

        for node in sealed_package.definition.nodes:
            if (
                node.node_type == "script"
                and isinstance(node.value, str)
                and not is_inline_script(node.value)
            ):
                base = sealed_package.root / "scripts" / node.value
                if node.options["runtime"] == "uv":
                    reject_ambiguous((base, base.with_suffix(".py")), "script")
                else:
                    reject_ambiguous(
                        (base, base.with_suffix(".ts"), base.with_suffix(".js")),
                        "script",
                    )
            mcp_value = node.options.get("mcp")
            references = (
                (mcp_value,)
                if isinstance(mcp_value, str)
                else tuple(mcp_value or ())
                if isinstance(mcp_value, tuple)
                else ()
            )
            for reference in references:
                direct = sealed_package.root / reference
                reject_ambiguous(
                    (
                        direct,
                        sealed_package.root / "mcp" / reference,
                        (sealed_package.root / "mcp" / reference).with_suffix(
                            ".yaml"
                        ),
                    ),
                    "MCP",
                )

        return sealed_package

    @staticmethod
    def _legacy_raw_package_paths(run_directory: Path) -> frozenset[str]:
        """Inventory the bounded historical package closure without parsing YAML."""
        paths: set[str] = set()
        pending = [run_directory]
        while pending:
            directory = pending.pop()
            try:
                children = tuple(os.scandir(directory))
            except OSError as exc:
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "pre-language workflow package is unreadable",
                ) from exc
            for entry in children:
                path = Path(entry.path)
                relative = path.relative_to(run_directory).as_posix()
                relative_path = PurePosixPath(relative)
                first_part = relative_path.parts[0]
                try:
                    if entry.is_symlink():
                        raise WorkflowLanguageCompatibilityError(
                            "workflow_snapshot_integrity_mismatch",
                            "pre-language workflow package contains a symlink",
                        )
                    if entry.is_dir(follow_symlinks=False):
                        if first_part not in _LEGACY_NON_PACKAGE_ROOTS:
                            pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        raise WorkflowLanguageCompatibilityError(
                            "workflow_snapshot_integrity_mismatch",
                            "pre-language workflow package contains a special file",
                        )
                except OSError as exc:
                    raise WorkflowLanguageCompatibilityError(
                        "workflow_snapshot_integrity_mismatch",
                        "pre-language workflow package is unreadable",
                    ) from exc
                if (
                    relative in _LEGACY_NON_PACKAGE_FILES
                    or first_part in _LEGACY_NON_PACKAGE_ROOTS
                ):
                    continue
                if (
                    len(relative) > _LEGACY_PACKAGE_PATH_CHARS
                    or relative_path.is_absolute()
                    or relative_path.as_posix() != relative
                    or any(part in {"", ".", ".."} for part in relative_path.parts)
                ):
                    raise WorkflowLanguageCompatibilityError(
                        "workflow_snapshot_integrity_mismatch",
                        "pre-language workflow package path is invalid",
                    )
                paths.add(relative)
                if len(paths) > _LEGACY_PACKAGE_PATHS:
                    raise WorkflowLanguageCompatibilityError(
                        "workflow_snapshot_integrity_mismatch",
                        "pre-language workflow package has too many resources",
                    )
        if "definition.yaml" not in paths:
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow definition is missing",
            )
        for relative in paths:
            candidate = PurePosixPath(relative)
            if (
                candidate.parts[0] == "scripts"
                and candidate.suffix in {".js", ".py", ".ts"}
                and candidate.with_suffix("").as_posix() in paths
            ) or (
                candidate.parts[0] == "mcp"
                and candidate.suffix == ".yaml"
                and candidate.with_suffix("").as_posix() in paths
            ):
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "pre-language workflow has ambiguous executable resources",
                )
        return frozenset(paths)

    @staticmethod
    def _legacy_raw_package_bytes(
        run_directory: Path,
        package_paths: frozenset[str],
        *,
        read_budget: WorkflowResourceReadBudget | None = None,
    ) -> Mapping[str, bytes]:
        """Read one admission-bounded byte set for pre-parse authentication."""
        return RunScheduler._stable_snapshot_bytes(
            run_directory,
            package_paths,
            read_budget=read_budget,
            legacy_capacity=True,
        )

    @staticmethod
    def _snapshot_digest_from_bytes(resources: Mapping[str, bytes]) -> str:
        digest = hashlib.sha256()
        for relative in sorted(resources):
            data = resources[relative]
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(data)).encode("ascii"))
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _stable_snapshot_bytes(
        run_directory: Path,
        paths: Iterable[str],
        *,
        read_budget: WorkflowResourceReadBudget | None = None,
        legacy_capacity: bool = False,
    ) -> dict[str, bytes]:
        """Read the exact sealed path set with per-file race detection."""
        budget = read_budget or WorkflowResourceReadBudget(
            max_file_bytes=WORKFLOW_RESOURCE_MAX_FILE_BYTES,
            max_total_bytes=WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
            max_files=WORKFLOW_RESOURCE_MAX_FILES,
        )
        result: dict[str, bytes] = {}
        for relative in sorted(paths):
            path = run_directory / relative
            try:
                data = budget.read(path, verify_cached_identity=True)
            except WorkflowResourceCapacityError as exc:
                if legacy_capacity:
                    raise WorkflowLanguageCompatibilityError(
                        "workflow_legacy_snapshot_unverifiable",
                        "pre-language workflow exceeds the authenticated resource "
                        "limit; re-trust the installed workflow and start a new run",
                    ) from exc
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "sealed workflow snapshot exceeds the authenticated resource limit",
                ) from exc
            except OSError as exc:
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "sealed workflow snapshot is unreadable",
                ) from exc
            result[relative] = data
        return result

    @staticmethod
    def _legacy_auxiliary_bytes(
        run_directory: Path,
        resources: Mapping[str, object],
        resources_bytes: bytes,
        *,
        read_budget: WorkflowResourceReadBudget | None = None,
    ) -> dict[str, bytes]:
        """Authenticate exact inputs and generated skills from resources.json."""
        malformed = WorkflowLanguageCompatibilityError(
            "workflow_snapshot_integrity_mismatch",
            "pre-language workflow auxiliary resource identity is malformed",
        )

        def digest_map(value: object, prefix: str, *, nested: bool) -> dict[str, str]:
            if not isinstance(value, Mapping):
                raise malformed
            expected: dict[str, str] = {}
            for name, digest in value.items():
                if (
                    not isinstance(name, str)
                    or not isinstance(digest, str)
                    or _SHA256.fullmatch(digest) is None
                    or "\\" in name
                    or "\0" in name
                ):
                    raise malformed
                parts = PurePosixPath(name).parts
                expected_parts = 2 if nested else 1
                if (
                    len(parts) != expected_parts
                    or any(part in {"", ".", ".."} for part in parts)
                    or PurePosixPath(name).as_posix() != name
                ):
                    raise malformed
                expected[f"{prefix}/{name}.md"] = digest
            return expected

        expected: dict[str, str] = {}
        expected.update(digest_map(resources.get("node_skills"), "node-skills", nested=False))
        expected.update(
            digest_map(
                resources.get("node_agent_skills"),
                "node-agent-skills",
                nested=True,
            )
        )
        inputs_digest = resources.get("inputs_sha256")
        if not isinstance(inputs_digest, str) or _SHA256.fullmatch(inputs_digest) is None:
            raise malformed
        inputs_map = RunScheduler._stable_snapshot_bytes(
            run_directory,
            ("inputs.json",),
            read_budget=read_budget,
            legacy_capacity=True,
        )
        inputs_bytes = inputs_map["inputs.json"]
        if not hmac.compare_digest(hashlib.sha256(inputs_bytes).hexdigest(), inputs_digest):
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow inputs manifest differs from admitted bytes",
            )
        try:
            inputs = json.loads(inputs_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise malformed from exc
        if not isinstance(inputs, Mapping):
            raise malformed
        for record in inputs.values():
            if not isinstance(record, Mapping):
                raise malformed
            relative = record.get("relative_path")
            digest = record.get("sha256")
            if (
                not isinstance(relative, str)
                or not relative.startswith("inputs/")
                or not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
            ):
                raise malformed
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or path.as_posix() != relative
                or any(part in {"", ".", ".."} for part in path.parts)
                or relative in expected
            ):
                raise malformed
            expected[relative] = digest

        actual: set[str] = set()
        for root_name in ("inputs", "node-skills", "node-agent-skills"):
            root = run_directory / root_name
            if not root.exists():
                continue
            if root.is_symlink() or not root.is_dir():
                raise malformed
            pending = [root]
            while pending:
                directory = pending.pop()
                try:
                    children = tuple(os.scandir(directory))
                except OSError as exc:
                    raise malformed from exc
                for entry in children:
                    path = Path(entry.path)
                    relative = path.relative_to(run_directory).as_posix()
                    try:
                        if entry.is_symlink():
                            raise malformed
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            actual.add(relative)
                        else:
                            raise malformed
                    except OSError as exc:
                        raise malformed from exc
                    if len(actual) > _LEGACY_PACKAGE_PATHS:
                        raise malformed
        if actual != set(expected):
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow auxiliary resources differ from admitted paths",
            )
        authenticated = dict(
            RunScheduler._legacy_raw_package_bytes(
                run_directory,
                frozenset(expected),
                read_budget=read_budget,
            )
        )
        for relative, expected_digest in expected.items():
            if not hmac.compare_digest(
                hashlib.sha256(authenticated[relative]).hexdigest(), expected_digest
            ):
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "pre-language workflow auxiliary resource differs from admitted bytes",
                )
        authenticated["inputs.json"] = inputs_bytes
        authenticated["resources.json"] = resources_bytes
        return authenticated

    @staticmethod
    def _verify_legacy_journaled_seals(
        projection: Mapping[str, object],
        *,
        run_directory: Path,
        definition_bytes: bytes,
        policy_bytes: bytes,
        resources_bytes: bytes,
        policy_present: bool,
    ) -> tuple[bool, frozenset[str]]:
        """Verify every journaled pre-language byte seal before parsing YAML."""
        metadata = projection.get("run_metadata")
        if not isinstance(metadata, Mapping):
            return False, frozenset()

        verified_paths: set[str] = set()
        for name, relative, data in (
            ("sealed_definition_digest", "definition.yaml", definition_bytes),
            ("sealed_policy_digest", "policy.yaml", policy_bytes),
            ("sealed_input_digest", "resources.json", resources_bytes),
        ):
            admitted_digest = metadata.get(name)
            if admitted_digest is None:
                continue
            if (
                not isinstance(admitted_digest, str)
                or _SHA256.fullmatch(admitted_digest) is None
                or hashlib.sha256(data).hexdigest() != admitted_digest
            ):
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    f"pre-language workflow {name} differs from admitted bytes",
                )
            if relative != "policy.yaml" or policy_present:
                verified_paths.add(relative)

        admitted_snapshot_digest = metadata.get("sealed_snapshot_digest")
        if admitted_snapshot_digest is None:
            return False, frozenset(verified_paths)
        if (
            not isinstance(admitted_snapshot_digest, str)
            or _SHA256.fullmatch(admitted_snapshot_digest) is None
        ):
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow snapshot identity is malformed",
            )
        # The caller compares this seal with the complete shared-budget byte map
        # before treating this provisional proof as authorization. Reopening the
        # tree here would create a second, independently unbounded read budget.
        del run_directory
        return True, frozenset(verified_paths)

    @staticmethod
    def _legacy_identity_candidates(
        projection: Mapping[str, object],
    ) -> tuple[str, ...]:
        """Return only bounded path identities carried by the admission journal."""
        identity_candidates: set[str] = set()
        projected_workflow = projection.get("workflow")
        workflow_name = (
            projected_workflow if isinstance(projected_workflow, str) else ""
        )
        if (
            workflow_name
            and "/" not in workflow_name
            and "\\" not in workflow_name
            and workflow_name not in {".", ".."}
        ):
            for suffix in (".yaml", ".yml"):
                identity_candidates.add(f"{workflow_name}{suffix}")
                identity_candidates.add(f"workflows/{workflow_name}{suffix}")
        metadata = projection.get("run_metadata")
        if isinstance(metadata, Mapping):
            catalog_relative = metadata.get("catalog_source_relative")
            if isinstance(catalog_relative, str):
                identity_candidates.add(f"workflows/{catalog_relative}")
            workflow_relative = metadata.get("workflow_path")
            if isinstance(workflow_relative, str):
                identity_candidates.add(workflow_relative)
        return tuple(sorted(identity_candidates)[:8])

    @staticmethod
    def _verify_legacy_preparse_identity(
        projection: Mapping[str, object],
        *,
        run_directory: Path,
        expected_package_digest: str,
        package_paths: frozenset[str],
        package_bytes: Mapping[str, bytes],
        snapshot_verified: bool,
        directly_verified_paths: frozenset[str],
    ) -> str | None:
        """Authenticate every executable byte before allowing a YAML parse."""
        if snapshot_verified or package_paths.issubset(directly_verified_paths):
            return None
        matches = tuple(
            identity
            for identity in RunScheduler._legacy_identity_candidates(projection)
            if RunScheduler._legacy_package_digest_for_identity(
                run_directory,
                sealed_paths=package_paths,
                workflow_identity=identity,
                resource_bytes=package_bytes,
            )
            == expected_package_digest
        )
        if len(matches) == 1:
            return matches[0]
        raise WorkflowLanguageCompatibilityError(
            "workflow_legacy_snapshot_unverifiable",
            "pre-language workflow snapshot cannot be authenticated from its "
            "admission evidence; re-trust the installed workflow and start a new run",
        )

    @staticmethod
    def _verify_legacy_postparse_identity(
        package: WorkflowPackage,
        *,
        projection: Mapping[str, object],
        run_directory: Path,
        expected_package_digest: str,
        sealed_paths: frozenset[str],
        preparsed_paths: frozenset[str] | None,
        workflow_identity: str | None,
        authenticated_bytes: Mapping[str, bytes],
    ) -> None:
        """Require parsed closure equality and repeat raw authentication for races."""
        if preparsed_paths is None or sealed_paths != preparsed_paths:
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow parsed resource closure differs from sealed bytes",
            )
        projected_workflow = projection.get("workflow")
        workflow_name = (
            projected_workflow if isinstance(projected_workflow, str) else ""
        )
        if package.definition.name != workflow_name:
            raise WorkflowLanguageCompatibilityError(
                "workflow_snapshot_integrity_mismatch",
                "pre-language workflow name differs from admitted identity",
            )
        if workflow_identity is not None:
            if RunScheduler._legacy_package_digest_for_identity(
                run_directory,
                sealed_paths=sealed_paths,
                workflow_identity=workflow_identity,
                resource_bytes=authenticated_bytes,
            ) != expected_package_digest:
                raise WorkflowLanguageCompatibilityError(
                    "workflow_snapshot_integrity_mismatch",
                    "pre-language workflow package changed during verification",
                )

    @staticmethod
    def _legacy_package_digest_for_identity(
        run_directory: Path,
        *,
        sealed_paths: frozenset[str],
        workflow_identity: str,
        resource_bytes: Mapping[str, bytes] | None = None,
    ) -> str | None:
        """Recompute the historical package hash for one bounded path identity."""
        if (
            not workflow_identity
            or len(workflow_identity) > 512
            or "\\" in workflow_identity
            or "\0" in workflow_identity
        ):
            return None
        identity_path = PurePosixPath(workflow_identity)
        if (
            identity_path.is_absolute()
            or identity_path.as_posix() != workflow_identity
            or any(part in {"", ".", ".."} for part in identity_path.parts)
            or identity_path.suffix not in {".yaml", ".yml"}
        ):
            return None
        if resource_bytes is not None and frozenset(resource_bytes) != sealed_paths:
            return None
        sidecar_identity = identity_path.with_name(
            f"{identity_path.stem}.hermes.yaml"
        ).as_posix()
        resources: dict[str, bytes] = {}
        for relative in sealed_paths:
            identity = (
                workflow_identity
                if relative == "definition.yaml"
                else sidecar_identity
                if relative == "policy.yaml"
                else relative
            )
            if identity in resources:
                return None
            if resource_bytes is None:
                path = run_directory / relative
                try:
                    before = path.stat()
                    if path.is_symlink() or not path.is_file():
                        return None
                    data = path.read_bytes()
                    after = path.stat()
                except OSError:
                    return None
                if (
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    )
                    != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    )
                    or len(data) != before.st_size
                ):
                    return None
            else:
                data = resource_bytes.get(relative)
                if not isinstance(data, bytes):
                    return None
            resources[identity] = data
        digest = hashlib.sha256()
        for identity in sorted(resources):
            data = resources[identity]
            digest.update(identity.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(data)).encode("ascii"))
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
        return digest.hexdigest()

    def _run_execution_limits(self, package: WorkflowPackage) -> RunExecutionLimits:
        limits = package.sidecar.get("limits", {})
        resources = package.sidecar.get("resource_limits", {})
        if not isinstance(limits, Mapping) or not isinstance(resources, Mapping):
            raise ValueError("workflow sidecar limits must contain mappings")
        return RunExecutionLimits.resolve(
            WorkflowRuntimeConfig(
                **{
                    name: getattr(self.profile_execution_limits, name)
                    for name in self.profile_execution_limits.__dataclass_fields__
                }
            ),
            sidecar_limits=limits,
            sidecar_resources=resources,
        )

    def _run_execution_limits_with_sealed_phase3(
        self,
        package: WorkflowPackage,
        semantics: Phase3ExecutionSemantics,
    ) -> RunExecutionLimits:
        """Resolve legacy resource controls without rereading v3 semantic fields."""
        sidecar_limits = package.sidecar.get("limits", {})
        sidecar_resources = package.sidecar.get("resource_limits", {})
        if not isinstance(sidecar_limits, Mapping) or not isinstance(
            sidecar_resources, Mapping
        ):
            raise ValueError("workflow sidecar limits must contain mappings")
        sealed_names = {
            "ai_idle_timeout_seconds",
            "ai_wall_timeout_seconds",
            "provider_request_timeout_seconds",
            "subprocess_timeout_seconds",
            "combined_retries",
        }
        profile_values = {
            name: getattr(self.profile_execution_limits, name)
            for name in self.profile_execution_limits.__dataclass_fields__
        }
        profile_values.update({
            "ai_idle_timeout_seconds": semantics.limits[
                "ai_idle_timeout_seconds"
            ],
            "ai_wall_timeout_seconds": semantics.limits[
                "ai_wall_timeout_seconds"
            ],
            "provider_request_timeout_seconds": semantics.limits[
                "provider_request_timeout_seconds"
            ],
            "subprocess_timeout_seconds": semantics.limits[
                "subprocess_timeout_seconds"
            ],
            "combined_retries": semantics.limits["combined_total_attempts"],
        })
        base = RunExecutionLimits.resolve(
            WorkflowRuntimeConfig(**profile_values),
            sidecar_limits={
                name: value
                for name, value in sidecar_limits.items()
                if name not in sealed_names
            },
            sidecar_resources=sidecar_resources,
        )
        return semantics.to_run_execution_limits(base=base)

    def _prepare_run_package(
        self,
        run_id: str,
        schedule_revalidation,
        *,
        expected_state_version: int | None = None,
    ):
        try:
            read_budget = (
                self.store._scheduled_promotion_read_budget(
                    schedule_revalidation,
                    run_id,
                )
                if schedule_revalidation is not None
                else None
            )
            if read_budget is None:
                package, sealed_paths, sealed_bytes = (
                    self._load_verified_run_package(run_id)
                )
            else:
                package, sealed_paths, sealed_bytes = (
                    self._load_verified_run_package(
                        run_id,
                        read_budget=read_budget,
                    )
                )
            semantics = None
            if (
                package.language.effective_profile
                is WorkflowLanguageProfile.ARCHON_2026_07
                and package.language.normalizer_version == 3
            ):
                try:
                    resources_bytes = sealed_bytes.get("resources.json")
                    if not isinstance(resources_bytes, bytes):
                        raise WorkflowExecutionSemanticsError(
                            "sealed execution semantics resources are missing"
                        )
                    resources = json.loads(resources_bytes)
                    if not isinstance(resources, Mapping):
                        raise WorkflowExecutionSemanticsError(
                            "sealed execution semantics resources are malformed"
                        )
                    try:
                        canonical_resources = json.dumps(
                            resources,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode()
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise WorkflowExecutionSemanticsError(
                            "sealed execution semantics resources are malformed"
                        ) from exc
                    if not hmac.compare_digest(resources_bytes, canonical_resources):
                        raise WorkflowExecutionSemanticsError(
                            "sealed execution semantics resources are not canonical"
                        )
                    semantics = read_phase3_execution_semantics(
                        resources.get("phase3_execution_semantics"),
                        package=package,
                    )
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise WorkflowExecutionSemanticsError(
                        "sealed execution semantics resources are malformed"
                    ) from exc
                execution_limits = self._run_execution_limits_with_sealed_phase3(
                    package,
                    semantics,
                )
            else:
                execution_limits = self._run_execution_limits(package)
            return package, execution_limits, sealed_paths, sealed_bytes, semantics
        except WorkflowValidationError as exc:
            if not exc.issues:
                raise
            issue = exc.issues[0]
            if expected_state_version is None:
                expected_state_version = int(
                    self.store.load_run(run_id).get("state_version", -1)
                )
            self.store._fail_package_validation(
                run_id,
                expected_state_version=expected_state_version,
                error_code=issue.code,
                error_path=issue.path,
                error_message=issue.message,
                schedule_revalidation=schedule_revalidation,
            )
            return None
        except WorkflowExecutionSemanticsError as exc:
            if schedule_revalidation is None:
                raise
            if expected_state_version is None:
                expected_state_version = int(
                    self.store.load_run(run_id).get("state_version", -1)
                )
            self.store._fail_package_validation(
                run_id,
                expected_state_version=expected_state_version,
                error_code=exc.code,
                error_path="resources.phase3_execution_semantics",
                error_message=str(exc),
                schedule_revalidation=schedule_revalidation,
            )
            return None
        except Exception:
            if schedule_revalidation is None:
                raise
            self.store._fail_scheduled_package_preparation(
                run_id,
                schedule_revalidation,
            )
            return None

    def _authorize_scheduled_promotion(
        self,
        run_id: str,
        projection: Mapping[str, object],
    ):
        metadata = projection.get("run_metadata")
        if not isinstance(metadata, Mapping) or not isinstance(
            metadata.get("schedule_at"), str
        ):
            return True, None
        if self.execution_fence is None or self.runner_binding is None:
            # Legacy schedule-only projections predate fire-time evidence and
            # retain their existing direct-scheduler behavior. New admissions
            # cannot promote without the coordinator's actual execution context.
            if not isinstance(metadata.get("execution_identity"), str):
                return True, None
            self.store.fail_scheduled_revalidation(
                run_id,
                expected_state_version=int(projection.get("state_version", -1)),
            )
            return False, None
        try:
            from plugins.workflow.scheduled_revalidation import (
                revalidate_scheduled_run,
                scheduled_execution_context,
                verify_sealed_snapshot,
            )
            run_directory = self.store.run_directory(run_id)
            read_budget = WorkflowResourceReadBudget(
                max_file_bytes=WORKFLOW_RESOURCE_MAX_FILE_BYTES,
                max_total_bytes=WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
                max_files=WORKFLOW_RESOURCE_MAX_FILES,
            )
            verify_sealed_snapshot(
                projection,
                run_directory=run_directory,
                read_budget=read_budget,
            )

            def verify(current_projection: Mapping[str, object]) -> None:
                context = scheduled_execution_context(
                    current_projection,
                    self.runner_binding,
                )
                revalidate_scheduled_run(
                    current_projection,
                    context,
                    hermes_home=self.store.hermes_home,
                    run_directory=run_directory,
                    read_budget=read_budget,
                )

            authorization = self.store._scheduled_promotion_authorization(
                run_id,
                verify,
                resource_read_budget=read_budget,
            )
        except Exception:
            self.store.fail_scheduled_revalidation(
                run_id,
                expected_state_version=int(projection.get("state_version", -1)),
            )
            return False, None
        return True, authorization

    def _try_promote(
        self,
        run_id: str,
        *,
        now: datetime,
        schedule_revalidation,
    ) -> bool:
        if schedule_revalidation is None:
            return self.store.try_promote_run(run_id, now=now)
        return self.store.try_promote_run(
            run_id,
            now=now,
            schedule_revalidation=schedule_revalidation,
        )

    def _node_timeout(
        self,
        node: WorkflowNode,
        execution_limits: RunExecutionLimits | None = None,
        execution_semantics: Phase3ExecutionSemantics | None = None,
    ) -> float:
        if execution_semantics is not None:
            return float(
                execution_semantics.nodes[node.id][
                    "attempt_wall_timeout_seconds"
                ]
            )
        limits = execution_limits or self.profile_execution_limits
        if node.node_type in {"command", "prompt", "loop", "approval"}:
            return float(limits.ai_wall_timeout_seconds)
        return min(
            float(node.options.get("timeout", limits.subprocess_timeout_seconds)),
            float(limits.subprocess_timeout_seconds),
        )

    def _attempt_deadline_budget(
        self,
        node: WorkflowNode,
        execution_limits: RunExecutionLimits,
        execution_semantics: Phase3ExecutionSemantics | None,
        *,
        now: float | None = None,
    ) -> DeadlineBudget:
        if now is None:
            now = self._monotonic()
        if execution_semantics is not None:
            node_semantics = execution_semantics.nodes[node.id]
            attempt_wall = float(
                node_semantics["attempt_wall_timeout_seconds"]
            )
            sealed_idle = node_semantics["idle_timeout_seconds"]
            sealed_provider = node_semantics["provider_request_timeout_seconds"]
            return DeadlineBudget.from_attempt_semantics(
                now=now,
                attempt_wall_seconds=attempt_wall,
                idle_seconds=(
                    float(sealed_idle)
                    if sealed_idle is not None
                    else min(execution_limits.ai_idle_timeout_seconds, attempt_wall)
                ),
                provider_seconds=(
                    float(sealed_provider)
                    if sealed_provider is not None
                    else min(
                        execution_limits.provider_request_timeout_seconds,
                        attempt_wall,
                    )
                ),
            )
        timeout = self._node_timeout(node, execution_limits)
        idle_timeout = min(
            float(
                node.options.get(
                    "idle_timeout",
                    execution_limits.ai_idle_timeout_seconds,
                )
            ),
            execution_limits.ai_idle_timeout_seconds,
            timeout,
        )
        return DeadlineBudget.create(
            now=now,
            wall_seconds=timeout,
            idle_seconds=idle_timeout,
            provider_seconds=min(
                execution_limits.provider_request_timeout_seconds,
                timeout,
            ),
        )

    @staticmethod
    def _effective_retry_policy(
        node: WorkflowNode,
        execution_limits: RunExecutionLimits,
    ) -> RetryPolicy:
        policy = RetryPolicy.from_mapping(
            node.options.get("retry"),
            default_max_attempts=execution_limits.combined_retries,
        )
        return replace(
            policy,
            max_attempts=min(
                policy.max_attempts,
                execution_limits.combined_retries,
            ),
        )

    @staticmethod
    def _sealed_retry_grant(
        node: WorkflowNode,
        execution_semantics: Phase3ExecutionSemantics,
        *,
        retry_consumed: int,
    ) -> RetryLedgerGrant:
        retry = execution_semantics.nodes[node.id]["retry"]
        if not isinstance(retry, Mapping):
            raise RuntimeError("sealed retry semantics are malformed")
        return RetryLedgerGrant.from_projection(
            retry,
            retry_consumed=retry_consumed,
        )

    def _heartbeat_journal_reserve(
        self,
        node: WorkflowNode,
        execution_limits: RunExecutionLimits | None = None,
        execution_semantics: Phase3ExecutionSemantics | None = None,
    ) -> int:
        heartbeat_count = math.ceil(
            self._node_timeout(node, execution_limits, execution_semantics)
            / self.heartbeat_seconds
        )
        return heartbeat_count * 4096

    @staticmethod
    def _persistent_session_recovery_reserve(
        node: WorkflowNode,
        package: WorkflowPackage,
        projection: Mapping[str, object],
    ) -> int:
        """Reserve selection, winning obligation, and outcome frames up front."""
        if (
            package.language.effective_profile
            is not WorkflowLanguageProfile.ARCHON_2026_07
            or package.language.normalizer_version != 3
            or node.node_type not in {"command", "prompt"}
            or node.options.get("context") == "fresh"
            or not bool(
                node.options.get(
                    "persist_session",
                    package.definition.options.get("persist_sessions", False),
                )
            )
        ):
            return 0
        projection_bytes = len(
            json.dumps(
                projection,
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return 2 * TerminalJournalReserve.for_projection(
            projection_bytes
        ).terminal_reserve_bytes

    def _execute_claim(
        self,
        run_id: str,
        claim: NodeClaim,
        node: WorkflowNode,
        package,
        projection: dict[str, object],
        strict_reference_snapshot: _StrictReferenceSnapshot | None,
        execution_limits: RunExecutionLimits,
        execution_semantics: Phase3ExecutionSemantics | None,
        sealed_resource_paths: frozenset[str] | None,
        sealed_resource_bytes: Mapping[str, bytes] | None,
        claimed_deadline_budget: DeadlineBudget | None = None,
    ) -> None:
        with self._activity:
            self._active_executions += 1
        try:
            if not self._renew_execution_owner(run_id):
                self.store.release_claim_before_execution(claim)
                return
            executor = self.executors.get(node.node_type)
            if executor is None:
                result = NodeExecutionResult(
                    "failed",
                    error_code="unsupported_executor",
                    error_message=f"no executor for {node.node_type}",
                )
            else:
                self.store.mark_node_started(claim)
                if node.node_type != "cancel" and self._cancelled(run_id):
                    self._persist_result(
                        claim,
                        node,
                        NodeExecutionResult(
                            "cancelled",
                            error_code=self._cancellation_reason(run_id) or "cancelled",
                        ),
                        execution_limits,
                        language_profile=package.language.effective_profile,
                        execution_semantics=execution_semantics,
                        outward_action=(
                            node.id
                            in package.sidecar.get("outward_action_nodes", ())
                        ),
                    )
                    return
                node_state = dict(projection["nodes"][node.id])
                consumed_attempts = max(
                    0, int(node_state.get("retry_consumed", 0))
                )
                if execution_semantics is not None:
                    retry_grant = self._sealed_retry_grant(
                        node,
                        execution_semantics,
                        retry_consumed=consumed_attempts,
                    )
                    remaining_attempts = retry_grant.remaining_attempts
                else:
                    retry_policy = self._effective_retry_policy(
                        node, execution_limits
                    )
                    remaining_attempts = (
                        retry_policy.max_attempts - consumed_attempts
                    )
                if remaining_attempts <= 0:
                    self._persist_result(
                        claim,
                        node,
                        NodeExecutionResult(
                            "failed",
                            error_code="retry_budget_exhausted",
                            error_message="combined retry budget is exhausted",
                        ),
                        execution_limits,
                        language_profile=package.language.effective_profile,
                        execution_semantics=execution_semantics,
                        outward_action=(
                            node.id
                            in package.sidecar.get("outward_action_nodes", ())
                        ),
                    )
                    return
                approved_action_digest = self.store.consume_action_grant(claim)
                node_state.pop("action_grant", None)
                if approved_action_digest is not None:
                    node_state["approved_action_digest"] = approved_action_digest
                timeout = self._node_timeout(
                    node, execution_limits, execution_semantics
                )
                heartbeat_stop = threading.Event()
                ownership_lost = threading.Event()

                def heartbeat() -> None:
                    while not heartbeat_stop.wait(self.heartbeat_seconds):
                        if not self._renew_execution_owner(run_id):
                            ownership_lost.set()
                            return
                        if not self.store.renew_claim(
                            claim,
                            now=self._utcnow(),
                            monotonic_now=self._monotonic(),
                            lease_seconds=self.lease_seconds,
                            heartbeat_interval_seconds=self.heartbeat_seconds,
                        ):
                            self.store.expire_stale_claims(
                                run_id,
                                now=self._utcnow(),
                                monotonic_now=self._monotonic(),
                            )
                            return

                heartbeat_thread = threading.Thread(
                    target=heartbeat,
                    name=f"workflow-heartbeat-{claim.attempt_id}",
                    daemon=True,
                )
                heartbeat_thread.start()
                try:
                    if execution_semantics is not None:
                        if claimed_deadline_budget is None:
                            raise RuntimeError(
                                "sealed attempt deadline was not captured at claim"
                            )
                        deadline_budget = claimed_deadline_budget
                    else:
                        deadline_budget = self._attempt_deadline_budget(
                            node,
                            execution_limits,
                            execution_semantics,
                        )
                    variables = self._variables(
                        projection,
                        self.store.run_directory(run_id),
                        sealed_resource_paths=sealed_resource_paths,
                        sealed_resource_bytes=sealed_resource_bytes,
                        output_node_ids=(
                            node.depends_on
                            if package.language.normalizer_version == 3
                            else None
                        ),
                        resolved_outputs=(
                            strict_reference_snapshot.outputs
                            if strict_reference_snapshot is not None
                            else None
                        ),
                    )
                    loop_input = projection["nodes"][node.id].get(
                        "loop_user_input_artifact"
                    )
                    if node.node_type == "loop" and isinstance(loop_input, str):
                        variables = replace(
                            variables,
                            loop_user_input=self._read_text(
                                self.store.run_directory(run_id) / loop_input
                            ),
                        )
                    if not self._renew_execution_owner(run_id):
                        self.store.release_claim_before_execution(claim)
                        return
                    structured_output = package.language.structured_outputs.get(
                        node.id
                    )
                    structured_output_decision = (
                        _sealed_structured_output_decision(
                            projection,
                            node.id,
                            structured_output.schema_fingerprint,
                        )
                        if structured_output is not None
                        else None
                    )
                    result = executor.execute(
                        NodeExecutionContext(
                            run_id=run_id,
                            run_directory=self.store.run_directory(run_id),
                            node=node,
                            attempt_id=claim.attempt_id,
                            timeout_seconds=timeout,
                            is_cancelled=lambda: self._cancelled(run_id),
                            workflow_name=package.definition.name,
                            workflow_options=package.definition.options,
                            variable_context=variables,
                            output_resolver=(
                                strict_reference_snapshot.resolve
                                if strict_reference_snapshot is not None
                                else variables.output_reference
                                if package.language.normalizer_version == 3
                                else None
                            ),
                            predecessor_results=self._predecessor_results(
                                projection,
                                node.depends_on,
                                variables.node_outputs,
                            ),
                            node_state=node_state,
                            operator_scope=str(
                                projection.get("operator_scope_digest") or "local"
                            ),
                            ai_entitlement=derive_ai_entitlement(
                                projection.get("run_metadata", {}),
                                definition_digest=str(
                                    projection.get("definition_digest") or ""
                                ),
                                execution_context=(
                                    self.runner_binding.execution_context(
                                        surface="background",
                                        entitlement=AIEntitlementResolution("real"),
                                    )
                                    if self.runner_binding is not None
                                    else None
                                ),
                            ),
                            execution_limits=execution_limits,
                            resource_limits=ProcessResourceLimits(
                                max_rss_bytes=execution_limits.process_tree_rss_bytes,
                                max_cpu_seconds=execution_limits.process_tree_cpu_seconds,
                                max_descendants=execution_limits.max_descendants,
                            ),
                            deadline_budget=deadline_budget,
                            sealed_attempt_timeout=(execution_semantics is not None),
                            # Provider and workflow attempts draw from the same
                            # frozen per-run allowance, so the retry layers do not multiply.
                            max_provider_attempts=remaining_attempts,
                            cancellation_reason=lambda: self._cancellation_reason(
                                run_id
                            ),
                            record_iteration=lambda artifacts, state: (
                                self.store.record_loop_iteration(
                                    claim,
                                    artifacts=self._loop_iteration_artifacts(
                                        artifacts,
                                        node=node,
                                        language_profile=(
                                            package.language.effective_profile
                                        ),
                                    ),
                                    loop_state=state,
                                )
                            ),
                            spawn_intent=lambda executor_nonce: (
                                self.store.record_spawn_intent(
                                    claim, executor_nonce=executor_nonce
                                )
                            ),
                            spawn_failed=lambda executor_nonce, error_code: (
                                self.store.record_spawn_failed(
                                    claim,
                                    executor_nonce=executor_nonce,
                                    error_code=error_code,
                                )
                            ),
                            process_started=lambda identity: (
                                self.store.record_process_started(claim, identity)
                            ),
                            process_stopped=lambda identity, cleaned: (
                                self.store.record_process_stopped(
                                    claim, identity, cleaned=cleaned
                                )
                            ),
                            record_session_recovery_selection=lambda selection: (
                                self.store.record_persistent_session_recovery_selection(
                                    claim,
                                    selection,
                                )
                            ),
                            sealed_resource_paths=sealed_resource_paths,
                            sealed_resource_bytes=sealed_resource_bytes,
                            language_profile=package.language.effective_profile,
                            normalizer_version=package.language.normalizer_version,
                            structured_output=structured_output,
                            structured_output_decision=structured_output_decision,
                            outward_action=(
                                node.id
                                in package.sidecar.get("outward_action_nodes", ())
                            ),
                            monotonic=self._monotonic,
                            termination_policy=TerminationPolicy(
                                cooperative_grace_seconds=(
                                    execution_limits.cooperative_shutdown_seconds
                                ),
                                term_grace_seconds=execution_limits.term_grace_seconds,
                                kill_grace_seconds=(
                                    execution_limits.kill_reap_grace_seconds
                                ),
                                wait_timeout_seconds=(
                                    execution_limits.kill_reap_grace_seconds
                                ),
                            ),
                        )
                    )
                except SealedStructuredOutputDecisionError as exc:
                    result = NodeExecutionResult(
                        "failed",
                        error_code="structured_output_capability_drift",
                        error_message=str(exc),
                        metadata={"archon_terminal_failure": True},
                    )
                except WorkflowOutputReferenceError as exc:
                    result = NodeExecutionResult(
                        "failed",
                        error_code=exc.code,
                        error_message=str(exc),
                        metadata={
                            "archon_terminal_failure": True,
                            "additional_provider_attempts": 0,
                        },
                    )
                except Exception as exc:
                    result = NodeExecutionResult(
                        "failed",
                        error_code="executor_crash",
                        error_message=str(exc),
                    )
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=self.heartbeat_seconds)
                if ownership_lost.is_set():
                    return
            self._persist_result(
                claim,
                node,
                result,
                execution_limits,
                language_profile=package.language.effective_profile,
                execution_semantics=execution_semantics,
                outward_action=(
                    node.id in package.sidecar.get("outward_action_nodes", ())
                ),
            )
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                self.store.release_claim_before_execution(claim)
            if "stale" not in str(exc) and "terminal run" not in str(exc):
                if "execution fence" not in str(exc):
                    raise
        finally:
            with self._activity:
                self._active_executions -= 1
                self._activity.notify_all()

    @staticmethod
    def _loop_iteration_artifacts(
        artifacts: tuple[ArtifactRef, ...],
        *,
        node: WorkflowNode,
        language_profile: WorkflowLanguageProfile,
    ) -> tuple[ArtifactRef, ...]:
        if (
            language_profile is not WorkflowLanguageProfile.ARCHON_2026_07
            or node.options.get("output_type") is None
        ):
            return artifacts
        return tuple(
            replace(
                artifact,
                media_type="text/markdown; charset=utf-8",
            )
            if artifact.media_type == "text/plain"
            else artifact
            for artifact in artifacts
        )

    @staticmethod
    def _attach_declared_primary_output(
        node: WorkflowNode,
        result: NodeExecutionResult,
        language_profile: WorkflowLanguageProfile,
    ) -> NodeExecutionResult:
        if (
            result.status != "succeeded"
            or result.primary_output is not None
            or language_profile is not WorkflowLanguageProfile.ARCHON_2026_07
            or node.node_type not in {"bash", "script", "loop"}
            or node.options.get("output_type") is None
        ):
            return result
        if node.node_type == "loop":
            loop_state = result.metadata.get("loop_state")
            relative_path = (
                loop_state.get("output_artifact")
                if isinstance(loop_state, Mapping)
                else None
            )
            candidates = [
                artifact
                for artifact in result.artifacts
                if artifact.relative_path == relative_path
            ]
        else:
            candidates = list(result.artifacts[:1])
        if len(candidates) != 1:
            raise ArchonOutputIntegrityError(
                "declared output does not identify one executor artifact"
            )
        artifact = candidates[0]
        return replace(
            result,
            primary_output=PrimaryOutputCandidate(
                attempt_relative_path=artifact.relative_path,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                structured_value=None,
                schema_fingerprint=None,
                canonicalization_version=1,
                output_type=str(node.options["output_type"]),
            ),
        )

    def _persist_result(
        self,
        claim: NodeClaim,
        node: WorkflowNode,
        result: NodeExecutionResult,
        execution_limits: RunExecutionLimits,
        *,
        language_profile: WorkflowLanguageProfile = (
            WorkflowLanguageProfile.HERMES_LEGACY
        ),
        execution_semantics: Phase3ExecutionSemantics | None = None,
        outward_action: bool = False,
    ) -> None:
        if result.session_recovery_outcome is not None:
            if not self.store.record_persistent_session_recovery_outcome(
                claim,
                outcome=result.session_recovery_outcome,
            ):
                raise RuntimeError(
                    "persistent session recovery outcome lost its active claim"
                )
        projection: dict[str, object] | None = None
        retry_grant: RetryLedgerGrant | None = None
        retry_charge = None
        if execution_semantics is not None and result.status not in {
            "cancelled",
            "interrupted",
        }:
            projection = self.store.load_run(claim.run_id)
            node_state = projection["nodes"][claim.node_id]
            consumed_before = int(node_state.get("retry_consumed", 0))
            retry_grant = self._sealed_retry_grant(
                node,
                execution_semantics,
                retry_consumed=consumed_before,
            )
            if retry_grant.remaining_attempts > 0:
                provider_evidence = (
                    0
                    if node.node_type not in {"command", "prompt"}
                    else result.metadata.get(
                        "additional_provider_attempts",
                        result.metadata.get("provider_attempts"),
                    )
                )
                exactness = result.metadata.get("provider_attempts_exact")
                retry_charge = retry_grant.charge(
                    provider_evidence,
                    provider_attempts_exact=(
                        exactness if isinstance(exactness, bool) else None
                    ),
                )
                result = replace(
                    result,
                    metadata={
                        **result.metadata,
                        **retry_grant.evidence(retry_charge),
                    },
                )
        result = self._attach_declared_primary_output(
            node,
            result,
            language_profile,
        )
        if result.status == "failed" and result.error_code == "cleanup_failed":
            self.store.block_cleanup_failed(
                claim,
                artifacts=result.artifacts,
                error_message=result.error_message,
                metadata=(
                    result.metadata if execution_semantics is not None else None
                ),
                consumed_attempts=(
                    retry_charge.retry_consumed
                    if retry_charge is not None
                    else None
                ),
            )
            return
        if result.status != "failed":
            completion_metadata = dict(result.metadata)
            completion_artifacts = result.artifacts
            retained_candidate = None
            if result.status == "succeeded" and result.primary_output is not None:
                retained_candidate = result.primary_output
                if (
                    language_profile is WorkflowLanguageProfile.ARCHON_2026_07
                    and retained_candidate.output_type is not None
                    and retained_candidate.media_type == "text/plain"
                ):
                    source_media_type = retained_candidate.media_type
                    canonical_media_type = "text/markdown; charset=utf-8"
                    retained_candidate = replace(
                        retained_candidate,
                        media_type=canonical_media_type,
                    )
                    completion_artifacts = tuple(
                        replace(artifact, media_type=canonical_media_type)
                        if (
                            artifact.relative_path
                            == retained_candidate.attempt_relative_path
                            and artifact.media_type == source_media_type
                            and artifact.size_bytes == retained_candidate.size_bytes
                            and artifact.sha256 == retained_candidate.sha256
                        )
                        else artifact
                        for artifact in result.artifacts
                    )
                candidate_identity = primary_output_candidate_identity(
                    retained_candidate
                )
                primary_output_candidate_from_identity(candidate_identity)
                completion_metadata[PRIMARY_OUTPUT_CANDIDATE_METADATA_KEY] = (
                    candidate_identity
                )
            typed_publication = None
            if (
                language_profile is WorkflowLanguageProfile.ARCHON_2026_07
                and retained_candidate is not None
                and retained_candidate.output_type is not None
            ):
                session_id = completion_metadata.get("session_id")
                typed_publication = TypedPublicationCandidate(
                    attempt_relative_path=(
                        retained_candidate.attempt_relative_path
                    ),
                    output_type=retained_candidate.output_type,
                    media_type=retained_candidate.media_type,
                    size_bytes=retained_candidate.size_bytes,
                    sha256=retained_candidate.sha256,
                    schema_fingerprint=(
                        retained_candidate.schema_fingerprint
                    ),
                    canonicalization_version=(
                        retained_candidate.canonicalization_version
                    ),
                    session_id=session_id if isinstance(session_id, str) else None,
                )
            if retained_candidate is not None:
                self._ensure_output_resolution_state()
                with self._output_resolution_lock:
                    self._cache_primary_output_candidate(
                        (claim.run_id, claim.node_id, claim.attempt_id),
                        retained_candidate,
                    )
                    try:
                        self.store.complete_node(
                            claim,
                            status=result.status,
                            artifacts=completion_artifacts,
                            typed_publication=typed_publication,
                            error_code=result.error_code,
                            error_message=result.error_message,
                            metadata=completion_metadata,
                            session_registry_update=(
                                result.session_registry_update
                            ),
                            session_registry_authority=(
                                result.session_registry_authority
                            ),
                        )
                    except BaseException:
                        self._purge_attempt_output_cache(claim)
                        raise
            else:
                self.store.complete_node(
                    claim,
                    status=result.status,
                    artifacts=result.artifacts,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    metadata=completion_metadata,
                    session_registry_update=result.session_registry_update,
                    session_registry_authority=(
                        result.session_registry_authority
                    ),
                )
            return
        if execution_semantics is not None:
            assert retry_grant is not None
            policy = retry_grant.policy
        else:
            policy = self._effective_retry_policy(node, execution_limits)
        if projection is None:
            projection = self.store.load_run(claim.run_id)
        node_state = projection["nodes"][claim.node_id]
        consumed_before = int(node_state.get("retry_consumed", 0))
        if retry_charge is not None:
            provider_attempts = retry_charge.additional_provider_attempts
            consumed = retry_charge.retry_consumed
        else:
            provider_attempts = int(result.metadata.get("provider_attempts", 0))
            consumed = min(
                policy.max_attempts,
                consumed_before + 1 + provider_attempts,
            )
        known_no_effect = None
        if execution_semantics is not None:
            known_no_effect = result.metadata.get("known_no_effect") is True
            if (
                node.node_type in {"bash", "script"}
                and retry_grant is not None
                and not outward_action
                and result.error_code in {*_TRANSIENT_FAILURES, "process_exit"}
            ):
                known_no_effect = True
        failure = classify_failure(
            result.error_code,
            workflow_attempt=consumed_before + 1,
            provider_attempts=provider_attempts,
            maximum=policy.max_attempts,
            known_no_effect=known_no_effect,
            outward_action=(
                outward_action if execution_semantics is not None else False
            ),
        )
        never_retry = {
            "authentication",
            "authorization",
            "credit_exhausted",
            "validation",
            "cancelled",
            "unknown_side_effect",
            "outcome_unknown",
            "output_limit",
            "resource_limit",
            "cleanup_failed",
        }
        snapshot = read_language_snapshot(projection.get("language"))
        archon_terminal_failure = (
            snapshot is not None
            and snapshot.effective_profile
            is WorkflowLanguageProfile.ARCHON_2026_07
            and result.metadata.get("archon_terminal_failure") is True
        )
        if archon_terminal_failure:
            failure = FailureClass.FATAL
        elif execution_semantics is None:
            if (
                policy.on_error == "all"
                and failure is FailureClass.FATAL
                and result.error_code not in never_retry
                and not archon_terminal_failure
            ):
                failure = FailureClass.TRANSIENT
        elif (
            failure is FailureClass.UNKNOWN_ERROR
            and policy.on_error == "all"
            and not archon_terminal_failure
        ):
            failure = FailureClass.TRANSIENT
        metadata = {**result.metadata, "retry_consumed": consumed}
        if failure in {FailureClass.RECONCILE, FailureClass.UNKNOWN_OUTCOME}:
            self.store.complete_node(
                claim,
                status="paused",
                artifacts=result.artifacts,
                error_code=result.error_code,
                error_message=result.error_message,
                metadata={**metadata, "pending_interaction": "reconcile"},
            )
        elif failure is FailureClass.TRANSIENT and consumed < policy.max_attempts:
            workflow_attempt = len(node_state["attempts"])
            delay = compute_retry_delay(policy, workflow_attempt, jitter=self._jitter)
            self.store.schedule_retry(
                claim,
                next_attempt_at=self._utcnow() + timedelta(seconds=delay),
                artifacts=result.artifacts,
                error_code=result.error_code,
                error_message=result.error_message,
                metadata=metadata,
                consumed_attempts=consumed,
            )
        else:
            self.store.complete_node(
                claim,
                status="failed",
                artifacts=result.artifacts,
                error_code=result.error_code,
                error_message=result.error_message,
                metadata=metadata,
            )

    def advance(self, run_id: str, *, max_nodes: int | None = None):
        if self._shutdown.is_set():
            return self.store.load_run(run_id)
        self._reconcile_session_registry_update(run_id)
        if max_nodes is None:
            advanced = self.advance_all([run_id])
            return advanced.get(run_id, self.store.load_run(run_id))
        executed = 0
        initial = self.store.load_run(run_id)
        authorization = None
        if initial["status"] == "queued":
            authorized, authorization = self._authorize_scheduled_promotion(
                run_id, initial
            )
            if not authorized:
                return self.store.load_run(run_id)
        prepared_package = self._prepare_run_package(
            run_id,
            authorization,
            expected_state_version=int(initial.get("state_version", -1)),
        )
        if prepared_package is None:
            return self.store.load_run(run_id)
        (
            package,
            execution_limits,
            sealed_resource_paths,
            sealed_resource_bytes,
            execution_semantics,
        ) = prepared_package
        by_id = {node.id: node for node in package.definition.nodes}
        foreground_owner_id, foreground_owner_epoch = self._foreground_claim_token(
            self.store.load_run(run_id)
        )
        with self._activity:
            self._active_runs.add(run_id)
        try:
            while not self._shutdown.is_set() and (
                max_nodes is None or executed < max_nodes
            ):
                if not self._renew_execution_owner(run_id):
                    break
                self.store.expire_stale_claims(
                    run_id,
                    now=self._utcnow(),
                    monotonic_now=self._monotonic(),
                    current_owner_epoch=self.owner_id,
                )
                projection = self.store.load_run(run_id)
                if projection["status"] == "queued":
                    if not self._try_promote(
                        run_id,
                        now=self._utcnow(),
                        schedule_revalidation=authorization,
                    ):
                        break
                self.store.wake_due_retries(run_id, now=self._utcnow())
                self.store.wake_due_output_resolutions(
                    run_id, now=self._utcnow()
                )
                self._resolve_graph(run_id, package.definition.nodes)
                projection = self.store.load_run(run_id)
                if projection["status"] in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "abandoned",
                    "interrupted",
                    "paused",
                    "waiting_retry",
                }:
                    break
                ready: list[str] = []
                strict_reference_snapshots: dict[
                    str, _StrictReferenceSnapshot
                ] = {}
                for node_id in sorted(projection["nodes"]):
                    if projection["nodes"][node_id]["state"] != "ready":
                        continue
                    preflight = self._preflight_strict_node_references(
                        run_id,
                        by_id[node_id],
                        package,
                        projection,
                        sealed_resource_paths=sealed_resource_paths,
                        sealed_resource_bytes=sealed_resource_bytes,
                    )
                    if not preflight:
                        continue
                    ready.append(node_id)
                    if isinstance(preflight, _StrictReferenceSnapshot):
                        strict_reference_snapshots[node_id] = preflight
                remaining = None if max_nodes is None else max_nodes - executed
                capacity = min(
                    self.max_parallel_nodes,
                    execution_limits.max_parallel_nodes,
                )
                if remaining is not None:
                    capacity = min(capacity, remaining)
                claims = []
                fence_lost = False
                for node_id in ready[:capacity]:
                    claim_now = self._monotonic()
                    session_recovery_reserve = (
                        self._persistent_session_recovery_reserve(
                            by_id[node_id],
                            package,
                            projection,
                        )
                    )
                    try:
                        claim = self.store.claim_node(
                            run_id,
                            node_id,
                            self.owner_id,
                            lease_seconds=self.lease_seconds,
                            now=self._utcnow(),
                            monotonic_now=claim_now,
                            journal_reserve_bytes=self._heartbeat_journal_reserve(
                                by_id[node_id],
                                execution_limits,
                                execution_semantics,
                            )
                            + session_recovery_reserve,
                            terminal_journal_reserve_bytes=(
                                session_recovery_reserve
                            ),
                            executor_id=by_id[node_id].node_type,
                            owner_epoch=self.owner_id,
                            effect_classification=self.store.node_effect_classification(
                                run_id,
                                node_id,
                                projection=projection,
                            ),
                            execution_fence=self.execution_fence,
                            foreground_owner_id=foreground_owner_id,
                            foreground_owner_epoch=foreground_owner_epoch,
                            require_execution_authority=True,
                            max_run_workers=execution_limits.max_total_workers,
                        )
                    except StorageQuotaError as exc:
                        self.store.interrupt_for_host_pressure(run_id, message=str(exc))
                        break
                    except RuntimeError as exc:
                        if not self._is_execution_fence_loss(exc):
                            raise
                        fence_lost = True
                        break
                    if claim is not None:
                        claimed_deadline_budget = (
                            self._attempt_deadline_budget(
                                by_id[node_id],
                                execution_limits,
                                execution_semantics,
                                now=claim_now,
                            )
                            if execution_semantics is not None
                            else None
                        )
                        claims.append((
                            claim,
                            by_id[node_id],
                            projection,
                            strict_reference_snapshots.get(node_id),
                            claimed_deadline_budget,
                        ))
                if fence_lost:
                    for claim, *_rest in claims:
                        self.store.release_claim_before_execution(claim)
                    break
                if not claims:
                    break
                with ThreadPoolExecutor(
                    max_workers=self.max_parallel_nodes,
                    thread_name_prefix="workflow-node",
                ) as pool:
                    futures = [
                        pool.submit(
                            self._execute_claim,
                            run_id,
                            claim,
                            node,
                            package,
                            snapshot,
                            strict_snapshot,
                            execution_limits,
                            execution_semantics,
                            sealed_resource_paths,
                            sealed_resource_bytes,
                            budget,
                        )
                        for claim, node, snapshot, strict_snapshot, budget in claims
                    ]
                    for future in futures:
                        future.result()
                    self._reconcile_session_registry_update(run_id)
                executed += len(claims)
            self._resolve_graph(run_id, package.definition.nodes)
            return self.store.load_run(run_id)
        finally:
            with self._activity:
                self._active_runs.discard(run_id)
                self._activity.notify_all()
            self._purge_terminal_run_output_cache(run_id)

    def submit(self, run_id: str, fence: ExecutionFence) -> bool:
        """Submit one run without waiting, deduplicated under the exact fence."""
        if self._shutdown.is_set():
            return False
        if self.execution_fence != fence:
            return False
        with self._activity:
            if run_id in self._submitted_runs or run_id in self._active_runs:
                return False
            self._submitted_runs.add(run_id)

        def execute() -> None:
            try:
                self.advance(run_id)
            finally:
                with self._activity:
                    self._submitted_runs.discard(run_id)
                    self._activity.notify_all()
                self._purge_terminal_run_output_cache(run_id)

        try:
            self._submission_pool.submit(execute)
        except RuntimeError:
            with self._activity:
                self._submitted_runs.discard(run_id)
            return False
        return True

    def advance_all(self, run_ids: Iterable[str]):
        """Replenish ready work fairly across runs under one bounded pool."""
        run_ids = list(dict.fromkeys(run_ids))
        for run_id in run_ids:
            self._reconcile_session_registry_update(run_id)
        authorizations = {}
        preparation_state_versions = {}
        authorized_run_ids = []
        for run_id in run_ids:
            projection = self.store.load_run(run_id)
            authorization = None
            if projection["status"] == "queued":
                authorized, authorization = self._authorize_scheduled_promotion(
                    run_id, projection
                )
                if not authorized:
                    continue
            authorizations[run_id] = authorization
            preparation_state_versions[run_id] = int(
                projection.get("state_version", -1)
            )
            authorized_run_ids.append(run_id)
        run_ids = authorized_run_ids
        packages = {}
        execution_limits = {}
        sealed_resource_paths = {}
        sealed_resource_bytes = {}
        execution_semantics = {}
        package_failures = {}
        prepared_run_ids = []
        for run_id in run_ids:
            prepared_package = self._prepare_run_package(
                run_id,
                authorizations[run_id],
                expected_state_version=preparation_state_versions[run_id],
            )
            if prepared_package is None:
                package_failures[run_id] = self.store.load_run(run_id)
                continue
            package, limits, paths, resource_bytes, semantics = prepared_package
            packages[run_id] = package
            execution_limits[run_id] = limits
            sealed_resource_paths[run_id] = paths
            sealed_resource_bytes[run_id] = resource_bytes
            execution_semantics[run_id] = semantics
            prepared_run_ids.append(run_id)
        run_ids = prepared_run_ids
        foreground_tokens = {
            run_id: self._foreground_claim_token(self.store.load_run(run_id))
            for run_id in run_ids
        }
        with self._activity:
            self._active_runs.update(run_ids)
        pool = ThreadPoolExecutor(
            max_workers=self.max_parallel_nodes,
            thread_name_prefix="workflow-node",
        )
        futures = {}
        fair_cursor = 0
        try:
            while not self._shutdown.is_set():
                fence_lost = False
                if len(futures) >= self.max_parallel_nodes:
                    done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        completed_run_id = futures.pop(future)
                        future.result()
                        self._reconcile_session_registry_update(completed_run_id)
                candidates: dict[str, list[str]] = {}
                snapshots = {}
                strict_reference_snapshots: dict[
                    tuple[str, str], _StrictReferenceSnapshot
                ] = {}
                active = []
                for run_id in run_ids:
                    if not self._renew_execution_owner(run_id):
                        continue
                    self.store.expire_stale_claims(
                        run_id,
                        now=self._utcnow(),
                        monotonic_now=self._monotonic(),
                        current_owner_epoch=self.owner_id,
                    )
                    projection = self.store.load_run(run_id)
                    if projection["status"] == "queued":
                        self._try_promote(
                            run_id,
                            now=self._utcnow(),
                            schedule_revalidation=authorizations[run_id],
                        )
                    self.store.wake_due_retries(run_id, now=self._utcnow())
                    self.store.wake_due_output_resolutions(
                        run_id, now=self._utcnow()
                    )
                    self._resolve_graph(run_id, packages[run_id].definition.nodes)
                    projection = self.store.load_run(run_id)
                    if projection["status"] != "running":
                        continue
                    active.append(run_id)
                    snapshots[run_id] = projection
                    candidates[run_id] = []
                    for node_id in sorted(projection["nodes"]):
                        if projection["nodes"][node_id]["state"] != "ready":
                            continue
                        preflight = self._preflight_strict_node_references(
                            run_id,
                            next(
                                node
                                for node in packages[run_id].definition.nodes
                                if node.id == node_id
                            ),
                            packages[run_id],
                            projection,
                            sealed_resource_paths=sealed_resource_paths[run_id],
                            sealed_resource_bytes=sealed_resource_bytes[run_id],
                        )
                        if not preflight:
                            continue
                        candidates[run_id].append(node_id)
                        if isinstance(preflight, _StrictReferenceSnapshot):
                            strict_reference_snapshots[(run_id, node_id)] = preflight
                if not active and not futures:
                    break
                claims = []
                available = self.max_parallel_nodes - len(futures)
                while len(claims) < available:
                    claimed_this_round = False
                    ordered_active = (
                        active[fair_cursor % len(active) :]
                        + active[: fair_cursor % len(active)]
                        if active
                        else []
                    )
                    for run_id in ordered_active:
                        if len(claims) >= available:
                            break
                        if not candidates[run_id]:
                            continue
                        claimed_for_run = sum(
                            claim_run_id == run_id
                            for claim_run_id, *_rest in claims
                        )
                        executing_for_run = sum(
                            future_run_id == run_id
                            for future_run_id in futures.values()
                        )
                        if (
                            claimed_for_run + executing_for_run
                            >= execution_limits[run_id].max_parallel_nodes
                        ):
                            continue
                        node_id = candidates[run_id].pop(0)
                        node = next(
                            node
                            for node in packages[run_id].definition.nodes
                            if node.id == node_id
                        )
                        claim_now = self._monotonic()
                        session_recovery_reserve = (
                            self._persistent_session_recovery_reserve(
                                node,
                                packages[run_id],
                                snapshots[run_id],
                            )
                        )
                        try:
                            claim = self.store.claim_node(
                                run_id,
                                node_id,
                                self.owner_id,
                                lease_seconds=self.lease_seconds,
                                now=self._utcnow(),
                                monotonic_now=claim_now,
                                journal_reserve_bytes=self._heartbeat_journal_reserve(
                                    node,
                                    execution_limits[run_id],
                                    execution_semantics[run_id],
                                )
                                + session_recovery_reserve,
                                terminal_journal_reserve_bytes=(
                                    session_recovery_reserve
                                ),
                                executor_id=node.node_type,
                                owner_epoch=self.owner_id,
                                effect_classification=self.store.node_effect_classification(
                                    run_id,
                                    node_id,
                                    projection=snapshots[run_id],
                                ),
                                execution_fence=self.execution_fence,
                                foreground_owner_id=foreground_tokens[run_id][0],
                                foreground_owner_epoch=foreground_tokens[run_id][1],
                                require_execution_authority=True,
                                max_run_workers=(
                                    execution_limits[run_id].max_total_workers
                                ),
                            )
                        except StorageQuotaError as exc:
                            self.store.interrupt_for_host_pressure(
                                run_id, message=str(exc)
                            )
                            candidates[run_id].clear()
                            continue
                        except RuntimeError as exc:
                            if not self._is_execution_fence_loss(exc):
                                raise
                            fence_lost = True
                            break
                        if claim is None:
                            continue
                        claimed_deadline_budget = (
                            self._attempt_deadline_budget(
                                node,
                                execution_limits[run_id],
                                execution_semantics[run_id],
                                now=claim_now,
                            )
                            if execution_semantics[run_id] is not None
                            else None
                        )
                        claims.append((
                            run_id,
                            claim,
                            node,
                            packages[run_id],
                            snapshots[run_id],
                            strict_reference_snapshots.get((run_id, node_id)),
                            execution_limits[run_id],
                            execution_semantics[run_id],
                            sealed_resource_paths[run_id],
                            sealed_resource_bytes[run_id],
                            claimed_deadline_budget,
                        ))
                        fair_cursor = (active.index(run_id) + 1) % len(active)
                        claimed_this_round = True
                    if fence_lost:
                        break
                    if not claimed_this_round:
                        break
                if fence_lost:
                    for work_item in claims:
                        self.store.release_claim_before_execution(work_item[1])
                    break
                for claim in claims:
                    future = pool.submit(self._execute_claim, *claim)
                    futures[future] = claim[0]
                if not claims:
                    if not futures:
                        break
                    done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        completed_run_id = futures.pop(future)
                        future.result()
                        self._reconcile_session_registry_update(completed_run_id)
            if futures:
                done, _pending = wait(futures)
                for future in done:
                    completed_run_id = futures[future]
                    future.result()
                    self._reconcile_session_registry_update(completed_run_id)
            for run_id in run_ids:
                self._resolve_graph(run_id, packages[run_id].definition.nodes)
            return {
                **package_failures,
                **{run_id: self.store.load_run(run_id) for run_id in run_ids},
            }
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            with self._activity:
                self._active_runs.difference_update(run_ids)
                self._activity.notify_all()
            for run_id in run_ids:
                self._purge_terminal_run_output_cache(run_id)

    def shutdown(self, *, deadline_seconds: float | None = None) -> None:
        deadline_seconds = (
            self.shutdown_deadline_seconds
            if deadline_seconds is None
            else deadline_seconds
        )
        if deadline_seconds <= 0 or not math.isfinite(deadline_seconds):
            raise ValueError("deadline_seconds must be positive and finite")
        self.store.close_admission()
        self._shutdown.set()
        deadline = time.monotonic() + deadline_seconds
        with self._activity:
            active = tuple(self._active_runs)
        for run_id in active:
            if self.execution_fence is not None and not self._renew_execution_owner(
                run_id
            ):
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.store.record_cleanup_failed(
                    run_id, reason="shutdown_deadline_exhausted"
                )
                continue
            try:
                self.store.append_event(
                    run_id,
                    "coordinator_shutdown",
                    lock_timeout_seconds=remaining,
                )
            except (KeyError, RuntimeError, WorkflowLockTimeout):
                self.store.record_cleanup_failed(
                    run_id, reason="shutdown_event_not_persisted"
                )
        with self._activity:
            while self._active_executions and time.monotonic() < deadline:
                self._activity.wait(timeout=min(0.05, deadline - time.monotonic()))
        for run_id in active:
            if self.execution_fence is not None and not self._renew_execution_owner(
                run_id
            ):
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.store.record_cleanup_failed(
                    run_id, reason="shutdown_deadline_exhausted"
                )
                continue
            try:
                self.store.interrupt_active_claims(
                    run_id,
                    reason="shutdown",
                    lock_timeout_seconds=remaining,
                    fence=self.execution_fence,
                )
            except RuntimeError:
                continue
            except WorkflowLockTimeout:
                self.store.record_cleanup_failed(run_id, reason="shutdown_lock_timeout")
        self._submission_pool.shutdown(wait=True, cancel_futures=True)


__all__ = [
    "ConditionEvaluationError",
    "FailureClass",
    "RunScheduler",
    "classify_failure",
    "compute_retry_delay",
    "evaluate_condition",
    "evaluate_trigger_rule",
]
