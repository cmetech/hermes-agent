"""Durable workflow notification outbox and delivery receipts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from plugins.workflow.actions import WIRE_ACTIONS, available_actions
from plugins.workflow.evidence import (
    _UnsafeEvidencePath,
    _read_contained_regular_file,
)
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from plugins.workflow.sanitize import public_display_identifier


logger = logging.getLogger(__name__)


COALESCED_KINDS = frozenset({"failure", "stalled", "retry"})
PUBLIC_NOTIFICATION_KINDS = frozenset(
    {
        "approval_required",
        "input_required",
        "failure",
        "stalled",
        "reconciliation_required",
        "completion",
        "cancellation",
        "retry",
    }
)
ATTENTION_KINDS = frozenset(
    {
        "approval_required",
        "input_required",
        "failure",
        "stalled",
        "reconciliation_required",
    }
)
_PUBLIC_NOTIFICATION_STATUSES = frozenset(
    {
        "queued",
        "running",
        "waiting_retry",
        "paused",
        "recovery_pending",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "abandoned",
    }
)
_PUBLIC_NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "node_reconciliation_required",
        "run_reconciliation_required",
        "cancel_reconciliation_required",
        "run_failed",
        "cleanup_failed",
        "run_succeeded",
        "run_cancelled",
        "run_retry_waiting",
        "node_retry_scheduled",
        "run_stalled",
        "coordinator_stalled",
        "workflow_approval_required",
        "node_approval_required",
        "loop_input_required",
        "loop_signal_confirmation_required",
        "run_paused",
    }
)
_PUBLIC_NOTIFICATION_INTERACTION_TYPES = frozenset(
    {
        "approval",
        "workflow_approval",
        "loop_input",
        "loop_signal_confirmation",
        "reconcile",
    }
)
_PUBLIC_NOTIFICATION_CODES = frozenset(
    {
        "cleanup_failed",
        "host_pressure",
        "persistent_session_registry_update_pending",
        "provider_capability_drift",
        "schedule_overlap_forbidden",
        "schedule_revalidation_failed",
        "workflow_operation_failed",
    }
)
_PUBLIC_RUNTIME_MISMATCH_FIELDS = frozenset(
    {
        "provider",
        "model",
        "api_mode",
        "base_url_trust_class",
        "endpoint_sha256",
        "registration_provenance_digest",
    }
)
_PUBLIC_NOTIFICATION_PAYLOAD_TYPES = frozenset(
    {"workflow_transition", "delivery_decision", "projection_recovery"}
)
_PUBLIC_NOTIFICATION_DECISIONS = frozenset(
    {
        "terminal_dead_letter",
        "delivery_outcome_uncertain",
        "dead_letter_retried",
        "delivery_pruned",
    }
)
_PUBLIC_NOTIFICATION_STATES = frozenset(
    {"pending", "suppressed", "leased", "delivered", "dead", "pruned"}
)
_PUBLIC_NOTIFICATION_DESTINATIONS = frozenset({"desktop", "gateway:opaque"})
_PUBLIC_LOGICAL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")
_PUBLIC_NOTIFICATION_JSON_MAX_CHARS = 16_384
_WORKFLOW_PAYLOAD_FIELDS = frozenset(
    {
        "payload_type",
        "workflow",
        "status",
        "event_type",
        "node_id",
        "interaction",
        "code",
        "mismatched_fields",
        "state_version",
        "next_actions",
    }
)
_DELIVERY_PAYLOAD_FIELDS = frozenset(
    {
        "payload_type",
        "decision",
        "error",
        "attempts",
        "previous_attempts",
        "previous_error",
        "authority_scope",
        "delivery_state",
        "delivered_at",
        "dismissed_at",
        "state_version",
        "next_actions",
    }
)
_RECOVERY_PAYLOAD_FIELDS = frozenset(
    {"payload_type", "code", "state_version", "next_actions"}
)
_GENERIC_DELIVERY_FAILURE_REASON = "notification delivery failed"
_STABLE_DELIVERY_FAILURE_REASONS = frozenset(
    {
        "adapter_send_failed",
        "adapter_send_timeout",
        "adapter_unavailable",
        "bad_format",
        "delivery_store_unavailable",
        "forbidden",
        "gateway_loop_unavailable",
        "invalid_text",
        "not_found",
        "outcome_uncertain",
        "permanent_failure",
        "projection_failed",
        "rate_limited",
        "retryable_failure",
        "too_long",
        "transient",
        "unauthorized",
        "unknown",
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


def projected_pending_interaction(
    projection: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Extract the one public pending interaction from its node projection."""
    pending = projection.get("pending_interaction")
    if isinstance(pending, Mapping):
        return pending
    nodes = projection.get("nodes")
    if not isinstance(nodes, Mapping):
        return None
    return next(
        (
            candidate
            for node in nodes.values()
            if isinstance(node, Mapping)
            and isinstance(
                candidate := node.get("pending_interaction"), Mapping
            )
        ),
        None,
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
    if event_type in {"loop_signal_confirmation_required"}:
        return "approval_required"
    if event_type == "run_paused":
        pending = projected_pending_interaction(projection)
        pending_type = pending.get("type") if isinstance(pending, Mapping) else None
        if pending_type in {"approval", "workflow_approval"}:
            return "approval_required"
        if pending_type == "loop_input":
            return "input_required"
        if pending_type == "loop_signal_confirmation":
            return "approval_required"
        if pending_type == "reconcile":
            return "reconciliation_required"
    return None


def _bounded_int(value: object, *, minimum: int = 0, maximum: int = 1_000_000) -> int | None:
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


def _public_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}", value):
        return value
    projected = public_display_identifier(value)
    if _logical_identifier(projected, maximum_bytes=128) is not None:
        return projected
    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"redacted:{digest[:16]}"


