from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import RetryPolicy, RunExecutionLimits
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import (
    FailureClass,
    RunScheduler,
    classify_failure,
    compute_retry_delay,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _start(
    store,
    package,
    *,
    key="retry",
    execution_limits=None,
    run_metadata=None,
):
    prepared = store.prepare_run_snapshot(
        package,
        execution_limits=execution_limits,
    )
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            run_metadata=run_metadata,
        ),
        immutable_snapshot=prepared,
    )


def _archon_retry_package(
    tmp_path,
    workflow_writer,
    *,
    node_type: str,
    retry: dict[str, object] | None,
    combined_total_attempts: int,
    outward: bool = False,
):
    node = {
        "id": "work",
        node_type: "print('work')" if node_type == "script" else "work",
    }
    if node_type == "script":
        node["runtime"] = "uv"
    if retry is not None:
        node["retry"] = retry
    path = workflow_writer(
        tmp_path,
        name=f"archon-{node_type}-{combined_total_attempts}",
        nodes=[node],
    )
    sidecar = [
        "language_compatibility: archon-2026-07",
        f"limits: {{combined_retries: {combined_total_attempts}}}",
    ]
    if outward:
        sidecar.append("outward_action_nodes: [work]")
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "\n".join(sidecar) + "\n",
        encoding="utf-8",
    )
    return load_workflow(path)


@pytest.mark.parametrize(
    (
        "node_type",
        "retry",
        "combined_total_attempts",
        "requested_retries",
        "requested_total_attempts",
        "effective_total_attempts",
        "capped",
    ),
    [
        ("prompt", None, 5, 2, 3, 3, False),
        ("bash", None, 5, 0, 1, 1, False),
        ("bash", {"max_attempts": 1}, 5, 1, 2, 2, False),
        ("script", {"max_attempts": 1}, 5, 1, 2, 2, False),
        ("prompt", {"max_attempts": 5}, 5, 5, 6, 5, True),
    ],
)
def test_archon_retry_grant_and_success_charge_use_sealed_total_attempts(
    tmp_path,
    workflow_writer,
    node_type,
    retry,
    combined_total_attempts,
    requested_retries,
    requested_total_attempts,
    effective_total_attempts,
    capped,
) -> None:
    package = _archon_retry_package(
        tmp_path / node_type,
        workflow_writer,
        node_type=node_type,
        retry=retry,
        combined_total_attempts=combined_total_attempts,
    )
    store = RunStore(tmp_path / f"home-{node_type}")
    admitted = _start(store, package, key=f"sealed-{node_type}")
    grants: list[int] = []

    class Succeeds:
        def execute(self, context):
            grants.append(context.max_provider_attempts)
            return NodeExecutionResult(
                "succeeded",
                metadata={"provider_attempts": 0},
            )

    scheduler = RunScheduler(store)
    scheduler.executors[node_type] = Succeeds()

    result = scheduler.advance(admitted.run_id)

    node = result["nodes"]["work"]
    evidence = node["attempts"][0]["metadata"]
    assert result["status"] == "succeeded"
    assert grants == [effective_total_attempts]
    assert node["retry_consumed"] == 1
    assert evidence["requested_retries"] == requested_retries
    assert evidence["requested_total_attempts"] == requested_total_attempts
    assert evidence["effective_total_attempts"] == effective_total_attempts
    assert evidence["additional_provider_attempts"] == 0
    assert evidence["remaining_attempts"] == effective_total_attempts - 1
    assert evidence["capped"] is capped


@pytest.mark.parametrize(
    ("combined_total_attempts", "effective_total_attempts"),
    [(1, 1), (2, 2), (3, 3), (4, 3), (5, 3)],
)
def test_archon_ai_default_intersects_every_combined_cap_without_multiplication(
    tmp_path,
    workflow_writer,
    combined_total_attempts,
    effective_total_attempts,
) -> None:
    package = _archon_retry_package(
        tmp_path / str(combined_total_attempts),
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=combined_total_attempts,
    )
    store = RunStore(tmp_path / f"home-{combined_total_attempts}")
    admitted = _start(
        store,
        package,
        key=f"cap-{combined_total_attempts}",
        execution_limits=RunExecutionLimits(
            combined_retries=combined_total_attempts
        ),
    )
    grants: list[int] = []

    class Succeeds:
        def execute(self, context):
            grants.append(context.max_provider_attempts)
            return NodeExecutionResult(
                "succeeded", metadata={"provider_attempts": 0}
            )

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = Succeeds()

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert grants == [effective_total_attempts]
    assert result["nodes"]["work"]["retry_consumed"] == 1


