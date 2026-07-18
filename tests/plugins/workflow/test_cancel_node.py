from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.cancel import CancelExecutor
from plugins.workflow.models import WorkflowNode, freeze_value
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ManagedProcessTree, ProcessIdentity, TerminationPolicy


def _start(store: RunStore, package, *, key: str = "cancel-test"):
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
        ),
        immutable_snapshot=prepared,
    )


def test_cancel_executor_returns_typed_reason_without_allocating_process(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=WorkflowNode(
            id="stop",
            node_type="cancel",
            value="Unsafe branch",
            depends_on=(),
            source_index=0,
            source_line=1,
            options=freeze_value({}),
        ),
        attempt_id="attempt-1",
    )

    result = CancelExecutor().execute(context)

    assert result.status == "cancelled"
    assert result.error_code == "cancel_node"
    assert result.error_message == "Unsafe branch"
    assert not (run_directory / "nodes").exists()


def test_cancel_node_cancels_pending_downstream_without_starting_it(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="guarded-cancel",
            nodes=[
                {"id": "stop", "cancel": "Guard refused execution"},
                {
                    "id": "after",
                    "bash": f"touch {marker}",
                    "depends_on": ["stop"],
                    "trigger_rule": "all_done",
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "cancelled"
    assert result["nodes"]["stop"]["state"] == "cancelled"
    assert result["nodes"]["after"]["state"] == "cancelled"
    assert result["nodes"]["stop"]["attempts"][-1]["error_message"] == (
        "Guard refused execution"
    )
    assert not marker.exists()


def test_cancel_is_idempotent_and_reports_whether_completion_won(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="idempotent"))
    store = RunStore(tmp_path / "home")
    cancelled_run = _start(store, package, key="cancelled-first")

    first = store.cancel_run(cancelled_run.run_id)
    second = store.cancel_run(cancelled_run.run_id)

    assert first["status"] == "cancelled"
    assert first["cancellation_outcome"] == "cancelled"
    assert second["cancellation_outcome"] == "already_terminal"
    completed_run = _start(store, package, key="completed-first")
    RunScheduler(store).advance(completed_run.run_id)

    late = store.cancel_run(completed_run.run_id)

    assert late["status"] == "succeeded"
    assert late["cancellation_outcome"] == "already_terminal"


def test_cancelled_claim_rejects_late_success_and_releases_worker_capacity(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="late-result"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)

    store.cancel_run(admitted.run_id)

    with pytest.raises(RuntimeError, match="stale"):
        store.complete_node(claim, status="succeeded")
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


def test_cancelled_retry_never_wakes_or_allocates_a_worker(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="retry-cancel"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.schedule_retry(
        claim,
        next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
        error_code="network_error",
    )

    store.cancel_run(admitted.run_id)

    assert (
        store.wake_due_retries(
            admitted.run_id, now=datetime.now(timezone.utc) + timedelta(days=1)
        )
        == ()
    )
    assert RunScheduler(store).advance(admitted.run_id)["status"] == "cancelled"
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


def test_cancel_of_unknown_outward_outcome_requires_reconciliation(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="outward"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="paused",
        error_code="unknown_side_effect",
        metadata={"pending_interaction": "reconcile"},
    )

    result = store.cancel_run(admitted.run_id)

    assert result["status"] == "paused"
    assert result["cancellation_outcome"] == "reconciliation_required"
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == (
        "cancel_reconciliation_required"
    )


def test_cancel_running_outward_attempt_stops_process_but_preserves_uncertainty(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="active-outward"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "worker",
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None
    store.mark_node_started(claim)
    identity = ProcessIdentity(pid=999_996, start_time=56789, group_id=999_996)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    terminated = []
    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        classmethod(
            lambda cls, candidate, **kwargs: terminated.append(candidate) or True
        ),
    )

    result = store.cancel_run(admitted.run_id)

    assert terminated == [identity]
    assert result["status"] == "paused"
    assert result["cancellation_outcome"] == "reconciliation_required"
    node = store.load_run(admitted.run_id)["nodes"]["start"]
    assert node["pending_interaction"]["type"] == "reconcile"
    assert node["recovery"]["termination_confirmed"] is True
    assert node["attempts"][-1]["process_stop"]["cleaned"] is True


def test_cancelled_queued_run_never_starts_a_process(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "queued-must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="queued-cancel",
            nodes=[{"id": "work", "bash": f"touch {marker}"}],
        )
    )
    store = RunStore(tmp_path / "home")
    _first = _start(store, package, key="first")
    queued = _start(store, package, key="queued")
    assert queued.disposition == "queued"

    store.cancel_run(queued.run_id)
    result = RunScheduler(store).advance(queued.run_id)

    assert result["status"] == "cancelled"
    assert not marker.exists()


def test_cancelled_paused_loop_releases_capacity_without_spawning(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="paused-cancel"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="paused",
        metadata={"pending_interaction": {"type": "loop_input", "message": "review"}},
    )

    result = store.cancel_run(admitted.run_id)

    assert result["status"] == "cancelled"
    assert result["nodes"]["start"]["state"] == "cancelled"
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


@pytest.mark.live_system_guard_bypass
def test_cancel_running_script_reaps_its_spawned_descendant(
    tmp_path: Path, workflow_writer
) -> None:
    pid_file = tmp_path / "child.pid"
    source = (
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="script-cancel",
            nodes=[{"id": "work", "script": source, "runtime": "uv"}],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    scheduler = RunScheduler(
        store,
        cooperative_shutdown_seconds=0.05,
        term_grace_seconds=0.2,
        kill_reap_grace_seconds=0.2,
    )
    worker = threading.Thread(target=scheduler.advance, args=(admitted.run_id,))
    worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.01)
    assert pid_file.exists()

    store.cancel_run(admitted.run_id)
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert store.load_run(admitted.run_id)["status"] == "cancelled"
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            import psutil

            child = psutil.Process(child_pid)
            if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"script descendant {child_pid} survived cancellation")


def test_cancel_during_ai_loop_iteration_prevents_next_iteration(
    tmp_path: Path, workflow_writer
) -> None:
    entered = threading.Event()

    class BlockingRunner:
        def __init__(self) -> None:
            self.requests = 0

        def run(self, request, *, is_cancelled=None):
            self.requests += 1
            entered.set()
            deadline = time.monotonic() + 3
            while not is_cancelled() and time.monotonic() < deadline:
                time.sleep(0.01)
            return PluginAgentRunResult(
                final_response="",
                session_id="session",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "cancelled"},
            )

    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="loop-cancel",
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Work",
                        "until": "DONE",
                        "max_iterations": 3,
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    runner = BlockingRunner()
    worker = threading.Thread(
        target=RunScheduler(store, agent_runner=runner).advance,
        args=(admitted.run_id,),
    )
    worker.start()
    assert entered.wait(timeout=2)

    store.cancel_run(admitted.run_id)
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert runner.requests == 1
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


