from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
)
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
