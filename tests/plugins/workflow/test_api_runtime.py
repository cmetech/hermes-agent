from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.api_admission import ApiAdmissionAuthority, start_api_run
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.runner_binding import (
    assess_package_execution,
    background_execution_context,
    production_workflow_runner_binding,
)
from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowStoreRegistry,
)
from plugins.workflow.schema import load_workflow
import plugins.workflow.store as store_module
from plugins.workflow.store import (
    ArtifactRef,
    JournalRecoveryError,
    RunStore,
    TypedPublicationCandidate,
)
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)
import yaml


def test_api_v4_admission_assesses_child_executable_resources_from_compilation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    """Catch API risk assessment reopening the root for child-owned resources."""
    import plugins.workflow.language as language_module
    from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation
    from plugins.workflow.compat import assess_compatibility
    from plugins.workflow.models import WorkflowLanguageProfile

    monkeypatch.setattr(
        language_module,
        "CURRENT_NORMALIZER_BY_PROFILE",
        MappingProxyType({
            WorkflowLanguageProfile.HERMES_LEGACY: 2,
            WorkflowLanguageProfile.ARCHON_2026_07: 4,
        }),
    )
    home = tmp_path / "profile"
    workdir = tmp_path / "project"
    root = workflow_writer(
        workdir / ".hermes/workflows",
        name="api-child-resource-root",
        filename="api-child-resource-root.yaml",
        nodes=[{"id": "child", "include": "api-child-resource"}],
    )
    root.with_name("api-child-resource-root.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    child = workflow_writer(
        home / "workflows",
        name="api-child-resource",
        filename="api-child-resource.yaml",
        nodes=[
            {
                "id": "execute",
                "script": "child.py",
                "runtime": "uv",
            }
        ],
    )
    scripts = child.parent.parent / "scripts"
    scripts.mkdir()
    (scripts / "child.py").write_text("print('child')\n", encoding="utf-8")
    compilation = resolve_workflow_catalog_compilation(
        "api-child-resource-root",
        hermes_home=home,
        workdir=workdir,
        normalizer_version=4,
    )
    assert compilation is not None
    binding = production_workflow_runner_binding()
    context = background_execution_context(binding, requires_ai=False)
    compatibility = assess_compatibility(
        compilation.package,
        mcp_available=context.mcp_available,
        structured_output_decisions=context.structured_output_decisions(
            compilation.package
        ),
    )
    risk = build_risk_summary(
        compilation.package,
        compatibility,
        compilation=compilation,
    )
    WorkflowTrustStore(home).trust(
        compilation.composite_digest,
        actor="api-child-resource-test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="api-child-resource-test",
            host_kind="web",
            host_instance_id="api-child-resource-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader
    import plugins.workflow.trust as trust_module

    risk_builds = 0
    original_build_risk_summary = trust_module.build_risk_summary

    def counting_build_risk_summary(*args, **kwargs):
        nonlocal risk_builds
        risk_builds += 1
        return original_build_risk_summary(*args, **kwargs)

    monkeypatch.setattr(
        trust_module,
        "build_risk_summary",
        counting_build_risk_summary,
    )

    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=workdir,
        user_home=tmp_path,
        workflow_name="api-child-resource-root",
        values={},
        idempotency_key="api-child-resource",
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="api-child-resource-test",
            namespace="api-child-resource-test",
            operator_scope=None,
            source_instance="desktop:api-child-resource-test",
            assurance="local_admin_claim",
            trigger_source="desktop",
        ),
        catalog_source="project",
        runner_binding=binding,
    )

    run = store.load_run(str(admitted["run_id"]))
    assert risk_builds == 1
    assert run["snapshot_format_version"] == 2
    assert run["definition_digest"] == compilation.composite_digest


class _Store:
    def __init__(self, home: Path) -> None:
        self.home = home


