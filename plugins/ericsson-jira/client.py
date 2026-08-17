"""Jira REST client with bounded retry and compatibility classification."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping

if __package__:
    from ._common.client import BoundedClient
    from ._common.errors import (
        ConnectorError,
        category_for_status,
        remediation_for,
    )
    from ._common.transport import Response, validate_transport_path
    from .models import JiraAuth, JiraError, TransportResponse
    from .transport import NativeTransport, _JiraTransportFailure
else:
    from _common.client import BoundedClient
    from _common.errors import ConnectorError, category_for_status, remediation_for
    from _common.transport import Response, validate_transport_path
    from models import JiraAuth, JiraError, TransportResponse
    from transport import NativeTransport, _JiraTransportFailure


_MAX_RESPONSE_BYTES = 1024 * 1024
_RESOURCE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")
_REST_UNSUPPORTED_MESSAGES = frozenset(
    {
        "REST API v3 endpoint is not available",
        "REST API v3 endpoint is unsupported",
    }
)
_CLOUDFLARE_1010 = re.compile(rb"(?:error\s*1010|access denied[^<]{0,80}1010)", re.I)
_POTENTIALLY_UNCERTAIN = frozenset(
    {"transient", "capacity", "cancelled", "deadline", "invalid_remote_data"}
)


def _call_as_jira_error(operation):
    """Call shared code and detach its exception graph at the public boundary."""

    translated = None
    try:
        return operation()
    except ConnectorError as exc:
        translated = (exc.category, exc.remediation)
    category, remediation = translated
    raise JiraError(category, remediation=remediation) from None


class _SharedTransportAdapter:
    """Preserve Jira's native/curl transport behind the shared interface."""

    def __init__(self, transport) -> None:
        self._transport = transport

    def request(self, *args, **kwargs) -> Response:
        return self._request(lambda: self._transport.request(*args, **kwargs))

    def request_with_controls(self, *args, control, **kwargs) -> Response:
        controlled = getattr(self._transport, "request_with_controls", None)
        if controlled is None:
            operation = lambda: self._transport.request(*args, **kwargs)
        else:
            operation = lambda: controlled(*args, control=control, **kwargs)
        return self._request(operation)

    @staticmethod
    def _request(operation) -> Response:
        failure = None
        try:
            response = operation()
        except _JiraTransportFailure as exc:
            failure = (exc.category, exc.outcome_uncertain)
        except JiraError as exc:
            failure = (
                exc.category,
                bool(
                    getattr(exc, "outcome_uncertain", False)
                    or exc.category in _POTENTIALLY_UNCERTAIN
                ),
            )
        if failure is not None:
            category, outcome_uncertain = failure
            raise ConnectorError(
                category,
                service="jira",
                outcome_uncertain=outcome_uncertain,
            ) from None
        if isinstance(response, Response):
            return response
        return Response(response.status, response.headers, response.body)

    def close(self) -> None:
        category = None
        try:
            self._transport.close()
        except JiraError as exc:
            category = exc.category
        if category is not None:
            raise ConnectorError(category, service="jira") from None


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
        transport=None,
        max_retries: int = 2,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(max_retries) is not int or not 0 <= max_retries <= 4:
            raise JiraError("invalid_configuration")
        self.auth = authentication
        self.max_retries = max_retries
        chosen = transport or native_transport
        if chosen is None:
            chosen = NativeTransport(
                authentication, cancel_check=cancel_check, clock=clock
            )
        self._transport = chosen
        self._clock = clock
        self._resolved_rest_api_version: str | None = None
        self._client = _call_as_jira_error(
            lambda: BoundedClient(
                _SharedTransportAdapter(chosen),
                service="jira",
                max_retries=max_retries,
                total_timeout_seconds=float(authentication.request_timeout_seconds),
                request_timeout_seconds=float(authentication.request_timeout_seconds),
                cancel_check=cancel_check,
                clock=clock,
                sleep=sleep,
            )
        )

    def __repr__(self) -> str:
        return f"JiraClient(origin={self.auth.origin!r})"

    def close(self) -> None:
        _call_as_jira_error(self._client.close)

    def operation_deadline(self) -> float:
        return _call_as_jira_error(self._client.operation_deadline)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _perform(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        deadline: float,
    ) -> Response:
        return _call_as_jira_error(
            lambda: self._client.request(
                method,
                path,
                params=params,
                json_body=json_body,
                deadline=deadline,
                raise_on_status=False,
            )
        )

    @staticmethod
    def _validate_resource(resource: str) -> None:
        invalid = (
            not isinstance(resource, str)
            or not resource
            or len(resource) > 4096
            or resource.startswith("/")
            or "://" in resource
            or ".." in resource.split("/")
            or _RESOURCE.fullmatch(resource) is None
        )
        if not invalid:
            try:
                validate_transport_path(
                    f"/rest/api/3/{resource}",
                    path_prefix="/rest/api/",
                    allow_query=False,
                    reject_encoded_separators=True,
                )
            except ConnectorError:
                invalid = True
        if invalid:
            raise JiraError("invalid_input")

    @staticmethod
    def _raise_status(response: TransportResponse, method: str) -> None:
        if response.status < 400:
            if 300 <= response.status < 400:
                category = (
                    "invalid_remote_data" if method == "GET" else "write_ambiguous"
                )
                raise JiraError(category)
            return
        if method != "GET" and response.status >= 500:
            raise JiraError("write_ambiguous")
        category = category_for_status(response.status)
        raise JiraError(
            category, remediation=remediation_for(category, "jira")
        )

    @staticmethod
    def _decode(response: TransportResponse, method: str) -> Any:
        JiraClient._raise_status(response, method)
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise JiraError("capacity" if method == "GET" else "write_ambiguous")
        invalid_json = False
        try:
            return json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            invalid_json = True
        if invalid_json:
            category = "invalid_remote_data" if method == "GET" else "write_ambiguous"
            raise JiraError(category) from None

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
            deadline = self.operation_deadline()
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

    def rest_json_v2_mutation(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Perform exactly one mutation against Jira REST API v2.

        Mutations cannot use the automatic v3-to-v2 compatibility fallback:
        probing v3 and then writing to v2 would be two write attempts.  The
        callers of this narrow API select an endpoint whose contract is v2.
        """
        method = method.upper() if isinstance(method, str) else ""
        if method not in {"POST", "PUT", "DELETE"}:
            raise JiraError("invalid_input")
        self._validate_resource(resource)
        if deadline is None:
            deadline = self.operation_deadline()
        response = self._perform(
            method,
            f"/rest/api/2/{resource}",
            params=params,
            json_body=json_body,
            deadline=deadline,
        )
        if response.status == 204:
            self._raise_status(response, method)
            return None
        return self._decode(response, method)

    def rest_json_v2(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Perform one GET against REST API v2 without probing or fallback."""
        method = method.upper() if isinstance(method, str) else ""
        if method != "GET":
            raise JiraError("invalid_input")
        self._validate_resource(resource)
        if deadline is None:
            deadline = self.operation_deadline()
        response = self._perform(
            method,
            f"/rest/api/2/{resource}",
            params=params,
            json_body=None,
            deadline=deadline,
        )
        return self._decode(response, method)

    def _resolved_mutation_version(self, *, deadline: float) -> str:
        """Resolve auto configuration using only bounded GET requests.

        A version fallback after a mutation could apply the change twice.  The
        version is therefore established before a mutation, cached for this
        client, and never inferred from a mutation response.
        """
        configured = self.auth.rest_api_version
        if configured in {"2", "3"}:
            return configured
        if self._resolved_rest_api_version is not None:
            return self._resolved_rest_api_version

        v3 = self._perform(
            "GET",
            "/rest/api/3/serverInfo",
            params=None,
            json_body=None,
            deadline=deadline,
        )
        if is_rest_version_unsupported(v3):
            v2 = self._perform(
                "GET",
                "/rest/api/2/serverInfo",
                params=None,
                json_body=None,
                deadline=deadline,
            )
            self._decode(v2, "GET")
            self._resolved_rest_api_version = "2"
        else:
            self._decode(v3, "GET")
            self._resolved_rest_api_version = "3"
        return self._resolved_rest_api_version

    def rest_json_versioned_mutation(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body_by_version: Mapping[str, Any],
        empty_success_statuses: frozenset[int] = frozenset({204}),
        deadline: float | None = None,
    ) -> Any:
        """Perform exactly one mutation using a safely selected REST version.

        Explicit configuration selects that version directly.  Auto mode
        performs and caches a non-mutating ``serverInfo`` probe before issuing
        exactly one write with its matching version-specific body.
        """
        method = method.upper() if isinstance(method, str) else ""
        if method not in {"POST", "PUT", "DELETE"}:
            raise JiraError("invalid_input")
        self._validate_resource(resource)
        if (
            not isinstance(json_body_by_version, Mapping)
            or set(json_body_by_version) != {"3", "2"}
        ):
            raise JiraError("invalid_input")
        if (
            type(empty_success_statuses) is not frozenset
            or not empty_success_statuses
            or any(
                type(status) is not int or not 200 <= status < 300
                for status in empty_success_statuses
            )
        ):
            raise JiraError("invalid_input")
        if deadline is None:
            deadline = self.operation_deadline()
        version = self._resolved_mutation_version(deadline=deadline)
        response = self._perform(
            method,
            f"/rest/api/{version}/{resource}",
            params=params,
            json_body=json_body_by_version[version],
            deadline=deadline,
        )
        if response.status in empty_success_statuses and response.body == b"":
            self._raise_status(response, method)
            return None
        return self._decode(response, method)

    def rest_json_resolved_version(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Read once at an explicit or already-resolved REST API version.

        This deliberately does not probe or fall back.  It is for bounded
        reconciliation after ``rest_json_versioned_mutation`` has selected the
        version, preventing an ambiguous write from expanding to two issue
        reads in auto Data Center mode.
        """
        method = method.upper() if isinstance(method, str) else ""
        if method != "GET":
            raise JiraError("invalid_input")
        self._validate_resource(resource)
        if deadline is None:
            deadline = self.operation_deadline()
        configured = self.auth.rest_api_version
        if configured in {"2", "3"}:
            version = configured
        else:
            version = self._resolved_rest_api_version
            if version is None:
                raise JiraError("invalid_configuration")
        response = self._perform(
            method,
            f"/rest/api/{version}/{resource}",
            params=params,
            json_body=None,
            deadline=deadline,
        )
        return self._decode(response, method)
