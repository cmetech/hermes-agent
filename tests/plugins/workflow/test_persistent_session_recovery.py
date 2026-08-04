from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
import signal
import sqlite3
import sys
import threading
import time
from typing import Callable

import pytest

from agent.plugin_agent import (
    PluginAgentRunResult,
    PluginAgentRunner,
    PluginAgentSessionMissingError,
    _exchange_worker,
)
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.language_schema import compatibility_code_catalog
from plugins.workflow.models import (
    DeadlineBudget,
    ExecutionFence,
    WorkflowLanguageProfile,
    WorkflowNode,
    freeze_value,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.sanitize import public_run_projection
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.sessions import (
    NodeSessionKey,
    NodeSessionRegistry,
    PersistentSessionRecoverySelection,
    SessionRegistryUpdateCandidate,
)
from plugins.workflow.store import (
    JournalRecoveryError,
    NodeClaim,
    RunStore,
    StorageQuotaError,
)
from tools.managed_process import ManagedProcessTree, ProcessIdentity, TerminationPolicy


class _PersistentRunner:
    def __init__(self) -> None:
        self.requests = []
        self.shared_failure: BaseException | None = None
        self.fresh_failure = False
        self.before_fresh = None

    def run(self, request, **_kwargs):
        self.requests.append(request)
        if request.context_mode == "shared" and self.shared_failure is not None:
            raise self.shared_failure
        if request.context_mode == "fresh" and self.before_fresh is not None:
            self.before_fresh()
        if request.context_mode == "fresh" and self.fresh_failure:
            return PluginAgentRunResult(
                final_response="",
                session_id=None,
                provider=request.provider or "fake-provider",
                model=request.model or "fake-model",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"provider_attempts": 1, "failure_kind": "validation"},
            )
        return PluginAgentRunResult(
            final_response="ok",
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 1},
        )


def _registry_cas_process(home, ready, results, session_id) -> None:
    registry = NodeSessionRegistry(home)
    key = NodeSessionKey("process", "node", "scope", "provider", "default")
    ready.wait(timeout=10)
    results.put(
        registry.compare_and_set_or_observe(
            key,
            0,
            session_id,
            hashlib.sha256(session_id.encode()).hexdigest(),
        )
    )


def _execute_receipt_cut_coordinator(home, claim: NodeClaim, ready) -> None:
    """Pause the real parent/child protocol after start receipt, before execute."""
    store = RunStore(home)
    store.mark_node_started(claim)
    code = """
import json
import sys

request = json.loads(sys.stdin.readline())
nonce = request["provider_start_handshake"]["executor_nonce"]
print(json.dumps({"protocol_version":1,"type":"provider_ready",
                  "executor_nonce":nonce}), flush=True)
json.loads(sys.stdin.readline())
print(json.dumps({"protocol_version":1,"type":"provider_start_received",
                  "executor_nonce":nonce}), flush=True)
# Provider execution is impossible until the parent sends the next frame.
sys.stdin.readline()
"""

    def start_delivered(nonce: str) -> bool:
        recorded = store.record_provider_start_delivered(
            claim,
            executor_nonce=nonce,
        )
        ready.set()
        time.sleep(60)
        return recorded

    _exchange_worker(
        {
            "protocol_version": 1,
            "type": "run",
            "provider_start_handshake": {"required": True},
        },
        workdir=None,
        idle_timeout_seconds=120,
        wall_timeout_seconds=120,
        worker_argv=[sys.executable, "-c", code],
        spawn_intent=lambda nonce: store.record_spawn_intent(
            claim,
            executor_nonce=nonce,
        ),
        process_started=lambda identity: store.record_process_started(
            claim,
            identity,
        ),
        provider_dispatch=lambda nonce: store.record_provider_dispatch(
            claim,
            executor_nonce=nonce,
        ),
        provider_start_delivered=start_delivered,
        process_stopped=lambda identity, cleaned: store.record_process_stopped(
            claim,
            identity,
            cleaned=cleaned,
        ),
    )


def _crash_cut_coordinator(
    home,
    claim: NodeClaim,
    stage: str,
    ready,
    provider_pids,
) -> None:
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    key = NodeSessionKey(
        "crash-cuts",
        "analyze",
        "local",
        "fake-provider",
        "default",
    )
    store.mark_node_started(claim)
    if stage == "before_selection":
        ready.set()
        time.sleep(60)
        return
    record = registry.get(key)
    assert record is not None
    selection = PersistentSessionRecoverySelection(
        key=key,
        expected_generation=record.generation,
        missing_session_id=record.session_id,
        cache_fingerprint=record.cache_fingerprint,
        run_id=claim.run_id,
        attempt_id=claim.attempt_id,
    )
    assert store.record_persistent_session_recovery_selection(claim, selection)
    if stage == "after_selection_before_launch":
        ready.set()
        time.sleep(60)
        return

    nonce = "crash-cut-provider"
    assert store.record_spawn_intent(claim, executor_nonce=nonce)
    if stage == "after_spawn_intent_before_process":
        ready.set()
        time.sleep(60)
        return
    start_marker = store.root / "provider-start-authorized"
    provider_marker = store.root / "provider-started"
    code = (
        "import pathlib,time;"
        f"start=pathlib.Path({str(start_marker)!r});"
        f"started=pathlib.Path({str(provider_marker)!r});"
        "deadline=time.monotonic()+10;"
        "\nwhile not start.exists() and time.monotonic()<deadline: time.sleep(.01)"
        "\nif start.exists(): started.write_text('started')"
        "\ntime.sleep(60)"
    )
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", code],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0.05,
            term_grace_seconds=0.1,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=0.2,
        ),
    )
    provider_pids.put(tree.identity.pid)
    assert store.record_process_started(claim, tree.identity)
    if stage == "after_process_registration_before_dispatch":
        ready.set()
        time.sleep(60)
        return
    assert store.record_provider_dispatch(claim, executor_nonce=nonce)
    if stage == "after_dispatch_before_delivery":
        ready.set()
        time.sleep(60)
        return
    assert store.record_provider_start_delivered(claim, executor_nonce=nonce)
    assert store.record_provider_execute_received(claim, executor_nonce=nonce)
    assert store.record_provider_execute_released(claim, executor_nonce=nonce)
    start_marker.write_text("authorized", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not provider_marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert provider_marker.exists()
    if stage == "after_launch_before_completion":
        ready.set()
        time.sleep(60)
        return

    tree.close()
    assert store.record_process_stopped(claim, tree.identity, cleaned=tree.reaped)
    candidate = SessionRegistryUpdateCandidate(
        key=key,
        expected_generation=record.generation,
        new_session_id="fresh-session",
        cache_fingerprint=record.cache_fingerprint,
        winning_run_id=claim.run_id,
        winning_node_id=claim.node_id,
        winning_attempt_id=claim.attempt_id,
        recovery_selected=True,
    )
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": candidate.new_session_id,
            "cache_fingerprint": candidate.cache_fingerprint,
        },
        session_registry_update=candidate,
        session_registry_authority=candidate,
    )
    if stage == "after_completion_before_cas":
        ready.set()
        time.sleep(60)
        return
    outcome = registry.compare_and_set_or_observe(
        key,
        expected_generation=candidate.expected_generation,
        session_id=candidate.new_session_id,
        cache_fingerprint=candidate.cache_fingerprint,
    )
    if stage == "after_cas_before_outcome":
        ready.set()
        time.sleep(60)
        return
    store.resolve_session_registry_update(
        claim.run_id,
        candidate,
        outcome=outcome,
    )
    ready.set()
    time.sleep(60)


