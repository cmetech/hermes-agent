"""Bounded process-tree execution for explicit portable ``bash`` nodes."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

from plugins.workflow.executors.base import (
    BoundedProcessOutput,
    NodeExecutionContext,
    NodeExecutionResult,
    process_tree_active,
)
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


class BashExecutor:
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        if context.sealed_attempt_timeout:
            assert context.deadline_budget is not None
            if context.deadline_budget.wall_expired(context.monotonic()):
                return NodeExecutionResult(
                    "failed",
                    error_code="timeout",
                    error_message="bash node exceeded its timeout",
                )
        attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        stdout_path = attempt / "stdout.txt"
        stderr_path = attempt / "stderr.txt"
        variable_spill = attempt / "variables"
        artifacts_dir = context.run_directory / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        command = str(context.node.value)
        if context.variable_context is not None:
            command = context.variable_context.render_bash(
                command, spill_directory=variable_spill
            )
        if os.name == "nt":  # pragma: no cover - Windows CI path
            from tools.environments.local import _find_bash

            argv = [_find_bash(), "-c", command]
        else:
            argv = ["/bin/sh", "-c", command]
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
        policy = context.termination_policy
        started = context.monotonic()
        if (
            context.sealed_attempt_timeout
            and context.deadline_budget.wall_expired(started)
        ):
            return NodeExecutionResult(
                "failed",
                error_code="timeout",
                error_message="bash node exceeded its timeout",
            )
        timed_out = False
        cancelled = False
        resource_violation = None
        output = BoundedProcessOutput(
            stdout_path, stderr_path, limit=context.max_output_bytes
        )
        if (
            context.sealed_attempt_timeout
            and context.deadline_budget.wall_expired(context.monotonic())
        ):
            output.close()
            return NodeExecutionResult(
                "failed",
                error_code="timeout",
                error_message="bash node exceeded its timeout",
            )
        executor_nonce = uuid.uuid4().hex
        if context.spawn_intent is not None and not context.spawn_intent(executor_nonce):
            output.close()
            raise RuntimeError("executor spawn intent was rejected")
        if (
            context.sealed_attempt_timeout
            and context.deadline_budget.wall_expired(context.monotonic())
        ):
            output.close()
            if context.spawn_failed is not None:
                context.spawn_failed(executor_nonce, "timeout")
            return NodeExecutionResult(
                "failed",
                error_code="timeout",
                error_message="bash node exceeded its timeout",
            )
        try:
            tree = ManagedProcessTree.spawn(
                argv,
                policy=policy,
                cwd=context.run_directory,
                env=allowed_env,
                stdout=output.stdout,
                stderr=output.stderr,
            )
        except BaseException as exc:
            output.close()
            if context.spawn_failed is not None:
                context.spawn_failed(executor_nonce, type(exc).__name__)
            raise
        if context.process_started is not None and not context.process_started(
            tree.identity
        ):
            cancelled = True
            tree.terminate("workflow cancellation won process registration")
        output_limited = False
        cleanup_error = None
        try:
            while process_tree_active(tree):
                if context.is_cancelled is not None and context.is_cancelled():
                    cancelled = True
                    tree.terminate("workflow run cancelled")
                    break
                if context.sealed_attempt_timeout:
                    assert context.deadline_budget is not None
                    deadline_expired = context.deadline_budget.wall_expired(
                        context.monotonic()
                    )
                else:
                    deadline_expired = (
                        context.deadline_budget is not None
                        and context.deadline_budget.wall_expired(context.monotonic())
                    ) or context.monotonic() - started >= context.timeout_seconds
                if deadline_expired:
                    timed_out = True
                    tree.terminate("workflow node timeout")
                    break
                limit_exceeded, output_grew = output.poll()
                if output_grew and context.deadline_budget is not None:
                    context.deadline_budget.semantic_progress(context.monotonic())
                if limit_exceeded:
                    output_limited = True
                    tree.terminate("workflow output limit")
                    break
                if tree.process.poll() is None:
                    resource_violation = tree.resource_violation(
                        context.resource_limits
                    )
                    if resource_violation is not None:
                        tree.terminate("workflow resource limit")
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
        artifacts = [_artifact(stdout_path, context.run_directory, "text/plain")]
        if stderr_path.stat().st_size:
            artifacts.append(
                _artifact(stderr_path, context.run_directory, "text/plain")
            )
        if cleanup_error is not None:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "cleanup_failed",
                cleanup_error,
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
            )
        if timed_out:
            return NodeExecutionResult(
                "failed", tuple(artifacts), "timeout", "bash node exceeded its timeout"
            )
        if output_limited:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "output_limit",
                "bash node exceeded its output limit",
            )
        if resource_violation is not None:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "resource_limit",
                f"bash node exceeded {resource_violation}",
                {"resource_code": resource_violation},
            )
        if returncode != 0:
            return NodeExecutionResult(
                "failed",
                tuple(artifacts),
                "process_exit",
                f"bash node exited with status {returncode}",
            )
        return NodeExecutionResult("succeeded", tuple(artifacts))
