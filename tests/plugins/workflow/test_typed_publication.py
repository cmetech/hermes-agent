from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from agent.plugin_agent import PluginAgentRunResult
import plugins.workflow.store as store_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    PrimaryOutputCandidate,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import (
    ArtifactRef,
    RunStore,
    TypedPublicationCandidate,
)


def _node(kind: str, *, output_type: str) -> dict[str, object]:
    value: object = {
        "command": "produce",
        "prompt": "produce",
        "bash": "true",
        "script": "print('produce')",
        "loop": {"prompt": "produce", "until": "DONE", "max_iterations": 1},
        "approval": {"message": "approve?"},
        "cancel": "stop",
    }[kind]
    node = {"id": "produce", kind: value, "output_type": output_type}
    if kind == "script":
        node["runtime"] = "uv"
    return node


def _start_archon(
    store: RunStore,
    workflow_writer,
    root: Path,
    node,
    *,
    profile: str | None = "archon-2026-07",
):
    workflow = workflow_writer(root, name="typed-publication", nodes=[node])
    if "command" in node:
        (root / "commands").mkdir()
        (root / "commands" / "produce.md").write_text(
            "---\ndescription: Produce\n---\nProduce", encoding="utf-8"
        )
    if profile is not None:
        workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
            f"language_compatibility: {profile}\n", encoding="utf-8"
        )
    package = load_workflow(workflow)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=root.name,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    return admitted


class _OutputExecutor:
    def __init__(
        self,
        data: bytes,
        media_type: str,
        *,
        status: str = "succeeded",
        artifact_media_type: str | None = None,
    ):
        self.data = data
        self.media_type = media_type
        self.status = status
        self.artifact_media_type = artifact_media_type or media_type

    def execute(self, context):
        suffix = "json" if self.media_type == "application/json" else "md"
        path = (
            context.run_directory
            / "nodes"
            / context.node.id
            / context.attempt_id
            / f"output.{suffix}"
        )
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_bytes(self.data)
        relative = path.relative_to(context.run_directory).as_posix()
        digest = hashlib.sha256(self.data).hexdigest()
        artifact = ArtifactRef(
            relative,
            self.artifact_media_type,
            len(self.data),
            digest,
        )
        return NodeExecutionResult(
            self.status,
            (artifact,),
            metadata={"session_id": "session-1"},
            primary_output=PrimaryOutputCandidate(
                attempt_relative_path=relative,
                media_type=self.media_type,
                size_bytes=len(self.data),
                sha256=digest,
                structured_value=(
                    json.loads(self.data)
                    if self.media_type == "application/json"
                    else None
                ),
                schema_fingerprint=None,
                canonicalization_version=1,
                output_type=context.node.options.get("output_type"),
            ),
        )


