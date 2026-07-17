from __future__ import annotations

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


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
    scheduler = RunScheduler(store, heartbeat_seconds=0.1, lease_seconds=1)

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
