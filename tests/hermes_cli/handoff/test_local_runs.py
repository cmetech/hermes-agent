from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent import secret_scope
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from hermes_cli.handoff.local import LocalHermesChannel
from hermes_cli.handoff.models import HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.service import AgentHandoffService, ChannelDefinitelyNotAccepted
from hermes_cli.handoff.store import HandoffStore


TARGET_KEY = "reviewer-api-key-0123456789abcdef"
DEFAULT_KEY = "default-api-key-0123456789abcdef"


class _Agent:
    def __init__(self, output: str = "done") -> None:
        self.output = output
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

    def run_conversation(self, **_kwargs):
        return {"final_response": self.output}


class _EventBlockedAgent(_Agent):
    def __init__(self, *, acknowledge_interrupt: bool) -> None:
        super().__init__("late result")
        self.acknowledge_interrupt = acknowledge_interrupt
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupt_requested = threading.Event()

    def interrupt(self, _message=None):
        self.interrupt_requested.set()

    def run_conversation(self, **_kwargs):
        self.started.set()
        assert self.release.wait(timeout=5)
        if self.acknowledge_interrupt:
            return {"final_response": "interrupted", "interrupted": True}
        return {"final_response": self.output}


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    default_home = tmp_path / ".hermes"
    target_home = default_home / "profiles" / "reviewer"
    target_home.mkdir(parents=True)
    default_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    secret_scope.set_multiplex_active(True)
    yield default_home, target_home
    secret_scope.set_multiplex_active(False)


def _write_config(
    default_home: Path,
    *,
    port: int,
    multiplex: bool = True,
    host: str = "127.0.0.1",
    enabled: bool = True,
    allowlist: list[str] | None = None,
) -> None:
    allowlist_yaml = ""
    if allowlist is not None:
        allowlist_yaml = (
            "  multiplex_profile_allowlist: []\n"
            if not allowlist
            else "  multiplex_profile_allowlist:\n"
            + "".join(f"    - {name}\n" for name in allowlist)
        )
    (default_home / "config.yaml").write_text(
        "gateway:\n"
        f"  multiplex_profiles: {'true' if multiplex else 'false'}\n"
        f"{allowlist_yaml}"
        "  api_server:\n"
        f"    enabled: {'true' if enabled else 'false'}\n"
        f"    host: {json.dumps(host)}\n"
        f"    port: {port}\n",
        encoding="utf-8",
    )


@asynccontextmanager
async def _gateway(profile_env, *, agent=None, durable: bool = True):
    default_home, target_home = profile_env
    (default_home / ".env").write_text(
        f"API_SERVER_KEY={DEFAULT_KEY}\n", encoding="utf-8"
    )
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\n", encoding="utf-8"
    )
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": DEFAULT_KEY}))
    adapter.gateway_runner = SimpleNamespace(
        config=GatewayConfig(multiplex_profiles=True)
    )
    adapter._create_agent = agent or (lambda **_kwargs: _Agent())
    if not durable:
        adapter._run_idempotency_store.close()
        adapter._run_idempotency_store = RunIdempotencyStore(":memory:")

    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    app._state["api_server_adapter"] = adapter
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, f"/p/{{profile}}{path}", handler)
    server = TestServer(app)
    await server.start_server()
    _write_config(default_home, port=server.port)
    try:
        yield adapter, server
    finally:
        await server.close()
        adapter._close_cached_session_dbs()
        adapter._run_idempotency_store.close()
        adapter._response_store.close()


def _spec(prompt: str = "Review this change.") -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt=prompt,
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities=frozenset(),
    )


def _service(default_home: Path, channel=None):
    service = AgentHandoffService(
        store=HandoffStore(default_home / "handoffs.db"),
        channel=channel or LocalHermesChannel(),
    )
    snapshot = service.create(_spec(), "workflow/run-1", handoff_key="node/review")
    return service, snapshot


def _json_request(url: str, key: str, *, method="GET", body=None):
    request = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read())


async def _wait_for_run_task(adapter: APIServerAdapter, run_id: str) -> None:
    task = adapter._active_run_tasks.get(run_id)
    if task is not None:
        await task


