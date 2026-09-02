from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent import secret_scope
from agent.plugin_agent import PluginAgentRunResult
from gateway.config import GatewayConfig, PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli.handoff import AgentHandoffService, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.store import HandoffStore
from hermes_cli.plugin_services import BackgroundServiceContext
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorStore
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.models import ExecutionFence
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.trust import build_risk_summary
from tools.bot_mode_dm import message_agent_tool_schema
from tools.bot_mode_probe import BOT_CHAT_TITLE


TARGET_KEY = "reviewer-api-key-0123456789abcdef"
DEFAULT_KEY = "default-api-key-0123456789abcdef"


class _OpenAIWireProvider(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        body = json.dumps(
            {
                "object": "list",
                "data": [{"id": "test-model", "object": "model"}],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        with type(self).lock:
            type(self).requests.append(request)
        if request.get("stream"):
            chunks = (
                {
                    "id": "chatcmpl-handoff-e2e",
                    "object": "chat.completion.chunk",
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "CLI receipt approved"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-handoff-e2e",
                    "object": "chat.completion.chunk",
                    "model": "test-model",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                },
            )
            payload = "".join(
                f"data: {json.dumps(chunk)}\n\n" for chunk in chunks
            ) + "data: [DONE]\n\n"
            encoded = payload.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        encoded = json.dumps(
            {
                "id": "chatcmpl-handoff-e2e",
                "object": "chat.completion",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "CLI receipt approved",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        pass


class _DeterministicProvider:
    """Fake only the external inference boundary used by a real AIAgent."""

    def __init__(
        self,
        output: str = '{"answer":"approved"}',
        *,
        hold_response: bool = False,
    ) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not hold_response:
            self.release.set()

    def client(self, **_kwargs):
        provider = self

        class Completions:
            def create(self, **kwargs):
                from hermes_constants import get_hermes_home

                provider.calls.append(
                    {
                        "home": str(get_hermes_home().resolve()),
                        "messages": kwargs["messages"],
                        "stream": bool(kwargs.get("stream")),
                    }
                )
                provider.started.set()
                if not provider.release.wait(timeout=20):
                    raise TimeoutError("deterministic inference boundary was not released")
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
            chat=SimpleNamespace(completions=Completions()),
            close=lambda: None,
        )


class _RecordingInferenceBoundary:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="ordinary workflow result",
            session_id="ordinary-workflow-session",
            provider="deterministic-e2e",
            model="deterministic-e2e-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 1, "model_calls": 1},
        )


@pytest.fixture
def profile_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    default_home = tmp_path / ".hermes"
    target_home = default_home / "profiles" / "reviewer"
    target_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    secret_scope.set_multiplex_active(True)
    try:
        yield default_home, target_home
    finally:
        secret_scope.set_multiplex_active(False)


def _write_profile_config(default_home: Path, target_home: Path, *, port: int) -> None:
    (default_home / ".env").write_text(
        f"API_SERVER_KEY={DEFAULT_KEY}\n", encoding="utf-8"
    )
    (target_home / ".env").write_text(
        f"API_SERVER_KEY={TARGET_KEY}\nOPENAI_API_KEY=fake-model-key\n",
        encoding="utf-8",
    )
    (default_home / "config.yaml").write_text(
        "gateway:\n"
        "  multiplex_profiles: true\n"
        "  api_server:\n"
        "    enabled: true\n"
        "    host: 127.0.0.1\n"
        f"    port: {port}\n",
        encoding="utf-8",
    )
    (target_home / "config.yaml").write_text(
        "model:\n"
        "  provider: openai-api\n"
        "  default: test-model\n"
        "  api_mode: chat_completions\n"
        "agent:\n"
        "  max_iterations: 3\n",
        encoding="utf-8",
    )


def _write_cli_profile_config(
    default_home: Path, target_home: Path, *, provider_port: int
) -> None:
    (default_home / "config.yaml").write_text(
        "gateway:\n"
        "  multiplex_profiles: true\n"
        "  api_server:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    (target_home / ".env").write_text(
        "CUSTOM_API_KEY=local-provider-key\n", encoding="utf-8"
    )
    (target_home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom\n"
        "  default: test-model\n"
        f"  base_url: http://127.0.0.1:{provider_port}/v1\n"
        "  api_mode: chat_completions\n"
        "agent:\n"
        "  max_iterations: 3\n"
        "auxiliary:\n"
        "  title_generation:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )


@asynccontextmanager
async def _real_multiplex_gateway(profile_homes, provider, monkeypatch):
    default_home, target_home = profile_homes
    monkeypatch.setattr("run_agent.OpenAI", provider.client)
    adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": DEFAULT_KEY})
    )
    adapter.gateway_runner = SimpleNamespace(
        config=GatewayConfig(multiplex_profiles=True)
    )
    app = web.Application(middlewares=[adapter._make_profile_prefix_middleware()])
    app._state["api_server_adapter"] = adapter
    for method, path, handler in adapter._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, f"/p/{{profile}}{path}", handler)
    server = TestServer(app)
    await server.start_server()
    _write_profile_config(default_home, target_home, port=server.port)
    try:
        yield adapter
    finally:
        await server.close()
        adapter._close_cached_session_dbs()
        adapter._run_idempotency_store.close()
        adapter._response_store.close()


def _assigned_package(workflow_writer, root: Path):
    path = workflow_writer(
        root,
        name="release-review",
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
        "    endpoint: hermes://local/reviewer\n"
        "    interaction_policy: deny\n"
        "    deadline: PT4H\n"
        "    on_deadline: cancel_and_fail\n",
        encoding="utf-8",
    )
    return load_workflow(path)


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before deadline")


def _elected_coordinator(home: Path, *, utcnow=None):
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id=f"handoff-e2e-{time.monotonic_ns()}",
        ),
        hermes_home=home,
        heartbeat_seconds=1,
        lease_seconds=300,
        utcnow=utcnow,
    )
    store = RunStore(home)
    coordinator_store = CoordinatorStore(store.database)
    identity = service._identity()
    observed = utcnow() if utcnow is not None else datetime.now(timezone.utc)
    leadership = coordinator_store.try_acquire(
        identity, now=observed, lease_seconds=300
    )
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    scheduler = service._scheduler(store, fence=fence)
    return service, store, coordinator_store, identity, leadership, scheduler


