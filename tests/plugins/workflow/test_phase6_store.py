from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.models import LoopGroupChildScope
from plugins.workflow.provider_authority import (
    ProviderAuthorityEnvironment,
    resolve_workflow_provider_authority,
)
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import ArtifactRef, RunStore
from plugins.workflow.trust import WorkflowPackageDigest


_EXECUTION_AUTHORITY = {
    "schema_version": 1,
    "retry_consumed_before": 0,
    "remaining_attempts": 1,
    "iteration_consumed_before": 0,
    "remaining_iterations": 3,
    "remaining_wall_seconds": 60.0,
}


def _compile_group(tmp_path: Path, workflow_writer):
    workflow = workflow_writer(
        tmp_path / "source",
        name="phase6-store",
        filename="phase6-store.yaml",
        nodes=[
            {"id": "outside", "bash": "printf outside"},
            {
                "id": "group",
                "loop_group": {
                    "until": "DONE",
                    "max_iterations": 3,
                    "nodes": [
                        {"id": "select", "bash": "printf select"},
                        {
                            "id": "record",
                            "bash": "printf record",
                            "depends_on": ["select"],
                        },
                    ],
                },
            },
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    workflow.with_name("phase6-store.hermes.yaml").write_bytes(sidecar)
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=6,
    )


def _admit_group(
    tmp_path: Path,
    workflow_writer,
    *,
    max_total_workers: int = 4,
):
    compilation = _compile_group(tmp_path, workflow_writer)
    home = tmp_path / "home"
    store = RunStore(home, max_total_workers=max_total_workers)
    model_config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        }
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    authority = resolve_workflow_provider_authority(
        compilation.package,
        model_config=model_config,
        default_runtime=runtime,
        environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=False,
        ),
    )
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="phase6-store",
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    group = next(
        node for node in compilation.package.definition.nodes if node.id == "group"
    )
    return home, store, admitted.run_id, group


def _initialize(store: RunStore, run_id: str, group) -> dict[str, object]:
    before = store.load_run(run_id)
    assert store.initialize_loop_group(
        run_id,
        group.id,
        group.value["nodes"],
        max_iterations=group.value["max_iterations"],
        primary_sink="record",
        expected_state_version=before["state_version"],
    )
    return store.load_run(run_id)


def _scope(run_id: str, node_id: str, *, iteration: int = 1):
    return LoopGroupChildScope(run_id, "group", 1, iteration, node_id)


def _output(
    store: RunStore,
    scope: LoopGroupChildScope,
    attempt_id: str,
    value: bytes,
):
    relative = (
        f"nodes/{scope.group_id}/{scope.controller_generation}/iterations/"
        f"{scope.iteration:04d}/nodes/{scope.node_id}/{attempt_id}/output.txt"
    )
    path = store.run_directory(scope.run_id) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    digest = hashlib.sha256(value).hexdigest()
    artifact = ArtifactRef(relative, "text/plain", len(value), digest)
    candidate = {
        "attempt_relative_path": relative,
        "media_type": "text/plain",
        "size_bytes": len(value),
        "sha256": digest,
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": None,
    }
    return artifact, candidate


def _complete(
    store: RunStore,
    scope: LoopGroupChildScope,
    claim,
    value: bytes,
):
    artifact, candidate = _output(store, scope, claim.attempt_id, value)
    before = store.load_run(scope.run_id)
    store.complete_loop_group_child(
        scope,
        claim,
        status="succeeded",
        artifacts=(artifact,),
        metadata={"primary_output_candidate": candidate},
        expected_state_version=before["state_version"],
    )
    return candidate


