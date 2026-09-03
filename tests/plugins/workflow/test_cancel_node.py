from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import subprocess
import os
import sys
import threading
import time

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.handoff import (
    AdvanceResult,
    HandoffEndpoint,
    HandoffSnapshot,
    HandoffSpec,
)
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.cancel import CancelExecutor
from plugins.workflow.models import ExecutionFence, WorkflowNode, freeze_value
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ManagedProcessTree, ProcessIdentity, TerminationPolicy
from tests.plugins.workflow.test_phase6_store import (
    _EXECUTION_AUTHORITY,
    _admit_group,
    _initialize,
    _scope,
)
from tests.plugins.workflow.test_phase6_scheduler import OutputExecutor, _admit, _compile


def _start(
    store: RunStore,
    package,
    *,
    key: str = "cancel-test",
    interaction_policy: str | None = None,
):
    prepared = store.prepare_run_snapshot(package)
    if interaction_policy is not None:
        prepared = replace(
            prepared,
            assignments={
                "start": {
                    "endpoint": "hermes://peer/office/reviewer",
                    "interaction_policy": interaction_policy,
                    "on_deadline": "cancel_and_fail",
                }
            },
        )
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


def _wait_on_handoff(
    store: RunStore,
    package,
    *,
    handoff_id: str = "handoff-cancel",
    next_observation_at: datetime | None = None,
    deadline_at: datetime | None = None,
    interaction_policy: str | None = None,
):
    admitted = _start(
        store,
        package,
        interaction_policy=interaction_policy,
    )
    claim = store.claim_node(admitted.run_id, "start", "handoff-worker")
    assert claim is not None
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    next_observation_at = next_observation_at or now
    deadline_at = deadline_at or now + timedelta(hours=1)
    assert store.begin_handoff_wait(
        claim,
        handoff_id=handoff_id,
        generation=1,
        observed_version=3,
        observed_phase="active",
        next_observation_at=next_observation_at,
        deadline_at=deadline_at,
    )
    return admitted.run_id


