from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli.handoff import (
    AgentHandoffService,
    ChannelObservation,
    HandoffEndpoint,
    HandoffSpec,
    HandoffStore,
)
from hermes_cli.handoff.supervisor import AgentHandoffSupervisor
from tests.gateway import test_completion_delivery as gateway_delivery
import tui_gateway.server as tui_server


UTC = timezone.utc


def _finish_conversation(
    home,
    *,
    policy: str = "wake",
    hop_count: int = 0,
    host_kind: str = "gateway",
    session_key: str = "agent:default:telegram:dm:42",
):
    store = HandoffStore(home / "handoffs.db")
    spec = HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review the release.",
        output_schema=None,
        deadline_at=None,
        attribution={"profile": "default"},
        required_capabilities=frozenset(),
        return_route={
            "kind": "bot",
            "host_kind": host_kind,
            "profile": "default",
            "session_id": "bot-session-1",
            "session_key": session_key,
            "tool_call_id": "call-1",
            "delivery_policy": policy,
            "hop_count": hop_count,
        },
    )
    snapshot = store.create_or_get(
        "bot/default/bot-session-1", "call-1", spec, spec.fingerprint
    )
    snapshot = store.bind(
        snapshot.handoff_id,
        "local_bot_cli",
        {"profile": "reviewer", "mechanism": "local_bot_cli"},
        {},
        snapshot.state_version,
    )
    lease = store.claim_advance(
        snapshot.handoff_id,
        "destination",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    store.journal_attempt(lease, "submit")
    text = "review complete"
    store.commit_observation(
        lease,
        ChannelObservation(
            phase="succeeded",
            terminal_result={
                "text": text,
                "sha256": sha256(text.encode()).hexdigest(),
                "media_type": "text/plain",
                "size_bytes": len(text),
            },
        ),
    )
    return store, store.get(snapshot.handoff_id)


def _supervisor(home, store, events, *, owner: str):
    service = SimpleNamespace(store=store, advance=lambda *_args, **_kwargs: None)
    return AgentHandoffSupervisor(
        [("default", home)],
        owner=owner,
        completion_queue=events,
        service_factory=lambda _home: service,
    )


@pytest.fixture(autouse=True)
def _profile_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tui_server, "_hermes_home", home)
    yield home


@pytest.mark.asyncio
async def test_transcript_ack_crash_replays_without_a_second_bot_turn(
    _profile_home, monkeypatch
):
    home = _profile_home
    store, snapshot = _finish_conversation(home)
    events = queue.Queue()
    _supervisor(home, store, events, owner="gateway-before-crash").tick()
    event = events.get_nowait()
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="42",
        chat_type="dm",
        user_id="7",
    )
    first_adapter = SimpleNamespace(handle_message=AsyncMock())
    first_runner = gateway_delivery._runner(
        first_adapter,
        origins={event["session_key"]: SimpleNamespace(origin=source)},
    )
    first_runner._resolve_profile_home_for_source = lambda _source: home
    first_runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value="bot-session-1"),
    )
    first_runner._async_session_store = SimpleNamespace(
        _store=first_runner.session_store,
        has_platform_message_id=AsyncMock(side_effect=[False, True]),
    )
    real_complete = HandoffStore.complete_delivery
    monkeypatch.setattr(
        HandoffStore,
        "complete_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after transcript")
        ),
    )

    assert await first_runner._deliver_completion_notification(None, event) is False
    first_adapter.handle_message.assert_awaited_once()
    monkeypatch.setattr(HandoffStore, "complete_delivery", real_complete)
    with store._lock:
        store._conn.execute(
            "UPDATE handoff_deliveries SET lease_expires_at=? WHERE delivery_id=?",
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                event["delivery_id"],
            ),
        )
    store.close()

    restarted_store = HandoffStore(home / "handoffs.db")
    restarted_events = queue.Queue()
    _supervisor(
        home, restarted_store, restarted_events, owner="gateway-after-crash"
    ).tick()
    replay = restarted_events.get_nowait()
    second_adapter = SimpleNamespace(handle_message=AsyncMock())
    second_runner = gateway_delivery._runner(second_adapter)
    second_runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value="bot-session-1"),
    )
    second_runner._async_session_store = SimpleNamespace(
        _store=second_runner.session_store,
        has_platform_message_id=AsyncMock(return_value=True),
    )

    assert await second_runner._deliver_completion_notification(None, replay) is True
    second_adapter.handle_message.assert_not_awaited()
    assert restarted_store.get_delivery(replay["delivery_id"]).state == "delivered"

    listed = tui_server._methods["agent_handoff.list"]("r1", {"profile": "default"})
    row = listed["result"]["handoffs"][0]
    assert row["handoff_id"] == snapshot.handoff_id
    assert row["needs_attention"] is True
    acknowledged = tui_server._methods["agent_handoff.command"](
        "r2",
        {
            "profile": "default",
            "handoff_id": snapshot.handoff_id,
            "kind": "acknowledge",
            "command_id": "desktop-reconnect-ack",
        },
    )
    assert "error" not in acknowledged
    refreshed = tui_server._methods["agent_handoff.get"](
        "r3", {"profile": "default", "handoff_id": snapshot.handoff_id}
    )
    assert refreshed["result"]["needs_attention"] is False
    restarted_store.close()


