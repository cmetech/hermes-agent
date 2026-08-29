from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.executors.loop import LoopExecutor
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value


class FakeAgentRunner:
    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        return PluginAgentRunResult(
            final_response=self.response,
            session_id="session-1",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def context(
    tmp_path: Path,
    *,
    node_id: str = "n",
    attempt_id: str = "a",
    node_type: str = "prompt",
    value: object = "output",
    options: dict[str, object] | None = None,
    node_state: dict[str, object] | None = None,
) -> NodeExecutionContext:
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=tmp_path,
        node=WorkflowNode(
            id=node_id,
            node_type=node_type,
            value=freeze_value(value) if isinstance(value, dict) else value,
            depends_on=(),
            source_index=0,
            source_line=1,
            options=freeze_value(options or {}),
        ),
        attempt_id=attempt_id,
        workflow_options=freeze_value({"provider": "fake-provider", "model": "fake-model"}),
        node_state=freeze_value(node_state or {}),
    )


def _scoped(context: NodeExecutionContext, name: str) -> NodeExecutionContext:
    return replace(
        context,
        attempt_directory=context.run_directory / "scoped" / name / "attempt",
        publication_directory=context.run_directory / "scoped" / name / "artifacts",
    )


def test_context_preserves_top_level_paths_and_accepts_scoped_paths(tmp_path):
    base = context(tmp_path, node_id="n", attempt_id="a")
    assert base.effective_attempt_directory == tmp_path / "nodes" / "n" / "a"
    assert base.effective_publication_directory == tmp_path / "artifacts"

    child = replace(
        base,
        attempt_directory=tmp_path / "nodes/g/1/iterations/0001/nodes/n/a",
        publication_directory=tmp_path / "artifacts/loop-groups/g/iterations/0001/n",
    )
    assert child.effective_attempt_directory == child.attempt_directory
    assert child.effective_publication_directory == child.publication_directory


def test_bash_executor_writes_scoped_attempt_and_publication_files(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="bash",
            value="printf publication > \"$ARTIFACTS_DIR/bash.txt\"; printf bash",
        ),
        "bash",
    )

    result = BashExecutor().execute(execution)

    assert result.status == "succeeded"
    assert (execution.effective_attempt_directory / "stdout.txt").read_text() == "bash"
    assert (execution.effective_publication_directory / "bash.txt").read_text() == "publication"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "stdout.txt"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_script_executor_writes_scoped_attempt_and_publication_files(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="script",
            value=(
                "import os; from pathlib import Path; "
                "Path(os.environ['ARTIFACTS_DIR']).joinpath('script.txt').write_text('publication'); "
                "print('script')"
            ),
            options={"runtime": "uv", "deps": ()},
        ),
        "script",
    )

    result = ScriptExecutor().execute(execution)

    assert result.status == "succeeded"
    assert (execution.effective_attempt_directory / "stdout.txt").read_text() == "script"
    assert (execution.effective_publication_directory / "script.txt").read_text() == "publication"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "stdout.txt"


def test_agent_executor_writes_scoped_attempt_file(tmp_path: Path) -> None:
    execution = _scoped(context(tmp_path), "agent")

    result = AgentNodeExecutor(FakeAgentRunner("agent")).execute(execution)

    assert result.status == "succeeded"
    assert (execution.effective_attempt_directory / "output.txt").read_text() == "agent"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "output.txt"


def test_archon_agent_writer_uses_scoped_attempt_file(tmp_path: Path) -> None:
    execution = replace(
        _scoped(context(tmp_path), "archon-agent"),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
    )

    result = AgentNodeExecutor._write_archon_output(
        execution,
        b"archon",
        {},
        is_structured=False,
        structured_value=None,
        schema_fingerprint=None,
        canonicalization_version=None,
    )

    assert result.status == "succeeded"
    assert (execution.effective_attempt_directory / "output.md").read_bytes() == b"archon"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "output.md"


def test_approval_executor_writes_scoped_attempt_file(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="approval",
            value={
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            },
            node_state={"approval_rework": {"reason": "missing evidence"}},
        ),
        "approval",
    )

    result = ApprovalExecutor(FakeAgentRunner("approval")).execute(execution)

    assert result.status == "paused"
    assert (execution.effective_attempt_directory / "rework-output.txt").read_text() == "approval"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "rework-output.txt"


def test_loop_executor_passes_scoped_attempt_to_child(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="loop",
            value={"prompt": "Work", "until": "DONE", "max_iterations": 1},
        ),
        "loop",
    )

    result = LoopExecutor(FakeAgentRunner("done <promise>DONE</promise>")).execute(execution)

    assert result.status == "succeeded"
    assert (execution.effective_attempt_directory / "output.txt").read_text() == "done"
    assert tmp_path / result.artifacts[0].relative_path == execution.effective_attempt_directory / "output.txt"
