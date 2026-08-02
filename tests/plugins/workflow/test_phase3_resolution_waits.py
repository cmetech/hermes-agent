from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import time

import pytest

from plugins.workflow import scheduler as scheduler_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.models import ExecutionFence
from plugins.workflow.output_resolution import (
    ArchonOutputUnavailableError,
    WorkflowOutputReferenceError,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import ArtifactRef, RunStore
from tools.managed_process import ProcessIdentity


_PRODUCER_IDENTITY = {
    "node_id": "producer",
    "attempt_id": "attempt-winner",
    "publication_id": "a" * 32,
    "sha256": "b" * 64,
    "size_bytes": 5,
    "media_type": "text/markdown; charset=utf-8",
    "schema_fingerprint": None,
    "canonicalization_version": 1,
    "output_type": "text",
}


def _start_archon_run(tmp_path, workflow_writer, *, name: str) -> tuple[RunStore, str]:
    package_path = workflow_writer(
        tmp_path / name,
        name=name,
        nodes=[
            {"id": "producer", "bash": "printf ready"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["producer"],
                "when": "$producer.output == 'ready'",
            },
        ],
    )
    package_path.with_name(f"{package_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(package_path)
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    return store, admitted.run_id


def _complete_producer(
    store: RunStore,
    run_id: str,
    content: bytes = b"ready",
    *,
    node_id: str = "producer",
) -> None:
    claim = store.claim_node(run_id, node_id, f"resolution-{node_id}")
    assert claim is not None
    store.mark_node_started(claim)
    relative = f"nodes/{node_id}/{claim.attempt_id}/stdout.log"
    output = store.run_directory(run_id) / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(
            ArtifactRef(
                relative_path=relative,
                media_type="text/plain; charset=utf-8",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )


def _start_multi_reference_run(
    tmp_path,
    workflow_writer,
    *,
    name: str,
    condition: bool,
) -> tuple[RunStore, str]:
    consumer = {
        "id": "consumer",
        "bash": "printf '%s:%s' $p1.output $p2.output",
        "depends_on": ["p1", "p2"],
    }
    if condition:
        consumer["when"] = (
            "$p1.output == 'ready' && $p2.output == 'ready'"
        )
        consumer["bash"] = "true"
    package_path = workflow_writer(
        tmp_path / name,
        name=name,
        nodes=[
            {"id": "p1", "bash": "printf ready"},
            {"id": "p2", "bash": "printf ready"},
            consumer,
        ],
    )
    package_path.with_name(f"{package_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(package_path)
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    _complete_producer(store, admitted.run_id, node_id="p1")
    _complete_producer(store, admitted.run_id, node_id="p2")
    return store, admitted.run_id


def _start_noncondition_reference_run(
    tmp_path,
    workflow_writer,
    *,
    name: str,
) -> tuple[RunStore, str]:
    package_path = workflow_writer(
        tmp_path / name,
        name=name,
        nodes=[
            {"id": "producer", "bash": "printf ready"},
            {
                "id": "consumer",
                "bash": "printf '%s' $producer.output",
                "depends_on": ["producer"],
                "retry": {"max_attempts": 2, "on_error": "all"},
            },
        ],
    )
    package_path.with_name(f"{package_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(package_path)
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    _complete_producer(store, admitted.run_id)
    return store, admitted.run_id


def test_store_persists_exact_resolution_backoff_and_exhausts_on_sixth_read(
    tmp_path, workflow_writer
) -> None:
    """Catch wrong delay indexing, polling, or attempt charging in the wait CAS."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-backoff"
    )
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    delays = (0.25, 0.5, 1.0, 2.0, 4.0)

    for read_count, delay in enumerate(delays, start=1):
        assert store.defer_output_resolution(
            run_id,
            "consumer",
            producer_identity=_PRODUCER_IDENTITY,
            now=observed,
        )
        projection = store.load_run(run_id)
        consumer = projection["nodes"]["consumer"]
        due = observed + timedelta(seconds=delay)
        assert consumer["state"] == "waiting_resolution"
        assert consumer["resolution_read_count"] == read_count
        assert consumer["next_resolution_at"] == due.isoformat()
        assert consumer["resolution_producer_identity"] == _PRODUCER_IDENTITY
        assert consumer["attempts"] == []
        assert consumer["retry_consumed"] == 0

        # A scheduler sweep before the durable due instant is inert.
        assert store.wake_due_output_resolutions(
            run_id, now=due - timedelta(microseconds=1)
        ) == ()
        assert not store.defer_output_resolution(
            run_id,
            "consumer",
            producer_identity=_PRODUCER_IDENTITY,
            now=observed,
        )

        assert store.wake_due_output_resolutions(run_id, now=due) == ("consumer",)
        awakened = store.load_run(run_id)["nodes"]["consumer"]
        assert awakened["state"] == "pending"
        assert awakened["resolution_read_count"] == read_count
        assert "next_resolution_at" not in awakened
        observed = due

    assert store.defer_output_resolution(
        run_id,
        "consumer",
        producer_identity=_PRODUCER_IDENTITY,
        now=observed,
    )
    exhausted = store.load_run(run_id)
    consumer = exhausted["nodes"]["consumer"]
    assert consumer["state"] == "failed"
    assert consumer["resolution_read_count"] == 6
    assert "next_resolution_at" not in consumer
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert exhausted["last_error"] == {
        "code": "output_reference_unavailable",
        "message": "output reference remained unavailable after 6 reads",
        "node_id": "consumer",
    }


def test_resolution_wait_round_trips_every_restart_state(
    tmp_path, workflow_writer
) -> None:
    """Catch wake authority living only in process memory or run.json cache state."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-restart"
    )
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    for read_count, delay in enumerate((0.25, 0.5, 1.0, 2.0, 4.0), start=1):
        assert store.defer_output_resolution(
            run_id,
            "consumer",
            producer_identity=_PRODUCER_IDENTITY,
            now=observed,
        )
        (store.run_directory(run_id) / "run.json").unlink()
        restarted = RunStore(store.hermes_home)
        projection = restarted.load_run(run_id)
        consumer = projection["nodes"]["consumer"]
        due = observed + timedelta(seconds=delay)
        assert consumer["resolution_read_count"] == read_count
        assert consumer["next_resolution_at"] == due.isoformat()
        assert consumer["resolution_producer_identity"] == _PRODUCER_IDENTITY
        assert restarted.wake_due_output_resolutions(run_id, now=due) == (
            "consumer",
        )
        observed = due
        store = restarted


def test_resolution_wait_identity_is_immutable_and_success_clears_state(
    tmp_path, workflow_writer
) -> None:
    """Catch a transient wait silently switching to a different producer result."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-identity"
    )
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    assert store.defer_output_resolution(
        run_id,
        "consumer",
        producer_identity=_PRODUCER_IDENTITY,
        now=observed,
    )
    due = observed + timedelta(milliseconds=250)
    assert store.wake_due_output_resolutions(run_id, now=due) == ("consumer",)

    changed_identity = {**_PRODUCER_IDENTITY, "publication_id": "c" * 32}
    assert store.defer_output_resolution(
        run_id,
        "consumer",
        producer_identity=changed_identity,
        now=due,
    )
    failed = store.load_run(run_id)
    assert failed["nodes"]["consumer"]["state"] == "failed"
    assert failed["last_error"]["code"] == "output_reference_integrity"
    assert failed["nodes"]["consumer"]["attempts"] == []

    clear_store, clear_run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-clear"
    )
    assert clear_store.defer_output_resolution(
        clear_run_id,
        "consumer",
        producer_identity=_PRODUCER_IDENTITY,
        now=observed,
    )
    assert clear_store.wake_due_output_resolutions(clear_run_id, now=due) == (
        "consumer",
    )
    assert clear_store.clear_output_resolution(
        clear_run_id,
        "consumer",
        producer_identity=_PRODUCER_IDENTITY,
    )
    cleared = clear_store.load_run(clear_run_id)["nodes"]["consumer"]
    assert cleared["state"] == "pending"
    assert not any(key.startswith("resolution_") for key in cleared)
    assert "next_resolution_at" not in cleared


def test_resolution_wait_journal_events_are_bounded_and_recoverable(
    tmp_path, workflow_writer
) -> None:
    """Catch raw producer values or non-replayable wait transitions in the journal."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-journal"
    )
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    assert store.defer_output_resolution(
        run_id,
        "consumer",
        producer_identity=_PRODUCER_IDENTITY,
        now=observed,
    )

    events = store.tail_events(run_id)
    deferred = events[-1]
    assert deferred["event_type"] == "output_resolution_deferred"
    assert deferred["payload"] == {
        "error_code": "output_reference_temporarily_unavailable",
        "next_resolution_at": (
            observed + timedelta(milliseconds=250)
        ).isoformat(),
        "producer_identity_sha256": hashlib.sha256(
            json.dumps(
                _PRODUCER_IDENTITY,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "resolution_read_count": 1,
    }
    assert len(json.dumps(deferred["payload"]).encode("utf-8")) < 512


def test_scheduler_defers_only_transient_reads_before_claim_and_does_not_cache_miss(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch transient host I/O becoming missing output, a claim, or a cached miss."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-scheduler-transient"
    )
    _complete_producer(store, run_id)
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    current = [observed]
    scheduler = RunScheduler(store, utcnow=lambda: current[0])
    calls = 0

    def transient_read(**_kwargs):
        nonlocal calls
        calls += 1
        raise ArchonOutputUnavailableError("host read temporarily unavailable")

    monkeypatch.setattr(scheduler_module, "resolve_node_output", transient_read)
    nodes = load_workflow(
        store.run_directory(run_id) / "definition.yaml"
    ).definition.nodes

    scheduler._resolve_graph(run_id, nodes)
    waiting = store.load_run(run_id)["nodes"]["consumer"]
    assert waiting["state"] == "waiting_resolution"
    assert waiting["resolution_read_count"] == 1
    assert waiting["next_resolution_at"] == (
        observed + timedelta(milliseconds=250)
    ).isoformat()
    assert waiting["attempts"] == []
    assert waiting["retry_consumed"] == 0
    assert calls == 1
    assert scheduler._resolved_output_cache == {}
    assert store.claim_node(run_id, "consumer", "must-not-claim") is None
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0

    # Ordinary graph sweeps while not due cannot read, claim, or append events.
    sequence = store.load_run(run_id)["event_sequence"]
    scheduler._resolve_graph(run_id, nodes)
    assert calls == 1
    assert store.load_run(run_id)["event_sequence"] == sequence


def test_scheduler_retries_on_due_wake_then_clears_wait_after_success(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch successful rereads retaining stale wake state or scheduling again."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-scheduler-success"
    )
    _complete_producer(store, run_id)
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    current = [observed]
    scheduler = RunScheduler(store, utcnow=lambda: current[0])
    real_resolve = scheduler_module.resolve_node_output
    calls = 0

    def transient_then_success(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArchonOutputUnavailableError("host read temporarily unavailable")
        return real_resolve(**kwargs)

    monkeypatch.setattr(
        scheduler_module, "resolve_node_output", transient_then_success
    )
    nodes = load_workflow(
        store.run_directory(run_id) / "definition.yaml"
    ).definition.nodes

    scheduler._resolve_graph(run_id, nodes)
    due = observed + timedelta(milliseconds=250)
    current[0] = due
    assert store.wake_due_output_resolutions(run_id, now=due) == ("consumer",)
    scheduler._resolve_graph(run_id, nodes)

    consumer = store.load_run(run_id)["nodes"]["consumer"]
    assert consumer["state"] == "ready"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert not any(key.startswith("resolution_") for key in consumer)
    assert "next_resolution_at" not in consumer
    assert calls == 2


def test_condition_due_wake_rejects_changed_successful_publication_identity(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch a condition clearing retained A after a successful reread of B."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-condition-drift"
    )
    _complete_producer(store, run_id)
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    current = [observed]
    scheduler = RunScheduler(store, utcnow=lambda: current[0])
    real_resolve = scheduler_module.resolve_node_output
    calls = 0

    def transient_a_then_success_b(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArchonOutputUnavailableError("host read temporarily unavailable")
        return replace(real_resolve(**kwargs), publication_id="c" * 32)

    monkeypatch.setattr(
        scheduler_module, "resolve_node_output", transient_a_then_success_b
    )
    nodes = load_workflow(
        store.run_directory(run_id) / "definition.yaml"
    ).definition.nodes

    scheduler._resolve_graph(run_id, nodes)
    due = observed + timedelta(milliseconds=250)
    current[0] = due
    assert store.wake_due_output_resolutions(run_id, now=due) == ("consumer",)
    scheduler._resolve_graph(run_id, nodes)

    projection = store.load_run(run_id)
    consumer = projection["nodes"]["consumer"]
    assert projection["status"] == "failed"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert projection["last_error"]["code"] == "output_reference_integrity"
    assert calls == 2


@pytest.mark.parametrize("condition", (True, False), ids=("condition", "template"))
def test_restart_revalidates_retained_producer_before_alternating_transient_reads(
    tmp_path, workflow_writer, monkeypatch, condition: bool
) -> None:
    """Catch unrelated transient producers masquerading as publication drift."""
    store, run_id = _start_multi_reference_run(
        tmp_path,
        workflow_writer,
        name=f"resolution-alternating-{condition}",
        condition=condition,
    )
    real_resolve = scheduler_module.resolve_node_output
    phase = [1]

    def alternating_transients(**kwargs):
        node_id = kwargs["node_id"]
        if (phase[0], node_id) in {(1, "p2"), (2, "p1")}:
            raise ArchonOutputUnavailableError("host read temporarily unavailable")
        return real_resolve(**kwargs)

    monkeypatch.setattr(scheduler_module, "resolve_node_output", alternating_transients)
    nodes = load_workflow(
        store.run_directory(run_id) / "definition.yaml"
    ).definition.nodes
    consumer = next(node for node in nodes if node.id == "consumer")
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    first = RunScheduler(store, utcnow=lambda: observed)
    package, sealed_paths, sealed_bytes = first._load_verified_run_package(run_id)

    def observe(scheduler: RunScheduler) -> bool:
        if condition:
            scheduler._resolve_graph(run_id, nodes)
            return store.load_run(run_id)["nodes"]["consumer"]["state"] == "ready"
        else:
            scheduler._resolve_graph(run_id, nodes)
            projection = store.load_run(run_id)
            return scheduler._preflight_strict_node_references(
                run_id,
                consumer,
                package,
                projection,
                sealed_resource_paths=sealed_paths,
                sealed_resource_bytes=sealed_bytes,
            )

    assert not observe(first)
    first_wait = store.load_run(run_id)["nodes"]["consumer"]
    assert first_wait["state"] == "waiting_resolution"
    assert first_wait["resolution_producer_identity"]["node_id"] == "p2"

    first_due = observed + timedelta(milliseconds=250)
    assert store.wake_due_output_resolutions(run_id, now=first_due) == ("consumer",)
    phase[0] = 2
    second = RunScheduler(store, utcnow=lambda: first_due)
    assert second._resolved_output_cache == {}
    assert not observe(second)
    second_wait = store.load_run(run_id)["nodes"]["consumer"]
    assert second_wait["state"] == "waiting_resolution"
    assert second_wait["resolution_read_count"] == 1
    assert second_wait["resolution_producer_identity"]["node_id"] == "p1"
    assert second_wait["attempts"] == []
    assert second_wait["retry_consumed"] == 0

    second_due = first_due + timedelta(milliseconds=250)
    assert store.wake_due_output_resolutions(run_id, now=second_due) == ("consumer",)
    phase[0] = 3
    third = RunScheduler(store, utcnow=lambda: second_due)
    assert observe(third)
    completed_read = store.load_run(run_id)["nodes"]["consumer"]
    assert completed_read["state"] == "ready"
    assert completed_read["attempts"] == []
    assert completed_read["retry_consumed"] == 0
    assert not any(key.startswith("resolution_") for key in completed_read)


def test_scheduler_advance_selects_due_resolution_wake_before_runnable_work(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch due resolution state being stranded until a caller wakes it manually."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name="resolution-advance-due"
    )
    _complete_producer(store, run_id)
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    current = [observed]
    scheduler = RunScheduler(store, utcnow=lambda: current[0])
    real_resolve = scheduler_module.resolve_node_output
    calls = 0

    def transient_then_success(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArchonOutputUnavailableError("host read temporarily unavailable")
        return real_resolve(**kwargs)

    monkeypatch.setattr(
        scheduler_module, "resolve_node_output", transient_then_success
    )
    scheduler._resolve_graph(
        run_id,
        load_workflow(
            store.run_directory(run_id) / "definition.yaml"
        ).definition.nodes,
    )
    current[0] = observed + timedelta(milliseconds=250)

    completed = scheduler.advance(run_id, max_nodes=1)

    assert completed["status"] == "succeeded"
    assert completed["nodes"]["consumer"]["state"] == "succeeded"
    assert completed["nodes"]["consumer"]["attempts"][0]["state"] == "succeeded"
    assert calls == 2


def test_scheduler_preflights_noncondition_reference_before_claim(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch prompt/Bash/script references reaching an executor before host reads."""
    path = workflow_writer(
        tmp_path / "resolution-preclaim",
        name="resolution-preclaim",
        nodes=[
            {"id": "producer", "bash": "printf ready"},
            {
                "id": "consumer",
                "bash": "printf '%s' $producer.output",
                "depends_on": ["producer"],
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "resolution-preclaim-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="resolution-preclaim",
            concurrency_key="resolution-preclaim",
        ),
        immutable_snapshot=prepared,
    )
    _complete_producer(store, admitted.run_id)
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    scheduler = RunScheduler(store, utcnow=lambda: observed)

    def transient_read(**_kwargs):
        raise ArchonOutputUnavailableError("host read temporarily unavailable")

    monkeypatch.setattr(scheduler_module, "resolve_node_output", transient_read)

    result = scheduler.advance(admitted.run_id, max_nodes=1)

    consumer = result["nodes"]["consumer"]
    assert consumer["state"] == "waiting_resolution"
    assert consumer["resolution_read_count"] == 1
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert scheduler._resolved_output_cache == {}


def test_scheduler_preflight_terminal_reference_failure_is_zero_attempt(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch terminal prompt/Bash/script reference errors becoming executor failures."""
    path = workflow_writer(
        tmp_path / "resolution-preclaim-terminal",
        name="resolution-preclaim-terminal",
        nodes=[
            {"id": "producer", "bash": "printf ready"},
            {
                "id": "consumer",
                "bash": "printf '%s' $producer.output",
                "depends_on": ["producer"],
                "retry": {"max_attempts": 2, "on_error": "all"},
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "resolution-preclaim-terminal-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="resolution-preclaim-terminal",
            concurrency_key="resolution-preclaim-terminal",
        ),
        immutable_snapshot=prepared,
    )
    _complete_producer(store, admitted.run_id)
    scheduler = RunScheduler(store)

    def terminal_read(**_kwargs):
        raise WorkflowOutputReferenceError(
            "output_reference_integrity", "producer"
        )

    monkeypatch.setattr(scheduler_module, "resolve_node_output", terminal_read)

    result = scheduler.advance(admitted.run_id, max_nodes=1)

    consumer = result["nodes"]["consumer"]
    assert result["status"] == "failed"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert result["last_error"]["code"] == "output_reference_integrity"
    assert "next_resolution_at" not in consumer


@pytest.mark.parametrize("entrypoint", ("advance", "advance_all"))
def test_unbounded_scheduler_entrypoints_finalize_terminal_preflight_in_one_call(
    tmp_path, workflow_writer, monkeypatch, entrypoint: str
) -> None:
    """Catch advance_all returning running after preflight failed the final node."""
    store, run_id = _start_noncondition_reference_run(
        tmp_path, workflow_writer, name=f"resolution-finalize-{entrypoint}"
    )
    scheduler = RunScheduler(store)
    executor_calls = []

    def terminal_read(**_kwargs):
        raise WorkflowOutputReferenceError(
            "output_reference_integrity", "producer"
        )

    def reject_execution(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("executor allocated after terminal reference preflight")

    monkeypatch.setattr(scheduler_module, "resolve_node_output", terminal_read)
    monkeypatch.setattr(scheduler, "_execute_claim", reject_execution)
    if entrypoint == "advance":
        result = scheduler.advance(run_id)
    else:
        result = scheduler.advance_all([run_id])[run_id]

    consumer = result["nodes"]["consumer"]
    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "output_reference_integrity"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert executor_calls == []
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0


def test_coordinator_submission_finalizes_terminal_preflight_before_return(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch coordinator-submitted work needing an unrelated second sweep."""
    store, run_id = _start_noncondition_reference_run(
        tmp_path, workflow_writer, name="resolution-finalize-submit"
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="resolution-finalize-submit",
        host_kind="gateway",
        host_instance_id="resolution-finalize-submit-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    scheduler = RunScheduler(store, execution_fence=fence)
    executor_calls = []

    def terminal_read(**_kwargs):
        raise WorkflowOutputReferenceError(
            "output_reference_integrity", "producer"
        )

    def reject_execution(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("executor allocated after terminal reference preflight")

    monkeypatch.setattr(scheduler_module, "resolve_node_output", terminal_read)
    monkeypatch.setattr(scheduler, "_execute_claim", reject_execution)
    assert scheduler.submit(run_id, fence)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with scheduler._activity:
            if run_id not in scheduler._submitted_runs:
                break
        time.sleep(0.01)
    try:
        result = store.load_run(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    consumer = result["nodes"]["consumer"]
    assert run_id not in scheduler._submitted_runs
    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "output_reference_integrity"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert executor_calls == []


@pytest.mark.parametrize(
    "code",
    (
        "output_reference_missing",
        "output_reference_not_structured",
        "output_reference_field_missing",
        "output_reference_path_type",
        "output_reference_integrity",
    ),
)
def test_scheduler_terminal_reference_errors_never_enter_wait_or_consume_attempt(
    tmp_path, workflow_writer, monkeypatch, code: str
) -> None:
    """Catch non-transient strict failures leaking into wait or retry handling."""
    store, run_id = _start_archon_run(
        tmp_path, workflow_writer, name=f"resolution-terminal-{code}"
    )
    _complete_producer(store, run_id)
    scheduler = RunScheduler(store)

    def terminal_read(**_kwargs):
        raise WorkflowOutputReferenceError(code, "producer")

    monkeypatch.setattr(scheduler_module, "resolve_node_output", terminal_read)
    scheduler._resolve_graph(
        run_id,
        load_workflow(
            store.run_directory(run_id) / "definition.yaml"
        ).definition.nodes,
    )

    projection = store.load_run(run_id)
    consumer = projection["nodes"]["consumer"]
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert projection["last_error"]["code"] == code
    assert "next_resolution_at" not in consumer
    assert "resolution_read_count" not in consumer
