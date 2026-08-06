from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import threading

import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, parse_workflow_source_bytes
from plugins.workflow.store import (
    ArtifactRef,
    JournalRecoveryError,
    RunStore,
    StorageQuotaError,
    TypedPublicationCandidate,
)
from plugins.workflow.trust import WorkflowPackageDigest
from tools.managed_process import ManagedProcessTree, ProcessIdentity


def _reframe(event: dict[str, object]) -> dict[str, object]:
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
    return event


def _run(store, package, *, idempotency_key="crash"):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=idempotency_key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


class _CountedLoopRunner:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=self.response,
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


class _LeaseClock:
    def __init__(self, sample: LeaseClockSample) -> None:
        self.sample = sample

    def __call__(self) -> LeaseClockSample:
        return self.sample


def _phase4_signal_run(
    tmp_path,
    workflow_writer,
    *,
    downstream: bool = False,
    loop_overrides: dict[str, object] | None = None,
    node_overrides: dict[str, object] | None = None,
    lease_clock=None,
    admission_overrides: dict[str, object] | None = None,
):
    loop = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "interactive": True,
        "gate_message": "Accept or refine",
    }
    loop.update(loop_overrides or {})
    nodes = [
        {
            "id": "refine",
            "loop": loop,
            **(node_overrides or {}),
        }
    ]
    if downstream:
        nodes.append({
            "id": "after",
            "bash": "printf downstream",
            "depends_on": ["refine"],
        })
    workflow = workflow_writer(
        tmp_path / "signal-crash-source" / "workflows",
        name="signal-crash",
        interactive=True,
        nodes=nodes,
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    home = tmp_path / "signal-crash-home"
    store = (
        RunStore(home, lease_clock=lease_clock)
        if lease_clock is not None
        else RunStore(home)
    )
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admission = {
        "workflow_name": "signal-crash",
        "definition_digest": prepared.definition_digest,
        "policy_digest": prepared.policy_digest,
        "input_manifest_digest": prepared.input_manifest_digest,
        "trigger_source": "cli",
        "idempotency_key": "signal-crash",
        "concurrency_key": "signal-crash",
    }
    admission.update(admission_overrides or {})
    admitted = store.start_run(
        RunAdmissionRequest(**admission),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return home, store, admitted.run_id


def _expired_loop_recovery_scheduler(
    home,
    run_id: str,
    *,
    agent_runner,
) -> RunScheduler:
    restarted = RunStore(home)
    projection = restarted.load_run(run_id)
    claims = [
        node["claim"]
        for node in projection.get("nodes", {}).values()
        if isinstance(node, dict)
        and isinstance(node.get("loop_state"), dict)
        and node["loop_state"].get("_pending_loop_decision") is not None
        and isinstance(node.get("claim"), dict)
    ]
    if not claims:
        return RunScheduler(restarted, agent_runner=agent_runner)
    assert len(claims) == 1
    claim = claims[0]
    lease_seconds = float(claim["lease_seconds"])
    expired_utc = datetime.fromisoformat(claim["lease_expires_at"]) + timedelta(
        seconds=1
    )
    expired_monotonic = float(claim["heartbeat_monotonic"]) + lease_seconds + 1
    return RunScheduler(
        restarted,
        agent_runner=agent_runner,
        utcnow=lambda: expired_utc,
        monotonic=lambda: expired_monotonic,
    )


def _recorded_loop_recovery_takeover(
    tmp_path,
    workflow_writer,
    monkeypatch,
):
    _, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
    )
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated loss before recovery takeover")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="before recovery takeover"):
        RunScheduler(
            store,
            agent_runner=_CountedLoopRunner("draft <promise>DONE</promise>"),
        ).advance(run_id)

    recorded_claim = store.load_run(run_id)["nodes"]["refine"]["claim"]
    first_now = LeaseClockSample(
        datetime.fromisoformat(recorded_claim["lease_expires_at"])
        + timedelta(seconds=1),
        float(recorded_claim["heartbeat_monotonic"])
        + float(recorded_claim["lease_seconds"])
        + 1.0,
        "recorded-loop-recovery-boot",
    )
    stale = store.recorded_loop_decision(
        run_id,
        recovery_owner_id="recorded-loop-recoverer-a",
        now=first_now,
        lease_seconds=1,
    )
    assert stale is not None
    winner_now = LeaseClockSample(
        first_now.utc_now + timedelta(seconds=2),
        first_now.monotonic_now + 2.0,
        first_now.boot_id,
    )
    winner = store.recorded_loop_decision(
        run_id,
        recovery_owner_id="recorded-loop-recoverer-b",
        now=winner_now,
        lease_seconds=30,
    )
    assert winner is not None
    return store, run_id, stale, winner, winner_now


def _worker_claim_record(
    store: RunStore,
    attempt_id: str,
) -> dict[str, object] | None:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT owner_id, lease_expires_at FROM worker_claims "
            "WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
    return dict(row) if row is not None else None


@pytest.mark.parametrize(
    (
        "case",
        "response",
        "loop_overrides",
        "expected_status",
        "pending_type",
    ),
    [
        (
            "signal-immediate",
            "draft <promise>DONE</promise>",
            {"signal_completes": True},
            "succeeded",
            None,
        ),
        (
            "signal-confirmation",
            "draft <promise>DONE</promise>",
            {},
            "paused",
            "loop_signal_confirmation",
        ),
        ("ordinary-input", "draft", {}, "paused", "loop_input"),
        (
            "until-bash",
            "draft",
            {"until_bash": "exit 0"},
            "succeeded",
            None,
        ),
        (
            "until-bash-input",
            "draft",
            {"until_bash": "exit 1"},
            "paused",
            "loop_input",
        ),
        (
            "hard-limit",
            "draft",
            {"max_iterations": 1},
            "failed",
            None,
        ),
    ],
)
def test_restart_publishes_every_recorded_v4_loop_decision_without_provider_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
    case: str,
    response: str,
    loop_overrides: dict[str, object],
    expected_status: str,
    pending_type: str | None,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path / case,
        workflow_writer,
        loop_overrides=loop_overrides,
    )
    runner = _CountedLoopRunner(response)
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(
        store,
        "record_loop_iteration",
        crash_after_iteration_record,
    )
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(store, agent_runner=runner).advance(run_id)
    assert len(runner.requests) == 1
    assert len(store.load_run(run_id)["artifacts"]) == 1

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("restart replayed a recorded loop provider result")

    scheduler = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=NoReplayRunner(),
    )
    restarted = scheduler.store
    recovered = scheduler.advance(run_id)

    assert recovered["status"] == expected_status
    assert len(recovered["artifacts"]) == 1
    pending = recovered["nodes"]["refine"].get("pending_interaction")
    if pending_type is None:
        assert pending is None
    else:
        assert pending["type"] == pending_type
    assert len(runner.requests) == 1