def test_archon_mixed_provider_and_workflow_attempts_share_one_charge_ledger(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "mixed",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / "mixed-home")
    admitted = _start(store, package, key="mixed-ledger")
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    grants: list[int] = []

    class Mixed:
        def execute(self, context):
            grants.append(context.max_provider_attempts)
            if len(grants) == 1:
                return NodeExecutionResult(
                    "failed",
                    error_code="provider_timeout",
                    metadata={"provider_attempts": 1},
                )
            return NodeExecutionResult(
                "succeeded", metadata={"provider_attempts": 0}
            )

    scheduler = RunScheduler(
        store,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    scheduler.executors["prompt"] = Mixed()

    waiting = scheduler.advance(admitted.run_id)
    assert waiting["status"] == "waiting_retry"
    assert waiting["nodes"]["work"]["retry_consumed"] == 2
    now += timedelta(seconds=3)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert grants == [3, 1]
    assert result["nodes"]["work"]["retry_consumed"] == 3
    attempts = result["nodes"]["work"]["attempts"]
    assert [entry["metadata"]["additional_provider_attempts"] for entry in attempts] == [
        1,
        0,
    ]
    assert [entry["metadata"]["remaining_attempts"] for entry in attempts] == [
        1,
        0,
    ]


@pytest.mark.parametrize("reported", [None, -1, True, 3], ids=repr)
def test_archon_missing_or_invalid_exact_provider_evidence_consumes_full_grant(
    tmp_path, workflow_writer, reported
) -> None:
    package = _archon_retry_package(
        tmp_path / f"unknown-{reported!r}",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / f"unknown-home-{reported!r}")
    admitted = _start(store, package, key=f"unknown-{reported!r}")
    calls = 0

    class UnknownCount:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            metadata = {} if reported is None else {"provider_attempts": reported}
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                metadata=metadata,
            )

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = UnknownCount()

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "failed"
    assert calls == 1
    assert result["nodes"]["work"]["retry_consumed"] == 3
    evidence = result["nodes"]["work"]["attempts"][0]["metadata"]
    assert evidence["additional_provider_attempts"] == 2
    assert evidence["remaining_attempts"] == 0


def test_archon_explicit_inexact_provider_count_cannot_reduce_conservative_charge(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "explicit-inexact",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=3,
    )
    store = RunStore(tmp_path / "explicit-inexact-home")
    admitted = _start(store, package, key="explicit-inexact")

    class Inexact:
        def execute(self, _context):
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                metadata={
                    "provider_attempts": 0,
                    "provider_attempts_exact": False,
                },
            )

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = Inexact()
    result = scheduler.advance(admitted.run_id)

    node = result["nodes"]["work"]
    evidence = node["attempts"][0]["metadata"]
    assert result["status"] == "failed"
    assert node["retry_consumed"] == 3
    assert evidence["additional_provider_attempts"] == 2
    assert evidence["provider_attempts_exact"] is False


def test_archon_unknown_provider_outcome_reconciles_after_full_conservative_charge(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "unknown-outcome",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / "unknown-outcome-home")
    admitted = _start(store, package, key="unknown-outcome")

    class UnknownOutcome:
        def execute(self, _context):
            return NodeExecutionResult("failed", error_code="agent_failed")

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = UnknownOutcome()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "paused"
    node = result["nodes"]["work"]
    assert node["retry_consumed"] == 3
    assert node["pending_interaction"] == "reconcile"
    assert node["attempts"][0]["metadata"]["provider_attempts_exact"] is False


def test_archon_provider_grant_exhaustion_is_terminal_without_reconciliation(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "grant-exhausted",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=3,
    )
    store = RunStore(tmp_path / "grant-exhausted-home")
    admitted = _start(store, package, key="grant-exhausted")
    calls = 0

    class Exhausted:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            return NodeExecutionResult(
                "failed",
                error_code="provider_attempt_grant_exhausted",
                metadata={
                    "provider_attempts": 2,
                    "provider_attempts_exact": True,
                    "known_no_effect": True,
                },
            )

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = Exhausted()

    result = scheduler.advance(admitted.run_id)

    node = result["nodes"]["work"]
    assert result["status"] == "failed"
    assert node["state"] == "failed"
    assert node["retry_consumed"] == 3
    assert node.get("pending_interaction") is None
    assert result["last_error"]["code"] == "provider_attempt_grant_exhausted"
    assert calls == 1

    restarted = RunScheduler(RunStore(tmp_path / "grant-exhausted-home"))
    restarted.executors["prompt"] = Exhausted()
    resumed = restarted.advance(admitted.run_id)
    assert resumed["status"] == "failed"
    assert calls == 1