@pytest.mark.asyncio
async def test_real_profile_routes_bind_submit_replay_and_poll(profile_env):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (_adapter, server):
        service, created = _service(default_home)
        assessment = await asyncio.to_thread(
            service.validate_endpoint, created.spec.endpoint, "workflow/run-1"
        )
        bound = (await asyncio.to_thread(service.advance, created.handoff_id)).snapshot
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot

        assert assessment.available is True
        assert assessment.mechanism == "runs"
        assert bound.binding == {"profile": "reviewer", "mechanism": "runs"}
        assert set(bound.checkpoint or {}) == {"session_id"}
        assert submitted.phase in {"submitted", "active", "succeeded"}
        assert submitted.checkpoint["session_id"] == bound.checkpoint["session_id"]
        assert (
            submitted.checkpoint["idempotency_key"] == f"handoff-{created.handoff_id}"
        )
        assert submitted.checkpoint.get("run_id")
        assert TARGET_KEY not in json.dumps({
            "binding": dict(submitted.binding or {}),
            "checkpoint": dict(submitted.checkpoint or {}),
        })

        with pytest.raises(urllib.error.HTTPError) as wrong_key:
            await asyncio.to_thread(
                _json_request,
                f"http://127.0.0.1:{server.port}/p/reviewer/v1/capabilities",
                DEFAULT_KEY,
            )
        assert wrong_key.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as unserved:
            await asyncio.to_thread(
                _json_request,
                f"http://127.0.0.1:{server.port}/p/ghost/v1/capabilities",
                TARGET_KEY,
            )
        assert unserved.value.code == 404

        listing = await asyncio.to_thread(
            _json_request,
            f"http://127.0.0.1:{server.port}/p/reviewer/api/sessions"
            f"?title=Handoff%3A+{created.handoff_id}&include_hidden=1",
            TARGET_KEY,
        )
        assert [(row["id"], row["title"]) for row in listing["data"]] == [
            (bound.checkpoint["session_id"], f"Handoff: {created.handoff_id}")
        ]

        service.channel = LocalHermesChannel()
        for _ in range(20):
            observed = (
                await asyncio.to_thread(service.advance, created.handoff_id)
            ).snapshot
            if observed.phase == "succeeded":
                break
            await asyncio.sleep(0.02)
        assert observed.phase == "succeeded"
        assert observed.terminal_result["text"] == "done"


@pytest.mark.asyncio
async def test_fresh_adapter_and_channel_poll_completed_durable_run_after_restart(
    profile_env,
):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (adapter, _server):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        run_id = submitted.checkpoint["run_id"]
        await _wait_for_run_task(adapter, run_id)

    async with _gateway(profile_env) as (_restarted_adapter, _server):
        observed = await asyncio.to_thread(
            LocalHermesChannel().observe,
            submitted,
            budget_seconds=2,
        )

    assert observed.phase == "succeeded"
    assert observed.checkpoint["run_id"] == run_id
    assert observed.terminal_result["text"] == "done"


@pytest.mark.asyncio
async def test_bind_reuses_an_exact_title_session(profile_env):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (_adapter, server):
        service, created = _service(default_home)
        title = f"Handoff: {created.handoff_id}"
        seeded = await asyncio.to_thread(
            _json_request,
            f"http://127.0.0.1:{server.port}/p/reviewer/api/sessions",
            TARGET_KEY,
            method="POST",
            body={"title": title, "source": "api_server"},
        )
        bound = (await asyncio.to_thread(service.advance, created.handoff_id)).snapshot
        assert bound.checkpoint["session_id"] == seeded["session"]["id"]


@pytest.mark.asyncio
async def test_lost_submit_response_replays_exact_body_and_key(
    profile_env, monkeypatch
):
    import hermes_cli.handoff.local as local_module

    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (adapter, _server):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)
        original = local_module.open_credentialed_url
        lose_once = True

        def lose_response(request, **kwargs):
            nonlocal lose_once
            response = original(request, **kwargs)
            if lose_once and request.full_url.endswith("/v1/runs"):
                lose_once = False
                response.read()
                response.close()
                raise TimeoutError("response lost after acceptance")
            return response

        monkeypatch.setattr(local_module, "open_credentialed_url", lose_response)
        ambiguous = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        assert ambiguous.phase == "indeterminate"
        recovered = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot

        assert recovered.checkpoint.get("run_id")
        rows = adapter._run_idempotency_store._conn.execute(
            "SELECT count(*) FROM run_idempotency"
        ).fetchone()[0]
        assert rows == 1


@pytest.mark.asyncio
async def test_changed_payload_with_same_handoff_key_is_definitive_conflict(
    profile_env,
):
    default_home, _target_home = profile_env
    async with _gateway(profile_env):
        service, created = _service(default_home)
        bound = (await asyncio.to_thread(service.advance, created.handoff_id)).snapshot
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        conflicting = replace(
            submitted,
            spec=_spec("Different payload."),
            phase="indeterminate",
        )

        with pytest.raises(ChannelDefinitelyNotAccepted) as error:
            await asyncio.to_thread(
                LocalHermesChannel().reconcile,
                replace(conflicting, checkpoint=bound.checkpoint),
                budget_seconds=2,
            )
        assert error.value.failure_code == "idempotency_conflict"


