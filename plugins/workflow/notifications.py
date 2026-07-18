"""Durable workflow notification outbox and delivery receipts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Mapping

from plugins.workflow.sanitize import sanitize_projection


COALESCED_KINDS = frozenset({"failure", "stalled", "retry"})
ATTENTION_KINDS = frozenset(
    {
        "approval_required",
        "input_required",
        "failure",
        "stalled",
        "reconciliation_required",
    }
)


def install_notification_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_notification_outbox (
            notification_id TEXT PRIMARY KEY,
            transition_key TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            destination TEXT NOT NULL,
            transition_version INTEGER NOT NULL,
            coalesced_count INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            delivered_at TEXT,
            dismissed_at TEXT,
            last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS workflow_notification_delivery
        ON workflow_notification_outbox(destination, state, available_at);
        CREATE INDEX IF NOT EXISTS workflow_notification_attention
        ON workflow_notification_outbox(run_id, kind, state);
        CREATE TABLE IF NOT EXISTS workflow_notification_facts (
            transition_key TEXT PRIMARY KEY,
            notification_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            destination TEXT NOT NULL,
            transition_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_notification_fact_run
        ON workflow_notification_facts(run_id, occurred_at);
        CREATE TABLE IF NOT EXISTS workflow_notification_reconcile_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            cursor_created_at TEXT,
            cursor_run_id TEXT
        );
        INSERT OR IGNORE INTO workflow_notification_reconcile_state (
            singleton, cursor_created_at, cursor_run_id
        ) VALUES (1, NULL, NULL);
        """
    )


def notification_kind(event_type: str, projection: Mapping[str, object]) -> str | None:
    if event_type in {
        "node_reconciliation_required",
        "run_reconciliation_required",
        "cancel_reconciliation_required",
    }:
        return "reconciliation_required"
    if event_type in {"run_failed", "cleanup_failed"}:
        return "failure"
    if event_type in {"run_succeeded"}:
        return "completion"
    if event_type in {"run_cancelled"}:
        return "cancellation"
    if event_type in {"run_retry_waiting", "node_retry_scheduled"}:
        return "retry"
    if event_type in {"run_stalled", "coordinator_stalled"}:
        return "stalled"
    if event_type in {"workflow_approval_required", "node_approval_required"}:
        return "approval_required"
    if event_type in {"loop_input_required"}:
        return "input_required"
    if event_type == "run_paused":
        pending = projection.get("pending_interaction")
        if not isinstance(pending, Mapping):
            pending = next(
                (
                    node.get("pending_interaction")
                    for node in projection.get("nodes", {}).values()
                    if isinstance(node, Mapping)
                    and isinstance(node.get("pending_interaction"), Mapping)
                ),
                None,
            )
        pending_type = pending.get("type") if isinstance(pending, Mapping) else None
        if pending_type in {"approval", "workflow_approval"}:
            return "approval_required"
        if pending_type == "loop_input":
            return "input_required"
        if pending_type == "reconcile":
            return "reconciliation_required"
    return None


