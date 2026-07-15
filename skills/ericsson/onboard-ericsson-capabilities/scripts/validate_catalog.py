#!/usr/bin/env python3
"""Validate onboarding entries against packaged and documented capabilities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from catalog_lib import CatalogError, load_entries, validate_repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    return parser.parse_args()


def main() -> int:
    repo = parse_args().repo.resolve()
    try:
        entries = load_entries(repo)
        problems = validate_repository(repo, entries)
    except CatalogError as exc:
        problems = [str(exc)]
    problems = sorted(set(problems))
    print(json.dumps({"ok": not problems, "problems": problems}, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