@pytest.mark.parametrize(
    ("code", "known_no_effect", "outward_action", "expected"),
    [
        ("provider_timeout", False, False, "transient"),
        ("process_exit", True, False, "transient"),
        ("agent_failed", True, False, "unknown_error"),
        ("agent_failed", False, False, "unknown_outcome"),
        ("provider_timeout", False, True, "unknown_outcome"),
        ("cleanup_failed", True, False, "fatal"),
        ("resource_limit", True, False, "fatal"),
        ("structured_output_capability_drift", True, False, "fatal"),
        ("authentication", True, False, "fatal"),
        ("authorization", True, False, "fatal"),
        ("credit_exhausted", True, False, "fatal"),
    ],
)
def test_phase3_failure_classes_distinguish_unknown_effects_and_terminal_errors(
    code, known_no_effect, outward_action, expected
) -> None:
    assert classify_failure(
        code,
        known_no_effect=known_no_effect,
        outward_action=outward_action,
    ).value == expected


@pytest.mark.parametrize(
    "error_code",
    [
        "authentication",
        "authorization",
        "credit_exhausted",
        "validation",
        "invalid_request",
        "output_limit",
        "resource_limit",
        "cleanup_failed",
        "execution_integrity",
        "structured_output_capability_drift",
        "workflow_execution_semantics_mismatch",
        "workflow_language_snapshot_invalid",
    ],
)
def test_phase3_outward_nodes_preserve_closed_fatal_classification(error_code):
    assert classify_failure(
        error_code,
        known_no_effect=True,
        outward_action=True,
    ) is FailureClass.FATAL


@pytest.mark.parametrize("on_error", ["transient", "all"])
def test_archon_outward_execution_integrity_is_terminal_before_provider_launch(
    tmp_path, workflow_writer, on_error
) -> None:
    retry = {"max_attempts": 2, "on_error": on_error}
    package = _archon_retry_package(
        tmp_path / on_error,
        workflow_writer,
        node_type="prompt",
        retry=retry,
        combined_total_attempts=3,
        outward=True,
    )
    store = RunStore(tmp_path / f"home-{on_error}")
    admitted = _start(
        store,
        package,
        key=f"integrity-{on_error}",
        run_metadata={"ai_entitlement": "real"},
    )

    class ProviderTrap:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, _request, **_kwargs):
            self.calls += 1
            raise AssertionError("provider launched past entitlement integrity failure")

    runner = ProviderTrap()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    node = result["nodes"]["work"]
    assert result["status"] == "failed"
    assert runner.calls == 0
    assert len(node["attempts"]) == 1
    assert node["retry_consumed"] == 1
    assert node.get("pending_interaction") is None
    assert node["attempts"][0]["metadata"]["provider_attempts_exact"] is True
    assert node["attempts"][0]["metadata"]["additional_provider_attempts"] == 0


@pytest.mark.parametrize(
    ("total_provider_calls", "fallback_model"),
    [(1, None), (2, "fallback-model")],
    ids=("initial-only", "fallback-call"),
)
def test_archon_text_worker_totals_convert_once_before_durable_charge(
    tmp_path,
    workflow_writer,
    total_provider_calls,
    fallback_model,
) -> None:
    node = {"id": "work", "prompt": "work"}
    if fallback_model is not None:
        node["fallbackModel"] = fallback_model
    workflow = workflow_writer(
        tmp_path / f"text-{total_provider_calls}",
        name=f"text-total-{total_provider_calls}",
        nodes=[node],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n"
        "limits: {combined_retries: 3}\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / f"text-home-{total_provider_calls}")
    admitted = _start(store, package, key=f"text-{total_provider_calls}")

    class TextRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="done",
                session_id="text-session",
                provider="fake",
                model="fake-model",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={"provider_attempts": total_provider_calls},
            )

    runner = TextRunner()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    node_state = result["nodes"]["work"]
    attempt = node_state["attempts"][0]["metadata"]
    assert result["status"] == "succeeded"
    assert len(runner.requests) == 1
    assert runner.requests[0].fallback_model == fallback_model
    assert node_state["retry_consumed"] == total_provider_calls
    assert attempt["additional_provider_attempts"] == total_provider_calls - 1
    assert attempt["provider_attempts_exact"] is True


