from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from gateway.config import GatewayConfig, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
import hermes_cli.handoff.runs as runs_module
from hermes_cli.handoff import AgentHandoffService, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.peer import PeerHermesChannel
from hermes_cli.handoff.store import HandoffStore
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorStore
from plugins.workflow.models import ExecutionFence
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


SOURCE_KEY = "source-api-key-0123456789abcdef"
DESTINATION_KEY = "destination-api-key-0123456789abcdef"
DESTINATION_PROVIDER_KEY = "destination-provider-key-0123456789abcdef"


class _DeterministicProvider:
    def __init__(self, output: str = '{"answer":"approved"}') -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def client(self, **_kwargs):
        provider = self

        class Completions:
            def create(self, **kwargs):
                from agent.secret_scope import get_secret

                provider.calls.append(
                    {
                        "home": str(get_hermes_home().resolve()),
                        "provider_key": get_secret("OPENAI_API_KEY", ""),
                        "messages": kwargs["messages"],
                    }
                )
                if kwargs.get("stream"):
                    return iter(
                        [
                            SimpleNamespace(
                                model="test-model",
                                choices=[
                                    SimpleNamespace(
                                        delta=SimpleNamespace(
                                            content=provider.output,
                                            reasoning=None,
                                            tool_calls=None,
                                        ),
                                        finish_reason="stop",
                                    )
                                ],
                                usage=None,
                            )
                        ]
                    )
                return SimpleNamespace(
                    model="test-model",
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=provider.output,
                                reasoning=None,
                                tool_calls=[],
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )

        return SimpleNamespace(
            chat=SimpleNamespace(completions=Completions()), close=lambda: None
        )


class _BlockedAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupted = threading.Event()
        self.calls = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

    def interrupt(self, _message=None) -> None:
        self.interrupted.set()
        self.release.set()

    def steer(self, _text: str) -> bool:
        return True

    def run_conversation(self, **_kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=10)
        if self.interrupted.is_set():
            return {"final_response": "interrupted", "interrupted": True}
        return {"final_response": '{"answer":"approved"}'}


class _ApprovalAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.choice: str | None = None
        self.session_key = ""
        self.calls = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

    def interrupt(self, _message=None) -> None:
        if self.session_key:
            from tools.approval import resolve_gateway_approval

            resolve_gateway_approval(self.session_key, "deny", resolve_all=True)

    def steer(self, _text: str) -> bool:
        return True

    def run_conversation(self, **_kwargs):
        from tools import approval

        self.calls += 1
        self.session_key = approval.get_current_session_key()
        with approval._lock:
            notify = approval._gateway_notify_cbs[self.session_key]
        self.started.set()
        decision = approval._await_gateway_decision(
            self.session_key,
            notify,
            {
                "command": "dangerous remote action",
                "description": "remote approval",
                "pattern_key": "remote-action",
                "pattern_keys": ["remote-action"],
                "allow_session": False,
                "allow_permanent": False,
            },
        )
        self.choice = decision.get("choice")
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


