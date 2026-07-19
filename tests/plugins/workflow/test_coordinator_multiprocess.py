from __future__ import annotations

from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
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


def _run_blocked_old_epoch(
    home: str,
    run_id: str,
    owner_id: str,
    epoch: int,
    entered,
    release,
    results,
) -> None:
    store = RunStore(Path(home))
    original = store.mark_node_started

    def block_before_execution(claim, **kwargs):
        entered.set()
        release.wait(5)
        return original(claim, **kwargs)

    store.mark_node_started = block_before_execution
    scheduler = RunScheduler(
        store,
        owner_id=f"coordinator:{owner_id}:{epoch}",
        execution_fence=ExecutionFence(owner_id, epoch),
    )
    projection = scheduler.advance(run_id)
    results.put(
        (
            projection["status"],
            projection["nodes"]["start"]["state"],
        )
    )


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
        assert sum(is_leader for _owner, is_leader, _epoch in observed) == 1, observed
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


def test_mid_node_takeover_fences_old_epoch_before_outward_effect(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    marker = tmp_path / "outward-effects.txt"
    path = workflow_writer(
        tmp_path / "package",
        name="mid-node-takeover",
        nodes=[{"id": "start", "bash": f"echo epoch-2 >> {marker}"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "outward_action_nodes: [start]\n",
        encoding="utf-8",
    )
    package = load_workflow(path)
    store = RunStore(home)
    coordinator = CoordinatorStore(store.database)
    first_identity = _identity("first")
    first = coordinator.try_acquire(
        first_identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="mid-node-takeover",
            concurrency_key="mid-node-takeover",
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    results = context.Queue()
    old_epoch = context.Process(
        target=_run_blocked_old_epoch,
        args=(
            str(home),
            admitted.run_id,
            first_identity.owner_id,
            first.lease.epoch,
            entered,
            release,
            results,
        ),
    )
    old_epoch.start()
    try:
        assert entered.wait(5)
        with store._connect() as connection:
            connection.execute(
                "UPDATE coordinator_lease SET "
                "heartbeat_monotonic=heartbeat_monotonic-31 "
                "WHERE singleton=1 AND owner_id='first' AND epoch=1"
            )
        second_identity = _identity("second")
        second = coordinator.try_acquire(
            second_identity,
            now=datetime.now(timezone.utc),
            lease_seconds=30,
        )
        assert second.is_leader and second.lease.epoch == 2
        release.set()
        old_epoch.join(timeout=5)
        assert old_epoch.exitcode == 0
        assert results.get(timeout=5) == ("running", "ready")
        assert not marker.exists()

        completed = RunScheduler(
            store,
            owner_id="coordinator:second:2",
            execution_fence=ExecutionFence("second", 2),
        ).advance(admitted.run_id)
        assert completed["status"] == "succeeded"
        assert marker.read_text(encoding="utf-8").splitlines() == ["epoch-2"]
    finally:
        release.set()
        if old_epoch.is_alive():
            old_epoch.terminate()
            old_epoch.join(timeout=5)
