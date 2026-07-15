#!/usr/bin/env python3
"""Render and validate the shipped fictional Jira onboarding demonstration."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = SKILL_DIR / "fixtures/synthetic-jira-tickets.json"
DEFAULT_EXPECTED = SKILL_DIR / "fixtures/expected-jira-summary.md"
PRIORITIES = ("Highest", "High", "Medium", "Low")


def load_fixture(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("fixture must be a JSON object")
    if document.get("schema_version") != 1:
        raise ValueError("fixture schema_version must be 1")
    if document.get("fixture_id") != "SYNTH-JIRA-DIGEST-001":
        raise ValueError("fixture_id must identify the shipped synthetic fixture")
    if document.get("synthetic") is not True or document.get("mode") != "offline":
        raise ValueError("fixture must be explicitly synthetic and offline")
    for field in ("project", "assignee"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ValueError(f"fixture {field} must be a non-empty string")
    tickets = document.get("tickets")
    if not isinstance(tickets, list) or not 1 <= len(tickets) <= 25:
        raise ValueError("fixture tickets must contain 1 to 25 records")
    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            raise ValueError(f"ticket {index} must be an object")
        if set(ticket) != {"key", "summary", "status", "priority"}:
            raise ValueError(f"ticket {index} has unexpected fields")
        if not all(isinstance(ticket[field], str) and ticket[field].strip() for field in ticket):
            raise ValueError(f"ticket {index} fields must be non-empty strings")
        if not ticket["key"].startswith("SYNTH-JIRA-"):
            raise ValueError(f"ticket {index} key must be synthetic")
        if ticket["priority"] not in PRIORITIES:
            raise ValueError(f"ticket {index} priority is unsupported")
    return document


def render(document: dict[str, Any]) -> str:
    tickets = document["tickets"]
    lines = [
        "# Synthetic Jira assigned-ticket summary",
        "",
        f"Fixture: `{document['fixture_id']}` (fictional, offline)",
        "",
        f"Assigned to: **{document['assignee']}**",
        f"Open tickets: **{len(tickets)}**",
        "",
    ]
    for priority in PRIORITIES:
        matching = [ticket for ticket in tickets if ticket["priority"] == priority]
        if not matching:
            continue
        lines.extend([f"## {priority}", ""])
        lines.extend(
            f"- `{ticket['key']}` — {ticket['summary']} ({ticket['status']})"
            for ticket in matching
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def publish_output(path: Path, rendered: str) -> None:
    """Create an output once, without following or replacing a raced path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = rendered.encode("utf-8")
    if os.name == "posix":
        _publish_output_posix(path, encoded)
    else:
        _publish_output_portable(path, encoded)


def _write_complete(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short artifact write")
        view = view[written:]
    os.fsync(descriptor)


def _publish_output_posix(path: Path, content: bytes) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("secure no-follow output creation is unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(path.parent, directory_flags)
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        file_flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path.name, file_flags, 0o644, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("artifact destination is not a regular file")
        identity = (opened.st_dev, opened.st_ino)
        _write_complete(descriptor, content)
        if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != identity:
            raise OSError("artifact identity changed during publication")
        os.close(descriptor)
        descriptor = None
        os.fsync(directory_fd)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        _unlink_owned_output(path.name, directory_fd, identity)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def _unlink_owned_output(
    name: str, directory_fd: int, identity: tuple[int, int] | None
) -> None:
    if identity is None:
        return
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _publish_output_portable(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("artifact destination is not a regular file")
        identity = (opened.st_dev, opened.st_ino)
        _write_complete(descriptor, content)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        try:
            current = path.lstat()
            if identity is not None and stat.S_ISREG(current.st_mode) and (
                current.st_dev,
                current.st_ino,
            ) == identity:
                path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.check and args.output is None:
        raise ValueError("choose --check or --output")
    rendered = render(load_fixture(args.fixture))
    if args.check:
        expected = args.expected.read_text(encoding="utf-8")
        if rendered != expected:
            print(json.dumps({"ok": False, "error": "golden-mismatch"}), file=sys.stderr)
            return 1
    if args.output is not None:
        try:
            publish_output(args.output, rendered)
        except FileExistsError:
            print(json.dumps({"ok": False, "error": "output-exists"}), file=sys.stderr)
            return 1
    print(json.dumps({"ok": True, "fixture": "SYNTH-JIRA-DIGEST-001"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(2) from None
