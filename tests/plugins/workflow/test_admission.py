from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import shutil
import sqlite3
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _prepared(store: RunStore, workflow_writer, tmp_path, *, name="demo"):
    path = workflow_writer(
        tmp_path / f"package-{name}",
        name=name,
        nodes=[{"id": "first", "bash": "printf first"}],
    )
    package = load_workflow(path)
    return store.prepare_run_snapshot(package)


def _request(snapshot, *, key="delivery-1", policy="queue", name="demo"):
    return RunAdmissionRequest(
        workflow_name=name,
        definition_digest=snapshot.definition_digest,
        policy_digest=snapshot.policy_digest,
        input_manifest_digest=snapshot.input_manifest_digest,
        trigger_source="cli",
        idempotency_key=key,
        concurrency_key=name,
        concurrency_policy=policy,
    )


def test_duplicate_start_is_atomic_and_returns_one_run(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    request = _request(prepared)

    def start(_):
        snapshot = store.clone_prepared_snapshot(prepared)
        return store.start_run(request, immutable_snapshot=snapshot)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(start, range(100)))

    assert {result.run_id for result in results} == {results[0].run_id}
    assert sum(result.disposition == "created" for result in results) == 1
    assert sum(result.disposition == "existing" for result in results) == 99
    assert len(store.list_runs()) == 1


def test_reconciliation_cannot_remove_snapshot_before_owner_marker_is_written(
    tmp_path, workflow_writer, monkeypatch
):
    store = RunStore(tmp_path / "home")
    package_path = workflow_writer(
        tmp_path / "package-marker-race",
        name="marker-race",
        nodes=[{"id": "first", "bash": "printf first"}],
    )
    package = load_workflow(package_path)
    admission_snapshot = store.prepare_run_snapshot(package)
    original_write_owner = RunStore._write_snapshot_owner
    marker_started = threading.Event()
    allow_marker = threading.Event()

    def delayed_owner_marker(directory):
        marker_started.set()
        assert allow_marker.wait(5)
        original_write_owner(directory)

    monkeypatch.setattr(
        RunStore, "_write_snapshot_owner", staticmethod(delayed_owner_marker)
    )
    start_finished = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(store.prepare_run_snapshot, package)
        assert marker_started.wait(5)
        starting = pool.submit(
            store.start_run,
            _request(admission_snapshot, key="marker-race", name="marker-race"),
            immutable_snapshot=admission_snapshot,
        )
        starting.add_done_callback(lambda _future: start_finished.set())
        assert not start_finished.wait(0.1)
        allow_marker.set()
        prepared = preparing.result(timeout=5)
        admitted = starting.result(timeout=5)

    assert admitted.disposition == "created"
    assert prepared.staging_directory.exists()


