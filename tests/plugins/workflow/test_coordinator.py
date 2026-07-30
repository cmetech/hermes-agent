from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path
import threading
import time
from unittest.mock import ANY, MagicMock

import pytest

from hermes_cli.plugin_services import BackgroundServiceContext
from hermes_cli.plugin_invocation import DeliveryReceipt
import plugins.workflow.coordinator as coordinator_module
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
)
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ExecutionFence
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import run_showcase
from plugins.workflow.store import ForegroundExecutionConflict, RunStore
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


def test_foreground_lease_clock_resists_backward_and_forward_wall_steps(
    tmp_path, workflow_writer
) -> None:
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    backward_clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    backward_store = RunStore(tmp_path / "backward-home", lease_clock=backward_clock)
    package = load_workflow(
        workflow_writer(tmp_path / "backward-package", name="foreground-backward")
    )
    prepared = backward_store.prepare_run_snapshot(package)
    backward = backward_store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="foreground-backward",
            concurrency_key="foreground-backward",
            execution_mode="foreground",
            foreground_owner_id="original-owner",
            foreground_lease_seconds=30,
        ),
        immutable_snapshot=prepared,
    )
    backward_clock.sample = LeaseClockSample(utc - timedelta(days=1), 131.0, "boot-a")
    replacement = backward_store.claim_foreground_execution(
        backward.run_id,
        owner_id="replacement-owner",
        now=backward_clock.sample.utc_now,
        lease_seconds=30,
    )
    assert replacement is not None
    assert replacement.boot_id == "boot-a"
    assert replacement.heartbeat_monotonic == 131.0

    forward_clock = _LeaseClock(LeaseClockSample(utc, 200.0, "boot-a"))
    forward_store = RunStore(tmp_path / "forward-home", lease_clock=forward_clock)
    package = load_workflow(
        workflow_writer(tmp_path / "forward-package", name="foreground-forward")
    )
    prepared = forward_store.prepare_run_snapshot(package)
    forward = forward_store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="foreground-forward",
            concurrency_key="foreground-forward",
            execution_mode="foreground",
            foreground_owner_id="forward-owner",
            foreground_lease_seconds=30,
        ),
        immutable_snapshot=prepared,
    )
    coordinator = CoordinatorStore(forward_store.database, clock=forward_clock)
    leadership = coordinator.try_acquire(
        _identity("foreground-forward-coordinator"),
        now=utc,
        lease_seconds=30,
    )
    forward_clock.sample = LeaseClockSample(utc + timedelta(days=1), 201.0, "boot-a")
    with pytest.raises(ForegroundExecutionConflict, match="still active"):
        forward_store.adopt_expired_foreground(
            forward.run_id,
            ExecutionFence(leadership.lease.owner_id, leadership.lease.epoch),
            forward_clock.sample.utc_now,
        )


def test_legacy_foreground_lease_fails_closed_until_utc_deadline(
    tmp_path, workflow_writer
) -> None:
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = RunStore(tmp_path / "home", lease_clock=clock)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="legacy-foreground")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-foreground",
            concurrency_key="legacy-foreground",
            execution_mode="foreground",
            foreground_owner_id="legacy-owner",
            foreground_lease_seconds=30,
        ),
        immutable_snapshot=prepared,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET foreground_boot_id=NULL, "
            "foreground_heartbeat_monotonic=NULL, foreground_lease_seconds=NULL "
            "WHERE run_id=?",
            (admitted.run_id,),
        )

    clock.sample = LeaseClockSample(utc + timedelta(seconds=29), 10000.0, "boot-b")
    assert (
        store.claim_foreground_execution(
            admitted.run_id,
            owner_id="contender",
            now=clock.sample.utc_now,
            lease_seconds=30,
        )
        is None
    )

    clock.sample = LeaseClockSample(utc + timedelta(seconds=31), 10001.0, "boot-b")
    acquired = store.claim_foreground_execution(
        admitted.run_id,
        owner_id="contender",
        now=clock.sample.utc_now,
        lease_seconds=30,
    )
    assert acquired is not None
    assert acquired.epoch == 2


