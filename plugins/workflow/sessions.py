"""Profile-scoped persistent workflow node-session registry."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from plugins.workflow.locks import workflow_lock

try:  # pragma: no cover - platform import
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


_SHA256_LENGTH = 64
_MIRROR_MAX_CONTENT_BYTES = 500_000
_MIRROR_MAX_DOCUMENT_BYTES = 65_536
_MIRROR_MEDIA = {
    "application/json": "content.json",
    "text/markdown; charset=utf-8": "content.md",
}


class TypedMirrorIntegrityError(RuntimeError):
    pass


class _MalformedMirrorIndex(TypedMirrorIntegrityError):
    pass


@dataclass(frozen=True, slots=True)
class TypedMirrorObligation:
    mirror_id: str
    workflow: str
    node_id: str
    operator_scope: str
    run_id: str
    attempt_id: str
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class TypedMirrorRecord:
    entry_id: str
    mirror_id: str
    workflow: str
    node_id: str
    operator_scope: str
    run_id: str
    attempt_id: str
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _MirrorIndex:
    entry_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class _MirrorLayout:
    home: int
    workflow: int
    root: int
    content: int
    entries: int
    activations: int
    indexes: int

    def descriptors(self) -> tuple[int, ...]:
        return (
            self.home,
            self.workflow,
            self.root,
            self.content,
            self.entries,
            self.activations,
            self.indexes,
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"


def _is_reparse_point(observed: os.stat_result) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and getattr(observed, "st_file_attributes", 0) & marker)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _require_secure_mirror_io() -> None:
    required_dir_fd_functions = {
        os.link,
        os.mkdir,
        os.open,
        os.rename,
        os.stat,
        os.unlink,
    }
    if (
        os.name != "posix"
        or fcntl is None
        or not hasattr(os, "O_CLOEXEC")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not required_dir_fd_functions <= os.supports_dir_fd
        or os.listdir not in os.supports_fd
    ):
        raise TypedMirrorIntegrityError(
            "secure descriptor-relative typed mirror I/O is unavailable"
        )


def _directory_identity(descriptor: int) -> tuple[int, int]:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode) or _is_reparse_point(observed):
        raise TypedMirrorIntegrityError("typed mirror directory is unsafe")
    return observed.st_dev, observed.st_ino


def _open_directory_at(
    parent: int,
    name: str,
    *,
    create: bool,
) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TypedMirrorIntegrityError(
                "typed mirror directory is unavailable"
            ) from exc
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror directory is unsafe") from exc
    try:
        _directory_identity(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_regular_at(directory: int, name: str, *, max_bytes: int) -> bytes:
    try:
        observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror file is unavailable") from exc
    if not stat.S_ISREG(observed.st_mode) or _is_reparse_point(observed):
        raise TypedMirrorIntegrityError("typed mirror file is unsafe")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory)
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror file is unsafe") from exc
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or _is_reparse_point(current)
            or (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino)
            or current.st_size > max_bytes
        ):
            raise TypedMirrorIntegrityError("typed mirror file is unsafe")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise TypedMirrorIntegrityError("typed mirror file exceeds its byte ceiling")
        return data
    finally:
        os.close(descriptor)


def _validate_directory(path: Path) -> None:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror directory is unavailable") from exc
    if not stat.S_ISDIR(observed.st_mode) or _is_reparse_point(observed):
        raise TypedMirrorIntegrityError("typed mirror directory is unsafe")


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("typed mirror write made no progress")
        remaining = remaining[written:]


def _temporary_name(target: str) -> str:
    return f".{target}.{uuid.uuid4().hex}.tmp"


def _write_temporary_at(directory: int, target: str, data: bytes) -> str:
    temporary = _temporary_name(target)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory)
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror write failed") from exc
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, data)
        os.fsync(descriptor)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return temporary


def _write_immutable_at(directory: int, name: str, data: bytes) -> None:
    try:
        existing = _read_regular_at(directory, name, max_bytes=max(len(data), 1))
    except FileNotFoundError:
        pass
    else:
        if not hmac.compare_digest(existing, data):
            raise TypedMirrorIntegrityError("typed mirror immutable identity conflicts")
        return
    temporary = _write_temporary_at(directory, name, data)
    try:
        os.link(
            temporary,
            name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        os.fsync(directory)
    except FileExistsError:
        existing = _read_regular_at(directory, name, max_bytes=max(len(data), 1))
        if not hmac.compare_digest(existing, data):
            raise TypedMirrorIntegrityError("typed mirror immutable identity conflicts")
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror immutable write failed") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
    os.fsync(directory)


def _atomic_bytes_at(directory: int, name: str, data: bytes) -> None:
    temporary = _write_temporary_at(directory, name, data)
    try:
        os.replace(
            temporary,
            name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror atomic write failed") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass


_mirror_process_locks: dict[tuple[int, int], threading.RLock] = {}
_mirror_process_locks_guard = threading.Lock()


def _process_lock(identity: tuple[int, int]) -> threading.RLock:
    with _mirror_process_locks_guard:
        return _mirror_process_locks.setdefault(identity, threading.RLock())


class TypedMirrorStore:
    """Immutable profile-local typed content with one atomic scope pointer."""

    def __init__(
        self,
        hermes_home: str | Path,
        *,
        capacity_check: Callable[[int, int], None] | None = None,
        free_disk_check: Callable[[], None] | None = None,
    ):
        _require_secure_mirror_io()
        self.hermes_home = Path(hermes_home).resolve()
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.workflow_root = self.hermes_home / "workflows"
        self.root = self.workflow_root / "typed-mirrors"
        self.content_root = self.root / "content"
        self.entry_root = self.root / "entries"
        self.activation_root = self.root / "activations"
        self.index_root = self.root / "indexes"
        self.lock_path = self.root / ".scope-index.lock"
        self._capacity_check = capacity_check
        self._free_disk_check = free_disk_check
        with self._open_layout(create=True, verify_expected=False) as layout:
            self._expected_identities = tuple(
                _directory_identity(descriptor)
                for descriptor in layout.descriptors()
            )

    def _validate_paths(self) -> None:
        for directory in (
            self.hermes_home,
            self.workflow_root,
            self.root,
            self.content_root,
            self.entry_root,
            self.activation_root,
            self.index_root,
        ):
            _validate_directory(directory)

    @contextmanager
    def _open_layout(
        self,
        *,
        create: bool = False,
        verify_expected: bool = True,
    ) -> Iterator[_MirrorLayout]:
        descriptors: list[int] = []
        try:
            try:
                home = os.open(self.hermes_home, _directory_flags())
                descriptors.append(home)
                workflow = _open_directory_at(home, "workflows", create=create)
                descriptors.append(workflow)
                root = _open_directory_at(workflow, "typed-mirrors", create=create)
                descriptors.append(root)
                content = _open_directory_at(root, "content", create=create)
                descriptors.append(content)
                entries = _open_directory_at(root, "entries", create=create)
                descriptors.append(entries)
                activations = _open_directory_at(root, "activations", create=create)
                descriptors.append(activations)
                indexes = _open_directory_at(root, "indexes", create=create)
                descriptors.append(indexes)
                layout = _MirrorLayout(
                    home,
                    workflow,
                    root,
                    content,
                    entries,
                    activations,
                    indexes,
                )
                expected_identities = getattr(self, "_expected_identities", None)
                if expected_identities is not None and tuple(
                    _directory_identity(descriptor)
                    for descriptor in layout.descriptors()
                ) != expected_identities and verify_expected:
                    raise TypedMirrorIntegrityError(
                        "typed mirror directory identity changed"
                    )
                self._validate_paths()
            except TypedMirrorIntegrityError:
                raise
            except OSError as exc:
                raise TypedMirrorIntegrityError(
                    "typed mirror directory is unavailable"
                ) from exc
            yield layout
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextmanager
    def _locked_layout(self) -> Iterator[_MirrorLayout]:
        with self._open_layout() as layout:
            identity = _directory_identity(layout.root)
            process_lock = _process_lock(identity)
            with process_lock:
                flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
                try:
                    lock_descriptor = os.open(
                        ".scope-index.lock",
                        flags,
                        0o600,
                        dir_fd=layout.root,
                    )
                except OSError as exc:
                    raise TypedMirrorIntegrityError(
                        "typed mirror lock is unsafe"
                    ) from exc
                try:
                    observed = os.fstat(lock_descriptor)
                    if (
                        not stat.S_ISREG(observed.st_mode)
                        or observed.st_nlink != 1
                        or _is_reparse_point(observed)
                    ):
                        raise TypedMirrorIntegrityError(
                            "typed mirror lock is unsafe"
                        )
                    fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                    yield layout
                finally:
                    try:
                        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(lock_descriptor)

    @staticmethod
    def _layout_bytes(layout: _MirrorLayout) -> int:
        total = 0
        for descriptor in (
            layout.content,
            layout.entries,
            layout.activations,
            layout.indexes,
        ):
            for name in os.listdir(descriptor):
                try:
                    observed = os.stat(
                        name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(observed.st_mode) and not _is_reparse_point(observed):
                    total += observed.st_size
        return total

    @staticmethod
    def _missing_file_bytes(directory: int, name: str, data: bytes) -> int:
        try:
            existing = _read_regular_at(
                directory,
                name,
                max_bytes=max(len(data), 1),
            )
        except FileNotFoundError:
            return len(data) * 2
        if not hmac.compare_digest(existing, data):
            raise TypedMirrorIntegrityError(
                "typed mirror immutable identity conflicts"
            )
        return 0

    def _reserve_stage(
        self,
        layout: _MirrorLayout,
        obligation: TypedMirrorObligation,
        content: bytes,
        record: TypedMirrorRecord,
        entry_bytes: bytes,
    ) -> None:
        if self._free_disk_check is not None:
            self._free_disk_check()
        if self._capacity_check is None:
            return
        activation = self._activation_bytes(record.entry_id)
        index = self._index_bytes(record.entry_id, generation=1)
        required = sum((
            self._missing_file_bytes(
                layout.content,
                obligation.sha256,
                content,
            ),
            self._missing_file_bytes(
                layout.entries,
                f"{record.entry_id}.json",
                entry_bytes,
            ),
            self._missing_file_bytes(
                layout.activations,
                f"{record.entry_id}.json",
                activation,
            ),
            len(index) * 2,
        ))
        self._capacity_check(self._layout_bytes(layout), required)

    @staticmethod
    def _scope_id(workflow: str, node_id: str, operator_scope: str) -> str:
        return _sha256(
            _canonical_json({
                "workflow": workflow,
                "node_id": node_id,
                "operator_scope": operator_scope,
            })
        )

    @staticmethod
    def _validate_obligation(obligation: TypedMirrorObligation) -> None:
        text = (
            obligation.workflow,
            obligation.node_id,
            obligation.operator_scope,
            obligation.run_id,
            obligation.attempt_id,
            obligation.output_type,
        )
        if any(not isinstance(value, str) or not value or len(value) > 4096 for value in text):
            raise TypedMirrorIntegrityError("typed mirror obligation is invalid")
        if (
            len(obligation.mirror_id) != _SHA256_LENGTH
            or len(obligation.sha256) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in obligation.mirror_id)
            or any(character not in "0123456789abcdef" for character in obligation.sha256)
            or len(obligation.publication_id) != 32
            or any(
                character not in "0123456789abcdef"
                for character in obligation.publication_id
            )
            or obligation.media_type not in _MIRROR_MEDIA
            or obligation.content_name != _MIRROR_MEDIA[obligation.media_type]
            or isinstance(obligation.size_bytes, bool)
            or not isinstance(obligation.size_bytes, int)
            or not 0 <= obligation.size_bytes <= _MIRROR_MAX_CONTENT_BYTES
        ):
            raise TypedMirrorIntegrityError("typed mirror obligation is invalid")

    @staticmethod
    def _entry_document(obligation: TypedMirrorObligation) -> dict[str, object]:
        identity = {
            "schema_version": 1,
            "mirror_id": obligation.mirror_id,
            "workflow": obligation.workflow,
            "node_id": obligation.node_id,
            "operator_scope": obligation.operator_scope,
            "run_id": obligation.run_id,
            "attempt_id": obligation.attempt_id,
            "publication_id": obligation.publication_id,
            "content_name": obligation.content_name,
            "output_type": obligation.output_type,
            "media_type": obligation.media_type,
            "size_bytes": obligation.size_bytes,
            "sha256": obligation.sha256,
        }
        return {**identity, "entry_id": _sha256(_canonical_json(identity))}

    @staticmethod
    def _record(document: dict[str, object]) -> TypedMirrorRecord:
        return TypedMirrorRecord(
            entry_id=str(document["entry_id"]),
            mirror_id=str(document["mirror_id"]),
            workflow=str(document["workflow"]),
            node_id=str(document["node_id"]),
            operator_scope=str(document["operator_scope"]),
            run_id=str(document["run_id"]),
            attempt_id=str(document["attempt_id"]),
            publication_id=str(document["publication_id"]),
            content_name=str(document["content_name"]),
            output_type=str(document["output_type"]),
            media_type=str(document["media_type"]),
            size_bytes=int(document["size_bytes"]),
            sha256=str(document["sha256"]),
        )

    @classmethod
    def _verified_entry_document(
        cls,
        entry_bytes: bytes,
        *,
        expected_entry_id: str | None = None,
    ) -> dict[str, object]:
        try:
            document = json.loads(entry_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TypedMirrorIntegrityError("typed mirror entry is malformed") from exc
        if not isinstance(document, dict):
            raise TypedMirrorIntegrityError("typed mirror entry is malformed")
        entry_id = document.get("entry_id")
        material = dict(document)
        material.pop("entry_id", None)
        if (
            not isinstance(entry_id, str)
            or len(entry_id) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in entry_id)
            or (
                expected_entry_id is not None
                and not hmac.compare_digest(entry_id, expected_entry_id)
            )
            or not hmac.compare_digest(_sha256(_canonical_json(material)), entry_id)
        ):
            raise TypedMirrorIntegrityError("typed mirror entry identity is invalid")
        try:
            obligation = TypedMirrorObligation(
                mirror_id=document["mirror_id"],
                workflow=document["workflow"],
                node_id=document["node_id"],
                operator_scope=document["operator_scope"],
                run_id=document["run_id"],
                attempt_id=document["attempt_id"],
                publication_id=document["publication_id"],
                content_name=document["content_name"],
                output_type=document["output_type"],
                media_type=document["media_type"],
                size_bytes=document["size_bytes"],
                sha256=document["sha256"],
            )
            cls._validate_obligation(obligation)
        except (KeyError, TypeError) as exc:
            raise TypedMirrorIntegrityError("typed mirror entry is malformed") from exc
        if document != cls._entry_document(obligation):
            raise TypedMirrorIntegrityError("typed mirror entry is malformed")
        return document

    def _verified_record(
        self,
        layout: _MirrorLayout,
        entry_id: str,
    ) -> TypedMirrorRecord:
        if (
            not isinstance(entry_id, str)
            or len(entry_id) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in entry_id)
        ):
            raise TypedMirrorIntegrityError("typed mirror entry identity is invalid")
        entry_bytes = _read_regular_at(
            layout.entries,
            f"{entry_id}.json",
            max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
        )
        document = self._verified_entry_document(
            entry_bytes,
            expected_entry_id=entry_id,
        )
        record = self._record(document)
        content = _read_regular_at(
            layout.content,
            record.sha256,
            max_bytes=_MIRROR_MAX_CONTENT_BYTES,
        )
        if (
            len(content) != record.size_bytes
            or not hmac.compare_digest(_sha256(content), record.sha256)
        ):
            raise TypedMirrorIntegrityError("typed mirror content identity is invalid")
        return record

    @staticmethod
    def _activation_bytes(entry_id: str) -> bytes:
        return _canonical_json({"entry_id": entry_id, "schema_version": 1})

    @staticmethod
    def _index_bytes(entry_id: str, *, generation: int) -> bytes:
        return _canonical_json({
            "schema_version": 1,
            "generation": generation,
            "entry_id": entry_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    def _is_activated(self, layout: _MirrorLayout, entry_id: str) -> bool:
        expected = self._activation_bytes(entry_id)
        try:
            observed = _read_regular_at(
                layout.activations,
                f"{entry_id}.json",
                max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
            )
        except (FileNotFoundError, TypedMirrorIntegrityError):
            return False
        return hmac.compare_digest(observed, expected)

    @staticmethod
    def _parse_index(data: bytes) -> _MirrorIndex:
        try:
            index = json.loads(data)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _MalformedMirrorIndex("typed mirror index is malformed") from exc
        if (
            not isinstance(index, dict)
            or set(index)
            != {"schema_version", "generation", "entry_id", "updated_at"}
            or index.get("schema_version") != 1
            or isinstance(index.get("generation"), bool)
            or not isinstance(index.get("generation"), int)
            or index["generation"] < 1
            or not isinstance(index.get("updated_at"), str)
        ):
            raise _MalformedMirrorIndex("typed mirror index is malformed")
        try:
            updated_at = datetime.fromisoformat(index["updated_at"])
        except ValueError as exc:
            raise _MalformedMirrorIndex("typed mirror index is malformed") from exc
        entry_id = index.get("entry_id")
        if (
            updated_at.tzinfo is None
            or updated_at.utcoffset() is None
            or not isinstance(entry_id, str)
            or len(entry_id) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in entry_id)
        ):
            raise _MalformedMirrorIndex("typed mirror index is malformed")
        return _MirrorIndex(entry_id, int(index["generation"]))

    def _read_index(
        self,
        layout: _MirrorLayout,
        name: str,
    ) -> _MirrorIndex | None:
        try:
            data = _read_regular_at(
                layout.indexes,
                name,
                max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
            )
        except FileNotFoundError:
            return None
        return self._parse_index(data)

    def stage(
        self,
        obligation: TypedMirrorObligation,
        content: bytes,
    ) -> TypedMirrorRecord:
        """Durably stage immutable mirror data without making it visible."""
        self._validate_obligation(obligation)
        if (
            len(content) != obligation.size_bytes
            or not hmac.compare_digest(_sha256(content), obligation.sha256)
        ):
            raise TypedMirrorIntegrityError("typed mirror content digest does not match")
        entry = self._entry_document(obligation)
        entry_bytes = _canonical_json(entry)
        if len(entry_bytes) > _MIRROR_MAX_DOCUMENT_BYTES:
            raise TypedMirrorIntegrityError("typed mirror entry exceeds its byte ceiling")
        record = self._record(entry)
        with self._locked_layout() as layout:
            self._reserve_stage(
                layout,
                obligation,
                content,
                record,
                entry_bytes,
            )
            _write_immutable_at(layout.content, obligation.sha256, content)
            _write_immutable_at(
                layout.entries,
                f"{record.entry_id}.json",
                entry_bytes,
            )
        return record

    def point(
        self,
        record: TypedMirrorRecord,
        *,
        replace_current: bool = True,
    ) -> bool:
        """Atomically point the scope index at staged, still-invisible data."""
        index_name = (
            self._scope_id(record.workflow, record.node_id, record.operator_scope)
            + ".json"
        )
        with self._locked_layout() as layout:
            verified = self._verified_record(layout, record.entry_id)
            if verified != record:
                raise TypedMirrorIntegrityError("typed mirror staged record conflicts")
            try:
                current = self._read_index(layout, index_name)
            except _MalformedMirrorIndex:
                current = None
            if current is not None and current.entry_id == record.entry_id:
                current_record = self._verified_record(layout, record.entry_id)
                if current_record == record:
                    return True
            if not replace_current:
                if current is not None:
                    try:
                        self._verified_record(layout, current.entry_id)
                    except TypedMirrorIntegrityError:
                        pass
                    else:
                        if self._is_activated(layout, current.entry_id):
                            return False
            generation = current.generation if current is not None else 0
            _atomic_bytes_at(
                layout.indexes,
                index_name,
                self._index_bytes(record.entry_id, generation=generation + 1),
            )
        return True

    def verify(self, record: TypedMirrorRecord) -> TypedMirrorRecord:
        """Expose one immutable entry after its completion journal is durable."""
        with self._locked_layout() as layout:
            verified = self._verified_record(layout, record.entry_id)
            if verified != record:
                raise TypedMirrorIntegrityError("typed mirror staged record conflicts")
            _write_immutable_at(
                layout.activations,
                f"{record.entry_id}.json",
                self._activation_bytes(record.entry_id),
            )
        return record

    def activate(
        self,
        record: TypedMirrorRecord,
        *,
        replace_current: bool = True,
    ) -> TypedMirrorRecord:
        """Point and verify a staged entry for callers without a journal."""
        if not self.point(record, replace_current=replace_current):
            return record
        return self.verify(record)

    def complete(
        self,
        obligation: TypedMirrorObligation,
        content: bytes,
    ) -> TypedMirrorRecord:
        """Stage and activate a mirror for callers without a journal boundary."""
        return self.activate(self.stage(obligation, content))

    def invalidate(self, obligation: TypedMirrorObligation) -> None:
        """Remove only this obligation's current pointer; retain immutable history."""
        index_name = (
            self._scope_id(
                obligation.workflow,
                obligation.node_id,
                obligation.operator_scope,
            )
            + ".json"
        )
        with self._locked_layout() as layout:
            try:
                current = self._read_index(layout, index_name)
            except _MalformedMirrorIndex:
                current = None
            if current is None:
                return
            record = self._verified_record(layout, current.entry_id)
            if record.mirror_id != obligation.mirror_id:
                return
            os.unlink(index_name, dir_fd=layout.indexes)
            os.fsync(layout.indexes)

    def get(
        self,
        workflow: str,
        node_id: str,
        operator_scope: str,
    ) -> TypedMirrorRecord | None:
        index_name = self._scope_id(workflow, node_id, operator_scope) + ".json"
        try:
            with self._open_layout() as layout:
                current = self._read_index(layout, index_name)
                if current is None or not self._is_activated(
                    layout,
                    current.entry_id,
                ):
                    return None
                record = self._verified_record(layout, current.entry_id)
                if (
                    record.workflow != workflow
                    or record.node_id != node_id
                    or record.operator_scope != operator_scope
                ):
                    return None
                return record
        except (
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
            TypedMirrorIntegrityError,
        ):
            return None

    def list_history(
        self,
        workflow: str,
        node_id: str,
        operator_scope: str,
    ) -> tuple[TypedMirrorRecord, ...]:
        records: list[TypedMirrorRecord] = []
        try:
            with self._open_layout() as layout:
                for name in sorted(os.listdir(layout.entries)):
                    if not name.endswith(".json"):
                        continue
                    try:
                        entry_id = name.removesuffix(".json")
                        if not self._is_activated(layout, entry_id):
                            continue
                        record = self._verified_record(layout, entry_id)
                    except TypedMirrorIntegrityError:
                        continue
                    if (
                        record.workflow == workflow
                        and record.node_id == node_id
                        and record.operator_scope == operator_scope
                    ):
                        records.append(record)
        except TypedMirrorIntegrityError:
            return ()
        return tuple(records)


