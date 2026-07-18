"""Durable, epoch-fenced workflow coordinator ownership."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterator, Literal


CoordinatorHostKind = Literal["web", "gateway"]
CoordinatorHealthStatus = Literal["healthy", "standby", "unavailable", "degraded"]


def _instant(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _encoded(value: datetime) -> str:
    return _instant(value, name="timestamp").isoformat()


def _decoded(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _lease_seconds(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError("lease_seconds must be positive and finite")
    return float(value)


@dataclass(frozen=True, slots=True)
class CoordinatorIdentity:
    owner_id: str
    host_kind: CoordinatorHostKind
    host_instance_id: str
    pid: int
    process_start_time: int | None

    def __post_init__(self) -> None:
        for name in ("owner_id", "host_instance_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded non-empty text")
        if self.host_kind not in {"web", "gateway"}:
            raise ValueError("host_kind must be web or gateway")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if self.process_start_time is not None and (
            isinstance(self.process_start_time, bool)
            or not isinstance(self.process_start_time, int)
            or self.process_start_time < 0
        ):
            raise ValueError("process_start_time must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CoordinatorLease:
    owner_id: str
    host_kind: CoordinatorHostKind
    host_instance_id: str
    pid: int
    process_start_time: int | None
    epoch: int
    heartbeat_at: datetime
    lease_expires_at: datetime
    acquired_at: datetime
    sweep_cursor: str | None
    last_progress_at: datetime | None

    def matches(self, identity: CoordinatorIdentity) -> bool:
        return (
            self.owner_id == identity.owner_id
            and self.host_kind == identity.host_kind
            and self.host_instance_id == identity.host_instance_id
            and self.pid == identity.pid
            and self.process_start_time == identity.process_start_time
        )


@dataclass(frozen=True, slots=True)
class CoordinatorAcquisition:
    is_leader: bool
    lease: CoordinatorLease


@dataclass(frozen=True, slots=True)
class CoordinatorHealth:
    status: CoordinatorHealthStatus
    reason_code: str
    lease: CoordinatorLease | None


def install_coordinator_schema(connection: sqlite3.Connection) -> None:
    """Install workflow-owned coordinator tables on an existing store handle."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS coordinator_lease (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            owner_id TEXT NOT NULL,
            host_kind TEXT NOT NULL CHECK(host_kind IN ('web','gateway')),
            host_instance_id TEXT NOT NULL,
            pid INTEGER NOT NULL,
            process_start_time INTEGER,
            epoch INTEGER NOT NULL CHECK(epoch > 0),
            heartbeat_at TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            sweep_cursor TEXT,
            last_progress_at TEXT
        );
        CREATE TABLE IF NOT EXISTS coordinator_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )


