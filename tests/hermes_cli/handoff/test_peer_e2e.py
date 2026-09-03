from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent import secret_scope
from gateway.config import GatewayConfig, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
import hermes_cli.handoff.runs as runs_module
from hermes_cli.handoff import AgentHandoffService, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.peer import PeerHermesChannel
from hermes_cli.handoff.service import ChannelDefinitelyNotAccepted
from hermes_cli.handoff.store import HandoffStore


SOURCE_KEY = "source-api-key-0123456789abcdef"
DESTINATION_KEY = "destination-api-key-0123456789abcdef"


class _Agent:
    def __init__(self, *, blocked: bool = False) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupted = threading.Event()
        self.steers: list[str] = []
        if not blocked:
            self.release.set()
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

    def interrupt(self, _message=None) -> None:
        self.interrupted.set()
        self.release.set()

    def steer(self, text: str) -> bool:
        self.steers.append(text)
        return True

    def run_conversation(self, **_kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=10)
        if self.interrupted.is_set():
            return {"final_response": "interrupted", "interrupted": True}
        return {"final_response": '{"answer":"approved"}'}


def _app(adapter: APIServerAdapter, *, multiplex: bool) -> web.Application:
    middlewares = [adapter._make_profile_prefix_middleware()] if multiplex else []
    app = web.Application(middlewares=middlewares)
    app["api_server_adapter"] = adapter
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
        if multiplex:
            app.router.add_route(method, f"/p/{{profile}}{path}", handler)
    return app


def _close_adapter(adapter: APIServerAdapter) -> None:
    adapter._close_cached_session_dbs()
    adapter._run_idempotency_store.close()
    adapter._response_store.close()


@dataclass
class _PeerGateway:
    initiating_home: Path
    destination_home: Path
    destination_profile_home: Path
    source_adapter: APIServerAdapter
    source_server: TestServer
    adapter: APIServerAdapter
    server: TestServer
    agents: list[_Agent]

    @property
    def url(self) -> str:
        return str(self.server.make_url("")).rstrip("/")


@asynccontextmanager
async def _peer_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    blocked: bool = False,
    disabled_feature: str | None = None,
):
    initiating_root = tmp_path / "initiating"
    initiating_home = initiating_root / ".hermes"
    destination_root = tmp_path / "destination"
    destination_home = destination_root / ".hermes"
    destination_profile_home = destination_home / "profiles" / "reviewer"
    initiating_home.mkdir(parents=True)
    destination_profile_home.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(initiating_home))
    (initiating_home / ".env").write_text(
        f"API_SERVER_KEY={SOURCE_KEY}\n", encoding="utf-8"
    )
    source_adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": SOURCE_KEY})
    )
    source_server = TestServer(_app(source_adapter, multiplex=False))
    await source_server.start_server()

    monkeypatch.setattr(Path, "home", lambda: destination_root)
    monkeypatch.setenv("HERMES_HOME", str(destination_home))
    (destination_profile_home / ".env").write_text(
        f"API_SERVER_KEY={DESTINATION_KEY}\n", encoding="utf-8"
    )
    (destination_profile_home / "config.yaml").write_text(
        "model:\n  provider: custom\n  default: deterministic\n",
        encoding="utf-8",
    )
    secret_scope.set_multiplex_active(True)
    adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": "destination-default-key-012345"})
    )
    adapter.gateway_runner = SimpleNamespace(
        config=GatewayConfig(multiplex_profiles=True)
    )
    agents: list[_Agent] = []

    def create_agent(**_kwargs):
        agent = _Agent(blocked=blocked)
        agents.append(agent)
        return agent

    adapter._create_agent = create_agent
    if disabled_feature is not None:
        original_capabilities = adapter._handle_capabilities

        async def limited_capabilities(request):
            response = await original_capabilities(request)
            document = json.loads(response.body)
            if disabled_feature == "runs_idempotency":
                document["features"][disabled_feature] = {
                    "supported": True,
                    "durable": False,
                }
            else:
                document["features"][disabled_feature] = False
            return web.json_response(document, status=response.status)

        adapter._handle_capabilities = limited_capabilities

    server = TestServer(_app(adapter, multiplex=True))
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    (initiating_home / "config.yaml").write_text(
        f"bot_peers:\n  spark:\n    url: {url}\n", encoding="utf-8"
    )
    (initiating_home / ".env").write_text(
        f"API_SERVER_KEY={SOURCE_KEY}\n"
        f"HERMES_PEER_SPARK_KEY={DESTINATION_KEY}\n"
        "HERMES_PEER_OTHER_KEY=unrelated-key-0123456789abcdef\n",
        encoding="utf-8",
    )
    harness = _PeerGateway(
        initiating_home,
        destination_home,
        destination_profile_home,
        source_adapter,
        source_server,
        adapter,
        server,
        agents,
    )
    try:
        yield harness
    finally:
        for agent in agents:
            agent.release.set()
        tasks = list(adapter._active_run_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()
        await source_server.close()
        _close_adapter(adapter)
        _close_adapter(source_adapter)
        secret_scope.set_multiplex_active(False)


def _spec(
    *,
    prompt: str = "Review this change.",
    required: frozenset[str] = frozenset(),
    profile: str = "reviewer",
) -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse(f"hermes://peer/spark/{profile}"),
        prompt=prompt,
        output_schema=None,
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities=required,
    )