@pytest.mark.asyncio
async def test_interrupted_owner_is_nonterminal_indeterminate(profile_env):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (adapter, _server):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        run_id = submitted.checkpoint["run_id"]
        await _wait_for_run_task(adapter, run_id)
        adapter._run_idempotency_store.update_status(
            run_id,
            {"run_id": run_id, "status": "interrupted"},
        )
        adapter._run_statuses.pop(run_id, None)
        observed = await asyncio.to_thread(
            LocalHermesChannel().observe, submitted, budget_seconds=2
        )
        assert observed.phase == "indeterminate"
        assert observed.failure_code == "run_interrupted"


@pytest.mark.parametrize(
    ("run_status", "phase"),
    [
        ("queued", "submitted"),
        ("running", "active"),
        ("waiting_for_approval", "needs_input"),
        ("completed", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
        ("interrupted", "indeterminate"),
    ],
)
@pytest.mark.asyncio
async def test_run_statuses_map_to_facade_truth(profile_env, run_status, phase):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (adapter, _server):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        run_id = submitted.checkpoint["run_id"]
        await _wait_for_run_task(adapter, run_id)
        status = {"run_id": run_id, "status": run_status}
        if run_status == "completed":
            status["output"] = "authoritative output"
        adapter._run_idempotency_store.update_status(run_id, status)
        adapter._run_statuses.pop(run_id, None)

        observed = await asyncio.to_thread(
            LocalHermesChannel().observe, submitted, budget_seconds=2
        )
        assert observed.phase == phase
        if run_status == "completed":
            assert observed.terminal_result["text"] == "authoritative output"
        if run_status == "failed":
            assert observed.failure_code == "remote_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("acknowledge_interrupt", "terminal_phase"),
    [(True, "cancelled"), (False, "succeeded")],
)
async def test_stop_active_run_converges_through_cancelling_to_authoritative_truth(
    profile_env, acknowledge_interrupt, terminal_phase
):
    default_home, _target_home = profile_env
    agent = _EventBlockedAgent(acknowledge_interrupt=acknowledge_interrupt)
    async with _gateway(profile_env, agent=lambda **_kwargs: agent) as (
        adapter,
        _server,
    ):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)
        submitted = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        assert await asyncio.to_thread(agent.started.wait, 2)
        run_id = submitted.checkpoint["run_id"]
        service.command(
            created.handoff_id, "cancel", command_id="cancel-1", actor="workflow"
        )

        stopping = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        assert stopping.phase == "cancelling"
        assert stopping.checkpoint["status"] == "stopping"

        still_stopping = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        assert still_stopping.phase == "cancelling"
        assert agent.interrupt_requested.is_set()

        agent.release.set()
        await _wait_for_run_task(adapter, run_id)
        terminal = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        assert terminal.phase == terminal_phase
        if terminal_phase == "succeeded":
            assert terminal.terminal_result["text"] == "late result"


@pytest.mark.asyncio
async def test_capability_downgrade_refuses_binding(profile_env):
    default_home, _target_home = profile_env
    async with _gateway(profile_env, durable=False):
        service, created = _service(default_home)
        assessment = await asyncio.to_thread(
            service.validate_endpoint, created.spec.endpoint, "workflow/run-1"
        )
        bound = (await asyncio.to_thread(service.advance, created.handoff_id)).snapshot
        assert assessment.available is False
        assert assessment.failure_code == "runs_not_durable"
        assert bound.mechanism is None
        assert bound.failure_code == "runs_not_durable"


