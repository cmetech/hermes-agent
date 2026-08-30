from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json

import pytest

from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.store import RunStore, WorkflowConflict
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


def test_until_bash_records_controller_authority_before_process_dispatch(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-predicate-authority",
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "true",
                "max_iterations": 1,
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-predicate-authority")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "not complete"
    )

    class PredicateExecutor:
        def execute(self, context):
            event_types = {
                event["event_type"] for event in store.tail_events(run_id)
            }
            assert "loop_group_predicate_pending" in event_types
            assert context.spawn_intent is not None
            assert context.spawn_failed is not None
            assert context.process_started is not None
            assert context.process_stopped is not None
            durable_attempt_id = context.attempt_id.split("/", 1)[0]
            assert context.attempt_id.startswith(
                f"{durable_attempt_id}/until-recovery-0001-"
            )
            with store._connect() as connection:
                reserve = connection.execute(
                    "SELECT terminal_reserve_bytes FROM obligation_journal_reserves "
                    "WHERE attempt_id=?",
                    (durable_attempt_id,),
                ).fetchone()
                worker_count = connection.execute(
                    "SELECT COUNT(*) FROM worker_claims WHERE attempt_id IN (?, ?)",
                    (durable_attempt_id, context.attempt_id),
                ).fetchone()[0]
            assert reserve is not None
            assert int(reserve["terminal_reserve_bytes"]) > 0
            assert worker_count == 0
            return NodeExecutionResult("succeeded")

    scheduler.executors["bash"] = PredicateExecutor()

    result = scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "succeeded"
    assert any(
        event["event_type"] == "loop_group_predicate_decided"
        for event in store.tail_events(run_id)
    )


def test_failed_until_bash_reuses_scoped_between_iteration_input(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-predicate-input",
        interactive=True,
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "false",
                "max_iterations": 2,
                "interactive": True,
                "gate_message": "continue?",
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-predicate-input")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "not complete"
    )

    paused = scheduler.advance_all([run_id])[run_id]

    interaction = paused["nodes"]["group"]["pending_interaction"]
    assert paused["status"] == "paused"
    assert interaction["type"] == "loop_input"
    assert interaction["loop_group_scope"]["iteration"] == 1


def test_until_bash_runtime_failure_terminalizes_the_group_once(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-predicate-failure",
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "true",
                "max_iterations": 2,
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-predicate-failure")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "not complete"
    )
    scheduler.executors["bash"] = type(
        "FailedPredicate",
        (),
        {
            "execute": lambda self, context: NodeExecutionResult(
                "failed",
                error_code="predicate_timeout",
                error_message="predicate timed out at /private/operator/secret-command",
            )
        },
    )()

    failed = scheduler.advance_all([run_id])[run_id]

    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "predicate_timeout"
    assert failed["nodes"]["group"]["loop_group"]["state"] == "failed"
    evidence = (
        store.run_directory(run_id) / "events.jsonl"
    ).read_text(encoding="utf-8")
    assert "/private/operator/secret-command" not in evidence


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


