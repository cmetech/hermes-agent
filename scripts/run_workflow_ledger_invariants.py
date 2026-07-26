#!/usr/bin/env python3
"""Execute every executable invariant declared by the workflow ledger.

Each Python or JavaScript test file runs in its own subprocess.  Non-executable
fixture and runner references are retained as reference-only records; they
never receive a test result and therefore cannot satisfy an executed invariant.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import yaml


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
        command = [sys.executable, "-m", "pytest", "-q", path]
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


def _execute(repo: Path, path: str, kind: str, platform: str) -> dict[str, Any]:
    command, cwd = _command(repo, path, kind)
    env = os.environ.copy()
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
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    duration_ms = (time.monotonic_ns() - started) // 1_000_000
    digest = hashlib.sha256(path.encode()).hexdigest()
    return {
        "kind": "executed",
        "name": f"ledger invariant {digest}",
        "path": path,
        "result": "passed" if completed.returncode == 0 else "failed",
        "duration_ms": duration_ms,
        "platform": platform,
        "_stdout": completed.stdout,
        "_stderr": completed.stderr,
    }


def _run_group(
    repo: Path,
    paths: list[str],
    kind: str,
    platform: str,
    workers: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not paths:
        return results
    with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as executor:
        futures = {
            executor.submit(_execute, repo, path, kind, platform): path
            for path in paths
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()

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
    results.extend(_run_group(repo, by_kind["python"], "python", args.platform, 8))
    results.extend(_run_group(repo, by_kind["desktop"], "desktop", args.platform, 2))
    results.extend(
        _run_group(
            repo,
            by_kind["desktop-node"],
            "desktop-node",
            args.platform,
            2,
        )
    )
    results.extend(_run_group(repo, by_kind["node"], "node", args.platform, 2))
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