@pytest.mark.parametrize("mutation", ("foreign_attempt", "extra_field"))
def test_restart_rejects_malformed_or_foreign_loop_decision_authority(
    tmp_path,
    workflow_writer,
    monkeypatch,
    mutation: str,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path / mutation,
        workflow_writer,
        loop_overrides={"signal_completes": True},
    )
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(
            store,
            agent_runner=_CountedLoopRunner("draft <promise>DONE</promise>"),
        ).advance(run_id)

    run_path = store.run_directory(run_id) / "run.json"
    projection = json.loads(run_path.read_text())
    decision = projection["nodes"]["refine"]["loop_state"][
        "_pending_loop_decision"
    ]
    if mutation == "foreign_attempt":
        decision["attempt_id"] = "foreign-attempt"
    else:
        decision["unexpected"] = True
    run_path.write_text(json.dumps(projection), encoding="utf-8")

    with pytest.raises(JournalRecoveryError, match="journaled loop decision"):
        RunStore(home).recorded_loop_decision(run_id)


def test_concurrent_restart_publishes_recorded_loop_decision_once(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
    )
    runner = _CountedLoopRunner("draft <promise>DONE</promise>")
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(store, agent_runner=runner).advance(run_id)

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("concurrent recovery replayed the loop provider")

    def recover() -> str:
        recovered = _expired_loop_recovery_scheduler(
            home,
            run_id,
            agent_runner=NoReplayRunner(),
        ).advance(run_id)
        return str(recovered["status"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = tuple(pool.map(lambda _: recover(), range(2)))

    assert statuses == ("succeeded", "succeeded")
    assert len(runner.requests) == 1
    events = RunStore(home).tail_events(run_id)
    assert sum(event["event_type"] == "node_succeeded" for event in events) == 1


def test_scheduler_recovers_recorded_decision_across_claim_expiry_without_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
    )
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(
            store,
            agent_runner=_CountedLoopRunner("draft <promise>DONE</promise>"),
        ).advance(run_id)

    recorded = store.load_run(run_id)
    node = recorded["nodes"]["refine"]
    claim = dict(node["claim"])
    decision = dict(node["loop_state"]["_pending_loop_decision"])
    expired_at = datetime.fromisoformat(claim["lease_expires_at"]) + timedelta(
        seconds=1
    )
    expired_monotonic = (
        float(claim["heartbeat_monotonic"])
        + float(claim["lease_seconds"])
        + 1
    )

    assert store.expire_stale_claims(
        run_id,
        now=expired_at,
        monotonic_now=expired_monotonic,
    ) == ()
    preserved = store.load_run(run_id)
    assert preserved["status"] == "running"
    assert preserved["nodes"]["refine"]["claim"] == claim
    assert (
        preserved["nodes"]["refine"]["loop_state"]["_pending_loop_decision"]
        == decision
    )

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("expired recorded decision replayed the loop provider")

    recovered = RunScheduler(
        RunStore(home),
        agent_runner=NoReplayRunner(),
        utcnow=lambda: expired_at,
        monotonic=lambda: expired_monotonic,
    ).advance(run_id)

    assert recovered["status"] == "succeeded"
    assert recovered["nodes"]["refine"]["attempts"][-1]["attempt_id"] == (
        claim["attempt_id"]
    )


def test_foreground_adoption_routes_recorded_decision_through_recovery_without_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    started_at = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(started_at, 100.0, "boot-a"))
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
        lease_clock=clock,
        admission_overrides={
            "execution_mode": "foreground",
            "foreground_owner_id": "foreground-loop-owner",
            "foreground_lease_seconds": 1,
        },
    )
    foreground = store.load_run(run_id)
    foreground_epoch = int(foreground["foreground_epoch"])
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated foreground loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="foreground loss"):
        RunScheduler(
            store,
            owner_id="foreground-loop-worker",
            execution_owner_id="foreground-loop-owner",
            execution_owner_epoch=foreground_epoch,
            agent_runner=_CountedLoopRunner("draft <promise>DONE</promise>"),
            utcnow=lambda: started_at,
            monotonic=lambda: 100.0,
        ).advance(run_id)

    recorded = store.load_run(run_id)
    claim = dict(recorded["nodes"]["refine"]["claim"])
    decision = dict(
        recorded["nodes"]["refine"]["loop_state"]["_pending_loop_decision"]
    )
    adopted_at = datetime.fromisoformat(
        str(recorded["foreground_lease_expires_at"])
    ) + timedelta(seconds=1)
    adopted_monotonic = (
        float(recorded["foreground_heartbeat_monotonic"])
        + float(recorded["foreground_lease_seconds"])
        + 1.0
    )
    clock.sample = LeaseClockSample(
        adopted_at,
        adopted_monotonic,
        "boot-a",
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="recorded-loop-adopter",
        host_kind="gateway",
        host_instance_id="recorded-loop-adopter-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database, clock=clock).try_acquire(
        identity,
        now=adopted_at,
        lease_seconds=30,
    )
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)

    adopted = store.adopt_expired_foreground(run_id, fence, adopted_at)

    assert adopted["execution_mode"] == "background"
    assert adopted["status"] == "running"
    assert adopted["nodes"]["refine"]["claim"]["attempt_id"] == claim[
        "attempt_id"
    ]
    assert (
        adopted["nodes"]["refine"]["loop_state"]["_pending_loop_decision"]
        == decision
    )

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("foreground adoption replayed the recorded loop provider")

    recovered = RunScheduler(
        store,
        execution_fence=fence,
        agent_runner=NoReplayRunner(),
        utcnow=lambda: adopted_at,
        monotonic=lambda: adopted_monotonic,
    ).advance(run_id)

    assert recovered["status"] == "succeeded"
    assert recovered["nodes"]["refine"]["attempts"][-1]["attempt_id"] == claim[
        "attempt_id"
    ]


