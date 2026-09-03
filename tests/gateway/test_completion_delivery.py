"""Lifecycle-scoped gateway delivery regressions for terminal completions.

The gateway contract here is deliberately narrower than exactly-once: one live
GatewayRunner suppresses concurrent/replayed copies after successful adapter
injection, failed injection remains retryable, and durable async-delegation
state (when available) is acknowledged through its authoritative SQLite API.
"""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import queue
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    PlatformConfig,
    SendResult,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli.handoff.models import ChannelObservation, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.store import HandoffStore
from tools.process_registry import ProcessRegistry, ProcessSession


UTC = timezone.utc


class _BusyAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="reply-1")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "dm"}


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Any current/future durable compatibility path must stay in tmp state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tools.process_registry as pr_module

    monkeypatch.setattr(pr_module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = pr_module.ProcessRegistry()
    monkeypatch.setattr(pr_module, "process_registry", registry)
    return registry


def _runner(adapter, *, origins=None):
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.session_store = SimpleNamespace(
        _ensure_loaded=lambda: None,
        _entries=origins or {},
    )
    runner._session_source_cache = {}
    runner._completion_delivery_lock = __import__("threading").Lock()
    runner._completion_deliveries_inflight = set()
    runner._completion_deliveries_delivered = OrderedDict()
    runner._completion_delivery_retention = 2048
    runner._background_tasks = set()
    return runner


def _async_event(delegation_id="deleg_duplicate"):
    return {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": "agent:main:telegram:dm:12345:678",
        "goal": "Investigate flaky test",
        "status": "completed",
        "summary": "Found it",
        "api_calls": 1,
        "duration_seconds": 12.0,
        "dispatched_at": 1000.0,
        "completed_at": 1012.0,
        # PR #62479 stamps these on gateway-owned events. They must not
        # change the producer identity used for queue replay.
        "origin_profile": "default",
        "origin_hermes_home": "/tmp/hermes-default",
    }


def _completion_event(*, started_at, session_id="proc_reused"):
    return {
        "type": "completion",
        "session_id": session_id,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "started_at": started_at,
        "command": "echo done",
        "exit_code": 0,
        "completion_reason": "exited",
        "output": "done\n",
    }


def _handoff_event(
    home,
    *,
    text="done",
    phase="succeeded",
    host_kind="gateway",
    return_advance=False,
    session_key="agent:main:telegram:dm:12345:678",
):
    store = HandoffStore(home / "handoffs.db")
    spec = HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review it.",
        output_schema=None,
        deadline_at=None,
        attribution={"profile": "default"},
        required_capabilities=frozenset(),
        return_route={
            "kind": "bot",
            "host_kind": host_kind,
            "profile": "default",
            "session_id": "session-1",
            "session_key": session_key,
            "tool_call_id": "call-1",
            "delivery_policy": "wake",
            "hop_count": 0,
        },
    )
    snapshot = store.create_or_get(
        "bot/default/session-1", "call-1", spec, spec.fingerprint
    )
    snapshot = store.bind(
        snapshot.handoff_id,
        "local_bot_cli",
        {"profile": "reviewer", "mechanism": "local_bot_cli"},
        {},
        snapshot.state_version,
    )
    advance = store.claim_advance(
        snapshot.handoff_id,
        "test",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert advance is not None
    store.journal_attempt(advance, "submit")
    result = None
    if phase == "succeeded":
        encoded = text.encode()
        result = {
            "text": text,
            "sha256": sha256(encoded).hexdigest(),
            "media_type": "text/plain",
            "size_bytes": len(encoded),
        }
    store.commit_observation(
        advance,
        ChannelObservation(
            phase=phase,
            terminal_result=result,
            failure_code="remote_failed" if phase in {"failed", "indeterminate"} else None,
            checkpoint={
                "approval_request_id": "approval-1",
                "approval_choices": ["once", "deny"],
            }
            if phase == "needs_input"
            else {},
        ),
    )
    delivery = store.attention(snapshot.handoff_id, limit=1)[0]
    lease = store.claim_delivery(
        delivery.delivery_id,
        "supervisor-1",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    event = {
        "type": "handoff_return",
        "host_kind": host_kind,
        "delivery_id": delivery.delivery_id,
        "handoff_id": snapshot.handoff_id,
        "event_sequence": delivery.event_sequence,
        "profile": "default",
        "session_id": "session-1",
        "session_key": session_key,
        "tool_call_id": "call-1",
        "hop_count": 0,
        "delivery_claim": {
            "owner": lease.owner,
            "epoch": lease.epoch,
            "expires_at": lease.expires_at.isoformat(),
        },
    }
    return (store, event, advance) if return_advance else (store, event)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("succeeded", "completed successfully"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
        ("needs_input", "needs your input"),
        ("indeterminate", "could not be determined"),
    ],
)
def test_handoff_return_claim_authorizes_bounded_redacted_format(
    tmp_path, phase, expected
):
    from tools.async_delegation import claim_event_delivery, release_event_delivery
    from tools.process_registry import format_process_notification

    secret = "MY_SERVICE_TOKEN=abc123randomopaquetokenvalue999"
    store, event = _handoff_event(tmp_path, text=secret + ("x" * 40000), phase=phase)

    claim = claim_event_delivery(event, "gateway")
    assert claim
    rendered = format_process_notification(event, delivery_claim=claim)

    assert expected in rendered
    assert "abc123randomopaquetokenvalue999" not in rendered
    assert len(rendered.encode()) <= 32768
    release_event_delivery(event, claim)
    assert store.get_delivery(event["delivery_id"]).state == "pending"
    store.close()


def test_handoff_return_rejects_a_forged_delivery_claim(tmp_path):
    from tools.async_delegation import claim_event_delivery

    store, event = _handoff_event(tmp_path)
    event["delivery_claim"]["epoch"] += 1

    assert claim_event_delivery(event, "gateway") is None
    store.close()


def test_handoff_return_duplicate_queue_event_has_one_live_consumer(tmp_path):
    from tools.async_delegation import claim_event_delivery, release_event_delivery

    store, event = _handoff_event(tmp_path)
    claim = claim_event_delivery(event, "gateway-1")

    assert claim
    assert claim_event_delivery(dict(event), "gateway-2") is None
    release_event_delivery(event, claim)
    store.close()


def test_handoff_return_can_only_be_claimed_by_its_surface_host(tmp_path):
    from tools.async_delegation import claim_event_delivery, release_event_delivery

    store, event = _handoff_event(tmp_path, host_kind="web")

    assert claim_event_delivery(event, "gateway") is None
    claim = claim_event_delivery(event, "tui-poller")
    assert claim is not None
    release_event_delivery(event, claim)
    store.close()


def test_gateway_handoff_return_uses_delivery_identity_and_private_context(tmp_path):
    store, event = _handoff_event(tmp_path)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path

    assert asyncio.run(runner._inject_watch_notification("finished", event)) is True

    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.message_id == event["delivery_id"]
    assert delivered.metadata["gateway_session_id"] == "session-1"
    assert delivered.metadata["persist_user_display_kind"] == "handoff_return"
    assert delivered.metadata["handoff_return_hop_count"] == 1
    store.close()


def test_gateway_handoff_return_acknowledges_only_after_turn_acceptance(tmp_path):
    store, event = _handoff_event(tmp_path)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(side_effect=[False, True]),
    )

    assert asyncio.run(
        runner._deliver_completion_notification("finished", event)
    ) is True
    assert store.get_delivery(event["delivery_id"]).state == "delivered"
    store.close()


