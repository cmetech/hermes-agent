from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat

import pytest

import plugins.workflow.store as store_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.output_resolution import ArchonOutputIntegrityError
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import (
    ArtifactRef,
    JournalRecoveryError,
    RunStore,
    StorageQuotaError,
    TypedPublicationCandidate,
)


def _start_archon(
    store: RunStore,
    workflow_writer,
    root: Path,
    *,
    persist_sessions: bool = False,
    operator_scope: str | None = None,
    nodes: list[dict[str, object]] | None = None,
):
    workflow = workflow_writer(
        root,
        name="typed-recovery",
        persist_sessions=persist_sessions,
        nodes=(
            nodes
            if nodes is not None
            else [{"id": "produce", "bash": "true", "output_type": "Report"}]
        ),
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=root.name,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
            operator_scope=operator_scope,
        ),
        immutable_snapshot=prepared,
    )


def _candidate(
    store: RunStore,
    claim,
    data: bytes = b"durable report",
    *,
    schema_fingerprint: str | None = None,
    media_type: str = "text/markdown; charset=utf-8",
):
    filename = "output.json" if media_type == "application/json" else "output.md"
    source = (
        store.run_directory(claim.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / filename
    )
    source.parent.mkdir(parents=True, exist_ok=False)
    source.write_bytes(data)
    relative = source.relative_to(store.run_directory(claim.run_id)).as_posix()
    digest = hashlib.sha256(data).hexdigest()
    return (
        source,
        ArtifactRef(
            relative,
            media_type,
            len(data),
            digest,
        ),
        TypedPublicationCandidate(
            attempt_relative_path=relative,
            output_type="Report",
            media_type=media_type,
            size_bytes=len(data),
            sha256=digest,
            schema_fingerprint=schema_fingerprint,
            canonicalization_version=1,
            session_id="session-1",
        ),
    )


def _complete(store: RunStore, claim, artifact, candidate) -> None:
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(artifact,),
        typed_publication=candidate,
        metadata={
            "session_id": "session-1",
            "cache_fingerprint": "cache-1",
            "provider": "fake",
            "model": "fake",
        },
    )


def _published(projection: dict[str, object]) -> dict[str, object]:
    return next(
        artifact
        for artifact in projection["artifacts"]
        if "publication_id" in artifact
    )


def _rewrite_latest_projection(
    store: RunStore,
    run_id: str,
    mutation,
) -> None:
    directory = store.run_directory(run_id)
    journal = directory / "events.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    latest = json.loads(lines[-1])
    mutation(latest["projection"])
    latest["projection_sha256"] = store_module._projection_digest(
        latest["projection"]
    )
    _validated, encoded = store_module._encode_journal_frame(latest)
    journal.write_bytes(b"".join(lines[:-1]) + encoded)
    (directory / "run.json").unlink()


def _forged_metadata_digest(bundle: Path, **updates: object) -> str:
    metadata = json.loads((bundle / "metadata.json").read_bytes())
    metadata.update(updates)
    encoded = json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


class _ReparseStat:
    def __init__(self, observed: object) -> None:
        self._observed = observed
        self.st_file_attributes = getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._observed, name)


