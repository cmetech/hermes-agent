from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.locks import workflow_lock
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


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
