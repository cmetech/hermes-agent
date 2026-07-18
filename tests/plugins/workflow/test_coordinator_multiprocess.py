from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path

from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessIdentity


def _contend_for_lease(database: str, start, results, owner_id: str) -> None:
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id=owner_id,
        host_kind="gateway" if owner_id == "gateway" else "web",
        host_instance_id=f"host-{owner_id}",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    start.wait(timeout=5)
    result = CoordinatorStore(Path(database)).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    results.put((owner_id, result.is_leader, result.lease.owner_id, result.lease.epoch))


def test_two_spawned_hosts_elect_exactly_one_leader(tmp_path) -> None:
    run_store = RunStore(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_contend_for_lease,
            args=(str(run_store.database), start, results, owner_id),
        )
        for owner_id in ("gateway", "web")
    ]
    for process in processes:
        process.start()
    start.set()
    rows = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sum(1 for _owner, leader, _actual, _epoch in rows if leader) == 1
    assert {actual for _owner, _leader, actual, _epoch in rows} == {
        next(owner for owner, leader, _actual, _epoch in rows if leader)
    }
    assert {epoch for _owner, _leader, _actual, epoch in rows} == {1}


def test_expired_spawned_owner_is_taken_over_with_higher_epoch(tmp_path) -> None:
    run_store = RunStore(tmp_path)
    store = CoordinatorStore(run_store.database)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    process = ProcessIdentity.capture(os.getpid())
    first = CoordinatorIdentity(
        owner_id="dead-process",
        host_kind="gateway",
        host_instance_id="dead-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    second = CoordinatorIdentity(
        owner_id="replacement",
        host_kind="web",
        host_instance_id="replacement-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )

    assert store.try_acquire(first, now=now, lease_seconds=30).is_leader
    assert not store.try_acquire(
        second,
        now=now + timedelta(seconds=29),
        lease_seconds=30,
    ).is_leader
    takeover = store.try_acquire(
        second,
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )
    assert takeover.is_leader
    assert takeover.lease.epoch == 2
