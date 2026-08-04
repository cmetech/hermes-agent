from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
from types import SimpleNamespace

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ExecutionFence
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore


def _terminal_background_failure(tmp_path, workflow_writer, *, name: str):
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    identity = CoordinatorIdentity(
        owner_id=f"{name}-owner",
        host_kind="web",
        host_instance_id=f"{name}-host",
        pid=1,
        process_start_time=None,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity, now=now, lease_seconds=60
    )
    assert leadership.is_leader
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=name,
            nodes=[{"id": "fail", "bash": "exit 7"}],
        )
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=name,
            concurrency_key=name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(
        store,
        owner_id=f"coordinator:{identity.owner_id}:{leadership.lease.epoch}",
        execution_fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    ).advance(admitted.run_id)
    return store, admitted.run_id


def test_outbox_lease_requires_electron_ack_and_survives_restart(tmp_path):
    home = tmp_path / "home"
    store = RunStore(home)
    outbox = NotificationOutbox(store)
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    notification_id = outbox.record(
        run_id="run-1",
        kind="approval_required",
        destination="desktop",
        transition_version=4,
        payload={"workflow": "review", "interaction_id": "gate-1"},
        now=now,
    )

    restarted = NotificationOutbox(RunStore(home))
    leased = restarted.lease(
        destination="desktop",
        owner_id="electron-a",
        now=now,
        lease_seconds=30,
    )
    assert [item["notification_id"] for item in leased] == [notification_id]
    assert restarted.lease(
        destination="desktop",
        owner_id="electron-b",
        now=now + timedelta(seconds=1),
        lease_seconds=30,
    ) == ()
    assert restarted.ack(notification_id, owner_id="electron-b", now=now) is False
    assert restarted.ack(notification_id, owner_id="electron-a", now=now) is True
    assert restarted.pending_attention(run_id="run-1") == ()


def test_notification_failures_persist_only_fixed_value_free_diagnostics(tmp_path):
    """Provider-controlled delivery errors cannot enter durable public history."""
    store = RunStore(tmp_path / "notification-private-errors")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    canary = "private-notification-session-provider-path-history"
    notification_id = outbox.record(
        run_id="private-notification-run",
        kind="failure",
        destination="desktop",
        transition_version=1,
        payload={
            "last_error": canary,
            "sessionAlias": canary,
            "nested": {
                "node_session_id": canary,
                "messages": [f"embedded {canary}"],
            },
        },
        now=now,
    )
    leased = outbox.lease(
        destination="desktop",
        owner_id="notification-owner",
        now=now,
        lease_seconds=30,
    )
    assert leased[0]["notification_id"] == notification_id
    assert outbox.terminal_fail(
        notification_id,
        owner_id="notification-owner",
        error=canary,
        now=now,
    )

    with store._connect() as connection:
        durable = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT workflow_notification_outbox.payload_json, last_error, "
                "workflow_notification_facts.payload_json "
                "FROM workflow_notification_outbox LEFT JOIN "
                "workflow_notification_facts USING(notification_id) "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
            for value in row
        )
    assert canary not in durable
    assert canary not in str(outbox.history(run_id="private-notification-run"))


def test_expired_desktop_lease_returns_to_pending_and_dismissal_is_projection_only(
    tmp_path,
):
    store = RunStore(tmp_path / "home")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    notification_id = outbox.record(
        run_id="run-2",
        kind="failure",
        destination="desktop",
        transition_version=8,
        payload={"workflow": "broken"},
        now=now,
    )
    outbox.lease(
        destination="desktop",
        owner_id="crashed-renderer",
        now=now,
        lease_seconds=30,
    )

    leased = outbox.lease(
        destination="desktop",
        owner_id="replacement",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )
    assert leased[0]["notification_id"] == notification_id
    outbox.dismiss(notification_id, owner_id="replacement", now=now)
    assert store.list_runs() == ()
    assert outbox.pending_attention(run_id="run-2")[0]["dismissed_at"]


def test_flapping_delivery_coalesces_but_distinct_human_gates_do_not(tmp_path):
    outbox = NotificationOutbox(RunStore(tmp_path / "home"))
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    first = outbox.record(
        run_id="run-3",
        kind="failure",
        destination="desktop",
        transition_version=2,
        payload={"error": "one"},
        now=now,
    )
    second = outbox.record(
        run_id="run-3",
        kind="failure",
        destination="desktop",
        transition_version=3,
        payload={"error": "two"},
        now=now + timedelta(seconds=30),
    )
    gate_one = outbox.record(
        run_id="run-3",
        kind="approval_required",
        destination="desktop",
        transition_version=4,
        payload={"interaction_id": "one"},
        now=now,
    )
    gate_two = outbox.record(
        run_id="run-3",
        kind="approval_required",
        destination="desktop",
        transition_version=5,
        payload={"interaction_id": "two"},
        now=now,
    )

    assert first == second
    assert gate_one != gate_two
    leased = outbox.lease(
        destination="desktop",
        owner_id="electron",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
        limit=10,
    )
    summary = next(item for item in leased if item["notification_id"] == first)
    assert summary["coalesced_count"] == 2
    assert summary["transition_version"] == 3
    assert summary["payload"]["error"] == "two"


