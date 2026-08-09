from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import JournalRecoveryError, RunStore
import plugins.workflow.store as store_module


SCHEDULE_AT = "2099-01-02T03:04:05Z"
CORRUPT_SCHEDULE_AT = "2098-01-02T03:04:05Z"
CONCURRENT_SCHEDULE_AT = "2099-02-03T04:05:06Z"


def _scheduled_snapshot(store: RunStore, workflow_writer, tmp_path: Path, *, name: str):
    package = load_workflow(workflow_writer(tmp_path / f"package-{name}", name=name))
    return store.prepare_run_snapshot(package)


def _scheduled_request(
    snapshot,
    *,
    name: str,
    key: str = "scheduled",
    schedule_at: str = SCHEDULE_AT,
):
    return RunAdmissionRequest(
        workflow_name=name,
        definition_digest=snapshot.definition_digest,
        policy_digest=snapshot.policy_digest,
        input_manifest_digest=snapshot.input_manifest_digest,
        trigger_source="cli",
        idempotency_key=key,
        concurrency_key=name,
        run_metadata={"schedule_at": schedule_at},
    )


def _admit_scheduled(
    store: RunStore,
    workflow_writer,
    tmp_path: Path,
    *,
    name: str = "scheduled-store",
):
    snapshot = _scheduled_snapshot(store, workflow_writer, tmp_path, name=name)
    admitted = store.start_run(
        _scheduled_request(snapshot, name=name), immutable_snapshot=snapshot
    )
    assert admitted.disposition == "queued"
    assert admitted.run_id is not None
    return admitted.run_id


def _indexed_run(store: RunStore, run_id: str) -> dict[str, object]:
    with store._connect() as connection:
        return dict(
            connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        )


def _downgrade_to_v13_without_schedule(store: RunStore) -> None:
    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP INDEX runs_scheduled_queue")
        connection.execute("ALTER TABLE runs DROP COLUMN scheduled_at")
        connection.execute("PRAGMA user_version=13")


def test_winning_attempt_shared_context_identity_survives_journal_rebuild(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "shared-context-home"
    store = RunStore(home)
    package = load_workflow(
        workflow_writer(tmp_path / "shared-context-package", name="shared-context")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="shared-context",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    claim = store.claim_node(admitted.run_id, "start", "identity-owner")
    assert claim is not None
    store.mark_node_started(claim)
    identity = {
        "intended_authority_digest": "a" * 64,
        "model_visible_prefix_digest": "b" * 64,
        "shared_context_compatibility_digest": "c" * 64,
    }
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": "winning-session",
            "cache_fingerprint": "d" * 64,
            **identity,
        },
    )
    (store.run_directory(admitted.run_id) / "run.json").unlink()

    recovered = RunStore(home).load_run(admitted.run_id)
    evidence = RunScheduler._predecessor_results(
        recovered,
        ("start",),
        {},
    )["start"]

    assert evidence == {
        "session_id": "winning-session",
        "cache_fingerprint": "d" * 64,
        **identity,
    }


def _downgrade_to_v13_legacy_namespace(
    store: RunStore, *, retain_schedule_column: bool = False
) -> None:
    with sqlite3.connect(store.database) as connection:
        columns = connection.execute("PRAGMA table_info(runs)").fetchall()
        removed = {"idempotency_namespace_digest"}
        if not retain_schedule_column:
            removed.add("scheduled_at")
        retained = [row for row in columns if row[1] not in removed]
        definitions = []
        for _cid, name, declared_type, not_null, default, primary_key in retained:
            definition = f'"{name}" {declared_type}'
            if primary_key:
                definition += " PRIMARY KEY"
            if not_null:
                definition += " NOT NULL"
            if default is not None:
                definition += f" DEFAULT {default}"
            definitions.append(definition)
        names = ", ".join(f'"{row[1]}"' for row in retained)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("ALTER TABLE runs RENAME TO runs_v14_source")
        connection.execute(f"CREATE TABLE runs ({', '.join(definitions)})")
        connection.execute(
            f"INSERT INTO runs ({names}) SELECT {names} FROM runs_v14_source"
        )
        connection.execute("DROP TABLE runs_v14_source")
        connection.execute("PRAGMA user_version=13")


