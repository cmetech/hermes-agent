from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
)
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.models import ExecutionFence
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessIdentity


def _identity(name: str, *, host_kind: str = "gateway") -> CoordinatorIdentity:
    process = ProcessIdentity.capture(os.getpid())
    return CoordinatorIdentity(
        owner_id=name,
        host_kind=host_kind,
        host_instance_id=f"host-{name}",
        pid=process.pid,
        process_start_time=process.start_time,
    )


class _LeaseClock:
    def __init__(self, sample: LeaseClockSample) -> None:
        self.sample = sample

    def __call__(self) -> LeaseClockSample:
        return self.sample


def test_coordinator_lease_resists_backward_and_forward_wall_clock_steps(
    tmp_path,
) -> None:
    run_store = RunStore(tmp_path)
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = CoordinatorStore(run_store.database, clock=clock)
    first = store.try_acquire(_identity("first"), now=utc, lease_seconds=30)
    assert first.is_leader

    clock.sample = LeaseClockSample(
        utc - timedelta(days=1), 131.0, "boot-a"
    )
    takeover = store.try_acquire(
        _identity("backward-step"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert takeover.is_leader
    assert takeover.lease.epoch == 2

    clock.sample = LeaseClockSample(utc, 200.0, "boot-a")
    store.release(
        _identity("backward-step"),
        epoch=takeover.lease.epoch,
        now=clock.sample.utc_now,
    )
    leader = store.try_acquire(
        _identity("forward-owner"), now=clock.sample.utc_now, lease_seconds=30
    )
    clock.sample = LeaseClockSample(utc + timedelta(days=1), 201.0, "boot-a")
    standby = store.try_acquire(
        _identity("forward-step"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert not standby.is_leader
    assert standby.lease.owner_id == leader.lease.owner_id
    assert store.health(now=clock.sample.utc_now).status == "healthy"

    clock.sample = LeaseClockSample(
        utc + timedelta(days=1), 202.0, "boot-b"
    )
    after_reboot = store.try_acquire(
        _identity("rebooted-host"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert after_reboot.is_leader
    assert after_reboot.lease.epoch == leader.lease.epoch + 1


def test_legacy_clock_domain_fails_closed_until_utc_deadline(
    tmp_path,
) -> None:
    run_store = RunStore(tmp_path)
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = CoordinatorStore(run_store.database, clock=clock)
    first = store.try_acquire(_identity("legacy"), now=utc, lease_seconds=30)
    with run_store._connect() as connection:
        connection.execute(
            "UPDATE coordinator_lease SET boot_id=NULL, heartbeat_monotonic=NULL, "
            "lease_seconds=NULL WHERE singleton=1"
        )

    clock.sample = LeaseClockSample(utc + timedelta(seconds=29), 10000.0, "boot-b")
    refused = store.try_acquire(
        _identity("contender"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert not refused.is_leader
    assert refused.lease.epoch == first.lease.epoch

    clock.sample = LeaseClockSample(utc + timedelta(seconds=31), 10001.0, "boot-b")
    acquired = store.try_acquire(
        _identity("contender"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert acquired.is_leader
    assert acquired.lease.epoch == first.lease.epoch + 1


def test_stale_execution_fence_cannot_mutate_or_interrupt_successor_claim(
    tmp_path, workflow_writer
) -> None:
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = RunStore(tmp_path / "home", lease_clock=clock)
    coordinator = CoordinatorStore(store.database, clock=clock)
    first = coordinator.try_acquire(_identity("first"), now=utc, lease_seconds=30)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="epoch-fence")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="epoch-fence",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="epoch-fence",
            concurrency_key="epoch-fence",
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    first_fence = ExecutionFence("first", first.lease.epoch)
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "coordinator:first:1",
        now=utc,
        monotonic_now=100.0,
        execution_fence=first_fence,
    )
    assert claim is not None
    assert claim.execution_fence == first_fence

    clock.sample = LeaseClockSample(utc - timedelta(days=1), 131.0, "boot-a")
    second = coordinator.try_acquire(
        _identity("second"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert second.is_leader and second.lease.epoch == 2

    with pytest.raises(RuntimeError, match="execution fence"):
        store.mark_node_started(claim, now=clock.sample)
    with pytest.raises(RuntimeError, match="execution fence"):
        store.complete_node(claim, status="succeeded", now=clock.sample)
    with pytest.raises(RuntimeError, match="execution fence"):
        store.schedule_retry(
            claim,
            next_attempt_at=utc + timedelta(minutes=1),
            now=clock.sample,
        )
    assert store.interrupt_active_claims(
        admitted.run_id,
        reason="stale-shutdown",
        fence=ExecutionFence("second", second.lease.epoch),
        now=clock.sample,
    ) == ()
    assert store.load_run(admitted.run_id)["nodes"]["start"]["claim"][
        "attempt_id"
    ] == claim.attempt_id


def test_claim_heartbeat_cannot_mutate_after_epoch_changes(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = RunStore(tmp_path / "home", lease_clock=clock)
    coordinator = CoordinatorStore(store.database, clock=clock)
    first = coordinator.try_acquire(_identity("first"), now=utc, lease_seconds=30)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="heartbeat-fence")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="heartbeat-fence",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="heartbeat-fence",
            concurrency_key="heartbeat-fence",
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(
        admitted.run_id,
        "start",
        "coordinator:first:1",
        now=utc,
        monotonic_now=100.0,
        execution_fence=ExecutionFence("first", first.lease.epoch),
    )
    assert claim is not None
    before = store.load_run(admitted.run_id)
    checked = threading.Event()
    continue_renewal = threading.Event()
    original_check = store._assert_claim_execution_fence

    def pause_after_initial_check(*args, **kwargs) -> None:
        original_check(*args, **kwargs)
        checked.set()
        assert continue_renewal.wait(5)

    monkeypatch.setattr(store, "_assert_claim_execution_fence", pause_after_initial_check)
    outcome: list[bool | BaseException] = []

    def renew() -> None:
        try:
            outcome.append(
                store.renew_claim(
                    claim,
                    now=utc + timedelta(seconds=6),
                    monotonic_now=106.0,
                    fence_now=clock.sample,
                )
            )
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=renew)
    thread.start()
    assert checked.wait(5)
    clock.sample = LeaseClockSample(utc - timedelta(days=1), 131.0, "boot-a")
    second = coordinator.try_acquire(
        _identity("second"), now=clock.sample.utc_now, lease_seconds=30
    )
    assert second.is_leader and second.lease.epoch == 2
    continue_renewal.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert outcome == [False]
    after = store.load_run(admitted.run_id)
    assert after["state_version"] == before["state_version"]
    assert after["nodes"]["start"]["claim"] == before["nodes"]["start"]["claim"]


def _hold_coordinator_lease(database: str, ready, release) -> None:
    process = ProcessIdentity.capture(os.getpid())
    identity = CoordinatorIdentity(
        owner_id="child-coordinator",
        host_kind="gateway",
        host_instance_id="child-host",
        pid=process.pid,
        process_start_time=process.start_time,
    )
    acquired = CoordinatorStore(Path(database)).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    )
    ready.put(acquired.is_leader)
    release.wait(5)


def test_coordinator_lease_renews_releases_and_fences_stale_epoch(tmp_path) -> None:
    run_store = RunStore(tmp_path)
    store = CoordinatorStore(run_store.database)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    first = _identity("first")
    second = _identity("second", host_kind="web")

    acquired = store.try_acquire(first, now=now, lease_seconds=30)
    assert acquired.is_leader is True
    assert acquired.lease.epoch == 1
    assert acquired.lease.owner_id == "first"

    standby = store.try_acquire(second, now=now, lease_seconds=30)
    assert standby.is_leader is False
    assert standby.lease.owner_id == "first"
    assert standby.lease.epoch == 1

    assert store.renew(
        first,
        epoch=1,
        now=now + timedelta(seconds=5),
        lease_seconds=30,
        sweep_cursor="run-10",
        last_progress_at=now + timedelta(seconds=4),
    )
    observed = store.observe(now=now + timedelta(seconds=6))
    assert observed is not None
    assert observed.sweep_cursor == "run-10"
    assert observed.last_progress_at == now + timedelta(seconds=4)

    assert store.release(first, epoch=1, now=now + timedelta(seconds=7))
    takeover = store.try_acquire(
        second,
        now=now + timedelta(seconds=7),
        lease_seconds=30,
    )
    assert takeover.is_leader is True
    assert takeover.lease.epoch == 2

    assert not store.renew(
        first,
        epoch=1,
        now=now + timedelta(seconds=8),
        lease_seconds=30,
    )
    assert not store.release(first, epoch=1, now=now + timedelta(seconds=8))
    assert store.observe(now=now + timedelta(seconds=8)).owner_id == "second"


def test_expired_lease_requires_reacquisition_and_increments_epoch(tmp_path) -> None:
    run_store = RunStore(tmp_path)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(now, 100.0, "boot-a"))
    store = CoordinatorStore(run_store.database, clock=clock)
    identity = _identity("laptop")

    first = store.try_acquire(identity, now=now, lease_seconds=30)
    assert first.is_leader is True
    clock.sample = LeaseClockSample(
        now + timedelta(minutes=5), 131.0, "boot-a"
    )
    assert not store.renew(
        identity,
        epoch=first.lease.epoch,
        now=now + timedelta(minutes=5),
        lease_seconds=30,
    )

    reclaimed = store.try_acquire(
        identity,
        now=now + timedelta(minutes=5),
        lease_seconds=30,
    )
    assert reclaimed.is_leader is True
    assert reclaimed.lease.epoch == first.lease.epoch + 1


def test_coordinator_health_distinguishes_missing_fresh_and_stale(tmp_path) -> None:
    run_store = RunStore(tmp_path)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(now, 100.0, "boot-a"))
    store = CoordinatorStore(run_store.database, clock=clock)

    assert store.health(now=now).status == "unavailable"
    acquired = store.try_acquire(_identity("owner"), now=now, lease_seconds=30)
    assert acquired.is_leader is True
    clock.sample = LeaseClockSample(
        now + timedelta(seconds=1), 101.0, "boot-a"
    )
    assert store.health(now=now + timedelta(seconds=1)).status == "healthy"
    clock.sample = LeaseClockSample(
        now + timedelta(seconds=31), 131.0, "boot-a"
    )
    stale = store.health(now=now + timedelta(seconds=31))
    assert stale.status == "unavailable"
    assert stale.reason_code == "coordinator_lease_expired"


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before deadline")


def _service(
    home: Path,
    *,
    host_kind: str,
    host_instance_id: str,
    web_grace_seconds: float = 0.05,
) -> WorkflowCoordinatorService:
    return WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind=host_kind,
            host_instance_id=host_instance_id,
        ),
        hermes_home=home,
        heartbeat_seconds=0.02,
        lease_seconds=0.15,
        web_election_grace_seconds=web_grace_seconds,
        sweep_backoff_seconds=(0.02, 0.04, 0.08),
    )


def test_gateway_leader_and_web_standby_report_cached_health(tmp_path) -> None:
    gateway = _service(tmp_path, host_kind="gateway", host_instance_id="gateway-1")
    web = _service(tmp_path, host_kind="web", host_instance_id="web-1")
    gateway_stop = threading.Event()
    web_stop = threading.Event()
    threads = [
        threading.Thread(target=gateway.run, args=(gateway_stop,)),
        threading.Thread(target=web.run, args=(web_stop,)),
    ]
    for thread in threads:
        thread.start()
    try:
        _wait_until(lambda: gateway.health().code == "leader")
        _wait_until(lambda: web.health().code == "standby")
        started = time.monotonic()
        health = web.health()
        assert time.monotonic() - started < 0.05
        assert health.state == "healthy"
        assert health.heartbeat_at is not None
    finally:
        gateway_stop.set()
        web_stop.set()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


def test_web_only_host_observes_election_grace_then_becomes_leader(tmp_path) -> None:
    service = _service(
        tmp_path,
        host_kind="web",
        host_instance_id="web-only",
        web_grace_seconds=0.08,
    )
    stop = threading.Event()
    started = time.monotonic()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
        _wait_until(lambda: service.health().code == "leader")
        assert time.monotonic() - started >= 0.07
    finally:
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_admission_wake_survives_without_process_local_notification(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    monkeypatch.setattr(CoordinatorStore, "notify_local", lambda _self: None)
    package = load_workflow(workflow_writer(tmp_path / "package", name="wake"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="wake",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="durable-wake",
            concurrency_key="wake",
        ),
        immutable_snapshot=prepared,
    )
    now = datetime.now(timezone.utc)
    identity = _identity("wake-reader")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)

    wakes = coordinator.pending_wakes(
        identity,
        epoch=leadership.lease.epoch,
        now=now,
    )
    assert [(wake.run_id, wake.reason_code) for wake in wakes] == [
        (admitted.run_id, "run_admitted")
    ]


def test_durable_approval_wake_continues_outside_mutating_caller(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="coordinated-approval",
            nodes=[
                {
                    "id": "review",
                    "approval": {"message": "Approve?"},
                },
                {
                    "id": "finish",
                    "bash": "printf finished",
                    "depends_on": ["review"],
                },
            ],
        )
    )
    store = RunStore(tmp_path)
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="continuation-owner",
    )
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
        _wait_until(lambda: service.health().code == "leader")
        prepared = store.prepare_run_snapshot(package)
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="desktop",
                idempotency_key="approval-wake",
                concurrency_key=package.definition.name,
                execution_mode="background",
            ),
            immutable_snapshot=prepared,
        )
        _wait_until(lambda: store.get_run_status(admitted.run_id)["status"] == "paused")
        paused = store.get_run_status(admitted.run_id)
        interaction = paused["nodes"]["review"]["pending_interaction"]
        store.approve_run(
            admitted.run_id,
            expected_state_version=paused["state_version"],
            interaction_id=interaction["interaction_id"],
            channel="desktop",
        )
        _wait_until(
            lambda: store.get_run_status(admitted.run_id)["status"] == "succeeded"
        )
    finally:
        stop.set()
        CoordinatorStore(store.database).notify_local()
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_coordinator_promotes_queued_run_after_lane_owner_cancels(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="promotion",
            nodes=[{"id": "gate", "approval": {"message": "Continue?"}}],
        )
    )
    store = RunStore(tmp_path)
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="promotion-owner",
    )
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
        _wait_until(lambda: service.health().code == "leader")

        def admit(key: str):
            prepared = store.prepare_run_snapshot(package)
            return store.start_run(
                RunAdmissionRequest(
                    workflow_name="promotion",
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=key,
                    concurrency_key="promotion",
                    execution_mode="background",
                ),
                immutable_snapshot=prepared,
            )

        first = admit("promotion-first")
        second = admit("promotion-second")
        assert second.disposition == "queued"
        _wait_until(lambda: store.get_run_status(first.run_id)["status"] == "paused")
        assert store.get_run_status(second.run_id)["status"] == "queued"

        store.cancel_run(first.run_id)

        _wait_until(lambda: store.get_run_status(first.run_id)["status"] == "cancelled")
        _wait_until(lambda: store.get_run_status(second.run_id)["status"] == "paused")
        promoted = store.get_run_status(second.run_id)
        assert promoted["queue_position"] is None
        assert promoted["blocked_by_run_id"] is None
    finally:
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_workflow_plugin_registers_one_generic_service_for_both_hosts() -> None:
    from plugins.workflow import register
    from plugins.workflow.coordinator import create_workflow_coordinator

    context = MagicMock()
    register(context)

    context.register_background_service.assert_called_once_with(
        "coordinator",
        create_workflow_coordinator,
        hosts={"web", "gateway"},
    )