@asynccontextmanager
async def _remote_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: _DeterministicProvider | None = None,
    agent_factory=None,
):
    initiating_home = tmp_path / "initiating" / ".hermes"
    destination_root = tmp_path / "destination"
    destination_home = destination_root / ".hermes"
    target_home = destination_home / "profiles" / "reviewer"
    initiating_home.mkdir(parents=True)
    target_home.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(initiating_home))
    source = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": SOURCE_KEY})
    )
    source_server = TestServer(_app(source, multiplex=False))
    await source_server.start_server()

    monkeypatch.setattr(Path, "home", lambda: destination_root)
    monkeypatch.setenv("HERMES_HOME", str(destination_home))
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={DESTINATION_KEY}\n"
        f"OPENAI_API_KEY={DESTINATION_PROVIDER_KEY}\n",
        encoding="utf-8",
    )
    (target_home / "config.yaml").write_text(
        "model:\n"
        "  provider: openai-api\n"
        "  default: test-model\n"
        "  api_mode: chat_completions\n"
        "agent:\n  max_iterations: 3\n"
        "auxiliary:\n  title_generation:\n    enabled: false\n",
        encoding="utf-8",
    )
    secret_scope.set_multiplex_active(True)
    if provider is not None:
        monkeypatch.setattr("run_agent.OpenAI", provider.client)
    adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": "destination-default-key-012345"})
    )
    adapter.gateway_runner = SimpleNamespace(
        config=GatewayConfig(multiplex_profiles=True)
    )
    agents: list[object] = []
    if agent_factory is not None:
        def create_agent(**_kwargs):
            agent = agent_factory()
            agents.append(agent)
            return agent

        adapter._create_agent = create_agent
    server = TestServer(_app(adapter, multiplex=True))
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    (initiating_home / "config.yaml").write_text(
        f"bot_peers:\n  spark:\n    url: {url}\n", encoding="utf-8"
    )
    (initiating_home / ".env").write_text(
        f"API_SERVER_KEY={SOURCE_KEY}\n"
        f"HERMES_PEER_SPARK_KEY={DESTINATION_KEY}\n",
        encoding="utf-8",
    )
    initiating_token = set_hermes_home_override(initiating_home)
    try:
        yield SimpleNamespace(
            initiating_home=initiating_home,
            destination_home=destination_home,
            target_home=target_home,
            adapter=adapter,
            server=server,
            source=source,
            source_server=source_server,
            agents=agents,
            url=url,
        )
    finally:
        for agent in agents:
            release = getattr(agent, "release", None)
            if release is not None:
                release.set()
            try:
                agent.interrupt()
            except Exception:
                pass
        tasks = list(adapter._active_run_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()
        await source_server.close()
        _close_adapter(adapter)
        _close_adapter(source)
        secret_scope.set_multiplex_active(False)
        reset_hermes_home_override(initiating_token)


def _assigned_package(
    workflow_writer,
    root: Path,
    *,
    interaction_policy: str = "deny",
    deadline: str = "PT4H",
):
    path = workflow_writer(
        root,
        name=f"remote-review-{interaction_policy}",
        nodes=[
            {
                "id": "review",
                "prompt": "Return the release review as structured JSON.",
                "output_format": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                    "additionalProperties": False,
                },
            }
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "outward_action_nodes: [review]\n"
        "assignments:\n"
        "  review:\n"
        "    endpoint: hermes://peer/spark/reviewer\n"
        f"    interaction_policy: {interaction_policy}\n"
        f"    deadline: {deadline}\n"
        "    on_deadline: cancel_and_fail\n",
        encoding="utf-8",
    )
    return load_workflow(path)


def _coordinator(home: Path, clock):
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id=f"remote-e2e-{time.monotonic_ns()}",
        ),
        hermes_home=home,
        heartbeat_seconds=1,
        lease_seconds=300,
        utcnow=lambda: clock["now"],
    )
    store = RunStore(home)
    coordinator = CoordinatorStore(store.database)
    identity = service._identity()
    leadership = coordinator.try_acquire(
        identity, now=clock["now"], lease_seconds=300
    )
    assert leadership.is_leader
    scheduler = service._scheduler(
        store, fence=ExecutionFence(identity.owner_id, leadership.lease.epoch)
    )
    return service, store, coordinator, identity, leadership, scheduler


async def _start(store: RunStore, package, key: str):
    def admit():
        prepared = store.prepare_run_snapshot(package, initiator_profile="default")
        return store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key=key,
                concurrency_key=key,
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )

    return await asyncio.to_thread(admit)


async def _sweep(parts, clock) -> None:
    service, store, coordinator, identity, leadership, scheduler = parts
    clock["now"] += timedelta(seconds=6)
    await asyncio.to_thread(
        service._sweep_once,
        store,
        coordinator,
        identity,
        leadership.lease.epoch,
        scheduler,
    )


async def _drive(parts, clock, predicate, *, attempts=100):
    for _ in range(attempts):
        await _sweep(parts, clock)
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("remote workflow did not converge")


def _request(url: str, key: str):
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read())


