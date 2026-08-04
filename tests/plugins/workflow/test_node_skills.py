from __future__ import annotations

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.executors.ai import AgentNodeExecutor

from tests.plugins.workflow.test_ai_executor import (
    FakeAgentRunner,
    _archon_context,
    _node,
)


class CapturingRunner:
    def __init__(self):
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="done",
            session_id=f"session-{len(self.requests)}",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def test_only_selected_snapshotted_skill_enters_one_fresh_node(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="skill-scope",
            nodes=[
                {"id": "skilled", "prompt": "first", "skills": ["review"]},
                {"id": "fresh", "prompt": "second", "depends_on": ["skilled"]},
            ],
        )
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: (
            "<skill name=review>SELECTED_SKILL_CONTENT</skill>",
            names,
            [],
        ),
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
            idempotency_key="skills",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    runner = CapturingRunner()

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert "SELECTED_SKILL_CONTENT" in runner.requests[0].prompt
    assert "SELECTED_SKILL_CONTENT" not in runner.requests[1].prompt
    assert all(request.skills == () for request in runner.requests)


def test_structured_repair_does_not_receive_original_snapshotted_skill(tmp_path):
    runner = FakeAgentRunner("not json", '{"answer":"fixed"}')
    node = _node(
        "skill-repair",
        "ORIGINAL TASK",
        skills=["review"],
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )
    relative = "node-skills/skill-repair.md"
    context = _archon_context(
        tmp_path,
        node,
        sealed_resource_paths=frozenset({relative}),
        sealed_resource_bytes={relative: b"SELECTED_SKILL_CONTENT"},
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert "SELECTED_SKILL_CONTENT" in runner.requests[0].prompt
    assert "SELECTED_SKILL_CONTENT" not in runner.requests[1].prompt
    assert "ORIGINAL TASK" not in runner.requests[1].prompt
    assert runner.requests[1].skills == ()
