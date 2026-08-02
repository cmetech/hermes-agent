from __future__ import annotations

import pytest

from agent.plugin_agent import PluginAgentRunRequest, PluginAgentRunResult
from agent.plugin_agent_worker import _build_inline_agent_handler
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore

from tests.plugins.workflow.test_ai_executor import (
    FakeAgentRunner,
    _archon_context,
    _context,
    _node,
)


def test_ordinary_node_denies_raw_delegation_and_has_no_inline_agents(tmp_path):
    runner = FakeAgentRunner("done")

    result = AgentNodeExecutor(runner).execute(
        _context(tmp_path, _node("ordinary", "work"))
    )

    assert result.status == "succeeded"
    assert "delegate_task" in runner.requests[0].denied_tools
    assert runner.requests[0].inline_agents == {}


def test_declared_agents_are_scoped_and_aliases_resolve_without_delegate(tmp_path):
    runner = FakeAgentRunner("done")
    node = _node(
        "parent",
        "coordinate",
        agents={
            "evidence-reviewer": {
                "description": "Review evidence",
                "prompt": "Review the supplied evidence",
                "model": "child-model",
                "tools": ["Read"],
                "disallowedTools": ["Bash"],
                "skills": [],
                "maxTurns": 3,
            }
        },
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "succeeded"
    child = runner.requests[0].inline_agents["evidence-reviewer"]
    assert child["allowed_tools"] == ["read_file"]
    assert child["denied_tools"] == ["terminal", "delegate_task"]
    assert child["max_iterations"] == 3
    assert "delegate_task" in runner.requests[0].denied_tools


def test_structured_repair_has_no_inline_agents_or_delegation_surface(tmp_path):
    runner = FakeAgentRunner("not json", '{"answer":"fixed"}')
    node = _node(
        "agent-repair",
        "coordinate",
        agents={
            "reviewer": {
                "description": "Review evidence",
                "prompt": "Review it",
            }
        },
        output_format={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    result = AgentNodeExecutor(runner).execute(_archon_context(tmp_path, node))

    assert result.status == "succeeded"
    assert runner.requests[0].inline_agents
    assert runner.requests[1].inline_agents == {}
    assert runner.requests[1].allowed_tools == ()
    assert runner.requests[1].enabled_toolsets == ()
    assert "delegate_task" in runner.requests[1].denied_tools


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_inline_agent_skill_uses_authenticated_bytes_without_reopening_source(
    tmp_path, mutation
):
    runner = FakeAgentRunner("done")
    node = _node(
        "parent",
        "coordinate",
        agents={
            "reviewer": {
                "description": "Review evidence",
                "prompt": "Review it",
                "skills": ["reviewing"],
            }
        },
    )
    relative = "node-agent-skills/parent/reviewer.md"
    skill = tmp_path / "run" / relative
    skill.parent.mkdir(parents=True)
    authenticated = b"AUTHENTICATED CHILD SKILL"
    skill.write_bytes(authenticated)
    context = _context(
        tmp_path,
        node,
        sealed_resource_paths=frozenset({relative}),
        sealed_resource_bytes={relative: authenticated},
    )
    if mutation == "delete":
        skill.unlink()
    elif mutation == "rename":
        skill.rename(skill.with_suffix(".gone"))
    else:
        skill.write_text("FORGED CHILD SKILL", encoding="utf-8")

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert (
        runner.requests[0].inline_agents["reviewer"]["instructions"]
        == "AUTHENTICATED CHILD SKILL"
    )


class ChildRunner:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return self.result


def test_inline_agent_handler_is_synchronous_bounded_and_returns_sanitized_result(
    tmp_path,
):
    runner = ChildRunner(
        PluginAgentRunResult(
            final_response="child result",
            session_id="child-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 2, "output_tokens": 1},
            audit={},
        )
    )
    emitted = []
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            "reviewer": {
                "description": "review",
                "prompt": "Base prompt",
                "model": None,
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "instructions": "SELECTED CHILD SKILL",
                "max_iterations": 2,
            }
        },
        workdir=tmp_path,
        parent_request=None,
        runner_factory=lambda _plugin_id: runner,
        emit_progress=lambda **payload: emitted.append(payload),
        pause=lambda descriptor: None,
    )

    payload = handler({"agent_id": "reviewer", "task": "Inspect this"})

    assert payload["status"] == "completed"
    assert payload["result"] == "child result"
    assert (
        runner.requests[0].prompt
        == "SELECTED CHILD SKILL\n\nBase prompt\n\nInspect this"
    )
    assert runner.requests[0].max_iterations == 2
    assert [event["phase"] for event in emitted] == [
        "inline_agent_started",
        "inline_agent_completed",
    ]


def test_strict_inline_agent_inherits_one_private_provider_authority(tmp_path):
    runner = ChildRunner(
        PluginAgentRunResult(
            final_response="child result",
            session_id="child-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )
    )
    descriptor = {
        "version": 1,
        "host": "127.0.0.1",
        "port": 43210,
        "authkey": "YXV0aG9yaXR5",
    }
    parent = PluginAgentRunRequest(
        prompt="parent",
        sealed_provider_attempt_grant=True,
        max_api_attempts=5,
        _provider_attempt_authority=descriptor,
    )
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            "reviewer": {
                "description": "review",
                "prompt": "Base",
                "model": None,
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "instructions": "",
                "max_iterations": 2,
            }
        },
        workdir=tmp_path,
        parent_request=parent,
        runner_factory=lambda _plugin_id: runner,
        emit_progress=lambda **_payload: None,
        pause=lambda _descriptor: None,
    )

    assert handler({"agent_id": "reviewer", "task": "Inspect"})["status"] == (
        "completed"
    )
    child = runner.requests[0]
    assert child.sealed_provider_attempt_grant is True
    assert child.max_api_attempts == 5
    assert child._provider_attempt_authority == descriptor


