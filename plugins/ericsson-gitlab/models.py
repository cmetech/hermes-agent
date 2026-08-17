"""Immutable, bounded value objects for the Ericsson GitLab connector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SAFE_ERROR_MESSAGES = {
    "invalid_configuration": "GitLab configuration is invalid",
    "invalid_input": "GitLab request input is invalid",
    "authentication": "GitLab authentication failed",
    "permission": "GitLab permission denied",
    "not_found": "GitLab resource was not found",
    "group_ambiguity": "GitLab reference does not identify one project",
    "conflict": "GitLab resource conflicts with the request",
    "rate_limited": "GitLab rate limit was reached",
    "transient": "GitLab service is temporarily unavailable",
    "invalid_remote_data": "GitLab returned invalid data",
    "cancelled": "GitLab request was cancelled",
    "deadline": "GitLab request deadline was exceeded",
    "capacity": "GitLab result exceeded a safe limit",
    "write_ambiguous": "GitLab write outcome is unknown",
    "circuit_open": "GitLab calls are paused after repeated failures",
    "confirmation_required": "GitLab change needs explicit confirmation",
}


class GitLabError(RuntimeError):
    """Stable error with no remote body, path, or credential text."""

    def __init__(
        self,
        category: str,
        message: str | None = None,
        *,
        remediation: str | None = None,
    ) -> None:
        self.category = category if category in SAFE_ERROR_MESSAGES else "transient"
        self.remediation = remediation
        super().__init__(message or SAFE_ERROR_MESSAGES[self.category])


@dataclass(frozen=True, slots=True)
class GitLabAuth:
    origin: str
    pat: str
    certificate_pair: tuple[Any, Any] | None = None
    tls_context: Any | None = None

    def __repr__(self) -> str:
        certificate_state = "set" if self.certificate_pair else "unset"
        return (
            f"GitLabAuth(origin={self.origin!r}, pat=<redacted>, "
            f"certificate_pair={certificate_state})"
        )


@dataclass(frozen=True, slots=True)
class PageResult:
    items: tuple[dict[str, Any], ...]
    truncated: bool
    next_page: int | None
    next_offset: int | None = None
