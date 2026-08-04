"""Durable workflow notification outbox and delivery receipts."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from plugins.workflow.evidence import (
    _UnsafeEvidencePath,
    _read_contained_regular_file,
)
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from plugins.workflow.sanitize import sanitize_projection


logger = logging.getLogger(__name__)


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
_GENERIC_DELIVERY_FAILURE_REASON = "notification delivery failed"
_STABLE_DELIVERY_FAILURE_REASONS = frozenset(
    {
        "adapter_send_failed",
        "adapter_send_timeout",
        "adapter_unavailable",
        "delivery_store_unavailable",
        "gateway_loop_unavailable",
        "invalid_text",
        "outcome_uncertain",
        "permanent_failure",
        "retryable_failure",
        "unauthorized",
    }
)


class NotificationReconciliationError(RuntimeError):
    """A run journal could not be safely corroborated for notifications."""


class _NotificationRepairPageFull(Exception):
    """The next safe journal would exceed this scanner iteration's budget."""


def _stable_delivery_failure_reason(error: object) -> str:
    if isinstance(error, str) and error in _STABLE_DELIVERY_FAILURE_REASONS:
        return error
    return _GENERIC_DELIVERY_FAILURE_REASON


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
        CREATE INDEX IF NOT EXISTS workflow_notification_dead_letter
        ON workflow_notification_outbox(updated_at, notification_id)
        WHERE state='dead';
        CREATE INDEX IF NOT EXISTS workflow_notification_retention
        ON workflow_notification_outbox(state, dismissed_at, delivered_at, notification_id);
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
        CREATE INDEX IF NOT EXISTS workflow_notification_fact_history
        ON workflow_notification_facts(
            run_id, occurred_at DESC, transition_key DESC
        );
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


