from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
import plugins.workflow.coordinator as coordinator_module
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ExecutionFence
from plugins.workflow.notifications import NotificationOutbox
import plugins.workflow.notifications as notifications_module
from plugins.workflow.sanitize import public_run_projection
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
import plugins.workflow.store as store_module
from plugins.workflow.store import (
    ForegroundExecutionConflict,
    JournalRecoveryError,
    RunStore,
)
from plugins.workflow.executors.base import NodeExecutionResult
from tools.managed_process import ProcessIdentity


UTC = timezone.utc


class _Clocks:
    def __init__(self, wall: datetime, monotonic: float = 100.0) -> None:
        self.wall = wall
        self.monotonic_value = monotonic

    def utcnow(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def lease_sample(self) -> LeaseClockSample:
        return LeaseClockSample(self.wall, self.monotonic_value, "scheduled-test")


def _identity(name: str) -> CoordinatorIdentity:
    process = ProcessIdentity.capture(os.getpid())
    return CoordinatorIdentity(
        owner_id=name,
        host_kind="gateway",
        host_instance_id=f"host-{name}",
        pid=process.pid,
        process_start_time=process.start_time,
    )


def _service(
    home: Path,
    clocks: _Clocks,
    *,
    sweep_backoff_seconds: tuple[float, ...] = (30.0, 60.0),
) -> WorkflowCoordinatorService:
    return WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="scheduled-test",
        ),
        hermes_home=home,
        heartbeat_seconds=100.0,
        lease_seconds=300.0,
        sweep_backoff_seconds=sweep_backoff_seconds,
        utcnow=clocks.utcnow,
        monotonic=clocks.monotonic,
    )


def _package(workflow_writer, root: Path, *, name: str):
    return load_workflow(workflow_writer(root, name=name))


def _admit(
    store: RunStore,
    package,
    *,
    key: str,
    schedule_at: str | None,
    concurrency_key: str | None = None,
    concurrency_policy: str = "queue",
):
    snapshot = store.prepare_run_snapshot(package)
    metadata = {"schedule_at": schedule_at} if schedule_at is not None else None
    result = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key=key,
            concurrency_key=concurrency_key or key,
            concurrency_policy=concurrency_policy,
            execution_mode="background",
            run_metadata=metadata,
        ),
        immutable_snapshot=snapshot,
    )
    assert result.run_id is not None
    return result


def _rewrite_as_legacy_policy_projection(store: RunStore, run_id: str):
    directory = store.run_directory(run_id)
    projection_path = directory / "run.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection.pop("outward_action_nodes")
    encoded_frames = []
    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        event["projection"].pop("outward_action_nodes")
        event["projection_sha256"] = store_module._projection_digest(
            event["projection"]
        )
        event.pop("frame_sha256")
        _framed, encoded = store_module._encode_journal_frame(event)
        encoded_frames.append(encoded)
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    (directory / "events.jsonl").write_bytes(b"".join(encoded_frames))
    return store.load_run(run_id)


def _legacy_repair_quota_case(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
):
    clocks = _Clocks(datetime(2026, 4, 6, 9, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="legacy-repair-quota")
    workflow_path = workflow_writer(
        tmp_path / "package",
        name="legacy-repair-quota",
    )
    workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml").write_text(
        "outward_action_nodes:\n- start\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
    admitted = _admit(store, package, key="legacy-quota", schedule_at=None)
    projection = _rewrite_as_legacy_policy_projection(store, admitted.run_id)
    assert store._transition_run_repair(
        "legacy_effect_policy_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    directory = store.run_directory(admitted.run_id)
    evidence_bytes = sum(
        (directory / filename).stat().st_size
        for filename in ("run.json", "events.jsonl", "policy.yaml")
    )
    corroborate = MagicMock(
        return_value=(projection, store_module._projection_digest(projection), "a" * 64)
    )
    monkeypatch.setattr(store, "_corroborate_run_evidence_locked", corroborate)
    monkeypatch.setattr(
        store,
        "_legacy_effect_policy_nodes",
        lambda *_args, **_kwargs: [],
    )
    return store, admitted.run_id, evidence_bytes, corroborate


def test_future_scheduled_run_isolated_from_immediate_queue_consumers(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=4,
        max_queued_runs=2,
        max_nonterminal_runs=3,
        lease_clock=clocks.lease_sample,
    )
    _coordinator, identity, epoch = _leader(
        store, clocks, name="consumer-matrix-leader"
    )
    package = _package(workflow_writer, tmp_path / "package", name="consumer-matrix")
    due = clocks.wall + timedelta(hours=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        concurrency_key="shared",
    )

    projection = store.load_run(scheduled.run_id)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, queue_position, queue_sequence, lane_state, "
            "foreground_owner_id, foreground_epoch FROM runs WHERE run_id=?",
            (scheduled.run_id,),
        ).fetchone()
        counts = {
            "queued": connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status='queued'"
            ).fetchone()[0],
            "nonterminal": connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN "
                "('queued','running','waiting_retry','paused','interrupted')"
            ).fetchone()[0],
            "running": connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status='running'"
            ).fetchone()[0],
            "workers": connection.execute(
                "SELECT COUNT(*) FROM worker_claims"
            ).fetchone()[0],
        }

    assert tuple(row)[:2] == ("queued", None)
    assert isinstance(row["queue_sequence"], int)
    assert row["lane_state"] == "released"
    assert row["foreground_owner_id"] is None
    assert row["foreground_epoch"] is None
    assert projection["execution_mode"] == "background"
    assert counts == {"queued": 1, "nonterminal": 1, "running": 0, "workers": 0}
    assert (
        public_run_projection(projection, now=clocks.wall)["presentation_state"]
        == "scheduled_wait"
    )
    due_projection = public_run_projection(projection, now=due)
    assert "presentation_state" not in due_projection
    ordinary, _cursor, _exhausted = store.coordinator_candidates(
        after=None, now=clocks.wall
    )
    assert scheduled.run_id not in {str(item["run_id"]) for item in ordinary}
    assert (
        store.claim_foreground_execution(
            scheduled.run_id,
            owner_id="must-not-adopt",
            now=clocks.wall,
            lease_seconds=30,
        )
        is None
    )
    with pytest.raises(ForegroundExecutionConflict, match="not foreground-owned"):
        store.adopt_expired_foreground(
            scheduled.run_id,
            ExecutionFence(identity.owner_id, epoch),
            clocks.wall,
        )

    second = _admit(
        store,
        package,
        key="scheduled-capacity",
        schedule_at=(due + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )
    rejected_snapshot = store.prepare_run_snapshot(package)
    rejected = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=rejected_snapshot.definition_digest,
            policy_digest=rejected_snapshot.policy_digest,
            input_manifest_digest=rejected_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="scheduled-over-capacity",
            concurrency_key="other",
            concurrency_policy="queue",
            execution_mode="background",
            run_metadata={
                "schedule_at": (due + timedelta(minutes=2))
                .isoformat()
                .replace("+00:00", "Z")
            },
        ),
        immutable_snapshot=rejected_snapshot,
    )
    assert second.run_id is not None
    assert rejected.reason_code == "queued_capacity"

    quota_store = RunStore(
        tmp_path / "quota-home",
        max_queued_runs=3,
        max_nonterminal_runs=1,
        lease_clock=clocks.lease_sample,
    )
    _leader(quota_store, clocks, name="nonterminal-quota-leader")
    quota_package = _package(
        workflow_writer, tmp_path / "quota-package", name="nonterminal-quota"
    )
    _admit(
        quota_store,
        quota_package,
        key="first",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
    )
    quota_snapshot = quota_store.prepare_run_snapshot(quota_package)
    quota_rejected = quota_store.start_run(
        RunAdmissionRequest(
            workflow_name=quota_package.definition.name,
            definition_digest=quota_snapshot.definition_digest,
            policy_digest=quota_snapshot.policy_digest,
            input_manifest_digest=quota_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="second",
            concurrency_key="second",
            concurrency_policy="queue",
            execution_mode="background",
            run_metadata={"schedule_at": due.isoformat().replace("+00:00", "Z")},
        ),
        immutable_snapshot=quota_snapshot,
    )
    assert quota_rejected.reason_code == "nonterminal_capacity"


