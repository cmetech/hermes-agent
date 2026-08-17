"""HTTP transport with bounded responses.

Deliberately separate from the retry/breaker policy in client.py: the Jira
connector reaches its instance through a curl fallback on hosts where the
native client is blocked by Cloudflare, and that transport must be
swappable without duplicating retry logic.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Mapping
from urllib.parse import unquote_to_bytes, urlsplit

import httpx

if __package__:
    from .errors import ConnectorError
else:  # standalone source tests import modules directly from the plugin root
    from errors import ConnectorError

__all__ = [
    "Response",
    "RequestControl",
    "HttpxTransport",
    "validate_transport_path",
]

_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.I)
_MAX_DECODE_PASSES = 4


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str:
        """Case-insensitive header lookup; missing headers read as empty."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return ""


@dataclass(frozen=True)
class RequestControl:
    """Absolute operation budget checked before and during response streaming."""

    deadline: float
    cancel_check: Callable[[], bool]
    clock: Callable[[], float] = time.monotonic
    service: str | None = None

    def remaining(self, *, outcome_uncertain: bool = False) -> float:
        if self.cancel_check():
            raise ConnectorError(
                "cancelled",
                service=self.service,
                outcome_uncertain=outcome_uncertain,
            )
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ConnectorError(
                "deadline",
                service=self.service,
                outcome_uncertain=outcome_uncertain,
            )
        return remaining


def _decoded_path(path: str, *, reject_encoded_separators: bool) -> str:
    current = path
    for _ in range(_MAX_DECODE_PASSES):
        if _MALFORMED_ESCAPE.search(current) or (
            reject_encoded_separators and _ENCODED_SEPARATOR.search(current)
        ):
            raise ValueError("unsafe percent encoding")
        try:
            decoded = unquote_to_bytes(current).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("path is not valid UTF-8") from exc
        if decoded == current:
            return decoded
        current = decoded
    raise ValueError("path encoding is too deeply nested")


def validate_transport_path(
    path: str,
    *,
    path_prefix: str,
    allow_query: bool = True,
    maximum_bytes: int = 8192,
    reject_encoded_separators: bool = False,
) -> None:
    """Reject paths whose decoded form can change routing semantics."""

    try:
        parsed = urlsplit(path) if isinstance(path, str) else None
        decoded = (
            _decoded_path(
                parsed.path,
                reject_encoded_separators=reject_encoded_separators,
            )
            if parsed is not None
            else ""
        )
        encoded_length = len(path.encode("utf-8")) if isinstance(path, str) else 0
    except (UnicodeEncodeError, ValueError):
        parsed = None
        decoded = ""
        encoded_length = maximum_bytes + 1
    if (
        parsed is None
        or not path.startswith(path_prefix)
        or not decoded.startswith(path_prefix)
        or encoded_length > maximum_bytes
        or parsed.scheme
        or parsed.netloc
        or (parsed.query and not allow_query)
        or parsed.fragment
        or any(character in decoded for character in ("\\", "\x00", "#", "?"))
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        raise ConnectorError("invalid_input")


class HttpxTransport:
    """Synchronous transport that streams and caps every response body.

    ``trust_env=False`` and ``follow_redirects=False`` are both deliberate:
    the former stops a corporate proxy environment from silently rerouting
    credentialed requests, the latter stops a redirect from replaying the
    Authorization header to another host.
    """

    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
        path_prefix: str,
        max_response_bytes: int = 2 * 1024 * 1024,
        connect_timeout_seconds: float = 5.0,
        tls_context: Any = None,
        mock_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._path_prefix = path_prefix
        self._max_response_bytes = int(max_response_bytes)
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        options: dict[str, Any] = {
            "base_url": base_url,
            "headers": dict(headers),
            "follow_redirects": False,
            "trust_env": False,
        }
        if tls_context is not None:
            options["verify"] = tls_context
        if mock_transport is not None:
            options["transport"] = mock_transport
        self._client = httpx.Client(**options)

    def close(self) -> None:
        self._client.close()

    def _validate_path(self, path: str) -> None:
        validate_transport_path(path, path_prefix=self._path_prefix)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        timeout_seconds: float,
        content: Any | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response:
        return self.request_with_controls(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            content=content,
            extra_headers=extra_headers,
            control=None,
        )

    def request_with_controls(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        timeout_seconds: float,
        control: RequestControl | None,
        content: Any | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response:
        self._validate_path(path)
        if content is not None and json_body is not None:
            raise ConnectorError(
                "invalid_input",
                detail="content and json_body are mutually exclusive",
            )
        if control is not None:
            timeout_seconds = min(
                timeout_seconds,
                control.remaining(outcome_uncertain=False),
            )
        timeout = httpx.Timeout(
            connect=min(self._connect_timeout_seconds, timeout_seconds),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=min(self._connect_timeout_seconds, timeout_seconds),
        )
        invalid_request = False
        try:
            request = self._client.build_request(
                method,
                path,
                params=params,
                json=json_body,
                content=content,
                headers=dict(extra_headers) if extra_headers else None,
                timeout=timeout,
            )
        except (TypeError, ValueError, OverflowError, httpx.InvalidURL):
            invalid_request = True
        if invalid_request:
            raise ConnectorError("invalid_input") from None
        if control is not None:
            final_remaining = control.remaining(outcome_uncertain=False)
            request_timeout = min(timeout_seconds, final_remaining)
            request.extensions["timeout"] = {
                "connect": min(self._connect_timeout_seconds, request_timeout),
                "read": request_timeout,
                "write": request_timeout,
                "pool": min(self._connect_timeout_seconds, request_timeout),
            }
        body = bytearray()
        with closing(self._client.send(request, stream=True)) as response:
            headers = dict(response.headers)
            status = response.status_code
            deterministic_client_response = 400 <= status < 500
            try:
                if control is not None:
                    control.remaining(outcome_uncertain=True)
                for chunk in response.iter_bytes():
                    if control is not None:
                        control.remaining(outcome_uncertain=True)
                    if len(body) + len(chunk) > self._max_response_bytes:
                        raise ConnectorError("capacity", outcome_uncertain=True)
                    body.extend(chunk)
            except (ConnectorError, httpx.RequestError):
                if not deterministic_client_response:
                    raise
                body.clear()
        return Response(status=status, headers=headers, body=bytes(body))
