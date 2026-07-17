from __future__ import annotations

import threading
import time

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


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