@pytest.mark.parametrize("policy", ["allow", "queue", "forbid"])
def test_future_scheduled_run_never_blocks_same_key_immediate_work(
    tmp_path: Path,
    workflow_writer,
    policy: str,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    store = RunStore(tmp_path / policy, lease_clock=clocks.lease_sample)
    _leader(store, clocks, name=f"same-key-{policy}")
    package = _package(
        workflow_writer, tmp_path / f"package-{policy}", name=f"same-key-{policy}"
    )
    scheduled = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=(clocks.wall + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        concurrency_key="shared",
        concurrency_policy=policy,
    )
    immediate = _admit(
        store,
        package,
        key="immediate",
        schedule_at=None,
        concurrency_key="shared",
        concurrency_policy=policy,
    )

    assert store.load_run(scheduled.run_id)["status"] == "queued"
    current = store.load_run(immediate.run_id)
    assert current["status"] == "running"
    assert current["queue_position"] is None
    assert current["blocked_by_run_id"] is None


@pytest.mark.parametrize("policy", ["allow", "queue", "forbid"])
def test_due_scheduled_run_preserves_declared_overlap_policy_before_claim(
    tmp_path: Path,
    workflow_writer,
    policy: str,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 3, 12, 0, tzinfo=UTC))
    store = RunStore(tmp_path / policy, lease_clock=clocks.lease_sample)
    _leader(store, clocks, name=f"due-policy-{policy}")
    package = _package(
        workflow_writer, tmp_path / f"due-package-{policy}", name=f"due-policy-{policy}"
    )
    active = _admit(
        store,
        package,
        key="active",
        schedule_at=None,
        concurrency_key="shared",
        concurrency_policy="allow",
    )
    due = clocks.wall + timedelta(minutes=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        concurrency_key="shared",
        concurrency_policy=policy,
    )
    clocks.wall = due

    promoted = store.try_promote_run(scheduled.run_id, now=clocks.wall)
    projection = store.load_run(scheduled.run_id)
    with store._connect() as connection:
        claims = connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (scheduled.run_id,)
        ).fetchone()[0]

    if policy == "allow":
        assert promoted is True
        assert projection["status"] == "running"
    elif policy == "queue":
        assert promoted is False
        assert projection["status"] == "queued"
        assert projection["queue_position"] == projection["queue_sequence"]
    else:
        assert promoted is False
        assert projection["status"] == "failed"
        assert projection["last_error"]["code"] == "schedule_overlap_forbidden"
        assert any(
            event["event_type"] == "run_failed"
            for event in store.tail_events(scheduled.run_id)
        )
    assert claims == 0
    assert store.load_run(active.run_id)["status"] == "running"


def test_forbid_overlap_defers_outbox_until_after_promotion_transaction(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 3, 12, 0, tzinfo=UTC))
    store = RunStore(tmp_path / "home", lease_clock=clocks.lease_sample)
    _leader(store, clocks, name="forbid-overlap-leader")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="forbid-overlap",
    )
    active = _admit(
        store,
        package,
        key="active",
        schedule_at=None,
        concurrency_key="shared",
        concurrency_policy="allow",
    )
    due = clocks.wall + timedelta(minutes=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        concurrency_key="shared",
        concurrency_policy="forbid",
    )
    clocks.wall = due
    outbox = NotificationOutbox(store)

    class _FailingNotificationOutbox:
        def __init__(self, _store) -> None:
            pass

        def record(self, **kwargs) -> str:
            raise AssertionError("outbox record called during promotion transaction")

    with monkeypatch.context() as patched:
        patched.setattr(
            notifications_module,
            "NotificationOutbox",
            _FailingNotificationOutbox,
        )
        promoted = store.try_promote_run(scheduled.run_id, now=clocks.wall)

    projection = store.load_run(scheduled.run_id)
    events = store.tail_events(scheduled.run_id)
    with store._connect() as connection:
        claims = connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (scheduled.run_id,)
        ).fetchone()[0]

    assert promoted is False
    assert projection["status"] == "failed"
    assert projection["last_error"]["code"] == "schedule_overlap_forbidden"
    assert [event["event_type"] for event in events].count("run_failed") == 1
    assert claims == 0
    assert store.load_run(active.run_id)["status"] == "running"

    assert outbox.reconcile_journal() == 1
    facts = outbox.history(run_id=scheduled.run_id)
    assert len(facts) == 1
    assert facts[0]["kind"] == "failure"
    with store._connect() as connection:
        fact_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_facts WHERE run_id=?",
            (scheduled.run_id,),
        ).fetchone()[0]
        outbox_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_outbox WHERE run_id=?",
            (scheduled.run_id,),
        ).fetchone()[0]
    assert (fact_count, outbox_count) == (1, 1)
    assert outbox.reconcile_journal() == 0
    assert len(outbox.history(run_id=scheduled.run_id)) == 1


def test_backward_wall_jump_restores_scheduled_wait_without_losing_fairness(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 3, 13, 0, tzinfo=UTC))
    store = RunStore(tmp_path / "home", lease_clock=clocks.lease_sample)
    _leader(store, clocks, name="backward-wait-leader")
    package = _package(
        workflow_writer,
        tmp_path / "backward-package",
        name="backward-scheduled-wait",
    )
    active = _admit(
        store,
        package,
        key="active",
        schedule_at=None,
        concurrency_key="shared",
        concurrency_policy="allow",
    )
    due = clocks.wall + timedelta(minutes=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        concurrency_key="shared",
        concurrency_policy="queue",
    )
    original_sequence = store.load_run(scheduled.run_id)["queue_sequence"]

    clocks.wall = due
    assert store.try_promote_run(scheduled.run_id, now=clocks.wall) is False
    blocked = store.load_run(scheduled.run_id)
    assert blocked["queue_position"] == original_sequence
    assert blocked["blocked_by_run_id"] == active.run_id

    clocks.wall = due - timedelta(microseconds=1)
    assert store.try_promote_run(scheduled.run_id, now=clocks.wall) is False
    waiting = store.load_run(scheduled.run_id)
    assert waiting["queue_sequence"] == original_sequence
    assert waiting["queue_position"] is None
    assert waiting["blocked_by_run_id"] is None
    with store._connect() as connection:
        indexed = connection.execute(
            "SELECT queue_sequence, queue_position, blocked_by_run_id "
            "FROM runs WHERE run_id=?",
            (scheduled.run_id,),
        ).fetchone()
    assert tuple(indexed) == (original_sequence, None, None)
    public = public_run_projection(
        store.get_run_status(scheduled.run_id), now=clocks.wall
    )
    assert public["presentation_state"] == "scheduled_wait"
    assert public["blocking_reason"] == "scheduled_wait"


def test_scheduled_run_uses_existing_retry_clock_only_after_it_fires(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 4, 12, 0, tzinfo=UTC))
    package = _package(
        workflow_writer,
        tmp_path / "retry-package",
        name="scheduled-retry",
    )
    store = RunStore(tmp_path / "retry-home", lease_clock=clocks.lease_sample)
    _coordinator, identity, epoch = _leader(
        store, clocks, name="scheduled-retry-leader"
    )
    due = clocks.wall + timedelta(minutes=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled-retry",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
        concurrency_policy="allow",
    )
    calls = 0

    class _FailsOnce:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            if calls == 1:
                return NodeExecutionResult("failed", error_code="provider_timeout")
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(
        store,
        execution_fence=ExecutionFence(identity.owner_id, epoch),
        utcnow=clocks.utcnow,
        jitter=lambda: 0.5,
    )
    scheduler.executors["bash"] = _FailsOnce()
    try:
        assert scheduler.advance(scheduled.run_id)["status"] == "queued"
        assert calls == 0
        clocks.wall = due
        waiting = scheduler.advance(scheduled.run_id)
        retry_at = due + timedelta(seconds=1)
        assert waiting["status"] == "waiting_retry"
        assert waiting["nodes"]["start"]["next_attempt_at"] == retry_at.isoformat()
        assert calls == 1
        assert scheduler.advance(scheduled.run_id)["status"] == "waiting_retry"
        assert calls == 1
        clocks.wall = retry_at
        assert scheduler.advance(scheduled.run_id)["status"] == "succeeded"
        assert calls == 2
    finally:
        scheduler.shutdown(deadline_seconds=1)


def _leader(
    store: RunStore, clocks: _Clocks, *, name: str = "scheduled-leader"
) -> tuple[CoordinatorStore, CoordinatorIdentity, int]:
    coordinator = CoordinatorStore(store.database, clock=clocks.lease_sample)
    identity = _identity(name)
    acquired = coordinator.try_acquire(
        identity,
        now=clocks.wall,
        lease_seconds=300.0,
    )
    assert acquired.is_leader
    return coordinator, identity, acquired.lease.epoch


def _complete_pending_wakes(
    coordinator: CoordinatorStore,
    identity: CoordinatorIdentity,
    epoch: int,
    *,
    now: datetime,
) -> None:
    while True:
        wakes = coordinator.pending_wakes(identity, epoch=epoch, now=now, limit=100)
        if not wakes:
            return
        for wake in wakes:
            assert coordinator.complete_wake(
                wake.generation,
                identity,
                epoch=epoch,
                now=now,
                outcome="test_setup",
            )