@pytest.mark.asyncio
async def test_assigned_workflow_crosses_real_profile_and_resumes_after_restart(
    profile_homes, workflow_writer, monkeypatch
):
    default_home, target_home = profile_homes
    provider = _DeterministicProvider(hold_response=True)
    async with _real_multiplex_gateway(profile_homes, provider, monkeypatch) as adapter:
        package = _assigned_package(workflow_writer, default_home / "packages")
        risk = build_risk_summary(package, assess_compatibility(package)).to_dict()
        assert risk["assignments"] == (
            {
                "node_id": "review",
                "endpoint": "hermes://local/reviewer",
                "target_profile": "reviewer",
                "mode": "task",
                "interaction_policy": "deny",
                "deadline": "PT4H",
                "on_deadline": "cancel_and_fail",
                "possible_mechanisms": (
                    ("runs",) if os.name == "nt" else ("runs", "local_cli")
                ),
            },
        )

        clock = {"now": datetime.now(timezone.utc)}
        first = _elected_coordinator(default_home, utcnow=lambda: clock["now"])
        service, store, coordinator_store, identity, leadership, scheduler = first
        prepared = await asyncio.to_thread(
            store.prepare_run_snapshot, package, initiator_profile="default"
        )
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key="release-review-1",
                concurrency_key=package.definition.name,
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )

        await asyncio.to_thread(
            service._sweep_once,
            store,
            coordinator_store,
            identity,
            leadership.lease.epoch,
            scheduler,
        )
        await asyncio.to_thread(
            _wait_until,
            lambda: (
                store.load_run(admitted.run_id)["nodes"]["review"]["state"]
                == "waiting_handoff"
                and scheduler.active_run_count == 0
            ),
        )
        waiting = store.load_run(admitted.run_id)
        handoff = waiting["nodes"]["review"]["handoff"]
        assert waiting["status"] == "running"
        assert scheduler.active_run_count == 0
        assert handoff["last_observed_phase"] == "prepared"
        assert store.database.is_file()
        assert (default_home / "handoffs.db").is_file()

        for _ in range(4):
            clock["now"] += timedelta(seconds=6)
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            if adapter._run_idempotency_ids:
                break
        await asyncio.to_thread(
            _wait_until,
            lambda: bool(adapter._run_idempotency_ids) and provider.started.is_set(),
        )
        assert len(adapter._run_idempotency_ids) == 1
        destination_run_id = next(iter(adapter._run_idempotency_ids))
        destination_task = adapter._active_run_tasks[destination_run_id]
        assert not destination_task.done()
        assert adapter._run_statuses[destination_run_id]["status"] == "running"

        scheduler.shutdown(deadline_seconds=2)
        coordinator_store.release(
            identity,
            epoch=leadership.lease.epoch,
            now=clock["now"],
        )
        restart_clock = {"now": clock["now"] + timedelta(minutes=1)}
        second = _elected_coordinator(
            default_home, utcnow=lambda: restart_clock["now"]
        )
        (
            restarted_service,
            restarted_store,
            restarted_coordinator_store,
            restarted_identity,
            restarted_leadership,
            restarted_scheduler,
        ) = second
        try:
            candidates, _cursor, _exhausted = restarted_store.coordinator_candidates(
                after=None, now=restart_clock["now"]
            )
            assert admitted.run_id in {row["run_id"] for row in candidates}
            assert adapter._run_idempotency_ids == {destination_run_id}
            assert not destination_task.done()
            provider.release.set()
            await destination_task
            assert adapter._run_statuses[destination_run_id]["status"] == "completed"
            for _ in range(4):
                restart_clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    restarted_service._sweep_once,
                    restarted_store,
                    restarted_coordinator_store,
                    restarted_identity,
                    restarted_leadership.lease.epoch,
                    restarted_scheduler,
                )
                if restarted_store.load_run(admitted.run_id)["status"] == "succeeded":
                    break
            await asyncio.to_thread(
                _wait_until,
                lambda: restarted_store.load_run(admitted.run_id)["status"]
                == "succeeded",
            )
            final = restarted_store.load_run(admitted.run_id)
        finally:
            provider.release.set()
            restarted_scheduler.shutdown(deadline_seconds=2)

        handoff_store = HandoffStore(default_home / "handoffs.db")
        try:
            completed_handoff = handoff_store.get(handoff["handoff_id"])
        finally:
            handoff_store.close()
        assert completed_handoff.phase == "succeeded"
        assert completed_handoff.binding == {
            "profile": "reviewer",
            "mechanism": "runs",
        }
        assert completed_handoff.checkpoint["run_id"] == destination_run_id
        assert completed_handoff.checkpoint["idempotency_key"] == (
            f"handoff-{completed_handoff.handoff_id}"
        )
        artifact = next(
            item for item in final["artifacts"] if item["node_id"] == "review"
        )
        structured_output = (
            restarted_store.run_directory(admitted.run_id)
            / artifact["relative_path"]
        ).read_text(encoding="utf-8")
        assert json.loads(structured_output) == {"answer": "approved"}
        assert len(provider.calls) == 1
        assert {call["home"] for call in provider.calls} == {
            str(target_home.resolve())
        }
        assert destination_run_id in adapter._run_idempotency_ids


