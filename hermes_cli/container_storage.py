"""Container detection and read-only mount-persistence evidence.

The mount table is intentionally read on every inspection.  Container mounts
can change while a long-lived gateway process is running, and stale evidence
must never authorize creation of an encryption key on ephemeral storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from hermes_constants import get_hermes_home


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


_MOUNT_ESCAPES = {
    "040": " ",
    "011": "\t",
    "012": "\n",
    "134": "\\",
}
_EPHEMERAL_FILESYSTEMS = frozenset(
    {"overlay", "fuse-overlayfs", "tmpfs", "ramfs", "aufs"}
)
_CGROUP_FILESYSTEMS = frozenset({"cgroup", "cgroup2"})
_CONTAINER_ROOT_FILESYSTEMS = frozenset({"aufs", "fuse-overlayfs", "overlay"})
_CONTAINER_CGROUP_ROOT_MARKERS = (
    "/docker/",
    "/libpod",
    "/lxc/",
    "/kubepods/",
    "/containerd/",
    "/crio",
    "/cri-o/",
)
_PERSISTENCE_ACK_CONFIG_KEY = "security.container_persistence_acknowledged"


def _decode_mount_field(value: str) -> str:
    """Decode exactly the four escape sequences emitted by mountinfo.

    This is deliberately a single pass: a literal ``\\040`` is encoded as
    ``\\134040`` and must decode to a backslash followed by three digits, not
    be interpreted a second time as a space.
    """
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            decoded.append(value[index])
            index += 1
            continue
        code = value[index + 1 : index + 4]
        if len(code) != 3 or code not in _MOUNT_ESCAPES:
            raise ValueError("unsupported mountinfo escape")
        decoded.append(_MOUNT_ESCAPES[code])
        index += 4
    return "".join(decoded)


def _parse_mountinfo_line(line: str) -> _MountInfo:
    fields = line.split()
    if fields.count("-") != 1:
        raise ValueError("invalid mountinfo separator")
    separator = fields.index("-")
    if separator < 6 or len(fields) != separator + 4:
        raise ValueError("incomplete mountinfo record")
    if not fields[0].isdigit() or not fields[1].isdigit():
        raise ValueError("invalid mount identifiers")
    major_minor = fields[2].split(":", 1)
    if len(major_minor) != 2 or not all(part.isdigit() for part in major_minor):
        raise ValueError("invalid mount device")
    root = _decode_mount_field(fields[3])
    mount_point_text = _decode_mount_field(fields[4])
    _decode_mount_field(fields[5])
    for optional_field in fields[6:separator]:
        _decode_mount_field(optional_field)
    fs_type = fields[separator + 1].strip().lower()
    source = _decode_mount_field(fields[separator + 2])
    _decode_mount_field(fields[separator + 3])
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


def _container_runtime_kind(mounts: tuple[_MountInfo, ...] = ()) -> str:
    """Classify runtime markers plus the already-parsed mount evidence."""
    if os.environ.get("HERMES_CONTAINER"):
        return "ambiguous"
    if os.environ.get("HERMES_DESKTOP_CHILD_PID"):
        return "host"
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    if Path("/.dockerenv").exists():
        return "docker"
    if Path("/run/.containerenv").exists():
        return "podman"
    try:
        cgroup = Path("/proc/1/cgroup").read_text(
            encoding="utf-8", errors="replace"
        ).lower()
    except OSError:
        cgroup = ""
    if "kubepods" in cgroup:
        return "kubernetes"
    if "docker" in cgroup:
        return "docker"
    if "podman" in cgroup or "libpod" in cgroup:
        return "podman"
    if any(marker in cgroup for marker in ("containerd", "crio", "cri-o", "/lxc/")):
        return "ambiguous"
    for mount in mounts:
        if (
            mount.mount_point == Path("/")
            and mount.fs_type in _CONTAINER_ROOT_FILESYSTEMS
        ):
            return "ambiguous"
        root = mount.root.lower()
        if (
            mount.fs_type in _CGROUP_FILESYSTEMS
            and (
                mount.mount_point == Path("/sys/fs/cgroup")
                or Path("/sys/fs/cgroup") in mount.mount_point.parents
            )
            and any(marker in root for marker in _CONTAINER_CGROUP_ROOT_MARKERS)
        ):
            return "ambiguous"
    return "host"


def _operator_acknowledges_container_persistence() -> bool:
    """Read the active profile acknowledgement directly, without caching."""
    try:
        raw = yaml.safe_load(
            (get_hermes_home() / "config.yaml").read_text(
                encoding="utf-8", errors="strict"
            )
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        return False
    if not isinstance(raw, dict):
        return False
    security = raw.get("security")
    return (
        isinstance(security, dict)
        and security.get("container_persistence_acknowledged") is True
    )


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
    lines = text.splitlines()
    if any(not line.strip() for line in lines):
        return _unknown("mountinfo is malformed (blank record)")
    try:
        mounts = tuple(_parse_mountinfo_line(line) for line in lines)
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
    deepest_depth = max(len(item.mount_point.parts) for item in enclosing)
    deepest = tuple(
        item for item in enclosing if len(item.mount_point.parts) == deepest_depth
    )
    if len(deepest) != 1:
        return _unknown(f"deepest mount evidence for {target} is ambiguous")
    mount = deepest[0]
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
    runtime_kind = _container_runtime_kind(mounts)
    if runtime_kind in {"kubernetes", "ambiguous"}:
        if _operator_acknowledges_container_persistence():
            return MountPersistence(
                PersistenceState.PERSISTENT,
                mount.mount_point,
                mount.fs_type,
                mount.source,
                f"distinct {kind} mount accepted by explicit operator "
                f"acknowledgement in config.yaml "
                f"({_PERSISTENCE_ACK_CONFIG_KEY}: true)",
            )
        runtime_label = (
            "Kubernetes" if runtime_kind == "kubernetes" else "ambiguous container"
        )
        return MountPersistence(
            PersistenceState.UNKNOWN,
            mount.mount_point,
            mount.fs_type,
            mount.source,
            f"{runtime_label} mountinfo cannot distinguish durable storage "
            f"from runtime-scoped storage; after verifying durable backing, "
            f"set {_PERSISTENCE_ACK_CONFIG_KEY}: true in config.yaml",
        )
    return MountPersistence(
        PersistenceState.PERSISTENT,
        mount.mount_point,
        mount.fs_type,
        mount.source,
        f"distinct {kind} mount is persistent evidence",
    )


def is_container() -> bool:
    """Return whether runtime markers identify a container process."""
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        mountinfo = ""
    mounts: list[_MountInfo] = []
    for line in mountinfo.splitlines():
        try:
            mounts.append(_parse_mountinfo_line(line))
        except ValueError:
            continue
    return _container_runtime_kind(tuple(mounts)) != "host"


__all__ = [
    "MountPersistence",
    "PersistenceState",
    "inspect_mount_persistence",
    "is_container",
]
