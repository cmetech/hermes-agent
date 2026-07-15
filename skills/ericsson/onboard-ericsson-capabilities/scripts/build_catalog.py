#!/usr/bin/env python3
"""Build or freshness-check the committed Ericsson onboarding catalog."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from catalog_lib import build_catalog, serialize_catalog


CATALOG_PATH = (
    "skills/ericsson/onboard-ericsson-capabilities/references/catalog.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    expected = serialize_catalog(build_catalog(repo))
    target = repo / CATALOG_PATH
    if args.check:
        current = target.read_bytes() if target.is_file() else None
        if current != expected.encode("utf-8"):
            print("catalog is stale")
            return 1
        return 0
    _atomic_write(target, expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