def test_coordinator_adopts_legacy_cli_showcase_and_reworks_without_real_ai(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    runner_calls = 0

    def forbidden_real_run(*_args, **_kwargs):
        nonlocal runner_calls
        runner_calls += 1
        raise AssertionError("adopted legacy showcase selected real model execution")

    monkeypatch.setattr(
        "agent.plugin_agent.PluginAgentRunner.run", forbidden_real_run
    )
    started = run_showcase(
        "laptop-diagnostic",
        hermes_home=tmp_path,
        symptom="fictional adopted startup issue",
        no_wait=True,
        idempotency_key="v302-adopted-laptop",
    )
    run_id = started["run_id"]
    store = RunStore(tmp_path)
    before = store.get_run_status(run_id)
    assert before["status"] in {"running", "queued"}
    assert set(before["run_metadata"]) == {
        "showcase_id",
        "showcase_version",
        "bundle_digest",
        "risk_digest",
    }
    assert store.release_foreground_execution(
        run_id,
        owner_id=before["foreground_owner_id"],
        epoch=before["foreground_epoch"],
        now=datetime.now(timezone.utc),
    )

    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="legacy-showcase-adopter",
    )
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
        _wait_until(
            lambda: store.get_run_status(run_id)["status"] == "paused",
            timeout=10,
        )
        paused = store.get_run_status(run_id)
        assert paused["execution_mode"] == "background"
        pending = paused["pending_interaction"]
        store.reject_run(
            run_id,
            reason="make the adopted plan more cautious",
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
            channel="desktop",
        )
        _wait_until(
            lambda: store.get_run_status(run_id)["nodes"]["review-plan"].get(
                "approval_rework_attempts"
            )
            == 1,
            timeout=10,
        )
        reworked = store.get_run_status(run_id)
        assert reworked["status"] == "paused"
        assert runner_calls == 0
    finally:
        stop.set()
        CoordinatorStore(store.database).notify_local()
        thread.join(timeout=10)
        assert not thread.is_alive()
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
        sweep_cursor=("2026-07-18T12:00:00+00:00", "run-10"),
        last_progress_at=now + timedelta(seconds=4),
        last_sweep_at=now + timedelta(seconds=3),
    )
    observed = store.observe(now=now + timedelta(seconds=6))
    assert observed is not None
    assert observed.sweep_cursor == ("2026-07-18T12:00:00+00:00", "run-10")
    assert observed.last_progress_at == now + timedelta(seconds=4)
    assert observed.last_sweep_at == now + timedelta(seconds=3)

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


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
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
    delivery_port=None,
) -> WorkflowCoordinatorService:
    return WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind=host_kind,
            host_instance_id=host_instance_id,
            delivery_port=delivery_port,
        ),
        hermes_home=home,
        heartbeat_seconds=0.1,
        lease_seconds=3.0,
        web_election_grace_seconds=web_grace_seconds,
        sweep_backoff_seconds=(0.02, 0.04, 0.08),
    )


def test_gateway_leader_and_web_standby_report_cached_health(tmp_path) -> None:
    gateway = _service(tmp_path, host_kind="gateway", host_instance_id="gateway-1")
    web = _service(tmp_path, host_kind="web", host_instance_id="web-1")
    gateway_stop = threading.Event()
    web_stop = threading.Event()
    gateway_thread = threading.Thread(target=gateway.run, args=(gateway_stop,))
    web_thread = threading.Thread(target=web.run, args=(web_stop,))
    gateway_thread.start()
    web_started = False
    try:
        _wait_until(lambda: gateway.health().code == "leader")
        web_thread.start()
        web_started = True
        _wait_until(lambda: web.health().code == "standby")
        started = time.monotonic()
        health = web.health()
        assert time.monotonic() - started < 0.05
        assert health.state == "healthy"
        assert health.heartbeat_at is not None
    finally:
        gateway_stop.set()
        web_stop.set()
        threads = (gateway_thread, web_thread) if web_started else (gateway_thread,)
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