@dataclass(frozen=True)
class NodeSessionKey:
    workflow: str
    node_id: str
    scope: str
    provider: str
    profile: str


@dataclass(frozen=True)
class NodeSessionRecord:
    key: NodeSessionKey
    session_id: str
    cache_fingerprint: str
    generation: int
    updated_at: str


class NodeSessionRegistry:
    """Generation-CAS registry; profiles never share its database."""

    def __init__(self, hermes_home: str | Path):
        self.root = Path(hermes_home).resolve() / "workflows"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "node-sessions.sqlite3"
        self.lock_path = self.root / ".node-sessions.lock"
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS node_sessions (
                    workflow TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    cache_fingerprint TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workflow, node_id, scope, provider, profile)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _values(key: NodeSessionKey) -> tuple[str, ...]:
        return (key.workflow, key.node_id, key.scope, key.provider, key.profile)

    def get(self, key: NodeSessionKey) -> NodeSessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM node_sessions WHERE workflow=? AND node_id=? "
                "AND scope=? AND provider=? AND profile=?",
                self._values(key),
            ).fetchone()
        if row is None:
            return None
        return NodeSessionRecord(
            key=key,
            session_id=row["session_id"],
            cache_fingerprint=row["cache_fingerprint"],
            generation=row["generation"],
            updated_at=row["updated_at"],
        )

    def compare_and_set(
        self,
        key: NodeSessionKey,
        expected_generation: int,
        session_id: str,
        cache_fingerprint: str,
    ) -> bool:
        if expected_generation < 0:
            raise ValueError("expected_generation must be non-negative")
        if not session_id or not cache_fingerprint:
            raise ValueError("session_id and cache_fingerprint must be non-empty")
        now = datetime.now(timezone.utc).isoformat()
        with workflow_lock(self.lock_path):
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT generation FROM node_sessions WHERE workflow=? AND "
                    "node_id=? AND scope=? AND provider=? AND profile=?",
                    self._values(key),
                ).fetchone()
                generation = int(row["generation"]) if row is not None else 0
                if generation != expected_generation:
                    connection.rollback()
                    return False
                if row is None:
                    connection.execute(
                        "INSERT INTO node_sessions VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            *self._values(key),
                            session_id,
                            cache_fingerprint,
                            1,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE node_sessions SET session_id=?, cache_fingerprint=?, "
                        "generation=?, updated_at=? WHERE workflow=? AND node_id=? "
                        "AND scope=? AND provider=? AND profile=?",
                        (
                            session_id,
                            cache_fingerprint,
                            generation + 1,
                            now,
                            *self._values(key),
                        ),
                    )
                connection.commit()
                return True

    def reset(
        self, workflow: str, *, scope: str | None = None, node_id: str | None = None
    ) -> int:
        clauses = ["workflow=?"]
        values: list[str] = [workflow]
        if scope is not None:
            clauses.append("scope=?")
            values.append(scope)
        if node_id is not None:
            clauses.append("node_id=?")
            values.append(node_id)
        with workflow_lock(self.lock_path):
            with self._connect() as connection:
                cursor = connection.execute(
                    f"DELETE FROM node_sessions WHERE {' AND '.join(clauses)}", values
                )
                return cursor.rowcount

    def get_mirror(self, key: NodeSessionKey) -> TypedMirrorRecord | None:
        return TypedMirrorStore(self.root.parent).get(
            key.workflow,
            key.node_id,
            key.scope,
        )

    def list_mirror_history(
        self, key: NodeSessionKey
    ) -> tuple[TypedMirrorRecord, ...]:
        return TypedMirrorStore(self.root.parent).list_history(
            key.workflow,
            key.node_id,
            key.scope,
        )


__all__ = [
    "NodeSessionKey",
    "NodeSessionRecord",
    "NodeSessionRegistry",
    "TypedMirrorIntegrityError",
    "TypedMirrorObligation",
    "TypedMirrorRecord",
    "TypedMirrorStore",
]