def test_gateway_handoff_return_replay_acks_without_a_second_model_turn(tmp_path):
    store, event = _handoff_event(tmp_path)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value="session-1"),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(return_value=True),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is True
    adapter.handle_message.assert_not_awaited()
    assert store.get_delivery(event["delivery_id"]).state == "delivered"
    store.close()


def test_gateway_handoff_return_releases_when_acceptance_is_not_persisted(tmp_path):
    store, event = _handoff_event(tmp_path)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(return_value=False),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is False
    adapter.handle_message.assert_awaited_once()
    assert store.get_delivery(event["delivery_id"]).state == "pending"
    store.close()


@pytest.mark.parametrize(
    "post_acceptance_receipt",
    [False, RuntimeError("receipt unavailable")],
    ids=["not-yet-persisted", "receipt-read-failed"],
)
def test_gateway_handoff_return_survives_supervisor_owner_rotation_before_receipt(
    tmp_path, post_acceptance_receipt,
):
    store, event = _handoff_event(tmp_path)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(
            side_effect=[False, post_acceptance_receipt, False, True]
        ),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is False
    pending = store.get_delivery(event["delivery_id"])
    replay_lease = store.claim_delivery(
        pending.delivery_id,
        "supervisor-2",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert replay_lease is not None
    replay = {
        **event,
        "delivery_claim": {
            "owner": replay_lease.owner,
            "epoch": replay_lease.epoch,
            "expires_at": replay_lease.expires_at.isoformat(),
        },
    }

    assert asyncio.run(
        runner._deliver_completion_notification(None, replay)
    ) is None
    adapter.handle_message.assert_awaited_once()
    pending = store.get_delivery(event["delivery_id"])
    final_lease = store.claim_delivery(
        pending.delivery_id,
        "supervisor-3",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert final_lease is not None
    replay["delivery_claim"] = {
        "owner": final_lease.owner,
        "epoch": final_lease.epoch,
        "expires_at": final_lease.expires_at.isoformat(),
    }

    assert asyncio.run(
        runner._deliver_completion_notification(None, replay)
    ) is True
    adapter.handle_message.assert_awaited_once()
    assert store.get_delivery(event["delivery_id"]).state == "delivered"
    assert (
        runner._completion_delivery_identity(event)
        not in runner._completion_deliveries_inflight
    )
    store.close()


def test_gateway_handoff_return_process_restart_retries_after_receipt_recovery(
    tmp_path,
):
    store, event = _handoff_event(tmp_path)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    first_adapter = SimpleNamespace(handle_message=AsyncMock())
    first_runner = _runner(first_adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    first_runner._resolve_profile_home_for_source = lambda _source: tmp_path
    first_runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
    )
    first_runner._async_session_store = SimpleNamespace(
        _store=first_runner.session_store,
        has_platform_message_id=AsyncMock(return_value=False),
    )

    assert asyncio.run(
        first_runner._deliver_completion_notification(None, event)
    ) is False
    first_adapter.handle_message.assert_awaited_once()

    pending = store.get_delivery(event["delivery_id"])
    recovery_lease = store.claim_delivery(
        pending.delivery_id,
        "gateway-after-restart",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert recovery_lease is not None
    replay = {
        **event,
        "delivery_claim": {
            "owner": recovery_lease.owner,
            "epoch": recovery_lease.epoch,
            "expires_at": recovery_lease.expires_at.isoformat(),
        },
    }

    restarted_adapter = SimpleNamespace(handle_message=AsyncMock())
    restarted_runner = _runner(restarted_adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    restarted_runner._resolve_profile_home_for_source = lambda _source: tmp_path
    restarted_runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
    )
    restarted_runner._async_session_store = SimpleNamespace(
        _store=restarted_runner.session_store,
        has_platform_message_id=AsyncMock(side_effect=[False, False, True]),
    )

    assert asyncio.run(
        restarted_runner._deliver_completion_notification(None, replay)
    ) is False
    restarted_adapter.handle_message.assert_not_awaited()
    identity = restarted_runner._completion_delivery_identity(event)
    assert identity not in restarted_runner._completion_deliveries_inflight

    pending = store.get_delivery(event["delivery_id"])
    retry_lease = store.claim_delivery(
        pending.delivery_id,
        "gateway-retry",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert retry_lease is not None
    replay["delivery_claim"] = {
        "owner": retry_lease.owner,
        "epoch": retry_lease.epoch,
        "expires_at": retry_lease.expires_at.isoformat(),
    }

    assert asyncio.run(
        restarted_runner._deliver_completion_notification(None, replay)
    ) is True
    restarted_adapter.handle_message.assert_awaited_once()
    assert store.get_delivery(event["delivery_id"]).state == "delivered"
    store.close()


def test_gateway_handoff_return_retries_after_busy_fifo_capacity(tmp_path):
    store, event, advance = _handoff_event(
        tmp_path,
        phase="needs_input",
        return_advance=True,
        session_key="agent:main:telegram:dm:12345",
    )
    adapter = _BusyAdapter()
    handler = AsyncMock(return_value=None)
    adapter.set_message_handler(handler)
    runner = _runner(adapter)
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._is_user_authorized = lambda _source: True
    runner._effective_busy_input_mode = lambda _source: "queue"
    runner._draining = False
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value=None),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(
            side_effect=[False, False, False, True]
        ),
    )
    adapter.set_busy_session_handler(runner._handle_active_session_busy_message)
    session_key = event["session_key"]
    adapter._active_sessions[session_key] = asyncio.Event()
    adapter._heal_stale_session_lock = lambda _session_key: None
    filler = MessageEvent(
        text="queued",
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="678",
        ),
    )
    adapter._pending_messages[session_key] = filler
    runner._session_state(session_key).conversation.queued_events.extend(
        [filler] * (runner._BUSY_QUEUE_MAX_PENDING - 1)
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is False
    rejected = store.get_delivery(event["delivery_id"])
    assert rejected.attempts == 0
    assert rejected.failure_code == "delivery_backpressure"
    assert rejected.acknowledged_at is None
    handler.assert_not_awaited()

    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded",
            terminal_result={
                "text": "terminal result",
                "sha256": sha256(b"terminal result").hexdigest(),
                "media_type": "text/plain",
                "size_bytes": len(b"terminal result"),
            },
        ),
    )
    assert store.get_delivery(event["delivery_id"]).acknowledged_at is not None

    adapter._pending_messages.clear()
    runner._session_state(session_key).conversation.queued_events.clear()
    terminal = store.attention(event["handoff_id"], limit=1)[0]
    terminal_lease = store.claim_delivery(
        terminal.delivery_id,
        "supervisor-terminal",
        now=terminal.next_attempt_at,
        lease_seconds=30,
    )
    assert terminal_lease is not None
    terminal_event = {
        **event,
        "delivery_id": terminal.delivery_id,
        "event_sequence": terminal.event_sequence,
        "delivery_claim": {
            "owner": terminal_lease.owner,
            "epoch": terminal_lease.epoch,
            "expires_at": terminal_lease.expires_at.isoformat(),
        },
    }

    assert asyncio.run(
        runner._deliver_completion_notification(None, terminal_event)
    ) is False
    queued = adapter.get_pending_message(session_key)
    assert queued is not None
    assert queued.message_id == terminal.delivery_id
    asyncio.run(handler(queued))
    handler.assert_awaited_once()

    pending = store.get_delivery(terminal.delivery_id)
    receipt_lease = store.claim_delivery(
        pending.delivery_id,
        "supervisor-receipt",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert receipt_lease is not None
    terminal_event["delivery_claim"] = {
        "owner": receipt_lease.owner,
        "epoch": receipt_lease.epoch,
        "expires_at": receipt_lease.expires_at.isoformat(),
    }
    assert asyncio.run(
        runner._deliver_completion_notification(None, terminal_event)
    ) is True
    handler.assert_awaited_once()
    assert store.get_delivery(terminal.delivery_id).state == "delivered"
    store.close()


def test_gateway_handoff_return_waits_for_queued_turn_receipt(tmp_path):
    store, event, advance = _handoff_event(
        tmp_path, phase="needs_input", return_advance=True
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value=None),
    )
    receipt = AsyncMock(return_value=False)
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=receipt,
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is False
    submitted = adapter.handle_message.await_args.args[0]
    assert submitted.allow_gateway_control is False
    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded",
            terminal_result={
                "text": "terminal result",
                "sha256": sha256(b"terminal result").hexdigest(),
                "media_type": "text/plain",
                "size_bytes": len(b"terminal result"),
            },
        ),
    )

    pending = store.get_delivery(event["delivery_id"])
    assert pending.acknowledged_at is None
    for _ in range(10):
        lease = store.claim_delivery(
            pending.delivery_id,
            event["delivery_claim"]["owner"],
            now=pending.next_attempt_at,
            lease_seconds=30,
        )
        assert lease is not None
        replay = {
            **event,
            "delivery_claim": {
                "owner": lease.owner,
                "epoch": lease.epoch,
                "expires_at": lease.expires_at.isoformat(),
            },
        }
        assert asyncio.run(
            runner._deliver_completion_notification(None, replay)
        ) is None
        pending = store.get_delivery(event["delivery_id"])
        assert pending.state == "pending"
        assert pending.attempts == 1

    receipt.return_value = True
    receipt_lease = store.claim_delivery(
        pending.delivery_id,
        event["delivery_claim"]["owner"],
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert receipt_lease is not None
    replay["delivery_claim"] = {
        "owner": receipt_lease.owner,
        "epoch": receipt_lease.epoch,
        "expires_at": receipt_lease.expires_at.isoformat(),
    }
    assert asyncio.run(
        runner._deliver_completion_notification(None, replay)
    ) is True
    adapter.handle_message.assert_awaited_once()
    old = store.get_delivery(event["delivery_id"])
    assert old.state == "delivered"
    assert old.acknowledged_at is not None
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)
    assert len(due) == 1
    assert due[0].event_sequence > old.event_sequence
    store.close()