def test_stale_recorded_loop_recoverer_cannot_schedule_retry_after_takeover(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    store, run_id, stale, winner, winner_now = _recorded_loop_recovery_takeover(
        tmp_path,
        workflow_writer,
        monkeypatch,
    )
    before = store.load_run(run_id)
    events_before = store.tail_events(run_id)

    with pytest.raises(RuntimeError, match="stale node completion"):
        store.schedule_retry(
            stale.claim,
            next_attempt_at=winner_now.utc_now + timedelta(seconds=30),
            error_code="provider_error",
        )

    assert store.load_run(run_id) == before
    assert store.tail_events(run_id) == events_before
    worker = _worker_claim_record(store, winner.claim.attempt_id)
    assert worker is not None
    assert worker["owner_id"] == winner.claim.owner_id
    assert worker["lease_expires_at"] == winner.claim.lease_expires_at.isoformat()


def test_stale_recorded_loop_recoverer_cannot_block_cleanup_after_takeover(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    store, run_id, stale, winner, _ = _recorded_loop_recovery_takeover(
        tmp_path,
        workflow_writer,
        monkeypatch,
    )
    before = store.load_run(run_id)
    events_before = store.tail_events(run_id)

    with pytest.raises(RuntimeError, match="stale cleanup failure"):
        store.block_cleanup_failed(
            stale.claim,
            error_message="stale recoverer cleanup failed",
        )

    assert store.load_run(run_id) == before
    assert store.tail_events(run_id) == events_before
    worker = _worker_claim_record(store, winner.claim.attempt_id)
    assert worker is not None
    assert worker["owner_id"] == winner.claim.owner_id
    assert worker["lease_expires_at"] == winner.claim.lease_expires_at.isoformat()


def test_stale_fenced_executor_cannot_release_recorded_loop_recovery_winner(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    started_at = datetime(2026, 8, 6, 13, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(started_at, 200.0, "boot-a"))
    _, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
        lease_clock=clock,
    )
    process = ProcessIdentity.capture(os.getpid())
    identity_a = CoordinatorIdentity(
        owner_id="recorded-loop-coordinator-a",
        host_kind="gateway",
        host_instance_id="recorded-loop-host-a",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    coordinator = CoordinatorStore(store.database, clock=clock)
    leadership_a = coordinator.try_acquire(
        identity_a,
        now=started_at,
        lease_seconds=1,
    )
    assert leadership_a.is_leader
    fence_a = ExecutionFence(identity_a.owner_id, leadership_a.lease.epoch)
    claims = []
    original_claim = store.claim_node

    def capture_claim(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        if claim is not None:
            claims.append(claim)
        return claim

    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated stale coordinator before publication")

    monkeypatch.setattr(store, "claim_node", capture_claim)
    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    runner = _CountedLoopRunner("draft <promise>DONE</promise>")
    scheduler_a = RunScheduler(
        store,
        owner_id="recorded-loop-worker-a",
        execution_fence=fence_a,
        agent_runner=runner,
        utcnow=lambda: started_at,
        monotonic=lambda: 200.0,
    )
    with pytest.raises(SystemExit, match="stale coordinator"):
        scheduler_a.advance(run_id)
    assert len(claims) == 1
    assert len(runner.requests) == 1

    recorded = store.load_run(run_id)
    prepared = scheduler_a._prepare_run_package(
        run_id,
        None,
        expected_state_version=int(recorded["state_version"]),
    )
    assert prepared is not None
    package, execution_limits, resource_paths, resource_bytes, semantics = prepared
    node = next(node for node in package.definition.nodes if node.id == "refine")

    takeover_at = started_at + timedelta(seconds=2)
    clock.sample = LeaseClockSample(takeover_at, 202.0, "boot-a")
    identity_b = CoordinatorIdentity(
        owner_id="recorded-loop-coordinator-b",
        host_kind="gateway",
        host_instance_id="recorded-loop-host-b",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership_b = coordinator.try_acquire(
        identity_b,
        now=takeover_at,
        lease_seconds=30,
    )
    assert leadership_b.is_leader
    fence_b = ExecutionFence(identity_b.owner_id, leadership_b.lease.epoch)
    winner = store.recorded_loop_decision(
        run_id,
        recovery_owner_id="recorded-loop-recoverer-b",
        now=clock.sample,
        lease_seconds=30,
        execution_fence=fence_b,
    )
    assert winner is not None
    before = store.load_run(run_id)
    events_before = store.tail_events(run_id)
    worker_before = _worker_claim_record(store, winner.claim.attempt_id)
    assert worker_before is not None

    scheduler_a._execute_claim(
        run_id,
        claims[0],
        node,
        package,
        recorded,
        None,
        execution_limits,
        semantics,
        resource_paths,
        resource_bytes,
    )

    assert store.load_run(run_id) == before
    assert store.tail_events(run_id) == events_before
    worker_after = _worker_claim_record(store, winner.claim.attempt_id)
    assert worker_after is not None
    assert worker_after == worker_before
    assert worker_after["owner_id"] == winner.claim.owner_id
    assert len(runner.requests) == 1


def test_fresh_recorded_loop_decision_cannot_be_taken_over_from_live_executor(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={
            "until_bash": 'printf x >> "$ARTIFACTS_DIR/live-predicate"; exit 1',
        },
    )
    original_runner = _CountedLoopRunner("draft")
    original_record = store.record_loop_iteration
    iteration_recorded = threading.Event()
    release_original = threading.Event()

    def block_live_executor_after_record(*args, **kwargs):
        original_record(*args, **kwargs)
        iteration_recorded.set()
        assert release_original.wait(timeout=10)

    monkeypatch.setattr(
        store,
        "record_loop_iteration",
        block_live_executor_after_record,
    )
    original_scheduler = RunScheduler(store, agent_runner=original_runner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(original_scheduler.advance, run_id)
        assert iteration_recorded.wait(timeout=10)
        before = store.load_run(run_id)
        attempt_id = before["nodes"]["refine"]["claim"]["attempt_id"]
        takeover_runner = _CountedLoopRunner("must not run")
        try:
            observed = RunScheduler(
                RunStore(home),
                agent_runner=takeover_runner,
            ).advance(run_id)

            assert observed["status"] == "running"
            assert observed["nodes"]["refine"]["claim"]["attempt_id"] == attempt_id
            assert takeover_runner.requests == []
            assert not (
                store.run_directory(run_id) / "artifacts" / "live-predicate"
            ).exists()
            assert not any(
                event["event_type"]
                in {
                    "loop_predicate_recovery_prepared",
                    "loop_decision_recorded",
                    "loop_continuation_recovered",
                    "node_paused",
                    "node_succeeded",
                }
                for event in store.tail_events(run_id)
            )
        finally:
            release_original.set()
        completed = original.result(timeout=10)

    assert completed["status"] == "paused"
    assert (
        store.run_directory(run_id) / "artifacts" / "live-predicate"
    ).read_bytes() == b"x"
    assert len(original_runner.requests) == 1


def test_restart_preserves_typed_loop_publication_attempt_authority(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"signal_completes": True},
        node_overrides={"output_type": "LoopReport"},
    )
    runner = _CountedLoopRunner("typed <promise>DONE</promise>")
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(store, agent_runner=runner).advance(run_id)

    recovery_runner = _CountedLoopRunner("must not run")
    recovered = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=recovery_runner,
    ).advance(run_id)

    attempt_id = recovered["nodes"]["refine"]["attempts"][-1]["attempt_id"]
    publication = next(
        artifact
        for artifact in recovered["artifacts"]
        if artifact.get("publication_id")
    )
    assert recovered["status"] == "succeeded"
    assert publication["attempt_id"] == attempt_id
    assert publication["output_type"] == "LoopReport"
    assert recovery_runner.requests == []


def test_restart_continues_after_recorded_noninteractive_iteration_without_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={"interactive": False, "signal_completes": True},
    )
    first = _CountedLoopRunner("draft")
    original_record = store.record_loop_iteration

    def crash_after_iteration_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated process loss after iteration journal")

    monkeypatch.setattr(store, "record_loop_iteration", crash_after_iteration_record)
    with pytest.raises(SystemExit, match="after iteration journal"):
        RunScheduler(store, agent_runner=first).advance(run_id)

    second = _CountedLoopRunner("refined <promise>DONE</promise>")
    scheduler = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=second,
    )
    recovered = scheduler.advance(run_id)

    assert recovered["status"] == "running"
    assert recovered["nodes"]["refine"]["state"] == "ready"
    assert len(second.requests) == 0

    completed = RunScheduler(
        scheduler.store,
        agent_runner=second,
    ).advance(run_id)

    assert completed["status"] == "succeeded"
    assert len(first.requests) == 1
    assert len(second.requests) == 1


@pytest.mark.parametrize("crash_after_final_decision", (False, True))
def test_restart_reconciles_until_bash_crash_window_without_provider_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
    crash_after_final_decision: bool,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={
            "until_bash": 'printf x >> "$ARTIFACTS_DIR/predicate-count"',
        },
    )
    runner = _CountedLoopRunner("draft")
    original_decision = store.record_loop_decision

    def crash_at_decision(*args, **kwargs):
        if crash_after_final_decision:
            original_decision(*args, **kwargs)
        raise SystemExit("simulated process loss around predicate decision")

    monkeypatch.setattr(store, "record_loop_decision", crash_at_decision)
    with pytest.raises(SystemExit, match="around predicate decision"):
        RunScheduler(store, agent_runner=runner).advance(run_id)

    counter = store.run_directory(run_id) / "artifacts" / "predicate-count"
    assert counter.read_bytes() == b"x"

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("predicate recovery replayed the loop provider")

    scheduler = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=NoReplayRunner(),
    )
    restarted = scheduler.store
    recovered = scheduler.advance(run_id)

    assert recovered["status"] == "succeeded"
    assert counter.read_bytes() == (b"x" if crash_after_final_decision else b"xx")
    assert len(runner.requests) == 1
    recovery_events = [
        event
        for event in restarted.tail_events(run_id)
        if event["event_type"] == "loop_predicate_recovery_prepared"
    ]
    assert len(recovery_events) == (0 if crash_after_final_decision else 1)