def test_fresh_scheduled_publication_derives_query_column_and_uses_partial_index(
    tmp_path: Path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=0)
    run_id = _admit_scheduled(store, workflow_writer, tmp_path)

    indexed = _indexed_run(store, run_id)
    assert indexed["status"] == "queued"
    assert indexed["admission_state"] == "published"
    assert indexed["scheduled_at"] == SCHEDULE_AT
    assert store.load_run(run_id)["run_metadata"]["schedule_at"] == SCHEDULE_AT
    with store._connect() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT run_id FROM runs "
            "WHERE admission_state='published' AND status='queued' "
            "AND scheduled_at IS NOT NULL AND scheduled_at<=? "
            "ORDER BY scheduled_at, created_at, run_id",
            (SCHEDULE_AT,),
        ).fetchall()
    assert any("runs_scheduled_queue" in str(row["detail"]) for row in plan)


def test_current_store_reinstalls_run_repair_lookup_index_on_every_open(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    with store._connect() as connection:
        connection.execute("DROP INDEX IF EXISTS repair_events_run_reason_sequence")
        connection.execute(
            "DROP INDEX IF EXISTS repair_events_revalidation_sequence"
        )

    reopened = RunStore(home)

    with reopened._connect() as connection:
        columns = tuple(
            str(row["name"])
            for row in connection.execute(
                "PRAGMA index_info(repair_events_run_reason_sequence)"
            ).fetchall()
        )
        revalidation_columns = tuple(
            str(row["name"])
            for row in connection.execute(
                "PRAGMA index_info(repair_events_revalidation_sequence)"
            ).fetchall()
        )
    assert columns == ("run_id", "reason_code", "sequence")
    assert revalidation_columns == ("sequence", "run_id", "reason_code")


def test_submicrosecond_schedule_survives_index_and_journal_recovery(
    tmp_path: Path, workflow_writer
) -> None:
    exact_schedule = "2099-01-02T03:04:05.1234561Z"
    store = RunStore(tmp_path / "home", max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-submicrosecond"
    )
    admitted = store.start_run(
        _scheduled_request(
            snapshot,
            name="scheduled-submicrosecond",
            schedule_at=exact_schedule,
        ),
        immutable_snapshot=snapshot,
    )
    assert admitted.run_id is not None
    directory = store.run_directory(admitted.run_id)
    (directory / "run.json").unlink()

    recovered = store.load_run(admitted.run_id)

    assert recovered["run_metadata"]["schedule_at"] == exact_schedule
    assert _indexed_run(store, admitted.run_id)["scheduled_at"] == exact_schedule


def test_reserved_scheduled_publication_recovers_from_projection(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-reservation"
    )
    request = _scheduled_request(snapshot, name="scheduled-reservation")

    def crash_before_publication(*_args, **_kwargs):
        raise RuntimeError("injected publication crash")

    monkeypatch.setattr(store, "_mark_reservation_published", crash_before_publication)
    with pytest.raises(RuntimeError, match="injected publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, admission_state, scheduled_at FROM runs"
        ).fetchone()
    assert reserved["admission_state"] == "reserved"
    assert reserved["scheduled_at"] == SCHEDULE_AT

    restarted = RunStore(home, max_executing_runs=0)

    indexed = _indexed_run(restarted, str(reserved["run_id"]))
    assert indexed["admission_state"] == "published"
    assert indexed["status"] == "queued"
    assert indexed["scheduled_at"] == SCHEDULE_AT


def test_genuine_v13_migration_backfills_reserved_schedule_before_recovery(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-v13-reservation"
    )
    request = _scheduled_request(snapshot, name="scheduled-v13-reservation")

    def crash_before_publication(*_args, **_kwargs):
        raise RuntimeError("injected publication crash")

    monkeypatch.setattr(store, "_mark_reservation_published", crash_before_publication)
    with pytest.raises(RuntimeError, match="injected publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, admission_state, scheduled_at FROM runs"
        ).fetchone()
    assert reserved["admission_state"] == "reserved"
    assert reserved["scheduled_at"] == SCHEDULE_AT

    _downgrade_to_v13_without_schedule(store)

    restarted = RunStore(home, max_executing_runs=0)

    indexed = _indexed_run(restarted, str(reserved["run_id"]))
    assert indexed["admission_state"] == "published"
    assert indexed["status"] == "queued"
    assert indexed["scheduled_at"] == SCHEDULE_AT


def test_genuine_v13_migration_leaves_incomplete_reservation_for_cleanup(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-v13-incomplete"
    )
    request = _scheduled_request(snapshot, name="scheduled-v13-incomplete")

    def crash_before_durable_publication(*_args, **_kwargs):
        raise RuntimeError("injected pre-publication crash")

    monkeypatch.setattr(
        store, "_publish_reserved_run", crash_before_durable_publication
    )
    with pytest.raises(RuntimeError, match="injected pre-publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, run_directory, staging_directory, admission_state FROM runs"
        ).fetchone()
    assert reserved["admission_state"] == "reserved"
    assert not Path(reserved["run_directory"]).exists()
    assert Path(reserved["staging_directory"]).is_dir()

    _downgrade_to_v13_without_schedule(store)

    restarted = RunStore(home, max_executing_runs=0)

    with restarted._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        event = connection.execute(
            "SELECT event_type, reason_code FROM admission_events "
            "WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (reserved["run_id"],),
        ).fetchone()
    assert tuple(event) == ("admission_reservation_released", "incomplete_publication")
    assert restarted.storage_health()["status"] == "healthy"


def test_v13_namespace_migration_carries_incomplete_reservation_to_cleanup(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-v13-namespace-incomplete"
    )
    request = _scheduled_request(snapshot, name="scheduled-v13-namespace-incomplete")

    def crash_before_durable_publication(*_args, **_kwargs):
        raise RuntimeError("injected pre-publication crash")

    monkeypatch.setattr(
        store, "_publish_reserved_run", crash_before_durable_publication
    )
    with pytest.raises(RuntimeError, match="injected pre-publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, run_directory, staging_directory, admission_state, "
            "start_digest FROM runs"
        ).fetchone()
    assert reserved["admission_state"] == "reserved"
    assert not Path(reserved["run_directory"]).exists()
    assert Path(reserved["staging_directory"]).is_dir()

    _downgrade_to_v13_legacy_namespace(store)

    migrated_reservation: dict[str, object] = {}
    original_reconcile = RunStore._reconcile_admission

    def inspect_namespace_row_then_reconcile(restarted: RunStore) -> None:
        with restarted._connect() as connection:
            row = connection.execute(
                "SELECT idempotency_namespace_digest, start_digest, scheduled_at "
                "FROM runs WHERE run_id=?",
                (reserved["run_id"],),
            ).fetchone()
        if row is not None:
            migrated_reservation.update(dict(row))
        original_reconcile(restarted)

    monkeypatch.setattr(
        RunStore, "_reconcile_admission", inspect_namespace_row_then_reconcile
    )

    restarted = RunStore(home, max_executing_runs=0)

    assert migrated_reservation["idempotency_namespace_digest"]
    assert migrated_reservation["start_digest"] == reserved["start_digest"]
    assert migrated_reservation["scheduled_at"] is None
    assert restarted.storage_health() == {"status": "healthy", "reasons": []}
    with restarted._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        event = connection.execute(
            "SELECT event_type, reason_code FROM admission_events "
            "WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (reserved["run_id"],),
        ).fetchone()
    assert tuple(event) == ("admission_reservation_released", "incomplete_publication")
    assert restarted.storage_health()["status"] == "healthy"


def test_v13_namespace_migration_keeps_durable_reserved_schedule_for_publication(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-v13-namespace-reserved"
    )
    request = _scheduled_request(snapshot, name="scheduled-v13-namespace-reserved")

    def crash_before_publication(*_args, **_kwargs):
        raise RuntimeError("injected publication crash")

    monkeypatch.setattr(store, "_mark_reservation_published", crash_before_publication)
    with pytest.raises(RuntimeError, match="injected publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, admission_state, scheduled_at FROM runs"
        ).fetchone()
    assert reserved["admission_state"] == "reserved"
    assert reserved["scheduled_at"] == SCHEDULE_AT

    _downgrade_to_v13_legacy_namespace(store)

    restarted = RunStore(home, max_executing_runs=0)

    indexed = _indexed_run(restarted, str(reserved["run_id"]))
    assert indexed["admission_state"] == "published"
    assert indexed["status"] == "queued"
    assert indexed["scheduled_at"] == SCHEDULE_AT
    assert restarted.storage_health() == {"status": "healthy", "reasons": []}


def test_v13_namespace_migration_fails_closed_for_corrupt_durable_reservation(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    snapshot = _scheduled_snapshot(
        store, workflow_writer, tmp_path, name="scheduled-v13-namespace-corrupt"
    )
    request = _scheduled_request(snapshot, name="scheduled-v13-namespace-corrupt")

    def crash_before_publication(*_args, **_kwargs):
        raise RuntimeError("injected publication crash")

    monkeypatch.setattr(store, "_mark_reservation_published", crash_before_publication)
    with pytest.raises(RuntimeError, match="injected publication crash"):
        store.start_run(request, immutable_snapshot=snapshot)
    with store._connect() as connection:
        reserved = connection.execute(
            "SELECT run_id, run_directory, admission_state FROM runs"
        ).fetchone()
    directory = Path(reserved["run_directory"])
    assert reserved["admission_state"] == "reserved"
    assert directory.is_dir()
    (directory / "run.json").write_text("{broken", encoding="utf-8")

    _downgrade_to_v13_legacy_namespace(store)

    restarted = RunStore(home, max_executing_runs=0)

    health = restarted.storage_health()
    assert health["status"] == "repair_required"
    assert any(reason["reason_code"] == "index_corrupt" for reason in health["reasons"])


def test_projection_schedule_derivation_rejects_noncanonical_metadata() -> None:
    with pytest.raises(JournalRecoveryError, match="not canonical"):
        RunStore._scheduled_at_from_projection({
            "run_metadata": {"schedule_at": "2099-01-02T04:04:05+01:00"}
        })


@pytest.mark.parametrize("metadata", ([], "", 0, ["not", "metadata"]))
def test_projection_schedule_derivation_rejects_non_mapping_metadata(
    metadata: object,
) -> None:
    with pytest.raises(JournalRecoveryError, match="metadata is malformed"):
        RunStore._scheduled_at_from_projection({"run_metadata": metadata})


@pytest.mark.parametrize("projection", ({}, {"run_metadata": None}))
def test_projection_schedule_derivation_accepts_absent_metadata(
    projection: dict[str, object],
) -> None:
    assert RunStore._scheduled_at_from_projection(projection) is None


def test_projection_schedule_parity_rejects_disagreement() -> None:
    projection = {"run_metadata": {"schedule_at": SCHEDULE_AT}}

    assert RunStore._scheduled_at_from_projection(projection) == SCHEDULE_AT
    with pytest.raises(JournalRecoveryError, match="parity mismatch"):
        RunStore._scheduled_at_from_projection(projection, indexed=CORRUPT_SCHEDULE_AT)


def test_unscheduled_projection_keeps_null_derived_column_and_exact_start_digest(
    tmp_path: Path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=0)
    name = "unscheduled-store"
    snapshot = _scheduled_snapshot(store, workflow_writer, tmp_path, name=name)
    request = RunAdmissionRequest(
        workflow_name=name,
        definition_digest=snapshot.definition_digest,
        policy_digest=snapshot.policy_digest,
        input_manifest_digest=snapshot.input_manifest_digest,
        trigger_source="cli",
        idempotency_key="unscheduled",
        concurrency_key=name,
    )
    expected_digest = RunStore._start_digest(request)

    admitted = store.start_run(request, immutable_snapshot=snapshot)

    indexed = _indexed_run(store, str(admitted.run_id))
    assert indexed["scheduled_at"] is None
    assert indexed["start_digest"] == expected_digest
    projection_path = store.run_directory(str(admitted.run_id)) / "run.json"
    projection = json.loads(projection_path.read_bytes())
    assert projection["run_metadata"] == {}
    assert RunStore._start_digest_from_projection(projection) == expected_digest


def test_unscheduled_api_golden_digest_and_metadata_remain_byte_identical() -> None:
    request = RunAdmissionRequest(
        workflow_name="digest-fixture",
        definition_digest="1" * 64,
        policy_digest="2" * 64,
        input_manifest_digest="3" * 64,
        trigger_source="api",
        idempotency_key="intent-api",
        idempotency_namespace="api:service:test:writer",
        concurrency_key="digest-fixture",
        operator_scope="service:test:writer",
        run_metadata={"zeta": "last", "alpha": "one"},
        provenance=TriggerProvenance.authenticated_api(
            assurance="verified_adapter",
            intent_key="intent-api",
            source_instance="api:token:test",
            principal="service:test:writer",
        ),
    )

    assert RunStore._start_digest(request) == (
        "2432809a726b15ac48c7a0ccc7c2c7ed122fe79c8d68f0c85ecc862f8d91e475"
    )
    assert "schedule_at" not in request.run_metadata


def test_genuine_v13_migration_backfills_schedule_and_is_idempotent(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-v13-migration"
    )
    projection_bytes = (store.run_directory(run_id) / "run.json").read_bytes()
    start_digest = str(_indexed_run(store, run_id)["start_digest"])
    _downgrade_to_v13_without_schedule(store)
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
        assert "scheduled_at" not in {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }

    migrated = RunStore(home, max_executing_runs=0)

    assert _indexed_run(migrated, run_id)["scheduled_at"] == SCHEDULE_AT
    assert _indexed_run(migrated, run_id)["start_digest"] == start_digest
    assert (
        migrated.run_directory(run_id) / "run.json"
    ).read_bytes() == projection_bytes
    first_schema = _schema_sql(migrated)
    reopened = RunStore(home, max_executing_runs=0)
    assert _schema_sql(reopened) == first_schema
    assert _indexed_run(reopened, run_id)["scheduled_at"] == SCHEDULE_AT


def test_v13_migration_scopes_uncorroborated_published_evidence_to_one_run(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    healthy_run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-v13-healthy"
    )
    damaged_run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-v13-damaged"
    )
    with store._connect() as connection:
        generation = connection.execute(
            "SELECT value FROM store_metadata WHERE key='generation'"
        ).fetchone()["value"]

    _downgrade_to_v13_without_schedule(store)
    damaged_projection_path = store.run_directory(damaged_run_id) / "run.json"
    damaged_projection = json.loads(
        damaged_projection_path.read_text(encoding="utf-8")
    )
    damaged_projection["run_metadata"]["schedule_at"] = CORRUPT_SCHEDULE_AT
    damaged_projection_path.write_text(
        json.dumps(damaged_projection), encoding="utf-8"
    )

    restarted = RunStore(home, max_executing_runs=0)

    with restarted._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute(
                "SELECT value FROM store_metadata WHERE key='generation'"
            ).fetchone()["value"]
            == generation
        )
    assert not tuple(restarted.quarantine_root.glob("admission-index-*"))
    assert _indexed_run(restarted, healthy_run_id)["scheduled_at"] == SCHEDULE_AT
    assert _indexed_run(restarted, damaged_run_id)["scheduled_at"] is None
    assert damaged_projection_path.is_file()
    assert restarted._active_run_repair_reasons(healthy_run_id) == ()
    assert restarted._active_run_repair_reasons(damaged_run_id) == (
        "run_evidence_uncorroborated",
    )
    assert restarted.storage_health() == {"status": "healthy", "reasons": []}

    unrelated_run_id = _admit_scheduled(
        restarted, workflow_writer, tmp_path, name="scheduled-v13-unrelated"
    )
    assert _indexed_run(restarted, unrelated_run_id)["scheduled_at"] == SCHEDULE_AT


