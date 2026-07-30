from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import RetryPolicy
from plugins.workflow.scheduler import RunScheduler, compute_retry_delay
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _start(store, package, *, key="retry"):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


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
                metadata={"archon_terminal_failure": True},
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
