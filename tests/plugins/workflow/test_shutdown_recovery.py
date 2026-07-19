from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessIdentity


def _start(store, package, key="shutdown"):
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


def test_shutdown_closes_admission_interrupts_workers_and_returns_bounded(
    tmp_path, workflow_writer
):
    package = load_workflow(workflow_writer(tmp_path / "package"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    entered = threading.Event()

    class Cooperative:
        def execute(self, context):
            entered.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            return NodeExecutionResult("interrupted", error_code="shutdown")

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = Cooperative()
    worker = threading.Thread(target=scheduler.advance, args=(admitted.run_id,))
    worker.start()
    assert entered.wait(2)

    started = time.monotonic()
    scheduler.shutdown(deadline_seconds=1)
    worker.join(timeout=2)

    assert time.monotonic() - started < 1.5
    assert not worker.is_alive()
    result = store.load_run(admitted.run_id)
    assert result["status"] == "interrupted"
    assert any(
        e["event_type"] == "coordinator_shutdown"
        for e in store.tail_events(admitted.run_id)
    )

    snapshot = store.clone_prepared_snapshot(store.prepare_run_snapshot(package))
    rejected = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="after-shutdown",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=snapshot,
    )
    assert rejected.reason_code == "admission_closed"


def test_shutdown_cancellation_reaches_isolated_agent_runner(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="ai-shutdown",
            nodes=[{"id": "wait", "prompt": "wait"}],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    entered = threading.Event()

    class CancellableRunner:
        def run(self, request, *, is_cancelled=None):
            del request
            entered.set()
            while not is_cancelled():
                time.sleep(0.01)
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fake",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "cancelled"},
            )

    scheduler = RunScheduler(store, agent_runner=CancellableRunner())
    thread = threading.Thread(target=scheduler.advance, args=(admitted.run_id,))
    thread.start()
    assert entered.wait(2)

    scheduler.shutdown(deadline_seconds=2)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert store.load_run(admitted.run_id)["status"] == "interrupted"


def test_shutdown_does_not_interrupt_a_successor_epoch_claim(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="successor"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="successor")
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="successor",
        host_kind="gateway",
        host_instance_id="successor-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    acquired = CoordinatorStore(store.database).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    successor = ExecutionFence("successor", acquired.lease.epoch)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        f"coordinator:successor:{successor.owner_epoch}",
        execution_fence=successor,
    )
    assert claim is not None

    stale = RunScheduler(store, execution_fence=ExecutionFence("stale", 1))
    with stale._activity:
        stale._active_runs.add(admitted.run_id)
    before = store.load_run(admitted.run_id)["state_version"]
    stale.shutdown(deadline_seconds=1)

    projection = store.load_run(admitted.run_id)
    assert projection["state_version"] == before
    assert projection["nodes"]["start"]["claim"]["attempt_id"] == claim.attempt_id
