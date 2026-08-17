"""Stable, redacted error and identity types for the Confluence connector."""

from __future__ import annotations

from dataclasses import dataclass

SAFE_ERROR_MESSAGES = {
    "invalid_configuration": "Confluence configuration is invalid",
    "invalid_input": "Confluence request input is invalid",
    "authentication": "Confluence authentication failed",
    "permission": "Confluence permission denied",
    "not_found": "Confluence content was not found",
    "conflict": "Confluence content changed since it was read",
    "rate_limited": "Confluence rate limit was reached",
    "transient": "Confluence service is temporarily unavailable",
    "write_ambiguous": "Confluence write outcome is unknown",
    "invalid_remote_data": "Confluence returned invalid data",
    "cancelled": "Confluence request was cancelled",
    "deadline": "Confluence request deadline was exceeded",
    "capacity": "Confluence result exceeded a safe limit",
    "circuit_open": "Confluence calls are paused after repeated failures",
    "confirmation_required": "Confluence change needs explicit confirmation",
}

# Only literals controlled by this connector may leave an exception as
# remediation.  Remote messages and token-bearing values must never pass this
# boundary, even when a future caller supplies them accidentally.
_SAFE_REMEDIATIONS = frozenset({"Update the Confluence token."})


def safe_remediation(value: object) -> str | None:
    """Return only static, connector-owned remediation guidance."""
    if type(value) is not str or value not in _SAFE_REMEDIATIONS:
        return None
    return value


class ConfluenceError(RuntimeError):
    """Stable classified failure that never includes remote or secret text."""

    def __init__(self, category: object, *, remediation: object = None) -> None:
        self.category = (
            category
            if type(category) is str and category in SAFE_ERROR_MESSAGES
            else "transient"
        )
        self.remediation = safe_remediation(remediation)
        super().__init__(SAFE_ERROR_MESSAGES[self.category])


@dataclass(frozen=True, slots=True)
class ConfluenceAuth:
    origin: str
    # Cloud lives under /wiki/rest/api, Server/DC under /rest/api. Derived
    # once at configuration time so no operation has to think about it.
    api_base: str
    authorization: str
    request_timeout_seconds: int
    default_max_results: int
