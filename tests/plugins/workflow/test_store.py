from __future__ import annotations

import json
import os
import sqlite3
from collections import namedtuple

import pytest

from plugins.workflow.schema import load_workflow
from plugins.workflow.store import InputSnapshotError, RunStore, StorageQuotaError


def test_store_connection_context_closes_the_database_handle(tmp_path) -> None:
    store = RunStore(tmp_path / "home")

    with store._connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


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


def test_prepare_snapshot_captures_text_arguments_without_projecting_values(
    tmp_path, workflow_writer
):
    package = load_workflow(workflow_writer(tmp_path / "package"))
    store = RunStore(tmp_path / "home")

    prepared = store.prepare_run_snapshot(
        package, values={"arguments": "private argument"}
    )

    manifest = json.loads((prepared.staging_directory / "inputs.json").read_text())
    assert "private argument" not in json.dumps(manifest)
    captured = prepared.staging_directory / manifest["arguments"]["relative_path"]
    assert captured.read_text() == "private argument"


def test_prepare_snapshot_rejects_nonportable_input_filename_segments(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package"))
    store = RunStore(tmp_path / "home")

    for name in (
        "CON",
        "nul.txt",
        "COM1",
        "foo:bar",
        "a?b",
        "a*b",
        "<x>",
        "a|b",
        "trailing.",
        "trailing ",
        "😀" * 64,
        "COM¹",
        "COM².txt",
        "com³",
        "LPT¹",
        "lpt².log",
        "LPT³",
    ):
        with pytest.raises(InputSnapshotError, match="invalid.*input name"):
            store.prepare_run_snapshot(package, values={name: "value"})

    with pytest.raises(InputSnapshotError, match="invalid.*input name"):
        store.prepare_run_snapshot(
            package,
            values={"Mode": "first", "mode": "second"},
        )

    source = tmp_path / "report.txt"
    source.write_text("FILE", encoding="utf-8")
    with pytest.raises(InputSnapshotError, match="invalid.*input name"):
        store.prepare_run_snapshot(
            package,
            inputs={"report.txt": source},
            values={"report": "TEXT"},
        )


def test_prepare_snapshot_copies_digest_covered_package_resources(
    tmp_path, workflow_writer
):
    root = tmp_path / "package"
    workflow = workflow_writer(root, nodes=[{"id": "collect", "command": "collect"}])
    (root / "commands").mkdir()
    (root / "commands" / "collect.md").write_text("portable command")

    prepared = RunStore(tmp_path / "home").prepare_run_snapshot(load_workflow(workflow))

    assert (
        prepared.staging_directory / "commands" / "collect.md"
    ).read_text() == "portable command"


def test_prepare_snapshot_materializes_selected_skill_instructions(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[{"id": "analyze", "prompt": "Analyze", "skills": ["reports"]}],
        )
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("SNAPSHOTTED SKILL", names, []),
    )

    prepared = RunStore(tmp_path / "home").prepare_run_snapshot(package)

    assert (
        prepared.staging_directory / "node-skills" / "analyze.md"
    ).read_text() == "SNAPSHOTTED SKILL"


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


def test_prepare_snapshot_rejects_unreadable_or_changed_during_copy(
    tmp_path, workflow_writer, monkeypatch
):
    workflow = load_workflow(workflow_writer(tmp_path / "package"))
    source = tmp_path / "evidence.txt"
    source.write_text("original")
    store = RunStore(tmp_path / "home")
    original_read = type(source).read_bytes

    def unreadable(path):
        if path == source:
            raise PermissionError("denied")
        return original_read(path)

    monkeypatch.setattr(type(source), "read_bytes", unreadable)
    with pytest.raises(InputSnapshotError, match="unreadable"):
        store.prepare_run_snapshot(workflow, inputs={"evidence": source})

    def changed(path):
        data = original_read(path)
        if path == source:
            path.write_text("changed")
        return data

    monkeypatch.setattr(type(source), "read_bytes", changed)
    with pytest.raises(InputSnapshotError, match="changed during copy"):
        store.prepare_run_snapshot(workflow, inputs={"evidence": source})


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


def test_snapshot_refuses_low_disk_and_per_run_quota(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(workflow_writer(tmp_path / "package"))
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "plugins.workflow.store.shutil.disk_usage",
        lambda _path: usage(100 * 1024**3, 99 * 1024**3, 512 * 1024**2),
    )
    store = RunStore(tmp_path / "low-disk-home")
    with pytest.raises(StorageQuotaError, match="free_disk_watermark"):
        store.prepare_run_snapshot(package)
    assert list(store.staging_root.iterdir()) == []

    monkeypatch.setattr(
        "plugins.workflow.store.shutil.disk_usage",
        lambda _path: usage(100 * 1024**3, 50 * 1024**3, 50 * 1024**3),
    )
    store = RunStore(tmp_path / "quota-home", max_run_bytes=1)
    with pytest.raises(StorageQuotaError, match="run_storage_quota"):
        store.prepare_run_snapshot(package)
