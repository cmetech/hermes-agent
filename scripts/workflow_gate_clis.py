#!/usr/bin/env python3
"""Resolve only the installed, pinned gate CLIs; never use a package runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


CLI_IDENTITIES = (
    ("typescript", "6.0.3", "tsc", "bin/tsc"),
    ("vitest", "4.1.10", "vitest", "vitest.mjs"),
    ("tsx", "4.23.1", "tsx", "dist/cli.mjs"),
)


def regular_file(path: Path) -> Path:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("local CLI identity must be a regular non-symlink file")
    resolved = path.resolve(strict=True)
    if any(character in str(resolved) for character in "\r\n"):
        raise ValueError("local CLI identity contains a line separator")
    return resolved


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate local CLI manifest field")
        result[key] = value
    return result


def local_clis(modules: Path, node: Path) -> tuple[Path, ...]:
    """The caller first bounds the dependency view to approved repository roots."""
    runtime = regular_file(node)
    if not node.is_absolute() or not os.access(runtime, os.X_OK):
        raise ValueError("local Node must be an absolute executable file")
    root = modules.resolve(strict=True)
    result = [runtime]
    for name, version, command, entry in CLI_IDENTITIES:
        package = root / name
        if not stat.S_ISDIR(package.lstat().st_mode):
            raise ValueError("local CLI package must be a non-symlink directory")
        manifest_path = regular_file(package / "package.json")
        executable = regular_file(package / entry)
        # Intermediate links are not an alternate executable identity either.
        parent = (package / entry).parent
        while parent != root:
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError("local CLI path contains a linked directory")
            parent = parent.parent
        if not manifest_path.is_relative_to(package) or not executable.is_relative_to(
            package
        ):
            raise ValueError("local CLI identity escapes its package")
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != name
            or manifest.get("version") != version
        ):
            raise ValueError("local CLI package identity does not match the lockfile")
        declared = manifest.get("bin")
        if isinstance(declared, dict):
            declared = declared.get(command)
        if declared != f"./{entry}" and declared != entry:
            raise ValueError("local CLI executable identity does not match its package")
        result.append(executable)
    return tuple(result)


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("expected dependency root and Node executable")
        paths = local_clis(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError) as error:
        print(f"local gate CLI validation failed: {error}", file=sys.stderr)
        return 1
    print("\n".join(map(str, paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
