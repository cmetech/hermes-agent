"""Durable profile-local lifecycle ledger for agent handoffs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from agent.structured_output import canonical_json_bytes
from hermes_cli.sqlite_util import write_txn
from hermes_constants import get_hermes_home

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot, HandoffSpec


_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 16_384
_TERMINAL_PHASES = frozenset({"succeeded", "failed", "cancelled"})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_SEMANTIC_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_KINDS = frozenset({"cancel", "reconcile"})
_ATTEMPT_OPERATIONS = frozenset({"bind", "submit", "reconcile", "observe", "cancel"})
_SAFE_DATA_KEYS = frozenset({
    "actor",
    "command_id",
    "command_kind",
    "correlation_id",
    "event_id",
    "failure_code",
    "idempotency_key",
    "mechanism",
    "operation",
    "profile",
    "reason_code",
    "receipt_sha256",
    "request_sha256",
    "run_id",
    "session_id",
    "status",
})
_SHA_KEYS = frozenset({"receipt_sha256", "request_sha256"})
_LEGAL_TRANSITIONS = {
    "prepared": frozenset({
        "prepared",
        "submitted",
        "active",
        "needs_input",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "submitted": frozenset({
        "submitted",
        "active",
        "needs_input",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "active": frozenset({
        "active",
        "needs_input",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "needs_input": frozenset({
        "needs_input",
        "active",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "cancelling": frozenset({
        "cancelling",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "indeterminate": frozenset({
        "indeterminate",
        "submitted",
        "active",
        "needs_input",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "succeeded": frozenset({"succeeded"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


class HandoffStoreError(RuntimeError):
    """Base error for durable handoff operations."""


class HandoffNotFound(HandoffStoreError):
    """The requested handoff does not exist in this profile."""


class HandoffConflict(HandoffStoreError):
    """A semantic key or command ID was reused with different content."""


class HandoffStateConflict(HandoffStoreError):
    """A compare-and-set or lifecycle precondition did not match."""


class StaleAdvanceLease(HandoffStoreError):
    """An expired, released, or fenced worker attempted a write."""


@dataclass(frozen=True, slots=True)
class AdvanceLease:
    handoff_id: str
    owner: str
    epoch: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class HandoffEvent:
    handoff_id: str
    sequence: int
    event_id: str
    phase_before: str | None
    phase_after: str
    kind: str
    actor: str
    data: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidencePage:
    events: tuple[HandoffEvent, ...]
    next_after_sequence: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class CommandRecord:
    handoff_id: str
    command_id: str
    content_fingerprint: str
    kind: str
    payload: Mapping[str, object]
    delivery_state: str
    created_at: datetime
    updated_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value
        .astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"handoff {name} is invalid")
    return value


def _semantic_key(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SEMANTIC_KEY.fullmatch(value):
        raise ValueError(f"handoff {name} is invalid")
    return value


def _json(value: object, *, max_bytes: int = _MAX_JSON_BYTES) -> str:
    try:
        return canonical_json_bytes(value, max_bytes=max_bytes).decode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("handoff JSON is invalid or exceeds its byte limit") from exc


def _safe_data(
    value: Mapping[str, object] | None, *, reject_unsafe: bool
) -> Mapping[str, object]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("handoff safe data must be a mapping")
    safe: dict[str, object] = {}
    for key, item in value.items():
        if key not in _SAFE_DATA_KEYS:
            if reject_unsafe:
                raise ValueError("handoff safe data contains an unsupported field")
            continue
        try:
            if key in _SHA_KEYS:
                if not isinstance(item, str) or not _SHA256.fullmatch(item):
                    raise ValueError
            else:
                _identifier(item, key)
                if item.lower().startswith(("bearer", "basic")):
                    raise ValueError
        except ValueError:
            if reject_unsafe:
                raise ValueError("handoff safe data contains an unsafe value") from None
            continue
        safe[key] = item
    _json(safe)
    return _freeze(safe)  # type: ignore[return-value]


def _spec_json(spec: HandoffSpec) -> str:
    return _json(
        {
            "mode": spec.mode,
            "endpoint": spec.endpoint.canonical,
            "prompt": spec.prompt,
            "output_schema": spec.output_schema,
            "deadline_at": _timestamp(spec.deadline_at),
            "attribution": spec.attribution,
            "required_capabilities": sorted(spec.required_capabilities),
        },
        max_bytes=3_200_000,
    )


def _spec_from_json(value: str) -> HandoffSpec:
    raw = json.loads(value)
    deadline = _parse_timestamp(raw["deadline_at"])
    return HandoffSpec(
        mode=raw["mode"],
        endpoint=HandoffEndpoint.parse(raw["endpoint"]),
        prompt=raw["prompt"],
        output_schema=raw["output_schema"],
        deadline_at=deadline,
        attribution=raw["attribution"],
        required_capabilities=frozenset(raw["required_capabilities"]),
    )


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS handoffs (
        handoff_id TEXT PRIMARY KEY,
        key_scope TEXT NOT NULL,
        handoff_key TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        spec_fingerprint TEXT NOT NULL,
        mechanism TEXT,
        binding_json TEXT,
        checkpoint_json TEXT,
        phase TEXT NOT NULL,
        state_version INTEGER NOT NULL,
        next_advance_at TEXT,
        deadline_at TEXT,
        submit_attempted_at TEXT,
        cancel_requested_at TEXT,
        terminal_result_json TEXT,
        failure_code TEXT,
        advance_owner TEXT,
        advance_epoch INTEGER NOT NULL DEFAULT 0,
        advance_expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (key_scope, handoff_key)
    )""",
    """CREATE TABLE IF NOT EXISTS handoff_events (
        handoff_id TEXT NOT NULL REFERENCES handoffs(handoff_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        phase_before TEXT,
        phase_after TEXT NOT NULL,
        kind TEXT NOT NULL,
        actor TEXT NOT NULL,
        safe_data_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (handoff_id, sequence)
    )""",
    """CREATE TABLE IF NOT EXISTS handoff_commands (
        handoff_id TEXT NOT NULL REFERENCES handoffs(handoff_id) ON DELETE CASCADE,
        command_id TEXT NOT NULL,
        content_fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL,
        safe_payload_json TEXT NOT NULL,
        delivery_state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (handoff_id, command_id)
    )""",
    "CREATE INDEX IF NOT EXISTS handoffs_due_idx ON handoffs(phase, next_advance_at)",
    "CREATE INDEX IF NOT EXISTS handoffs_created_idx ON handoffs(created_at DESC, handoff_id DESC)",
)