def _archon_package(workflow_writer, root, *, name="persistent", nodes=None):
    path = workflow_writer(
        root,
        name=name,
        persist_sessions=True,
        provider="fake-provider",
        model="fake-model",
        nodes=nodes or [{"id": "analyze", "prompt": "Analyze"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    return load_workflow(path)


def _admit(store, package, key):
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


def _run_once(store, package, runner, registry, key):
    admitted = _admit(store, package, key)
    result = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=registry,
    ).advance(admitted.run_id)
    return admitted.run_id, result


def _rewrite_latest_projection(
    store: RunStore,
    run_id: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    directory = store.run_directory(run_id)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    latest = events[-1]
    mutate(latest["projection"])
    latest["projection_sha256"] = hashlib.sha256(
        json.dumps(
            latest["projection"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    material = dict(latest)
    material.pop("frame_sha256", None)
    latest["frame_sha256"] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    (directory / "run.json").write_text("{broken", encoding="utf-8")


def _rewrite_journal_event(
    store: RunStore,
    run_id: str,
    event_type: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    directory = store.run_directory(run_id)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    matches = [event for event in events if event.get("event_type") == event_type]
    assert len(matches) == 1
    event = matches[0]
    mutate(event)
    projection = event.get("projection")
    if isinstance(projection, dict):
        event["projection_sha256"] = hashlib.sha256(
            json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    material = dict(event)
    material.pop("frame_sha256", None)
    event["frame_sha256"] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )


def _resolved_running_recovery(
    store: RunStore,
    package,
) -> tuple[str, SessionRegistryUpdateCandidate]:
    admitted = _admit(store, package, "resolved-running")
    claim = store.claim_node(
        admitted.run_id,
        "first",
        "resolved-running-owner",
        journal_reserve_bytes=2 * 1024 * 1024,
        terminal_journal_reserve_bytes=2 * 1024 * 1024,
    )
    assert claim is not None
    store.mark_node_started(claim)
    key = NodeSessionKey(
        package.definition.name,
        "first",
        "local",
        "fake-provider",
        "default",
    )
    selection = PersistentSessionRecoverySelection(
        key=key,
        expected_generation=0,
        missing_session_id="missing-running-session",
        cache_fingerprint="a" * 64,
        run_id=admitted.run_id,
        attempt_id=claim.attempt_id,
    )
    assert store.record_persistent_session_recovery_selection(claim, selection)
    candidate = SessionRegistryUpdateCandidate(
        key=key,
        expected_generation=0,
        new_session_id="fresh-running-session",
        cache_fingerprint="a" * 64,
        winning_run_id=admitted.run_id,
        winning_node_id="first",
        winning_attempt_id=claim.attempt_id,
        recovery_selected=True,
    )
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": candidate.new_session_id,
            "cache_fingerprint": candidate.cache_fingerprint,
        },
        session_registry_update=candidate,
        session_registry_authority=candidate,
    )
    store.resolve_session_registry_update(
        admitted.run_id,
        candidate,
        outcome="stale_entry_replaced",
    )
    assert store.load_run(admitted.run_id)["status"] == "running"
    return admitted.run_id, candidate


def test_confirmed_missing_cross_run_session_starts_fresh_once(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "package")
    store = RunStore(tmp_path / "home")
    registry = NodeSessionRegistry(tmp_path / "home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    _run_id, recovered = _run_once(store, package, runner, registry, "recover")

    assert recovered["status"] == "succeeded"
    assert [request.context_mode for request in runner.requests] == [
        "fresh",
        "shared",
        "fresh",
    ]


def test_same_run_shared_session_missing_fails_without_fresh_request(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / "same-run",
        name="same-run",
        nodes=[
            {"id": "first", "prompt": "First"},
            {
                "id": "second",
                "prompt": "Second",
                "depends_on": ["first"],
                "context": "shared",
            },
        ],
    )
    store = RunStore(tmp_path / "same-run-home")
    registry = NodeSessionRegistry(tmp_path / "same-run-home")
    runner = _PersistentRunner()
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")

    _run_id, result = _run_once(store, package, runner, registry, "same-run")

    assert result["status"] == "failed"
    assert result["nodes"]["second"]["attempts"][0]["error_code"] == (
        "context_missing_session"
    )
    assert [request.context_mode for request in runner.requests] == [
        "fresh",
        "shared",
    ]


def test_cross_run_session_probe_failure_is_not_treated_as_confirmed_absence(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "unavailable")
    store = RunStore(tmp_path / "unavailable-home")
    registry = NodeSessionRegistry(tmp_path / "unavailable-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = OSError("private database path")
    _run_id, result = _run_once(store, package, runner, registry, "unavailable")

    assert result["status"] == "failed"
    attempt = result["nodes"]["analyze"]["attempts"][0]
    assert attempt["error_code"] == "persistent_session_recovery_unavailable"
    assert attempt["metadata"]["provider_attempts"] == 0
    assert len(runner.requests) == 2


@pytest.mark.parametrize(
    ("boundary", "expected_status", "expected_code"),
    [
        ("deadline", "failed", "provider_timeout"),
        ("cancel", "cancelled", "cancelled"),
    ],
)
def test_recovery_reseals_authority_before_fresh_provider_launch(
    tmp_path, boundary, expected_status, expected_code
) -> None:
    registry = NodeSessionRegistry(tmp_path / "reseal-home")
    runner = _PersistentRunner()
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    node = WorkflowNode(
        id="analyze",
        node_type="prompt",
        value="Analyze",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    now = [0.0]
    cancelled = [False]

    def select_recovery(_selection) -> bool:
        if boundary == "deadline":
            now[0] = 2.0
        else:
            cancelled[0] = True
        return True

    context = NodeExecutionContext(
        run_id="run-reseal",
        run_directory=tmp_path,
        node=node,
        attempt_id="attempt-reseal",
        workflow_name="reseal",
        workflow_options=freeze_value(
            {
                "persist_sessions": True,
                "provider": "fake-provider",
                "model": "fake-model",
            }
        ),
        operator_scope="scope-digest",
        variable_context=VariableContext(
            workflow_id="run-reseal",
            normalizer_version=3,
        ),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=3,
        deadline_budget=DeadlineBudget.create(
            now=0,
            wall_seconds=1,
            idle_seconds=1,
            provider_seconds=1,
        ),
        sealed_attempt_timeout=True,
        monotonic=lambda: now[0],
        is_cancelled=lambda: cancelled[0],
        record_session_recovery_selection=select_recovery,
    )
    executor = AgentNodeExecutor(runner, session_registry=registry)
    fingerprint = executor._fingerprint(context)
    key = NodeSessionKey(
        "reseal", "analyze", "scope-digest", "fake-provider", "default"
    )
    registry.compare_and_set_or_observe(
        key,
        0,
        "missing-session",
        fingerprint,
    )

    result = executor.execute(context)

    assert result.status == expected_status
    assert result.error_code == expected_code
    assert result.metadata["provider_attempts"] == 0
    assert result.session_recovery_outcome == "fresh_execution_failed"
    assert [request.context_mode for request in runner.requests] == ["shared"]


def test_real_session_database_failure_is_recovery_unavailable_before_spawn(
    tmp_path, monkeypatch
) -> None:
    import hermes_state

    database = tmp_path / "corrupt-state.db"
    database.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", database)
    registry = NodeSessionRegistry(tmp_path / "real-db-home")
    node = WorkflowNode(
        id="analyze",
        node_type="prompt",
        value="Analyze",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    context = NodeExecutionContext(
        run_id="run-real-db",
        run_directory=tmp_path,
        node=node,
        attempt_id="attempt-real-db",
        workflow_name="real-db",
        workflow_options=freeze_value(
            {
                "persist_sessions": True,
                "provider": "fake-provider",
                "model": "fake-model",
            }
        ),
        operator_scope="scope-digest",
        variable_context=VariableContext(
            workflow_id="run-real-db",
            normalizer_version=3,
        ),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=3,
        spawn_intent=lambda _nonce: (_ for _ in ()).throw(
            AssertionError("worker spawn reached")
        ),
    )
    executor = AgentNodeExecutor(
        PluginAgentRunner("workflow"),
        session_registry=registry,
    )
    registry.compare_and_set_or_observe(
        NodeSessionKey(
            "real-db",
            "analyze",
            "scope-digest",
            "fake-provider",
            "default",
        ),
        0,
        "missing-session",
        executor._fingerprint(context),
    )

    result = executor.execute(context)

    assert result.status == "failed"
    assert result.error_code == "persistent_session_recovery_unavailable"
    assert result.metadata["provider_attempts"] == 0


def test_same_run_real_session_database_failure_is_recovery_unavailable(
    tmp_path, monkeypatch
) -> None:
    import hermes_state

    database = tmp_path / "corrupt-same-run-state.db"
    database.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", database)
    node = WorkflowNode(
        id="second",
        node_type="prompt",
        value="Continue",
        depends_on=("first",),
        source_index=1,
        source_line=2,
        options=freeze_value({"context": "shared"}),
    )
    base = dict(
        run_id="run-real-same-db",
        run_directory=tmp_path,
        node=node,
        attempt_id="attempt-real-same-db",
        workflow_name="real-same-db",
        workflow_options=freeze_value({}),
        operator_scope="scope-digest",
        variable_context=VariableContext(
            workflow_id="run-real-same-db",
            normalizer_version=3,
        ),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=3,
        spawn_intent=lambda _nonce: (_ for _ in ()).throw(
            AssertionError("worker spawn reached")
        ),
    )
    executor = AgentNodeExecutor(PluginAgentRunner("workflow"))
    fingerprint = executor._fingerprint(NodeExecutionContext(**base))
    context = NodeExecutionContext(
        **base,
        predecessor_results={
            "first": {
                "session_id": "same-run-session",
                "cache_fingerprint": fingerprint,
            }
        },
    )

    result = executor.execute(context)

    assert result.status == "failed"
    assert result.error_code == "persistent_session_recovery_unavailable"
    assert result.metadata["provider_attempts"] == 0


def test_crash_after_provider_worker_spawn_is_never_replayed_as_prelaunch(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "spawn-crash")
    store = RunStore(tmp_path / "spawn-crash-home")
    admitted = _admit(store, package, "spawn-crash")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "crashed-owner",
        executor_id="prompt",
        effect_classification="replay_safe",
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_spawn_intent(claim, executor_nonce="provider-worker")
    identity = ProcessIdentity(pid=999_991, start_time=12345, group_id=999_991)
    assert store.record_process_started(claim, identity)
    assert store.record_provider_dispatch(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_start_delivered(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_execute_received(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_execute_released(
        claim,
        executor_nonce="provider-worker",
    )
    monkeypatch.setattr(
        RunStore,
        "_observe_process_identity",
        staticmethod(lambda _serialized: "known_stopped"),
    )

    assert store.interrupt_active_claims(
        admitted.run_id,
        reason="scheduler_crash",
    ) == ("analyze",)

    recovered = store.load_run(admitted.run_id)
    node = recovered["nodes"]["analyze"]
    assert recovered["status"] == "paused"
    assert node["recovery"]["observation"] == "outcome_uncertain"
    assert node["pending_interaction"]["type"] == "reconcile"
    assert store.claim_node(
        admitted.run_id,
        "analyze",
        "must-not-replay",
    ) is None


def test_crash_after_reaped_provider_worker_still_requires_reconciliation(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "reaped-spawn-crash")
    store = RunStore(tmp_path / "reaped-spawn-crash-home")
    admitted = _admit(store, package, "reaped-spawn-crash")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "crashed-owner",
        executor_id="agent",
        effect_classification="replay_safe",
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_spawn_intent(claim, executor_nonce="provider-worker")
    identity = ProcessIdentity(pid=999_991, start_time=12345, group_id=999_991)
    assert store.record_process_started(claim, identity)
    assert store.record_provider_dispatch(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_start_delivered(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_execute_received(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_provider_execute_released(
        claim,
        executor_nonce="provider-worker",
    )
    assert store.record_process_stopped(claim, identity, cleaned=True)

    assert store.interrupt_active_claims(
        admitted.run_id,
        reason="scheduler_crash",
    ) == ("analyze",)

    recovered = store.load_run(admitted.run_id)
    node = recovered["nodes"]["analyze"]
    assert recovered["status"] == "paused"
    assert node["recovery"]["observation"] == "outcome_uncertain"
    assert node["recovery"]["termination_confirmed"] is True
    assert node["pending_interaction"]["type"] == "reconcile"
    assert store.claim_node(
        admitted.run_id,
        "analyze",
        "must-not-replay",
    ) is None


@pytest.mark.live_system_guard_bypass
def test_reaped_real_worker_before_provider_dispatch_is_known_zero(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "predispatch-crash")
    store = RunStore(tmp_path / "predispatch-crash-home")
    admitted = _admit(store, package, "predispatch-crash")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "crashed-owner",
        executor_id="prompt",
        effect_classification="replay_safe",
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_spawn_intent(claim, executor_nonce="provider-worker")
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0.05,
            term_grace_seconds=0.1,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=0.2,
        ),
    )
    try:
        assert store.record_process_started(claim, tree.identity)
    finally:
        tree.close()
    assert tree.reaped is True
    assert store.record_process_stopped(claim, tree.identity, cleaned=True)

    assert store.interrupt_active_claims(
        admitted.run_id,
        reason="scheduler_crash",
    ) == ("analyze",)

    recovered = RunStore(tmp_path / "predispatch-crash-home").load_run(
        admitted.run_id
    )
    node = recovered["nodes"]["analyze"]
    assert recovered["status"] == "interrupted"
    assert node["recovery"]["observation"] == "known_stopped"
    assert node.get("pending_interaction") is None


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    "cancel_order",
    ("before-authorization", "after-authorization", "after-execute-receipt"),
)
def test_provider_dispatch_cannot_cross_durable_cancellation(
    tmp_path, workflow_writer, cancel_order, monkeypatch
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / f"dispatch-cancel-{cancel_order}",
        name=f"dispatch-cancel-{cancel_order}",
    )
    store = RunStore(tmp_path / f"dispatch-cancel-{cancel_order}-home")
    admitted = _admit(store, package, f"dispatch-cancel-{cancel_order}")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "dispatch-cancel-owner",
        executor_id="prompt",
        effect_classification="replay_safe",
    )
    assert claim is not None
    store.mark_node_started(claim)
    nonce = "dispatch-cancel-provider"
    assert store.record_spawn_intent(claim, executor_nonce=nonce)
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0.05,
            term_grace_seconds=0.1,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=0.2,
        ),
    )
    cancel_errors: list[BaseException] = []
    cancellation_recorded = threading.Event()
    release_termination = threading.Event()
    original_terminate_existing = ManagedProcessTree.terminate_existing

    def blocked_terminate_existing(identity, **kwargs):
        cancellation_recorded.set()
        if not release_termination.wait(timeout=10):
            raise AssertionError("cancellation test did not release termination")
        return original_terminate_existing(identity, **kwargs)

    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        staticmethod(blocked_terminate_existing),
    )

    def cancel() -> None:
        try:
            store.cancel_run(admitted.run_id)
        except BaseException as exc:
            cancel_errors.append(exc)

    cancel_thread = threading.Thread(target=cancel, daemon=True)
    try:
        assert store.record_process_started(claim, tree.identity)
        if cancel_order in {"after-authorization", "after-execute-receipt"}:
            assert store.record_provider_dispatch(claim, executor_nonce=nonce)
        if cancel_order == "after-execute-receipt":
            assert store.record_provider_start_delivered(claim, executor_nonce=nonce)
            assert store.record_provider_execute_received(claim, executor_nonce=nonce)
        cancel_thread.start()
        assert cancellation_recorded.wait(timeout=10)
        assert store.load_run(admitted.run_id)["desired_status"] == "cancelled"
        if cancel_order == "before-authorization":
            crossed_cancellation = store.record_provider_dispatch(
                claim,
                executor_nonce=nonce,
            )
        elif cancel_order == "after-authorization":
            crossed_cancellation = store.record_provider_start_delivered(
                claim,
                executor_nonce=nonce,
            )
        else:
            crossed_cancellation = store.record_provider_execute_released(
                claim,
                executor_nonce=nonce,
            )
    finally:
        release_termination.set()
        cancel_thread.join(timeout=10)
        tree.close()

    assert not cancel_thread.is_alive()
    assert cancel_errors == []
    assert not crossed_cancellation


@pytest.mark.parametrize(
    ("durable_boundary", "expected_status", "expected_observation"),
    (
        ("execute-received", "interrupted", "known_stopped"),
        ("execute-released", "paused", "outcome_uncertain"),
    ),
)
def test_recovery_effect_boundary_is_true_execute_release(
    tmp_path,
    workflow_writer,
    monkeypatch,
    durable_boundary,
    expected_status,
    expected_observation,
) -> None:
    """Treating preparatory receipt as effectful either replays or false-pauses."""
    short_name = "exec-recv" if durable_boundary == "execute-received" else "exec-rel"
    package = _archon_package(
        workflow_writer,
        tmp_path / f"execute-boundary-{durable_boundary}",
        name=short_name,
    )
    store = RunStore(tmp_path / f"execute-boundary-{durable_boundary}-home")
    admitted = _admit(store, package, short_name)
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "execute-boundary-owner",
        executor_id="prompt",
        effect_classification="replay_safe",
    )
    assert claim is not None
    store.mark_node_started(claim)
    nonce = "execute-boundary-provider"
    assert store.record_spawn_intent(claim, executor_nonce=nonce)
    identity = ProcessIdentity(pid=999_991, start_time=12345, group_id=999_991)
    assert store.record_process_started(claim, identity)
    assert store.record_provider_dispatch(claim, executor_nonce=nonce)
    assert store.record_provider_start_delivered(claim, executor_nonce=nonce)
    assert store.record_provider_execute_received(claim, executor_nonce=nonce)
    if durable_boundary == "execute-released":
        assert store.record_provider_execute_released(claim, executor_nonce=nonce)
    monkeypatch.setattr(
        RunStore,
        "_observe_process_identity",
        staticmethod(lambda _serialized: "known_stopped"),
    )

    assert store.interrupt_active_claims(
        admitted.run_id,
        reason="scheduler_crash",
    ) == ("analyze",)

    recovered = store.load_run(admitted.run_id)
    assert recovered["status"] == expected_status
    assert (
        recovered["nodes"]["analyze"]["recovery"]["observation"]
        == expected_observation
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX coordinator kill")
@pytest.mark.live_system_guard_bypass
def test_real_coordinator_death_before_execute_receipt_is_known_zero(
    tmp_path, workflow_writer
) -> None:
    """The production exchange/store seam must not mark start receipt effectful."""
    home = tmp_path / "real-execute-receipt-cut-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "real-execute-receipt-cut",
        name="exec-cut",
    )
    store = RunStore(home)
    admitted = _admit(store, package, "exec-cut")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "coordinator-that-will-die",
        executor_id="prompt",
        effect_classification="replay_safe",
        journal_reserve_bytes=4 * 1024 * 1024,
    )
    assert claim is not None
    ready = multiprocessing.Event()
    coordinator = multiprocessing.Process(
        target=_execute_receipt_cut_coordinator,
        args=(home, claim, ready),
    )
    coordinator.start()
    assert ready.wait(timeout=15)
    os.kill(coordinator.pid, signal.SIGKILL)
    coordinator.join(timeout=10)
    assert not coordinator.is_alive()
    assert coordinator.exitcode == -signal.SIGKILL

    # The child was still blocked waiting for execute and exits on parent EOF.
    time.sleep(0.2)
    restarted = RunStore(home)
    assert restarted.interrupt_active_claims(
        admitted.run_id,
        reason="scheduler_crash",
    ) == ("analyze",)
    recovered = restarted.load_run(admitted.run_id)
    assert recovered["status"] == "interrupted"
    assert (
        recovered["nodes"]["analyze"]["recovery"]["observation"]
        == "known_stopped"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX coordinator kill")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    "stage",
    (
        "before_selection",
        "after_selection_before_launch",
        "after_spawn_intent_before_process",
        "after_process_registration_before_dispatch",
        "after_dispatch_before_delivery",
        "after_launch_before_completion",
        "after_completion_before_cas",
        "after_cas_before_outcome",
        "after_outcome",
    ),
)
def test_killed_coordinator_restart_preserves_each_recovery_boundary(
    tmp_path, workflow_writer, stage
) -> None:
    home = tmp_path / f"crash-cut-{stage}"
    package = _archon_package(
        workflow_writer,
        tmp_path / f"crash-cut-package-{stage}",
        name="crash-cuts",
    )
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    key = NodeSessionKey(
        "crash-cuts",
        "analyze",
        "local",
        "fake-provider",
        "default",
    )
    registry.compare_and_set_or_observe(
        key,
        0,
        "missing-session",
        "a" * 64,
    )
    admitted = _admit(store, package, stage)
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "coordinator-that-will-die",
        executor_id="prompt",
        effect_classification="replay_safe",
        journal_reserve_bytes=4 * 1024 * 1024,
        terminal_journal_reserve_bytes=4 * 1024 * 1024,
    )
    assert claim is not None
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    provider_pids = context.Queue()
    coordinator = context.Process(
        target=_crash_cut_coordinator,
        args=(home, claim, stage, ready, provider_pids),
    )
    coordinator.start()
    assert ready.wait(timeout=15), f"coordinator did not reach {stage}"
    coordinator.terminate()
    coordinator.join(timeout=10)
    assert coordinator.exitcode == -signal.SIGTERM

    if stage in {
        "after_process_registration_before_dispatch",
        "after_dispatch_before_delivery",
        "after_launch_before_completion",
        "after_completion_before_cas",
        "after_cas_before_outcome",
        "after_outcome",
    }:
        provider_pid = provider_pids.get(timeout=5)
        try:
            os.kill(provider_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(provider_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)

    restarted = RunStore(home)
    if stage in {
        "before_selection",
        "after_selection_before_launch",
        "after_spawn_intent_before_process",
        "after_process_registration_before_dispatch",
        "after_dispatch_before_delivery",
    }:
        assert restarted.interrupt_active_claims(
            admitted.run_id,
            reason="coordinator_crash",
        ) == ("analyze",)
        recovered = restarted.load_run(admitted.run_id)
        assert recovered["status"] == "interrupted"
        expected_observation = (
            "known_stopped"
            if stage
            in {
                "after_process_registration_before_dispatch",
                "after_dispatch_before_delivery",
            }
            else "not_started"
        )
        assert (
            recovered["nodes"]["analyze"]["recovery"]["observation"]
            == expected_observation
        )
        recoveries = recovered["nodes"]["analyze"].get("session_recoveries", [])
        assert bool(recoveries) is (stage != "before_selection")
        assert registry.get(key).session_id == "missing-session"
    elif stage == "after_launch_before_completion":
        assert restarted.interrupt_active_claims(
            admitted.run_id,
            reason="coordinator_crash",
        ) == ("analyze",)
        recovered = restarted.load_run(admitted.run_id)
        assert recovered["status"] == "paused"
        assert recovered["nodes"]["analyze"]["recovery"]["observation"] == (
            "outcome_uncertain"
        )
        assert registry.get(key).session_id == "missing-session"
    else:
        recovered = RunScheduler(
            restarted,
            session_registry=registry,
        ).advance(admitted.run_id)
        assert recovered["status"] == "succeeded"
        assert restarted.pending_session_registry_update(admitted.run_id) is None
        assert registry.get(key).session_id == "fresh-session"


def test_registry_read_failure_is_recovery_unavailable_before_provider(
    tmp_path, workflow_writer
) -> None:
    class CorruptRegistry(NodeSessionRegistry):
        def get(self, key):
            raise sqlite3.DatabaseError("private corrupt path")

    package = _archon_package(workflow_writer, tmp_path / "corrupt")
    store = RunStore(tmp_path / "corrupt-home")
    runner = _PersistentRunner()

    _run_id, result = _run_once(
        store,
        package,
        runner,
        CorruptRegistry(tmp_path / "corrupt-home"),
        "corrupt",
    )

    assert result["status"] == "failed"
    assert result["nodes"]["analyze"]["attempts"][0]["error_code"] == (
        "persistent_session_recovery_unavailable"
    )
    assert runner.requests == []


def test_corrupt_registry_fingerprint_is_unavailable_not_fresh_execution(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "corrupt-fingerprint")
    store = RunStore(tmp_path / "corrupt-fingerprint-home")
    registry = NodeSessionRegistry(tmp_path / "corrupt-fingerprint-home")
    runner = _PersistentRunner()
    key = NodeSessionKey(
        "persistent", "analyze", "local", "fake-provider", "default"
    )
    registry.compare_and_set_or_observe(
        key,
        0,
        "corrupt-session",
        "a" * 64,
    )
    with sqlite3.connect(registry.database) as connection:
        connection.execute(
            "UPDATE node_sessions SET cache_fingerprint='not-a-digest'"
        )

    _run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "corrupt-fingerprint",
    )

    assert result["status"] == "failed"
    attempt = result["nodes"]["analyze"]["attempts"][0]
    assert attempt["error_code"] == "persistent_session_recovery_unavailable"
    assert attempt["metadata"]["provider_attempts"] == 0
    assert runner.requests == []


def test_legacy_scheduler_without_agent_never_creates_session_registry(
    tmp_path, monkeypatch
) -> None:
    import plugins.workflow.scheduler as scheduler_module

    monkeypatch.setattr(
        scheduler_module,
        "NodeSessionRegistry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session registry created")
        ),
    )
    scheduler = RunScheduler(RunStore(tmp_path / "legacy-no-agent-home"))
    try:
        assert scheduler.session_registry is None
    finally:
        scheduler.shutdown()


def test_recovery_selection_is_durable_before_fresh_provider_launch(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "selection")
    store = RunStore(tmp_path / "selection-home")
    registry = NodeSessionRegistry(tmp_path / "selection-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = PluginAgentSessionMissingError("private missing detail")
    admitted = _admit(store, package, "selection")
    observed = []

    def observe_selection() -> None:
        run = store.load_run(admitted.run_id)
        observed.append(
            list(run["nodes"]["analyze"].get("session_recoveries", ()))
        )

    runner.before_fresh = observe_selection
    result = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=registry,
    ).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert len(observed) == 1
    assert observed[0][0]["outcome"] == "fresh_start_selected"
    assert observed[0][0]["provider_attempts_before_recovery"] == 0
    events = store.tail_events(admitted.run_id)
    selection = next(
        event
        for event in events
        if event["event_type"] == "persistent_session_missing_fresh_start"
    )
    assert selection["attempt_id"] == result["nodes"]["analyze"]["attempts"][0][
        "attempt_id"
    ]
    public = str(events)
    assert "private missing detail" not in public
    assert "session-1" not in public

    evidence = EvidenceReader(store).query(
        admitted.run_id,
        kind="recovery",
    )
    persistent = next(
        item
        for item in evidence["items"]
        if item.get("recovery_kind") == "persistent_session"
    )
    assert persistent["node_id"] == "analyze"
    assert persistent["outcome"] == "stale_entry_replaced"
    assert persistent["source"] == "cross_run_registry"
    assert persistent["provider_attempts_before_recovery"] == 0
    assert len(persistent["missing_session_sha256"]) == 64
    assert len(persistent["cache_fingerprint_sha256"]) == 64
    assert not {
        "session_id",
        "key",
        "cache_fingerprint",
        "history",
        "path",
        "response",
    }.intersection(persistent)


def test_persistent_recovery_history_is_bounded_before_seventh_record(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "bounded-history")
    store = RunStore(tmp_path / "bounded-history-home")
    admitted = _admit(store, package, "bounded-history")
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "bounded-owner",
        journal_reserve_bytes=2 * 1024 * 1024,
        terminal_journal_reserve_bytes=2 * 1024 * 1024,
    )
    assert claim is not None
    store.mark_node_started(claim)
    projection = store.load_run(admitted.run_id)
    recoveries = []
    history_attempts = []
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(6):
            attempt_id = f"history-{index}"
            missing_session_id = f"missing-history-{index}"
            cache_fingerprint = hashlib.sha256(attempt_id.encode()).hexdigest()
            recoveries.append({
                "attempt_id": attempt_id,
                "registry_generation": index,
                "missing_session_sha256": hashlib.sha256(
                    missing_session_id.encode()
                ).hexdigest(),
                "cache_fingerprint_sha256": hashlib.sha256(
                    cache_fingerprint.encode()
                ).hexdigest(),
                "source": "cross_run_registry",
                "provider": "fake-provider",
                "runtime_profile": "default",
                "provider_attempts_before_recovery": 0,
                "outcome": "fresh_execution_failed",
            })
            history_attempts.append({
                "attempt_id": attempt_id,
                "state": "failed",
            })
            store._write_private_authority(
                connection,
                table="session_recovery_selection_authority",
                run_id=admitted.run_id,
                attempt_id=attempt_id,
                authority={
                    "schema_version": 1,
                    "run_id": admitted.run_id,
                    "attempt_id": attempt_id,
                    "key": {
                        "workflow": "persistent",
                        "node_id": "analyze",
                        "scope": "local",
                        "provider": "fake-provider",
                        "profile": "default",
                    },
                    "expected_generation": index,
                    "missing_session_id": missing_session_id,
                    "cache_fingerprint": cache_fingerprint,
                    "source": "cross_run_registry",
                    "provider_attempts_before_recovery": 0,
                },
            )
        connection.commit()
    projection["nodes"]["analyze"]["session_recoveries"] = recoveries
    projection["nodes"]["analyze"]["attempts"] = [
        *history_attempts,
        *projection["nodes"]["analyze"]["attempts"],
    ]
    store.append_event(
        admitted.run_id,
        "test_seed_bounded_recovery_history",
        projection_updates={"nodes": projection["nodes"]},
    )

    with pytest.raises(StorageQuotaError, match="evidence is full"):
        store.record_persistent_session_recovery_selection(
            claim,
            PersistentSessionRecoverySelection(
                key=NodeSessionKey(
                    "persistent",
                    "analyze",
                    "local",
                    "fake-provider",
                    "default",
                ),
                expected_generation=6,
                missing_session_id="missing-session",
                cache_fingerprint="c" * 64,
                run_id=admitted.run_id,
                attempt_id=claim.attempt_id,
            ),
        )

    assert len(
        store.load_run(admitted.run_id)["nodes"]["analyze"][
            "session_recoveries"
        ]
    ) == 6


def test_recovered_session_authority_is_private_across_every_public_surface(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "privacy")
    store = RunStore(tmp_path / "privacy-home")
    registry = NodeSessionRegistry(tmp_path / "privacy-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(store, package, runner, registry, "privacy")

    internal = store.load_run(run_id)
    node = internal["nodes"]["analyze"]
    new_session_id = node["session_id"]
    cache_fingerprint = node["cache_fingerprint"]
    assert result["status"] == "succeeded"
    assert new_session_id == "session-3"
    assert len(cache_fingerprint) == 64

    public_surfaces = {
        "status": store.get_run_status(run_id),
        "public_projection": public_run_projection(store.get_run_status(run_id)),
        "events": store.tail_events(run_id),
        "attempts": EvidenceReader(store).query(run_id, kind="attempts"),
        "timeline": EvidenceReader(store).query(run_id, kind="timeline"),
        "recovery": EvidenceReader(store).query(run_id, kind="recovery"),
    }
    for name, surface in public_surfaces.items():
        encoded = json.dumps(surface, sort_keys=True)
        assert new_session_id not in encoded, name
        assert cache_fingerprint not in encoded, name


def test_recovered_typed_output_session_is_private_across_artifact_surfaces(
    tmp_path, workflow_writer, capsys
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / "typed-privacy",
        nodes=[
            {
                "id": "analyze",
                "prompt": "Analyze",
                "output_type": "RecoveredReport",
            }
        ],
    )
    store = RunStore(tmp_path / "typed-privacy-home")
    registry = NodeSessionRegistry(tmp_path / "typed-privacy-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "typed-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "typed-privacy",
    )

    internal = store.load_run(run_id)
    new_session_id = internal["nodes"]["analyze"]["session_id"]
    publication = next(
        artifact
        for artifact in internal["artifacts"]
        if "publication_id" in artifact
    )
    assert result["status"] == "succeeded"
    assert publication["session_id"] == new_session_id == "session-3"

    public_surfaces = {
        "status": store.get_run_status(run_id),
        "events": store.tail_events(run_id),
        "latest_event_page": store.latest_event_page(run_id),
        "artifacts": EvidenceReader(store).query(run_id, kind="artifacts"),
        "timeline": EvidenceReader(store).query(run_id, kind="timeline"),
    }
    for name, surface in public_surfaces.items():
        assert new_session_id not in json.dumps(surface, sort_keys=True), name

    from plugins.workflow.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(tmp_path / "typed-privacy-home"),
        "events",
        run_id,
        "--json",
    ])
    assert args.func(args) == 0
    cli_events = json.loads(capsys.readouterr().out)
    assert new_session_id not in json.dumps(cli_events, sort_keys=True)


def test_postresolution_event_privacy_uses_private_anchor_without_public_marker(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / "markerless-event-privacy",
        nodes=[
            {
                "id": "analyze",
                "prompt": "Analyze",
                "output_type": "RecoveredReport",
            }
        ],
    )
    home = tmp_path / "markerless-event-privacy-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, "markerless-event-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "markerless-event-privacy",
    )
    protected_session_id = result["nodes"]["analyze"]["session_id"]

    def damage_winner(projection) -> None:
        node = projection["nodes"]["analyze"]
        attempt = node["attempts"][-1]
        attempt["state"] = "failed"
        attempt.pop("session_registry_authority")
        attempt["metadata"]["session_id"] = "substituted-private-session"
        node["session_id"] = "substituted-private-session"

    _rewrite_latest_projection(store, run_id, damage_winner)

    page = store.latest_event_page(run_id)
    assert protected_session_id not in json.dumps(page, sort_keys=True)
    assert "substituted-private-session" not in json.dumps(page, sort_keys=True)