@pytest.mark.parametrize(
    ("raised", "error_code", "expected_status"),
    [
        (OSError("network down"), "network_error", "failed"),
        (RuntimeError("runner broke"), "agent_execution_failed", "paused"),
    ],
    ids=("os-error", "runner-error"),
)
def test_archon_real_runner_exceptions_keep_conservative_charge_provenance(
    tmp_path, workflow_writer, raised, error_code, expected_status
) -> None:
    package = _archon_retry_package(
        tmp_path / error_code,
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=3,
    )
    store = RunStore(tmp_path / f"home-{error_code}")
    admitted = _start(store, package, key=error_code)

    class RaisingRunner:
        def run(self, _request, **_kwargs):
            raise raised

    result = RunScheduler(store, agent_runner=RaisingRunner()).advance(admitted.run_id)

    node = result["nodes"]["work"]
    attempt = node["attempts"][0]["metadata"]
    assert result["status"] == expected_status
    assert node["retry_consumed"] == 3
    assert attempt["additional_provider_attempts"] == 2
    assert attempt["provider_attempts_exact"] is False


@pytest.mark.parametrize(
    "reported",
    [None, -1],
    ids=("missing", "invalid"),
)
def test_archon_real_failed_result_keeps_conservative_charge_provenance(
    tmp_path, workflow_writer, reported
) -> None:
    package = _archon_retry_package(
        tmp_path / f"reported-{reported}",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=3,
    )
    store = RunStore(tmp_path / f"home-reported-{reported}")
    admitted = _start(store, package, key=f"reported-{reported}")

    class FailedRunner:
        def run(self, _request, **_kwargs):
            audit = {"failure_kind": "agent_failed"}
            if reported is not None:
                audit["provider_attempts"] = reported
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit=audit,
            )

    result = RunScheduler(store, agent_runner=FailedRunner()).advance(admitted.run_id)

    node = result["nodes"]["work"]
    attempt = node["attempts"][0]["metadata"]
    assert result["status"] == "paused"
    assert node["retry_consumed"] == 3
    assert attempt["additional_provider_attempts"] == 2
    assert attempt["provider_attempts_exact"] is False


