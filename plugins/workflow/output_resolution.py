"""Immutable executor output identities for Archon workflow consumers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping


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


@dataclass(frozen=True, slots=True)
class ResolvedNodeOutput:
    """One immutable downstream view of a winning node output."""

    canonical_bytes: bytes
    value: object
    text: str
    media_type: str
    sha256: str
    node_id: str
    attempt_id: str
    publication_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value))


def resolve_node_output(
    *,
    run_directory: Path,
    node_id: str,
    attempt_id: str,
    descriptor: Mapping[str, object],
    candidate: PrimaryOutputCandidate | None = None,
    publication_id: str | None = None,
) -> ResolvedNodeOutput:
    """Resolve one descriptor without consulting raw provider response text."""
    relative_path = descriptor.get("relative_path")
    media_type = descriptor.get("media_type")
    size_bytes = descriptor.get("size_bytes")
    digest = descriptor.get("sha256")
    if (
        descriptor.get("node_id") != node_id
        or descriptor.get("attempt_id") != attempt_id
        or not isinstance(relative_path, str)
        or not relative_path
        or not isinstance(media_type, str)
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > 500_000
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ArchonOutputIntegrityError("Archon output descriptor is invalid")
    if candidate is not None and (
        candidate.attempt_relative_path != relative_path
        or candidate.media_type != media_type
        or candidate.size_bytes != size_bytes
        or candidate.sha256 != digest
    ):
        raise ArchonOutputIntegrityError(
            "Archon output candidate and descriptor disagree"
        )
    root = run_directory.resolve()
    path = run_directory / relative_path
    try:
        if path.is_symlink():
            raise ArchonOutputIntegrityError("Archon output path is not regular")
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(root)
        observed = resolved_path.stat()
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise ArchonOutputIntegrityError("Archon output path is not regular")
        canonical_bytes = resolved_path.read_bytes()
    except ArchonOutputIntegrityError:
        raise
    except (OSError, ValueError) as exc:
        raise ArchonOutputIntegrityError("Archon output could not be read") from exc
    if (
        len(canonical_bytes) != size_bytes
        or hashlib.sha256(canonical_bytes).hexdigest() != digest
    ):
        raise ArchonOutputIntegrityError("Archon output digest does not match")
    try:
        text = canonical_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ArchonOutputIntegrityError("Archon output is not UTF-8") from exc
    if candidate is not None and candidate.structured_value is not None:
        value = candidate.structured_value
    else:
        try:
            # Phase 2 retains the legacy JSON-looking text adapter for shell,
            # script, and schemaless outputs. Phase 3 may make media/type
            # interpretation strict without changing the canonical bytes.
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            if media_type == "application/json":
                raise ArchonOutputIntegrityError(
                    "Archon JSON output is invalid"
                ) from exc
            value = text
    return ResolvedNodeOutput(
        canonical_bytes=canonical_bytes,
        value=value,
        text=text,
        media_type=media_type,
        sha256=digest,
        node_id=node_id,
        attempt_id=attempt_id,
        publication_id=publication_id,
    )


def resolve_legacy_output_values(
    projection: Mapping[str, object],
    run_directory: Path,
    *,
    read_text: Callable[[Path], str],
) -> dict[str, object]:
    """Run the frozen Hermes artifact scan and text/JSON parsing behavior."""
    outputs: dict[str, object] = {}
    for artifact in projection.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        relative = str(artifact.get("relative_path", ""))
        if not Path(relative).name.startswith(("output.", "stdout.")):
            continue
        node_id = str(artifact.get("node_id", ""))
        try:
            text = read_text(run_directory / relative)
            try:
                outputs[node_id] = json.loads(text)
            except json.JSONDecodeError:
                outputs[node_id] = text
        except (OSError, UnicodeError, ValueError):
            continue
    return outputs


__all__ = [
    "ArchonOutputIntegrityError",
    "PrimaryOutputCandidate",
    "ResolvedNodeOutput",
    "resolve_legacy_output_values",
    "resolve_node_output",
    "write_archon_output_exclusive",
]
