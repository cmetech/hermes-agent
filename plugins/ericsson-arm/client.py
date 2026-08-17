"""Bounded Artifactory REST transport on the shared connector client."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Mapping

if __package__:
    from ._common.client import BoundedClient
    from ._common.errors import ConnectorError, category_for_status, remediation_for
    from ._common.transport import HttpxTransport, RequestControl, Response
    from .models import ArmAuth, ArmError
else:
    from _common.client import BoundedClient
    from _common.errors import ConnectorError, category_for_status, remediation_for
    from _common.transport import HttpxTransport, RequestControl, Response
    from models import ArmAuth, ArmError


_ACCESS_SCHEME = "cloudflare-access"
_ACCESS_HOST = "cloudflareaccess.com"

_ACCESS_REMEDIATION = (
    "Access to this Artifactory was refused at the edge, before the request "
    "reached Artifactory. This is normally an expired or missing mTLS client "
    "certificate rather than a problem with the Artifactory token. Check the "
    "client certificate and key configured for this profile."
)


def _is_access_challenge(response: Response) -> bool:
    """Return whether a response is Cloudflare Access refusing mTLS."""
    return (
        _ACCESS_SCHEME in response.header("www-authenticate").lower()
        or _ACCESS_HOST in response.header("location").lower()
    )


class _AccessChallengeTransport:
    """Surface Cloudflare Access before write ambiguity is decided.

    BoundedClient correctly treats ordinary write redirects as uncertain. An
    Access redirect is different: it proves the request was rejected at the
    edge, before it reached Artifactory, so its outcome is deterministic.
    """

    def __init__(self, transport) -> None:
        self._transport = transport

    @staticmethod
    def _response_or_access_error(response: Response) -> Response:
        if _is_access_challenge(response):
            raise ConnectorError(
                "edge_authentication", service="arm", outcome_uncertain=False
            )
        return response

    def request(self, *args, **kwargs) -> Response:
        return self._response_or_access_error(self._transport.request(*args, **kwargs))

    def request_with_controls(self, *args, control, **kwargs) -> Response:
        controlled_request = getattr(self._transport, "request_with_controls", None)
        if controlled_request is None:
            response = self._transport.request(*args, **kwargs)
        else:
            response = controlled_request(*args, control=control, **kwargs)
        return self._response_or_access_error(response)

    def close(self) -> None:
        self._transport.close()


@contextmanager
def _as_arm_error():
    """Translate shared errors at the connector boundary.

    ConnectorError.detail may quote caller input; ArmError guarantees no
    remote or secret text reaches the host.
    """
    try:
        yield
    except ConnectorError as exc:
        remediation = (
            _ACCESS_REMEDIATION
            if exc.category == "edge_authentication"
            else exc.remediation
        )
        raise ArmError(exc.category, remediation=remediation) from None


class ArmClient:
    def __init__(
        self,
        authentication: ArmAuth,
        *,
        transport=None,
        max_retries: int = 2,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.auth = authentication
        self.path_prefix = authentication.api_root
        self.headers = {
            authentication.auth_header_name: authentication.auth_header_value,
            "Accept": "application/json",
        }
        if transport is None:
            transport = HttpxTransport(
                base_url=authentication.origin,
                headers=self.headers,
                path_prefix=self.path_prefix,
                max_response_bytes=max_response_bytes,
                connect_timeout_seconds=5.0,
                tls_context=authentication.tls_context,
            )
        self._transport = transport
        with _as_arm_error():
            self._client = BoundedClient(
                _AccessChallengeTransport(transport),
                service="arm",
                max_retries=max_retries,
                total_timeout_seconds=float(authentication.request_timeout_seconds),
                request_timeout_seconds=float(authentication.request_timeout_seconds),
                cancel_check=cancel_check,
                clock=clock,
                sleep=sleep,
            )

    def __repr__(self) -> str:
        return f"ArmClient(origin={self.auth.origin!r})"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        self._client.close()

    def operation_deadline(self) -> float:
        return self._client.operation_deadline()

    def _validate(self, path: str) -> None:
        if not isinstance(path, str) or not path.startswith(self.path_prefix):
            raise ArmError("invalid_input")

    def _classify(self, response: Response) -> Response:
        """Raise for a non-2xx, naming the edge separately from the origin."""
        if 200 <= response.status < 300:
            return response
        if _is_access_challenge(response):
            raise ArmError("edge_authentication", remediation=_ACCESS_REMEDIATION)
        if 300 <= response.status < 400:
            raise ArmError(
                "invalid_remote_data",
                remediation=(
                    "Artifactory redirected the request instead of answering "
                    "it. Check that the base URL names the Artifactory origin."
                ),
            )
        category = category_for_status(response.status)
        raise ArmError(category, remediation=remediation_for(category, "arm"))

    def send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        content: Any | None = None,
        extra_headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
        classify: bool = True,
    ) -> Response:
        """Issue one request, classifying the response unless asked not to."""
        self._validate(path)
        with _as_arm_error():
            response = self._client.request(
                method,
                path,
                params=params,
                json_body=json_body,
                content=content,
                extra_headers=extra_headers,
                deadline=deadline,
                raise_on_status=False,
            )
        return self._classify(response) if classify else response

    def checksum_probe(
        self,
        path: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
        deadline: float | None = None,
    ) -> Response:
        """Send one checksum-only PUT and return any response status.

        A checksum deploy's 3xx and 5xx response is an input to its safe full
        upload fallback, not a mutation outcome to classify as ambiguous. This
        deliberately bypasses ``BoundedClient.request``'s write-status policy,
        while retaining its deadline, cancellation, transport controls, and
        Cloudflare edge classification.
        """
        self._validate(path)
        if deadline is None:
            deadline = self.operation_deadline()
        bounded = self._client
        try:
            bounded._check_breaker(path)
            remaining = bounded._remaining(deadline)
            control = RequestControl(
                deadline=deadline,
                cancel_check=bounded._cancel_check,
                clock=bounded._clock,
                service="arm",
            )
            request_options = {
                "params": None,
                "json_body": None,
                "timeout_seconds": min(remaining, bounded._request_timeout_seconds),
                "extra_headers": extra_headers,
            }
            controlled_request = getattr(self._transport, "request_with_controls", None)
            if controlled_request is None:
                response = self._transport.request("PUT", path, **request_options)
            else:
                response = controlled_request(
                    "PUT", path, control=control, **request_options
                )
        except ConnectorError as exc:
            category = "write_ambiguous" if exc.outcome_uncertain else exc.category
            raise ArmError(category, remediation=exc.remediation) from None
        except Exception:
            raise ArmError("write_ambiguous") from None
        try:
            control.remaining(outcome_uncertain=True)
        except ConnectorError:
            raise ArmError("write_ambiguous") from None
        if _is_access_challenge(response):
            raise ArmError("edge_authentication", remediation=_ACCESS_REMEDIATION)
        return response

    @staticmethod
    def _decode(response: Response) -> Any:
        if not response.body:
            return None
        if response.body.lstrip()[:1] == b"<":
            raise ArmError(
                "invalid_remote_data",
                remediation=(
                    "Artifactory returned HTML where JSON was expected, which "
                    "normally means an authentication interstitial answered "
                    "instead of the API."
                ),
            )
        try:
            return json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ArmError("invalid_remote_data") from None

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
    ) -> Any:
        return self._decode(
            self.send(
                method, path, params=params, json_body=json_body,
                deadline=deadline,
            )
        )

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        return self.request_json("GET", path, params=params, deadline=deadline)

    def post_text(
        self, path: str, text: str, *, deadline: float | None = None
    ) -> Any:
        """POST a plain-text body for Artifactory Query Language."""
        return self._decode(
            self.send(
                "POST",
                path,
                content=text.encode("utf-8"),
                extra_headers={"Content-Type": "text/plain"},
                deadline=deadline,
            )
        )
