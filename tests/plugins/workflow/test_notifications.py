from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import threading
from types import SimpleNamespace
from typing import Mapping

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.notifications import NotificationOutbox, notification_kind
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ExecutionFence
from plugins.workflow.schema import load_workflow, parse_workflow_source_bytes
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowPackageDigest


def _handoff_notification_payload(
    *,
    transition_version: int,
    handoff_id: str = "handoff-legacy",
    generation: int = 7,
    failure_code: str = "submission_indeterminate",
) -> dict[str, object]:
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    return {
        "payload_type": "workflow_transition",
        "workflow": "review",
        "status": "running",
        "event_type": "handoff_indeterminate",
        "node_id": "review",
        "state_version": transition_version,
        "next_actions": ["status", "events", "reconcile", "cancel"],
        "handoff": {
            "handoff_id": handoff_id,
            "generation": generation,
            "endpoint": "hermes://local/reviewer",
            "node_id": "review",
            "phase": "indeterminate",
            "age_seconds": transition_version,
            "last_successful_observation_at": observed.isoformat(),
            "next_action": "reconcile",
            "failure_code": failure_code,
            "commands": {
                "show": f"hermes handoff show {handoff_id}",
                "evidence": f"hermes handoff evidence {handoff_id}",
                "reconcile": f"hermes handoff reconcile {handoff_id}",
            },
        },
    }


def _replace_with_legacy_notification_outbox(store: RunStore) -> None:
    with store._connect() as connection:
        connection.execute("DROP TABLE workflow_notification_outbox")
        connection.executescript(
            """
            CREATE TABLE workflow_notification_outbox (
                notification_id TEXT PRIMARY KEY,
                transition_key TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                destination TEXT NOT NULL,
                transition_version INTEGER NOT NULL,
                coalesced_count INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT,
                dismissed_at TEXT,
                last_error TEXT
            );
            """
        )


def _insert_notification_row(
    store: RunStore,
    *,
    notification_id: str,
    transition_key: str,
    transition_version: int,
    payload: Mapping[str, object],
    state: str,
    created_at: datetime,
    coalesced_count: int = 1,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> None:
    timestamp = created_at.isoformat()
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO workflow_notification_outbox ("
            "notification_id, transition_key, run_id, kind, destination, "
            "transition_version, coalesced_count, payload_json, state, "
            "created_at, updated_at, available_at, lease_owner, "
            "lease_expires_at, delivered_at) "
            "VALUES (?, ?, 'legacy-run', 'reconciliation_required', "
            "'desktop', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notification_id,
                transition_key,
                transition_version,
                coalesced_count,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                state,
                timestamp,
                timestamp,
                timestamp,
                lease_owner,
                lease_expires_at.isoformat() if lease_expires_at else None,
                delivered_at.isoformat() if delivered_at else None,
            ),
        )


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


def test_handoff_input_notification_is_closed_deduplicated_and_restart_safe(
    tmp_path,
) -> None:
    home = tmp_path / "handoff-input-notification"
    projection = {
        "workflow": "review",
        "status": "paused",
        "pending_interaction": {
            "type": "handoff_input",
            "interaction_id": "a" * 64,
            "node_id": "review",
            "remote_request_id": "remote-approval-1",
            "remote_choices": ["once", "deny"],
            "prompt": "Bearer secret prompt /Users/private/result.txt",
        },
    }
    kind = notification_kind("handoff_input_required", projection, node_id="review")

    assert kind == "approval_required"
    outbox = NotificationOutbox(RunStore(home))
    payload = {
        **projection,
        "event_type": "handoff_input_required",
        "node_id": "review",
        "interaction": projection["pending_interaction"],
    }
    first = outbox.record(
        run_id="handoff-input-run",
        kind=kind,
        destination="desktop",
        transition_version=4,
        payload=payload,
    )
    duplicate = outbox.record(
        run_id="handoff-input-run",
        kind=kind,
        destination="desktop",
        transition_version=4,
        payload=payload,
    )

    assert duplicate == first
    restarted = NotificationOutbox(RunStore(home))
    item = restarted.pending_attention(run_id="handoff-input-run")[0]
    assert item["payload"]["event_type"] == "handoff_input_required"
    assert item["payload"]["node_id"] == "review"
    assert item["payload"]["interaction"] == {
        "type": "handoff_input",
        "interaction_id": "a" * 64,
    }
    assert "remote-approval-1" not in json.dumps(item, sort_keys=True)


