#!/usr/bin/env python3
"""Execute every executable invariant declared by the workflow ledger.

Each Python or JavaScript test file runs in its own subprocess.  Non-executable
fixture and runner references are retained as reference-only records; they
never receive a test result and therefore cannot satisfy an executed invariant.
"""

from __future__ import annotations

import argparse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import yaml


_DEFAULT_TIMEOUT_SECONDS = 900.0
_DEFAULT_OUTPUT_LIMIT_BYTES = 1_048_576
_POLL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 1.0


def _kind(path: str) -> str:
    item = Path(path)
    if path.startswith("tests/") and item.suffix == ".py" and item.name.startswith(
        "test_"
    ):
        return "python"
    if path.startswith("apps/desktop/electron/") and item.name.endswith(".test.ts"):
        return "desktop-node"
    if path.startswith("apps/desktop/") and ".test." in item.name and item.suffix in {
        ".ts",
        ".tsx",
    }:
        return "desktop"
    if item.name.endswith(".test.mjs"):
        return "node"
    return "reference"


def _command(repo: Path, path: str, kind: str) -> tuple[list[str], Path]:
    if kind == "python":
        # Keep the absolute virtualenv entry point.  Resolving the symlink to
        # uv's base interpreter would silently discard the venv/site-packages.
        command = [str(Path(sys.executable).absolute()), "-m", "pytest", "-q", path]
        if path == "tests/plugins/workflow/test_installed_distribution_e2e.py":
            command.extend(["-m", "integration"])
        return command, repo
    if kind == "desktop":
        relative = Path(path).relative_to("apps/desktop").as_posix()
        return ["npx", "vitest", "run", relative], repo / "apps/desktop"
    if kind == "desktop-node":
        relative = Path(path).relative_to("apps/desktop").as_posix()
        return ["npx", "tsx", "--test", relative], repo / "apps/desktop"
    if kind == "node":
        return ["node", "--test", path], repo
    raise AssertionError(f"unsupported executable invariant kind: {kind}")


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.lock = threading.Lock()

    def consume(self, stream: Any) -> None:
        try:
            while chunk := stream.read(65_536):
                with self.lock:
                    remaining = self.limit - len(self.data)
                    if remaining > 0:
                        self.data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.truncated = True
        finally:
            stream.close()

    def text(self) -> str:
        rendered = bytes(self.data).decode("utf-8", errors="replace")
        if self.truncated:
            rendered += "\n...[output truncated]"
        return rendered


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait()


