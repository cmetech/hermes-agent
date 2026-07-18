from __future__ import annotations

from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessIdentity


def _identity(owner_id: str) -> CoordinatorIdentity:
    process = ProcessIdentity.capture(os.getpid())
    return CoordinatorIdentity(
        owner_id=owner_id,
        host_kind="gateway",
        host_instance_id=f"host-{owner_id}",
        pid=process.pid,
        process_start_time=process.start_time,
    )


def _contend(database: str, owner_id: str, barrier, results, release) -> None:
    store = CoordinatorStore(Path(database))
    barrier.wait()
    acquired = store.try_acquire(
        _identity(owner_id),
        now=datetime.now(timezone.utc),
        lease_seconds=2,
    )
    results.put((owner_id, acquired.is_leader, acquired.lease.epoch))
    release.wait(5)


def _acquire_and_exit(database: str, owner_id: str, results) -> None:
    acquired = CoordinatorStore(Path(database)).try_acquire(
        _identity(owner_id),
        now=datetime.now(timezone.utc),
        lease_seconds=0.25,
    )
    results.put((acquired.is_leader, acquired.lease.epoch))


def _acquire_after_lock(database: str, results) -> None:
    acquired = CoordinatorStore(Path(database)).try_acquire(
        _identity("after-lock"),
        now=datetime.now(timezone.utc),
        lease_seconds=2,
    )
    results.put(acquired.is_leader)


def test_two_processes_elect_exactly_one_leader(tmp_path) -> None:
    run_store = RunStore(tmp_path / "home")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    results = context.Queue()
    release = context.Event()
    processes = [
        context.Process(
            target=_contend,
            args=(str(run_store.database), owner, barrier, results, release),
        )
        for owner in ("first", "second")
    ]
    for process in processes:
        process.start()
    try:
        barrier.wait(timeout=5)
        observed = [results.get(timeout=5), results.get(timeout=5)]
        assert sum(is_leader for _owner, is_leader, _epoch in observed) == 1
        assert {epoch for _owner, _is_leader, epoch in observed} == {1}
    finally:
        release.set()
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0


def test_expired_crashed_process_lease_is_taken_over_with_new_epoch(tmp_path) -> None:
    run_store = RunStore(tmp_path / "home")
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    crashed = context.Process(
        target=_acquire_and_exit,
        args=(str(run_store.database), "crashed", results),
    )
    crashed.start()
    crashed.join(timeout=5)
    assert crashed.exitcode == 0
    assert results.get(timeout=5) == (True, 1)

    time.sleep(0.3)
    takeover = context.Process(
        target=_acquire_and_exit,
        args=(str(run_store.database), "takeover", results),
    )
    takeover.start()
    takeover.join(timeout=5)
    assert takeover.exitcode == 0
    assert results.get(timeout=5) == (True, 2)


def test_election_waits_for_sqlite_writer_without_split_leadership(tmp_path) -> None:
    run_store = RunStore(tmp_path / "home")
    connection = sqlite3.connect(run_store.database, timeout=30)
    connection.execute("BEGIN IMMEDIATE")
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    contender = context.Process(
        target=_acquire_after_lock,
        args=(str(run_store.database), results),
    )
    contender.start()
    try:
        time.sleep(0.1)
        assert contender.is_alive()
        connection.rollback()
        contender.join(timeout=5)
        assert contender.exitcode == 0
        assert results.get(timeout=5) is True
    finally:
        if contender.is_alive():
            contender.terminate()
            contender.join(timeout=5)
        connection.close()
