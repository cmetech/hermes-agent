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
