"""Behavior tests for bounded Microsoft Graph transfers and polling."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tools.microsoft_graph_auth import GraphCredentials, MicrosoftGraphTokenProvider
from tools import microsoft_graph_client as graph


def _provider() -> MicrosoftGraphTokenProvider:
    provider = MicrosoftGraphTokenProvider(GraphCredentials("tenant", "client", "secret"))
    provider._cached_token = type(  # type: ignore[attr-defined]
        "Token",
        (),
        {
            "access_token": "cached-token",
            "is_expired": lambda self, skew_seconds=0: False,
            "expires_in_seconds": 3600,
        },
    )()
    return provider


@pytest.mark.anyio
async def test_download_limit_removes_partial_and_destination(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"0123456789")

    destination = tmp_path / "artifact.bin"
    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphLimitError):
        await client.download_to_file("/content", destination, max_bytes=5)

    assert not destination.exists()
    assert not Path(f"{destination}.part").exists()


@pytest.mark.anyio
async def test_download_api_failure_removes_stale_partial(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "accessDenied", "message": "raw-private-body"}},
        )

    destination = tmp_path / "artifact.bin"
    partial = Path(f"{destination}.part")
    partial.write_bytes(b"stale")
    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=0
    )

    with pytest.raises(graph.MicrosoftGraphAPIError):
        await client.download_to_file("/content", destination, max_bytes=1024)

    assert not partial.exists()


@pytest.mark.anyio
async def test_download_deadline_stops_before_network_and_cleans_partial(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"data")

    destination = tmp_path / "artifact.bin"
    client = graph.MicrosoftGraphClient(
        _provider(),
        transport=httpx.MockTransport(handler),
        clock=lambda: 10.0,
    )

    with pytest.raises(graph.MicrosoftGraphDeadlineError):
        await client.download_to_file(
            "/content", destination, max_bytes=10, deadline=10.0
        )

    assert calls == []
    assert not Path(f"{destination}.part").exists()


@pytest.mark.anyio
async def test_download_follows_one_preauthorized_https_redirect_without_bearer(
    tmp_path,
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://tenant.sharepoint.com/download?sig=private"
                },
            )
        assert request.url.host == "tenant.sharepoint.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"redirected content")

    destination = tmp_path / "artifact.bin"
    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    result = await client.download_to_file(
        "/drives/d/items/i/content", destination, max_bytes=1024
    )

    assert destination.read_bytes() == b"redirected content"
    assert result["size_bytes"] == len(b"redirected content")
    assert requests[0].headers["authorization"] == "Bearer cached-token"
    assert "sig=private" not in repr(result)


@pytest.mark.anyio
async def test_download_rejects_unsafe_preauthorized_redirect_before_following(
    tmp_path,
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://tenant.sharepoint.com/download?sig=private"},
        )

    destination = tmp_path / "artifact.bin"
    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphClientError, match="redirect"):
        await client.download_to_file("/content", destination, max_bytes=1024)

    assert len(requests) == 1
    assert not destination.exists()
    assert not Path(f"{destination}.part").exists()


@pytest.mark.anyio
async def test_small_upload_uses_bounded_binary_put():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "item-1", "size": 3})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    result = await client.upload_small("/drives/d/root:/a.txt:/content", b"abc", max_bytes=3)

    assert result == {"id": "item-1", "size": 3}
    assert requests[0].method == "PUT"
    assert requests[0].content == b"abc"
    assert requests[0].headers["content-type"] == "application/octet-stream"
    assert requests[0].headers["authorization"] == "Bearer cached-token"


@pytest.mark.anyio
async def test_small_upload_does_not_replay_ambiguous_transport_failure():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadError("response lost", request=request)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=3
    )

    with pytest.raises(graph.MicrosoftGraphAmbiguousWriteError):
        await client.upload_small("/content", b"abc", max_bytes=3)

    assert len(requests) == 1


@pytest.mark.anyio
async def test_small_upload_does_not_replay_ambiguous_server_failure():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": {"code": "unavailable"}})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=3
    )

    with pytest.raises(graph.MicrosoftGraphAmbiguousWriteError):
        await client.upload_small("/content", b"abc", max_bytes=3)

    assert len(requests) == 1


@pytest.mark.anyio
async def test_small_upload_may_retry_definite_rate_limit():
    requests = []

    async def no_sleep(_delay):
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"id": "uploaded"})

    client = graph.MicrosoftGraphClient(
        _provider(),
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep=no_sleep,
    )

    result = await client.upload_small("/content", b"abc", max_bytes=3)

    assert result == {"id": "uploaded"}
    assert len(requests) == 2


@pytest.mark.anyio
async def test_upload_session_sends_aligned_ranges_without_bearer(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 700_000)
    ranges = []
    session_creations = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            session_creations.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.example/session-1"},
            )
        assert "authorization" not in request.headers
        ranges.append(request.headers["content-range"])
        if len(ranges) < 3:
            next_offset = 327_680 * len(ranges)
            return httpx.Response(202, json={"nextExpectedRanges": [f"{next_offset}-"]})
        return httpx.Response(201, json={"id": "item-1", "size": 700_000})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    result = await client.upload_via_session(
        "/drives/d/root:/upload.bin:/createUploadSession",
        source,
        max_bytes=800_000,
        chunk_size=327_680,
        max_chunks=4,
        conflict_behavior="replace",
    )

    assert result == {"id": "item-1", "size": 700_000}
    assert session_creations == [
        {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
    ]
    assert ranges == [
        "bytes 0-327679/700000",
        "bytes 327680-655359/700000",
        "bytes 655360-699999/700000",
    ]


@pytest.mark.anyio
async def test_upload_session_honors_server_resume_offset(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 700_000)
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.example/session-2"},
            )
        starts.append(request.headers["content-range"])
        if len(starts) == 1:
            return httpx.Response(202, json={"nextExpectedRanges": ["655360-"]})
        return httpx.Response(201, json={"id": "item-2"})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    await client.upload_via_session(
        "/session",
        source,
        max_bytes=800_000,
        chunk_size=327_680,
        max_chunks=3,
    )

    assert starts == [
        "bytes 0-327679/700000",
        "bytes 655360-699999/700000",
    ]


@pytest.mark.anyio
async def test_upload_session_retries_same_range_after_rate_limit(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 10)
    ranges = []
    sleeps = []
    creates = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            creates.append(request)
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.example/throttled"},
            )
        ranges.append(request.headers["content-range"])
        if len(ranges) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(201, json={"id": "item-after-retry"})

    async def sleep(delay):
        sleeps.append(delay)

    client = graph.MicrosoftGraphClient(
        _provider(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        max_retries=1,
    )

    result = await client.upload_via_session(
        "/session", source, max_bytes=100, chunk_size=327_680, max_chunks=1
    )

    assert result["id"] == "item-after-retry"
    assert len(creates) == 1
    assert ranges == ["bytes 0-9/10", "bytes 0-9/10"]
    assert sleeps == [3.0]


@pytest.mark.anyio
async def test_upload_session_rejects_unaligned_chunk_before_network(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 10)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphLimitError, match="320 KiB"):
        await client.upload_via_session(
            "/session", source, max_bytes=100, chunk_size=100, max_chunks=2
        )

    assert calls == []


@pytest.mark.anyio
async def test_upload_session_classifies_expired_session(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 10)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.example/expired"},
            )
        return httpx.Response(410, json={"error": "expired"})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphUploadSessionExpired):
        await client.upload_via_session(
            "/session", source, max_bytes=100, chunk_size=327_680, max_chunks=1
        )


@pytest.mark.anyio
async def test_upload_session_cancellation_prevents_remote_write(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 10)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphCancelledError):
        await client.upload_via_session(
            "/session",
            source,
            max_bytes=100,
            chunk_size=327_680,
            max_chunks=1,
            cancel_check=lambda: True,
        )

    assert calls == []


@pytest.mark.anyio
async def test_ambiguous_upload_does_not_create_a_second_session(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"a" * 10)
    creates = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            creates.append(request)
            return httpx.Response(
                200,
                json={"uploadUrl": "https://upload.example/ambiguous"},
            )
        raise httpx.ReadTimeout("completion unknown", request=request)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=3
    )

    with pytest.raises(graph.MicrosoftGraphAmbiguousWriteError):
        await client.upload_via_session(
            "/session", source, max_bytes=100, chunk_size=327_680, max_chunks=1
        )

    assert len(creates) == 1


@pytest.mark.anyio
async def test_async_operation_honors_retry_after_and_completes():
    sleeps = []
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                202,
                json={"status": "inProgress", "percentageComplete": 50},
                headers={"Retry-After": "2"},
            )
        return httpx.Response(200, json={"status": "completed", "resourceId": "item-3"})

    async def sleep(delay):
        sleeps.append(delay)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), sleep=sleep
    )

    result = await client.poll_async_operation(
        "https://graph.microsoft.com/v1.0/monitor/1", max_polls=2
    )

    assert result["status"] == "completed"
    assert sleeps == [2.0]
    assert all(call.headers["authorization"] == "Bearer cached-token" for call in calls)


@pytest.mark.anyio
async def test_async_operation_retry_delay_cannot_outlive_deadline():
    calls = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            202,
            json={"status": "inProgress"},
            headers={"Retry-After": "86400"},
        )

    async def sleep(delay):
        sleeps.append(delay)

    client = graph.MicrosoftGraphClient(
        _provider(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        clock=lambda: 10.0,
    )

    with pytest.raises(graph.MicrosoftGraphDeadlineError):
        await client.poll_async_operation(
            "https://graph.microsoft.com/v1.0/monitor/1",
            max_polls=2,
            deadline=15.0,
        )

    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.anyio
async def test_start_async_operation_posts_once_then_polls_trusted_location():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={"Location": "https://graph.microsoft.com/v1.0/monitor/copy-1"},
            )
        return httpx.Response(200, json={"status": "completed", "resourceId": "copy"})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    result = await client.start_async_operation(
        "/drives/d/items/i/copy",
        json_body={"parentReference": {"driveId": "d", "id": "parent"}},
        max_polls=2,
    )

    assert result == {"status": "completed", "resourceId": "copy"}
    assert [(call.method, str(call.url)) for call in calls] == [
        ("POST", "https://graph.microsoft.com/v1.0/drives/d/items/i/copy"),
        ("GET", "https://graph.microsoft.com/v1.0/monitor/copy-1"),
    ]


@pytest.mark.anyio
async def test_start_async_operation_polls_validated_external_monitor_without_bearer():
    calls = []
    monitor_url = (
        "https://tenant.sharepoint.com/sites/Governance/"
        "_api/v2.1/monitor/copy-1?token=private"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": monitor_url})
        assert str(request.url) == monitor_url
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"status": "completed", "resourceId": "copy"})

    validated = []

    def validate_monitor(location: str) -> None:
        validated.append(location)

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    result = await client.start_async_operation(
        "/drives/d/items/i/copy",
        json_body={"parentReference": {"driveId": "d", "id": "parent"}},
        max_polls=2,
        monitor_url_validator=validate_monitor,
    )

    assert result == {"status": "completed", "resourceId": "copy"}
    assert validated == [monitor_url]
    assert calls[0].headers["authorization"] == "Bearer cached-token"
    assert "authorization" not in calls[1].headers


@pytest.mark.anyio
async def test_external_monitor_redirect_is_ambiguous_and_never_followed_or_restarted():
    calls = []
    monitor_url = "https://tenant.sharepoint.com/_api/v2.0/monitor/copy-1"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": monitor_url})
        return httpx.Response(302, headers={"Location": "https://attacker.example/steal"})

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=0
    )

    with pytest.raises(graph.MicrosoftGraphAmbiguousWriteError):
        await client.start_async_operation(
            "/drives/d/items/i/copy",
            json_body={"parentReference": {"driveId": "d", "id": "parent"}},
            max_polls=2,
            monitor_url_validator=lambda _location: None,
        )

    assert [(call.method, str(call.url)) for call in calls] == [
        ("POST", "https://graph.microsoft.com/v1.0/drives/d/items/i/copy"),
        ("GET", monitor_url),
    ]


@pytest.mark.anyio
async def test_async_operation_terminal_failure_is_bounded_and_redacted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "failed", "error": "raw-private-provider-body"},
        )

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphAsyncOperationError) as caught:
        await client.poll_async_operation(
            "https://graph.microsoft.com/v1.0/monitor/1", max_polls=1
        )

    assert "raw-private-provider-body" not in str(caught.value)


@pytest.mark.anyio
async def test_pagination_rejects_next_link_outside_graph_origin():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host != "graph.microsoft.com":
            raise AssertionError("cross-origin nextLink was requested")
        return httpx.Response(
            200,
            json={
                "value": [{"id": "one"}],
                "@odata.nextLink": "https://attacker.example/steal",
            },
        )

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(graph.MicrosoftGraphClientError, match="origin"):
        await client.collect_paginated("/items")

    assert len(calls) == 1


@pytest.mark.anyio
async def test_api_error_public_projection_excludes_response_body_and_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "badRequest",
                    "message": "raw-private-provider-body cached-token",
                }
            },
        )

    client = graph.MicrosoftGraphClient(
        _provider(), transport=httpx.MockTransport(handler), max_retries=0
    )

    with pytest.raises(graph.MicrosoftGraphAPIError) as caught:
        await client.get_json("/items")

    error = caught.value
    assert error.payload is None
    assert "raw-private-provider-body" not in str(error)
    assert "cached-token" not in str(error)
