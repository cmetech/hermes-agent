from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
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
