from __future__ import annotations

from pathlib import Path

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.models import WorkflowNode, freeze_value
from plugins.workflow.resources import VariableContext


class FakeAgentRunner:
    def __init__(self, *responses: str):
        self.requests = []
        self.responses = list(responses)

    def run(self, request):
        self.requests.append(request)
        output = self.responses.pop(0)
        return PluginAgentRunResult(
            final_response=output,
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 4, "output_tokens": 2},
            audit={"tool_names": list(request.allowed_tools or ())},
        )


def _node(node_id: str, prompt: str, **options) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        node_type="prompt",
        value=prompt,
        depends_on=tuple(options.pop("depends_on", ())),
        source_index=0,
        source_line=1,
        options=freeze_value(options),
    )


def _context(tmp_path: Path, node: WorkflowNode, **kwargs) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir(exist_ok=True)
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id=f"attempt-{node.id}",
        workflow_name="ai-demo",
        workflow_options=freeze_value({
            "provider": "fake-provider",
            "model": "fake-model",
        }),
        variable_context=VariableContext(arguments="evidence", workflow_id="run-1"),
        **kwargs,
    )


def test_fresh_prompt_uses_host_runner_and_validates_structured_output(tmp_path):
    runner = FakeAgentRunner('{"answer":"ok","count":2}')
    node = _node(
        "analyze",
        "Analyze $ARGUMENTS",
        context="fresh",
        allowed_tools=["read_file"],
        output_format={
            "type": "object",
            "required": ["answer", "count"],
            "properties": {
                "answer": {"type": "string"},
                "count": {"type": "integer"},
            },
        },
    )

    result = AgentNodeExecutor(runner, profile_name="default").execute(
        _context(tmp_path, node)
    )

    assert result.status == "succeeded"
    assert runner.requests[0].prompt == "Analyze evidence"
    assert runner.requests[0].context_mode == "fresh"
    assert runner.requests[0].allowed_tools == ("read_file",)
    assert result.metadata["session_id"] == "session-1"
    assert result.metadata["cache_fingerprint"]
    output = tmp_path / "run" / result.artifacts[0].relative_path
    assert output.read_text() == '{"answer":"ok","count":2}'


def test_invalid_structured_output_fails_without_repairing_prose(tmp_path):
    runner = FakeAgentRunner('Result: {"answer":"ok"}')
    node = _node(
        "analyze",
        "Analyze",
        output_format={"type": "object", "required": ["answer"]},
    )

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, node))

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"
    assert not result.artifacts


def test_shared_context_resumes_only_one_compatible_predecessor(tmp_path):
    runner = FakeAgentRunner("first", "second")
    executor = AgentNodeExecutor(runner, profile_name="default")
    first = _node("first", "First", allowed_tools=["read_file"])
    first_result = executor.execute(_context(tmp_path, first))
    second = _node(
        "second",
        "Second",
        context="shared",
        allowed_tools=["read_file"],
        depends_on=["first"],
    )

    second_result = executor.execute(
        _context(
            tmp_path,
            second,
            predecessor_results={"first": first_result.metadata},
        )
    )

    assert second_result.status == "succeeded"
    assert runner.requests[1].context_mode == "shared"
    assert runner.requests[1].session_id == "session-1"


def test_shared_context_rejects_ambiguous_or_cache_incompatible_join(tmp_path):
    runner = FakeAgentRunner("unused")
    executor = AgentNodeExecutor(runner)
    node = _node(
        "join",
        "Join",
        context="shared",
        allowed_tools=["read_file"],
        depends_on=["left", "right"],
    )
    ambiguous = executor.execute(
        _context(
            tmp_path,
            node,
            predecessor_results={
                "left": {"session_id": "a", "cache_fingerprint": "x"},
                "right": {"session_id": "b", "cache_fingerprint": "x"},
            },
        )
    )
    mismatch_node = _node(
        "second",
        "Second",
        context="shared",
        allowed_tools=["terminal"],
        depends_on=["first"],
    )
    mismatch = executor.execute(
        _context(
            tmp_path,
            mismatch_node,
            predecessor_results={
                "first": {"session_id": "a", "cache_fingerprint": "wrong"}
            },
        )
    )

    assert ambiguous.error_code == "context_ambiguous"
    assert mismatch.error_code == "context_incompatible"
    assert "fresh" in mismatch.error_message
    assert runner.requests == []