def test_gateway_handoff_return_revalidates_claim_after_target_lookup(tmp_path):
    store, event, advance = _handoff_event(
        tmp_path, phase="needs_input", return_advance=True
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path

    async def finish_handoff(_session_id):
        store.commit_observation(advance, ChannelObservation(phase="active"))
        store.commit_observation(
            advance,
            ChannelObservation(
                phase="succeeded",
                terminal_result={
                    "text": "terminal result",
                    "sha256": sha256(b"terminal result").hexdigest(),
                    "media_type": "text/plain",
                    "size_bytes": len(b"terminal result"),
                },
            ),
        )
        return {"ended_at": None}

    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(side_effect=finish_handoff),
        get_compression_tip=AsyncMock(return_value=None),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(return_value=False),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is None
    adapter.handle_message.assert_not_awaited()
    assert store.get_delivery(event["delivery_id"]).acknowledged_at is not None

    terminal = store.attention(event["handoff_id"], limit=1)[0]
    terminal_lease = store.claim_delivery(
        terminal.delivery_id,
        "supervisor-2",
        now=terminal.next_attempt_at,
        lease_seconds=30,
    )
    assert terminal_lease is not None
    terminal_event = {
        **event,
        "delivery_id": terminal.delivery_id,
        "event_sequence": terminal.event_sequence,
        "delivery_claim": {
            "owner": terminal_lease.owner,
            "epoch": terminal_lease.epoch,
            "expires_at": terminal_lease.expires_at.isoformat(),
        },
    }
    runner._session_db.get_session = AsyncMock(return_value={"ended_at": None})
    runner._async_session_store.has_platform_message_id = AsyncMock(
        side_effect=[False, True]
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, terminal_event)
    ) is True
    adapter.handle_message.assert_awaited_once()
    store.close()