def test_reused_key_with_changed_digest_conflicts(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    first = store.start_run(_request(prepared), immutable_snapshot=prepared)
    changed = store.prepare_empty_snapshot(
        definition_digest="f" * 64,
        policy_digest="0" * 64,
        input_manifest_digest="1" * 64,
    )
    second = store.start_run(_request(changed), immutable_snapshot=changed)

    assert first.disposition == "created"
    assert second.disposition == "rejected"
    assert second.reason_code == "idempotency_conflict"
    assert len(store.list_runs()) == 1


def test_legacy_digest_fallback_rejects_current_and_tampered_snapshots(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    current_retry = store.clone_prepared_snapshot(prepared)
    tampered_retry = store.clone_prepared_snapshot(prepared)
    first = store.start_run(_request(prepared), immutable_snapshot=prepared)

    current_retry = replace(current_retry, input_manifest_digest="1" * 64)
    current = store.start_run(
        _request(current_retry), immutable_snapshot=current_retry
    )

    (tampered_retry.staging_directory / "resources.json").write_text("not json\n")
    tampered_retry = replace(tampered_retry, input_manifest_digest="2" * 64)
    tampered = store.start_run(
        _request(tampered_retry), immutable_snapshot=tampered_retry
    )

    assert first.disposition == "created"
    assert (current.disposition, current.reason_code) == (
        "rejected",
        "idempotency_conflict",
    )
    assert (tampered.disposition, tampered.reason_code) == (
        "rejected",
        "idempotency_conflict",
    )


def test_overlap_policies_queue_forbid_and_allow(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    snapshots = [store.clone_prepared_snapshot(prepared) for _ in range(4)]
    first = store.start_run(
        _request(prepared, key="one"), immutable_snapshot=snapshots[0]
    )
    queued = store.start_run(
        _request(prepared, key="two"),
        immutable_snapshot=snapshots[1],
    )
    forbidden = store.start_run(
        _request(prepared, key="three", policy="forbid"),
        immutable_snapshot=snapshots[2],
    )
    allowed = store.start_run(
        _request(prepared, key="four", policy="allow"),
        immutable_snapshot=snapshots[3],
    )

    assert first.disposition == "created"
    assert queued.disposition == "queued"
    assert queued.blocked_by_run_id == first.run_id
    assert forbidden == forbidden.__class__(None, "rejected", "overlap_forbidden")
    assert allowed.disposition == "created"


def test_start_rate_and_queue_capacity_reject_before_worker_allocation(
    tmp_path, workflow_writer
):
    rate_store = RunStore(tmp_path / "rate-home", max_start_requests_per_minute=1)
    prepared = _prepared(rate_store, workflow_writer, tmp_path, name="rate-demo")
    snapshots = [rate_store.clone_prepared_snapshot(prepared) for _ in range(2)]
    assert (
        rate_store.start_run(
            _request(prepared, key="one", policy="allow", name="rate-demo"),
            immutable_snapshot=snapshots[0],
        ).disposition
        == "created"
    )
    rejected = rate_store.start_run(
        _request(prepared, key="two", policy="allow", name="rate-demo"),
        immutable_snapshot=snapshots[1],
    )
    assert rejected.reason_code == "start_rate_capacity"
    assert not snapshots[1].staging_directory.exists()

    queue_store = RunStore(tmp_path / "queue-home", max_queued_runs=1)
    queued_prepared = _prepared(
        queue_store, workflow_writer, tmp_path, name="queue-demo"
    )
    queued_snapshots = [
        queue_store.clone_prepared_snapshot(queued_prepared) for _ in range(3)
    ]
    queue_store.start_run(
        _request(queued_prepared, key="one", name="queue-demo"),
        immutable_snapshot=queued_snapshots[0],
    )
    assert (
        queue_store.start_run(
            _request(queued_prepared, key="two", name="queue-demo"),
            immutable_snapshot=queued_snapshots[1],
        ).disposition
        == "queued"
    )
    rejected = queue_store.start_run(
        _request(queued_prepared, key="three", name="queue-demo"),
        immutable_snapshot=queued_snapshots[2],
    )
    assert rejected.reason_code == "queued_capacity"
    assert not queued_snapshots[2].staging_directory.exists()


@pytest.mark.parametrize("with_unrelated_held_lane", [False, True])
def test_queue_policy_start_queues_at_execution_capacity(
    tmp_path, workflow_writer, with_unrelated_held_lane
) -> None:
    store = RunStore(
        tmp_path / "execution-capacity-home",
        max_executing_runs=1,
        max_queued_runs=2,
        max_nonterminal_runs=10,
        max_start_requests_per_minute=10,
    )
    if with_unrelated_held_lane:
        held_path = workflow_writer(
            tmp_path / "held-capacity-package",
            name="held-capacity-lane",
            nodes=[{"id": "gate", "approval": {"message": "Hold?"}}],
        )
        held_package = load_workflow(held_path)
        held_snapshot = store.prepare_run_snapshot(held_package)
        held = store.start_run(
            _request(
                held_snapshot,
                key="held-capacity-lane",
                policy="queue",
                name="held-capacity-lane",
            ),
            immutable_snapshot=held_snapshot,
        )
        assert RunScheduler(store).advance(held.run_id)["status"] == "paused"

    blocker = _prepared(
        store,
        workflow_writer,
        tmp_path,
        name="execution-capacity-blocker",
    )
    assert (
        store.start_run(
            _request(
                blocker,
                key="execution-capacity-blocker",
                policy="allow",
                name="execution-capacity-blocker",
            ),
            immutable_snapshot=blocker,
        ).disposition
        == "created"
    )
    queued = _prepared(
        store,
        workflow_writer,
        tmp_path,
        name="execution-capacity-queued",
    )

    result = store.start_run(
        _request(
            queued,
            key="execution-capacity-queued",
            policy="queue",
            name="execution-capacity-queued",
        ),
        immutable_snapshot=queued,
    )

    assert result.disposition == "queued"
    assert result.run_id is not None
    assert store.load_run(result.run_id)["status"] == "queued"


def test_execution_capacity_queue_respects_queue_bound_and_other_policies(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(
        tmp_path / "execution-policy-home",
        max_executing_runs=1,
        max_queued_runs=1,
        max_nonterminal_runs=10,
        max_start_requests_per_minute=10,
    )

    def start(name: str, *, policy: str):
        snapshot = _prepared(store, workflow_writer, tmp_path, name=name)
        return store.start_run(
            _request(snapshot, key=name, policy=policy, name=name),
            immutable_snapshot=snapshot,
        )

    assert start("capacity-policy-blocker", policy="allow").disposition == "created"
    assert start("capacity-policy-allow", policy="allow").reason_code == (
        "executing_capacity"
    )
    assert start("capacity-policy-forbid", policy="forbid").reason_code == (
        "executing_capacity"
    )
    assert start("capacity-policy-queued", policy="queue").disposition == "queued"
    assert start("capacity-policy-queue-full", policy="queue").reason_code == (
        "queued_capacity"
    )


def test_start_racing_shutdown_is_discoverable_or_rejected_before_publish(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        return store.start_run(_request(prepared), immutable_snapshot=prepared)

    def shutdown():
        barrier.wait()
        store.close_admission()

    with ThreadPoolExecutor(max_workers=2) as pool:
        start_future = pool.submit(start)
        shutdown_future = pool.submit(shutdown)
        result = start_future.result()
        shutdown_future.result()

    if result.disposition == "created":
        assert store.get_run_status(result.run_id)["status"] == "running"
    else:
        assert result.reason_code == "admission_closed"
        assert store.list_runs() == ()
        assert not prepared.staging_directory.exists()


def test_restart_releases_crashed_reservation_and_allows_one_retry(
    tmp_path, workflow_writer, monkeypatch
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    retry_snapshot = store.clone_prepared_snapshot(prepared)
    request = _request(prepared)

    def crash_before_publication(*_args, **_kwargs):
        raise RuntimeError("simulated crash before queue publication")

    monkeypatch.setattr(store, "_publish_reserved_run", crash_before_publication)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.start_run(request, immutable_snapshot=prepared)

    restarted = RunStore(tmp_path / "home")
    events = restarted.list_admission_events()
    result = restarted.start_run(request, immutable_snapshot=retry_snapshot)

    assert events[-1]["event_type"] == "admission_reservation_released"
    assert events[-1]["reason_code"] == "incomplete_publication"
    assert result.disposition == "created"
    assert len(restarted.list_runs()) == 1


def test_restart_finishes_publication_when_projection_was_durable(
    tmp_path, workflow_writer, monkeypatch
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    duplicate_snapshot = store.clone_prepared_snapshot(prepared)
    request = _request(prepared)

    def crash_after_projection(*_args, **_kwargs):
        raise RuntimeError("simulated crash after durable projection")

    monkeypatch.setattr(store, "_mark_reservation_published", crash_after_projection)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.start_run(request, immutable_snapshot=prepared)

    restarted = RunStore(tmp_path / "home")
    existing = restarted.start_run(request, immutable_snapshot=duplicate_snapshot)

    assert existing.disposition == "existing"
    assert len(restarted.list_runs()) == 1
    assert restarted.list_admission_events()[-1]["event_type"] == (
        "admission_reservation_recovered"
    )


def test_restart_releases_projection_without_complete_initial_event(
    tmp_path, workflow_writer, monkeypatch
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)

    def crash_with_partial_files(**kwargs):
        run_directory = kwargs["run_directory"]
        run_directory.parent.mkdir(parents=True, exist_ok=True)
        os.replace(kwargs["snapshot"].staging_directory, run_directory)
        (run_directory / "run.json").write_text(
            json.dumps({
                "run_id": kwargs["run_id"],
                "status": kwargs["status"],
                "event_sequence": 1,
            })
        )
        (run_directory / "events.jsonl").touch()
        raise RuntimeError("simulated crash during initial event")

    monkeypatch.setattr(store, "_publish_reserved_run", crash_with_partial_files)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.start_run(_request(prepared), immutable_snapshot=prepared)

    restarted = RunStore(tmp_path / "home")

    assert restarted.list_runs() == ()
    assert restarted.list_admission_events()[-1]["event_type"] == (
        "admission_reservation_released"
    )


def test_restart_removes_snapshot_owned_by_dead_preparer(
    tmp_path, workflow_writer, monkeypatch
):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    monkeypatch.setattr(
        RunStore, "_snapshot_owner_alive", staticmethod(lambda _pid: False)
    )

    restarted = RunStore(tmp_path / "home")

    assert not prepared.staging_directory.exists()
    assert restarted.list_admission_events()[-1]["event_type"] == (
        "orphan_snapshot_removed"
    )


def test_snapshot_owner_probe_uses_cross_platform_pid_helper(monkeypatch) -> None:
    import gateway.status

    observed = []
    monkeypatch.setattr(
        gateway.status,
        "_pid_exists",
        lambda pid: observed.append(pid) or True,
    )
    monkeypatch.setattr(
        os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("os.kill(pid, 0) is unsafe on Windows")
        ),
    )

    assert RunStore._snapshot_owner_alive(12345) is True
    assert observed == [12345]


@pytest.mark.parametrize(
    "damage", ["missing", "empty", "corrupt", "replaced", "partial"]
)
def test_damaged_admission_index_preserves_and_recovers_corroborated_run_evidence(
    tmp_path, workflow_writer, damage
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = _prepared(store, workflow_writer, tmp_path)
    admitted = store.start_run(_request(prepared), immutable_snapshot=prepared)
    run_id = admitted.run_id
    assert run_id
    evidence_directory = store.run_directory(run_id)
    projection_before = (evidence_directory / "run.json").read_bytes()
    journal_before = (evidence_directory / "events.jsonl").read_bytes()

    if damage == "replaced":
        replacement = RunStore(tmp_path / "replacement-home")
        shutil.copyfile(replacement.database, store.database)
    elif damage == "partial":
        partial = tmp_path / "partial.sqlite3"
        with sqlite3.connect(partial) as connection:
            connection.execute(
                "CREATE TABLE runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
            )
        shutil.copyfile(partial, store.database)
    else:
        store.database.unlink()
        if damage == "empty":
            store.database.touch()
            store.authority_marker.unlink()
        elif damage == "corrupt":
            store.database.write_bytes(b"not a sqlite database")

    restarted = RunStore(home)

    assert evidence_directory.is_dir()
    assert (evidence_directory / "run.json").read_bytes() == projection_before
    assert (evidence_directory / "events.jsonl").read_bytes() == journal_before
    assert restarted.load_run(run_id)["status"] == "running"
    assert restarted.storage_health()["status"] == "repair_required"
    repair = restarted.list_repair_events()[-1]
    assert repair["run_id"] == run_id
    assert repair["outcome"] == "index_rebuilt"
    assert repair["projection_sha256"]
    assert repair["journal_sha256"]

    blocked_snapshot = _prepared(
        restarted, workflow_writer, tmp_path, name="blocked-after-index-damage"
    )
    blocked = restarted.start_run(
        _request(
            blocked_snapshot,
            key="blocked-after-index-damage",
            name="blocked-after-index-damage",
        ),
        immutable_snapshot=blocked_snapshot,
    )
    assert blocked.reason_code == "storage_repair_required"
    assert not blocked_snapshot.staging_directory.exists()

    assert restarted.repair_storage()["status"] == "healthy"
    resumed_snapshot = _prepared(
        restarted, workflow_writer, tmp_path, name="after-index-repair"
    )
    resumed = restarted.start_run(
        _request(
            resumed_snapshot,
            key="after-index-repair",
            name="after-index-repair",
        ),
        immutable_snapshot=resumed_snapshot,
    )
    assert resumed.disposition == "created"


def test_status_inconsistency_uses_corroborated_evidence_and_requires_repair(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = _prepared(store, workflow_writer, tmp_path)
    run_id = store.start_run(_request(prepared), immutable_snapshot=prepared).run_id
    assert run_id
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET status='failed' WHERE run_id=?",
            (run_id,),
        )

    restarted = RunStore(home)

    assert restarted.get_run_status(run_id)["status"] == "running"
    assert restarted.storage_health()["status"] == "repair_required"
    repair = restarted.list_repair_events()[-1]
    assert repair["reason_code"] == "index_status_inconsistent"
    assert repair["outcome"] == "index_rebuilt"


def test_fifo_promotion_refuses_newer_queue_sequence_until_older_runs(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=1)
    package = load_workflow(
        workflow_writer(tmp_path / "fifo-package", name="fifo-promotion")
    )

    def start(key: str):
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
                concurrency_policy="queue",
            ),
            immutable_snapshot=prepared,
        )

    blocker = start("fifo-blocker")
    older = start("fifo-older")
    newer = start("fifo-newer")
    assert older.disposition == newer.disposition == "queued"
    older_projection = store.load_run(older.run_id)
    newer_projection = store.load_run(newer.run_id)
    assert older_projection["queue_sequence"] < newer_projection["queue_sequence"]

    assert RunScheduler(store).advance(blocker.run_id)["status"] == "succeeded"
    assert not store.try_promote_run(newer.run_id)
    assert store.try_promote_run(older.run_id)
    assert store.load_run(newer.run_id)["queue_sequence"] == newer_projection[
        "queue_sequence"
    ]


def test_lane_ineligible_fifo_head_does_not_starve_an_independent_lane(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=2)
    held_path = workflow_writer(
        tmp_path / "held-package",
        name="held-lane",
        nodes=[{"id": "gate", "approval": {"message": "Hold lane?"}}],
    )
    held_path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: queue\n",
        encoding="utf-8",
    )
    held_package = load_workflow(held_path)
    independent_package = load_workflow(
        workflow_writer(tmp_path / "independent-package", name="independent-lane")
    )

    def start(package, key: str):
        snapshot = store.prepare_run_snapshot(package)
        return store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=snapshot.definition_digest,
                policy_digest=snapshot.policy_digest,
                input_manifest_digest=snapshot.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=key,
                concurrency_key=package.definition.name,
                concurrency_policy="queue",
            ),
            immutable_snapshot=snapshot,
        )

    holder = start(held_package, "held-lane-owner")
    independent_holder = start(independent_package, "independent-owner")
    blocked_head = start(held_package, "held-lane-blocked-head")
    independent = start(independent_package, "independent-younger")

    assert blocked_head.disposition == "queued"
    assert independent.disposition == "queued"
    assert RunScheduler(store).advance(holder.run_id)["status"] == "paused"
    store.cancel_run(independent_holder.run_id)
    assert not store.try_promote_run(blocked_head.run_id)
    assert store.try_promote_run(independent.run_id)
    assert store.load_run(independent.run_id)["status"] == "running"


def test_runnable_request_skips_lane_ineligible_fifo_head(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "request-home", max_executing_runs=1)
    held_path = workflow_writer(
        tmp_path / "request-held-package",
        name="request-held-lane",
        nodes=[{"id": "gate", "approval": {"message": "Hold lane?"}}],
    )
    held_path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: queue\n",
        encoding="utf-8",
    )
    held_package = load_workflow(held_path)
    independent_package = load_workflow(
        workflow_writer(
            tmp_path / "request-independent-package",
            name="request-independent-lane",
        )
    )

    def start(package, key: str):
        snapshot = store.prepare_run_snapshot(package)
        return store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=snapshot.definition_digest,
                policy_digest=snapshot.policy_digest,
                input_manifest_digest=snapshot.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=key,
                concurrency_key=package.definition.name,
                concurrency_policy="queue",
            ),
            immutable_snapshot=snapshot,
        )

    holder = start(held_package, "request-held-owner")
    assert RunScheduler(store).advance(holder.run_id)["status"] == "paused"
    assert start(held_package, "request-blocked-head").disposition == "queued"

    independent = start(independent_package, "request-independent-younger")

    assert independent.disposition == "created"
    assert store.load_run(independent.run_id)["status"] == "running"


