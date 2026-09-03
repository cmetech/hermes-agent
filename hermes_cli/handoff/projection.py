"""Bounded public projections for handoff consumers."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import HandoffSnapshot
from .store import EvidencePage, HandoffEvent


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def snapshot_summary(
    snapshot: HandoffSnapshot, *, now: datetime | None = None
) -> dict[str, object]:
    result = snapshot.terminal_result
    terminal_summary = None
    if result is not None:
        terminal_summary = {
            "media_type": result["media_type"],
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
        }
    created = snapshot.created_at or datetime.now(timezone.utc)
    current = now or datetime.now(timezone.utc)
    return {
        "handoff_id": snapshot.handoff_id,
        "endpoint": snapshot.spec.endpoint.canonical,
        "mechanism": snapshot.mechanism,
        "phase": snapshot.phase,
        "age_seconds": max(0, int((current - created).total_seconds())),
        "next_observation_at": _timestamp(snapshot.next_advance_at),
        "terminal_summary": terminal_summary,
        "failure_code": snapshot.failure_code,
        "created_at": _timestamp(snapshot.created_at),
        "updated_at": _timestamp(snapshot.updated_at),
    }


def event_summary(event: HandoffEvent) -> dict[str, object]:
    return {
        "handoff_id": event.handoff_id,
        "sequence": event.sequence,
        "event_id": event.event_id,
        "phase_before": event.phase_before,
        "phase_after": event.phase_after,
        "kind": event.kind,
        "actor": event.actor,
        "data": dict(event.data),
        "created_at": _timestamp(event.created_at),
    }


def evidence_payload(page: EvidencePage) -> dict[str, object]:
    return {
        "events": [event_summary(event) for event in page.events],
        "next_after_sequence": page.next_after_sequence,
        "has_more": page.has_more,
    }


__all__ = ["event_summary", "evidence_payload", "snapshot_summary"]