def test_gateway_handoff_return_reserves_dispatch_before_adapter_acceptance(tmp_path):
    store, event, advance = _handoff_event(
        tmp_path, phase="needs_input", return_advance=True
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="678",
    )

    async def finish_handoff(_event):
        store.commit_observation(advance, ChannelObservation(phase="active"))
        store.commit_observation(
            advance,
            ChannelObservation(
                phase="succeeded",
                terminal_result={
                    "text": "terminal result",
                    "sha256": sha256(b"terminal result").hexdigest(),
                    "media_type": "text/plain",
                    "size_bytes": len(b"terminal result"),
                },
            ),
        )

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=finish_handoff))
    runner = _runner(adapter, origins={
        event["session_key"]: SimpleNamespace(origin=source),
    })
    runner._resolve_profile_home_for_source = lambda _source: tmp_path
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={"ended_at": None}),
        get_compression_tip=AsyncMock(return_value=None),
    )
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=AsyncMock(side_effect=[False, True]),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is True
    adapter.handle_message.assert_awaited_once()
    first = store.get_delivery(event["delivery_id"])
    assert first.state == "delivered"
    assert first.acknowledged_at is not None
    assert len(store.due_deliveries(now=datetime.now(UTC), limit=10)) == 1
    store.close()


def test_gateway_handoff_return_user_boundary_fails_closed_with_attention(tmp_path):
    store, event = _handoff_event(tmp_path)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(return_value={
            "ended_at": "2026-09-02T12:00:00Z",
            "end_reason": "session_reset",
        }),
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is None
    adapter.handle_message.assert_not_awaited()
    assert store.get_delivery(event["delivery_id"]).state == "pending"
    assert store.has_attention(event["handoff_id"]) is True
    store.close()


def test_gateway_handoff_return_replay_resolves_compression_tip(tmp_path):
    store, event = _handoff_event(tmp_path)
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    runner._session_db = SimpleNamespace(
        get_session=AsyncMock(side_effect=[
            {"ended_at": "2026-09-02T12:00:00Z", "end_reason": "compression"},
            {"ended_at": None},
        ]),
        get_compression_tip=AsyncMock(return_value="session-tip"),
    )
    seen = AsyncMock(return_value=True)
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        has_platform_message_id=seen,
    )

    assert asyncio.run(
        runner._deliver_completion_notification(None, event)
    ) is True
    seen.assert_awaited_once_with("session-tip", event["delivery_id"])
    adapter.handle_message.assert_not_awaited()
    store.close()


def _stop_after_sleeps(monkeypatch, runner, count):
    sleep_calls = 0

    async def _bounded_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= count:
            runner._running = False

    monkeypatch.setattr(asyncio, "sleep", _bounded_sleep)


def test_duplicate_async_queue_replay_injects_once(monkeypatch, isolated_registry):
    """Byte-identical queue replays produce one turn in one gateway lifecycle."""
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(dict(_async_event()))
    isolated.put(dict(_async_event()))

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_awaited_once()


