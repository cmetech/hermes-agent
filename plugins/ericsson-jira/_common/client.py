"""Retry, deadline and breaker policy shared by the Ericsson connectors.

Finding F1 was that ericsson-gitlab retried HTTP 429 with no delay at all,
ignoring the Retry-After header the server had just sent -- turning one
rate-limit response into three.  ericsson-jira already did this correctly.
Both now share this one implementation, so the two cannot diverge again.

Method-aware retry is preserved from ericsson-jira and is deliberately
stricter than super-cli, whose retry sits at the http.RoundTripper layer
and will happily replay a POST.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

if __package__:
    from .errors import (
        RETRYABLE_STATUSES,
        ConnectorError,
        category_for_status,
    )
    from .transport import RequestControl, Response
else:
    from errors import RETRYABLE_STATUSES, ConnectorError, category_for_status
    from transport import RequestControl, Response

__all__ = ["BoundedClient"]

_MAX_HONOURED_RETRY_AFTER = 5.0
_MAX_BACKOFF = 2.0


class BoundedClient:
    """Wraps a transport with finite deadlines, retries and a breaker."""

    def __init__(
        self,
        transport,
        *,
        service: str,
        max_retries: int = 2,
        total_timeout_seconds: float = 30.0,
        request_timeout_seconds: float = 20.0,
        breaker_threshold: int = 5,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 <= max_retries <= 4:
            raise ConnectorError("invalid_configuration", service=service)
        if not 0 < total_timeout_seconds <= 300:
            raise ConnectorError("invalid_configuration", service=service)
        self._transport = transport
        self._service = service
        self._max_retries = int(max_retries)
        self._total_timeout_seconds = float(total_timeout_seconds)
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._breaker_threshold = int(breaker_threshold)
        self._cancel_check = cancel_check or (lambda: False)
        self._clock = clock
        self._sleep = sleep
        self._failures: dict[str, int] = {}

    def close(self) -> None:
        self._transport.close()

    def operation_deadline(self) -> float:
        """One deadline shared by every request in a logical operation."""
        return self._clock() + self._total_timeout_seconds

    def _remaining(self, deadline: float) -> float:
        if self._cancel_check():
            raise ConnectorError("cancelled", service=self._service)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ConnectorError("deadline", service=self._service)
        return remaining

    @staticmethod
    def retry_delay(response: Response | None, attempt: int) -> float:
        """Seconds to wait before retrying.

        Honours Retry-After when it is present and sane; a server asking for
        an hour is refused in favour of the normal backoff, because blocking
        an agent turn that long is worse than giving up.
        """
        raw = response.header("retry-after") if response is not None else ""
        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = -1.0
            if 0 <= value <= _MAX_HONOURED_RETRY_AFTER:
                return value
        return min(0.5 * (2 ** attempt), _MAX_BACKOFF)

    @staticmethod
    def _is_service_failure(status: int) -> bool:
        """Does this status say the service is unwell, or just answer us?

        Only service-health signals may trip the breaker. A 404 or a 401 is a
        deterministic answer about this particular request -- counting them
        would open the circuit on perfectly healthy traffic that happens to
        ask about missing issues.
        """
        return status >= 500 or status in RETRYABLE_STATUSES

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
        raise_on_status: bool = True,
        content: Any | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response:
        """Issue one request under retry, deadline and breaker policy.

        ``raise_on_status=False`` returns the final response instead of
        raising for a non-2xx status, for callers that must classify the body
        themselves (Jira's REST v2 fallback and Cloudflare-1010 detection).
        It suppresses *status* errors only: deadline, cancellation, capacity,
        circuit_open and write_ambiguous are client-side facts and still raise.
        """
        method = method.upper()
        if deadline is None:
            deadline = self.operation_deadline()
        self._check_breaker(path)
        idempotent = method == "GET"
        attempt = 0
        while True:
            remaining = self._remaining(deadline)
            control = RequestControl(
                deadline=deadline,
                cancel_check=self._cancel_check,
                clock=self._clock,
                service=self._service,
            )
            connector_failure = None
            transport_failure = False
            try:
                controlled_request = getattr(
                    self._transport, "request_with_controls", None
                )
                request_options = {
                    "params": params,
                    "json_body": json_body,
                    "timeout_seconds": min(
                        remaining, self._request_timeout_seconds
                    ),
                }
                if content is not None:
                    request_options["content"] = content
                if extra_headers is not None:
                    request_options["extra_headers"] = extra_headers
                if controlled_request is None:
                    response = self._transport.request(
                        method, path, **request_options
                    )
                else:
                    response = controlled_request(
                        method, path, control=control, **request_options
                    )
            except ConnectorError as exc:
                connector_failure = exc
            except Exception:
                transport_failure = True

            if connector_failure is not None or transport_failure:
                uncertain = transport_failure or bool(
                    connector_failure
                    and connector_failure.outcome_uncertain
                )
                if not idempotent:
                    if uncertain:
                        self._record_failure(path)
                        raise ConnectorError(
                            "write_ambiguous", service=self._service
                        ) from None
                    assert connector_failure is not None
                    raise connector_failure
                retryable_transport = transport_failure or bool(
                    connector_failure
                    and connector_failure.category == "transient"
                )
                if not retryable_transport:
                    assert connector_failure is not None
                    raise connector_failure
                remaining_after_failure = self._remaining(deadline)
                if attempt >= self._max_retries:
                    self._record_failure(path)
                    raise ConnectorError(
                        "transient", service=self._service
                    ) from None
                delay = self.retry_delay(None, attempt)
                if delay >= remaining_after_failure:
                    raise ConnectorError("deadline", service=self._service)
                self._sleep(delay)
                attempt += 1
                continue

            deterministic_client_response = 400 <= response.status < 500
            if not deterministic_client_response:
                post_dispatch_failure = None
                try:
                    control.remaining(outcome_uncertain=True)
                except ConnectorError as exc:
                    post_dispatch_failure = exc
                if post_dispatch_failure is not None:
                    if not idempotent:
                        self._record_failure(path)
                        raise ConnectorError(
                            "write_ambiguous", service=self._service
                        ) from None
                    raise post_dispatch_failure

            if not idempotent and (
                response.status >= 500 or 300 <= response.status < 400
            ):
                self._record_failure(path)
                raise ConnectorError(
                    "write_ambiguous", service=self._service
                )

            if response.status in RETRYABLE_STATUSES and idempotent:
                if attempt < self._max_retries:
                    delay = self.retry_delay(response, attempt)
                    if delay >= self._remaining(deadline):
                        raise ConnectorError("deadline", service=self._service)
                    self._sleep(delay)
                    attempt += 1
                    continue

            if self._is_service_failure(response.status):
                self._record_failure(path)
            else:
                self._clear_failures(path)

            if raise_on_status:
                if response.status >= 400:
                    raise ConnectorError(
                        category_for_status(response.status),
                        service=self._service,
                    )
                if 300 <= response.status < 400:
                    raise ConnectorError(
                        "invalid_remote_data", service=self._service
                    )
            return response

    # -- circuit breaker (Task 5 exercises these directly) ----------------

    def _breaker_key(self, path: str) -> str:
        return path.split("?", 1)[0]

    def _check_breaker(self, path: str) -> None:
        if self._failures.get(self._breaker_key(path), 0) >= self._breaker_threshold:
            raise ConnectorError("circuit_open", service=self._service)

    def _record_failure(self, path: str) -> None:
        key = self._breaker_key(path)
        self._failures[key] = self._failures.get(key, 0) + 1

    def _clear_failures(self, path: str) -> None:
        self._failures.pop(self._breaker_key(path), None)