@pytest.mark.asyncio
async def test_remote_workflow_handoff_uses_destination_profile_and_credentials(
    tmp_path, monkeypatch, workflow_writer
):
    provider = _DeterministicProvider()
    async with _remote_gateway(
        tmp_path, monkeypatch, provider=provider
    ) as gateway:
        package = _assigned_package(
            workflow_writer, tmp_path / "remote-workflow-main"
        )
        clock = {"now": datetime.now(timezone.utc)}
        parts = _coordinator(gateway.initiating_home, clock)
        store = parts[1]
        admitted = await _start(store, package, "remote-workflow-main")
        try:
            await _drive(
                parts,
                clock,
                lambda: store.load_run(admitted.run_id)["status"] == "succeeded",
            )
            final = store.load_run(admitted.run_id)
        finally:
            parts[-1].shutdown(deadline_seconds=2)

        handoff_id = final["nodes"]["review"]["handoff"]["handoff_id"]
        handoff_store = HandoffStore(gateway.initiating_home / "handoffs.db")
        try:
            handoff = handoff_store.get(handoff_id)
        finally:
            handoff_store.close()
        assert handoff.phase == "succeeded"
        assert handoff.checkpoint["run_id"] in gateway.adapter._run_idempotency_ids
        assert handoff.checkpoint["session_id"]
        assert len(provider.calls) == 1
        assert provider.calls[0]["home"] == str(gateway.target_home.resolve())
        assert provider.calls[0]["provider_key"] == DESTINATION_PROVIDER_KEY
        artifact = next(item for item in final["artifacts"] if item["node_id"] == "review")
        assert json.loads(
            (store.run_directory(admitted.run_id) / artifact["relative_path"]).read_text()
        ) == {"answer": "approved"}

        session_url = (
            f"{gateway.url}/p/reviewer/api/sessions/"
            f"{handoff.checkpoint['session_id']}"
        )
        status, session = await asyncio.to_thread(
            _request, session_url, DESTINATION_KEY
        )
        assert status == 200
        assert session["session"]["id"] == handoff.checkpoint["session_id"]
        with pytest.raises(urllib.error.HTTPError) as rejected:
            await asyncio.to_thread(_request, session_url, SOURCE_KEY)
        assert rejected.value.code == 401

        persisted = b"".join(
            path.read_bytes()
            for root in (gateway.initiating_home, gateway.destination_home)
            for path in root.glob("*.db*")
        )
        assert DESTINATION_KEY.encode() not in persisted
        assert DESTINATION_PROVIDER_KEY.encode() not in persisted


@pytest.mark.parametrize(
    ("action", "expected_choice"), [("approve", "once"), ("reject", "deny")]
)
@pytest.mark.asyncio
async def test_remote_handoff_approval_pause_restart_and_response(
    tmp_path, monkeypatch, workflow_writer, action, expected_choice
):
    async with _remote_gateway(
        tmp_path, monkeypatch, agent_factory=_ApprovalAgent
    ) as gateway:
        package = _assigned_package(
            workflow_writer,
            tmp_path / f"remote-approval-{action}",
            interaction_policy="pause",
        )
        clock = {"now": datetime.now(timezone.utc)}
        first = _coordinator(gateway.initiating_home, clock)
        store = first[1]
        admitted = await _start(store, package, f"remote-approval-{action}")
        await _drive(
            first,
            clock,
            lambda: store.load_run(admitted.run_id)["status"] == "paused",
        )
        paused = store.load_run(admitted.run_id)
        pending = paused["nodes"]["review"]["pending_interaction"]
        assert pending["type"] == "handoff_input"
        assert set(pending) == {
            "type",
            "interaction_id",
            "node_id",
            "remote_request_id",
            "remote_choices",
        }

        first[-1].shutdown(deadline_seconds=2)
        first[2].release(
            first[3], epoch=first[4].lease.epoch, now=clock["now"]
        )
        clock["now"] += timedelta(minutes=1)
        restarted = _coordinator(gateway.initiating_home, clock)
        restarted_store = restarted[1]
        try:
            getattr(restarted_store, f"{action}_run")(
                admitted.run_id,
                expected_state_version=paused["state_version"],
                interaction_id=pending["interaction_id"],
            )
            await _drive(
                restarted,
                clock,
                lambda: restarted_store.load_run(admitted.run_id)["status"]
                == "succeeded",
            )
        finally:
            restarted[-1].shutdown(deadline_seconds=2)

        agent = gateway.agents[0]
        assert agent.choice == expected_choice
        assert agent.calls == 1
        assert len(gateway.adapter._run_idempotency_ids) == 1