def test_until_bash_recovery_propagates_decision_storage_failure_without_redispatch(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={
            "until_bash": 'printf x >> "$ARTIFACTS_DIR/predicate-quota-count"',
        },
    )
    runner = _CountedLoopRunner("draft")
    original_record = store.record_loop_iteration

    def crash_before_predicate(*args, **kwargs):
        original_record(*args, **kwargs)
        raise SystemExit("simulated loss before quota predicate")

    monkeypatch.setattr(store, "record_loop_iteration", crash_before_predicate)
    with pytest.raises(SystemExit, match="before quota predicate"):
        RunScheduler(store, agent_runner=runner).advance(run_id)

    recorded_claim = store.load_run(run_id)["nodes"]["refine"]["claim"]
    expired_at = datetime.fromisoformat(
        recorded_claim["lease_expires_at"]
    ) + timedelta(seconds=1)
    expired_monotonic = (
        float(recorded_claim["heartbeat_monotonic"])
        + float(recorded_claim["lease_seconds"])
        + 1.0
    )
    restarted = RunStore(home)

    def fail_decision_journal(*_args, **_kwargs):
        raise StorageQuotaError("injected loop decision quota failure")

    monkeypatch.setattr(restarted, "record_loop_decision", fail_decision_journal)

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("decision storage failure replayed the loop provider")

    with pytest.raises(StorageQuotaError, match="injected loop decision"):
        RunScheduler(
            restarted,
            agent_runner=NoReplayRunner(),
            utcnow=lambda: expired_at,
            monotonic=lambda: expired_monotonic,
        ).advance(run_id)

    counter = store.run_directory(run_id) / "artifacts" / "predicate-quota-count"
    assert counter.read_bytes() == b"x"
    assert len(runner.requests) == 1
    failed_closed = restarted.load_run(run_id)
    assert (
        failed_closed["nodes"]["refine"]["loop_state"][
            "_pending_loop_decision"
        ]["kind"]
        == "until_bash_pending"
    )


def test_until_bash_recovery_authenticates_one_shot_feedback_without_provider_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={
            "until_bash": "test $LOOP_USER_INPUT = 'accept predicate'",
        },
    )
    first = RunScheduler(
        store,
        agent_runner=_CountedLoopRunner("draft"),
    ).advance(run_id)
    pending = first["nodes"]["refine"]["pending_interaction"]
    assert pending["type"] == "loop_input"
    ready = store.provide_loop_input(
        run_id,
        "accept predicate",
        expected_state_version=first["state_version"],
        interaction_id=pending["interaction_id"],
    )
    feedback_path = ready["nodes"]["refine"]["loop_user_input_artifact"]
    second_runner = _CountedLoopRunner("refined")
    original_record = store.record_loop_iteration

    def crash_after_second_iteration(*args, **kwargs):
        original_record(*args, **kwargs)
        if kwargs["loop_state"]["iteration"] == 2:
            raise SystemExit("simulated loss before feedback predicate")

    monkeypatch.setattr(
        store,
        "record_loop_iteration",
        crash_after_second_iteration,
    )
    with pytest.raises(SystemExit, match="before feedback predicate"):
        RunScheduler(store, agent_runner=second_runner).advance(run_id)

    recorded = store.load_run(run_id)
    assert recorded["nodes"]["refine"]["loop_user_input_artifact"] == feedback_path

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("feedback predicate recovery replayed the provider")

    recovered = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=NoReplayRunner(),
    ).advance(run_id)

    assert recovered["status"] == "succeeded"
    assert recovered["nodes"]["refine"]["loop_state"]["completed_by"] == (
        "until_bash"
    )
    assert recovered["nodes"]["refine"].get("loop_user_input_artifact") is None
    assert len(second_runner.requests) == 1


@pytest.mark.parametrize("mutation", ("same_size_digest", "symlink"))
def test_until_bash_recovery_rejects_changed_feedback_before_predicate_dispatch(
    tmp_path,
    workflow_writer,
    monkeypatch,
    mutation: str,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        loop_overrides={
            "until_bash": (
                'printf x >> "$ARTIFACTS_DIR/feedback-predicate-count"; '
                "test $LOOP_USER_INPUT = 'accept predicate'"
            ),
        },
    )
    first = RunScheduler(
        store,
        agent_runner=_CountedLoopRunner("draft"),
    ).advance(run_id)
    ready = store.provide_loop_input(
        run_id,
        "accept predicate",
        expected_state_version=first["state_version"],
        interaction_id=first["nodes"]["refine"]["pending_interaction"][
            "interaction_id"
        ],
    )
    relative_path = ready["nodes"]["refine"]["loop_user_input_artifact"]
    original_record = store.record_loop_iteration

    def crash_after_second_iteration(*args, **kwargs):
        original_record(*args, **kwargs)
        if kwargs["loop_state"]["iteration"] == 2:
            raise SystemExit("simulated loss before feedback predicate")

    monkeypatch.setattr(
        store,
        "record_loop_iteration",
        crash_after_second_iteration,
    )
    with pytest.raises(SystemExit, match="before feedback predicate"):
        RunScheduler(
            store,
            agent_runner=_CountedLoopRunner("refined"),
        ).advance(run_id)

    feedback_path = store.run_directory(run_id) / relative_path
    if mutation == "same_size_digest":
        feedback_path.write_text("reject predicate", encoding="utf-8")
    else:
        replacement = tmp_path / "foreign-feedback.txt"
        replacement.write_text("accept predicate", encoding="utf-8")
        feedback_path.unlink()
        feedback_path.symlink_to(replacement)

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("changed feedback recovery replayed the provider")

    scheduler = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=NoReplayRunner(),
    )
    with pytest.raises(JournalRecoveryError, match="loop feedback"):
        scheduler.advance(run_id)

    predicate_count = (
        store.run_directory(run_id) / "artifacts" / "feedback-predicate-count"
    )
    assert predicate_count.read_bytes() == b"x"
    assert "accept predicate" not in json.dumps(store.tail_events(run_id))


