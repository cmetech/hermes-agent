from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.output_resolution import PrimaryOutputCandidate
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


def _start_archon(store: RunStore, workflow_writer, root: Path, node):
    workflow = workflow_writer(root, name="typed-publication", nodes=[node])
    if "command" in node:
        (root / "commands").mkdir()
        (root / "commands" / "produce.md").write_text(
            "---\ndescription: Produce\n---\nProduce", encoding="utf-8"
        )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
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
    def __init__(self, data: bytes, media_type: str, *, status: str = "succeeded"):
        self.data = data
        self.media_type = media_type
        self.status = status

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
        artifact = ArtifactRef(relative, self.media_type, len(self.data), digest)
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


@pytest.mark.parametrize(
    ("kind", "data", "media_type", "content_name"),
    [
        ("command", b'{"answer":1}', "application/json", "content.json"),
        ("prompt", b"", "text/markdown", "content.md"),
        ("bash", b"bash output", "text/markdown", "content.md"),
        ("script", b'{"script":true}', "application/json", "content.json"),
        ("loop", b"loop output", "text/markdown", "content.md"),
        ("approval", b"approved", "text/markdown", "content.md"),
    ],
)
def test_each_successful_output_node_publishes_one_atomic_typed_bundle(
    tmp_path, workflow_writer, kind, data, media_type, content_name
) -> None:
    output_type = "MixedCase/Result-分析"
    store = RunStore(tmp_path / "home")
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / kind,
        _node(kind, output_type=output_type),
    )
    scheduler = RunScheduler(store)
    scheduler.executors[kind] = _OutputExecutor(data, media_type)

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
    assert artifact["metadata_sha256"] == hashlib.sha256(metadata_bytes).hexdigest()
    assert metadata == {
        "attempt_id": artifact["attempt_id"],
        "canonicalization_version": 1,
        "content_name": content_name,
        "language_profile": "archon-2026-07",
        "media_type": media_type,
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


def test_cancel_never_publishes_even_when_an_executor_returns_output(
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
    scheduler.executors["cancel"] = _OutputExecutor(
        b"must not publish", "text/markdown", status="cancelled"
    )

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "cancelled"
    assert not (store.run_directory(admitted.run_id) / "publications").exists()
    assert all("publication_id" not in artifact for artifact in result["artifacts"])


def _attempt_publication(
    store: RunStore,
    claim,
    data: bytes,
    *,
    output_type: str = "Report",
) -> tuple[ArtifactRef, TypedPublicationCandidate]:
    path = (
        store.run_directory(claim.run_id)
        / "nodes"
        / claim.node_id
        / claim.attempt_id
        / "output.md"
    )
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_bytes(data)
    relative = path.relative_to(store.run_directory(claim.run_id)).as_posix()
    digest = hashlib.sha256(data).hexdigest()
    artifact = ArtifactRef(relative, "text/markdown", len(data), digest)
    candidate = TypedPublicationCandidate(
        attempt_relative_path=relative,
        output_type=output_type,
        media_type="text/markdown",
        size_bytes=len(data),
        sha256=digest,
        schema_fingerprint=None,
        canonicalization_version=1,
        session_id=None,
    )
    return artifact, candidate


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
    admitted = _start_archon(
        store,
        workflow_writer,
        tmp_path / "winner",
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
    active_artifact, active_candidate = _attempt_publication(
        store,
        active,
        b"winner",
        output_type="CaseSensitive/" + ("Ω" * 200),
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
