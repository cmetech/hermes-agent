from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
import stat

import pytest

import hermes_cli.handoff.store as store_module
from hermes_cli.handoff.models import ChannelObservation, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.store import (
    HandoffConflict,
    HandoffStateConflict,
    HandoffStore,
    StaleAdvanceLease,
)


UTC = timezone.utc


def _spec(prompt: str = "Review this change.") -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt=prompt,
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=UTC),
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities=frozenset(),
    )


def _conversation_spec() -> HandoffSpec:
    return HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Can you review the release?",
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=UTC),
        attribution={"sender": "default"},
        required_capabilities=frozenset({"follow_up"}),
        return_route={
            "kind": "bot",
            "host_kind": "gateway",
            "profile": "default",
            "session_id": "20260902_120000_abc123",
            "session_key": "agent:default:telegram:dm:42",
            "tool_call_id": "call_message_1",
            "delivery_policy": "wake",
            "hop_count": 0,
        },
    )


def _prepare_conversation(store: HandoffStore):
    spec = _conversation_spec()
    snapshot = store.create_or_get(
        "bot/default/session-1", "call-1", spec, spec.fingerprint
    )
    snapshot = store.bind(
        snapshot.handoff_id,
        "local_runs",
        {"profile": "reviewer", "mechanism": "local_runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    store.journal_attempt(lease, "submit")
    return snapshot, lease


def _create(store: HandoffStore, key: str = "node/review"):
    spec = _spec()
    return store.create_or_get("workflow/run-1", key, spec, spec.fingerprint)


def _claim(store: HandoffStore, handoff_id: str, owner: str = "worker-1"):
    lease = store.claim_advance(
        handoff_id,
        owner,
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    return lease


def _result(text: str = "accepted") -> dict[str, object]:
    encoded = text.encode()
    return {
        "text": text,
        "sha256": sha256(encoded).hexdigest(),
        "media_type": "text/plain",
        "size_bytes": len(encoded),
    }


def test_create_replays_equivalent_spec_and_rejects_conflicting_key_reuse(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    spec = _spec()

    first = store.create_or_get("workflow/run-1", "node/review", spec, spec.fingerprint)
    replay = store.create_or_get(
        "workflow/run-1", "node/review", spec, spec.fingerprint
    )

    assert replay == first
    assert len(store.list({}, limit=10, before=None)) == 1
    with pytest.raises(HandoffConflict):
        changed = _spec("Review a different change.")
        store.create_or_get(
            "workflow/run-1", "node/review", changed, changed.fingerprint
        )


def test_conversation_spec_and_return_route_round_trip(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    spec = _conversation_spec()

    created = store.create_or_get(
        "bot/default/session-1", "call-1", spec, spec.fingerprint
    )
    loaded = store.get(created.handoff_id)

    assert loaded.spec == spec
    assert loaded.spec.return_route == spec.return_route


def test_task_spec_serialization_omits_absent_return_route(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    created = _create(store)

    with sqlite3.connect(path) as conn:
        raw = conn.execute(
            "SELECT spec_json FROM handoffs WHERE handoff_id=?",
            (created.handoff_id,),
        ).fetchone()[0]

    assert "return_route" not in json.loads(raw)
    assert store.get(created.handoff_id).spec.fingerprint == _spec().fingerprint


def test_v1_database_migrates_to_current_schema_without_losing_rows(tmp_path):
    path = tmp_path / "handoffs.db"
    legacy = HandoffStore(path)
    snapshot = _create(legacy)
    legacy.record_command(
        snapshot.handoff_id, "reconcile-1", "reconcile", {"actor": "operator"}
    )
    legacy.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS handoff_deliveries")
        conn.execute("PRAGMA user_version=1")

    migrated = HandoffStore(path)

    assert migrated.get(snapshot.handoff_id).handoff_id == snapshot.handoff_id
    assert migrated.get_command(snapshot.handoff_id, "reconcile-1").kind == "reconcile"
    assert [
        event.kind
        for event in migrated.evidence(
            snapshot.handoff_id, after_sequence=0, limit=10
        ).events
    ] == ["created", "reconcile_requested"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='handoff_deliveries'"
        ).fetchone() == ("handoff_deliveries",)
    migrated.close()

    reopened = HandoffStore(path)
    assert reopened.get(snapshot.handoff_id).handoff_id == snapshot.handoff_id


def test_v2_database_adds_delivery_dispatch_state_without_losing_rows(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot, lease = _prepare_conversation(store)
    store.commit_observation(lease, ChannelObservation(phase="needs_input"))
    delivery = store.attention(snapshot.handoff_id, limit=1)[0]
    store.close()
    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(handoff_deliveries)")
        }
        if "dispatch_started_at" in columns:
            conn.execute(
                "ALTER TABLE handoff_deliveries DROP COLUMN dispatch_started_at"
            )
        conn.execute("PRAGMA user_version=2")

    migrated = HandoffStore(path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(handoff_deliveries)")
        }
        assert "dispatch_started_at" in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert migrated.get_delivery(delivery.delivery_id).handoff_id == snapshot.handoff_id
    migrated.close()


def test_future_database_version_is_rejected(tmp_path):
    path = tmp_path / "handoffs.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=99")

    with pytest.raises(store_module.HandoffStoreError, match="unsupported.*99"):
        HandoffStore(path)


def test_v1_migration_rolls_back_schema_and_version_together(
    tmp_path, monkeypatch
):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE handoff_deliveries")
        conn.execute("PRAGMA user_version=1")
    monkeypatch.setattr(
        store_module,
        "_SCHEMA",
        (*store_module._SCHEMA, "this is not valid SQL"),
    )

    with pytest.raises(sqlite3.OperationalError):
        HandoffStore(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='handoff_deliveries'"
        ).fetchone() is None


@pytest.mark.parametrize(
    ("phase", "failure_code", "terminal_result"),
    [
        ("needs_input", None, None),
        ("indeterminate", "observation_indeterminate", None),
        ("succeeded", None, _result("private result")),
        ("failed", "remote_failed", None),
        ("cancelled", None, None),
    ],
)
def test_attention_observation_creates_one_redacted_delivery(
    tmp_path, phase, failure_code, terminal_result
):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot, lease = _prepare_conversation(store)
    observation = ChannelObservation(
        phase=phase,
        checkpoint={"status": phase},
        failure_code=failure_code,
        terminal_result=terminal_result,
    )

    observed = store.commit_observation(lease, observation)
    replayed = store.commit_observation(lease, observation)
    deliveries = store.attention(snapshot.handoff_id, limit=10)

    assert replayed == observed
    assert len(deliveries) == 1
    assert deliveries[0].handoff_id == snapshot.handoff_id
    assert deliveries[0].route == _conversation_spec().return_route
    assert deliveries[0].event_sequence == store.evidence(
        snapshot.handoff_id, after_sequence=0, limit=100
    ).events[-1].sequence
    with sqlite3.connect(path) as conn:
        raw = repr(conn.execute("SELECT * FROM handoff_deliveries").fetchall())
    assert "private result" not in raw


def test_new_attention_supersedes_an_undelivered_return(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, lease = _prepare_conversation(store)

    store.commit_observation(lease, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    store.commit_observation(lease, ChannelObservation(phase="active"))
    store.commit_observation(
        lease,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    attention = store.attention(snapshot.handoff_id, limit=10)
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)

    assert len(attention) == 1
    assert len(due) == 1
    assert due[0].delivery_id == attention[0].delivery_id
    assert due[0].event_sequence > first.event_sequence
    assert store.get_delivery(first.delivery_id).acknowledged_at is not None


@pytest.mark.parametrize("settle", ["complete", "release"])
def test_dispatch_started_before_supersession_finishes_in_event_order(
    tmp_path, settle
):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    delivery_lease = store.claim_delivery(
        first.delivery_id,
        "gateway",
        now=first.next_attempt_at,
        lease_seconds=30,
    )
    assert delivery_lease is not None
    store.begin_delivery_dispatch(delivery_lease)

    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    assert store.get_delivery(first.delivery_id).acknowledged_at is None
    assert store.due_deliveries(now=datetime.now(UTC), limit=10) == ()
    if settle == "complete":
        settled = store.complete_delivery(delivery_lease)
        assert settled.state == "delivered"
    else:
        settled = store.release_delivery(
            delivery_lease,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=2),
            failure_code="delivery_retryable",
        )
        assert settled.state == "pending"
    assert settled.acknowledged_at is not None
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)
    assert len(due) == 1
    assert due[0].event_sequence > first.event_sequence


def test_abandoned_dispatch_yields_to_newer_delivery_after_lease_expiry(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    delivery_lease = store.claim_delivery(
        first.delivery_id,
        "gateway-before-restart",
        now=first.next_attempt_at,
        lease_seconds=1,
    )
    assert delivery_lease is not None
    store.begin_delivery_dispatch(delivery_lease)
    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    after_expiry = delivery_lease.expires_at + timedelta(microseconds=1)
    assert [
        item.delivery_id
        for item in store.due_deliveries(now=after_expiry, limit=10)
    ] == [first.delivery_id]
    assert store.claim_delivery(
        first.delivery_id,
        "gateway-after-restart",
        now=after_expiry,
        lease_seconds=30,
    ) is None
    assert store.get_delivery(first.delivery_id).acknowledged_at is not None
    due = store.due_deliveries(now=after_expiry, limit=10)
    assert len(due) == 1
    assert due[0].event_sequence > first.event_sequence


def test_receipt_pending_dispatch_stays_ordered_for_the_same_host(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    delivery_lease = store.claim_delivery(
        first.delivery_id,
        "gateway-process",
        now=first.next_attempt_at,
        lease_seconds=30,
    )
    assert delivery_lease is not None
    store.begin_delivery_dispatch(delivery_lease)
    pending = store.defer_delivery_receipt(
        delivery_lease,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=2),
    )

    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    assert store.get_delivery(first.delivery_id).acknowledged_at is None
    assert [
        item.delivery_id
        for item in store.due_deliveries(now=pending.next_attempt_at, limit=10)
    ] == [first.delivery_id]
    receipt_lease = store.claim_delivery(
        first.delivery_id,
        "gateway-process",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert receipt_lease is not None
    assert store.get_delivery(first.delivery_id).attempts == 1
    settled = store.complete_delivery(receipt_lease)
    assert settled.state == "delivered"
    assert settled.acknowledged_at is not None
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)
    assert len(due) == 1
    assert due[0].event_sequence > first.event_sequence


def test_receipt_pending_dispatch_does_not_exhaust_long_turn_attempts(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    _snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    delivery = store.due_deliveries(now=datetime.now(UTC), limit=1)[0]
    lease = store.claim_delivery(
        delivery.delivery_id,
        "tui-process",
        now=delivery.next_attempt_at,
        lease_seconds=30,
    )
    assert lease is not None
    store.begin_delivery_dispatch(lease)
    pending = store.defer_delivery_receipt(
        lease,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=2),
    )

    reclaim_at = pending.next_attempt_at
    for _ in range(10):
        lease = store.claim_delivery(
            delivery.delivery_id,
            "tui-process",
            now=reclaim_at,
            lease_seconds=30,
        )
        assert lease is not None
        reclaim_at = lease.expires_at

    assert store.get_delivery(delivery.delivery_id).attempts == 1
    assert store.complete_delivery(lease).state == "delivered"


def test_receipt_pending_dispatch_is_reconciled_by_a_restarted_host(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    delivery_lease = store.claim_delivery(
        first.delivery_id,
        "gateway-before-restart",
        now=first.next_attempt_at,
        lease_seconds=30,
    )
    assert delivery_lease is not None
    store.begin_delivery_dispatch(delivery_lease)
    pending = store.defer_delivery_receipt(
        delivery_lease,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=2),
    )
    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    restarted_lease = store.claim_delivery(
        first.delivery_id,
        "gateway-after-restart",
        now=pending.next_attempt_at,
        lease_seconds=30,
    )
    assert restarted_lease is not None
    assert store.get_delivery(first.delivery_id).attempts == 1
    settled = store.release_delivery(
        restarted_lease,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=2),
        failure_code="delivery_retryable",
    )
    assert settled.acknowledged_at is not None
    due = store.due_deliveries(now=pending.next_attempt_at, limit=10)
    assert len(due) == 1
    assert due[0].event_sequence > first.event_sequence


def test_completed_dispatch_does_not_block_a_later_terminal_return(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, advance = _prepare_conversation(store)
    store.commit_observation(advance, ChannelObservation(phase="needs_input"))
    first = store.attention(snapshot.handoff_id, limit=1)[0]
    delivery_lease = store.claim_delivery(
        first.delivery_id,
        "gateway",
        now=first.next_attempt_at,
        lease_seconds=30,
    )
    assert delivery_lease is not None
    store.begin_delivery_dispatch(delivery_lease)
    store.complete_delivery(delivery_lease)

    store.commit_observation(advance, ChannelObservation(phase="active"))
    store.commit_observation(
        advance,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("terminal result")
        ),
    )

    old = store.get_delivery(first.delivery_id)
    assert old.state == "delivered"
    assert old.acknowledged_at is not None
    attention = store.attention(snapshot.handoff_id, limit=10)
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)
    assert len(attention) == 1
    assert len(due) == 1
    assert due[0].delivery_id == attention[0].delivery_id
    assert due[0].event_sequence > first.event_sequence


def test_task_observation_never_creates_a_return_delivery(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)

    store.commit_observation(
        lease, ChannelObservation(phase="failed", failure_code="remote_failed")
    )

    assert store.attention(snapshot.handoff_id, limit=10) == ()


def test_observation_and_return_delivery_are_one_transaction(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot, lease = _prepare_conversation(store)
    before = store.get(snapshot.handoff_id)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_delivery BEFORE INSERT ON handoff_deliveries "
            "BEGIN SELECT RAISE(ABORT, 'injected delivery failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected delivery failure"):
        store.commit_observation(
            lease,
            ChannelObservation(phase="failed", failure_code="remote_failed"),
        )

    assert store.get(snapshot.handoff_id) == before
    assert store.attention(snapshot.handoff_id, limit=10) == ()


def test_delivery_claim_release_completion_is_fenced_and_restart_safe(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot, handoff_lease = _prepare_conversation(store)
    store.commit_observation(
        handoff_lease,
        ChannelObservation(
            phase="succeeded", terminal_result=_result("private result")
        ),
    )
    due = store.due_deliveries(now=datetime.now(UTC), limit=10)
    assert len(due) == 1
    first = store.claim_delivery(
        due[0].delivery_id,
        "publisher-1",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert first is not None
    assert store.claim_delivery(
        due[0].delivery_id,
        "publisher-2",
        now=datetime.now(UTC),
        lease_seconds=30,
    ) is None
    store.close()

    restarted = HandoffStore(path)
    takeover = restarted.claim_delivery(
        due[0].delivery_id,
        "publisher-2",
        now=first.expires_at,
        lease_seconds=30,
    )
    assert takeover is not None
    assert takeover.epoch == first.epoch + 1
    with pytest.raises(StaleAdvanceLease):
        restarted.complete_delivery(first)
    retry_at = datetime.now(UTC) + timedelta(seconds=5)
    released = restarted.release_delivery(
        takeover,
        next_attempt_at=retry_at,
        failure_code="publish_failed",
    )
    assert released.state == "pending"
    assert released.failure_code == "publish_failed"
    assert restarted.due_deliveries(now=datetime.now(UTC), limit=10) == ()
    retry = restarted.claim_delivery(
        due[0].delivery_id,
        "publisher-3",
        now=retry_at,
        lease_seconds=30,
    )
    assert retry is not None
    completed = restarted.complete_delivery(retry)
    assert completed.state == "delivered"
    assert restarted.due_deliveries(now=retry_at, limit=10) == ()


def test_delivery_retry_limit_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_MAX_DELIVERY_ATTEMPTS", 1)
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, handoff_lease = _prepare_conversation(store)
    store.commit_observation(
        handoff_lease,
        ChannelObservation(phase="failed", failure_code="remote_failed"),
    )
    delivery = store.due_deliveries(now=datetime.now(UTC), limit=1)[0]
    lease = store.claim_delivery(
        delivery.delivery_id,
        "publisher",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None

    failed = store.release_delivery(
        lease,
        next_attempt_at=datetime.now(UTC),
        failure_code="publish_failed",
    )

    assert failed.state == "failed"
    assert store.due_deliveries(now=datetime.now(UTC), limit=1) == ()
    assert len(store.attention(snapshot.handoff_id, limit=10)) == 1


def test_expired_final_delivery_attempt_is_marked_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_MAX_DELIVERY_ATTEMPTS", 1)
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, handoff_lease = _prepare_conversation(store)
    store.commit_observation(
        handoff_lease,
        ChannelObservation(phase="failed", failure_code="remote_failed"),
    )
    delivery = store.due_deliveries(now=datetime.now(UTC), limit=1)[0]
    lease = store.claim_delivery(
        delivery.delivery_id,
        "crashed-publisher",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None

    assert store.claim_delivery(
        delivery.delivery_id,
        "restarted-publisher",
        now=lease.expires_at,
        lease_seconds=30,
    ) is None

    failed = store.get_delivery(delivery.delivery_id)
    assert failed.state == "failed"
    assert failed.failure_code == "delivery_attempts_exhausted"
    assert store.attention(snapshot.handoff_id, limit=1) == (failed,)


def test_delivery_can_fail_closed_without_clearing_attention(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, handoff_lease = _prepare_conversation(store)
    store.commit_observation(
        handoff_lease,
        ChannelObservation(phase="failed", failure_code="remote_failed"),
    )
    delivery = store.due_deliveries(now=datetime.now(UTC), limit=1)[0]
    lease = store.claim_delivery(
        delivery.delivery_id,
        "publisher",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None

    failed = store.fail_delivery(lease, failure_code="wake_unavailable")

    assert failed.state == "failed"
    assert failed.failure_code == "wake_unavailable"
    assert store.attention(snapshot.handoff_id, limit=10) == (failed,)


def test_acknowledgement_clears_attention_without_changing_delivery_truth(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot, lease = _prepare_conversation(store)
    terminal = store.commit_observation(
        lease, ChannelObservation(phase="failed", failure_code="remote_failed")
    )
    delivery = store.attention(snapshot.handoff_id, limit=1)[0]

    acknowledged = store.acknowledge(snapshot.handoff_id, actor="operator")

    assert acknowledged == 1
    assert store.attention(snapshot.handoff_id, limit=10) == ()
    assert store.get(snapshot.handoff_id) == terminal
    assert store.get_delivery(delivery.delivery_id).state == delivery.state
    assert store.get_delivery(delivery.delivery_id).acknowledged_at is not None
    assert [
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ][-1] == "acknowledged"
    assert store.acknowledge(snapshot.handoff_id, actor="operator") == 0


def test_concurrent_creators_converge_on_one_handoff(tmp_path):
    path = tmp_path / "handoffs.db"
    stores = [HandoffStore(path), HandoffStore(path)]
    spec = _spec()

    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(
            pool.map(
                lambda store: store.create_or_get(
                    "workflow/run-1",
                    "node/review",
                    spec,
                    spec.fingerprint,
                ),
                stores,
            )
        )

    assert snapshots[0].handoff_id == snapshots[1].handoff_id
    assert len(stores[0].list({}, limit=10, before=None)) == 1
    assert [
        event.kind
        for event in stores[0]
        .evidence(
            snapshots[0].handoff_id,
            after_sequence=0,
            limit=10,
        )
        .events
    ] == ["created"]


def test_create_and_created_event_are_one_transaction(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_handoff_event BEFORE INSERT ON handoff_events "
            "BEGIN SELECT RAISE(ABORT, 'injected event failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected event failure"):
        _create(store)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0


def test_binding_is_idempotent_immutable_and_version_checked(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    created = _create(store)
    binding = {"profile": "reviewer", "mechanism": "runs"}

    bound = store.bind(
        created.handoff_id,
        "runs",
        binding,
        {"run_id": "run-1"},
        created.state_version,
    )
    replay = store.bind(
        created.handoff_id,
        "runs",
        binding,
        {"run_id": "run-1"},
        bound.state_version,
    )

    assert replay == bound
    with pytest.raises(HandoffConflict):
        store.bind(
            created.handoff_id,
            "cli",
            {"profile": "reviewer", "mechanism": "cli"},
            {"session_id": "session-1"},
            bound.state_version,
        )
    with pytest.raises(HandoffStateConflict):
        store.bind(
            created.handoff_id,
            "runs",
            binding,
            {"run_id": "run-1"},
            created.state_version,
        )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("prepared", "submitted"),
        ("submitted", "active"),
        ("active", "needs_input"),
        ("needs_input", "active"),
        ("active", "indeterminate"),
        ("indeterminate", "submitted"),
        ("submitted", "succeeded"),
        ("submitted", "failed"),
        ("submitted", "cancelled"),
        ("cancelling", "succeeded"),
        ("cancelling", "failed"),
        ("cancelling", "cancelled"),
    ],
)
def test_legal_lifecycle_transitions_commit(tmp_path, before: str, after: str):
    store = HandoffStore(tmp_path / f"{before}-{after}.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    store.journal_attempt(lease, "submit")

    paths = {
        "prepared": [],
        "submitted": ["submitted"],
        "active": ["submitted", "active"],
        "needs_input": ["submitted", "active", "needs_input"],
        "indeterminate": ["indeterminate"],
        "cancelling": ["submitted"],
    }
    for phase in paths[before]:
        store.commit_observation(lease, ChannelObservation(phase=phase))
    if before == "cancelling":
        store.record_command(
            snapshot.handoff_id, "cancel-1", "cancel", {"actor": "workflow"}
        )

    observation = ChannelObservation(
        phase=after,
        terminal_result=_result() if after == "succeeded" else None,
        failure_code="remote_failed" if after == "failed" else None,
    )

    assert store.commit_observation(lease, observation).phase == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("active", "submitted"),
        ("needs_input", "submitted"),
        ("cancelling", "active"),
        ("submitted", "prepared"),
        ("indeterminate", "prepared"),
    ],
)
def test_illegal_lifecycle_transitions_record_nothing(
    tmp_path, before: str, after: str
):
    store = HandoffStore(tmp_path / f"{before}-{after}.db")
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    store.journal_attempt(lease, "submit")
    paths = {
        "submitted": ["submitted"],
        "active": ["submitted", "active"],
        "needs_input": ["submitted", "active", "needs_input"],
        "indeterminate": ["indeterminate"],
        "cancelling": ["submitted"],
    }
    for phase in paths[before]:
        store.commit_observation(lease, ChannelObservation(phase=phase))
    if before == "cancelling":
        store.record_command(
            snapshot.handoff_id, "cancel-1", "cancel", {"actor": "workflow"}
        )
    prior = store.get(snapshot.handoff_id)
    event_count = len(
        store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events
    )

    with pytest.raises(HandoffStateConflict):
        store.commit_observation(lease, ChannelObservation(phase=after))

    assert store.get(snapshot.handoff_id) == prior
    assert (
        len(store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events)
        == event_count
    )


def test_terminal_snapshot_and_result_are_immutable(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    store.journal_attempt(lease, "submit")
    store.commit_observation(lease, ChannelObservation(phase="submitted"))
    terminal = ChannelObservation(phase="succeeded", terminal_result=_result())

    first = store.commit_observation(lease, terminal)
    event_count = len(
        store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events
    )

    assert store.commit_observation(lease, terminal) == first
    assert (
        len(store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events)
        == event_count
    )
    with pytest.raises(HandoffStateConflict):
        store.commit_observation(
            lease,
            ChannelObservation(phase="succeeded", terminal_result=_result("changed")),
        )
    with pytest.raises(HandoffStateConflict):
        store.commit_observation(
            lease, ChannelObservation(phase="failed", failure_code="remote_failed")
        )


def test_command_replay_is_content_bound_and_cancel_is_durable(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)

    first = store.record_command(
        snapshot.handoff_id,
        "cancel-1",
        "cancel",
        {"actor": "workflow"},
    )
    replay = store.record_command(
        snapshot.handoff_id,
        "cancel-1",
        "cancel",
        {"actor": "workflow"},
    )

    assert replay == first
    cancelled = store.get(snapshot.handoff_id)
    assert cancelled.phase == "cancelling"
    assert cancelled.cancel_requested_at is not None
    with pytest.raises(HandoffConflict):
        store.record_command(
            snapshot.handoff_id,
            "cancel-1",
            "reconcile",
            {"actor": "workflow"},
        )
    with pytest.raises(ValueError):
        store.record_command(
            snapshot.handoff_id,
            "unsafe-1",
            "cancel",
            {"authorization": "Bearer secret"},
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("respond", {"actor": "workflow", "request_id": "approval-1", "choice": "once"}),
        ("steer", {"actor": "workflow", "text": "Tighten the conclusion."}),
        (
            "message",
            {
                "actor": "workflow",
                "text": "Please check the follow-up.",
                "correlation_id": "follow-up-1",
            },
        ),
    ],
)
def test_control_command_payloads_are_closed_bounded_and_content_bound(
    tmp_path, kind, payload
):
    store = HandoffStore(tmp_path / "commands.db")
    snapshot = _create(store)

    first = store.record_command(snapshot.handoff_id, "command-1", kind, payload)
    replay = store.record_command(snapshot.handoff_id, "command-1", kind, payload)

    assert replay == first
    assert dict(first.payload) == payload
    with pytest.raises(HandoffConflict):
        store.record_command(
            snapshot.handoff_id,
            "command-1",
            kind,
            {**payload, "actor": "different"},
        )
    with pytest.raises(ValueError):
        store.record_command(
            snapshot.handoff_id,
            "unsafe-command",
            kind,
            {**payload, "authorization": "Bearer secret"},
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("respond", {"actor": "workflow", "request_id": "approval-1"}),
        ("respond", {"actor": "workflow", "request_id": "approval-1", "choice": "yes"}),
        ("steer", {"actor": "workflow", "text": ""}),
        ("steer", {"actor": "workflow", "text": "x" * 16_385}),
        ("message", {"actor": "workflow", "text": "follow up"}),
        (
            "message",
            {"actor": "workflow", "text": "follow up", "correlation_id": "https://unsafe.test"},
        ),
    ],
)
def test_control_command_payloads_reject_missing_invalid_and_oversized_values(
    tmp_path, kind, payload
):
    store = HandoffStore(tmp_path / "invalid-commands.db")
    snapshot = _create(store)

    with pytest.raises(ValueError):
        store.record_command(snapshot.handoff_id, "command-1", kind, payload)


def test_command_delivery_state_is_cas_protected_and_never_returns_to_pending(
    tmp_path,
):
    store = HandoffStore(tmp_path / "delivery.db")
    snapshot = _create(store)
    store.record_command(
        snapshot.handoff_id,
        "steer-1",
        "steer",
        {"actor": "workflow", "text": "Tighten the conclusion."},
    )
    lease = _claim(store, snapshot.handoff_id)

    claimed = store.claim_delivery_command(lease)

    assert claimed is not None
    assert claimed.delivery_state == "pending"
    assert store.get_command(snapshot.handoff_id, "steer-1").delivery_state == (
        "attempted"
    )
    delivered = store.complete_delivery_command(
        lease, "steer-1", "delivered"
    )
    assert delivered.delivery_state == "delivered"
    assert store.complete_delivery_command(lease, "steer-1", "delivered") == delivered
    with pytest.raises(HandoffStateConflict):
        store.complete_delivery_command(lease, "steer-1", "indeterminate")
    with pytest.raises(ValueError):
        store.complete_delivery_command(lease, "steer-1", "pending")


def test_restart_claim_returns_attempted_command_for_read_only_reconciliation(
    tmp_path,
):
    store = HandoffStore(tmp_path / "restart-command.db")
    snapshot = _create(store)
    store.record_command(
        snapshot.handoff_id,
        "respond-1",
        "respond",
        {"actor": "workflow", "request_id": "approval-1", "choice": "once"},
    )
    first_lease = _claim(store, snapshot.handoff_id)
    assert store.claim_delivery_command(first_lease).delivery_state == "pending"
    store.release_advance(first_lease, next_advance_at=None)

    restarted_lease = _claim(store, snapshot.handoff_id)
    restarted = store.claim_delivery_command(restarted_lease)

    assert restarted is not None
    assert restarted.command_id == "respond-1"
    assert restarted.delivery_state == "attempted"


def test_attempt_is_durable_before_io_and_event_data_is_redacted(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    unsafe = {
        "operation": "submit",
        "authorization": "Bearer super-secret",
        "provider_error": "raw provider error with token=super-secret",
        "profile_path": "/Users/example/.hermes/profiles/reviewer",
        "status": "Bearer-super-secret",
    }

    journaled = store.journal_attempt(lease, "submit", data=unsafe)

    def adapter_call() -> None:
        persisted = HandoffStore(path).get(snapshot.handoff_id)
        assert persisted.submit_attempted_at is not None
        assert (
            HandoffStore(path)
            .evidence(
                snapshot.handoff_id,
                after_sequence=0,
                limit=100,
            )
            .events[-1]
            .kind
            == "submit_attempted"
        )

    adapter_call()
    assert journaled.submit_attempted_at is not None
    evidence = store.evidence(snapshot.handoff_id, after_sequence=0, limit=100)
    encoded = json.dumps([dict(event.data) for event in evidence.events])
    assert "super-secret" not in encoded
    assert "provider error" not in encoded
    assert "/Users/example" not in encoded
    with sqlite3.connect(path) as conn:
        persisted = " ".join(
            value
            for row in conn.execute("SELECT safe_data_json FROM handoff_events")
            for value in row
        )
    assert "super-secret" not in persisted


def test_submit_requires_a_sealed_binding_and_cannot_be_attempted_twice(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)

    with pytest.raises(HandoffStateConflict):
        store.journal_attempt(lease, "submit")

    bound = store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    store.journal_attempt(lease, "submit")

    assert bound.mechanism == "runs"
    with pytest.raises(HandoffStateConflict):
        store.journal_attempt(lease, "submit")


def test_fenced_binding_journals_before_commit_and_preserves_immutable_rules(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)

    journaled = store.journal_attempt(lease, "bind")
    assert journaled.binding is None
    assert (
        store.evidence(snapshot.handoff_id, after_sequence=0, limit=10).events[-1].kind
        == "bind_attempted"
    )

    bound = store.commit_binding(
        lease,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {"run_id": "run-1"},
    )
    assert bound.mechanism == "runs"
    assert bound.checkpoint == {"run_id": "run-1"}

    assert (
        store.commit_binding(
            lease,
            "runs",
            {"profile": "reviewer", "mechanism": "runs"},
            {"run_id": "run-1"},
        )
        == bound
    )
    with pytest.raises(HandoffConflict):
        store.commit_binding(
            lease,
            "cli",
            {"profile": "reviewer", "mechanism": "cli"},
            {"session_id": "session-1"},
        )


@pytest.mark.parametrize("taken_over", [False, True])
def test_expired_or_taken_over_lease_cannot_commit_binding(
    tmp_path, monkeypatch, taken_over: bool
):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    first = _claim(store, snapshot.handoff_id, "worker-1")
    monkeypatch.setattr(store_module, "_utc_now", lambda: first.expires_at)
    if taken_over:
        assert (
            store.claim_advance(
                snapshot.handoff_id,
                "worker-2",
                now=first.expires_at,
                lease_seconds=30,
            )
            is not None
        )

    with pytest.raises(StaleAdvanceLease):
        store.commit_binding(
            first,
            "runs",
            {"profile": "reviewer", "mechanism": "runs"},
            {},
        )

    assert store.get(snapshot.handoff_id).binding is None


def test_fenced_binding_rejects_terminal_handoff(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)
    store.commit_observation(
        lease, ChannelObservation(phase="failed", failure_code="policy_denied")
    )

    with pytest.raises(HandoffStateConflict):
        store.commit_binding(
            lease,
            "runs",
            {"profile": "reviewer", "mechanism": "runs"},
            {},
        )


def test_cancel_winner_prevents_any_later_submit_fact_or_event(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    cancelled = store.record_command(
        snapshot.handoff_id, "cancel-1", "cancel", {"actor": "workflow"}
    )
    before = store.get(snapshot.handoff_id)

    with pytest.raises(HandoffStateConflict):
        store.journal_attempt(lease, "submit")

    after = store.get(snapshot.handoff_id)
    assert cancelled.kind == "cancel"
    assert before.phase == after.phase == "cancelling"
    assert after.submit_attempted_at is None
    assert "submit_attempted" not in {
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    }


def test_submit_winner_may_be_followed_by_durable_cancellation(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)

    submitted = store.journal_attempt(lease, "submit")
    store.record_command(
        snapshot.handoff_id, "cancel-1", "cancel", {"actor": "workflow"}
    )

    cancelled = store.get(snapshot.handoff_id)
    assert submitted.submit_attempted_at is not None
    assert cancelled.submit_attempted_at == submitted.submit_attempted_at
    assert cancelled.cancel_requested_at is not None
    assert cancelled.phase == "cancelling"


def test_cancel_fact_is_recorded_without_mutating_a_terminal_result(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    bound = store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    store.journal_attempt(lease, "submit")
    store.commit_observation(lease, ChannelObservation(phase="submitted"))
    terminal = store.commit_observation(
        lease,
        ChannelObservation(phase="succeeded", terminal_result=_result()),
    )

    store.record_command(
        terminal.handoff_id, "cancel-after-terminal", "cancel", {"actor": "workflow"}
    )
    after = store.get(terminal.handoff_id)

    assert bound.mechanism == "runs"
    assert after.phase == "succeeded"
    assert after.terminal_result == terminal.terminal_result
    assert after.cancel_requested_at is not None


def test_event_sequences_and_evidence_pages_are_monotonic(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    bound = store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    store.journal_attempt(lease, "submit")
    store.commit_observation(lease, ChannelObservation(phase="submitted"))

    first = store.evidence(snapshot.handoff_id, after_sequence=0, limit=2)
    second = store.evidence(
        snapshot.handoff_id,
        after_sequence=first.next_after_sequence,
        limit=2,
    )

    assert bound.state_version > snapshot.state_version
    assert [event.sequence for event in first.events + second.events] == [1, 2, 3, 4]
    assert [event.kind for event in first.events + second.events] == [
        "created",
        "bound",
        "submit_attempted",
        "observed",
    ]
    assert first.has_more is True
    assert second.has_more is False


def test_list_is_filtered_bounded_and_cursor_paginated(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshots = [_create(store, f"node/{index}") for index in range(3)]
    other_spec = _spec()
    store.create_or_get(
        "workflow/run-2", "node/other", other_spec, other_spec.fingerprint
    )

    first = store.list({"key_scope": "workflow/run-1"}, limit=2, before=None)
    second = store.list(
        {"key_scope": "workflow/run-1"},
        limit=2,
        before=first[-1].handoff_id,
    )

    assert len(first) == 2
    assert len(second) == 1
    assert {item.handoff_id for item in first + second} == {
        item.handoff_id for item in snapshots
    }


def test_list_cursor_order_is_immutable_when_cursor_row_updates(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshots = [_create(store, f"node/{index}") for index in range(3)]
    first = store.list({"key_scope": "workflow/run-1"}, limit=2, before=None)
    cursor = first[-1]

    store.bind(
        cursor.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        cursor.state_version,
    )
    second = store.list(
        {"key_scope": "workflow/run-1"}, limit=2, before=cursor.handoff_id
    )

    assert not (
        {item.handoff_id for item in first} & {item.handoff_id for item in second}
    )
    assert {item.handoff_id for item in first + second} == {
        item.handoff_id for item in snapshots
    }


@pytest.mark.parametrize("expiry_offset_seconds", [0, 1])
@pytest.mark.parametrize("operation", ["journal", "observe", "release"])
def test_expired_lease_is_stale_before_takeover(
    tmp_path, monkeypatch, expiry_offset_seconds: int, operation: str
):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    operation_time = lease.expires_at + timedelta(seconds=expiry_offset_seconds)
    monkeypatch.setattr(store_module, "_utc_now", lambda: operation_time)
    before = store.get(snapshot.handoff_id)
    event_count = len(
        store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events
    )

    with pytest.raises(StaleAdvanceLease):
        if operation == "journal":
            store.journal_attempt(lease, "observe")
        elif operation == "observe":
            store.commit_observation(
                lease,
                ChannelObservation(phase="failed", failure_code="remote_failed"),
            )
        else:
            store.release_advance(lease, next_advance_at=operation_time)

    assert store.get(snapshot.handoff_id) == before
    assert (
        len(store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events)
        == event_count
    )


def test_expired_lease_takeover_fences_every_old_worker_write(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    first = _claim(store, snapshot.handoff_id, "worker-1")
    takeover = store.claim_advance(
        snapshot.handoff_id,
        "worker-2",
        now=first.expires_at,
        lease_seconds=30,
    )

    assert takeover is not None
    assert takeover.epoch == first.epoch + 1
    before = store.get(snapshot.handoff_id)
    event_count = len(
        store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events
    )
    with pytest.raises(StaleAdvanceLease):
        store.journal_attempt(first, "submit")
    with pytest.raises(StaleAdvanceLease):
        store.commit_observation(
            first, ChannelObservation(phase="failed", failure_code="remote_failed")
        )
    with pytest.raises(StaleAdvanceLease):
        store.release_advance(
            first, next_advance_at=datetime.now(UTC) + timedelta(minutes=1)
        )
    assert store.get(snapshot.handoff_id) == before
    assert (
        len(store.evidence(snapshot.handoff_id, after_sequence=0, limit=100).events)
        == event_count
    )


def test_unbound_terminal_handoff_cannot_be_bound(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)
    terminal = store.commit_observation(
        lease, ChannelObservation(phase="failed", failure_code="policy_denied")
    )

    with pytest.raises(HandoffStateConflict):
        store.bind(
            terminal.handoff_id,
            "runs",
            {"profile": "reviewer", "mechanism": "runs"},
            {},
            terminal.state_version,
        )

    assert store.get(terminal.handoff_id) == terminal


@pytest.mark.parametrize("phase", ["succeeded", "cancelled"])
def test_prepared_handoff_accepts_terminal_reconciliation_only_after_submit_journal(
    tmp_path, phase: str
):
    store = HandoffStore(tmp_path / "handoffs.db")
    snapshot = _create(store)
    lease = _claim(store, snapshot.handoff_id)
    observation = ChannelObservation(
        phase=phase,
        terminal_result=_result() if phase == "succeeded" else None,
    )

    with pytest.raises(HandoffStateConflict):
        store.commit_observation(lease, observation)

    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    store.journal_attempt(lease, "submit")

    assert store.commit_observation(lease, observation).phase == phase


def test_submit_fact_and_event_roll_back_together_on_event_failure(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    snapshot = _create(store)
    store.bind(
        snapshot.handoff_id,
        "runs",
        {"profile": "reviewer", "mechanism": "runs"},
        {},
        snapshot.state_version,
    )
    lease = _claim(store, snapshot.handoff_id)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_submit_event BEFORE INSERT ON handoff_events "
            "WHEN NEW.kind = 'submit_attempted' "
            "BEGIN SELECT RAISE(ABORT, 'injected submit event failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected submit event failure"):
        store.journal_attempt(lease, "submit")

    rolled_back = store.get(snapshot.handoff_id)
    assert rolled_back.submit_attempted_at is None
    assert "submit_attempted" not in {
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    }

    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER reject_submit_event")
    committed = store.journal_attempt(lease, "submit")

    assert committed.submit_attempted_at is not None
    assert [
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ].count("submit_attempted") == 1


def test_database_reopens_in_wal_mode_with_foreign_keys_and_durable_state(tmp_path):
    path = tmp_path / "handoffs.db"
    first = HandoffStore(path)
    snapshot = _create(first)
    first.close()

    reopened = HandoffStore(path)

    assert reopened.get(snapshot.handoff_id) == snapshot
    assert reopened.journal_mode == "wal"
    assert reopened.foreign_keys_enabled is True
    assert [
        event.kind
        for event in reopened.evidence(
            snapshot.handoff_id,
            after_sequence=0,
            limit=10,
        ).events
    ] == ["created"]


def test_database_sidecars_are_owner_only(tmp_path):
    path = tmp_path / "handoffs.db"
    store = HandoffStore(path)
    _create(store)

    sidecars = [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ]
    assert all(sidecar.exists() for sidecar in sidecars)
    assert [stat.S_IMODE(sidecar.stat().st_mode) for sidecar in sidecars] == [0o600] * 3
