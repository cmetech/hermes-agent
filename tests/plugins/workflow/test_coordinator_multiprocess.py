from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path
import shlex
import sqlite3
import threading
import time

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.coordinator import WorkflowCoordinatorService
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


def _race_resolution_wake(home: str, run_id: str, due: str, barrier, results) -> None:
    barrier.wait()
    awakened = RunStore(Path(home)).wake_due_output_resolutions(
        run_id, now=datetime.fromisoformat(due)
    )
    results.put(awakened)


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


def _own_foreground_until_killed(
    home: str,
    workflow_path: str,
    key: str,
    unresolved_outward_spawn: bool,
    results,
) -> None:
    store = RunStore(Path(home))
    package = load_workflow(Path(workflow_path))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            execution_mode="foreground",
            foreground_owner_id=f"foreground-process-{os.getpid()}",
            foreground_lease_seconds=0.2,
        ),
        immutable_snapshot=prepared,
    )
    if unresolved_outward_spawn:
        claim = store.claim_node(
            admitted.run_id,
            "start",
            f"foreground-process-{os.getpid()}",
            executor_id="bash",
            effect_classification="outward",
        )
        assert claim is not None
        store.mark_node_started(claim)
        assert store.record_spawn_intent(
            claim, executor_nonce="owner-died-after-spawn-intent"
        )
    results.put(admitted.run_id)
    time.sleep(30)


def _coordinator_service(home: Path) -> WorkflowCoordinatorService:
    return WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="foreground-adopter",
        ),
        hermes_home=home,
        heartbeat_seconds=0.1,
        lease_seconds=3.0,
        sweep_backoff_seconds=(0.02, 0.04, 0.08),
    )


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before deadline")


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
        nodes=[
            {
                "id": "start",
                "bash": f"echo epoch-2 >> {shlex.quote(str(marker))}",
            }
        ],
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


def test_foreground_owner_death_is_adopted_and_replay_safe_run_continues(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    workflow_path = workflow_writer(
        tmp_path / "safe-package",
        name="foreground-safe-adoption",
        nodes=[{"id": "start", "bash": "true"}],
    )
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    owner = context.Process(
        target=_own_foreground_until_killed,
        args=(str(home), str(workflow_path), "safe-adoption", False, results),
    )
    owner.start()
    run_id = results.get(timeout=5)
    owner.terminate()
    owner.join(timeout=5)
    assert owner.exitcode is not None
    time.sleep(0.25)

    service = _coordinator_service(home)
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    store = RunStore(home)
    try:
        _wait_until(lambda: store.get_run_status(run_id)["status"] == "succeeded")
        projection = store.load_run(run_id)
        assert projection["execution_mode"] == "background"
        assert projection["foreground_owner_id"] is None
        assert projection["execution_handoff"]["transition"] == (
            "foreground_execution_adopted"
        )
        assert projection["execution_handoff"]["execution_mode"] == "background"
        assert "foreground_execution_adopted" in {
            event["event_type"] for event in store.tail_events(run_id)
        }
    finally:
        stop.set()
        CoordinatorStore(store.database).notify_local()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_due_resolution_wake_has_one_multiprocess_cas_winner(
    tmp_path, workflow_writer
) -> None:
    """Catch two coordinators recording the same failed-read wake."""
    home = tmp_path / "resolution-race-home"
    path = workflow_writer(
        tmp_path / "resolution-race-package",
        name="resolution-race",
        nodes=[
            {"id": "producer", "bash": "true"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["producer"],
                "when": "$producer.output == 'ready'",
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    store = RunStore(home)
    package = load_workflow(path)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="resolution-race",
            concurrency_key="resolution-race",
        ),
        immutable_snapshot=prepared,
    )
    identity = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "publication_id": "a" * 32,
        "sha256": "b" * 64,
        "size_bytes": 5,
        "media_type": "text/markdown; charset=utf-8",
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": "text",
    }
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    assert store.defer_output_resolution(
        admitted.run_id,
        "consumer",
        producer_identity=identity,
        now=observed,
    )
    due = observed + timedelta(milliseconds=250)

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_resolution_wake,
            args=(str(home), admitted.run_id, due.isoformat(), barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in processes]
    assert sorted(outcomes, key=len) == [(), ("consumer",)]
    projection = store.load_run(admitted.run_id)
    assert projection["nodes"]["consumer"]["state"] == "pending"
    assert projection["nodes"]["consumer"]["resolution_read_count"] == 1
    assert sum(
        event["event_type"] == "output_resolution_ready"
        for event in store.tail_events(admitted.run_id)
    ) == 1


def test_foreground_owner_death_with_unresolved_outward_spawn_reconciles(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    marker = tmp_path / "must-not-run.txt"
    workflow_path = workflow_writer(
        tmp_path / "outward-package",
        name="foreground-outward-adoption",
        nodes=[{"id": "start", "bash": f"echo duplicate >> {marker}"}],
    )
    workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml").write_text(
        "outward_action_nodes: [start]\n",
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    owner = context.Process(
        target=_own_foreground_until_killed,
        args=(str(home), str(workflow_path), "outward-adoption", True, results),
    )
    owner.start()
    run_id = results.get(timeout=5)
    owner.terminate()
    owner.join(timeout=5)
    assert owner.exitcode is not None
    time.sleep(0.25)

    service = _coordinator_service(home)
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    store = RunStore(home)
    try:
        _wait_until(lambda: store.get_run_status(run_id)["status"] == "paused")
        projection = store.load_run(run_id)
        node = projection["nodes"]["start"]
        assert projection["execution_mode"] == "background"
        assert node["recovery"]["observation"] == "outcome_uncertain"
        assert node["pending_interaction"]["type"] == "reconcile"
        assert not marker.exists()
    finally:
        stop.set()
        CoordinatorStore(store.database).notify_local()
        thread.join(timeout=5)
        assert not thread.is_alive()