def test_controller_initializes_once_without_consuming_the_only_worker(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(
        tmp_path, workflow_writer, max_total_workers=1
    )

    initialized = _initialize(store, run_id, group)
    controller = initialized["nodes"]["group"]["loop_group"]

    assert controller == {
        "schema_version": 1,
        "controller_generation": 1,
        "iteration": 1,
        "max_iterations": 3,
        "state": "running",
        "primary_sink": "record",
        "previous_outputs": {},
        "body": {
            "select": {
                "id": "select",
                "type": "bash",
                "depends_on": [],
                "state": "ready",
                "attempts": [],
            },
            "record": {
                "id": "record",
                "type": "bash",
                "depends_on": ["select"],
                "state": "pending",
                "attempts": [],
            },
        },
    }
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0

    version = initialized["state_version"]
    assert not store.initialize_loop_group(
        run_id,
        group.id,
        group.value["nodes"],
        max_iterations=3,
        primary_sink="record",
        expected_state_version=version,
    )
    assert store.load_run(run_id)["state_version"] == version


def test_child_claim_uses_existing_capacity_and_cross_store_atomicity(
    tmp_path, workflow_writer
) -> None:
    home, store, run_id, group = _admit_group(
        tmp_path, workflow_writer, max_total_workers=1
    )
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    peer = RunStore(home, max_total_workers=1)

    def _concurrent_claim(candidate):
        try:
            return candidate.claim_loop_group_child(
                scope,
                "worker",
                expected_state_version=initialized["state_version"],
                max_run_workers=1,
                execution_authority=_EXECUTION_AUTHORITY,
            )
        except RuntimeError as exc:
            assert str(exc) == "stale loop group state version"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(pool.map(_concurrent_claim, (store, peer)))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    claim = winners[0]
    assert claim.loop_group_scope == scope
    with store._connect() as connection:
        row = connection.execute(
            "SELECT run_id, node_id, owner_id FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()
        assert tuple(row) == (run_id, scope.worker_node_id, "worker")
    assert store.claim_node(
        run_id,
        "outside",
        "outside-worker",
        max_run_workers=1,
        execution_authority=_EXECUTION_AUTHORITY,
    ) is None


def test_child_completion_records_scoped_attempt_output_and_artifact(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert claim is not None
    candidate = _complete(store, scope, claim, b"selected")

    completed = store.load_run(run_id)
    body = completed["nodes"]["group"]["loop_group"]["body"]
    select = body["select"]
    assert select["state"] == "succeeded"
    assert select["output"] == candidate
    assert select["attempts"][-1]["attempt_id"] == claim.attempt_id
    assert select["attempts"][-1]["loop_group_scope"] == {
        "run_id": run_id,
        "group_id": "group",
        "controller_generation": 1,
        "iteration": 1,
        "node_id": "select",
        "worker_node_id": scope.worker_node_id,
    }
    assert select["artifacts"] == [
        {
            "node_id": scope.worker_node_id,
            "attempt_id": claim.attempt_id,
            "relative_path": candidate["attempt_relative_path"],
            "media_type": "text/plain",
            "size_bytes": len(b"selected"),
            "sha256": candidate["sha256"],
            "loop_group_scope": select["attempts"][-1]["loop_group_scope"],
        }
    ]
    assert body["record"]["state"] == "ready"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 0

    stale_version = completed["state_version"]
    with pytest.raises(RuntimeError, match="stale loop group child completion"):
        store.complete_loop_group_child(
            scope,
            claim,
            status="succeeded",
            expected_state_version=stale_version,
        )
    assert store.load_run(run_id)["state_version"] == stale_version


def test_iteration_commit_carries_only_output_descriptors_and_rejects_stale_scope(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    select_scope = _scope(run_id, "select")
    select_claim = store.claim_loop_group_child(
        select_scope,
        "select-worker",
        expected_state_version=initialized["state_version"],
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert select_claim is not None
    select_output = _complete(store, select_scope, select_claim, b"selected")
    ready = store.load_run(run_id)
    record_scope = _scope(run_id, "record")
    record_claim = store.claim_loop_group_child(
        record_scope,
        "record-worker",
        expected_state_version=ready["state_version"],
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert record_claim is not None
    record_output = _complete(store, record_scope, record_claim, b"recorded")
    before_commit = store.load_run(run_id)

    assert store.record_loop_group_iteration(
        record_scope,
        expected_state_version=before_commit["state_version"],
    )

    committed = store.load_run(run_id)
    controller = committed["nodes"]["group"]["loop_group"]
    assert controller["iteration"] == 2
    assert controller["previous_outputs"] == {
        "select": select_output,
        "record": record_output,
    }
    assert controller["body"]["select"]["state"] == "ready"
    assert controller["body"]["select"]["attempts"] == []
    assert controller["body"]["record"]["state"] == "pending"
    assert controller["body"]["record"]["attempts"] == []
    assert "metadata" not in controller["previous_outputs"]["select"]

    stale_scope = _scope(run_id, "record", iteration=1)
    version = committed["state_version"]
    with pytest.raises(RuntimeError, match="stale loop group iteration"):
        store.record_loop_group_iteration(
            stale_scope,
            expected_state_version=version,
        )
    with pytest.raises(RuntimeError, match="stale loop group state version"):
        store.claim_loop_group_child(
            _scope(run_id, "select", iteration=2),
            "worker",
            expected_state_version=version - 1,
            execution_authority=_EXECUTION_AUTHORITY,
        )
    assert store.load_run(run_id)["state_version"] == version


def test_fail_loop_group_is_generation_and_state_version_fenced(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    stale = LoopGroupChildScope(run_id, "group", 2, 1, "select")

    with pytest.raises(RuntimeError, match="stale loop group generation"):
        store.fail_loop_group(
            stale,
            error_code="group_failed",
            error_message="failed",
            expected_state_version=initialized["state_version"],
        )

    current = _scope(run_id, "select")
    assert store.fail_loop_group(
        current,
        error_code="group_failed",
        error_message="failed",
        expected_state_version=initialized["state_version"],
    )
    failed = store.load_run(run_id)
    assert failed["status"] == "failed"
    assert failed["nodes"]["group"]["state"] == "failed"
    assert failed["nodes"]["group"]["loop_group"]["state"] == "failed"


def test_child_heartbeat_and_stale_expiry_use_existing_claim_lifecycle(
    tmp_path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claimed_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        now=claimed_at,
        monotonic_now=100.0,
        lease_seconds=10.0,
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert claim is not None
    store.mark_node_started(claim)
    assert store.renew_claim(
        claim,
        now=claimed_at + timedelta(seconds=2),
        monotonic_now=102.0,
        lease_seconds=10.0,
        heartbeat_interval_seconds=1.0,
    )

    expired = store.expire_stale_claims(
        run_id,
        now=claimed_at + timedelta(seconds=13),
        monotonic_now=113.0,
    )

    assert expired == (scope.worker_node_id,)
    projection = store.load_run(run_id)
    child = projection["nodes"]["group"]["loop_group"]["body"]["select"]
    assert projection["status"] == "interrupted"
    assert child["state"] == "interrupted"
    assert child["recovery"]["attempt_id"] == claim.attempt_id
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE attempt_id=?",
            (claim.attempt_id,),
        ).fetchone()[0] == 0


def test_run_cancellation_clears_nested_claim_and_controller(
    tmp_path, workflow_writer
) -> None:
    home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert claim is not None

    cancelled = store.cancel_run(run_id)

    controller = cancelled["nodes"]["group"]["loop_group"]
    child = controller["body"]["select"]
    assert cancelled["status"] == "cancelled"
    assert controller["state"] == "cancelled"
    assert child["state"] == "cancelled"
    assert "claim" not in child
    restarted = RunStore(home).load_run(run_id)
    assert "claim" not in restarted["nodes"]["group"]["loop_group"]["body"][
        "select"
    ]