def test_transition_identity_is_idempotent(tmp_path):
    outbox = NotificationOutbox(RunStore(tmp_path / "home"))
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    first = outbox.record(
        run_id="run-4",
        kind="completion",
        destination="desktop",
        transition_version=9,
        payload={},
        now=now,
    )
    duplicate = outbox.record(
        run_id="run-4",
        kind="completion",
        destination="desktop",
        transition_version=9,
        payload={},
        now=now,
    )
    assert duplicate == first
    assert len(outbox.history(run_id="run-4")) == 1


def test_coordinator_reconciles_journal_outbox_crash_gap(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="gap"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="gap",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="gap",
            concurrency_key="gap",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    outbox = NotificationOutbox(store)
    assert outbox.history(run_id=admitted.run_id)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?",
            (admitted.run_id,),
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?",
            (admitted.run_id,),
        )

    repaired = outbox.reconcile_journal()

    assert repaired == 1
    facts = outbox.history(run_id=admitted.run_id)
    assert facts[0]["kind"] == "completion"
    assert facts[0]["state"] == "suppressed"


def test_journal_reconciliation_pages_and_wraps_without_stranding_old_runs(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="paged-gap"))
    run_ids = []
    for index in range(3):
        prepared = store.prepare_run_snapshot(package)
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name="paged-gap",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=f"paged-gap-{index}",
                concurrency_key=f"paged-gap-{index}",
            ),
            immutable_snapshot=prepared,
        )
        RunScheduler(store).advance(admitted.run_id)
        run_ids.append(admitted.run_id)

    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute("DELETE FROM workflow_notification_facts")
        connection.execute("DELETE FROM workflow_notification_outbox")

    assert [outbox.reconcile_journal(limit_runs=1) for _ in range(3)] == [1, 1, 1]
    assert all(outbox.history(run_id=run_id) for run_id in run_ids)

    oldest = run_ids[0]
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?", (oldest,)
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?", (oldest,)
        )

    repaired = [outbox.reconcile_journal(limit_runs=1) for _ in range(4)]
    assert sum(repaired) == 1
    assert outbox.history(run_id=oldest)


def test_notification_history_is_newest_first_and_keyset_paginated(tmp_path):
    outbox = NotificationOutbox(RunStore(tmp_path / "home"))
    now = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
    for version in range(205):
        outbox.record(
            run_id="run-newest",
            kind="completion",
            destination="desktop",
            transition_version=version,
            payload={"version": version},
            delivery_state="suppressed",
            now=now + timedelta(microseconds=version),
        )

    newest = outbox.history(run_id="run-newest", limit=200)

    assert [item["payload"]["version"] for item in newest[:3]] == [204, 203, 202]
    assert newest[-1]["payload"]["version"] == 5
    older = outbox.history(
        run_id="run-newest",
        limit=200,
        before=(newest[-1]["occurred_at"], newest[-1]["transition_key"]),
    )
    assert [item["payload"]["version"] for item in older] == [4, 3, 2, 1, 0]


def test_bounded_repair_reads_one_run_page_and_only_candidate_fact_keys(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="bounded-gap"))
    run_ids = []
    for index in range(3):
        prepared = store.prepare_run_snapshot(package)
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name="bounded-gap",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=f"bounded-gap-{index}",
                concurrency_key=f"bounded-gap-{index}",
            ),
            immutable_snapshot=prepared,
        )
        RunScheduler(store).advance(admitted.run_id)
        run_ids.append(admitted.run_id)
    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute("DELETE FROM workflow_notification_facts")
        connection.execute("DELETE FROM workflow_notification_outbox")
        first = connection.execute(
            "SELECT run_id FROM runs ORDER BY created_at, run_id LIMIT 1"
        ).fetchone()["run_id"]
    byte_budget = (store.run_directory(first) / "events.jsonl").stat().st_size
    journal_reads = []
    original_read = store._read_journal_events

    def traced_read(directory, **kwargs):
        journal_reads.append((directory / "events.jsonl").stat().st_size)
        return original_read(directory, **kwargs)

    candidate_batches = []
    original_existing = outbox._existing_transition_keys

    def traced_existing(connection, transition_keys):
        keys = tuple(transition_keys)
        candidate_batches.append(keys)
        return original_existing(connection, keys)

    monkeypatch.setattr(store, "_read_journal_events", traced_read)
    monkeypatch.setattr(outbox, "_existing_transition_keys", traced_existing)

    repaired = outbox.reconcile_journal(
        limit_runs=2,
        max_journal_bytes=byte_budget,
    )

    assert repaired == 1
    assert len(journal_reads) == 1
    assert sum(journal_reads) <= byte_budget
    assert candidate_batches and candidate_batches[0]
    assert all(first in key for key in candidate_batches[0])


