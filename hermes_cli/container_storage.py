"""Container detection and read-only mount-persistence evidence.

The mount table is intentionally read on every inspection.  Container mounts
can change while a long-lived gateway process is running, and stale evidence
must never authorize creation of an encryption key on ephemeral storage.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PersistenceState(Enum):
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MountPersistence:
    state: PersistenceState
    mount_point: Path | None
    fs_type: str | None
    source: str | None
    reason: str


@dataclass(frozen=True)
class _MountInfo:
    root: str
    mount_point: Path
    fs_type: str
    source: str


_MOUNT_ESCAPE_RE = re.compile(r"\\(040|011|012|134)")
_UNDECODED_OCTAL_RE = re.compile(r"\\[0-7]{3}")
_MOUNT_ESCAPES = {
    "040": " ",
    "011": "\t",
    "012": "\n",
    "134": "\\",
}
_EPHEMERAL_FILESYSTEMS = frozenset({"overlay", "tmpfs", "ramfs", "aufs"})
_CONTAINER_MARKERS = (
    "docker",
    "podman",
    "libpod",
    "/lxc/",
    "kubepods",
    "containerd",
    "crio",
    "cri-o",
)


def _decode_mount_field(value: str) -> str:
    decoded = _MOUNT_ESCAPE_RE.sub(
        lambda match: _MOUNT_ESCAPES[match.group(1)], value
    )
    if _UNDECODED_OCTAL_RE.search(decoded):
        raise ValueError("unsupported mountinfo escape")
    return decoded


def _parse_mountinfo_line(line: str) -> _MountInfo:
    fields = line.split()
    try:
        separator = fields.index("-")
    except ValueError as exc:
        raise ValueError("missing mountinfo separator") from exc
    if separator < 6 or len(fields) < separator + 4:
        raise ValueError("incomplete mountinfo record")
    if not fields[0].isdigit() or not fields[1].isdigit():
        raise ValueError("invalid mount identifiers")
    major_minor = fields[2].split(":", 1)
    if len(major_minor) != 2 or not all(part.isdigit() for part in major_minor):
        raise ValueError("invalid mount device")
    root = _decode_mount_field(fields[3])
    mount_point_text = _decode_mount_field(fields[4])
    fs_type = fields[separator + 1].strip().lower()
    source = _decode_mount_field(fields[separator + 2])
    if not root.startswith("/") or not mount_point_text.startswith("/"):
        raise ValueError("mount paths must be absolute")
    if not fs_type or not source:
        raise ValueError("missing mount filesystem evidence")
    return _MountInfo(
        root=root,
        mount_point=Path(os.path.normpath(mount_point_text)),
        fs_type=fs_type,
        source=source,
    )


def _unknown(reason: str) -> MountPersistence:
    return MountPersistence(PersistenceState.UNKNOWN, None, None, None, reason)


def inspect_mount_persistence(
    path: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> MountPersistence:
    """Return uncached persistence evidence for the deepest enclosing mount.

    A container root mount alone is not evidence that state survives a new
    container.  Only a distinct mount enclosing *path* can be classified as
    persistent; known memory/union filesystems are explicitly ephemeral.
    """
    target = Path(os.path.abspath(os.fspath(path)))
    try:
        text = mountinfo_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        return _unknown(f"mountinfo unavailable ({type(exc).__name__})")
    if not text.strip():
        return _unknown("mountinfo is empty")
    try:
        mounts = tuple(
            _parse_mountinfo_line(line)
            for line in text.splitlines()
            if line.strip()
        )
    except ValueError as exc:
        return _unknown(f"mountinfo is malformed ({exc})")
    if not mounts:
        return _unknown("mountinfo contains no records")

    enclosing = tuple(
        mount
        for mount in mounts
        if mount.mount_point == target or mount.mount_point in target.parents
    )
    if not enclosing:
        return _unknown(f"no mount entry encloses {target}")
    mount = max(
        enclosing,
        key=lambda item: len(item.mount_point.parts),
    )
    if mount.mount_point == Path("/"):
        return MountPersistence(
            PersistenceState.UNKNOWN,
            mount.mount_point,
            mount.fs_type,
            mount.source,
            "container root mount alone is not persistence evidence",
        )
    if mount.fs_type in _EPHEMERAL_FILESYSTEMS:
        return MountPersistence(
            PersistenceState.EPHEMERAL,
            mount.mount_point,
            mount.fs_type,
            mount.source,
            f"distinct {mount.fs_type} mount is ephemeral",
        )
    kind = "bind" if mount.root != "/" else "volume"
    return MountPersistence(
        PersistenceState.PERSISTENT,
        mount.mount_point,
        mount.fs_type,
        mount.source,
        f"distinct {kind} mount is persistent evidence",
    )


def is_container() -> bool:
    """Return whether runtime markers identify a container process."""
    if os.environ.get("HERMES_CONTAINER"):
        return True
    if os.environ.get("HERMES_DESKTOP_CHILD_PID"):
        return False
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    for path in (Path("/proc/1/cgroup"), Path("/proc/self/mountinfo")):
        try:
            evidence = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if any(marker in evidence for marker in _CONTAINER_MARKERS):
            return True
    return False


__all__ = [
    "MountPersistence",
    "PersistenceState",
    "inspect_mount_persistence",
    "is_container",
]