@pytest.mark.asyncio
async def test_active_destination_cancellation_converges_through_real_stop(
    profile_homes, workflow_writer, monkeypatch
):
    default_home, _target_home = profile_homes
    provider = _DeterministicProvider(hold_response=True)
    async with _real_multiplex_gateway(profile_homes, provider, monkeypatch) as adapter:
        package = _assigned_package(
            workflow_writer, default_home / "cancellation-package"
        )
        clock = {"now": datetime.now(timezone.utc)}
        service, store, coordinator_store, identity, leadership, scheduler = (
            _elected_coordinator(default_home, utcnow=lambda: clock["now"])
        )
        prepared = await asyncio.to_thread(
            store.prepare_run_snapshot, package, initiator_profile="default"
        )
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key="release-review-cancel-active",
                concurrency_key="release-review-cancel-active",
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )
        try:
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            await asyncio.to_thread(
                _wait_until,
                lambda: (
                    store.load_run(admitted.run_id)["nodes"]["review"]["state"]
                    == "waiting_handoff"
                    and scheduler.active_run_count == 0
                ),
            )
            for _ in range(4):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
                if adapter._run_idempotency_ids:
                    break
            await asyncio.to_thread(
                _wait_until,
                lambda: bool(adapter._run_idempotency_ids)
                and provider.started.is_set(),
            )
            assert len(adapter._run_idempotency_ids) == 1
            destination_run_id = next(iter(adapter._run_idempotency_ids))
            destination_task = adapter._active_run_tasks[destination_run_id]
            assert not destination_task.done()
            assert adapter._run_statuses[destination_run_id]["status"] == "running"

            cancelling = store.cancel_run(admitted.run_id)
            assert cancelling["cancellation_outcome"] == "cancelling"
            assert cancelling["status"] == "running"
            assert cancelling["desired_status"] == "cancelled"
            assert cancelling["nodes"]["review"]["state"] == "waiting_handoff"
            assert cancelling["nodes"]["review"]["handoff_cancel"]["state"] == (
                "pending"
            )

            clock["now"] += timedelta(seconds=6)
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            pending = store.load_run(admitted.run_id)
            assert pending["status"] == "running"
            assert pending["desired_status"] == "cancelled"
            assert pending["nodes"]["review"]["state"] == "waiting_handoff"
            assert pending["nodes"]["review"]["handoff_cancel"]["state"] == (
                "recorded"
            )
            assert adapter._run_statuses[destination_run_id]["status"] == "stopping"

            provider.release.set()
            await destination_task
            assert adapter._run_statuses[destination_run_id]["status"] == "cancelled"
            for _ in range(3):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
                if store.load_run(admitted.run_id)["status"] == "cancelled":
                    break
            cancelled = store.load_run(admitted.run_id)
            assert cancelled["status"] == "cancelled"
            assert cancelled["desired_status"] is None
            assert cancelled["nodes"]["review"]["state"] == "cancelled"
            handoff_id = cancelled["nodes"]["review"]["handoff"]["handoff_id"]
            handoff_store = HandoffStore(default_home / "handoffs.db")
            try:
                terminal_handoff = handoff_store.get(handoff_id)
            finally:
                handoff_store.close()
            assert terminal_handoff.phase == "cancelled"
            assert terminal_handoff.checkpoint["run_id"] == destination_run_id
            assert terminal_handoff.checkpoint["status"] == "cancelled"
        finally:
            provider.release.set()
            scheduler.shutdown(deadline_seconds=2)