def _service(
    harness: _PeerGateway,
    *,
    prompt: str = "Review this change.",
    required: frozenset[str] = frozenset(),
    handoff_key: str = "node/review",
):
    service = AgentHandoffService(
        HandoffStore(harness.initiating_home / "handoffs.db"),
        channel=PeerHermesChannel(harness.initiating_home),
    )
    created = service.create(
        _spec(prompt=prompt, required=required),
        "workflow/run-1",
        handoff_key=handoff_key,
    )
    return service, created


async def _advance(service: AgentHandoffService, handoff_id: str):
    return await asyncio.to_thread(service.advance, handoff_id, budget_seconds=3)


def _json_request(url: str, key: str):
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


@pytest.mark.asyncio
async def test_remote_bot_conversation_uses_authenticated_profile_bot_chat(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch) as harness:
        spec = HandoffSpec(
            mode="conversation",
            endpoint=HandoffEndpoint.parse("hermes://peer/spark/reviewer"),
            prompt="Message from 🤖 hermes (@hermes): Review the release.",
            output_schema=None,
            deadline_at=None,
            attribution={"profile": "default", "handle": "hermes"},
            required_capabilities=frozenset({"cancellation", "follow_up"}),
            return_route={
                "kind": "bot",
                "host_kind": "gateway",
                "profile": "default",
                "session_id": "bot-session-1",
                "session_key": "agent:default:telegram:dm:42",
                "tool_call_id": "call-peer-bot",
                "delivery_policy": "wake",
                "hop_count": 0,
            },
        )
        service = AgentHandoffService(
            HandoffStore(harness.initiating_home / "handoffs.db"),
            channel=PeerHermesChannel(harness.initiating_home),
        )
        created = service.create(
            spec, "bot/default/bot-session-1", handoff_key="call-peer-bot"
        )
        bound = (await _advance(service, created.handoff_id)).snapshot
        submitted = (await _advance(service, created.handoff_id)).snapshot
        for _ in range(40):
            completed = (await _advance(service, created.handoff_id)).snapshot
            if completed.phase == "succeeded":
                break
            await asyncio.sleep(0.02)

        assert bound.mechanism == "peer_runs"
        assert bound.checkpoint["session_id"] == submitted.checkpoint["session_id"]
        assert (
            submitted.checkpoint["idempotency_key"]
            == f"handoff-{created.handoff_id}"
        )
        assert completed.phase == "succeeded"
        assert completed.terminal_result["text"] == '{"answer":"approved"}'
        assert sum(agent.calls for agent in harness.agents) == 1
        sessions = await asyncio.to_thread(
            _json_request,
            f"{harness.url}/p/reviewer/api/sessions?title=Bot%20Chat&include_hidden=1",
            DESTINATION_KEY,
        )
        assert [(row["id"], row["title"]) for row in sessions["data"]] == [
            (bound.checkpoint["session_id"], "Bot Chat")
        ]
        with pytest.raises(urllib.error.HTTPError) as wrong_credential:
            await asyncio.to_thread(
                _json_request,
                f"{harness.url}/p/reviewer/api/sessions?title=Bot%20Chat",
                SOURCE_KEY,
            )
        assert wrong_credential.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as unrelated_profile:
            await asyncio.to_thread(
                _json_request,
                f"{harness.url}/p/missing/api/sessions?title=Bot%20Chat",
                DESTINATION_KEY,
            )
        assert unrelated_profile.value.code == 404
        persisted = b"".join(
            path.read_bytes() for path in harness.initiating_home.glob("handoffs.db*")
        )
        assert DESTINATION_KEY.encode() not in persisted
        assert harness.url.encode() not in persisted
        service.store.close()