def test_unroutable_async_event_is_not_requeued_forever(
    monkeypatch, isolated_registry,
):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    event = _async_event("deleg_desktop_or_cli")
    event["session_key"] = "20260711_unparseable_ui_session"
    isolated.put(event)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_not_awaited()
    assert isolated.empty()


def test_concurrent_claims_share_the_same_narrow_delivery_seam():
    """Concurrent consumers in one runner cannot both enter the adapter."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_injection(_event):
        entered.set()
        await release.wait()

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_blocked_injection))
    runner = _runner(adapter)
    event = _async_event()
    text = "completion"

    async def _exercise():
        first = asyncio.create_task(runner._deliver_completion_notification(text, dict(event)))
        await entered.wait()
        second = asyncio.create_task(runner._deliver_completion_notification(text, dict(event)))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)

    assert sorted(asyncio.run(_exercise()), key=str) == [None, True]
    adapter.handle_message.assert_awaited_once()


def test_failed_async_injection_is_retried_and_only_success_is_acked(
    monkeypatch, isolated_registry,
):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(_async_event())

    adapter = SimpleNamespace(
        handle_message=AsyncMock(side_effect=[RuntimeError("temporary"), None])
    )
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=3)

    from tools import async_delegation

    acknowledgements = []
    monkeypatch.setattr(
        async_delegation,
        "complete_completion_delivery",
        lambda delegation_id, _claim_id: acknowledgements.append(delegation_id) or True,
        raising=False,
    )

    asyncio.run(runner._async_delegation_watcher(interval=0))

    assert adapter.handle_message.await_count == 2
    assert acknowledgements == ["deleg_duplicate"]


def _persist_pending_completion(event):
    from tools import async_delegation

    async_delegation._persist_dispatch({
        "delegation_id": event["delegation_id"],
        "session_key": event["session_key"],
        "origin_ui_session_id": "",
        "parent_session_id": event.get("parent_session_id"),
        "dispatched_at": event["dispatched_at"],
    })
    async_delegation._persist_completion(event, {
        "status": "completed",
        "summary": event["summary"],
    })


def test_explicit_kill_returns_output_before_consuming_notification(monkeypatch):
    import tools.process_registry as pr_module

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_kill_consumed",
        command="sleep 999",
        task_id="task",
        started_at=1.0,
        output_buffer="important terminal output\n",
        notify_on_complete=True,
    )
    session.process = MagicMock()
    session.process.pid = 4242
    registry._running[session.id] = session
    monkeypatch.setattr(registry, "_terminate_host_pid", lambda *_a, **_kw: None)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)
    monkeypatch.setattr(pr_module, "process_registry", registry)

    result = registry.kill_process(session.id)
    assert result["status"] == "killed"
    assert result["output"] == "important terminal output\n"
    assert registry.is_completion_consumed(session.id)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _instant_sleep(*_a, **_kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    asyncio.run(runner._run_process_watcher({
        "session_id": session.id,
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "notify_on_complete": True,
    }))

    adapter.handle_message.assert_not_awaited()


def test_process_tool_redacts_explicit_kill_output(monkeypatch):
    from tools import process_registry as pr_module

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_kill_redacted",
        command="printenv",
        task_id="task",
        started_at=1.0,
        output_buffer="PRIVATE_TOKEN=opaque-value\n",
        exited=True,
        exit_code=0,
    )
    registry._finished[session.id] = session
    monkeypatch.setattr(pr_module, "process_registry", registry)

    def _redact(result):
        assert result["output"] == "PRIVATE_TOKEN=opaque-value\n"
        result["output"] = "PRIVATE_TOKEN=<redacted>\n"
        return result

    monkeypatch.setattr(pr_module, "_redact_process_result", _redact)

    result = json.loads(pr_module._handle_process({
        "action": "kill",
        "session_id": session.id,
    }))
    assert result["output"] == "PRIVATE_TOKEN=<redacted>\n"


def test_autonomous_completion_redacts_real_command_and_output_secrets(monkeypatch):
    import agent.redact as redact_module
    import tools.process_registry as pr_module

    secret = "abc123randomopaquetokenvalue999"
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_autonomous_redaction",
        command=f"printenv MY_SERVICE_TOKEN={secret}",
        task_id="task",
        started_at=1234.5,
        output_buffer=f"MY_SERVICE_TOKEN={secret}\nHOME=/home/user\n",
        exited=True,
        exit_code=0,
        notify_on_complete=True,
    )
    registry._finished[session.id] = session
    monkeypatch.setattr(pr_module, "process_registry", registry)
    monkeypatch.setattr(redact_module, "_REDACT_ENABLED", True)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _instant_sleep(*_a, **_kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    asyncio.run(runner._run_process_watcher({
        "session_id": session.id,
        "check_interval": 0,
        "session_key": "agent:main:telegram:dm:123",
        "platform": "telegram",
        "chat_type": "dm",
        "chat_id": "123",
        "notify_on_complete": True,
    }))

    delivered = adapter.handle_message.await_args.args[0]
    assert secret not in delivered.text
    assert "HOME=/home/user" in delivered.text


def test_concurrent_process_watchers_coalesce_one_session_completion_turn(monkeypatch):
    """Concurrent terminal watchers for one session must re-enter the agent once."""
    import tools.process_registry as pr_module

    registry = ProcessRegistry()
    watchers = []
    for index in range(3):
        session = ProcessSession(
            id=f"proc_batch_{index}",
            command=f"printf batch-{index}",
            task_id=f"task-{index}",
            started_at=1000.0 + index,
            output_buffer=f"batch-{index}\n",
            exited=True,
            exit_code=0,
            notify_on_complete=True,
        )
        registry._finished[session.id] = session
        watchers.append({
            "session_id": session.id,
            "check_interval": 0,
            "session_key": "agent:main:telegram:dm:123",
            "platform": "telegram",
            "chat_type": "dm",
            "chat_id": "123",
            "notify_on_complete": True,
        })
    monkeypatch.setattr(pr_module, "process_registry", registry)

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _exercise():
        await asyncio.gather(*(
            runner._run_process_watcher(watcher)
            for watcher in watchers
        ))

    asyncio.run(_exercise())

    adapter.handle_message.assert_awaited_once()
    delivered = adapter.handle_message.await_args.args[0]
    assert "3 background processes completed" in delivered.text
    for index in range(3):
        assert f"proc_batch_{index}" in delivered.text


def test_completion_arriving_during_batch_delivery_schedules_next_flush():
    """A new event cannot be stranded behind an in-flight batch for its route."""
    first_delivery_entered = asyncio.Event()
    release_first_delivery = asyncio.Event()
    delivery_count = 0

    async def _deliver(_event):
        nonlocal delivery_count
        delivery_count += 1
        if delivery_count == 1:
            first_delivery_entered.set()
            await release_first_delivery.wait()

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_deliver))
    runner = _runner(adapter)

    async def _exercise():
        first = asyncio.create_task(runner._enqueue_process_completion_notification(
            "first completion",
            _completion_event(started_at=1.0, session_id="proc_first"),
        ))
        await first_delivery_entered.wait()
        second = asyncio.create_task(runner._enqueue_process_completion_notification(
            "second completion",
            _completion_event(started_at=2.0, session_id="proc_second"),
        ))
        release_first_delivery.set()
        assert await first is True
        assert await asyncio.wait_for(second, timeout=1.0) is True

    asyncio.run(_exercise())

    assert adapter.handle_message.await_count == 2


def test_completion_batches_do_not_cross_conversation_routes():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    first = _completion_event(started_at=1.0, session_id="proc_route_a")
    second = _completion_event(started_at=2.0, session_id="proc_route_b")
    second["session_key"] = "agent:main:telegram:dm:456"
    second["chat_id"] = "456"

    async def _exercise():
        return await asyncio.gather(
            runner._enqueue_process_completion_notification("first", first),
            runner._enqueue_process_completion_notification("second", second),
        )

    assert asyncio.run(_exercise()) == [True, True]
    assert adapter.handle_message.await_count == 2


def test_failed_coalesced_delivery_retries_all_entries():
    attempts = 0

    async def _deliver(_event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary adapter failure")

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_deliver))
    runner = _runner(adapter)
    events = [
        _completion_event(started_at=float(index), session_id=f"proc_retry_{index}")
        for index in range(2)
    ]

    async def _enqueue_all():
        return await asyncio.gather(*(
            runner._enqueue_process_completion_notification(f"event-{index}", event)
            for index, event in enumerate(events)
        ))

    async def _exercise():
        assert await _enqueue_all() == [False, False]
        assert await _enqueue_all() == [True, True]

    asyncio.run(_exercise())
    assert adapter.handle_message.await_count == 2


def test_coalesced_success_records_every_completion_identity():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    events = [
        _completion_event(started_at=float(index), session_id=f"proc_ledger_{index}")
        for index in range(3)
    ]

    async def _exercise():
        return await asyncio.gather(*(
            runner._enqueue_process_completion_notification(f"event-{index}", event)
            for index, event in enumerate(events)
        ))

    assert asyncio.run(_exercise()) == [True, True, True]
    for event in events:
        identity = runner._completion_delivery_identity(event)
        assert identity in runner._completion_deliveries_delivered


def test_coalesced_format_bounds_details_and_reports_omitted_count():
    async def _format():
        loop = asyncio.get_running_loop()
        entries = [
            (
                f"event-{index}",
                _completion_event(
                    started_at=float(index), session_id=f"proc_bound_{index}"
                ),
                loop.create_future(),
            )
            for index in range(12)
        ]
        return GatewayRunner._format_coalesced_process_completions(entries)

    text = asyncio.run(_format())

    for index in range(10):
        assert f"proc_bound_{index}" in text
    assert "proc_bound_10" not in text
    assert "proc_bound_11" not in text
    assert "and 2 more completion(s)" in text


def test_coalesced_format_force_redacts_output_when_redaction_disabled(monkeypatch):
    """A user setting cannot disable the gateway's outbound secret floor."""
    import agent.redact as redact_module

    secret = "abc123randomopaquetokenvalue999"
    monkeypatch.setattr(redact_module, "_REDACT_ENABLED", False)

    async def _format():
        loop = asyncio.get_running_loop()
        first = _completion_event(started_at=1.0, session_id="proc_secret")
        first["output"] = (
            f"MY_SERVICE_TOKEN={secret}\n"
            "HOME=/home/user\n"
        )
        second = _completion_event(started_at=2.0, session_id="proc_control")
        return GatewayRunner._format_coalesced_process_completions([
            ("first", first, loop.create_future()),
            ("second", second, loop.create_future()),
        ])

    text = asyncio.run(_format())

    assert secret not in text
    assert "HOME=/home/user" in text