def _logical_identifier(value: object, *, maximum_bytes: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > maximum_bytes or _PUBLIC_LOGICAL_IDENTIFIER.fullmatch(value) is None:
        return None
    return value


def _public_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _closed_actions(value: object) -> list[str] | None:
    if not isinstance(value, list) or len(value) > len(WIRE_ACTIONS):
        return None
    actions: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in WIRE_ACTIONS or item in actions:
            return None
        actions.append(item)
    return actions


def _recovery_actions(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    actions: list[str] = []
    for item in value[: len(WIRE_ACTIONS)]:
        if isinstance(item, str) and item in WIRE_ACTIONS and item not in actions:
            actions.append(item)
    return actions


def _closed_interaction(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    interaction_type = value.get("type")
    if interaction_type not in _PUBLIC_NOTIFICATION_INTERACTION_TYPES:
        return None
    projected: dict[str, object] = {"type": interaction_type}
    interaction_id = _public_identifier(value.get("interaction_id"))
    if interaction_id is not None:
        projected["interaction_id"] = interaction_id
    iteration = _bounded_int(value.get("iteration"), minimum=1, maximum=100)
    maximum = _bounded_int(value.get("max_iterations"), minimum=1, maximum=100)
    if iteration is not None and maximum is not None and iteration <= maximum:
        projected["iteration"] = iteration
        projected["max_iterations"] = maximum
    return projected


def _workflow_notification_payload(
    payload: Mapping[str, object],
    *,
    transition_version: int,
) -> dict[str, object]:
    """Build one closed workflow-transition payload without visiting unknown fields."""
    projected: dict[str, object] = {"payload_type": "workflow_transition"}
    workflow = _public_identifier(payload.get("workflow"))
    if workflow is not None:
        projected["workflow"] = workflow
    status = payload.get("status")
    if isinstance(status, str) and status in _PUBLIC_NOTIFICATION_STATUSES:
        projected["status"] = status
    event_type = payload.get("event_type")
    if isinstance(event_type, str) and event_type in _PUBLIC_NOTIFICATION_EVENT_TYPES:
        projected["event_type"] = event_type
    node_id = _public_identifier(payload.get("node_id"))
    if node_id is not None:
        projected["node_id"] = node_id
    raw_interaction = payload.get("interaction")
    interaction = _closed_interaction(raw_interaction)
    if interaction is not None:
        projected["interaction"] = interaction
    raw_code = payload.get("code")
    last_error = payload.get("last_error")
    if raw_code is None and isinstance(last_error, Mapping):
        raw_code = last_error.get("code")
    if isinstance(raw_code, str):
        projected["code"] = (
            raw_code
            if raw_code in _PUBLIC_NOTIFICATION_CODES
            else "workflow_operation_failed"
        )
    mismatches = payload.get("mismatched_fields")
    if isinstance(mismatches, list):
        safe_mismatches = [
            item
            for item in mismatches[: len(_PUBLIC_RUNTIME_MISMATCH_FIELDS)]
            if isinstance(item, str) and item in _PUBLIC_RUNTIME_MISMATCH_FIELDS
        ]
        if safe_mismatches:
            projected["mismatched_fields"] = list(dict.fromkeys(safe_mismatches))
    projected["state_version"] = transition_version
    if isinstance(status, str) and status in _PUBLIC_NOTIFICATION_STATUSES:
        projected["next_actions"] = available_actions(status, raw_interaction)
    else:
        projected["next_actions"] = ["status", "events"]
    return projected


def _delivery_decision_payload(
    decision: object,
    payload: Mapping[str, object],
    *,
    transition_version: int,
) -> dict[str, object]:
    if not isinstance(decision, str) or decision not in _PUBLIC_NOTIFICATION_DECISIONS:
        return _projection_recovery_payload(transition_version=transition_version)
    projected: dict[str, object] = {
        "payload_type": "delivery_decision",
        "decision": decision,
        "state_version": transition_version,
        "next_actions": [],
    }
    for field in ("attempts", "previous_attempts"):
        count = _bounded_int(payload.get(field))
        if count is not None:
            projected[field] = count
    for field in ("error", "previous_error"):
        value = payload.get(field)
        if isinstance(value, str):
            projected[field] = _stable_delivery_failure_reason(value)
    scope = _logical_identifier(payload.get("authority_scope"))
    if scope is not None:
        projected["authority_scope"] = scope
    delivery_state = payload.get("delivery_state")
    if isinstance(delivery_state, str) and delivery_state in _PUBLIC_NOTIFICATION_STATES:
        projected["delivery_state"] = delivery_state
    for field in ("delivered_at", "dismissed_at"):
        value = _public_timestamp(payload.get(field))
        if value is not None:
            projected[field] = value
    return projected


def _projection_recovery_payload(
    *,
    transition_version: int,
    actions: object = None,
) -> dict[str, object]:
    return {
        "payload_type": "projection_recovery",
        "code": "notification_projection_invalid",
        "state_version": transition_version,
        "next_actions": _recovery_actions(actions),
    }


def _decode_notification_payload(
    raw_payload: object,
    *,
    transition_version: int,
) -> dict[str, object] | None:
    """Decode an exact persisted public payload without retaining unknown fields."""
    if not isinstance(raw_payload, dict):
        return None
    payload_type = raw_payload.get("payload_type")
    if payload_type not in _PUBLIC_NOTIFICATION_PAYLOAD_TYPES:
        return None
    allowed_fields = {
        "workflow_transition": _WORKFLOW_PAYLOAD_FIELDS,
        "delivery_decision": _DELIVERY_PAYLOAD_FIELDS,
        "projection_recovery": _RECOVERY_PAYLOAD_FIELDS,
    }[payload_type]
    if not set(raw_payload).issubset(allowed_fields):
        return None
    if (
        type(raw_payload.get("state_version")) is not int
        or raw_payload.get("state_version") != transition_version
    ):
        return None
    actions = _closed_actions(raw_payload.get("next_actions"))
    if actions is None:
        return None
    if payload_type == "projection_recovery":
        if raw_payload.get("code") != "notification_projection_invalid":
            return None
        return _projection_recovery_payload(
            transition_version=transition_version,
            actions=actions,
        )
    if payload_type == "delivery_decision":
        decision = raw_payload.get("decision")
        if decision not in _PUBLIC_NOTIFICATION_DECISIONS:
            return None
        rebuilt = _delivery_decision_payload(
            decision,
            raw_payload,
            transition_version=transition_version,
        )
        if rebuilt != raw_payload:
            return None
        return rebuilt
    rebuilt = _workflow_notification_payload(
        raw_payload,
        transition_version=transition_version,
    )
    rebuilt["next_actions"] = actions
    if rebuilt != raw_payload:
        return None
    return rebuilt


def _load_notification_payload(
    raw: object,
    *,
    transition_version: int,
) -> tuple[dict[str, object], bool]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate notification payload key")
            result[key] = value
        return result

    if not isinstance(raw, str) or len(raw) > _PUBLIC_NOTIFICATION_JSON_MAX_CHARS:
        loaded = None
    else:
        try:
            loaded = json.loads(
                raw,
                object_pairs_hook=unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except (RecursionError, TypeError, ValueError):
            loaded = None
    decoded = _decode_notification_payload(
        loaded,
        transition_version=transition_version,
    )
    if decoded is not None:
        return decoded, True
    actions = loaded.get("next_actions") if isinstance(loaded, dict) else None
    return (
        _projection_recovery_payload(
            transition_version=transition_version,
            actions=actions,
        ),
        False,
    )


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
        normalized = _logical_identifier(authority_scope)
        if normalized is None:
            raise ValueError("authority_scope must be a bounded logical identifier")
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
            _delivery_decision_payload(
                decision,
                payload,
                transition_version=int(row["transition_version"]),
            ),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
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
        if _logical_identifier(run_id) is None:
            raise ValueError("run_id must be a bounded logical identifier")
        if destination != "desktop" and not destination.startswith("gateway:"):
            raise ValueError("destination must be desktop or a gateway capability")
        if destination.startswith("gateway:"):
            capability = destination.removeprefix("gateway:")
            if (
                not capability
                or len(capability.encode("utf-8", errors="surrogatepass")) > 4096
                or any(
                    ord(character) < 0x20 or ord(character) == 0x7F
                    for character in capability
                )
            ):
                raise ValueError("gateway capability must be non-empty and bounded")
        if type(transition_version) is not int or not 0 <= transition_version <= 1_000_000_000:
            raise ValueError("transition_version must be a bounded integer")
        safe_kind = kind if kind in PUBLIC_NOTIFICATION_KINDS else "reconciliation_required"
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        transition_destination = destination
        if destination.startswith("gateway:"):
            capability = destination.removeprefix("gateway:")
            transition_destination = (
                "gateway:sha256:" + hashlib.sha256(capability.encode()).hexdigest()
            )
        transition_key = (
            f"{run_id}:{safe_kind}:{transition_version}:{transition_destination}"
        )
        projected_payload = (
            _workflow_notification_payload(
                payload,
                transition_version=transition_version,
            )
            if kind in PUBLIC_NOTIFICATION_KINDS
            else _projection_recovery_payload(transition_version=transition_version)
        )
        safe_payload = json.dumps(
            projected_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
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
            if safe_kind in COALESCED_KINDS:
                window = (observed - timedelta(seconds=60)).isoformat()
                candidate = connection.execute(
                    "SELECT notification_id FROM workflow_notification_outbox "
                    "WHERE run_id=? AND kind=? AND destination=? AND state='pending' "
                    "AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                    (run_id, safe_kind, destination, window),
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
                            safe_kind,
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
                    safe_kind,
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
                    safe_kind,
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
                    "interaction": projected_pending_interaction(projection),
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
            self._private_gateway_delivery(
                row,
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
                "occurred_at": _public_timestamp(row["occurred_at"]),
                "transition_key": self._public_transition_key(
                    row["transition_key"]
                ),
            }
            for row in rows
        )

    @staticmethod
    def _public_transition_key(value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > 1024:
            return "redacted:invalid-transition"
        projected = value
        if ":gateway:" in projected:
            projected = projected.partition(":gateway:")[0] + ":gateway:opaque"
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in projected):
            return "redacted:invalid-transition"
        return projected

    @classmethod
    def _private_gateway_delivery(
        cls,
        row: Mapping[str, object],
        **updates: object,
    ) -> dict[str, object]:
        """Add the opaque delivery capability only to the coordinator-owned DTO."""
        public = cls._public(row, **updates)
        destination = str(row["destination"])
        capability = destination.removeprefix("gateway:")
        public["delivery_capability"] = capability
        return public

    @staticmethod
    def _public(
        row: Mapping[str, object],
        **updates: object,
    ) -> dict[str, object]:
        transition_version = _bounded_int(
            row["transition_version"], maximum=1_000_000_000
        )
        if transition_version is None:
            transition_version = 0
        payload, payload_valid = _load_notification_payload(
            row["payload_json"],
            transition_version=transition_version,
        )
        raw_kind = row["kind"]
        kind = (
            raw_kind
            if payload_valid
            and payload["payload_type"] != "projection_recovery"
            and raw_kind in PUBLIC_NOTIFICATION_KINDS
            else "reconciliation_required"
        )
        destination = str(row["destination"])
        if destination.startswith("gateway:"):
            destination = "gateway:opaque"
        if destination not in _PUBLIC_NOTIFICATION_DESTINATIONS:
            destination = "desktop"
        notification_id = _logical_identifier(row["notification_id"])
        run_id = _logical_identifier(row["run_id"])
        state_value = updates.get("state", row["state"])
        state = (
            state_value
            if isinstance(state_value, str) and state_value in _PUBLIC_NOTIFICATION_STATES
            else "dead"
        )
        lease_owner_value = updates.get("lease_owner", row["lease_owner"])
        lease_owner = _logical_identifier(lease_owner_value)
        last_error_value = row["last_error"]
        last_error = (
            _stable_delivery_failure_reason(last_error_value)
            if isinstance(last_error_value, str)
            else None
        )
        return {
            "notification_id": notification_id or "redacted:invalid-notification",
            "run_id": run_id or "redacted:invalid-run",
            "kind": kind,
            "destination": destination,
            "transition_version": transition_version,
            "coalesced_count": _bounded_int(
                row["coalesced_count"], minimum=1
            )
            or 1,
            "payload": payload,
            "state": state,
            "created_at": _public_timestamp(row["created_at"]),
            "updated_at": _public_timestamp(row["updated_at"]),
            "lease_owner": lease_owner,
            "lease_expires_at": _public_timestamp(
                updates.get("lease_expires_at", row["lease_expires_at"])
            ),
            "delivered_at": _public_timestamp(row["delivered_at"]),
            "dismissed_at": _public_timestamp(row["dismissed_at"]),
            "attempts": _bounded_int(row["attempts"]) or 0,
            "last_error": last_error,
        }


__all__ = [
    "ATTENTION_KINDS",
    "COALESCED_KINDS",
    "PUBLIC_NOTIFICATION_KINDS",
    "NotificationOutbox",
    "install_notification_schema",
    "notification_kind",
]
