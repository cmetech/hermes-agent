from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow
from plugins.workflow.store import (
    ArtifactRef,
    JournalRecoveryError,
    RunStore,
    TypedPublicationCandidate,
)
from plugins.workflow.notifications import NotificationOutbox


def _terminal_run(store, tmp_path, workflow_writer, *, name: str, scope=None):
    package = load_workflow(workflow_writer(tmp_path / name, name=name))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
            operator_scope=scope,
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    return admitted.run_id


def _terminal_background_run(store, tmp_path, workflow_writer, *, name: str):
    now = datetime.now(timezone.utc)
    identity = CoordinatorIdentity(
        owner_id=f"{name}-coordinator",
        host_kind="web",
        host_instance_id=f"{name}-coordinator",
        pid=1,
        process_start_time=None,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity, now=now, lease_seconds=60
    )
    assert leadership.is_leader
    package = load_workflow(workflow_writer(tmp_path / name, name=name))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=name,
            concurrency_key=name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(
        store,
        owner_id=f"coordinator:{identity.owner_id}:{leadership.lease.epoch}",
        execution_fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    ).advance(admitted.run_id)
    return admitted.run_id


def test_archive_is_reversible_visibility_metadata_not_execution_state(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    run_id = _terminal_run(store, tmp_path, workflow_writer, name="archive-demo")
    current = store.get_run_status(run_id)

    archived = store.archive_run(
        run_id, expected_state_version=current["state_version"]
    )

    assert archived["status"] == "succeeded"
    assert archived["archived_at"]
    assert archived["archive_version"] == 1
    assert archived["next_actions"] == ["status", "events", "restore"]
    restored = store.restore_run(
        run_id, expected_state_version=archived["state_version"]
    )
    assert restored["status"] == "succeeded"
    assert restored["archived_at"] is None
    assert restored["restored_to_history"] is True
    assert restored["archive_version"] == 2
    assert restored["next_actions"] == ["status", "events", "archive"]


def test_typed_publication_bundle_is_one_archive_restore_and_cleanup_unit(
    tmp_path, workflow_writer
) -> None:
    root = tmp_path / "typed-retention"
    workflow = workflow_writer(
        root,
        name="typed-retention",
        nodes=[{"id": "report", "bash": "true", "output_type": "Report"}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="typed-retention",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "report", "owner")
    assert claim is not None
    data = b"retained together"
    source = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / "output.md"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(data)
    relative = source.relative_to(store.run_directory(admitted.run_id)).as_posix()
    digest = hashlib.sha256(data).hexdigest()
    artifact = ArtifactRef(
        relative,
        "text/markdown; charset=utf-8",
        len(data),
        digest,
    )
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(artifact,),
        typed_publication=TypedPublicationCandidate(
            attempt_relative_path=relative,
            output_type="Report",
            media_type="text/markdown; charset=utf-8",
            size_bytes=len(data),
            sha256=digest,
            schema_fingerprint=None,
            canonicalization_version=1,
            session_id=None,
        ),
    )
    current = store.load_run(admitted.run_id)
    publication = next(
        entry for entry in current["artifacts"] if "publication_id" in entry
    )
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / publication["publication_id"]
    )

    archived = store.archive_run(
        admitted.run_id,
        expected_state_version=current["state_version"],
    )
    assert sorted(path.name for path in bundle.iterdir()) == [
        "content.md",
        "metadata.json",
    ]
    store.restore_run(
        admitted.run_id,
        expected_state_version=archived["state_version"],
    )
    assert sorted(path.name for path in bundle.iterdir()) == [
        "content.md",
        "metadata.json",
    ]

    preview = store.cleanup_runs(older_than=timedelta(0))
    executed = store.cleanup_runs(
        execute=True,
        confirmation_token=preview["confirmation_token"],
    )
    quarantined_bundle = (
        Path(executed["quarantine_paths"][0])
        / "publications"
        / publication["publication_id"]
    )
    assert sorted(path.name for path in quarantined_bundle.iterdir()) == [
        "content.md",
        "metadata.json",
    ]


