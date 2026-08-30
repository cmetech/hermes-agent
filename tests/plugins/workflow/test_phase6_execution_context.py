from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

import pytest

from agent.plugin_agent import PluginAgentRunResult
from agent.structured_output import normalize_schema
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.executors.loop import LoopExecutor
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowStructuredOutput,
    freeze_value,
)


class FakeAgentRunner:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        return PluginAgentRunResult(
            final_response=self.responses.pop(0),
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
        publication_directory=(
            context.run_directory / "publications" / name / "nested" / "artifacts"
        ),
    )


def _structured(
    execution: NodeExecutionContext, schema: dict[str, object]
) -> NodeExecutionContext:
    normalized = normalize_schema(schema)
    return replace(
        execution,
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=6,
        structured_output=WorkflowStructuredOutput(
            canonical_schema=normalized.canonical_schema,
            schema_fingerprint=normalized.schema_fingerprint,
        ),
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


def test_v6_bash_rejects_output_that_violates_its_sealed_schema(tmp_path: Path) -> None:
    execution = _structured(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value="printf '{\"count\":2}'",
                options={"output_type": "AggregateJson"},
            ),
            "structured-bash-invalid",
        ),
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "maximum": 1}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"


def test_v6_bash_rejects_empty_output_for_nonempty_schema(tmp_path: Path) -> None:
    execution = _structured(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value=":",
                options={"output_type": "AggregateJson"},
            ),
            "structured-bash-empty",
        ),
        {"type": "object", "minProperties": 1},
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"


def test_v6_bash_rejects_tampered_structured_output_identity(tmp_path: Path) -> None:
    execution = _structured(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value="printf '{\"count\":1}'",
                options={"output_type": "AggregateJson"},
            ),
            "structured-bash-tamper",
        ),
        {
            "type": "object",
            "properties": {"count": {"const": 1}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
    execution = replace(
        execution,
        structured_output=replace(
            execution.structured_output,
            schema_fingerprint="0" * 64,
        ),
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "structured_output_integrity"


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


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_v6_script_validates_canonicalizes_and_authenticates_structured_output(
    tmp_path: Path,
) -> None:
    execution = _structured(
        _scoped(
            context(
                tmp_path,
                node_type="script",
                value="console.log('{\"name\":\"ticket\", \"count\":1}')",
                options={
                    "runtime": "bun",
                    "deps": (),
                    "output_type": "AggregateJson",
                },
            ),
            "structured-script-valid",
        ),
        {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 1},
                "name": {"const": "ticket"},
            },
            "required": ["count", "name"],
            "additionalProperties": False,
        },
    )

    result = ScriptExecutor().execute(execution)

    assert result.status == "succeeded"
    assert result.primary_output is not None
    assert result.primary_output.schema_fingerprint == (
        execution.structured_output.schema_fingerprint
    )
    assert result.primary_output.structured_value == freeze_value(
        {"count": 1, "name": "ticket"}
    )
    output = tmp_path / result.primary_output.attempt_relative_path
    assert output.name == "output.json"
    assert output.read_bytes() == b'{"count":1,"name":"ticket"}'


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
@pytest.mark.parametrize(
    "rendered",
    [
        '{"records":[],"completion_marker":"WRONG"}',
        '{"records":[{"key":"A"},{"key":"A"}],"completion_marker":""}',
        '{}',
    ],
)
def test_v6_script_rejects_adversarial_aggregate_output(
    tmp_path: Path, rendered: str
) -> None:
    execution = _structured(
        _scoped(
            context(
                tmp_path,
                node_type="script",
                value=f"console.log({rendered!r})",
                options={
                    "runtime": "bun",
                    "deps": (),
                    "output_type": "AggregateJson",
                },
            ),
            f"structured-script-invalid-{abs(hash(rendered))}",
        ),
        {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 25,
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"],
                        "additionalProperties": False,
                    },
                },
                "completion_marker": {
                    "enum": ["", "<promise>BATCH_COMPLETE</promise>"]
                },
            },
            "required": ["records", "completion_marker"],
            "additionalProperties": False,
        },
    )

    result = ScriptExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"


def test_artifact_free_bash_rejects_even_an_empty_generated_artifact(
    tmp_path: Path,
) -> None:
    execution = replace(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value=': > "$ARTIFACTS_DIR/forbidden.txt"; printf ok',
                options={"artifacts": False},
            ),
            "artifact-free-bash",
        ),
        max_artifact_bytes=0,
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "artifact_limit"


def test_artifact_free_bash_rejects_an_empty_generated_directory(
    tmp_path: Path,
) -> None:
    execution = replace(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value='mkdir "$ARTIFACTS_DIR/forbidden"; printf ok',
                options={"artifacts": False},
            ),
            "artifact-free-bash-directory",
        ),
        max_artifact_bytes=0,
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "artifact_limit"


def test_artifact_free_bash_fails_closed_on_a_same_content_retry_artifact(
    tmp_path: Path,
) -> None:
    execution = replace(
        _scoped(
            context(
                tmp_path,
                node_type="bash",
                value='printf unchanged > "$ARTIFACTS_DIR/forbidden.txt"; printf ok',
                options={"artifacts": False},
            ),
            "artifact-free-bash-retry",
        ),
        max_artifact_bytes=0,
    )
    execution.effective_publication_directory.mkdir(parents=True)
    (execution.effective_publication_directory / "forbidden.txt").write_text(
        "unchanged", encoding="utf-8"
    )

    result = BashExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "artifact_limit"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_artifact_free_script_rejects_even_an_empty_generated_artifact(
    tmp_path: Path,
) -> None:
    execution = replace(
        _scoped(
            context(
                tmp_path,
                node_type="script",
                value=(
                    "import os; from pathlib import Path; "
                    "Path(os.environ['ARTIFACTS_DIR']).joinpath('forbidden.txt').touch(); "
                    "print('ok')"
                ),
                options={"runtime": "uv", "deps": (), "artifacts": False},
            ),
            "artifact-free-script",
        ),
        max_artifact_bytes=0,
    )

    result = ScriptExecutor().execute(execution)

    assert result.status == "failed"
    assert result.error_code == "artifact_limit"


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
    output = execution.effective_attempt_directory / "iteration-0001" / "output.txt"
    assert output.read_text() == "done"
    assert tmp_path / result.artifacts[0].relative_path == output


def test_loop_executor_scopes_each_ordinary_iteration(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="loop",
            value={"prompt": "Work", "until": "DONE", "max_iterations": 2},
        ),
        "loop-multiple",
    )

    result = LoopExecutor(
        FakeAgentRunner("first", "done <promise>DONE</promise>")
    ).execute(execution)

    assert result.status == "succeeded"
    first = execution.effective_attempt_directory / "iteration-0001" / "output.txt"
    second = execution.effective_attempt_directory / "iteration-0002" / "output.txt"
    assert first.read_text() == "first"
    assert second.read_text() == "done"
    assert [tmp_path / artifact.relative_path for artifact in result.artifacts] == [
        first,
        second,
    ]


def test_loop_executor_scopes_until_bash_check(tmp_path: Path) -> None:
    execution = _scoped(
        context(
            tmp_path,
            node_type="loop",
            value={
                "prompt": "Work",
                "until": "DONE",
                "max_iterations": 2,
                "until_bash": "false",
            },
        ),
        "loop-check",
    )

    result = LoopExecutor(
        FakeAgentRunner("first", "done <promise>DONE</promise>")
    ).execute(execution)

    assert result.status == "succeeded"
    assert (
        execution.effective_attempt_directory / "until-0001" / "stdout.txt"
    ).read_text() == ""
