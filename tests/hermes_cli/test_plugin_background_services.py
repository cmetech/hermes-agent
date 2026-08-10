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
    BackgroundServiceHost,
    BackgroundServiceReloadBlocked,
)
from hermes_cli.plugin_configuration import (
    PluginConfigurationDescriptor,
    SetupActionMetadata,
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


def test_successful_reload_rebinds_setup_actions_to_the_discovery_profile(
    tmp_path, monkeypatch
) -> None:
    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    profile_a.mkdir()
    profile_b.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    manager = PluginManager()
    old = _BlockingService(threading.Event(), threading.Event())
    _context(manager, key="reload-actions").register_background_service(
        "service", lambda _context: old, hosts={"web"}
    )
    first = manager.start_background_services("web")
    assert old.entered.wait(timeout=1)

    descriptor = PluginConfigurationDescriptor(
        version=1,
        fields=(),
        setup_actions=(SetupActionMetadata(id="connect", label="Connect"),),
    )
    manifest = PluginManifest(
        name="reload-actions",
        key="reload-actions",
        source="bundled",
        path="unused",
        kind="backend",
        configuration=descriptor,
    )
    replacement = _BlockingService(threading.Event(), threading.Event())

    def register(context: PluginContext) -> None:
        context.register_background_service(
            "service", lambda _context: replacement, hosts={"web"}
        )
        context.register_setup_action("connect", lambda _context: {"ok": True})

    monkeypatch.setattr(manager, "static_plugin_inventory", lambda: [manifest])
    monkeypatch.setattr(
        manager,
        "_load_directory_module",
        lambda _manifest: SimpleNamespace(register=register),
    )

    replacements = manager.reload_background_services(timeout=1)

    same_profile = manager.setup_action_registrations("reload-actions")
    assert sorted(same_profile) == ["connect"]

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(profile_b))
    try:
        assert manager.setup_action_registrations("reload-actions") == {}
    finally:
        reset_hermes_home_override(token)
    assert sorted(manager.setup_action_registrations("reload-actions")) == ["connect"]
    assert first.snapshot()[0].lifecycle == "stopped"
    assert replacements[0].shutdown(timeout=1)


def test_failed_reload_never_publishes_partial_setup_action_authority(
    tmp_path, monkeypatch
) -> None:
    profile = tmp_path / "profile-a"
    profile.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    manager = PluginManager()
    old = _BlockingService(threading.Event(), threading.Event())
    _context(manager, key="reload-actions").register_background_service(
        "service", lambda _context: old, hosts={"web"}
    )
    host = manager.start_background_services("web")
    assert old.entered.wait(timeout=1)

    def partial_discovery() -> None:
        manager._setup_actions["reload-actions"] = {
            "connect": {"handler": lambda _context: {}, "readiness": None}
        }
        raise RuntimeError("reload discovery failed")

    monkeypatch.setattr(manager, "_discover_and_load_inner", partial_discovery)
    with pytest.raises(RuntimeError, match="reload discovery failed"):
        manager.reload_background_services(timeout=1)

    assert manager.setup_action_registrations("reload-actions") == {}


def test_provider_reload_blocks_callers_until_new_registry_is_published(
    monkeypatch,
) -> None:
    manager = PluginManager()
    old = _BlockingService(threading.Event(), threading.Event())
    _context(manager, key="atomic-reload").register_background_service(
        "service", lambda _context: old, hosts={"web"}
    )
    host = manager.start_background_services("web")
    assert old.entered.wait(timeout=1)
    discovery_entered = threading.Event()
    release_discovery = threading.Event()
    caller_returned = threading.Event()
    replacements: list[tuple] = []

    def replacement_discovery() -> None:
        discovery_entered.set()
        release_discovery.wait()
        _context(manager, key="atomic-reload").register_background_service(
            "service",
            lambda _context: _BlockingService(
                threading.Event(), threading.Event()
            ),
            hosts={"web"},
        )

    monkeypatch.setattr(manager, "_discover_and_load_inner", replacement_discovery)
    reload_thread = threading.Thread(
        target=lambda: replacements.append(
            manager.reload_background_services(timeout=1)
        )
    )
    reload_thread.start()
    assert discovery_entered.wait(timeout=1)
    caller = threading.Thread(
        target=lambda: (
            manager.discover_and_load(),
            caller_returned.set(),
        )
    )
    caller.start()
    time.sleep(0.05)
    assert caller_returned.is_set() is False

    release_discovery.set()
    reload_thread.join(timeout=1)
    caller.join(timeout=1)

    assert caller_returned.is_set()
    assert len(replacements) == 1
    assert replacements[0][0].generation == host.generation + 1
    assert replacements[0][0].shutdown(timeout=1)


