from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.models import ExecutionFence
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import (
    ArtifactRef,
    JournalRecoveryError,
    RunStore,
    TypedPublicationCandidate,
)
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