def _execute_attempt(
    repo: Path,
    path: str,
    kind: str,
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    command, cwd = _command(repo, path, kind)
    env = os.environ.copy()
    for inherited in (
        "HERMES_PYTHON",
        "PYTEST_ADDOPTS",
        "PYTHON_BIN",
        "WORKFLOW_MERGE_GATE_FAST",
    ):
        env.pop(inherited, None)
    env.update(
        {
            "HERMES_OFFLINE": "1",
            "NOUS_API_KEY": "",
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "PYTHONUTF8": "1",
            "WORKFLOW_LEDGER_EXECUTION_ACTIVE": "1",
        }
    )
    started = time.monotonic_ns()
    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(command, **popen_options)
    except OSError as exc:
        duration_ms = (time.monotonic_ns() - started) // 1_000_000
        return {
            "result": "infrastructure_error",
            "duration_ms": duration_ms,
            "output_truncated": False,
            "_stdout": "",
            "_stderr": str(exc),
        }
    stdout = _BoundedCapture(output_limit_bytes)
    stderr = _BoundedCapture(output_limit_bytes)
    readers = [
        threading.Thread(target=stdout.consume, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.consume, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while process.poll() is None:
            if cancel_event.is_set():
                _terminate_process_group(process)
                raise CancelledError()
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_group(process)
                break
            time.sleep(_POLL_SECONDS)
    except BaseException:
        _terminate_process_group(process)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=_TERMINATE_GRACE_SECONDS)
    duration_ms = (time.monotonic_ns() - started) // 1_000_000
    returncode = process.returncode
    if timed_out:
        result = "timed_out"
    elif returncode == 0:
        result = "passed"
    elif returncode is not None and returncode < 0:
        result = "signaled"
    elif returncode == 1:
        result = "failed"
    else:
        result = "infrastructure_error"
    record = {
        "result": result,
        "duration_ms": duration_ms,
        "output_truncated": stdout.truncated or stderr.truncated,
        "_stdout": stdout.text(),
        "_stderr": stderr.text(),
    }
    if result == "signaled" and returncode is not None:
        record["termination_signal"] = -returncode
    return record


def _execute(
    repo: Path,
    path: str,
    kind: str,
    platform: str,
    timeout_seconds: float,
    output_limit_bytes: int,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    attempts = [
        _execute_attempt(
            repo,
            path,
            kind,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            cancel_event=cancel_event,
        )
    ]
    if attempts[0]["result"] == "failed":
        if cancel_event.is_set():
            raise CancelledError()
        attempts.append(
            _execute_attempt(
                repo,
                path,
                kind,
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
                cancel_event=cancel_event,
            )
        )
    digest = hashlib.sha256(path.encode()).hexdigest()
    result = "passed" if attempts[-1]["result"] == "passed" else "failed"
    return {
        "kind": "executed",
        "name": f"ledger invariant {digest}",
        "path": path,
        "result": result,
        "duration_ms": sum(int(attempt["duration_ms"]) for attempt in attempts),
        "platform": platform,
        "attempts": [
            {
                "attempt": index,
                "result": attempt["result"],
                "duration_ms": attempt["duration_ms"],
                "output_truncated": attempt["output_truncated"],
                **(
                    {"termination_signal": attempt["termination_signal"]}
                    if "termination_signal" in attempt
                    else {}
                ),
            }
            for index, attempt in enumerate(attempts, start=1)
        ],
        "flaky_on_first_attempt": (
            len(attempts) == 2
            and attempts[0]["result"] == "failed"
            and result == "passed"
        ),
        "_stdout": "".join(str(attempt["_stdout"]) for attempt in attempts),
        "_stderr": "".join(str(attempt["_stderr"]) for attempt in attempts),
    }


def _run_group(
    repo: Path,
    paths: list[str],
    kind: str,
    platform: str,
    workers: int,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not paths:
        return results
    cancel_event = threading.Event()
    executor = ThreadPoolExecutor(max_workers=min(workers, len(paths)))
    futures: dict[Any, str] = {}
    try:
        futures = {
            executor.submit(
                _execute,
                repo,
                path,
                kind,
                platform,
                timeout_seconds,
                output_limit_bytes,
                cancel_event,
            ): path
            for path in paths
        }
        for future in as_completed(futures):
            results.append(future.result())
    except BaseException:
        cancel_event.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--output-limit-bytes", type=int, default=_DEFAULT_OUTPUT_LIMIT_BYTES
    )
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= _DEFAULT_TIMEOUT_SECONDS:
        parser.error(
            f"--timeout-seconds must be in (0, {_DEFAULT_TIMEOUT_SECONDS:g}]"
        )
    if not 1 <= args.output_limit_bytes <= _DEFAULT_OUTPUT_LIMIT_BYTES:
        parser.error(
            "--output-limit-bytes must be in "
            f"[1, {_DEFAULT_OUTPUT_LIMIT_BYTES}]"
        )

    repo = args.repo.resolve()
    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    paths = sorted(
        {
            path
            for entry in manifest["upstream_changes"]
            for path in entry["tests"]
        }
    )
    by_kind = {
        kind: [path for path in paths if _kind(path) == kind]
        for kind in ("python", "desktop", "desktop-node", "node")
    }
    results: list[dict[str, Any]] = []
    group_options = (args.timeout_seconds, args.output_limit_bytes)
    results.extend(
        _run_group(repo, by_kind["python"], "python", args.platform, 2, *group_options)
    )
    results.extend(
        _run_group(
            repo, by_kind["desktop"], "desktop", args.platform, 2, *group_options
        )
    )
    results.extend(
        _run_group(
            repo,
            by_kind["desktop-node"],
            "desktop-node",
            args.platform,
            2,
            *group_options,
        )
    )
    results.extend(
        _run_group(repo, by_kind["node"], "node", args.platform, 2, *group_options)
    )
    executed = {item["path"] for item in results}
    for path in paths:
        if path in executed:
            continue
        digest = hashlib.sha256(path.encode()).hexdigest()
        results.append(
            {
                "kind": "reference",
                "name": f"ledger reference {digest}",
                "path": path,
                "reason": "non-executable invariant reference",
            }
        )

    results.sort(key=lambda item: item["path"])
    failed = [item for item in results if item.get("result") == "failed"]
    serializable = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in results
    ]
    args.output.write_text(
        json.dumps(serializable, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for item in failed:
        print(f"ledger invariant failed: {item['path']}", file=sys.stderr)
        if item["_stdout"]:
            print(item["_stdout"], file=sys.stderr)
        if item["_stderr"]:
            print(item["_stderr"], file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
