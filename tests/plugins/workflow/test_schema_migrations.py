from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore, _STORE_SCHEMA_VERSION


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "store"
    / "pre-production-amendment-v2.0.9"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_fixture_hashes(manifest: dict[str, object]) -> None:
    hashes = manifest["sha256"]
    assert isinstance(hashes, dict)
    for relative_path, expected in hashes.items():
        assert _sha256(FIXTURE / relative_path) == expected


def _schema_manifest(store: RunStore) -> tuple[object, ...]:
    with store._connect() as connection:
        objects = connection.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') AS sql "
            "FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        manifest = []
        for row in objects:
            columns = ()
            indexes = ()
            if row["type"] == "table":
                columns = tuple(
                    tuple(column)
                    for column in connection.execute(
                        f'PRAGMA table_info("{row["name"]}")'
                    ).fetchall()
                )
                indexes = tuple(
                    tuple(index)
                    for index in connection.execute(
                        f'PRAGMA index_list("{row["name"]}")'
                    ).fetchall()
                )
            manifest.append((tuple(row), columns, indexes))
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            _STORE_SCHEMA_VERSION
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return tuple(manifest)


def test_pre_amendment_v209_store_reaches_current_full_schema_idempotently(
    tmp_path: Path,
) -> None:
    manifest = json.loads((FIXTURE / "fixture-manifest.json").read_text())
    _assert_fixture_hashes(manifest)
    expected = manifest["expected"]
    assert isinstance(expected, dict)

    home = tmp_path / "home"
    workflows = home / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy2(FIXTURE / "admission.db", workflows / "admission.sqlite3")
    shutil.copytree(FIXTURE / "runs", workflows / "runs")
    database = workflows / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        before = dict(
            connection.execute(
                "SELECT * FROM runs WHERE run_id='migration-run'"
            ).fetchone()
        )
        legacy_prefix = str(manifest["legacy_run_directory_prefix"])
        assert before["run_directory"].startswith(legacy_prefix)
        relocated = str(workflows) + before["run_directory"][len(legacy_prefix) :]
        connection.execute(
            "UPDATE runs SET run_directory=? WHERE run_id='migration-run'",
            (relocated,),
        )
    immutable_evidence = {
        relative: _sha256(FIXTURE / relative)
        for relative in manifest["sha256"]
        if relative != "admission.db"
    }

    store = RunStore(home)
    first_manifest = _schema_manifest(store)
    projection = store.load_run("migration-run")

    assert store.storage_health()["status"] == "healthy"
    assert projection["run_id"] == expected["run_id"]
    assert projection["workflow"] == expected["workflow"]
    assert projection["status"] == expected["status"]
    assert projection["event_sequence"] == expected["event_sequence"]
    assert projection["state_version"] == expected["state_version"]
    legacy_attempt = projection["nodes"]["start"]["attempts"][0]
    assert legacy_attempt["attempt_id"] == expected["attempt_id"]
    assert "spawn" not in legacy_attempt
    assert (
        store.node_effect_classification(
            "migration-run", "start", projection=projection
        )
        == "outward"
    )
    with store._connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runs)")
        }
        assert {
            "idempotency_namespace_digest",
            "execution_mode",
            "foreground_epoch",
            "foreground_lease_expires_at",
            "foreground_owner_id",
            "provenance_json",
            "projection_schema_version",
            "projection_state_version",
            "projection_sha256",
            "journal_sequence",
            "journal_sha256",
            "integrity_verified_at",
            "queue_sequence",
            "pause_lane_policy",
            "lane_state",
        } <= columns
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "admission_events",
            "attempt_journal_reserves",
            "cleanup_history",
            "cleanup_previews",
            "coordinator_events",
            "coordinator_lease",
            "coordinator_wakes",
            "repair_events",
            "runs",
            "store_metadata",
            "store_repair_state",
            "worker_claims",
            "workflow_notification_facts",
            "workflow_notification_outbox",
            "workflow_notification_reconcile_state",
        } <= tables
        reserve_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(attempt_journal_reserves)"
            )
        }
        assert {
            "attempt_id",
            "run_id",
            "terminal_reserve_bytes",
            "projection_limit_bytes",
            "created_at",
        } <= reserve_columns
        repair_state_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(store_repair_state)")
        }
        assert {
            "run_id",
            "attempt_id",
            "reason_code",
            "detected_at",
            "payload_json",
        } <= repair_state_columns
        cleanup_preview_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(cleanup_previews)")
        }
        assert "authority_binding_digest" in cleanup_preview_columns
        coordinator_lease_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(coordinator_lease)")
        }
        assert {
            "boot_id",
            "heartbeat_monotonic",
            "lease_seconds",
            "sweep_cursor_created_at",
            "sweep_cursor_run_id",
            "last_sweep_at",
        } <= coordinator_lease_columns
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "attempt_journal_reserves_run",
            "coordinator_wakes_pending",
            "runs_concurrency",
            "runs_coordinator_scan",
            "worker_claims_lease",
            "workflow_notification_delivery",
            "workflow_notification_fact_run",
        } <= indexes
        runs_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()[0]
        normalized_runs_schema = "".join(runs_schema.lower().split())
        assert (
            "unique(idempotency_namespace_digest,workflow_name,"
            "idempotency_digest)" in normalized_runs_schema
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            _STORE_SCHEMA_VERSION
        )
        migrated = dict(
            connection.execute(
                "SELECT * FROM runs WHERE run_id='migration-run'"
            ).fetchone()
        )
        queue_counter = connection.execute(
            "SELECT value FROM store_metadata WHERE key='queue_sequence'"
        ).fetchone()
    for key, value in before.items():
        if key != "run_directory":
            assert migrated[key] == value
    assert migrated["run_directory"] == relocated
    assert migrated["projection_schema_version"] == 1
    assert migrated["projection_state_version"] == expected["state_version"]
    assert migrated["status"] == projection["status"]
    assert migrated["desired_status"] == projection.get("desired_status")
    assert migrated["execution_mode"] == projection.get(
        "execution_mode", "foreground"
    )
    assert migrated["queue_position"] == projection.get("queue_position")
    assert migrated["blocked_by_run_id"] == projection.get("blocked_by_run_id")
    assert len(migrated["projection_sha256"]) == 64
    assert migrated["journal_sequence"] == expected["event_sequence"]
    assert len(migrated["journal_sha256"]) == 64
    assert migrated["integrity_verified_at"]
    assert migrated["queue_sequence"] is None
    assert migrated["pause_lane_policy"] == "hold"
    assert migrated["lane_state"] == "released"
    assert queue_counter is not None
    assert int(queue_counter["value"]) >= 0

    reopened = RunStore(home)
    second_manifest = _schema_manifest(reopened)
    assert second_manifest == first_manifest
    assert reopened.load_run("migration-run")["event_sequence"] == expected[
        "event_sequence"
    ]

    retry_package = tmp_path / "retry-package"
    retry_package.mkdir()
    shutil.copy2(
        Path(relocated) / "definition.yaml",
        retry_package / "migration-fixture.yaml",
    )
    shutil.copy2(
        Path(relocated) / "policy.yaml",
        retry_package / "migration-fixture.hermes.yaml",
    )
    package = load_workflow(retry_package / "migration-fixture.yaml")
    prepared = store.prepare_run_snapshot(package)
    duplicate = reopened.start_run(
        RunAdmissionRequest(
            workflow_name="migration-fixture",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-semantic-request",
            idempotency_namespace="profile-local:cli",
            concurrency_key="migration-fixture",
        ),
        immutable_snapshot=prepared,
    )
    assert duplicate.run_id == "migration-run"
    assert duplicate.disposition == "existing"

    for relative, digest in immutable_evidence.items():
        assert _sha256(workflows / relative) == digest
    _assert_fixture_hashes(manifest)

    reopened.append_event("migration-run", "migration_probe", {"version": 2})
    migrated_events = [
        json.loads(line)
        for line in (Path(relocated) / "events.jsonl").read_text().splitlines()
    ]
    assert migrated_events[0]["schema_version"] == 1
    assert migrated_events[-1]["schema_version"] == 2
    assert migrated_events[-1]["frame_version"] == 1
    assert reopened.load_run("migration-run")["event_sequence"] == (
        expected["event_sequence"] + 1
    )


