from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
import plugins.workflow.coordinator as coordinator_module
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import JournalRecoveryError, RunStore
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
            concurrency_key=key,
            concurrency_policy="queue",
            execution_mode="background",
            run_metadata=metadata,
        ),
        immutable_snapshot=snapshot,
    )
    assert result.run_id is not None
    return result


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
    assert service._scheduled_sweep_cursor is not None
    assert not any(
        call.args[0] == newly_due.run_id for call in scheduler.submit.call_args_list
    )

    scheduler.reset_mock()
    clocks.wall = due
    service._sweep_once(store, coordinator, identity, epoch, scheduler, normal_cursor)

    assert any(
        call.args[0] == newly_due.run_id for call in scheduler.submit.call_args_list
    )


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