def test_handoff_attention_payload_is_actionable_and_closed(tmp_path) -> None:
    store = RunStore(tmp_path / "handoff-attention")
    outbox = NotificationOutbox(store)
    canary = "Bearer secret prompt body /Users/private/task.txt"
    payload = {
        "workflow": "review",
        "status": "running",
        "event_type": "handoff_indeterminate",
        "node_id": "review",
        "handoff": {
            "handoff_id": "handoff-1",
            "generation": 1,
            "endpoint": "hermes://local/reviewer",
            "phase": "indeterminate",
            "age_seconds": 90,
            "last_successful_observation_at": "2026-09-01T12:00:00+00:00",
            "next_action": "reconcile",
            "failure_code": "cancellation_indeterminate",
        },
        "prompt": canary,
        "raw_error": canary,
        "headers": {"Authorization": canary},
    }

    notification_id = outbox.record(
        run_id="handoff-run",
        kind="reconciliation_required",
        destination="desktop",
        transition_version=4,
        payload=payload,
    )

    item = outbox.pending_attention(run_id="handoff-run")[0]
    assert item["notification_id"] == notification_id
    assert item["payload"]["handoff"] == {
        "handoff_id": "handoff-1",
        "generation": 1,
        "endpoint": "hermes://local/reviewer",
        "node_id": "review",
        "phase": "indeterminate",
        "age_seconds": 90,
        "last_successful_observation_at": "2026-09-01T12:00:00+00:00",
        "next_action": "reconcile",
        "failure_code": "cancellation_indeterminate",
        "commands": {
            "show": "hermes handoff show handoff-1",
            "evidence": "hermes handoff evidence handoff-1",
            "reconcile": "hermes handoff reconcile handoff-1",
        },
    }
    assert canary not in json.dumps(item, sort_keys=True)
    assert notification_kind("handoff_active", payload) is None
    assert notification_kind("handoff_indeterminate", payload) == (
        "reconciliation_required"
    )

    newer_id = outbox.record(
        run_id="handoff-run",
        kind="reconciliation_required",
        destination="desktop",
        transition_version=5,
        payload={
            **payload,
            "handoff": {**payload["handoff"], "generation": 2},
        },
    )
    assert outbox.clear_handoff_attention(
        run_id="handoff-run",
        node_id="review",
        handoff_id="handoff-1",
        generation=1,
    ) == 1
    assert [
        item["notification_id"]
        for item in outbox.pending_attention(run_id="handoff-run")
    ] == [newer_id]
    assert outbox.history(run_id="handoff-run")[0]["state"] == "pending"


def test_distinct_handoff_failures_remain_independently_actionable(tmp_path) -> None:
    outbox = NotificationOutbox(RunStore(tmp_path / "distinct-handoff-failures"))
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    def record(version: int, handoff_id: str) -> str:
        payload = _handoff_notification_payload(
            transition_version=version,
            handoff_id=handoff_id,
            generation=1,
            failure_code="remote_failed",
        )
        payload["event_type"] = "handoff_failed"
        payload["handoff"] = {
            **payload["handoff"],
            "phase": "failed",
            "next_action": "inspect",
        }
        return outbox.record(
            run_id="two-failed-handoffs",
            kind="failure",
            destination="desktop",
            transition_version=version,
            payload=payload,
            now=observed + timedelta(seconds=version),
        )

    first = record(1, "handoff-first")
    second = record(2, "handoff-second")

    assert first != second
    assert [
        item["payload"]["handoff"]["handoff_id"]
        for item in outbox.pending_attention(run_id="two-failed-handoffs")
    ] == ["handoff-first", "handoff-second"]
    assert outbox.clear_handoff_attention(
        run_id="two-failed-handoffs",
        node_id="review",
        handoff_id="handoff-first",
        generation=1,
    ) == 1
    assert [
        item["notification_id"]
        for item in outbox.pending_attention(run_id="two-failed-handoffs")
    ] == [second]
    assert outbox.clear_handoff_attention(
        run_id="two-failed-handoffs",
        node_id="review",
        handoff_id="handoff-second",
        generation=1,
    ) == 1
    assert outbox.pending_attention(run_id="two-failed-handoffs") == ()


def test_same_phase_handoff_observation_only_notifies_when_indeterminate() -> None:
    projection = {
        "nodes": {
            "review": {
                "handoff": {
                    "last_observed_phase": "active",
                },
            },
        },
    }

    assert notification_kind(
        "handoff_observed",
        projection,
        node_id="review",
    ) is None
    projection["nodes"]["review"]["handoff"]["last_observed_phase"] = (
        "indeterminate"
    )
    assert notification_kind(
        "handoff_observed",
        projection,
        node_id="review",
    ) == "reconciliation_required"


def test_handoff_reconciliation_coalesces_by_exact_generation_after_ack(
    tmp_path,
) -> None:
    outbox = NotificationOutbox(RunStore(tmp_path / "handoff-coalescing"))
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    def record(version: int, generation: int) -> str:
        return outbox.record(
            run_id="handoff-coalesce-run",
            kind="reconciliation_required",
            destination="desktop",
            transition_version=version,
            payload={
                "workflow": "review",
                "status": "running",
                "event_type": "handoff_indeterminate",
                "node_id": "review",
                "handoff": {
                    "handoff_id": "handoff-coalesce",
                    "generation": generation,
                    "endpoint": "hermes://local/reviewer",
                    "phase": "indeterminate",
                    "age_seconds": version,
                    "last_successful_observation_at": observed.isoformat(),
                    "next_action": "reconcile",
                    "failure_code": "cancellation_indeterminate",
                },
            },
            now=observed + timedelta(minutes=version),
        )

    first = record(4, 1)
    leased = outbox.lease(
        destination="desktop",
        owner_id="electron",
        now=observed + timedelta(minutes=4),
        lease_seconds=30,
    )
    assert [item["notification_id"] for item in leased] == [first]
    assert outbox.ack(
        first,
        owner_id="electron",
        now=observed + timedelta(minutes=4),
    )

    repeated = record(5, 1)
    newer_generation = record(6, 2)

    assert repeated == first
    assert newer_generation != first
    assert [
        item["notification_id"]
        for item in outbox.pending_attention(run_id="handoff-coalesce-run")
    ] == [newer_generation]


