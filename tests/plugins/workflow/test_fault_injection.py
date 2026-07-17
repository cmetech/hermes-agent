from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _request(prepared, key: str) -> RunAdmissionRequest:
    return RunAdmissionRequest(
        workflow_name="duplicate-stress",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="api",
        idempotency_key=key,
        concurrency_key="duplicate-stress",
    )


def test_hundred_duplicate_deliveries_publish_exactly_one_run(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="duplicate-stress")
    )
    store = RunStore(tmp_path / "home", max_start_requests_per_minute=200)
    prepared = store.prepare_run_snapshot(package)

    def start(_index: int):
        snapshot = store.prepare_run_snapshot(package)
        return store.start_run(_request(snapshot, "same-source-key"), immutable_snapshot=snapshot)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(start, range(100)))

    run_ids = {result.run_id for result in results}
    assert len(run_ids) == 1
    assert sum(result.disposition == "created" for result in results) == 1
    assert all(result.disposition in {"created", "existing"} for result in results)
    assert prepared.staging_directory.exists()


def test_twenty_simultaneous_approval_decisions_have_one_durable_winner(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "approval",
            name="approval-race",
            nodes=[{"id": "review", "approval": {"message": "Review"}}],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    request = RunAdmissionRequest(
        workflow_name="approval-race",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="cli",
        idempotency_key="approval-race",
        concurrency_key="approval-race",
    )
    run_id = store.start_run(request, immutable_snapshot=prepared).run_id
    assert run_id
    from plugins.workflow.scheduler import RunScheduler

    RunScheduler(store).advance(run_id)
    paused = store.load_run(run_id)
    interaction = paused["nodes"]["review"]["pending_interaction"]["interaction_id"]

    def decide(_index: int) -> str:
        try:
            result = store.approve_run(run_id, interaction_id=interaction)
            return result.outcome
        except (RuntimeError, ValueError):
            return "stale"

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(decide, range(20)))
    assert outcomes.count("applied") == 1
    assert store.load_run(run_id)["status"] == "running"
