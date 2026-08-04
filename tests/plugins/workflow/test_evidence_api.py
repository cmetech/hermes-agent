from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow import evidence as evidence_module
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.output_resolution import ArchonOutputIntegrityError
from plugins.workflow.schema import load_workflow
import plugins.workflow.store as store_module
from plugins.workflow.store import (
    ArtifactRef,
    RunStore,
    TypedPublicationCandidate,
)


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


def _published_store(
    tmp_path,
    workflow_writer,
    *,
    name: str,
    operator_scope: str | None = None,
    body: bytes = b"bounded publication body",
    output_type: str = "BoundedReport",
    session_id: str | None = "session-evidence",
):
    workflow = workflow_writer(
        tmp_path / "package",
        name=name,
        nodes=[{
            "id": "produce",
            "bash": "true",
            "output_type": output_type,
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
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
            operator_scope=operator_scope,
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "produce", "evidence-worker")
    assert claim is not None
    source = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / "output.md"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(body)
    relative_path = source.relative_to(
        store.run_directory(admitted.run_id)
    ).as_posix()
    digest = hashlib.sha256(body).hexdigest()
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(
            ArtifactRef(
                relative_path=relative_path,
                media_type="text/markdown; charset=utf-8",
                size_bytes=len(body),
                sha256=digest,
            ),
        ),
        typed_publication=TypedPublicationCandidate(
            attempt_relative_path=relative_path,
            output_type=output_type,
            media_type="text/markdown; charset=utf-8",
            size_bytes=len(body),
            sha256=digest,
            schema_fingerprint=None,
            canonicalization_version=1,
            session_id=session_id,
        ),
    )
    return store, admitted, claim, digest, len(body)


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

    assert page["items"] == [
        {
            "relative_path": "report.json",
            "sha256": "a" * 64,
            "size_bytes": 10,
            "media_type": "application/json",
        }
    ]


def test_typed_artifact_evidence_exposes_only_bounded_publication_metadata(
    tmp_path, workflow_writer
) -> None:
    store, admitted, claim, digest, size = _published_store(
        tmp_path, workflow_writer, name="typed-artifact-evidence"
    )

    page = EvidenceReader(store).query(admitted.run_id, kind="artifacts")

    assert page["schema_version"] == 1
    assert page["kind"] == "artifacts"
    assert page["truncated"] is False
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item == {
        "publication_id": item["publication_id"],
        "output_type": "BoundedReport",
        "media_type": "text/markdown; charset=utf-8",
        "size_bytes": size,
        "sha256": digest,
        "node_id": "produce",
        "attempt_id": claim.attempt_id,
        "schema_fingerprint": None,
        "produced_at": item["produced_at"],
        "session_id": "session-evidence",
        "integrity_status": "verified",
        "recovery_status": "verified",
    }
    assert len(item["publication_id"]) == 32
    assert len(item["produced_at"]) <= 64
    assert not {
        "body",
        "content",
        "relative_path",
        "path",
        "metadata_sha256",
    }.intersection(item)


def test_publication_lookup_revalidates_and_returns_verified_content(
    tmp_path, workflow_writer
) -> None:
    body = b"verified publication"
    store, admitted, _claim, digest, size = _published_store(
        tmp_path,
        workflow_writer,
        name="publication-lookup",
        body=body,
    )
    publication_id = EvidenceReader(store).query(
        admitted.run_id, kind="artifacts"
    )["items"][0]["publication_id"]

    publication = EvidenceReader(store).lookup_publication(
        admitted.run_id,
        publication_id,
    )

    assert publication.publication_id == publication_id
    assert publication.content_name == "content.md"
    assert publication.media_type == "text/markdown; charset=utf-8"
    assert publication.size_bytes == size
    assert publication.sha256 == digest
    assert publication.content == body


def test_run_store_publication_lookup_opens_only_requested_body(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        name="store-selective-lookup",
        nodes=[
            {
                "id": "produce-a",
                "bash": "true",
                "output_type": "ReportA",
            },
            {
                "id": "produce-b",
                "bash": "true",
                "output_type": "ReportB",
            },
            {
                "id": "finish",
                "bash": "true",
                "depends_on": ["produce-a", "produce-b"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="store-selective-lookup",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    expected_bodies = {
        "produce-a": b"REQUESTED_PUBLICATION",
        "produce-b": b"UNRELATED_PUBLICATION_MUST_NOT_BE_OPENED",
    }
    for node_id, body in expected_bodies.items():
        claim = store.claim_node(
            admitted.run_id,
            node_id,
            f"{node_id}-worker",
        )
        assert claim is not None
        source = (
            store.run_directory(admitted.run_id)
            / "nodes"
            / claim.node_id
            / claim.attempt_id
            / "output.md"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(body)
        relative = source.relative_to(
            store.run_directory(admitted.run_id)
        ).as_posix()
        digest = hashlib.sha256(body).hexdigest()
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(
                ArtifactRef(
                    relative,
                    "text/markdown; charset=utf-8",
                    len(body),
                    digest,
                ),
            ),
            typed_publication=TypedPublicationCandidate(
                attempt_relative_path=relative,
                output_type="ReportA" if node_id == "produce-a" else "ReportB",
                media_type="text/markdown; charset=utf-8",
                size_bytes=len(body),
                sha256=digest,
                schema_fingerprint=None,
                canonicalization_version=1,
                session_id=f"{node_id}-session",
            ),
        )
    projection = store.load_run(admitted.run_id)
    publications = {
        artifact["node_id"]: artifact
        for artifact in projection["artifacts"]
        if "publication_id" in artifact
    }
    requested = publications["produce-a"]
    unrelated = publications["produce-b"]
    unrelated_path = (
        f"publications/{unrelated['publication_id']}/"
        f"{unrelated['content_name']}"
    )
    real_read = store_module._read_descriptor_relative

    def reject_unrelated_body(directory, relative_path, *, size_bytes):
        if str(relative_path) == unrelated_path:
            pytest.fail("store lookup opened an unrelated publication body")
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        reject_unrelated_body,
    )

    publication = store.lookup_publication(
        admitted.run_id,
        requested["publication_id"],
    )

    assert publication.publication_id == requested["publication_id"]
    assert publication.content == expected_bodies["produce-a"]


def test_store_lookup_rejects_checked_descriptor_above_producer_metadata_bound(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store, admitted, _claim, _digest, _size = _published_store(
        tmp_path,
        workflow_writer,
        name="store-lookup-metadata-bound",
    )
    projection = store.load_run(admitted.run_id)
    artifacts = [dict(artifact) for artifact in projection["artifacts"]]
    descriptor = next(
        artifact for artifact in artifacts if "publication_id" in artifact
    )
    descriptor["output_type"] = "X" * 16_385
    store.append_event(
        admitted.run_id,
        "forged_publication_descriptor",
        projection_updates={"artifacts": artifacts},
    )
    publication_path = (
        f"publications/{descriptor['publication_id']}/"
        f"{descriptor['content_name']}"
    )
    real_read = store_module._read_descriptor_relative

    def reject_body_open(directory, relative_path, *, size_bytes):
        if str(relative_path) == publication_path:
            pytest.fail("oversized descriptor must be rejected before body open")
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        reject_body_open,
    )

    with pytest.raises(store_module.PublicationIntegrityError):
        store.lookup_publication(
            admitted.run_id,
            descriptor["publication_id"],
        )


@pytest.mark.parametrize(
    ("output_type", "session_id"),
    [
        ("X" * 16_385, "session"),
        ("Report", "S" * 16_385),
    ],
)
def test_typed_publication_producer_rejects_metadata_above_canonical_bound(
    tmp_path,
    workflow_writer,
    output_type,
    session_id,
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        name=f"producer-bound-{output_type[:1]}-{session_id[:1]}",
        nodes=[{
            "id": "produce",
            "bash": "true",
            "output_type": "Report",
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=package.definition.name,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "produce", "producer-worker")
    assert claim is not None
    body = b"producer-boundary"
    source = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / "output.md"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(body)
    relative = source.relative_to(store.run_directory(admitted.run_id)).as_posix()
    digest = hashlib.sha256(body).hexdigest()

    with pytest.raises(
        ArchonOutputIntegrityError,
        match="typed publication candidate is invalid",
    ):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(
                ArtifactRef(
                    relative,
                    "text/markdown; charset=utf-8",
                    len(body),
                    digest,
                ),
            ),
            typed_publication=TypedPublicationCandidate(
                attempt_relative_path=relative,
                output_type=output_type,
                media_type="text/markdown; charset=utf-8",
                size_bytes=len(body),
                sha256=digest,
                schema_fingerprint=None,
                canonicalization_version=1,
                session_id=session_id,
            ),
        )


@pytest.mark.parametrize(
    "publication_id",
    [
        "f" * 32,
        "../metadata.json",
        "a/b",
        "%2e%2e%2fcontent.md",
        "",
    ],
)
def test_publication_lookup_rejects_unknown_and_path_like_ids(
    tmp_path, workflow_writer, publication_id
) -> None:
    store, admitted, _claim, _digest, _size = _published_store(
        tmp_path,
        workflow_writer,
        name=f"publication-id-{hashlib.sha256(publication_id.encode()).hexdigest()[:8]}",
    )

    with pytest.raises(evidence_module.PublicationNotFoundError):
        EvidenceReader(store).lookup_publication(
            admitted.run_id,
            publication_id,
        )


def test_publication_lookup_authorizes_scope_before_opening_content(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store, admitted, _claim, _digest, _size = _published_store(
        tmp_path,
        workflow_writer,
        name="publication-scope",
        operator_scope="service:test:owner",
    )
    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        lambda *_args, **_kwargs: pytest.fail(
            "unauthorized publication body must not be opened"
        ),
    )

    with pytest.raises(KeyError):
        EvidenceReader(store).lookup_publication(
            admitted.run_id,
            "not-even-an-opaque-id",
            operator_scope="service:test:other",
        )


@pytest.mark.parametrize("mutation", ["size", "digest", "symlink"])
def test_publication_lookup_rejects_post_authorization_content_swaps(
    tmp_path, workflow_writer, monkeypatch, mutation
) -> None:
    original = b"original publication"
    store, admitted, _claim, _digest, _size = _published_store(
        tmp_path,
        workflow_writer,
        name=f"publication-swap-{mutation}",
        body=original,
    )
    item = EvidenceReader(store).query(
        admitted.run_id, kind="artifacts"
    )["items"][0]
    publication_id = item["publication_id"]
    content = (
        store.run_directory(admitted.run_id)
        / "publications"
        / publication_id
        / "content.md"
    )
    real_run_directory = store.run_directory
    swapped = False

    def authorize_then_swap(*args, **kwargs):
        nonlocal swapped
        directory = real_run_directory(*args, **kwargs)
        if not swapped:
            swapped = True
            if mutation == "size":
                content.write_bytes(original + b"x")
            elif mutation == "digest":
                content.write_bytes(b"x" * len(original))
            else:
                replacement = tmp_path / "outside-publication"
                replacement.write_bytes(original)
                content.unlink()
                content.symlink_to(replacement)
        return directory

    monkeypatch.setattr(store, "run_directory", authorize_then_swap)

    with pytest.raises(evidence_module.PublicationIntegrityError):
        EvidenceReader(store).lookup_publication(
            admitted.run_id,
            publication_id,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_type", "application/octet-stream"),
        ("media_type", []),
        ("content_name", "metadata.json"),
        ("size_bytes", True),
        ("sha256", "not-a-digest"),
    ],
)
def test_publication_lookup_rejects_untrusted_descriptor_fields(
    tmp_path, workflow_writer, monkeypatch, field, value
) -> None:
    store, admitted, _claim, _digest, _size = _published_store(
        tmp_path,
        workflow_writer,
        name=f"publication-descriptor-{field}",
    )
    publication_id = EvidenceReader(store).query(
        admitted.run_id, kind="artifacts"
    )["items"][0]["publication_id"]
    projection = store.load_run(admitted.run_id)
    artifacts = [dict(artifact) for artifact in projection["artifacts"]]
    descriptor = next(
        artifact
        for artifact in artifacts
        if artifact.get("publication_id") == publication_id
    )
    descriptor[field] = value
    store.append_event(
        admitted.run_id,
        "forged_publication_descriptor",
        projection_updates={"artifacts": artifacts},
    )
    real_read = store_module._read_descriptor_relative

    def reject_publication_body(directory, relative_path, *, size_bytes):
        if str(relative_path).startswith(f"publications/{publication_id}/"):
            pytest.fail("invalid descriptor must be rejected before body open")
        return real_read(
            directory,
            relative_path,
            size_bytes=size_bytes,
        )

    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        reject_publication_body,
    )

    with pytest.raises(evidence_module.PublicationIntegrityError):
        EvidenceReader(store).lookup_publication(
            admitted.run_id,
            publication_id,
        )


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


class _Phase3ProjectionStore:
    def __init__(self, run: dict[str, object]) -> None:
        self.run = run

    def get_run_status(self, _run_id: str, *, operator_scope=None):
        return self.run


def test_phase3_attempt_evidence_names_requested_effective_retry_and_error_fields() -> None:
    store = _Phase3ProjectionStore({
        "nodes": {
            "agent": {
                "attempts": [
                    {
                        "attempt_id": "attempt-1",
                        "state": "failed",
                        "error_code": "provider_timeout",
                        "error_message": "bounded failure",
                        "metadata": {
                            "requested_retries": 5,
                            "requested_total_attempts": 6,
                            "effective_total_attempts": 5,
                            "retry_consumed": 2,
                            "remaining_attempts": 3,
                            "additional_provider_attempts": 2,
                            "capped": True,
                            "provider_response": "not projected",
                        },
                    }
                ]
            }
        }
    })

    page = EvidenceReader(store).query("run-1", kind="attempts")

    assert page["items"] == [
        {
            "node_id": "agent",
            "attempt_id": "attempt-1",
            "state": "failed",
            "retry": {
                "requested_retries": 5,
                "requested_total_attempts": 6,
                "effective_total_attempts": 5,
                "retry_consumed": 2,
                "remaining_attempts": 3,
                "additional_provider_attempts": 2,
                "capped": True,
            },
            "error": {"code": "provider_timeout", "message": "bounded failure"},
        }
    ]


def test_persistent_session_recovery_evidence_is_a_closed_bounded_projection() -> None:
    store = _Phase3ProjectionStore({
        "nodes": {
            "agent": {
                "session_recoveries": [
                    {
                        "attempt_id": "attempt-1",
                        "registry_generation": 7,
                        "source": "cross_run_registry",
                        "provider": "test-provider",
                        "runtime_profile": "default",
                        "provider_attempts_before_recovery": 0,
                        "outcome": "stale_entry_replaced",
                        "missing_session_sha256": "a" * 64,
                        "cache_fingerprint_sha256": "b" * 64,
                        "pending_session_registry_update": {"key": "not projected"},
                        "provider_history": ["not projected"],
                    }
                ]
            }
        }
    })

    page = EvidenceReader(store).query("run-1", kind="recovery")

    assert page["items"] == [
        {
            "node_id": "agent",
            "attempt_id": "attempt-1",
            "recovery_kind": "persistent_session",
            "registry_generation": 7,
            "missing_session_sha256": "a" * 64,
            "cache_fingerprint_sha256": "b" * 64,
            "source": "cross_run_registry",
            "provider": "test-provider",
            "runtime_profile": "default",
            "provider_attempts_before_recovery": 0,
            "outcome": "stale_entry_replaced",
        }
    ]
    assert len(page["items"]) <= 200