def test_real_pending_wake_completes_not_due_then_indexed_sweep_submits_once(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    store = RunStore(tmp_path / "home", lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks)
    package = _package(workflow_writer, tmp_path / "package", name="scheduled-wake")
    due = clocks.wall + timedelta(days=365)
    admitted = _admit(
        store,
        package,
        key="scheduled-wake",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    stall_calls: list[str] = []
    original_stall = store.record_stall_if_due

    def record_stall(run_id, **kwargs):
        stall_calls.append(run_id)
        return original_stall(run_id, **kwargs)

    monkeypatch.setattr(store, "record_stall_if_due", record_stall)
    service = _service(tmp_path / "home", clocks)
    fence = ExecutionFence(identity.owner_id, epoch)

    actionable, cursor, _progress = service._sweep_once(
        store, coordinator, identity, epoch, scheduler
    )

    assert actionable is False
    assert cursor is None
    assert stall_calls == []
    scheduler.submit.assert_not_called()
    assert store.load_run(admitted.run_id)["status"] == "queued"
    with store._connect() as connection:
        wake = connection.execute(
            "SELECT completed_at, outcome FROM coordinator_wakes WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()
        claims = connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (admitted.run_id,)
        ).fetchone()[0]
    assert wake["completed_at"] is not None
    assert wake["outcome"] == "scheduled_not_due"
    assert claims == 0
    assert not [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "run_promoted"
    ]

    clocks.wall -= timedelta(days=1)
    service._sweep_once(store, coordinator, identity, epoch, scheduler)
    scheduler.submit.assert_not_called()

    clocks.wall = due
    actionable, cursor, _progress = service._sweep_once(
        store, coordinator, identity, epoch, scheduler
    )

    assert actionable is True
    assert cursor is None
    scheduler.submit.assert_called_once_with(admitted.run_id, fence)
    assert (
        coordinator.pending_wakes(identity, epoch=epoch, now=clocks.wall, limit=100)
        == ()
    )


def test_candidates_filter_future_rows_before_paging_and_compare_exact_instants(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 3, 8, 7, 0, 0, 123456, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=200,
        max_nonterminal_runs=250,
        max_start_requests_per_minute=250,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="candidate-leader")
    package = _package(
        workflow_writer, tmp_path / "package", name="scheduled-candidates"
    )
    far_future = "2027-03-08T07:00:00Z"
    future_ids = {
        _admit(
            store,
            package,
            key=f"far-future-{index:03d}",
            schedule_at=far_future,
        ).run_id
        for index in range(101)
    }
    immediate = _admit(
        store,
        package,
        key="immediate-behind-future-page",
        schedule_at=None,
    )
    before = _admit(
        store,
        package,
        key="same-second-before",
        schedule_at="2026-03-08T07:00:00.1234559Z",
    )
    after = _admit(
        store,
        package,
        key="same-second-after",
        schedule_at="2026-03-08T07:00:00.1234561Z",
    )
    equal = _admit(
        store,
        package,
        key="same-second-equal",
        schedule_at="2026-03-08T07:00:00.123456Z",
    )

    ordinary_rows, cursor, exhausted = store.coordinator_candidates(
        after=None, limit=2, now=clocks.wall
    )
    scheduled_rows, scheduled_cursor, scheduled_exhausted = (
        store.scheduled_coordinator_candidates(after=None, limit=2, now=clocks.wall)
    )

    assert [row["run_id"] for row in ordinary_rows] == [immediate.run_id]
    assert cursor == (ordinary_rows[0]["created_at"], immediate.run_id)
    assert exhausted is True
    assert [row["run_id"] for row in scheduled_rows] == [
        before.run_id,
        equal.run_id,
    ]
    assert scheduled_exhausted is True
    assert scheduled_cursor == (scheduled_rows[-1]["created_at"], equal.run_id)
    selected = {row["run_id"] for row in (*ordinary_rows, *scheduled_rows)}
    assert after.run_id not in selected
    assert selected.isdisjoint(future_ids)

    local_offset = clocks.wall.astimezone(timezone(timedelta(hours=-4)))
    offset_scheduled, _offset_cursor, offset_exhausted = (
        store.scheduled_coordinator_candidates(after=None, limit=100, now=local_offset)
    )
    assert [row["run_id"] for row in offset_scheduled] == [
        before.run_id,
        equal.run_id,
    ]
    assert offset_exhausted is True

    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=NULL WHERE run_id=?", (after.run_id,)
        )
    with pytest.raises(JournalRecoveryError, match="schedule index parity mismatch"):
        store.coordinator_candidates(after=None, limit=100, now=clocks.wall)


def test_newly_due_scheduled_work_is_not_hidden_behind_the_normal_cursor(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 1, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="forward-jump-leader")
    package = _package(
        workflow_writer, tmp_path / "package", name="forward-jump-candidates"
    )
    due = clocks.wall + timedelta(hours=1)
    scheduled = _admit(
        store,
        package,
        key="scheduled-before-normal-pages",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
    )
    ordinary = [
        _admit(store, package, key=f"ordinary-{index}", schedule_at=None)
        for index in range(3)
    ]

    first, cursor, exhausted = store.coordinator_candidates(
        after=None, limit=2, now=clocks.wall
    )

    assert [row["run_id"] for row in first] == [
        ordinary[0].run_id,
        ordinary[1].run_id,
    ]
    assert cursor is not None
    assert exhausted is False

    clocks.wall = due
    next_sweep, _scheduled_cursor, _next_exhausted = (
        store.scheduled_coordinator_candidates(
            after=None,
            limit=2,
            now=clocks.wall,
        )
    )

    assert scheduled.run_id in {row["run_id"] for row in next_sweep}


def test_active_run_repairs_do_not_consume_coordinator_candidate_pages(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 2, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-candidate-isolation")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-candidate-isolation",
    )
    due = (clocks.wall - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    repaired_ordinary = _admit(
        store,
        package,
        key="repaired-ordinary",
        schedule_at=None,
    )
    healthy_ordinary = _admit(
        store,
        package,
        key="healthy-ordinary",
        schedule_at=None,
    )
    repaired_scheduled = _admit(
        store,
        package,
        key="repaired-scheduled",
        schedule_at=due,
    )
    healthy_scheduled = _admit(
        store,
        package,
        key="healthy-scheduled",
        schedule_at=due,
    )
    for run_id in (repaired_ordinary.run_id, repaired_scheduled.run_id):
        assert store._transition_run_repair(
            "run_evidence_uncorroborated",
            run_id=run_id,
            outcome="repair_required",
        )

    ordinary, ordinary_cursor, ordinary_exhausted = store.coordinator_candidates(
        after=None,
        now=clocks.wall,
        limit=1,
    )
    scheduled, scheduled_cursor, scheduled_exhausted = (
        store.scheduled_coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=1,
        )
    )

    assert [row["run_id"] for row in ordinary] == [healthy_ordinary.run_id]
    assert ordinary_cursor == (ordinary[0]["created_at"], healthy_ordinary.run_id)
    assert ordinary_exhausted is True
    assert [row["run_id"] for row in scheduled] == [healthy_scheduled.run_id]
    assert scheduled_cursor == (
        scheduled[0]["created_at"],
        healthy_scheduled.run_id,
    )
    assert scheduled_exhausted is True

    for run_id in (repaired_ordinary.run_id, repaired_scheduled.run_id):
        assert store._transition_run_repair(
            "run_evidence_uncorroborated",
            run_id=run_id,
            outcome="repair_verified",
        )

    ordinary, _ordinary_cursor, _ordinary_exhausted = store.coordinator_candidates(
        after=None,
        now=clocks.wall,
        limit=10,
    )
    scheduled, _scheduled_cursor, _scheduled_exhausted = (
        store.scheduled_coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=10,
        )
    )
    assert {row["run_id"] for row in ordinary} == {
        repaired_ordinary.run_id,
        healthy_ordinary.run_id,
    }
    assert {row["run_id"] for row in scheduled} == {
        repaired_scheduled.run_id,
        healthy_scheduled.run_id,
    }


def test_coordinator_repair_filters_use_bounded_composite_index_lookups(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 2, 13, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-filter-index")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-filter-index",
    )
    due = (clocks.wall - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    ordinary_run_id = _admit(
        store,
        package,
        key="ordinary",
        schedule_at=None,
    ).run_id
    scheduled_run_id = _admit(
        store,
        package,
        key="scheduled",
        schedule_at=due,
    ).run_id

    with store._connect() as connection:
        ordinary_plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT runs.run_id FROM runs WHERE "
            "runs.admission_state='published' AND runs.status='queued' "
            "AND runs.scheduled_at IS NULL AND "
            + store_module._RUN_SCOPED_REPAIR_EXCLUSION_SQL
        ).fetchall()
        scheduled_plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT runs.run_id FROM runs "
            "INDEXED BY runs_scheduled_queue WHERE "
            "runs.admission_state='published' AND runs.status='queued' "
            "AND runs.scheduled_at IS NOT NULL AND runs.scheduled_at<? AND "
            + store_module._RUN_SCOPED_REPAIR_EXCLUSION_SQL,
            (due,),
        ).fetchall()
    for plan in (ordinary_plan, scheduled_plan):
        details = tuple(str(row["detail"]) for row in plan)
        assert not any(detail == "SCAN repair" for detail in details)
        assert sum(
            "repair_events_run_reason_sequence" in detail for detail in details
        ) >= 2

    original_connect = store._connect

    def count_opcodes(call):
        opcode_count = 0

        def counted_connect():
            connection = original_connect()

            def count_opcode() -> int:
                nonlocal opcode_count
                opcode_count += 1
                return 0

            connection.set_progress_handler(count_opcode, 1)
            return connection

        monkeypatch.setattr(store, "_connect", counted_connect)
        try:
            result = call()
        finally:
            monkeypatch.setattr(store, "_connect", original_connect)
        return result, opcode_count

    (_ordinary_before, _cursor, _exhausted), ordinary_before = count_opcodes(
        lambda: store.coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=100,
        )
    )
    (_scheduled_before, _cursor, _exhausted), scheduled_before = count_opcodes(
        lambda: store.scheduled_coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=100,
        )
    )
    with store._connect() as connection:
        connection.execute(
            "WITH RECURSIVE history(value) AS ("
            "SELECT 1 UNION ALL SELECT value+1 FROM history WHERE value<10000"
            ") INSERT INTO repair_events ("
            "detected_at, reason_code, outcome, run_id, payload_json"
            ") SELECT ?, 'unrelated_history', 'evidence_preserved', "
            "'unrelated-' || value, '{}' FROM history",
            (clocks.wall.isoformat(),),
        )

    (ordinary_after_rows, _cursor, _exhausted), ordinary_after = count_opcodes(
        lambda: store.coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=100,
        )
    )
    (scheduled_after_rows, _cursor, _exhausted), scheduled_after = count_opcodes(
        lambda: store.scheduled_coordinator_candidates(
            after=None,
            now=clocks.wall,
            limit=100,
        )
    )

    assert [row["run_id"] for row in ordinary_after_rows] == [ordinary_run_id]
    assert [row["run_id"] for row in scheduled_after_rows] == [scheduled_run_id]
    assert ordinary_after <= ordinary_before + 500
    assert scheduled_after <= scheduled_before + 500