@pytest.mark.asyncio
async def test_destination_interruption_is_actionable_and_redacted(
    profile_homes, workflow_writer, monkeypatch
):
    default_home, _target_home = profile_homes
    provider = _DeterministicProvider()
    async with _real_multiplex_gateway(profile_homes, provider, monkeypatch) as adapter:
        package = _assigned_package(workflow_writer, default_home / "attention-package")
        clock = {"now": datetime.now(timezone.utc)}
        (
            service,
            store,
            coordinator_store,
            identity,
            leadership,
            scheduler,
        ) = _elected_coordinator(default_home, utcnow=lambda: clock["now"])
        prepared = await asyncio.to_thread(
            store.prepare_run_snapshot, package, initiator_profile="default"
        )
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key="release-review-attention",
                concurrency_key="release-review-attention",
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )
        try:
            clock["now"] += timedelta(seconds=6)
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            await asyncio.to_thread(
                _wait_until,
                lambda: (
                    store.load_run(admitted.run_id)["nodes"]["review"]["state"]
                    == "waiting_handoff"
                    and scheduler.active_run_count == 0
                ),
            )
            for _ in range(3):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
                if adapter._run_idempotency_ids:
                    break
            assert len(adapter._run_idempotency_ids) == 1
            destination_run_id = next(iter(adapter._run_idempotency_ids))
            destination_task = adapter._active_run_tasks.get(destination_run_id)
            if destination_task is not None:
                await destination_task

            # Simulate a destination gateway dying after keyed acceptance but before
            # importing a terminal status.  The real Runs durability path must turn
            # the orphaned owner into `interrupted` on the next HTTP observation.
            stale_status = json.dumps(
                {"run_id": destination_run_id, "status": "running"},
                sort_keys=True,
                separators=(",", ":"),
            )
            with adapter._run_idempotency_store._lock:
                adapter._run_idempotency_store._conn.execute(
                    "UPDATE run_idempotency SET status_json=?, owner_pid=?, "
                    "owner_started=? WHERE run_id=?",
                    (stale_status, 999_999_999, 1, destination_run_id),
                )
                adapter._run_idempotency_store._conn.commit()
            adapter._run_statuses.pop(destination_run_id, None)

            for _ in range(2):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
            outbox = NotificationOutbox(store)
            outbox.reconcile_journal()
            attention = outbox.pending_attention(run_id=admitted.run_id)
            assert len(attention) == 1
            payload = attention[0]["payload"]
            handoff_attention = payload["handoff"]
            assert handoff_attention["phase"] == "indeterminate"
            assert handoff_attention["failure_code"] == "run_interrupted"
            handoff_id = handoff_attention["handoff_id"]
            assert handoff_attention["commands"] == {
                "show": f"hermes handoff show {handoff_id}",
                "evidence": f"hermes handoff evidence {handoff_id}",
                "reconcile": f"hermes handoff reconcile {handoff_id}",
            }

            handoff_store = HandoffStore(default_home / "handoffs.db")
            try:
                evidence = AgentHandoffService(handoff_store).evidence(handoff_id)
                serialized_evidence = json.dumps(
                    [
                        {
                            "kind": event.kind,
                            "actor": event.actor,
                            "data": dict(event.data),
                        }
                        for event in evidence.events
                    ],
                    sort_keys=True,
                )
            finally:
                handoff_store.close()
            serialized_attention = json.dumps(attention, sort_keys=True)
            for secret in (
                DEFAULT_KEY,
                TARGET_KEY,
                "fake-model-key",
                "Return the release review as structured JSON.",
            ):
                assert secret not in serialized_attention
                assert secret not in serialized_evidence
        finally:
            scheduler.shutdown(deadline_seconds=2)


