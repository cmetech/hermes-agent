from __future__ import annotations

import multiprocessing

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _decide(home, run_id, version, interaction_id, decision, start, output):
    store = RunStore(home)
    start.wait()
    method = store.approve_run if decision == "approved" else store.reject_run
    result = method(
        run_id,
        expected_state_version=version,
        interaction_id=interaction_id,
    )
    output.put((result.decision, result.outcome))


def test_approve_reject_process_race_has_one_committed_winner(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="race",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    paused = RunScheduler(store).advance(admitted.run_id)
    interaction = paused["nodes"]["review"]["pending_interaction"]["interaction_id"]

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_decide,
            args=(
                str(home),
                admitted.run_id,
                paused["state_version"],
                interaction,
                decision,
                start,
                output,
            ),
        )
        for decision in ("approved", "rejected")
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [output.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert [outcome for _decision, outcome in outcomes].count("applied") == 1
    assert [outcome for _decision, outcome in outcomes].count("already_decided") == 1
    assert len({decision for decision, _outcome in outcomes}) == 1