def test_archon_fallback_without_exact_worker_count_uses_conservative_provenance(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "fallback-conservative",
        name="fallback-conservative",
        nodes=[{
            "id": "work",
            "prompt": "work",
            "fallbackModel": "fallback-model",
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n"
        "limits: {combined_retries: 3}\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "fallback-conservative-home")
    admitted = _start(store, package, key="fallback-conservative")

    class FallbackCountMissing:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fallback-model",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "provider_timeout"},
            )

    runner = FallbackCountMissing()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    node = result["nodes"]["work"]
    evidence = node["attempts"][0]["metadata"]
    assert runner.requests[0].fallback_model == "fallback-model"
    assert result["status"] == "failed"
    assert node["retry_consumed"] == 3
    assert evidence["additional_provider_attempts"] == 2
    assert evidence["provider_attempts_exact"] is False


def test_archon_structured_repair_failure_keeps_conservative_charge_provenance(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "repair",
        name="repair-accounting",
        nodes=[{
            "id": "work",
            "prompt": "produce structured data",
            "output_format": {
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n"
        "limits: {combined_retries: 3}\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="fake-provider",
            model="fake-model",
        ),
    )
    decision = execution_context.structured_output_decisions(package)["work"]
    store = RunStore(tmp_path / "repair-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="repair-accounting",
            concurrency_key=package.definition.name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )

    class RepairFails:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 2:
                raise RuntimeError("repair worker unavailable")
            structured = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": decision.declaration_source,
            }
            return PluginAgentRunResult(
                final_response="not json",
                session_id="repair-session",
                provider=decision.effective_provider,
                model=decision.model,
                status="completed",
                pending_interaction=None,
                usage={},
                audit={
                    **structured,
                    "api_calls": 1,
                    "api_mode": decision.api_mode,
                },
                structured_output=structured,
            )

    runner = RepairFails()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    node = result["nodes"]["work"]
    attempt = node["attempts"][0]["metadata"]
    assert len(runner.requests) == 2
    assert result["status"] == "failed"
    assert node["retry_consumed"] == 3
    assert attempt["additional_provider_attempts"] == 2
    assert attempt["provider_attempts_exact"] is False
    assert attempt["audit"]["provider_attempts_exact"] is False


@pytest.mark.parametrize(
    ("node_type", "additional_provider_attempts", "expected_charge"),
    [("bash", 0, 1), ("prompt", 1, 2)],
    ids=("deterministic", "ai"),
)
def test_archon_cleanup_failure_atomically_charges_and_blocks_restart_replay(
    tmp_path,
    workflow_writer,
    node_type,
    additional_provider_attempts,
    expected_charge,
) -> None:
    package = _archon_retry_package(
        tmp_path / node_type,
        workflow_writer,
        node_type=node_type,
        retry={"max_attempts": 2, "on_error": "all"},
        combined_total_attempts=3,
    )
    store = RunStore(tmp_path / f"cleanup-home-{node_type}")
    admitted = _start(store, package, key=f"cleanup-{node_type}")
    calls = 0

    class CleanupFails:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            return NodeExecutionResult(
                "failed",
                error_code="cleanup_failed",
                error_message="owned process remained",
                metadata={
                    "additional_provider_attempts": additional_provider_attempts,
                    "provider_attempts_exact": True,
                },
            )

    scheduler = RunScheduler(store)
    scheduler.executors[node_type] = CleanupFails()
    first = scheduler.advance(admitted.run_id)

    node = first["nodes"]["work"]
    assert first["desired_status"] == "cleanup_failed"
    assert node["retry_consumed"] == expected_charge
    assert node["attempts"][0]["metadata"]["retry_consumed"] == expected_charge
    assert node["attempts"][0]["metadata"]["provider_attempts_exact"] is True

    class ReplayTrap:
        def execute(self, _context):
            raise AssertionError("cleanup-blocked attempt replayed after restart")

    restarted = RunScheduler(store)
    restarted.executors[node_type] = ReplayTrap()
    after_restart = restarted.advance(admitted.run_id)

    assert calls == 1
    assert after_restart["desired_status"] == "cleanup_failed"
    assert after_restart["nodes"]["work"]["retry_consumed"] == expected_charge


def test_archon_unknown_no_effect_retries_only_under_explicit_all_policy(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "unknown-no-effect",
        workflow_writer,
        node_type="prompt",
        retry={"max_attempts": 1, "on_error": "all"},
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / "unknown-no-effect-home")
    admitted = _start(store, package, key="unknown-no-effect")
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    calls = 0

    class UnknownThenSuccess:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            if calls == 1:
                return NodeExecutionResult(
                    "failed",
                    error_code="agent_failed",
                    metadata={
                        "provider_attempts": 0,
                        "known_no_effect": True,
                    },
                )
            return NodeExecutionResult(
                "succeeded", metadata={"provider_attempts": 0}
            )

    scheduler = RunScheduler(
        store,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    scheduler.executors["prompt"] = UnknownThenSuccess()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=3)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert calls == 2
    assert result["nodes"]["work"]["retry_consumed"] == 2


def test_archon_deterministic_retry_requires_explicit_block_and_known_outcome(
    tmp_path, workflow_writer
) -> None:
    package = _archon_retry_package(
        tmp_path / "deterministic",
        workflow_writer,
        node_type="bash",
        retry={"max_attempts": 1},
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / "deterministic-home")
    admitted = _start(store, package, key="deterministic")
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    calls = 0

    class ExitsThenSucceeds:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            if calls == 1:
                return NodeExecutionResult("failed", error_code="process_exit")
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(
        store,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    scheduler.executors["bash"] = ExitsThenSucceeds()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=3)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert calls == 2
    assert result["nodes"]["work"]["retry_consumed"] == 2


@pytest.mark.parametrize(
    "error_code",
    [
        "authentication",
        "authorization",
        "credit_exhausted",
        "validation",
        "resource_limit",
        "structured_output_capability_drift",
    ],
)
def test_archon_all_policy_never_replays_terminal_failure_classes(
    tmp_path, workflow_writer, error_code
) -> None:
    package = _archon_retry_package(
        tmp_path / error_code,
        workflow_writer,
        node_type="prompt",
        retry={"max_attempts": 5, "on_error": "all"},
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / f"home-{error_code}")
    admitted = _start(store, package, key=error_code)
    calls = 0

    class Fatal:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            return NodeExecutionResult(
                "failed",
                error_code=error_code,
                metadata={"provider_attempts": 0},
            )

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = Fatal()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "failed"
    assert calls == 1
    assert result["nodes"]["work"]["retry_consumed"] == 1


def test_retry_delay_is_seeded_and_capped():
    policy = RetryPolicy(max_attempts=5, delay_ms=1000)
    assert RetryPolicy.from_mapping(None).max_attempts == 5
    assert compute_retry_delay(policy, 1, jitter=lambda: 0.5) == 1.0
    assert compute_retry_delay(policy, 3, jitter=lambda: 0.5) == 4.0
    assert compute_retry_delay(policy, 5, jitter=lambda: 1.0) <= 60.0


def test_transient_failure_waits_without_occupying_capacity_then_retries(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="retryable",
            nodes=[
                {
                    "id": "work",
                    "bash": "true",
                    "retry": {
                        "max_attempts": 3,
                        "delay_ms": 1000,
                        "on_error": "transient",
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    calls = 0

    class Flaky:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            if calls == 1:
                return NodeExecutionResult("failed", error_code="provider_timeout")
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = Flaky()

    waiting = scheduler.advance(admitted.run_id)
    assert waiting["status"] == "waiting_retry"
    assert waiting["nodes"]["work"]["state"] == "waiting_retry"
    assert (
        waiting["nodes"]["work"]["next_attempt_at"]
        == (now + timedelta(seconds=1)).isoformat()
    )
    assert calls == 1
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    assert calls == 1

    now += timedelta(seconds=1)
    assert scheduler.advance(admitted.run_id)["status"] == "succeeded"
    assert calls == 2


def test_waiting_retry_lane_policy_releases_and_requeues_when_due_lane_is_busy(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="retry-lane",
            nodes=[
                {
                    "id": "work",
                    "bash": "true",
                    "retry": {
                        "max_attempts": 2,
                        "delay_ms": 1000,
                        "on_error": "transient",
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    first = _start(store, package, key="first")
    second = _start(store, package, key="second")
    assert second.disposition == "queued"
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)

    class FailsOnce:
        def execute(self, _context):
            return NodeExecutionResult("failed", error_code="provider_timeout")

    first_scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    first_scheduler.executors["bash"] = FailsOnce()
    assert first_scheduler.advance(first.run_id)["status"] == "waiting_retry"

    assert store.try_promote_run(second.run_id)
    assert store.get_run_status(second.run_id)["status"] == "running"

    due = now + timedelta(seconds=1)
    assert store.wake_due_retries(first.run_id, now=due) == ()
    requeued = store.get_run_status(first.run_id)
    assert requeued["status"] == "queued"
    assert requeued["blocked_by_run_id"] == second.run_id
    assert requeued["nodes"]["work"]["state"] == "waiting_retry"

    assert RunScheduler(store).advance(second.run_id)["status"] == "succeeded"
    assert store.try_promote_run(first.run_id)
    assert store.wake_due_retries(first.run_id, now=due) == ("work",)


def test_default_paused_outward_lane_policy_holds_and_explicit_release_interleaves(
    tmp_path, workflow_writer
):
    default_path = workflow_writer(
        tmp_path / "paused-package",
        name="held-paused-lane",
        nodes=[{"id": "gate", "approval": {"message": "Hold?"}}],
    )
    default_path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: queue\noutward_action_nodes: [gate]\n",
        encoding="utf-8",
    )
    paused_package = load_workflow(default_path)
    paused_store = RunStore(tmp_path / "paused-home")
    paused = _start(paused_store, paused_package, key="paused")
    assert RunScheduler(paused_store).advance(paused.run_id)["status"] == "paused"
    behind_paused = _start(paused_store, paused_package, key="behind-paused")
    assert behind_paused.disposition == "queued"
    assert not paused_store.try_promote_run(behind_paused.run_id)

    release_path = workflow_writer(
        tmp_path / "released-package",
        name="released-paused-lane",
        nodes=[{"id": "gate", "approval": {"message": "Hold?"}}],
    )
    release_path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: queue\npause_lane_policy: release\n"
        "outward_action_nodes: [gate]\n",
        encoding="utf-8",
    )
    release_package = load_workflow(release_path)
    release_store = RunStore(tmp_path / "released-home")
    released = _start(release_store, release_package, key="released")
    assert RunScheduler(release_store).advance(released.run_id)["status"] == "paused"
    interleaved = _start(release_store, release_package, key="interleaved")

    assert interleaved.disposition == "created"
    assert release_store.load_run(interleaved.run_id)["status"] == "running"


def test_interrupted_lane_policy_releases_replay_safe_but_holds_uncertain_outward(
    tmp_path, workflow_writer
) -> None:
    safe_package = load_workflow(
        workflow_writer(
            tmp_path / "safe-interrupted-package",
            name="safe-interrupted-lane",
        )
    )
    safe_store = RunStore(tmp_path / "safe-interrupted-home")
    safe = _start(safe_store, safe_package, key="safe-interrupted")
    safe_store.interrupt_for_host_pressure(safe.run_id, message="synthetic pressure")
    behind_safe = _start(safe_store, safe_package, key="behind-safe")
    assert behind_safe.disposition == "created"

    outward_path = workflow_writer(
        tmp_path / "outward-interrupted-package",
        name="outward-interrupted-lane",
    )
    outward_path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: queue\npause_lane_policy: release\n"
        "outward_action_nodes: [start]\n",
        encoding="utf-8",
    )
    outward_package = load_workflow(outward_path)
    outward_store = RunStore(tmp_path / "outward-interrupted-home")
    outward = _start(outward_store, outward_package, key="outward-interrupted")
    projection = outward_store.load_run(outward.run_id)
    nodes = {node_id: dict(node) for node_id, node in projection["nodes"].items()}
    nodes["start"].update({
        "state": "interrupted",
        "attempts": [
            {
                "attempt_id": "uncertain-outward-attempt",
                "state": "interrupted",
                "effect_classification": "outward",
            }
        ],
        "recovery": {
            "attempt_id": "uncertain-outward-attempt",
            "effect_classification": "outward",
            "observation": "outcome_uncertain",
            "termination_confirmed": False,
        },
    })
    outward_store.append_event(
        outward.run_id,
        "fault_injected_uncertain_outward_interrupt",
        projection_updates={"status": "interrupted", "nodes": nodes},
    )
    with outward_store._connect() as connection:
        connection.execute(
            "UPDATE runs SET status='interrupted' WHERE run_id=?",
            (outward.run_id,),
        )
    behind_outward = _start(outward_store, outward_package, key="behind-outward")

    assert behind_outward.disposition == "queued"
    assert not outward_store.try_promote_run(behind_outward.run_id)


def test_fatal_and_unknown_side_effect_failures_are_not_retried(
    tmp_path, workflow_writer
):
    for error_code, expected in [
        ("authentication", "failed"),
        ("authorization", "failed"),
        ("credit_exhausted", "failed"),
        ("validation", "failed"),
        ("unknown_side_effect", "paused"),
    ]:
        package = load_workflow(
            workflow_writer(
                tmp_path / error_code,
                name=error_code.replace("_", "-"),
                nodes=[
                    {
                        "id": "work",
                        "bash": "true",
                        "retry": {"max_attempts": 5, "on_error": "all"},
                    }
                ],
            )
        )
        store = RunStore(tmp_path / f"home-{error_code}")
        admitted = _start(store, package)

        class Fatal:
            def execute(self, _context):
                return NodeExecutionResult("failed", error_code=error_code)

        scheduler = RunScheduler(store)
        scheduler.executors["bash"] = Fatal()
        result = scheduler.advance(admitted.run_id)
        assert result["status"] == expected
        assert len(result["nodes"]["work"]["attempts"]) == 1


def test_archon_trusted_structured_failure_is_terminal_without_freezing_legacy_retry(
    tmp_path, workflow_writer
):
    def package_at(root, *, archon):
        workflow = workflow_writer(
            root,
            name="archon-terminal" if archon else "legacy-retry",
            nodes=[
                {
                    "id": "work",
                    "prompt": "work",
                    "retry": {
                        "max_attempts": 3,
                        "delay_ms": 1000,
                        "on_error": "all",
                    },
                }
            ],
        )
        if archon:
            workflow.with_name("example.hermes.yaml").write_text(
                "language_compatibility: archon-2026-07\n", encoding="utf-8"
            )
        return load_workflow(workflow)

    class InvalidStructuredOutput:
        def execute(self, _context):
            return NodeExecutionResult(
                "failed",
                error_code="structured_output_invalid",
                metadata={
                    "archon_terminal_failure": True,
                    "provider_attempts": 0,
                },
            )

    archon_store = RunStore(tmp_path / "archon-home")
    archon = _start(
        archon_store,
        package_at(tmp_path / "archon-package", archon=True),
        key="archon",
    )
    archon_scheduler = RunScheduler(archon_store)
    archon_scheduler.executors["prompt"] = InvalidStructuredOutput()

    archon_result = archon_scheduler.advance(archon.run_id)

    assert archon_result["status"] == "failed"
    assert len(archon_result["nodes"]["work"]["attempts"]) == 1

    legacy_store = RunStore(tmp_path / "legacy-home")
    legacy = _start(
        legacy_store,
        package_at(tmp_path / "legacy-package", archon=False),
        key="legacy",
    )
    legacy_scheduler = RunScheduler(legacy_store)
    legacy_scheduler.executors["prompt"] = InvalidStructuredOutput()

    legacy_result = legacy_scheduler.advance(legacy.run_id)

    assert legacy_result["status"] == "waiting_retry"
    assert len(legacy_result["nodes"]["work"]["attempts"]) == 1


def test_cancel_wins_over_due_retry_wakeup(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-retry",
            nodes=[
                {
                    "id": "work",
                    "bash": "true",
                    "retry": {"max_attempts": 2, "delay_ms": 1000},
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)

    class Timeout:
        def execute(self, _context):
            return NodeExecutionResult("failed", error_code="provider_timeout")

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = Timeout()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"

    store.cancel_run(admitted.run_id)
    assert store.wake_due_retries(admitted.run_id, now=now + timedelta(days=1)) == ()
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


def test_archon_cancel_after_claim_wins_before_executor_and_charges_nothing(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = _archon_retry_package(
        tmp_path / "cancel-after-claim",
        workflow_writer,
        node_type="prompt",
        retry=None,
        combined_total_attempts=5,
    )
    store = RunStore(tmp_path / "cancel-after-claim-home")
    admitted = _start(store, package, key="cancel-after-claim")
    original = store.mark_node_started

    def cancel_after_mark(claim, **kwargs):
        original(claim, **kwargs)
        store.cancel_run(claim.run_id)

    monkeypatch.setattr(store, "mark_node_started", cancel_after_mark)
    calls = 0

    class MustNotRun:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = MustNotRun()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "cancelled"
    assert result["nodes"]["work"].get("retry_consumed", 0) == 0
    assert calls == 0


def test_differently_timed_retries_each_wake_when_due(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="multi-retry",
            nodes=[
                {
                    "id": node_id,
                    "bash": "true",
                    "retry": {"max_attempts": 2, "delay_ms": delay},
                }
                for node_id, delay in (("early", 1000), ("late", 2000))
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    calls = {"early": 0, "late": 0}

    class OnceFlaky:
        def execute(self, context):
            calls[context.node.id] += 1
            if calls[context.node.id] == 1:
                return NodeExecutionResult("failed", error_code="provider_timeout")
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = OnceFlaky()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=1)
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    assert calls == {"early": 2, "late": 1}
    now += timedelta(seconds=1)
    assert scheduler.advance(admitted.run_id)["status"] == "succeeded"
    assert calls == {"early": 2, "late": 2}


def test_provider_attempts_are_cumulative_and_unknown_outcome_still_reconciles(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="combined-budget",
            nodes=[
                {
                    "id": "work",
                    "bash": "true",
                    "retry": {"max_attempts": 5, "delay_ms": 1000},
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    calls = 0
    grants = []

    class ProviderRetries:
        def execute(self, context):
            nonlocal calls
            calls += 1
            assert context.execution_limits is not None
            assert context.execution_limits.combined_retries == 5
            grants.append(context.max_provider_attempts)
            code = "provider_timeout" if calls == 1 else "unknown_side_effect"
            return NodeExecutionResult(
                "failed",
                error_code=code,
                metadata={"provider_attempts": 2 if calls == 1 else 1},
            )

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = ProviderRetries()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=1)
    result = scheduler.advance(admitted.run_id)
    assert result["status"] == "paused"
    assert grants == [5, 2]
    assert result["nodes"]["work"]["retry_consumed"] == 5


def test_run_combined_retries_cap_explicit_node_retry_attempts(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "run-cap",
        name="run-cap",
        nodes=[{
            "id": "work",
            "bash": "true",
            "retry": {"max_attempts": 5, "delay_ms": 1000},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 2}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "run-cap-home")
    admitted = _start(store, package)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    calls = 0

    class AlwaysTransient:
        def execute(self, context):
            nonlocal calls
            calls += 1
            assert context.execution_limits is not None
            assert context.execution_limits.combined_retries == 2
            return NodeExecutionResult("failed", error_code="provider_timeout")

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = AlwaysTransient()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=1)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "failed"
    assert calls == 2
    assert result["nodes"]["work"]["retry_consumed"] == 2


def test_exhausted_replay_fails_without_invoking_executor(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "exhausted-replay",
            name="exhausted-replay",
            nodes=[{
                "id": "work",
                "bash": "true",
                "retry": {"max_attempts": 2, "delay_ms": 1000},
            }],
        )
    )
    store = RunStore(tmp_path / "exhausted-replay-home")
    admitted = _start(store, package, key="exhausted-replay")

    class ExhaustsBudget:
        def execute(self, _context):
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                metadata={"provider_attempts": 1},
            )

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = ExhaustsBudget()
    exhausted = scheduler.advance(admitted.run_id)
    assert exhausted["status"] == "failed"
    assert exhausted["nodes"]["work"]["retry_consumed"] == 2

    replayed = store.retry_run(admitted.run_id, node_id="work")
    assert replayed["nodes"]["work"]["state"] == "ready"

    calls = 0
    grants = []

    class MustNotRun:
        def execute(self, context):
            nonlocal calls
            calls += 1
            grants.append(context.max_provider_attempts)
            return NodeExecutionResult("succeeded")

    scheduler.executors["bash"] = MustNotRun()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "failed"
    assert result["nodes"]["work"]["state"] == "failed"
    assert result["last_error"]["code"] == "retry_budget_exhausted"
    assert result["nodes"]["work"]["retry_consumed"] == 2
    assert calls == 0
    assert grants == []
