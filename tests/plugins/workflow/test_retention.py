from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.locks import workflow_lock
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.notifications import NotificationOutbox


def _terminal_run(store, tmp_path, workflow_writer, *, name: str):
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
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
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