@pytest.mark.parametrize(
    "contract",
    [
        {"supported": "false", "durable": True},
        {"supported": True, "durable": 1},
    ],
)
@pytest.mark.asyncio
async def test_truthy_non_boolean_capability_values_refuse_binding(
    profile_env, contract
):
    default_home, target_home = profile_env
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\n", encoding="utf-8"
    )

    async def capabilities(_request):
        return web.json_response({"features": {"runs_idempotency": contract}})

    app = web.Application()
    app.router.add_get("/p/reviewer/v1/capabilities", capabilities)
    server = TestServer(app)
    await server.start_server()
    _write_config(default_home, port=server.port)
    try:
        assessment = await asyncio.to_thread(
            AgentHandoffService(
                store=HandoffStore(default_home / "malformed-capability.db")
            ).validate_endpoint,
            "hermes://local/reviewer",
            "workflow/run-1",
        )
    finally:
        await server.close()

    assert assessment.available is False
    assert assessment.failure_code == "runs_not_durable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wildcard",
    ["0.0.0.0", "::", "[::]", "0:0:0:0:0:0:0:0", "[0:0:0:0:0:0:0:0]"],
)
async def test_unspecified_listener_connects_through_ipv4_loopback(
    profile_env, wildcard
):
    default_home, _target_home = profile_env
    async with _gateway(profile_env) as (_adapter, server):
        _write_config(default_home, port=server.port, host=wildcard)
        assessment = await asyncio.to_thread(
            AgentHandoffService(
                store=HandoffStore(default_home / "wildcard.db")
            ).validate_endpoint,
            "hermes://local/reviewer",
            "workflow/run-1",
        )
        assert assessment.available is True


@pytest.mark.parametrize(
    ("changes", "key", "expected"),
    [
        ({"multiplex": False}, TARGET_KEY, "multiplex_required"),
        ({"enabled": False}, TARGET_KEY, "api_server_disabled"),
        ({"host": "192.0.2.10"}, TARGET_KEY, "listener_not_loopback"),
        ({"allowlist": []}, TARGET_KEY, "profile_not_served"),
        ({}, "", "api_server_key_missing"),
        ({}, "short", "api_server_key_weak"),
    ],
)
def test_binding_gates_fail_closed(profile_env, changes, key, expected):
    default_home, target_home = profile_env
    (target_home / ".env").write_text(f"API_SERVER_KEY={key}\n", encoding="utf-8")
    _write_config(default_home, port=9, **changes)
    assessment = AgentHandoffService(
        store=HandoffStore(default_home / f"{expected}.db")
    ).validate_endpoint("hermes://local/reviewer", "workflow/run-1")
    assert assessment.available is False
    assert assessment.failure_code == expected


def test_target_secret_never_borrows_ambient_default_key(profile_env, monkeypatch):
    default_home, target_home = profile_env
    (target_home / ".env").unlink(missing_ok=True)
    monkeypatch.setenv("API_SERVER_KEY", DEFAULT_KEY)
    _write_config(default_home, port=9)
    assessment = AgentHandoffService(
        store=HandoffStore(default_home / "ambient.db")
    ).validate_endpoint("hermes://local/reviewer", "workflow/run-1")
    assert assessment.failure_code == "api_server_key_missing"


@pytest.mark.asyncio
async def test_loopback_request_bypasses_configured_http_proxy(
    profile_env, monkeypatch
):
    default_home, target_home = profile_env
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\n", encoding="utf-8"
    )
    proxy_requests = []
    target_authorization = []

    async def proxy_capture(request):
        proxy_requests.append({
            "path": request.raw_path,
            "authorization": request.headers.get("Authorization"),
        })
        return web.json_response({
            "features": {"runs_idempotency": {"supported": True, "durable": True}}
        })

    proxy_app = web.Application()
    proxy_app.router.add_route("*", "/{tail:.*}", proxy_capture)
    proxy_server = TestServer(proxy_app)
    await proxy_server.start_server()

    async def capabilities(request):
        target_authorization.append(request.headers.get("Authorization"))
        return web.json_response({
            "features": {"runs_idempotency": {"supported": True, "durable": True}}
        })

    target_app = web.Application()
    target_app.router.add_get("/p/reviewer/v1/capabilities", capabilities)
    target_server = TestServer(target_app)
    await target_server.start_server()
    _write_config(default_home, port=target_server.port)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy_server.port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy_server.port}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(urllib.request, "_opener", None)
    try:
        assessment = await asyncio.to_thread(
            AgentHandoffService(
                store=HandoffStore(default_home / "proxy-boundary.db")
            ).validate_endpoint,
            "hermes://local/reviewer",
            "workflow/run-1",
        )
    finally:
        await target_server.close()
        await proxy_server.close()

    assert assessment.available is True
    assert proxy_requests == []
    assert target_authorization == [f"Bearer {TARGET_KEY}"]