def test_unrecoverable_migrated_run_does_not_block_healthy_scheduled_sweep(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 3, 12, 0, tzinfo=UTC))
    home = tmp_path / "home"
    store = RunStore(
        home,
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="migration-repair-isolation",
    )
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="migration-repair-isolation",
    )
    due = (clocks.wall - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    damaged = _admit(
        store,
        package,
        key="damaged",
        schedule_at=due,
    )
    healthy = _admit(
        store,
        package,
        key="healthy",
        schedule_at=due,
    )
    store.append_event(
        damaged.run_id,
        "semantic_progress",
        {"step": "before-corruption"},
    )
    for wake in coordinator.pending_wakes(
        identity,
        epoch=epoch,
        now=clocks.wall,
    ):
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=epoch,
            now=clocks.wall,
            outcome="test_setup",
        )
    damaged_directory = store.run_directory(damaged.run_id)
    damaged_journal = damaged_directory / "events.jsonl"
    frames = damaged_journal.read_bytes().splitlines(keepends=True)
    assert len(frames) == 2
    original_journal = b"".join(frames)
    corrupted_journal = frames[0] + b"{not-json}\n" + frames[1]
    damaged_journal.write_bytes(corrupted_journal)

    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP INDEX runs_scheduled_queue")
        connection.execute("ALTER TABLE runs DROP COLUMN scheduled_at")
        connection.execute("PRAGMA user_version=13")

    restarted = RunStore(
        home,
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    with restarted._connect() as connection:
        damaged_row = connection.execute(
            "SELECT admission_state, status, scheduled_at FROM runs WHERE run_id=?",
            (damaged.run_id,),
        ).fetchone()
    assert tuple(damaged_row) == ("published", "queued", None)
    assert restarted._active_run_repair_reasons(damaged.run_id) == (
        "run_evidence_uncorroborated",
    )
    assert restarted._active_run_repair_reasons(healthy.run_id) == ()

    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = _service(home, clocks)
    service.notification_repair_seconds = 0.01

    actionable, _cursor, _progress = service._sweep_once(
        restarted,
        coordinator,
        identity,
        epoch,
        scheduler,
    )

    assert actionable is True
    assert [call.args[0] for call in scheduler.submit.call_args_list] == [
        healthy.run_id
    ]
    assert damaged_journal.read_bytes() == corrupted_journal
    assert "run_evidence_uncorroborated" in restarted._active_run_repair_reasons(
        damaged.run_id
    )
    assert not tuple(restarted.quarantine_root.glob("admission-index-*"))

    damaged_journal.write_bytes(original_journal)
    scheduler.reset_mock()
    for _attempt in range(4):
        clocks.monotonic_value += 1
        service._sweep_once(
            restarted,
            coordinator,
            identity,
            epoch,
            scheduler,
        )
        if damaged.run_id in {
            call.args[0] for call in scheduler.submit.call_args_list
        }:
            break

    assert damaged.run_id in {
        call.args[0] for call in scheduler.submit.call_args_list
    }
    assert restarted._active_run_repair_reasons(damaged.run_id) == ()
    with restarted._connect() as connection:
        repaired_schedule = connection.execute(
            "SELECT scheduled_at FROM runs WHERE run_id=?",
            (damaged.run_id,),
        ).fetchone()["scheduled_at"]
    assert repaired_schedule == due


def test_restored_legacy_effect_policy_is_revalidated_before_submission(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="legacy-policy-revalidation",
    )
    workflow_path = workflow_writer(
        tmp_path / "package",
        name="legacy-policy-revalidation",
    )
    workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml").write_text(
        "outward_action_nodes:\n- start\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
    admitted = _admit(
        store,
        package,
        key="legacy-policy",
        schedule_at=None,
    )
    legacy_projection = _rewrite_as_legacy_policy_projection(
        store,
        admitted.run_id,
    )
    for wake in coordinator.pending_wakes(
        identity,
        epoch=epoch,
        now=clocks.wall,
    ):
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=epoch,
            now=clocks.wall,
            outcome="test_setup",
        )
    policy = store.run_directory(admitted.run_id) / "policy.yaml"
    original_policy = policy.read_bytes()
    policy.write_text("outward_action_nodes: []\n", encoding="utf-8")
    with pytest.raises(JournalRecoveryError, match="policy digest mismatch"):
        store.node_effect_classification(
            admitted.run_id,
            "start",
            projection=legacy_projection,
        )
    policy.write_bytes(original_policy)

    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = _service(tmp_path / "home", clocks)

    service._sweep_once(
        store,
        coordinator,
        identity,
        epoch,
        scheduler,
    )
    service._sweep_once(
        store,
        coordinator,
        identity,
        epoch,
        scheduler,
    )

    assert [call.args[0] for call in scheduler.submit.call_args_list] == [
        admitted.run_id
    ]
    assert store._active_run_repair_reasons(admitted.run_id) == ()


def test_repair_revalidation_cursor_bypasses_locked_and_corrupt_rows(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 5, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=10,
        max_nonterminal_runs=10,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="repair-revalidation-fairness",
    )
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-revalidation-fairness",
    )
    locked = _admit(store, package, key="locked", schedule_at=None)
    corrupt = [
        _admit(store, package, key=f"corrupt-{index}", schedule_at=None)
        for index in range(5)
    ]
    recoverable = _admit(store, package, key="recoverable", schedule_at=None)
    healthy = _admit(store, package, key="healthy", schedule_at=None)
    for wake in coordinator.pending_wakes(
        identity,
        epoch=epoch,
        now=clocks.wall,
    ):
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=epoch,
            now=clocks.wall,
            outcome="test_setup",
        )
    for admitted in corrupt:
        journal = store.run_directory(admitted.run_id) / "events.jsonl"
        with journal.open("ab") as stream:
            stream.write(b"{not-json}\n")
    for admitted in (locked, *corrupt, recoverable):
        assert store._transition_run_repair(
            "run_evidence_uncorroborated",
            run_id=admitted.run_id,
            outcome="repair_required",
        )

    ready = threading.Event()
    release = threading.Event()

    def hold_oldest_lock() -> None:
        with workflow_lock(store._run_lock_path(locked.run_id)):
            ready.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_oldest_lock)
    holder.start()
    assert ready.wait(timeout=1)
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = _service(tmp_path / "home", clocks)
    try:
        started = time.monotonic()
        service._sweep_once(
            store,
            coordinator,
            identity,
            epoch,
            scheduler,
        )
        first_elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()
    assert first_elapsed < 0.5
    assert healthy.run_id in {
        call.args[0] for call in scheduler.submit.call_args_list
    }

    for _attempt in range(len(corrupt) + 2):
        service._sweep_once(
            store,
            coordinator,
            identity,
            epoch,
            scheduler,
        )
        if recoverable.run_id in {
            call.args[0] for call in scheduler.submit.call_args_list
        }:
            break

    assert recoverable.run_id in {
        call.args[0] for call in scheduler.submit.call_args_list
    }
    assert store._active_run_repair_reasons(recoverable.run_id) == ()
    assert all(
        "run_evidence_uncorroborated"
        in store._active_run_repair_reasons(admitted.run_id)
        for admitted in corrupt
    )


