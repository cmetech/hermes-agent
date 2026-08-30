from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import pytest

from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from tests.plugins.workflow.test_phase6_scheduler import (
    _STRUCTURED_OUTPUT,
    OutputExecutor,
    _admit,
    _compile,
    _group,
)
from tests.plugins.workflow.test_phase6_store import (
    _EXECUTION_AUTHORITY,
    _admit_group,
    _initialize,
    _scope,
)


def _run_group(tmp_path, workflow_writer, *, output: str, group: dict):
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-controller",
        nodes=[group],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-controller")
    executor = OutputExecutor(lambda _context, _rendered: output)
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = executor
    result = scheduler.advance_all([run_id])[run_id]
    return store, scheduler, run_id, result


def test_signal_completion_strips_only_the_exact_marker_and_skips_until_bash(
    tmp_path, workflow_writer
) -> None:
    store, scheduler, run_id, result = _run_group(
        tmp_path,
        workflow_writer,
        output="kept DONE text\n<promise>DONE</promise>\n",
        group={
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "exit 91",
                "max_iterations": 2,
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        },
    )

    assert result["status"] == "succeeded"
    assert result["nodes"]["group"]["state"] == "succeeded"
    output = scheduler._output_values(
        result, store.run_directory(run_id), node_ids=("group",)
    )["group"]
    assert output.text == "kept DONE text"
    assert not any(
        event["event_type"] == "loop_group_until_bash_started"
        for event in store.tail_events(run_id)
    )


def test_until_bash_completes_only_when_the_sink_has_no_signal(
    tmp_path, workflow_writer
) -> None:
    store, _scheduler, run_id, result = _run_group(
        tmp_path,
        workflow_writer,
        output="not complete",
        group={
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "true",
                "max_iterations": 2,
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        },
    )

    assert result["status"] == "succeeded"
    decisions = [
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == "loop_group_iteration_decided"
    ]
    assert [event["payload"]["decision"] for event in decisions] == [
        "until_bash_success"
    ]


def test_first_terminal_body_node_in_definition_order_is_the_primary_sink(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-primary-sink",
        nodes=[
            {
                "id": "group",
                "loop_group": {
                    "until": "DONE",
                    "max_iterations": 1,
                    "nodes": [
                        {"id": "z-first", "prompt": "first"},
                        {"id": "a-second", "prompt": "second"},
                    ],
                },
            }
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="phase6-primary-sink")
    executor = OutputExecutor(
        lambda context, _rendered: (
            "winner <promise>DONE</promise>"
            if context.node.id.endswith("/z-first")
            else "not the sink"
        )
    )
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["prompt"] = executor

    result = scheduler.advance_all([run_id])[run_id]
    resolved = scheduler._output_values(
        result,
        store.run_directory(run_id),
        node_ids=("group",),
    )["group"]

    assert result["status"] == "succeeded"
    assert result["nodes"]["group"]["loop_group"]["primary_sink"] == "z-first"
    assert resolved.text == "winner"


def test_structured_primary_sink_becomes_the_outer_group_contract(
    tmp_path, workflow_writer
) -> None:
    store, scheduler, run_id, result = _run_group(
        tmp_path,
        workflow_writer,
        output='{"present":"<promise>DONE</promise>"}',
        group={
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "max_iterations": 1,
                "nodes": [
                    {
                        "id": "sink",
                        "prompt": "produce",
                        "output_format": _STRUCTURED_OUTPUT,
                    }
                ],
            },
        },
    )

    assert result["status"] == "succeeded"
    resolved = scheduler._output_values(
        result,
        store.run_directory(run_id),
        node_ids=("group",),
    )["group"]
    assert dict(resolved.value) == {"present": ""}
    assert resolved.schema_fingerprint is not None
    assert resolved.text == '{"present":""}'