def test_cancellation_race_emits_one_authoritative_return(_profile_home):
    home = _profile_home
    store = HandoffStore(home / "handoffs.db")
    spec = HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review the release.",
        output_schema=None,
        deadline_at=None,
        attribution={"profile": "default"},
        required_capabilities=frozenset(),
        return_route={
            "kind": "bot",
            "host_kind": "gateway",
            "profile": "default",
            "session_id": "bot-session-1",
            "tool_call_id": "call-race",
            "delivery_policy": "wake",
            "hop_count": 0,
        },
    )
    snapshot = store.create_or_get(
        "bot/default/bot-session-1", "call-race", spec, spec.fingerprint
    )
    snapshot = store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {"run_id": "run-race"},
        snapshot.state_version,
    )
    lease = store.claim_advance(
        snapshot.handoff_id, "observer", now=datetime.now(UTC), lease_seconds=30
    )
    assert lease is not None
    store.journal_attempt(lease, "submit")
    barrier = threading.Barrier(2)
    failures = []

    def cancel():
        try:
            barrier.wait()
            store.record_command(
                snapshot.handoff_id,
                "cancel-race",
                "cancel",
                {"actor": "bot"},
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    thread = threading.Thread(target=cancel)
    thread.start()
    barrier.wait()
    text = "authoritative completion"
    store.commit_observation(
        lease,
        ChannelObservation(
            phase="succeeded",
            terminal_result={
                "text": text,
                "sha256": sha256(text.encode()).hexdigest(),
                "media_type": "text/plain",
                "size_bytes": len(text),
            },
        ),
    )
    thread.join(timeout=3)

    assert failures == []
    assert not thread.is_alive()
    assert store.get(snapshot.handoff_id).phase == "succeeded"
    deliveries = store.attention(snapshot.handoff_id, limit=10)
    assert len(deliveries) == 1
    events = queue.Queue()
    supervisor = _supervisor(home, store, events, owner="race-host")
    supervisor.tick()
    supervisor.tick()
    assert events.qsize() == 1
    assert events.get_nowait()["delivery_id"] == deliveries[0].delivery_id
    store.close()


@pytest.mark.parametrize(
    ("policy", "hop_count", "failure_code"),
    [("attention", 0, None), ("wake", 1, "handoff_hop_limit")],
)
def test_disabled_or_hop_limited_wake_retains_attention(
    _profile_home, policy, hop_count, failure_code
):
    home = _profile_home
    store, snapshot = _finish_conversation(home, policy=policy, hop_count=hop_count)
    events = queue.Queue()
    _supervisor(home, store, events, owner="limited-host").tick()

    assert events.empty()
    assert store.has_attention(snapshot.handoff_id) is True
    delivery = store.attention(snapshot.handoff_id, limit=1)[0]
    assert delivery.failure_code == failure_code
    store.close()


def _tui_rpc_process(home, method: str, params: dict) -> dict:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["STAGE3_RPC"] = json.dumps({"id": "stage3", "method": method, "params": params})
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os; from pathlib import Path; "
                "import tui_gateway.server as s; "
                "s._hermes_home=Path(os.environ['HERMES_HOME']); "
                "s._real_stdout.write(json.dumps(s.handle_request("
                "json.loads(os.environ['STAGE3_RPC']))))"
            ),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return json.loads(completed.stdout)