@pytest.mark.asyncio
async def test_expired_deadline_drives_real_cancel_and_actionable_attention(
    profile_homes, workflow_writer, monkeypatch
):
    default_home, _target_home = profile_homes
    provider = _DeterministicProvider(hold_response=True)
    async with _real_multiplex_gateway(profile_homes, provider, monkeypatch) as adapter:
        package = _assigned_package(workflow_writer, default_home / "deadline-package")
        clock = {"now": datetime.now(timezone.utc)}
        service, store, coordinator_store, identity, leadership, scheduler = (
            _elected_coordinator(default_home, utcnow=lambda: clock["now"])
        )
        prepared = await asyncio.to_thread(
            store.prepare_run_snapshot, package, initiator_profile="default"
        )
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key="release-review-deadline",
                concurrency_key="release-review-deadline",
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )
        try:
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            await asyncio.to_thread(
                _wait_until,
                lambda: (
                    store.load_run(admitted.run_id)["nodes"]["review"]["state"]
                    == "waiting_handoff"
                    and scheduler.active_run_count == 0
                ),
            )
            for _ in range(4):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
                if adapter._run_idempotency_ids:
                    break
            await asyncio.to_thread(
                _wait_until,
                lambda: bool(adapter._run_idempotency_ids)
                and provider.started.is_set(),
            )
            assert len(adapter._run_idempotency_ids) == 1
            destination_run_id = next(iter(adapter._run_idempotency_ids))
            destination_task = adapter._active_run_tasks[destination_run_id]
            before_deadline = store.load_run(admitted.run_id)
            assert before_deadline["nodes"]["review"].get("handoff_cancel") is None
            assert not destination_task.done()
            assert adapter._run_statuses[destination_run_id]["status"] == "running"

            clock["now"] += timedelta(hours=5)
            await asyncio.to_thread(
                service._sweep_once,
                store,
                coordinator_store,
                identity,
                leadership.lease.epoch,
                scheduler,
            )
            expired = store.load_run(admitted.run_id)
            assert expired["status"] == "running"
            assert expired["nodes"]["review"]["state"] == "waiting_handoff"
            assert expired["nodes"]["review"]["handoff_cancel"] == {
                "command_id": (
                    f"workflow-{admitted.run_id}-review-1-deadline-cancel"
                ),
                "reason_code": "deadline_exceeded",
                "state": "recorded",
            }
            assert adapter._run_statuses[destination_run_id]["status"] == "stopping"

            outbox = NotificationOutbox(store)
            outbox.reconcile_journal()
            attention = outbox.pending_attention(run_id=admitted.run_id)
            assert len(attention) == 1
            handoff_attention = attention[0]["payload"]["handoff"]
            assert handoff_attention["failure_code"] == "deadline_exceeded"
            handoff_id = handoff_attention["handoff_id"]
            assert handoff_attention["commands"] == {
                "show": f"hermes handoff show {handoff_id}",
                "evidence": f"hermes handoff evidence {handoff_id}",
                "reconcile": f"hermes handoff reconcile {handoff_id}",
            }

            handoff_store = HandoffStore(default_home / "handoffs.db")
            try:
                evidence = AgentHandoffService(handoff_store).evidence(handoff_id)
                serialized_evidence = json.dumps(
                    [
                        {
                            "kind": event.kind,
                            "actor": event.actor,
                            "data": dict(event.data),
                        }
                        for event in evidence.events
                    ],
                    sort_keys=True,
                )
            finally:
                handoff_store.close()
            serialized_attention = json.dumps(attention, sort_keys=True)
            for secret in (
                DEFAULT_KEY,
                TARGET_KEY,
                "fake-model-key",
                "Return the release review as structured JSON.",
            ):
                assert secret not in serialized_attention
                assert secret not in serialized_evidence

            provider.release.set()
            await destination_task
            assert adapter._run_statuses[destination_run_id]["status"] == "cancelled"
            for _ in range(3):
                clock["now"] += timedelta(seconds=6)
                await asyncio.to_thread(
                    service._sweep_once,
                    store,
                    coordinator_store,
                    identity,
                    leadership.lease.epoch,
                    scheduler,
                )
                if store.load_run(admitted.run_id)["status"] == "failed":
                    break
            final = store.load_run(admitted.run_id)
            assert final["status"] == "failed"
            assert final["last_error"] == {
                "code": "handoff_deadline_exceeded",
                "message": "handoff deadline exceeded",
                "node_id": "review",
            }
            assert final["nodes"]["review"]["state"] == "failed"
        finally:
            provider.release.set()
            scheduler.shutdown(deadline_seconds=2)


