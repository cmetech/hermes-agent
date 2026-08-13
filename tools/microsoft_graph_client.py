"""Reusable Microsoft Graph REST client helpers."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from agent.retry_utils import parse_retry_after_seconds
from tools.microsoft_graph_auth import GraphCredentials, MicrosoftGraphTokenProvider
from tools.microsoft_graph_identity import GraphAccessTokenProvider


DEFAULT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_UPLOAD_FRAGMENT_BYTES = 320 * 1024


class MicrosoftGraphClientError(RuntimeError):
    """Base class for Graph client failures."""


class MicrosoftGraphAPIError(MicrosoftGraphClientError):
    """Raised when a Graph API request fails."""

    def __init__(
        self,
        status_code: int,
        method: str,
        url: str,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        payload: Any = None,
    ) -> None:
        self.status_code = status_code
        self.method = method
        self.url = url
        self.retry_after_seconds = retry_after_seconds
        self.payload = payload
        super().__init__(
            f"Microsoft Graph API error {status_code} for {method} {url}: {message}"
        )


class MicrosoftGraphLimitError(MicrosoftGraphClientError):
    """Raised when a caller-supplied transfer or polling bound is exceeded."""


class MicrosoftGraphCancelledError(MicrosoftGraphClientError):
    """Raised when cooperative cancellation stops an operation."""


class MicrosoftGraphDeadlineError(MicrosoftGraphClientError):
    """Raised when an operation exceeds its monotonic deadline."""


class MicrosoftGraphUploadSessionExpired(MicrosoftGraphClientError):
    """Raised when Graph reports that an upload session is no longer usable."""


class MicrosoftGraphAmbiguousWriteError(MicrosoftGraphClientError):
    """Raised when a write may have completed but no response was received."""


class MicrosoftGraphAsyncOperationError(MicrosoftGraphClientError):
    """Raised when a monitored asynchronous operation fails."""


class MicrosoftGraphClient:
    """Minimal async Microsoft Graph client with retries and pagination."""

    def __init__(
        self,
        token_provider: GraphAccessTokenProvider,
        *,
        base_url: str = DEFAULT_GRAPH_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        user_agent: str = "Hermes-Agent/graph-client",
    ) -> None:
        self.token_provider = token_provider
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        self.user_agent = user_agent

    @classmethod
    def from_env(cls, **kwargs: Any) -> "MicrosoftGraphClient":
        credentials = GraphCredentials.from_env()
        provider = MicrosoftGraphTokenProvider(credentials)
        return cls(provider, **kwargs)

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._request("GET", path, params=params, headers=headers)
        return self._decode_json(response)

    async def post_json(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._request("POST", path, json_body=json_body, headers=headers)
        return self._decode_json(response)

    async def patch_json(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._request("PATCH", path, json_body=json_body, headers=headers)
        if response.status_code == 204 or not response.content:
            return {}
        return self._decode_json(response)

    async def delete(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request("DELETE", path, headers=headers)
        if response.status_code == 204 or not response.content:
            return {"deleted": True, "status_code": response.status_code}
        return self._decode_json(response)

    async def iterate_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        next_url: str | None = self._resolve_url(path)
        next_params = dict(params or {})
        while next_url:
            response = await self._request(
                "GET",
                next_url,
                params=next_params or None,
                headers=headers,
            )
            payload = self._decode_json(response)
            if not isinstance(payload, dict):
                raise MicrosoftGraphClientError(
                    f"Expected paginated Graph response dict, got {type(payload).__name__}."
                )
            yield payload
            next_url = payload.get("@odata.nextLink")
            if next_url is not None:
                if not isinstance(next_url, str):
                    raise MicrosoftGraphClientError(
                        "Microsoft Graph pagination returned an invalid nextLink."
                    )
                self._validate_graph_origin(next_url)
            next_params = {}

    async def collect_paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> list[Any]:
        items: list[Any] = []
        async for page in self.iterate_pages(path, params=params, headers=headers):
            value = page.get("value")
            if isinstance(value, list):
                items.extend(value)
        return items

    async def download_to_file(
        self,
        path: str,
        destination: str | Path,
        *,
        headers: dict[str, str] | None = None,
        chunk_size: int = 65536,
        max_bytes: int | None = None,
        deadline: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Download a Graph resource to disk, streaming the response body.

        The body is written chunk-by-chunk via ``response.aiter_bytes`` with
        the ``httpx.AsyncClient`` kept open for the duration of the iteration,
        so recordings and other large artifacts do not need to fit in memory.
        """
        url = self._resolve_url(path)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_suffix(target.suffix + ".part")
        tmp_target.unlink(missing_ok=True)

        attempt = 0
        last_error: Exception | None = None

        try:
            while attempt <= self.max_retries:
                self._check_control(deadline=deadline, cancel_check=cancel_check)
                token = await self.token_provider.get_access_token(
                    force_refresh=attempt > 0 and self._should_refresh_token(last_error)
                )
                request_headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                }
                if headers:
                    request_headers.update(headers)

                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout),
                        transport=self._transport,
                    ) as client:
                        async with client.stream(
                            "GET",
                            url,
                            headers=request_headers,
                        ) as response:
                            if response.status_code >= 400:
                                await response.aread()
                                api_error = self._build_api_error("GET", url, response)
                                last_error = api_error

                                if (
                                    response.status_code == 401
                                    and attempt < self.max_retries
                                ):
                                    self.token_provider.clear_cache()
                                    await self._sleep(
                                        self._retry_delay(response, attempt)
                                    )
                                    attempt += 1
                                    continue

                                if (
                                    self._should_retry(response)
                                    and attempt < self.max_retries
                                ):
                                    await self._sleep(
                                        self._retry_delay(response, attempt)
                                    )
                                    attempt += 1
                                    continue

                                raise api_error

                            content_type = response.headers.get("content-type")
                            total = 0
                            with tmp_target.open("wb") as handle:
                                async for chunk in response.aiter_bytes(
                                    chunk_size=chunk_size
                                ):
                                    self._check_control(
                                        deadline=deadline,
                                        cancel_check=cancel_check,
                                    )
                                    if chunk:
                                        total += len(chunk)
                                        if max_bytes is not None and total > max_bytes:
                                            raise MicrosoftGraphLimitError(
                                                "Microsoft Graph download exceeded its byte limit."
                                            )
                                        handle.write(chunk)
                except httpx.HTTPError:
                    last_error = MicrosoftGraphClientError(
                        "Microsoft Graph download transport failed."
                    )
                    tmp_target.unlink(missing_ok=True)
                    if attempt >= self.max_retries:
                        raise MicrosoftGraphClientError(
                            f"Microsoft Graph download failed for GET {self._safe_url(url)}."
                        ) from None
                    await self._sleep(self._retry_delay(None, attempt))
                    attempt += 1
                    continue
                os.replace(tmp_target, target)
                return {
                    "path": str(target),
                    "size_bytes": target.stat().st_size,
                    "content_type": content_type,
                }
        finally:
            tmp_target.unlink(missing_ok=True)

    async def upload_small(
        self,
        path: str,
        data: bytes,
        *,
        max_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """PUT a bounded in-memory payload to a Graph content endpoint."""
        if len(data) > max(0, int(max_bytes)):
            raise MicrosoftGraphLimitError(
                "Microsoft Graph small upload exceeded its byte limit."
            )
        upload_headers = {"Content-Type": "application/octet-stream"}
        if headers:
            upload_headers.update(headers)
        response = await self._request(
            "PUT",
            path,
            content=data,
            headers=upload_headers,
        )
        return self._decode_json(response)

    async def upload_via_session(
        self,
        create_session_path: str,
        source: str | Path,
        *,
        max_bytes: int,
        chunk_size: int,
        max_chunks: int,
        conflict_behavior: str = "fail",
        deadline: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Stream a local file through a Graph upload session without bearer leakage."""
        if chunk_size <= 0 or chunk_size % GRAPH_UPLOAD_FRAGMENT_BYTES:
            raise MicrosoftGraphLimitError(
                "Upload chunk size must be a positive multiple of 320 KiB."
            )
        if max_chunks <= 0:
            raise MicrosoftGraphLimitError("Upload chunk count must be positive.")
        if conflict_behavior not in {"fail", "replace", "rename"}:
            raise MicrosoftGraphClientError("Unsupported upload conflict behavior.")
        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise MicrosoftGraphClientError("Upload source must be a regular file.")
        total = source_path.stat().st_size
        if total > max(0, int(max_bytes)):
            raise MicrosoftGraphLimitError("Upload source exceeded its byte limit.")
        self._check_control(deadline=deadline, cancel_check=cancel_check)
        session = await self.post_json(
            create_session_path,
            json_body={
                "item": {"@microsoft.graph.conflictBehavior": conflict_behavior}
            },
        )
        upload_url = session.get("uploadUrl") if isinstance(session, dict) else None
        parsed_upload_url = httpx.URL(upload_url) if isinstance(upload_url, str) else None
        if (
            parsed_upload_url is None
            or parsed_upload_url.scheme != "https"
            or not parsed_upload_url.host
            or bool(parsed_upload_url.username)
            or bool(parsed_upload_url.password)
        ):
            raise MicrosoftGraphClientError(
                "Microsoft Graph returned an invalid upload session URL."
            )

        offset = 0
        chunks_sent = 0
        with source_path.open("rb") as handle:
            while offset < total:
                self._check_control(deadline=deadline, cancel_check=cancel_check)
                if chunks_sent >= max_chunks:
                    raise MicrosoftGraphLimitError(
                        "Microsoft Graph upload exceeded its chunk limit."
                    )
                handle.seek(offset)
                chunk = handle.read(min(chunk_size, total - offset))
                if not chunk:
                    raise MicrosoftGraphClientError(
                        "Upload source ended before its declared size."
                    )
                end = offset + len(chunk) - 1
                response = await self._put_upload_chunk(
                    upload_url,
                    chunk,
                    content_range=f"bytes {offset}-{end}/{total}",
                    deadline=deadline,
                    cancel_check=cancel_check,
                )
                chunks_sent += 1
                if response.status_code in {200, 201}:
                    payload = self._decode_json(response)
                    return payload if isinstance(payload, dict) else {"completed": True}
                if response.status_code in {404, 410}:
                    raise MicrosoftGraphUploadSessionExpired(
                        "Microsoft Graph upload session expired."
                    )
                if response.status_code != 202:
                    raise self._build_api_error("PUT", upload_url, response)
                payload = self._decode_json(response)
                offset = self._next_upload_offset(payload, total)

        raise MicrosoftGraphAmbiguousWriteError(
            "Microsoft Graph upload ended without a terminal response."
        )

    async def _put_upload_chunk(
        self,
        upload_url: str,
        chunk: bytes,
        *,
        content_range: str,
        deadline: float | None,
        cancel_check: Callable[[], bool] | None,
    ) -> httpx.Response:
        """Send one idempotent session range, retrying only definite responses."""
        for attempt in range(self.max_retries + 1):
            self._check_control(deadline=deadline, cancel_check=cancel_check)
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout),
                    transport=self._transport,
                ) as client:
                    response = await client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": content_range,
                        },
                    )
            except httpx.HTTPError:
                raise MicrosoftGraphAmbiguousWriteError(
                    "Microsoft Graph upload completion is ambiguous; reconcile the existing session."
                ) from None
            if not self._should_retry(response) or attempt >= self.max_retries:
                return response
            await self._sleep(self._retry_delay(response, attempt))
        raise MicrosoftGraphClientError("Microsoft Graph upload retry loop exhausted.")

    async def poll_async_operation(
        self,
        location: str,
        *,
        max_polls: int,
        deadline: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Poll a trusted Graph monitor URL until a terminal bounded result."""
        self._validate_graph_origin(location)
        if max_polls <= 0:
            raise MicrosoftGraphLimitError("Async operation poll count must be positive.")
        for poll_number in range(max_polls):
            self._check_control(deadline=deadline, cancel_check=cancel_check)
            response = await self._request("GET", location)
            payload = self._decode_json(response)
            if not isinstance(payload, dict):
                raise MicrosoftGraphAsyncOperationError(
                    "Microsoft Graph async operation returned an invalid status."
                )
            status = str(payload.get("status") or "").strip().casefold()
            if status in {"completed", "succeeded"}:
                return payload
            if status in {"failed", "cancelled", "canceled", "deletefailed"}:
                raise MicrosoftGraphAsyncOperationError(
                    f"Microsoft Graph async operation ended with status {status}."
                )
            if poll_number + 1 < max_polls:
                retry_after = parse_retry_after_seconds(response.headers)
                await self._sleep(
                    retry_after if retry_after is not None else max(0.0, poll_interval)
                )
        raise MicrosoftGraphLimitError(
            "Microsoft Graph async operation exceeded its poll limit."
        )

    async def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = self._resolve_url(path_or_url)
        attempt = 0
        last_error: Exception | None = None

        while attempt <= self.max_retries:
            token = await self.token_provider.get_access_token(
                force_refresh=attempt > 0 and self._should_refresh_token(last_error)
            )
            request_headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
            if json_body is not None:
                request_headers["Content-Type"] = "application/json"
            if headers:
                request_headers.update(headers)

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout),
                    transport=self._transport,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        content=content,
                        headers=request_headers,
                    )
            except httpx.HTTPError:
                last_error = MicrosoftGraphClientError(
                    "Microsoft Graph request transport failed."
                )
                if attempt >= self.max_retries:
                    raise MicrosoftGraphClientError(
                        f"Microsoft Graph request failed for {method} {self._safe_url(url)}."
                    ) from None
                await self._sleep(self._retry_delay(None, attempt))
                attempt += 1
                continue

            if response.status_code < 400:
                return response

            api_error = self._build_api_error(method, url, response)
            last_error = api_error

            if response.status_code == 401 and attempt < self.max_retries:
                self.token_provider.clear_cache()
                await self._sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue

            if self._should_retry(response) and attempt < self.max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue

            raise api_error

        raise MicrosoftGraphClientError(
            f"Microsoft Graph request exhausted retries for {method} {url}."
        )

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            self._validate_graph_origin(path_or_url)
            return path_or_url
        path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        return f"{self.base_url}{path}"

    def _validate_graph_origin(self, url: str) -> None:
        candidate = httpx.URL(url)
        base = httpx.URL(self.base_url)
        if (
            candidate.scheme.casefold(),
            candidate.host.casefold() if candidate.host else None,
            candidate.port,
        ) != (
            base.scheme.casefold(),
            base.host.casefold() if base.host else None,
            base.port,
        ):
            raise MicrosoftGraphClientError(
                "Microsoft Graph URL is outside the configured Graph origin."
            )

    def _check_control(
        self,
        *,
        deadline: float | None,
        cancel_check: Callable[[], bool] | None,
    ) -> None:
        if cancel_check is not None and cancel_check():
            raise MicrosoftGraphCancelledError("Microsoft Graph operation was cancelled.")
        if deadline is not None and self._clock() >= deadline:
            raise MicrosoftGraphDeadlineError("Microsoft Graph operation deadline expired.")

    @staticmethod
    def _next_upload_offset(payload: Any, total: int) -> int:
        ranges = payload.get("nextExpectedRanges") if isinstance(payload, dict) else None
        if not isinstance(ranges, list) or not ranges or not isinstance(ranges[0], str):
            raise MicrosoftGraphClientError(
                "Microsoft Graph upload session omitted its resume offset."
            )
        match = re.fullmatch(r"(\d+)-(?:\d+)?", ranges[0].strip())
        if match is None:
            raise MicrosoftGraphClientError(
                "Microsoft Graph upload session returned an invalid resume offset."
            )
        offset = int(match.group(1))
        if offset < 0 or offset >= total or offset % GRAPH_UPLOAD_FRAGMENT_BYTES:
            raise MicrosoftGraphClientError(
                "Microsoft Graph upload session returned an unsafe resume offset."
            )
        return offset

    @staticmethod
    def _safe_url(url: str) -> str:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(query=None, fragment=None))

    @staticmethod
    def _decode_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise MicrosoftGraphClientError(
                "Microsoft Graph response was not valid JSON for "
                f"{response.request.method} {response.request.url}"
            ) from exc

    @staticmethod
    def _should_retry(response: httpx.Response | None) -> bool:
        if response is None:
            return True
        return response.status_code == 429 or 500 <= response.status_code < 600

    @staticmethod
    def _should_refresh_token(error: Exception | None) -> bool:
        return isinstance(error, MicrosoftGraphAPIError) and error.status_code == 401

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = parse_retry_after_seconds(response.headers)
            if retry_after is not None:
                return retry_after
        return min(8.0, 0.5 * (2 ** attempt))

    @staticmethod
    def _build_api_error(
        method: str,
        url: str,
        response: httpx.Response,
    ) -> MicrosoftGraphAPIError:
        payload: Any = None
        message = response.reason_phrase or "request failed"
        try:
            parsed_payload = response.json()
        except ValueError:
            parsed_payload = None

        if isinstance(parsed_payload, dict):
            error = parsed_payload.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                if code:
                    message = str(code)
            elif isinstance(error, str):
                message = "remote error"

        retry_after: float | None = parse_retry_after_seconds(response.headers)

        return MicrosoftGraphAPIError(
            response.status_code,
            method,
            MicrosoftGraphClient._safe_url(url),
            message,
            retry_after_seconds=retry_after,
            payload=payload,
        )