def _value_free_notification_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Project notification data without durable raw recovery/error authority."""
    projected = sanitize_projection(dict(payload))
    if not isinstance(projected, dict):
        return {}
    sensitive_values: set[str] = set()

    def normalized_key(key: object) -> str:
        return "".join(
            character for character in str(key).lower() if character.isalnum()
        )

    def is_sensitive_key(key: object) -> bool:
        normalized = normalized_key(key)
        return (
            normalized.endswith("sessionid")
            or normalized.endswith("cachefingerprint")
            or normalized in {"sessionalias", "fingerprintalias"}
        ) and not normalized.endswith("sha256")

    to_collect: list[object] = [projected]
    while to_collect:
        current = to_collect.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if is_sensitive_key(key) and isinstance(value, str):
                    sensitive_values.add(value)
                to_collect.append(value)
        elif isinstance(current, list):
            to_collect.extend(current)

    pending: list[object] = [projected]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key in tuple(current):
                normalized = normalized_key(key)
                if is_sensitive_key(key) or normalized in {
                    "sessionregistryupdatecandidate",
                    "pendingsessionregistryupdate",
                    "pendingsessionregistryupdates",
                }:
                    current.pop(key, None)
                    continue
                if normalized == "lasterror":
                    current[key] = "workflow operation failed"
                    continue
                child = current[key]
                if isinstance(child, str):
                    for private_value in sorted(
                        sensitive_values, key=len, reverse=True
                    ):
                        if private_value:
                            child = child.replace(private_value, "[REDACTED]")
                    current[key] = child
                else:
                    pending.append(child)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                if isinstance(child, str):
                    for private_value in sorted(
                        sensitive_values, key=len, reverse=True
                    ):
                        if private_value:
                            child = child.replace(private_value, "[REDACTED]")
                    current[index] = child
                else:
                    pending.append(child)
    return projected


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

    @staticmethod
    def _authority_scope(authority_scope: str) -> str:
        if not isinstance(authority_scope, str) or not authority_scope.strip():
            raise ValueError("authority_scope must not be empty")
        normalized = authority_scope.strip()
        if len(normalized.encode("utf-8")) > 4096:
            raise ValueError("authority_scope is too large")
        return normalized

    @staticmethod
    def _record_decision_fact(
        connection: sqlite3.Connection,
        row: Mapping[str, object],
        *,
        decision: str,
        occurrence_key: str,
        payload: Mapping[str, object],
        occurred_at: str,
    ) -> None:
        safe_payload = json.dumps(
            _value_free_notification_payload(
                {"decision": decision, **dict(payload)}
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workflow_notification_facts ("
            "transition_key, notification_id, run_id, kind, destination, "
            "transition_version, payload_json, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"notification:{row['notification_id']}:{occurrence_key}",
                row["notification_id"],
                row["run_id"],
                row["kind"],
                row["destination"],
                row["transition_version"],
                safe_payload,
                occurred_at,
            ),
        )

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
        transition_destination = destination
        if destination.startswith("gateway:"):
            capability = destination.removeprefix("gateway:")
            transition_destination = (
                "gateway:sha256:" + hashlib.sha256(capability.encode()).hexdigest()
            )
        transition_key = (
            f"{run_id}:{kind}:{transition_version}:{transition_destination}"
        )
        safe_payload = json.dumps(
            _value_free_notification_payload(payload),
            sort_keys=True,
            separators=(",", ":"),
        )
        gateway_destination = self._gateway_destination(
            run_id,
            source_destination=destination,
            delivery_state=delivery_state,
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
                notification_id = str(duplicate["notification_id"])
                if gateway_destination is not None:
                    self.record(
                        run_id=run_id,
                        kind=kind,
                        destination=gateway_destination,
                        transition_version=transition_version,
                        payload=payload,
                        delivery_state=delivery_state,
                        now=observed,
                    )
                return notification_id
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
                    notification_id = str(candidate["notification_id"])
                    if gateway_destination is not None:
                        self.record(
                            run_id=run_id,
                            kind=kind,
                            destination=gateway_destination,
                            transition_version=transition_version,
                            payload=payload,
                            delivery_state=delivery_state,
                            now=observed,
                        )
                    return notification_id
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
        if gateway_destination is not None:
            self.record(
                run_id=run_id,
                kind=kind,
                destination=gateway_destination,
                transition_version=transition_version,
                payload=payload,
                delivery_state=delivery_state,
                now=observed,
            )
        return notification_id

    def _gateway_destination(
        self,
        run_id: str,
        *,
        source_destination: str,
        delivery_state: str,
    ) -> str | None:
        if source_destination != "desktop" or delivery_state != "pending":
            return None
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT provenance_json FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            provenance = json.loads(str(row["provenance_json"]))
        except (TypeError, ValueError):
            return None
        capability = provenance.get("return_route")
        if (
            provenance.get("assurance") != "verified_adapter"
            or not isinstance(capability, str)
            or not capability
        ):
            return None
        return f"gateway:{capability}"

    @staticmethod
    def _existing_transition_keys(
        connection: sqlite3.Connection,
        transition_keys,
    ) -> set[str]:
        keys = tuple(dict.fromkeys(str(key) for key in transition_keys))
        existing: set[str] = set()
        for start in range(0, len(keys), 500):
            batch = keys[start : start + 500]
            if not batch:
                continue
            placeholders = ",".join("?" for _ in batch)
            existing.update(
                str(row["transition_key"])
                for row in connection.execute(
                    "SELECT transition_key FROM workflow_notification_facts "
                    f"WHERE transition_key IN ({placeholders})",
                    batch,
                ).fetchall()
            )
        return existing

    def _journal_candidates(
        self,
        run_id: str,
        *,
        max_journal_bytes: int,
        page_bytes_remaining: int | None = None,
        allow_page_overrun: bool = False,
    ) -> tuple[list[dict[str, object]], int]:
        directory = self.store.run_directory(run_id)
        journal_path = Path(directory) / "events.jsonl"
        try:
            with workflow_lock(self.store._run_lock_path(run_id), timeout_seconds=0.05):
                _, reported_size = _read_contained_regular_file(
                    directory, journal_path, 0
                )
                if reported_size > max_journal_bytes:
                    raise NotificationReconciliationError(
                        "journal exceeds its enforced quota"
                    )
                if (
                    page_bytes_remaining is not None
                    and reported_size > page_bytes_remaining
                    and not allow_page_overrun
                ):
                    raise _NotificationRepairPageFull
                data, reported_size = _read_contained_regular_file(
                    directory, journal_path, max_journal_bytes + 1
                )
                if reported_size > max_journal_bytes:
                    raise NotificationReconciliationError(
                        "journal exceeds its enforced quota"
                    )
                events = self.store._verified_public_journal_events_locked(
                    directory,
                    run_id=run_id,
                    recover_torn_tail=False,
                    journal_data=data,
                )
        except NotificationReconciliationError:
            raise
        except _NotificationRepairPageFull:
            raise
        except WorkflowLockTimeout:
            raise
        except (
            _UnsafeEvidencePath,
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise NotificationReconciliationError(
                "journal could not be safely corroborated"
            ) from exc
        if not events:
            raise NotificationReconciliationError("journal contains no events")
        candidates: list[dict[str, object]] = []
        for event in events:
            projection = event.get("projection")
            if not isinstance(projection, Mapping):
                continue
            kind = notification_kind(str(event.get("event_type") or ""), projection)
            if kind is None:
                continue
            candidates.append(
                {
                    "transition_key": (
                        f"{run_id}:{kind}:{int(projection['state_version'])}:desktop"
                    ),
                    "run_id": run_id,
                    "kind": kind,
                    "transition_version": int(projection["state_version"]),
                    "projection": projection,
                    "event": event,
                }
            )
        return candidates, reported_size

    def _record_candidates(self, candidates: list[dict[str, object]]) -> int:
        with self.store._connect() as connection:
            existing = self._existing_transition_keys(
                connection,
                (candidate["transition_key"] for candidate in candidates),
            )
        repaired = 0
        for candidate in candidates:
            transition_key = str(candidate["transition_key"])
            if transition_key in existing:
                continue
            projection = candidate["projection"]
            event = candidate["event"]
            if not isinstance(projection, Mapping) or not isinstance(event, Mapping):
                continue
            timestamp = datetime.fromisoformat(str(event["timestamp"]))
            self.record(
                run_id=str(candidate["run_id"]),
                kind=str(candidate["kind"]),
                destination="desktop",
                transition_version=int(candidate["transition_version"]),
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
        return repaired

    def reconcile_run(
        self,
        run_id: str,
        *,
        max_journal_bytes: int | None = None,
    ) -> int:
        """Corroborate one complete bounded journal and repair missing facts."""
        enforced_limit = (
            int(self.store.max_journal_bytes)
            if max_journal_bytes is None
            else max_journal_bytes
        )
        if (
            isinstance(enforced_limit, bool)
            or not isinstance(enforced_limit, int)
            or enforced_limit < 1
        ):
            raise ValueError("max_journal_bytes must be a positive integer")
        candidates, _ = self._journal_candidates(
            run_id, max_journal_bytes=enforced_limit
        )
        return self._record_candidates(candidates)

    def reconcile_journal(
        self,
        *,
        limit_runs: int = 20,
        max_journal_bytes: int | None = None,
    ) -> int:
        """Idempotently repair a crash gap between journal and outbox writes."""
        if not 1 <= limit_runs <= 1000:
            raise ValueError("limit_runs must be between 1 and 1000")
        byte_budget = (
            int(self.store.max_journal_bytes)
            if max_journal_bytes is None
            else max_journal_bytes
        )
        if (
            isinstance(byte_budget, bool)
            or not isinstance(byte_budget, int)
            or byte_budget < 1
        ):
            raise ValueError("max_journal_bytes must be a positive integer")
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
        candidates: list[dict[str, object]] = []
        processed_rows = []
        consumed_bytes = 0
        for row in rows:
            run_id = str(row["run_id"])
            try:
                run_candidates, journal_bytes = self._journal_candidates(
                    run_id,
                    max_journal_bytes=int(self.store.max_journal_bytes),
                    page_bytes_remaining=byte_budget - consumed_bytes,
                    allow_page_overrun=not consumed_bytes,
                )
            except _NotificationRepairPageFull:
                break
            except WorkflowLockTimeout:
                timeout_count = self.store._note_notification_repair_timeout(run_id)
                if timeout_count == 3 or timeout_count % 10 == 0:
                    logger.warning(
                        "workflow notification repair lock contention run_id=%s "
                        "consecutive_timeouts=%d cursor retained",
                        run_id,
                        timeout_count,
                    )
                break
            except NotificationReconciliationError:
                self.store._transition_run_repair(
                    "notification_reconciliation_unverified",
                    run_id=run_id,
                    outcome="repair_required",
                )
                processed_rows.append(row)
                continue
            self.store._clear_notification_repair_timeout(run_id)
            self.store._transition_run_repair(
                "notification_reconciliation_unverified",
                run_id=run_id,
                outcome="repair_verified",
            )
            if not consumed_bytes and journal_bytes > byte_budget:
                logger.warning(
                    "workflow notification repair processing bounded oversized "
                    "journal run_id=%s journal_bytes=%d byte_budget=%d",
                    row["run_id"],
                    journal_bytes,
                    byte_budget,
                )
            consumed_bytes += journal_bytes
            processed_rows.append(row)
            candidates.extend(run_candidates)
        repaired = self._record_candidates(candidates)
        if processed_rows:
            last = processed_rows[-1]
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

    def lease_gateway(
        self,
        *,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: float = 30,
        limit: int = 20,
    ) -> tuple[dict[str, object], ...]:
        """Lease pending opaque Gateway projections without resolving routes."""
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
                "AND destination LIKE 'gateway:%' AND lease_expires_at<=?",
                (timestamp,),
            )
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                "destination LIKE 'gateway:%' AND state='pending' "
                "AND available_at<=? ORDER BY created_at, notification_id LIMIT ?",
                (timestamp, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE workflow_notification_outbox SET state='leased', "
                    "lease_owner=?, lease_expires_at=?, attempts=attempts+1, "
                    "updated_at=? WHERE notification_id=? AND state='pending'",
                    (owner_id, expires, timestamp, row["notification_id"]),
                )
            connection.commit()
        return tuple(
            self._public(
                row,
                include_gateway_capability=True,
                lease_owner=owner_id,
                lease_expires_at=expires,
            )
            for row in rows
        )

    def terminal_fail(
        self,
        notification_id: str,
        *,
        owner_id: str,
        error: str,
        outcome_uncertain: bool = False,
        now: datetime | None = None,
    ) -> bool:
        """Durably stop a delivery that is unsafe or pointless to replay."""
        observed = self._aware(now or datetime.now(timezone.utc))
        failure_reason = _stable_delivery_failure_reason(error)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                "notification_id=? AND state='leased' AND lease_owner=?",
                (notification_id, owner_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            decision = (
                "delivery_outcome_uncertain"
                if outcome_uncertain
                else "terminal_dead_letter"
            )
            connection.execute(
                "UPDATE workflow_notification_outbox SET state='dead', "
                "updated_at=?, lease_owner=NULL, lease_expires_at=NULL, "
                "last_error=? WHERE notification_id=?",
                (observed.isoformat(), failure_reason, notification_id),
            )
            self._record_decision_fact(
                connection,
                row,
                decision=decision,
                occurrence_key=f"{decision}:{uuid.uuid4().hex}",
                payload={
                    "error": failure_reason,
                    "attempts": row["attempts"],
                },
                occurred_at=observed.isoformat(),
            )
            connection.commit()
        return True

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
        failure_reason = _stable_delivery_failure_reason(error)
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
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
                    failure_reason,
                    notification_id,
                ),
            )
            if dead:
                self._record_decision_fact(
                    connection,
                    row,
                    decision="terminal_dead_letter",
                    occurrence_key=f"terminal-dead:{attempts}:{uuid.uuid4().hex}",
                    payload={
                        "attempts": attempts,
                        "error": failure_reason,
                    },
                    occurred_at=observed.isoformat(),
                )
            connection.commit()
        return True

    def retry_dead(
        self,
        notification_id: str,
        authority_scope: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        scope = self._authority_scope(authority_scope)
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_notification_outbox "
                "WHERE notification_id=? AND state='dead'",
                (notification_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            attempts = int(row["attempts"])
            self._record_decision_fact(
                connection,
                row,
                decision="dead_letter_retried",
                occurrence_key=f"dead-retry:{attempts}:{uuid.uuid4().hex}",
                payload={
                    "authority_scope": scope,
                    "previous_attempts": attempts,
                    "previous_error": row["last_error"],
                },
                occurred_at=timestamp,
            )
            connection.execute(
                "UPDATE workflow_notification_outbox SET state='pending', "
                "available_at=?, updated_at=?, lease_owner=NULL, "
                "lease_expires_at=NULL, attempts=0, last_error=NULL "
                "WHERE notification_id=? AND state='dead'",
                (timestamp, timestamp, notification_id),
            )
            connection.commit()
        return True

    def prune_deliveries(
        self,
        *,
        older_than: timedelta,
        authority_scope: str,
        limit: int = 200,
        now: datetime | None = None,
    ) -> int:
        scope = self._authority_scope(authority_scope)
        if not isinstance(older_than, timedelta) or older_than < timedelta(0):
            raise ValueError("older_than must be a non-negative duration")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        cutoff = (observed - older_than).isoformat()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                "(state='delivered' OR dismissed_at IS NOT NULL) AND "
                "COALESCE(dismissed_at, delivered_at, updated_at)<=? "
                "ORDER BY COALESCE(dismissed_at, delivered_at, updated_at), "
                "notification_id LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            for row in rows:
                self._record_decision_fact(
                    connection,
                    row,
                    decision="delivery_pruned",
                    occurrence_key=f"delivery-pruned:{timestamp}",
                    payload={
                        "authority_scope": scope,
                        "delivery_state": row["state"],
                        "delivered_at": row["delivered_at"],
                        "dismissed_at": row["dismissed_at"],
                    },
                    occurred_at=timestamp,
                )
                connection.execute(
                    "DELETE FROM workflow_notification_outbox "
                    "WHERE notification_id=? AND "
                    "(state='delivered' OR dismissed_at IS NOT NULL)",
                    (row["notification_id"],),
                )
            connection.commit()
        return len(rows)

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

    def pending_attention_page(
        self,
        *,
        limit: int,
        observed_at: datetime,
        before: tuple[str, str, str] | None = None,
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Return a bounded newest-first keyset page of attention deliveries."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        observed = self._aware(observed_at)
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 3
            or not all(isinstance(value, str) and value for value in before)
        ):
            raise ValueError(
                "before must be an updated_at/run_id/notification_id tuple"
            )
        clauses = [
            "outbox.kind IN ('approval_required','input_required','failure','stalled','reconciliation_required')",
            "outbox.state IN ('pending','leased','dead')",
            "outbox.updated_at<=?",
        ]
        values: list[object] = [observed.isoformat()]
        if operator_scope is not None:
            clauses.append("runs.operator_scope_digest=?")
            values.append(self.store._scope_digest(operator_scope))
        if before is not None:
            clauses.append(
                "(outbox.updated_at<? OR "
                "(outbox.updated_at=? AND outbox.run_id<?) OR "
                "(outbox.updated_at=? AND outbox.run_id=? "
                "AND outbox.notification_id<?))"
            )
            values.extend(
                (
                    before[0],
                    before[0],
                    before[1],
                    before[0],
                    before[1],
                    before[2],
                )
            )
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT outbox.* FROM workflow_notification_outbox AS outbox "
                "JOIN runs ON runs.run_id=outbox.run_id WHERE "
                + " AND ".join(clauses)
                + " ORDER BY outbox.updated_at DESC, outbox.run_id DESC, "
                "outbox.notification_id DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
        return tuple(self._public(row) for row in rows)

    def history(
        self,
        *,
        run_id: str | None = None,
        limit: int = 200,
        before: tuple[str, str] | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 2
            or not all(isinstance(value, str) and value for value in before)
        ):
            raise ValueError("before must be an occurred_at/transition_key tuple")
        clauses = []
        values: list[object] = []
        if run_id is not None:
            clauses.append("facts.run_id=?")
            values.append(run_id)
        if before is not None:
            clauses.append(
                "(facts.occurred_at<? OR "
                "(facts.occurred_at=? AND facts.transition_key<?))"
            )
            values.extend((before[0], before[0], before[1]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(limit)
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT facts.*, COALESCE(outbox.state, 'pruned') AS state, "
                "COALESCE(outbox.coalesced_count, 1) AS coalesced_count, "
                "COALESCE(outbox.created_at, facts.occurred_at) AS created_at, "
                "COALESCE(outbox.updated_at, facts.occurred_at) AS updated_at, "
                "outbox.lease_owner, "
                "outbox.lease_expires_at, outbox.delivered_at, "
                "outbox.dismissed_at, COALESCE(outbox.attempts, 0) AS attempts, "
                "outbox.last_error "
                "FROM workflow_notification_facts AS facts LEFT JOIN "
                "workflow_notification_outbox AS outbox USING(notification_id)"
                + where
                + " ORDER BY facts.occurred_at DESC, "
                "facts.transition_key DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return tuple(
            {
                **self._public(row),
                "transition_version": row["transition_version"],
                "payload": json.loads(str(row["payload_json"])),
                "occurred_at": row["occurred_at"],
                "transition_key": row["transition_key"],
            }
            for row in rows
        )

    @staticmethod
    def _public(
        row: Mapping[str, object],
        *,
        include_gateway_capability: bool = False,
        **updates: object,
    ) -> dict[str, object]:
        destination = str(row["destination"])
        if destination.startswith("gateway:") and not include_gateway_capability:
            destination = "gateway:opaque"
        return {
            "notification_id": row["notification_id"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "destination": destination,
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
