from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading

import pytest

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


def test_null_interaction_does_not_apply_or_reuse_prior_gate_decision(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="two-gate-identity",
            nodes=[
                {"id": "first", "approval": {"message": "First?"}},
                {
                    "id": "second",
                    "approval": {"message": "Second?"},
                    "depends_on": ["first"],
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="two-gate",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    scheduler = RunScheduler(store)
    first = scheduler.advance(admitted.run_id)
    first_id = first["nodes"]["first"]["pending_interaction"]["interaction_id"]
    store.approve_run(
        admitted.run_id,
        expected_state_version=first["state_version"],
        interaction_id=first_id,
    )
    second = scheduler.advance(admitted.run_id)
    second_id = second["nodes"]["second"]["pending_interaction"]["interaction_id"]
    assert second_id != first_id

    with pytest.raises(ValueError, match="interaction ID is required"):
        store.approve_run(
            admitted.run_id,
            expected_state_version=second["state_version"],
            interaction_id=None,
        )

    unchanged = store.load_run(admitted.run_id)
    assert unchanged["state_version"] == second["state_version"]
    assert unchanged["nodes"]["second"]["pending_interaction"][
        "interaction_id"
    ] == second_id


def test_resume_approve_and_input_capacity_race_queues_every_continuation(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    setup_store = RunStore(home, max_executing_runs=4)

    def start(name: str, *, approval: bool = False) -> str:
        package = load_workflow(
            workflow_writer(
                tmp_path / name,
                name=name,
                nodes=[
                    {
                        "id": "start",
                        **(
                            {"approval": {"message": "Approve?"}}
                            if approval
                            else {"bash": "true"}
                        ),
                    }
                ],
            )
        )
        prepared = setup_store.prepare_run_snapshot(package)
        admitted = setup_store.start_run(
            RunAdmissionRequest(
                workflow_name=name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=name,
                concurrency_key=name,
                concurrency_policy="allow",
            ),
            immutable_snapshot=prepared,
        )
        assert admitted.run_id is not None
        return admitted.run_id

    approval_run = start("capacity-approval", approval=True)
    approval_state = RunScheduler(setup_store).advance(approval_run)
    approval_id = approval_state["nodes"]["start"]["pending_interaction"][
        "interaction_id"
    ]

    input_run = start("capacity-input")
    input_claim = setup_store.claim_node(input_run, "start", "input-worker")
    assert input_claim is not None
    setup_store.mark_node_started(input_claim)
    input_id = "capacity-loop-input"
    setup_store.complete_node(
        input_claim,
        status="paused",
        metadata={
            "pending_interaction": {
                "type": "loop_input",
                "interaction_id": input_id,
                "message": "Review",
            },
            "loop_state": {"iteration": 1},
        },
    )
    input_state = setup_store.load_run(input_run)

    resume_run = start("capacity-resume")
    setup_store.interrupt_for_host_pressure(
        resume_run, message="synthetic capacity setup"
    )
    resume_state = setup_store.load_run(resume_run)

    blocker = start("capacity-blocker")
    action_store = RunStore(home, max_executing_runs=1)
    always_run_nodes = RunScheduler(action_store).verified_always_run_nodes(
        resume_run
    )
    barrier = threading.Barrier(4)

    def approve() -> None:
        barrier.wait()
        action_store.approve_run(
            approval_run,
            expected_state_version=approval_state["state_version"],
            interaction_id=approval_id,
        )

    def provide_input() -> None:
        barrier.wait()
        action_store.provide_loop_input(
            input_run,
            "continue",
            expected_state_version=input_state["state_version"],
            interaction_id=input_id,
        )

    def resume() -> None:
        barrier.wait()
        action_store.resume_run(
            resume_run,
            always_run_nodes=always_run_nodes,
            expected_state_version=resume_state["state_version"],
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(action) for action in (approve, provide_input, resume)]
        barrier.wait()
        for future in futures:
            future.result(timeout=10)

    assert action_store.load_run(blocker)["status"] == "running"
    continued = [
        action_store.load_run(run_id)
        for run_id in (approval_run, input_run, resume_run)
    ]
    assert {run["status"] for run in continued} == {"queued"}
    sequences = [run["queue_sequence"] for run in continued]
    assert all(isinstance(sequence, int) for sequence in sequences)
    assert len(set(sequences)) == 3
    with action_store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runs WHERE status='running'"
        ).fetchone()[0] == 1
