from __future__ import annotations

import subprocess
import sys
import threading
import time

import psutil
import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult, process_tree_active
from plugins.workflow import locks as workflow_locks
from plugins.workflow.locks import workflow_lock
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ManagedProcessTree


@pytest.mark.parametrize(
    ("active", "expected"),
    [(True, "still_running"), (False, "known_stopped"), (None, "outcome_uncertain")],
)
def test_windows_persisted_job_query_controls_recovery_observation(
    monkeypatch, active, expected
) -> None:
    monkeypatch.setattr(
        ManagedProcessTree,
        "existing_tree_active",
        classmethod(lambda cls, identity: active),
    )

    assert RunStore._observe_process_identity({
        "pid": 77,
        "start_time": 7,
        "group_id": 77,
        "job_name": "Local\\HermesManagedProcess-test",
    }) == expected


def test_hundred_fast_cycles_release_every_claim_and_scheduler_thread(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="soak", nodes=[{"id": "fast", "bash": "true"}])
    )
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=100,
        max_nonterminal_runs=120,
        max_start_requests_per_minute=120,
    )
    # lease_seconds=15, not 1. What this test actually asserts is leak freedom
    # over 100 cycles -- every worker_claim released, no scheduler threads left,
    # empty quarantine -- and the lease length is incidental to that. But each
    # cycle does a package directory copy plus fsync'd writes, which on Windows
    # can exceed a 1-second lease; release_or_expire_claim then expires the
    # claim mid-advance and the run lands 'interrupted' instead of 'succeeded'.
    # That failed roughly half of Windows CI runs. heartbeat_seconds stays at
    # 0.1 so the heartbeat path is still exercised ~150x per lease.
    scheduler = RunScheduler(store, heartbeat_seconds=0.1, lease_seconds=15)

    class FastExecutor:
        def execute(self, _context):
            return NodeExecutionResult("succeeded")

    scheduler.executors["bash"] = FastExecutor()
    run_ids = []
    for index in range(100):
        prepared = store.prepare_run_snapshot(package)
        request = RunAdmissionRequest(
            workflow_name="soak",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"cycle-{index}",
            concurrency_key=f"cycle-{index}",
            concurrency_policy="allow",
        )
        run_id = store.start_run(request, immutable_snapshot=prepared).run_id
        assert run_id
        run_ids.append(run_id)
        assert scheduler.advance(run_id)["status"] == "succeeded"

    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM worker_claims").fetchone()[0] == 0
    assert scheduler.active_run_count == 0
    assert list(store.quarantine_root.iterdir()) == []
    assert all(store.load_run(run_id)["status"] == "succeeded" for run_id in run_ids)


def test_ten_thousand_run_lock_paths_leave_no_unowned_lock_registry_entries(
    tmp_path,
) -> None:
    lock_root = tmp_path / "run-locks"
    for index in range(10_000):
        with workflow_lock(lock_root / f"run-{index}.lock"):
            pass

    prefix = str(lock_root.resolve())
    assert not any(key.startswith(prefix) for key in workflow_locks._process_locks)


def test_lock_registry_retains_waiter_until_the_last_owner_releases(tmp_path) -> None:
    path = tmp_path / "contended.lock"
    key = str(path.resolve())
    waiter_started = threading.Event()
    waiter_acquired = threading.Event()

    def wait_for_lock() -> None:
        waiter_started.set()
        with workflow_lock(path):
            waiter_acquired.set()

    with workflow_lock(path):
        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        assert waiter_started.wait(timeout=1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with workflow_locks._process_locks_guard:
                entry = workflow_locks._process_locks[key]
                if entry.waiters == 1:
                    break
            time.sleep(0.001)
        assert entry.owners == 1
        assert entry.waiters == 1
        assert not waiter_acquired.is_set()

    thread.join(timeout=1)
    assert waiter_acquired.is_set()
    assert not thread.is_alive()
    assert key not in workflow_locks._process_locks


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows Job Object contract")
@pytest.mark.live_system_guard_bypass
def test_windows_detached_grandchild_is_quiescent_before_store_confirmation(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="windows-job-lifecycle")
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="windows-job-lifecycle",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    grandchild_pids: list[int] = []

    class DetachedGrandchildExecutor:
        def execute(self, context):
            grandchild_code = "import time; time.sleep(60)"
            parent_code = (
                "import subprocess,sys;"
                f"p=subprocess.Popen([sys.executable,'-c',{grandchild_code!r}],"
                "creationflags=subprocess.DETACHED_PROCESS,"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL);"
                "print(p.pid,flush=True)"
            )
            tree = ManagedProcessTree.spawn(
                [sys.executable, "-c", parent_code],
                stdout=subprocess.PIPE,
            )
            assert tree.process.stdout is not None
            grandchild_pids.append(
                int(tree.process.stdout.readline().decode().strip())
            )
            assert context.process_started is not None
            assert context.process_started(tree.identity)
            tree.process.wait(timeout=5)
            assert process_tree_active(tree)
            tree.close()
            cleaned = tree.reaped and not process_tree_active(tree)
            assert context.process_stopped is not None
            context.process_stopped(tree.identity, cleaned)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = DetachedGrandchildExecutor()
    try:
        projection = scheduler.advance(admitted.run_id)
        attempt = projection["nodes"]["start"]["attempts"][0]
        assert projection["status"] == "succeeded"
        assert attempt["process_identity"]["job_name"]
        assert attempt["process_stop"]["cleaned"] is True
        assert all(not psutil.pid_exists(pid) for pid in grandchild_pids)
    finally:
        for pid in grandchild_pids:
            if psutil.pid_exists(pid):
                psutil.Process(pid).kill()