def test_coalesced_format_redacts_before_truncating_output(monkeypatch):
    """Truncation cannot remove the prefix needed to recognize a secret."""
    import agent.redact as redact_module

    marker = "SHOULD_NOT_SURVIVE"
    monkeypatch.setattr(redact_module, "_REDACT_ENABLED", False)

    async def _format():
        loop = asyncio.get_running_loop()
        first = _completion_event(started_at=1.0, session_id="proc_long_secret")
        first["output"] = f"MY_SERVICE_TOKEN={'x' * 900}{marker}\n"
        second = _completion_event(started_at=2.0, session_id="proc_control")
        return GatewayRunner._format_coalesced_process_completions([
            ("first", first, loop.create_future()),
            ("second", second, loop.create_future()),
        ])

    text = asyncio.run(_format())

    assert marker not in text


def test_duplicate_primary_does_not_discard_fresh_batch_sibling():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    duplicate = _completion_event(started_at=1.0, session_id="proc_duplicate")
    fresh = _completion_event(started_at=2.0, session_id="proc_fresh")
    duplicate_identity = runner._completion_delivery_identity(duplicate)
    runner._completion_deliveries_delivered[duplicate_identity] = None

    async def _exercise():
        return await asyncio.gather(
            runner._enqueue_process_completion_notification("duplicate", duplicate),
            runner._enqueue_process_completion_notification("fresh", fresh),
        )

    assert asyncio.run(_exercise()) == [True, True]
    adapter.handle_message.assert_awaited_once()
    fresh_identity = runner._completion_delivery_identity(fresh)
    assert fresh_identity in runner._completion_deliveries_delivered


