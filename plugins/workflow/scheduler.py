"""Bounded durable scheduling for portable workflow DAGs."""

from __future__ import annotations

import ast
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
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
import threading
import time
import uuid
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from agent.structured_output import StructuredOutputStrategy
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.entitlement import AIEntitlementResolution, derive_ai_entitlement
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
from plugins.workflow.models import (
    DeadlineBudget,
    ExecutionFence,
    RetryPolicy,
    RunExecutionLimits,
    WorkflowNode,
    WorkflowLanguageProfile,
    WorkflowPackage,
    WorkflowRuntimeConfig,
)
from plugins.workflow.resources import ResourceResolver, VariableContext
from plugins.workflow.schema import is_inline_script, load_workflow_snapshot
from plugins.workflow.sessions import NodeSessionRegistry
from plugins.workflow.store import NodeClaim, RunStore, StorageQuotaError
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
_LEGACY_PACKAGE_PATHS = 4096
_LEGACY_PACKAGE_PATH_CHARS = 512
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


class FailureClass(Enum):
    TRANSIENT = "transient"
    FATAL = "fatal"
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
    "process_exit",
}


def classify_failure(
    error_code: str | None,
    *,
    workflow_attempt: int = 1,
    provider_attempts: int = 0,
    maximum: int = 5,
) -> FailureClass:
    """Map executor failures to a closed retry taxonomy."""
    code = (error_code or "").lower()
    if code in {"cancelled", "shutdown", "interrupted"}:
        return FailureClass.CANCELLED
    if code in {"unknown_side_effect", "outcome_unknown"}:
        return FailureClass.RECONCILE
    if workflow_attempt + provider_attempts >= maximum:
        return FailureClass.EXHAUSTED
    if code in _TRANSIENT_FAILURES:
        return FailureClass.TRANSIENT
    if code in _FATAL_FAILURES:
        return FailureClass.FATAL
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
        raise ValueError("sealed structured-output decision is missing")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("sealed structured-output decision is malformed") from exc
    if not isinstance(value, Mapping) or set(value) != _SEALED_STRUCTURED_DECISION_FIELDS:
        raise ValueError("sealed structured-output decision is malformed")
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
        raise ValueError("sealed structured-output decision is malformed") from exc
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
        raise ValueError("sealed structured-output decision is contradictory")
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
            registry = session_registry or NodeSessionRegistry(store.hermes_home)
            ai_executor = AgentNodeExecutor(
                agent_runner,
                session_registry=registry,
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
        self, projection: Mapping[str, object], run_directory: Path
    ) -> dict[str, object]:
        outputs: dict[str, object] = {}
        for artifact in projection.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            relative = str(artifact.get("relative_path", ""))
            if not Path(relative).name.startswith(("output.", "stdout.")):
                continue
            node_id = str(artifact.get("node_id", ""))
            try:
                text = self._read_text(run_directory / relative)
                try:
                    outputs[node_id] = json.loads(text)
                except json.JSONDecodeError:
                    outputs[node_id] = text
            except (OSError, UnicodeError, ValueError):
                continue
        return outputs

    def _variables(
        self,
        projection: dict[str, object],
        run_directory: Path,
        *,
        sealed_resource_paths: frozenset[str] | None = None,
        sealed_resource_bytes: Mapping[str, bytes] | None = None,
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
        outputs = {
            node: value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            for node, value in self._output_values(projection, run_directory).items()
        }
        return VariableContext(
            arguments=arguments,
            user_message=arguments,
            artifacts_dir=run_directory / "artifacts",
            workflow_id=str(projection["run_id"]),
            base_branch="base",
            docs_dir=run_directory / "docs",
            node_outputs=outputs,
        )

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
            outputs = self._output_values(projection, self.store.run_directory(run_id))
            transitions: dict[str, tuple[str, str | None]] = {}
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
                    try:
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
            if not transitions:
                self.store.finalize_if_complete(run_id)
                return
            self.store.transition_pending_nodes(run_id, transitions)

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

    def _prepare_run_package(self, run_id: str, schedule_revalidation):
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
            return (
                package,
                self._run_execution_limits(package),
                sealed_paths,
                sealed_bytes,
            )
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
    ) -> float:
        limits = execution_limits or self.profile_execution_limits
        if node.node_type in {"command", "prompt", "loop", "approval"}:
            return float(limits.ai_wall_timeout_seconds)
        return min(
            float(node.options.get("timeout", limits.subprocess_timeout_seconds)),
            float(limits.subprocess_timeout_seconds),
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

    def _heartbeat_journal_reserve(
        self,
        node: WorkflowNode,
        execution_limits: RunExecutionLimits | None = None,
    ) -> int:
        heartbeat_count = math.ceil(
            self._node_timeout(node, execution_limits) / self.heartbeat_seconds
        )
        return heartbeat_count * 4096

    def _execute_claim(
        self,
        run_id: str,
        claim: NodeClaim,
        node: WorkflowNode,
        package,
        projection: dict[str, object],
        execution_limits: RunExecutionLimits,
        sealed_resource_paths: frozenset[str] | None,
        sealed_resource_bytes: Mapping[str, bytes] | None,
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
                    )
                    return
                node_state = dict(projection["nodes"][node.id])
                retry_policy = self._effective_retry_policy(node, execution_limits)
                consumed_attempts = max(
                    0, int(node_state.get("retry_consumed", 0))
                )
                remaining_attempts = retry_policy.max_attempts - consumed_attempts
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
                    )
                    return
                approved_action_digest = self.store.consume_action_grant(claim)
                node_state.pop("action_grant", None)
                if approved_action_digest is not None:
                    node_state["approved_action_digest"] = approved_action_digest
                timeout = self._node_timeout(node, execution_limits)
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
                    deadline_budget = DeadlineBudget.create(
                        now=self._monotonic(),
                        wall_seconds=timeout,
                        idle_seconds=idle_timeout,
                        provider_seconds=min(
                            execution_limits.provider_request_timeout_seconds,
                            timeout,
                        ),
                    )
                    variables = self._variables(
                        projection,
                        self.store.run_directory(run_id),
                        sealed_resource_paths=sealed_resource_paths,
                        sealed_resource_bytes=sealed_resource_bytes,
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
                            predecessor_results={
                                dependency: {
                                    field: projection["nodes"][dependency][field]
                                    for field in ("session_id", "cache_fingerprint")
                                    if field in projection["nodes"][dependency]
                                }
                                for dependency in node.depends_on
                            },
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
                            # Provider and workflow attempts draw from the same
                            # frozen per-run allowance, so the retry layers do not multiply.
                            max_provider_attempts=remaining_attempts,
                            cancellation_reason=lambda: self._cancellation_reason(
                                run_id
                            ),
                            record_iteration=lambda artifacts, state: (
                                self.store.record_loop_iteration(
                                    claim,
                                    artifacts=artifacts,
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
                            sealed_resource_paths=sealed_resource_paths,
                            sealed_resource_bytes=sealed_resource_bytes,
                            language_profile=package.language.effective_profile,
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
            self._persist_result(claim, node, result, execution_limits)
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

    def _persist_result(
        self,
        claim: NodeClaim,
        node: WorkflowNode,
        result: NodeExecutionResult,
        execution_limits: RunExecutionLimits,
    ) -> None:
        if result.status == "failed" and result.error_code == "cleanup_failed":
            self.store.block_cleanup_failed(
                claim,
                artifacts=result.artifacts,
                error_message=result.error_message,
            )
            return
        if result.status != "failed":
            self.store.complete_node(
                claim,
                status=result.status,
                artifacts=result.artifacts,
                error_code=result.error_code,
                error_message=result.error_message,
                metadata=result.metadata,
            )
            return
        policy = self._effective_retry_policy(node, execution_limits)
        projection = self.store.load_run(claim.run_id)
        node_state = projection["nodes"][claim.node_id]
        consumed_before = int(node_state.get("retry_consumed", 0))
        provider_attempts = int(result.metadata.get("provider_attempts", 0))
        consumed = min(policy.max_attempts, consumed_before + 1 + provider_attempts)
        failure = classify_failure(
            result.error_code,
            workflow_attempt=consumed_before + 1,
            provider_attempts=provider_attempts,
            maximum=policy.max_attempts,
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
        if (
            policy.on_error == "all"
            and failure is FailureClass.FATAL
            and result.error_code not in never_retry
            and not archon_terminal_failure
        ):
            failure = FailureClass.TRANSIENT
        metadata = {**result.metadata, "retry_consumed": consumed}
        if failure is FailureClass.RECONCILE:
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
        prepared_package = self._prepare_run_package(run_id, authorization)
        if prepared_package is None:
            return self.store.load_run(run_id)
        (
            package,
            execution_limits,
            sealed_resource_paths,
            sealed_resource_bytes,
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
                ready = sorted(
                    node_id
                    for node_id, state in projection["nodes"].items()
                    if state["state"] == "ready"
                )
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
                    try:
                        claim = self.store.claim_node(
                            run_id,
                            node_id,
                            self.owner_id,
                            lease_seconds=self.lease_seconds,
                            now=self._utcnow(),
                            monotonic_now=self._monotonic(),
                            journal_reserve_bytes=self._heartbeat_journal_reserve(
                                by_id[node_id], execution_limits
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
                        claims.append((claim, by_id[node_id], projection))
                if fence_lost:
                    for claim, _node, _projection in claims:
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
                            execution_limits,
                            sealed_resource_paths,
                            sealed_resource_bytes,
                        )
                        for claim, node, snapshot in claims
                    ]
                    for future in futures:
                        future.result()
                executed += len(claims)
            self._resolve_graph(run_id, package.definition.nodes)
            return self.store.load_run(run_id)
        finally:
            with self._activity:
                self._active_runs.discard(run_id)
                self._activity.notify_all()

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
        authorizations = {}
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
            authorized_run_ids.append(run_id)
        run_ids = authorized_run_ids
        packages = {}
        execution_limits = {}
        sealed_resource_paths = {}
        sealed_resource_bytes = {}
        prepared_run_ids = []
        for run_id in run_ids:
            prepared_package = self._prepare_run_package(
                run_id,
                authorizations[run_id],
            )
            if prepared_package is None:
                continue
            package, limits, paths, resource_bytes = prepared_package
            packages[run_id] = package
            execution_limits[run_id] = limits
            sealed_resource_paths[run_id] = paths
            sealed_resource_bytes[run_id] = resource_bytes
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
                        futures.pop(future)
                        future.result()
                candidates: dict[str, list[str]] = {}
                snapshots = {}
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
                    self._resolve_graph(run_id, packages[run_id].definition.nodes)
                    projection = self.store.load_run(run_id)
                    if projection["status"] != "running":
                        continue
                    active.append(run_id)
                    snapshots[run_id] = projection
                    candidates[run_id] = sorted(
                        node_id
                        for node_id, node in projection["nodes"].items()
                        if node["state"] == "ready"
                    )
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
                        try:
                            claim = self.store.claim_node(
                                run_id,
                                node_id,
                                self.owner_id,
                                lease_seconds=self.lease_seconds,
                                now=self._utcnow(),
                                monotonic_now=self._monotonic(),
                                journal_reserve_bytes=self._heartbeat_journal_reserve(
                                    next(
                                        node
                                        for node in packages[run_id].definition.nodes
                                        if node.id == node_id
                                    ),
                                    execution_limits[run_id],
                                ),
                                executor_id=next(
                                    node.node_type
                                    for node in packages[run_id].definition.nodes
                                    if node.id == node_id
                                ),
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
                        node = next(
                            node
                            for node in packages[run_id].definition.nodes
                            if node.id == node_id
                        )
                        claims.append((
                            run_id,
                            claim,
                            node,
                            packages[run_id],
                            snapshots[run_id],
                            execution_limits[run_id],
                            sealed_resource_paths[run_id],
                            sealed_resource_bytes[run_id],
                        ))
                        fair_cursor = (active.index(run_id) + 1) % len(active)
                        claimed_this_round = True
                    if fence_lost:
                        break
                    if not claimed_this_round:
                        break
                if fence_lost:
                    for (
                        _run_id,
                        claim,
                        _node,
                        _package,
                        _snapshot,
                        _limits,
                        _sealed_paths,
                        _sealed_bytes,
                    ) in claims:
                        self.store.release_claim_before_execution(claim)
                    break
                for claim in claims:
                    future = pool.submit(self._execute_claim, *claim)
                    futures[future] = claim[0]
                if not claims:
                    if not futures:
                        break
                    done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.pop(future)
                        future.result()
            if futures:
                done, _pending = wait(futures)
                for future in done:
                    future.result()
            return {run_id: self.store.load_run(run_id) for run_id in run_ids}
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            with self._activity:
                self._active_runs.difference_update(run_ids)
                self._activity.notify_all()

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