def test_board_history_archive_views_use_utc_injectable_clock_and_exact_boundary(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    boundary_id = _terminal_run(store, tmp_path, workflow_writer, name="boundary")
    old_id = _terminal_run(store, tmp_path, workflow_writer, name="old")
    restored_id = _terminal_run(store, tmp_path, workflow_writer, name="restored")
    archived_id = _terminal_run(store, tmp_path, workflow_writer, name="archived")
    now = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    boundary = now - timedelta(days=7)
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET updated_at=? WHERE run_id=?",
            (boundary.isoformat(), boundary_id),
        )
        connection.execute(
            "UPDATE runs SET updated_at=? WHERE run_id=?",
            ((boundary - timedelta(microseconds=1)).isoformat(), old_id),
        )
    restored = store.get_run_status(restored_id)
    store.archive_run(restored_id, expected_state_version=restored["state_version"])
    archived = store.get_run_status(restored_id)
    store.restore_run(restored_id, expected_state_version=archived["state_version"])
    archived = store.get_run_status(archived_id)
    store.archive_run(archived_id, expected_state_version=archived["state_version"])

    board = {run["run_id"] for run in store.list_runs(view="board", now=now)}
    history = {run["run_id"] for run in store.list_runs(view="history", now=now)}
    archive = {run["run_id"] for run in store.list_runs(view="archive", now=now)}

    assert boundary_id in board
    assert old_id in history
    assert restored_id in history and restored_id not in board
    assert archived_id in archive and archived_id not in board
    with pytest.raises(ValueError, match="timezone-aware"):
        store.list_runs(view="board", now=datetime(2026, 1, 1))


def test_archive_rejects_nonterminal_and_stale_state(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "active", name="active"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="active",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="active",
            concurrency_key="active",
        ),
        immutable_snapshot=prepared,
    )
    with pytest.raises(ValueError, match="terminal"):
        store.archive_run(admitted.run_id, expected_state_version=1)

    run_id = _terminal_run(store, tmp_path, workflow_writer, name="stale-archive")
    with pytest.raises(RuntimeError, match="state version"):
        store.archive_run(run_id, expected_state_version=1)