class NotificationOutbox:
    """Lease-based delivery authority backed by the RunStore SQLite index."""

    def __init__(self, store) -> None:
        self.store = store
        with self.store._connect() as connection:
            install_notification_schema(connection)

    @staticmethod
    def _aware(now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("notification clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    def record(
        self,
        *,
        run_id: str,
        kind: str,
        destination: str,
        transition_version: int,
        payload: Mapping[str, object],
        delivery_state: str = "pending",
        now: datetime | None = None,
    ) -> str:
        if delivery_state not in {"pending", "suppressed"}:
            raise ValueError("delivery_state must be pending or suppressed")
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        transition_key = f"{run_id}:{kind}:{transition_version}:{destination}"
        safe_payload = json.dumps(
            sanitize_projection(dict(payload)), sort_keys=True, separators=(",", ":")
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT notification_id FROM workflow_notification_facts "
                "WHERE transition_key=?",
                (transition_key,),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return str(duplicate["notification_id"])
            if kind in COALESCED_KINDS:
                window = (observed - timedelta(seconds=60)).isoformat()
                candidate = connection.execute(
                    "SELECT notification_id FROM workflow_notification_outbox "
                    "WHERE run_id=? AND kind=? AND destination=? AND state='pending' "
                    "AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                    (run_id, kind, destination, window),
                ).fetchone()
                if candidate is not None:
                    connection.execute(
                        "UPDATE workflow_notification_outbox SET transition_key=?, "
                        "transition_version=?, coalesced_count=coalesced_count+1, "
                        "payload_json=?, updated_at=? WHERE notification_id=?",
                        (
                            transition_key,
                            transition_version,
                            safe_payload,
                            timestamp,
                            candidate["notification_id"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO workflow_notification_facts ("
                        "transition_key, notification_id, run_id, kind, destination, "
                        "transition_version, payload_json, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            transition_key,
                            candidate["notification_id"],
                            run_id,
                            kind,
                            destination,
                            transition_version,
                            safe_payload,
                            timestamp,
                        ),
                    )
                    connection.commit()
                    return str(candidate["notification_id"])
            notification_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO workflow_notification_outbox ("
                "notification_id, transition_key, run_id, kind, destination, "
                "transition_version, payload_json, state, created_at, updated_at, "
                "available_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notification_id,
                    transition_key,
                    run_id,
                    kind,
                    destination,
                    transition_version,
                    safe_payload,
                    delivery_state,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO workflow_notification_facts ("
                "transition_key, notification_id, run_id, kind, destination, "
                "transition_version, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_key,
                    notification_id,
                    run_id,
                    kind,
                    destination,
                    transition_version,
                    safe_payload,
                    timestamp,
                ),
            )
            connection.commit()
        return notification_id

    def reconcile_journal(self, *, limit_runs: int = 200) -> int:
        """Idempotently repair a crash gap between journal and outbox writes."""
        if not 1 <= limit_runs <= 1000:
            raise ValueError("limit_runs must be between 1 and 1000")
        repaired = 0
        with self.store._connect() as connection:
            cursor = connection.execute(
                "SELECT cursor_created_at, cursor_run_id FROM "
                "workflow_notification_reconcile_state WHERE singleton=1"
            ).fetchone()
            cursor_created_at = cursor["cursor_created_at"] if cursor else None
            cursor_run_id = cursor["cursor_run_id"] if cursor else None

            def page(after_created_at: str | None, after_run_id: str | None):
                if after_created_at is None or after_run_id is None:
                    return connection.execute(
                        "SELECT run_id, run_directory, created_at FROM runs "
                        "WHERE admission_state='published' "
                        "ORDER BY created_at, run_id LIMIT ?",
                        (limit_runs,),
                    ).fetchall()
                return connection.execute(
                    "SELECT run_id, run_directory, created_at FROM runs "
                    "WHERE admission_state='published' AND "
                    "(created_at>? OR (created_at=? AND run_id>?)) "
                    "ORDER BY created_at, run_id LIMIT ?",
                    (
                        after_created_at,
                        after_created_at,
                        after_run_id,
                        limit_runs,
                    ),
                ).fetchall()

            rows = page(cursor_created_at, cursor_run_id)
            if not rows and cursor_created_at is not None:
                rows = page(None, None)
            existing = {
                str(item["transition_key"])
                for item in connection.execute(
                    "SELECT transition_key FROM workflow_notification_facts"
                ).fetchall()
            }
        for row in rows:
            try:
                events = self.store._read_journal_events(
                    self.store.run_directory(str(row["run_id"]))
                )
            except (KeyError, OSError, ValueError):
                continue
            for event in events:
                projection = event.get("projection")
                if not isinstance(projection, Mapping):
                    continue
                kind = notification_kind(str(event.get("event_type") or ""), projection)
                if kind is None:
                    continue
                transition_key = (
                    f"{row['run_id']}:{kind}:"
                    f"{int(projection['state_version'])}:desktop"
                )
                if transition_key in existing:
                    continue
                timestamp = datetime.fromisoformat(str(event["timestamp"]))
                self.record(
                    run_id=str(row["run_id"]),
                    kind=kind,
                    destination="desktop",
                    transition_version=int(projection["state_version"]),
                    payload={
                        "workflow": projection.get("workflow"),
                        "status": projection.get("status"),
                        "event_type": event.get("event_type"),
                        "node_id": event.get("node_id"),
                        "last_error": projection.get("last_error"),
                    },
                    delivery_state=(
                        "pending"
                        if projection.get("execution_mode") == "background"
                        else "suppressed"
                    ),
                    now=timestamp,
                )
                existing.add(transition_key)
                repaired += 1
        if rows:
            last = rows[-1]
            with self.store._connect() as connection:
                connection.execute(
                    "UPDATE workflow_notification_reconcile_state SET "
                    "cursor_created_at=?, cursor_run_id=? WHERE singleton=1",
                    (last["created_at"], last["run_id"]),
                )
        return repaired

    def lease(
        self,
        *,
        destination: str,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: float = 30,
        limit: int = 20,
    ) -> tuple[dict[str, object], ...]:
        if not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be bounded text")
        if not 1 <= limit <= 100 or lease_seconds <= 0:
            raise ValueError("notification lease bounds are invalid")
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        expires = (observed + timedelta(seconds=lease_seconds)).isoformat()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE workflow_notification_outbox SET state='pending', "
                "lease_owner=NULL, lease_expires_at=NULL WHERE state='leased' "
                "AND lease_expires_at<=?",
                (timestamp,),
            )
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE destination=? "
                "AND state='pending' AND available_at<=? "
                "ORDER BY created_at, notification_id LIMIT ?",
                (destination, timestamp, limit),
            ).fetchall()
            ids = [row["notification_id"] for row in rows]
            for notification_id in ids:
                connection.execute(
                    "UPDATE workflow_notification_outbox SET state='leased', "
                    "lease_owner=?, lease_expires_at=?, attempts=attempts+1, "
                    "updated_at=? WHERE notification_id=? AND state='pending'",
                    (owner_id, expires, timestamp, notification_id),
                )
            connection.commit()
        return tuple(self._public(row, lease_owner=owner_id, lease_expires_at=expires) for row in rows)

    def ack(self, notification_id: str, *, owner_id: str, now: datetime | None = None) -> bool:
        timestamp = self._aware(now or datetime.now(timezone.utc)).isoformat()
        with self.store._connect() as connection:
            changed = connection.execute(
                "UPDATE workflow_notification_outbox SET state='delivered', "
                "delivered_at=?, updated_at=?, lease_owner=NULL, lease_expires_at=NULL "
                "WHERE notification_id=? AND state='leased' AND lease_owner=?",
                (timestamp, timestamp, notification_id, owner_id),
            ).rowcount
        return changed == 1

    def fail(
        self,
        notification_id: str,
        *,
        owner_id: str,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        observed = self._aware(now or datetime.now(timezone.utc))
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM workflow_notification_outbox WHERE "
                "notification_id=? AND state='leased' AND lease_owner=?",
                (notification_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"])
            dead = attempts >= 8
            available = observed + timedelta(seconds=min(300, 2 ** min(attempts, 8)))
            connection.execute(
                "UPDATE workflow_notification_outbox SET state=?, available_at=?, "
                "updated_at=?, lease_owner=NULL, lease_expires_at=NULL, last_error=? "
                "WHERE notification_id=?",
                (
                    "dead" if dead else "pending",
                    available.isoformat(),
                    observed.isoformat(),
                    error[:512],
                    notification_id,
                ),
            )
        return True

    def dismiss(
        self, notification_id: str, *, owner_id: str, now: datetime | None = None
    ) -> bool:
        timestamp = self._aware(now or datetime.now(timezone.utc)).isoformat()
        with self.store._connect() as connection:
            changed = connection.execute(
                "UPDATE workflow_notification_outbox SET dismissed_at=?, updated_at=? "
                "WHERE notification_id=? AND (lease_owner=? OR state='delivered')",
                (timestamp, timestamp, notification_id, owner_id),
            ).rowcount
        return changed == 1

    def pending_attention(self, *, run_id: str | None = None) -> tuple[dict[str, object], ...]:
        clauses = [
            "kind IN ('approval_required','input_required','failure','stalled','reconciliation_required')",
            "state IN ('pending','leased','dead')",
        ]
        values: list[object] = []
        if run_id is not None:
            clauses.append("run_id=?")
            values.append(run_id)
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at",
                values,
            ).fetchall()
        return tuple(self._public(row) for row in rows)

    def history(self, *, run_id: str | None = None, limit: int = 200) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        where = " WHERE facts.run_id=?" if run_id else ""
        values = (run_id, limit) if run_id else (limit,)
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT facts.*, outbox.state, outbox.coalesced_count, "
                "outbox.created_at, outbox.updated_at, outbox.lease_owner, "
                "outbox.lease_expires_at, outbox.delivered_at, "
                "outbox.dismissed_at, outbox.attempts, outbox.last_error "
                "FROM workflow_notification_facts AS facts JOIN "
                "workflow_notification_outbox AS outbox USING(notification_id)"
                + where
                + " ORDER BY occurred_at, transition_key LIMIT ?",
                values,
            ).fetchall()
        return tuple(
            {
                **self._public(row),
                "transition_version": row["transition_version"],
                "payload": json.loads(str(row["payload_json"])),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        )

    @staticmethod
    def _public(row: Mapping[str, object], **updates: object) -> dict[str, object]:
        return {
            "notification_id": row["notification_id"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "destination": row["destination"],
            "transition_version": row["transition_version"],
            "coalesced_count": row["coalesced_count"],
            "payload": json.loads(str(row["payload_json"])),
            "state": updates.get("state", row["state"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "lease_owner": updates.get("lease_owner", row["lease_owner"]),
            "lease_expires_at": updates.get("lease_expires_at", row["lease_expires_at"]),
            "delivered_at": row["delivered_at"],
            "dismissed_at": row["dismissed_at"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
        }


__all__ = [
    "ATTENTION_KINDS",
    "COALESCED_KINDS",
    "NotificationOutbox",
    "install_notification_schema",
    "notification_kind",
]
