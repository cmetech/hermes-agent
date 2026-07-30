"""Immutable executor output identities for Archon workflow consumers."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_HAS_DESCRIPTOR_RELATIVE_IO = (
    os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


class ArchonOutputIntegrityError(RuntimeError):
    """An attempt-local Archon output could not be created safely."""


def _safe_component(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{kind}-{digest}"


def _directory_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _open_directory(parent: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    try:
        observed = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if not stat.S_ISDIR(observed.st_mode):
        os.close(descriptor)
        raise ArchonOutputIntegrityError("Archon output directory is not regular")
    return descriptor


def _write_descriptor_relative(
    run_directory: Path,
    node_component: str,
    attempt_component: str,
    filename: str,
    data: bytes,
) -> Path:
    descriptors: list[int] = []
    output_descriptor: int | None = None
    remove_output = False
    try:
        root = os.open(run_directory, _directory_flags())
        descriptors.append(root)
        nodes = _open_directory(root, "nodes", create=True)
        descriptors.append(nodes)
        node = _open_directory(nodes, node_component, create=True)
        descriptors.append(node)
        try:
            os.mkdir(attempt_component, mode=0o700, dir_fd=node)
        except FileExistsError as exc:
            raise ArchonOutputIntegrityError(
                "Archon output attempt already exists"
            ) from exc
        attempt = _open_directory(node, attempt_component, create=False)
        descriptors.append(attempt)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        output_descriptor = os.open(filename, flags, 0o600, dir_fd=attempt)
        remove_output = True
        observed = os.fstat(output_descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise ArchonOutputIntegrityError("Archon output target is not regular")
        view = memoryview(data)
        while view:
            written = os.write(output_descriptor, view)
            if written <= 0:
                raise OSError("short Archon output write")
            view = view[written:]
        os.fsync(output_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        remove_output = False
    except ArchonOutputIntegrityError:
        raise
    except (OSError, ValueError) as exc:
        raise ArchonOutputIntegrityError("Archon output creation failed") from exc
    finally:
        if output_descriptor is not None:
            try:
                os.close(output_descriptor)
            except OSError:
                pass
        if remove_output and descriptors:
            try:
                os.unlink(filename, dir_fd=descriptors[-1])
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return run_directory / "nodes" / node_component / attempt_component / filename


def write_archon_output_exclusive(
    run_directory: Path,
    *,
    node_id: str,
    attempt_id: str,
    filename: str,
    data: bytes,
) -> Path:
    """Create one contained output without following attacker-controlled links."""
    if filename not in {"output.json", "output.md"}:
        raise ArchonOutputIntegrityError("Archon output filename is invalid")
    # Path checks cannot make a later pathname-based create race-free. Hosts
    # without handle-relative creation must fail before touching the tree.
    if not _HAS_DESCRIPTOR_RELATIVE_IO:
        raise ArchonOutputIntegrityError(
            "Secure descriptor-relative Archon output creation is unavailable"
        )
    node_component = _safe_component("node", node_id)
    attempt_component = _safe_component("attempt", attempt_id)
    return _write_descriptor_relative(
        run_directory,
        node_component,
        attempt_component,
        filename,
        data,
    )


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_json(item) for key, item in value.items()
        })
    if isinstance(value, tuple | list):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class PrimaryOutputCandidate:
    """One attempt-local authoritative value, not yet a publication."""

    attempt_relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    structured_value: object | None
    schema_fingerprint: str | None
    canonicalization_version: int
    output_type: str | None

    def __post_init__(self) -> None:
        if self.structured_value is not None:
            object.__setattr__(
                self, "structured_value", _freeze_json(self.structured_value)
            )


__all__ = [
    "ArchonOutputIntegrityError",
    "PrimaryOutputCandidate",
    "write_archon_output_exclusive",
]
