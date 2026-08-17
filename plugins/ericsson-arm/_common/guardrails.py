"""Mutation gating for connector tools.

super-cli refuses every mutating command unless the caller passes one of
--dry-run or --confirm; there is no implicit-execute path anywhere in it.
The connectors default dry_run=False, so an agent that simply omits the
parameter performs the write (finding F3).  This helper makes "said
nothing" a refusal rather than a commit.
"""

from __future__ import annotations

if __package__:
    from .errors import ConnectorError
else:
    from errors import ConnectorError

__all__ = ["require_explicit_intent"]


def require_explicit_intent(*, dry_run, confirm, action: str) -> bool:
    """Return True to execute, False to preview; raise if intent is unclear.

    Refusing the both-flags case is deliberate: it means the caller does not
    know what it wants, and guessing on a mutation is exactly the wrong
    instinct.
    """
    if type(dry_run) is not bool or type(confirm) is not bool:
        raise ConnectorError(
            "invalid_input",
            detail=f"dry_run and confirm must be booleans for: {action}",
        )
    if dry_run and confirm:
        raise ConnectorError(
            "invalid_input",
            detail=(
                f"dry_run and confirm are mutually exclusive for: {action}. "
                f"Pass exactly one."
            ),
        )
    if dry_run:
        return False
    if confirm:
        return True
    raise ConnectorError(
        "confirmation_required",
        detail=(
            f"This would modify {action}. Re-run with dry_run=true to preview "
            f"the change, or confirm=true to apply it."
        ),
    )