def test_unassigned_workflow_and_bot_mode_contracts_remain_separate(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "unassigned-package",
            name="ordinary-workflow",
            nodes=[{"id": "ordinary", "prompt": "Run without a handoff."}],
        )
    )
    (
        _service,
        store,
        _coordinator_store,
        identity,
        leadership,
        unused_scheduler,
    ) = _elected_coordinator(tmp_path / "ordinary-home")
    prepared = store.prepare_run_snapshot(package, initiator_profile="default")
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="deterministic-e2e",
            model="deterministic-e2e-model",
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="ordinary-workflow",
            concurrency_key="ordinary-workflow",
            execution_mode="background",
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None, admitted
    provider = _RecordingInferenceBoundary()
    scheduler = RunScheduler(
        store,
        agent_runner=provider,
        execution_fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    )
    try:
        projection = scheduler.advance(admitted.run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)
        unused_scheduler.shutdown(deadline_seconds=2)

    assert projection["status"] == "succeeded"
    assert len(provider.requests) == 1
    assert projection["nodes"]["ordinary"].get("handoff") is None
    assert not (store.hermes_home / "handoffs.db").exists()

    bot_schema = message_agent_tool_schema()["function"]
    assert BOT_CHAT_TITLE == "Bot Chat"
    assert bot_schema["name"] == "message_agent"
    assert "own Bot Chat" in bot_schema["description"]
    assert "FIRE-AND-FORGET" in bot_schema["description"]


