"""Bounded direct GitLab REST transport, on the shared connector client.

Retry, Retry-After handling, backoff, deadlines and the circuit breaker now
come from _common.client.BoundedClient.  Before this, a 429 was retried
immediately with no delay (finding F1) -- the Jira connector had always done
it correctly and this one had not, which is precisely the divergence a
shared client prevents.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import time
from typing import Any, Callable, Mapping

import httpx

if __package__:
    from ._common.client import BoundedClient
    from ._common.errors import (
        RETRYABLE_STATUSES,
        ConnectorError,
        category_for_status,
    )
    from ._common.transport import HttpxTransport
    from .models import GitLabAuth, GitLabError
else:
    from _common.client import BoundedClient
    from _common.errors import RETRYABLE_STATUSES, ConnectorError, category_for_status
    from _common.transport import HttpxTransport
    from models import GitLabAuth, GitLabError


def _call_as_gitlab_error(operation):
    """Call shared code and detach its exception graph at the boundary.

    ConnectorError.detail may quote caller input, and GitLabError exists to
    guarantee no remote or secret text ever reaches the host. Translating
    here keeps that guarantee while carrying the remediation string through.
    """
    translated = None
    try:
        return operation()
    except ConnectorError as exc:
        translated = (exc.category, exc.remediation)
    category, remediation = translated
    raise GitLabError(category, remediation=remediation) from None


class _ClientCompatibilityAdapter:
    """Keep the raw stream hook consumed by legacy write operations."""

    def __init__(self, bounded: BoundedClient, raw_client=None) -> None:
        self._bounded = bounded
        self._raw_client = raw_client

    def request(self, *args, **kwargs):
        return self._bounded.request(*args, **kwargs)

    def operation_deadline(self) -> float:
        return self._bounded.operation_deadline()

    def close(self) -> None:
        self._bounded.close()

    @contextmanager
    def stream(self, method: str, *args, **kwargs):
        if self._raw_client is None:
            raise RuntimeError("transport does not expose a streaming client")
        mutating = method.upper() not in {"GET", "HEAD"}
        try:
            with self._raw_client.stream(method, *args, **kwargs) as response:
                if mutating and response.status_code in RETRYABLE_STATUSES:
                    raise GitLabError("write_ambiguous")
                yield response
        except GitLabError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            if mutating:
                raise GitLabError("write_ambiguous") from None
            raise


class GitLabClient:
    """Synchronous client with finite deadlines, retries and response bounds."""

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
        sleep: Callable[[float], None] = time.sleep,
        transport=None,
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
        # operations.py reads these directly; they are part of the contract.
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

        if transport is None:
            transport = HttpxTransport(
                base_url=authentication.origin,
                headers={
                    "PRIVATE-TOKEN": authentication.pat,
                    "Accept": "application/json",
                },
                path_prefix="/api/v4/",
                max_response_bytes=max_response_bytes,
                connect_timeout_seconds=connect_timeout_seconds,
                tls_context=getattr(authentication, "tls_context", None),
            )
        self._transport = transport
        bounded_client = _call_as_gitlab_error(
            lambda: BoundedClient(
                transport,
                service="gitlab",
                max_retries=max_retries,
                total_timeout_seconds=total_timeout_seconds,
                request_timeout_seconds=read_timeout_seconds,
                cancel_check=cancel_check,
                clock=clock,
                sleep=sleep,
            )
        )
        self._client = _ClientCompatibilityAdapter(
            bounded_client, getattr(transport, "_client", None)
        )

    def __repr__(self) -> str:
        return f"GitLabClient(origin={self.auth.origin!r})"

    def close(self) -> None:
        _call_as_gitlab_error(self._client.close)

    def operation_deadline(self) -> float:
        return self._client.operation_deadline()

    def _check_cancelled(self, deadline: float) -> None:
        if self._cancel_check():
            raise GitLabError("cancelled")
        if self._clock() >= deadline:
            raise GitLabError("deadline")

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
        validator = getattr(self._transport, "_validate_path", None)
        if validator is None:
            return
        _call_as_gitlab_error(lambda: validator(path))

    @staticmethod
    def _error_for_status(status: int) -> GitLabError:
        shared = ConnectorError(category_for_status(status), service="gitlab")
        return GitLabError(shared.category, remediation=shared.remediation)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
    ) -> Any:
        value, _headers = self._request(
            method, path, params=params, json_body=json_body, deadline=deadline
        )
        return value

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ):
        """Fetch a non-JSON body (job traces, raw file contents).

        Still bounded by the transport's max_response_bytes, so a runaway
        job log cannot exhaust memory.
        """
        return _call_as_gitlab_error(
            lambda: self._client.request(
                method, path, params=params, json_body=None, deadline=deadline
            )
        )

    def _request(self, method, path, *, params, json_body, deadline):
        _status, value, headers = self.request_json_response(
            method,
            path,
            params=params,
            json_body=json_body,
            deadline=deadline,
        )
        return value, headers

    def request_json_response(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
        raise_on_status: bool = True,
    ) -> tuple[int, Any, Mapping[str, str]]:
        """Return status, bounded decoded JSON and headers via the public client."""

        if deadline is None:
            deadline = self.operation_deadline()
        response = _call_as_gitlab_error(
            lambda: self._client.request(
                method,
                path,
                params=params,
                json_body=json_body,
                deadline=deadline,
                raise_on_status=raise_on_status,
            )
        )
        invalid_json = False
        try:
            value = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            invalid_json = True
        if invalid_json:
            mutating = method.upper() not in {"GET", "HEAD"}
            if not raise_on_status and response.status >= 400:
                value = None
            elif mutating:
                raise GitLabError("write_ambiguous") from None
            else:
                raise GitLabError("invalid_remote_data") from None
        return response.status, value, response.headers

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        value, _headers = self.get_json_page(
            path, params=params, deadline=deadline
        )
        return value

    def get_json_page(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> tuple[Any, Mapping[str, str]]:
        """Return bounded JSON plus headers, for X-Total pagination."""
        return self._request(
            "GET", path, params=params, json_body=None, deadline=deadline
        )
