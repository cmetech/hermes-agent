"""Bounded process-tree execution for explicit portable ``bash`` nodes."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
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
        attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
        attempt.mkdir(parents=True, exist_ok=False)
        stdout_path = attempt / "stdout.txt"
        stderr_path = attempt / "stderr.txt"
        artifacts_dir = context.run_directory / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        if os.name == "nt":  # pragma: no cover - Windows CI path
            argv = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/s",
                "/c",
                str(context.node.value),
            ]
        else:
            argv = ["/bin/sh", "-c", str(context.node.value)]
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
        timed_out = False
        cancelled = False
        resource_violation = None
        if context.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        remaining_output = context.max_output_bytes
        output_lock = threading.Lock()
        output_limited = threading.Event()

        def drain(stream, path: Path) -> None:
            nonlocal remaining_output
            try:
                with path.open("wb") as output:
                    while True:
                        chunk = stream.read(64 * 1024)
                        if not chunk:
                            break
                        with output_lock:
                            accepted = min(len(chunk), remaining_output)
                            remaining_output -= accepted
                            if accepted < len(chunk):
                                output_limited.set()
                        if accepted:
                            output.write(chunk[:accepted])
                            if context.deadline_budget is not None:
                                context.deadline_budget.semantic_progress(
                                    context.monotonic()
                                )
            except (OSError, ValueError):
                output_limited.set()
            finally:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        tree = ManagedProcessTree.spawn(
            argv,
            policy=policy,
            cwd=context.run_directory,
            env=allowed_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert tree.process.stdout is not None
        assert tree.process.stderr is not None
        readers = (
            threading.Thread(
                target=drain,
                args=(tree.process.stdout, stdout_path),
                name=f"workflow-stdout-{context.attempt_id}",
                daemon=True,
            ),
            threading.Thread(
                target=drain,
                args=(tree.process.stderr, stderr_path),
                name=f"workflow-stderr-{context.attempt_id}",
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        try:
            while tree.process.poll() is None or any(
                reader.is_alive() for reader in readers
            ):
                if context.is_cancelled is not None and context.is_cancelled():
                    cancelled = True
                    tree.terminate("workflow run cancelled")
                    break
                if (
                    context.deadline_budget is not None
                    and context.deadline_budget.wall_expired(context.monotonic())
                ) or context.monotonic() - started >= context.timeout_seconds:
                    timed_out = True
                    tree.terminate("workflow node timeout")
                    break
                if output_limited.is_set():
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
        finally:
            tree.close()
            for reader in readers:
                reader.join(timeout=policy.wait_timeout_seconds)
            for stream in (tree.process.stdout, tree.process.stderr):
                if not stream.closed:
                    stream.close()
            for reader in readers:
                reader.join(timeout=policy.kill_grace_seconds)
        returncode = tree.process.returncode
        artifacts = [_artifact(stdout_path, context.run_directory, "text/plain")]
        if stderr_path.stat().st_size:
            artifacts.append(
                _artifact(stderr_path, context.run_directory, "text/plain")
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
        if output_limited.is_set():
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