def test_batch_format_failure_resolves_waiters_for_retry(monkeypatch):
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    monkeypatch.setattr(
        runner,
        "_format_coalesced_process_completions",
        MagicMock(side_effect=ValueError("bad batch")),
    )
    events = [
        _completion_event(started_at=float(index), session_id=f"proc_format_{index}")
        for index in range(2)
    ]

    async def _exercise():
        pending = asyncio.gather(*(
            runner._enqueue_process_completion_notification(f"event-{index}", event)
            for index, event in enumerate(events)
        ))
        return await asyncio.wait_for(pending, timeout=1.0)

    assert asyncio.run(_exercise()) == [False, False]
    adapter.handle_message.assert_not_awaited()


def test_shutdown_cancels_batch_during_window_and_settles_waiter_for_retry():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    sleep_entered = asyncio.Event()
    release_sleep = asyncio.Event()
    real_sleep = asyncio.sleep
    event = _completion_event(started_at=1.0, session_id="proc_cancel_window")

    async def _controlled_sleep(delay):
        if delay == runner._completion_notification_batch_window:
            sleep_entered.set()
            await release_sleep.wait()
            return
        await real_sleep(delay)

    async def _exercise():
        pending = asyncio.create_task(
            runner._enqueue_process_completion_notification("completion", event)
        )
        await sleep_entered.wait()
        flush_task = next(iter(runner._completion_notification_batch_tasks.values()))
        assert flush_task in runner._background_tasks

        await runner._cancel_process_completion_batch_tasks()

        assert await asyncio.wait_for(pending, timeout=1.0) is False
        assert flush_task.cancelled()
        assert flush_task not in runner._background_tasks
        assert runner._completion_notification_batches == {}
        assert runner._completion_notification_batch_tasks == {}

    with patch("gateway.run.asyncio.sleep", new=_controlled_sleep):
        asyncio.run(_exercise())
    adapter.handle_message.assert_not_awaited()


def test_shutdown_cancels_blocked_batch_delivery_and_keeps_it_retryable():
    delivery_entered = asyncio.Event()

    async def _blocked_delivery(_event):
        delivery_entered.set()
        await asyncio.Event().wait()

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_blocked_delivery))
    runner = _runner(adapter)
    runner._completion_notification_batch_window = 0
    event = _completion_event(started_at=1.0, session_id="proc_cancel_delivery")

    async def _exercise():
        pending = asyncio.create_task(
            runner._enqueue_process_completion_notification("completion", event)
        )
        await delivery_entered.wait()
        flush_task = next(iter(runner._completion_notification_batch_flush_tasks))

        await runner._cancel_process_completion_batch_tasks()

        assert await asyncio.wait_for(pending, timeout=1.0) is False
        assert flush_task.cancelled()
        assert runner._completion_delivery_identity(event) not in runner._completion_deliveries_inflight
        assert runner._completion_delivery_identity(event) not in runner._completion_deliveries_delivered
        assert runner._completion_notification_batches == {}
        assert runner._completion_notification_batch_tasks == {}

    asyncio.run(_exercise())
    adapter.handle_message.assert_awaited_once()


def test_completion_enqueue_stays_retryable_after_shutdown_starts():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)

    async def _exercise():
        await runner._cancel_process_completion_batch_tasks()
        return await runner._enqueue_process_completion_notification(
            "completion",
            _completion_event(started_at=1.0, session_id="proc_after_shutdown"),
        )

    assert asyncio.run(_exercise()) is False
    assert runner._completion_notification_batches == {}
    assert runner._completion_notification_batch_tasks == {}
    adapter.handle_message.assert_not_awaited()


def test_successful_batch_releases_all_lifecycle_task_references():
    adapter = SimpleNamespace(handle_message=AsyncMock(return_value=None))
    runner = _runner(adapter)
    runner._completion_notification_batch_window = 0

    async def _exercise():
        result = await runner._enqueue_process_completion_notification(
            "completion",
            _completion_event(started_at=1.0, session_id="proc_success_cleanup"),
        )
        await asyncio.sleep(0)
        return result

    assert asyncio.run(_exercise()) is True
    assert runner._completion_notification_batch_tasks == {}
    assert runner._completion_notification_batch_flush_tasks == set()
    assert runner._background_tasks == set()


