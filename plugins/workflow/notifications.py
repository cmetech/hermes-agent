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

from hermes_cli.handoff import HandoffEndpoint
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
        "handoff_deadline_exceeded",
        "handoff_failed",
        "handoff_indeterminate",
        "handoff_observed",
        "handoff_terminal",
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
        "handoff",
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
_PUBLIC_HANDOFF_FAILURE_CODES = frozenset(
    {
        "authentication_failed",
        "capability_mismatch",
        "cancellation_indeterminate",
        "channel_unavailable",
        "deadline_exceeded",
        "destination_busy",
        "endpoint_invalid",
        "endpoint_unavailable",
        "handoff_attention_required",
        "handoff_deadline_exceeded",
        "handoff_indeterminate",
        "handoff_remote_failed",
        "identity_unverified",
        "integrity_failed",
        "local_cli_failed",
        "local_cli_process_lost",
        "local_cli_timeout",
        "local_cli_wrapper_error",
        "needs_input",
        "observation_retryable",
        "output_invalid",
        "policy_denied",
        "protocol_violation",
        "remote_failed",
        "return_delivery_failed",
        "run_interrupted",
        "run_status_unknown",
        "submission_indeterminate",
        "submission_rejected",
        "supervisor_unhealthy",
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


def _persisted_handoff_identity(
    row: sqlite3.Row,
) -> tuple[tuple[str, str, int], dict[str, object]] | None:
    transition_version = _bounded_int(
        row["transition_version"],
        maximum=1_000_000_000,
    )
    if transition_version is None:
        return None
    payload, valid = _load_notification_payload(
        row["payload_json"],
        transition_version=transition_version,
    )
    handoff = payload.get("handoff") if valid else None
    if not isinstance(handoff, Mapping):
        return None
    return (
        (
            str(handoff["node_id"]),
            str(handoff["handoff_id"]),
            int(handoff["generation"]),
        ),
        payload,
    )


def _notification_lease_is_active(
    row: sqlite3.Row,
    *,
    observed_at: datetime,
) -> bool:
    if row["state"] != "leased":
        return False
    lease_expires_at = _public_timestamp(row["lease_expires_at"])
    return (
        lease_expires_at is not None
        and datetime.fromisoformat(lease_expires_at) > observed_at
    )


def _consolidate_handoff_notifications(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    identity: tuple[str, str, int],
    observed_at: datetime,
) -> str:
    earliest = min(
        rows,
        key=lambda row: (str(row["created_at"]), str(row["notification_id"])),
    )
    latest = max(
        rows,
        key=lambda row: (
            int(row["transition_version"]),
            str(row["updated_at"]),
            str(row["notification_id"]),
        ),
    )
    delivered = [row for row in rows if row["state"] == "delivered"]
    active_leases = [
        row
        for row in rows
        if _notification_lease_is_active(row, observed_at=observed_at)
    ]
    canonical = max(
        delivered or active_leases or [earliest],
        key=lambda row: (str(row["updated_at"]), str(row["notification_id"])),
    )
    canonical_id = str(canonical["notification_id"])
    duplicate_ids = [
        str(row["notification_id"])
        for row in rows
        if row["notification_id"] != canonical_id
    ]
    if duplicate_ids:
        connection.executemany(
            "UPDATE workflow_notification_facts SET notification_id=? "
            "WHERE notification_id=?",
            ((canonical_id, notification_id) for notification_id in duplicate_ids),
        )
        connection.executemany(
            "DELETE FROM workflow_notification_outbox WHERE notification_id=?",
            ((notification_id,) for notification_id in duplicate_ids),
        )
    latest_payload, valid = _load_notification_payload(
        latest["payload_json"],
        transition_version=int(latest["transition_version"]),
    )
    if not valid:
        raise sqlite3.IntegrityError("validated handoff payload became invalid")
    connection.execute(
        "UPDATE workflow_notification_outbox SET transition_key=?, "
        "transition_version=?, coalesced_count=?, payload_json=?, updated_at=?, "
        "state=?, available_at=?, lease_owner=?, lease_expires_at=?, attempts=?, "
        "delivered_at=?, dismissed_at=?, last_error=?, "
        "handoff_node_id=?, handoff_id=?, handoff_generation=? "
        "WHERE notification_id=?",
        (
            latest["transition_key"],
            latest["transition_version"],
            sum(
                _bounded_int(row["coalesced_count"], minimum=1) or 1
                for row in rows
            ),
            json.dumps(
                latest_payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            latest["updated_at"],
            canonical["state"],
            canonical["available_at"],
            canonical["lease_owner"],
            canonical["lease_expires_at"],
            canonical["attempts"],
            canonical["delivered_at"],
            canonical["dismissed_at"],
            canonical["last_error"],
            *identity,
            canonical_id,
        ),
    )
    return canonical_id


def _migrate_notification_handoff_identity(
    connection: sqlite3.Connection,
) -> None:
    observed_at = datetime.now(timezone.utc)
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(workflow_notification_outbox)"
        )
    }
    for name, column_type in (
        ("handoff_node_id", "TEXT"),
        ("handoff_id", "TEXT"),
        ("handoff_generation", "INTEGER"),
    ):
        if name not in columns:
            connection.execute(
                "ALTER TABLE workflow_notification_outbox "
                f"ADD COLUMN {name} {column_type}"
            )
    index_row = next(
        (
            row
            for row in connection.execute(
                "PRAGMA index_list(workflow_notification_outbox)"
            )
            if row["name"] == "workflow_notification_handoff_identity"
        ),
        None,
    )
    full_validation = index_row is None or int(index_row["unique"]) != 1
    if index_row is not None and int(index_row["unique"]) != 1:
        connection.execute("DROP INDEX workflow_notification_handoff_identity")
    if full_validation:
        rows = connection.execute(
            "SELECT * FROM workflow_notification_outbox"
        ).fetchall()
        connection.execute(
            "UPDATE workflow_notification_outbox SET handoff_node_id=NULL, "
            "handoff_id=NULL, handoff_generation=NULL"
        )
        groups: dict[tuple[str, str, str, str, str, int], list[sqlite3.Row]] = {}
        for row in rows:
            validated = _persisted_handoff_identity(row)
            if validated is None:
                continue
            identity, _payload = validated
            key = (
                str(row["run_id"]),
                str(row["kind"]),
                str(row["destination"]),
                *identity,
            )
            groups.setdefault(key, []).append(row)
        for key, group in groups.items():
            _consolidate_handoff_notifications(
                connection,
                group,
                identity=(key[3], key[4], key[5]),
                observed_at=observed_at,
            )
    else:
        rows = connection.execute(
            "SELECT * FROM workflow_notification_outbox "
            "WHERE handoff_node_id IS NULL OR handoff_id IS NULL "
            "OR handoff_generation IS NULL"
        ).fetchall()
        for stale_row in rows:
            current = connection.execute(
                "SELECT * FROM workflow_notification_outbox "
                "WHERE notification_id=?",
                (stale_row["notification_id"],),
            ).fetchone()
            if current is None:
                continue
            validated = _persisted_handoff_identity(current)
            if validated is None:
                if any(
                    current[field] is not None
                    for field in (
                        "handoff_node_id",
                        "handoff_id",
                        "handoff_generation",
                    )
                ):
                    connection.execute(
                        "UPDATE workflow_notification_outbox SET "
                        "handoff_node_id=NULL, handoff_id=NULL, "
                        "handoff_generation=NULL WHERE notification_id=?",
                        (current["notification_id"],),
                    )
                continue
            identity, _payload = validated
            existing = connection.execute(
                "SELECT * FROM workflow_notification_outbox "
                "WHERE run_id=? AND kind=? AND destination=? "
                "AND handoff_node_id=? AND handoff_id=? "
                "AND handoff_generation=? AND notification_id<>?",
                (
                    current["run_id"],
                    current["kind"],
                    current["destination"],
                    *identity,
                    current["notification_id"],
                ),
            ).fetchone()
            group = [current]
            if existing is not None:
                existing_validation = _persisted_handoff_identity(existing)
                if existing_validation is None or existing_validation[0] != identity:
                    connection.execute(
                        "UPDATE workflow_notification_outbox SET "
                        "handoff_node_id=NULL, handoff_id=NULL, "
                        "handoff_generation=NULL WHERE notification_id=?",
                        (existing["notification_id"],),
                    )
                else:
                    group.append(existing)
            _consolidate_handoff_notifications(
                connection,
                group,
                identity=identity,
                observed_at=observed_at,
            )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "workflow_notification_handoff_identity "
        "ON workflow_notification_outbox("
        "run_id, kind, destination, handoff_node_id, handoff_id, "
        "handoff_generation) WHERE handoff_node_id IS NOT NULL "
        "AND handoff_id IS NOT NULL AND handoff_generation IS NOT NULL"
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
            last_error TEXT,
            handoff_node_id TEXT,
            handoff_id TEXT,
            handoff_generation INTEGER
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
    connection.execute("BEGIN IMMEDIATE")
    try:
        _migrate_notification_handoff_identity(connection)
    except Exception:
        connection.rollback()
        raise
    connection.commit()


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


def projected_handoff_attention(
    projection: Mapping[str, object],
    node_id: object,
    *,
    observed_at: datetime,
    failure_code: object = None,
) -> dict[str, object] | None:
    if not isinstance(node_id, str):
        return None
    nodes = projection.get("nodes")
    node = nodes.get(node_id) if isinstance(nodes, Mapping) else None
    handoff = node.get("handoff") if isinstance(node, Mapping) else None
    assignments = projection.get("assignments")
    assignment = (
        assignments.get(node_id) if isinstance(assignments, Mapping) else None
    )
    if not isinstance(handoff, Mapping) or not isinstance(assignment, Mapping):
        return None
    try:
        admitted_at = datetime.fromisoformat(str(node["handoff_admitted_at"]))
        age_seconds = max(
            0,
            int((observed_at.astimezone(timezone.utc) - admitted_at).total_seconds()),
        )
    except (KeyError, TypeError, ValueError):
        return None
    phase = handoff.get("last_observed_phase")
    safe_failure_code = public_handoff_failure_code(
        failure_code,
        phase=str(phase),
        deadline_exceeded=node.get("handoff_deadline_exceeded") is True,
        cancelling=(
            projection.get("desired_status") == "cancelled"
            or isinstance(node.get("handoff_cancel"), Mapping)
        ),
    )
    return _closed_handoff(
        {
            "handoff_id": handoff.get("handoff_id"),
            "generation": handoff.get("generation"),
            "endpoint": assignment.get("endpoint"),
            "phase": phase,
            "age_seconds": age_seconds,
            "last_successful_observation_at": node.get(
                "handoff_last_successful_observation_at",
            ),
            "next_action": "reconcile",
            "failure_code": safe_failure_code or "handoff_attention_required",
        },
        node_id=node_id,
    )


def public_handoff_failure_code(
    value: object,
    *,
    phase: str,
    deadline_exceeded: bool,
    cancelling: bool,
) -> str | None:
    """Return one stable public failure code without provider-controlled prose."""
    if isinstance(value, str) and value in _PUBLIC_HANDOFF_FAILURE_CODES:
        return value
    if deadline_exceeded:
        return "deadline_exceeded"
    if phase == "failed":
        return "remote_failed"
    if phase == "indeterminate":
        return (
            "cancellation_indeterminate"
            if cancelling
            else "submission_indeterminate"
        )
    return None


def notification_kind(
    event_type: str,
    projection: Mapping[str, object],
    *,
    node_id: object = None,
) -> str | None:
    if event_type == "handoff_observed":
        nodes = projection.get("nodes")
        node = nodes.get(node_id) if isinstance(nodes, Mapping) else None
        handoff = node.get("handoff") if isinstance(node, Mapping) else None
        return (
            "reconciliation_required"
            if isinstance(handoff, Mapping)
            and handoff.get("last_observed_phase") == "indeterminate"
            else None
        )
    if event_type in {"handoff_indeterminate", "handoff_deadline_exceeded"}:
        return "reconciliation_required"
    if event_type == "handoff_failed":
        return "failure"
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


def _closed_handoff(
    value: object,
    *,
    node_id: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    handoff_id = _logical_identifier(value.get("handoff_id"))
    generation = _bounded_int(value.get("generation"), maximum=1_000_000_000)
    node = _public_identifier(node_id)
    phase = value.get("phase")
    if handoff_id is None or generation is None or generation < 1 or node is None or phase not in {
        "prepared",
        "submitted",
        "active",
        "needs_input",
        "cancelling",
        "indeterminate",
        "succeeded",
        "failed",
        "cancelled",
    }:
        return None
    try:
        endpoint = HandoffEndpoint.parse(str(value.get("endpoint"))).canonical
    except (TypeError, ValueError):
        return None
    age_seconds = _bounded_int(value.get("age_seconds"), maximum=31_536_000)
    raw_observed_at = value.get("last_successful_observation_at")
    observed_at = (
        _public_timestamp(raw_observed_at) if raw_observed_at is not None else None
    )
    next_action = value.get("next_action")
    failure_code = value.get("failure_code")
    if (
        age_seconds is None
        or raw_observed_at is not None and observed_at is None
        or next_action not in {"inspect", "reconcile", "cancel", "wait"}
        or failure_code not in _PUBLIC_HANDOFF_FAILURE_CODES
    ):
        return None
    return {
        "handoff_id": handoff_id,
        "generation": generation,
        "endpoint": endpoint,
        "node_id": node,
        "phase": phase,
        "age_seconds": age_seconds,
        "last_successful_observation_at": observed_at,
        "next_action": next_action,
        "failure_code": failure_code,
        "commands": {
            "show": f"hermes handoff show {handoff_id}",
            "evidence": f"hermes handoff evidence {handoff_id}",
            "reconcile": f"hermes handoff reconcile {handoff_id}",
        },
    }


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
    handoff = _closed_handoff(payload.get("handoff"), node_id=payload.get("node_id"))
    if handoff is not None:
        projected["handoff"] = handoff
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

    @classmethod
    def _from_initialized_store(cls, store) -> NotificationOutbox:
        """Bind a store that already installed the outbox schema at startup."""
        outbox = cls.__new__(cls)
        outbox.store = store
        return outbox

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
        handoff = projected_payload.get("handoff")
        handoff_identity = (
            (
                handoff.get("node_id"),
                handoff.get("handoff_id"),
                handoff.get("generation"),
            )
            if isinstance(handoff, Mapping)
            else (None, None, None)
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
            if isinstance(handoff, Mapping):
                candidate = connection.execute(
                    "SELECT notification_id FROM workflow_notification_outbox "
                    "WHERE run_id=? AND kind=? AND destination=? "
                    "AND handoff_node_id=? AND handoff_id=? "
                    "AND handoff_generation=? ORDER BY updated_at DESC LIMIT 1",
                    (run_id, safe_kind, destination, *handoff_identity),
                ).fetchone()
                if candidate is not None:
                    connection.execute(
                        "UPDATE workflow_notification_outbox SET transition_key=?, "
                        "transition_version=?, coalesced_count=coalesced_count+1, "
                        "payload_json=?, updated_at=?, handoff_node_id=?, "
                        "handoff_id=?, handoff_generation=? WHERE notification_id=?",
                        (
                            transition_key,
                            transition_version,
                            safe_payload,
                            timestamp,
                            *handoff_identity,
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
                        "payload_json=?, updated_at=?, handoff_node_id=?, "
                        "handoff_id=?, handoff_generation=? WHERE notification_id=?",
                        (
                            transition_key,
                            transition_version,
                            safe_payload,
                            timestamp,
                            *handoff_identity,
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
                "available_at, handoff_node_id, handoff_id, handoff_generation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    *handoff_identity,
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
            event_type = str(event.get("event_type") or "")
            kind = notification_kind(
                event_type,
                projection,
                node_id=event.get("node_id"),
            )
            if kind is None and event_type != "handoff_terminal":
                continue
            candidates.append(
                {
                    "transition_key": (
                        f"{run_id}:{kind or 'handoff_clear'}:"
                        f"{int(projection['state_version'])}:desktop"
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
            terminal_event = event.get("event_type") in {
                "handoff_failed",
                "handoff_terminal",
            }
            if terminal_event:
                node_id = event.get("node_id")
                payload = event.get("payload")
                handoff_id = (
                    payload.get("handoff_id")
                    if isinstance(payload, Mapping)
                    else None
                )
                generation = (
                    payload.get("generation")
                    if isinstance(payload, Mapping)
                    else None
                )
                if isinstance(node_id, str):
                    repaired += self.clear_handoff_attention(
                        run_id=str(candidate["run_id"]),
                        node_id=node_id,
                        handoff_id=(
                            str(handoff_id) if isinstance(handoff_id, str) else None
                        ),
                        generation=(
                            generation if isinstance(generation, int) else None
                        ),
                        now=timestamp,
                    )
                if event.get("event_type") == "handoff_terminal":
                    continue
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
                    "handoff": projected_handoff_attention(
                        projection,
                        event.get("node_id"),
                        observed_at=timestamp,
                        failure_code=(
                            event.get("payload", {}).get("failure_code")
                            if isinstance(event.get("payload"), Mapping)
                            else None
                        ),
                    ),
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
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be bounded text")
        if not 1 <= limit <= 100 or lease_seconds <= 0:
            raise ValueError("notification lease bounds are invalid")
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        expires = (observed + timedelta(seconds=lease_seconds)).isoformat()
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE workflow_notification_outbox SET state='pending', "
                "lease_owner=NULL, lease_expires_at=NULL WHERE state='leased' "
                f"AND lease_expires_at<=?{scope_clause}",
                (timestamp, *scope_values),
            )
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE destination=? "
                "AND state='pending' AND available_at<=? "
                f"{scope_clause} ORDER BY created_at, notification_id LIMIT ?",
                (destination, timestamp, *scope_values, limit),
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

    def ack(
        self,
        notification_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
        operator_scope: str | None = None,
    ) -> bool:
        timestamp = self._aware(now or datetime.now(timezone.utc)).isoformat()
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            changed = connection.execute(
                "UPDATE workflow_notification_outbox SET state='delivered', "
                "delivered_at=?, updated_at=?, lease_owner=NULL, lease_expires_at=NULL "
                "WHERE notification_id=? AND state='leased' AND lease_owner=?"
                f"{scope_clause}",
                (timestamp, timestamp, notification_id, owner_id, *scope_values),
            ).rowcount
        return changed == 1

    def fail(
        self,
        notification_id: str,
        *,
        owner_id: str,
        error: str,
        now: datetime | None = None,
        operator_scope: str | None = None,
    ) -> bool:
        observed = self._aware(now or datetime.now(timezone.utc))
        failure_reason = _stable_delivery_failure_reason(error)
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                "notification_id=? AND state='leased' AND lease_owner=?"
                f"{scope_clause}",
                (notification_id, owner_id, *scope_values),
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
        operator_scope: str | None = None,
    ) -> bool:
        scope = self._authority_scope(authority_scope)
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_notification_outbox "
                f"WHERE notification_id=? AND state='dead'{scope_clause}",
                (notification_id, *scope_values),
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
        operator_scope: str | None = None,
    ) -> int:
        scope = self._authority_scope(authority_scope)
        if not isinstance(older_than, timedelta) or older_than < timedelta(0):
            raise ValueError("older_than must be a non-negative duration")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        observed = self._aware(now or datetime.now(timezone.utc))
        timestamp = observed.isoformat()
        cutoff = (observed - older_than).isoformat()
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE "
                "(state='delivered' OR dismissed_at IS NOT NULL) AND "
                "COALESCE(dismissed_at, delivered_at, updated_at)<=? "
                f"{scope_clause} ORDER BY "
                "COALESCE(dismissed_at, delivered_at, updated_at), "
                "notification_id LIMIT ?",
                (cutoff, *scope_values, limit),
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
        self,
        notification_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
        operator_scope: str | None = None,
    ) -> bool:
        timestamp = self._aware(now or datetime.now(timezone.utc)).isoformat()
        scope_clause = (
            " AND EXISTS (SELECT 1 FROM runs WHERE "
            "runs.run_id=workflow_notification_outbox.run_id AND "
            "runs.operator_scope_digest=?)"
            if operator_scope is not None
            else ""
        )
        scope_values: tuple[object, ...] = (
            (self.store._scope_digest(operator_scope),)
            if operator_scope is not None
            else ()
        )
        with self.store._connect() as connection:
            changed = connection.execute(
                "UPDATE workflow_notification_outbox SET dismissed_at=?, updated_at=? "
                "WHERE notification_id=? AND (lease_owner=? OR state='delivered')"
                f"{scope_clause}",
                (timestamp, timestamp, notification_id, owner_id, *scope_values),
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

    def clear_handoff_attention(
        self,
        *,
        run_id: str,
        node_id: str,
        handoff_id: str | None = None,
        generation: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """Resolve current attention deliveries for one terminal handoff node."""
        if _logical_identifier(run_id) is None or _public_identifier(node_id) is None:
            raise ValueError("run_id and node_id must be bounded identifiers")
        if handoff_id is not None and _logical_identifier(handoff_id) is None:
            raise ValueError("handoff_id must be a bounded identifier")
        if (
            generation is not None
            and (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 1
                or generation > 1_000_000_000
            )
        ):
            raise ValueError("generation must be a bounded positive integer")
        timestamp = self._aware(now or datetime.now(timezone.utc)).isoformat()
        with self.store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM workflow_notification_outbox WHERE run_id=? "
                "AND kind IN ('failure','stalled','reconciliation_required') "
                "AND state IN ('pending','leased','dead')",
                (run_id,),
            ).fetchall()
            ids = []
            for row in rows:
                payload, valid = _load_notification_payload(
                    row["payload_json"],
                    transition_version=int(row["transition_version"]),
                )
                handoff = payload.get("handoff") if valid else None
                if (
                    isinstance(handoff, Mapping)
                    and handoff.get("node_id") == node_id
                    and (
                        handoff_id is None
                        or handoff.get("handoff_id") == handoff_id
                    )
                    and (
                        generation is None
                        or handoff.get("generation") == generation
                    )
                ):
                    ids.append(str(row["notification_id"]))
            connection.executemany(
                "UPDATE workflow_notification_outbox SET state='delivered', "
                "delivered_at=?, updated_at=?, lease_owner=NULL, lease_expires_at=NULL "
                "WHERE notification_id=?",
                ((timestamp, timestamp, notification_id) for notification_id in ids),
            )
            connection.commit()
        return len(ids)

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
