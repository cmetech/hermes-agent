from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowStoreRegistry,
)
from plugins.workflow.schema import load_workflow
import plugins.workflow.store as store_module
from plugins.workflow.store import (
    ArtifactRef,
    RunStore,
    TypedPublicationCandidate,
)


class _Store:
    def __init__(self, home: Path) -> None:
        self.home = home


def test_registry_reuses_profile_store_and_evicts_only_idle_lru(tmp_path) -> None:
    created: list[Path] = []

    def factory(home: Path):
        created.append(home)
        return _Store(home)

    registry = WorkflowStoreRegistry(max_profiles=2, store_factory=factory)
    one = tmp_path / "one"
    two = tmp_path / "two"
    three = tmp_path / "three"

    with registry.lease(one) as first:
        with registry.lease(one) as repeated:
            assert repeated is first
        with registry.lease(two):
            with pytest.raises(StoreRegistryCapacityError):
                with registry.lease(three):
                    pass

    with registry.lease(three):
        pass

    assert created == [one.resolve(), two.resolve(), three.resolve()]
    assert registry.snapshot()["profiles"] == 2


@pytest.mark.parametrize(
    "values",
    [
        {"max_cached_profiles": 0},
        {"max_event_waiters": 0},
        {"store_io_workers": 0},
        {"max_cached_profiles": True},
        {"max_event_waiters": "many"},
    ],
)
def test_api_limits_reject_zero_unbounded_or_non_integer_values(values) -> None:
    with pytest.raises(ValueError):
        WorkflowApiLimits.from_mapping(values)


def test_api_limit_defaults_are_bounded() -> None:
    limits = WorkflowApiLimits.from_mapping({})
    assert limits.max_cached_profiles == 8
    assert limits.max_event_waiters == 16
    assert limits.store_io_workers == 4


def _queued_run_with_real_typed_publication(
    tmp_path, workflow_writer, monkeypatch
) -> tuple[RunStore, str, str, str]:
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    workflow = workflow_writer(
        tmp_path / "package",
        name="coordinator-metadata-only",
        nodes=[
            {
                "id": "produce",
                "bash": "true",
                "output_type": "CoordinatorReport",
            },
            {
                "id": "retry",
                "bash": "false",
                "depends_on": ["produce"],
                "retry": {"max_attempts": 2},
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home", max_executing_runs=1)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="coordinator-metadata-only",
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "produce", "publication-worker")
    assert claim is not None
    body = b"REAL_JOURNALED_PUBLICATION_BODY"
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
            output_type="CoordinatorReport",
            media_type="text/markdown; charset=utf-8",
            size_bytes=len(body),
            sha256=digest,
            schema_fingerprint=None,
            canonicalization_version=1,
            session_id="coordinator-session",
        ),
    )
    assert store.transition_pending_nodes(
        admitted.run_id,
        {"retry": ("ready", None)},
    ) == ("retry",)
    retry_claim = store.claim_node(
        admitted.run_id,
        "retry",
        "retry-worker",
    )
    assert retry_claim is not None
    store.schedule_retry(
        retry_claim,
        next_attempt_at=now - timedelta(seconds=1),
    )

    blocker = load_workflow(
        workflow_writer(tmp_path / "blocker", name="capacity-blocker")
    )
    blocker_snapshot = store.prepare_run_snapshot(blocker)
    blocked_by = store.start_run(
        RunAdmissionRequest(
            workflow_name=blocker.definition.name,
            definition_digest=blocker_snapshot.definition_digest,
            policy_digest=blocker_snapshot.policy_digest,
            input_manifest_digest=blocker_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="capacity-blocker",
            concurrency_key=blocker.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=blocker_snapshot,
    )
    assert blocked_by.disposition == "created"
    assert store.wake_due_retries(admitted.run_id, now=now) == ()
    queued = store.load_run(admitted.run_id)
    assert queued["status"] == "queued"
    publication = next(
        artifact
        for artifact in queued["artifacts"]
        if "publication_id" in artifact
    )
    return (
        store,
        admitted.run_id,
        publication["publication_id"],
        publication["content_name"],
    )


def test_queued_coordinator_candidate_scan_never_opens_real_artifact_bodies(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store, run_id, publication_id, content_name = (
        _queued_run_with_real_typed_publication(
            tmp_path,
            workflow_writer,
            monkeypatch,
        )
    )
    real_read = store_module._read_descriptor_relative

    def reject_publication_body(directory, relative_path, *, size_bytes):
        if str(relative_path) == (
            f"publications/{publication_id}/{content_name}"
        ):
            pytest.fail(
                "queued coordinator scan must not open publication bodies"
            )
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

    candidates, _cursor, _exhausted = store.coordinator_candidates(
        after=None,
        now=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert any(
        candidate["run_id"] == run_id and candidate["status"] == "queued"
        for candidate in candidates
    )


def test_coordinator_candidate_scan_never_opens_unjournaled_artifact_bodies(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="coordinator-metadata-only")
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="coordinator-metadata-only",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    body = (
        store.run_directory(admitted.run_id)
        / "publications"
        / ("a" * 32)
        / "content.md"
    )
    body.parent.mkdir(parents=True)
    body.write_bytes(b"BODY_MUST_NOT_BE_OPENED")
    monkeypatch.setattr(
        store_module,
        "_read_descriptor_relative",
        lambda *_args, **_kwargs: pytest.fail(
            "coordinator candidate scan must not open artifact bodies"
        ),
    )

    candidates, _cursor, _exhausted = store.coordinator_candidates(
        after=None,
        now=datetime.now(timezone.utc),
    )

    assert [candidate["run_id"] for candidate in candidates] == [admitted.run_id]