def test_gateway_standby_delivers_notifications_while_web_holds_leadership(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    delivered: list[tuple[str, str, str]] = []

    class Port:
        def deliver(self, capability: str, text: str, key: str) -> DeliveryReceipt:
            delivered.append((capability, text, key))
            return DeliveryReceipt(status="delivered", transport_id="message-1")

    web = _service(tmp_path, host_kind="web", host_instance_id="web-leader")
    gateway = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="gateway-standby",
        delivery_port=Port(),
    )
    web_stop = threading.Event()
    gateway_stop = threading.Event()
    web_thread = threading.Thread(target=web.run, args=(web_stop,))
    gateway_thread = threading.Thread(target=gateway.run, args=(gateway_stop,))
    web_thread.start()
    try:
        _wait_until(lambda: web.health().code == "leader")
        gateway_thread.start()
        _wait_until(lambda: gateway.health().code == "standby")

        package = load_workflow(
            workflow_writer(tmp_path / "package", name="standby-delivery")
        )
        snapshot = store.prepare_run_snapshot(package)
        capability = "server-minted-capability"
        admitted = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=snapshot.definition_digest,
                policy_digest=snapshot.policy_digest,
                input_manifest_digest=snapshot.input_manifest_digest,
                trigger_source="chat",
                idempotency_key="standby-delivery",
                concurrency_key=package.definition.name,
                execution_mode="background",
                provenance=TriggerProvenance(
                    source="chat",
                    assurance="verified_adapter",
                    intent_key="standby-delivery",
                    source_instance="gateway:telegram",
                    actor_id="gateway:telegram:user-1",
                    return_route=capability,
                ),
            ),
            immutable_snapshot=snapshot,
        )
        outbox = NotificationOutbox(store)
        outbox.record(
            run_id=admitted.run_id,
            kind="completion",
            destination="desktop",
            transition_version=99,
            payload={"workflow": package.definition.name, "status": "succeeded"},
        )

        _wait_until(
            lambda: any(
                row["destination"] == "gateway:opaque"
                and row["state"] == "delivered"
                for row in outbox.history(run_id=admitted.run_id)
            )
        )

        assert web.health().code == "leader"
        assert gateway.health().code == "standby"
        assert delivered[0][0] == capability
        gateway_rows = [
            row
            for row in outbox.history(run_id=admitted.run_id)
            if row["destination"] == "gateway:opaque"
        ]
        assert gateway_rows[0]["state"] == "delivered"
    finally:
        web_stop.set()
        gateway_stop.set()
        CoordinatorStore(store.database).notify_local()
        web_thread.join(timeout=10)
        gateway_thread.join(timeout=10)
        assert not web_thread.is_alive()
        assert not gateway_thread.is_alive()


def test_blocked_standby_delivery_does_not_delay_leadership_takeover(
    tmp_path,
) -> None:
    store = RunStore(tmp_path)
    delivery_started = threading.Event()
    release_delivery = threading.Event()

    class BlockingPort:
        def deliver(self, _capability: str, _text: str, _key: str) -> DeliveryReceipt:
            delivery_started.set()
            if not release_delivery.wait(timeout=5):
                raise TimeoutError("test delivery was not released")
            return DeliveryReceipt(status="delivered", transport_id="message-1")

    web = _service(tmp_path, host_kind="web", host_instance_id="web-leader")
    gateway = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="gateway-standby",
        delivery_port=BlockingPort(),
    )
    web_stop = threading.Event()
    gateway_stop = threading.Event()
    web_thread = threading.Thread(target=web.run, args=(web_stop,))
    gateway_thread = threading.Thread(target=gateway.run, args=(gateway_stop,))
    web_thread.start()
    try:
        _wait_until(lambda: web.health().code == "leader")
        gateway_thread.start()
        _wait_until(lambda: gateway.health().code == "standby")
        NotificationOutbox(store).record(
            run_id="blocked-standby-delivery",
            kind="completion",
            destination="gateway:server-minted-capability",
            transition_version=1,
            payload={"status": "succeeded"},
        )
        assert delivery_started.wait(timeout=1)

        web_stop.set()
        CoordinatorStore(store.database).notify_local()
        web_thread.join(timeout=2)
        assert not web_thread.is_alive()

        _wait_until(lambda: gateway.health().code == "leader", timeout=1)
        assert not release_delivery.is_set()
    finally:
        release_delivery.set()
        web_stop.set()
        gateway_stop.set()
        CoordinatorStore(store.database).notify_local()
        web_thread.join(timeout=2)
        gateway_thread.join(timeout=6)
        assert not web_thread.is_alive()
        assert not gateway_thread.is_alive()