def test_restart_cancel_reaps_a_durably_recorded_process_identity(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    package = load_workflow(workflow_writer(tmp_path / "package", name="orphan-cancel"))
    store = RunStore(home)
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "lost-coordinator")
    assert claim is not None
    store.mark_node_started(claim)
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        policy=TerminationPolicy(
            term_grace_seconds=0.2,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=0.2,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert store.record_process_started(claim, tree.identity)

    restarted = RunStore(home)
    result = restarted.cancel_run(admitted.run_id)
    tree.process.wait(timeout=2)

    assert result["status"] == "cancelled"
    assert not tree.identity.is_current()
    events = restarted.tail_events(admitted.run_id)
    assert any(event["event_type"] == "process_reaped" for event in events)


def test_uninterruptible_recorded_process_reports_cleanup_failed_and_blocks_work(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="stuck-cleanup"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    identity = ProcessIdentity(pid=999_999, start_time=None, group_id=999_999)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        classmethod(lambda cls, identity, **kwargs: False),
    )
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)

    result = store.cancel_run(admitted.run_id)
    duplicate = _start(store, package, key="blocked-by-cleanup")

    assert result["status"] == "running"
    assert result["cancellation_outcome"] == "cleanup_failed"
    assert result["desired_status"] == "cancelled"
    assert duplicate.disposition == "queued"
    events = store.tail_events(admitted.run_id)
    assert any(event["event_type"] == "cleanup_failed" for event in events)
    assert not any(event["event_type"] == "process_reaped" for event in events)