def test_api_admission_seals_resolved_profile_execution_authority(
    tmp_path, workflow_writer
) -> None:
    from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation

    home = tmp_path / "profile"
    path = workflow_writer(
        home / "workflows",
        name="archon-sealed-api-limits",
        filename="archon-sealed-api-limits.yaml",
        nodes=[{"id": "start", "bash": "true"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {
                "entries": {
                    "workflow": {
                        "runtime": {
                            "ai_idle_timeout_seconds": 120,
                            "ai_wall_timeout_seconds": 240,
                            "provider_request_timeout_seconds": 90,
                            "subprocess_timeout_seconds": 30,
                            "combined_retries": 2,
                        }
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    compilation = resolve_workflow_catalog_compilation(
        path.stem,
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="profile",
    )
    assert compilation is not None
    package = compilation.package
    binding = production_workflow_runner_binding()
    context = background_execution_context(binding, requires_ai=False)
    _compatibility, risk = assess_package_execution(
        package,
        context,
        compilation=compilation,
    )
    WorkflowTrustStore(home).trust(
        compilation.composite_digest,
        actor="test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    coordinator = CoordinatorStore(store.database)
    acquired = coordinator.try_acquire(
        CoordinatorIdentity(
            owner_id="api-sealed-limits",
            host_kind="web",
            host_instance_id="api-sealed-limits",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader

    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="archon-sealed-api-limits",
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="api-sealed-limits",
            namespace="api-sealed-limits",
            operator_scope=None,
            source_instance="desktop:api-sealed-limits",
            assurance="local_admin_claim",
            trigger_source="desktop",
        ),
        catalog_source="profile",
        runner_binding=binding,
    )
    resources = json.loads(
        (
            store.run_directory(str(admitted["run_id"])) / "resources.json"
        ).read_bytes()
    )

    assert resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 120.0,
        "ai_wall_timeout_seconds": 240.0,
        "provider_request_timeout_seconds": 90.0,
        "subprocess_timeout_seconds": 30.0,
        "combined_total_attempts": 2,
    }


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


def _forge_checked_typed_descriptor(
    store: RunStore,
    run_id: str,
    corruption: str,
) -> tuple[str, str]:
    projection = store.load_run(run_id)
    artifacts = deepcopy(projection["artifacts"])
    descriptor = next(
        artifact for artifact in artifacts if "publication_id" in artifact
    )
    updates: dict[str, object] = {"artifacts": artifacts}
    if corruption == "unknown_media":
        descriptor["media_type"] = "application/octet-stream"
    elif corruption == "invalid_size_type":
        descriptor["size_bytes"] = True
    elif corruption == "duplicate_publication_id":
        artifacts.append(dict(descriptor))
    elif corruption == "non_winning_attempt":
        nodes = deepcopy(projection["nodes"])
        winner = next(
            attempt
            for attempt in nodes[descriptor["node_id"]]["attempts"]
            if attempt["attempt_id"] == descriptor["attempt_id"]
        )
        winner["state"] = "failed"
        updates["nodes"] = nodes
    elif corruption == "sealed_output_type_mismatch":
        descriptor["output_type"] = "ForgedOutputType"
    elif corruption == "sealed_schema_mismatch":
        descriptor["schema_fingerprint"] = "f" * 64
    elif corruption == "legacy_unversioned":
        descriptor.pop("typed_publication_version")
    else:
        raise AssertionError(f"unknown corruption fixture: {corruption}")
    store.append_event(
        run_id,
        "forged_typed_descriptor",
        projection_updates=updates,
    )
    return descriptor["publication_id"], descriptor["content_name"]


@pytest.mark.parametrize(
    "corruption",
    [
        "unknown_media",
        "invalid_size_type",
        "duplicate_publication_id",
        "non_winning_attempt",
        "sealed_output_type_mismatch",
        "sealed_schema_mismatch",
        "legacy_unversioned",
    ],
)
def test_queued_coordinator_rejects_corrupt_checked_typed_metadata_without_body_reads(
    tmp_path,
    workflow_writer,
    monkeypatch,
    corruption,
) -> None:
    store, run_id, _publication_id, _content_name = (
        _queued_run_with_real_typed_publication(
            tmp_path,
            workflow_writer,
            monkeypatch,
        )
    )
    publication_id, content_name = _forge_checked_typed_descriptor(
        store,
        run_id,
        corruption,
    )
    publication_path = f"publications/{publication_id}/{content_name}"
    real_read = store_module._read_descriptor_relative

    def reject_publication_body(directory, relative_path, *, size_bytes):
        if str(relative_path) == publication_path:
            pytest.fail(
                "coordinator metadata validation must not open publication bodies"
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

    with pytest.raises(JournalRecoveryError):
        store.coordinator_candidates(
            after=None,
            now=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        )

    assert "typed_publication_integrity" in store._active_run_repair_reasons(
        run_id
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