@pytest.mark.asyncio
async def test_remote_handoff_lost_submit_response_recovers_same_key_once(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch) as harness:
        service, created = _service(harness)
        await _advance(service, created.handoff_id)
        original = runs_module.open_credentialed_url
        lose_once = True

        def lose_committed_response(request, **kwargs):
            nonlocal lose_once
            response = original(request, **kwargs)
            if lose_once and request.full_url.endswith("/v1/runs"):
                lose_once = False
                response.read()
                response.close()
                raise TimeoutError("accepted response lost")
            return response

        monkeypatch.setattr(
            runs_module, "open_credentialed_url", lose_committed_response
        )
        ambiguous = (await _advance(service, created.handoff_id)).snapshot
        recovered = (await _advance(service, created.handoff_id)).snapshot

        assert ambiguous.phase == "indeterminate"
        assert recovered.checkpoint["run_id"] in harness.adapter._run_idempotency_ids
        assert len(harness.adapter._run_idempotency_ids) == 1
        await asyncio.gather(
            *harness.adapter._active_run_tasks.values(), return_exceptions=True
        )
        assert sum(agent.calls for agent in harness.agents) == 1
        service.store.close()


@pytest.mark.asyncio
async def test_remote_handoff_duplicate_key_replays_and_conflict_rejects(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch) as harness:
        service, created = _service(harness)
        bound = (await _advance(service, created.handoff_id)).snapshot
        submitted = (await _advance(service, created.handoff_id)).snapshot
        channel = PeerHermesChannel(harness.initiating_home)

        replayed = await asyncio.to_thread(
            channel.reconcile,
            replace(submitted, phase="indeterminate", checkpoint=bound.checkpoint),
            budget_seconds=3,
        )
        assert replayed.checkpoint["run_id"] == submitted.checkpoint["run_id"]
        with pytest.raises(ChannelDefinitelyNotAccepted) as error:
            await asyncio.to_thread(
                channel.reconcile,
                replace(
                    submitted,
                    phase="indeterminate",
                    spec=_spec(prompt="Changed payload."),
                    checkpoint=bound.checkpoint,
                ),
                budget_seconds=3,
            )
        assert error.value.failure_code == "idempotency_key_conflict"
        assert len(harness.adapter._run_idempotency_ids) == 1
        assert sum(agent.calls for agent in harness.agents) == 1
        service.store.close()


@pytest.mark.asyncio
async def test_remote_handoff_destination_restart_reports_interrupted(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch, blocked=True) as harness:
        service, created = _service(harness)
        await _advance(service, created.handoff_id)
        submitted = (await _advance(service, created.handoff_id)).snapshot
        run_id = submitted.checkpoint["run_id"]
        assert harness.agents[0].started.wait(timeout=3)
        port = harness.server.port

        await harness.server.close()
        task = harness.adapter._active_run_tasks[run_id]
        database = Path(harness.adapter._run_idempotency_store._db_path)
        _close_adapter(harness.adapter)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE run_idempotency SET owner_pid=999999999, owner_started=1 "
                "WHERE run_id=?",
                (run_id,),
            )

        monkeypatch.setenv("HERMES_HOME", str(harness.destination_home))
        restarted = APIServerAdapter(
            PlatformConfig(
                enabled=True, extra={"key": "destination-default-key-012345"}
            )
        )
        restarted.gateway_runner = SimpleNamespace(
            config=GatewayConfig(multiplex_profiles=True)
        )
        restarted_server = TestServer(_app(restarted, multiplex=True), port=port)
        await restarted_server.start_server()
        harness.adapter = restarted
        harness.server = restarted_server

        observed = (await _advance(service, created.handoff_id)).snapshot
        assert observed.phase == "indeterminate"
        assert observed.failure_code == "run_interrupted"
        assert observed.checkpoint["run_id"] == run_id
        assert restarted._run_idempotency_ids == {run_id}
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        service.store.close()