def test_legacy_queued_projection_sync_preserves_migrated_fifo_sequence(
    tmp_path: Path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=1)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="legacy-queued-fifo")
    )

    def start(key: str):
        prepared = store.prepare_run_snapshot(package)
        return store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=key,
                concurrency_key=package.definition.name,
                concurrency_policy="queue",
            ),
            immutable_snapshot=prepared,
        )

    start("blocker")
    queued = start("legacy-queued")
    projection = store.load_run(queued.run_id)
    sequence = projection.pop("queue_sequence")
    directory = store.run_directory(queued.run_id)

    with store._connect() as connection:
        store._sync_integrity_index(
            connection,
            projection=projection,
            journal_sha256=_sha256(directory / "events.jsonl"),
        )
        indexed = connection.execute(
            "SELECT queue_sequence, queue_position FROM runs WHERE run_id=?",
            (queued.run_id,),
        ).fetchone()

    assert indexed["queue_sequence"] == sequence
    assert indexed["queue_position"] == sequence


def test_future_index_schema_is_preserved_and_rebuilt_fail_closed(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="future-index"))
    home = tmp_path / "home"
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="future-index",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="future-index",
            concurrency_key="future-index",
        ),
        immutable_snapshot=prepared,
    )
    directory = store.run_directory(admitted.run_id)
    evidence_hashes = {
        path.relative_to(directory): _sha256(path)
        for path in directory.rglob("*")
        if path.is_file()
    }
    with sqlite3.connect(store.database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA user_version=999")

    restarted = RunStore(home)

    assert restarted.storage_health()["status"] == "repair_required"
    assert restarted.load_run(admitted.run_id)["status"] == "running"
    preserved = list(restarted.quarantine_root.glob("admission-index-*/admission.sqlite3"))
    assert len(preserved) == 1
    for relative, digest in evidence_hashes.items():
        assert _sha256(directory / relative) == digest
    duplicate = restarted.prepare_run_snapshot(package)
    rejected = restarted.start_run(
        RunAdmissionRequest(
            workflow_name="future-index",
            definition_digest=duplicate.definition_digest,
            policy_digest=duplicate.policy_digest,
            input_manifest_digest=duplicate.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="new-request",
            concurrency_key="future-index",
        ),
        immutable_snapshot=duplicate,
    )
    assert rejected.reason_code == "storage_repair_required"