def _tui_return_process(home: Path, action: str) -> dict:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            r'''
from pathlib import Path
from types import SimpleNamespace
import json
import os
import queue
import sys

from hermes_cli.handoff.supervisor import create_agent_handoff_supervisor
import hermes_cli.handoff.supervisor as supervisor_module
from hermes_cli.plugin_services import BackgroundServiceContext
from hermes_state import SessionDB
from tools.async_delegation import claim_event_delivery
from tools.process_registry import process_registry
import tui_gateway.server as server

action = sys.argv[1]
home = Path(os.environ["HERMES_HOME"])
if action == "disconnect":
    supervisor_module._DELIVERY_LEASE_SECONDS = 1.0
supervisor = create_agent_handoff_supervisor(
    BackgroundServiceContext(host_kind="web", host_instance_id=f"tui-{action}"),
    source_home=home,
)
supervisor.tick()
event = process_registry.completion_queue.get_nowait()
runtime = next(item for item in supervisor._profiles if item.profile == "default")
db = SessionDB(home / "state.db")
if action == "disconnect":
    claim = claim_event_delivery(event, "tui-poller")
    assert claim is not None
    db.create_session("bot-session-1", "cli")
    db.set_session_title("bot-session-1", "Bot Chat")
    db.append_message(
        "bot-session-1",
        "user",
        "durable handoff return",
        platform_message_id=event["delivery_id"],
        display_kind="handoff_return",
    )
    dispatched = None
else:
    session = {
        "agent": SimpleNamespace(_session_db=db, session_id="bot-session-1"),
        "session_key": "bot-session-1",
        "profile_home": str(home),
        "_finalized": False,
    }
    server._hermes_home = home
    assert server._session_owns_notification_event("reconnected", session, event)
    server._run_prompt_submit = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("persisted return must not run another model turn")
    )
    dispatched = server._dispatch_handoff_return("reconnected", session, event)
delivery = runtime.service.store.get_delivery(event["delivery_id"])
print(json.dumps({
    "delivery_id": event["delivery_id"],
    "dispatched": dispatched,
    "persisted": db.has_platform_message_id("bot-session-1", event["delivery_id"]),
    "state": delivery.state,
    "attempts": delivery.attempts,
}), file=server._real_stdout)
db.close()
for item in supervisor._profiles:
    item.service.store.close()
''',
            action,
        ],
        cwd=Path(__file__).parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_tui_desktop_process_restart_reopens_durable_attention(_profile_home):
    home = _profile_home
    store, snapshot = _finish_conversation(home)
    store.close()

    listed = _tui_rpc_process(
        home, "agent_handoff.list", {"profile": "default", "limit": 10}
    )
    reopened = _tui_rpc_process(
        home,
        "agent_handoff.evidence",
        {"profile": "default", "handoff_id": snapshot.handoff_id, "limit": 10},
    )

    assert listed["result"]["handoffs"][0]["handoff_id"] == snapshot.handoff_id
    assert listed["result"]["handoffs"][0]["needs_attention"] is True
    assert reopened["result"]["result_preview"] == {
        "text": "review complete",
        "truncated": False,
    }


def test_tui_process_disconnect_reconnect_acks_persisted_return_without_model_turn(
    _profile_home,
):
    home = _profile_home
    store, snapshot = _finish_conversation(
        home, host_kind="web", session_key="bot-session-1"
    )
    delivery_id = store.attention(snapshot.handoff_id, limit=1)[0].delivery_id
    store.close()

    disconnected = _tui_return_process(home, "disconnect")
    time.sleep(1.1)
    reconnected = _tui_return_process(home, "reconnect")

    assert disconnected == {
        "delivery_id": delivery_id,
        "dispatched": None,
        "persisted": True,
        "state": "pending",
        "attempts": 1,
    }
    assert reconnected == {
        "delivery_id": delivery_id,
        "dispatched": True,
        "persisted": True,
        "state": "delivered",
        "attempts": 2,
    }