class CoordinatorStore:
    """SQLite authority for one workflow coordinator leader per profile."""

    def __init__(self, database: str | Path, *, busy_timeout_seconds: float = 5.0):
        self.database = Path(database)
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, int | float)
            or not math.isfinite(float(busy_timeout_seconds))
            or busy_timeout_seconds <= 0
        ):
            raise ValueError("busy_timeout_seconds must be positive and finite")
        self.busy_timeout_seconds = float(busy_timeout_seconds)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                f"PRAGMA busy_timeout={int(self.busy_timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _lease(row: sqlite3.Row) -> CoordinatorLease:
        heartbeat_at = _decoded(row["heartbeat_at"])
        lease_expires_at = _decoded(row["lease_expires_at"])
        acquired_at = _decoded(row["acquired_at"])
        assert heartbeat_at is not None
        assert lease_expires_at is not None
        assert acquired_at is not None
        return CoordinatorLease(
            owner_id=str(row["owner_id"]),
            host_kind=str(row["host_kind"]),
            host_instance_id=str(row["host_instance_id"]),
            pid=int(row["pid"]),
            process_start_time=(
                int(row["process_start_time"])
                if row["process_start_time"] is not None
                else None
            ),
            epoch=int(row["epoch"]),
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
            acquired_at=acquired_at,
            sweep_cursor=(str(row["sweep_cursor"]) if row["sweep_cursor"] else None),
            last_progress_at=_decoded(row["last_progress_at"]),
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        timestamp: datetime,
        event_type: str,
        owner_id: str,
        epoch: int,
        payload: dict[str, object] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO coordinator_events "
            "(timestamp, event_type, owner_id, epoch, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _encoded(timestamp),
                event_type,
                owner_id,
                epoch,
                json.dumps(payload or {}, sort_keys=True, separators=(",", ":")),
            ),
        )

    def observe(self, *, now: datetime) -> CoordinatorLease | None:
        _instant(now, name="now")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM coordinator_lease WHERE singleton=1"
            ).fetchone()
        return self._lease(row) if row is not None else None

    def health(self, *, now: datetime) -> CoordinatorHealth:
        instant = _instant(now, name="now")
        lease = self.observe(now=instant)
        if lease is None:
            return CoordinatorHealth(
                status="unavailable",
                reason_code="coordinator_missing",
                lease=None,
            )
        if lease.lease_expires_at <= instant:
            return CoordinatorHealth(
                status="unavailable",
                reason_code="coordinator_lease_expired",
                lease=lease,
            )
        return CoordinatorHealth(
            status="healthy",
            reason_code="coordinator_heartbeat_fresh",
            lease=lease,
        )

    def try_acquire(
        self,
        identity: CoordinatorIdentity,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> CoordinatorAcquisition:
        instant = _instant(now, name="now")
        duration = _lease_seconds(lease_seconds)
        expires = instant + timedelta(seconds=duration)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM coordinator_lease WHERE singleton=1"
                ).fetchone()
                if row is not None:
                    current = self._lease(row)
                    if current.lease_expires_at > instant:
                        connection.commit()
                        return CoordinatorAcquisition(
                            is_leader=current.matches(identity),
                            lease=current,
                        )
                    epoch = current.epoch + 1
                    sweep_cursor = current.sweep_cursor
                    last_progress_at = current.last_progress_at
                else:
                    epoch = 1
                    sweep_cursor = None
                    last_progress_at = None
                connection.execute(
                    "INSERT INTO coordinator_lease ("
                    "singleton, owner_id, host_kind, host_instance_id, pid, "
                    "process_start_time, epoch, heartbeat_at, lease_expires_at, "
                    "acquired_at, sweep_cursor, last_progress_at) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "owner_id=excluded.owner_id, host_kind=excluded.host_kind, "
                    "host_instance_id=excluded.host_instance_id, pid=excluded.pid, "
                    "process_start_time=excluded.process_start_time, "
                    "epoch=excluded.epoch, heartbeat_at=excluded.heartbeat_at, "
                    "lease_expires_at=excluded.lease_expires_at, "
                    "acquired_at=excluded.acquired_at, "
                    "sweep_cursor=excluded.sweep_cursor, "
                    "last_progress_at=excluded.last_progress_at",
                    (
                        identity.owner_id,
                        identity.host_kind,
                        identity.host_instance_id,
                        identity.pid,
                        identity.process_start_time,
                        epoch,
                        _encoded(instant),
                        _encoded(expires),
                        _encoded(instant),
                        sweep_cursor,
                        _encoded(last_progress_at) if last_progress_at else None,
                    ),
                )
                self._event(
                    connection,
                    timestamp=instant,
                    event_type="coordinator_acquired",
                    owner_id=identity.owner_id,
                    epoch=epoch,
                    payload={
                        "host_kind": identity.host_kind,
                        "host_instance_id": identity.host_instance_id,
                        "pid": identity.pid,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM coordinator_lease WHERE singleton=1"
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        assert row is not None
        return CoordinatorAcquisition(is_leader=True, lease=self._lease(row))

    def renew(
        self,
        identity: CoordinatorIdentity,
        *,
        epoch: int,
        now: datetime,
        lease_seconds: float,
        sweep_cursor: str | None = None,
        last_progress_at: datetime | None = None,
    ) -> bool:
        instant = _instant(now, name="now")
        duration = _lease_seconds(lease_seconds)
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("epoch must be a positive integer")
        if sweep_cursor is not None and (
            not isinstance(sweep_cursor, str) or len(sweep_cursor) > 512
        ):
            raise ValueError("sweep_cursor must be bounded text")
        progress = (
            _instant(last_progress_at, name="last_progress_at")
            if last_progress_at is not None
            else None
        )
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE coordinator_lease SET heartbeat_at=?, lease_expires_at=?, "
                "sweep_cursor=COALESCE(?, sweep_cursor), "
                "last_progress_at=COALESCE(?, last_progress_at) "
                "WHERE singleton=1 AND owner_id=? AND host_kind=? "
                "AND host_instance_id=? AND pid=? "
                "AND process_start_time IS ? AND epoch=? AND lease_expires_at>?",
                (
                    _encoded(instant),
                    _encoded(instant + timedelta(seconds=duration)),
                    sweep_cursor,
                    _encoded(progress) if progress else None,
                    identity.owner_id,
                    identity.host_kind,
                    identity.host_instance_id,
                    identity.pid,
                    identity.process_start_time,
                    epoch,
                    _encoded(instant),
                ),
            ).rowcount
        return updated == 1

    def release(
        self,
        identity: CoordinatorIdentity,
        *,
        epoch: int,
        now: datetime,
    ) -> bool:
        instant = _instant(now, name="now")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("epoch must be a positive integer")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updated = connection.execute(
                    "UPDATE coordinator_lease SET heartbeat_at=?, lease_expires_at=? "
                    "WHERE singleton=1 AND owner_id=? AND host_kind=? "
                    "AND host_instance_id=? AND pid=? "
                    "AND process_start_time IS ? AND epoch=?",
                    (
                        _encoded(instant),
                        _encoded(instant),
                        identity.owner_id,
                        identity.host_kind,
                        identity.host_instance_id,
                        identity.pid,
                        identity.process_start_time,
                        epoch,
                    ),
                ).rowcount
                if updated:
                    self._event(
                        connection,
                        timestamp=instant,
                        event_type="coordinator_released",
                        owner_id=identity.owner_id,
                        epoch=epoch,
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return updated == 1


__all__ = [
    "CoordinatorAcquisition",
    "CoordinatorHealth",
    "CoordinatorHealthStatus",
    "CoordinatorHostKind",
    "CoordinatorIdentity",
    "CoordinatorLease",
    "CoordinatorStore",
    "install_coordinator_schema",
]
