from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import (
    JournalRecoveryError,
    RunStore,
    StorageQuotaError,
)
import plugins.workflow.store as store_module
import plugins.workflow.sessions as sessions_module
from tools.managed_process import ProcessIdentity


def _request(prepared, key: str) -> RunAdmissionRequest:
    return RunAdmissionRequest(
        workflow_name="duplicate-stress",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="api",
        idempotency_key=key,
        concurrency_key="duplicate-stress",
    )


def _start(store: RunStore, package, key: str):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )


def test_torn_final_journal_frame_preserves_complete_evidence(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="torn-tail"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "torn-tail")
    store.append_event(admitted.run_id, "semantic_progress", {"step": 1})
    expected = store.load_run(admitted.run_id)
    directory = store.run_directory(admitted.run_id)
    torn = b'{"frame_version":1,"schema_version":2,"sequence":3'
    with (directory / "events.jsonl").open("ab") as handle:
        handle.write(torn)
        handle.flush()

    recovered = store.load_run(admitted.run_id)

    assert recovered == expected
    assert (directory / "events.jsonl").read_bytes().endswith(b"\n")
    preserved = list(directory.glob("events.jsonl.torn-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == torn


def test_run_listing_isolates_corrupt_evidence_and_reports_degradation(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="list-health"))
    store = RunStore(tmp_path / "home")
    healthy = _start(store, package, "healthy")
    corrupt = _start(store, package, "corrupt")
    directory = store.run_directory(corrupt.run_id)
    (directory / "run.json").write_text("{broken", encoding="utf-8")
    (directory / "events.jsonl").write_text("{broken\n", encoding="utf-8")

    listed = store.list_runs(workflow="list-health")

    by_id = {run["run_id"]: run for run in listed}
    assert by_id[healthy.run_id]["health"] == "healthy"
    assert by_id[corrupt.run_id]["health"] == "storage_degraded"
    assert by_id[corrupt.run_id]["status_authoritative"] is False
    assert by_id[corrupt.run_id]["next_actions"] == []
    assert by_id[corrupt.run_id]["blocking_reason"] == (
        "run_evidence_uncorroborated"
    )
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert store._active_run_repair_reasons(corrupt.run_id) == (
        "run_evidence_uncorroborated",
    )

    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET status='failed' WHERE run_id=?", (corrupt.run_id,)
        )
    running = {run["run_id"]: run for run in store.list_runs(status="running")}
    assert healthy.run_id in running
    assert running[corrupt.run_id]["health"] == "storage_degraded"
    unrelated = _start(store, package, "unrelated-admission")
    assert unrelated.run_id


def test_status_drift_is_resynchronized_on_same_process_load(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="status-drift")
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "status-drift")
    terminal = RunScheduler(store).advance(admitted.run_id)
    assert terminal["status"] == "succeeded"
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET status='running', desired_status='cancelled', "
            "execution_mode='background', queue_position=7, "
            "blocked_by_run_id='stale-blocker', projection_state_version=0 "
            "WHERE run_id=?",
            (admitted.run_id,),
        )

    loaded = store.load_run(admitted.run_id)

    assert loaded == terminal
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, desired_status, execution_mode, queue_position, "
            "blocked_by_run_id, projection_state_version FROM runs WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()
    assert tuple(row) == (
        "succeeded",
        terminal.get("desired_status"),
        terminal["execution_mode"],
        terminal.get("queue_position"),
        terminal.get("blocked_by_run_id"),
        terminal["state_version"],
    )


def test_new_journal_frames_have_content_checksums(tmp_path, workflow_writer) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="checksums"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "checksums")
    store.append_event(admitted.run_id, "semantic_progress", {"step": 1})
    lines = store.run_directory(admitted.run_id).joinpath("events.jsonl").read_text()
    events = [json.loads(line) for line in lines.splitlines()]

    assert all(event["schema_version"] == 2 for event in events)
    assert all(event["frame_version"] == 1 for event in events)
    assert all(len(event["frame_sha256"]) == 64 for event in events)