def test_oversized_first_journal_is_repaired(tmp_path, workflow_writer) -> None:
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    identity = CoordinatorIdentity(
        owner_id="oversized-repair",
        host_kind="web",
        host_instance_id="oversized-repair",
        pid=1,
        process_start_time=None,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity, now=now, lease_seconds=60
    )
    assert leadership.is_leader
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="oversized-repair",
            nodes=[{"id": "fail", "bash": "exit 7"}],
        )
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="oversized-repair",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="oversized-repair",
            concurrency_key="oversized-repair",
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(
        store,
        owner_id="coordinator:oversized-repair:1",
        execution_fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    ).advance(admitted.run_id)
    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?",
            (admitted.run_id,),
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?",
            (admitted.run_id,),
        )
    journal_size = (
        (store.run_directory(admitted.run_id) / "events.jsonl").stat().st_size
    )

    repaired = outbox.reconcile_journal(
        limit_runs=1, max_journal_bytes=journal_size - 1
    )

    assert repaired == 1
    attention = outbox.pending_attention(run_id=admitted.run_id)
    assert attention[0]["kind"] == "failure"
    with store._connect() as connection:
        cursor = connection.execute(
            "SELECT cursor_run_id FROM workflow_notification_reconcile_state "
            "WHERE singleton=1"
        ).fetchone()
    assert cursor["cursor_run_id"] == admitted.run_id


def test_torn_tail_repair_is_run_scoped_visible_and_later_verified(
    tmp_path, workflow_writer
) -> None:
    store, run_id = _terminal_background_failure(
        tmp_path, workflow_writer, name="run-scoped-torn-tail"
    )
    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?", (run_id,)
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?", (run_id,)
        )
    journal = store.run_directory(run_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":999')

    assert outbox.reconcile_journal(limit_runs=1) == 0
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert not store.repair_marker.exists()
    assert store._active_run_repair_reasons(run_id) == (
        "notification_reconciliation_unverified",
    )

    store.get_run_status(run_id)
    assert outbox.reconcile_journal(limit_runs=1) == 1
    assert store._active_run_repair_reasons(run_id) == ()
    assert store.list_repair_events()[-1]["outcome"] == "repair_verified"


def test_repair_lock_timeout_retains_cursor_warns_and_retries(
    tmp_path, workflow_writer, caplog
) -> None:
    store, run_id = _terminal_background_failure(
        tmp_path, workflow_writer, name="repair-lock-timeout"
    )
    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?", (run_id,)
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?", (run_id,)
        )

    ready = threading.Event()
    release = threading.Event()

    def hold_run_lock() -> None:
        with workflow_lock(store._run_lock_path(run_id)):
            ready.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_run_lock)
    holder.start()
    assert ready.wait(timeout=1)
    try:
        with caplog.at_level(logging.WARNING):
            assert [outbox.reconcile_journal(limit_runs=1) for _ in range(3)] == [
                0,
                0,
                0,
            ]
        with store._connect() as connection:
            cursor = connection.execute(
                "SELECT cursor_run_id FROM "
                "workflow_notification_reconcile_state WHERE singleton=1"
            ).fetchone()
        assert cursor["cursor_run_id"] is None
        assert (
            sum("cursor retained" in record.message for record in caplog.records) == 1
        )
        assert store._active_run_repair_reasons(run_id) == ()
    finally:
        release.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert outbox.reconcile_journal(limit_runs=1) == 1
    assert outbox.pending_attention(run_id=run_id)[0]["kind"] == "failure"


def test_bounded_repair_has_its_own_cadence(monkeypatch, tmp_path) -> None:
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="notification-cadence",
        ),
        hermes_home=tmp_path,
        notification_repair_seconds=300,
    )
    store = SimpleNamespace(max_journal_bytes=4096)
    calls = []

    def reconcile(_self, **kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(NotificationOutbox, "__init__", lambda self, _store: None)
    monkeypatch.setattr(NotificationOutbox, "reconcile_journal", reconcile)

    service._repair_notifications_if_due(store, now_monotonic=0.0)
    service._repair_notifications_if_due(store, now_monotonic=299.0)
    service._repair_notifications_if_due(store, now_monotonic=300.0)

    assert calls == [
        {"limit_runs": 20, "max_journal_bytes": 4096},
        {"limit_runs": 20, "max_journal_bytes": 4096},
    ]