def test_pause_lane_policy_is_rejected_outside_queue_overlap(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(tmp_path / "invalid-lane-policy", name="invalid-lane")
    path.with_name("example.hermes.yaml").write_text(
        "overlap_policy: allow\npause_lane_policy: release\n",
        encoding="utf-8",
    )

    with pytest.raises(
        WorkflowValidationError, match="pause_lane_policy requires queue"
    ):
        load_workflow(path)


def test_uncorroborated_orphan_evidence_is_quarantined_without_deletion(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = _prepared(store, workflow_writer, tmp_path)
    run_id = store.start_run(_request(prepared), immutable_snapshot=prepared).run_id
    assert run_id
    evidence_directory = store.run_directory(run_id)
    events_path = evidence_directory / "events.jsonl"
    event = json.loads(events_path.read_text())
    event["projection_sha256"] = "0" * 64
    events_path.write_text(json.dumps(event, sort_keys=True) + "\n")
    store.database.unlink()

    restarted = RunStore(home)

    assert not evidence_directory.exists()
    preserved = list(restarted.quarantine_root.glob(f"orphan-{run_id}-*"))
    assert len(preserved) == 1
    assert (preserved[0] / "run.json").is_file()
    assert (preserved[0] / "events.jsonl").is_file()
    assert restarted.storage_health()["status"] == "repair_required"
    repair = restarted.list_repair_events()[-1]
    assert repair["run_id"] == run_id
    assert repair["outcome"] == "evidence_quarantined"
    assert repair["preserved_path"] == str(preserved[0])
    with pytest.raises(KeyError):
        restarted.load_run(run_id)


def test_corrupt_index_is_preserved_for_forensics(tmp_path, workflow_writer) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = _prepared(store, workflow_writer, tmp_path)
    store.start_run(_request(prepared), immutable_snapshot=prepared)
    corrupt_bytes = b"not a sqlite database"
    store.database.write_bytes(corrupt_bytes)

    restarted = RunStore(home)

    preserved = list(restarted.quarantine_root.glob("admission-index-*/admission.sqlite3"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt_bytes
