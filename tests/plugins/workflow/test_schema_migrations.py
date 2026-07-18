from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


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


def test_pre_amendment_v209_store_migrates_without_rewriting_evidence(
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
    projection = store.load_run("migration-run")

    assert store.storage_health()["status"] == "healthy"
    assert projection["run_id"] == expected["run_id"]
    assert projection["workflow"] == expected["workflow"]
    assert projection["status"] == expected["status"]
    assert projection["event_sequence"] == expected["event_sequence"]
    assert projection["state_version"] == expected["state_version"]
    assert projection["nodes"]["start"]["attempts"][0]["attempt_id"] == expected[
        "attempt_id"
    ]
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
            "projection_schema_version",
            "projection_state_version",
            "projection_sha256",
            "journal_sequence",
            "journal_sha256",
            "integrity_verified_at",
        } <= columns
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "admission_events",
            "cleanup_history",
            "cleanup_previews",
            "repair_events",
            "runs",
            "store_metadata",
            "worker_claims",
        } <= tables
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"runs_concurrency", "worker_claims_lease"} <= indexes
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        migrated = dict(
            connection.execute(
                "SELECT * FROM runs WHERE run_id='migration-run'"
            ).fetchone()
        )
    for key, value in before.items():
        if key != "run_directory":
            assert migrated[key] == value
    assert migrated["run_directory"] == relocated
    assert migrated["projection_schema_version"] == 1
    assert migrated["projection_state_version"] == expected["state_version"]
    assert len(migrated["projection_sha256"]) == 64
    assert migrated["journal_sequence"] == expected["event_sequence"]
    assert len(migrated["journal_sha256"]) == 64
    assert migrated["integrity_verified_at"]

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
    duplicate = store.start_run(
        RunAdmissionRequest(
            workflow_name="migration-fixture",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-semantic-request",
            concurrency_key="migration-fixture",
        ),
        immutable_snapshot=prepared,
    )
    assert duplicate.run_id == "migration-run"
    assert duplicate.disposition == "existing"

    for relative, digest in immutable_evidence.items():
        assert _sha256(workflows / relative) == digest
    _assert_fixture_hashes(manifest)

    store.append_event("migration-run", "migration_probe", {"version": 2})
    migrated_events = [
        json.loads(line)
        for line in (Path(relocated) / "events.jsonl").read_text().splitlines()
    ]
    assert migrated_events[0]["schema_version"] == 1
    assert migrated_events[-1]["schema_version"] == 2
    assert migrated_events[-1]["frame_version"] == 1
    assert store.load_run("migration-run")["event_sequence"] == (
        expected["event_sequence"] + 1
    )


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
