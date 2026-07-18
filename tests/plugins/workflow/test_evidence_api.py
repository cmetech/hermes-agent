from __future__ import annotations

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def test_evidence_queries_are_bounded_sanitized_and_typed(tmp_path, workflow_writer):
    package = load_workflow(workflow_writer(tmp_path / "package", name="evidence"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="evidence",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="evidence-intent",
            concurrency_key="evidence",
        ),
        immutable_snapshot=prepared,
    )
    store.append_event(
        admitted.run_id,
        "diagnostic",
        {"password": "do-not-return", "message": "safe\x1b[31m text"},
    )

    page = EvidenceReader(store).query(
        admitted.run_id, kind="timeline", after=0, limit=200
    )

    assert page["schema_version"] == 1
    assert page["kind"] == "timeline"
    assert "do-not-return" not in str(page)
    assert "\x1b" not in str(page)
    assert page["next_cursor"] == 2
    assert page["truncated"] is False


def test_artifact_paths_are_reduced_to_safe_names(tmp_path, workflow_writer):
    package = load_workflow(workflow_writer(tmp_path / "package", name="artifacts"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="artifacts",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="artifact-intent",
            concurrency_key="artifacts",
        ),
        immutable_snapshot=prepared,
    )
    run = store.load_run(admitted.run_id)
    run["artifacts"] = [
        {
            "relative_path": "nodes/secret/location/report.json",
            "sha256": "a" * 64,
            "size_bytes": 10,
            "media_type": "application/json",
        }
    ]
    store.append_event(
        admitted.run_id,
        "artifact_test",
        projection_updates={"artifacts": run["artifacts"]},
    )

    page = EvidenceReader(store).query(admitted.run_id, kind="artifacts")

    assert page["items"][0]["relative_path"] == "report.json"
    assert page["items"][0]["sha256"] == "a" * 64
