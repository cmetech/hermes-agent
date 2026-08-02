"""Sequential bounded AI iteration for portable ``loop`` nodes."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import re
from typing import Mapping

from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import WorkflowNode, freeze_value
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.store import ArtifactRef


def _artifact(path: Path, run_directory: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _clean_completion(output: str, signal: str) -> tuple[bool, str]:
    tag = re.compile(
        rf"<\s*promise\s*>\s*{re.escape(signal)}\s*<\s*/\s*promise\s*>",
        re.IGNORECASE,
    )
    tagged = tag.search(output) is not None
    cleaned = tag.sub("", output).rstrip()
    if tagged:
        return True, cleaned
    plain = re.compile(rf"(?:^|\n)\s*{re.escape(signal)}\s*[.!?]*\s*$", re.IGNORECASE)
    return plain.search(output) is not None, output.rstrip()


class LoopExecutor:
    def __init__(self, agent_runner, *, deterministic_runner=None) -> None:
        self._agent = AgentNodeExecutor(
            agent_runner,
            deterministic_runner=deterministic_runner,
        )
        self._bash = BashExecutor()

    @staticmethod
    def _cancelled(context: NodeExecutionContext) -> NodeExecutionResult:
        reason = (
            context.cancellation_reason()
            if context.cancellation_reason is not None
            else "cancelled"
        )
        return NodeExecutionResult(
            "interrupted" if reason == "shutdown" else "cancelled",
            error_code=reason or "cancelled",
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        if not isinstance(context.node.value, Mapping):
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="loop must be a mapping",
            )
        loop = context.node.value
        prompt = str(loop.get("prompt", ""))
        signal = str(loop.get("until", ""))
        maximum = loop.get("max_iterations")
        if (
            not prompt
            or not signal
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 100
        ):
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="loop requires prompt, until, and bounded max_iterations",
            )
        previous_state = context.node_state.get("loop_state")
        start_iteration = (
            int(previous_state.get("iteration", 0)) + 1
            if isinstance(previous_state, Mapping)
            else 1
        )
        artifacts: list[ArtifactRef] = []
        if start_iteration > maximum:
            return NodeExecutionResult(
                "failed",
                error_code="loop_max_iterations",
                error_message=f"loop reached its hard limit of {maximum} iterations",
                metadata={"loop_state": {"iteration": maximum}},
            )
        base_variables = context.variable_context
        if not isinstance(base_variables, VariableContext):
            base_variables = VariableContext(workflow_id=context.run_id)
        previous_output = ""
        previous_metadata: Mapping[str, object] | None = None
        resumed = isinstance(previous_state, Mapping)
        fresh_context = bool(loop.get("fresh_context", False))
        if resumed and loop.get("interactive") is not True:
            previous_artifact = previous_state.get("output_artifact")
            if isinstance(previous_artifact, str) and previous_artifact:
                try:
                    path = (context.run_directory / previous_artifact).resolve(
                        strict=True
                    )
                    path.relative_to(context.run_directory.resolve())
                    data = path.read_bytes()
                    if len(data) > context.max_output_bytes:
                        raise ValueError("persisted loop output exceeds its bound")
                    previous_output = data.decode("utf-8")
                except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
                    return NodeExecutionResult(
                        "failed",
                        error_code="loop_state_invalid",
                        error_message=f"persisted loop output is unavailable: {exc}",
                    )

        for iteration in range(start_iteration, maximum + 1):
            if context.is_cancelled is not None and context.is_cancelled():
                cancelled = self._cancelled(context)
                return replace(cancelled, artifacts=tuple(artifacts))
            variables = replace(
                base_variables,
                loop_prev_output=previous_output,
                loop_user_input=(
                    base_variables.loop_user_input
                    if iteration == start_iteration
                    else ""
                ),
            )
            share = not fresh_context and not resumed and previous_metadata is not None
            options = {"context": "shared" if share else "fresh"}
            child = WorkflowNode(
                id=context.node.id,
                node_type="prompt",
                value=prompt,
                depends_on=tuple(dict.fromkeys((
                    *context.node.depends_on,
                    *(("previous",) if share else ()),
                ))),
                source_index=context.node.source_index,
                source_line=context.node.source_line,
                options=freeze_value(options),
            )
            child_context = replace(
                context,
                node=child,
                attempt_id=f"{context.attempt_id}/iteration-{iteration:04d}",
                variable_context=variables,
                predecessor_results=(
                    {"previous": previous_metadata}
                    if previous_metadata is not None and share
                    else {}
                ),
                node_state={},
            )
            result = self._agent.execute(child_context)
            iteration_artifacts = list(result.artifacts)
            artifacts.extend(iteration_artifacts)
            state = {
                "iteration": iteration,
                "max_iterations": maximum,
                "fresh_context": fresh_context,
            }
            if result.status != "succeeded":
                return replace(
                    result,
                    artifacts=tuple(artifacts),
                    metadata={**result.metadata, "loop_state": state},
                )
            output_path = context.run_directory / result.artifacts[-1].relative_path
            output = output_path.read_text(encoding="utf-8")
            completed, cleaned = _clean_completion(output, signal)
            if cleaned != output:
                output_path.write_text(cleaned, encoding="utf-8")
                artifacts[-1] = _artifact(
                    output_path, context.run_directory, result.artifacts[-1].media_type
                )
                iteration_artifacts[-1] = artifacts[-1]
            previous_output = cleaned
            previous_metadata = result.metadata
            state["output_artifact"] = iteration_artifacts[-1].relative_path
            if context.record_iteration is not None:
                context.record_iteration(tuple(iteration_artifacts), state)
            if completed:
                state["completed_by"] = "signal"
                return NodeExecutionResult(
                    "succeeded", tuple(artifacts), metadata={"loop_state": state}
                )
            until_bash = loop.get("until_bash")
            if isinstance(until_bash, str) and until_bash:
                bash_variables = replace(variables, loop_prev_output=cleaned)
                spill = (
                    context.run_directory
                    / "nodes"
                    / context.node.id
                    / context.attempt_id
                    / f"until-{iteration:04d}-variables"
                )
                bash_renderer = substitution_renderer(
                    bash_variables,
                    direct_dependencies=context.node.depends_on,
                    output_resolver=context.output_resolver,
                )
                rendered = bash_renderer.render_bash(
                    until_bash,
                    spill_directory=spill,
                )
                bash_node = WorkflowNode(
                    id=context.node.id,
                    node_type="bash",
                    value=rendered,
                    depends_on=(),
                    source_index=context.node.source_index,
                    source_line=context.node.source_line,
                    options=freeze_value({}),
                )
                check = self._bash.execute(
                    replace(
                        context,
                        node=bash_node,
                        attempt_id=f"{context.attempt_id}/until-{iteration:04d}",
                        variable_context=bash_variables,
                        predecessor_results={},
                        node_state={},
                    )
                )
                if check.status == "succeeded":
                    state["completed_by"] = "until_bash"
                    return NodeExecutionResult(
                        "succeeded", tuple(artifacts), metadata={"loop_state": state}
                    )
                if check.error_code != "process_exit":
                    return replace(
                        check,
                        artifacts=tuple(artifacts),
                        metadata={**check.metadata, "loop_state": state},
                    )
            if context.is_cancelled is not None and context.is_cancelled():
                cancelled = self._cancelled(context)
                return replace(cancelled, artifacts=tuple(artifacts))
            if loop.get("interactive") is True:
                message = str(loop.get("gate_message", ""))
                identity = hashlib.sha256(
                    f"{context.run_id}\0{context.node.id}\0{iteration}\0{message}".encode()
                ).hexdigest()
                interaction = {
                    "type": "loop_input",
                    "interaction_id": identity,
                    "message": message,
                    "iteration": iteration,
                }
                return NodeExecutionResult(
                    "paused",
                    tuple(artifacts),
                    metadata={
                        "pending_interaction": interaction,
                        "loop_state": state,
                    },
                )
            resumed = False

        return NodeExecutionResult(
            "failed",
            tuple(artifacts),
            "loop_max_iterations",
            f"loop reached its hard limit of {maximum} iterations",
            {"loop_state": {"iteration": maximum, "max_iterations": maximum}},
        )


__all__ = ["LoopExecutor"]
