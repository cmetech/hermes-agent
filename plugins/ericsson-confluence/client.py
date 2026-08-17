"""Bounded Confluence REST transport on the shared connector client."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

if __package__:
    from ._common.client import BoundedClient
    from ._common.errors import ConnectorError
    from ._common.transport import HttpxTransport
    from .models import ConfluenceAuth, ConfluenceError
else:
    from _common.client import BoundedClient
    from _common.errors import ConnectorError
    from _common.transport import HttpxTransport
    from models import ConfluenceAuth, ConfluenceError


@contextmanager
def _as_confluence_error():
    """Translate shared errors at the connector boundary.

    ConnectorError.detail may quote caller input; ConfluenceError guarantees
    no remote or secret text reaches the host.
    """
    try:
        yield
    except ConnectorError as exc:
        # Shared remediation text names the shared service and may include
        # remote details. Keep only connector-owned, static guidance.
        remediation = (
            "Update the Confluence token."
            if exc.category == "authentication"
            else None
        )
        raise ConfluenceError(exc.category, remediation=remediation) from None


class ConfluenceClient:
    def __init__(
        self,
        authentication: ConfluenceAuth,
        *,
        transport=None,
        max_retries: int = 2,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.auth = authentication
        # Derive the allow-listed prefix from the resolved API base, so a
        # Cloud instance permits /wiki/rest/api/ and a Data Center one does
        # not -- the allow-list stays exact rather than widening to both.
        self.path_prefix = urlsplit(authentication.api_base).path.rstrip("/") + "/"
        if transport is None:
            transport = HttpxTransport(
                base_url=authentication.origin,
                headers={
                    "Authorization": authentication.authorization,
                    "Accept": "application/json",
                },
                path_prefix=self.path_prefix,
                max_response_bytes=max_response_bytes,
                connect_timeout_seconds=5.0,
            )
        self._transport = transport
        with _as_confluence_error():
            self._client = BoundedClient(
                transport,
                service="confluence",
                max_retries=max_retries,
                total_timeout_seconds=float(authentication.request_timeout_seconds),
                request_timeout_seconds=float(authentication.request_timeout_seconds),
                cancel_check=cancel_check,
                clock=clock,
                sleep=sleep,
            )

    def __repr__(self) -> str:
        return f"ConfluenceClient(origin={self.auth.origin!r})"

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
            raise ConfluenceError("invalid_input")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        deadline: float | None = None,
    ) -> Any:
        self._validate(path)
        with _as_confluence_error():
            response = self._client.request(
                method, path, params=params, json_body=json_body,
                deadline=deadline,
            )
        if not response.body:
            return None
        try:
            return json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ConfluenceError("invalid_remote_data") from None

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        return self.request_json("GET", path, params=params, deadline=deadline)