@pytest.mark.parametrize("normalizer_version", [1, 2])
def test_legacy_public_session_projection_remains_exact(
    tmp_path, workflow_writer, normalizer_version
) -> None:
    workflow = workflow_writer(
        tmp_path / f"legacy-v{normalizer_version}",
        name=f"legacy-v{normalizer_version}",
        nodes=[{"id": "legacy", "prompt": "Legacy"}],
    )
    package = load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=None,
        normalizer_version=normalizer_version,
    )
    store = RunStore(tmp_path / f"legacy-v{normalizer_version}-home")
    admitted = _admit(store, package, f"legacy-v{normalizer_version}")
    claim = store.claim_node(admitted.run_id, "legacy", "legacy-owner")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": "legacy-public-session",
            "cache_fingerprint": "legacy-public-fingerprint",
        },
    )

    status = store.get_run_status(admitted.run_id)
    events = store.tail_events(admitted.run_id)
    assert status["nodes"]["legacy"]["session_id"] == "legacy-public-session"
    assert status["nodes"]["legacy"]["cache_fingerprint"] == (
        "legacy-public-fingerprint"
    )
    completed = next(event for event in events if event["event_type"] == "node_succeeded")
    assert completed["payload"]["metadata"]["session_id"] == (
        "legacy-public-session"
    )
    assert completed["payload"]["metadata"]["cache_fingerprint"] == (
        "legacy-public-fingerprint"
    )


