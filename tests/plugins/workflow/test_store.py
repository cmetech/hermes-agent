from __future__ import annotations

import json
import os

import pytest

from plugins.workflow.schema import load_workflow
from plugins.workflow.store import InputSnapshotError, RunStore


def test_prepare_snapshot_copies_inputs_immutably(tmp_path, workflow_writer):
    workflow = workflow_writer(tmp_path / "package", name="copy-input")
    source = tmp_path / "evidence.txt"
    source.write_text("original", encoding="utf-8")
    store = RunStore(tmp_path / "home")

    prepared = store.prepare_run_snapshot(
        load_workflow(workflow), inputs={"evidence": source}
    )
    source.write_text("mutated", encoding="utf-8")
    source.unlink()

    manifest = json.loads((prepared.staging_directory / "inputs.json").read_text())
    captured = prepared.staging_directory / manifest["evidence"]["relative_path"]
    assert captured.read_text(encoding="utf-8") == "original"
    assert manifest["evidence"]["sha256"] == prepared.input_digests["evidence"]


def test_prepare_snapshot_rejects_symlink_and_oversized_input(
    tmp_path, workflow_writer
):
    workflow = load_workflow(workflow_writer(tmp_path / "package"))
    target = tmp_path / "target.txt"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    store = RunStore(tmp_path / "home", max_input_bytes=4)

    with pytest.raises(InputSnapshotError, match="symlink"):
        store.prepare_run_snapshot(workflow, inputs={"evidence": link})
    with pytest.raises(InputSnapshotError, match="exceeds"):
        store.prepare_run_snapshot(workflow, inputs={"evidence": target})


def test_projection_and_journal_are_monotonic(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    prepared = store.prepare_run_snapshot(package)
    from plugins.workflow.admission import RunAdmissionRequest

    result = store.start_run(
        RunAdmissionRequest(
            workflow_name="example",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="one",
            concurrency_key="example",
        ),
        immutable_snapshot=prepared,
    )
    store.append_event(result.run_id, "semantic_progress", {"phase": "ready"})

    run = store.load_run(result.run_id)
    events = store.tail_events(result.run_id, limit=10)
    assert run["event_sequence"] == 2
    assert [event["sequence"] for event in events] == [1, 2]
    assert os.path.exists(store.run_directory(result.run_id) / "run.json")