class HandoffStore:
    """SQLite-backed, profile-local handoff source of truth."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = (
            Path(db_path) if db_path is not None else get_hermes_home() / "handoffs.db"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        try:
            from hermes_state import apply_wal_with_fallback

            self.journal_mode = apply_wal_with_fallback(
                self._conn, db_label="handoffs.db"
            )
            self._conn.execute("PRAGMA foreign_keys=ON")
            self.foreign_keys_enabled = (
                self._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            )
            self._install_schema()
            self._secure_files()
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._secure_files()
            self._conn.close()

    def __enter__(self) -> "HandoffStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _install_schema(self) -> None:
        with self._lock, write_txn(self._conn):
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise HandoffStoreError(
                    f"unsupported handoff schema version: {version}"
                )
            for statement in _SCHEMA:
                self._conn.execute(statement)
            self._conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _secure_files(self) -> None:
        for path in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                pass

    def _row(self, handoff_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id=?",
            (handoff_id,),
        ).fetchone()
        if row is None:
            raise HandoffNotFound(handoff_id)
        return row

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> HandoffSnapshot:
        def load(name: str) -> Any:
            return None if row[name] is None else json.loads(row[name])

        return HandoffSnapshot(
            handoff_id=row["handoff_id"],
            key_scope=row["key_scope"],
            handoff_key=row["handoff_key"],
            spec=_spec_from_json(row["spec_json"]),
            spec_fingerprint=row["spec_fingerprint"],
            phase=row["phase"],
            state_version=row["state_version"],
            mechanism=row["mechanism"],
            binding=load("binding_json"),
            checkpoint=load("checkpoint_json"),
            next_advance_at=_parse_timestamp(row["next_advance_at"]),
            submit_attempted_at=_parse_timestamp(row["submit_attempted_at"]),
            cancel_requested_at=_parse_timestamp(row["cancel_requested_at"]),
            terminal_result=load("terminal_result_json"),
            failure_code=row["failure_code"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    def _append_event(
        self,
        handoff_id: str,
        *,
        phase_before: str | None,
        phase_after: str,
        kind: str,
        actor: str = "service",
        data: Mapping[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> None:
        safe = _safe_data(data, reject_unsafe=False)
        sequence = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM handoff_events WHERE handoff_id=?",
            (handoff_id,),
        ).fetchone()[0]
        self._conn.execute(
            """INSERT INTO handoff_events
               (handoff_id, sequence, event_id, phase_before, phase_after, kind, actor,
                safe_data_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                handoff_id,
                sequence,
                str(uuid4()),
                phase_before,
                phase_after,
                _identifier(kind, "event kind"),
                _identifier(actor, "event actor"),
                _json(safe),
                _timestamp(created_at or _utc_now()),
            ),
        )

    def create_or_get(
        self,
        key_scope: str,
        handoff_key: str,
        spec: HandoffSpec,
        spec_fingerprint: str,
    ) -> HandoffSnapshot:
        _semantic_key(key_scope, "key scope")
        _semantic_key(handoff_key, "key")
        if not isinstance(spec, HandoffSpec):
            raise ValueError("handoff spec is invalid")
        if spec_fingerprint != spec.fingerprint or not _SHA256.fullmatch(
            spec_fingerprint
        ):
            raise ValueError("handoff spec fingerprint is invalid")
        encoded_spec = _spec_json(spec)
        now = _utc_now()
        with self._lock, write_txn(self._conn):
            existing = self._conn.execute(
                "SELECT * FROM handoffs WHERE key_scope=? AND handoff_key=?",
                (key_scope, handoff_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["spec_fingerprint"] != spec_fingerprint
                    or existing["spec_json"] != encoded_spec
                ):
                    raise HandoffConflict("handoff key already has different content")
                return self._snapshot(existing)

            handoff_id = str(uuid4())
            stamp = _timestamp(now)
            self._conn.execute(
                """INSERT INTO handoffs
                   (handoff_id, key_scope, handoff_key, spec_json, spec_fingerprint,
                    phase, state_version, deadline_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'prepared', 0, ?, ?, ?)""",
                (
                    handoff_id,
                    key_scope,
                    handoff_key,
                    encoded_spec,
                    spec_fingerprint,
                    _timestamp(spec.deadline_at),
                    stamp,
                    stamp,
                ),
            )
            self._append_event(
                handoff_id,
                phase_before=None,
                phase_after="prepared",
                kind="created",
                created_at=now,
            )
            return self._snapshot(self._row(handoff_id))

    def get(self, handoff_id: str) -> HandoffSnapshot:
        with self._lock:
            return self._snapshot(self._row(handoff_id))

    def list(
        self,
        query: Mapping[str, object] | None,
        *,
        limit: int,
        before: str | None,
    ) -> tuple[HandoffSnapshot, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise ValueError("handoff list limit must be between 1 and 200")
        query = query or {}
        if not isinstance(query, Mapping) or not set(query) <= {"key_scope", "phase"}:
            raise ValueError("handoff list query is invalid")
        clauses: list[str] = []
        values: list[object] = []
        for field in ("key_scope", "phase"):
            if field in query:
                clauses.append(f"{field}=?")
                validator = _semantic_key if field == "key_scope" else _identifier
                values.append(validator(query[field], field))
        with self._lock:
            if before is not None:
                cursor = self._row(before)
                clauses.append(
                    "(created_at < ? OR (created_at = ? AND handoff_id < ?))"
                )
                values.extend((
                    cursor["created_at"],
                    cursor["created_at"],
                    cursor["handoff_id"],
                ))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self._conn.execute(
                f"SELECT * FROM handoffs{where} ORDER BY created_at DESC, handoff_id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
            return tuple(self._snapshot(row) for row in rows)

    def evidence(
        self,
        handoff_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> EvidencePage:
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise ValueError("handoff evidence sequence is invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise ValueError("handoff evidence limit must be between 1 and 200")
        with self._lock:
            self._row(handoff_id)
            rows = self._conn.execute(
                """SELECT * FROM handoff_events
                   WHERE handoff_id=? AND sequence>? ORDER BY sequence LIMIT ?""",
                (handoff_id, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = tuple(
            HandoffEvent(
                handoff_id=row["handoff_id"],
                sequence=row["sequence"],
                event_id=row["event_id"],
                phase_before=row["phase_before"],
                phase_after=row["phase_after"],
                kind=row["kind"],
                actor=row["actor"],
                data=_freeze(json.loads(row["safe_data_json"])),  # type: ignore[arg-type]
                created_at=_parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
            )
            for row in rows
        )
        return EvidencePage(
            events=events,
            next_after_sequence=events[-1].sequence if events else after_sequence,
            has_more=has_more,
        )

    def bind(
        self,
        handoff_id: str,
        mechanism: str,
        binding: Mapping[str, object],
        checkpoint: Mapping[str, object],
        expected_version: int,
    ) -> HandoffSnapshot:
        with self._lock, write_txn(self._conn):
            row = self._row(handoff_id)
            if row["state_version"] != expected_version:
                raise HandoffStateConflict("handoff state version changed")
            return self._bind_row(row, mechanism, binding, checkpoint)

    def commit_binding(
        self,
        lease: AdvanceLease,
        mechanism: str,
        binding: Mapping[str, object],
        checkpoint: Mapping[str, object],
    ) -> HandoffSnapshot:
        with self._lock, write_txn(self._conn):
            row = self._lease_row(lease, _utc_now())
            return self._bind_row(row, mechanism, binding, checkpoint)

    def _bind_row(
        self,
        row: sqlite3.Row,
        mechanism: str,
        binding: Mapping[str, object],
        checkpoint: Mapping[str, object],
    ) -> HandoffSnapshot:
        mechanism = _identifier(mechanism, "mechanism")
        candidate = ChannelObservation(
            phase="prepared",
            mechanism=mechanism,
            binding=binding,
            checkpoint=checkpoint,
        )
        if candidate.binding and candidate.binding.get("mechanism") != mechanism:
            raise ValueError("handoff binding mechanism does not match")
        binding_json = _json(candidate.binding)
        checkpoint_json = _json(candidate.checkpoint)
        if row["phase"] in _TERMINAL_PHASES:
            raise HandoffStateConflict("terminal handoff cannot be bound")
        if row["mechanism"] is not None:
            if (
                row["mechanism"] == mechanism
                and row["binding_json"] == binding_json
                and row["checkpoint_json"] == checkpoint_json
            ):
                return self._snapshot(row)
            raise HandoffConflict("handoff mechanism binding is immutable")
        if row["submit_attempted_at"] is not None:
            raise HandoffStateConflict("handoff mechanism cannot bind after submission")
        now = _utc_now()
        self._conn.execute(
            """UPDATE handoffs SET mechanism=?, binding_json=?, checkpoint_json=?,
               state_version=state_version+1, updated_at=? WHERE handoff_id=?""",
            (
                mechanism,
                binding_json,
                checkpoint_json,
                _timestamp(now),
                row["handoff_id"],
            ),
        )
        self._append_event(
            row["handoff_id"],
            phase_before=row["phase"],
            phase_after=row["phase"],
            kind="bound",
            data={"mechanism": mechanism},
            created_at=now,
        )
        return self._snapshot(self._row(row["handoff_id"]))

    def claim_advance(
        self,
        handoff_id: str,
        owner: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> AdvanceLease | None:
        owner = _identifier(owner, "lease owner")
        now = _aware_utc(now, "lease time")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int | float)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("handoff lease duration must be finite and positive")
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, write_txn(self._conn):
            row = self._row(handoff_id)
            if row["phase"] in _TERMINAL_PHASES:
                return None
            existing_expiry = _parse_timestamp(row["advance_expires_at"])
            if (
                row["advance_owner"] is not None
                and existing_expiry is not None
                and existing_expiry > now
            ):
                return None
            epoch = row["advance_epoch"] + 1
            self._conn.execute(
                """UPDATE handoffs SET advance_owner=?, advance_epoch=?, advance_expires_at=?
                   WHERE handoff_id=?""",
                (owner, epoch, _timestamp(expires_at), handoff_id),
            )
            return AdvanceLease(handoff_id, owner, epoch, expires_at)

    def _lease_row(self, lease: AdvanceLease, now: datetime) -> sqlite3.Row:
        if not isinstance(lease, AdvanceLease):
            raise ValueError("handoff advance lease is invalid")
        row = self._row(lease.handoff_id)
        expires_at = _parse_timestamp(row["advance_expires_at"])
        if (
            row["advance_owner"] != lease.owner
            or row["advance_epoch"] != lease.epoch
            or expires_at is None
            or expires_at <= now
        ):
            raise StaleAdvanceLease("handoff advance lease is stale")
        return row

    def journal_attempt(
        self,
        lease: AdvanceLease,
        operation: str,
        *,
        data: Mapping[str, object] | None = None,
    ) -> HandoffSnapshot:
        if operation not in _ATTEMPT_OPERATIONS:
            raise ValueError("handoff advance operation is invalid")
        redacted = dict(_safe_data(data, reject_unsafe=False))
        redacted["operation"] = operation
        kind = f"{operation}_attempted"
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            row = self._lease_row(lease, now)
            if row["phase"] in _TERMINAL_PHASES:
                raise HandoffStateConflict("terminal handoff cannot start external I/O")
            if operation == "submit" and (
                row["phase"] != "prepared" or row["cancel_requested_at"] is not None
            ):
                raise HandoffStateConflict("cancelled handoff cannot be submitted")
            if operation == "submit" and (
                row["mechanism"] is None or row["binding_json"] is None
            ):
                raise HandoffStateConflict("handoff must bind before submission")
            if operation == "submit" and row["submit_attempted_at"] is not None:
                raise HandoffStateConflict("handoff submission was already attempted")
            if operation == "submit" and row["submit_attempted_at"] is None:
                self._conn.execute(
                    """UPDATE handoffs SET submit_attempted_at=?, state_version=state_version+1,
                       updated_at=? WHERE handoff_id=?""",
                    (_timestamp(now), _timestamp(now), lease.handoff_id),
                )
            else:
                self._conn.execute(
                    "UPDATE handoffs SET updated_at=? WHERE handoff_id=?",
                    (_timestamp(now), lease.handoff_id),
                )
            self._append_event(
                lease.handoff_id,
                phase_before=row["phase"],
                phase_after=row["phase"],
                kind=kind,
                data=redacted,
                created_at=now,
            )
            return self._snapshot(self._row(lease.handoff_id))

    def commit_observation(
        self,
        lease: AdvanceLease,
        observation: ChannelObservation,
    ) -> HandoffSnapshot:
        if not isinstance(observation, ChannelObservation):
            raise ValueError("handoff observation is invalid")
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            row = self._lease_row(lease, now)
            current = self._snapshot(row)
            if observation.phase not in _LEGAL_TRANSITIONS[current.phase]:
                raise HandoffStateConflict(
                    f"illegal handoff phase transition: {current.phase} -> {observation.phase}"
                )
            if (
                current.phase == "prepared"
                and observation.phase not in {"prepared", "failed"}
                and current.submit_attempted_at is None
            ):
                raise HandoffStateConflict(
                    "handoff cannot advance before submission is journaled"
                )
            if (
                observation.phase == "cancelling"
                and current.cancel_requested_at is None
            ):
                raise HandoffStateConflict(
                    "handoff cannot cancel without a durable command"
                )
            if observation.phase == "succeeded" and observation.terminal_result is None:
                raise HandoffStateConflict(
                    "succeeded handoff requires a terminal result"
                )
            if (
                observation.phase != "succeeded"
                and observation.terminal_result is not None
            ):
                raise HandoffStateConflict("terminal result is valid only for success")
            if observation.phase == "failed" and observation.failure_code is None:
                raise HandoffStateConflict(
                    "failed handoff requires a stable failure code"
                )
            if current.mechanism is not None and observation.mechanism not in (
                None,
                current.mechanism,
            ):
                raise HandoffConflict("handoff mechanism binding is immutable")
            if current.binding is not None and observation.binding not in (
                None,
                current.binding,
            ):
                raise HandoffConflict("handoff mechanism binding is immutable")
            if current.mechanism is None and observation.mechanism is not None:
                raise HandoffStateConflict(
                    "handoff mechanism must be sealed before observation"
                )
            if current.binding is None and observation.binding is not None:
                raise HandoffStateConflict(
                    "handoff binding must be sealed before observation"
                )

            checkpoint_json = _json(observation.checkpoint)
            binding_json = (
                row["binding_json"]
                if observation.binding is None
                else _json(observation.binding)
            )
            mechanism = (
                row["mechanism"]
                if observation.mechanism is None
                else observation.mechanism
            )
            result_json = (
                None
                if observation.terminal_result is None
                else _json(observation.terminal_result, max_bytes=600_000)
            )
            values = (
                observation.phase,
                mechanism,
                binding_json,
                checkpoint_json,
                _timestamp(observation.next_advance_at),
                result_json,
                observation.failure_code,
            )
            existing = (
                row["phase"],
                row["mechanism"],
                row["binding_json"],
                row["checkpoint_json"],
                row["next_advance_at"],
                row["terminal_result_json"],
                row["failure_code"],
            )
            if current.phase in _TERMINAL_PHASES:
                if values == existing:
                    return current
                raise HandoffStateConflict("terminal handoff facts are immutable")

            self._conn.execute(
                """UPDATE handoffs SET phase=?, mechanism=?, binding_json=?, checkpoint_json=?,
                   next_advance_at=?, terminal_result_json=?, failure_code=?,
                   state_version=state_version+1, updated_at=? WHERE handoff_id=?""",
                (*values, _timestamp(now), lease.handoff_id),
            )
            self._append_event(
                lease.handoff_id,
                phase_before=current.phase,
                phase_after=observation.phase,
                kind="observed",
                data={
                    "failure_code": observation.failure_code,
                    "mechanism": mechanism,
                    "status": observation.phase,
                },
                created_at=now,
            )
            return self._snapshot(self._row(lease.handoff_id))

    def release_advance(
        self,
        lease: AdvanceLease,
        *,
        next_advance_at: datetime | None,
    ) -> None:
        if next_advance_at is not None:
            next_advance_at = _aware_utc(next_advance_at, "next advance")
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            self._lease_row(lease, now)
            self._conn.execute(
                """UPDATE handoffs SET advance_owner=NULL, advance_expires_at=NULL,
                   next_advance_at=?, state_version=state_version+1, updated_at=?
                   WHERE handoff_id=?""",
                (_timestamp(next_advance_at), _timestamp(now), lease.handoff_id),
            )

    def record_command(
        self,
        handoff_id: str,
        command_id: str,
        kind: str,
        payload: Mapping[str, object],
    ) -> CommandRecord:
        command_id = _identifier(command_id, "command ID")
        if kind not in _COMMAND_KINDS:
            raise ValueError("handoff command kind is unsupported")
        safe_payload = _safe_data(payload, reject_unsafe=True)
        payload_json = _json(safe_payload)
        fingerprint = sha256(
            _json({"kind": kind, "payload": safe_payload}).encode()
        ).hexdigest()
        now = _utc_now()
        with self._lock, write_txn(self._conn):
            row = self._row(handoff_id)
            existing = self._conn.execute(
                "SELECT * FROM handoff_commands WHERE handoff_id=? AND command_id=?",
                (handoff_id, command_id),
            ).fetchone()
            if existing is not None:
                if existing["content_fingerprint"] != fingerprint:
                    raise HandoffConflict(
                        "handoff command ID already has different content"
                    )
                return self._command(existing)

            stamp = _timestamp(now)
            self._conn.execute(
                """INSERT INTO handoff_commands
                   (handoff_id, command_id, content_fingerprint, kind, safe_payload_json,
                    delivery_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (handoff_id, command_id, fingerprint, kind, payload_json, stamp, stamp),
            )
            phase_after = row["phase"]
            if kind == "cancel":
                if row["phase"] not in _TERMINAL_PHASES:
                    phase_after = "cancelling"
                self._conn.execute(
                    """UPDATE handoffs SET phase=?, cancel_requested_at=COALESCE(cancel_requested_at, ?),
                       next_advance_at=NULL, state_version=state_version+1, updated_at=?
                       WHERE handoff_id=?""",
                    (phase_after, stamp, stamp, handoff_id),
                )
            else:
                self._conn.execute(
                    """UPDATE handoffs SET next_advance_at=NULL,
                       state_version=state_version+1, updated_at=? WHERE handoff_id=?""",
                    (stamp, handoff_id),
                )
            self._append_event(
                handoff_id,
                phase_before=row["phase"],
                phase_after=phase_after,
                kind="cancel_requested" if kind == "cancel" else "reconcile_requested",
                actor=str(safe_payload.get("actor", "service")),
                data={"command_id": command_id, "command_kind": kind},
                created_at=now,
            )
            return self._command(
                self._conn.execute(
                    "SELECT * FROM handoff_commands WHERE handoff_id=? AND command_id=?",
                    (handoff_id, command_id),
                ).fetchone()
            )

    @staticmethod
    def _command(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            handoff_id=row["handoff_id"],
            command_id=row["command_id"],
            content_fingerprint=row["content_fingerprint"],
            kind=row["kind"],
            payload=_freeze(json.loads(row["safe_payload_json"])),  # type: ignore[arg-type]
            delivery_state=row["delivery_state"],
            created_at=_parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_timestamp(row["updated_at"]),  # type: ignore[arg-type]
        )


__all__ = [
    "AdvanceLease",
    "CommandRecord",
    "EvidencePage",
    "HandoffConflict",
    "HandoffEvent",
    "HandoffNotFound",
    "HandoffStateConflict",
    "HandoffStore",
    "HandoffStoreError",
    "StaleAdvanceLease",
]
