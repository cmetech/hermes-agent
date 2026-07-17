from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import JournalRecoveryError, RunStore


def _run(store, package):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="crash",
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
    store.resume_run(admitted.run_id)
    replacement = store.claim_node(admitted.run_id, "start", "replacement")
    assert replacement is not None

    with pytest.raises(RuntimeError, match="stale node completion"):
        store.complete_node(old, status="succeeded")
    store.complete_node(replacement, status="succeeded")
    assert store.load_run(admitted.run_id)["status"] == "succeeded"


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
    (run_dir / "events.jsonl").write_text(json.dumps(events[0]) + "\n")
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
    (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n")
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
    RunScheduler(store).advance(admitted.run_id)
    assert store.load_run(admitted.run_id)["status"] == "failed"

    resumed = store.resume_run(admitted.run_id)

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