def test_gateway_retryable_delivery_receipt_requeues_outbox_row(tmp_path) -> None:
    outbox = NotificationOutbox(RunStore(tmp_path))
    notification_id = outbox.record(
        run_id="retryable-gateway-delivery",
        kind="completion",
        destination="gateway:server-minted-capability",
        transition_version=1,
        payload={"status": "succeeded"},
    )

    class RetryablePort:
        def deliver(self, _capability: str, _text: str, _key: str) -> DeliveryReceipt:
            return DeliveryReceipt(
                status="retryable_failure", detail="delivery_store_unavailable"
            )

    assert WorkflowCoordinatorService._deliver_gateway_notifications(
        outbox,
        RetryablePort(),
        owner_id="delivery:gateway-standby",
    ) == 0

    row = next(
        item for item in outbox.history() if item["notification_id"] == notification_id
    )
    assert row["state"] == "pending"
    assert row["attempts"] == 1
    assert row["lease_owner"] is None
    assert row["last_error"] == "delivery_store_unavailable"


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
    assert status["health"] == "healthy"
    assert status["blocking_reason"] is None


def test_runtime_stall_defaults_and_lease_ratio_are_validated() -> None:
    from plugins.workflow.models import WorkflowRuntimeConfig

    config = WorkflowRuntimeConfig()
    assert config.runnable_stall_seconds == 60
    assert config.semantic_stall_seconds == 300

    with pytest.raises(ValueError, match="at least three heartbeats"):
        WorkflowRuntimeConfig(heartbeat_seconds=5, lease_seconds=14)


def test_scheduler_submit_is_nonblocking_deduplicated_and_avoids_head_of_line(
    tmp_path, monkeypatch
) -> None:
    fence = ExecutionFence("leader", 1)
    scheduler = RunScheduler(
        RunStore(tmp_path), max_parallel_nodes=2, execution_fence=fence
    )
    first_started = threading.Event()
    release_first = threading.Event()
    second_completed = threading.Event()

    def advance(run_id):
        if run_id == "long":
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_completed.set()

    monkeypatch.setattr(scheduler, "advance", advance)
    started = time.monotonic()
    assert scheduler.submit("long", fence)
    assert time.monotonic() - started < 0.05
    assert first_started.wait(timeout=1)
    assert not scheduler.submit("long", fence)
    assert scheduler.submit("short", fence)
    assert second_completed.wait(timeout=1)
    release_first.set()
    scheduler.shutdown(deadline_seconds=2)


def test_sweep_selection_preserves_global_order_below_budget() -> None:
    ordered = ["scheduled-0", "periodic-0", "scheduled-1"]

    selected = coordinator_module._select_sweep_run_ids(
        ordered,
        ["periodic-0"],
    )

    assert selected == ordered


def test_sweep_selection_reserves_periodic_head_before_saturated_prefix() -> None:
    scheduled = [f"scheduled-{index:03d}" for index in range(100)]

    selected = coordinator_module._select_sweep_run_ids(
        [*scheduled, "periodic-0", "periodic-1"],
        ["periodic-0", "periodic-1"],
    )

    assert selected == ["periodic-0", *scheduled[:99]]


def test_sweep_selection_does_not_duplicate_reserved_head_in_prefix() -> None:
    scheduled = [f"scheduled-{index:03d}" for index in range(100)]

    selected = coordinator_module._select_sweep_run_ids(
        ["periodic-0", *scheduled],
        ["periodic-0"],
    )

    assert selected == ["periodic-0", *scheduled[:99]]
    assert selected.count("periodic-0") == 1


