from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import socket
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from hermes_cli.plugin_services import (
    BackgroundServiceHealth,
    BackgroundServiceReloadBlocked,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition did not become true before deadline")


@dataclass
class _BlockingService:
    entered: threading.Event
    exited: threading.Event
    health_calls: int = 0

    def run(self, stop_event: threading.Event) -> None:
        self.entered.set()
        stop_event.wait()
        self.exited.set()

    def health(self) -> BackgroundServiceHealth:
        self.health_calls += 1
        return BackgroundServiceHealth(
            state="healthy",
            code="ready",
            heartbeat_at=datetime.now(timezone.utc),
        )


def _context(manager: PluginManager, *, key: str = "example/plugin") -> PluginContext:
    return PluginContext(
        PluginManifest(name="example", key=key, source="bundled"),
        manager,
    )


def test_registration_is_attributed_filtered_and_duplicate_safe() -> None:
    manager = PluginManager()
    context = _context(manager)
    called: list[str] = []

    def factory(service_context):
        called.append(service_context.host_kind)
        return _BlockingService(threading.Event(), threading.Event())

    context.register_background_service("coordinator.v1", factory, hosts={"web"})
    with pytest.raises(ValueError, match="duplicate background service"):
        context.register_background_service("coordinator.v1", factory, hosts={"web"})
    with pytest.raises(ValueError, match="service name"):
        context.register_background_service("Not Valid", factory, hosts={"web"})
    with pytest.raises(ValueError, match="unknown background service host"):
        context.register_background_service("unknown-host", factory, hosts={"cron"})

    gateway = manager.start_background_services("gateway")
    assert gateway.snapshot() == ()
    assert called == []
    assert gateway.shutdown(timeout=0.2)

    web = manager.start_background_services("web")
    _wait_until(lambda: bool(called))
    snapshot = web.snapshot()[0]
    assert snapshot.qualified_name == "example/plugin:coordinator.v1"
    assert snapshot.plugin == "example/plugin"
    assert snapshot.host_kind == "web"
    assert snapshot.lifecycle in {"constructing", "running"}
    assert web.shutdown(timeout=1)


def test_failed_plugin_registration_rolls_back_only_its_services(monkeypatch) -> None:
    manager = PluginManager()
    sibling_calls: list[str] = []
    failed_calls: list[str] = []
    _context(manager, key="sibling").register_background_service(
        "keeper",
        lambda context: (
            sibling_calls.append(context.host_kind)
            or _BlockingService(threading.Event(), threading.Event())
        ),
        hosts={"web"},
    )

    def register(context: PluginContext) -> None:
        context.register_background_service(
            "partial",
            lambda service_context: (
                failed_calls.append(service_context.host_kind)
                or _BlockingService(threading.Event(), threading.Event())
            ),
            hosts={"web"},
        )
        raise RuntimeError("registration failed after partial service")

    monkeypatch.setattr(
        manager,
        "_load_directory_module",
        lambda _manifest: SimpleNamespace(register=register),
    )
    manager._load_plugin(
        PluginManifest(name="broken", key="broken", source="bundled", path="unused")
    )

    host = manager.start_background_services("web")
    _wait_until(lambda: bool(sibling_calls))
    assert [item.qualified_name for item in host.snapshot()] == ["sibling:keeper"]
    assert failed_calls == []
    assert host.shutdown(timeout=1)


def test_factory_run_and_early_return_failures_are_isolated() -> None:
    manager = PluginManager()
    context = _context(manager, key="failure-cases")
    sibling = _BlockingService(threading.Event(), threading.Event())

    def factory_failure(_context):
        raise RuntimeError("factory secret must be sanitized")

    class RunFailure:
        def run(self, _stop_event):
            raise RuntimeError("run secret must be sanitized")

        def health(self):
            return BackgroundServiceHealth(state="healthy", code="unused")

    class EarlyReturn:
        def run(self, _stop_event):
            return None

        def health(self):
            return BackgroundServiceHealth(state="healthy", code="unused")

    context.register_background_service("factory", factory_failure, hosts={"web"})
    context.register_background_service("run", lambda _context: RunFailure(), hosts={"web"})
    context.register_background_service(
        "early", lambda _context: EarlyReturn(), hosts={"web"}
    )
    context.register_background_service("sibling", lambda _context: sibling, hosts={"web"})

    host = manager.start_background_services("web")

    def settled() -> bool:
        snapshots = {item.qualified_name: item for item in host.snapshot()}
        return (
            len(snapshots) == 4
            and snapshots["failure-cases:factory"].lifecycle == "failed"
            and snapshots["failure-cases:run"].lifecycle == "failed"
            and snapshots["failure-cases:early"].lifecycle == "failed"
            and snapshots["failure-cases:sibling"].lifecycle == "running"
        )

    _wait_until(settled)
    snapshots = {item.qualified_name: item for item in host.snapshot()}
    assert snapshots["failure-cases:factory"].failure_code == "factory_failed"
    assert snapshots["failure-cases:run"].failure_code == "run_failed"
    assert snapshots["failure-cases:early"].failure_code == "run_returned"
    assert all(
        "secret" not in (item.failure_message or "") for item in snapshots.values()
    )
    assert sibling.entered.is_set()
    assert host.shutdown(timeout=1)


def test_snapshot_uses_cached_health_and_stuck_probe_blocks_reload(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.plugin_services.HEALTH_PROBE_INTERVAL_SECONDS", 0.01
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_services.HEALTH_PROBE_TIMEOUT_SECONDS", 0.05
    )
    manager = PluginManager()
    context = _context(manager, key="blocked-health")
    health_entered = threading.Event()
    release_health = threading.Event()
    replacement_factories = 0

    class BlockingHealth(_BlockingService):
        def health(self) -> BackgroundServiceHealth:
            self.health_calls += 1
            health_entered.set()
            release_health.wait()
            return BackgroundServiceHealth(state="healthy", code="late")

    service = BlockingHealth(threading.Event(), threading.Event())
    context.register_background_service(
        "coordinator", lambda _context: service, hosts={"web"}
    )
    host = manager.start_background_services("web")
    assert health_entered.wait(timeout=1)
    time.sleep(0.06)

    started = time.monotonic()
    snapshot = host.snapshot()[0]
    elapsed = time.monotonic() - started
    assert elapsed < 0.1
    assert snapshot.health.code == "health_timeout"
    assert service.health_calls == 1

    def replacement_discovery() -> None:
        nonlocal replacement_factories

        def factory(_context):
            nonlocal replacement_factories
            replacement_factories += 1
            return _BlockingService(threading.Event(), threading.Event())

        _context(manager, key="blocked-health").register_background_service(
            "coordinator", factory, hosts={"web"}
        )

    monkeypatch.setattr(manager, "_discover_and_load_inner", replacement_discovery)
    with pytest.raises(BackgroundServiceReloadBlocked, match="stop timeout"):
        manager.reload_background_services(timeout=0.05)
    assert replacement_factories == 0
    assert host.snapshot()[0].lifecycle == "stop_timeout"

    release_health.set()
    assert host.shutdown(timeout=1)


def test_health_exception_is_cached_without_breaking_run(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.plugin_services.HEALTH_PROBE_INTERVAL_SECONDS", 0.01
    )
    manager = PluginManager()
    context = _context(manager, key="health-error")

    class HealthError(_BlockingService):
        def health(self) -> BackgroundServiceHealth:
            raise RuntimeError("sensitive health details")

    service = HealthError(threading.Event(), threading.Event())
    context.register_background_service("service", lambda _context: service, hosts={"web"})
    host = manager.start_background_services("web")
    _wait_until(lambda: host.snapshot()[0].health.code == "health_failed")
    snapshot = host.snapshot()[0]
    assert snapshot.lifecycle == "running"
    assert "sensitive" not in snapshot.health.message
    assert host.shutdown(timeout=1)


def test_factory_stop_timeout_cannot_start_run_after_shutdown() -> None:
    manager = PluginManager()
    context = _context(manager, key="slow-factory")
    factory_entered = threading.Event()
    release_factory = threading.Event()
    run_entered = threading.Event()

    class MustNotRun:
        def run(self, _stop_event: threading.Event) -> None:
            run_entered.set()

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="unexpected")

    def factory(_context):
        factory_entered.set()
        release_factory.wait()
        return MustNotRun()

    context.register_background_service("service", factory, hosts={"web"})
    host = manager.start_background_services("web")
    assert factory_entered.wait(timeout=1)

    assert host.shutdown(timeout=0.05) is False
    assert host.snapshot()[0].lifecycle == "stop_timeout"
    release_factory.set()
    _wait_until(lambda: host.snapshot()[0].lifecycle == "stopped")
    assert run_entered.is_set() is False


