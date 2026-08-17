"""Error taxonomy shared by the Ericsson connectors.

Categories stay machine-readable (existing plugin code compares
``str(error)`` to a category name, and that contract is preserved).  What is
new is ``remediation``: super-cli pairs every failure with the exact command
that fixes it, and the absence of that is finding F7.  Because these
connectors are configured through the Hermes profile UI rather than a CLI,
the remediation names the profile field instead of a command.
"""

from __future__ import annotations

__all__ = [
    "ConnectorError",
    "RETRYABLE_STATUSES",
    "category_for_status",
    "remediation_for",
]

RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

_STATUS_CATEGORY = {
    400: "invalid_input",
    401: "authentication",
    403: "permission",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}

_REMEDIATION = {
    "authentication": (
        "The {service} token is missing, expired, or invalid. Update the "
        "{service} personal access token in the connector's configuration."
    ),
    "permission": (
        "The {service} token is valid but lacks permission for this "
        "resource. Check the token's scopes, or that your account can see "
        "the project or space."
    ),
    "not_found": (
        "The {service} resource does not exist, or the token cannot see it. "
        "Verify the identifier, then verify the token's access."
    ),
    "invalid_configuration": (
        "The {service} connector configuration is invalid. Re-check the base "
        "URL and authentication mode."
    ),
    "rate_limited": (
        "{service} is rate limiting this client. It will retry automatically; "
        "if it persists, reduce how often this tool is called."
    ),
    "circuit_open": (
        "Repeated failures against {service} have tripped this connector's "
        "circuit breaker, so further calls are being refused locally. Check "
        "whether {service} is reachable and healthy, then retry."
    ),
}


def category_for_status(status: int) -> str:
    """Map an HTTP status onto a stable, machine-readable category."""
    category = _STATUS_CATEGORY.get(status)
    if category is not None:
        return category
    if 500 <= status <= 599:
        return "transient"
    return "invalid_remote_data"


def remediation_for(category: str, service: str) -> str | None:
    """Return operator-facing repair guidance, or None when there is none."""
    template = _REMEDIATION.get(category)
    if template is None:
        return None
    return template.format(service=service)


class ConnectorError(Exception):
    """One connector failure.

    ``str(error)`` is deliberately just the category: existing connector code
    and tests treat the exception message as the category token, and this
    class must slot in without rewriting them.
    """

    def __init__(
        self,
        category: str,
        *,
        service: str | None = None,
        detail: str | None = None,
        outcome_uncertain: bool = False,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.service = service
        self.detail = detail
        self.outcome_uncertain = bool(outcome_uncertain)
        self.remediation = (
            remediation_for(category, service) if service else None
        )