def test_failed_fresh_recovery_records_outcome_without_registry_obligation(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "fresh-failed")
    store = RunStore(tmp_path / "fresh-failed-home")
    registry = NodeSessionRegistry(tmp_path / "fresh-failed-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = PluginAgentSessionMissingError("private missing")
    runner.fresh_failure = True
    run_id, result = _run_once(store, package, runner, registry, "fresh-failed")

    raw = store.load_run(run_id)
    assert result["status"] == "failed"
    assert "pending_session_registry_update" not in raw
    assert raw["nodes"]["analyze"]["session_recoveries"][-1]["outcome"] == (
        "fresh_execution_failed"
    )
    assert [request.context_mode for request in runner.requests] == [
        "fresh",
        "shared",
        "fresh",
    ]


def test_fresh_recovery_exception_records_failed_outcome(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "fresh-exception")
    store = RunStore(tmp_path / "fresh-exception-home")
    registry = NodeSessionRegistry(tmp_path / "fresh-exception-home")
    runner = _PersistentRunner()

    _run_once(store, package, runner, registry, "seed")
    runner.shared_failure = PluginAgentSessionMissingError("private missing")

    def fail_fresh() -> None:
        raise OSError("private fresh worker failure")

    runner.before_fresh = fail_fresh
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "fresh-exception",
    )

    raw = store.load_run(run_id)
    assert result["status"] == "failed"
    assert "pending_session_registry_update" not in raw
    assert raw["nodes"]["analyze"]["session_recoveries"][-1]["outcome"] == (
        "fresh_execution_failed"
    )