def test_shutdown_signals_every_service_before_waiting() -> None:
    manager = PluginManager()
    context = _context(manager, key="aggregate-stop")
    stop_events: list[threading.Event] = []
    all_signalled = [threading.Event(), threading.Event()]

    class ObserveStop:
        def __init__(self, index: int) -> None:
            self.index = index
            self.entered = threading.Event()

        def run(self, stop_event: threading.Event) -> None:
            stop_events.append(stop_event)
            self.entered.set()
            stop_event.wait()
            deadline = time.monotonic() + 0.5
            while len(stop_events) < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            if len(stop_events) == 2 and all(event.is_set() for event in stop_events):
                all_signalled[self.index].set()

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="ready")

    services = [ObserveStop(0), ObserveStop(1)]
    for index, service in enumerate(services):
        context.register_background_service(
            f"service-{index}", lambda _context, item=service: item, hosts={"web"}
        )
    host = manager.start_background_services("web")
    assert all(service.entered.wait(timeout=1) for service in services)

    assert host.shutdown(timeout=1)
    assert all(event.is_set() for event in all_signalled)


def test_safe_mode_skips_factories_and_direct_force_reload_is_interlocked(
    monkeypatch,
) -> None:
    manager = PluginManager()
    context = _context(manager, key="safe-mode")
    calls = 0

    def factory(_context):
        nonlocal calls
        calls += 1
        return _BlockingService(threading.Event(), threading.Event())

    context.register_background_service("service", factory, hosts={"web"})
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    skipped = manager.start_background_services("web")
    assert calls == 0
    assert skipped.snapshot()[0].lifecycle == "skipped_safe_mode"
    assert skipped.shutdown(timeout=0.1)

    monkeypatch.delenv("HERMES_SAFE_MODE")
    running = manager.start_background_services("web")
    _wait_until(lambda: calls == 1)
    with pytest.raises(RuntimeError, match="hosted generation is active"):
        manager.discover_and_load(force=True)
    assert running.shutdown(timeout=1)