def test_shutdown_cancels_overlapping_flushes_for_same_route():
    delivery_entered = asyncio.Event()

    async def _blocked_delivery(_event):
        delivery_entered.set()
        await asyncio.Event().wait()

    adapter = SimpleNamespace(handle_message=AsyncMock(side_effect=_blocked_delivery))
    runner = _runner(adapter)
    runner._completion_notification_batch_window = 0
    first_event = _completion_event(started_at=1.0, session_id="proc_old_flush")
    second_event = _completion_event(started_at=2.0, session_id="proc_new_flush")

    async def _exercise():
        first = asyncio.create_task(
            runner._enqueue_process_completion_notification("first", first_event)
        )
        await delivery_entered.wait()

        # The first task has detached from the route index while blocked in
        # adapter delivery.  A new completion for the same route must create a
        # second flush, and shutdown must still own and cancel both tasks.
        assert runner._completion_notification_batch_tasks == {}
        runner._completion_notification_batch_window = 3600
        second = asyncio.create_task(
            runner._enqueue_process_completion_notification("second", second_event)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        flush_tasks = set(runner._completion_notification_batch_flush_tasks)
        assert len(flush_tasks) == 2

        await runner._cancel_process_completion_batch_tasks()

        assert await asyncio.gather(first, second) == [False, False]
        assert all(task.cancelled() for task in flush_tasks)
        assert runner._completion_notification_batches == {}
        assert runner._completion_notification_batch_tasks == {}
        assert runner._completion_notification_batch_flush_tasks == set()
        assert runner._background_tasks == set()

    asyncio.run(_exercise())
    adapter.handle_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# Async-delegation same-tick coalescing (#70300)
# ---------------------------------------------------------------------------


def _distinct_async_event(delegation_id, session_key="agent:main:telegram:dm:12345:678"):
    event = _async_event(delegation_id)
    event["session_key"] = session_key
    event["summary"] = f"Result for {delegation_id}"
    return event


def test_same_tick_async_batch_coalesces_into_one_turn_and_acks_all_rows(
    monkeypatch, isolated_registry,
):
    """Three same-session async completions in one drain -> one synthetic turn.

    All three durable delegation rows must be honestly acknowledged only
    after the single consolidated injection was accepted by the adapter.
    """
    from tools import async_delegation

    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    events = [_distinct_async_event(f"deleg_batch_{i}") for i in range(3)]
    for event in events:
        _persist_pending_completion(event)
        isolated.put(dict(event))

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_awaited_once()
    delivered = adapter.handle_message.await_args.args[0]
    assert "3 background subagent delegations" in delivered.text
    for i in range(3):
        assert f"Result for deleg_batch_{i}" in delivered.text
    for event in events:
        row = async_delegation.get_durable_delegation(event["delegation_id"])
        assert row is not None
        assert row["delivery_state"] == "delivered"
    assert isolated.empty()


def test_same_tick_async_events_for_different_sessions_do_not_coalesce(
    monkeypatch, isolated_registry,
):
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(_distinct_async_event("deleg_route_a"))
    isolated.put(_distinct_async_event(
        "deleg_route_b", session_key="agent:main:telegram:dm:99999:678",
    ))

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    assert adapter.handle_message.await_count == 2
    texts = [call.args[0].text for call in adapter.handle_message.await_args_list]
    assert not any("background subagent delegations" in text for text in texts)
    assert any("deleg_route_a" in text for text in texts)
    assert any("deleg_route_b" in text for text in texts)


def test_single_async_event_latency_and_text_are_unchanged(
    monkeypatch, isolated_registry,
):
    """A lone completion keeps the plain per-event formatter output."""
    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    isolated.put(_distinct_async_event("deleg_single"))

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_awaited_once()
    delivered = adapter.handle_message.await_args.args[0]
    assert "background subagent delegations" not in delivered.text
    assert "deleg_single" in delivered.text


def test_failed_coalesced_async_batch_releases_claims_and_retries(
    monkeypatch, isolated_registry,
):
    """A rejected consolidated injection leaves every durable row pending."""
    from tools import async_delegation

    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    events = [_distinct_async_event(f"deleg_retry_{i}") for i in range(2)]
    for event in events:
        _persist_pending_completion(event)
        isolated.put(dict(event))

    adapter = SimpleNamespace(
        handle_message=AsyncMock(side_effect=[RuntimeError("temporary"), None])
    )
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=3)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    # First tick fails as one batch, second tick delivers the same batch.
    assert adapter.handle_message.await_count == 2
    for event in events:
        row = async_delegation.get_durable_delegation(event["delegation_id"])
        assert row is not None
        assert row["delivery_state"] == "delivered"
    assert isolated.empty()


def test_sibling_claimed_by_other_consumer_is_not_double_delivered(
    monkeypatch, isolated_registry,
):
    """A sibling owned elsewhere is excluded from the consolidated turn."""
    from tools import async_delegation

    isolated = queue.Queue()
    monkeypatch.setattr(isolated_registry, "completion_queue", isolated)
    events = [_distinct_async_event(f"deleg_owned_{i}") for i in range(2)]
    for event in events:
        _persist_pending_completion(event)
        isolated.put(dict(event))
    # Simulate another live consumer holding the second row's claim.
    assert async_delegation.claim_completion_delivery(
        events[1]["delegation_id"], "other-consumer:claim",
    )

    adapter = SimpleNamespace(handle_message=AsyncMock())
    runner = _runner(adapter)
    _stop_after_sleeps(monkeypatch, runner, count=2)

    asyncio.run(runner._async_delegation_watcher(interval=0))

    adapter.handle_message.assert_awaited_once()
    delivered = adapter.handle_message.await_args.args[0]
    assert "Result for deleg_owned_0" in delivered.text
    assert "Result for deleg_owned_1" not in delivered.text
    row = async_delegation.get_durable_delegation(events[1]["delegation_id"])
    assert row["delivery_state"] == "pending"
