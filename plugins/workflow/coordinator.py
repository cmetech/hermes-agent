"""Plugin-owned durable workflow coordinator service."""

from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import Future, ThreadPoolExecutor
import logging
import json
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
from plugins.workflow.schedule_time import run_is_scheduled_wait
from tools.managed_process import ProcessIdentity


logger = logging.getLogger(__name__)
_MAX_SWEEP_DELAY_SECONDS = 5.0


def _select_sweep_run_ids(
    ordered_run_ids: list[str],
    periodic_run_ids: list[str],
    scheduled_continuation_run_ids: list[str] | None = None,
) -> list[str]:
    if len(ordered_run_ids) <= 100:
        return ordered_run_ids
    reserved: list[str] = []
    for run_ids in (periodic_run_ids, scheduled_continuation_run_ids or []):
        if run_ids and run_ids[0] not in reserved:
            reserved.append(run_ids[0])
    return reserved + [
        run_id for run_id in ordered_run_ids if run_id not in reserved
    ][: 100 - len(reserved)]


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
        runnable_stall_seconds: float = 60.0,
        semantic_stall_seconds: float = 300.0,
        notification_repair_seconds: float = 300.0,
        sweep_backoff_seconds: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0, 60.0),
        utcnow: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        runner_binding=None,
    ) -> None:
        for name, value in (
            ("heartbeat_seconds", heartbeat_seconds),
            ("lease_seconds", lease_seconds),
            ("web_election_grace_seconds", web_election_grace_seconds),
            ("runnable_stall_seconds", runnable_stall_seconds),
            ("semantic_stall_seconds", semantic_stall_seconds),
            ("notification_repair_seconds", notification_repair_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if lease_seconds < 3 * heartbeat_seconds:
            raise ValueError("lease_seconds must be at least three heartbeats")
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
        self.runnable_stall_seconds = float(runnable_stall_seconds)
        self.semantic_stall_seconds = float(semantic_stall_seconds)
        self.notification_repair_seconds = float(notification_repair_seconds)
        self.sweep_backoff_seconds = tuple(float(value) for value in sweep_backoff_seconds)
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._runner_binding = runner_binding
        self._boot_id = current_boot_id()
        self._notification_repair_due_at = 0.0
        self._scheduled_sweep_cursor: tuple[str, str] | None = None
        self._scheduled_sweep_observed_at: datetime | None = None
        self._scheduled_sweep_queue_sequence_fence: int | None = None
        self._repair_revalidation_cursor: int | None = None
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
        self.runnable_stall_seconds = float(runtime.runnable_stall_seconds)
        self.semantic_stall_seconds = float(runtime.semantic_stall_seconds)

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

    def _scheduler(self, run_store, *, fence: ExecutionFence):
        from hermes_cli.profiles import get_active_profile_name
        from plugins.workflow.cli import _runtime_config, _scheduler
        from plugins.workflow import runner_binding as runner_binding_module

        runtime = _runtime_config(run_store.hermes_home)
        binding = (
            self._runner_binding
            if self._runner_binding is not None
            else runner_binding_module.production_workflow_runner_binding()
        )
        scheduler = _scheduler(
            run_store,
            runtime,
            runner_binding=binding,
            profile_name=get_active_profile_name(),
            owner_id=f"coordinator:{fence.owner_id}:{fence.owner_epoch}",
            utcnow=self._utcnow,
            monotonic=self._monotonic,
        )
        scheduler.execution_fence = fence
        return scheduler

    def _repair_notifications_if_due(
        self,
        run_store,
        *,
        now_monotonic: float,
    ) -> None:
        if now_monotonic < self._notification_repair_due_at:
            return
        self._notification_repair_due_at = (
            now_monotonic + self.notification_repair_seconds
        )
        from plugins.workflow.notifications import NotificationOutbox

        NotificationOutbox(run_store).reconcile_journal(
            limit_runs=20,
            max_journal_bytes=int(run_store.max_journal_bytes),
        )

    @staticmethod
    def _deliver_gateway_notifications(outbox, delivery_port, *, owner_id: str) -> int:
        """Project leased outbox rows through the host port exactly once."""
        delivered = 0
        for notification in outbox.lease_gateway(owner_id=owner_id, limit=20):
            notification_id = str(notification["notification_id"])
            capability = str(notification["delivery_capability"])
            idempotency_key = (
                f"workflow-notification:{notification_id}:"
                f"{notification['transition_version']}"
            )
            text = json.dumps(
                notification["payload"], sort_keys=True, separators=(",", ":")
            )
            try:
                receipt = delivery_port.deliver(capability, text, idempotency_key)
            except Exception as exc:
                logger.exception("Gateway notification delivery failed before receipt")
                outbox.terminal_fail(
                    notification_id,
                    owner_id=owner_id,
                    error=f"delivery_exception:{type(exc).__name__}",
                    outcome_uncertain=True,
                )
                continue
            if receipt.status == "delivered":
                if outbox.ack(notification_id, owner_id=owner_id):
                    delivered += 1
            elif receipt.status == "retryable_failure":
                outbox.fail(
                    notification_id,
                    owner_id=owner_id,
                    error=receipt.detail or receipt.status,
                )
            else:
                outbox.terminal_fail(
                    notification_id,
                    owner_id=owner_id,
                    error=receipt.detail or receipt.status,
                    outcome_uncertain=receipt.status == "outcome_uncertain",
                )
        return delivered

    def _drain_gateway_notifications(self, run_store, identity) -> int:
        """Drain Gateway projections without acquiring workflow leadership."""
        if self.context.delivery_port is None:
            return 0
        from plugins.workflow.notifications import NotificationOutbox

        return self._deliver_gateway_notifications(
            NotificationOutbox(run_store),
            self.context.delivery_port,
            owner_id=f"delivery:{identity.owner_id}",
        )

    def _sweep_once(
        self,
        run_store,
        coordinator_store: CoordinatorStore,
        identity: CoordinatorIdentity,
        epoch: int,
        scheduler,
        cursor: tuple[str, str] | None = None,
    ) -> tuple[bool, tuple[str, str] | None, datetime | None]:
        from plugins.workflow.store import ForegroundExecutionConflict

        fence = ExecutionFence(identity.owner_id, epoch)
        with run_store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run_store.assert_execution_fence(connection, fence)
            connection.commit()
        sweep_started = self._monotonic()
        deadline = sweep_started + 2.0
        self._repair_notifications_if_due(
            run_store,
            now_monotonic=sweep_started,
        )
        if self.context.delivery_port is not None:
            from plugins.workflow.notifications import NotificationOutbox

            self._deliver_gateway_notifications(
                NotificationOutbox(run_store),
                self.context.delivery_port,
                owner_id=f"coordinator:{identity.owner_id}:{epoch}",
            )
        now = self._utcnow().astimezone(timezone.utc)
        wakes = coordinator_store.pending_wakes(
            identity,
            epoch=epoch,
            now=now,
            limit=100,
        )
        fresh_scheduled, _fresh_scheduled_cursor, _fresh_scheduled_exhausted = (
            run_store.scheduled_coordinator_candidates(
                after=None,
                now=now,
                limit=100,
            )
        )
        if self._scheduled_sweep_observed_at is None:
            self._scheduled_sweep_cursor = None
            (
                self._scheduled_sweep_observed_at,
                self._scheduled_sweep_queue_sequence_fence,
            ) = run_store.scheduled_coordinator_generation(
                now=now,
            )
        scheduled_observed_at = self._scheduled_sweep_observed_at
        assert scheduled_observed_at is not None
        scheduled_fence = self._scheduled_sweep_queue_sequence_fence
        assert scheduled_fence is not None
        scheduled_continuation, _scheduled_cursor, scheduled_exhausted = (
            run_store.scheduled_coordinator_candidates(
                after=self._scheduled_sweep_cursor,
                now=scheduled_observed_at,
                through_queue_sequence=scheduled_fence,
                limit=100,
            )
        )
        scheduled_by_run = {
            str(row["run_id"]): row
            for row in (*fresh_scheduled, *scheduled_continuation)
        }
        periodic, _page_cursor, page_exhausted = run_store.coordinator_candidates(
            after=cursor,
            now=now,
            limit=100,
        )
        wake_by_run: dict[str, list[object]] = {}
        for wake in wakes:
            wake_by_run.setdefault(wake.run_id, []).append(wake)
        periodic_by_run = {str(row["run_id"]): row for row in periodic}
        indexed_by_run = {**scheduled_by_run, **periodic_by_run}
        uncorroborated_wake_ids: list[str] = []
        for run_id in wake_by_run:
            if run_id in indexed_by_run:
                continue
            try:
                projection = run_store.load_run(run_id)
                run_store._scheduled_at_from_projection(projection)
                created_at = str(projection["created_at"])
            except Exception:
                uncorroborated_wake_ids.append(run_id)
            else:
                if run_is_scheduled_wait(projection, observed=now):
                    for wake in wake_by_run[run_id]:
                        coordinator_store.complete_wake(
                            wake.generation,
                            identity,
                            epoch=epoch,
                            now=self._utcnow().astimezone(timezone.utc),
                            outcome="scheduled_not_due",
                        )
                    continue
                indexed_by_run[run_id] = {
                    "run_id": run_id,
                    "created_at": created_at,
                }
        indexed_run_ids = [
            str(row["run_id"])
            for row in sorted(
                indexed_by_run.values(),
                key=lambda row: (str(row["created_at"]), str(row["run_id"])),
            )
        ]
        ordered_run_ids = _select_sweep_run_ids(
            uncorroborated_wake_ids + indexed_run_ids,
            [str(row["run_id"]) for row in periodic],
            [str(row["run_id"]) for row in scheduled_continuation],
        )
        actionable_work = False
        progress_at: datetime | None = None
        processed_runs: set[str] = set()
        for run_id in ordered_run_ids:
            if self._monotonic() >= deadline:
                break
            outcome = "not_actionable"
            run_actionable = False
            try:
                before = run_store.load_run(run_id)
                run_store._scheduled_at_from_projection(before)
                scheduled_not_due = run_is_scheduled_wait(before, observed=now)
                if scheduled_not_due:
                    outcome = "scheduled_not_due"
                elif before.get("execution_mode") == "foreground":
                    try:
                        run_store.adopt_expired_foreground(run_id, fence, now)
                    except ForegroundExecutionConflict:
                        outcome = "foreground_owned"
                    else:
                        before = run_store.load_run(run_id)
                        outcome = "foreground_adopted"
                        run_actionable = True
                if (
                    not scheduled_not_due
                    and before.get("execution_mode") == "background"
                    and before.get("status") in {"queued", "running", "waiting_retry"}
                ):
                    stalled = run_store.record_stall_if_due(
                        run_id,
                        fence=fence,
                        now=self._lease_clock(),
                        runnable_stall_seconds=self.runnable_stall_seconds,
                        semantic_stall_seconds=self.semantic_stall_seconds,
                    )
                    submitted = scheduler.submit(run_id, fence)
                    run_actionable = run_actionable or stalled or submitted
                    if submitted:
                        outcome = "submitted"
                    elif stalled:
                        outcome = "stalled"
                    elif outcome != "foreground_adopted":
                        outcome = "already_submitted"
            except KeyError:
                outcome = "run_missing"
            except RuntimeError as exc:
                if "execution fence" in str(exc):
                    raise
                logger.exception("Workflow coordinator failed run %s", run_id)
                outcome = "advance_failed"
            except Exception:
                logger.exception("Workflow coordinator failed run %s", run_id)
                outcome = "advance_failed"
            processed_runs.add(run_id)
            if run_actionable:
                actionable_work = True
                progress_at = self._utcnow().astimezone(timezone.utc)
            for wake in wake_by_run.get(run_id, ()):
                coordinator_store.complete_wake(
                    wake.generation,
                    identity,
                    epoch=epoch,
                    now=self._utcnow().astimezone(timezone.utc),
                    outcome=outcome,
                )
        (
            repair_candidate,
            repair_cursor,
            repair_page_exhausted,
        ) = run_store.repair_revalidation_candidate(
            after=self._repair_revalidation_cursor,
        )
        if repair_candidate is not None:
            if run_store.revalidate_run_repair(
                str(repair_candidate["run_id"]),
                str(repair_candidate["reason_code"]),
            ):
                actionable_work = True
                progress_at = self._utcnow().astimezone(timezone.utc)
            self._repair_revalidation_cursor = repair_cursor
        elif repair_page_exhausted:
            self._repair_revalidation_cursor = None
        else:
            self._repair_revalidation_cursor = repair_cursor
        processed_page = True
        processed_periodic_cursor = cursor
        for row in periodic:
            run_id = str(row["run_id"])
            if run_id not in processed_runs:
                processed_page = False
                break
            processed_periodic_cursor = (str(row["created_at"]), run_id)
        scheduled_processed = True
        scheduled_cursor = self._scheduled_sweep_cursor
        for row in scheduled_continuation:
            run_id = str(row["run_id"])
            if run_id not in processed_runs:
                scheduled_processed = False
                break
            scheduled_cursor = (str(row["created_at"]), run_id)
        if scheduled_exhausted and scheduled_processed:
            self._scheduled_sweep_cursor = None
            self._scheduled_sweep_observed_at = None
            self._scheduled_sweep_queue_sequence_fence = None
        else:
            self._scheduled_sweep_cursor = scheduled_cursor
        next_cursor = (
            None
            if page_exhausted and processed_page
            else processed_periodic_cursor
        )
        return actionable_work, next_cursor, progress_at

    def _lead(
        self,
        stop_event: threading.Event,
        *,
        run_store,
        coordinator_store: CoordinatorStore,
        identity: CoordinatorIdentity,
        epoch: int,
    ) -> bool:
        self._scheduled_sweep_cursor = None
        self._scheduled_sweep_observed_at = None
        self._scheduled_sweep_queue_sequence_fence = None
        self._repair_revalidation_cursor = None
        scheduler = self._scheduler(
            run_store,
            fence=ExecutionFence(identity.owner_id, epoch),
        )
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-sweep")
        future: Future | None = None
        backoff_index = 0
        next_sweep = self._monotonic()
        heartbeat_due = self._monotonic() + self.heartbeat_seconds
        observed = coordinator_store.observe(
            now=self._utcnow().astimezone(timezone.utc)
        )
        observed_is_current = (
            observed is not None
            and observed.matches(identity)
            and observed.epoch == epoch
        )
        cursor = observed.sweep_cursor if observed_is_current else None
        progress_at = observed.last_progress_at if observed_is_current else None
        last_sweep_at = observed.last_sweep_at if observed_is_current else None
        leadership_current = True
        try:
            while leadership_current and not stop_event.is_set():
                if future is not None and future.done():
                    found, cursor, progress = future.result()
                    future = None
                    if progress is not None:
                        progress_at = progress
                    last_sweep_at = self._utcnow().astimezone(timezone.utc)
                    if found:
                        backoff_index = 0
                    else:
                        backoff_index = min(
                            backoff_index + 1,
                            len(self.sweep_backoff_seconds) - 1,
                        )
                    next_sweep = self._monotonic() + min(
                        self.sweep_backoff_seconds[backoff_index],
                        _MAX_SWEEP_DELAY_SECONDS,
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
                        last_sweep_at=last_sweep_at,
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
                        cursor,
                    )

                wait_for = min(
                    _MAX_SWEEP_DELAY_SECONDS,
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
        delivery_pool = (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="workflow-standby-delivery",
            )
            if self.context.delivery_port is not None
            else None
        )
        delivery_future: Future[int] | None = None
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

                    if delivery_pool is not None and (
                        delivery_future is None or delivery_future.done()
                    ):
                        if delivery_future is not None:
                            try:
                                delivery_future.result()
                            except Exception:
                                logger.exception(
                                    "Workflow standby notification drain failed"
                                )
                        delivery_future = delivery_pool.submit(
                            self._drain_gateway_notifications,
                            run_store,
                            identity,
                        )

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
            if delivery_pool is not None:
                delivery_pool.shutdown(wait=True, cancel_futures=True)


def create_workflow_coordinator(
    context: BackgroundServiceContext,
) -> WorkflowCoordinatorService:
    """Dormant factory used by generic Web and Gateway service hosts."""
    return WorkflowCoordinatorService(context)


__all__ = ["WorkflowCoordinatorService", "create_workflow_coordinator"]