def test_handoff_reconciliation_identity_survives_more_than_512_newer_rows(
    tmp_path,
) -> None:
    home = tmp_path / "handoff-coalescing-index"
    outbox = NotificationOutbox(RunStore(home))
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    def record(version: int, handoff_id: str) -> str:
        return outbox.record(
            run_id="handoff-index-run",
            kind="reconciliation_required",
            destination="desktop",
            transition_version=version,
            payload={
                "workflow": "review",
                "status": "running",
                "event_type": "handoff_indeterminate",
                "node_id": "review",
                "handoff": {
                    "handoff_id": handoff_id,
                    "generation": 1,
                    "endpoint": "hermes://local/reviewer",
                    "phase": "indeterminate",
                    "age_seconds": version,
                    "last_successful_observation_at": observed.isoformat(),
                    "next_action": "reconcile",
                    "failure_code": "submission_indeterminate",
                },
            },
            now=observed + timedelta(seconds=version),
        )

    first = record(1, "handoff-original")
    leased = outbox.lease(
        destination="desktop",
        owner_id="electron",
        now=observed + timedelta(seconds=1),
        lease_seconds=30,
    )
    assert [item["notification_id"] for item in leased] == [first]
    assert outbox.ack(first, owner_id="electron", now=observed)
    for version in range(2, 515):
        record(version, f"handoff-intervening-{version}")

    repeated = NotificationOutbox(RunStore(home)).record(
        run_id="handoff-index-run",
        kind="reconciliation_required",
        destination="desktop",
        transition_version=515,
        payload={
            "workflow": "review",
            "status": "running",
            "event_type": "handoff_observed",
            "node_id": "review",
            "handoff": {
                "handoff_id": "handoff-original",
                "generation": 1,
                "endpoint": "hermes://local/reviewer",
                "phase": "indeterminate",
                "age_seconds": 515,
                "last_successful_observation_at": observed.isoformat(),
                "next_action": "reconcile",
                "failure_code": "cancellation_indeterminate",
            },
        },
        now=observed + timedelta(seconds=515),
    )

    assert repeated == first