def test_sweep_selection_reserves_periodic_and_scheduled_continuation_heads() -> None:
    fresh = [f"fresh-{index:03d}" for index in range(100)]

    selected = coordinator_module._select_sweep_run_ids(
        [*fresh, "continuation-0", "periodic-0"],
        ["periodic-0"],
        ["continuation-0"],
    )

    assert selected == ["periodic-0", "continuation-0", *fresh[:98]]
    assert len(selected) == 100


def test_idle_backoff_uses_actionable_work_not_rows_seen(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    now = datetime.now(timezone.utc)
    identity = _identity("idle-scan")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="idle-scan")
    )
    prepared = store.prepare_run_snapshot(package)
    store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="idle-scan",
            concurrency_key=package.definition.name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = False
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="idle-scan",
    )

    actionable, _cursor, _progress = service._sweep_once(
        store,
        coordinator,
        identity,
        leadership.lease.epoch,
        scheduler,
    )

    assert actionable is False
    scheduler.submit.assert_called_once()
    scheduler.advance.assert_not_called()


def test_repair_lock_timeout_does_not_block_delivery_or_scheduling(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    identity = _identity("repair-contention")
    now = datetime.now(timezone.utc)
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="repair-contention")
    )

    terminal_snapshot = store.prepare_run_snapshot(package)
    terminal = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=terminal_snapshot.definition_digest,
            policy_digest=terminal_snapshot.policy_digest,
            input_manifest_digest=terminal_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="repair-contention-terminal",
            concurrency_key="repair-contention-terminal",
            execution_mode="background",
        ),
        immutable_snapshot=terminal_snapshot,
    )
    RunScheduler(
        store,
        owner_id=f"coordinator:{identity.owner_id}:{leadership.lease.epoch}",
        execution_fence=fence,
    ).advance(terminal.run_id)
    for wake in coordinator.pending_wakes(
        identity,
        epoch=leadership.lease.epoch,
        now=now,
        limit=100,
    ):
        if wake.run_id == terminal.run_id:
            assert coordinator.complete_wake(
                wake.generation,
                identity,
                epoch=leadership.lease.epoch,
                now=now,
                outcome="test_terminal_setup",
            )

    queued_snapshot = store.prepare_run_snapshot(package)
    queued = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=queued_snapshot.definition_digest,
            policy_digest=queued_snapshot.policy_digest,
            input_manifest_digest=queued_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="repair-contention-queued",
            concurrency_key="repair-contention-queued",
            concurrency_policy="allow",
            execution_mode="background",
        ),
        immutable_snapshot=queued_snapshot,
    )
    NotificationOutbox(store).record(
        run_id=queued.run_id,
        kind="failure",
        destination="gateway:opaque-capability",
        transition_version=999,
        payload={"workflow": package.definition.name},
        now=now,
    )

    delivered = []

    class Port:
        def deliver(self, capability: str, text: str, key: str) -> DeliveryReceipt:
            delivered.append((capability, text, key))
            return DeliveryReceipt(status="delivered", transport_id="message-1")

    ready = threading.Event()
    release = threading.Event()

    def hold_run_lock() -> None:
        with workflow_lock(store._run_lock_path(terminal.run_id)):
            ready.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_run_lock)
    holder.start()
    assert ready.wait(timeout=1)
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="repair-contention",
        delivery_port=Port(),
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    try:
        actionable, _cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
        )
    finally:
        release.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert actionable is True
    assert delivered == [("opaque-capability", ANY, ANY)]
    scheduler.submit.assert_any_call(queued.run_id, fence)


