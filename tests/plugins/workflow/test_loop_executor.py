from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.loop import LoopExecutor
from plugins.workflow.models import WorkflowNode, freeze_value
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class FakeAgentRunner:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=self.responses.pop(0),
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def _context(
    tmp_path: Path,
    loop: dict[str, object],
    *,
    variable_context: VariableContext | None = None,
    node_state: dict[str, object] | None = None,
    is_cancelled=None,
    depends_on: tuple[str, ...] = (),
) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir(exist_ok=True)
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=WorkflowNode(
            id="iterate",
            node_type="loop",
            value=freeze_value(loop),
            depends_on=depends_on,
            source_index=0,
            source_line=1,
            options=freeze_value({}),
        ),
        attempt_id=f"attempt-{len(list((run_directory / 'nodes').glob('*'))) if (run_directory / 'nodes').exists() else 0}",
        workflow_name="loop-demo",
        workflow_options=freeze_value({
            "provider": "fake-provider",
            "model": "fake-model",
        }),
        variable_context=variable_context or VariableContext(workflow_id="run-1"),
        node_state=node_state or {},
        is_cancelled=is_cancelled,
    )


def test_v3_loop_prompt_rechecks_direct_dependency_before_iteration(tmp_path) -> None:
    runner = FakeAgentRunner("provider must not run")
    context = _context(
        tmp_path,
        {
            "prompt": "Use $producer.output.answer",
            "until": "DONE",
            "max_iterations": 1,
        },
        variable_context=VariableContext(
            normalizer_version=3,
            node_outputs={
                "producer": ResolvedNodeOutput(
                    canonical_bytes=b'{"answer":"ready"}',
                    value={"answer": "ready"},
                    text='{"answer":"ready"}',
                    media_type="application/json",
                    sha256="1" * 64,
                    node_id="producer",
                    attempt_id="attempt-winner",
                    publication_id="a" * 32,
                    schema_fingerprint="3" * 64,
                    canonicalization_version=1,
                )
            },
        ),
    )

    with pytest.raises(WorkflowOutputReferenceError) as exc:
        LoopExecutor(runner).execute(context)

    assert exc.value.code == "output_reference_not_declared_dependency"
    assert runner.requests == []


def _artifact_text(context: NodeExecutionContext, result, index: int = -1) -> str:
    return (context.run_directory / result.artifacts[index].relative_path).read_text(
        encoding="utf-8"
    )


