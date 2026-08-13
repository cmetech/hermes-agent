"""Jira REST client with bounded retry and compatibility classification."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping

import httpx

if __package__:
    from .models import JiraAuth, JiraError, TransportResponse
    from .transport import NativeTransport
else:
    from models import JiraAuth, JiraError, TransportResponse
    from transport import NativeTransport


_MAX_RESPONSE_BYTES = 1024 * 1024
_RETRYABLE = frozenset({429, 502, 503, 504})
_STATUS_CATEGORY = {
    400: "invalid_input",
    401: "authentication",
    403: "permission",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
}
_RESOURCE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")
_REST_UNSUPPORTED_MESSAGES = frozenset(
    {
        "REST API v3 endpoint is not available",
        "REST API v3 endpoint is unsupported",
    }
)
_CLOUDFLARE_1010 = re.compile(rb"(?:error\s*1010|access denied[^<]{0,80}1010)", re.I)


def _header(response: TransportResponse, name: str) -> str:
    lowered = name.lower()
    for key, value in response.headers.items():
        if key.lower() == lowered:
            return value
    return ""


def is_rest_version_unsupported(response: TransportResponse) -> bool:
    """Classify only the bounded, structured v3-missing compatibility response."""

    if response.status != 404 or len(response.body) > 8192:
        return False
    if "application/json" not in _header(response, "content-type").lower():
        return False
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    messages = payload.get("errorMessages") if isinstance(payload, dict) else None
    return (
        isinstance(messages, list)
        and len(messages) == 1
        and messages[0] in _REST_UNSUPPORTED_MESSAGES
    )


def is_cloudflare_1010_response(response: TransportResponse) -> bool:
    """Return true only for the approved bounded Cloudflare error-1010 response."""

    if response.status != 403 or len(response.body) > 8192:
        return False
    server = _header(response, "server").lower()
    ray = _header(response, "cf-ray")
    content_type = _header(response, "content-type").lower()
    return (
        server.startswith("cloudflare")
        and bool(ray)
        and ("text/html" in content_type or "text/plain" in content_type)
        and _CLOUDFLARE_1010.search(response.body) is not None
    )


class JiraClient:
    def __init__(
        self,
        authentication: JiraAuth,
        *,
        native_transport=None,
        max_retries: int = 2,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(max_retries) is not int or not 0 <= max_retries <= 4:
            raise JiraError("invalid_configuration")
        self._cancel_check = cancel_check or (lambda: False)
        self.auth = authentication
        self.max_retries = max_retries
        self._transport = native_transport or NativeTransport(
            authentication, cancel_check=self._cancel_check
        )
        self._clock = clock
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"JiraClient(origin={self.auth.origin!r})"

    def close(self) -> None:
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _check(self, deadline: float) -> float:
        if self._cancel_check():
            raise JiraError("cancelled")
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise JiraError("deadline")
        return remaining

    @staticmethod
    def _retry_delay(response: TransportResponse, attempt: int) -> float:
        retry_after = _header(response, "retry-after")
        if retry_after:
            try:
                value = float(retry_after)
            except ValueError:
                value = 0.0
            if 0 <= value <= 5:
                return value
        return min(0.5 * (2**attempt), 2.0)

    def _perform(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        deadline: float,
    ) -> TransportResponse:
        attempt = 0
        while True:
            remaining = self._check(deadline)
            try:
                result = self._transport.request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    timeout_seconds=min(remaining, self.auth.request_timeout_seconds),
                )
            except JiraError:
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                if method != "GET":
                    raise JiraError("write_ambiguous") from None
                if attempt >= self.max_retries:
                    raise JiraError("transient") from None
                attempt += 1
                continue
            self._check(deadline)
            if result.status in _RETRYABLE and method == "GET" and attempt < self.max_retries:
                delay = self._retry_delay(result, attempt)
                if delay >= self._check(deadline):
                    raise JiraError("deadline")
                self._sleep(delay)
                attempt += 1
                continue
            return result

    @staticmethod
    def _validate_resource(resource: str) -> None:
        if (
            not isinstance(resource, str)
            or not resource
            or len(resource) > 4096
            or resource.startswith("/")
            or "://" in resource
            or ".." in resource.split("/")
            or _RESOURCE.fullmatch(resource) is None
        ):
            raise JiraError("invalid_input")

    @staticmethod
    def _raise_status(response: TransportResponse, method: str) -> None:
        if response.status < 400:
            if 300 <= response.status < 400:
                raise JiraError("invalid_remote_data")
            return
        if method != "GET" and response.status in _RETRYABLE:
            raise JiraError("write_ambiguous")
        category = _STATUS_CATEGORY.get(response.status)
        if category is None:
            category = "transient" if 500 <= response.status <= 599 else "invalid_remote_data"
        raise JiraError(category)

    @staticmethod
    def _decode(response: TransportResponse, method: str) -> Any:
        JiraClient._raise_status(response, method)
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise JiraError("capacity")
        try:
            return json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise JiraError("invalid_remote_data") from None

    def rest_json(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        json_body_by_version: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        method = method.upper() if isinstance(method, str) else ""
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise JiraError("invalid_input")
        self._validate_resource(resource)
        if deadline is None:
            deadline = self._clock() + self.auth.request_timeout_seconds
        versions = (
            ("3", "2")
            if self.auth.rest_api_version == "auto"
            else (self.auth.rest_api_version,)
        )
        if json_body_by_version is not None and (
            not isinstance(json_body_by_version, Mapping)
            or set(json_body_by_version) != {"3", "2"}
        ):
            raise JiraError("invalid_input")

        def body_for(version: str):
            if json_body_by_version is None:
                return json_body
            return json_body_by_version[version]

        first = self._perform(
            method,
            f"/rest/api/{versions[0]}/{resource}",
            params=params,
            json_body=body_for(versions[0]),
            deadline=deadline,
        )
        if len(versions) == 2 and is_rest_version_unsupported(first):
            first = self._perform(
                method,
                f"/rest/api/{versions[1]}/{resource}",
                params=params,
                json_body=body_for(versions[1]),
                deadline=deadline,
            )
        return self._decode(first, method)