def test_sweep_cursor_never_skips_page_prefix_when_wake_consumes_budget(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    now = datetime.now(timezone.utc)
    identity = _identity("cursor-prefix")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cursor-prefix")
    )
    admitted = []
    for index in range(3):
        prepared = store.prepare_run_snapshot(package)
        admitted.append(
            store.start_run(
                RunAdmissionRequest(
                    workflow_name=package.definition.name,
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=f"cursor-prefix-{index}",
                    concurrency_key=f"cursor-prefix-{index}",
                    concurrency_policy="allow",
                    execution_mode="background",
                ),
                immutable_snapshot=prepared,
            ).run_id
        )
    wakes = coordinator.pending_wakes(
        identity,
        epoch=leadership.lease.epoch,
        now=now,
    )
    for wake in wakes[:2]:
        assert coordinator.complete_wake(
            wake.generation,
            identity,
            epoch=leadership.lease.epoch,
            now=now,
            outcome="test_setup",
        )
    monotonic = MagicMock(side_effect=(0.0, 0.1, 0.2, 2.1))
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="cursor-prefix",
        ),
        hermes_home=tmp_path,
        monotonic=monotonic,
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = False

    _actionable, cursor, _progress = service._sweep_once(
        store,
        coordinator,
        identity,
        leadership.lease.epoch,
        scheduler,
    )

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert submitted
    assert submitted == admitted[: len(submitted)]
    last_submitted = store.load_run(submitted[-1])
    assert cursor == (last_submitted["created_at"], submitted[-1])
    assert all(
        call.args[1] == ExecutionFence(identity.owner_id, leadership.lease.epoch)
        for call in scheduler.submit.call_args_list
    )


def test_scheduled_due_pages_advance_past_a_stably_lane_blocked_first_page(
    tmp_path, workflow_writer
) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    # Freeze the lease clock, exactly as the sibling test below does. Without it
    # the service runs on a frozen clock while the STORE still judges lease
    # freshness against the real monotonic clock (lease_is_fresh compares
    # sample.monotonic_now - lease.heartbeat_monotonic against lease_seconds).
    # The 101 admissions between try_acquire and the first sweep take well over
    # 30 real seconds on Windows, so the lease genuinely expired and the sweep
    # died with "stale coordinator execution fence". The product was right; the
    # test was measuring wall-clock I/O speed rather than page advancement.
    clock = _LeaseClock(LeaseClockSample(now, 100.0, "scheduled-page"))
    store = RunStore(
        tmp_path,
        max_queued_runs=102,
        max_nonterminal_runs=102,
        max_start_requests_per_minute=200,
        lease_clock=clock,
    )
    coordinator = CoordinatorStore(store.database, clock=clock)
    identity = _identity("scheduled-page")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(workflow_writer(tmp_path / "package", name="scheduled-page"))

    def admit(index: int, *, concurrency_key: str) -> str:
        prepared = store.prepare_run_snapshot(package)
        return store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key=f"scheduled-page-{index:03d}",
                concurrency_key=concurrency_key,
                concurrency_policy="queue",
                execution_mode="background",
                run_metadata={"schedule_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")},
            ),
            immutable_snapshot=prepared,
        ).run_id

    blocked = [admit(index, concurrency_key="blocked-lane") for index in range(100)]
    independent = admit(100, concurrency_key="independent-lane")
    for wake in coordinator.pending_wakes(identity, epoch=leadership.lease.epoch, now=now):
        assert coordinator.complete_wake(
            wake.generation, identity, epoch=leadership.lease.epoch, now=now, outcome="test_setup"
        )

    service = WorkflowCoordinatorService(
        BackgroundServiceContext(host_kind="gateway", host_instance_id="scheduled-page"),
        hermes_home=tmp_path,
        utcnow=lambda: now,
        monotonic=lambda: 0.0,
    )
    scheduler = MagicMock()
    scheduler.submit.side_effect = lambda run_id, _fence: run_id == independent

    _first_actionable, cursor, _first_progress = service._sweep_once(
        store, coordinator, identity, leadership.lease.epoch, scheduler
    )
    second_actionable, second_periodic_cursor, _second_progress = service._sweep_once(
        store, coordinator, identity, leadership.lease.epoch, scheduler, cursor=cursor
    )

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert submitted.count(independent) == 1
    assert second_actionable is True
    assert len(submitted[:100]) == 100
    assert len(submitted[100:]) == 100
    assert submitted[100] == independent
    assert cursor is None
    assert second_periodic_cursor is None
    assert set(submitted[:100]) == set(blocked)