def test_loop_completes_on_case_insensitive_promise_and_strips_signal(
    tmp_path: Path,
) -> None:
    runner = FakeAgentRunner("Work finished. <Promise> complete </PROMISE>")
    context = _context(
        tmp_path,
        {"prompt": "Do work", "until": "COMPLETE", "max_iterations": 3},
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.metadata["loop_state"]["iteration"] == 1
    assert "promise" not in _artifact_text(context, result).lower()
    assert _artifact_text(context, result).strip() == "Work finished."


def test_fresh_loop_passes_previous_output_and_fails_at_hard_limit(
    tmp_path: Path,
) -> None:
    runner = FakeAgentRunner("first attempt", "second attempt")
    context = _context(
        tmp_path,
        {
            "prompt": "Previous: $LOOP_PREV_OUTPUT",
            "until": "DONE",
            "max_iterations": 2,
            "fresh_context": True,
        },
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "loop_max_iterations"
    assert [request.context_mode for request in runner.requests] == ["fresh", "fresh"]
    assert runner.requests[0].prompt == "Previous: "
    assert runner.requests[1].prompt == "Previous: first attempt"
    assert len(result.artifacts) == 2


def test_shared_loop_resumes_the_previous_iteration_session(tmp_path: Path) -> None:
    runner = FakeAgentRunner("keep going", "<promise>DONE</promise>")
    context = _context(
        tmp_path,
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "fresh_context": False,
        },
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].context_mode == "fresh"
    assert runner.requests[1].context_mode == "shared"
    assert runner.requests[1].session_id == "session-1"


def test_noninteractive_restart_restores_previous_persisted_output(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    output = run_directory / "nodes" / "iterate" / "prior" / "output.txt"
    output.parent.mkdir(parents=True)
    output.write_text("persisted draft", encoding="utf-8")
    runner = FakeAgentRunner("<promise>DONE</promise>")
    context = _context(
        tmp_path,
        {
            "prompt": "Previous: $LOOP_PREV_OUTPUT",
            "until": "DONE",
            "max_iterations": 2,
        },
        node_state={
            "loop_state": {
                "iteration": 1,
                "output_artifact": "nodes/iterate/prior/output.txt",
            }
        },
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.requests[0].prompt == "Previous: persisted draft"


def test_until_bash_exit_zero_completes_using_shell_quoted_previous_output(
    tmp_path: Path,
) -> None:
    runner = FakeAgentRunner("value with spaces")
    context = _context(
        tmp_path,
        {
            "prompt": "Produce value",
            "until": "DONE",
            "until_bash": "test $LOOP_PREV_OUTPUT = 'value with spaces'",
            "max_iterations": 3,
        },
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.metadata["loop_state"]["completed_by"] == "until_bash"
    assert _artifact_text(context, result) == "value with spaces"


def test_v3_loop_prompt_and_until_bash_share_strict_rendered_field(tmp_path) -> None:
    runner = FakeAgentRunner("keep going")
    variables = VariableContext(
        normalizer_version=3,
        node_outputs={
            "producer": ResolvedNodeOutput(
                canonical_bytes=b'{"answer":"value with spaces"}',
                value={"answer": "value with spaces"},
                text='{"answer":"value with spaces"}',
                media_type="application/json",
                sha256="1" * 64,
                node_id="producer",
                attempt_id="attempt-winner",
                publication_id="a" * 32,
                schema_fingerprint="3" * 64,
                canonicalization_version=1,
            )
        },
    )
    context = _context(
        tmp_path,
        {
            "prompt": "Use $producer.output.answer",
            "until": "DONE",
            "until_bash": "test $producer.output.answer = 'value with spaces'",
            "max_iterations": 2,
        },
        variable_context=variables,
        depends_on=("producer",),
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.metadata["loop_state"]["completed_by"] == "until_bash"
    assert runner.requests[0].prompt == "Use value with spaces"


def test_v3_until_bash_reference_failure_precedes_spill_side_effect(tmp_path) -> None:
    runner = FakeAgentRunner("keep going")
    context = _context(
        tmp_path,
        {
            "prompt": "Work",
            "until": "DONE",
            "until_bash": "test $producer.output.answer = ready",
            "max_iterations": 2,
        },
        variable_context=VariableContext(normalizer_version=3),
    )

    with pytest.raises(WorkflowOutputReferenceError) as exc:
        LoopExecutor(runner).execute(context)

    assert exc.value.code == "output_reference_not_declared_dependency"
    assert list(context.run_directory.glob("**/until-0001-variables")) == []


def test_interactive_loop_pauses_and_resume_injects_user_input_fresh(
    tmp_path: Path,
) -> None:
    first_runner = FakeAgentRunner("draft")
    loop = {
        "prompt": "Feedback: $LOOP_USER_INPUT; previous: $LOOP_PREV_OUTPUT",
        "until": "DONE",
        "max_iterations": 3,
        "interactive": True,
        "gate_message": "Review the draft",
    }
    first_context = _context(tmp_path, loop)

    paused = LoopExecutor(first_runner).execute(first_context)

    assert paused.status == "paused"
    assert paused.metadata["pending_interaction"] == {
        "type": "loop_input",
        "interaction_id": hashlib.sha256(
            "\0".join(["run-1", "iterate", "1", "Review the draft"]).encode()
        ).hexdigest(),
        "message": "Review the draft",
        "iteration": 1,
    }
    second_runner = FakeAgentRunner("<promise>DONE</promise>")
    resumed_context = _context(
        tmp_path,
        loop,
        variable_context=VariableContext(
            workflow_id="run-1", loop_user_input="tighten the evidence"
        ),
        node_state={"loop_state": paused.metadata["loop_state"]},
    )

    completed = LoopExecutor(second_runner).execute(resumed_context)

    assert completed.status == "succeeded"
    assert second_runner.requests[0].context_mode == "fresh"
    assert second_runner.requests[0].prompt == (
        "Feedback: tighten the evidence; previous: "
    )
    assert completed.metadata["loop_state"]["iteration"] == 2


def test_loop_checks_cancellation_between_iterations(tmp_path: Path) -> None:
    runner = FakeAgentRunner("not done", "must not run")
    checks = iter((False, False, True))
    context = _context(
        tmp_path,
        {"prompt": "Work", "until": "DONE", "max_iterations": 3},
        is_cancelled=lambda: next(checks, True),
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "cancelled"
    assert len(runner.requests) == 1


def test_scheduler_persists_interactive_loop_pause_state(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="interactive-loop",
            interactive=True,
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Refine",
                        "until": "DONE",
                        "max_iterations": 3,
                        "interactive": True,
                        "gate_message": "Review this iteration",
                    },
                }
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
            idempotency_key="interactive-loop",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    result = RunScheduler(store, agent_runner=FakeAgentRunner("draft")).advance(
        admitted.run_id
    )

    assert result["status"] == "paused"
    assert result["nodes"]["iterate"]["loop_state"]["iteration"] == 1
    assert result["nodes"]["iterate"]["pending_interaction"]["type"] == "loop_input"


def test_scheduler_uses_ai_wall_deadline_for_loop_nodes(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "home")
    scheduler = RunScheduler(
        store,
        agent_runner=FakeAgentRunner("done"),
        ai_wall_timeout_seconds=321,
        subprocess_timeout_seconds=17,
    )
    node = WorkflowNode(
        id="iterate",
        node_type="loop",
        value=freeze_value({
            "prompt": "work",
            "until": "DONE",
            "max_iterations": 1,
        }),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    assert scheduler._node_timeout(node) == 321


def test_scheduler_journals_each_loop_iteration_before_starting_the_next(
    tmp_path: Path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        name="journaled-loop",
        nodes=[
            {
                "id": "iterate",
                "loop": {
                    "prompt": "Refine",
                    "until": "DONE",
                    "max_iterations": 2,
                },
                "output_type": "LoopReport",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="journaled-loop",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    class InspectingRunner(FakeAgentRunner):
        def run(self, request, **kwargs):
            if self.requests:
                projection = store.load_run(admitted.run_id)
                assert projection["nodes"]["iterate"]["loop_state"]["iteration"] == 1
                assert any(
                    event["event_type"] == "loop_iteration_completed"
                    for event in store.tail_events(admitted.run_id)
                )
            return super().run(request, **kwargs)

    result = RunScheduler(
        store, agent_runner=InspectingRunner("draft", "<promise>DONE</promise>")
    ).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    iteration_events = [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "loop_iteration_completed"
    ]
    assert [event["payload"]["iteration"] for event in iteration_events] == [1, 2]
    assert len(result["artifacts"]) == 2
    published = [
        artifact
        for artifact in result["artifacts"]
        if artifact.get("publication_id") is not None
    ]
    assert len(published) == 1
    completion = next(
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "node_succeeded"
    )
    journaled = next(
        artifact
        for artifact in completion["payload"]["artifacts"]
        if artifact["relative_path"] == published[0]["relative_path"]
    )
    assert journaled == published[0]


def test_scheduler_preserves_terminal_frames_when_loop_reaches_journal_quota(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="journal-quota-loop",
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Refine",
                        "until": "DONE",
                        "max_iterations": 100,
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home", max_journal_bytes=192 * 1024)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="journal-quota-loop",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    result = RunScheduler(
        store,
        agent_runner=FakeAgentRunner(*("keep working" for _ in range(100))),
        heartbeat_seconds=10,
        ai_idle_timeout_seconds=60,
        ai_wall_timeout_seconds=60,
        provider_request_timeout_seconds=60,
    ).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert result["nodes"]["iterate"]["state"] == "failed"
    assert not result["nodes"]["iterate"].get("claim")
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == "run_failed"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0] == 0


def test_paused_loop_accepts_input_and_resumes_through_scheduler(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="resumable-loop",
            interactive=True,
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Feedback: $LOOP_USER_INPUT",
                        "until": "DONE",
                        "max_iterations": 2,
                        "interactive": True,
                        "gate_message": "Review",
                    },
                }
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
            idempotency_key="resumable-loop",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store, agent_runner=FakeAgentRunner("draft")).advance(admitted.run_id)
    paused = store.load_run(admitted.run_id)
    interaction_id = paused["nodes"]["iterate"]["pending_interaction"][
        "interaction_id"
    ]

    resumed = store.provide_loop_input(
        admitted.run_id,
        "tighten evidence",
        expected_state_version=paused["state_version"],
        interaction_id=interaction_id,
    )
    runner = FakeAgentRunner("<promise>DONE</promise>")
    completed = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert resumed["status"] == "running"
    assert completed["status"] == "succeeded"
    assert runner.requests[0].prompt == "Feedback: tighten evidence"
    assert (
        "tighten evidence"
        not in (store.run_directory(admitted.run_id) / "events.jsonl").read_text()
    )
