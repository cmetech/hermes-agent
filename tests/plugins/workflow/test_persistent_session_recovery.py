from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import sqlite3
from typing import Callable

import pytest

from agent.plugin_agent import (
    PluginAgentRunResult,
    PluginAgentRunner,
    PluginAgentSessionMissingError,
)
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.language_schema import compatibility_code_catalog
from plugins.workflow.models import (
    DeadlineBudget,
    WorkflowLanguageProfile,
    WorkflowNode,
    freeze_value,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.sanitize import public_run_projection
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import (
    NodeSessionKey,
    NodeSessionRegistry,
    SessionRegistryUpdateCandidate,
)
from plugins.workflow.store import JournalRecoveryError, RunStore, StorageQuotaError
from tools.managed_process import ProcessIdentity


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
