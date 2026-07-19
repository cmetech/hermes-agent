from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow import evidence as evidence_module
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _admitted_store(tmp_path, workflow_writer, *, name: str):
    package = load_workflow(workflow_writer(tmp_path / "package", name=name))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=f"{name}-intent",
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    return store, admitted


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


def test_log_evidence_rejects_symlink_outside_run(
    tmp_path, workflow_writer
) -> None:
    store, admitted = _admitted_store(
        tmp_path, workflow_writer, name="symlink-log"
    )
    secret = tmp_path / "outside-secret"
    secret.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
    stdout = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / "n1"
        / "a1"
        / "stdout.txt"
    )
    stdout.parent.mkdir(parents=True)
    stdout.symlink_to(secret)

    page = EvidenceReader(store).query(admitted.run_id, kind="logs")

    assert "OUTSIDE_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]


def test_log_evidence_rejects_symlinked_parent_outside_run(
    tmp_path, workflow_writer
) -> None:
    store, admitted = _admitted_store(
        tmp_path, workflow_writer, name="symlink-parent"
    )
    outside_attempt = tmp_path / "outside-node" / "a1"
    outside_attempt.mkdir(parents=True)
    (outside_attempt / "stdout.txt").write_text(
        "PARENT_ESCAPE_SENTINEL", encoding="utf-8"
    )
    nodes = store.run_directory(admitted.run_id) / "nodes"
    nodes.mkdir(exist_ok=True)
    (nodes / "n1").symlink_to(outside_attempt.parent, target_is_directory=True)

    page = EvidenceReader(store).query(admitted.run_id, kind="logs")

    assert "PARENT_ESCAPE_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]


def test_fallback_reparse_attribute_is_rejected_before_open(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    candidate = root / "stdout.txt"
    candidate.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
    regular = candidate.lstat()
    reparse = SimpleNamespace(
        st_mode=regular.st_mode,
        st_dev=regular.st_dev,
        st_ino=regular.st_ino,
        st_size=regular.st_size,
        st_file_attributes=evidence_module._FILE_ATTRIBUTE_REPARSE_POINT,
    )
    real_lstat = Path.lstat

    def reparse_lstat(path):
        return reparse if path == candidate else real_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        evidence_module.os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("reparse path must not be opened"),
    )

    with pytest.raises(evidence_module._UnsafeEvidencePath):
        evidence_module._read_fallback_contained_file(
            root, candidate.relative_to(root), 1024
        )


def test_fallback_identity_swap_after_open_is_rejected_before_read(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    candidate = root / "stdout.txt"
    candidate.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
    regular = candidate.lstat()
    swapped = SimpleNamespace(
        st_mode=regular.st_mode,
        st_dev=regular.st_dev,
        st_ino=regular.st_ino + 1,
        st_size=regular.st_size,
        st_file_attributes=0,
    )
    observations = iter((regular, swapped))
    monkeypatch.setattr(
        evidence_module,
        "_reject_reparse_components",
        lambda _root, _relative: next(observations),
    )
    monkeypatch.setattr(
        evidence_module,
        "_read_descriptor",
        lambda *_args, **_kwargs: pytest.fail(
            "identity-swapped path must not be read"
        ),
    )

    with pytest.raises(evidence_module._UnsafeEvidencePath):
        evidence_module._read_fallback_contained_file(
            root, candidate.relative_to(root), 1024
        )


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows reparse points")
def test_windows_log_evidence_rejects_real_reparse_escape(
    tmp_path, workflow_writer
) -> None:
    store, admitted = _admitted_store(
        tmp_path, workflow_writer, name="windows-reparse-log"
    )
    outside_attempt = tmp_path / "outside-node" / "a1"
    outside_attempt.mkdir(parents=True)
    (outside_attempt / "stdout.txt").write_text(
        "WINDOWS_REPARSE_SENTINEL", encoding="utf-8"
    )
    nodes = store.run_directory(admitted.run_id) / "nodes"
    nodes.mkdir(exist_ok=True)
    link = nodes / "n1"
    try:
        os.symlink(outside_attempt.parent, link, target_is_directory=True)
    except OSError as symlink_error:
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside_attempt.parent)],
            capture_output=True,
            text=True,
        )
        if junction.returncode != 0:
            pytest.fail(
                "could not create hostile Windows symlink or junction: "
                f"symlink={symlink_error}; junction={junction.stderr.strip()}"
            )

    page = EvidenceReader(store).query(admitted.run_id, kind="logs")

    assert "WINDOWS_REPARSE_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]


def test_regular_log_evidence_is_sanitized_and_aggregate_bounded(
    tmp_path, workflow_writer
) -> None:
    store, admitted = _admitted_store(tmp_path, workflow_writer, name="regular-log")
    stdout = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / "n1"
        / "a1"
        / "stdout.txt"
    )
    stdout.parent.mkdir(parents=True)
    stdout.write_bytes(b"visible\x1b[31m" + b"x" * (300 * 1024))

    page = EvidenceReader(store).query(admitted.run_id, kind="logs")

    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["text"].startswith("visible")
    assert "\x1b" not in item["text"]
    assert item["bytes_returned"] == 256 * 1024
    assert item["truncated"] is True
    assert "warnings" not in page
