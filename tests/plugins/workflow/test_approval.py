from __future__ import annotations

import argparse
import json

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import register_cli
from plugins.workflow.models import ApprovalDecision
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _start(store, package, *, key="approval"):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def test_approval_survives_restart_captures_trimmed_output_and_continues(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="durable-gate",
            nodes=[
                {
                    "id": "review",
                    "approval": {
                        "message": "Approve the proposed plan?",
                        "capture_response": True,
                    },
                },
                {
                    "id": "finish",
                    "bash": "printf '%s' '$review.output'",
                    "depends_on": ["review"],
                },
            ],
        )
    )
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, package)

    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    assert paused["status"] == "paused"
    assert pending["type"] == "workflow_approval"

    restarted = RunStore(home)
    decision = restarted.approve_run(
        admitted.run_id,
        comment="  looks good  ",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
        actor="operator-1",
        channel="cli",
    )
    assert decision == ApprovalDecision(
        run_id=admitted.run_id,
        node_id="review",
        decision="approved",
        outcome="applied",
        interaction_id=pending["interaction_id"],
        state_version=decision.state_version,
    )

    completed = RunScheduler(RunStore(home)).advance(admitted.run_id)
    assert completed["status"] == "succeeded"
    output = next(
        artifact
        for artifact in completed["artifacts"]
        if artifact["node_id"] == "review"
        and artifact["relative_path"].endswith("output.txt")
    )
    assert (
        restarted.run_directory(admitted.run_id) / output["relative_path"]
    ).read_text() == "looks good"
    decision_event = next(
        event
        for event in restarted.tail_events(admitted.run_id)
        if event["event_type"] == "interaction_approved"
    )
    assert decision_event["payload"]["artifact"]["sha256"] == output["sha256"]

    duplicate = restarted.approve_run(
        admitted.run_id,
        comment="ignored",
        interaction_id=pending["interaction_id"],
    )
    assert duplicate.outcome == "already_decided"
    assert duplicate.decision == "approved"


class ReworkRunner:
    def __init__(self):
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="revised plan",
            session_id="rework-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def test_rejection_runs_bounded_rework_with_reason_then_cancels(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="rework-gate",
            nodes=[
                {
                    "id": "review",
                    "approval": {
                        "message": "Approve?",
                        "on_reject": {
                            "prompt": "Revise because: $REJECTION_REASON",
                            "max_attempts": 1,
                        },
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="rework")
    runner = ReworkRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first_pause = scheduler.advance(admitted.run_id)

    rejected = store.reject_run(
        admitted.run_id,
        reason="  missing evidence  ",
        expected_state_version=first_pause["state_version"],
        interaction_id=first_pause["nodes"]["review"]["pending_interaction"][
            "interaction_id"
        ],
    )
    assert rejected.outcome == "applied"
    second_pause = scheduler.advance(admitted.run_id)
    assert second_pause["status"] == "paused"
    assert runner.requests[0].prompt == "Revise because: missing evidence"
    assert second_pause["nodes"]["review"]["approval_rework_attempts"] == 1

    exhausted = store.reject_run(
        admitted.run_id,
        reason="still incomplete",
        expected_state_version=second_pause["state_version"],
        interaction_id=second_pause["nodes"]["review"]["pending_interaction"][
            "interaction_id"
        ],
    )
    assert exhausted.outcome == "applied"
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


class ToolApprovalRunner:
    def __init__(self):
        self.requests = []
        self.digests = iter(("a" * 64, "b" * 64))

    def run(self, request, **_kwargs):
        self.requests.append(request)
        digest = next(self.digests)
        return PluginAgentRunResult(
            final_response="",
            session_id="tool-session",
            provider="fake",
            model="fake",
            status="paused",
            pending_interaction={"kind": "approval", "action_digest": digest},
            usage={},
            audit={},
        )


def test_worker_action_grant_is_consumed_before_spawn_and_mismatch_repauses(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="tool-gate",
            nodes=[{"id": "act", "prompt": "perform action"}],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="tool")
    runner = ToolApprovalRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first = scheduler.advance(admitted.run_id)
    pending = first["nodes"]["act"]["pending_interaction"]

    store.approve_run(
        admitted.run_id,
        expected_state_version=first["state_version"],
        interaction_id=pending["action_digest"],
    )
    second = scheduler.advance(admitted.run_id)

    assert runner.requests[1].approved_action_digest == "a" * 64
    assert second["status"] == "paused"
    assert second["nodes"]["act"]["pending_interaction"]["action_digest"] == "b" * 64
    persisted = (store.run_directory(admitted.run_id) / "events.jsonl").read_text()
    assert "approved_action_digest" not in persisted


def test_cli_approve_and_reject_have_stable_codes_and_continue_is_opt_in(
    tmp_path, workflow_writer, capsys
):
    workdir = tmp_path / "repo"
    package = load_workflow(
        workflow_writer(
            workdir / ".hermes" / "workflows",
            name="cli-gate",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, package, key="cli")
    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    parser = _parser()

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "approve",
        admitted.run_id,
        "--comment",
        "ok",
        "--json",
    ])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "applied"
    assert store.load_run(admitted.run_id)["status"] == "running"

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "reject",
        admitted.run_id,
        "--reason",
        "late",
        "--json",
    ])
    assert args.func(args) == 3
    assert json.loads(capsys.readouterr().out)["outcome"] == "already_decided"
    assert pending["interaction_id"]