def test_disappearing_repair_candidate_does_not_abort_healthy_sweep(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 10, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="disappearing-repair",
    )
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="disappearing-repair",
    )
    disappearing = _admit(store, package, key="disappearing", schedule_at=None)
    healthy = _admit(store, package, key="healthy", schedule_at=None)
    for wake in coordinator.pending_wakes(
        identity,
        epoch=epoch,
        now=clocks.wall,
    ):
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=epoch,
            now=clocks.wall,
            outcome="test_setup",
        )
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=disappearing.run_id,
        outcome="repair_required",
    )
    original_run_directory = store.run_directory

    def run_directory_after_cleanup(
        run_id: str,
        *,
        operator_scope: str | None = None,
    ) -> Path:
        if run_id == disappearing.run_id:
            raise KeyError(run_id)
        return original_run_directory(run_id, operator_scope=operator_scope)

    monkeypatch.setattr(store, "run_directory", run_directory_after_cleanup)
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = _service(tmp_path / "home", clocks)
    service._notification_repair_due_at = clocks.monotonic_value + 100

    actionable, _cursor, _progress = service._sweep_once(
        store,
        coordinator,
        identity,
        epoch,
        scheduler,
    )

    assert actionable is True
    assert [call.args[0] for call in scheduler.submit.call_args_list] == [
        healthy.run_id
    ]
    assert service._repair_revalidation_cursor is not None


def test_revalidation_lane_leaves_notification_repairs_to_outbox_cadence(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="notification-repair-ownership")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="notification-repair-ownership",
    )
    notification = _admit(store, package, key="notification", schedule_at=None)
    evidence = _admit(store, package, key="evidence", schedule_at=None)
    assert store._transition_run_repair(
        "notification_reconciliation_unverified",
        run_id=notification.run_id,
        outcome="repair_required",
    )
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=evidence.run_id,
        outcome="repair_required",
    )

    candidate, cursor, exhausted = store.repair_revalidation_candidate(
        after=None,
    )

    assert candidate == {
        "sequence": cursor,
        "run_id": evidence.run_id,
        "reason_code": "run_evidence_uncorroborated",
    }
    assert exhausted is False
    assert store._active_run_repair_reasons(notification.run_id) == (
        "notification_reconciliation_unverified",
    )


def test_revalidation_selector_skips_large_unrelated_history_in_one_call(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 15, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-selector-scaling")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-selector-scaling",
    )
    admitted = _admit(store, package, key="eligible", schedule_at=None)
    with store._connect() as connection:
        connection.execute(
            "WITH RECURSIVE history(value) AS ("
            "SELECT 1 UNION ALL SELECT value+1 FROM history WHERE value<10000"
            ") INSERT INTO repair_events ("
            "detected_at, reason_code, outcome, run_id, payload_json"
            ") SELECT ?, 'unrelated_history', 'evidence_preserved', "
            "'unrelated-' || value, '{}' FROM history",
            (clocks.wall.isoformat(),),
        )
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    opcode_count = 0
    original_connect = store._connect

    def counted_connect():
        connection = original_connect()

        def count_opcode() -> int:
            nonlocal opcode_count
            opcode_count += 1
            return 0

        connection.set_progress_handler(count_opcode, 1)
        return connection

    monkeypatch.setattr(store, "_connect", counted_connect)

    candidate, cursor, exhausted = store.repair_revalidation_candidate(
        after=None,
    )

    assert candidate == {
        "sequence": cursor,
        "run_id": admitted.run_id,
        "reason_code": "run_evidence_uncorroborated",
    }
    assert exhausted is False
    assert opcode_count < 500


def test_failed_revalidation_wraps_while_unrelated_history_keeps_growing(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 30, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-selector-wrap")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-selector-wrap",
    )
    admitted = _admit(store, package, key="retry", schedule_at=None)
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    candidate, cursor, exhausted = store.repair_revalidation_candidate(
        after=None,
    )
    assert candidate is not None
    assert exhausted is False

    retries = 1
    for batch in range(2):
        with store._connect() as connection:
            connection.execute(
                "WITH RECURSIVE history(value) AS ("
                "SELECT 1 UNION ALL SELECT value+1 FROM history WHERE value<100"
                ") INSERT INTO repair_events ("
                "detected_at, reason_code, outcome, run_id, payload_json"
                ") SELECT ?, 'unrelated_history', 'evidence_preserved', "
                "? || value, '{}' FROM history",
                (clocks.wall.isoformat(), f"growing-{batch}-"),
            )
        candidate, cursor, exhausted = store.repair_revalidation_candidate(
            after=cursor,
        )
        if exhausted:
            cursor = None
        if candidate is not None:
            retries += 1

    assert retries == 2
    assert candidate is not None
    assert candidate["run_id"] == admitted.run_id