def test_background_admission_requires_a_fresh_coordinator_without_evidence_leak(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    package = load_workflow(workflow_writer(tmp_path / "package", name="background"))
    prepared = store.prepare_run_snapshot(package)
    request = RunAdmissionRequest(
        workflow_name="background",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="api",
        idempotency_key="background-no-owner",
        concurrency_key="background",
        execution_mode="background",
    )

    rejected = store.start_run(request, immutable_snapshot=prepared)

    assert rejected.reason_code == "coordinator_unavailable"
    assert rejected.run_id is None
    assert list(store.runs_root.glob("*/*")) == []


def test_background_status_exposes_coordinator_loss_and_structural_stall(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    now = datetime.now(timezone.utc)
    identity = _identity("status-owner")
    acquired = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(workflow_writer(tmp_path / "package", name="health"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="health",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="health",
            concurrency_key="health",
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )

    healthy = store.get_run_status(admitted.run_id)
    assert healthy["health"] == "healthy"
    assert healthy["coordinator"]["status"] == "healthy"
    assert healthy["coordinator"]["epoch"] == acquired.lease.epoch

    assert coordinator.release(
        identity,
        epoch=acquired.lease.epoch,
        now=datetime.now(timezone.utc),
    )
    unavailable = store.get_run_status(admitted.run_id)
    assert unavailable["health"] == "coordinator_unavailable"
    assert unavailable["blocking_reason"] == "coordinator_lease_expired"

    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="replacement-owner",
    )
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
        _wait_until(lambda: service.health().code == "leader")
        _wait_until(
            lambda: store.get_run_status(admitted.run_id)["status"] == "succeeded"
        )
        recovered = store.get_run_status(admitted.run_id)
        assert recovered["health"] == "terminal"
        assert recovered["coordinator"]["status"] == "healthy"
    finally:
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_running_graph_with_no_current_node_is_reported_stalled(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    assert coordinator.try_acquire(
        _identity("stall-owner"),
        now=datetime.now(timezone.utc),
        lease_seconds=30,
    ).is_leader
    package = load_workflow(workflow_writer(tmp_path / "package", name="stalled"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="stalled",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="stalled",
            concurrency_key="stalled",
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
        projection_updates={"nodes": nodes},
    )

    status = store.get_run_status(admitted.run_id)

    assert status["status"] == "running"
    assert status["health"] == "stalled"
    assert status["blocking_reason"] == "no_runnable_or_owned_node"


def test_foreground_and_background_admission_are_fenced_by_live_leader(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    now = datetime.now(timezone.utc)
    identity = _identity("admission-owner")
    assert coordinator.try_acquire(identity, now=now, lease_seconds=30).is_leader
    package = load_workflow(workflow_writer(tmp_path / "package", name="arbitration"))

    foreground_snapshot = store.prepare_run_snapshot(package)
    foreground = store.start_run(
        RunAdmissionRequest(
            workflow_name="arbitration",
            definition_digest=foreground_snapshot.definition_digest,
            policy_digest=foreground_snapshot.policy_digest,
            input_manifest_digest=foreground_snapshot.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="foreground-race",
            concurrency_key="arbitration",
            execution_mode="foreground",
            foreground_owner_id="foreground-process",
        ),
        immutable_snapshot=foreground_snapshot,
    )
    assert foreground.reason_code == "coordinator_active"

    background_snapshot = store.prepare_run_snapshot(package)
    background = store.start_run(
        RunAdmissionRequest(
            workflow_name="arbitration",
            definition_digest=background_snapshot.definition_digest,
            policy_digest=background_snapshot.policy_digest,
            input_manifest_digest=background_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="background-owned",
            concurrency_key="arbitration",
            execution_mode="background",
        ),
        immutable_snapshot=background_snapshot,
    )
    assert background.disposition == "created"
    projection = store.load_run(background.run_id)
    assert projection["execution_mode"] == "background"


def test_foreground_admission_is_fenced_by_coordinator_in_another_process(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="process-arbitration")
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    child = context.Process(
        target=_hold_coordinator_lease,
        args=(str(store.database), ready, release),
    )
    child.start()
    try:
        assert ready.get(timeout=5) is True
        prepared = store.prepare_run_snapshot(package)
        result = store.start_run(
            RunAdmissionRequest(
                workflow_name="process-arbitration",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key="cross-process-foreground",
                concurrency_key="process-arbitration",
                execution_mode="foreground",
                foreground_owner_id="parent-foreground",
            ),
            immutable_snapshot=prepared,
        )
        assert result.reason_code == "coordinator_active"
        assert result.run_id is None
    finally:
        release.set()
        child.join(timeout=5)
        assert child.exitcode == 0


def test_foreground_execution_lease_requires_exact_unexpired_fencing_token(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    package = load_workflow(workflow_writer(tmp_path / "package", name="foreground"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="foreground",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="foreground-lease",
            concurrency_key="foreground",
            execution_mode="foreground",
            foreground_owner_id="foreground-owner",
            foreground_lease_seconds=30,
        ),
        immutable_snapshot=prepared,
    )
    admitted_at = datetime.fromisoformat(
        store.load_run(admitted.run_id)["foreground_lease_expires_at"]
    ) - timedelta(seconds=30)

    assert not store.renew_foreground_execution(
        admitted.run_id,
        owner_id="different-owner",
        epoch=1,
        now=admitted_at + timedelta(seconds=1),
        lease_seconds=30,
    )
    assert store.renew_foreground_execution(
        admitted.run_id,
        owner_id="foreground-owner",
        epoch=1,
        now=admitted_at + timedelta(seconds=1),
        lease_seconds=30,
    )

    assert not store.claim_foreground_execution(
        admitted.run_id,
        owner_id="replacement-owner",
        now=admitted_at + timedelta(seconds=2),
        lease_seconds=30,
    )
    assert store.release_foreground_execution(
        admitted.run_id,
        owner_id="foreground-owner",
        epoch=1,
        now=admitted_at + timedelta(seconds=2),
    )
    replacement = store.claim_foreground_execution(
        admitted.run_id,
        owner_id="replacement-owner",
        now=admitted_at + timedelta(seconds=3),
        lease_seconds=30,
    )
    assert replacement is not None
    assert replacement.owner_id == "replacement-owner"
    assert replacement.epoch == 2
    assert not store.renew_foreground_execution(
        admitted.run_id,
        owner_id="foreground-owner",
        epoch=1,
        now=admitted_at + timedelta(minutes=2),
        lease_seconds=30,
    )


def test_scheduler_does_not_claim_after_foreground_execution_lease_expires(
    tmp_path, workflow_writer
) -> None:
    marker = tmp_path / "must-not-run"
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="expired-foreground",
            nodes=[{"id": "effect", "bash": f"touch {marker}"}],
        )
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="expired-foreground",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="expired-foreground",
            concurrency_key="expired-foreground",
            execution_mode="foreground",
            foreground_owner_id="expired-owner",
            foreground_lease_seconds=1,
        ),
        immutable_snapshot=prepared,
    )
    expired_at = datetime.fromisoformat(
        store.load_run(admitted.run_id)["foreground_lease_expires_at"]
    ) + timedelta(seconds=1)

    result = RunScheduler(
        store,
        owner_id="expired-owner",
        execution_owner_id="expired-owner",
        execution_owner_epoch=1,
        utcnow=lambda: expired_at,
    ).advance(admitted.run_id)

    assert result["nodes"]["effect"]["state"] == "ready"
    assert not marker.exists()