def test_cancel_waiting_handoff_records_one_stable_command_and_waits_for_truth(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-handoff",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)

    first = store.cancel_run(run_id)
    second = RunStore(store.hermes_home).cancel_run(run_id)

    assert first["status"] == second["status"] == "running"
    assert first["desired_status"] == second["desired_status"] == "cancelled"
    assert first["cancellation_outcome"] == second["cancellation_outcome"] == (
        "cancelling"
    )
    node = second["nodes"]["start"]
    assert node["state"] == "waiting_handoff"
    assert node["handoff_cancel"] == {
        "command_id": f"workflow-{run_id}-start-1-cancel",
        "state": "pending",
    }
    assert [
        event["event_type"]
        for event in store.tail_events(run_id)
        if event["event_type"] == "handoff_cancelling"
    ] == ["handoff_cancelling"]
    event_types = [event["event_type"] for event in store.tail_events(run_id)]
    assert "handoff_admitted" in event_types
    assert "handoff_active" in event_types


@pytest.mark.parametrize("response_recorded", [False, True])
def test_cancel_handoff_input_discards_unsent_response_and_wins(
    tmp_path: Path,
    workflow_writer,
    response_recorded: bool,
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"cancel-handoff-input-{response_recorded}",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    run_id = _wait_on_handoff(
        store,
        package,
        interaction_policy="pause",
    )
    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="needs_input",
        next_observation_at=observed + timedelta(seconds=5),
        approval_request_id="approval-1",
        approval_choices=("once", "deny"),
    )
    paused = store.load_run(run_id)
    if response_recorded:
        store.approve_run(
            run_id,
            expected_state_version=paused["state_version"],
            interaction_id=paused["nodes"]["start"]["pending_interaction"][
                "interaction_id"
            ],
        )

    cancelled = store.cancel_run(run_id)

    node = cancelled["nodes"]["start"]
    assert cancelled["desired_status"] == "cancelled"
    assert node["state"] == "waiting_handoff"
    assert "pending_interaction" not in node
    assert "handoff_response" not in node
    assert node["handoff_cancel"]["state"] == "pending"


def test_handoff_deadline_unpauses_input_and_uses_cancel_path(
    tmp_path: Path,
    workflow_writer,
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="deadline-handoff-input",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    run_id = _wait_on_handoff(
        store,
        package,
        interaction_policy="pause",
    )
    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="needs_input",
        next_observation_at=observed + timedelta(seconds=5),
        approval_request_id="approval-1",
        approval_choices=("once", "deny"),
    )

    command_id = store.request_handoff_cancel(
        run_id,
        "start",
        reason_code="deadline_exceeded",
    )

    projection = store.load_run(run_id)
    node = projection["nodes"]["start"]
    assert projection["status"] == "running"
    assert node["state"] == "waiting_handoff"
    assert "pending_interaction" not in node
    assert node["handoff_cancel"] == {
        "command_id": command_id,
        "state": "pending",
        "reason_code": "deadline_exceeded",
    }


def test_coordinator_records_cancel_command_before_delivering_it(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="deliver-handoff-cancel",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    run_id = _wait_on_handoff(
        store,
        package,
        next_observation_at=observed + timedelta(hours=1),
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="cancel-coordinator",
        host_kind="gateway",
        host_instance_id="cancel-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=observed,
        lease_seconds=30,
    )
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="delegate",
        output_schema=None,
        deadline_at=None,
        attribution={"consumer": "workflow"},
        required_capabilities=frozenset(),
    )

    class Service:
        def __init__(self) -> None:
            self.calls = []

        def command(self, handoff_id, kind, *, command_id, actor):
            self.calls.append(("command", handoff_id, kind, command_id, actor))

        def advance(self, handoff_id, *, budget_seconds):
            self.calls.append(("advance", handoff_id))
            return AdvanceResult(
                HandoffSnapshot(
                    handoff_id=handoff_id,
                    key_scope="default",
                    handoff_key=f"{run_id}:start:1",
                    spec=spec,
                    spec_fingerprint=spec.fingerprint,
                    phase="cancelling",
                    state_version=4,
                    next_advance_at=datetime.now(timezone.utc) + timedelta(seconds=5),
                ),
                "cancel",
                True,
            )

    service = Service()
    store.cancel_run(run_id)
    scheduler = RunScheduler(
        store,
        execution_fence=fence,
        handoff_service=service,
        utcnow=lambda: observed,
    )
    try:
        scheduler.advance_due_handoffs(
            run_id,
            deadline=time.monotonic() + 2,
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    command_id = f"workflow-{run_id}-start-1-cancel"
    assert service.calls == [
        ("command", "handoff-cancel", "cancel", command_id, "workflow"),
        ("advance", "handoff-cancel"),
    ]
    assert store.load_run(run_id)["nodes"]["start"]["handoff_cancel"] == {
        "command_id": command_id,
        "state": "recorded",
    }


def test_cancel_crash_persists_every_handoff_command_for_reopen_repair(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-crash-two-handoffs",
            nodes=[
                {"id": "start", "prompt": "delegate one"},
                {"id": "other", "prompt": "delegate two"},
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    for index, node_id in enumerate(("start", "other"), start=1):
        claim = store.claim_node(admitted.run_id, node_id, f"worker-{index}")
        assert claim is not None
        assert store.begin_handoff_wait(
            claim,
            handoff_id=f"handoff-{node_id}",
            generation=1,
            observed_version=3,
            observed_phase="active",
            next_observation_at=observed + timedelta(hours=1),
            deadline_at=observed + timedelta(hours=2),
        )

    def crash_after_first_durable_cancel_write() -> None:
        raise RuntimeError("simulated process death")

    monkeypatch.setattr(
        store,
        "_notify_coordinator",
        crash_after_first_durable_cancel_write,
    )
    with pytest.raises(RuntimeError, match="simulated process death"):
        store.cancel_run(admitted.run_id)

    reopened = RunStore(store.hermes_home).load_run(admitted.run_id)
    assert reopened["desired_status"] == "cancelled"
    assert {
        node_id: reopened["nodes"][node_id].get("handoff_cancel")
        for node_id in ("start", "other")
    } == {
        "start": {
            "command_id": f"workflow-{admitted.run_id}-start-1-cancel",
            "state": "pending",
        },
        "other": {
            "command_id": f"workflow-{admitted.run_id}-other-1-cancel",
            "state": "pending",
        },
    }


def test_coordinator_delivers_deadline_cancel_before_a_later_poll(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="deadline-before-poll",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    observed = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(
        store,
        package,
        next_observation_at=observed + timedelta(hours=1),
        deadline_at=observed - timedelta(seconds=1),
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="deadline-coordinator",
        host_kind="gateway",
        host_instance_id="deadline-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=observed,
        lease_seconds=30,
    )
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="delegate",
        output_schema=None,
        deadline_at=None,
        attribution={"consumer": "workflow"},
        required_capabilities=frozenset(),
    )

    class Service:
        def __init__(self) -> None:
            self.calls = []

        def command(self, handoff_id, kind, *, command_id, actor):
            self.calls.append(("command", handoff_id, kind, command_id, actor))

        def advance(self, handoff_id, *, budget_seconds):
            self.calls.append(("advance", handoff_id))
            return AdvanceResult(
                HandoffSnapshot(
                    handoff_id=handoff_id,
                    key_scope="default",
                    handoff_key=f"{run_id}:start:1",
                    spec=spec,
                    spec_fingerprint=spec.fingerprint,
                    phase="cancelling",
                    state_version=4,
                    next_advance_at=observed + timedelta(seconds=5),
                ),
                "cancel",
                True,
            )

    service = Service()
    scheduler = RunScheduler(
        store,
        execution_fence=fence,
        handoff_service=service,
        utcnow=lambda: observed,
    )
    try:
        assert scheduler.advance_due_handoffs(
            run_id,
            deadline=time.monotonic() + 2,
        ) == (1, 0, 0)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    command_id = f"workflow-{run_id}-start-1-deadline-cancel"
    assert service.calls == [
        ("command", "handoff-cancel", "cancel", command_id, "workflow"),
        ("advance", "handoff-cancel"),
    ]


def test_cancel_command_consuming_cycle_deadline_skips_followup_advance(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-command-deadline",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(
        store,
        package,
        next_observation_at=observed + timedelta(hours=1),
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="deadline-budget-coordinator",
        host_kind="gateway",
        host_instance_id="deadline-budget-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity,
        now=observed,
        lease_seconds=30,
    )
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    monotonic = [100.0]

    class Service:
        def __init__(self) -> None:
            self.calls = []

        def command(self, handoff_id, kind, *, command_id, actor):
            self.calls.append(("command", handoff_id))
            monotonic[0] = 102.0

        def advance(self, handoff_id, *, budget_seconds):
            self.calls.append(("advance", handoff_id))
            raise AssertionError("advance must not outlive the cycle deadline")

    service = Service()
    store.cancel_run(run_id)
    scheduler = RunScheduler(
        store,
        execution_fence=fence,
        handoff_service=service,
        utcnow=lambda: observed,
        monotonic=lambda: monotonic[0],
    )
    try:
        assert scheduler.advance_due_handoffs(
            run_id,
            deadline=101.0,
        ) == (1, 0, 0)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert service.calls == [("command", "handoff-cancel")]


def test_coordinator_takeover_during_deadline_recording_prevents_external_io(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="deadline-takeover",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    observed = datetime.now(timezone.utc)
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(
        store,
        package,
        next_observation_at=observed + timedelta(hours=1),
        deadline_at=observed - timedelta(seconds=1),
    )
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="old-deadline-coordinator",
        host_kind="gateway",
        host_instance_id="old-deadline-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    coordinator = CoordinatorStore(store.database)
    leadership = coordinator.try_acquire(identity, now=observed, lease_seconds=30)
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    original = store.request_handoff_cancel

    def record_then_take_over(*args, **kwargs):
        command_id = original(*args, **kwargs)
        assert coordinator.release(
            identity,
            epoch=leadership.lease.epoch,
            now=observed,
        )
        successor = CoordinatorIdentity(
            owner_id="new-deadline-coordinator",
            host_kind="gateway",
            host_instance_id="new-deadline-host",
            pid=process.pid,
            process_start_time=process.start_time,
        )
        assert coordinator.try_acquire(
            successor,
            now=observed,
            lease_seconds=30,
        ).is_leader
        return command_id

    monkeypatch.setattr(store, "request_handoff_cancel", record_then_take_over)

    class Service:
        def __init__(self) -> None:
            self.calls = []

        def command(self, *args, **kwargs):
            self.calls.append("command")

        def advance(self, *args, **kwargs):
            self.calls.append("advance")

    service = Service()
    scheduler = RunScheduler(
        store,
        execution_fence=fence,
        handoff_service=service,
        utcnow=lambda: observed,
    )
    try:
        assert scheduler.advance_due_handoffs(
            run_id,
            deadline=time.monotonic() + 2,
        ) == (1, 0, 0)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert service.calls == []


@pytest.mark.parametrize(
    ("phase", "run_status", "node_status"),
    (("cancelled", "cancelled", "cancelled"), ("failed", "failed", "failed")),
)
def test_cancel_handoff_terminal_truth_wins_the_race(
    tmp_path: Path,
    workflow_writer,
    phase: str,
    run_status: str,
    node_status: str,
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"handoff-{phase}",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)
    store.cancel_run(run_id)

    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase=phase,
        next_observation_at=datetime.now(timezone.utc),
    )

    projection = store.load_run(run_id)
    assert projection["status"] == run_status
    assert projection.get("desired_status") is None
    assert projection["nodes"]["start"]["state"] == node_status


def test_cancel_waits_for_authoritative_truth_from_every_handoff(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-two-handoffs",
            nodes=[
                {"id": "start", "prompt": "delegate one"},
                {"id": "other", "prompt": "delegate two"},
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    observed = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    for index, node_id in enumerate(("start", "other"), start=1):
        claim = store.claim_node(admitted.run_id, node_id, f"worker-{index}")
        assert claim is not None
        assert store.begin_handoff_wait(
            claim,
            handoff_id=f"handoff-{node_id}",
            generation=1,
            observed_version=3,
            observed_phase="active",
            next_observation_at=observed,
            deadline_at=observed + timedelta(hours=1),
        )
    store.cancel_run(admitted.run_id)

    assert store.refresh_handoff_wait(
        admitted.run_id,
        "start",
        handoff_id="handoff-start",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="cancelled",
        next_observation_at=observed,
    )
    partial = store.load_run(admitted.run_id)
    assert partial["status"] == "running"
    assert partial["desired_status"] == "cancelled"
    assert partial["nodes"]["start"]["state"] == "cancelled"
    assert partial["nodes"]["other"]["state"] == "waiting_handoff"

    assert store.refresh_handoff_wait(
        admitted.run_id,
        "other",
        handoff_id="handoff-other",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="cancelled",
        next_observation_at=observed,
    )
    final = store.load_run(admitted.run_id)
    assert final["status"] == "cancelled"
    assert final.get("desired_status") is None


def test_indeterminate_cancel_stays_nonterminal_and_actionable(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="handoff-indeterminate",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)
    store.cancel_run(run_id)

    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="indeterminate",
        next_observation_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    projection = store.load_run(run_id)
    assert projection["status"] == "running"
    assert projection["desired_status"] == "cancelled"
    assert projection["nodes"]["start"]["state"] == "waiting_handoff"
    assert any(
        event["event_type"] == "handoff_indeterminate"
        for event in store.tail_events(run_id)
    )

    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=4,
        observed_version=5,
        observed_phase="cancelling",
        next_observation_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert any(
        event["event_type"] == "handoff_reconciled"
        for event in store.tail_events(run_id)
    )


def test_same_handoff_phase_updates_health_without_repeating_transition(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="handoff-same-phase",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)

    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="active",
        next_observation_at=datetime.now(timezone.utc) + timedelta(seconds=5),
    )

    event_types = [event["event_type"] for event in store.tail_events(run_id)]
    assert event_types.count("handoff_active") == 1
    assert "handoff_observed" in event_types


def test_success_before_cancel_is_validated_then_blocks_downstream(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "downstream-must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="handoff-success-race",
            nodes=[
                {"id": "start", "prompt": "delegate"},
                {
                    "id": "after",
                    "bash": f"touch {marker}",
                    "depends_on": ["start"],
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)
    store.cancel_run(run_id)
    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="succeeded",
        next_observation_at=datetime.now(timezone.utc),
    )

    claim = store.claim_node(run_id, "start", "validator")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="succeeded",
        metadata={"handoff_id": "handoff-cancel", "validated": True},
    )

    projection = store.load_run(run_id)
    assert projection["status"] == "cancelled"
    assert projection["desired_status"] is None
    assert projection["nodes"]["start"]["state"] == "succeeded"
    assert projection["nodes"]["after"]["state"] == "cancelled"
    assert not marker.exists()


def test_handoff_deadline_uses_the_same_idempotent_cancel_intent(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="handoff-deadline",
            nodes=[{"id": "start", "prompt": "delegate"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)

    first = store.request_handoff_cancel(
        run_id,
        "start",
        reason_code="deadline_exceeded",
    )
    second = store.request_handoff_cancel(
        run_id,
        "start",
        reason_code="deadline_exceeded",
    )

    assert first == second == f"workflow-{run_id}-start-1-deadline-cancel"
    projection = store.load_run(run_id)
    assert projection["status"] == "running"
    assert projection.get("desired_status") is None
    assert projection["nodes"]["start"]["handoff_cancel"] == {
        "command_id": first,
        "state": "pending",
        "reason_code": "deadline_exceeded",
    }
    assert [
        event["event_type"]
        for event in store.tail_events(run_id)
        if event["event_type"] == "handoff_deadline_exceeded"
    ] == ["handoff_deadline_exceeded"]


def test_deadline_success_is_validated_before_run_fails_and_blocks_downstream(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "deadline-downstream-must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="handoff-deadline-success",
            nodes=[
                {"id": "start", "prompt": "delegate"},
                {
                    "id": "after",
                    "bash": f"touch {marker}",
                    "depends_on": ["start"],
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _wait_on_handoff(store, package)
    store.request_handoff_cancel(
        run_id,
        "start",
        reason_code="deadline_exceeded",
    )
    assert store.refresh_handoff_wait(
        run_id,
        "start",
        handoff_id="handoff-cancel",
        generation=1,
        expected_observed_version=3,
        observed_version=4,
        observed_phase="succeeded",
        next_observation_at=datetime.now(timezone.utc),
    )

    claim = store.claim_node(run_id, "start", "deadline-validator")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="succeeded",
        metadata={"handoff_id": "handoff-cancel", "validated": True},
    )

    projection = store.load_run(run_id)
    assert projection["status"] == "failed"
    assert projection["last_error"] == {
        "code": "handoff_deadline_exceeded",
        "message": "handoff deadline exceeded",
        "node_id": "start",
    }
    assert projection["nodes"]["start"]["state"] == "failed"
    assert projection["nodes"]["after"]["state"] == "cancelled"
    assert any(
        event["event_type"] == "node_succeeded"
        for event in store.tail_events(run_id)
    )
    assert not marker.exists()


def test_cancel_executor_returns_typed_reason_without_allocating_process(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=WorkflowNode(
            id="stop",
            node_type="cancel",
            value="Unsafe branch",
            depends_on=(),
            source_index=0,
            source_line=1,
            options=freeze_value({}),
        ),
        attempt_id="attempt-1",
    )

    result = CancelExecutor().execute(context)

    assert result.status == "cancelled"
    assert result.error_code == "cancel_node"
    assert result.error_message == "Unsafe branch"
    assert not (run_directory / "nodes").exists()


def test_cancel_node_cancels_pending_downstream_without_starting_it(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="guarded-cancel",
            nodes=[
                {"id": "stop", "cancel": "Guard refused execution"},
                {
                    "id": "after",
                    "bash": f"touch {marker}",
                    "depends_on": ["stop"],
                    "trigger_rule": "all_done",
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "cancelled"
    assert result["nodes"]["stop"]["state"] == "cancelled"
    assert result["nodes"]["after"]["state"] == "cancelled"
    assert result["nodes"]["stop"]["attempts"][-1]["error_message"] == (
        "Guard refused execution"
    )
    assert not marker.exists()


def test_cancel_is_idempotent_and_reports_whether_completion_won(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="idempotent"))
    store = RunStore(tmp_path / "home")
    cancelled_run = _start(store, package, key="cancelled-first")

    first = store.cancel_run(cancelled_run.run_id)
    second = store.cancel_run(cancelled_run.run_id)

    assert first["status"] == "cancelled"
    assert first["cancellation_outcome"] == "cancelled"
    assert second["cancellation_outcome"] == "already_terminal"
    completed_run = _start(store, package, key="completed-first")
    RunScheduler(store).advance(completed_run.run_id)

    late = store.cancel_run(completed_run.run_id)

    assert late["status"] == "succeeded"
    assert late["cancellation_outcome"] == "already_terminal"


def test_cancelled_claim_rejects_late_success_and_releases_worker_capacity(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="late-result"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)

    store.cancel_run(admitted.run_id)

    with pytest.raises(RuntimeError, match="stale"):
        store.complete_node(claim, status="succeeded")
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


def test_cancelled_retry_never_wakes_or_allocates_a_worker(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="retry-cancel"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.schedule_retry(
        claim,
        next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
        error_code="network_error",
    )

    store.cancel_run(admitted.run_id)

    assert (
        store.wake_due_retries(
            admitted.run_id, now=datetime.now(timezone.utc) + timedelta(days=1)
        )
        == ()
    )
    assert RunScheduler(store).advance(admitted.run_id)["status"] == "cancelled"
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


def test_cancel_of_unknown_outward_outcome_requires_reconciliation(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="outward"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="paused",
        error_code="unknown_side_effect",
        metadata={"pending_interaction": "reconcile"},
    )

    result = store.cancel_run(admitted.run_id)

    assert result["status"] == "paused"
    assert result["cancellation_outcome"] == "reconciliation_required"
    assert store.tail_events(admitted.run_id)[-1]["event_type"] == (
        "cancel_reconciliation_required"
    )


def test_cancel_running_outward_attempt_stops_process_but_preserves_uncertainty(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="active-outward"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "worker",
        executor_id="bash",
        effect_classification="outward",
    )
    assert claim is not None
    store.mark_node_started(claim)
    identity = ProcessIdentity(pid=999_996, start_time=56789, group_id=999_996)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    terminated = []
    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        classmethod(
            lambda cls, candidate, **kwargs: terminated.append(candidate) or True
        ),
    )

    result = store.cancel_run(admitted.run_id)

    assert terminated == [identity]
    assert result["status"] == "paused"
    assert result["cancellation_outcome"] == "reconciliation_required"
    node = store.load_run(admitted.run_id)["nodes"]["start"]
    assert node["pending_interaction"]["type"] == "reconcile"
    assert node["recovery"]["termination_confirmed"] is True
    assert node["attempts"][-1]["process_stop"]["cleaned"] is True


def test_cancelled_queued_run_never_starts_a_process(
    tmp_path: Path, workflow_writer
) -> None:
    marker = tmp_path / "queued-must-not-run"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="queued-cancel",
            nodes=[{"id": "work", "bash": f"touch {marker}"}],
        )
    )
    store = RunStore(tmp_path / "home")
    _first = _start(store, package, key="first")
    queued = _start(store, package, key="queued")
    assert queued.disposition == "queued"

    store.cancel_run(queued.run_id)
    result = RunScheduler(store).advance(queued.run_id)

    assert result["status"] == "cancelled"
    assert not marker.exists()


def test_cancelled_paused_loop_releases_capacity_without_spawning(
    tmp_path: Path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="paused-cancel"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="paused",
        metadata={"pending_interaction": {"type": "loop_input", "message": "review"}},
    )

    result = store.cancel_run(admitted.run_id)

    assert result["status"] == "cancelled"
    assert result["nodes"]["start"]["state"] == "cancelled"
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_claims").fetchone()[0] == 0
        )


@pytest.mark.live_system_guard_bypass
def test_cancel_running_script_reaps_its_spawned_descendant(
    tmp_path: Path, workflow_writer
) -> None:
    pid_file = tmp_path / "child.pid"
    source = (
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="script-cancel",
            nodes=[{"id": "work", "script": source, "runtime": "uv"}],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    scheduler = RunScheduler(
        store,
        cooperative_shutdown_seconds=0.05,
        term_grace_seconds=0.2,
        kill_reap_grace_seconds=0.2,
    )
    worker = threading.Thread(target=scheduler.advance, args=(admitted.run_id,))
    worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.01)
    assert pid_file.exists()

    store.cancel_run(admitted.run_id)
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert store.load_run(admitted.run_id)["status"] == "cancelled"
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            import psutil

            child = psutil.Process(child_pid)
            if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"script descendant {child_pid} survived cancellation")


def test_cancel_during_ai_loop_iteration_prevents_next_iteration(
    tmp_path: Path, workflow_writer
) -> None:
    entered = threading.Event()

    class BlockingRunner:
        def __init__(self) -> None:
            self.requests = 0

        def run(self, request, *, is_cancelled=None):
            self.requests += 1
            entered.set()
            deadline = time.monotonic() + 3
            while not is_cancelled() and time.monotonic() < deadline:
                time.sleep(0.01)
            return PluginAgentRunResult(
                final_response="",
                session_id="session",
                provider=request.provider or "fake",
                model=request.model or "fake",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "cancelled"},
            )

    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="loop-cancel",
            nodes=[
                {
                    "id": "iterate",
                    "loop": {
                        "prompt": "Work",
                        "until": "DONE",
                        "max_iterations": 3,
                    },
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    runner = BlockingRunner()
    worker = threading.Thread(
        target=RunScheduler(store, agent_runner=runner).advance,
        args=(admitted.run_id,),
    )
    worker.start()
    assert entered.wait(timeout=2)

    store.cancel_run(admitted.run_id)
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert runner.requests == 1
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


def test_restart_cancel_reaps_a_durably_recorded_process_identity(
    tmp_path: Path, workflow_writer
) -> None:
    home = tmp_path / "home"
    package = load_workflow(workflow_writer(tmp_path / "package", name="orphan-cancel"))
    store = RunStore(home)
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "lost-coordinator")
    assert claim is not None
    store.mark_node_started(claim)
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        policy=TerminationPolicy(
            term_grace_seconds=0.2,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=0.2,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert store.record_process_started(claim, tree.identity)

    restarted = RunStore(home)
    result = restarted.cancel_run(admitted.run_id)
    tree.process.wait(timeout=2)

    assert result["status"] == "cancelled"
    assert not tree.identity.is_current()
    events = restarted.tail_events(admitted.run_id)
    assert any(event["event_type"] == "process_reaped" for event in events)


def test_uninterruptible_recorded_process_reports_cleanup_failed_and_blocks_work(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="stuck-cleanup"))
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    claim = store.claim_node(admitted.run_id, "start", "worker")
    assert claim is not None
    store.mark_node_started(claim)
    identity = ProcessIdentity(pid=999_999, start_time=None, group_id=999_999)
    assert store.record_process_started(claim, identity)
    monkeypatch.setattr(
        ManagedProcessTree,
        "terminate_existing",
        classmethod(lambda cls, identity, **kwargs: False),
    )
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)

    result = store.cancel_run(admitted.run_id)
    duplicate = _start(store, package, key="blocked-by-cleanup")

    assert result["status"] == "running"
    assert result["cancellation_outcome"] == "cleanup_failed"
    assert result["desired_status"] == "cancelled"
    assert duplicate.disposition == "queued"
    events = store.tail_events(admitted.run_id)
    assert any(event["event_type"] == "cleanup_failed" for event in events)
    assert not any(event["event_type"] == "process_reaped" for event in events)


def test_cancelled_nested_outward_reconciliation_retains_exact_scope(
    tmp_path: Path, workflow_writer
) -> None:
    _home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    claim = store.claim_loop_group_child(
        scope,
        "worker",
        expected_state_version=initialized["state_version"],
        executor_id="bash",
        effect_classification="outward",
        execution_authority=_EXECUTION_AUTHORITY,
    )
    assert claim is not None
    store.mark_node_started(claim)

    result = store.cancel_run(run_id)

    child = result["nodes"]["group"]["loop_group"]["body"]["select"]
    assert result["cancellation_outcome"] == "reconciliation_required"
    assert child["recovery"]["loop_group_scope"] == scope.durable_record()
    assert child["pending_interaction"]["loop_group_scope"] == scope.durable_record()


def test_cancel_during_loop_group_predicate_reaps_process_before_group_terminal(
    tmp_path: Path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-predicate-cancel",
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "until_bash": "sleep 30",
                "max_iterations": 1,
                "nodes": [{"id": "sink", "prompt": "produce"}],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-predicate-cancel")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "not complete"
    )
    finished = threading.Event()

    def run_scheduler() -> None:
        try:
            scheduler.advance_all([run_id])
        finally:
            finished.set()

    worker = threading.Thread(target=run_scheduler, daemon=True)
    worker.start()
    # ManagedProcessTree pins and exec-confirms the child before publishing its
    # durable identity; allow loaded macOS runners enough time for that handoff.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(
            event["event_type"] == "process_started"
            for event in store.tail_events(run_id)
        ):
            break
        time.sleep(0.01)
    else:
        pytest.fail("predicate process did not start")

    cancelled = store.cancel_run(run_id)
    worker.join(timeout=5)

    assert finished.is_set()
    assert cancelled["status"] == "cancelled"
    event_types = [event["event_type"] for event in store.tail_events(run_id)]
    assert "process_reaped" in event_types
    assert "loop_group_cancelled" in event_types
    with store._connect() as connection:
        predicate_reserves = connection.execute(
            "SELECT COUNT(*) FROM obligation_journal_reserves WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
    assert predicate_reserves == 0


def test_run_cancel_does_not_relabel_an_already_succeeded_group(
    tmp_path: Path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-completed-group-cancel",
        nodes=[
            {
                "id": "group",
                "loop_group": {
                    "until": "DONE",
                    "max_iterations": 1,
                    "signal_completes": True,
                    "nodes": [{"id": "sink", "prompt": "produce"}],
                },
            },
            {"id": "later", "bash": "true", "depends_on": ["group"]},
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="phase6-completed-group-cancel")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda _context, _rendered: "done <promise>DONE</promise>"
    )

    partial = scheduler.advance(run_id, max_nodes=1)
    cancelled = store.cancel_run(run_id)

    assert partial["nodes"]["group"]["state"] == "succeeded"
    assert cancelled["nodes"]["group"]["state"] == "succeeded"
    cancelled_groups = [
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == "loop_group_cancelled"
    ]
    assert cancelled_groups == []


def test_nested_approval_rejection_reaps_a_parallel_body_process(
    tmp_path: Path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="phase6-nested-rejection-process",
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "max_iterations": 1,
                "nodes": [
                    {"id": "gate", "approval": {"message": "approve"}},
                    {"id": "slow", "bash": "sleep 30"},
                ],
            },
        }],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="phase6-nested-rejection-process")
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    approval = scheduler.executors["approval"]

    class ApprovalAfterProcessStarts:
        def execute(self, context):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if any(
                    event["event_type"] == "process_started"
                    for event in store.tail_events(run_id)
                ):
                    return approval.execute(context)
                time.sleep(0.01)
            pytest.fail("parallel body process did not start")

    scheduler.executors["approval"] = ApprovalAfterProcessStarts()
    finished = threading.Event()

    def run_scheduler() -> None:
        try:
            scheduler.advance_all([run_id])
        except RuntimeError:
            pass
        finally:
            finished.set()

    worker = threading.Thread(target=run_scheduler, daemon=True)
    worker.start()
    deadline = time.monotonic() + 20
    identity = None
    paused = None
    while time.monotonic() < deadline:
        paused = store.load_run(run_id)
        controller = paused["nodes"]["group"].get("loop_group")
        if not isinstance(controller, dict):
            time.sleep(0.01)
            continue
        body = controller["body"]
        pending = body["gate"].get("pending_interaction")
        active = body["slow"].get("claim")
        serialized = active.get("process_identity") if isinstance(active, dict) else None
        if isinstance(pending, dict) and isinstance(serialized, dict):
            identity = ProcessIdentity(
                pid=int(serialized["pid"]),
                start_time=serialized.get("start_time"),
                group_id=serialized.get("group_id"),
                job_name=serialized.get("job_name"),
            )
            break
        time.sleep(0.01)
    else:
        pytest.fail("nested approval and process did not overlap")

    assert identity is not None
    assert paused is not None
    pending = paused["nodes"]["group"]["loop_group"]["body"]["gate"][
        "pending_interaction"
    ]
    try:
        store.reject_run(
            run_id,
            interaction_id=pending["interaction_id"],
            expected_state_version=paused["state_version"],
        )
        worker.join(timeout=5)

        assert finished.is_set()
        assert not identity.is_current()
        assert store.load_run(run_id)["status"] == "cancelled"
        assert any(
            event["event_type"] == "process_reaped"
            for event in store.tail_events(run_id)
        )
    finally:
        if identity.is_current():
            ManagedProcessTree.terminate_existing(identity)
        worker.join(timeout=5)
