"""Profile-local operator commands for agent handoffs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import sys
from uuid import uuid4

from .models import HANDOFF_PHASES, HandoffSnapshot
from .service import AgentHandoffService, HandoffServiceError
from .store import (
    EvidencePage,
    HandoffConflict,
    HandoffNotFound,
    HandoffStateConflict,
    HandoffStoreError,
)


def _service() -> AgentHandoffService:
    return AgentHandoffService()


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _summary(snapshot: HandoffSnapshot) -> dict[str, object]:
    result = snapshot.terminal_result
    terminal_summary = None
    if result is not None:
        terminal_summary = {
            "media_type": result["media_type"],
            "sha256": result["sha256"],
            "size_bytes": result["size_bytes"],
        }
    created = snapshot.created_at or datetime.now(timezone.utc)
    return {
        "handoff_id": snapshot.handoff_id,
        "endpoint": snapshot.spec.endpoint.canonical,
        "mechanism": snapshot.mechanism,
        "phase": snapshot.phase,
        "age_seconds": max(0, int((datetime.now(timezone.utc) - created).total_seconds())),
        "next_observation_at": _timestamp(snapshot.next_advance_at),
        "terminal_summary": terminal_summary,
        "failure_code": snapshot.failure_code,
        "created_at": _timestamp(snapshot.created_at),
        "updated_at": _timestamp(snapshot.updated_at),
    }


def _event(event) -> dict[str, object]:
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


def _print_snapshot(snapshot: HandoffSnapshot) -> None:
    item = _summary(snapshot)
    print(f"handoff_id: {item['handoff_id']}")
    print(f"endpoint: {item['endpoint']}")
    print(f"mechanism: {item['mechanism'] or '-'}")
    print(f"phase: {item['phase']}")
    print(f"age: {item['age_seconds']}s")
    print(f"next_observation: {item['next_observation_at'] or '-'}")
    print(
        "terminal_summary: "
        + (json.dumps(item["terminal_summary"], sort_keys=True) if item["terminal_summary"] else "-")
    )
    print(f"failure_code: {item['failure_code'] or '-'}")


def _print_error(code: str, *, json_output: bool, command_id: str | None = None) -> None:
    if json_output:
        payload: dict[str, object] = {"error": {"code": code}}
        if command_id is not None:
            payload["command_id"] = command_id
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return
    print(f"error: {code}", file=sys.stderr)
    if command_id is not None:
        print(f"command_id: {command_id}", file=sys.stderr)


def _evidence_payload(page: EvidencePage) -> dict[str, object]:
    return {
        "events": [_event(event) for event in page.events],
        "next_after_sequence": page.next_after_sequence,
        "has_more": page.has_more,
    }


def cmd_handoff(args) -> int:
    """Run one diagnostic or convergent handoff operation."""
    action = getattr(args, "handoff_action", None)
    json_output = bool(getattr(args, "json", False))
    command_id = None
    if action in {"reconcile", "cancel"}:
        command_id = getattr(args, "command_id", None) or f"operator-{uuid4().hex}"

    service = _service()
    try:
        if action == "list":
            query = {"phase": args.phase} if args.phase else None
            snapshots = service.list(query, limit=args.limit)
            if json_output:
                print(
                    json.dumps(
                        {"handoffs": [_summary(item) for item in snapshots]},
                        sort_keys=True,
                    )
                )
            elif not snapshots:
                print("No handoffs.")
            else:
                for index, snapshot in enumerate(snapshots):
                    if index:
                        print()
                    _print_snapshot(snapshot)
            return 0

        if action == "show":
            snapshot = service.get(args.handoff_id)
            if json_output:
                print(json.dumps(_summary(snapshot), sort_keys=True))
            else:
                _print_snapshot(snapshot)
            return 0

        if action == "evidence":
            page = service.evidence(
                args.handoff_id,
                after_sequence=args.after,
                limit=args.limit,
            )
            payload = _evidence_payload(page)
            if json_output:
                print(json.dumps(payload, sort_keys=True))
            else:
                for event in payload["events"]:
                    print(
                        f"{event['sequence']}\t{event['created_at']}\t{event['kind']}\t"
                        f"{event['phase_before'] or '-'} -> {event['phase_after']}\t"
                        f"{json.dumps(event['data'], sort_keys=True)}"
                    )
                print(f"next_after_sequence: {page.next_after_sequence}")
                print(f"has_more: {str(page.has_more).lower()}")
            return 0

        if action in {"reconcile", "cancel"}:
            snapshot = service.command(
                args.handoff_id,
                action,
                command_id=command_id,
                actor="operator",
            )
            payload = {"command_id": command_id, **_summary(snapshot)}
            if json_output:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(f"command_id: {command_id}")
                _print_snapshot(snapshot)
            return 0

        if action == "advance":
            budget = args.budget_seconds
            if not math.isfinite(budget) or budget <= 0:
                raise ValueError("invalid budget")
            result = service.advance(args.handoff_id, budget_seconds=budget)
            payload = {
                "operation": result.operation,
                "observation_folded": result.observation_folded,
                "work_done": result.work_done,
                **_summary(result.snapshot),
            }
            if json_output:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(f"operation: {result.operation or '-'}")
                print(f"observation_folded: {str(result.observation_folded).lower()}")
                _print_snapshot(result.snapshot)
            return 0

        raise ValueError("unknown handoff action")
    except HandoffNotFound:
        _print_error("handoff_not_found", json_output=json_output, command_id=command_id)
        return 1
    except (HandoffConflict, HandoffStateConflict):
        _print_error("handoff_conflict", json_output=json_output, command_id=command_id)
        return 1
    except ValueError:
        _print_error("invalid_argument", json_output=json_output, command_id=command_id)
        return 2
    except (HandoffStoreError, HandoffServiceError):
        _print_error("handoff_operation_failed", json_output=json_output, command_id=command_id)
        return 1
    except Exception:
        _print_error("handoff_internal_error", json_output=json_output, command_id=command_id)
        return 1
    finally:
        store = getattr(service, "store", None)
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def build_handoff_parser(subparsers) -> None:
    """Attach the profile-local ``handoff`` operator command."""
    parser = subparsers.add_parser(
        "handoff",
        help="Inspect and safely advance local agent handoffs",
        description="Inspect the selected profile's durable agent-handoff ledger.",
    )
    actions = parser.add_subparsers(dest="handoff_action", required=True)

    list_parser = actions.add_parser("list", help="List recent handoffs")
    list_parser.add_argument("--phase", choices=sorted(HANDOFF_PHASES))
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--json", action="store_true")

    show_parser = actions.add_parser("show", help="Show safe handoff diagnostics")
    show_parser.add_argument("handoff_id")
    show_parser.add_argument("--json", action="store_true")

    evidence_parser = actions.add_parser("evidence", help="Show redacted evidence events")
    evidence_parser.add_argument("handoff_id")
    evidence_parser.add_argument("--after", type=int, default=0, metavar="SEQUENCE")
    evidence_parser.add_argument("--limit", type=int, default=100)
    evidence_parser.add_argument("--json", action="store_true")

    for name in ("reconcile", "cancel"):
        command_parser = actions.add_parser(name, help=f"Request {name} idempotently")
        command_parser.add_argument("handoff_id")
        command_parser.add_argument("--command-id")
        command_parser.add_argument("--json", action="store_true")

    advance_parser = actions.add_parser(
        "advance", help="Perform one bounded convergence step"
    )
    advance_parser.add_argument("handoff_id")
    advance_parser.add_argument("--budget-seconds", type=float, default=2.0, metavar="N")
    advance_parser.add_argument("--json", action="store_true")

    parser.set_defaults(func=cmd_handoff)


__all__ = ["build_handoff_parser", "cmd_handoff"]
