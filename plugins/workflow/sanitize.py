"""Single bounded sanitizer for workflow operator projections."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePath
from decimal import Decimal, InvalidOperation
from typing import Mapping

from plugins.workflow.actions import WIRE_ACTIONS

from plugins.workflow.schedule_time import (
    ScheduleInstantError,
    normalize_rfc3339_instant,
    run_is_scheduled_wait,
)


_SECRET_KEY = re.compile(
    r"(?i)(secret|password|token|authorization|api[_-]?key|credential|reasoning|prompt|command|provider[_-]?response|feedback|stderr|base[_-]?url|uri|return[_-]?route)"
)
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PORTABLE_INPUT_INVALID = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_DEVICE_NAME = re.compile(
    r"(?i)^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$"
)
_PORTABLE_COMPONENT_MAX_UNITS = 255
_TEXT_INPUT_SUFFIX = ".txt"
_PROJECTION_MAX_CHARS = 16_384
_TRUNCATION_SUFFIX = "…[TRUNCATED]"
_DISPLAY_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
_DISPLAY_CREDENTIAL_PREFIX = re.compile(
    r"(?i)^(?:sk|pk|api|key|token|bearer|basic|oauth|secret|password)[_-]"
)
_DISPLAY_HIGH_ENTROPY = re.compile(r"(?i)(?:[0-9a-f]{32,}|[a-z0-9_-]{64,})")
_DISPLAY_REDACTED = re.compile(r"^redacted:[0-9a-f]{16}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_PUBLIC_ID = re.compile(r"^[0-9a-f]{32}$")
_PUBLIC_RUN_STATUSES = frozenset({
    "queued",
    "running",
    "waiting_retry",
    "recovery_pending",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
})
_PUBLIC_NODE_STATES = frozenset({
    "pending",
    "ready",
    "claimed",
    "running",
    "waiting_retry",
    "waiting_resolution",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
})
_PUBLIC_HEALTH = frozenset({
    "healthy",
    "terminal",
    "user_wait",
    "interrupted",
    "waiting",
    "retry_wait",
    "stalled",
    "coordinator_unavailable",
    "storage_degraded",
})
_PUBLIC_INTERACTION_TYPES = frozenset({
    "approval",
    "workflow_approval",
    "loop_input",
    "loop_signal_confirmation",
    "capability",
    "reconcile",
})
_PUBLIC_INTERACTION_EVENT_TYPES = frozenset({
    "loop_input_provided",
    "loop_signal_confirmation_required",
    "loop_signal_accepted",
    "loop_feedback_provided",
})
_PUBLIC_PROVENANCE_SOURCES = frozenset({
    "api",
    "background_agent",
    "chat",
    "cli",
    "cron",
    "desktop",
})
_PUBLIC_ASSURANCE = frozenset({
    "legacy_unknown",
    "local_admin_claim",
    "system_schedule",
    "verified_adapter",
})
_PUBLIC_ERROR = {
    "code": "workflow_operation_failed",
    "message": "Workflow operation failed.",
}
_PUBLIC_ATTEMPT_ERROR_CODES = frozenset({
    "execution_integrity",
    "package_mcp_unavailable",
})
_PUBLIC_RETRY_FIELDS = (
    "requested_retries",
    "requested_total_attempts",
    "effective_total_attempts",
    "retry_consumed",
    "remaining_attempts",
    "additional_provider_attempts",
)


def public_display_identifier(value: object) -> str:
    """Return a bounded provider/model label or one stable wholesale redaction."""
    raw = value if isinstance(value, str) else str(value)
    if _DISPLAY_REDACTED.fullmatch(raw):
        return raw
    safe = (
        0 < len(raw) <= 128
        and _DISPLAY_IDENTIFIER.fullmatch(raw) is not None
        and not raw.startswith(("/", "\\"))
        and all(part not in {"", ".", ".."} for part in raw.split("/"))
        and _DISPLAY_CREDENTIAL_PREFIX.search(raw) is None
        and _DISPLAY_HIGH_ENTROPY.search(raw) is None
        and not any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw)
    )
    if safe:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"redacted:{digest[:16]}"


def workflow_input_name_is_portable(name: object, *, max_length: int = 128) -> bool:
    """Return whether a name is one portable filename segment on every host OS."""
    if not isinstance(name, str):
        return False
    component = name + _TEXT_INPUT_SUFFIX
    try:
        utf8_units = len(component.encode("utf-8"))
        utf16_units = len(component.encode("utf-16-le")) // 2
    except UnicodeError:
        return False
    return (
        bool(name.strip())
        and len(name) <= max_length
        and utf8_units <= _PORTABLE_COMPONENT_MAX_UNITS
        and utf16_units <= _PORTABLE_COMPONENT_MAX_UNITS
        and name not in {".", ".."}
        and not name.endswith((".", " "))
        and _PORTABLE_INPUT_INVALID.search(name) is None
        and _CONTROL.search(name) is None
        and _WINDOWS_DEVICE_NAME.fullmatch(name) is None
    )


def workflow_input_names_are_portable(names: object) -> bool:
    """Reject non-portable names and case-insensitive component collisions."""
    try:
        iterator = iter(names)
    except TypeError:
        return False
    seen: set[str] = set()
    for name in iterator:
        if not workflow_input_name_is_portable(name):
            return False
        folded = name.casefold()
        if folded in seen:
            return False
        seen.add(folded)
    return True


def workflow_filename_components_are_distinct(components: object) -> bool:
    """Reject generated filename components that alias case-insensitively."""
    try:
        iterator = iter(components)
    except TypeError:
        return False
    seen: set[str] = set()
    for component in iterator:
        if not isinstance(component, str):
            return False
        folded = component.casefold()
        if folded in seen:
            return False
        seen.add(folded)
    return True


def projection_key_is_secret(key: str) -> bool:
    """Return whether a projection key names operator-sensitive content."""
    return bool(_SECRET_KEY.search(key))


def sanitize_text(value: str, *, max_chars: int = 16_384) -> tuple[str, bool]:
    cleaned = _CONTROL.sub("�", _ANSI.sub("", value))
    if len(cleaned) <= max_chars:
        return cleaned, False
    return cleaned[:max_chars], True


def sanitize_evidence_bytes(
    value: bytes, *, max_chars: int = 16_384
) -> tuple[str, bool]:
    """Decode and sanitize untrusted evidence through the shared text policy."""
    return sanitize_text(value.decode("utf-8", errors="replace"), max_chars=max_chars)


def sanitize_projection(value: object, *, key: str = "", depth: int = 0) -> object:
    if depth > 12:
        return "[TRUNCATED_DEPTH]"
    if projection_key_is_secret(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child): sanitize_projection(item, key=str(child), depth=depth + 1)
            for child, item in list(value.items())[:200]
            if str(child).lower()
            not in {"operator_scope_digest", "idempotency_key_digest"}
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_projection(item, key=key, depth=depth + 1) for item in value[:200]
        ]
    if isinstance(value, str):
        cleaned, truncated = sanitize_text(value, max_chars=_PROJECTION_MAX_CHARS)
        if key.lower() in {"provider", "model"}:
            return public_display_identifier(cleaned)
        if key.lower() == "transition_key" and ":gateway:" in cleaned:
            cleaned = cleaned.partition(":gateway:")[0] + ":gateway:opaque"
        if key.lower() in {"path", "source_path", "run_directory", "relative_path"}:
            cleaned = PurePath(cleaned).name
        if truncated:
            cleaned = cleaned[: _PROJECTION_MAX_CHARS - len(_TRUNCATION_SUFFIX)]
            return cleaned + _TRUNCATION_SUFFIX
        return cleaned
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_projection(str(value), key=key, depth=depth + 1)


def _bounded_identifier(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    if _SHA256.fullmatch(value) or _OPAQUE_PUBLIC_ID.fullmatch(value):
        return value
    return public_display_identifier(value)


def _opaque_identifier(value: object) -> str:
    raw = value if isinstance(value, str) else repr(type(value).__name__)
    digest = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"redacted:{digest[:16]}"


def _bounded_integer(value: object, *, default: int = 0) -> int:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else default


def _bounded_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return value


def _bounded_code(value: object, *, fallback: str | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return fallback
    projected = public_display_identifier(value)
    return projected if not projected.startswith("redacted:") else fallback


def _public_actions(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        action
        for action in value[: len(WIRE_ACTIONS)]
        if isinstance(action, str) and action in WIRE_ACTIONS
    ]


def _public_digest(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def public_pending_interaction(
    value: object, *, node_id: object = None
) -> dict[str, object] | None:
    """Project only interaction routing facts, never rendered user-facing text."""
    if isinstance(value, str):
        value = {"type": value}
    if not isinstance(value, Mapping):
        return None
    kind = value.get("type")
    interaction_id = value.get("interaction_id")
    if kind not in _PUBLIC_INTERACTION_TYPES:
        return None
    projected: dict[str, object] = {"type": kind}
    if isinstance(interaction_id, str):
        projected["interaction_id"] = _bounded_identifier(
            interaction_id, fallback="interaction"
        )
    resolved_node_id = node_id if node_id is not None else value.get("node_id")
    if isinstance(resolved_node_id, str):
        projected["node_id"] = _bounded_identifier(resolved_node_id, fallback="node")
    if kind == "loop_signal_confirmation":
        projected["iteration"] = _bounded_integer(value.get("iteration"))
        projected["max_iterations"] = _bounded_integer(value.get("max_iterations"))
    return projected


def public_attempt_projection(
    node_id: object,
    attempt: Mapping[str, object],
    *,
    manifest_digest: object = None,
) -> dict[str, object]:
    """Build the closed public attempt DTO from private durable state."""
    metadata = attempt.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    retry = {
        field: _bounded_integer(metadata.get(field)) for field in _PUBLIC_RETRY_FIELDS
    }
    retry["capped"] = metadata.get("capped") is True
    projected: dict[str, object] = {
        "item_type": "attempt",
        "node_id": _bounded_identifier(node_id, fallback="node"),
        "attempt_id": _bounded_identifier(attempt.get("attempt_id"), fallback="attempt"),
        "state": (
            attempt.get("state")
            if attempt.get("state") in _PUBLIC_NODE_STATES
            else "failed"
        ),
        "retry": retry,
    }
    authority_digest = _public_digest(metadata.get("intended_authority_digest"))
    manifest = _public_digest(manifest_digest)
    if authority_digest is not None and manifest is not None:
        projected["provider_authority"] = {
            "authority_digest": authority_digest,
            "manifest_digest": manifest,
        }
    audit = metadata.get("audit")
    budget = audit.get("cost_budget") if isinstance(audit, Mapping) else None
    if isinstance(budget, Mapping):
        safe_budget: dict[str, object] = {}
        for field in ("max_budget_usd", "settled_cost_usd", "remaining_usd", "overage_usd"):
            candidate = budget.get(field)
            if not isinstance(candidate, str) or len(candidate) > 64:
                continue
            try:
                decimal = Decimal(candidate)
            except InvalidOperation:
                continue
            if decimal.is_finite():
                safe_budget[field] = candidate
        count = budget.get("settlement_count")
        if type(count) is int and 0 <= count <= 1_000_000:
            safe_budget["settlement_count"] = count
        if safe_budget:
            projected["cost_budget"] = safe_budget
    if attempt.get("error_code") is not None or attempt.get("error_message") is not None:
        projected["error"] = dict(_PUBLIC_ERROR)
    error_code = attempt.get("error_code")
    if isinstance(error_code, str) and error_code in _PUBLIC_ATTEMPT_ERROR_CODES:
        projected["error_code"] = error_code
    for field in ("started_at", "completed_at", "next_attempt_at"):
        timestamp = _bounded_timestamp(attempt.get(field))
        if timestamp is not None:
            projected[field] = timestamp
    return projected


def public_artifact_projection(artifact: Mapping[str, object]) -> dict[str, object]:
    """Return artifact integrity metadata without paths, content, or session identity."""
    projected: dict[str, object] = {"item_type": "artifact"}
    publication_id = artifact.get("publication_id")
    typed_publication = (
        artifact.get("typed_publication_version") == 2
        and isinstance(publication_id, str)
        and _OPAQUE_PUBLIC_ID.fullmatch(publication_id) is not None
    )
    if typed_publication:
        projected["publication_id"] = publication_id
    for field in ("node_id", "attempt_id", "output_type"):
        value = artifact.get(field)
        if isinstance(value, str):
            projected[field] = _bounded_identifier(value, fallback=field)
    media_type = artifact.get("media_type")
    if isinstance(media_type, str) and 1 <= len(media_type) <= 128:
        projected["media_type"] = sanitize_text(media_type, max_chars=128)[0]
    for field in ("sha256", "schema_fingerprint"):
        digest = _public_digest(artifact.get(field))
        if digest is not None:
            projected[field] = digest
    size = artifact.get("size_bytes")
    if type(size) is int and 0 <= size <= 1 << 50:
        projected["size_bytes"] = size
    produced_at = _bounded_timestamp(artifact.get("produced_at"))
    if produced_at is not None:
        projected["produced_at"] = produced_at
    projected["integrity_status"] = (
        "verified" if typed_publication else "legacy_unverified"
    )
    projected["recovery_status"] = (
        "verified" if typed_publication else "projection_recovered"
    )
    return projected


def _public_node_projection(
    node_id: object,
    node: Mapping[str, object],
    *,
    manifest_digest: object,
) -> dict[str, object]:
    public_id = _bounded_identifier(node_id, fallback="node")
    state = node.get("state")
    projected: dict[str, object] = {
        "id": public_id,
        "state": state if state in _PUBLIC_NODE_STATES else "interrupted",
        "depends_on": [
            _bounded_identifier(item, fallback="node")
            for item in (node.get("depends_on") or [])[:200]
            if isinstance(item, str)
        ],
    }
    attempts = node.get("attempts")
    if isinstance(attempts, list):
        projected_attempts = [
            public_attempt_projection(public_id, attempt, manifest_digest=manifest_digest)
            for attempt in attempts[:200]
            if isinstance(attempt, Mapping)
        ]
        projected["attempts"] = projected_attempts
        projected["attempt_count"] = len(projected_attempts)
    else:
        projected["attempts"] = []
        projected["attempt_count"] = 0
    pending = public_pending_interaction(node.get("pending_interaction"), node_id=public_id)
    if pending is not None:
        projected["pending_interaction"] = pending
    for field in ("approval_rework_attempts", "retry_consumed"):
        if field in node:
            projected[field] = _bounded_integer(node.get(field))
    for field in ("next_attempt_at", "started_at", "completed_at"):
        timestamp = _bounded_timestamp(node.get(field))
        if timestamp is not None:
            projected[field] = timestamp
    if node.get("error_code") is not None or node.get("error_message") is not None:
        projected["error"] = dict(_PUBLIC_ERROR)
    return projected


def public_event_projection(value: Mapping[str, object]) -> dict[str, object]:
    """Build a closed timeline event; journal payloads are private by default."""
    event_type = _bounded_code(value.get("event_type"), fallback="workflow_event")
    projected: dict[str, object] = {
        "item_type": "timeline_event",
        "sequence": _bounded_integer(value.get("sequence")),
        "timestamp": _bounded_timestamp(value.get("timestamp"))
        or "1970-01-01T00:00:00+00:00",
        "run_id": _bounded_identifier(value.get("run_id"), fallback="run"),
        "event_type": event_type or "workflow_event",
    }
    for field in ("node_id", "attempt_id"):
        item = value.get(field)
        if isinstance(item, str):
            projected[field] = _bounded_identifier(item, fallback=field)
    payload = value.get("payload")
    if isinstance(payload, Mapping):
        for field in ("interaction_id", "reason_code", "decision", "outcome"):
            code = _bounded_code(payload.get(field))
            if code is not None:
                projected[field] = code
        if str(event_type).startswith("interaction_") or event_type in (
            _PUBLIC_INTERACTION_EVENT_TYPES
        ):
            for field in ("actor", "channel"):
                identifier = payload.get(field)
                if isinstance(identifier, str):
                    projected[field] = public_display_identifier(identifier)
    if value.get("payload_truncated") is True:
        projected["payload_truncated"] = True
    return projected


def public_cleanup_projection(value: Mapping[str, object]) -> dict[str, object]:
    outcome = _bounded_code(value.get("outcome"), fallback="cleanup_projection_invalid")
    if _bounded_timestamp(value.get("timestamp")) is None:
        outcome = "cleanup_projection_invalid"
    return {
        "item_type": "cleanup",
        "sequence": _bounded_integer(value.get("sequence")),
        "files": _bounded_integer(value.get("files")),
        "bytes": _bounded_integer(value.get("bytes")),
        "outcome": outcome or "cleanup_projection_invalid",
    }


def public_run_projection(
    value: Mapping[str, object], *, now: datetime | None = None
) -> dict[str, object]:
    """Build the closed run DTO while retaining private durable state server-side."""
    raw_status = value.get("status")
    valid_status = raw_status in _PUBLIC_RUN_STATUSES
    status = raw_status if valid_status else "recovery_pending"
    health = value.get("health")
    if health not in _PUBLIC_HEALTH:
        health = "storage_degraded" if not valid_status else "healthy"
    workflow = value.get("workflow")
    if not valid_status:
        workflow = _opaque_identifier(workflow)
    nodes = value.get("nodes")
    manifest_digest = value.get("provider_resolution_sha256")
    projected: dict[str, object] = {
        "schema_version": 1,
        "action": value.get("action") if value.get("action") in WIRE_ACTIONS else "status",
        "run_id": _bounded_identifier(value.get("run_id"), fallback="run"),
        "workflow": _bounded_identifier(workflow, fallback="workflow"),
        "status": status,
        "status_authoritative": bool(value.get("status_authoritative", valid_status)),
        "health": health,
        "updated_at": _bounded_timestamp(value.get("updated_at"))
        or "1970-01-01T00:00:00+00:00",
        "state_version": _bounded_integer(value.get("state_version")),
        "progress": {"kind": "graph", "completed_nodes": 0, "total_nodes": 0},
        "nodes": {},
        "artifacts": [],
        "attempts": _bounded_integer(value.get("attempts")),
        "current_nodes": [],
        "previous_node": None,
        "next_retry_at": None,
        "pending_interaction": None,
        "next_actions": _public_actions(value.get("next_actions")),
        "queue_position": None,
        "blocked_by_run_id": None,
    }
    if isinstance(nodes, Mapping):
        projected_nodes = {
            _bounded_identifier(node_id, fallback="node"): _public_node_projection(
                node_id, node, manifest_digest=manifest_digest
            )
            for node_id, node in list(nodes.items())[:200]
            if isinstance(node, Mapping)
        }
        projected["nodes"] = projected_nodes
    progress = value.get("progress")
    if isinstance(progress, Mapping) and progress.get("kind") == "graph":
        projected["progress"] = {
            "kind": "graph",
            "completed_nodes": _bounded_integer(progress.get("completed_nodes")),
            "total_nodes": _bounded_integer(progress.get("total_nodes")),
        }
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list):
        projected["artifacts"] = [
            public_artifact_projection(artifact)
            for artifact in artifacts[:200]
            if isinstance(artifact, Mapping)
        ]
    current_nodes = value.get("current_nodes")
    if isinstance(current_nodes, list):
        projected["current_nodes"] = [
            _bounded_identifier(item, fallback="node")
            for item in current_nodes[:200]
            if isinstance(item, str)
        ]
    previous_node = value.get("previous_node")
    if isinstance(previous_node, str):
        projected["previous_node"] = _bounded_identifier(previous_node, fallback="node")
    projected["next_retry_at"] = _bounded_timestamp(value.get("next_retry_at"))
    projected["pending_interaction"] = public_pending_interaction(
        value.get("pending_interaction")
    )
    queue_position = value.get("queue_position")
    if type(queue_position) is int and 0 <= queue_position <= 1_000_000:
        projected["queue_position"] = queue_position
    blocked_by = value.get("blocked_by_run_id")
    if isinstance(blocked_by, str):
        projected["blocked_by_run_id"] = _bounded_identifier(blocked_by, fallback="run")
    for field in (
        "workflow_version",
        "execution_mode",
        "admission_disposition",
        "presentation_state",
    ):
        code = _bounded_code(value.get(field))
        if code is not None:
            projected[field] = code
    trigger = value.get("trigger")
    if trigger in _PUBLIC_PROVENANCE_SOURCES:
        projected["trigger"] = trigger
    provenance = value.get("provenance")
    if isinstance(provenance, Mapping):
        source = provenance.get("source")
        assurance = provenance.get("assurance")
        if source in _PUBLIC_PROVENANCE_SOURCES and assurance in _PUBLIC_ASSURANCE:
            public_provenance: dict[str, object] = {
                "source": source,
                "assurance": assurance,
            }
            admitted_at = _bounded_timestamp(provenance.get("admitted_at"))
            if admitted_at is not None:
                public_provenance["admitted_at"] = admitted_at
            projected["provenance"] = public_provenance
    for field in ("definition_digest", "provider_resolution_sha256"):
        digest = _public_digest(value.get(field))
        if digest is not None:
            projected[field] = digest
    for field in (
        "created_at",
        "started_at",
        "completed_at",
        "archived_at",
        "last_semantic_progress_at",
    ):
        timestamp = _bounded_timestamp(value.get(field))
        if timestamp is not None:
            projected[field] = timestamp
    for field in ("event_sequence", "archive_version"):
        if field in value:
            projected[field] = _bounded_integer(value.get(field))
    if value.get("restored_to_history") is not None:
        projected["restored_to_history"] = value.get("restored_to_history") is True
    blocking = _bounded_code(value.get("blocking_reason"))
    if not valid_status:
        blocking = "run_evidence_uncorroborated"
    if blocking is not None:
        projected["blocking_reason"] = blocking
    warnings = value.get("warnings")
    if isinstance(warnings, (list, tuple)):
        projected["warnings"] = [
            code
            for item in warnings[:50]
            if (code := _bounded_code(item)) is not None
        ]
    coordinator = value.get("coordinator")
    if isinstance(coordinator, Mapping):
        status_code = _bounded_code(coordinator.get("status"), fallback="unavailable")
        public_coordinator: dict[str, object] = {"status": status_code or "unavailable"}
        reason = _bounded_code(coordinator.get("reason_code"))
        if reason is not None:
            public_coordinator["reason_code"] = reason
        for field in ("epoch",):
            if field in coordinator:
                public_coordinator[field] = _bounded_integer(coordinator.get(field))
        for field in ("heartbeat_at", "lease_expires_at"):
            timestamp = _bounded_timestamp(coordinator.get(field))
            if timestamp is not None:
                public_coordinator[field] = timestamp
        projected["coordinator"] = public_coordinator
    if not valid_status or value.get("last_error") is not None or any(
        isinstance(node, Mapping)
        and any(
            isinstance(attempt, Mapping)
            and (attempt.get("error_code") is not None or attempt.get("error_message") is not None)
            for attempt in node.get("attempts", [])
        )
        for node in (nodes.values() if isinstance(nodes, Mapping) else ())
    ):
        projected["last_error"] = dict(_PUBLIC_ERROR)
    metadata = value.get("run_metadata")
    if not isinstance(metadata, Mapping):
        return projected
    schedule_at = metadata.get("schedule_at")
    try:
        canonical_schedule_at = normalize_rfc3339_instant(schedule_at)
    except ScheduleInstantError:
        return projected
    if schedule_at != canonical_schedule_at:
        return projected
    projected["schedule_at"] = canonical_schedule_at
    if run_is_scheduled_wait(
        value,
        observed=now or datetime.now(timezone.utc),
    ):
        projected["presentation_state"] = "scheduled_wait"
    return projected


__all__ = [
    "projection_key_is_secret",
    "public_artifact_projection",
    "public_attempt_projection",
    "public_cleanup_projection",
    "public_display_identifier",
    "public_event_projection",
    "public_pending_interaction",
    "public_run_projection",
    "sanitize_evidence_bytes",
    "sanitize_projection",
    "sanitize_text",
    "workflow_filename_components_are_distinct",
    "workflow_input_name_is_portable",
    "workflow_input_names_are_portable",
]