def test_successful_hosted_reload_starts_next_generation_after_quiescence(
    monkeypatch,
) -> None:
    manager = PluginManager()
    old_exited = threading.Event()
    old = _BlockingService(threading.Event(), old_exited)
    _context(manager, key="reload").register_background_service(
        "service", lambda _context: old, hosts={"web"}
    )
    first = manager.start_background_services("web")
    assert old.entered.wait(timeout=1)

    new = _BlockingService(threading.Event(), threading.Event())

    def replacement_discovery() -> None:
        def factory(_context):
            assert old_exited.is_set()
            return new

        _context(manager, key="reload").register_background_service(
            "service", factory, hosts={"web"}
        )

    monkeypatch.setattr(manager, "_discover_and_load_inner", replacement_discovery)
    replacements = manager.reload_background_services(timeout=1)

    assert first.snapshot()[0].lifecycle == "stopped"
    assert len(replacements) == 1
    assert replacements[0].generation == first.generation + 1
    assert new.entered.wait(timeout=1)
    assert replacements[0].shutdown(timeout=1)


def test_reference_factory_is_dormant_before_run(tmp_path) -> None:
    process = psutil.Process()
    lease_root = tmp_path / "leases"
    lease_root.mkdir()

    def snapshot() -> tuple[set[int], set[int], set[tuple[str, int]], set[str]]:
        threads = {thread.ident for thread in threading.enumerate() if thread.ident}
        children = {child.pid for child in process.children(recursive=True)}
        listeners = {
            (str(connection.laddr.ip), int(connection.laddr.port))
            for connection in process.net_connections(kind="inet")
            if connection.status == psutil.CONN_LISTEN and connection.laddr
        }
        leases = {path.name for path in lease_root.iterdir()}
        return threads, children, listeners, leases

    before = snapshot()
    from hermes_cli.plugin_services import BackgroundServiceContext
    from plugins.workflow.coordinator import create_workflow_coordinator

    returned = create_workflow_coordinator(
        BackgroundServiceContext(host_kind="web", host_instance_id="test")
    )
    after = snapshot()

    assert returned.health().code == "registered"
    assert after == before
