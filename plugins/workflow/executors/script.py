"""Contained Bun and uv execution for portable ``script`` nodes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from plugins.workflow.executors.base import (
    BoundedProcessOutput,
    NodeExecutionContext,
    NodeExecutionResult,
    process_tree_active,
)
from plugins.workflow.resources import ResourceResolver, VariableContext
from plugins.workflow.schema import is_inline_script
from plugins.workflow.store import ArtifactRef
from tools.managed_process import ManagedProcessTree


def _artifact(path: Path, run_directory: Path, media_type: str) -> ArtifactRef:
    data = path.read_bytes()
    return ArtifactRef(
        relative_path=path.relative_to(run_directory).as_posix(),
        media_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


class ScriptExecutor:
    def __init__(
        self, *, runtime_locator: Callable[[str], str | None] = shutil.which
    ) -> None:
        self._runtime_locator = runtime_locator

    def _argv(
        self, context: NodeExecutionContext, runtime_path: str
    ) -> tuple[list[str], tuple[str, ...]]:
        node = context.node
        runtime = str(node.options["runtime"])
        dependencies = tuple(str(dep) for dep in node.options.get("deps", ()))
        inline = is_inline_script(str(node.value))
        variables = context.variable_context
        if not isinstance(variables, VariableContext):
            variables = VariableContext(workflow_id=context.run_id)
        warnings: tuple[str, ...] = ()
        if runtime == "bun":
            if dependencies:
                warnings = ("script deps are ignored for the bun runtime",)
            if inline:
                return [
                    runtime_path,
                    "--no-env-file",
                    "-e",
                    variables.render_prompt(str(node.value)),
                ], warnings
            resource = ResourceResolver(context.run_directory).script(
                str(node.value), runtime=runtime
            )
            return [runtime_path, "--no-env-file", "run", str(resource.path)], warnings
        argv = [runtime_path, "run", "--no-project"]
        for dependency in dependencies:
            argv.extend(("--with", dependency))
        if inline:
            argv.extend(("python", "-c", variables.render_prompt(str(node.value))))
        else:
            resource = ResourceResolver(context.run_directory).script(
                str(node.value), runtime=runtime
            )
            argv.extend(("python", str(resource.path)))
        return argv, warnings

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        runtime = str(context.node.options.get("runtime", ""))
        if runtime not in {"bun", "uv"}:
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="invalid script runtime",
            )
        runtime_path = self._runtime_locator(runtime)
        if not runtime_path:
            return NodeExecutionResult(
                "failed",
                error_code="runtime_missing",
                error_message=f"script runtime is not installed: {runtime}",
            )
        attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        stdout_path = attempt / "stdout.txt"
        stderr_path = attempt / "stderr.txt"
        artifacts_dir = context.run_directory / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        try:
            argv, warnings = self._argv(context, runtime_path)
        except (FileNotFoundError, OSError, ValueError) as exc:
            return NodeExecutionResult(
                "failed", error_code="validation", error_message=str(exc)
            )
        allowed_env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {"PATH", "HOME", "TMPDIR", "TEMP", "SystemRoot", "ComSpec", "PATHEXT"}
        }
        allowed_env.update({
            "HERMES_WORKFLOW_RUN_ID": context.run_id,
            "HERMES_WORKFLOW_RUN_DIR": str(context.run_directory),
            "ARTIFACTS_DIR": str(artifacts_dir),
        })
        variables = context.variable_context
        if isinstance(variables, VariableContext):
            allowed_env.update(variables.environment())
        output = BoundedProcessOutput(
            stdout_path, stderr_path, limit=context.max_output_bytes
        )
        try:
            tree = ManagedProcessTree.spawn(
                argv,
                policy=context.termination_policy,
                cwd=context.run_directory,
                env=allowed_env,
                stdout=output.stdout,
                stderr=output.stderr,
            )
        except OSError as exc:
            output.close()
            return NodeExecutionResult(
                "failed", error_code="runtime_missing", error_message=str(exc)
            )
        cancelled = False
        if context.process_started is not None and not context.process_started(
            tree.identity
        ):
            cancelled = True
            tree.terminate("workflow cancellation won process registration")
        started = context.monotonic()
        timed_out = False
        resource_violation = None
        output_limited = False
        cleanup_error = None
        try:
            while process_tree_active(tree):
                if context.is_cancelled is not None and context.is_cancelled():
                    cancelled = True
                    tree.terminate("workflow run cancelled")
                    break
                if (
                    context.deadline_budget is not None
                    and context.deadline_budget.wall_expired(context.monotonic())
                ) or context.monotonic() - started >= context.timeout_seconds:
                    timed_out = True
                    tree.terminate("workflow script timeout")
                    break
                limit_exceeded, output_grew = output.poll()
                if output_grew and context.deadline_budget is not None:
                    context.deadline_budget.semantic_progress(context.monotonic())
                if limit_exceeded:
                    output_limited = True
                    tree.terminate("workflow script output limit")
                    break
                if tree.process.poll() is None:
                    resource_violation = tree.resource_violation(
                        context.resource_limits
                    )
                    if resource_violation is not None:
                        tree.terminate("workflow script resource limit")
                        break
                time.sleep(0.01)
        except RuntimeError as exc:
            cleanup_error = str(exc)
        finally:
            try:
                tree.close()
            except RuntimeError as exc:
                cleanup_error = cleanup_error or str(exc)
            output_limited = output.close() or output_limited
            if context.process_stopped is not None:
                context.process_stopped(
                    tree.identity, cleanup_error is None and tree.reaped
                )
        returncode = tree.process.returncode
        output_text = stdout_path.read_text(encoding="utf-8").rstrip("\r\n")
        stdout_path.write_text(output_text, encoding="utf-8")
        media_type = "text/plain"
        try:
            json.loads(output_text)
        except json.JSONDecodeError:
            pass
        else:
            json_path = stdout_path.with_name("output.json")
            stdout_path.replace(json_path)
            stdout_path = json_path
            media_type = "application/json"
        artifacts = [_artifact(stdout_path, context.run_directory, media_type)]
        if stderr_path.stat().st_size:
            artifacts.append(
                _artifact(stderr_path, context.run_directory, "text/plain")
            )
        metadata = {"warnings": warnings} if warnings else {}
        if cleanup_error is not None:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "cleanup_failed",
                cleanup_error,
                metadata,
            )
        if cancelled:
            reason = (
                context.cancellation_reason()
                if context.cancellation_reason is not None
                else "cancelled"
            )
            return NodeExecutionResult(
                "interrupted" if reason == "shutdown" else "cancelled",
                tuple(artifacts),
                reason or "cancelled",
                metadata=metadata,
            )
        if timed_out:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "timeout",
                "script node exceeded its timeout",
                metadata,
            )
        if output_limited:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "output_limit",
                "script node exceeded its output limit",
                metadata,
            )
        if resource_violation is not None:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "resource_limit",
                f"script node exceeded {resource_violation}",
                {**metadata, "resource_code": resource_violation},
            )
        if returncode != 0:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "process_exit",
                f"script node exited with status {returncode}",
                metadata,
            )
        return NodeExecutionResult("succeeded", tuple(artifacts), metadata=metadata)


__all__ = ["ScriptExecutor"]
