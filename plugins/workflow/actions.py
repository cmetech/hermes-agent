"""Authoritative workflow operator action vocabulary and state validation."""

from __future__ import annotations

from typing import Mapping


INSPECTION_ACTIONS = ("status", "events")
MUTATION_ACTIONS = frozenset({
    "approve",
    "reject",
    "provide-input",
    "resume",
    "retry",
    "reconcile",
    "cancel",
    "abandon",
    "archive",
    "restore",
})


def available_actions(
    status: str,
    pending_interaction: Mapping[str, object] | None = None,
    *,
    health: str | None = None,
    archived: bool = False,
) -> list[str]:
    actions = list(INSPECTION_ACTIONS)
    interaction_type = (
        str(pending_interaction.get("type")) if pending_interaction else None
    )
    if status == "paused":
        if interaction_type in {"approval", "workflow_approval"}:
            actions.extend(("approve", "reject", "cancel"))
        elif interaction_type == "loop_input":
            actions.extend(("provide-input", "cancel"))
        elif interaction_type == "reconcile":
            actions.extend(("reconcile", "cancel"))
        else:
            actions.append("cancel")
    elif status in {"running", "queued", "waiting_retry"}:
        actions.append("cancel")
        if status == "running" and health == "stalled":
            actions.append("resume")
    elif status in {"failed", "interrupted"}:
        actions.extend(("resume", "retry", "abandon"))
    else:
        actions.append("restore" if archived else "archive")
    return actions


def mutation_is_valid(
    action: str,
    *,
    status: str,
    pending_interaction: Mapping[str, object] | None = None,
    health: str | None = None,
    archived: bool = False,
) -> bool:
    return action in MUTATION_ACTIONS and action in available_actions(
        status,
        pending_interaction,
        health=health,
        archived=archived,
    )


__all__ = [
    "INSPECTION_ACTIONS",
    "MUTATION_ACTIONS",
    "available_actions",
    "mutation_is_valid",
]
