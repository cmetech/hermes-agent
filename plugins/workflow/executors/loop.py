"""Sequential bounded AI iteration for portable ``loop`` nodes."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
from pathlib import Path
import re
from typing import Mapping

from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
from plugins.workflow.language import (
    supports_phase3_semantics,
    supports_phase4_semantics,
)
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import LoopSignalConfirmation, WorkflowNode, freeze_value
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    ArchonOutputUnavailableError,
    _read_descriptor_relative,
)
from plugins.workflow.resources import (
    ResourceResolver,
    VariableContext,
    substitution_renderer,
)
from plugins.workflow.store import ArtifactRef


def _artifact(path: Path, run_directory: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _clean_completion(
    output: str,
    signal: str,
    *,
    strip_plain: bool = False,
) -> tuple[bool, str]:
    tag = re.compile(
        rf"<\s*promise\s*>\s*{re.escape(signal)}\s*<\s*/\s*promise\s*>",
        re.IGNORECASE,
    )
    tagged = tag.search(output) is not None
    cleaned = tag.sub("", output).rstrip()
    if tagged:
        return True, cleaned
    plain = re.compile(rf"(?:^|\n)\s*{re.escape(signal)}\s*[.!?]*\s*$", re.IGNORECASE)
    matched = plain.search(output) is not None
    return matched, plain.sub("", output).rstrip() if matched and strip_plain else output.rstrip()


class LoopExecutor:
    def __init__(self, agent_runner, *, deterministic_runner=None) -> None:
        self._agent = AgentNodeExecutor(
            agent_runner,
            deterministic_runner=deterministic_runner,
        )
        self._bash = BashExecutor()

    @staticmethod
    def _cancelled(
        context: NodeExecutionContext,
        *,
        loop_state: Mapping[str, object] | None = None,
        loop_input_consumed: bool = False,
    ) -> NodeExecutionResult:
        reason = (
            context.cancellation_reason()
            if context.cancellation_reason is not None
            else "cancelled"
        )
        return NodeExecutionResult(
            "interrupted" if reason == "shutdown" else "cancelled",
            error_code=reason or "cancelled",
            metadata={
                **({"loop_state": dict(loop_state)} if loop_state is not None else {}),
                **(
                    {"_loop_input_consumed": True}
                    if loop_input_consumed
                    else {}
                ),
            },
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        if not isinstance(context.node.value, Mapping):
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="loop must be a mapping",
            )
        loop = context.node.value
        phase4 = supports_phase4_semantics(
            context.language_profile,
            context.normalizer_version,
        )
        loop_semantics = context.node.options.get("_sealed_loop_semantics")
        if phase4 and (
            not isinstance(loop_semantics, Mapping)
            or set(loop_semantics)
            != {
                "prompt_source",
                "command_binding",
                "effective_interactive",
                "signal_completes",
            }
        ):
            return NodeExecutionResult(
                "failed",
                error_code="workflow_snapshot_integrity_mismatch",
                error_message="sealed loop semantics are unavailable",
            )
        if phase4 and loop_semantics["prompt_source"] == "command":
            binding = loop_semantics["command_binding"]
            if not isinstance(binding, str) or loop.get("command") != binding:
                return NodeExecutionResult(
                    "failed",
                    error_code="workflow_snapshot_integrity_mismatch",
                    error_message="sealed loop command binding is unavailable",
                )
            try:
                prompt = ResourceResolver(
                    context.run_directory,
                    sealed_paths=context.sealed_resource_paths,
                    sealed_bytes=context.sealed_resource_bytes,
                ).command(binding).body
            except (FileNotFoundError, UnicodeError, ValueError) as exc:
                return NodeExecutionResult(
                    "failed",
                    error_code="workflow_snapshot_integrity_mismatch",
                    error_message=f"sealed loop command binding is unavailable: {exc}",
                )
        else:
            prompt = str(loop.get("prompt", ""))
        effective_interactive = (
            bool(loop_semantics["effective_interactive"])
            if phase4
            else loop.get("interactive") is True
        )
        signal_completes = (
            bool(loop_semantics["signal_completes"])
            if phase4
            else True
        )
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
                metadata={
                    "loop_state": {"iteration": maximum},
                    **({"archon_terminal_failure": True} if phase4 else {}),
                },
            )
        base_variables = context.variable_context
        if not isinstance(base_variables, VariableContext):
            base_variables = VariableContext(workflow_id=context.run_id)
        until_bash_template = loop.get("until_bash")
        loop_output_resolver = context.output_resolver
        if supports_phase3_semantics(
            context.language_profile, base_variables.normalizer_version
        ):
            strict_renderer = substitution_renderer(
                base_variables,
                direct_dependencies=context.node.depends_on,
                output_resolver=context.output_resolver,
            )
            reference_snapshot = strict_renderer.resolve_outputs(
                prompt,
                until_bash_template if isinstance(until_bash_template, str) else "",
            )

            def frozen_output_resolver(node_id, path):
                return reference_snapshot[(node_id, tuple(path))]

            loop_output_resolver = frozen_output_resolver
        previous_output = ""
        previous_metadata: Mapping[str, object] | None = None
        resumed = isinstance(previous_state, Mapping)
        fresh_context = bool(loop.get("fresh_context", False))
        committed_state: dict[str, object] | None = None
        if phase4:
            committed_state = {
                key: previous_state[key]
                for key in (
                    "iteration",
                    "max_iterations",
                    "fresh_context",
                    "output_artifact",
                    "output_size_bytes",
                    "output_sha256",
                )
                if isinstance(previous_state, Mapping) and key in previous_state
            }
            committed_state.setdefault("iteration", 0)
            committed_state.setdefault("max_iterations", maximum)
            committed_state.setdefault("fresh_context", fresh_context)
        if resumed and (phase4 or loop.get("interactive") is not True):
            previous_artifact = previous_state.get("output_artifact")
            if isinstance(previous_artifact, str) and previous_artifact:
                try:
                    if phase4:
                        expected_size = previous_state.get("output_size_bytes")
                        expected_digest = previous_state.get("output_sha256")
                        if (
                            isinstance(expected_size, bool)
                            or not isinstance(expected_size, int)
                            or expected_size < 0
                            or expected_size > context.max_output_bytes
                            or not isinstance(expected_digest, str)
                        ):
                            raise ValueError(
                                "persisted loop output identity changed"
                            )
                        data = _read_descriptor_relative(
                            context.run_directory,
                            previous_artifact,
                            size_bytes=expected_size,
                        )
                        if not hmac.compare_digest(
                            hashlib.sha256(data).hexdigest(), expected_digest
                        ):
                            raise ValueError(
                                "persisted loop output identity changed"
                            )
                    else:
                        path = (context.run_directory / previous_artifact).resolve(
                            strict=True
                        )
                        path.relative_to(context.run_directory.resolve())
                        if not path.is_file():
                            raise ValueError(
                                "persisted loop output is not a regular file"
                            )
                        before = path.stat()
                        data = path.read_bytes()
                        after = path.stat()
                        if (
                            before.st_dev,
                            before.st_ino,
                            before.st_size,
                            before.st_mtime_ns,
                        ) != (
                            after.st_dev,
                            after.st_ino,
                            after.st_size,
                            after.st_mtime_ns,
                        ):
                            raise ValueError(
                                "persisted loop output changed during read"
                            )
                        if len(data) > context.max_output_bytes:
                            raise ValueError(
                                "persisted loop output exceeds its bound"
                            )
                    previous_output = data.decode("utf-8")
                except (
                    ArchonOutputIntegrityError,
                    ArchonOutputUnavailableError,
                    FileNotFoundError,
                    OSError,
                    UnicodeError,
                    ValueError,
                ) as exc:
                    return NodeExecutionResult(
                        "failed",
                        error_code="loop_state_invalid",
                        error_message=f"persisted loop output is unavailable: {exc}",
                    )

        for iteration in range(start_iteration, maximum + 1):
            if context.is_cancelled is not None and context.is_cancelled():
                cancelled = self._cancelled(
                    context,
                    loop_state=committed_state,
                )
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
                output_resolver=loop_output_resolver,
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
                persisted_state = committed_state if phase4 else state
                return replace(
                    result,
                    artifacts=tuple(artifacts),
                    metadata={
                        **result.metadata,
                        "loop_state": persisted_state,
                        **(
                            {"_loop_input_consumed": True}
                            if phase4
                            and iteration == start_iteration
                            and bool(base_variables.loop_user_input)
                            else {}
                        ),
                    },
                )
            output_path = context.run_directory / result.artifacts[-1].relative_path
            output = output_path.read_text(encoding="utf-8")
            completed, cleaned = _clean_completion(
                output,
                signal,
                strip_plain=phase4,
            )
            if cleaned != output:
                output_path.write_text(cleaned, encoding="utf-8")
                artifacts[-1] = _artifact(
                    output_path, context.run_directory, result.artifacts[-1].media_type
                )
                iteration_artifacts[-1] = artifacts[-1]
            previous_output = cleaned
            previous_metadata = result.metadata
            state["output_artifact"] = iteration_artifacts[-1].relative_path
            if phase4:
                state["output_size_bytes"] = iteration_artifacts[-1].size_bytes
                state["output_sha256"] = iteration_artifacts[-1].sha256
            pending_confirmation = None
            if phase4 and completed and not signal_completes:
                result_artifact = iteration_artifacts[-1]
                pending_confirmation = LoopSignalConfirmation.create(
                    run_id=context.run_id,
                    node_id=context.node.id,
                    message=str(loop.get("gate_message", "")),
                    iteration=iteration,
                    max_iterations=maximum,
                    result_artifact=result_artifact.relative_path,
                    result_sha256=result_artifact.sha256,
                )
            message = str(loop.get("gate_message", ""))
            input_interaction = None
            if (
                phase4
                and not completed
                and not (isinstance(until_bash_template, str) and until_bash_template)
                and iteration < maximum
                and effective_interactive
            ):
                identity = hashlib.sha256(
                    f"{context.run_id}\0{context.node.id}\0{iteration}\0{message}".encode()
                ).hexdigest()
                input_interaction = {
                    "type": "loop_input",
                    "interaction_id": identity,
                    "message": message,
                    "iteration": iteration,
                }
            pending_decision = None
            if phase4:
                if completed and signal_completes:
                    pending_decision = {
                        "kind": "signal_success",
                        "iteration": iteration,
                    }
                elif pending_confirmation is not None:
                    pending_decision = {
                        "kind": "signal_confirmation",
                        "iteration": iteration,
                        "pending_interaction": pending_confirmation.to_dict(),
                    }
                elif isinstance(until_bash_template, str) and until_bash_template:
                    pending_decision = {
                        "kind": "until_bash_pending",
                        "iteration": iteration,
                        "until_bash_sha256": hashlib.sha256(
                            until_bash_template.encode("utf-8")
                        ).hexdigest(),
                    }
                elif iteration == maximum:
                    pending_decision = {
                        "kind": "hard_limit",
                        "iteration": iteration,
                    }
                elif input_interaction is not None:
                    pending_decision = {
                        "kind": "ordinary_input",
                        "iteration": iteration,
                        "pending_interaction": input_interaction,
                    }
                else:
                    pending_decision = {
                        "kind": "continue",
                        "iteration": iteration,
                    }
            if context.record_iteration is not None:
                journal_state = state
                if pending_decision is not None:
                    journal_state = {
                        **state,
                        "_pending_loop_decision": pending_decision,
                    }
                context.record_iteration(
                    tuple(iteration_artifacts), journal_state
                )
            if phase4:
                committed_state = dict(state)
            if completed and signal_completes:
                state["completed_by"] = "signal"
                return NodeExecutionResult(
                    "succeeded", tuple(artifacts), metadata={"loop_state": state}
                )
            if completed:
                state["completed_by"] = "signal_pending_confirmation"
                assert pending_confirmation is not None
                return NodeExecutionResult(
                    "paused",
                    tuple(artifacts),
                    metadata={
                        "pending_interaction": pending_confirmation.to_dict(),
                        "loop_state": state,
                    },
                )
            until_bash = until_bash_template
            if isinstance(until_bash, str) and until_bash:
                bash_variables = replace(variables, loop_prev_output=cleaned)
                bash_node = WorkflowNode(
                    id=context.node.id,
                    node_type="bash",
                    value=until_bash,
                    depends_on=context.node.depends_on,
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
                        output_resolver=loop_output_resolver,
                        predecessor_results={},
                        node_state={},
                    )
                )
                if check.status == "succeeded":
                    state["completed_by"] = "until_bash"
                    if phase4 and context.record_loop_decision is not None:
                        context.record_loop_decision({
                            **state,
                            "_pending_loop_decision": {
                                "kind": "until_bash_success",
                                "iteration": iteration,
                            },
                        })
                    return NodeExecutionResult(
                        "succeeded", tuple(artifacts), metadata={"loop_state": state}
                    )
                if check.error_code != "process_exit":
                    if phase4 and context.record_loop_decision is not None:
                        context.record_loop_decision({
                            **state,
                            "_pending_loop_decision": {
                                "kind": "until_bash_failure",
                                "iteration": iteration,
                                "status": check.status,
                                "error_code": check.error_code,
                                "error_message": check.error_message,
                                "archon_terminal_failure": (
                                    check.metadata.get("archon_terminal_failure") is True
                                ),
                            },
                        })
                    return replace(
                        check,
                        artifacts=tuple(artifacts),
                        metadata={**check.metadata, "loop_state": state},
                    )
            if context.is_cancelled is not None and context.is_cancelled():
                cancelled = self._cancelled(
                    context,
                    loop_state=committed_state,
                    loop_input_consumed=(
                        iteration == start_iteration
                        and bool(base_variables.loop_user_input)
                    ),
                )
                return replace(cancelled, artifacts=tuple(artifacts))
            if iteration == maximum:
                if (
                    phase4
                    and isinstance(until_bash_template, str)
                    and until_bash_template
                    and context.record_loop_decision is not None
                ):
                    context.record_loop_decision({
                        **state,
                        "_pending_loop_decision": {
                            "kind": "hard_limit",
                            "iteration": iteration,
                        },
                    })
                break
            if effective_interactive:
                identity = hashlib.sha256(
                    f"{context.run_id}\0{context.node.id}\0{iteration}\0{message}".encode()
                ).hexdigest()
                interaction = {
                    "type": "loop_input",
                    "interaction_id": identity,
                    "message": message,
                    "iteration": iteration,
                }
                if (
                    phase4
                    and isinstance(until_bash_template, str)
                    and until_bash_template
                    and context.record_loop_decision is not None
                ):
                    context.record_loop_decision({
                        **state,
                        "_pending_loop_decision": {
                            "kind": "ordinary_input",
                            "iteration": iteration,
                            "pending_interaction": interaction,
                        },
                    })
                return NodeExecutionResult(
                    "paused",
                    tuple(artifacts),
                    metadata={
                        "pending_interaction": interaction,
                        "loop_state": state,
                    },
                )
            if (
                phase4
                and isinstance(until_bash_template, str)
                and until_bash_template
                and context.record_loop_decision is not None
            ):
                context.record_loop_decision({
                    **state,
                    "_pending_loop_decision": {
                        "kind": "continue",
                        "iteration": iteration,
                    },
                })
            resumed = False

        return NodeExecutionResult(
            "failed",
            tuple(artifacts),
            "loop_max_iterations",
            f"loop reached its hard limit of {maximum} iterations",
            {
                "loop_state": (
                    committed_state
                    if phase4 and committed_state is not None
                    else {
                        "iteration": maximum,
                        "max_iterations": maximum,
                    }
                ),
                **({"archon_terminal_failure": True} if phase4 else {}),
            },
        )


__all__ = ["LoopExecutor"]