def test_recovery_reserve_refusal_happens_before_provider_allocation(
    tmp_path, workflow_writer
) -> None:
    class ReserveBoundaryStore(RunStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.allowed_claim_reserve = None
            self.observed_claim_reserves = []

        def _ensure_run_capacity(
            self, directory, projection, *, journal_reserve_bytes=0
        ):
            if journal_reserve_bytes:
                self.observed_claim_reserves.append(journal_reserve_bytes)
                if (
                    self.allowed_claim_reserve is not None
                    and journal_reserve_bytes > self.allowed_claim_reserve
                ):
                    raise StorageQuotaError("recovery reserve refused")
            return super()._ensure_run_capacity(
                directory,
                projection,
                journal_reserve_bytes=journal_reserve_bytes,
            )

    explicit_fresh = _archon_package(
        workflow_writer,
        tmp_path / "reserve-fresh",
        name="reserve",
        nodes=[{"id": "analyze", "prompt": "Analyze", "context": "fresh"}],
    )
    recoverable = _archon_package(
        workflow_writer,
        tmp_path / "reserve-recoverable",
        name="reserve",
    )
    store = ReserveBoundaryStore(
        tmp_path / "reserve-home",
        max_run_bytes=64 * 1024 * 1024,
        max_journal_bytes=32 * 1024 * 1024,
    )
    registry = NodeSessionRegistry(tmp_path / "reserve-home")
    runner = _PersistentRunner()

    _run_once(store, explicit_fresh, runner, registry, "fresh")
    baseline = max(store.observed_claim_reserves)
    store.observed_claim_reserves.clear()
    store.allowed_claim_reserve = baseline

    _run_id, refused = _run_once(
        store,
        recoverable,
        runner,
        registry,
        "recoverable",
    )

    assert max(store.observed_claim_reserves) > baseline
    assert refused["status"] == "interrupted"
    assert len(runner.requests) == 1


def test_fresh_execution_returns_private_registry_candidate_without_writing(
    tmp_path,
) -> None:
    home = tmp_path / "candidate-home"
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    node = WorkflowNode(
        id="analyze",
        node_type="prompt",
        value="Analyze",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    run_directory = tmp_path / "candidate-run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-candidate",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-candidate",
        workflow_name="candidate",
        workflow_options=freeze_value(
            {
                "persist_sessions": True,
                "provider": "fake-provider",
                "model": "fake-model",
            }
        ),
        operator_scope="scope-digest",
        variable_context=VariableContext(
            workflow_id="run-candidate",
            normalizer_version=3,
        ),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=3,
    )
    key = NodeSessionKey(
        "candidate",
        "analyze",
        "scope-digest",
        "fake-provider",
        "default",
    )

    result = AgentNodeExecutor(
        runner,
        session_registry=registry,
    ).execute(context)

    candidate = getattr(result, "session_registry_update", None)
    assert result.status == "succeeded"
    assert registry.get(key) is None
    assert candidate is not None
    assert candidate.key == key
    assert candidate.expected_generation == 0
    assert candidate.new_session_id == "session-1"
    assert candidate.cache_fingerprint == result.metadata["cache_fingerprint"]
    assert candidate.winning_run_id == "run-candidate"
    assert candidate.winning_node_id == "analyze"
    assert candidate.winning_attempt_id == "attempt-candidate"
    assert result.session_registry_authority == candidate


def test_success_and_registry_obligation_are_atomic_before_cas(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    package = _archon_package(workflow_writer, tmp_path / "atomic", name="atomic")
    store = RunStore(tmp_path / "atomic-home")
    registry = UnavailableRegistry(tmp_path / "atomic-home")
    runner = _PersistentRunner()

    run_id, result = _run_once(store, package, runner, registry, "atomic")

    raw = store.load_run(run_id)
    pending = raw.get("pending_session_registry_update")
    assert pending is not None, (result["status"], raw)
    assert result["status"] == "running"
    assert raw["nodes"]["analyze"]["state"] == "succeeded"
    assert pending["winning_attempt_id"] == raw["nodes"]["analyze"]["attempts"][
        0
    ]["attempt_id"]
    assert pending["new_session_id"] == "session-1"
    assert pending["retry_count"] == 1
    assert registry.get(
        NodeSessionKey(
            "atomic",
            "analyze",
            "local",
            "fake-provider",
            "default",
        )
    ) is None
    public = public_run_projection(store.get_run_status(run_id))
    assert "pending_session_registry_update" not in public

    with sqlite3.connect(store.database) as connection:
        claim_count = connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        reserve = connection.execute(
            "SELECT terminal_reserve_bytes, consumed_bytes "
            "FROM obligation_journal_reserves WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert claim_count == 0
    assert reserve is not None
    assert reserve[0] > reserve[1]

    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "DELETE FROM obligation_journal_reserves WHERE run_id=?",
            (run_id,),
        )
    restarted_store = RunStore(tmp_path / "atomic-home")
    with sqlite3.connect(restarted_store.database) as connection:
        rebuilt_reserve = connection.execute(
            "SELECT terminal_reserve_bytes FROM obligation_journal_reserves "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert rebuilt_reserve is not None


def test_parallel_persistent_nodes_keep_every_winning_registry_obligation(
    tmp_path, workflow_writer
) -> None:
    class SwitchableRegistry(NodeSessionRegistry):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.available = False

        def compare_and_set_or_observe(self, *args, **kwargs):
            if not self.available:
                raise OSError("private registry path")
            return super().compare_and_set_or_observe(*args, **kwargs)

    package = _archon_package(
        workflow_writer,
        tmp_path / "parallel",
        name="parallel",
        nodes=[
            {"id": "first", "prompt": "First"},
            {"id": "second", "prompt": "Second"},
        ],
    )
    store = RunStore(tmp_path / "parallel-home")
    registry = SwitchableRegistry(tmp_path / "parallel-home")
    runner = _PersistentRunner()
    clock = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    admitted = _admit(store, package, "parallel")
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=registry,
        max_parallel_nodes=2,
        utcnow=lambda: clock[0],
    )

    scheduler.advance(admitted.run_id)

    pending = store.load_run(admitted.run_id)
    assert {node["state"] for node in pending["nodes"].values()} == {"succeeded"}
    assert len(pending["pending_session_registry_updates"]) == 2
    assert len(runner.requests) == 2

    registry.available = True
    clock[0] += timedelta(seconds=2)
    completed = scheduler.advance(admitted.run_id)
    if completed["status"] != "succeeded":
        completed = scheduler.advance(admitted.run_id)

    assert completed["status"] == "succeeded"
    assert "pending_session_registry_updates" not in completed
    assert {
        registry.get(
            NodeSessionKey(
                "parallel",
                node_id,
                "local",
                "fake-provider",
                "default",
            )
        ).session_id
        for node_id in ("first", "second")
    } == {"session-1", "session-2"}


def test_scheduler_replenishment_retains_the_sixty_fifth_registry_obligation(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    nodes = [
        {"id": f"node-{index:03d}", "prompt": f"Node {index}"}
        for index in range(65)
    ]
    package = _archon_package(
        workflow_writer,
        tmp_path / "replenished",
        name="replenished",
        nodes=nodes,
    )
    store = RunStore(tmp_path / "replenished-home")
    runner = _PersistentRunner()
    admitted = _admit(store, package, "replenished")
    seeded = store.load_run(admitted.run_id)
    pending = {}
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(64):
            node_id = f"node-{index:03d}"
            attempt_id = f"seed-attempt-{index:03d}"
            session_id = f"seed-session-{index:03d}"
            cache_fingerprint = hashlib.sha256(node_id.encode()).hexdigest()
            candidate = SessionRegistryUpdateCandidate(
                key=NodeSessionKey(
                    "replenished",
                    node_id,
                    "local",
                    "fake-provider",
                    "default",
                ),
                expected_generation=0,
                new_session_id=session_id,
                cache_fingerprint=cache_fingerprint,
                winning_run_id=admitted.run_id,
                winning_node_id=node_id,
                winning_attempt_id=attempt_id,
                recovery_selected=False,
            )
            authority = {
                "schema_version": 1,
                "key": {
                    "workflow": "replenished",
                    "node_id": node_id,
                    "scope": "local",
                    "provider": "fake-provider",
                    "profile": "default",
                },
                "expected_generation": 0,
                "new_session_id": session_id,
                "cache_fingerprint": cache_fingerprint,
                "winning_run_id": admitted.run_id,
                "winning_node_id": node_id,
                "winning_attempt_id": attempt_id,
                "recovery_selected": False,
                "retry_count": 0,
            }
            seeded["nodes"][node_id].update({
                "state": "succeeded",
                "session_id": session_id,
                "cache_fingerprint": cache_fingerprint,
                "attempts": [{
                    "attempt_id": attempt_id,
                    "state": "succeeded",
                    "completed_at": "2026-08-01T00:00:00+00:00",
                    "metadata": {
                        "session_id": session_id,
                        "cache_fingerprint": cache_fingerprint,
                    },
                    "session_registry_authority": authority,
                }],
            })
            pending[attempt_id] = authority
            store._write_private_authority(
                connection,
                table="session_registry_winner_authority",
                run_id=admitted.run_id,
                attempt_id=attempt_id,
                authority=authority,
            )
        connection.commit()
    store.append_event(
        admitted.run_id,
        "test_seed_registry_obligations",
        projection_updates={
            "nodes": seeded["nodes"],
            "pending_session_registry_updates": pending,
        },
    )
    store = RunStore(tmp_path / "replenished-home")
    clock = datetime(2026, 8, 1, tzinfo=timezone.utc)

    result = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=UnavailableRegistry(tmp_path / "replenished-home"),
        max_parallel_nodes=1,
        utcnow=lambda: clock,
    ).advance(admitted.run_id)

    pending = result["pending_session_registry_updates"]
    assert len(runner.requests) == 1
    assert len(pending) == 65
    assert {node["state"] for node in result["nodes"].values()} == {"succeeded"}
    assert result["status"] == "running"


def test_registry_cas_distinguishes_replacement_replay_and_newer_winner(
    tmp_path,
) -> None:
    registry = NodeSessionRegistry(tmp_path / "cas-home")
    key = NodeSessionKey("cas", "node", "scope", "provider", "default")

    replaced = registry.compare_and_set_or_observe(
        key, 0, "winner-session", "winner-fingerprint"
    )
    replayed = registry.compare_and_set_or_observe(
        key, 0, "winner-session", "winner-fingerprint"
    )
    retained = registry.compare_and_set_or_observe(
        key, 0, "stale-session", "stale-fingerprint"
    )

    assert replaced == "stale_entry_replaced"
    assert replayed == "stale_entry_replaced_already_applied"
    assert retained == "newer_entry_retained"
    assert registry.get(key).session_id == "winner-session"


def test_registry_generation_cas_has_one_winner_across_processes(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_registry_cas_process,
            args=(tmp_path / "multiprocess-home", ready, results, session_id),
        )
        for session_id in ("process-session-a", "process-session-b")
    ]
    for process in processes:
        process.start()
    ready.set()
    outcomes = [results.get(timeout=15) for _process in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(outcomes) == ["newer_entry_retained", "stale_entry_replaced"]


def test_live_completion_rejects_substituted_registry_authority(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(workflow_writer, tmp_path / "live-authority")
    store = RunStore(tmp_path / "live-authority-home")
    admitted = _admit(store, package, "live-authority")
    claim = store.claim_node(admitted.run_id, "analyze", "owner")
    assert claim is not None
    store.mark_node_started(claim)
    candidate = SessionRegistryUpdateCandidate(
        key=NodeSessionKey(
            "persistent", "analyze", "local", "fake-provider", "default"
        ),
        expected_generation=0,
        new_session_id="winning-session",
        cache_fingerprint="a" * 64,
        winning_run_id=admitted.run_id,
        winning_node_id="analyze",
        winning_attempt_id=claim.attempt_id,
    )
    substituted = SessionRegistryUpdateCandidate(
        key=candidate.key,
        expected_generation=0,
        new_session_id="substituted-session",
        cache_fingerprint="a" * 64,
        winning_run_id=admitted.run_id,
        winning_node_id="analyze",
        winning_attempt_id=claim.attempt_id,
    )

    with pytest.raises(ValueError, match="winning completion"):
        store.complete_node(
            claim,
            status="succeeded",
            metadata={
                "session_id": candidate.new_session_id,
                "cache_fingerprint": candidate.cache_fingerprint,
            },
            session_registry_update=candidate,
            session_registry_authority=substituted,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("generation", -1),
        ("session_id", "s" * 4097),
        ("cache_fingerprint", "f" * 4097),
        ("updated_at", "not-an-instant"),
    ],
)
def test_registry_rejects_malformed_or_oversized_durable_rows(
    tmp_path, column, value
) -> None:
    registry = NodeSessionRegistry(tmp_path / "malformed-row-home")
    key = NodeSessionKey("row", "node", "scope", "provider", "default")
    registry.compare_and_set_or_observe(key, 0, "session", "legacy-fingerprint")
    with sqlite3.connect(registry.database) as connection:
        connection.execute(
            f"UPDATE node_sessions SET {column}=? WHERE workflow='row'",
            (value,),
        )

    with pytest.raises(ValueError, match="malformed"):
        registry.get(key)


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_generation": True},
        {"new_session_id": "s" * 4097},
        {"cache_fingerprint": "not-a-sha256"},
        {"recovery_selected": "false"},
    ],
)
def test_registry_candidate_rejects_noncanonical_authority(overrides) -> None:
    values = {
        "key": NodeSessionKey(
            "candidate", "node", "scope", "provider", "default"
        ),
        "expected_generation": 0,
        "new_session_id": "session",
        "cache_fingerprint": "a" * 64,
        "winning_run_id": "run",
        "winning_node_id": "node",
        "winning_attempt_id": "attempt",
        "recovery_selected": False,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        SessionRegistryUpdateCandidate(**values)


def test_phase3_catalog_registers_real_session_recovery_codes_and_event() -> None:
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    expected = {
        "context_missing_session": (True, False),
        "persistent_session_recovery_unavailable": (True, False),
        "persistent_session_missing_fresh_start": (False, True),
        "persistent_session_registry_update_pending": (True, True),
    }
    assert {
        code: (catalog[code]["runtime_failure"], catalog[code]["evidence"])
        for code in expected
    } == expected


def test_registry_reconciliation_retries_exactly_then_requires_operator_resume(
    tmp_path, workflow_writer
) -> None:
    class SwitchableRegistry(NodeSessionRegistry):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.available = False
            self.attempt_times = []

        def compare_and_set_or_observe(self, *args, **kwargs):
            self.attempt_times.append(clock[0])
            if not self.available:
                raise OSError("private registry path")
            return super().compare_and_set_or_observe(*args, **kwargs)

    clock = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    package = _archon_package(workflow_writer, tmp_path / "retry", name="retry")
    store = RunStore(tmp_path / "retry-home")
    registry = SwitchableRegistry(tmp_path / "retry-home")
    runner = _PersistentRunner()
    admitted = _admit(store, package, "retry")
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=registry,
        utcnow=lambda: clock[0],
    )

    scheduler.advance(admitted.run_id)
    not_due, _cursor, _exhausted = store.coordinator_candidates(
        after=None,
        now=clock[0] + timedelta(milliseconds=500),
    )
    due, _cursor, _exhausted = store.coordinator_candidates(
        after=None,
        now=clock[0] + timedelta(seconds=1),
    )
    assert admitted.run_id not in {item["run_id"] for item in not_due}
    assert admitted.run_id in {item["run_id"] for item in due}
    for delay in (1, 2, 4, 8):
        before_due = clock[0] + timedelta(seconds=delay - 0.25)
        clock[0] = before_due
        scheduler.advance(admitted.run_id)
        clock[0] = before_due + timedelta(seconds=0.25)
        scheduler.advance(admitted.run_id)

    pending = store.load_run(admitted.run_id)
    assert pending["status"] == "recovery_pending"
    assert pending["last_error"]["code"] == (
        "persistent_session_registry_update_pending"
    )
    assert len(registry.attempt_times) == 5
    assert [
        int((later - earlier).total_seconds())
        for earlier, later in zip(registry.attempt_times, registry.attempt_times[1:])
    ] == [1, 2, 4, 8]
    assert len(runner.requests) == 1

    deferred_events = [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "persistent_session_registry_update_deferred"
    ]
    assert [
        int(
            (
                    datetime.fromisoformat(
                        event["payload"]["next_registry_update_at"]
                    )
                - registry.attempt_times[index]
            ).total_seconds()
        )
        for index, event in enumerate(deferred_events)
    ] == [1, 2, 4, 8, 16]

    resumed = store.resume_run(
        admitted.run_id,
        always_run_nodes=frozenset(),
    )
    assert resumed["status"] == "running"
    assert resumed["nodes"]["analyze"]["state"] == "succeeded"
    registry.available = True
    completed = scheduler.advance(admitted.run_id)

    assert completed["status"] == "succeeded"
    assert len(runner.requests) == 1
    with sqlite3.connect(store.database) as connection:
        reserve_count = connection.execute(
            "SELECT COUNT(*) FROM obligation_journal_reserves WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0]
    assert reserve_count == 0


def test_reconciliation_observes_prior_apply_and_never_clobbers_newer_entry(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    clock = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    package = _archon_package(
        workflow_writer,
        tmp_path / "collisions",
        name="collisions",
    )
    store = RunStore(tmp_path / "collisions-home")
    runner = _PersistentRunner()

    for key, installed_session, expected_outcome in (
        ("already", "session-1", "stale_entry_replaced_already_applied"),
        ("newer", "newer-session", "newer_entry_retained"),
    ):
        unavailable = UnavailableRegistry(tmp_path / "collisions-home")
        admitted = _admit(store, package, key)
        RunScheduler(
            store,
            agent_runner=runner,
            session_registry=unavailable,
            utcnow=lambda: clock[0],
        ).advance(admitted.run_id)
        raw = store.load_run(admitted.run_id)
        pending = raw["pending_session_registry_update"]
        registry_key = NodeSessionKey(
            "collisions", "analyze", "local", "fake-provider", "default"
        )
        real_registry = NodeSessionRegistry(tmp_path / "collisions-home")
        real_registry.compare_and_set_or_observe(
            registry_key,
            pending["expected_generation"],
            installed_session,
            (
                pending["cache_fingerprint"]
                if key == "already"
                else "newer-fingerprint"
            ),
        )
        clock[0] += timedelta(seconds=1)
        completed = RunScheduler(
            store,
            agent_runner=runner,
            session_registry=real_registry,
            utcnow=lambda: clock[0],
        ).advance(admitted.run_id)

        assert completed["status"] == "succeeded"
        resolution = next(
            event
            for event in reversed(store.tail_events(admitted.run_id))
            if event["event_type"]
            in {"run_succeeded", "persistent_session_registry_update_resolved"}
        )
        assert resolution["payload"]["outcome"] == expected_outcome
        assert real_registry.get(registry_key).session_id == installed_session

    assert len(runner.requests) == 2


def test_cancellation_waits_for_winning_registry_obligation(
    tmp_path, workflow_writer
) -> None:
    class SwitchableRegistry(NodeSessionRegistry):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.available = False

        def compare_and_set_or_observe(self, *args, **kwargs):
            if not self.available:
                raise OSError("private registry path")
            return super().compare_and_set_or_observe(*args, **kwargs)

    clock = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    package = _archon_package(workflow_writer, tmp_path / "cancel", name="cancel")
    store = RunStore(tmp_path / "cancel-home")
    registry = SwitchableRegistry(tmp_path / "cancel-home")
    runner = _PersistentRunner()
    admitted = _admit(store, package, "cancel")
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=registry,
        utcnow=lambda: clock[0],
    )
    scheduler.advance(admitted.run_id)

    requested = store.cancel_run(admitted.run_id)

    assert requested["status"] == "running"
    assert requested["desired_status"] == "cancelled"
    assert "pending_session_registry_update" in requested
    assert "run_cancelled" not in {
        event["event_type"] for event in store.tail_events(admitted.run_id)
    }
    registry.available = True
    clock[0] += timedelta(seconds=1)
    cancelled = scheduler.advance(admitted.run_id)

    assert cancelled["status"] == "cancelled"
    assert "pending_session_registry_update" not in cancelled
    assert len(runner.requests) == 1


def test_uncorroborated_registry_obligation_fails_closed_during_rebuild(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    package = _archon_package(workflow_writer, tmp_path / "damaged", name="damaged")
    store = RunStore(tmp_path / "damaged-home")
    run_id, _result = _run_once(
        store,
        package,
        _PersistentRunner(),
        UnavailableRegistry(tmp_path / "damaged-home"),
        "damaged",
    )
    _rewrite_latest_projection(
        store,
        run_id,
        lambda projection: projection["pending_session_registry_update"].update(
            {"winning_attempt_id": "uncorroborated-attempt"}
        ),
    )

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda pending: pending.update({"expected_generation": 1}),
            id="generation",
        ),
        pytest.param(
            lambda pending: pending.update(
                {"new_session_id": "substituted-session"}
            ),
            id="new-session",
        ),
        pytest.param(
            lambda pending: pending.update({"cache_fingerprint": "f" * 64}),
            id="fingerprint",
        ),
        pytest.param(
            lambda pending: pending["key"].update(
                {"provider": "substituted-provider"}
            ),
            id="provider",
        ),
        pytest.param(
            lambda pending: pending["key"].update(
                {"scope": "substituted-scope"}
            ),
            id="scope",
        ),
        pytest.param(
            lambda pending: pending["key"].update(
                {"profile": "substituted-profile"}
            ),
            id="profile",
        ),
        pytest.param(
            lambda pending: pending.update({"recovery_selected": "false"}),
            id="recovery-selected-type",
        ),
    ],
)
def test_substituted_registry_authority_fails_closed_during_rebuild(
    tmp_path, workflow_writer, mutate
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    package = _archon_package(
        workflow_writer,
        tmp_path / "substituted",
        name="substituted",
    )
    store = RunStore(tmp_path / "substituted-home")
    run_id, _result = _run_once(
        store,
        package,
        _PersistentRunner(),
        UnavailableRegistry(tmp_path / "substituted-home"),
        "substituted",
    )
    _rewrite_latest_projection(
        store,
        run_id,
        lambda projection: mutate(
            projection["pending_session_registry_update"]
        ),
    )

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


def test_lockstep_substitution_cannot_replace_private_winner_authority(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    package = _archon_package(
        workflow_writer,
        tmp_path / "lockstep-authority",
        name="lockstep-authority",
    )
    store = RunStore(tmp_path / "lockstep-authority-home")
    run_id, _result = _run_once(
        store,
        package,
        _PersistentRunner(),
        UnavailableRegistry(tmp_path / "lockstep-authority-home"),
        "lockstep-authority",
    )

    def substitute_every_journal_copy(projection) -> None:
        pending = projection["pending_session_registry_update"]
        attempt = projection["nodes"]["analyze"]["attempts"][-1]
        pending["new_session_id"] = "substituted-session"
        attempt["session_registry_authority"]["new_session_id"] = (
            "substituted-session"
        )
        attempt["metadata"]["session_id"] = "substituted-session"
        projection["nodes"]["analyze"]["session_id"] = "substituted-session"

    _rewrite_latest_projection(store, run_id, substitute_every_journal_copy)

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


def test_selected_recovery_missing_session_digest_has_private_anchor(
    tmp_path, workflow_writer
) -> None:
    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *args, **kwargs):
            raise OSError("private registry path")

    home = tmp_path / "selection-anchor-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "selection-anchor",
        name="selection-anchor",
    )
    store = RunStore(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, NodeSessionRegistry(home), "selection-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, _result = _run_once(
        store,
        package,
        runner,
        UnavailableRegistry(home),
        "selection-anchor",
    )

    def substitute_missing_digest(projection) -> None:
        recovery = projection["nodes"]["analyze"]["session_recoveries"][-1]
        recovery["missing_session_sha256"] = "b" * 64

    _rewrite_latest_projection(store, run_id, substitute_missing_digest)

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