def test_provider_reload_control_failure_releases_discovery_waiters() -> None:
    manager = PluginManager()

    class BrokenHost:
        host_kind = "web"
        shutdown_timeout = 1.0
        is_started = True
        is_quiescent = False

        def request_stop(self):
            raise OSError("host control failed")

    manager._background_service_hosts["web"] = BrokenHost()

    with pytest.raises(OSError, match="host control failed"):
        manager.reload_background_services(timeout=0.1)

    assert manager._background_reload_in_progress is False
    assert manager._background_reload_thread_id is None


def test_same_kind_start_returns_existing_live_generation() -> None:
    manager = PluginManager()
    calls = 0
    service = _BlockingService(threading.Event(), threading.Event())

    def factory(_context):
        nonlocal calls
        calls += 1
        return service

    _context(manager, key="same-kind").register_background_service(
        "service", factory, hosts={"web"}
    )
    first = manager.start_background_services("web")
    assert service.entered.wait(timeout=1)

    second = manager.start_background_services("web")

    assert second is first
    assert calls == 1
    assert first.shutdown(timeout=1)


def test_concurrent_same_kind_start_cannot_replace_bound_unstarted_host(
    monkeypatch,
) -> None:
    manager = PluginManager()
    service = _BlockingService(threading.Event(), threading.Event())
    _context(manager, key="same-kind-race").register_background_service(
        "service", lambda _context: service, hosts={"web"}
    )
    start_entered = threading.Event()
    release_start = threading.Event()
    original_start = BackgroundServiceHost.start

    def delayed_start(host):
        start_entered.set()
        release_start.wait()
        original_start(host)

    monkeypatch.setattr(BackgroundServiceHost, "start", delayed_start)
    returned: list[BackgroundServiceHost] = []
    first_thread = threading.Thread(
        target=lambda: returned.append(manager.start_background_services("web"))
    )
    second_thread = threading.Thread(
        target=lambda: returned.append(manager.start_background_services("web"))
    )
    first_thread.start()
    assert start_entered.wait(timeout=1)
    second_thread.start()
    time.sleep(0.05)
    release_start.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert len(returned) == 2
    assert returned[0] is returned[1]
    assert service.entered.wait(timeout=1)
    assert returned[0].shutdown(timeout=1)


def test_same_kind_stop_timeout_never_starts_overlapping_generation() -> None:
    manager = PluginManager()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    class WedgedService:
        def run(self, _stop_event: threading.Event) -> None:
            entered.set()
            release.wait()

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="ready")

    def factory(_context):
        nonlocal calls
        calls += 1
        return WedgedService()

    _context(manager, key="same-kind-wedged").register_background_service(
        "service", factory, hosts={"web"}
    )
    first = manager.start_background_services("web")
    assert entered.wait(timeout=1)
    assert first.shutdown(timeout=0.05) is False

    second = None
    try:
        second = manager.start_background_services("web")
        assert second is first
        assert calls == 1
    finally:
        release.set()
        assert first.shutdown(timeout=1)
        if second is not None and second is not first:
            assert second.shutdown(timeout=1)


def test_reference_factory_and_cached_health_are_dormant_before_run(
    tmp_path, monkeypatch
) -> None:
    process = psutil.Process()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plugins.workflow.store import RunStore

    store = RunStore(tmp_path)

    def snapshot() -> tuple[set[int], set[int], set[tuple[str, int]], tuple[int, int]]:
        threads = {thread.ident for thread in threading.enumerate() if thread.ident}
        children = {child.pid for child in process.children(recursive=True)}
        listeners = {
            (str(connection.laddr.ip), int(connection.laddr.port))
            for connection in process.net_connections(kind="inet")
            if connection.status == psutil.CONN_LISTEN and connection.laddr
        }
        with store._connect() as connection:
            durable_rows = (
                connection.execute("SELECT count(*) FROM coordinator_lease").fetchone()[0],
                connection.execute("SELECT count(*) FROM coordinator_events").fetchone()[0],
            )
        return threads, children, listeners, durable_rows

    before = snapshot()
    from hermes_cli.plugin_services import BackgroundServiceContext
    from plugins.workflow.coordinator import create_workflow_coordinator

    returned = create_workflow_coordinator(
        BackgroundServiceContext(host_kind="web", host_instance_id="test")
    )
    after = snapshot()

    assert after == before
    monkeypatch.setattr(
        "sqlite3.connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached health must not open SQLite")
        ),
    )
    started = time.monotonic()
    assert returned.health().code == "registered"
    assert time.monotonic() - started < 0.1