def _assert_real_posix_cli_receipt(
    profile_homes, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_home, target_home = profile_homes
    monkeypatch.setenv("HOME", str(default_home.parent))
    monkeypatch.setenv("HERMES_STATE_DB_GUARD_BYPASS", "1")
    _OpenAIWireProvider.requests = []
    provider_server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIWireProvider)
    provider_thread = threading.Thread(
        target=provider_server.serve_forever, daemon=True
    )
    provider_thread.start()
    _write_cli_profile_config(
        default_home,
        target_home,
        provider_port=int(provider_server.server_address[1]),
    )

    handoff_store = HandoffStore(default_home / "handoffs.db")
    service = AgentHandoffService(handoff_store)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Answer this receipt check without using tools.",
        output_schema=None,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        attribution={"consumer": "workflow", "node": "receipt"},
        required_capabilities=frozenset(),
    )
    try:
        assessment = service.validate_endpoint(spec.endpoint, "workflow/receipt")
        created = service.create(
            spec,
            "workflow/receipt",
            handoff_key="receipt-node-1",
        )
        bound = service.advance(created.handoff_id, budget_seconds=2).snapshot
        submitted = service.advance(created.handoff_id, budget_seconds=2).snapshot
        deadline = time.monotonic() + 30
        completed = submitted
        while completed.phase not in {"succeeded", "failed", "indeterminate"}:
            assert time.monotonic() < deadline, completed
            time.sleep(0.05)
            completed = service.advance(
                created.handoff_id, budget_seconds=2
            ).snapshot
        evidence = service.evidence(created.handoff_id)
    finally:
        handoff_store.close()
        provider_server.shutdown()
        provider_server.server_close()
        provider_thread.join(timeout=2)

    assert assessment.available is True
    assert assessment.mechanism == "local_cli"
    assert bound.binding == {"profile": "reviewer", "mechanism": "local_cli"}
    assert submitted.phase in {"submitted", "active", "succeeded"}
    assert completed.phase == "succeeded", completed
    assert "CLI receipt approved" in completed.terminal_result["text"]
    assert completed.checkpoint["receipt_version"] == 3
    assert completed.checkpoint["exit_code"] == 0
    assert {event.kind for event in evidence.events} >= {
        "created",
        "bound",
        "submit_attempted",
        "observed",
    }
    assert _OpenAIWireProvider.requests
    assert any(
        message.get("content") == spec.prompt
        for request in _OpenAIWireProvider.requests
        for message in request.get("messages", [])
        if isinstance(message, dict)
    )
    assert (target_home / "state.db").is_file()
    with sqlite3.connect(target_home / "state.db") as connection:
        titles = {
            str(row[0])
            for row in connection.execute(
                "SELECT title FROM sessions WHERE title IS NOT NULL"
            )
        }
    assert f"Handoff: {completed.handoff_id}" in titles
    assert BOT_CHAT_TITLE not in titles


@pytest.mark.macos_only
@pytest.mark.live_system_guard_bypass
def test_macos_cli_fallback_writes_a_real_dedicated_receipt(
    profile_homes, monkeypatch
) -> None:
    _assert_real_posix_cli_receipt(profile_homes, monkeypatch)


@pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
def test_linux_cli_fallback_writes_a_real_dedicated_receipt(
    profile_homes, monkeypatch
) -> None:
    _assert_real_posix_cli_receipt(profile_homes, monkeypatch)


@pytest.mark.windows_only
def test_windows_host_reports_cli_fallback_unavailable(
    profile_homes,
) -> None:
    default_home, target_home = profile_homes
    _write_cli_profile_config(default_home, target_home, provider_port=1)
    service = AgentHandoffService(HandoffStore(default_home / "handoffs.db"))
    try:
        assessment = service.validate_endpoint(
            HandoffEndpoint.parse("hermes://local/reviewer"),
            "workflow/windows",
        )
    finally:
        service.store.close()
    assert assessment.available is False
    assert assessment.mechanism is None
    assert assessment.failure_code == "local_cli_lock_unavailable"