def test_complete_frame_checksum_corruption_fails_closed_without_tail_truncation(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="bad-frame"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "bad-frame")
    store.append_event(admitted.run_id, "semantic_progress", {"step": 1})
    directory = store.run_directory(admitted.run_id)
    journal = directory / "events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[-1]["payload"] = {"step": "tampered"}
    tampered = "".join(json.dumps(event) + "\n" for event in events)
    journal.write_text(tampered, encoding="utf-8")

    with pytest.raises(JournalRecoveryError, match="frame checksum mismatch"):
        store.load_run(admitted.run_id)

    assert journal.read_text() == tampered
    assert not list(directory.glob("events.jsonl.torn-*"))
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert store._active_run_repair_reasons(admitted.run_id) == (
        "run_evidence_uncorroborated",
    )


def test_middle_frame_corruption_cannot_replace_the_index_integrity_baseline(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="bad-middle"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "bad-middle")
    store.append_event(admitted.run_id, "semantic_progress", {"step": 1})
    store.append_event(admitted.run_id, "semantic_progress", {"step": 2})
    directory = store.run_directory(admitted.run_id)
    journal = directory / "events.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[-2]["payload"] = {"step": "tampered"}
    tampered = "".join(json.dumps(event) + "\n" for event in events)
    journal.write_text(tampered, encoding="utf-8")
    with store._connect() as connection:
        trusted_digest = connection.execute(
            "SELECT journal_sha256 FROM runs WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0]

    with pytest.raises(JournalRecoveryError, match="frame checksum mismatch"):
        store.load_run(admitted.run_id)

    with store._connect() as connection:
        assert connection.execute(
            "SELECT journal_sha256 FROM runs WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0] == trusted_digest
    assert store.storage_health()["status"] == "repair_required"


def test_fsync_interruption_after_complete_frame_replays_durable_journal_head(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="fsync-fault"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "fsync-fault")
    before = store.load_run(admitted.run_id)
    original_fsync = store_module.os.fsync
    calls = 0

    def interrupted_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated fsync interruption")
        return original_fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(store_module.os, "fsync", interrupted_fsync)
        with pytest.raises(OSError, match="fsync interruption"):
            store.append_event(admitted.run_id, "semantic_progress", {"step": 1})

    recovered = store.load_run(admitted.run_id)
    assert recovered["event_sequence"] == before["event_sequence"] + 1
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == "semantic_progress"


def test_run_directory_publish_retries_transient_replace_sharing_violation(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="replace-retry"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    original_replace = store_module.os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        if Path(source) == prepared.staging_directory and attempts < 2:
            attempts += 1
            raise PermissionError("simulated Windows sharing violation")
        return original_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", flaky_replace)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="replace-retry",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="replace-retry",
            concurrency_key="replace-retry",
        ),
        immutable_snapshot=prepared,
    )

    assert attempts == 2
    assert store.load_run(admitted.run_id)["status"] == "running"


def test_evidence_corroboration_waits_for_the_run_writer_lock(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="locked-read"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "locked-read")
    directory = store.run_directory(admitted.run_id)
    acquired = threading.Event()
    release = threading.Event()

    def hold_writer_lock():
        from plugins.workflow.locks import workflow_lock

        with workflow_lock(store._run_lock_path(admitted.run_id)):
            acquired.set()
            assert release.wait(timeout=2)

    owner = threading.Thread(target=hold_writer_lock)
    owner.start()
    assert acquired.wait(timeout=1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            corroboration = pool.submit(
                store._corroborate_run_evidence,
                directory,
                run_id=admitted.run_id,
            )
            time.sleep(0.05)
            assert not corroboration.done()
            release.set()
            projection, _, _ = corroboration.result(timeout=2)
    finally:
        release.set()
    owner.join(timeout=2)
    assert projection["run_id"] == admitted.run_id
    assert not owner.is_alive()


def test_healthy_load_validates_only_tail_and_does_not_rewrite_integrity_row(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="read-bound"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "read-bound")
    for sequence in range(20):
        store.append_event(admitted.run_id, "semantic_progress", {"step": sequence})
    store.load_run(admitted.run_id)
    with store._connect() as connection:
        verified_at = connection.execute(
            "SELECT integrity_verified_at FROM runs WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0]
    original_validate = store._validate_journal_frame
    validated = []

    def count_validation(event, *, line_number):
        validated.append(line_number)
        return original_validate(event, line_number=line_number)

    monkeypatch.setattr(store, "_validate_journal_frame", count_validation)
    store.load_run(admitted.run_id)

    with store._connect() as connection:
        unchanged = connection.execute(
            "SELECT integrity_verified_at FROM runs WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0]
    assert validated == [21]
    assert unchanged == verified_at


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed"])
def test_terminal_reserve_survives_exhausted_ordinary_journal_quota(
    tmp_path, workflow_writer, terminal_status
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name=f"terminal-{terminal_status}")
    )
    store = RunStore(tmp_path / "home", max_journal_bytes=192 * 1024)
    admitted = _start(store, package, f"terminal-{terminal_status}")
    claim = store.claim_node(admitted.run_id, "start", "quota-worker")
    assert claim is not None
    store.mark_node_started(claim)

    completed_iterations = 0
    for iteration in range(1, 500):
        try:
            store.record_loop_iteration(
                claim,
                artifacts=(),
                loop_state={"iteration": iteration, "padding": "x" * 2_000},
            )
        except StorageQuotaError:
            break
        completed_iterations = iteration
    else:  # pragma: no cover - the bounded quota must stop ordinary frames
        raise AssertionError("ordinary journal frames did not exhaust their budget")
    assert completed_iterations > 0

    store.complete_node(
        claim,
        status=terminal_status,
        error_code="quota_probe" if terminal_status == "failed" else None,
    )

    projection = store.load_run(admitted.run_id)
    assert projection["status"] == terminal_status
    assert projection["nodes"]["start"]["state"] == terminal_status
    events = store.tail_events(admitted.run_id)
    assert events[-2]["event_type"] == f"node_{terminal_status}"
    assert events[-1]["event_type"] == f"run_{terminal_status}"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM attempt_journal_reserves WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 0


def test_terminal_reserve_failure_retains_claim_and_records_sqlite_repair_state(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="terminal-repair")
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "terminal-repair")
    claim = store.claim_node(admitted.run_id, "start", "repair-worker")
    assert claim is not None
    store.mark_node_started(claim)
    original_append = store._append_locked

    def fail_terminal_append(directory, projection, event_type, *args, **kwargs):
        if event_type == "node_succeeded":
            raise StorageQuotaError("simulated terminal reserve exhaustion")
        return original_append(directory, projection, event_type, *args, **kwargs)

    monkeypatch.setattr(store, "_append_locked", fail_terminal_append)
    with pytest.raises(StorageQuotaError, match="terminal reserve exhaustion"):
        store.complete_node(claim, status="succeeded")

    projection = store.load_run(admitted.run_id)
    assert projection["status"] == "running"
    assert projection["nodes"]["start"]["state"] == "running"
    assert store.claim_node(admitted.run_id, "start", "second-worker") is None
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 1
        repair = connection.execute(
            "SELECT reason_code FROM store_repair_state "
            "WHERE run_id=? AND attempt_id=?",
            (admitted.run_id, claim.attempt_id),
        ).fetchone()
    assert repair is not None
    assert repair["reason_code"] == "terminal_journal_reserve_exhausted"
    store.repair_marker.unlink()
    assert {
        reason["reason_code"] for reason in store.storage_health()["reasons"]
    } == {"terminal_journal_reserve_exhausted"}


def test_process_stop_keeps_active_claim_until_terminal_evidence_is_indexed(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="process-stop-terminal-order")
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, "process-stop-terminal-order")
    claim = store.claim_node(admitted.run_id, "start", "process-worker")
    assert claim is not None
    store.mark_node_started(claim)
    identity = ProcessIdentity(pid=999_989, start_time=91_234, group_id=999_989)
    assert store.record_process_started(claim, identity)

    assert store.record_process_stopped(claim, identity, cleaned=True)
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM attempt_journal_reserves WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 1

    store.complete_node(claim, status="succeeded")
    assert store.load_run(admitted.run_id)["status"] == "succeeded"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("side", ["before", "after"])
def test_typed_mirror_index_replace_crash_is_idempotently_recoverable(
    tmp_path, monkeypatch, side
) -> None:
    mirror_store_type = getattr(sessions_module, "TypedMirrorStore", None)
    obligation_type = getattr(sessions_module, "TypedMirrorObligation", None)
    assert mirror_store_type is not None
    assert obligation_type is not None
    data = b"mirror crash bytes"
    digest = hashlib.sha256(data).hexdigest()
    obligation = obligation_type(
        mirror_id=hashlib.sha256(b"mirror-obligation").hexdigest(),
        workflow="workflow",
        node_id="node",
        operator_scope="scope",
        run_id="run",
        attempt_id="attempt",
        publication_id="a" * 32,
        content_name="content.md",
        output_type="Report",
        media_type="text/markdown; charset=utf-8",
        size_bytes=len(data),
        sha256=digest,
    )
    mirrors = mirror_store_type(tmp_path / "home")
    original = sessions_module._atomic_bytes_at
    index_name = mirrors._scope_id("workflow", "node", "scope") + ".json"
    armed = True

    def fail_index(directory, name, payload):
        nonlocal armed
        if name == index_name and armed:
            armed = False
            if side == "before":
                raise OSError("index replace crash")
            original(directory, name, payload)
            raise OSError("index replace crash")
        return original(directory, name, payload)

    monkeypatch.setattr(sessions_module, "_atomic_bytes_at", fail_index)
    with pytest.raises(OSError, match="index replace crash"):
        mirrors.complete(obligation, data)
    monkeypatch.setattr(sessions_module, "_atomic_bytes_at", original)

    recovered = mirrors.complete(obligation, data)
    repeated = mirrors.complete(obligation, data)

    assert repeated == recovered
    assert mirrors.get("workflow", "node", "scope") == recovered
    assert len(mirrors.list_history("workflow", "node", "scope")) == 1


def test_hundred_duplicate_deliveries_publish_exactly_one_run(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="duplicate-stress")
    )
    store = RunStore(tmp_path / "home", max_start_requests_per_minute=200)
    prepared = store.prepare_run_snapshot(package)

    def start(_index: int):
        snapshot = store.prepare_run_snapshot(package)
        return store.start_run(_request(snapshot, "same-source-key"), immutable_snapshot=snapshot)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(start, range(100)))

    run_ids = {result.run_id for result in results}
    assert len(run_ids) == 1
    assert sum(result.disposition == "created" for result in results) == 1
    assert all(result.disposition in {"created", "existing"} for result in results)
    assert prepared.staging_directory.exists()


def test_twenty_simultaneous_approval_decisions_have_one_durable_winner(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "approval",
            name="approval-race",
            nodes=[{"id": "review", "approval": {"message": "Review"}}],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    request = RunAdmissionRequest(
        workflow_name="approval-race",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="cli",
        idempotency_key="approval-race",
        concurrency_key="approval-race",
    )
    run_id = store.start_run(request, immutable_snapshot=prepared).run_id
    assert run_id
    from plugins.workflow.scheduler import RunScheduler

    RunScheduler(store).advance(run_id)
    paused = store.load_run(run_id)
    interaction = paused["nodes"]["review"]["pending_interaction"]["interaction_id"]

    def decide(_index: int) -> str:
        try:
            result = store.approve_run(run_id, interaction_id=interaction)
            return result.outcome
        except (RuntimeError, ValueError):
            return "stale"

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(decide, range(20)))
    assert outcomes.count("applied") == 1
    assert store.load_run(run_id)["status"] == "running"
