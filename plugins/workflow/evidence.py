"""Typed, bounded workflow evidence queries."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
import re
import stat
from pathlib import Path
from typing import Mapping

from plugins.workflow.sanitize import sanitize_evidence_bytes, sanitize_projection


EVIDENCE_KINDS = frozenset({
    "timeline",
    "interactions",
    "attempts",
    "logs",
    "outputs",
    "artifacts",
    "recovery",
    "coordinator",
    "cleanup",
    "notifications",
})

_LOG_READ_LIMIT = 256 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_PUBLICATION_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PUBLICATION_CONTENT_NAMES = {
    "application/json": "content.json",
    "text/markdown; charset=utf-8": "content.md",
}
_PUBLICATION_CONTENT_LIMIT = 500_000
_PUBLICATION_METADATA_TEXT_LIMIT = 1_024


class _UnsafeEvidencePath(Exception):
    """An evidence candidate could not be proven to be a contained regular file."""


class PublicationNotFoundError(LookupError):
    """No authorized publication matches the opaque identifier."""


class PublicationIntegrityError(RuntimeError):
    """A publication descriptor or content file failed verification."""


@dataclass(frozen=True, slots=True)
class VerifiedPublication:
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str
    node_id: str
    attempt_id: str
    schema_fingerprint: str | None
    produced_at: str
    session_id: str | None
    content: bytes


def _identity(file_stat: os.stat_result) -> tuple[int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
    )


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _absolute_contained_path(root: Path, candidate: Path) -> tuple[Path, Path]:
    root_absolute = Path(os.path.abspath(root))
    candidate_absolute = Path(os.path.abspath(candidate))
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise _UnsafeEvidencePath from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _UnsafeEvidencePath

    try:
        resolved_root = root_absolute.resolve(strict=True)
        candidate_absolute.resolve(strict=False).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _UnsafeEvidencePath from exc
    return root_absolute, relative


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
    return flags


def _read_descriptor(file_descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(file_descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_posix_contained_file(
    root: Path, relative: Path, limit: int
) -> tuple[bytes, int]:
    descriptors: list[int] = []
    try:
        directory_descriptor = os.open(root, _open_flags(directory=True))
        descriptors.append(directory_descriptor)
        if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
            raise _UnsafeEvidencePath

        for component in relative.parts[:-1]:
            directory_descriptor = os.open(
                component,
                _open_flags(directory=True),
                dir_fd=directory_descriptor,
            )
            descriptors.append(directory_descriptor)
            directory_stat = os.fstat(directory_descriptor)
            if not stat.S_ISDIR(directory_stat.st_mode) or _is_reparse_point(
                directory_stat
            ):
                raise _UnsafeEvidencePath

        filename = relative.parts[-1]
        before = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or _is_reparse_point(before):
            raise _UnsafeEvidencePath

        file_descriptor = os.open(
            filename,
            _open_flags(),
            dir_fd=directory_descriptor,
        )
        descriptors.append(file_descriptor)
        opened = os.fstat(file_descriptor)
        after_open = os.stat(
            filename, dir_fd=directory_descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after_open)
        ):
            raise _UnsafeEvidencePath

        data = _read_descriptor(file_descriptor, limit)
        after_read = os.stat(
            filename, dir_fd=directory_descriptor, follow_symlinks=False
        )
        if _identity(opened) != _identity(after_read):
            raise _UnsafeEvidencePath
        return data, opened.st_size
    except (OSError, ValueError) as exc:
        raise _UnsafeEvidencePath from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _reject_reparse_components(root: Path, relative: Path) -> os.stat_result:
    current = root
    root_stat = current.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(root_stat):
        raise _UnsafeEvidencePath
    for index, component in enumerate(relative.parts):
        current /= component
        component_stat = current.lstat()
        if stat.S_ISLNK(component_stat.st_mode) or _is_reparse_point(component_stat):
            raise _UnsafeEvidencePath
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(
            component_stat.st_mode
        ):
            raise _UnsafeEvidencePath
    return component_stat


def _read_fallback_contained_file(
    root: Path, relative: Path, limit: int
) -> tuple[bytes, int]:
    candidate = root / relative
    file_descriptor: int | None = None
    try:
        before = _reject_reparse_components(root, relative)
        if not stat.S_ISREG(before.st_mode):
            raise _UnsafeEvidencePath
        file_descriptor = os.open(candidate, _open_flags())
        opened = os.fstat(file_descriptor)
        after_open = _reject_reparse_components(root, relative)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after_open)
        ):
            raise _UnsafeEvidencePath
        data = _read_descriptor(file_descriptor, limit)
        after_read = _reject_reparse_components(root, relative)
        if _identity(opened) != _identity(after_read):
            raise _UnsafeEvidencePath
        return data, opened.st_size
    except (OSError, ValueError) as exc:
        raise _UnsafeEvidencePath from exc
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass


def _read_contained_regular_file(
    root: Path, candidate: Path, limit: int
) -> tuple[bytes, int]:
    """Read a bounded regular file without following untrusted path components."""
    if limit < 0:
        raise ValueError("evidence read limit must be non-negative")
    root_absolute, relative = _absolute_contained_path(root, candidate)
    descriptor_relative_supported = (
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )
    if descriptor_relative_supported:
        return _read_posix_contained_file(root_absolute, relative, limit)
    return _read_fallback_contained_file(root_absolute, relative, limit)


class EvidenceReader:
    def __init__(self, store) -> None:
        self.store = store

    def query(
        self,
        run_id: str,
        *,
        kind: str,
        after: int = 0,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        if kind not in EVIDENCE_KINDS:
            raise ValueError("unsupported evidence kind")
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid evidence cursor or limit")
        run = self.store.get_run_status(run_id, operator_scope=operator_scope)
        if kind == "timeline":
            page = self.store.events_after(
                run_id,
                after=after,
                limit=limit,
                operator_scope=operator_scope,
            )
            return {
                "schema_version": 1,
                "kind": kind,
                "items": sanitize_projection(page["events"]),
                "next_cursor": page["next_cursor"],
                "truncated": len(page["events"]) == limit,
            }
        warnings: list[str] = []
        if kind == "logs":
            directory = self.store.run_directory(
                run_id, operator_scope=operator_scope
            )
            items, warnings = self._logs(directory)
        else:
            items = self._items(run_id, run, kind=kind, operator_scope=operator_scope)
        page = items[after : after + limit]
        response = {
            "schema_version": 1,
            "kind": kind,
            "items": sanitize_projection(page),
            "next_cursor": after + len(page),
            "truncated": after + len(page) < len(items),
        }
        if warnings:
            response["warnings"] = warnings
        return response

    def lookup_publication(
        self,
        run_id: str,
        publication_id: str,
        *,
        operator_scope: str | None = None,
    ) -> VerifiedPublication:
        # Authorization and checked journal/projection recovery precede even
        # opaque-ID validation so an attacker cannot probe another scope.
        run = self.store.get_run_status(
            run_id,
            operator_scope=operator_scope,
        )
        if (
            not isinstance(publication_id, str)
            or _PUBLICATION_ID.fullmatch(publication_id) is None
        ):
            raise PublicationNotFoundError(publication_id)
        artifacts = run.get("artifacts")
        matches = (
            [
                artifact
                for artifact in artifacts
                if isinstance(artifact, Mapping)
                and artifact.get("publication_id") == publication_id
            ]
            if isinstance(artifacts, list)
            else []
        )
        if not matches:
            raise PublicationNotFoundError(publication_id)
        if len(matches) != 1:
            raise PublicationIntegrityError("publication descriptor is ambiguous")
        descriptor = matches[0]
        media_type = descriptor.get("media_type")
        content_name = descriptor.get("content_name")
        expected_content_name = (
            _PUBLICATION_CONTENT_NAMES.get(media_type)
            if isinstance(media_type, str)
            else None
        )
        size_bytes = descriptor.get("size_bytes")
        content_sha256 = descriptor.get("sha256")
        output_type = descriptor.get("output_type")
        node_id = descriptor.get("node_id")
        attempt_id = descriptor.get("attempt_id")
        schema_fingerprint = descriptor.get("schema_fingerprint")
        produced_at = descriptor.get("produced_at")
        session_id = descriptor.get("session_id")
        bounded_text = (output_type, node_id, attempt_id, produced_at)
        if (
            expected_content_name is None
            or content_name != expected_content_name
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= size_bytes <= _PUBLICATION_CONTENT_LIMIT
            or not isinstance(content_sha256, str)
            or _SHA256.fullmatch(content_sha256) is None
            or any(
                not isinstance(value, str)
                or not value
                or len(value) > _PUBLICATION_METADATA_TEXT_LIMIT
                for value in bounded_text
            )
            or (
                schema_fingerprint is not None
                and (
                    not isinstance(schema_fingerprint, str)
                    or _SHA256.fullmatch(schema_fingerprint) is None
                )
            )
            or (
                session_id is not None
                and (
                    not isinstance(session_id, str)
                    or len(session_id) > _PUBLICATION_METADATA_TEXT_LIMIT
                )
            )
        ):
            raise PublicationIntegrityError("publication descriptor is invalid")
        directory = self.store.run_directory(
            run_id,
            operator_scope=operator_scope,
        )
        candidate = (
            directory
            / "publications"
            / publication_id
            / expected_content_name
        )
        try:
            content, observed_size = _read_contained_regular_file(
                directory,
                candidate,
                size_bytes + 1,
            )
        except _UnsafeEvidencePath as exc:
            raise PublicationIntegrityError(
                "publication content path is unsafe"
            ) from exc
        if observed_size != size_bytes or len(content) != size_bytes:
            raise PublicationIntegrityError("publication content size changed")
        observed_digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(observed_digest, content_sha256):
            raise PublicationIntegrityError("publication content digest changed")
        return VerifiedPublication(
            publication_id=publication_id,
            content_name=expected_content_name,
            output_type=output_type,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=content_sha256,
            node_id=node_id,
            attempt_id=attempt_id,
            schema_fingerprint=schema_fingerprint,
            produced_at=produced_at,
            session_id=session_id,
            content=content,
        )

    def _items(self, run_id, run, *, kind, operator_scope):
        nodes = run.get("nodes", {})
        node_items = nodes.items() if isinstance(nodes, Mapping) else ()
        if kind == "interactions":
            historical = [
                event
                for event in self.store.tail_events(
                    run_id, limit=200, operator_scope=operator_scope
                )
                if str(event.get("event_type", "")).startswith("interaction_")
                or event.get("event_type") == "loop_input_provided"
            ]
            pending_items = [
                {"node_id": node_id, **pending}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                and isinstance((pending := node.get("pending_interaction")), Mapping)
            ]
            return [*historical, *pending_items]
        if kind == "attempts":
            return [
                {"node_id": node_id, **attempt}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                for attempt in node.get("attempts", [])
                if isinstance(attempt, Mapping)
            ]
        if kind == "outputs":
            return [
                {"node_id": node_id, "output": node["output"]}
                for node_id, node in node_items
                if isinstance(node, Mapping) and node.get("output") is not None
            ]
        if kind == "artifacts":
            return [
                self._artifact_evidence_item(artifact)
                for artifact in run.get("artifacts", [])
                if isinstance(artifact, Mapping)
            ]
        if kind == "recovery":
            return [
                {"node_id": node_id, "recovery": node["recovery"]}
                for node_id, node in node_items
                if isinstance(node, Mapping)
                and isinstance(node.get("recovery"), Mapping)
            ]
        if kind == "coordinator":
            return [
                {
                    "coordinator": run.get("coordinator"),
                    "execution_mode": run.get("execution_mode"),
                    "health": run.get("health"),
                    "blocking_reason": run.get("blocking_reason"),
                    "last_semantic_progress_at": run.get("last_semantic_progress_at"),
                }
            ]
        if kind == "cleanup":
            return list(
                self.store.cleanup_history(run_id, operator_scope=operator_scope)
            )
        if kind == "notifications":
            from plugins.workflow.notifications import NotificationOutbox

            return list(NotificationOutbox(self.store).history(run_id=run_id))
        return []

    @staticmethod
    def _artifact_evidence_item(
        artifact: Mapping[str, object],
    ) -> dict[str, object]:
        if not isinstance(artifact.get("publication_id"), str):
            return dict(artifact)
        return {
            "publication_id": artifact.get("publication_id"),
            "output_type": artifact.get("output_type"),
            "media_type": artifact.get("media_type"),
            "size_bytes": artifact.get("size_bytes"),
            "sha256": artifact.get("sha256"),
            "node_id": artifact.get("node_id"),
            "attempt_id": artifact.get("attempt_id"),
            "schema_fingerprint": artifact.get("schema_fingerprint"),
            "produced_at": artifact.get("produced_at"),
            "session_id": artifact.get("session_id"),
            # load_run() revalidates the checked journal, projection descriptor,
            # bundle containment, canonical filename, size, and digest before
            # EvidenceReader can observe this projection.
            "integrity_status": "verified",
            "recovery_status": "verified",
        }

    @staticmethod
    def _logs(directory: Path) -> tuple[list[dict[str, object]], list[str]]:
        items = []
        warnings: list[str] = []
        remaining = _LOG_READ_LIMIT
        for path in sorted((directory / "nodes").glob("*/*/std*.txt")):
            if remaining <= 0:
                break
            try:
                data, size = _read_contained_regular_file(directory, path, remaining)
            except _UnsafeEvidencePath:
                if "unsafe_evidence_path" not in warnings:
                    warnings.append("unsafe_evidence_path")
                continue
            remaining -= len(data)
            text, truncated = sanitize_evidence_bytes(data)
            items.append({
                "node_id": path.parent.parent.name,
                "attempt_id": path.parent.name,
                "stream": "stderr" if path.name == "stderr.txt" else "stdout",
                "text": text,
                "bytes_returned": len(data),
                "truncated": truncated or size > len(data),
            })
        return items, warnings


__all__ = [
    "EVIDENCE_KINDS",
    "EvidenceReader",
    "PublicationIntegrityError",
    "PublicationNotFoundError",
    "VerifiedPublication",
    "_read_contained_regular_file",
]