@pytest.mark.parametrize(
    "corruption",
    ("authority-marker", "exact-session-authority", "missing-session-digest"),
)
def test_resolved_terminal_recovery_keeps_private_anchor_authoritative(
    tmp_path, workflow_writer, corruption
) -> None:
    home = tmp_path / f"resolved-terminal-{corruption}-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / f"resolved-terminal-{corruption}",
        name=f"resolved-terminal-{corruption}",
    )
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, f"{corruption}-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        f"{corruption}-resolved",
    )
    assert result["status"] == "succeeded"
    assert store.pending_session_registry_update(run_id) is None

    def corrupt(projection) -> None:
        node = projection["nodes"]["analyze"]
        attempt = node["attempts"][-1]
        recovery = node["session_recoveries"][-1]
        if corruption == "authority-marker":
            attempt.pop("session_registry_authority")
        elif corruption == "exact-session-authority":
            attempt["session_registry_authority"]["new_session_id"] = (
                "substituted-terminal-session"
            )
            attempt["session_registry_authority"]["cache_fingerprint"] = "f" * 64
            attempt["metadata"]["session_id"] = "substituted-terminal-session"
            attempt["metadata"]["cache_fingerprint"] = "f" * 64
            node["session_id"] = "substituted-terminal-session"
            node["cache_fingerprint"] = "f" * 64
            recovery["cache_fingerprint_sha256"] = hashlib.sha256(
                ("f" * 64).encode()
            ).hexdigest()
        else:
            recovery["missing_session_sha256"] = "b" * 64

    _rewrite_latest_projection(store, run_id, corrupt)

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


@pytest.mark.parametrize(
    "corruption",
    ("authority-marker", "exact-session-authority", "missing-session-digest"),
)
def test_resolved_running_recovery_keeps_private_anchor_authoritative(
    tmp_path, workflow_writer, corruption
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / f"resolved-running-{corruption}",
        name=f"resolved-running-{corruption}",
        nodes=[
            {"id": "first", "prompt": "First"},
            {
                "id": "second",
                "prompt": "Continue",
                "depends_on": ["first"],
                "context": "shared",
            },
        ],
    )
    store = RunStore(tmp_path / f"resolved-running-{corruption}-home")
    run_id, _candidate = _resolved_running_recovery(store, package)

    def corrupt(projection) -> None:
        node = projection["nodes"]["first"]
        attempt = node["attempts"][-1]
        recovery = node["session_recoveries"][-1]
        if corruption == "authority-marker":
            attempt.pop("session_registry_authority")
        elif corruption == "exact-session-authority":
            attempt["session_registry_authority"]["new_session_id"] = (
                "substituted-running-session"
            )
            attempt["session_registry_authority"]["cache_fingerprint"] = "f" * 64
            attempt["metadata"]["session_id"] = "substituted-running-session"
            attempt["metadata"]["cache_fingerprint"] = "f" * 64
            node["session_id"] = "substituted-running-session"
            node["cache_fingerprint"] = "f" * 64
            recovery["cache_fingerprint_sha256"] = hashlib.sha256(
                ("f" * 64).encode()
            ).hexdigest()
        else:
            recovery["missing_session_sha256"] = "b" * 64

    _rewrite_latest_projection(store, run_id, corrupt)

    runner = _PersistentRunner()
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        session_registry=NodeSessionRegistry(
            tmp_path / f"resolved-running-{corruption}-home"
        ),
    )
    try:
        with pytest.raises(JournalRecoveryError, match="valid recovery data"):
            scheduler.advance(run_id)
    finally:
        scheduler.shutdown()
    assert runner.requests == []


@pytest.mark.parametrize("run_shape", ("terminal", "still-running"))
def test_immutable_winner_anchor_rejects_attempt_state_downgrade(
    tmp_path, workflow_writer, run_shape
) -> None:
    """Removing the immutable winner check must expose/substitute continuation."""
    home = tmp_path / f"winner-downgrade-{run_shape}-home"
    if run_shape == "terminal":
        package = _archon_package(
            workflow_writer,
            tmp_path / "winner-downgrade-terminal",
            name="winner-downgrade-terminal",
        )
        store = RunStore(home)
        registry = NodeSessionRegistry(home)
        runner = _PersistentRunner()
        _run_once(store, package, runner, registry, "winner-downgrade-seed")
        runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
        run_id, result = _run_once(
            store,
            package,
            runner,
            registry,
            "winner-downgrade-terminal",
        )
        assert result["status"] == "succeeded"
        node_id = "analyze"
    else:
        package = _archon_package(
            workflow_writer,
            tmp_path / "winner-downgrade-running",
            name="winner-downgrade-running",
            nodes=[
                {"id": "first", "prompt": "First"},
                {
                    "id": "second",
                    "prompt": "Continue",
                    "depends_on": ["first"],
                    "context": "shared",
                },
            ],
        )
        store = RunStore(home)
        run_id, _candidate = _resolved_running_recovery(store, package)
        node_id = "first"

    def downgrade(projection) -> None:
        node = projection["nodes"][node_id]
        attempt = node["attempts"][-1]
        attempt["state"] = "failed"
        attempt.pop("session_registry_authority", None)
        attempt["metadata"]["session_id"] = "substituted-session"
        attempt["metadata"]["cache_fingerprint"] = "f" * 64
        node["session_id"] = "substituted-session"
        node["cache_fingerprint"] = "f" * 64

    _rewrite_latest_projection(store, run_id, downgrade)

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)
    if run_shape == "still-running":
        scheduler = RunScheduler(
            store,
            agent_runner=_PersistentRunner(),
            session_registry=NodeSessionRegistry(home),
        )
        try:
            with pytest.raises(JournalRecoveryError, match="valid recovery data"):
                scheduler.advance(run_id)
        finally:
            scheduler.shutdown()


@pytest.mark.parametrize(
    "authority_damage",
    ("missing", "bad-digest", "malformed-json", "unreadable-table"),
)
def test_active_selection_fails_closed_without_readable_private_anchor(
    tmp_path, workflow_writer, authority_damage
) -> None:
    """Treating an unreadable private authority as empty must accept public state."""
    home = tmp_path / f"selection-authority-{authority_damage}-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / f"selection-authority-{authority_damage}",
        name=f"selection-authority-{authority_damage}",
    )
    store = RunStore(home)
    admitted = _admit(store, package, f"selection-authority-{authority_damage}")
    claim = store.claim_node(admitted.run_id, "analyze", "selection-owner")
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_persistent_session_recovery_selection(
        claim,
        PersistentSessionRecoverySelection(
            key=NodeSessionKey(
                package.definition.name,
                "analyze",
                "local",
                "fake-provider",
                "default",
            ),
            expected_generation=0,
            missing_session_id="missing-session",
            cache_fingerprint="a" * 64,
            run_id=admitted.run_id,
            attempt_id=claim.attempt_id,
        ),
    )

    with sqlite3.connect(store.database) as connection:
        if authority_damage == "missing":
            connection.execute(
                "DELETE FROM session_recovery_selection_authority "
                "WHERE attempt_id=?",
                (claim.attempt_id,),
            )
        elif authority_damage == "bad-digest":
            connection.execute(
                "UPDATE session_recovery_selection_authority "
                "SET authority_sha256=? WHERE attempt_id=?",
                ("0" * 64, claim.attempt_id),
            )
        elif authority_damage == "malformed-json":
            connection.execute(
                "UPDATE session_recovery_selection_authority "
                "SET authority_json=?, authority_sha256=? WHERE attempt_id=?",
                ("{", hashlib.sha256(b"{").hexdigest(), claim.attempt_id),
            )
        else:
            connection.execute("DROP TABLE session_recovery_selection_authority")
        connection.commit()

    with pytest.raises(
        JournalRecoveryError,
        match="private session authority|valid recovery data",
    ):
        store.load_run(admitted.run_id)


@pytest.mark.parametrize("recovery_state", ("fresh-failed", "resolved"))
def test_completed_recovery_state_reverse_requires_selection_anchor(
    tmp_path, workflow_writer, recovery_state
) -> None:
    """Dropping reverse selection checks must trust a journal-only completion."""
    home = tmp_path / f"completed-selection-{recovery_state}-home"
    workflow_name = "sel-fail" if recovery_state == "fresh-failed" else "sel-resolved"
    package = _archon_package(
        workflow_writer,
        tmp_path / f"completed-selection-{recovery_state}",
        name=workflow_name,
    )
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, f"{recovery_state}-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    runner.fresh_failure = recovery_state == "fresh-failed"
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        f"completed-{recovery_state}",
    )
    expected_status = "failed" if recovery_state == "fresh-failed" else "succeeded"
    assert result["status"] == expected_status
    attempt_id = result["nodes"]["analyze"]["attempts"][-1]["attempt_id"]
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "DELETE FROM session_recovery_selection_authority WHERE attempt_id=?",
            (attempt_id,),
        )
        connection.commit()

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