def test_first_load_after_v13_repair_rebuild_resynchronizes_schedule(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-v13-first-load"
    )
    authoritative = store.load_run(run_id)
    _downgrade_to_v13_without_schedule(store)
    projection_path = store.run_directory(run_id) / "run.json"
    rewritten = json.loads(projection_path.read_text(encoding="utf-8"))
    rewritten["run_metadata"]["schedule_at"] = CORRUPT_SCHEDULE_AT
    projection_path.write_text(json.dumps(rewritten), encoding="utf-8")

    migrated = RunStore(home, max_executing_runs=0)

    assert _indexed_run(migrated, run_id)["scheduled_at"] is None
    assert migrated._active_run_repair_reasons(run_id) == (
        "run_evidence_uncorroborated",
    )

    rebuilt = migrated.load_run(run_id)

    assert rebuilt == authoritative
    assert rebuilt["run_metadata"]["schedule_at"] == SCHEDULE_AT
    assert _indexed_run(migrated, run_id)["scheduled_at"] == SCHEDULE_AT
    assert migrated._active_run_repair_reasons(run_id) == ()


def _schema_sql(store: RunStore) -> tuple[tuple[str, str, str], ...]:
    with store._connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        )


def test_v13_runtime_refuses_v14_index_in_newer_schema_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "home")
    stale_runtime = object.__new__(RunStore)
    stale_runtime.database = store.database
    monkeypatch.setattr(store_module, "_STORE_SCHEMA_VERSION", 13)

    with pytest.raises(sqlite3.DatabaseError, match="newer than this runtime"):
        stale_runtime._create_or_migrate_schema()


