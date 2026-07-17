from __future__ import annotations

from datetime import timedelta
import threading

from plugins.workflow.admission import RunAdmissionRequest
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
    preview = store.cleanup_runs(older_than=timedelta(0), dry_run=True)
    assert preview["run_ids"] == [admitted.run_id]
    assert store.run_directory(admitted.run_id).exists()

    deleted = store.cleanup_runs(older_than=timedelta(0), dry_run=False)
    assert deleted["run_ids"] == [admitted.run_id]
    assert deleted["files"] == preview["files"]
    assert store.list_runs() == ()


def test_cleanup_racing_reader_does_not_recreate_run_directory(
    tmp_path, workflow_writer, monkeypatch
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
    run_directory = store.run_directory(admitted.run_id)
    entered = threading.Event()
    release = threading.Event()
    original_rmtree = __import__("shutil").rmtree

    def blocked_rmtree(path, *args, **kwargs):
        if str(path).startswith(str(store.quarantine_root)):
            entered.set()
            release.wait(timeout=3)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("plugins.workflow.store.shutil.rmtree", blocked_rmtree)
    cleanup = threading.Thread(
        target=store.cleanup_runs,
        kwargs={"older_than": timedelta(0), "dry_run": False},
    )
    cleanup.start()
    assert entered.wait(timeout=2)
    reader_result = []

    def read():
        try:
            store.load_run(admitted.run_id)
            reader_result.append("loaded")
        except KeyError:
            reader_result.append("removed")

    reader = threading.Thread(target=read)
    reader.start()
    release.set()
    cleanup.join(timeout=3)
    reader.join(timeout=3)

    assert reader_result == ["removed"]
    assert not run_directory.exists()