def test_revalidation_allows_exact_aggregate_evidence_quota(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, run_id, evidence_bytes, corroborate = _legacy_repair_quota_case(
        tmp_path,
        workflow_writer,
        monkeypatch,
    )
    store.max_run_bytes = evidence_bytes

    assert store.revalidate_run_repair(
        run_id,
        "legacy_effect_policy_uncorroborated",
    )
    corroborate.assert_called_once()


def test_revalidation_rejects_aggregate_evidence_over_run_quota(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, run_id, evidence_bytes, corroborate = _legacy_repair_quota_case(
        tmp_path,
        workflow_writer,
        monkeypatch,
    )
    store.max_run_bytes = evidence_bytes - 1

    assert not store.revalidate_run_repair(
        run_id,
        "legacy_effect_policy_uncorroborated",
    )
    corroborate.assert_not_called()


def test_revalidation_rejects_valid_evidence_growth_before_snapshot_read(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 45, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-snapshot-growth")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-snapshot-growth",
    )
    admitted = _admit(store, package, key="growth", schedule_at=None)
    store.append_event(
        admitted.run_id,
        "semantic_progress",
        {"step": "snapshot-growth"},
    )
    directory = store.run_directory(admitted.run_id)
    projection_path = directory / "run.json"
    journal_path = directory / "events.jsonl"
    frames = journal_path.read_bytes().splitlines(keepends=True)
    event = json.loads(frames[-1])
    event["payload"] = {"padding": "x" * (32 * 1024)}
    event.pop("frame_sha256")
    _framed, encoded = store_module._encode_journal_frame(event)
    grown_journal = b"".join((*frames[:-1], encoded))
    store.max_run_bytes = (
        projection_path.stat().st_size + journal_path.stat().st_size
    )
    assert len(grown_journal) + projection_path.stat().st_size > store.max_run_bytes
    assert len(grown_journal) <= store.max_journal_bytes
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    original_open = Path.open
    mutated = False

    def open_after_quota(path: Path, *args, **kwargs):
        nonlocal mutated
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if path == projection_path and "r" in mode and "b" in mode and not mutated:
            mutated = True
            with original_open(journal_path, "wb") as stream:
                stream.write(grown_journal)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_quota)

    assert not store.revalidate_run_repair(
        admitted.run_id,
        "run_evidence_uncorroborated",
    )
    assert mutated
    assert store._active_run_repair_reasons(admitted.run_id) == (
        "run_evidence_uncorroborated",
    )


def test_legacy_policy_validation_uses_quota_accounted_snapshot(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 50, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="legacy-policy-snapshot")
    workflow_path = workflow_writer(
        tmp_path / "package",
        name="legacy-policy-snapshot",
    )
    workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml").write_text(
        "outward_action_nodes:\n- start\n",
        encoding="utf-8",
    )
    admitted = _admit(
        store,
        load_workflow(workflow_path),
        key="legacy-policy-snapshot",
        schedule_at=None,
    )
    _rewrite_as_legacy_policy_projection(store, admitted.run_id)
    assert store._transition_run_repair(
        "legacy_effect_policy_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    policy_path = store.run_directory(admitted.run_id) / "policy.yaml"
    original_sync = store._sync_integrity_index
    mutated = False

    def sync_then_replace_policy(
        connection,
        *,
        projection,
        journal_sha256,
    ) -> None:
        nonlocal mutated
        original_sync(
            connection,
            projection=projection,
            journal_sha256=journal_sha256,
        )
        policy_path.write_text("outward_action_nodes: []\n", encoding="utf-8")
        mutated = True

    monkeypatch.setattr(store, "_sync_integrity_index", sync_then_replace_policy)

    assert store.revalidate_run_repair(
        admitted.run_id,
        "legacy_effect_policy_uncorroborated",
    )
    assert mutated
    assert store._active_run_repair_reasons(admitted.run_id) == ()


def test_snapshot_revalidation_preserves_torn_tail_recovery(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 11, 55, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="repair-snapshot-torn-tail")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="repair-snapshot-torn-tail",
    )
    admitted = _admit(store, package, key="torn-tail", schedule_at=None)
    store.append_event(
        admitted.run_id,
        "semantic_progress",
        {"step": "complete"},
    )
    directory = store.run_directory(admitted.run_id)
    journal_path = directory / "events.jsonl"
    torn = b'{"frame_version":1,"schema_version":2,"sequence":999'
    with journal_path.open("ab") as stream:
        stream.write(torn)
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )

    assert store.revalidate_run_repair(
        admitted.run_id,
        "run_evidence_uncorroborated",
    )
    assert journal_path.read_bytes().endswith(b"\n")
    preserved = tuple(directory.glob("events.jsonl.torn-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == torn
    assert store._active_run_repair_reasons(admitted.run_id) == ()


def test_repair_revalidation_accepts_valid_journal_above_fixed_probe_sizes(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 6, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_run_bytes=16 * 1024 * 1024,
        max_journal_bytes=8 * 1024 * 1024,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="large-repair-revalidation",
    )
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="large-repair-revalidation",
    )
    admitted = _admit(store, package, key="large", schedule_at=None)
    journal = store.run_directory(admitted.run_id) / "events.jsonl"
    store.append_event(
        admitted.run_id,
        "semantic_progress",
        {"step": "large-frame"},
    )
    frames = journal.read_bytes().splitlines(keepends=True)
    event = json.loads(frames[-1])
    event["payload"] = {"padding": "x" * (4 * 1024 * 1024)}
    event.pop("frame_sha256")
    _framed, encoded = store_module._encode_journal_frame(event)
    journal.write_bytes(b"".join((*frames[:-1], encoded)))
    assert journal.stat().st_size > 4 * 1024 * 1024
    store.load_run(admitted.run_id)
    for wake in coordinator.pending_wakes(
        identity,
        epoch=epoch,
        now=clocks.wall,
    ):
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=epoch,
            now=clocks.wall,
            outcome="test_setup",
        )
    assert store._transition_run_repair(
        "run_evidence_uncorroborated",
        run_id=admitted.run_id,
        outcome="repair_required",
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = _service(tmp_path / "home", clocks)

    service._sweep_once(
        store,
        coordinator,
        identity,
        epoch,
        scheduler,
    )
    service._sweep_once(
        store,
        coordinator,
        identity,
        epoch,
        scheduler,
    )

    assert admitted.run_id in {
        call.args[0] for call in scheduler.submit.call_args_list
    }
    assert store._active_run_repair_reasons(admitted.run_id) == ()


def test_real_sweep_rescues_newly_due_work_behind_both_active_cursors(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 1, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=250,
        max_nonterminal_runs=250,
        max_start_requests_per_minute=250,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store, clocks, name="active-cursor-forward-jump"
    )
    package = _package(
        workflow_writer, tmp_path / "package", name="active-cursor-forward-jump"
    )
    due = clocks.wall + timedelta(hours=1)
    newly_due = _admit(
        store,
        package,
        key="newly-due-before-backlogs",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
    )
    for index in range(51):
        _admit(
            store,
            package,
            key=f"already-due-{index:03d}",
            schedule_at="2026-04-01T11:00:00Z",
        )
    for index in range(51):
        _admit(store, package, key=f"ordinary-{index:03d}", schedule_at=None)
    while True:
        wakes = coordinator.pending_wakes(
            identity, epoch=epoch, now=clocks.wall, limit=100
        )
        if not wakes:
            break
        for wake in wakes:
            assert coordinator.complete_wake(
                wake.generation,
                identity,
                epoch=epoch,
                now=clocks.wall,
                outcome="test_setup",
            )

    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    _actionable, normal_cursor, _progress = service._sweep_once(
        store, coordinator, identity, epoch, scheduler
    )

    assert normal_cursor is not None
    assert not any(
        call.args[0] == newly_due.run_id for call in scheduler.submit.call_args_list
    )

    scheduler.reset_mock()
    clocks.wall = due
    service._sweep_once(store, coordinator, identity, epoch, scheduler, normal_cursor)

    assert any(
        call.args[0] == newly_due.run_id for call in scheduler.submit.call_args_list
    )


def test_consecutive_forward_jump_discovers_new_due_work_during_backlogs(
    tmp_path: Path,
    workflow_writer,
) -> None:
    t0 = datetime(2026, 4, 3, 12, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    t2 = t1 + timedelta(hours=1)
    clocks = _Clocks(t0)
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=200,
        max_nonterminal_runs=200,
        max_start_requests_per_minute=200,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store, clocks, name="consecutive-forward-jump"
    )
    package = _package(
        workflow_writer, tmp_path / "package", name="consecutive-forward-jump"
    )
    newly_due_at_t2 = _admit(
        store,
        package,
        key="newly-due-at-t2",
        schedule_at=t2.isoformat().replace("+00:00", "Z"),
    )
    clocks.wall = t0 + timedelta(microseconds=1)
    for index in range(51):
        _admit(
            store,
            package,
            key=f"newly-due-at-t1-{index:03d}",
            schedule_at=t1.isoformat().replace("+00:00", "Z"),
        )
    clocks.wall = t0 + timedelta(microseconds=2)
    for index in range(101):
        _admit(
            store,
            package,
            key=f"base-due-{index:03d}",
            schedule_at=(t0 - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
    clocks.wall = t0
    _complete_pending_wakes(coordinator, identity, epoch, now=clocks.wall)

    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    _actionable, ordinary_cursor, _progress = service._sweep_once(
        store, coordinator, identity, epoch, scheduler
    )

    clocks.wall = t1
    _actionable, ordinary_cursor, _progress = service._sweep_once(
        store, coordinator, identity, epoch, scheduler, ordinary_cursor
    )
    assert not any(
        call.args[0] == newly_due_at_t2.run_id
        for call in scheduler.submit.call_args_list
    )

    scheduler.reset_mock()
    clocks.wall = t2
    service._sweep_once(store, coordinator, identity, epoch, scheduler, ordinary_cursor)

    assert any(
        call.args[0] == newly_due_at_t2.run_id
        for call in scheduler.submit.call_args_list
    )


def test_due_and_ordinary_candidates_share_one_global_admission_order(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=100,
        max_nonterminal_runs=100,
        max_start_requests_per_minute=100,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(store, clocks, name="global-admission-order")
    package = _package(
        workflow_writer, tmp_path / "package", name="global-admission-order"
    )
    scheduled = [
        _admit(
            store,
            package,
            key=f"older-scheduled-{index:03d}",
            schedule_at=(clocks.wall - timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        for index in range(51)
    ]
    clocks.wall += timedelta(microseconds=1)
    ordinary = _admit(store, package, key="newer-ordinary", schedule_at=None)
    _complete_pending_wakes(coordinator, identity, epoch, now=clocks.wall)
    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.submit.return_value = True

    service._sweep_once(store, coordinator, identity, epoch, scheduler)

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert submitted == [run.run_id for run in scheduled] + [ordinary.run_id]


def test_full_due_backlog_cannot_starve_periodic_running_page(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 12, 0, tzinfo=UTC))
    due = clocks.wall + timedelta(hours=1)
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=4,
        max_queued_runs=100,
        max_nonterminal_runs=104,
        max_start_requests_per_minute=104,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store,
        clocks,
        name="full-due-backlog",
    )
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="full-due-backlog",
    )
    scheduled = [
        _admit(
            store,
            package,
            key=f"older-scheduled-{index:03d}",
            schedule_at=due.isoformat().replace("+00:00", "Z"),
        )
        for index in range(100)
    ]
    periodic = [
        _admit(
            store,
            package,
            key=f"later-running-{index:03d}",
            schedule_at=None,
            concurrency_policy="allow",
        )
        for index in range(4)
    ]
    assert all(
        store.load_run(run.run_id)["status"] == "running" for run in periodic
    )
    assert max(
        store.load_run(run.run_id)["created_at"] for run in scheduled
    ) < min(store.load_run(run.run_id)["created_at"] for run in periodic)
    clocks.wall = due
    _complete_pending_wakes(coordinator, identity, epoch, now=clocks.wall)
    service = _service(tmp_path / "home", clocks)
    submissions_by_sweep: list[list[str]] = []

    class DeadlineScheduler:
        def begin_sweep(self) -> None:
            submissions_by_sweep.append([])

        def submit(self, run_id: str, fence: ExecutionFence) -> bool:
            assert fence == ExecutionFence(identity.owner_id, epoch)
            submissions_by_sweep[-1].append(run_id)
            clocks.monotonic_value += 2.1
            return True

    scheduler = DeadlineScheduler()
    cursor = None
    observed_cursors = []
    for _ in range(4):
        scheduler.begin_sweep()
        _actionable, cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            epoch,
            scheduler,
            cursor,
        )
        observed_cursors.append(cursor)

    periodic_ids = [run.run_id for run in periodic]
    submitted_periodic = [
        run_id
        for sweep in submissions_by_sweep
        for run_id in sweep
        if run_id in periodic_ids
    ]
    expected_cursors = [
        (
            store.load_run(run.run_id)["created_at"],
            run.run_id,
        )
        for run in periodic[:-1]
    ] + [None]
    assert (submitted_periodic, observed_cursors) == (
        periodic_ids,
        expected_cursors,
    )
    assert submissions_by_sweep[0][0] in periodic_ids
    assert [sweep[0] for sweep in submissions_by_sweep] == periodic_ids
    assert all(len(sweep) == 1 for sweep in submissions_by_sweep)
    assert all(len(sweep) == len(set(sweep)) for sweep in submissions_by_sweep)
    assert observed_cursors == expected_cursors


def test_newer_pending_wake_does_not_bypass_older_due_admissions(
    tmp_path: Path,
    workflow_writer,
) -> None:
    admitted_at = datetime(2026, 4, 4, 13, 0, tzinfo=UTC)
    due_at = admitted_at + timedelta(hours=1)
    clocks = _Clocks(admitted_at)
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=100,
        max_nonterminal_runs=100,
        max_start_requests_per_minute=100,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store, clocks, name="wake-global-admission-order"
    )
    package = _package(
        workflow_writer, tmp_path / "package", name="wake-global-admission-order"
    )
    scheduled = [
        _admit(
            store,
            package,
            key=f"older-future-{index:03d}",
            schedule_at=due_at.isoformat().replace("+00:00", "Z"),
        )
        for index in range(51)
    ]
    _complete_pending_wakes(coordinator, identity, epoch, now=clocks.wall)
    clocks.wall += timedelta(microseconds=1)
    ordinary = _admit(store, package, key="newer-wake", schedule_at=None)
    clocks.wall = due_at
    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.submit.return_value = True

    service._sweep_once(store, coordinator, identity, epoch, scheduler)

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert set(submitted[:51]) == {run.run_id for run in scheduled}
    assert submitted[51] == ordinary.run_id


def test_future_wake_is_completed_outside_a_full_execution_order_page(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 15, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=150,
        max_nonterminal_runs=150,
        max_start_requests_per_minute=150,
        lease_clock=clocks.lease_sample,
    )
    coordinator, identity, epoch = _leader(
        store, clocks, name="administrative-future-wake"
    )
    package = _package(
        workflow_writer, tmp_path / "package", name="administrative-future-wake"
    )
    for index in range(100):
        _admit(
            store,
            package,
            key=f"older-due-{index:03d}",
            schedule_at=(clocks.wall - timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z"),
        )
    _complete_pending_wakes(coordinator, identity, epoch, now=clocks.wall)
    clocks.wall += timedelta(microseconds=1)
    future = _admit(
        store,
        package,
        key="future-outside-execution-page",
        schedule_at=(clocks.wall + timedelta(days=365))
        .isoformat()
        .replace("+00:00", "Z"),
    )
    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.submit.return_value = True

    service._sweep_once(store, coordinator, identity, epoch, scheduler)

    assert not any(
        call.args[0] == future.run_id for call in scheduler.submit.call_args_list
    )
    with store._connect() as connection:
        wake = connection.execute(
            "SELECT completed_at, outcome FROM coordinator_wakes WHERE run_id=?",
            (future.run_id,),
        ).fetchone()
    assert wake["completed_at"] is not None
    assert wake["outcome"] == "scheduled_not_due"


def test_exact_future_schedule_filtering_is_bounded_and_eventually_eligible(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 2, 12, 0, 0, 123456, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=200,
        max_nonterminal_runs=250,
        max_start_requests_per_minute=250,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="bounded-exact-filter-leader")
    package = _package(
        workflow_writer, tmp_path / "package", name="bounded-exact-filter"
    )
    future_ids = {
        _admit(
            store,
            package,
            key=f"submicrosecond-future-{index:03d}",
            schedule_at="2026-04-02T12:00:00.1234561Z",
        ).run_id
        for index in range(101)
    }
    earlier_due = [
        _admit(
            store,
            package,
            key=f"earlier-due-{index}",
            schedule_at=f"2026-04-02T11:59:5{index}Z",
        )
        for index in range(2)
    ]
    equal = _admit(
        store,
        package,
        key="microsecond-equal",
        schedule_at="2026-04-02T12:00:00.123456Z",
    )
    loaded: list[str] = []
    original_load_run = store.load_run
    original_connect = store._connect
    statements: list[str] = []

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    def counted_load_run(run_id: str, *args, **kwargs):
        loaded.append(run_id)
        return original_load_run(run_id, *args, **kwargs)

    monkeypatch.setattr(store, "_connect", traced_connect)
    monkeypatch.setattr(store, "load_run", counted_load_run)

    first, scheduled_cursor, exhausted = store.scheduled_coordinator_candidates(
        after=None,
        limit=2,
        now=clocks.wall,
    )

    assert [row["run_id"] for row in first] == [run.run_id for run in earlier_due]
    assert exhausted is False
    assert len(loaded) <= 3
    scheduled_select = next(
        statement
        for statement in statements
        if "scheduled_at IS NOT NULL" in statement
        and "ORDER BY created_at, run_id" in statement
    )
    with original_connect() as connection:
        plan = tuple(
            str(row["detail"])
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {scheduled_select}"
            ).fetchall()
        )
    assert any("runs_scheduled_queue" in detail for detail in plan)

    before_loads = len(loaded)
    remainder, scheduled_cursor, exhausted = store.scheduled_coordinator_candidates(
        after=scheduled_cursor,
        limit=2,
        now=clocks.wall,
    )
    assert [row["run_id"] for row in remainder] == [equal.run_id]
    assert exhausted is True
    assert len(loaded) - before_loads <= 3

    clocks.wall = datetime(2026, 4, 2, 12, 0, 0, 123457, tzinfo=UTC)
    scheduled_cursor = None if exhausted else scheduled_cursor
    discovered: set[str] = set()
    while True:
        before_loads = len(loaded)
        page, scheduled_cursor, exhausted = store.scheduled_coordinator_candidates(
            after=scheduled_cursor,
            limit=25,
            now=clocks.wall,
        )
        assert len(loaded) - before_loads <= 26
        discovered.update(str(row["run_id"]) for row in page)
        if exhausted:
            break

    assert future_ids <= discovered


def test_scheduled_candidate_generation_fixes_due_time_and_admission_fence(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=4,
        max_nonterminal_runs=4,
        max_start_requests_per_minute=10,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="scheduled-generation-fence")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="scheduled-generation-fence",
    )
    captured = [
        _admit(
            store,
            package,
            key=f"captured-{index}",
            schedule_at="2026-04-04T11:59:00Z",
        ).run_id
        for index in range(2)
    ]
    generation_observed, queue_sequence_fence = store.scheduled_coordinator_generation(
        now=clocks.wall
    )
    assert generation_observed == clocks.wall
    assert queue_sequence_fence == 2
    later = _admit(
        store,
        package,
        key="later",
        schedule_at="2026-04-04T11:59:00Z",
    ).run_id

    fenced, _cursor, exhausted = store.scheduled_coordinator_candidates(
        after=None,
        now=generation_observed,
        through_queue_sequence=queue_sequence_fence,
        limit=100,
    )
    unfenced, _cursor, _exhausted = store.scheduled_coordinator_candidates(
        after=None,
        now=clocks.wall,
        limit=100,
    )

    assert [row["run_id"] for row in fenced] == captured
    assert exhausted is True
    assert [row["run_id"] for row in unfenced] == [*captured, later]
    for invalid_fence in (-1, True, "2"):
        with pytest.raises(
            ValueError,
            match="through_queue_sequence must be a non-negative integer",
        ):
            store.scheduled_coordinator_candidates(
                after=None,
                now=clocks.wall,
                through_queue_sequence=invalid_fence,
                limit=100,
            )