def _peer_service(home: Path, *, key: str, required=frozenset({"approval"})):
    service = AgentHandoffService(
        HandoffStore(home / "handoffs.db"),
        channel=PeerHermesChannel(home),
    )
    created = service.create(
        HandoffSpec(
            mode="task",
            endpoint=HandoffEndpoint.parse("hermes://peer/spark/reviewer"),
            prompt="Review this race.",
            output_schema=None,
            deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
            attribution={"workflow": "race", "node": "review"},
            required_capabilities=required,
        ),
        f"workflow/{key}",
        handoff_key=key,
    )
    return service, created


async def _advance(service, handoff_id):
    return await asyncio.to_thread(service.advance, handoff_id, budget_seconds=3)


async def _to_phase(service, created, phase: str):
    snapshot = created
    for _ in range(12):
        if snapshot.phase == phase:
            return snapshot
        snapshot = (await _advance(service, created.handoff_id)).snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"handoff did not reach {phase}")


@pytest.mark.asyncio
async def test_remote_handoff_stop_interrupted_and_cancellation_races(
    tmp_path, monkeypatch, workflow_writer
):
    async with _remote_gateway(
        tmp_path / "stop", monkeypatch, agent_factory=_BlockedAgent
    ) as gateway:
        service, created = _peer_service(
            gateway.initiating_home, key="successful-stop", required=frozenset()
        )
        active = await _to_phase(service, created, "active")
        service.command(
            active.handoff_id,
            "cancel",
            command_id="cancel-1",
            actor="workflow",
        )
        await _advance(service, active.handoff_id)
        terminal = await _to_phase(service, active, "cancelled")
        assert terminal.phase == "cancelled"

        second, second_created = _peer_service(
            gateway.initiating_home, key="already-interrupted", required=frozenset()
        )
        second_active = await _to_phase(second, second_created, "active")
        run_id = second_active.checkpoint["run_id"]
        gateway.adapter._set_run_status(run_id, "interrupted")
        interrupted = (await _advance(second, second_active.handoff_id)).snapshot
        assert interrupted.phase == "indeterminate"
        assert interrupted.failure_code == "run_interrupted"

        third, third_created = _peer_service(
            gateway.initiating_home, key="cancel-complete", required=frozenset()
        )
        third_active = await _to_phase(third, third_created, "active")
        agent = gateway.agents[-1]
        agent.release.set()
        third.command(
            third_active.handoff_id,
            "cancel",
            command_id="cancel-complete",
            actor="workflow",
        )
        for _ in range(8):
            raced = (await _advance(third, third_active.handoff_id)).snapshot
            if raced.phase in {"succeeded", "cancelled"}:
                break
            await asyncio.sleep(0.02)
        assert raced.phase in {"succeeded", "cancelled"}
        assert len(gateway.adapter._run_idempotency_ids) == 3
        for candidate in (service, second, third):
            candidate.store.close()

    async with _remote_gateway(
        tmp_path / "approval-races", monkeypatch, agent_factory=_ApprovalAgent
    ) as gateway:
        response_service, response_created = _peer_service(
            gateway.initiating_home, key="cancel-response"
        )
        waiting = await _to_phase(response_service, response_created, "needs_input")
        response_service.command(
            waiting.handoff_id,
            "respond",
            command_id="response-1",
            actor="workflow",
            request_id=waiting.checkpoint["approval_request_id"],
            choice="once",
        )
        response_service.command(
            waiting.handoff_id,
            "cancel",
            command_id="cancel-response",
            actor="workflow",
        )
        for _ in range(10):
            response_race = (
                await _advance(response_service, waiting.handoff_id)
            ).snapshot
            if response_race.phase in {"succeeded", "cancelled", "indeterminate"}:
                break
        assert response_race.phase in {"succeeded", "cancelled", "indeterminate"}
        assert gateway.agents[0].calls == 1

        package = _assigned_package(
            workflow_writer,
            tmp_path / "deadline-input",
            interaction_policy="pause",
            deadline="PT1M",
        )
        clock = {"now": datetime.now(timezone.utc)}
        parts = _coordinator(gateway.initiating_home, clock)
        workflow_store = parts[1]
        admitted = await _start(workflow_store, package, "deadline-input")
        await _drive(
            parts,
            clock,
            lambda: workflow_store.load_run(admitted.run_id)["status"] == "paused",
        )
        clock["now"] += timedelta(minutes=2)
        try:
            await _drive(
                parts,
                clock,
                lambda: workflow_store.load_run(admitted.run_id)["status"]
                == "failed",
            )
        finally:
            parts[-1].shutdown(deadline_seconds=2)
        deadline_race = workflow_store.load_run(admitted.run_id)
        assert deadline_race["last_error"]["code"] == "handoff_deadline_exceeded"
        assert len(gateway.adapter._run_idempotency_ids) == 2
        response_service.store.close()