@pytest.mark.parametrize(
    ("boundary", "journaled"),
    [
        ("content_before", False),
        ("content_after", False),
        ("metadata_before", False),
        ("metadata_after", False),
        ("staging_fsync_before", False),
        ("staging_fsync_after", False),
        ("rename_before", False),
        ("rename_after", False),
        ("journal_append_before", False),
        ("journal_append_after", True),
        ("projection_replace_before", True),
        ("projection_replace_after", True),
    ],
)
def test_recovery_converges_every_publication_crash_boundary(
    tmp_path,
    workflow_writer,
    monkeypatch,
    boundary: str,
    journaled: bool,
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start_archon(store, workflow_writer, tmp_path / boundary)
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    _source, artifact, candidate = _candidate(store, claim)

    if boundary.startswith("content_") or boundary.startswith("metadata_"):
        original = store_module._write_publication_file
        target_call = 1 if boundary.startswith("content_") else 2
        after = boundary.endswith("_after")
        calls = 0

        def fail_file(descriptor, name, data):
            nonlocal calls
            calls += 1
            if calls == target_call and not after:
                raise OSError(f"{boundary} crash")
            result = original(descriptor, name, data)
            if calls == target_call and after:
                raise OSError(f"{boundary} crash")
            return result

        monkeypatch.setattr(store_module, "_write_publication_file", fail_file)
    elif boundary.startswith("staging_fsync_"):
        original = store_module._fsync_publication_directory
        after = boundary.endswith("_after")

        def fail_fsync(descriptor, *, boundary: str):
            if boundary == "staging" and not after:
                raise OSError("staging fsync crash")
            result = original(descriptor, boundary=boundary)
            if boundary == "staging" and after:
                raise OSError("staging fsync crash")
            return result

        monkeypatch.setattr(store_module, "_fsync_publication_directory", fail_fsync)
    elif boundary.startswith("rename_"):
        original = store_module._commit_publication_directory_noreplace
        after = boundary.endswith("_after")

        def fail_rename(*args, **kwargs):
            if not after:
                raise OSError("rename crash")
            original(*args, **kwargs)
            raise OSError("rename crash")

        monkeypatch.setattr(
            store_module,
            "_commit_publication_directory_noreplace",
            fail_rename,
        )
    elif boundary == "journal_append_before":
        original = store._append_locked

        def fail_append(directory, projection, event_type, *args, **kwargs):
            if event_type == "node_succeeded":
                raise OSError("journal append crash")
            return original(directory, projection, event_type, *args, **kwargs)

        monkeypatch.setattr(store, "_append_locked", fail_append)
    else:
        original = store_module._atomic_json
        after = boundary.endswith("_after")
        armed = True

        def fail_projection(path, value):
            nonlocal armed
            if Path(path).name != "run.json" or not armed:
                return original(path, value)
            armed = False
            if boundary == "journal_append_after" or not after:
                raise OSError(f"{boundary} crash")
            original(path, value)
            raise OSError(f"{boundary} crash")

        monkeypatch.setattr(store_module, "_atomic_json", fail_projection)

    with pytest.raises(OSError, match="crash"):
        _complete(store, claim, artifact, candidate)

    monkeypatch.undo()
    restarted = RunStore(home)
    projection = restarted.load_run(admitted.run_id)
    publications = restarted.run_directory(admitted.run_id) / "publications"
    entries = list(publications.iterdir()) if publications.exists() else []
    assert not any(path.name.startswith(".staging-") for path in entries)
    finals = [path for path in entries if not path.name.startswith(".")]
    if journaled:
        published = _published(projection)
        assert projection["nodes"]["produce"]["state"] == "succeeded"
        assert [path.name for path in finals] == [published["publication_id"]]
        assert (finals[0] / "content.md").read_bytes() == b"durable report"
        assert (finals[0] / "metadata.json").is_file()
    else:
        assert projection["nodes"]["produce"]["state"] != "succeeded"
        assert finals == []


def test_recovery_reconstructs_only_the_journaled_winning_attempt_bytes(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start_archon(store, workflow_writer, tmp_path / "reconstruct")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    source, artifact, candidate = _candidate(store, claim, b"winning bytes")
    _complete(store, claim, artifact, candidate)
    projection = store.load_run(admitted.run_id)
    published = _published(projection)
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / published["publication_id"]
    )
    shutil.rmtree(bundle)
    distractor = source.parent.parent / "not-the-winner" / "output.md"
    distractor.parent.mkdir()
    distractor.write_bytes(b"newer but unauthorized")

    recovered = RunStore(home).load_run(admitted.run_id)

    restored = (
        store.run_directory(admitted.run_id)
        / "publications"
        / _published(recovered)["publication_id"]
    )
    assert (restored / "content.md").read_bytes() == b"winning bytes"
    assert json.loads((restored / "metadata.json").read_bytes())["attempt_id"] == (
        claim.attempt_id
    )


def test_recovery_refuses_missing_bundle_when_winning_attempt_digest_changed(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start_archon(store, workflow_writer, tmp_path / "digest-mismatch")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    source, artifact, candidate = _candidate(store, claim, b"winning bytes")
    _complete(store, claim, artifact, candidate)
    projection = store.load_run(admitted.run_id)
    published = _published(projection)
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / published["publication_id"]
    )
    shutil.rmtree(bundle)
    source.write_bytes(b"forged bytes!")

    with pytest.raises(JournalRecoveryError, match="typed publication integrity"):
        RunStore(home).load_run(admitted.run_id)

    restarted = RunStore(home)
    assert restarted._active_run_repair_reasons(admitted.run_id) == (
        "typed_publication_integrity",
    )
    assert not bundle.exists()


def test_invalid_journaled_descriptor_records_stable_publication_repair_evidence(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start_archon(store, workflow_writer, tmp_path / "bad-descriptor")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    _source, artifact, candidate = _candidate(store, claim, b"journaled bytes")
    _complete(store, claim, artifact, candidate)
    directory = store.run_directory(admitted.run_id)
    journal = directory / "events.jsonl"
    events = [json.loads(line) for line in journal.read_bytes().splitlines()]
    latest = events[-1]
    publication = _published(latest["projection"])
    publication["metadata_sha256"] = "invalid"
    latest["projection_sha256"] = store_module._projection_digest(
        latest["projection"]
    )
    _validated, encoded = store_module._encode_journal_frame(latest)
    lines = journal.read_bytes().splitlines(keepends=True)
    journal.write_bytes(b"".join(lines[:-1]) + encoded)
    (directory / "run.json").unlink()

    with pytest.raises(JournalRecoveryError, match="descriptor is invalid"):
        store.load_run(admitted.run_id)

    assert "typed_publication_integrity" in store._active_run_repair_reasons(
        admitted.run_id
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "demoted",
        "duplicate",
        "wrong_output_type",
        "missing_schema_fingerprint",
        "wrong_schema_fingerprint",
        "unexpected_schema_fingerprint",
    ],
)
def test_checked_journal_requires_exact_sealed_typed_publication_contract(
    tmp_path,
    workflow_writer,
    corruption: str,
) -> None:
    structured = corruption in {
        "missing_schema_fingerprint",
        "wrong_schema_fingerprint",
    }
    nodes: list[dict[str, object]] = [
        {"id": "produce", "bash": "true", "output_type": "Report"}
    ]
    if structured:
        nodes[0]["output_format"] = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / corruption,
        nodes=nodes,
    )
    admitted_projection = store.load_run(admitted.run_id)
    structured_outputs = admitted_projection["language"]["structured_outputs"]
    schema_fingerprint = (
        structured_outputs["produce"]["schema_fingerprint"]
        if structured
        else None
    )
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    data = b'{"answer":"ok"}' if structured else b"sealed report"
    _source, artifact, candidate = _candidate(
        store,
        claim,
        data,
        schema_fingerprint=schema_fingerprint,
        media_type="application/json" if structured else "text/markdown; charset=utf-8",
    )
    _complete(store, claim, artifact, candidate)
    projection = store.load_run(admitted.run_id)
    publication = _published(projection)
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / publication["publication_id"]
    )
    original_content = (bundle / publication["content_name"]).read_bytes()
    original_metadata = (bundle / "metadata.json").read_bytes()

    def corrupt(checked: dict[str, object]) -> None:
        artifacts = checked["artifacts"]
        descriptor = _published(checked)
        if corruption == "missing":
            artifacts.remove(descriptor)
        elif corruption == "demoted":
            for field in (
                "publication_id",
                "content_name",
                "output_type",
                "metadata_sha256",
                "schema_fingerprint",
                "canonicalization_version",
                "produced_at",
                "session_id",
            ):
                descriptor.pop(field)
        elif corruption == "duplicate":
            duplicate = dict(descriptor)
            duplicate["publication_id"] = "b" * 32
            duplicate["metadata_sha256"] = _forged_metadata_digest(
                bundle,
                publication_id="b" * 32,
            )
            artifacts.append(duplicate)
        elif corruption == "wrong_output_type":
            descriptor["output_type"] = "DifferentReport"
            descriptor["metadata_sha256"] = _forged_metadata_digest(
                bundle,
                output_type="DifferentReport",
            )
        elif corruption == "missing_schema_fingerprint":
            descriptor["schema_fingerprint"] = None
            descriptor["metadata_sha256"] = _forged_metadata_digest(
                bundle,
                schema_fingerprint=None,
            )
        elif corruption == "wrong_schema_fingerprint":
            descriptor["schema_fingerprint"] = "f" * 64
            descriptor["metadata_sha256"] = _forged_metadata_digest(
                bundle,
                schema_fingerprint="f" * 64,
            )
        else:
            descriptor["schema_fingerprint"] = "e" * 64
            descriptor["metadata_sha256"] = _forged_metadata_digest(
                bundle,
                schema_fingerprint="e" * 64,
            )

    _rewrite_latest_projection(store, admitted.run_id, corrupt)

    with pytest.raises(JournalRecoveryError, match="typed publication"):
        store.load_run(admitted.run_id)

    assert (bundle / publication["content_name"]).read_bytes() == original_content
    assert (bundle / "metadata.json").read_bytes() == original_metadata
    assert "typed_publication_integrity" in store._active_run_repair_reasons(
        admitted.run_id
    )