@pytest.mark.parametrize("operator_action", ("resume", "retry"))
@pytest.mark.parametrize(
    "include_independent",
    (pytest.param(False, id="descendant"), pytest.param(True, id="independent")),
)
def test_operator_restarts_only_the_failed_current_iteration_child(
    tmp_path, workflow_writer, operator_action, include_independent
) -> None:
    body = [
        {"id": "completed", "prompt": "completed"},
        {
            "id": "flaky",
            "prompt": "flaky",
            "depends_on": ["completed"],
        },
        {
            "id": "downstream",
            "prompt": "downstream",
            "depends_on": ["flaky"],
        },
    ]
    if include_independent:
        body.append({"id": "independent", "prompt": "independent"})
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"phase6-operator-{operator_action}",
        nodes=[
            _group(
                body,
                maximum=2,
            )
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key=f"phase6-operator-{operator_action}")
    calls = {"completed": 0, "flaky": 0, "downstream": 0}
    if include_independent:
        calls["independent"] = 0

    def output(context, _rendered):
        child_id = context.node.id.rsplit("/", 1)[-1]
        calls[child_id] += 1
        if child_id == "completed":
            return "completed"
        if child_id == "independent":
            return "independent"
        if child_id == "downstream":
            if "iterations/0002" in context.effective_attempt_directory.as_posix():
                return "finished <promise>DONE</promise>"
            return "continue"
        if "iterations/0002" in context.effective_attempt_directory.as_posix():
            if calls[child_id] == 2:
                return NodeExecutionResult(
                    "failed",
                    error_code="validation",
                    error_message="retry this child",
                    metadata={
                        "archon_terminal_failure": True,
                        "known_no_effect": True,
                        "provider_attempts": 0,
                        "provider_attempts_exact": True,
                    },
                )
            return "finished <promise>DONE</promise>"
        return "continue"

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(output)
    failed = scheduler.advance_all([run_id])[run_id]
    failed_controller = failed["nodes"]["group"]["loop_group"]
    completed_attempt = failed_controller["body"]["completed"]["attempts"][0][
        "attempt_id"
    ]

    assert failed["status"] == "failed"
    assert failed_controller["iteration"] == 2
    assert failed_controller["body"]["completed"]["state"] == "succeeded"
    assert failed_controller["body"]["flaky"]["state"] == "failed"
    assert failed_controller["body"]["downstream"]["state"] == "cancelled"
    assert failed_controller["body"]["downstream"]["attempts"] == []
    if include_independent:
        assert failed_controller["body"]["independent"]["state"] == "cancelled"
        assert failed_controller["body"]["independent"]["attempts"] == []
        assert calls["independent"] == 1

    if operator_action == "resume":
        resumed = store.resume_run(run_id, always_run_nodes=set())
    else:
        resumed = store.retry_run(run_id, node_id="group/flaky")

    resumed_controller = resumed["nodes"]["group"]["loop_group"]
    assert resumed["status"] == "running"
    assert resumed["nodes"]["group"]["state"] == "running"
    assert resumed_controller["state"] == "running"
    assert resumed_controller["controller_generation"] == 1
    assert resumed_controller["iteration"] == 2
    assert resumed_controller["body"]["completed"]["state"] == "succeeded"
    assert resumed_controller["body"]["completed"]["attempts"][0][
        "attempt_id"
    ] == completed_attempt
    assert resumed_controller["body"]["flaky"]["state"] == "ready"
    assert resumed_controller["body"]["downstream"]["state"] == "pending"
    if include_independent:
        assert resumed_controller["body"]["independent"]["state"] == "ready"

    completed = scheduler.advance_all([run_id])[run_id]
    completed_body = completed["nodes"]["group"]["loop_group"]["body"]
    assert completed["status"] == "succeeded"
    expected_calls = {
        "completed": 2,
        "flaky": 3,
        "downstream": 2,
    }
    if include_independent:
        expected_calls["independent"] = 2
    assert calls == expected_calls
    assert len(completed_body["completed"]["attempts"]) == 1
    assert len(completed_body["flaky"]["attempts"]) == 2
    assert len(completed_body["downstream"]["attempts"]) == 1
    if include_independent:
        assert len(completed_body["independent"]["attempts"]) == 1