def test_slow_drip_response_cannot_exceed_total_operation_budget(
    profile_env, monkeypatch
):
    import hermes_cli.handoff.local as local_module

    default_home, target_home = profile_env
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\n", encoding="utf-8"
    )
    _write_config(default_home, port=9)
    socket_timeouts = []
    payload = json.dumps({
        "features": {"runs_idempotency": {"supported": True, "durable": True}}
    }).encode()

    class _Socket:
        def settimeout(self, timeout):
            socket_timeouts.append(timeout)

    class _SlowDrip:
        def __init__(self):
            self._chunks = iter([payload[:1], payload[1:2], payload[2:]])
            self.fp = SimpleNamespace(
                raw=SimpleNamespace(_sock=_Socket()),
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            time.sleep(0.30)
            return payload

        def read1(self, _size):
            time.sleep(0.02)
            return next(self._chunks, b"")

    monkeypatch.setattr(
        local_module,
        "open_credentialed_url",
        lambda *_args, **_kwargs: _SlowDrip(),
    )
    service, created = _service(default_home)
    service.channel._connection("reviewer")
    started_at = time.monotonic()
    observation = service.channel.bind(created, budget_seconds=0.05)
    elapsed = time.monotonic() - started_at

    assert observation.failure_code == "endpoint_unavailable"
    assert elapsed < 0.18
    assert len(socket_timeouts) >= 2
    assert socket_timeouts[-1] < socket_timeouts[0] <= 0.05


@pytest.mark.asyncio
async def test_response_reads_are_bounded_and_http_details_are_redacted(
    profile_env, monkeypatch
):
    import hermes_cli.handoff.local as local_module

    default_home, _target_home = profile_env
    async with _gateway(profile_env):
        service, created = _service(default_home)
        await asyncio.to_thread(service.advance, created.handoff_id)

        class _Oversized:
            def __init__(self):
                self.remaining = local_module.MAX_RESPONSE_BYTES + 1

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, size):
                assert size == local_module.MAX_RESPONSE_BYTES + 1
                return b"x" * size

            def read1(self, size):
                if not self.remaining:
                    return b""
                count = min(size, self.remaining)
                self.remaining -= count
                return b"x" * count

        monkeypatch.setattr(
            local_module, "open_credentialed_url", lambda *_a, **_kw: _Oversized()
        )
        result = (await asyncio.to_thread(service.advance, created.handoff_id)).snapshot
        assert result.phase == "indeterminate"
        assert result.failure_code == "submission_indeterminate"
        assert "x" not in json.dumps([
            dict(e.data) for e in service.evidence(created.handoff_id).events
        ])


@pytest.mark.asyncio
async def test_http_error_body_is_never_persisted(profile_env, monkeypatch):
    import hermes_cli.handoff.local as local_module

    default_home, _target_home = profile_env
    async with _gateway(profile_env):
        service, created = _service(default_home)
        error = urllib.error.HTTPError(
            "http://127.0.0.1/capabilities",
            503,
            "Bearer target-secret /private/profile/path",
            {},
            None,
        )
        monkeypatch.setattr(
            local_module,
            "open_credentialed_url",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        )
        snapshot = (
            await asyncio.to_thread(service.advance, created.handoff_id)
        ).snapshot
        evidence = json.dumps([
            dict(event.data) for event in service.evidence(created.handoff_id).events
        ])
        assert snapshot.failure_code == "http_503"
        assert "target-secret" not in evidence
        assert "/private/profile" not in evidence


@pytest.mark.asyncio
async def test_cross_origin_redirect_strips_target_authorization(profile_env):
    default_home, target_home = profile_env
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\n", encoding="utf-8"
    )
    seen = []

    async def sink(request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({
            "features": {"runs_idempotency": {"supported": True, "durable": True}}
        })

    sink_app = web.Application()
    sink_app.router.add_get("/capabilities", sink)
    sink_server = TestServer(sink_app)
    await sink_server.start_server()

    async def redirect(_request):
        raise web.HTTPFound(f"http://127.0.0.1:{sink_server.port}/capabilities")

    source_app = web.Application()
    source_app.router.add_get("/p/reviewer/v1/capabilities", redirect)
    source_server = TestServer(source_app)
    await source_server.start_server()
    _write_config(default_home, port=source_server.port)
    try:
        assessment = await asyncio.to_thread(
            AgentHandoffService(
                store=HandoffStore(default_home / "redirect.db")
            ).validate_endpoint,
            "hermes://local/reviewer",
            "workflow/run-1",
        )
    finally:
        await source_server.close()
        await sink_server.close()
    assert assessment.available is True
    assert seen == [None]


def test_default_service_uses_the_local_runs_channel(profile_env):
    default_home, _target_home = profile_env
    service = AgentHandoffService(store=HandoffStore(default_home / "default.db"))
    assert isinstance(service.channel, LocalHermesChannel)