def test_notification_schema_migrates_and_indexes_legacy_handoff_identity(
    tmp_path,
) -> None:
    home = tmp_path / "legacy-handoff-notifications"
    store = RunStore(home)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    legacy_payload = json.dumps(
        {
            "payload_type": "workflow_transition",
            "workflow": "review",
            "status": "running",
            "event_type": "handoff_indeterminate",
            "node_id": "review",
            "state_version": 1,
            "next_actions": ["status", "events", "reconcile", "cancel"],
            "handoff": {
                "handoff_id": "handoff-legacy",
                "generation": 7,
                "endpoint": "hermes://local/reviewer",
                "node_id": "review",
                "phase": "indeterminate",
                "age_seconds": 1,
                "last_successful_observation_at": observed.isoformat(),
                "next_action": "reconcile",
                "failure_code": "submission_indeterminate",
                "commands": {
                    "show": "hermes handoff show handoff-legacy",
                    "evidence": "hermes handoff evidence handoff-legacy",
                    "reconcile": "hermes handoff reconcile handoff-legacy",
                },
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with store._connect() as connection:
        connection.execute("DROP TABLE workflow_notification_outbox")
        connection.executescript(
            """
            CREATE TABLE workflow_notification_outbox (
                notification_id TEXT PRIMARY KEY,
                transition_key TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                destination TEXT NOT NULL,
                transition_version INTEGER NOT NULL,
                coalesced_count INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT,
                dismissed_at TEXT,
                last_error TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO workflow_notification_outbox ("
            "notification_id, transition_key, run_id, kind, destination, "
            "transition_version, payload_json, state, created_at, updated_at, "
            "available_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-notification",
                "legacy-transition",
                "legacy-run",
                "reconciliation_required",
                "desktop",
                1,
                legacy_payload,
                "delivered",
                observed.isoformat(),
                observed.isoformat(),
                observed.isoformat(),
            ),
        )

    store = RunStore(home)
    NotificationOutbox(store)
    with store._connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(workflow_notification_outbox)"
            )
        }
        indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(workflow_notification_outbox)"
            )
        }
        identity = connection.execute(
            "SELECT handoff_node_id, handoff_id, handoff_generation "
            "FROM workflow_notification_outbox WHERE notification_id=?",
            ("legacy-notification",),
        ).fetchone()

    assert {
        "handoff_node_id",
        "handoff_id",
        "handoff_generation",
    } <= columns
    assert "workflow_notification_handoff_identity" in indexes
    assert tuple(identity) == ("review", "handoff-legacy", 7)
    repeated = NotificationOutbox(RunStore(home)).record(
        run_id="legacy-run",
        kind="reconciliation_required",
        destination="desktop",
        transition_version=2,
        payload=json.loads(legacy_payload),
        now=observed + timedelta(seconds=1),
    )
    assert repeated == "legacy-notification"


def test_notification_schema_serializes_concurrent_legacy_upgrade(tmp_path) -> None:
    home = tmp_path / "concurrent-notification-upgrade"
    store = RunStore(home)
    _replace_with_legacy_notification_outbox(store)
    ready = threading.Barrier(12)

    def initialize(_index: int) -> None:
        ready.wait()
        NotificationOutbox(store)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(initialize, index) for index in range(12)]
        errors = [future.exception() for future in futures]

    assert errors == [None] * 12
    with store._connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(workflow_notification_outbox)"
            )
        }
    assert {
        "handoff_node_id",
        "handoff_id",
        "handoff_generation",
    } <= columns


def test_handoff_identity_upsert_is_singleton_under_concurrent_writers(
    tmp_path,
) -> None:
    home = tmp_path / "concurrent-handoff-upsert"
    store = RunStore(home)
    ready = threading.Barrier(12)

    def record(index: int) -> str:
        ready.wait()
        version = index + 1
        return NotificationOutbox(store).record(
            run_id="concurrent-run",
            kind="reconciliation_required",
            destination="desktop",
            transition_version=version,
            payload=_handoff_notification_payload(
                transition_version=version,
                handoff_id="handoff-concurrent",
                generation=3,
            ),
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        notification_ids = list(pool.map(record, range(12)))

    assert len(set(notification_ids)) == 1
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_outbox "
            "WHERE run_id='concurrent-run'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_facts "
            "WHERE run_id='concurrent-run'"
        ).fetchone()[0] == 12


def test_notification_schema_backfills_null_identity_after_columns_exist(
    tmp_path,
) -> None:
    home = tmp_path / "mixed-version-notification"
    store = RunStore(home)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    _insert_notification_row(
        store,
        notification_id="mixed-version-notification",
        transition_key="mixed-version-transition",
        transition_version=3,
        payload=_handoff_notification_payload(transition_version=3),
        state="delivered",
        created_at=observed,
    )

    NotificationOutbox(RunStore(home))

    with store._connect() as connection:
        identity = connection.execute(
            "SELECT handoff_node_id, handoff_id, handoff_generation "
            "FROM workflow_notification_outbox WHERE notification_id=?",
            ("mixed-version-notification",),
        ).fetchone()
    assert tuple(identity) == ("review", "handoff-legacy", 7)


def test_malformed_delivered_legacy_row_cannot_suppress_valid_attention(
    tmp_path,
) -> None:
    home = tmp_path / "malformed-legacy-notification"
    store = RunStore(home)
    _replace_with_legacy_notification_outbox(store)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    malformed = {
        **_handoff_notification_payload(transition_version=1),
        "unsafe_private_body": "Bearer secret /Users/private/task.txt",
    }
    _insert_notification_row(
        store,
        notification_id="malformed-delivered",
        transition_key="malformed-transition",
        transition_version=1,
        payload=malformed,
        state="delivered",
        created_at=observed,
    )

    outbox = NotificationOutbox(RunStore(home))
    valid_id = outbox.record(
        run_id="legacy-run",
        kind="reconciliation_required",
        destination="desktop",
        transition_version=2,
        payload=_handoff_notification_payload(transition_version=2),
        now=observed + timedelta(seconds=1),
    )

    assert valid_id != "malformed-delivered"
    attention = outbox.pending_attention(run_id="legacy-run")
    assert [item["notification_id"] for item in attention] == [valid_id]
    assert "unsafe_private_body" not in json.dumps(attention)
    with store._connect() as connection:
        malformed_identity = connection.execute(
            "SELECT handoff_node_id, handoff_id, handoff_generation "
            "FROM workflow_notification_outbox WHERE notification_id=?",
            ("malformed-delivered",),
        ).fetchone()
    assert tuple(malformed_identity) == (None, None, None)


def test_notification_migration_consolidates_duplicate_identity_and_facts(
    tmp_path,
) -> None:
    home = tmp_path / "duplicate-handoff-notification"
    store = RunStore(home)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    with store._connect() as connection:
        connection.execute("DROP INDEX workflow_notification_handoff_identity")
        connection.execute(
            "CREATE INDEX workflow_notification_handoff_identity "
            "ON workflow_notification_outbox("
            "run_id, kind, destination, handoff_node_id, handoff_id, "
            "handoff_generation) WHERE handoff_node_id IS NOT NULL "
            "AND handoff_id IS NOT NULL AND handoff_generation IS NOT NULL"
        )
    _insert_notification_row(
        store,
        notification_id="acknowledged-original",
        transition_key="duplicate-transition-1",
        transition_version=1,
        payload=_handoff_notification_payload(transition_version=1),
        state="leased",
        created_at=observed,
        lease_owner="stale-client",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    _insert_notification_row(
        store,
        notification_id="later-duplicate",
        transition_key="duplicate-transition-2",
        transition_version=2,
        payload=_handoff_notification_payload(
            transition_version=2,
            failure_code="cancellation_indeterminate",
        ),
        state="delivered",
        created_at=observed + timedelta(seconds=1),
        coalesced_count=2,
        delivered_at=observed + timedelta(seconds=1),
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_notification_outbox SET handoff_node_id='review', "
            "handoff_id='handoff-legacy', handoff_generation=7"
        )
        connection.executemany(
            "INSERT INTO workflow_notification_facts ("
            "transition_key, notification_id, run_id, kind, destination, "
            "transition_version, payload_json, occurred_at) "
            "SELECT transition_key, notification_id, run_id, kind, destination, "
            "transition_version, payload_json, ? "
            "FROM workflow_notification_outbox WHERE notification_id=?",
            (
                (observed.isoformat(), "acknowledged-original"),
                (
                    (observed + timedelta(seconds=1)).isoformat(),
                    "later-duplicate",
                ),
            ),
        )

    outbox = NotificationOutbox(RunStore(home))

    with store._connect() as connection:
        rows = connection.execute(
            "SELECT notification_id, transition_key, transition_version, "
            "coalesced_count, payload_json, state "
            "FROM workflow_notification_outbox WHERE run_id='legacy-run'"
        ).fetchall()
        fact_ids = [
            row["notification_id"]
            for row in connection.execute(
                "SELECT notification_id FROM workflow_notification_facts "
                "WHERE run_id='legacy-run' ORDER BY transition_version"
            )
        ]
        identity_index = next(
            row
            for row in connection.execute(
                "PRAGMA index_list(workflow_notification_outbox)"
            )
            if row["name"] == "workflow_notification_handoff_identity"
        )

    assert len(rows) == 1
    assert rows[0]["notification_id"] == "later-duplicate"
    assert rows[0]["transition_key"] == "duplicate-transition-2"
    assert rows[0]["transition_version"] == 2
    assert rows[0]["coalesced_count"] == 3
    assert rows[0]["state"] == "delivered"
    assert json.loads(rows[0]["payload_json"])["handoff"]["failure_code"] == (
        "cancellation_indeterminate"
    )
    assert fact_ids == ["later-duplicate", "later-duplicate"]
    assert identity_index["unique"] == 1
    assert outbox.pending_attention(run_id="legacy-run") == ()

    NotificationOutbox(RunStore(home))
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_notification_outbox "
            "WHERE run_id='legacy-run'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("completion", "expected_state"),
    (("ack", "delivered"), ("fail", "pending")),
)
def test_notification_migration_preserves_active_lease_id_for_client_completion(
    tmp_path,
    completion: str,
    expected_state: str,
) -> None:
    home = tmp_path / f"active-lease-{completion}"
    store = RunStore(home)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    with store._connect() as connection:
        connection.execute("DROP INDEX workflow_notification_handoff_identity")
        connection.execute(
            "CREATE INDEX workflow_notification_handoff_identity "
            "ON workflow_notification_outbox("
            "run_id, kind, destination, handoff_node_id, handoff_id, "
            "handoff_generation) WHERE handoff_node_id IS NOT NULL "
            "AND handoff_id IS NOT NULL AND handoff_generation IS NOT NULL"
        )
    _insert_notification_row(
        store,
        notification_id="earlier-pending",
        transition_key="active-lease-transition-1",
        transition_version=1,
        payload=_handoff_notification_payload(transition_version=1),
        state="pending",
        created_at=observed,
    )
    _insert_notification_row(
        store,
        notification_id="client-issued-lease",
        transition_key="active-lease-transition-2",
        transition_version=2,
        payload=_handoff_notification_payload(transition_version=2),
        state="leased",
        created_at=observed + timedelta(seconds=1),
        lease_owner="desktop-client",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_notification_outbox SET handoff_node_id='review', "
            "handoff_id='handoff-legacy', handoff_generation=7"
        )
        connection.executemany(
            "INSERT INTO workflow_notification_facts ("
            "transition_key, notification_id, run_id, kind, destination, "
            "transition_version, payload_json, occurred_at) "
            "SELECT transition_key, notification_id, run_id, kind, destination, "
            "transition_version, payload_json, ? "
            "FROM workflow_notification_outbox WHERE notification_id=?",
            (
                (observed.isoformat(), "earlier-pending"),
                (
                    (observed + timedelta(seconds=1)).isoformat(),
                    "client-issued-lease",
                ),
            ),
        )

    outbox = NotificationOutbox(RunStore(home))

    if completion == "ack":
        completed = outbox.ack(
            "client-issued-lease",
            owner_id="desktop-client",
            now=observed + timedelta(seconds=2),
        )
    else:
        completed = outbox.fail(
            "client-issued-lease",
            owner_id="desktop-client",
            error="retryable_delivery_failure",
            now=observed + timedelta(seconds=2),
        )

    assert completed is True
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT notification_id, state FROM workflow_notification_outbox "
            "WHERE run_id='legacy-run'"
        ).fetchall()
        fact_ids = [
            row["notification_id"]
            for row in connection.execute(
                "SELECT notification_id FROM workflow_notification_facts "
                "WHERE run_id='legacy-run' ORDER BY transition_version"
            )
        ]
    assert [(row["notification_id"], row["state"]) for row in rows] == [
        ("client-issued-lease", expected_state)
    ]
    assert fact_ids == ["client-issued-lease", "client-issued-lease"]


def test_notification_migration_expired_lease_does_not_outrank_earliest_row(
    tmp_path,
) -> None:
    home = tmp_path / "expired-lease"
    store = RunStore(home)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    with store._connect() as connection:
        connection.execute("DROP INDEX workflow_notification_handoff_identity")
        connection.execute(
            "CREATE INDEX workflow_notification_handoff_identity "
            "ON workflow_notification_outbox("
            "run_id, kind, destination, handoff_node_id, handoff_id, "
            "handoff_generation) WHERE handoff_node_id IS NOT NULL "
            "AND handoff_id IS NOT NULL AND handoff_generation IS NOT NULL"
        )
    _insert_notification_row(
        store,
        notification_id="earliest-pending",
        transition_key="expired-lease-transition-1",
        transition_version=1,
        payload=_handoff_notification_payload(transition_version=1),
        state="pending",
        created_at=observed,
    )
    _insert_notification_row(
        store,
        notification_id="expired-lease",
        transition_key="expired-lease-transition-2",
        transition_version=2,
        payload=_handoff_notification_payload(transition_version=2),
        state="leased",
        created_at=observed + timedelta(seconds=1),
        lease_owner="gone-client",
        lease_expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE workflow_notification_outbox SET handoff_node_id='review', "
            "handoff_id='handoff-legacy', handoff_generation=7"
        )

    NotificationOutbox(RunStore(home))

    with store._connect() as connection:
        row = connection.execute(
            "SELECT notification_id, state FROM workflow_notification_outbox "
            "WHERE run_id='legacy-run'"
        ).fetchone()
    assert tuple(row) == ("earliest-pending", "pending")


def test_journal_reconciliation_clears_deferred_terminal_handoff_attention(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "handoff-terminal-repair")
    package = load_workflow(
        workflow_writer(
            tmp_path / "handoff-package",
            name="handoff-terminal-repair",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="handoff-terminal-repair",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "start", "handoff-worker")
    assert claim is not None
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    assert store.begin_handoff_wait(
        claim,
        handoff_id="handoff-terminal-repair",
        generation=1,
        observed_version=3,
        observed_phase="active",
        next_observation_at=observed,
        deadline_at=observed + timedelta(hours=1),
    )
    outbox = NotificationOutbox(store)
    outbox.record(
        run_id=admitted.run_id,
        kind="reconciliation_required",
        destination="desktop",
        transition_version=3,
        payload={
            "workflow": package.definition.name,
            "status": "running",
            "event_type": "handoff_indeterminate",
            "node_id": "start",
            "handoff": {
                "handoff_id": "handoff-terminal-repair",
                "generation": 1,
                "endpoint": "hermes://local/reviewer",
                "phase": "indeterminate",
                "age_seconds": 1,
                "last_successful_observation_at": observed.isoformat(),
                "next_action": "reconcile",
                "failure_code": "cancellation_indeterminate",
            },
        },
        now=observed,
    )
    identity = CoordinatorIdentity(
        owner_id="terminal-repair-owner",
        host_kind="web",
        host_instance_id="terminal-repair-host",
        pid=1,
        process_start_time=None,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert leadership.is_leader
    assert store.refresh_handoff_wait(
        admitted.run_id,
        "start",
        handoff_id="handoff-terminal-repair",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="succeeded",
        next_observation_at=observed,
        fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    )

    assert len(outbox.pending_attention(run_id=admitted.run_id)) == 1
    assert outbox.reconcile_run(admitted.run_id) >= 1
    assert outbox.pending_attention(run_id=admitted.run_id) == ()


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


def test_notification_authority_values_are_value_free_across_durable_delivery(
    tmp_path,
):
    """Endpoint and registration identities never cross the notification boundary."""
    store = RunStore(tmp_path / "notification-private-authority")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
    endpoint_identity = "a1" * 32
    nested_endpoint_identity = "b2" * 32
    registration_identity = "c3" * 32
    nested_registration_identity = "d4" * 32
    authority_field_names = [
        "endpoint_sha256",
        "registration_provenance_digest",
    ]

    notification_id = outbox.record(
        run_id="private-authority-run",
        kind="failure",
        destination="desktop",
        transition_version=1,
        payload={
            "code": "provider_capability_drift",
            "mismatched_fields": authority_field_names,
            "expected_runtime_identity": {
                "endpoint_sha256": endpoint_identity,
                "registration_provenance_digest": registration_identity,
                "nested": [
                    {"expectedEndpointSha256": nested_endpoint_identity},
                    {
                        "liveRegistrationProvenanceDigest": (
                            nested_registration_identity
                        )
                    },
                ],
            },
            "detail": (
                f"endpoint changed from {endpoint_identity}; registration "
                f"changed from {registration_identity}"
            ),
        },
        now=now,
    )

    leased = outbox.lease(
        destination="desktop",
        owner_id="notification-owner",
        now=now,
        lease_seconds=30,
    )
    history = outbox.history(run_id="private-authority-run")
    with store._connect() as connection:
        durable = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT workflow_notification_outbox.payload_json, "
                "workflow_notification_facts.payload_json "
                "FROM workflow_notification_outbox LEFT JOIN "
                "workflow_notification_facts USING(notification_id) "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
            for value in row
        )

    public_payloads = json.dumps(
        {"leased": leased, "history": history, "durable": durable},
        sort_keys=True,
    )
    for private_value in (
        endpoint_identity,
        nested_endpoint_identity,
        registration_identity,
        nested_registration_identity,
    ):
        assert private_value not in public_payloads
    assert leased[0]["payload"]["code"] == "provider_capability_drift"
    assert leased[0]["payload"]["mismatched_fields"] == authority_field_names
    assert history[0]["payload"]["mismatched_fields"] == authority_field_names


def test_notification_authority_containers_scrub_raw_echoes_before_sanitizing(
    tmp_path,
):
    """Raw authority leaves stay private even under secret or cyclic containers."""
    store = RunStore(tmp_path / "notification-private-authority-containers")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 8, 13, tzinfo=timezone.utc)
    endpoint_identity = "e5" * 32
    nested_endpoint_identity = "f6" * 32
    registration_identity = "a7" * 32
    nested_registration_identity = "b8" * 32
    authority_field_names = [
        "endpoint_sha256",
        "registration_provenance_digest",
    ]
    endpoint_container = {
        "primary": endpoint_identity,
        "nested": [nested_endpoint_identity],
    }
    endpoint_container["cycle"] = endpoint_container

    notification_id = outbox.record(
        run_id="private-authority-container-run",
        kind="failure",
        destination="desktop",
        transition_version=1,
        payload={
            "code": "provider_capability_drift",
            "status": "failed",
            "interaction": {
                "type": "reconcile",
                "interaction_id": "authority-container-interaction",
            },
            "mismatched_fields": authority_field_names,
            "credentialEndpointSha256": endpoint_container,
            "registration_provenance_digest": [
                registration_identity,
                {"nested": nested_registration_identity},
            ],
            "detail": (
                f"endpoint values {endpoint_identity} {nested_endpoint_identity}; "
                f"registration value {registration_identity}"
            ),
            "messages": [
                f"registration nested {nested_registration_identity}",
                {"echo": endpoint_identity},
            ],
        },
        now=now,
    )

    leased = outbox.lease(
        destination="desktop",
        owner_id="notification-owner",
        now=now,
        lease_seconds=30,
    )
    history = outbox.history(run_id="private-authority-container-run")
    with store._connect() as connection:
        outbox_payloads = [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM workflow_notification_outbox "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
        ]
        fact_payloads = [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM workflow_notification_facts "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
        ]

    public_payloads = json.dumps(
        {
            "outbox": outbox_payloads,
            "facts": fact_payloads,
            "leased": leased,
            "history": history,
        },
        sort_keys=True,
    )
    for private_value in (
        endpoint_identity,
        nested_endpoint_identity,
        registration_identity,
        nested_registration_identity,
    ):
        assert private_value not in public_payloads
    assert outbox_payloads and fact_payloads
    expected_actions = ["status", "events", "resume", "retry", "abandon"]
    for item in (leased[0], history[0]):
        assert item["payload"]["code"] == "provider_capability_drift"
        assert item["payload"]["mismatched_fields"] == authority_field_names
        assert item["payload"]["next_actions"] == expected_actions


@pytest.mark.parametrize(
    "case",
    ("child_201", "depth_13", "unsupported_set", "deep_first_alias"),
)
def test_incomplete_notification_authority_collection_fails_closed(
    tmp_path,
    case,
):
    """Unprovable authority collection retains only recovery-safe metadata."""
    private_value = {
        "child_201": "c1" * 32,
        "depth_13": "d2" * 32,
        "unsupported_set": "e3" * 32,
        "deep_first_alias": "f4" * 32,
    }[case]
    if case == "child_201":
        private_key = "credentialEndpointSha256"
        private_container = [f"public-{index}" for index in range(200)] + [
            private_value
        ]
        mismatch = "endpoint_sha256"
    elif case == "depth_13":
        private_key = "expectedRegistrationProvenanceDigest"
        private_container = private_value
        for _ in range(12):
            private_container = [private_container]
        mismatch = "registration_provenance_digest"
    elif case == "unsupported_set":
        private_key = "expectedEndpointSha256"
        private_container = {private_value}
        mismatch = "endpoint_sha256"
    else:
        private_key = "expectedRegistrationProvenanceDigest"
        shared = {"leaf": private_value}
        deep = shared
        for _ in range(10):
            deep = [deep]
        private_container = {"shallow": shared, "deep": deep}
        mismatch = "registration_provenance_digest"

    store = RunStore(tmp_path / f"notification-incomplete-{case}")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 8, 14, tzinfo=timezone.utc)
    arbitrary_sibling = f"arbitrary-sibling-{case}"
    notification_id = outbox.record(
        run_id=f"incomplete-authority-{case}",
        kind="failure",
        destination="desktop",
        transition_version=1,
        payload={
            "code": "provider_capability_drift",
            "status": "failed",
            "interaction": {
                "type": "reconcile",
                "interaction_id": f"interaction-{case}",
            },
            "mismatched_fields": [mismatch],
            private_key: private_container,
            "detail": f"{arbitrary_sibling} {private_value}",
            "messages": [private_value, arbitrary_sibling],
        },
        now=now,
    )

    leased = outbox.lease(
        destination="desktop",
        owner_id="notification-owner",
        now=now,
        lease_seconds=30,
    )
    history = outbox.history(run_id=f"incomplete-authority-{case}")
    with store._connect() as connection:
        outbox_payloads = [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM workflow_notification_outbox "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
        ]
        fact_payloads = [
            row["payload_json"]
            for row in connection.execute(
                "SELECT payload_json FROM workflow_notification_facts "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchall()
        ]

    rendered = json.dumps(
        {
            "outbox": outbox_payloads,
            "facts": fact_payloads,
            "leased": leased,
            "history": history,
        },
        sort_keys=True,
    )
    assert private_value not in rendered
    assert arbitrary_sibling not in rendered
    assert outbox_payloads and fact_payloads
    expected_actions = ["status", "events", "resume", "retry", "abandon"]
    for item in (leased[0], history[0]):
        projected = item["payload"]
        assert set(projected) == {
            "payload_type",
            "code",
            "status",
            "interaction",
            "mismatched_fields",
            "state_version",
            "next_actions",
        }
        assert projected["code"] == "provider_capability_drift"
        assert projected["payload_type"] == "workflow_transition"
        assert projected["interaction"] == {
            "type": "reconcile",
            "interaction_id": f"interaction-{case}",
        }
        assert projected["mismatched_fields"] == [mismatch]
        assert projected["next_actions"] == expected_actions


def test_notification_failures_preserve_allowlisted_stable_delivery_reason(tmp_path):
    store = RunStore(tmp_path / "stable-delivery-reason")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    notification_ids = []
    for version in (1, 2):
        notification_ids.append(
            outbox.record(
                run_id="stable-delivery-reason-run",
                kind="completion",
                destination="desktop",
                transition_version=version,
                payload={"status": "succeeded"},
                now=now,
            )
        )
    leased = outbox.lease(
        destination="desktop",
        owner_id="stable-delivery-owner",
        now=now,
        lease_seconds=30,
        limit=2,
    )
    assert {item["notification_id"] for item in leased} == set(notification_ids)

    assert outbox.fail(
        notification_ids[0],
        owner_id="stable-delivery-owner",
        error="delivery_store_unavailable",
        now=now,
    )
    assert outbox.terminal_fail(
        notification_ids[1],
        owner_id="stable-delivery-owner",
        error="delivery_store_unavailable",
        now=now,
    )

    history = outbox.history(run_id="stable-delivery-reason-run")
    deliveries = {
        item["notification_id"]: item for item in history if item["last_error"]
    }
    assert deliveries[notification_ids[0]]["last_error"] == (
        "delivery_store_unavailable"
    )
    assert deliveries[notification_ids[1]]["last_error"] == (
        "delivery_store_unavailable"
    )
    dead_letter = next(
        item
        for item in history
        if item["notification_id"] == notification_ids[1]
        and item["payload"].get("decision") == "terminal_dead_letter"
    )
    assert dead_letter["payload"]["error"] == "delivery_store_unavailable"


def test_notification_failures_normalize_free_form_delivery_detail(tmp_path):
    store = RunStore(tmp_path / "free-form-delivery-detail")
    outbox = NotificationOutbox(store)
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    canary = "private adapter session /path history"
    notification_ids = []
    for version in (1, 2):
        notification_ids.append(
            outbox.record(
                run_id="free-form-delivery-detail-run",
                kind="completion",
                destination="desktop",
                transition_version=version,
                payload={"status": "succeeded"},
                now=now,
            )
        )
    outbox.lease(
        destination="desktop",
        owner_id="free-form-delivery-owner",
        now=now,
        lease_seconds=30,
        limit=2,
    )

    assert outbox.fail(
        notification_ids[0],
        owner_id="free-form-delivery-owner",
        error=canary,
        now=now,
    )
    assert outbox.terminal_fail(
        notification_ids[1],
        owner_id="free-form-delivery-owner",
        error=canary,
        now=now,
    )

    history = outbox.history(run_id="free-form-delivery-detail-run")
    assert canary not in json.dumps(history, sort_keys=True)
    deliveries = {
        item["notification_id"]: item for item in history if item["last_error"]
    }
    assert {
        deliveries[notification_id]["last_error"]
        for notification_id in notification_ids
    } == {"notification delivery failed"}
    dead_letter = next(
        item
        for item in history
        if item["notification_id"] == notification_ids[1]
        and item["payload"].get("decision") == "terminal_dead_letter"
    )
    assert dead_letter["payload"]["error"] == "notification delivery failed"


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
        payload={"status": "failed", "event_type": "run_failed"},
        now=now,
    )
    second = outbox.record(
        run_id="run-3",
        kind="failure",
        destination="desktop",
        transition_version=3,
        payload={"status": "failed", "event_type": "run_failed"},
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
    assert summary["payload"]["payload_type"] == "workflow_transition"
    assert summary["payload"]["status"] == "failed"


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


def test_repaired_phase4_signal_notification_matches_primary_payload(
    tmp_path,
    workflow_writer,
) -> None:
    workflow_path = workflow_writer(
        tmp_path / "phase4-signal/workflows",
        name="phase4-notification-repair",
        filename="phase4-notification-repair.yaml",
        interactive=True,
        nodes=[{
            "id": "refine",
            "loop": {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": 3,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
        }],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    store = RunStore(tmp_path / "phase4-home")
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="phase4-notification-repair",
            concurrency_key="phase4-notification-repair",
        ),
        immutable_snapshot=prepared,
    )

    class SignalRunner:
        def run(self, request, **_kwargs):
            return PluginAgentRunResult(
                final_response="draft <promise>DONE</promise>",
                session_id="notification-repair-session",
                provider=request.provider or "fake-provider",
                model=request.model or "fake-model",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    RunScheduler(store, agent_runner=SignalRunner()).advance(admitted.run_id)
    outbox = NotificationOutbox(store)
    primary = next(
        item
        for item in outbox.history(run_id=admitted.run_id)
        if item["kind"] == "approval_required"
    )
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?",
            (admitted.run_id,),
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?",
            (admitted.run_id,),
        )

    assert outbox.reconcile_run(admitted.run_id) >= 1
    repaired = next(
        item
        for item in outbox.history(run_id=admitted.run_id)
        if item["kind"] == primary["kind"]
        and item["transition_version"] == primary["transition_version"]
    )

    assert repaired["kind"] == primary["kind"]
    assert repaired["transition_version"] == primary["transition_version"]
    assert repaired["payload"] == primary["payload"]


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

    assert [item["transition_version"] for item in newest[:3]] == [204, 203, 202]
    assert newest[-1]["transition_version"] == 5
    older = outbox.history(
        run_id="run-newest",
        limit=200,
        before=(newest[-1]["occurred_at"], newest[-1]["transition_key"]),
    )
    assert [item["transition_version"] for item in older] == [4, 3, 2, 1, 0]


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