def test_journal_selection_without_committed_private_anchor_fails_closed(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """A crash after journal fsync but before SQLite commit must not select fresh."""
    home = tmp_path / "selection-journal-commit-gap-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "selection-journal-commit-gap",
        name="selection-journal-commit-gap",
    )
    store = RunStore(home)
    admitted = _admit(store, package, "selection-journal-commit-gap")
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="selection-owner",
        host_kind="gateway",
        host_instance_id="selection-owner-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    claim = store.claim_node(
        admitted.run_id,
        "analyze",
        "selection-owner",
        execution_fence=fence,
    )
    assert claim is not None
    store.mark_node_started(claim)
    original_append = store._append_locked

    def crash_after_fsync(*args, **kwargs):
        original_append(*args, **kwargs)
        raise RuntimeError("crash after journal fsync before sqlite commit")

    monkeypatch.setattr(store, "_append_locked", crash_after_fsync)
    with pytest.raises(RuntimeError, match="journal fsync"):
        store.record_persistent_session_recovery_selection(
            claim,
            PersistentSessionRecoverySelection(
                key=NodeSessionKey(
                    package.definition.name,
                    "analyze",
                    "local",
                    "fake-provider",
                    "default",
                ),
                expected_generation=0,
                missing_session_id="missing-session",
                cache_fingerprint="a" * 64,
                run_id=admitted.run_id,
                attempt_id=claim.attempt_id,
            ),
        )
    monkeypatch.setattr(store, "_append_locked", original_append)

    with sqlite3.connect(store.database) as connection:
        row = connection.execute(
            "SELECT 1 FROM session_recovery_selection_authority "
            "WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()
    assert row is None
    with pytest.raises(
        JournalRecoveryError,
        match="private session authority|valid recovery data",
    ):
        RunStore(home).load_run(admitted.run_id)


def test_selected_preobligation_recovery_requires_its_private_evidence(
    tmp_path, workflow_writer
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / "selected-preobligation-anchor",
        name="selected-preobligation-anchor",
    )
    store = RunStore(tmp_path / "selected-preobligation-anchor-home")
    admitted = _admit(store, package, "selected-preobligation-anchor")
    claim = store.claim_node(admitted.run_id, "analyze", "selection-owner")
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_persistent_session_recovery_selection(
        claim,
        PersistentSessionRecoverySelection(
            key=NodeSessionKey(
                package.definition.name,
                "analyze",
                "local",
                "fake-provider",
                "default",
            ),
            expected_generation=0,
            missing_session_id="selected-missing-session",
            cache_fingerprint="a" * 64,
            run_id=admitted.run_id,
            attempt_id=claim.attempt_id,
        ),
    )

    _rewrite_latest_projection(
        store,
        admitted.run_id,
        lambda projection: projection["nodes"]["analyze"][
            "session_recoveries"
        ][-1].update({"missing_session_sha256": "b" * 64}),
    )

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(admitted.run_id)


def test_fresh_failed_recovery_requires_its_private_selection_evidence(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "fresh-failed-anchor-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "fresh-failed-anchor",
        name="fresh-failed-anchor",
    )
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, "fresh-failed-anchor-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    runner.fresh_failure = True
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "fresh-failed-anchor",
    )
    assert result["status"] == "failed"
    assert store.pending_session_registry_update(run_id) is None

    _rewrite_latest_projection(
        store,
        run_id,
        lambda projection: projection["nodes"]["analyze"].update(
            {"session_recoveries": []}
        ),
    )

    with pytest.raises(JournalRecoveryError, match="valid recovery data"):
        store.load_run(run_id)


def test_no_fence_selection_precommit_is_not_activated_by_heartbeat(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """A missing selection frame must keep its private precommit inert."""
    home = tmp_path / "selection-precommit-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "selection-precommit",
        name="selection-precommit",
    )
    store = RunStore(home)
    admitted = _admit(store, package, "selection-precommit")
    claim = store.claim_node(admitted.run_id, "analyze", "selection-owner")
    assert claim is not None
    store.mark_node_started(claim)
    original_append = store._append_locked

    def crash_before_selection_frame(*_args, **_kwargs):
        raise RuntimeError("selection frame was not appended")

    monkeypatch.setattr(store, "_append_locked", crash_before_selection_frame)
    with pytest.raises(RuntimeError, match="selection frame"):
        store.record_persistent_session_recovery_selection(
            claim,
            PersistentSessionRecoverySelection(
                key=NodeSessionKey(
                    package.definition.name,
                    "analyze",
                    "local",
                    "fake-provider",
                    "default",
                ),
                expected_generation=0,
                missing_session_id="orphaned-selection-session",
                cache_fingerprint="a" * 64,
                run_id=admitted.run_id,
                attempt_id=claim.attempt_id,
            ),
        )
    monkeypatch.setattr(store, "_append_locked", original_append)

    with sqlite3.connect(store.database) as connection:
        assert connection.execute(
            "SELECT 1 FROM session_recovery_selection_authority "
            "WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone() is not None

    current = json.loads(
        (store.run_directory(admitted.run_id) / "run.json").read_text()
    )
    active = current["nodes"]["analyze"]["claim"]
    assert store.renew_claim(
        claim,
        now=datetime.fromisoformat(active["heartbeat_at"]) + timedelta(seconds=6),
        monotonic_now=float(active["heartbeat_monotonic"]) + 6,
    )
    restarted = RunStore(home)
    recovered = restarted.load_run(admitted.run_id)
    assert recovered["nodes"]["analyze"].get("session_recoveries", []) == []
    assert restarted.interrupt_active_claims(
        admitted.run_id,
        reason="coordinator_shutdown",
    ) == ("analyze",)
    assert restarted.load_run(admitted.run_id)["status"] == "interrupted"


def test_no_fence_winner_precommit_is_not_activated_by_sibling_event(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """A missing success frame must keep its private winner precommit inert."""
    home = tmp_path / "winner-precommit-home"
    package = _archon_package(
        workflow_writer,
        tmp_path / "winner-precommit",
        name="winner-precommit",
        nodes=[
            {"id": "first", "prompt": "First"},
            {"id": "sibling", "prompt": "Sibling"},
        ],
    )
    store = RunStore(home)
    admitted = _admit(store, package, "winner-precommit")
    claim = store.claim_node(admitted.run_id, "first", "winner-owner")
    assert claim is not None
    store.mark_node_started(claim)
    key = NodeSessionKey(
        package.definition.name,
        "first",
        "local",
        "fake-provider",
        "default",
    )
    assert store.record_persistent_session_recovery_selection(
        claim,
        PersistentSessionRecoverySelection(
            key=key,
            expected_generation=0,
            missing_session_id="missing-winner-session",
            cache_fingerprint="a" * 64,
            run_id=admitted.run_id,
            attempt_id=claim.attempt_id,
        ),
    )
    candidate = SessionRegistryUpdateCandidate(
        key=key,
        expected_generation=0,
        new_session_id="orphaned-winner-session",
        cache_fingerprint="a" * 64,
        winning_run_id=admitted.run_id,
        winning_node_id="first",
        winning_attempt_id=claim.attempt_id,
        recovery_selected=True,
    )
    original_append = store._append_locked

    def crash_before_winner_frame(*_args, **_kwargs):
        raise RuntimeError("winner frame was not appended")

    monkeypatch.setattr(store, "_append_locked", crash_before_winner_frame)
    with pytest.raises(RuntimeError, match="winner frame"):
        store.complete_node(
            claim,
            status="succeeded",
            metadata={
                "session_id": candidate.new_session_id,
                "cache_fingerprint": candidate.cache_fingerprint,
            },
            session_registry_update=candidate,
            session_registry_authority=candidate,
        )
    monkeypatch.setattr(store, "_append_locked", original_append)

    with sqlite3.connect(store.database) as connection:
        assert connection.execute(
            "SELECT 1 FROM session_registry_winner_authority WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone() is not None

    sibling = store.claim_node(admitted.run_id, "sibling", "sibling-owner")
    assert sibling is not None
    restarted = RunStore(home)
    recovered = restarted.load_run(admitted.run_id)
    assert recovered["nodes"]["first"]["state"] == "running"
    assert "pending_session_registry_update" not in recovered
    assert NodeSessionRegistry(home).get(key) is None
    assert restarted.interrupt_active_claims(
        admitted.run_id,
        reason="coordinator_shutdown",
    ) == ("first", "sibling")
    assert restarted.load_run(admitted.run_id)["status"] == "interrupted"
    assert NodeSessionRegistry(home).get(key) is None


@pytest.mark.parametrize("authority_damage", ("deleted", "wrong-shaped"))
def test_session_bearing_completion_event_fails_closed_without_winner_authority(
    tmp_path, workflow_writer, capsys, authority_damage
) -> None:
    """Event/CLI reads must validate the payload-bearing recovery winner."""
    package = _archon_package(
        workflow_writer,
        tmp_path / f"event-winner-{authority_damage}",
        name=f"event-winner-{authority_damage}",
        nodes=[
            {
                "id": "analyze",
                "prompt": "Analyze",
                "output_type": "RecoveredReport",
            }
        ],
    )
    home = tmp_path / f"event-winner-{authority_damage}-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, f"event-{authority_damage}-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        f"event-{authority_damage}",
    )
    original_session = result["nodes"]["analyze"]["session_id"]
    substituted_session = "substituted-event-session"
    attempt_id = result["nodes"]["analyze"]["attempts"][-1]["attempt_id"]

    def damage_completion(event) -> None:
        projection = event["projection"]
        node = projection["nodes"]["analyze"]
        attempt = node["attempts"][-1]
        attempt.pop("session_registry_authority", None)
        attempt["metadata"]["session_id"] = substituted_session
        attempt["metadata"]["cache_fingerprint"] = "f" * 64
        node["session_id"] = substituted_session
        node["cache_fingerprint"] = "f" * 64
        for artifact in projection["artifacts"]:
            if artifact.get("attempt_id") == attempt_id:
                artifact["session_id"] = substituted_session
        for artifact in event["payload"].get("artifacts", []):
            artifact["session_id"] = substituted_session

    _rewrite_journal_event(store, run_id, "node_succeeded", damage_completion)
    with sqlite3.connect(store.database) as connection:
        if authority_damage == "deleted":
            connection.execute(
                "DELETE FROM session_registry_winner_authority "
                "WHERE attempt_id=?",
                (attempt_id,),
            )
        else:
            original_authority = connection.execute(
                "SELECT authority_json FROM session_registry_winner_authority "
                "WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            assert original_authority is not None
            wrong_shaped_authority = json.loads(original_authority[0])
            wrong_shaped_authority["candidate"] = {"wrong": "shape"}
            malformed = json.dumps(
                wrong_shaped_authority,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                "UPDATE session_registry_winner_authority "
                "SET authority_json=?, authority_sha256=? WHERE attempt_id=?",
                (
                    malformed,
                    hashlib.sha256(malformed.encode()).hexdigest(),
                    attempt_id,
                ),
            )
        connection.commit()

    for read_events in (store.tail_events, store.latest_event_page):
        with pytest.raises(JournalRecoveryError) as exc_info:
            read_events(run_id)
        diagnostic = str(exc_info.value)
        assert original_session not in diagnostic
        assert substituted_session not in diagnostic

    from plugins.workflow.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(home),
        "events",
        run_id,
        "--json",
    ])
    assert args.func(args) == 70
    cli_output = capsys.readouterr().out
    assert original_session not in cli_output
    assert substituted_session not in cli_output


@pytest.mark.parametrize(
    "completion_damage",
    ("recovery-list", "event-type", "normalizer", "all"),
)
def test_selected_recovery_event_privacy_does_not_trust_mutable_completion_fields(
    tmp_path, workflow_writer, capsys, completion_damage
) -> None:
    """Bound selection authority must identify damaged recovery completions."""
    package = _archon_package(
        workflow_writer,
        tmp_path / f"event-selection-{completion_damage}",
        name=f"event-selection-{completion_damage}",
        nodes=[
            {
                "id": "analyze",
                "prompt": "Analyze",
                "output_type": "RecoveredReport",
            }
        ],
    )
    home = tmp_path / f"event-selection-{completion_damage}-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, f"{completion_damage}-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        f"event-selection-{completion_damage}",
    )
    internal = store.load_run(run_id)
    original_session = internal["nodes"]["analyze"]["session_id"]
    original_fingerprint = internal["nodes"]["analyze"]["cache_fingerprint"]
    substituted_session = f"substituted-{completion_damage}-session"
    substituted_fingerprint = "f" * 64
    attempt_id = result["nodes"]["analyze"]["attempts"][-1]["attempt_id"]

    def damage_completion(event) -> None:
        projection = event["projection"]
        node = projection["nodes"]["analyze"]
        attempt = node["attempts"][-1]
        attempt.pop("session_registry_authority", None)
        attempt["metadata"]["session_id"] = substituted_session
        attempt["metadata"]["cache_fingerprint"] = substituted_fingerprint
        node["session_id"] = substituted_session
        node["cache_fingerprint"] = substituted_fingerprint
        if completion_damage in {"recovery-list", "all"}:
            node["session_recoveries"] = []
        if completion_damage in {"normalizer", "all"}:
            projection["language"]["normalizer_version"] = 2
        if completion_damage in {"event-type", "all"}:
            event["event_type"] = "node_completion_rewritten"
        for artifact in projection["artifacts"]:
            if artifact.get("attempt_id") == attempt_id:
                artifact["session_id"] = substituted_session
        event["payload"]["metadata"]["session_id"] = substituted_session
        event["payload"]["metadata"][
            "cache_fingerprint"
        ] = substituted_fingerprint
        for artifact in event["payload"].get("artifacts", []):
            artifact["session_id"] = substituted_session

    _rewrite_journal_event(store, run_id, "node_succeeded", damage_completion)
    with sqlite3.connect(store.database) as connection:
        assert connection.execute(
            "SELECT 1 FROM session_recovery_selection_authority "
            "WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone() is not None
        connection.execute(
            "DELETE FROM session_registry_winner_authority WHERE attempt_id=?",
            (attempt_id,),
        )
        connection.commit()

    public_reads = (
        lambda: store.tail_events(run_id),
        lambda: store.latest_event_page(run_id),
        lambda: store.events_after(run_id),
        lambda: EvidenceReader(store).query(run_id, kind="timeline"),
    )
    for read_public in public_reads:
        with pytest.raises(JournalRecoveryError) as exc_info:
            read_public()
        diagnostic = str(exc_info.value)
        for private_value in (
            original_session,
            original_fingerprint,
            substituted_session,
            substituted_fingerprint,
        ):
            assert private_value not in diagnostic

    from plugins.workflow.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(home),
        "events",
        run_id,
        "--json",
    ])
    assert args.func(args) == 70
    cli_output = capsys.readouterr().out
    for private_value in (
        original_session,
        original_fingerprint,
        substituted_session,
        substituted_fingerprint,
    ):
        assert private_value not in cli_output