def test_cleanup_is_dry_run_then_quarantines_terminal_run(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="cleanup-demo"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="cleanup-demo",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="cleanup",
            concurrency_key="cleanup-demo",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    preview = store.cleanup_runs(older_than=timedelta(0))
    assert preview["run_ids"] == [admitted.run_id]
    assert preview["execute"] is False
    assert preview["confirmation_token"]
    assert store.run_directory(admitted.run_id).exists()

    with pytest.raises(ValueError, match="confirmation token"):
        store.cleanup_runs(older_than=timedelta(0), execute=True)
    deleted = store.cleanup_runs(
        older_than=timedelta(0),
        execute=True,
        confirmation_token=preview["confirmation_token"],
    )
    assert deleted["run_ids"] == [admitted.run_id]
    assert deleted["execute"] is True
    assert deleted["files"] == preview["files"]
    assert store.list_runs() == ()
    history = store.list_cleanup_history()
    assert history[-1]["run_id"] == admitted.run_id
    assert history[-1]["outcome"] == "quarantined"
    assert history[-1]["quarantine_path"]
    assert Path(history[-1]["quarantine_path"]).is_dir()


def test_cleanup_preview_blocks_on_live_reader_and_requires_fresh_preview(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="race-cleanup"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="race-cleanup",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="race-cleanup",
            concurrency_key="race-cleanup",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    entered = threading.Event()
    release = threading.Event()

    def hold_reader_lock():
        with workflow_lock(store._run_lock_path(admitted.run_id)):
            entered.set()
            assert release.wait(timeout=3)

    reader = threading.Thread(target=hold_reader_lock)
    reader.start()
    assert entered.wait(timeout=2)
    blocked = store.cleanup_runs(older_than=timedelta(0))
    assert blocked["confirmation_token"] is None
    assert blocked["candidates"][0]["blocked_reasons"] == ["active_reader_or_writer"]
    release.set()
    reader.join(timeout=3)

    preview = store.cleanup_runs(older_than=timedelta(0))
    assert preview["confirmation_token"]
    result = store.cleanup_runs(
        older_than=timedelta(0),
        execute=True,
        confirmation_token=preview["confirmation_token"],
    )
    assert result["run_ids"] == [admitted.run_id]


def test_cleanup_rejects_changed_preview_and_preserves_evidence(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="stale-cleanup"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="stale-cleanup",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="stale-cleanup",
            concurrency_key="stale-cleanup",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    preview = store.cleanup_runs(older_than=timedelta(0))
    store.append_event(admitted.run_id, "evidence_annotation", {"note": "changed"})

    with pytest.raises(RuntimeError, match="cleanup preview changed"):
        store.cleanup_runs(
            older_than=timedelta(0),
            execute=True,
            confirmation_token=preview["confirmation_token"],
        )

    assert store.run_directory(admitted.run_id).is_dir()


def test_cleanup_fails_closed_when_storage_repair_is_required(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="repair-cleanup"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="repair-cleanup",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="repair-cleanup",
            concurrency_key="repair-cleanup",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    store._mark_repair_required("test_index_uncertain")

    preview = store.cleanup_runs(older_than=timedelta(0))

    assert preview["index_integrity"] == "repair_required"
    assert preview["confirmation_token"] is None
    assert preview["blocked_reasons"] == ["storage_repair_required"]


def test_cleanup_token_expires_without_removing_evidence(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="expired-cleanup"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="expired-cleanup",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="expired-cleanup",
            concurrency_key="expired-cleanup",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    preview = store.cleanup_runs(older_than=timedelta(0))
    token = preview["confirmation_token"]
    with store._connect() as connection:
        connection.execute(
            "UPDATE cleanup_previews SET expires_at='2000-01-01T00:00:00+00:00'"
        )

    with pytest.raises(ValueError, match="expired"):
        store.cleanup_runs(execute=True, confirmation_token=token)

    assert store.run_directory(admitted.run_id).is_dir()


def test_cleanup_preview_blocks_live_claim_and_reconciliation(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="blocked-cleanup"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="blocked-cleanup",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="blocked-cleanup",
            concurrency_key="blocked-cleanup",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    projection = store.load_run(admitted.run_id)
    node_id = next(iter(projection["nodes"]))
    projection["nodes"][node_id]["pending_interaction"] = {
        "type": "reconcile",
        "interaction_id": "uncertain-effect",
    }
    store.append_event(
        admitted.run_id,
        "reconciliation_required",
        projection_updates={"nodes": projection["nodes"]},
    )
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO worker_claims ("
            "attempt_id, run_id, node_id, owner_id, lease_expires_at"
            ") VALUES ('stale-attempt', ?, ?, 'stale-owner', "
            "'2099-01-01T00:00:00+00:00')",
            (admitted.run_id, node_id),
        )

    preview = store.cleanup_runs(older_than=timedelta(0))

    assert preview["confirmation_token"] is None
    assert preview["candidates"][0]["blocked_reasons"] == [
        "live_worker_claim",
        "reconciliation_required",
    ]


def test_cleanup_preview_blocks_pending_notification_dependency(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    run_id = _terminal_run(store, tmp_path, workflow_writer, name="notify-cleanup")
    # Terminal completion creates this automatically; the explicit record also
    # makes the dependency contract independent of executor details.
    NotificationOutbox(store).record(
        run_id=run_id,
        kind="completion",
        destination="desktop",
        transition_version=999,
        payload={},
    )

    preview = store.cleanup_runs(older_than=timedelta(0))

    assert preview["confirmation_token"] is None
    assert "pending_notification_delivery" in preview["candidates"][0][
        "blocked_reasons"
    ]
    assert preview["notification_dependencies"]["count"] >= 1


def test_cleanup_repairs_terminal_notification_gap_before_preview(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    run_id = _terminal_background_run(
        store, tmp_path, workflow_writer, name="cleanup-notification-gap"
    )
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?", (run_id,)
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?", (run_id,)
        )

    preview = store.cleanup_runs(older_than=timedelta(0))

    assert preview["confirmation_token"] is None
    assert preview["notification_dependencies"]["count"] == 1
    assert store.run_directory(run_id).is_dir()


@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "oversized"])
def test_cleanup_preserves_run_when_notification_corroboration_fails(
    tmp_path, workflow_writer, damage
) -> None:
    store = RunStore(tmp_path / "home")
    run_id = _terminal_run(
        store, tmp_path, workflow_writer, name=f"cleanup-{damage}-journal"
    )
    journal = store.run_directory(run_id) / "events.jsonl"
    store.max_journal_bytes = journal.stat().st_size + 1024
    if damage == "missing":
        journal.unlink()
    elif damage == "empty":
        journal.write_bytes(b"")
    elif damage == "corrupt":
        journal.write_bytes(b"{not-valid-json}\n")
    else:
        with journal.open("ab") as stream:
            stream.write(b"x" * 1025)

    preview = store.cleanup_runs(older_than=timedelta(0))

    assert preview["confirmation_token"] is None
    assert (
        "notification_reconciliation_unverified"
        in preview["candidates"][0]["blocked_reasons"]
    )
    assert store.run_directory(run_id).is_dir()


def test_run_read_damage_is_contained_while_unrelated_cleanup_and_admission_work(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    damaged = _terminal_run(
        store, tmp_path, workflow_writer, name="damaged", scope="scope-damaged"
    )
    clean = _terminal_run(
        store, tmp_path, workflow_writer, name="clean", scope="scope-clean"
    )
    run_directory = store.run_directory(
        damaged, operator_scope="scope-damaged"
    )
    journal = run_directory / "events.jsonl"
    projection = run_directory / "run.json"
    original_journal = journal.read_bytes()
    original_projection = projection.read_bytes()
    frames = original_journal.splitlines(keepends=True)
    assert len(frames) > 1
    journal.write_bytes(frames[0] + b"{not-json}\n" + b"".join(frames[1:]))
    projection.unlink()
    damaged_reads = 0
    original_corroborate = store._corroborate_run_evidence

    def traced_corroborate(directory, **kwargs):
        nonlocal damaged_reads
        if Path(directory) == run_directory:
            damaged_reads += 1
        return original_corroborate(directory, **kwargs)

    monkeypatch.setattr(store, "_corroborate_run_evidence", traced_corroborate)

    package = load_workflow(
        workflow_writer(tmp_path / "new-valid", name="new-valid")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="new-valid",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="new-valid",
            concurrency_key="new-valid",
            operator_scope="scope-clean",
        ),
        immutable_snapshot=prepared,
    )

    assert admitted.run_id
    assert damaged_reads == 1
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert run_directory.is_dir()
    preserved = [
        event
        for event in store.list_repair_events()
        if event["run_id"] == damaged
        and event["reason_code"] == "published_evidence_uncorroborated"
    ]
    assert len(preserved) == 1
    assert preserved[0]["outcome"] == "evidence_preserved"

    second_package = load_workflow(
        workflow_writer(tmp_path / "new-valid-2", name="new-valid-2")
    )
    second_prepared = store.prepare_run_snapshot(second_package)
    second = store.start_run(
        RunAdmissionRequest(
            workflow_name="new-valid-2",
            definition_digest=second_prepared.definition_digest,
            policy_digest=second_prepared.policy_digest,
            input_manifest_digest=second_prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="new-valid-2",
            concurrency_key="new-valid-2",
            operator_scope="scope-clean",
        ),
        immutable_snapshot=second_prepared,
    )

    assert second.run_id
    assert damaged_reads == 1
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    with pytest.raises(JournalRecoveryError):
        store.get_run_status(damaged, operator_scope="scope-damaged")
    assert store.storage_health() == {"status": "healthy", "reasons": []}

    damaged_list = store.list_runs(operator_scope="scope-damaged")

    assert damaged_list[0]["blocking_reason"] == "run_evidence_uncorroborated"
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert not store.repair_marker.exists()
    attention = store.attention_candidates(
        operator_scope="scope-damaged",
        observed_at=datetime.now(timezone.utc),
        limit=10,
    )
    assert attention[0]["warnings"] == ["run_evidence_uncorroborated"]
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    with pytest.raises(JournalRecoveryError):
        EvidenceReader(store).query(
            damaged,
            kind="timeline",
            operator_scope="scope-damaged",
        )
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert NotificationOutbox(store).reconcile_journal(limit_runs=1) == 0
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    damaged_preview = store.cleanup_runs(
        older_than=timedelta(0), operator_scope="scope-damaged"
    )
    assert damaged_preview["confirmation_token"] is None
    assert "notification_reconciliation_unverified" in damaged_preview[
        "candidates"
    ][0]["blocked_reasons"]
    assert store.storage_health() == {"status": "healthy", "reasons": []}

    clean_preview = store.cleanup_runs(
        older_than=timedelta(0), operator_scope="scope-clean"
    )
    assert clean_preview["confirmation_token"]
    assert clean in clean_preview["run_ids"]
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    clean_directory = store.run_directory(clean, operator_scope="scope-clean")
    cleaned = store.cleanup_runs(
        older_than=timedelta(0),
        execute=True,
        confirmation_token=clean_preview["confirmation_token"],
    )
    assert cleaned["run_ids"] == [clean]
    assert not clean_directory.exists()
    assert store.storage_health() == {"status": "healthy", "reasons": []}

    journal.write_bytes(original_journal)
    projection.write_bytes(original_projection)
    restored = store.list_runs(operator_scope="scope-damaged")
    assert restored[0]["health"] != "storage_degraded"
    outbox = NotificationOutbox(store)
    outbox.reconcile_journal(limit_runs=100)
    outbox.reconcile_journal(limit_runs=100)
    assert store._active_run_repair_reasons(damaged) == ()
    assert store.storage_health() == {"status": "healthy", "reasons": []}


def test_prune_preserves_facts_until_explicit_workflow_cleanup(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    run_id = _terminal_run(store, tmp_path, workflow_writer, name="prune-cleanup")
    outbox = NotificationOutbox(store)
    delivered_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    notification_id = outbox.record(
        run_id=run_id,
        kind="failure",
        destination="desktop",
        transition_version=999,
        payload={},
        now=delivered_at,
    )
    assert outbox.lease(
        destination="desktop",
        owner_id="electron",
        now=delivered_at,
        limit=1,
    )
    assert outbox.ack(notification_id, owner_id="electron", now=delivered_at)

    assert outbox.prune_deliveries(
        older_than=timedelta(days=30),
        authority_scope="profile-local-dashboard",
        now=delivered_at + timedelta(days=29),
    ) == 0
    assert outbox.prune_deliveries(
        older_than=timedelta(days=30),
        authority_scope="profile-local-dashboard",
        now=delivered_at + timedelta(days=31),
    ) == 1
    history = outbox.history(run_id=run_id)
    prune_fact = next(
        item
        for item in history
        if item["payload"].get("decision") == "delivery_pruned"
    )
    assert prune_fact["state"] == "pruned"
    assert any(item["transition_version"] == 999 for item in history)

    preview = store.cleanup_runs(older_than=timedelta(0))
    assert preview["confirmation_token"]
    store.cleanup_runs(
        execute=True,
        confirmation_token=preview["confirmation_token"],
    )
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_facts WHERE run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_outbox WHERE run_id=?",
            (run_id,),
        ).fetchone()[0] == 0