def test_scheduled_due_prefix_is_resampled_during_a_sustained_forward_stream(
    tmp_path, workflow_writer
) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(now, 100.0, "scheduled-stream"))
    store = RunStore(
        tmp_path,
        max_queued_runs=400,
        max_nonterminal_runs=400,
        max_start_requests_per_minute=1000,
        lease_clock=clock,
    )
    coordinator = CoordinatorStore(store.database, clock=clock)
    identity = _identity("scheduled-stream")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="scheduled-stream")
    )

    def admit(index: str, *, schedule_at: datetime) -> str:
        prepared = store.prepare_run_snapshot(package)
        result = store.start_run(
            RunAdmissionRequest(
                workflow_name=package.definition.name,
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="api",
                idempotency_key=f"scheduled-stream-{index}",
                concurrency_key=f"scheduled-stream-{index}",
                concurrency_policy="queue",
                execution_mode="background",
                run_metadata={
                    "schedule_at": schedule_at.isoformat().replace("+00:00", "Z")
                },
            ),
            immutable_snapshot=prepared,
        )
        assert result.run_id is not None
        return result.run_id

    def drain_wakes() -> None:
        while wakes := coordinator.pending_wakes(
            identity,
            epoch=leadership.lease.epoch,
            now=clock.sample.utc_now,
        ):
            for wake in wakes:
                assert coordinator.complete_wake(
                    wake.generation,
                    identity,
                    epoch=leadership.lease.epoch,
                    now=clock.sample.utc_now,
                    outcome="test_setup",
                )

    target = admit("target", schedule_at=now + timedelta(seconds=1))
    for index in range(101):
        admit(f"initial-{index:03d}", schedule_at=now - timedelta(seconds=1))
    drain_wakes()

    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="scheduled-stream",
        ),
        hermes_home=tmp_path,
        utcnow=lambda: clock.sample.utc_now,
        monotonic=lambda: clock.sample.monotonic_now,
    )
    scheduler = MagicMock()
    scheduler.submit.side_effect = lambda run_id, _fence: run_id == target

    _actionable, periodic_cursor, _progress = service._sweep_once(
        store,
        coordinator,
        identity,
        leadership.lease.epoch,
        scheduler,
    )
    target_projection = store.load_run(target)
    target_key = (str(target_projection["created_at"]), target)
    assert service._scheduled_sweep_cursor is not None
    assert service._scheduled_sweep_cursor > target_key

    clock.sample = LeaseClockSample(
        now + timedelta(seconds=2),
        102.0,
        "scheduled-stream",
    )
    unfenced_cursor = service._scheduled_sweep_cursor
    for wave in range(2):
        for index in range(100):
            admit(
                f"wave-{wave}-{index:03d}",
                schedule_at=clock.sample.utc_now - timedelta(seconds=1),
            )
        drain_wakes()
        _page, unfenced_cursor, exhausted = store.scheduled_coordinator_candidates(
            after=unfenced_cursor,
            now=clock.sample.utc_now,
            limit=100,
        )
        assert exhausted is False
        _actionable, periodic_cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
            cursor=periodic_cursor,
        )

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert target in submitted