class _AgentRunner:
    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        return PluginAgentRunResult(
            final_response=self.response,
            session_id="session-1",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


@pytest.mark.parametrize(
    ("kind", "data", "published_media_type", "content_name"),
    [
        (
            "command",
            b"command output",
            "text/markdown; charset=utf-8",
            "content.md",
        ),
        ("prompt", b"", "text/markdown; charset=utf-8", "content.md"),
    ],
)
def test_production_ai_output_node_publishes_one_atomic_typed_bundle(
    tmp_path,
    workflow_writer,
    kind,
    data,
    published_media_type,
    content_name,
) -> None:
    output_type = "MixedCase/Result-分析"
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / kind,
        _node(kind, output_type=output_type),
    )
    scheduler = RunScheduler(
        store,
        agent_runner=_AgentRunner(data.decode("utf-8")),
    )

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    artifact = result["artifacts"][0]
    publication_id = artifact["publication_id"]
    bundle = store.run_directory(admitted.run_id) / "publications" / publication_id
    metadata_bytes = (bundle / "metadata.json").read_bytes()
    metadata = json.loads(metadata_bytes)
    assert (bundle / content_name).read_bytes() == data
    assert sorted(path.name for path in bundle.iterdir()) == [
        content_name,
        "metadata.json",
    ]
    assert artifact["content_name"] == content_name
    assert artifact["output_type"] == output_type
    assert artifact["media_type"] == published_media_type
    assert artifact["metadata_sha256"] == hashlib.sha256(metadata_bytes).hexdigest()
    assert metadata == {
        "attempt_id": artifact["attempt_id"],
        "canonicalization_version": 1,
        "content_name": content_name,
        "language_profile": "archon-2026-07",
        "media_type": published_media_type,
        "node_id": "produce",
        "output_type": output_type,
        "produced_at": metadata["produced_at"],
        "publication_id": publication_id,
        "run_id": admitted.run_id,
        "schema_fingerprint": None,
        "session_id": "session-1",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    assert len(metadata_bytes) <= 65_536
    assert all(
        identity not in publication_id
        for identity in (admitted.run_id, "produce", artifact["attempt_id"])
    )


def test_production_cancel_never_publishes(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "cancel",
        _node("cancel", output_type="CancellationReceipt"),
    )
    scheduler = RunScheduler(store)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "cancelled"
    assert not (store.run_directory(admitted.run_id) / "publications").exists()
    assert all("publication_id" not in artifact for artifact in result["artifacts"])


def test_hermes_legacy_primary_output_completes_without_publication(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "legacy",
        _node("prompt", output_type="LegacyResult"),
        profile=None,
    )
    scheduler = RunScheduler(store)
    scheduler.executors["prompt"] = _OutputExecutor(b"legacy output", "text/plain")
    observed_error = None

    try:
        result = scheduler.advance(admitted.run_id)
    except RuntimeError as exc:
        observed_error = exc
        result = store.load_run(admitted.run_id)

    assert observed_error is None
    assert result["status"] == "succeeded"
    assert not (store.run_directory(admitted.run_id) / "publications").exists()
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert set(artifact) == {
        "attempt_id",
        "media_type",
        "node_id",
        "relative_path",
        "sha256",
        "size_bytes",
    }
    assert artifact["attempt_id"] == result["nodes"]["produce"]["attempts"][-1][
        "attempt_id"
    ]
    assert artifact["media_type"] == "text/plain"
    assert artifact["node_id"] == "produce"
    assert artifact["relative_path"].endswith("/output.md")
    assert artifact["sha256"] == hashlib.sha256(b"legacy output").hexdigest()
    assert artifact["size_bytes"] == len(b"legacy output")


def test_typed_publication_rejects_candidate_artifact_media_disagreement(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "media-disagreement",
        _node("bash", output_type="Report"),
    )
    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = _OutputExecutor(
        b"report",
        "text/plain",
        artifact_media_type="application/json",
    )

    with pytest.raises(
        ArchonOutputIntegrityError,
        match="does not match one executor artifact",
    ):
        scheduler.advance(admitted.run_id)

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


def test_typed_publication_rejects_conflicting_same_path_artifact(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "conflicting-descriptor",
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"report")
    conflicting = ArtifactRef(
        artifact.relative_path,
        "application/json",
        artifact.size_bytes,
        artifact.sha256,
    )

    with pytest.raises(ArchonOutputIntegrityError, match="conflicting"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact, conflicting),
            typed_publication=candidate,
        )

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


def _attempt_publication(
    store: RunStore,
    claim,
    data: bytes,
    *,
    output_type: str = "Report",
    media_type: str = "text/markdown; charset=utf-8",
    path_attempt_id: str | None = None,
) -> tuple[ArtifactRef, TypedPublicationCandidate]:
    path = (
        store.run_directory(claim.run_id)
        / "nodes"
        / claim.node_id
        / (path_attempt_id or claim.attempt_id)
        / "output.md"
    )
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_bytes(data)
    relative = path.relative_to(store.run_directory(claim.run_id)).as_posix()
    digest = hashlib.sha256(data).hexdigest()
    artifact = ArtifactRef(relative, media_type, len(data), digest)
    candidate = TypedPublicationCandidate(
        attempt_relative_path=relative,
        output_type=output_type,
        media_type=media_type,
        size_bytes=len(data),
        sha256=digest,
        schema_fingerprint=None,
        canonicalization_version=1,
        session_id=None,
    )
    return artifact, candidate


