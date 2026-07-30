from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import time

import pytest

from agent.plugin_agent import PluginAgentRunResult
import plugins.workflow.store as store_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import ArtifactRef, RunStore, TypedPublicationCandidate
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
    # daemon=True: if any assertion below fails before scheduler.shutdown()
    # runs, this thread stays in its `while not is_cancelled()` loop forever.
    # As a non-daemon thread it then blocks interpreter exit, so pytest printed
    # its summary and hung -- a 2-minute Windows job burned its full 40-minute
    # timeout with no further output. A stuck test must fail, not wedge CI.
    worker = threading.Thread(
        target=scheduler.advance, args=(admitted.run_id,), daemon=True
    )
    worker.start()
    # 30s, not 2s: this only waits for the worker to REACH the executor,
    # which means a claim plus store writes first. That is setup, not the
    # bounded-shutdown behaviour under test, and 2s is not enough on Windows.
    # The real timing assertions below are deliberately left untouched.
    assert entered.wait(30)

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
    # daemon=True for the same reason as the worker thread above.
    thread = threading.Thread(
        target=scheduler.advance, args=(admitted.run_id,), daemon=True
    )
    thread.start()
    # 30s, not 2s: this only waits for the worker to REACH the executor,
    # which means a claim plus store writes first. That is setup, not the
    # bounded-shutdown behaviour under test, and 2s is not enough on Windows.
    # The real timing assertions below are deliberately left untouched.
    assert entered.wait(30)

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


def test_restart_finishes_journaled_pending_typed_mirror_from_verified_bundle(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    root = tmp_path / "pending-mirror"
    workflow = workflow_writer(
        root,
        name="pending-mirror",
        persist_sessions=True,
        nodes=[
            {
                "id": "report",
                "prompt": "Report",
                "output_type": "Report",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, load_workflow(workflow), key="pending-mirror")
    claim = store.claim_node(admitted.run_id, "report", "owner")
    assert claim is not None
    data = b"pending mirror"
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
    mirror_store_type = getattr(store_module, "TypedMirrorStore", None)
    assert mirror_store_type is not None

    def crash_before_index(self, obligation, content):
        raise OSError("mirror completion crash")

    monkeypatch.setattr(mirror_store_type, "stage", crash_before_index)
    with pytest.raises(OSError, match="mirror completion crash"):
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
                session_id="session-1",
            ),
            metadata={
                "session_id": "session-1",
                "cache_fingerprint": "cache-1",
                "provider": "default",
            },
        )
    assert "typed_mirror_required" in {
        event["event_type"] for event in store.tail_events(admitted.run_id)
    }
    key = NodeSessionKey(
        "pending-mirror",
        "report",
        "local",
        "default",
        "default",
    )
    assert NodeSessionRegistry(home).get_mirror(key) is None

    monkeypatch.undo()
    restarted = RunStore(home)
    restarted.load_run(admitted.run_id)

    mirror = NodeSessionRegistry(home).get_mirror(key)
    assert mirror is not None
    assert mirror.run_id == admitted.run_id
    event_types = [
        event["event_type"] for event in restarted.tail_events(admitted.run_id)
    ]
    assert event_types.count("typed_mirror_required") == 1
    assert event_types.count("typed_mirror_completed") == 1


def test_restart_activates_completed_typed_mirror_after_pointer_crash(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    root = tmp_path / "completed-mirror"
    workflow = workflow_writer(
        root,
        name="completed-mirror",
        persist_sessions=True,
        nodes=[
            {
                "id": "report",
                "prompt": "Report",
                "output_type": "Report",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, load_workflow(workflow), key="completed-mirror")
    claim = store.claim_node(admitted.run_id, "report", "owner")
    assert claim is not None
    data = b"completed mirror"
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
    mirror_store_type = getattr(store_module, "TypedMirrorStore", None)
    assert mirror_store_type is not None

    def crash_before_activation(self, record):
        index_path = self.index_root / (
            self._scope_id(
                record.workflow,
                record.node_id,
                record.operator_scope,
            )
            + ".json"
        )
        assert json.loads(index_path.read_bytes())["entry_id"] == record.entry_id
        assert store.tail_events(admitted.run_id)[-1]["event_type"] == (
            "typed_mirror_completed"
        )
        raise OSError("mirror activation crash")

    monkeypatch.setattr(mirror_store_type, "verify", crash_before_activation)
    with pytest.raises(OSError, match="mirror activation crash"):
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
                session_id="session-1",
            ),
            metadata={
                "session_id": "session-1",
                "cache_fingerprint": "cache-1",
                "provider": "default",
            },
        )
    event_types = [
        event["event_type"] for event in store.tail_events(admitted.run_id)
    ]
    assert event_types.count("typed_mirror_required") == 1
    assert event_types.count("typed_mirror_completed") == 1
    key = NodeSessionKey(
        "completed-mirror",
        "report",
        "local",
        "default",
        "default",
    )
    assert NodeSessionRegistry(home).get_mirror(key) is None

    monkeypatch.undo()
    restarted = RunStore(home)
    restarted.load_run(admitted.run_id)

    mirror = NodeSessionRegistry(home).get_mirror(key)
    assert mirror is not None
    assert mirror.run_id == admitted.run_id