@pytest.mark.parametrize(
    "later_created_at",
    (
        "2026-04-04T10:00:00+00:00",
        "2026-04-04T09:59:59+00:00",
    ),
    ids=("same-created-at", "backward-created-at"),
)
def test_scheduled_generation_sequence_fence_excludes_post_capture_admission(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
    later_created_at: str,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 4, 12, 0, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=4,
        max_nonterminal_runs=4,
        max_start_requests_per_minute=10,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="scheduled-created-key-fence")
    package = _package(
        workflow_writer,
        tmp_path / "package",
        name="scheduled-created-key-fence",
    )
    run_ids = iter(("1" * 32, "f" * 32, "0" * 32))
    original_utc_now = store_module._utc_now
    original_uuid4 = store_module.uuid.uuid4

    def deterministic_run_uuid():
        caller = inspect.currentframe().f_back
        if caller is not None and caller.f_code.co_name == "_start_run_locked":
            return SimpleNamespace(hex=next(run_ids))
        return original_uuid4()

    def admit_at(key: str, *, created_at: str) -> str:
        snapshot = store.prepare_run_snapshot(package)
        monkeypatch.setattr(store_module, "_utc_now", lambda: created_at)
        try:
            result = store.start_run(
                RunAdmissionRequest(
                    workflow_name=package.definition.name,
                    definition_digest=snapshot.definition_digest,
                    policy_digest=snapshot.policy_digest,
                    input_manifest_digest=snapshot.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=key,
                    concurrency_key=key,
                    concurrency_policy="queue",
                    execution_mode="background",
                    run_metadata={"schedule_at": "2026-04-04T11:59:00Z"},
                ),
                immutable_snapshot=snapshot,
            )
        finally:
            monkeypatch.setattr(store_module, "_utc_now", original_utc_now)
        assert result.run_id is not None
        return result.run_id

    monkeypatch.setattr(store_module.uuid, "uuid4", deterministic_run_uuid)
    captured = [
        admit_at(
            f"captured-clock-{index}",
            created_at="2026-04-04T10:00:00+00:00",
        )
        for index in range(2)
    ]
    generation_observed, queue_sequence_fence = store.scheduled_coordinator_generation(
        now=clocks.wall
    )
    assert queue_sequence_fence == 2
    later = admit_at("later-clock", created_at=later_created_at)

    fenced, _cursor, _exhausted = store.scheduled_coordinator_candidates(
        after=None,
        now=generation_observed,
        through_queue_sequence=queue_sequence_fence,
        limit=100,
    )
    unfenced, _cursor, _exhausted = store.scheduled_coordinator_candidates(
        after=None,
        now=clocks.wall,
        limit=100,
    )

    assert later not in {str(row["run_id"]) for row in fenced}
    assert {str(row["run_id"]) for row in unfenced} == {*captured, later}