def test_legacy_inline_agent_does_not_gain_provider_authority(tmp_path):
    runner = ChildRunner(
        PluginAgentRunResult(
            final_response="child result",
            session_id="child-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )
    )
    parent = PluginAgentRunRequest(prompt="parent", max_api_attempts=2)
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            "reviewer": {
                "description": "review",
                "prompt": "Base",
                "model": None,
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "instructions": "",
                "max_iterations": 2,
            }
        },
        workdir=tmp_path,
        parent_request=parent,
        runner_factory=lambda _plugin_id: runner,
        emit_progress=lambda **_payload: None,
        pause=lambda _descriptor: None,
    )

    assert handler({"agent_id": "reviewer", "task": "Inspect"})["status"] == (
        "completed"
    )
    child = runner.requests[0]
    assert child.sealed_provider_attempt_grant is False
    assert child._provider_attempt_authority is None


def test_inline_agent_pending_approval_bubbles_to_parent_pause(tmp_path):
    pending = {"kind": "approval", "action_digest": "c" * 64}
    runner = ChildRunner(
        PluginAgentRunResult(
            final_response="",
            session_id="child-session",
            provider="fake",
            model="fake",
            status="paused",
            pending_interaction=pending,
            usage={},
            audit={},
        )
    )
    paused = []
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            "reviewer": {
                "description": "review",
                "prompt": "Base",
                "model": None,
                "allowed_tools": [],
                "denied_tools": ["delegate_task", "workflow_agent"],
                "instructions": "",
                "max_iterations": 2,
            }
        },
        workdir=tmp_path,
        parent_request=None,
        runner_factory=lambda _plugin_id: runner,
        emit_progress=lambda **_payload: None,
        pause=paused.append,
    )

    payload = handler({"agent_id": "reviewer", "task": "act"})

    assert payload["status"] == "paused"
    assert paused == [pending]


def test_inline_agent_rejects_unknown_id_and_oversized_task(tmp_path):
    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={},
        workdir=tmp_path,
        parent_request=None,
        runner_factory=lambda _plugin_id: ChildRunner(None),
        emit_progress=lambda **_payload: None,
        pause=lambda _descriptor: None,
    )
    assert (
        handler({"agent_id": "missing", "task": "x"})["error"] == "unknown inline agent"
    )
    assert "task" in handler({"agent_id": "missing", "task": "x" * 100_001})["error"]


def test_inline_agent_skill_content_is_snapshotted_separately_from_parent(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[
                {
                    "id": "parent",
                    "prompt": "coordinate",
                    "agents": {
                        "reviewer": {
                            "description": "review",
                            "prompt": "inspect",
                            "skills": ["child-skill"],
                        }
                    },
                }
            ],
        )
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("CHILD_ONLY_SKILL", names, []),
    )

    prepared = RunStore(tmp_path / "home").prepare_run_snapshot(package)

    child = prepared.staging_directory / "node-agent-skills" / "parent" / "reviewer.md"
    assert child.read_text() == "CHILD_ONLY_SKILL"
    assert not (prepared.staging_directory / "node-skills" / "parent.md").exists()
