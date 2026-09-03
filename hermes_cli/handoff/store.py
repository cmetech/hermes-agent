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


_SCHEMA_VERSION = 2
_MAX_JSON_BYTES = 16_384
_MAX_DELIVERY_ATTEMPTS = 8
_TERMINAL_PHASES = frozenset({"succeeded", "failed", "cancelled"})
_ATTENTION_PHASES = frozenset({
    "needs_input", "indeterminate", "succeeded", "failed", "cancelled",
})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_SEMANTIC_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMAND_KINDS = frozenset({"cancel", "message", "reconcile", "respond", "steer"})
_APPROVAL_CHOICES = frozenset({"once", "session", "always", "deny"})
_DELIVERY_STATES = frozenset({"delivered", "indeterminate"})
_RETURN_DELIVERY_STATES = frozenset({"pending", "delivered", "failed"})
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
class DeliveryLease:
    delivery_id: str
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


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    handoff_id: str
    event_sequence: int
    route: Mapping[str, object]
    method: str
    state: str
    attempts: int
    next_attempt_at: datetime | None
    acknowledged_at: datetime | None
    failure_code: str | None
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


def _command_payload(
    kind: str, payload: Mapping[str, object]
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("handoff command payload must be a mapping")
    expected = {
        "cancel": {"actor"},
        "reconcile": {"actor"},
        "respond": {"actor", "request_id", "choice"},
        "steer": {"actor", "text"},
        "message": {"actor", "text", "correlation_id"},
    }[kind]
    if set(payload) != expected:
        raise ValueError("handoff command payload fields are invalid")
    normalized = dict(payload)
    _identifier(normalized["actor"], "command actor")
    if kind == "respond":
        _identifier(normalized["request_id"], "approval request ID")
        if (
            not isinstance(normalized["choice"], str)
            or normalized["choice"] not in _APPROVAL_CHOICES
        ):
            raise ValueError("handoff approval choice is invalid")
    if kind in {"steer", "message"}:
        text = normalized["text"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > _MAX_JSON_BYTES
            or "\0" in text
        ):
            raise ValueError("handoff command text is invalid or exceeds its byte limit")
    if kind == "message":
        _identifier(normalized["correlation_id"], "correlation ID")
    _json(normalized)
    return _freeze(normalized)  # type: ignore[return-value]


def _spec_json(spec: HandoffSpec) -> str:
    payload = {
        "mode": spec.mode,
        "endpoint": spec.endpoint.canonical,
        "prompt": spec.prompt,
        "output_schema": spec.output_schema,
        "deadline_at": _timestamp(spec.deadline_at),
        "attribution": spec.attribution,
        "required_capabilities": sorted(spec.required_capabilities),
    }
    if spec.return_route is not None:
        payload["return_route"] = spec.return_route
    return _json(payload, max_bytes=3_200_000)


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
        return_route=raw.get("return_route"),
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
    """CREATE TABLE IF NOT EXISTS handoff_deliveries (
        delivery_id TEXT PRIMARY KEY,
        handoff_id TEXT NOT NULL,
        event_sequence INTEGER NOT NULL,
        route_kind TEXT NOT NULL,
        route_json TEXT NOT NULL,
        method TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        lease_owner TEXT,
        lease_epoch INTEGER NOT NULL DEFAULT 0,
        lease_expires_at TEXT,
        acknowledged_at TEXT,
        failure_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (handoff_id, event_sequence, route_kind),
        FOREIGN KEY (handoff_id, event_sequence)
            REFERENCES handoff_events(handoff_id, sequence) ON DELETE CASCADE
    )""",
    "CREATE INDEX IF NOT EXISTS handoffs_due_idx ON handoffs(phase, next_advance_at)",
    "CREATE INDEX IF NOT EXISTS handoffs_created_idx ON handoffs(created_at DESC, handoff_id DESC)",
    """CREATE INDEX IF NOT EXISTS handoff_deliveries_due_idx
       ON handoff_deliveries(state, method, next_attempt_at)""",
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
            if version not in (0, 1, _SCHEMA_VERSION):
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
    ) -> HandoffEvent:
        safe = _safe_data(data, reject_unsafe=False)
        sequence = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM handoff_events WHERE handoff_id=?",
            (handoff_id,),
        ).fetchone()[0]
        event_id = str(uuid4())
        stamp = created_at or _utc_now()
        self._conn.execute(
            """INSERT INTO handoff_events
               (handoff_id, sequence, event_id, phase_before, phase_after, kind, actor,
                safe_data_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                handoff_id,
                sequence,
                event_id,
                phase_before,
                phase_after,
                _identifier(kind, "event kind"),
                _identifier(actor, "event actor"),
                _json(safe),
                _timestamp(stamp),
            ),
        )
        return HandoffEvent(
            handoff_id=handoff_id,
            sequence=sequence,
            event_id=event_id,
            phase_before=phase_before,
            phase_after=phase_after,
            kind=kind,
            actor=actor,
            data=safe,
            created_at=stamp,
        )

    def _append_delivery(
        self, snapshot: HandoffSnapshot, event: HandoffEvent
    ) -> None:
        route = snapshot.spec.return_route
        if route is None or event.phase_after not in _ATTENTION_PHASES:
            return
        route_kind = str(route["kind"])
        method = (
            str(route["delivery_policy"])
            if route_kind == "bot"
            else "attention"
        )
        state = "pending" if method == "wake" else "delivered"
        stamp = _timestamp(event.created_at)
        self._conn.execute(
            """INSERT INTO handoff_deliveries
               (delivery_id, handoff_id, event_sequence, route_kind, route_json,
                method, state, next_attempt_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(handoff_id, event_sequence, route_kind) DO NOTHING""",
            (
                str(uuid4()),
                snapshot.handoff_id,
                event.sequence,
                route_kind,
                _json(route),
                method,
                state,
                stamp if state == "pending" else None,
                stamp,
                stamp,
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
            if values == existing:
                return current

            self._conn.execute(
                """UPDATE handoffs SET phase=?, mechanism=?, binding_json=?, checkpoint_json=?,
                   next_advance_at=?, terminal_result_json=?, failure_code=?,
                   state_version=state_version+1, updated_at=? WHERE handoff_id=?""",
                (*values, _timestamp(now), lease.handoff_id),
            )
            event = self._append_event(
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
            snapshot = self._snapshot(self._row(lease.handoff_id))
            self._append_delivery(snapshot, event)
            return snapshot

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
        safe_payload = _command_payload(kind, payload)
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
                kind={
                    "cancel": "cancel_requested",
                    "reconcile": "reconcile_requested",
                }.get(kind, "command_recorded"),
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

    def get_command(self, handoff_id: str, command_id: str) -> CommandRecord:
        command_id = _identifier(command_id, "command ID")
        with self._lock:
            self._row(handoff_id)
            row = self._conn.execute(
                "SELECT * FROM handoff_commands WHERE handoff_id=? AND command_id=?",
                (handoff_id, command_id),
            ).fetchone()
            if row is None:
                raise HandoffNotFound("handoff command not found")
            return self._command(row)

    def claim_delivery_command(self, lease: AdvanceLease) -> CommandRecord | None:
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            handoff = self._lease_row(lease, now)
            if handoff["cancel_requested_at"] is not None:
                return None
            row = self._conn.execute(
                """SELECT * FROM handoff_commands
                   WHERE handoff_id=? AND kind IN ('message', 'respond', 'steer')
                     AND delivery_state IN ('pending', 'attempted')
                   ORDER BY CASE delivery_state WHEN 'attempted' THEN 0 ELSE 1 END,
                            created_at, command_id
                   LIMIT 1""",
                (lease.handoff_id,),
            ).fetchone()
            if row is None:
                return None
            command = self._command(row)
            if command.delivery_state == "pending":
                stamp = _timestamp(now)
                changed = self._conn.execute(
                    """UPDATE handoff_commands SET delivery_state='attempted', updated_at=?
                       WHERE handoff_id=? AND command_id=? AND delivery_state='pending'""",
                    (stamp, lease.handoff_id, command.command_id),
                ).rowcount
                if changed != 1:
                    raise HandoffStateConflict("handoff command claim lost its CAS")
                self._append_event(
                    lease.handoff_id,
                    phase_before=handoff["phase"],
                    phase_after=handoff["phase"],
                    kind="command_attempted",
                    actor=str(command.payload["actor"]),
                    data={
                        "command_id": command.command_id,
                        "command_kind": command.kind,
                        "status": "attempted",
                    },
                    created_at=now,
                )
            return command

    def complete_delivery_command(
        self,
        lease: AdvanceLease,
        command_id: str,
        delivery_state: str,
        *,
        failure_code: str | None = None,
    ) -> CommandRecord:
        command_id = _identifier(command_id, "command ID")
        if delivery_state not in _DELIVERY_STATES:
            raise ValueError("handoff command delivery state is invalid")
        if failure_code is not None:
            _identifier(failure_code, "command failure code")
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            handoff = self._lease_row(lease, now)
            row = self._conn.execute(
                "SELECT * FROM handoff_commands WHERE handoff_id=? AND command_id=?",
                (lease.handoff_id, command_id),
            ).fetchone()
            if row is None:
                raise HandoffNotFound("handoff command not found")
            command = self._command(row)
            if command.delivery_state == delivery_state:
                return command
            if command.delivery_state != "attempted":
                raise HandoffStateConflict(
                    "handoff command delivery state is immutable"
                )
            stamp = _timestamp(now)
            self._conn.execute(
                """UPDATE handoff_commands SET delivery_state=?, updated_at=?
                   WHERE handoff_id=? AND command_id=? AND delivery_state='attempted'""",
                (delivery_state, stamp, lease.handoff_id, command_id),
            )
            self._append_event(
                lease.handoff_id,
                phase_before=handoff["phase"],
                phase_after=handoff["phase"],
                kind="command_delivery",
                actor=str(command.payload["actor"]),
                data={
                    "command_id": command_id,
                    "command_kind": command.kind,
                    "status": delivery_state,
                    **({"failure_code": failure_code} if failure_code else {}),
                },
                created_at=now,
            )
            return self._command(
                self._conn.execute(
                    "SELECT * FROM handoff_commands WHERE handoff_id=? AND command_id=?",
                    (lease.handoff_id, command_id),
                ).fetchone()
            )

    def get_delivery(self, delivery_id: str) -> DeliveryRecord:
        delivery_id = _identifier(delivery_id, "delivery ID")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoff_deliveries WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise HandoffNotFound("handoff delivery not found")
            return self._delivery(row)

    def attention(
        self, handoff_id: str, *, limit: int
    ) -> tuple[DeliveryRecord, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise ValueError("handoff attention limit must be between 1 and 200")
        with self._lock:
            self._row(handoff_id)
            rows = self._conn.execute(
                """SELECT * FROM handoff_deliveries
                   WHERE handoff_id=? AND acknowledged_at IS NULL
                   ORDER BY created_at, delivery_id LIMIT ?""",
                (handoff_id, limit),
            ).fetchall()
            return tuple(self._delivery(row) for row in rows)

    def has_attention(self, handoff_id: str) -> bool:
        with self._lock:
            self._row(handoff_id)
            return self._conn.execute(
                """SELECT 1 FROM handoff_deliveries
                   WHERE handoff_id=? AND acknowledged_at IS NULL LIMIT 1""",
                (handoff_id,),
            ).fetchone() is not None

    def due_deliveries(
        self, *, now: datetime, limit: int
    ) -> tuple[DeliveryRecord, ...]:
        now = _aware_utc(now, "delivery due time")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise ValueError("handoff delivery limit must be between 1 and 200")
        stamp = _timestamp(now)
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM handoff_deliveries
                   WHERE method='wake' AND state='pending'
                     AND acknowledged_at IS NULL
                     AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                     AND (lease_owner IS NULL OR lease_expires_at<=?)
                   ORDER BY next_attempt_at, created_at, delivery_id LIMIT ?""",
                (stamp, stamp, limit),
            ).fetchall()
            return tuple(self._delivery(row) for row in rows)

    def claim_delivery(
        self,
        delivery_id: str,
        owner: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> DeliveryLease | None:
        delivery_id = _identifier(delivery_id, "delivery ID")
        owner = _identifier(owner, "delivery owner")
        now = _aware_utc(now, "delivery lease time")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int | float)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("handoff delivery lease must be finite and positive")
        expires_at = now + timedelta(seconds=float(lease_seconds))
        stamp = _timestamp(now)
        with self._lock, write_txn(self._conn):
            changed = self._conn.execute(
                """UPDATE handoff_deliveries
                   SET lease_owner=?, lease_epoch=lease_epoch+1,
                       lease_expires_at=?, attempts=attempts+1, updated_at=?
                   WHERE delivery_id=? AND method='wake' AND state='pending'
                     AND acknowledged_at IS NULL AND attempts<?
                     AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                     AND (lease_owner IS NULL OR lease_expires_at<=?)""",
                (
                    owner,
                    _timestamp(expires_at),
                    stamp,
                    delivery_id,
                    _MAX_DELIVERY_ATTEMPTS,
                    stamp,
                    stamp,
                ),
            ).rowcount
            if changed != 1:
                self._conn.execute(
                    """UPDATE handoff_deliveries
                       SET state='failed', next_attempt_at=NULL,
                           lease_owner=NULL, lease_expires_at=NULL,
                           failure_code=COALESCE(
                               failure_code, 'delivery_attempts_exhausted'
                           ), updated_at=?
                       WHERE delivery_id=? AND method='wake' AND state='pending'
                         AND acknowledged_at IS NULL AND attempts>=?
                         AND (lease_owner IS NULL OR lease_expires_at<=?)""",
                    (stamp, delivery_id, _MAX_DELIVERY_ATTEMPTS, stamp),
                )
                return None
            row = self._conn.execute(
                "SELECT lease_epoch FROM handoff_deliveries WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            return DeliveryLease(delivery_id, owner, row["lease_epoch"], expires_at)

    def _delivery_lease_row(
        self, lease: DeliveryLease, now: datetime
    ) -> sqlite3.Row:
        if not isinstance(lease, DeliveryLease):
            raise ValueError("handoff delivery lease is invalid")
        row = self._conn.execute(
            "SELECT * FROM handoff_deliveries WHERE delivery_id=?",
            (lease.delivery_id,),
        ).fetchone()
        expires_at = None if row is None else _parse_timestamp(row["lease_expires_at"])
        if (
            row is None
            or row["lease_owner"] != lease.owner
            or row["lease_epoch"] != lease.epoch
            or row["state"] != "pending"
            or row["acknowledged_at"] is not None
            or expires_at is None
            or expires_at <= now
        ):
            raise StaleAdvanceLease("handoff delivery lease is stale")
        return row

    def release_delivery(
        self,
        lease: DeliveryLease,
        *,
        next_attempt_at: datetime,
        failure_code: str,
    ) -> DeliveryRecord:
        next_attempt_at = _aware_utc(next_attempt_at, "next delivery attempt")
        failure_code = _identifier(failure_code, "delivery failure code")
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            row = self._delivery_lease_row(lease, now)
            state = "failed" if row["attempts"] >= _MAX_DELIVERY_ATTEMPTS else "pending"
            self._conn.execute(
                """UPDATE handoff_deliveries
                   SET state=?, next_attempt_at=?, lease_owner=NULL,
                       lease_expires_at=NULL, failure_code=?, updated_at=?
                   WHERE delivery_id=?""",
                (
                    state,
                    _timestamp(next_attempt_at) if state == "pending" else None,
                    failure_code,
                    _timestamp(now),
                    lease.delivery_id,
                ),
            )
            return self.get_delivery(lease.delivery_id)

    def complete_delivery(self, lease: DeliveryLease) -> DeliveryRecord:
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            self._delivery_lease_row(lease, now)
            self._conn.execute(
                """UPDATE handoff_deliveries
                   SET state='delivered', next_attempt_at=NULL, lease_owner=NULL,
                       lease_expires_at=NULL, failure_code=NULL, updated_at=?
                   WHERE delivery_id=?""",
                (_timestamp(now), lease.delivery_id),
            )
            return self.get_delivery(lease.delivery_id)

    def fail_delivery(
        self, lease: DeliveryLease, *, failure_code: str
    ) -> DeliveryRecord:
        failure_code = _identifier(failure_code, "delivery failure code")
        with self._lock, write_txn(self._conn):
            now = _utc_now()
            self._delivery_lease_row(lease, now)
            self._conn.execute(
                """UPDATE handoff_deliveries
                   SET state='failed', next_attempt_at=NULL, lease_owner=NULL,
                       lease_expires_at=NULL, failure_code=?, updated_at=?
                   WHERE delivery_id=?""",
                (failure_code, _timestamp(now), lease.delivery_id),
            )
            return self.get_delivery(lease.delivery_id)

    def acknowledge(self, handoff_id: str, *, actor: str) -> int:
        actor = _identifier(actor, "acknowledgement actor")
        with self._lock, write_txn(self._conn):
            handoff = self._row(handoff_id)
            now = _utc_now()
            changed = self._conn.execute(
                """UPDATE handoff_deliveries SET acknowledged_at=?, updated_at=?
                   WHERE handoff_id=? AND acknowledged_at IS NULL""",
                (_timestamp(now), _timestamp(now), handoff_id),
            ).rowcount
            if changed:
                self._append_event(
                    handoff_id,
                    phase_before=handoff["phase"],
                    phase_after=handoff["phase"],
                    kind="acknowledged",
                    actor=actor,
                    created_at=now,
                )
            return changed

    @staticmethod
    def _delivery(row: sqlite3.Row) -> DeliveryRecord:
        if row["state"] not in _RETURN_DELIVERY_STATES:
            raise HandoffStoreError("handoff delivery state is invalid")
        return DeliveryRecord(
            delivery_id=row["delivery_id"],
            handoff_id=row["handoff_id"],
            event_sequence=row["event_sequence"],
            route=_freeze(json.loads(row["route_json"])),  # type: ignore[arg-type]
            method=row["method"],
            state=row["state"],
            attempts=row["attempts"],
            next_attempt_at=_parse_timestamp(row["next_attempt_at"]),
            acknowledged_at=_parse_timestamp(row["acknowledged_at"]),
            failure_code=row["failure_code"],
            created_at=_parse_timestamp(row["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse_timestamp(row["updated_at"]),  # type: ignore[arg-type]
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
    "DeliveryLease",
    "DeliveryRecord",
    "EvidencePage",
    "HandoffConflict",
    "HandoffEvent",
    "HandoffNotFound",
    "HandoffStateConflict",
    "HandoffStore",
    "HandoffStoreError",
    "StaleAdvanceLease",
]
