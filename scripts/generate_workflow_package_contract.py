#!/usr/bin/env python3
"""Write or verify the immutable workflow-package contract artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.workflow.marketplace.contract import (  # noqa: E402
    CONTRACT_PATH,
    VECTOR_PATH,
    render_contract_artifacts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify workflow-package contract artifacts."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            artifact_mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            artifact_mode = 0o644
        os.fchmod(descriptor, artifact_mode)
        temporary_file = os.fdopen(descriptor, "wb")
        descriptor = -1
        with temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _check(path: Path, expected: bytes) -> bool:
    try:
        actual = path.read_bytes()
    except FileNotFoundError:
        print(f"missing generated artifact: {path.relative_to(ROOT)}", file=sys.stderr)
        return False
    if actual != expected:
        print(f"generated artifact is stale: {path.relative_to(ROOT)}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    contract_bytes, vector_bytes = render_contract_artifacts()
    artifacts = (
        (CONTRACT_PATH, contract_bytes),
        (VECTOR_PATH, vector_bytes),
    )
    if args.write:
        for path, content in artifacts:
            _write(path, content)
        return 0
    return 0 if all(_check(path, content) for path, content in artifacts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
