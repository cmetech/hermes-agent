"""Plugin-owned durable workflow coordinator service."""

from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Callable

from hermes_cli.plugin_services import (
    BackgroundServiceContext,
    BackgroundServiceHealth,
)
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorLease,
    CoordinatorStore,
)
from plugins.workflow.lease_clock import (
    LeaseClockSample,
    current_boot_id,
    lease_is_fresh,
)
from plugins.workflow.models import ExecutionFence
from tools.managed_process import ProcessIdentity


logger = logging.getLogger(__name__)


class WorkflowCoordinatorService:
    """Elect and heartbeat one workflow coordinator without model authority."""

    def __init__(
        self,
        context: BackgroundServiceContext,
        *,
        hermes_home: str | Path | None = None,
        heartbeat_seconds: float = 5.0,
        lease_seconds: float = 30.0,
        web_election_grace_seconds: float = 3.0,
        sweep_backoff_seconds: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0, 60.0),
        utcnow: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("heartbeat_seconds", heartbeat_seconds),
            ("lease_seconds", lease_seconds),
            ("web_election_grace_seconds", web_election_grace_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        if not sweep_backoff_seconds or any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or value <= 0
            for value in sweep_backoff_seconds
        ):
            raise ValueError("sweep_backoff_seconds must contain positive values")
        self.context = context
        self._use_profile_config = hermes_home is None
        self._hermes_home = Path(hermes_home).resolve() if hermes_home else None
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.lease_seconds = float(lease_seconds)
        self.web_election_grace_seconds = float(web_election_grace_seconds)
        self.sweep_backoff_seconds = tuple(float(value) for value in sweep_backoff_seconds)
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._boot_id = current_boot_id()
        self._health_lock = threading.Lock()
        self._health = BackgroundServiceHealth(
            state="starting",
            code="registered",
        )

    def health(self) -> BackgroundServiceHealth:
        """Return the cached O(1) service state without touching SQLite."""
        with self._health_lock:
            return self._health

    def _set_health(
        self,
        *,
        state: str,
        code: str,
        message: str = "",
        heartbeat_at: datetime | None = None,
    ) -> None:
        snapshot = BackgroundServiceHealth(
            state=state,
            code=code,
            message=message,
            heartbeat_at=heartbeat_at,
        )
        with self._health_lock:
            self._health = snapshot

    def _home(self) -> Path:
        if self._hermes_home is not None:
            return self._hermes_home
        from hermes_constants import get_hermes_home

        return get_hermes_home().resolve()

    def _lease_clock(self) -> LeaseClockSample:
        return LeaseClockSample(
            self._utcnow().astimezone(timezone.utc),
            self._monotonic(),
            self._boot_id,
        )

    def _identity(self) -> CoordinatorIdentity:
        process = ProcessIdentity.capture(os.getpid())
        return CoordinatorIdentity(
            owner_id=(
                f"{self.context.host_kind}-{self.context.host_instance_id}-"
                f"{uuid.uuid4().hex}"
            ),
            host_kind=self.context.host_kind,
            host_instance_id=self.context.host_instance_id,
            pid=process.pid,
            process_start_time=process.start_time,
        )

    def _apply_profile_config(self) -> None:
        if not self._use_profile_config:
            return
        from plugins.workflow.cli import _runtime_config

        runtime = _runtime_config(self._home())
        self.heartbeat_seconds = float(runtime.heartbeat_seconds)
        self.lease_seconds = float(runtime.lease_seconds)
        self.web_election_grace_seconds = float(
            runtime.coordinator_web_election_grace_seconds
        )

    def _web_may_contend(
        self,
        lease: CoordinatorLease | None,
        *,
        now: datetime,
        eligible_at: float | None,
    ) -> tuple[bool, float | None]:
        if self.context.host_kind != "web":
            return True, None
        if lease is not None and lease_is_fresh(lease, self._lease_clock()):
            return False, None
        if eligible_at is None:
            eligible_at = self._monotonic() + self.web_election_grace_seconds
        return self._monotonic() >= eligible_at, eligible_at

    @staticmethod
    def _scheduler(run_store, *, fence: ExecutionFence):
        from agent.plugin_agent import PluginAgentRunner
        from hermes_cli.profiles import get_active_profile_name
        from plugins.workflow.cli import _runtime_config, _scheduler

        runtime = _runtime_config(run_store.hermes_home)
        scheduler = _scheduler(
            run_store,
            runtime,
            agent_runner=PluginAgentRunner(plugin_id="workflow"),
            profile_name=get_active_profile_name(),
            owner_id=f"coordinator:{fence.owner_id}:{fence.owner_epoch}",
        )
        scheduler.execution_fence = fence
        return scheduler

    def _sweep_once(
        self,
        run_store,
        coordinator_store: CoordinatorStore,
        identity: CoordinatorIdentity,
        epoch: int,
        scheduler,
    ) -> tuple[bool, str | None, datetime | None]:
        from plugins.workflow.notifications import NotificationOutbox
        from plugins.workflow.store import ForegroundExecutionConflict

        fence = ExecutionFence(identity.owner_id, epoch)
        with run_store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run_store.assert_execution_fence(connection, fence)
            connection.commit()
        NotificationOutbox(run_store).reconcile_journal(limit_runs=200)
        now = self._utcnow().astimezone(timezone.utc)
        wakes = coordinator_store.pending_wakes(
            identity,
            epoch=epoch,
            now=now,
            limit=100,
        )
        run_ids = list(dict.fromkeys(wake.run_id for wake in wakes))
        periodic = run_store.list_runs(limit=200)
        run_ids.extend(
            str(run["run_id"])
            for run in periodic
            if run.get("status") in {"queued", "running", "waiting_retry"}
            and run.get("execution_mode") in {"background", "foreground"}
            and run.get("run_id") not in run_ids
        )
        progress_at: datetime | None = None
        for run_id in run_ids:
            outcome = "advanced"
            try:
                before = run_store.get_run_status(run_id)
                after = before
                if before.get("execution_mode") == "foreground":
                    try:
                        run_store.adopt_expired_foreground(run_id, fence, now)
                    except ForegroundExecutionConflict:
                        after = before
                        outcome = "foreground_owned"
                    else:
                        before = run_store.get_run_status(run_id)
                        after = before
                        outcome = "foreground_adopted"
                if before.get("execution_mode") == "background" and before.get(
                    "status"
                ) in {"queued", "running", "waiting_retry"}:
                    scheduler.advance(run_id)
                    after = run_store.get_run_status(run_id)
                    if after.get("state_version") != before.get("state_version"):
                        progress_at = self._utcnow().astimezone(timezone.utc)
                    else:
                        outcome = "no_change"
            except KeyError:
                outcome = "run_missing"
            except Exception:
                logger.exception("Workflow coordinator failed to advance run %s", run_id)
                outcome = "advance_failed"
            for wake in wakes:
                if wake.run_id == run_id:
                    coordinator_store.complete_wake(
                        wake.generation,
                        identity,
                        epoch=epoch,
                        now=self._utcnow().astimezone(timezone.utc),
                        outcome=outcome,
                    )
        return bool(run_ids), (run_ids[-1] if run_ids else None), progress_at

    def _lead(
        self,
        stop_event: threading.Event,
        *,
        run_store,
        coordinator_store: CoordinatorStore,
        identity: CoordinatorIdentity,
        epoch: int,
    ) -> bool:
        scheduler = self._scheduler(
            run_store,
            fence=ExecutionFence(identity.owner_id, epoch),
        )
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-sweep")
        future: Future | None = None
        backoff_index = 0
        next_sweep = self._monotonic()
        heartbeat_due = self._monotonic() + self.heartbeat_seconds
        cursor: str | None = None
        progress_at: datetime | None = None
        leadership_current = True
        try:
            while leadership_current and not stop_event.is_set():
                if future is not None and future.done():
                    found, cursor, progress = future.result()
                    future = None
                    if progress is not None:
                        progress_at = progress
                    if found:
                        backoff_index = 0
                    else:
                        backoff_index = min(
                            backoff_index + 1,
                            len(self.sweep_backoff_seconds) - 1,
                        )
                    next_sweep = (
                        self._monotonic()
                        + self.sweep_backoff_seconds[backoff_index]
                    )

                now_monotonic = self._monotonic()
                if now_monotonic >= heartbeat_due:
                    now = self._utcnow().astimezone(timezone.utc)
                    leadership_current = coordinator_store.renew(
                        identity,
                        epoch=epoch,
                        now=now,
                        lease_seconds=self.lease_seconds,
                        sweep_cursor=cursor,
                        last_progress_at=progress_at,
                    )
                    if not leadership_current:
                        self._set_health(
                            state="degraded",
                            code="leadership_lost",
                            message="coordinator epoch is no longer current",
                        )
                        break
                    self._set_health(
                        state="healthy",
                        code="leader",
                        heartbeat_at=now,
                    )
                    heartbeat_due = now_monotonic + self.heartbeat_seconds

                if future is None and self._monotonic() >= next_sweep:
                    future = pool.submit(
                        self._sweep_once,
                        run_store,
                        coordinator_store,
                        identity,
                        epoch,
                        scheduler,
                    )

                wait_for = min(
                    max(0.001, heartbeat_due - self._monotonic()),
                    max(0.001, next_sweep - self._monotonic())
                    if future is None
                    else 0.05,
                )
                woke = coordinator_store.wait_for_local_wake(
                    stop_event,
                    timeout=wait_for,
                )
                if woke and future is None:
                    next_sweep = min(next_sweep, self._monotonic())
        finally:
            scheduler.shutdown(
                deadline_seconds=min(8.0, scheduler.shutdown_deadline_seconds)
            )
            if future is not None:
                try:
                    future.result(timeout=8.0)
                except Exception:
                    logger.exception("Workflow coordinator sweep did not stop cleanly")
            # Do not let run() report quiescence while its sweep still owns a
            # scheduler or node workers. If an executor ignores bounded
            # cancellation, the generic host must observe stop_timeout rather
            # than overlap a replacement generation.
            pool.shutdown(wait=True, cancel_futures=True)
        return leadership_current

    def run(self, stop_event: threading.Event) -> None:
        """Maintain election and heartbeats until the host requests stop."""
        self._apply_profile_config()
        identity = self._identity()
        coordinator_store: CoordinatorStore | None = None
        leader_epoch: int | None = None
        web_eligible_at: float | None = None
        try:
            while not stop_event.is_set():
                try:
                    if coordinator_store is None:
                        from plugins.workflow.store import RunStore

                        run_store = RunStore(
                            self._home(), lease_clock=self._lease_clock
                        )
                        coordinator_store = CoordinatorStore(
                            run_store.database, clock=self._lease_clock
                        )
                    now = self._utcnow().astimezone(timezone.utc)
                    if leader_epoch is not None:
                        leadership_current = self._lead(
                            stop_event,
                            run_store=run_store,
                            coordinator_store=coordinator_store,
                            identity=identity,
                            epoch=leader_epoch,
                        )
                        if not leadership_current:
                            leader_epoch = None
                        continue

                    lease = coordinator_store.observe(now=now)
                    may_contend, web_eligible_at = self._web_may_contend(
                        lease,
                        now=now,
                        eligible_at=web_eligible_at,
                    )
                    if not may_contend:
                        if lease is not None and lease_is_fresh(
                            lease, self._lease_clock()
                        ):
                            self._set_health(
                                state="healthy",
                                code="standby",
                                heartbeat_at=lease.heartbeat_at,
                            )
                        else:
                            self._set_health(
                                state="starting",
                                code="election_grace",
                            )
                        stop_event.wait(
                            min(
                                self.heartbeat_seconds,
                                max(
                                    0.001,
                                    (web_eligible_at or self._monotonic())
                                    - self._monotonic(),
                                ),
                            )
                        )
                        continue

                    acquisition = coordinator_store.try_acquire(
                        identity,
                        now=now,
                        lease_seconds=self.lease_seconds,
                    )
                    if acquisition.is_leader:
                        leader_epoch = acquisition.lease.epoch
                        web_eligible_at = None
                        self._set_health(
                            state="healthy",
                            code="leader",
                            heartbeat_at=acquisition.lease.heartbeat_at,
                        )
                    else:
                        web_eligible_at = None
                        self._set_health(
                            state="healthy",
                            code="standby",
                            heartbeat_at=acquisition.lease.heartbeat_at,
                        )
                    stop_event.wait(self.heartbeat_seconds)
                except Exception:
                    logger.exception("Workflow coordinator election tick failed")
                    leader_epoch = None
                    coordinator_store = None
                    self._set_health(
                        state="unhealthy",
                        code="coordinator_store_error",
                        message="workflow coordinator persistence is unavailable",
                    )
                    stop_event.wait(max(1.0, self.heartbeat_seconds))
        finally:
            if coordinator_store is not None and leader_epoch is not None:
                try:
                    coordinator_store.release(
                        identity,
                        epoch=leader_epoch,
                        now=self._utcnow().astimezone(timezone.utc),
                    )
                except Exception:
                    logger.exception("Workflow coordinator lease release failed")


def create_workflow_coordinator(
    context: BackgroundServiceContext,
) -> WorkflowCoordinatorService:
    """Dormant factory used by generic Web and Gateway service hosts."""
    return WorkflowCoordinatorService(context)


__all__ = ["WorkflowCoordinatorService", "create_workflow_coordinator"]
