from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import JournalRecoveryError, RunStore
import plugins.workflow.store as store_module


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
    assert by_id[corrupt.run_id]["blocking_reason"] == "storage_repair_required"
    assert store.storage_health()["status"] == "repair_required"

    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET status='failed' WHERE run_id=?", (corrupt.run_id,)
        )
    running = {run["run_id"]: run for run in store.list_runs(status="running")}
    assert healthy.run_id in running
    assert running[corrupt.run_id]["health"] == "storage_degraded"
    refused = _start(store, package, "must-not-use-uncertain-capacity")
    assert refused.reason_code == "storage_repair_required"


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
    assert store.storage_health()["status"] == "repair_required"


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
