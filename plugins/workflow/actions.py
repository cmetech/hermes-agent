"""Authoritative workflow operator action vocabulary and state validation."""

from __future__ import annotations

from typing import Mapping

from plugins.workflow.models import LoopSignalConfirmation


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
LANE_STATES = frozenset({"held", "released"})


def lane_state_for(
    status: str,
    *,
    pause_lane_policy: str = "hold",
    unresolved_outward_attempt: bool = False,
) -> str:
    """Project durable lane ownership independently from lifecycle status."""
    if pause_lane_policy not in {"hold", "release"}:
        raise ValueError("pause_lane_policy must be hold or release")
    if status == "running":
        return "held"
    if status == "paused":
        return "held" if pause_lane_policy == "hold" else "released"
    if status == "interrupted":
        return "held" if unresolved_outward_attempt else "released"
    return "released"


def available_actions(
    status: str,
    pending_interaction: Mapping[str, object] | None = None,
    *,
    health: str | None = None,
    archived: bool = False,
    can_resume: bool = True,
) -> list[str]:
    actions = list(INSPECTION_ACTIONS)
    interaction_type = (
        str(pending_interaction.get("type")) if pending_interaction else None
    )
    if status == "paused":
        if interaction_type in {"approval", "workflow_approval"}:
            actions.extend(("approve", "reject", "cancel"))
        elif interaction_type == "loop_signal_confirmation":
            try:
                confirmation = LoopSignalConfirmation.from_mapping(
                    pending_interaction
                )
            except ValueError:
                actions.append("cancel")
                return actions
            actions.append("approve")
            if confirmation.iteration < confirmation.max_iterations:
                actions.append("provide-input")
            actions.append("cancel")
        elif interaction_type == "loop_input":
            actions.extend(("provide-input", "cancel"))
        elif interaction_type == "reconcile":
            actions.extend(("reconcile", "cancel"))
        else:
            actions.append("cancel")
    elif status in {"running", "queued", "waiting_retry"}:
        actions.append("cancel")
        if status == "running" and health == "stalled" and can_resume:
            actions.append("resume")
    elif status == "recovery_pending":
        actions.extend(("resume", "cancel"))
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
    can_resume: bool = True,
) -> bool:
    return action in MUTATION_ACTIONS and action in available_actions(
        status,
        pending_interaction,
        health=health,
        archived=archived,
        can_resume=can_resume,
    )


__all__ = [
    "INSPECTION_ACTIONS",
    "LANE_STATES",
    "MUTATION_ACTIONS",
    "available_actions",
    "lane_state_for",
    "mutation_is_valid",
]