def _assert_private_values_absent(
    read_public: Callable[[], object],
    private_values: tuple[str, ...],
) -> None:
    try:
        public = read_public()
    except JournalRecoveryError as exc:
        encoded = str(exc)
    else:
        encoded = json.dumps(public, sort_keys=True)
    for private_value in private_values:
        assert private_value not in encoded


def _rewrite_recovery_completion_chain(
    store: RunStore,
    run_id: str,
    mutate: Callable[[dict[str, object], str], None],
) -> None:
    directory = store.run_directory(run_id)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    start = next(
        index
        for index, event in enumerate(events)
        if event.get("event_type") == "node_succeeded"
    )
    for event in events[start:]:
        projection = event["projection"]
        mutate(projection, str(event.get("event_type")))
        event["projection_sha256"] = hashlib.sha256(
            json.dumps(
                projection,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        material = dict(event)
        material.pop("frame_sha256", None)
        event["frame_sha256"] = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    completion = events[start]
    completion_payload = completion.setdefault("payload", {})
    assert isinstance(completion_payload, dict)
    completion_metadata = completion_payload.setdefault("metadata", {})
    assert isinstance(completion_metadata, dict)
    completion_metadata["session_id"] = "substituted-selection-session"
    completion_metadata["cache_fingerprint"] = "f" * 64
    completion["frame_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in completion.items() if key != "frame_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    (directory / "run.json").write_text("{broken", encoding="utf-8")


def _authenticated_workflow_api_client(home):
    import importlib.util
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    name = f"workflow_dashboard_api_privacy_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.local_admin_authenticated = True
        return await call_next(request)

    app.include_router(module.router, prefix="/api/plugins/workflow")
    return TestClient(app)


def test_failed_fresh_recovery_selection_is_the_public_privacy_boundary(
    tmp_path, workflow_writer, capsys, monkeypatch
) -> None:
    """A normal failed fresh request must not publish its exact fingerprint."""
    package = _archon_package(
        workflow_writer,
        tmp_path / "fresh-failure-public-privacy",
        name="fresh-failure-public-privacy",
    )
    home = tmp_path / "fresh-failure-public-privacy-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, "fresh-failure-privacy-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    runner.fresh_failure = True
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "fresh-failure-privacy",
    )
    assert result["status"] == "failed"
    internal = store.load_run(run_id)
    fingerprint = internal["nodes"]["analyze"]["cache_fingerprint"]
    assert len(fingerprint) == 64

    for read_public in (
        lambda: store.get_run_status(run_id),
        lambda: store.tail_events(run_id),
        lambda: store.latest_event_page(run_id),
        lambda: store.events_after(run_id),
        lambda: EvidenceReader(store).query(run_id, kind="timeline"),
    ):
        _assert_private_values_absent(read_public, (fingerprint,))

    monkeypatch.setenv("HERMES_HOME", str(home))
    api = _authenticated_workflow_api_client(home)
    api_responses = (
        api.get(f"/api/plugins/workflow/runs/{run_id}"),
        api.get(f"/api/plugins/workflow/runs/{run_id}/events"),
        api.get(
            f"/api/plugins/workflow/runs/{run_id}/evidence",
            params={"kind": "timeline"},
        ),
    )
    assert all(response.status_code == 200 for response in api_responses)
    assert all(fingerprint not in response.text for response in api_responses)

    from plugins.workflow.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(home),
        "events",
        run_id,
        "--json",
    ])
    assert args.func(args) == 0
    assert fingerprint not in capsys.readouterr().out


@pytest.mark.parametrize(
    "projection_damage",
    ("attempt-state", "node-state", "run-state", "all-states", "attempt-removed", "node-removed"),
)
@pytest.mark.parametrize("typed_output", (False, True), ids=("schemaless", "typed"))
def test_selected_recovery_privacy_does_not_depend_on_mutable_projection_state(
    tmp_path, workflow_writer, capsys, projection_damage, typed_output
) -> None:
    """A selected attempt stays private even when recomputed state removes success."""
    package = _archon_package(
        workflow_writer,
        tmp_path / f"selection-state-privacy-{projection_damage}-{typed_output}",
        name=f"selection-state-privacy-{projection_damage}-{typed_output}",
        nodes=[
            {
                "id": "analyze",
                "prompt": "Analyze",
                **({"output_type": "RecoveredReport"} if typed_output else {}),
            }
        ],
    )
    home = tmp_path / f"selection-state-privacy-{projection_damage}-{typed_output}-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, f"{projection_damage}-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        f"{projection_damage}-recovery",
    )
    internal = store.load_run(run_id)
    node = internal["nodes"]["analyze"]
    original_session = node["session_id"]
    original_fingerprint = node["cache_fingerprint"]
    attempt_id = result["nodes"]["analyze"]["attempts"][-1]["attempt_id"]
    substituted_session = "substituted-selection-session"
    substituted_fingerprint = "f" * 64

    def damage(projection, _event_type) -> None:
        projected_nodes = projection["nodes"]
        projected_node = projected_nodes.get("analyze")
        if isinstance(projected_node, dict):
            attempts = projected_node.get("attempts", [])
            selected_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.get("attempt_id") == attempt_id
                ),
                None,
            )
            if isinstance(selected_attempt, dict):
                selected_attempt.pop("session_registry_authority", None)
                selected_attempt["metadata"]["session_id"] = substituted_session
                selected_attempt["metadata"][
                    "cache_fingerprint"
                ] = substituted_fingerprint
                if projection_damage in {"attempt-state", "all-states"}:
                    selected_attempt["state"] = "failed"
            projected_node["session_id"] = substituted_session
            projected_node["cache_fingerprint"] = substituted_fingerprint
            if projection_damage in {"node-state", "all-states"}:
                projected_node["state"] = "failed"
            if projection_damage == "attempt-removed":
                projected_node["attempts"] = [
                    attempt
                    for attempt in attempts
                    if attempt.get("attempt_id") != attempt_id
                ]
        if projection_damage == "node-removed":
            projected_nodes.pop("analyze", None)
        if projection_damage in {"run-state", "all-states"}:
            projection["status"] = "failed"
        for artifact in projection.get("artifacts", []):
            if artifact.get("attempt_id") == attempt_id:
                artifact["session_id"] = substituted_session

    _rewrite_recovery_completion_chain(store, run_id, damage)
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "DELETE FROM session_registry_winner_authority WHERE attempt_id=?",
            (attempt_id,),
        )
        connection.commit()

    private_values = (
        original_session,
        original_fingerprint,
        substituted_session,
        substituted_fingerprint,
    )
    for read_public in (
        lambda: store.get_run_status(run_id),
        lambda: store.tail_events(run_id),
        lambda: store.latest_event_page(run_id),
        lambda: store.events_after(run_id),
        lambda: EvidenceReader(store).query(run_id, kind="timeline"),
    ):
        _assert_private_values_absent(read_public, private_values)

    from plugins.workflow.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(home),
        "events",
        run_id,
        "--json",
    ])
    assert args.func(args) in {0, 70}
    output = capsys.readouterr().out
    for private_value in private_values:
        assert private_value not in output


def test_selection_precommit_identity_redacts_events_when_activation_is_rewritten(
    tmp_path, workflow_writer
) -> None:
    """A damaged activation frame remains inert authority but cannot leak identity."""
    package = _archon_package(
        workflow_writer,
        tmp_path / "selection-activation-privacy",
        name="selection-activation-privacy",
    )
    home = tmp_path / "selection-activation-privacy-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _PersistentRunner()
    _run_once(store, package, runner, registry, "activation-privacy-seed")
    runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    runner.fresh_failure = True
    run_id, result = _run_once(
        store,
        package,
        runner,
        registry,
        "activation-privacy",
    )
    attempt_id = result["nodes"]["analyze"]["attempts"][-1]["attempt_id"]
    substituted_session = "rewritten-activation-session"
    substituted_fingerprint = "f" * 64

    def rewrite_activation(event) -> None:
        event["event_type"] = "selection_activation_rewritten"
        event["payload"]["session_id"] = substituted_session
        event["payload"]["cache_fingerprint"] = substituted_fingerprint
        projection = event["projection"]
        node = projection["nodes"]["analyze"]
        attempt = next(
            item
            for item in node["attempts"]
            if item["attempt_id"] == attempt_id
        )
        metadata = attempt.setdefault("metadata", {})
        metadata["session_id"] = substituted_session
        metadata["cache_fingerprint"] = substituted_fingerprint
        node["session_id"] = substituted_session
        node["cache_fingerprint"] = substituted_fingerprint

    _rewrite_journal_event(
        store,
        run_id,
        "persistent_session_missing_fresh_start",
        rewrite_activation,
    )

    for read_public in (store.tail_events, store.latest_event_page):
        _assert_private_values_absent(
            lambda read_public=read_public: read_public(run_id),
            (substituted_session, substituted_fingerprint),
        )


@pytest.mark.parametrize("completion_status", ("cancelled", "interrupted"))
def test_selected_nonwinning_completion_redacts_private_session_fields(
    tmp_path, workflow_writer, completion_status
) -> None:
    package = _archon_package(
        workflow_writer,
        tmp_path / f"selection-{completion_status}-privacy",
        name=f"selection-{completion_status}-privacy",
    )
    store = RunStore(tmp_path / f"selection-{completion_status}-privacy-home")
    admitted = _admit(store, package, f"selection-{completion_status}-privacy")
    claim = store.claim_node(admitted.run_id, "analyze", "selection-owner")
    assert claim is not None
    store.mark_node_started(claim)
    session_id = f"private-{completion_status}-session"
    fingerprint = "a" * 64
    assert store.record_persistent_session_recovery_selection(
        claim,
        PersistentSessionRecoverySelection(
            key=NodeSessionKey(
                package.definition.name,
                "analyze",
                "local",
                "fake-provider",
                "default",
            ),
            expected_generation=0,
            missing_session_id="missing-session",
            cache_fingerprint=fingerprint,
            run_id=admitted.run_id,
            attempt_id=claim.attempt_id,
        ),
    )
    store.complete_node(
        claim,
        status=completion_status,
        error_code=completion_status,
        metadata={
            "session_id": session_id,
            "cache_fingerprint": fingerprint,
        },
    )

    for read_public in (
        lambda: store.get_run_status(admitted.run_id),
        lambda: store.tail_events(admitted.run_id),
        lambda: store.latest_event_page(admitted.run_id),
        lambda: store.events_after(admitted.run_id),
        lambda: EvidenceReader(store).query(admitted.run_id, kind="timeline"),
    ):
        _assert_private_values_absent(read_public, (session_id, fingerprint))