@pytest.mark.parametrize(
    ("feature", "required", "failure_code"),
    [
        ("runs_idempotency", frozenset(), "runs_not_durable"),
        ("run_status", frozenset(), "run_status_unavailable"),
        ("run_stop", frozenset(), "capability_mismatch"),
        (
            "run_approval_response",
            frozenset({"approval"}),
            "capability_mismatch",
        ),
        ("run_steer", frozenset({"steering"}), "capability_mismatch"),
        ("run_steer", frozenset({"follow_up"}), "capability_mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_remote_handoff_capability_mismatch_fails_before_submit(
    tmp_path, monkeypatch, feature, required, failure_code
):
    async with _peer_gateway(
        tmp_path, monkeypatch, disabled_feature=feature
    ) as harness:
        service, created = _service(harness, required=required)
        failed = (await _advance(service, created.handoff_id)).snapshot

        assert failed.phase == "failed"
        assert failed.failure_code == failure_code
        assert not harness.adapter._run_idempotency_ids
        assert not harness.agents
        service.store.close()


def _redirect_handler(*, redirect_to: str | None = None, capabilities=False):
    records: list[dict[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            records.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            if redirect_to is not None and self.path != "/capabilities":
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.end_headers()
                return
            if capabilities:
                payload = json.dumps(
                    {
                        "features": {
                            "run_submission": True,
                            "runs_idempotency": {
                                "supported": True,
                                "durable": True,
                            },
                            "run_status": True,
                            "run_stop": True,
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(401)
            self.end_headers()

        def log_message(self, *_args):
            pass

    Handler.records = records
    return Handler, records


def _server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_remote_handoff_redirect_and_proxy_boundaries(tmp_path, monkeypatch):
    home = tmp_path / "initiator"
    home.mkdir()
    same_handler, same_records = _redirect_handler(capabilities=True)
    same_server, same_thread = _server(same_handler)
    same_url = f"http://127.0.0.1:{same_server.server_port}"
    same_handler.redirect_to = f"{same_url}/capabilities"

    class SameOriginRedirect(same_handler):
        def do_GET(self):  # noqa: N802
            if self.path != "/capabilities":
                same_records.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                self.send_response(302)
                self.send_header("Location", f"{same_url}/capabilities")
                self.end_headers()
                return
            super().do_GET()

    same_server.shutdown()
    same_thread.join(timeout=3)
    same_server.server_close()
    same_server, same_thread = _server(SameOriginRedirect)
    same_url = f"http://127.0.0.1:{same_server.server_port}"

    try:
        (home / "config.yaml").write_text(
            f"bot_peers:\n  spark:\n    url: {same_url}\n", encoding="utf-8"
        )
        (home / ".env").write_text(
            f"HERMES_PEER_SPARK_KEY={DESTINATION_KEY}\n", encoding="utf-8"
        )
        endpoint = HandoffEndpoint.parse("hermes://peer/spark/reviewer")
        assessment = PeerHermesChannel(home).validate_endpoint(endpoint, "workflow")
        assert assessment.available is True
        assert [record["authorization"] for record in same_records[-2:]] == [
            f"Bearer {DESTINATION_KEY}",
            f"Bearer {DESTINATION_KEY}",
        ]

        sink_handler, sink_records = _redirect_handler()
        sink, sink_thread = _server(sink_handler)
        sink_url = f"http://127.0.0.1:{sink.server_port}/capabilities"
        source_handler, source_records = _redirect_handler(redirect_to=sink_url)
        source, source_thread = _server(source_handler)
        source_url = f"http://127.0.0.1:{source.server_port}"
        try:
            (home / "config.yaml").write_text(
                f"bot_peers:\n  spark:\n    url: {source_url}\n",
                encoding="utf-8",
            )
            assessment = PeerHermesChannel(home).validate_endpoint(
                endpoint, "workflow"
            )
            assert assessment.available is False
            assert source_records[0]["authorization"] == (
                f"Bearer {DESTINATION_KEY}"
            )
            assert sink_records[0]["authorization"] is None

            proxy_handler, proxy_records = _redirect_handler()
            proxy, proxy_thread = _server(proxy_handler)
            monkeypatch.setenv(
                "HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}"
            )
            monkeypatch.setenv(
                "HTTPS_PROXY", f"http://127.0.0.1:{proxy.server_port}"
            )
            monkeypatch.setenv("NO_PROXY", "")
            (home / "config.yaml").write_text(
                f"bot_peers:\n  spark:\n    url: {same_url}\n",
                encoding="utf-8",
            )
            assert PeerHermesChannel(home).validate_endpoint(
                endpoint, "workflow"
            ).available
            assert proxy_records == []
        finally:
            for running, thread in (
                (source, source_thread),
                (sink, sink_thread),
                (proxy, proxy_thread),
            ):
                running.shutdown()
                running.server_close()
                thread.join(timeout=3)
    finally:
        same_server.shutdown()
        same_server.server_close()
        same_thread.join(timeout=3)


@pytest.mark.asyncio
async def test_remote_handoff_registry_and_credential_isolation(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch, blocked=True) as harness:
        channel = PeerHermesChannel(harness.initiating_home)
        missing = HandoffEndpoint.parse("hermes://peer/missing/reviewer")
        wrong_profile = HandoffEndpoint.parse("hermes://peer/spark/missing")
        assert channel.validate_endpoint(missing, "workflow").failure_code == (
            "peer_not_found"
        )
        assert channel.validate_endpoint(
            wrong_profile, "workflow"
        ).failure_code == "endpoint_unavailable"

        env_path = harness.initiating_home / ".env"
        env_path.write_text(
            "HERMES_PEER_OTHER_KEY=unrelated-key-0123456789abcdef\n",
            encoding="utf-8",
        )
        endpoint = HandoffEndpoint.parse("hermes://peer/spark/reviewer")
        assert channel.validate_endpoint(endpoint, "workflow").failure_code == (
            "peer_auth_unavailable"
        )
        env_path.write_text(
            f"HERMES_PEER_SPARK_KEY={DESTINATION_KEY}\n",
            encoding="utf-8",
        )

        service, first = _service(harness, handoff_key="retarget")
        bound = (await _advance(service, first.handoff_id)).snapshot
        before_ids = set(harness.adapter._run_idempotency_ids)
        (harness.initiating_home / "config.yaml").write_text(
            "bot_peers:\n  spark:\n    url: http://127.0.0.1:9\n",
            encoding="utf-8",
        )
        changed = (await _advance(service, first.handoff_id)).snapshot
        assert bound.phase == "prepared"
        assert changed.phase == "indeterminate"
        assert set(harness.adapter._run_idempotency_ids) == before_ids

        (harness.initiating_home / "config.yaml").write_text(
            f"bot_peers:\n  spark:\n    url: {harness.url}\n", encoding="utf-8"
        )
        second_service, second = _service(harness, handoff_key="rotation")
        await _advance(second_service, second.handoff_id)
        env_path.write_text(
            "HERMES_PEER_SPARK_KEY=rotated-key-0123456789abcdef\n",
            encoding="utf-8",
        )
        rotated = (await _advance(second_service, second.handoff_id)).snapshot
        assert rotated.phase == "indeterminate"
        assert not harness.adapter._run_idempotency_ids

        persisted = b"".join(
            path.read_bytes()
            for path in harness.initiating_home.glob("handoffs.db*")
        )
        for secret in (DESTINATION_KEY, "rotated-key-0123456789abcdef"):
            assert secret.encode() not in persisted
        assert harness.url.encode() not in persisted
        service.store.close()
        second_service.store.close()


@pytest.mark.asyncio
async def test_remote_handoff_follow_up_steer_and_lost_response(
    tmp_path, monkeypatch
):
    async with _peer_gateway(tmp_path, monkeypatch, blocked=True) as harness:
        service, created = _service(
            harness, required=frozenset({"steering", "follow_up"})
        )
        await _advance(service, created.handoff_id)
        await _advance(service, created.handoff_id)
        active = (await _advance(service, created.handoff_id)).snapshot
        assert active.phase == "active"
        service.command(
            active.handoff_id,
            "message",
            command_id="message-1",
            actor="workflow",
            text="Check the follow-up.",
            correlation_id="follow-up-1",
        )
        delivered = await _advance(service, active.handoff_id)
        assert delivered.operation == "deliver_command"
        assert harness.agents[0].steers == ["Check the follow-up."]
        assert service.store.get_command(
            active.handoff_id, "message-1"
        ).payload["correlation_id"] == "follow-up-1"

        service.command(
            active.handoff_id,
            "steer",
            command_id="steer-1",
            actor="workflow",
            text="Tighten the conclusion.",
        )
        original = runs_module.open_credentialed_url
        lose_once = True

        def lose_steer_response(request, **kwargs):
            nonlocal lose_once
            response = original(request, **kwargs)
            if lose_once and request.full_url.endswith("/steer"):
                lose_once = False
                response.read()
                response.close()
                raise TimeoutError("steer response lost")
            return response

        monkeypatch.setattr(runs_module, "open_credentialed_url", lose_steer_response)
        await _advance(service, active.handoff_id)
        await _advance(service, active.handoff_id)

        assert harness.agents[0].steers == [
            "Check the follow-up.",
            "Tighten the conclusion.",
        ]
        assert service.store.get_command(
            active.handoff_id, "steer-1"
        ).delivery_state == "indeterminate"
        service.store.close()