@pytest.mark.parametrize("retain_schedule_column", (False, True))
def test_v13_namespace_migration_derives_schedule_without_trusting_source_column(
    tmp_path: Path, workflow_writer, retain_schedule_column: bool
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-v13-namespace"
    )
    _downgrade_to_v13_legacy_namespace(
        store, retain_schedule_column=retain_schedule_column
    )
    with sqlite3.connect(store.database) as connection:
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }
        assert ("scheduled_at" in source_columns) is retain_schedule_column
        assert "idempotency_namespace_digest" not in source_columns
        if retain_schedule_column:
            connection.execute(
                "UPDATE runs SET scheduled_at=? WHERE run_id=?",
                (CORRUPT_SCHEDULE_AT, run_id),
            )

    migrated = RunStore(home, max_executing_runs=0)

    indexed = _indexed_run(migrated, run_id)
    assert indexed["scheduled_at"] == SCHEDULE_AT
    assert indexed["idempotency_namespace_digest"]
    assert migrated.load_run(run_id)["status"] == "queued"


def test_namespace_migration_obeys_run_then_sqlite_lock_order_and_uses_fresh_evidence(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-namespace-lock-order"
    )
    directory = store.run_directory(run_id)
    _downgrade_to_v13_legacy_namespace(store)
    with sqlite3.connect(store.database) as connection:
        assert "idempotency_namespace_digest" not in {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }

    original_workflow_lock = store_module.workflow_lock
    target_lock = store._run_lock_path(run_id).resolve()
    migration_waiting_for_run = threading.Event()
    migration_thread: dict[str, int] = {}

    @contextmanager
    def bounded_observed_lock(path: Path, *, timeout_seconds: float = 5.0):
        if (
            threading.get_ident() == migration_thread.get("ident")
            and Path(path).resolve() == target_lock
        ):
            probe = sqlite3.connect(store.database, timeout=0, isolation_level=None)
            lock_order_error = None
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
            except sqlite3.OperationalError as exc:
                lock_order_error = exc
            finally:
                probe.close()
            migration_waiting_for_run.set()
            if lock_order_error is not None:
                raise AssertionError(
                    "namespace migration acquired SQLite before the run lock"
                ) from lock_order_error
            timeout_seconds = min(timeout_seconds, 0.25)
        with original_workflow_lock(path, timeout_seconds=timeout_seconds):
            yield

    monkeypatch.setattr(store_module, "workflow_lock", bounded_observed_lock)

    def migrate() -> RunStore:
        migration_thread["ident"] = threading.get_ident()
        return RunStore(home, max_executing_runs=0)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_workflow_lock(target_lock):
            migration = pool.submit(migrate)
            assert migration_waiting_for_run.wait(timeout=2)
            connection = sqlite3.connect(
                store.database, timeout=1.0, isolation_level=None
            )
            try:
                connection.execute("PRAGMA busy_timeout=1000")
                connection.execute("BEGIN IMMEDIATE")
                projection = json.loads(
                    (directory / "run.json").read_text(encoding="utf-8")
                )
                projection["run_metadata"]["schedule_at"] = CONCURRENT_SCHEDULE_AT
                store._append_locked(
                    directory,
                    projection,
                    "schedule_revised",
                    {"schedule_at": CONCURRENT_SCHEDULE_AT},
                    defer_notification=True,
                    reserve_connection=connection,
                )
                connection.execute(
                    "UPDATE runs SET start_digest=?, updated_at=? WHERE run_id=?",
                    (
                        RunStore._start_digest_from_projection(projection),
                        projection["updated_at"],
                        run_id,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        migrated = migration.result(timeout=2)

    indexed = _indexed_run(migrated, run_id)
    projection = migrated.load_run(run_id)
    assert indexed["scheduled_at"] == CONCURRENT_SCHEDULE_AT
    assert indexed["start_digest"] == RunStore._start_digest_from_projection(projection)
    assert projection["run_metadata"]["schedule_at"] == CONCURRENT_SCHEDULE_AT
    assert projection["event_sequence"] == 2


@pytest.mark.parametrize(
    "reconstruction",
    ("missing_row", "missing_index", "journal_only", "rebuilt_projection"),
)
def test_every_reconstruction_path_preserves_scheduled_identity(
    tmp_path: Path, workflow_writer, reconstruction: str
) -> None:
    home = tmp_path / f"home-{reconstruction}"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name=f"scheduled-{reconstruction}"
    )
    directory = store.run_directory(run_id)
    if reconstruction == "missing_row":
        with store._connect() as connection:
            connection.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        recovered = RunStore(home, max_executing_runs=0)
    elif reconstruction == "missing_index":
        store.database.unlink()
        recovered = RunStore(home, max_executing_runs=0)
    else:
        if reconstruction == "journal_only":
            (directory / "run.json").unlink()
        else:
            (directory / "run.json").write_text("{broken", encoding="utf-8")
        assert store.load_run(run_id)["run_metadata"]["schedule_at"] == SCHEDULE_AT
        recovered = store

    indexed = _indexed_run(recovered, run_id)
    assert indexed["status"] == "queued"
    assert indexed["scheduled_at"] == SCHEDULE_AT
    assert recovered.load_run(run_id)["run_metadata"]["schedule_at"] == SCHEDULE_AT


def test_sql_only_schedule_corruption_is_detected_repaired_and_fail_closed(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-sql-corruption"
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=? WHERE run_id=?",
            (CORRUPT_SCHEDULE_AT, run_id),
        )

    restarted = RunStore(home, max_executing_runs=0)

    assert _indexed_run(restarted, run_id)["scheduled_at"] == SCHEDULE_AT
    assert restarted.storage_health()["status"] == "repair_required"
    repair = restarted.list_repair_events()[-1]
    assert repair["reason_code"] == "index_schedule_inconsistent"
    assert repair["outcome"] == "index_rebuilt"
    assert restarted.load_run(run_id)["status"] == "queued"


def test_loaded_integrity_detects_and_repairs_sql_only_schedule_corruption(
    tmp_path: Path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-loaded-integrity"
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=? WHERE run_id=?",
            (CORRUPT_SCHEDULE_AT, run_id),
        )

    loaded = store.load_run(run_id)

    assert loaded["status"] == "queued"
    assert _indexed_run(store, run_id)["scheduled_at"] == SCHEDULE_AT
    assert store.storage_health()["status"] == "repair_required"
    assert store.list_repair_events()[-1]["reason_code"] == (
        "index_schedule_inconsistent"
    )


def test_loaded_integrity_repair_never_writes_stale_projection_schedule(
    tmp_path: Path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "home", max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-loaded-corroboration"
    )
    stale = store.load_run(run_id)
    stale["run_metadata"]["schedule_at"] = "2097-01-02T03:04:05Z"
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET scheduled_at=? WHERE run_id=?",
            (CORRUPT_SCHEDULE_AT, run_id),
        )

    def interrupt_after_parity_repair(*_args, **_kwargs):
        raise RuntimeError("injected sync interruption")

    monkeypatch.setattr(
        RunStore,
        "_sync_integrity_index",
        staticmethod(interrupt_after_parity_repair),
    )

    with pytest.raises(RuntimeError, match="injected sync interruption"):
        store._sync_loaded_integrity(store.run_directory(run_id), stale)

    assert _indexed_run(store, run_id)["scheduled_at"] == SCHEDULE_AT


def test_projection_only_schedule_corruption_is_preserved_and_fails_closed(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home, max_executing_runs=0)
    run_id = _admit_scheduled(
        store, workflow_writer, tmp_path, name="scheduled-projection-corruption"
    )
    projection_path = store.run_directory(run_id) / "run.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection["run_metadata"]["schedule_at"] = CORRUPT_SCHEDULE_AT
    projection_path.write_text(json.dumps(projection), encoding="utf-8")

    restarted = RunStore(home, max_executing_runs=0)

    assert _indexed_run(restarted, run_id)["scheduled_at"] == SCHEDULE_AT
    assert restarted._active_run_repair_reasons(run_id) == (
        "run_evidence_uncorroborated",
    )
    repaired = restarted.load_run(run_id)
    assert repaired["run_metadata"]["schedule_at"] == SCHEDULE_AT
    assert repaired["status"] == "queued"
    assert restarted._active_run_repair_reasons(run_id) == ()
