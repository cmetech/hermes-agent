from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.run as gateway_run
from hermes_cli.plugin_services import (
    BackgroundServiceHealth,
    BackgroundServiceReloadBlocked,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.gateway.test_startup_restart_race import (
    make_startup_runner,
    patch_startup_side_effects,
)


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition did not become true before deadline")


def _register(manager: PluginManager, name: str, factory) -> None:
    PluginContext(
        PluginManifest(name="gateway-test", key="gateway-test", source="bundled"),
        manager,
    ).register_background_service(name, factory, hosts={"gateway"})


@pytest.mark.asyncio
async def test_gateway_start_hosts_services_after_discovery_before_running(
    tmp_path,
    monkeypatch,
) -> None:
    patch_startup_side_effects(monkeypatch, tmp_path)
    events: list[str] = []
    manager = PluginManager()
    entered = threading.Event()
    release = threading.Event()

    class Service:
        def run(self, stop_event: threading.Event) -> None:
            events.append("service_running")
            entered.set()
            stop_event.wait()
            events.append("service_stopped")
            release.set()

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="ready")

    def broken_factory(_context):
        raise RuntimeError("one broken service must not stop Gateway")

    _register(manager, "coordinator", lambda _context: Service())
    _register(manager, "broken", broken_factory)

    from hermes_cli import plugins

    def discover_plugins(force: bool = False) -> None:
        assert force is False
        events.append("discovered")
        manager._discovered = True

    original_start = manager.start_background_services

    def start_background_services(host, *, shutdown_timeout=10.0):
        events.append("services_bound")
        return original_start(host, shutdown_timeout=shutdown_timeout)

    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    monkeypatch.setattr(plugins, "discover_plugins", discover_plugins)
    monkeypatch.setattr(manager, "start_background_services", start_background_services)

    runner = make_startup_runner(tmp_path)
    runner.config.platforms = {}
    runner._wire_teams_pipeline_runtime = MagicMock()
    runner._schedule_resume_pending_sessions = MagicMock()
    runner._finish_startup_restore = AsyncMock()
    runner._update_runtime_status.side_effect = lambda state, **_kwargs: events.append(
        f"runtime_{state}"
    )

    async def idle_watcher(*_args, **_kwargs):
        await asyncio.Event().wait()

    for method_name in (
        "_session_expiry_watcher",
        "_kanban_notifier_watcher",
        "_kanban_dispatcher_watcher",
        "_platform_reconnect_watcher",
        "_handoff_watcher",
        "_async_delegation_watcher",
        "_drain_control_watcher",
    ):
        setattr(runner, method_name, idle_watcher)

    created_tasks: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def create_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(gateway_run.asyncio, "create_task", create_task)

    try:
        assert await asyncio.wait_for(runner.start(), timeout=2) is True
        assert entered.wait(timeout=1)
        host = runner.plugin_background_services
        _wait_until(
            lambda: {
                item.qualified_name: item.lifecycle for item in host.snapshot()
            }
            == {
                "gateway-test:broken": "failed",
                "gateway-test:coordinator": "running",
            }
        )
        assert events.index("discovered") < events.index("services_bound")
        assert events.index("services_bound") < events.index("runtime_running")
    finally:
        host = getattr(runner, "plugin_background_services", None)
        if host is not None:
            assert host.shutdown(timeout=1)
        for task in created_tasks:
            task.cancel()
        await asyncio.gather(*created_tasks, return_exceptions=True)

    assert release.is_set()


@pytest.mark.asyncio
async def test_gateway_stop_quiesces_services_before_adapter_and_database_teardown() -> None:
    events: list[str] = []
    manager = PluginManager()
    entered = threading.Event()

    class Service:
        def run(self, stop_event: threading.Event) -> None:
            entered.set()
            stop_event.wait()
            events.append("service_stopped")

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="ready")

    _register(manager, "coordinator", lambda _context: Service())
    host = manager.start_background_services("gateway")
    assert entered.wait(timeout=1)

    runner, adapter = make_restart_runner()
    runner.plugin_background_services = host

    async def disconnect() -> None:
        events.append("adapter_disconnected")

    adapter.disconnect = disconnect
    runner.session_store._db.close.side_effect = lambda: events.append("database_closed")

    with (
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.release_gateway_runtime_lock"),
        patch("gateway.status.write_runtime_status"),
        patch("agent.auxiliary_client.shutdown_cached_clients"),
    ):
        await runner.stop()

    assert events.index("service_stopped") < events.index("adapter_disconnected")
    assert events.index("service_stopped") < events.index("database_closed")
    assert host.is_quiescent


def test_gateway_host_import_graph_is_workflow_agnostic() -> None:
    tree = ast.parse(Path(gateway_run.__file__).read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(module.startswith("plugins.workflow") for module in imports)


@pytest.mark.asyncio
async def test_gateway_provider_reload_runs_in_host_controller(monkeypatch) -> None:
    manager = PluginManager()
    old_host = object()
    replacement = MagicMock(host_kind="gateway")
    reload_threads: list[int] = []

    def reload_background_services(*, timeout):
        assert timeout == 10.0
        reload_threads.append(threading.get_ident())
        return (replacement,)

    monkeypatch.setattr(
        manager, "reload_background_services", reload_background_services
    )
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    runner = gateway_run.GatewayRunner.__new__(gateway_run.GatewayRunner)
    runner.plugin_background_services = old_host
    controller_thread = threading.get_ident()

    result = await runner.reload_plugin_background_services()

    assert result == {"ok": True}
    assert reload_threads and reload_threads[0] != controller_thread
    assert runner.plugin_background_services is replacement


@pytest.mark.asyncio
async def test_gateway_provider_reload_blocked_keeps_old_generation(
    monkeypatch,
) -> None:
    manager = PluginManager()
    old_host = object()

    def blocked(*, timeout):
        raise BackgroundServiceReloadBlocked(f"blocked after {timeout}")

    monkeypatch.setattr(manager, "reload_background_services", blocked)
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    runner = gateway_run.GatewayRunner.__new__(gateway_run.GatewayRunner)
    runner.plugin_background_services = old_host

    result = await runner.reload_plugin_background_services(timeout=0.1)

    assert result == {"ok": False, "error": "plugin_reload_blocked"}
    assert runner.plugin_background_services is old_host
