from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
)
from plugins.workflow.admission import RunAdmissionRequest
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
    store = CoordinatorStore(run_store.database)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    identity = _identity("laptop")

    first = store.try_acquire(identity, now=now, lease_seconds=30)
    assert first.is_leader is True
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
    store = CoordinatorStore(run_store.database)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    assert store.health(now=now).status == "unavailable"
    acquired = store.try_acquire(_identity("owner"), now=now, lease_seconds=30)
    assert acquired.is_leader is True
    assert store.health(now=now + timedelta(seconds=1)).status == "healthy"
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
        ),
        immutable_snapshot=prepared,
    )
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="continuation-owner",
    )
    stop = threading.Event()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    try:
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