def test_new_leadership_term_restarts_scheduled_paging_at_page_one(
    tmp_path, workflow_writer, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    store = RunStore(tmp_path, max_queued_runs=2, max_start_requests_per_minute=10)
    coordinator = CoordinatorStore(store.database)
    identity = _identity("scheduled-restart")
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    package = load_workflow(workflow_writer(tmp_path / "package", name="scheduled-restart"))
    run_ids = []
    for index in range(2):
        prepared = store.prepare_run_snapshot(package)
        run_ids.append(
            store.start_run(
                RunAdmissionRequest(
                    workflow_name=package.definition.name,
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=f"scheduled-restart-{index}",
                    concurrency_key=f"scheduled-restart-{index}",
                    execution_mode="background",
                    run_metadata={"schedule_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")},
                ),
                immutable_snapshot=prepared,
            ).run_id
        )
    for wake in coordinator.pending_wakes(identity, epoch=leadership.lease.epoch, now=now):
        assert coordinator.complete_wake(
            wake.generation, identity, epoch=leadership.lease.epoch, now=now, outcome="test_setup"
        )
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(host_kind="gateway", host_instance_id="scheduled-restart"),
        hermes_home=tmp_path,
        utcnow=lambda: now,
        monotonic=lambda: 0.0,
    )
    service._scheduled_sweep_cursor = (store.load_run(run_ids[-1])["created_at"], run_ids[-1])
    service._scheduled_sweep_observed_at = now - timedelta(minutes=1)
    service._scheduled_sweep_queue_sequence_fence = 1
    service._repair_revalidation_cursor = 99
    leadership_scheduler = MagicMock()
    leadership_scheduler.shutdown_deadline_seconds = 1.0
    monkeypatch.setattr(service, "_scheduler", lambda *_args, **_kwargs: leadership_scheduler)
    stopped = threading.Event()
    stopped.set()

    assert service._lead(
        stopped,
        run_store=store,
        coordinator_store=coordinator,
        identity=identity,
        epoch=leadership.lease.epoch,
    ) is True
    assert service._scheduled_sweep_cursor is None
    assert service._scheduled_sweep_observed_at is None
    assert service._scheduled_sweep_queue_sequence_fence is None
    assert service._repair_revalidation_cursor is None

    scheduler = MagicMock()
    scheduler.submit.return_value = False
    service._sweep_once(store, coordinator, identity, leadership.lease.epoch, scheduler)
    assert scheduler.submit.call_args_list[0].args[0] == run_ids[0]


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
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = RunStore(tmp_path / "home", lease_clock=clock)
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
    clock.sample = LeaseClockSample(expired_at, 102.0, "boot-a")

    result = RunScheduler(
        store,
        owner_id="expired-owner",
        execution_owner_id="expired-owner",
        execution_owner_epoch=1,
        utcnow=lambda: expired_at,
    ).advance(admitted.run_id)

    assert result["nodes"]["effect"]["state"] == "ready"
    assert not marker.exists()


def test_stale_foreground_scheduler_cannot_claim_after_background_adoption(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    marker = tmp_path / "must-not-run-after-adoption"
    utc = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    clock = _LeaseClock(LeaseClockSample(utc, 100.0, "boot-a"))
    store = RunStore(tmp_path / "home", lease_clock=clock)
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="foreground-adoption-race",
            nodes=[{"id": "effect", "bash": f"touch {marker}"}],
        )
    )
    snapshot = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="foreground-adoption-race",
            concurrency_key=package.definition.name,
            execution_mode="foreground",
            foreground_owner_id="stale-foreground-owner",
            foreground_lease_seconds=1,
        ),
        immutable_snapshot=snapshot,
    )
    expired_at = datetime.fromisoformat(
        store.load_run(admitted.run_id)["foreground_lease_expires_at"]
    ) + timedelta(seconds=1)
    clock.sample = LeaseClockSample(expired_at, 102.0, "boot-a")
    coordinator = CoordinatorStore(store.database, clock=clock)
    identity = _identity("foreground-adopter")
    leadership = coordinator.try_acquire(identity, now=expired_at, lease_seconds=30)
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    scheduler = RunScheduler(
        store,
        owner_id="stale-foreground-owner",
        execution_owner_id="stale-foreground-owner",
        execution_owner_epoch=1,
        utcnow=lambda: expired_at,
    )
    adopted = False

    def adopt_after_stale_owner_checked(_run_id: str) -> bool:
        nonlocal adopted
        if not adopted:
            store.adopt_expired_foreground(admitted.run_id, fence, expired_at)
            adopted = True
        return True

    monkeypatch.setattr(
        scheduler, "_renew_execution_owner", adopt_after_stale_owner_checked
    )

    result = scheduler.advance(admitted.run_id)

    assert adopted is True
    assert result["nodes"]["effect"]["state"] == "ready"
    assert not marker.exists()
