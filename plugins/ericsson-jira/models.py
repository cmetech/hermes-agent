"""Bounded value objects and safe errors for the Ericsson Jira connector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
}


class JiraError(RuntimeError):
    """Stable classified failure that never includes remote or secret text."""

    def __init__(self, category: str) -> None:
        self.category = category if category in SAFE_ERROR_MESSAGES else "transient"
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
