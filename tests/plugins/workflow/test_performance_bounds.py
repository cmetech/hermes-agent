from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
    record_coordinator_wake,
)
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.language_schema import iter_when_output_references
from plugins.workflow.models import ExecutionFence
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.topology import project_topology


class _SliceAccountingText(str):
    """A string that records copied slice volume across derived slices."""

    def __new__(
        cls, value: str, account: dict[str, int] | None = None
    ) -> _SliceAccountingText:
        instance = super().__new__(cls, value)
        instance.account = account if account is not None else {"characters": 0}
        return instance

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step == 1:
                self.account["characters"] += max(0, stop - start)
            if isinstance(value, str):
                return type(self)(value, self.account)
        return value


def _condition_reference_slice_volume(clauses: int) -> tuple[int, int, int]:
    expression = _SliceAccountingText(
        " && ".join(
            f"$producer.output.field == {index}" for index in range(clauses)
        )
    )

    references = tuple(
        iter_when_output_references(expression, normalizer_version=3)
    )

    assert len(references) == clauses
    assert references[0].start == 0
    assert references[-1].end == str(expression).rfind(" == ")
    return len(expression), expression.account["characters"], len(references)


def test_many_clause_condition_reference_discovery_copies_only_linear_bytes() -> None:
    small_bytes, small_slices, _ = _condition_reference_slice_volume(512)
    large_bytes, large_slices, count = _condition_reference_slice_volume(1024)

    assert count == 1024
    assert large_bytes > small_bytes
    assert large_slices <= (3 * small_slices) + large_bytes
    assert large_slices <= 4 * large_bytes


def test_thousand_node_projection_is_bounded_and_disables_mermaid(
    tmp_path, workflow_writer
) -> None:
    nodes = [
        {"id": f"node-{index:04d}", "bash": "true", **({"depends_on": [f"node-{index - 1:04d}"]} if index else {})}
        for index in range(1000)
    ]
    package = load_workflow(
        workflow_writer(tmp_path / "large", name="large", nodes=nodes)
    )
    started = time.perf_counter()
    result = project_topology(package.definition)
    elapsed = time.perf_counter() - started

    assert result.node_count == 1000
    assert result.edge_count == 999
    assert len(result.text.encode("utf-8")) <= 12 * 1024
    assert result.mermaid is None
    assert "topology_mermaid_too_many_nodes" in result.warnings
    assert elapsed < 2.0


def test_ten_thousand_expired_coordinator_diagnostics_are_pruned_without_losing_wakes(
    tmp_path,
) -> None:
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(days=8)).isoformat()
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO coordinator_events "
            "(timestamp, event_type, owner_id, epoch, payload_json) "
            "VALUES (?, 'diagnostic', 'owner', 1, '{}')",
            ((expired,) for _ in range(10_000)),
        )
        connection.executemany(
            "INSERT INTO coordinator_wakes "
            "(run_id, reason_code, created_at, completed_at, completed_epoch, outcome) "
            "VALUES (?, 'test', ?, ?, 1, 'processed')",
            ((f"completed-{index}", expired, expired) for index in range(9_999)),
        )
        connection.execute(
            "INSERT INTO coordinator_wakes (run_id, reason_code, created_at) "
            "VALUES ('unprocessed', 'test', ?)",
            (expired,),
        )
        record_coordinator_wake(
            connection, run_id="fresh", reason_code="test", now=now
        )
        event_count = connection.execute(
            "SELECT COUNT(*) FROM coordinator_events"
        ).fetchone()[0]
        wakes = connection.execute(
            "SELECT run_id, completed_at FROM coordinator_wakes ORDER BY generation"
        ).fetchall()

    assert event_count == 0
    assert [(row["run_id"], row["completed_at"]) for row in wakes] == [
        ("unprocessed", None),
        ("fresh", None),
    ]


def test_topology_injection_canaries_remain_strict_graph_grammar(
    tmp_path, workflow_writer
) -> None:
    node_id = "x%%{init:evil}%%-script-alert-1-click-style-class-quote-newline"
    package = load_workflow(
        workflow_writer(tmp_path / "canary", nodes=[{"id": node_id, "bash": "true"}])
    )
    result = project_topology(package.definition)

    assert result.mermaid is not None
    assert "%%" not in result.mermaid
    assert "<" not in result.mermaid
    assert "click " not in result.mermaid
    assert result.mermaid.splitlines()[0] == "flowchart LR"