def test_scheduled_due_query_vm_work_is_bounded_before_future_rows(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 4, 5, 12, 0, 0, 123456, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        max_queued_runs=350,
        max_nonterminal_runs=350,
        max_start_requests_per_minute=350,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="bounded-sql-work")
    package = _package(workflow_writer, tmp_path / "package", name="bounded-sql-work")
    future = "2026-04-05T12:00:00.1234561Z"

    for index in range(10):
        _admit(
            store,
            package,
            key=f"initial-future-{index:03d}",
            schedule_at=future,
        )

    original_connect = store._connect
    opcode_count = 0

    def counted_connect():
        nonlocal opcode_count
        connection = original_connect()

        def count_opcode() -> int:
            nonlocal opcode_count
            opcode_count += 1
            return 0

        connection.set_progress_handler(count_opcode, 1)
        return connection

    monkeypatch.setattr(store, "_connect", counted_connect)
    rows, _cursor, exhausted = store.scheduled_coordinator_candidates(
        after=None, limit=1, now=clocks.wall
    )
    assert rows == ()
    assert exhausted is True
    initial_opcodes = opcode_count

    monkeypatch.setattr(store, "_connect", original_connect)
    for index in range(300):
        _admit(
            store,
            package,
            key=f"additional-future-{index:03d}",
            schedule_at=future,
        )

    opcode_count = 0
    monkeypatch.setattr(store, "_connect", counted_connect)
    rows, _cursor, exhausted = store.scheduled_coordinator_candidates(
        after=None, limit=1, now=clocks.wall
    )

    assert rows == ()
    assert exhausted is True
    assert opcode_count <= initial_opcodes + 100


def test_promotion_rechecks_due_atomically_after_wall_clock_moves_backward(
    tmp_path: Path,
    workflow_writer,
) -> None:
    clocks = _Clocks(datetime(2026, 5, 1, 10, 0, 0, 123457, tzinfo=UTC))
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name="promotion-leader")
    package = _package(
        workflow_writer, tmp_path / "package", name="scheduled-promotion-race"
    )
    admitted = _admit(
        store,
        package,
        key="scheduled-promotion-race",
        schedule_at="2026-05-01T10:00:00.1234561Z",
    )
    candidates, _cursor, _exhausted = store.scheduled_coordinator_candidates(
        after=None, limit=100, now=clocks.wall
    )
    assert [row["run_id"] for row in candidates] == [admitted.run_id]
    store.limits["executing"] = 1

    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=NULL WHERE run_id=?", (admitted.run_id,)
        )
    with pytest.raises(JournalRecoveryError, match="schedule index parity mismatch"):
        store.try_promote_run(admitted.run_id, now=clocks.wall)
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=? WHERE run_id=?",
            ("2026-05-01T10:00:00.1234561Z", admitted.run_id),
        )

    with pytest.raises(ValueError, match="now is required"):
        store.try_promote_run(admitted.run_id)
    assert store.load_run(admitted.run_id)["status"] == "queued"

    with pytest.raises(ValueError, match="timezone-aware"):
        store.try_promote_run(
            admitted.run_id,
            now=datetime(2026, 5, 1, 10, 0, 0, 123457),
        )
    assert store.load_run(admitted.run_id)["status"] == "queued"

    clocks.wall = datetime(2026, 5, 1, 10, 0, 0, 123456, tzinfo=UTC)
    assert store.try_promote_run(admitted.run_id, now=clocks.wall) is False
    assert store.load_run(admitted.run_id)["status"] == "queued"
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (admitted.run_id,)
            ).fetchone()[0]
            == 0
        )

    clocks.wall = datetime(2026, 5, 1, 10, 0, 0, 123457, tzinfo=UTC)
    assert store.try_promote_run(admitted.run_id, now=clocks.wall) is True
    assert store.try_promote_run(admitted.run_id, now=clocks.wall) is True
    assert store.load_run(admitted.run_id)["status"] == "running"
    assert (
        len([
            event
            for event in store.tail_events(admitted.run_id)
            if event["event_type"] == "run_promoted"
        ])
        == 1
    )


@pytest.mark.parametrize("method", ["advance", "advance_all"])
def test_scheduler_threads_its_injected_wall_sample_into_promotion(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    clocks = _Clocks(datetime(2026, 6, 1, 12, 30, tzinfo=UTC))
    store = RunStore(
        tmp_path / method,
        max_executing_runs=0,
        lease_clock=clocks.lease_sample,
    )
    _leader(store, clocks, name=f"scheduler-{method}")
    package = _package(
        workflow_writer,
        tmp_path / f"package-{method}",
        name=f"scheduled-{method}",
    )
    admitted = _admit(
        store,
        package,
        key=f"scheduled-{method}",
        schedule_at="2026-06-01T12:30:00Z",
    )
    observed: list[datetime] = []

    def capture_promotion(run_id: str, *, now: datetime) -> bool:
        assert run_id == admitted.run_id
        observed.append(now)
        return False

    monkeypatch.setattr(store, "try_promote_run", capture_promotion)
    scheduler = RunScheduler(
        store,
        utcnow=clocks.utcnow,
        monotonic=clocks.monotonic,
    )
    try:
        if method == "advance":
            scheduler.advance(admitted.run_id, max_nodes=1)
        else:
            scheduler.advance_all([admitted.run_id])
    finally:
        scheduler.shutdown(deadline_seconds=1)

    assert observed == [clocks.wall]


def test_coordinator_scheduler_uses_the_same_wall_and_monotonic_clocks(
    tmp_path: Path,
) -> None:
    clocks = _Clocks(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
    wall_clock = clocks.utcnow
    monotonic_clock = clocks.monotonic
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="clock-threading",
        ),
        hermes_home=tmp_path,
        utcnow=wall_clock,
        monotonic=monotonic_clock,
    )
    scheduler = service._scheduler(
        RunStore(tmp_path),
        fence=ExecutionFence("clock-threading", 1),
    )
    try:
        assert scheduler._utcnow is wall_clock
        assert scheduler._monotonic is monotonic_clock
    finally:
        scheduler.shutdown(deadline_seconds=1)


def test_leader_never_waits_more_than_five_monotonic_seconds_for_far_future_run(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks = _Clocks(datetime(2026, 8, 1, 12, 0, tzinfo=UTC), monotonic=0.0)
    store = RunStore(tmp_path / "home", lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks, name="bounded-leader")
    package = _package(
        workflow_writer, tmp_path / "package", name="bounded-future-sweep"
    )
    due = clocks.wall + timedelta(days=365)
    scheduled = _admit(
        store,
        package,
        key="bounded-future-sweep",
        schedule_at=due.isoformat().replace("+00:00", "Z"),
    )
    immediate = _admit(
        store,
        package,
        key="bounded-immediate-work",
        schedule_at=None,
    )
    service = _service(tmp_path / "home", clocks)
    scheduler = MagicMock()
    scheduler.shutdown_deadline_seconds = 1.0
    submissions: list[tuple[str, datetime]] = []

    def submit(run_id: str, _fence: ExecutionFence) -> bool:
        submissions.append((run_id, clocks.wall))
        return True

    scheduler.submit.side_effect = submit
    service._scheduler = MagicMock(return_value=scheduler)
    stop = threading.Event()
    waits: list[float] = []

    class _ImmediatePool:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, function, *args):
            future: Future = Future()
            try:
                future.set_result(function(*args))
            except BaseException as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, *args, **kwargs) -> None:
            pass

    def wait_for_local_wake(_stop_event, *, timeout: float) -> bool:
        waits.append(timeout)
        clocks.monotonic_value += timeout
        if len(waits) == 2:
            clocks.wall -= timedelta(days=30)
        if len(waits) == 4:
            clocks.wall = due
        if len(waits) >= 6:
            stop.set()
        return False

    monkeypatch.setattr(coordinator_module, "ThreadPoolExecutor", _ImmediatePool)
    monkeypatch.setattr(coordinator, "wait_for_local_wake", wait_for_local_wake)

    assert service._lead(
        stop,
        run_store=store,
        coordinator_store=coordinator,
        identity=identity,
        epoch=epoch,
    )

    assert waits
    assert all(0 < timeout <= 5.0 for timeout in waits)
    assert any(run_id == immediate.run_id for run_id, _wall in submissions)
    scheduled_submissions = [
        wall for run_id, wall in submissions if run_id == scheduled.run_id
    ]
    assert scheduled_submissions == [due]
    scheduler.shutdown.assert_called_once()