@pytest.mark.parametrize("operator_action", ("resume", "retry"))
@pytest.mark.parametrize("sibling_effect", ("replay_safe", "outward"))
def test_operator_reopens_only_safe_unstarted_attempted_siblings(
    tmp_path, workflow_writer, operator_action, sibling_effect
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"phase6-attempted-sibling-{operator_action}-{sibling_effect}",
        nodes=[
            _group(
                [
                    {"id": "flaky", "prompt": "flaky"},
                    {"id": "independent", "prompt": "independent"},
                    {
                        "id": "sink",
                        "prompt": "sink",
                        "depends_on": ["flaky", "independent"],
                    },
                ]
            )
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(
        store,
        compilation,
        key=f"phase6-attempted-sibling-{operator_action}-{sibling_effect}",
    )
    group = compilation.package.definition.nodes[0]
    before = store.load_run(run_id)
    assert store.initialize_loop_group(
        run_id,
        group.id,
        group.value["nodes"],
        max_iterations=1,
        primary_sink="sink",
        expected_state_version=before["state_version"],
    )
    initialized = store.load_run(run_id)
    sibling_claim = store.claim_loop_group_child(
        _scope(run_id, "independent"),
        "independent-owner",
        expected_state_version=initialized["state_version"],
        executor_id="prompt",
        effect_classification=sibling_effect,
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert sibling_claim is not None
    calls = {"flaky": 0, "independent": 0, "sink": 0}

    def output(context, _rendered):
        child_id = context.node.id.rsplit("/", 1)[-1]
        calls[child_id] += 1
        if child_id == "flaky" and calls[child_id] == 1:
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="retry flaky",
                metadata={
                    "archon_terminal_failure": True,
                    "known_no_effect": True,
                    "provider_attempts": 0,
                    "provider_attempts_exact": True,
                },
            )
        if child_id == "sink":
            return "finished <promise>DONE</promise>"
        return child_id

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(output)
    failed = scheduler.advance_all([run_id])[run_id]
    sibling = failed["nodes"]["group"]["loop_group"]["body"]["independent"]
    assert failed["status"] == "failed"
    assert sibling["state"] == "cancelled"
    assert sibling["attempts"][-1]["attempt_id"] == sibling_claim.attempt_id
    assert sibling["attempts"][-1]["effect_classification"] == sibling_effect
    assert "claim" not in sibling
    assert "recovery" not in sibling

    if operator_action == "resume":
        resumed = store.resume_run(run_id, always_run_nodes=set())
    else:
        resumed = store.retry_run(run_id, node_id="group/flaky")
    resumed_sibling = resumed["nodes"]["group"]["loop_group"]["body"][
        "independent"
    ]

    if sibling_effect == "outward":
        assert resumed_sibling["state"] == "cancelled"
        assert len(resumed_sibling["attempts"]) == 1
        return

    assert resumed_sibling["state"] == "ready"
    completed = scheduler.advance_all([run_id])[run_id]
    completed_body = completed["nodes"]["group"]["loop_group"]["body"]
    assert completed["status"] == "succeeded"
    assert calls == {"flaky": 2, "independent": 1, "sink": 1}
    assert len(completed_body["independent"]["attempts"]) == 2


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


def test_body_approval_rejection_terminalizes_the_nested_controller(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-approval-rejection",
        nodes=[
            _group([
                {"id": "done", "prompt": "done"},
                {"id": "gate", "approval": {"message": "approve"}},
                {
                    "id": "sink",
                    "prompt": "sink",
                    "depends_on": ["done", "gate"],
                },
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="phase6-approval-rejection")
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "done"
    )
    paused = scheduler.advance_all([run_id])[run_id]
    interaction = paused["nodes"]["group"]["loop_group"]["body"]["gate"][
        "pending_interaction"
    ]

    store.reject_run(
        run_id,
        interaction_id=interaction["interaction_id"],
        expected_state_version=paused["state_version"],
    )
    cancelled = store.load_run(run_id)

    controller = cancelled["nodes"]["group"]["loop_group"]
    assert cancelled["status"] == "cancelled"
    assert cancelled["nodes"]["group"]["state"] == "cancelled"
    assert controller["state"] == "cancelled"
    assert {child["state"] for child in controller["body"].values()} <= {
        "succeeded",
        "cancelled",
        "skipped",
    }


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


def test_nested_reconciliation_requires_the_authenticated_scope(
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
    store.expire_stale_claims(
        run_id,
        now=claim.lease_expires_at + timedelta(seconds=1),
    )
    directory = store.run_directory(run_id)
    projection = store.load_run(run_id)
    child = projection["nodes"]["group"]["loop_group"]["body"]["select"]
    pending = child["pending_interaction"]
    pending.pop("loop_group_scope")
    directory.joinpath("run.json").write_text(json.dumps(projection), encoding="utf-8")

    with pytest.raises(WorkflowConflict, match="scope"):
        store.reconcile_run(
            run_id,
            "confirmed-failed",
            expected_state_version=projection["state_version"],
            interaction_id=pending["interaction_id"],
        )


def test_group_terminal_events_have_stable_scoped_families(
    tmp_path, workflow_writer
) -> None:
    store, _scheduler, run_id, _result = _run_group(
        tmp_path,
        workflow_writer,
        output="result",
        group={
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "true",
                "max_iterations": 1,
                "nodes": [{"id": "sink", "prompt": "private prompt"}],
            },
        },
    )

    expected = [
        "loop_group_iteration_completed",
        "loop_group_predicate_pending",
        "loop_group_predicate_decided",
        "loop_group_decision_recorded",
        "loop_group_iteration_decided",
        "loop_group_succeeded",
    ]
    events = [
        event
        for event in store.tail_events(run_id)
        if event["event_type"] in expected
    ]

    assert [event["event_type"] for event in events] == expected
    for event in events:
        scope = event["payload"]["loop_group_scope"]
        assert scope["run_id"] == run_id
        assert scope["group_id"] == "group"
        assert scope["node_id"] == "sink"


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
