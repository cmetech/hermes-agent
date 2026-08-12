"""Bounded direct GitLab REST transport."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx

if __package__:
    from .models import GitLabAuth, GitLabError
else:  # Standalone source tests import modules directly from the plugin root.
    from models import GitLabAuth, GitLabError


_RETRYABLE = frozenset({429, 502, 503, 504})
_STATUS_CATEGORY = {
    400: "invalid_input",
    401: "authentication",
    403: "permission",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}


class GitLabClient:
    """Synchronous client with finite deadlines, retries, and response bounds."""

    def __init__(
        self,
        authentication: GitLabAuth,
        *,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 20.0,
        total_timeout_seconds: float = 30.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_retries: int = 2,
        max_pages: int = 10,
        max_ref_pages: int = 10,
        max_diff_bytes: int = 30_000,
        max_changes: int = 100,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not 0 < connect_timeout_seconds <= 30
            or not 0 < read_timeout_seconds <= 120
            or not 0 < total_timeout_seconds <= 300
            or not 1 <= max_response_bytes <= 8 * 1024 * 1024
            or not 0 <= max_retries <= 4
            or not 1 <= max_pages <= 20
            or not 1 <= max_ref_pages <= 10
            or not 1 <= max_diff_bytes <= 1_000_000
            or not 1 <= max_changes <= 500
        ):
            raise GitLabError("invalid_configuration")
        self.auth = authentication
        self.max_response_bytes = int(max_response_bytes)
        self.max_retries = int(max_retries)
        self.max_pages = int(max_pages)
        self.max_ref_pages = int(max_ref_pages)
        self.max_diff_bytes = int(max_diff_bytes)
        self.max_changes = int(max_changes)
        self.total_timeout_seconds = float(total_timeout_seconds)
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        self._read_timeout_seconds = float(read_timeout_seconds)
        self._cancel_check = cancel_check or (lambda: False)
        self._clock = clock
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        options: dict[str, Any] = {
            "base_url": authentication.origin,
            "headers": {
                "PRIVATE-TOKEN": authentication.pat,
                "Accept": "application/json",
            },
            "timeout": timeout,
            "follow_redirects": False,
            "trust_env": False,
        }
        if authentication.tls_context is not None:
            options["verify"] = authentication.tls_context
        self._client = httpx.Client(**options)

    def __repr__(self) -> str:
        return f"GitLabClient(origin={self.auth.origin!r})"

    def close(self) -> None:
        self._client.close()

    def _check_cancelled(self, deadline: float) -> None:
        if self._cancel_check():
            raise GitLabError("cancelled")
        if self._clock() >= deadline:
            raise GitLabError("deadline")

    def operation_deadline(self) -> float:
        """Create one deadline to share across every request in an operation."""

        return self._clock() + self.total_timeout_seconds

    def _request_timeout(self, deadline: float) -> httpx.Timeout:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise GitLabError("deadline")
        return httpx.Timeout(
            connect=min(self._connect_timeout_seconds, remaining),
            read=min(self._read_timeout_seconds, remaining),
            write=min(self._read_timeout_seconds, remaining),
            pool=min(self._connect_timeout_seconds, remaining),
        )

    def _validate_path(self, path: str) -> None:
        if (
            not isinstance(path, str)
            or not path.startswith("/api/v4/")
            or len(path) > 8192
            or urlsplit(path).scheme
            or "\x00" in path
        ):
            raise GitLabError("invalid_input")

    @staticmethod
    def _error_for_status(status: int) -> GitLabError:
        if status in _STATUS_CATEGORY:
            return GitLabError(_STATUS_CATEGORY[status])
        if 500 <= status <= 599:
            return GitLabError("transient")
        return GitLabError("invalid_remote_data")

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Perform a proven-safe GET and decode one bounded JSON body."""

        value, _headers = self.get_json_page(path, params=params, deadline=deadline)
        return value

    def get_json_page(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> tuple[Any, Mapping[str, str]]:
        """Return bounded JSON plus a copied header map for pagination."""

        self._validate_path(path)
        if deadline is None:
            deadline = self.operation_deadline()
        attempt = 0
        while True:
            self._check_cancelled(deadline)
            response = None
            try:
                with self._client.stream(
                    "GET",
                    path,
                    params=params,
                    timeout=self._request_timeout(deadline),
                ) as current:
                    response = current
                    if 300 <= response.status_code < 400:
                        raise GitLabError("invalid_remote_data")
                    if response.status_code >= 400:
                        error = self._error_for_status(response.status_code)
                        if response.status_code in _RETRYABLE and attempt < self.max_retries:
                            attempt += 1
                            self._check_cancelled(deadline)
                            continue
                        raise error
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        self._check_cancelled(deadline)
                        if len(body) + len(chunk) > self.max_response_bytes:
                            raise GitLabError("capacity")
                        body.extend(chunk)
            except GitLabError:
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                self._check_cancelled(deadline)
                if attempt < self.max_retries:
                    attempt += 1
                    self._check_cancelled(deadline)
                    continue
                raise GitLabError("transient") from None
            try:
                return json.loads(bytes(body)), dict(response.headers)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise GitLabError("invalid_remote_data") from None