@pytest.mark.parametrize(
    "cut",
    [
        "before_bind",
        "after_submit_journal",
        "after_keyed_reservation",
        "after_run_id_persistence",
        "after_interaction_persistence",
        "after_response_command_journal",
    ],
)
@pytest.mark.asyncio
async def test_remote_handoff_restart_cuts_are_convergent(
    tmp_path, monkeypatch, cut
):
    async with _remote_gateway(
        tmp_path, monkeypatch, agent_factory=_ApprovalAgent
    ) as gateway:
        service, created = _peer_service(gateway.initiating_home, key=cut)
        snapshot = created
        if cut == "before_bind":
            assert snapshot.mechanism is None
        else:
            snapshot = (await _advance(service, created.handoff_id)).snapshot
            assert snapshot.phase == "prepared"
        if cut == "after_submit_journal":
            lease = service.store.claim_advance(
                created.handoff_id,
                "crashed-submit",
                now=datetime.now(timezone.utc),
                lease_seconds=30,
            )
            assert lease is not None
            snapshot = service.store.journal_attempt(lease, "submit")
            service.store.release_advance(lease, next_advance_at=None)
            assert snapshot.submit_attempted_at is not None
            assert not gateway.adapter._run_idempotency_ids
        elif cut == "after_keyed_reservation":
            original = runs_module.open_credentialed_url

            def lose_reserved_response(request, **kwargs):
                response = original(request, **kwargs)
                if request.full_url.endswith("/v1/runs"):
                    response.read()
                    response.close()
                    raise TimeoutError("reserved response lost")
                return response

            monkeypatch.setattr(
                runs_module, "open_credentialed_url", lose_reserved_response
            )
            snapshot = (await _advance(service, created.handoff_id)).snapshot
            monkeypatch.setattr(runs_module, "open_credentialed_url", original)
            assert snapshot.phase == "indeterminate"
            assert "run_id" not in snapshot.checkpoint
            assert len(gateway.adapter._run_idempotency_ids) == 1
        elif cut in {
            "after_run_id_persistence",
            "after_interaction_persistence",
            "after_response_command_journal",
        }:
            snapshot = (await _advance(service, created.handoff_id)).snapshot
            assert snapshot.checkpoint["run_id"] in (
                gateway.adapter._run_idempotency_ids
            )
        if cut in {
            "after_interaction_persistence",
            "after_response_command_journal",
        }:
            snapshot = await _to_phase(service, snapshot, "needs_input")
        if cut == "after_response_command_journal":
            service.command(
                snapshot.handoff_id,
                "respond",
                command_id="response-cut",
                actor="workflow",
                request_id=snapshot.checkpoint["approval_request_id"],
                choice="once",
            )
            assert service.store.get_command(
                snapshot.handoff_id, "response-cut"
            ).delivery_state == "pending"
        service.store.close()

        restarted = AgentHandoffService(
            HandoffStore(gateway.initiating_home / "handoffs.db"),
            channel=PeerHermesChannel(gateway.initiating_home),
        )
        response_recorded = cut == "after_response_command_journal"
        try:
            for _ in range(20):
                current = restarted.store.get(created.handoff_id)
                if current.phase == "needs_input" and not response_recorded:
                    restarted.command(
                        current.handoff_id,
                        "respond",
                        command_id="response-after-restart",
                        actor="workflow",
                        request_id=current.checkpoint["approval_request_id"],
                        choice="once",
                    )
                    response_recorded = True
                current = (await _advance(restarted, created.handoff_id)).snapshot
                if current.phase == "succeeded":
                    break
                await asyncio.sleep(0.02)
            assert current.phase == "succeeded"
            assert len(gateway.adapter._run_idempotency_ids) == 1
            assert gateway.agents[0].calls == 1
            assert gateway.agents[0].choice == "once"
        finally:
            restarted.store.close()