def test_restart_publishes_journaled_loop_signal_without_provider_replay(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    home, store, run_id = _phase4_signal_run(tmp_path, workflow_writer)
    runner = _CountedLoopRunner("draft <promise>DONE</promise>")
    original_complete = store.complete_node

    def crash_before_pause_publication(*args, **kwargs):
        if kwargs.get("status") == "paused":
            raise SystemExit("simulated process loss after iteration journal")
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(store, "complete_node", crash_before_pause_publication)
    with pytest.raises(SystemExit, match="simulated process loss"):
        RunScheduler(store, agent_runner=runner).advance(run_id)
    assert len(runner.requests) == 1
    assert any(
        event["event_type"] == "loop_iteration_completed"
        for event in store.tail_events(run_id)
    )

    monkeypatch.setattr(store, "complete_node", original_complete)

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("restart replayed a journaled provider result")

    scheduler = _expired_loop_recovery_scheduler(
        home,
        run_id,
        agent_runner=NoReplayRunner(),
    )
    restarted = scheduler.store
    recovered = scheduler.advance(run_id)

    assert recovered["status"] == "paused"
    pending = recovered["nodes"]["refine"]["pending_interaction"]
    assert pending["type"] == "loop_signal_confirmation"
    assert len(recovered["artifacts"]) == 1
    restarted.approve_run(
        run_id,
        expected_state_version=recovered["state_version"],
        interaction_id=pending["interaction_id"],
    )
    assert restarted.get_run_status(run_id)["status"] == "succeeded"
    assert len(runner.requests) == 1


def test_restart_schedules_downstream_once_after_signal_acceptance(
    tmp_path,
    workflow_writer,
) -> None:
    home, store, run_id = _phase4_signal_run(
        tmp_path,
        workflow_writer,
        downstream=True,
    )
    runner = _CountedLoopRunner("draft <promise>DONE</promise>")
    paused = RunScheduler(store, agent_runner=runner).advance(run_id)
    pending = paused["nodes"]["refine"]["pending_interaction"]

    store.approve_run(
        run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    class NoReplayRunner:
        def run(self, *_args, **_kwargs):
            pytest.fail("accepted signal replayed the loop provider")

    restarted = RunStore(home)
    completed = RunScheduler(
        restarted,
        agent_runner=NoReplayRunner(),
    ).advance(run_id)

    assert completed["status"] == "succeeded"
    assert completed["nodes"]["refine"]["state"] == "succeeded"
    assert completed["nodes"]["after"]["state"] == "succeeded"
    assert len(completed["nodes"]["after"]["attempts"]) == 1
    assert len(runner.requests) == 1


def test_expired_lease_interrupts_and_stale_completion_cannot_win(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    old = store.claim_node(admitted.run_id, "start", "dead", lease_seconds=1)
    assert old is not None

    assert store.expire_stale_claims(
        admitted.run_id, now=old.lease_expires_at + timedelta(seconds=1)
    ) == ("start",)
    assert store.load_run(admitted.run_id)["status"] == "interrupted"
    always_run_nodes = RunScheduler(store).verified_always_run_nodes(admitted.run_id)
    store.resume_run(
        admitted.run_id,
        always_run_nodes=always_run_nodes,
    )
    replacement = store.claim_node(admitted.run_id, "start", "replacement")
    assert replacement is not None

    with pytest.raises(RuntimeError, match="stale node completion"):
        store.complete_node(old, status="succeeded")
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == (
        "stale_node_completion"
    )
    store.complete_node(replacement, status="succeeded")
    assert store.load_run(admitted.run_id)["status"] == "succeeded"


def test_resolution_wait_restart_rebuilds_without_claim_or_immediate_poll(
    tmp_path, workflow_writer
) -> None:
    """Catch restart recovery treating a durable read wait as runnable work."""
    path = workflow_writer(
        tmp_path / "resolution-restart-package",
        name="resolution-restart",
        nodes=[
            {"id": "producer", "bash": "true"},
            {"id": "consumer", "bash": "true", "depends_on": ["producer"]},
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    home = tmp_path / "resolution-restart-home"
    store = RunStore(home)
    admitted = _run(store, load_workflow(path), idempotency_key="resolution-restart")
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    identity = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "publication_id": "a" * 32,
        "sha256": "b" * 64,
        "size_bytes": 5,
        "media_type": "text/markdown; charset=utf-8",
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": "text",
    }
    assert store.defer_output_resolution(
        admitted.run_id,
        "consumer",
        producer_identity=identity,
        now=observed,
    )
    (store.run_directory(admitted.run_id) / "run.json").unlink()

    restarted = RunStore(home)
    projection = restarted.load_run(admitted.run_id)
    assert projection["nodes"]["consumer"]["state"] == "waiting_resolution"
    assert projection["nodes"]["consumer"]["resolution_read_count"] == 1
    assert restarted.claim_node(
        admitted.run_id, "consumer", "restart-must-not-claim"
    ) is None
    assert restarted.wake_due_output_resolutions(
        admitted.run_id,
        now=observed + timedelta(milliseconds=249),
    ) == ()
    with restarted._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0] == 0


def test_expired_outward_attempt_preserves_identity_and_requires_reconciliation(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
        owner_epoch="leader-7",
        effect_classification="outward",
        evidence_paths=("nodes/start/stdout.txt", "nodes/start/stderr.txt"),
    )
    assert claim is not None
    identity = ProcessIdentity(pid=999_991, start_time=12345, group_id=999_991)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)

    assert store.expire_stale_claims(
        admitted.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    ) == ("start",)

    projection = store.load_run(admitted.run_id)
    node = projection["nodes"]["start"]
    attempt = node["attempts"][-1]
    assert projection["status"] == "paused"
    assert node["pending_interaction"]["type"] == "reconcile"
    assert node["recovery"]["observation"] == "still_running"
    assert attempt["process_identity"]["start_time"] == 12345
    assert attempt["effect_classification"] == "outward"
    assert attempt["owner_epoch"] == "leader-7"
    assert attempt["evidence_paths"] == [
        "nodes/start/stdout.txt",
        "nodes/start/stderr.txt",
    ]

    assert store.record_process_stopped(claim, identity, cleaned=True)
    stopped = store.load_run(admitted.run_id)["nodes"]["start"]["attempts"][-1]
    assert stopped["process_identity"]["pid"] == identity.pid
    assert stopped["process_stop"]["cleaned"] is True


def test_v3_restart_classifies_zero_effect_claim_before_fresh_attempt_budget(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    workflow = workflow_writer(
        tmp_path / "restart-zero-effect",
        nodes=[{"id": "start", "bash": "true", "timeout": 2_000}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    store = RunStore(tmp_path / "restart-zero-effect-home")
    admitted = _run(
        store,
        load_workflow(workflow),
        idempotency_key="restart-zero-effect",
    )
    old = store.claim_node(
        admitted.run_id,
        "start",
        "dead-owner",
        lease_seconds=1,
        executor_id="bash",
        effect_classification="replay_safe",
    )
    assert old is not None
    observed = []
    scheduler = RunScheduler(
        store,
        utcnow=lambda: old.lease_expires_at + timedelta(seconds=1),
    )
    monkeypatch.setattr(scheduler, "_renew_execution_owner", lambda _run_id: True)
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_args: observed.append((_args[1], _args[-1])),
    )
    try:
        interrupted = scheduler.advance(admitted.run_id, max_nodes=1)
        assert interrupted["status"] == "interrupted"
        assert observed == []
        scheduler.store.resume_run(
            admitted.run_id,
            always_run_nodes=scheduler.verified_always_run_nodes(admitted.run_id),
        )
        scheduler.advance(admitted.run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert len(observed) == 1
    replacement, budget = observed[0]
    assert replacement.attempt_id != old.attempt_id
    assert budget.remaining_wall(budget.last_semantic_progress) == 2.0
    event_types = [event["event_type"] for event in store.tail_events(admitted.run_id)]
    assert event_types.index("node_interrupted") < event_types.index(
        "node_claimed", event_types.index("node_claimed") + 1
    )


def test_v3_restart_classifies_active_process_before_any_duplicate_launch(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    workflow = workflow_writer(
        tmp_path / "restart-active-process",
        nodes=[{"id": "start", "bash": "true", "timeout": 2_000}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    store = RunStore(tmp_path / "restart-active-process-home")
    admitted = _run(
        store,
        load_workflow(workflow),
        idempotency_key="restart-active-process",
    )
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "dead-owner",
        lease_seconds=1,
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None
    identity = ProcessIdentity(pid=999_992, start_time=12346, group_id=999_992)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    launches = []
    scheduler = RunScheduler(
        store,
        utcnow=lambda: claim.lease_expires_at + timedelta(seconds=1),
    )
    monkeypatch.setattr(scheduler, "_renew_execution_owner", lambda _run_id: True)
    monkeypatch.setattr(
        scheduler, "_execute_claim", lambda *_args: launches.append(_args)
    )
    try:
        recovered = scheduler.advance(admitted.run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert recovered["status"] == "paused"
    assert recovered["nodes"]["start"]["recovery"]["observation"] == "still_running"
    assert recovered["nodes"]["start"]["attempts"][-1]["attempt_id"] == (
        claim.attempt_id
    )
    assert [
        event["event_type"] for event in store.tail_events(admitted.run_id)
    ].count("node_claimed") == 1
    assert launches == []


def test_spawn_intent_without_process_identity_is_outcome_uncertain(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_spawn_intent(claim, executor_nonce="nonce-before-spawn")

    assert store.expire_stale_claims(
        admitted.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    ) == ("start",)

    projection = store.load_run(admitted.run_id)
    node = projection["nodes"]["start"]
    attempt = node["attempts"][-1]
    assert projection["status"] == "paused"
    assert node["recovery"]["observation"] == "outcome_uncertain"
    assert node["recovery"]["termination_confirmed"] is False
    assert attempt["spawn"]["state"] == "intent"
    assert attempt["spawn"]["executor_nonce"] == "nonce-before-spawn"
    assert attempt["spawn"]["effect_classification"] == "outward"


@pytest.mark.parametrize("node_type", ["bash", "script"])
def test_spawn_intent_precedes_process_creation_and_spawn_failure_is_durable(
    tmp_path, workflow_writer, monkeypatch, node_type
) -> None:
    node = (
        {"id": "start", "bash": "true"}
        if node_type == "bash"
        else {
            "id": "start",
            "script": "print('never')",
            "runtime": "uv",
        }
    )
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(tmp_path / "package", nodes=[node])
    )
    admitted = _run(store, package)
    observed_spawn_states = []

    def fail_spawn(cls, *args, **kwargs):
        projection = store.load_run(admitted.run_id)
        attempt = projection["nodes"]["start"]["attempts"][-1]
        observed_spawn_states.append(attempt["spawn"]["state"])
        assert "process_identity" not in attempt
        raise OSError("injected spawn failure")

    monkeypatch.setattr(ManagedProcessTree, "spawn", classmethod(fail_spawn))
    scheduler = RunScheduler(store)
    if node_type == "script":
        scheduler.executors["script"] = ScriptExecutor(
            runtime_locator=lambda _runtime: "/fake/uv"
        )

    failed = scheduler.advance(admitted.run_id)

    assert failed["status"] == "failed"
    assert observed_spawn_states == ["intent"]
    attempt = failed["nodes"]["start"]["attempts"][-1]
    assert attempt["spawn"]["state"] == "failed"
    events = [event["event_type"] for event in store.tail_events(admitted.run_id)]
    assert events.index("spawn_intent") < events.index("spawn_failed")
    assert events.index("spawn_failed") < events.index("node_failed")


def test_stale_epoch_between_renewal_and_claim_is_nonfatal(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    scheduler = RunScheduler(
        store, execution_fence=ExecutionFence("old-coordinator", 1)
    )
    monkeypatch.setattr(scheduler, "_renew_execution_owner", lambda _run_id: True)

    def lose_epoch(*args, **kwargs):
        raise RuntimeError("stale coordinator execution fence")

    monkeypatch.setattr(store, "claim_node", lose_epoch)

    projection = scheduler.advance(admitted.run_id)

    assert projection["status"] == "running"
    assert projection["nodes"]["start"]["state"] == "ready"


def test_expired_attempt_is_reclaimed_only_by_same_fresh_coordinator_epoch(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="reclaim"))
    admitted = _run(store, package)
    coordinator = CoordinatorStore(store.database)
    now = datetime.now(timezone.utc)
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="reclaim-owner",
        host_kind="gateway",
        host_instance_id="reclaim-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    assert leadership.is_leader
    owner_epoch = f"coordinator:{identity.owner_id}:{leadership.lease.epoch}"
    claim = store.claim_node(
        admitted.run_id,
        "start",
        owner_epoch,
        lease_seconds=1,
        now=now,
        owner_epoch=owner_epoch,
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None
    store.mark_node_started(claim)
    child = ProcessIdentity(pid=999_992, start_time=23456, group_id=999_992)
    assert store.record_process_started(claim, child)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)

    expired = store.expire_stale_claims(
        admitted.run_id,
        now=claim.lease_expires_at + timedelta(seconds=1),
        current_owner_epoch=owner_epoch,
    )

    assert expired == ()
    projection = store.load_run(admitted.run_id)
    node = projection["nodes"]["start"]
    assert node["state"] == "running"
    assert node["claim"]["attempt_id"] == claim.attempt_id
    assert node["attempts"][-1]["attempt_id"] == claim.attempt_id
    assert "pending_interaction" not in node
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == "node_reclaimed"


def test_live_replay_safe_attempt_cannot_resume_until_termination_is_proven(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
        effect_classification="replay_safe",
    )
    assert claim is not None
    identity = ProcessIdentity(pid=999_992, start_time=23456, group_id=999_992)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    store.expire_stale_claims(
        admitted.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    )
    always_run_nodes = RunScheduler(store).verified_always_run_nodes(admitted.run_id)

    with pytest.raises(RuntimeError, match="executor is still running"):
        store.resume_run(
            admitted.run_id,
            always_run_nodes=always_run_nodes,
        )
    with pytest.raises(ValueError, match="replay-safe"):
        store.retry_run(admitted.run_id, node_id="start")

    assert store.record_process_stopped(claim, identity, cleaned=True)
    resumed = store.resume_run(
        admitted.run_id,
        always_run_nodes=always_run_nodes,
    )
    assert resumed["status"] == "running"
    assert resumed["nodes"]["start"]["state"] == "ready"


def test_pid_reuse_is_uncertain_and_never_authorizes_automatic_replay(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
        effect_classification="replay_safe",
    )
    assert claim is not None
    original = ProcessIdentity(pid=999_994, start_time=45678, group_id=999_994)
    assert store.record_process_started(claim, original)
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: True)
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(
            lambda cls, pid: ProcessIdentity(
                pid=pid,
                start_time=original.start_time + 1,
                group_id=pid,
            )
        ),
    )

    store.expire_stale_claims(
        admitted.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    )

    projection = store.load_run(admitted.run_id)
    node = projection["nodes"]["start"]
    assert projection["status"] == "paused"
    assert node["recovery"]["observation"] == "outcome_uncertain"
    assert node["pending_interaction"]["type"] == "reconcile"


def test_release_outward_claim_routes_to_reconciliation(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None

    assert store.release_or_expire_claim(claim)

    projection = store.load_run(admitted.run_id)
    node = projection["nodes"]["start"]
    assert projection["status"] == "paused"
    assert node["pending_interaction"]["type"] == "reconcile"


def test_cancel_after_lease_expiry_terminates_preserved_executor_identity(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
    )
    assert claim is not None
    identity = ProcessIdentity(pid=999_993, start_time=34567, group_id=999_993)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    store.expire_stale_claims(
        admitted.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    )
    terminated = []
    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        classmethod(
            lambda cls, candidate, **kwargs: terminated.append(candidate) or True
        ),
    )

    result = store.cancel_run(admitted.run_id)

    assert result["cancellation_outcome"] == "cancelled"
    assert terminated == [identity]


def test_abandon_failed_run_is_atomic_and_blocks_live_recovery(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(
            tmp_path / "failed-package",
            nodes=[{"id": "start", "bash": "false"}],
        )
    )
    admitted = _run(store, package)
    failed = RunScheduler(store).advance(admitted.run_id)
    assert failed["status"] == "failed"

    with pytest.raises(RuntimeError, match="stale terminal transition"):
        store.abandon_run(
            admitted.run_id,
            expected_state_version=int(failed["state_version"]) - 1,
        )
    abandoned = store.abandon_run(
        admitted.run_id,
        expected_state_version=int(failed["state_version"]),
    )
    assert abandoned["status"] == "abandoned"

    active = _run(store, package, idempotency_key="live-recovery")
    claim = store.claim_node(
        active.run_id,
        "start",
        "owner",
        lease_seconds=1,
        executor_id="bash",
    )
    assert claim is not None
    identity = ProcessIdentity(pid=999_995, start_time=None, group_id=999_995)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    store.expire_stale_claims(
        active.run_id, now=claim.lease_expires_at + timedelta(seconds=1)
    )

    with pytest.raises(RuntimeError, match="termination is unproven"):
        store.abandon_run(active.run_id)


def test_abandon_refuses_paused_run_with_live_claim(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(
            tmp_path / "parallel-package",
            name="parallel-abandon",
            nodes=[
                {"id": "gate", "approval": {"message": "Continue?"}},
                {"id": "worker", "bash": "sleep 30"},
            ],
        )
    )
    admitted = _run(store, package)
    worker = store.claim_node(
        admitted.run_id,
        "worker",
        "worker-owner",
        executor_id="bash",
    )
    assert worker is not None
    store.mark_node_started(worker)
    identity = ProcessIdentity(pid=999_996, start_time=56789, group_id=999_996)
    assert store.record_process_started(worker, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    gate = store.claim_node(admitted.run_id, "gate", "gate-owner")
    assert gate is not None
    store.mark_node_started(gate)
    store.complete_node(
        gate,
        status="paused",
        metadata={
            "pending_interaction": {
                "type": "workflow_approval",
                "interaction_id": "parallel-gate",
            }
        },
    )
    before = store.load_run(admitted.run_id)
    assert before["status"] == "paused"

    with pytest.raises(RuntimeError, match="live executor claim"):
        store.abandon_run(admitted.run_id)

    after = store.load_run(admitted.run_id)
    assert after["state_version"] == before["state_version"]
    assert after["nodes"]["worker"]["claim"]["attempt_id"] == worker.attempt_id


def test_scheduler_persists_outward_effect_classification_before_execution(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(tmp_path / "package", name="outward-metadata")
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "outward_action_nodes: [start]\n",
        encoding="utf-8",
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "home")
    admitted = _run(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    attempt = result["nodes"]["start"]["attempts"][-1]
    assert attempt["effect_classification"] == "outward"
    assert attempt["executor_id"] == "bash"
    assert attempt["owner_epoch"]
    assert attempt["evidence_paths"]


def test_corrupt_projection_is_quarantined_and_rebuilt_from_checked_journal(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    run_dir = store.run_directory(admitted.run_id)
    RunScheduler(store).advance(admitted.run_id)
    expected = store.load_run(admitted.run_id)
    (run_dir / "run.json").write_text("{broken", encoding="utf-8")

    rebuilt = store.load_run(admitted.run_id)

    assert rebuilt == expected
    assert list(run_dir.glob("run.json.corrupt-*"))


def test_heartbeat_extends_only_the_lease(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(admitted.run_id, "start", "owner", lease_seconds=30)
    assert claim is not None

    renewed_at = claim.lease_expires_at - timedelta(seconds=24)
    assert store.renew_claim(claim, now=renewed_at, lease_seconds=30)

    projection = store.load_run(admitted.run_id)
    active = projection["nodes"]["start"]["claim"]
    assert (
        active["lease_expires_at"] == (renewed_at + timedelta(seconds=30)).isoformat()
    )
    assert projection["last_semantic_progress_at"] is None
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == "node_heartbeat"
    run_dir = store.run_directory(admitted.run_id)
    journal_event = json.loads((run_dir / "events.jsonl").read_text().splitlines()[-1])
    assert "projection" not in journal_event

    (run_dir / "run.json").unlink()
    rebuilt = store.load_run(admitted.run_id)
    assert (
        rebuilt["nodes"]["start"]["claim"]["lease_expires_at"]
        == active["lease_expires_at"]
    )


def test_journal_gap_blocks_repair_and_preserves_diagnostics(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    run_dir = store.run_directory(admitted.run_id)
    events = [
        json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    events[0]["sequence"] = 2
    (run_dir / "events.jsonl").write_text(json.dumps(_reframe(events[0])) + "\n")
    (run_dir / "run.json").write_text("not-json")

    with pytest.raises(JournalRecoveryError, match="sequence gap"):
        store.load_run(admitted.run_id)
    assert (run_dir / "events.jsonl").exists()


def test_journal_digest_mismatch_blocks_repair(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    run_dir = store.run_directory(admitted.run_id)
    event = json.loads((run_dir / "events.jsonl").read_text())
    event["projection_sha256"] = "0" * 64
    (run_dir / "events.jsonl").write_text(json.dumps(_reframe(event)) + "\n")
    (run_dir / "run.json").write_text("not-json")

    with pytest.raises(JournalRecoveryError, match="digest mismatch"):
        store.load_run(admitted.run_id)


def test_durable_event_ahead_of_projection_is_replayed(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    run_dir = store.run_directory(admitted.run_id)
    stale = store.load_run(admitted.run_id)
    store.append_event(
        admitted.run_id,
        "semantic_progress",
        {"kind": "provider"},
    )
    expected = store.load_run(admitted.run_id)
    (run_dir / "run.json").write_text(json.dumps(stale), encoding="utf-8")

    assert store.load_run(admitted.run_id) == expected


def test_cancel_removes_projection_claim_before_restart_reconciliation(
    tmp_path, workflow_writer
):
    home = tmp_path / "home"
    store = RunStore(home)
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    assert store.claim_node(admitted.run_id, "start", "owner") is not None

    store.cancel_run(admitted.run_id)
    restarted = RunStore(home)

    projection = restarted.load_run(admitted.run_id)
    assert "claim" not in projection["nodes"]["start"]
    with restarted._connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0]
    assert count == 0


def test_resume_reruns_only_always_run_nodes(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[
                {"id": "cached", "bash": "true"},
                {"id": "refresh", "bash": "true", "always_run": True},
                {
                    "id": "fail",
                    "bash": "false",
                    "depends_on": ["cached", "refresh"],
                },
            ],
        )
    )
    admitted = _run(store, package)
    scheduler = RunScheduler(store)
    scheduler.advance(admitted.run_id)
    assert store.load_run(admitted.run_id)["status"] == "failed"
    always_run_nodes = scheduler.verified_always_run_nodes(admitted.run_id)

    resumed = store.resume_run(
        admitted.run_id,
        always_run_nodes=always_run_nodes,
    )

    assert resumed["nodes"]["cached"]["state"] == "succeeded"
    assert resumed["nodes"]["refresh"]["state"] == "ready"


def test_durable_diagnostics_redact_credentials_before_journaling(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(admitted.run_id, "start", "owner")
    assert claim is not None
    store.complete_node(
        claim,
        status="failed",
        error_code="network_error",
        error_message="request failed api_key=sk-super-secret-value",
    )

    run_dir = store.run_directory(admitted.run_id)
    assert "sk-super-secret-value" not in (run_dir / "run.json").read_text()
    assert "sk-super-secret-value" not in (run_dir / "events.jsonl").read_text()


def test_structurally_invalid_projection_rebuilds_from_journal(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    run_dir = store.run_directory(admitted.run_id)
    invalid = store.load_run(admitted.run_id)
    invalid["status"] = "mystery"
    (run_dir / "run.json").write_text(json.dumps(invalid))

    assert store.load_run(admitted.run_id)["status"] == "running"


def test_projection_rebuild_restores_journaled_publication_descriptor_and_bundle(
    tmp_path, workflow_writer
) -> None:
    root = tmp_path / "typed-projection-rebuild"
    workflow = workflow_writer(
        root,
        name="typed-projection-rebuild",
        nodes=[{"id": "start", "bash": "true", "output_type": "Report"}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    store = RunStore(tmp_path / "home")
    admitted = _run(store, load_workflow(workflow))
    claim = store.claim_node(admitted.run_id, "start", "owner")
    assert claim is not None
    data = b"journal authority"
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
            session_id=None,
        ),
    )
    expected = store.load_run(admitted.run_id)
    publication = next(
        entry for entry in expected["artifacts"] if "publication_id" in entry
    )
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / publication["publication_id"]
    )
    (store.run_directory(admitted.run_id) / "run.json").unlink()
    for path in tuple(bundle.iterdir()):
        path.unlink()
    bundle.rmdir()

    rebuilt = store.load_run(admitted.run_id)

    assert rebuilt == expected
    assert (bundle / "content.md").read_bytes() == data
    assert (bundle / "metadata.json").is_file()


def test_monotonic_gap_expires_claim_after_wall_clock_moves_backward(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(admitted.run_id, "start", "owner", lease_seconds=30)
    assert claim is not None
    active = store.load_run(admitted.run_id)["nodes"]["start"]["claim"]
    heartbeat = datetime.fromisoformat(active["heartbeat_at"])

    expired = store.expire_stale_claims(
        admitted.run_id,
        now=heartbeat - timedelta(hours=1),
        monotonic_now=float(active["heartbeat_monotonic"]) + 31,
    )

    assert expired == ("start",)
    assert store.load_run(admitted.run_id)["status"] == "interrupted"


def test_heartbeat_refuses_to_erase_a_suspend_clock_gap(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    admitted = _run(store, package)
    claim = store.claim_node(admitted.run_id, "start", "owner", lease_seconds=30)
    assert claim is not None
    active = store.load_run(admitted.run_id)["nodes"]["start"]["claim"]
    heartbeat = datetime.fromisoformat(active["heartbeat_at"])

    assert not store.renew_claim(
        claim,
        now=heartbeat - timedelta(hours=1),
        monotonic_now=float(active["heartbeat_monotonic"]) + 31,
        lease_seconds=30,
    )
    unchanged = store.load_run(admitted.run_id)["nodes"]["start"]["claim"]
    assert unchanged["heartbeat_at"] == active["heartbeat_at"]
