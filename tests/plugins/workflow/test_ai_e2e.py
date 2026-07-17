from __future__ import annotations

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class RecordingRunner:
    def __init__(self):
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="investigated",
            session_id="ai-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            audit={},
        )


def test_command_node_runs_from_immutable_snapshot_through_scheduler(
    tmp_path, workflow_writer
):
    root = tmp_path / "package"
    workflow = workflow_writer(
        root,
        name="ai-command",
        nodes=[{"id": "investigate", "command": "investigate"}],
    )
    (root / "commands").mkdir()
    (root / "commands" / "investigate.md").write_text(
        "---\ndescription: Investigate\n---\nInvestigate $ARGUMENTS"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package, values={"arguments": "disk"})
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="ai-command",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="ai-one",
            concurrency_key="ai-command",
        ),
        immutable_snapshot=prepared,
    )
    runner = RecordingRunner()

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert runner.requests[0].prompt == "Investigate disk"
    assert result["nodes"]["investigate"]["session_id"] == "ai-session"
    output = (
        store.run_directory(admitted.run_id) / result["artifacts"][0]["relative_path"]
    )
    assert output.read_text() == "investigated"
