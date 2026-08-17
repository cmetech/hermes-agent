"""Bounded value objects and safe errors for the Ericsson Jira connector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

if __package__:
    from ._common.errors import remediation_for
else:
    from _common.errors import remediation_for


SAFE_ERROR_MESSAGES = {
    "invalid_configuration": "Jira configuration is invalid",
    "invalid_input": "Jira request input is invalid",
    "authentication": "Jira authentication failed",
    "permission": "Jira permission denied",
    "not_found": "Jira resource was not found",
    "conflict": "Jira resource conflicts with the request",
    "rate_limited": "Jira rate limit was reached",
    "transient": "Jira service is temporarily unavailable",
    "write_ambiguous": "Jira write outcome is unknown",
    "invalid_remote_data": "Jira returned invalid data",
    "cancelled": "Jira request was cancelled",
    "deadline": "Jira request deadline was exceeded",
    "capacity": "Jira result exceeded a safe limit",
    "circuit_open": "Jira calls are paused after repeated failures",
    "confirmation_required": "Jira change needs explicit confirmation",
}


_SAFE_REMEDIATIONS = frozenset(
    remediation
    for category in SAFE_ERROR_MESSAGES
    if (remediation := remediation_for(category, "jira")) is not None
) | frozenset({"Update the Jira token."})


def safe_remediation(value: object) -> str | None:
    """Return only static, connector-owned remediation guidance."""
    if type(value) is not str or value not in _SAFE_REMEDIATIONS:
        return None
    return value


class JiraError(RuntimeError):
    """Stable classified failure that never includes remote or secret text."""

    def __init__(self, category: str, *, remediation: object = None) -> None:
        self.category = category if category in SAFE_ERROR_MESSAGES else "transient"
        self.remediation = safe_remediation(remediation)
        super().__init__(SAFE_ERROR_MESSAGES[self.category])


@dataclass(frozen=True, slots=True)
class JiraAuth:
    origin: str
    authorization: str
    auth_mode: str
    rest_api_version: str
    transport: str
    curl_executable: str
    request_timeout_seconds: int
    default_max_results: int

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.authorization}

    def __repr__(self) -> str:
        return (
            f"JiraAuth(origin={self.origin!r}, authorization=<redacted>, "
            f"auth_mode={self.auth_mode!r}, rest_api_version={self.rest_api_version!r}, "
            f"transport={self.transport!r})"
        )


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
