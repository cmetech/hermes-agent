from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import RetryPolicy
from plugins.workflow.scheduler import RunScheduler, compute_retry_delay
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _start(store, package):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="retry",
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

    class ProviderRetries:
        def execute(self, context):
            nonlocal calls
            calls += 1
            assert context.max_provider_attempts == 1
            code = "provider_timeout" if calls == 1 else "unknown_side_effect"
            return NodeExecutionResult(
                "failed", error_code=code, metadata={"provider_attempts": 2}
            )

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["bash"] = ProviderRetries()
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=1)
    result = scheduler.advance(admitted.run_id)
    assert result["status"] == "paused"
    assert result["nodes"]["work"]["retry_consumed"] == 5
