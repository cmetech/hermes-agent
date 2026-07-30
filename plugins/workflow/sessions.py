"""Profile-scoped persistent workflow node-session registry."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from plugins.workflow.locks import workflow_lock


_SHA256_LENGTH = 64
_MIRROR_MAX_CONTENT_BYTES = 500_000
_MIRROR_MAX_DOCUMENT_BYTES = 65_536
_MIRROR_MEDIA = {
    "application/json": "content.json",
    "text/markdown; charset=utf-8": "content.md",
}


class TypedMirrorIntegrityError(RuntimeError):
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: str | Path, target: str | Path) -> None:
    source_path = Path(source)
    target_path = Path(target)
    os.replace(source_path, target_path)
    _fsync_directory(target_path.parent)


def _atomic_bytes(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _durable_replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _is_reparse_point(observed: os.stat_result) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and getattr(observed, "st_file_attributes", 0) & marker)


def _read_regular(path: Path, *, max_bytes: int) -> bytes:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise TypedMirrorIntegrityError("typed mirror file is unavailable") from exc
    if not stat.S_ISREG(observed.st_mode) or _is_reparse_point(observed):
        raise TypedMirrorIntegrityError("typed mirror file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
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
    observed = path.lstat()
    if not stat.S_ISDIR(observed.st_mode) or _is_reparse_point(observed):
        raise TypedMirrorIntegrityError("typed mirror directory is unsafe")


class TypedMirrorStore:
    """Immutable profile-local typed content with one atomic scope pointer."""

    def __init__(self, hermes_home: str | Path):
        workflow_root = Path(hermes_home).resolve() / "workflows"
        self.root = workflow_root / "typed-mirrors"
        self.content_root = self.root / "content"
        self.entry_root = self.root / "entries"
        self.activation_root = self.root / "activations"
        self.index_root = self.root / "indexes"
        self.lock_path = self.root / ".scope-index.lock"
        for directory in (
            workflow_root,
            self.root,
            self.content_root,
            self.entry_root,
            self.activation_root,
            self.index_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            _validate_directory(directory)

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

    def _verified_record(self, entry_id: str) -> TypedMirrorRecord:
        if (
            not isinstance(entry_id, str)
            or len(entry_id) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in entry_id)
        ):
            raise TypedMirrorIntegrityError("typed mirror entry identity is invalid")
        entry_bytes = _read_regular(
            self.entry_root / f"{entry_id}.json",
            max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
        )
        document = self._verified_entry_document(
            entry_bytes,
            expected_entry_id=entry_id,
        )
        record = self._record(document)
        content = _read_regular(
            self.content_root / record.sha256,
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

    def _is_activated(self, entry_id: str) -> bool:
        expected = self._activation_bytes(entry_id)
        try:
            observed = _read_regular(
                self.activation_root / f"{entry_id}.json",
                max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
            )
        except TypedMirrorIntegrityError:
            return False
        return hmac.compare_digest(observed, expected)

    def _read_index_entry_id(self, index_path: Path) -> str | None:
        try:
            index = json.loads(
                _read_regular(index_path, max_bytes=_MIRROR_MAX_DOCUMENT_BYTES)
            )
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
                return None
            updated_at = datetime.fromisoformat(index["updated_at"])
            if updated_at.tzinfo is None:
                return None
            entry_id = index.get("entry_id")
            if (
                not isinstance(entry_id, str)
                or len(entry_id) != _SHA256_LENGTH
                or any(character not in "0123456789abcdef" for character in entry_id)
            ):
                return None
            return entry_id
        except (
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
            TypedMirrorIntegrityError,
        ):
            return None

    @staticmethod
    def _write_immutable(path: Path, data: bytes) -> None:
        try:
            existing = _read_regular(path, max_bytes=max(len(data), 1))
        except TypedMirrorIntegrityError:
            if path.exists() or path.is_symlink():
                raise
        else:
            if not hmac.compare_digest(existing, data):
                raise TypedMirrorIntegrityError("typed mirror immutable identity conflicts")
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".immutable-{path.name}.",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path, follow_symlinks=False)
            _fsync_directory(path.parent)
        except FileExistsError:
            existing = _read_regular(path, max_bytes=max(len(data), 1))
            if not hmac.compare_digest(existing, data):
                raise TypedMirrorIntegrityError(
                    "typed mirror immutable identity conflicts"
                )
        except OSError as exc:
            raise TypedMirrorIntegrityError(
                "typed mirror immutable write failed"
            ) from exc
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        _fsync_directory(path.parent)

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
        with workflow_lock(self.lock_path):
            self._write_immutable(self.content_root / obligation.sha256, content)
            self._write_immutable(self.entry_root / f"{record.entry_id}.json", entry_bytes)
        return record

    def point(
        self,
        record: TypedMirrorRecord,
        *,
        replace_current: bool = True,
    ) -> bool:
        """Atomically point the scope index at staged, still-invisible data."""
        index_path = self.index_root / (
            self._scope_id(record.workflow, record.node_id, record.operator_scope)
            + ".json"
        )
        with workflow_lock(self.lock_path):
            verified = self._verified_record(record.entry_id)
            if verified != record:
                raise TypedMirrorIntegrityError("typed mirror staged record conflicts")
            generation = 0
            try:
                index_path.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise TypedMirrorIntegrityError(
                    "typed mirror index is unavailable"
                ) from exc
            else:
                try:
                    current = json.loads(
                        _read_regular(
                            index_path,
                            max_bytes=_MIRROR_MAX_DOCUMENT_BYTES,
                        )
                    )
                    if isinstance(current, dict):
                        if current.get("entry_id") == record.entry_id:
                            current_record = self._verified_record(record.entry_id)
                            if current_record == record:
                                return True
                        if isinstance(
                            current.get("generation"), int
                        ) and not isinstance(current.get("generation"), bool):
                            generation = int(current["generation"])
                except (
                    TypeError,
                    UnicodeDecodeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    pass
            if not replace_current:
                current_id = self._read_index_entry_id(index_path)
                if current_id is not None:
                    try:
                        self._verified_record(current_id)
                    except TypedMirrorIntegrityError:
                        pass
                    else:
                        return False
            _atomic_bytes(
                index_path,
                _canonical_json({
                    "schema_version": 1,
                    "generation": generation + 1,
                    "entry_id": record.entry_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
            )
        return True

    def verify(self, record: TypedMirrorRecord) -> TypedMirrorRecord:
        """Expose one immutable entry after its completion journal is durable."""
        with workflow_lock(self.lock_path):
            verified = self._verified_record(record.entry_id)
            if verified != record:
                raise TypedMirrorIntegrityError("typed mirror staged record conflicts")
            self._write_immutable(
                self.activation_root / f"{record.entry_id}.json",
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

    def get(
        self,
        workflow: str,
        node_id: str,
        operator_scope: str,
    ) -> TypedMirrorRecord | None:
        index_path = self.index_root / (
            self._scope_id(workflow, node_id, operator_scope) + ".json"
        )
        try:
            entry_id = self._read_index_entry_id(index_path)
            if entry_id is None or not self._is_activated(entry_id):
                return None
            record = self._verified_record(entry_id)
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
        for path in sorted(self.entry_root.glob("*.json"), key=lambda item: item.name):
            try:
                entry_id = path.stem
                if not self._is_activated(entry_id):
                    continue
                record = self._verified_record(entry_id)
            except TypedMirrorIntegrityError:
                continue
            if (
                record.workflow == workflow
                and record.node_id == node_id
                and record.operator_scope == operator_scope
            ):
                records.append(record)
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