def test_recovery_removes_unjournaled_final_bundle_as_one_unit(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(store, workflow_writer, tmp_path / "orphan")
    run_directory = store.run_directory(admitted.run_id)
    orphan = run_directory / "publications" / ("f" * 32)
    orphan.mkdir(parents=True)
    (orphan / "content.md").write_bytes(b"orphan")
    (orphan / "metadata.json").write_text("{}\n", encoding="utf-8")

    store.load_run(admitted.run_id)

    assert not orphan.exists()


def test_publication_quota_failure_exposes_neither_content_nor_metadata(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(store, workflow_writer, tmp_path / "quota")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    _source, artifact, candidate = _candidate(store, claim, b"quota report")
    run_directory = store.run_directory(admitted.run_id)
    store.max_run_bytes = store._directory_bytes(run_directory)

    with pytest.raises(StorageQuotaError, match="run_storage_quota"):
        _complete(store, claim, artifact, candidate)

    publications = run_directory / "publications"
    assert not publications.exists() or list(publications.iterdir()) == []


def test_journaled_bundle_symlink_is_never_followed_during_recovery(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(store, workflow_writer, tmp_path / "symlink")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    _source, artifact, candidate = _candidate(store, claim, b"safe bytes")
    _complete(store, claim, artifact, candidate)
    published = _published(store.load_run(admitted.run_id))
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / published["publication_id"]
    )
    shutil.rmtree(bundle)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    bundle.symlink_to(outside, target_is_directory=True)

    recovered = store.load_run(admitted.run_id)

    restored = (
        store.run_directory(admitted.run_id)
        / "publications"
        / _published(recovered)["publication_id"]
    )
    assert not restored.is_symlink()
    assert (restored / "content.md").read_bytes() == b"safe bytes"
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("reparse_branch", ["root", "bundle", "source"])
def test_publication_reparse_points_fail_closed_without_touching_external_data(
    tmp_path,
    workflow_writer,
    monkeypatch,
    reparse_branch: str,
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / f"reparse-{reparse_branch}",
    )
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    source, artifact, candidate = _candidate(store, claim, b"safe bytes")
    _complete(store, claim, artifact, candidate)
    published = _published(store.load_run(admitted.run_id))
    publications = store.run_directory(admitted.run_id) / "publications"
    bundle = publications / published["publication_id"]
    if reparse_branch == "source":
        shutil.rmtree(bundle)
    target = {
        "root": publications,
        "bundle": bundle,
        "source": source,
    }[reparse_branch]
    sentinel = tmp_path / "external-sentinel"
    sentinel.write_bytes(b"outside remains untouched")
    original_lstat = Path.lstat

    def injected_reparse(path: Path):
        observed = original_lstat(path)
        if path == target:
            return _ReparseStat(observed)
        return observed

    monkeypatch.setattr(Path, "lstat", injected_reparse)

    with pytest.raises(JournalRecoveryError, match="typed publication integrity"):
        store.load_run(admitted.run_id)

    assert sentinel.read_bytes() == b"outside remains untouched"
    assert "typed_publication_integrity" in store._active_run_repair_reasons(
        admitted.run_id
    )


def test_recovery_rejects_non_regular_winning_attempt_source(
    tmp_path, workflow_writer
) -> None:
    if not hasattr(store_module.os, "mkfifo"):
        pytest.skip("FIFO is POSIX-specific")
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(store, workflow_writer, tmp_path / "fifo")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    source, artifact, candidate = _candidate(store, claim, b"safe bytes")
    _complete(store, claim, artifact, candidate)
    published = _published(store.load_run(admitted.run_id))
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / published["publication_id"]
    )
    shutil.rmtree(bundle)
    source.unlink()
    store_module.os.mkfifo(source)

    with pytest.raises(JournalRecoveryError, match="typed publication integrity"):
        store.load_run(admitted.run_id)


def test_invalid_publication_candidate_traversal_remains_rejected_before_journal(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(store, workflow_writer, tmp_path / "traversal")
    claim = store.claim_node(admitted.run_id, "produce", "owner")
    assert claim is not None
    source, artifact, candidate = _candidate(store, claim, b"safe bytes")
    escaped = TypedPublicationCandidate(
        attempt_relative_path="../" + source.name,
        output_type=candidate.output_type,
        media_type=candidate.media_type,
        size_bytes=candidate.size_bytes,
        sha256=candidate.sha256,
        schema_fingerprint=candidate.schema_fingerprint,
        canonicalization_version=candidate.canonicalization_version,
        session_id=candidate.session_id,
    )

    with pytest.raises(ArchonOutputIntegrityError):
        _complete(store, claim, artifact, escaped)