def test_typed_publication_rejects_contained_output_from_another_attempt(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "wrong-attempt",
        _node("bash", output_type="Report"),
    )
    active = store.claim_node(admitted.run_id, "produce", "active")
    assert active is not None
    artifact, candidate = _attempt_publication(
        store,
        active,
        b"foreign attempt",
        path_attempt_id="attempt-that-does-not-own-the-claim",
    )

    with pytest.raises(ArchonOutputIntegrityError, match="active attempt"):
        store.complete_node(
            active,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


@pytest.mark.parametrize("media_type", ["text/markdown", "application/octet-stream"])
def test_typed_publication_rejects_noncanonical_text_media_type(
    tmp_path, workflow_writer, media_type
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / media_type.replace("/", "-"),
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(
        store, claim, b"text", media_type=media_type
    )

    with pytest.raises(ArchonOutputIntegrityError, match="media type"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


def test_typed_publication_rejects_invalid_utf8_markdown(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "invalid-utf8",
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"\xff\xfe")

    with pytest.raises(ArchonOutputIntegrityError, match="UTF-8"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


def test_typed_publication_rejects_preexisting_publications_symlink(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "publication-symlink",
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"report")
    outside = tmp_path / "outside"
    outside.mkdir()
    publications = store.run_directory(admitted.run_id) / "publications"
    publications.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArchonOutputIntegrityError, match="publication directory"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    assert list(outside.iterdir()) == []


def test_typed_publication_atomically_rejects_existing_final_name(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "publication-collision",
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"report")
    publication_id = "a" * 32
    monkeypatch.setattr(
        store_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=publication_id),
    )
    final = store.run_directory(admitted.run_id) / "publications" / publication_id
    final.mkdir(parents=True)
    sentinel = final / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ArchonOutputIntegrityError, match="already exists"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in final.parent.iterdir()) == [publication_id]


def test_typed_publication_rejects_publications_directory_swap_at_commit(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "publication-swap",
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"report")
    run_directory = store.run_directory(admitted.run_id)
    publications = run_directory / "publications"
    displaced = run_directory / "publications-displaced"
    original_commit = getattr(
        store_module,
        "_commit_publication_directory_noreplace",
        None,
    )

    def swap_then_commit(*args, **kwargs):
        publications.rename(displaced)
        publications.mkdir()
        assert original_commit is not None
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        store_module,
        "_commit_publication_directory_noreplace",
        swap_then_commit,
        raising=False,
    )

    with pytest.raises(ArchonOutputIntegrityError, match="identity changed"):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    projection = store.load_run(admitted.run_id)
    assert projection["nodes"]["produce"]["state"] != "succeeded"
    assert not any(
        event["event_type"] == "node_succeeded"
        for event in store.tail_events(admitted.run_id)
    )


@pytest.mark.parametrize("failure_boundary", ["staging", "publication parent"])
def test_typed_publication_directory_fsync_failure_prevents_completion(
    tmp_path, workflow_writer, monkeypatch, failure_boundary
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / failure_boundary.replace(" ", "-"),
        _node("bash", output_type="Report"),
    )
    claim = store.claim_node(admitted.run_id, "produce", "active")
    assert claim is not None
    artifact, candidate = _attempt_publication(store, claim, b"report")
    original_fsync = getattr(
        store_module,
        "_fsync_publication_directory",
        None,
    )

    def fail_boundary(descriptor, *, boundary):
        if boundary == failure_boundary:
            raise OSError(f"{boundary} fsync failed")
        if original_fsync is not None:
            return original_fsync(descriptor, boundary=boundary)
        return None

    monkeypatch.setattr(
        store_module,
        "_fsync_publication_directory",
        fail_boundary,
        raising=False,
    )

    with pytest.raises(OSError, match=failure_boundary):
        store.complete_node(
            claim,
            status="succeeded",
            artifacts=(artifact,),
            typed_publication=candidate,
        )

    projection = store.load_run(admitted.run_id)
    assert projection["nodes"]["produce"]["state"] != "succeeded"
    assert not any(
        event["event_type"] == "node_succeeded"
        for event in store.tail_events(admitted.run_id)
    )


def test_stale_typed_completion_cannot_create_publication_staging_or_final_content(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "stale",
        _node("bash", output_type="Report"),
    )
    stale = store.claim_node(admitted.run_id, "produce", "stale", lease_seconds=1)
    assert stale is not None
    assert store.expire_stale_claims(
        admitted.run_id, now=stale.lease_expires_at + timedelta(seconds=1)
    ) == ("produce",)
    store.resume_run(
        admitted.run_id,
        always_run_nodes=RunScheduler(store).verified_always_run_nodes(admitted.run_id),
    )
    active = store.claim_node(admitted.run_id, "produce", "active")
    assert active is not None
    stale_artifact, stale_candidate = _attempt_publication(store, stale, b"loser")

    with pytest.raises(RuntimeError, match="stale node completion"):
        store.complete_node(
            stale,
            status="succeeded",
            artifacts=(stale_artifact,),
            typed_publication=stale_candidate,
        )

    assert not (store.run_directory(admitted.run_id) / "publications").exists()


def test_concurrent_completions_publish_only_the_active_attempt(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    output_type = "CaseSensitive/" + ("Ω" * 200)
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "winner",
        _node("bash", output_type=output_type),
    )
    stale = store.claim_node(admitted.run_id, "produce", "stale", lease_seconds=1)
    assert stale is not None
    assert store.expire_stale_claims(
        admitted.run_id, now=stale.lease_expires_at + timedelta(seconds=1)
    ) == ("produce",)
    store.resume_run(
        admitted.run_id,
        always_run_nodes=RunScheduler(store).verified_always_run_nodes(admitted.run_id),
    )
    active = store.claim_node(admitted.run_id, "produce", "active")
    assert active is not None
    stale_artifact, stale_candidate = _attempt_publication(store, stale, b"loser")
    active_artifact, active_candidate = _attempt_publication(
        store,
        active,
        b"winner",
        output_type=output_type,
    )
    start = threading.Barrier(2)

    def finish(claim, artifact, candidate):
        start.wait(timeout=5)
        try:
            store.complete_node(
                claim,
                status="succeeded",
                artifacts=(artifact,),
                typed_publication=candidate,
            )
        except RuntimeError as exc:
            return str(exc)
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda args: finish(*args),
                (
                    (stale, stale_artifact, stale_candidate),
                    (active, active_artifact, active_candidate),
                ),
            )
        )

    assert sorted(outcomes) == ["completed", "stale node completion"], outcomes
    projection = store.load_run(admitted.run_id)
    published = [
        artifact for artifact in projection["artifacts"] if "publication_id" in artifact
    ]
    assert len(published) == 1
    assert published[0]["attempt_id"] == active.attempt_id
    assert published[0]["relative_path"] == active_artifact.relative_path
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / published[0]["publication_id"]
    )
    metadata_bytes = (bundle / "metadata.json").read_bytes()
    assert len(metadata_bytes) <= 65_536
    assert b"loser" not in metadata_bytes
    assert (bundle / "content.md").read_bytes() == b"winner"
    completion = next(
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "node_succeeded"
    )
    assert completion["payload"]["artifacts"] == published