def test_between_iteration_input_reuses_the_existing_scoped_interaction(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-input",
        interactive=True,
        nodes=[
            {
                "id": "group",
                "loop_group": {
                    "until": "DONE",
                    "max_iterations": 2,
                    "interactive": True,
                    "signal_completes": True,
                    "gate_message": "continue?",
                    "nodes": [{"id": "sink", "prompt": "$LOOP_USER_INPUT"}],
                },
            }
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-input")
    executor = OutputExecutor(
        lambda context, rendered: (
            "<promise>DONE</promise>" if rendered == "operator note" else "not yet"
        )
    )
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = executor

    paused = scheduler.advance_all([run_id])[run_id]
    interaction = paused["nodes"]["group"]["pending_interaction"]
    assert interaction["type"] == "loop_input"
    assert interaction["loop_group_scope"]["iteration"] == 1
    continued = store.provide_loop_input(
        run_id,
        "operator note",
        expected_state_version=paused["state_version"],
        interaction_id=interaction["interaction_id"],
    )
    completed = scheduler.advance_all([run_id])[run_id]

    assert continued["nodes"]["group"]["loop_group"]["iteration"] == 2
    assert completed["status"] == "succeeded"
    assert [rendered for node, rendered in executor.rendered if node == "sink"] == [
        "",
        "operator note",
    ]
    feedback = next(
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == "loop_input_provided"
    )
    assert "operator note" not in str(feedback["payload"])


def test_signal_confirmation_approves_the_exact_group_attempt(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-confirmation",
        interactive=True,
        nodes=[
            {
                "id": "group",
                "loop_group": {
                    "until": "DONE",
                    "max_iterations": 2,
                    "interactive": True,
                    "gate_message": "accept?",
                    "nodes": [{"id": "sink", "prompt": "produce"}],
                },
            }
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-confirmation")
    executor = OutputExecutor(
        lambda _context, _rendered: "answer <promise>DONE</promise>"
    )
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = executor

    paused = scheduler.advance_all([run_id])[run_id]
    interaction = paused["nodes"]["group"]["pending_interaction"]
    attempt_id = interaction["attempt_id"]
    decision = store.approve_run(
        run_id,
        interaction_id=interaction["interaction_id"],
        expected_state_version=paused["state_version"],
    )
    completed = store.load_run(run_id)

    assert decision.node_id == "group"
    assert completed["status"] == "succeeded"
    assert completed["nodes"]["group"]["attempts"][-1]["attempt_id"] == attempt_id
    resolved = scheduler._output_values(
        completed,
        store.run_directory(run_id),
        node_ids=("group",),
    )["group"]
    assert resolved.text == "answer"


def test_hard_maximum_fails_without_a_dead_interaction(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-hard-limit",
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "max_iterations": 100,
                "interactive": True,
                "gate_message": "continue?",
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-hard-limit")
    admitted = store.load_run(run_id)
    assert store.renew_foreground_execution(
        run_id,
        owner_id=admitted["foreground_owner_id"],
        epoch=admitted["foreground_epoch"],
        now=datetime.now(timezone.utc),
        lease_seconds=120,
    )
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "not complete"
    )
    result = scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "failed", result["nodes"]["group"]
    assert result["last_error"]["code"] == "loop_group_max_iterations"
    assert result["nodes"]["group"].get("pending_interaction") is None


def test_body_approval_resumes_exact_child_without_replaying_succeeded_sibling(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-approval",
        nodes=[
            _group(
                [
                    {"id": "done", "prompt": "done"},
                    {"id": "gate", "approval": {"message": "approve"}},
                    {
                        "id": "sink",
                        "prompt": "sink",
                        "depends_on": ["done", "gate"],
                    },
                ]
            )
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="phase6-approval")
    executor = OutputExecutor(
        lambda context, _rendered: (
            "<promise>DONE</promise>" if context.node.id.endswith("/sink") else "done"
        )
    )
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["prompt"] = executor

    paused = scheduler.advance_all([run_id])[run_id]
    body = paused["nodes"]["group"]["loop_group"]["body"]
    interaction = body["gate"]["pending_interaction"]
    done_attempt = body["done"]["attempts"][0]["attempt_id"]
    assert paused["status"] == "paused"

    store.approve_run(
        run_id,
        interaction_id=interaction["interaction_id"],
        expected_state_version=paused["state_version"],
    )
    completed = scheduler.advance_all([run_id])[run_id]
    completed_body = completed["nodes"]["group"]["loop_group"]["body"]
    assert completed["status"] == "succeeded"
    assert [attempt["attempt_id"] for attempt in completed_body["done"]["attempts"]] == [
        done_attempt
    ]
    assert len(completed_body["gate"]["attempts"]) == 1


def test_child_events_are_scoped_and_drop_private_execution_content(
    tmp_path, workflow_writer
) -> None:
    store, _scheduler, run_id, _result = _run_group(
        tmp_path,
        workflow_writer,
        output="<promise>DONE</promise>",
        group=_group([{"id": "sink", "prompt": "private prompt"}]),
    )

    scoped = [
        event
        for event in store.tail_events(run_id)
        if event.get("attempt_id") is not None
    ]
    assert scoped
    assert all(
        isinstance(event["payload"].get("loop_group_scope"), Mapping)
        for event in scoped
    )
    forbidden = (
        "private prompt",
        "<promise>",
        "output.txt",
        "authorization",
        "credential",
        "environment",
    )
    assert all(
        not any(value in str(event["payload"]).lower() for value in forbidden)
        for event in scoped
    )


def test_outward_child_reconciliation_keeps_exact_scope_and_fails_the_group(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        executor_id="bash",
        effect_classification="outward",
        execution_authority=_EXECUTION_AUTHORITY,
        lease_seconds=1,
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.record_spawn_intent(claim, executor_nonce="outward-intent")

    assert store.expire_stale_claims(
        run_id,
        now=claim.lease_expires_at + timedelta(seconds=1),
    ) == (scope.worker_node_id,)
    paused = store.load_run(run_id)
    child = paused["nodes"]["group"]["loop_group"]["body"]["select"]
    interaction = child["pending_interaction"]
    assert interaction["loop_group_scope"] == scope.durable_record()
    with pytest.raises(RuntimeError, match="termination"):
        store.reconcile_run(
            run_id,
            "safe-to-retry",
            expected_state_version=paused["state_version"],
            interaction_id=interaction["interaction_id"],
        )
    failed = store.reconcile_run(
        run_id,
        "confirmed-failed",
        expected_state_version=paused["state_version"],
        interaction_id=interaction["interaction_id"],
    )

    assert failed["status"] == "failed"
    assert failed["nodes"]["group"]["state"] == "failed"
    assert failed["nodes"]["group"]["loop_group"]["state"] == "failed"


def test_cancellation_terminalizes_nested_children_and_rejects_stale_completion(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert claim is not None
    store.mark_node_started(claim)

    cancelled = store.cancel_run(run_id)

    controller = cancelled["nodes"]["group"]["loop_group"]
    assert cancelled["status"] == "cancelled"
    assert cancelled["nodes"]["group"]["state"] == "cancelled"
    assert controller["state"] == "cancelled"
    assert {child["state"] for child in controller["body"].values()} == {
        "cancelled"
    }
    with pytest.raises(RuntimeError, match="stale"):
        store.complete_loop_group_child(
            scope,
            claim,
            status="succeeded",
            expected_state_version=cancelled["state_version"],
        )
