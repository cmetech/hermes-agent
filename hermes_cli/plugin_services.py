"""Generic, host-owned lifecycle for plugin background services.

The host owns the supervisor thread and cooperative stop event. Plugins own
service semantics behind a blocking ``run(stop_event)`` call and expose only a
cached, constant-time health snapshot. Nothing in this module receives agent,
prompt, conversation, provider, tool, web, or gateway authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import logging
import math
import re
import threading
import time
from typing import Callable, Collection, Literal, Protocol, runtime_checkable

from hermes_cli.plugin_invocation import PluginDeliveryPort


logger = logging.getLogger(__name__)

BackgroundServiceHostKind = Literal["web", "gateway"]
BackgroundServiceHealthState = Literal[
    "starting", "healthy", "degraded", "unhealthy"
]
BackgroundServiceLifecycle = Literal[
    "registered",
    "constructing",
    "running",
    "stopping",
    "stopped",
    "failed",
    "stop_timeout",
    "skipped_safe_mode",
]

VALID_BACKGROUND_SERVICE_HOSTS = frozenset({"web", "gateway"})
VALID_BACKGROUND_SERVICE_HEALTH_STATES = frozenset(
    {"starting", "healthy", "degraded", "unhealthy"}
)
BACKGROUND_SERVICE_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
HEALTH_PROBE_INTERVAL_SECONDS = 5.0
HEALTH_PROBE_TIMEOUT_SECONDS = 0.1
_MAX_SNAPSHOT_TEXT = 512


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_text(value: object, *, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    return " ".join(printable.split())[:_MAX_SNAPSHOT_TEXT]


def _positive_timeout(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return timeout


@dataclass(frozen=True, slots=True)
class BackgroundServiceContext:
    """Immutable, non-authoritative process-local host identity."""

    host_kind: BackgroundServiceHostKind
    host_instance_id: str
    delivery_port: PluginDeliveryPort | None = None


@dataclass(frozen=True, slots=True)
class BackgroundServiceHealth:
    """Sanitized health facts cached by the generic host."""

    state: BackgroundServiceHealthState
    code: str
    message: str = ""
    heartbeat_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.state not in VALID_BACKGROUND_SERVICE_HEALTH_STATES:
            raise ValueError(f"invalid background service health state: {self.state}")
        if not isinstance(self.code, str) or not BACKGROUND_SERVICE_NAME_PATTERN.fullmatch(
            self.code
        ):
            raise ValueError("background service health code must be a stable token")
        if self.heartbeat_at is not None and (
            not isinstance(self.heartbeat_at, datetime)
            or self.heartbeat_at.tzinfo is None
        ):
            raise ValueError("background service heartbeat must be timezone-aware")


@runtime_checkable
class BackgroundService(Protocol):
    """Smallest enforceable plugin service contract."""

    def run(self, stop_event: threading.Event) -> None:
        """Block until stop is requested and owned work is quiescent."""

    def health(self) -> BackgroundServiceHealth:
        """Return an O(1), thread-safe snapshot without external I/O."""


BackgroundServiceFactory = Callable[[BackgroundServiceContext], BackgroundService]


@dataclass(frozen=True, slots=True)
class BackgroundServiceRegistration:
    qualified_name: str
    plugin: str
    name: str
    factory: BackgroundServiceFactory
    hosts: frozenset[BackgroundServiceHostKind]


@dataclass(frozen=True, slots=True)
class HostedServiceSnapshot:
    qualified_name: str
    plugin: str
    host_kind: BackgroundServiceHostKind
    host_instance_id: str
    generation: int
    lifecycle: BackgroundServiceLifecycle
    thread_alive: bool
    health_probe_alive: bool
    registered_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None
    health: BackgroundServiceHealth
    failure_code: str | None = None
    failure_message: str | None = None


class BackgroundServiceReloadBlocked(RuntimeError):
    """Raised when an old hosted generation cannot prove quiescence."""


@dataclass(slots=True)
class _HostedServiceState:
    registration: BackgroundServiceRegistration
    registered_at: datetime = field(default_factory=_utc_now)
    lifecycle: BackgroundServiceLifecycle = "registered"
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    health: BackgroundServiceHealth = field(
        default_factory=lambda: BackgroundServiceHealth(
            state="starting", code="registered"
        )
    )
    failure_code: str | None = None
    failure_message: str | None = None
    service: BackgroundService | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    run_thread: threading.Thread | None = None
    health_thread: threading.Thread | None = None
    health_probe_started: float | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def threads(self) -> tuple[threading.Thread, ...]:
        return tuple(
            thread
            for thread in (self.run_thread, self.health_thread)
            if thread is not None
        )


class BackgroundServiceHost:
    """One process-local generation of applicable plugin services."""

    def __init__(
        self,
        registrations: Collection[BackgroundServiceRegistration],
        *,
        host_kind: BackgroundServiceHostKind,
        host_instance_id: str,
        generation: int,
        shutdown_timeout: float,
        safe_mode: bool,
        delivery_port: PluginDeliveryPort | None = None,
        on_quiescent: Callable[["BackgroundServiceHost"], None] | None = None,
        factory_start_event: threading.Event | None = None,
    ) -> None:
        if host_kind not in VALID_BACKGROUND_SERVICE_HOSTS:
            raise ValueError(f"unknown background service host: {host_kind}")
        self.host_kind = host_kind
        self.host_instance_id = host_instance_id
        self.generation = generation
        self.shutdown_timeout = _positive_timeout(
            shutdown_timeout, name="shutdown_timeout"
        )
        self._safe_mode = safe_mode
        self.delivery_port = delivery_port
        self._on_quiescent = on_quiescent
        self._factory_start_event = factory_start_event
        self._states = tuple(
            _HostedServiceState(registration=registration)
            for registration in sorted(
                registrations, key=lambda item: item.qualified_name
            )
            if host_kind in registration.hosts
        )
        self._start_lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        """Start every applicable supervisor without invoking factories inline."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
            for state in self._states:
                if self._safe_mode:
                    with state.lock:
                        state.lifecycle = "skipped_safe_mode"
                        state.health = BackgroundServiceHealth(
                            state="unhealthy", code="safe_mode"
                        )
                    continue
                with state.lock:
                    state.lifecycle = "constructing"
                    state.health = BackgroundServiceHealth(
                        state="starting", code="constructing"
                    )
                    state.run_thread = threading.Thread(
                        target=self._run_service,
                        args=(state,),
                        name=(
                            "hermes-plugin-service-"
                            f"{self.generation}-{state.registration.qualified_name}"
                        ),
                        daemon=True,
                    )
                    state.run_thread.start()

    def _run_service(self, state: _HostedServiceState) -> None:
        registration = state.registration
        publication = self._factory_start_event
        if publication is not None:
            while not publication.wait(0.05):
                if state.stop_event.is_set():
                    return
            if state.stop_event.is_set():
                return
        context = BackgroundServiceContext(
            host_kind=self.host_kind,
            host_instance_id=self.host_instance_id,
            delivery_port=self.delivery_port,
        )
        try:
            service = registration.factory(context)
            if not isinstance(service, BackgroundService):
                raise TypeError(
                    "factory must return an object with run(stop_event) and health()"
                )
        except BaseException:
            logger.exception(
                "Plugin background service factory failed: %s",
                registration.qualified_name,
            )
            self._record_failure(
                state,
                code="factory_failed",
                message="background service factory failed",
            )
            return

        with state.lock:
            state.service = service
            if state.stop_event.is_set():
                if state.lifecycle != "stop_timeout":
                    state.lifecycle = "stopping"
                return
            state.lifecycle = "running"
            state.started_at = _utc_now()
            state.health = BackgroundServiceHealth(state="starting", code="probing")
            state.health_thread = threading.Thread(
                target=self._probe_health,
                args=(state,),
                name=(
                    "hermes-plugin-health-"
                    f"{self.generation}-{registration.qualified_name}"
                ),
                daemon=True,
            )
            state.health_thread.start()

        try:
            service.run(state.stop_event)
        except BaseException:
            logger.exception(
                "Plugin background service run failed: %s",
                registration.qualified_name,
            )
            self._record_failure(
                state,
                code="run_failed",
                message="background service run failed",
            )
            state.stop_event.set()
            return

        with state.lock:
            if state.stop_event.is_set() or state.lifecycle in {
                "stopping",
                "stop_timeout",
            }:
                if state.lifecycle != "stop_timeout":
                    state.lifecycle = "stopping"
            else:
                state.lifecycle = "failed"
                state.failure_code = "run_returned"
                state.failure_message = "background service returned before stop"
                state.stopped_at = _utc_now()
                state.stop_event.set()

    def _probe_health(self, state: _HostedServiceState) -> None:
        while not state.stop_event.is_set():
            with state.lock:
                service = state.service
                state.health_probe_started = time.monotonic()
            if service is None:
                return
            try:
                health = service.health()
                if not isinstance(health, BackgroundServiceHealth):
                    raise TypeError("health() must return BackgroundServiceHealth")
                health = replace(
                    health,
                    code=_bounded_text(health.code, fallback="health_invalid"),
                    message=_bounded_text(health.message),
                )
            except BaseException:
                logger.exception(
                    "Plugin background service health failed: %s",
                    state.registration.qualified_name,
                )
                health = BackgroundServiceHealth(
                    state="unhealthy",
                    code="health_failed",
                    message="background service health check failed",
                )
            with state.lock:
                state.health_probe_started = None
                if state.lifecycle not in {"stopped", "stop_timeout"}:
                    state.health = health
            if state.stop_event.wait(HEALTH_PROBE_INTERVAL_SECONDS):
                return

    @staticmethod
    def _record_failure(
        state: _HostedServiceState, *, code: str, message: str
    ) -> None:
        with state.lock:
            state.lifecycle = "failed"
            state.failure_code = code
            state.failure_message = message
            state.health = BackgroundServiceHealth(state="unhealthy", code=code)
            state.stopped_at = _utc_now()

    @staticmethod
    def _refresh_health_timeout(state: _HostedServiceState) -> None:
        with state.lock:
            started = state.health_probe_started
            probe = state.health_thread
            if (
                started is not None
                and probe is not None
                and probe.is_alive()
                and time.monotonic() - started >= HEALTH_PROBE_TIMEOUT_SECONDS
            ):
                state.health = BackgroundServiceHealth(
                    state="unhealthy",
                    code="health_timeout",
                    message="background service health check timed out",
                )

    def snapshot(self) -> tuple[HostedServiceSnapshot, ...]:
        """Return cached lifecycle facts without calling any plugin method."""
        snapshots = []
        stop_timeout_resolved = False
        for state in self._states:
            self._refresh_health_timeout(state)
            with state.lock:
                run_thread = state.run_thread
                health_thread = state.health_thread
                run_alive = bool(run_thread and run_thread.is_alive())
                health_alive = bool(health_thread and health_thread.is_alive())
                if (
                    state.lifecycle == "stop_timeout"
                    and not run_alive
                    and not health_alive
                ):
                    state.lifecycle = "stopped"
                    state.stopped_at = state.stopped_at or _utc_now()
                    stop_timeout_resolved = True
                snapshots.append(
                    HostedServiceSnapshot(
                        qualified_name=state.registration.qualified_name,
                        plugin=state.registration.plugin,
                        host_kind=self.host_kind,
                        host_instance_id=self.host_instance_id,
                        generation=self.generation,
                        lifecycle=state.lifecycle,
                        thread_alive=run_alive,
                        health_probe_alive=health_alive,
                        registered_at=state.registered_at,
                        started_at=state.started_at,
                        stopped_at=state.stopped_at,
                        health=state.health,
                        failure_code=state.failure_code,
                        failure_message=state.failure_message,
                    )
                )
        if stop_timeout_resolved and self.is_quiescent and self._on_quiescent:
            self._on_quiescent(self)
        return tuple(snapshots)

    def request_stop(self) -> None:
        """Signal every service before the host waits for any one service."""
        for state in self._states:
            state.stop_event.set()
        for state in self._states:
            with state.lock:
                if state.lifecycle in {"constructing", "running"}:
                    state.lifecycle = "stopping"

    def await_stopped(self, *, deadline: float) -> bool:
        """Join all owned threads against one caller-provided deadline."""
        for state in self._states:
            for thread in state.threads():
                remaining = max(0.0, deadline - time.monotonic())
                if remaining:
                    thread.join(timeout=remaining)
        quiescent = True
        for state in self._states:
            self._refresh_health_timeout(state)
            alive = any(thread.is_alive() for thread in state.threads())
            with state.lock:
                if alive:
                    quiescent = False
                    state.lifecycle = "stop_timeout"
                    state.failure_code = "stop_timeout"
                    state.failure_message = (
                        "background service did not stop before the host deadline"
                    )
                    state.health = BackgroundServiceHealth(
                        state="unhealthy", code="stop_timeout"
                    )
                elif state.lifecycle in {
                    "constructing",
                    "running",
                    "stopping",
                    "stop_timeout",
                }:
                    state.lifecycle = "stopped"
                    state.stopped_at = state.stopped_at or _utc_now()
        if quiescent and self._on_quiescent is not None:
            self._on_quiescent(self)
        return quiescent

    def shutdown(self, timeout: float | None = None) -> bool:
        """Cooperatively stop services within one aggregate deadline."""
        bounded_timeout = self.shutdown_timeout if timeout is None else _positive_timeout(
            timeout, name="timeout"
        )
        self.request_stop()
        return self.await_stopped(deadline=time.monotonic() + bounded_timeout)

    @property
    def is_quiescent(self) -> bool:
        return not any(
            thread.is_alive()
            for state in self._states
            for thread in state.threads()
        )

    @property
    def is_started(self) -> bool:
        """Return whether this bound generation crossed its idempotent start gate."""
        with self._start_lock:
            return self._started


__all__ = [
    "BACKGROUND_SERVICE_NAME_PATTERN",
    "BackgroundService",
    "BackgroundServiceContext",
    "BackgroundServiceFactory",
    "BackgroundServiceHealth",
    "BackgroundServiceHealthState",
    "BackgroundServiceHost",
    "BackgroundServiceHostKind",
    "BackgroundServiceLifecycle",
    "BackgroundServiceRegistration",
    "BackgroundServiceReloadBlocked",
    "HostedServiceSnapshot",
    "VALID_BACKGROUND_SERVICE_HOSTS",
]