def test_coordinator_cursor_reaches_run_201_with_bounded_keyset_pages(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(
        home,
        max_executing_runs=300,
        max_nonterminal_runs=300,
        max_total_workers=300,
        max_start_requests_per_minute=300,
    )
    now = datetime.now(timezone.utc)
    coordinator = CoordinatorStore(store.database)
    identity = CoordinatorIdentity("cursor-owner", "gateway", "cursor-host", 1, None)
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=600)
    assert leadership.is_leader
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cursor-work")
    )
    admitted = []
    for index in range(205):
        prepared = store.prepare_run_snapshot(package)
        admitted.append(
            store.start_run(
                RunAdmissionRequest(
                    workflow_name=package.definition.name,
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=f"cursor-{index:03d}",
                    concurrency_key=f"cursor-{index:03d}",
                    concurrency_policy="allow",
                    execution_mode="background",
                ),
                immutable_snapshot=prepared,
            ).run_id
        )
    with store._connect() as connection:
        query_plan = tuple(
            str(row["detail"])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT run_id, created_at, status, "
                "execution_mode FROM runs WHERE admission_state='published' "
                "AND status IN ('queued','running','waiting_retry') "
                "AND execution_mode IN ('background','foreground') "
                "ORDER BY created_at, run_id LIMIT 101"
            )
        )
    assert any("runs_coordinator_scan" in detail for detail in query_plan)
    assert not any("TEMP B-TREE" in detail for detail in query_plan)

    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="cursor-host",
        ),
        hermes_home=home,
    )
    cursor = None
    for _page in range(10):
        started = time.monotonic()
        _actionable, cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
            cursor,
        )
        assert time.monotonic() - started < 2.2
        if cursor is None and scheduler.submit.call_count >= len(admitted):
            break

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert set(submitted) == set(admitted)
    assert admitted[200] in submitted
    assert len(submitted) == 205


def test_stall_threshold_transitions_use_exact_monotonic_boundaries_and_deduplicate(
    tmp_path, workflow_writer
) -> None:
    base = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    sample = LeaseClockSample(base, 100.0, "boot-a")
    current_sample = [sample]
    store = RunStore(tmp_path / "home", lease_clock=lambda: current_sample[0])
    identity = CoordinatorIdentity(
        "threshold-owner", "gateway", "threshold-host", 1, None
    )
    coordinator = CoordinatorStore(
        store.database, clock=lambda: current_sample[0]
    )
    leadership = coordinator.try_acquire(identity, now=base, lease_seconds=600)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="stall-threshold")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="stall-threshold",
            concurrency_key=package.definition.name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    projection = store.load_run(admitted.run_id)
    nodes = {key: dict(value) for key, value in projection["nodes"].items()}
    nodes["start"]["state"] = "succeeded"
    store.append_event(
        admitted.run_id,
        "fault_injected_pending_finalization",
        projection_updates={
            "nodes": nodes,
            "last_runnable_progress_at": base.isoformat(),
            "last_runnable_progress_monotonic": 100.0,
            "progress_boot_id": "boot-a",
        },
    )
    fence = ExecutionFence("threshold-owner", leadership.lease.epoch)

    assert not store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=59), 159.999, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=60), 160.0, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert not store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=61), 161.0, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    events = [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "run_stalled"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["reason_code"] == "runnable_progress_stalled"

    semantic_prepared = store.prepare_run_snapshot(package)
    semantic = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=semantic_prepared.definition_digest,
            policy_digest=semantic_prepared.policy_digest,
            input_manifest_digest=semantic_prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="semantic-stall-threshold",
            concurrency_key="semantic-stall-threshold",
            execution_mode="background",
        ),
        immutable_snapshot=semantic_prepared,
    )
    claim = store.claim_node(
        semantic.run_id,
        "start",
        "threshold-worker",
        now=base,
        monotonic_now=100.0,
        execution_fence=fence,
    )
    assert claim is not None
    store.mark_node_started(claim, now=sample)
    store.append_event(semantic.run_id, "semantic_progress")
    current_sample[0] = LeaseClockSample(
        base + timedelta(seconds=1), 0.0, "boot-b"
    )
    store.append_event(semantic.run_id, "runnable_progress_after_restart")
    takeover = coordinator.try_acquire(
        identity,
        now=current_sample[0].utc_now,
        lease_seconds=600,
    )
    assert takeover.is_leader
    semantic_fence = ExecutionFence(identity.owner_id, takeover.lease.epoch)

    assert not store.record_stall_if_due(
        semantic.run_id,
        fence=semantic_fence,
        now=LeaseClockSample(base + timedelta(seconds=299), 49.999, "boot-b"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert store.record_stall_if_due(
        semantic.run_id,
        fence=semantic_fence,
        now=LeaseClockSample(base + timedelta(seconds=300), 50.0, "boot-b"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    semantic_events = [
        event
        for event in store.tail_events(semantic.run_id)
        if event["event_type"] == "run_stalled"
    ]
    assert len(semantic_events) == 1
    assert semantic_events[0]["payload"]["reason_code"] == (
        "semantic_progress_stalled"
    )
