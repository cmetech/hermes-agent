from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from hermes_cli.plugin_services import BackgroundServiceHealth
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition did not become true before deadline")


def _register(manager: PluginManager, name: str, factory) -> None:
    PluginContext(
        PluginManifest(name="web-test", key="web-test", source="bundled"),
        manager,
    ).register_background_service(name, factory, hosts={"web"})


def _install_test_manager(monkeypatch, manager: PluginManager, events: list[str]) -> None:
    from hermes_cli import plugins

    def discover_and_load(force: bool = False) -> None:
        assert force is False
        events.append("discovered")
        manager._discovered = True

    monkeypatch.setattr(manager, "discover_and_load", discover_and_load)
    monkeypatch.setattr(plugins, "_plugin_manager", manager)


def _isolate_lifespan_resources(monkeypatch, events: list[str]) -> None:
    from hermes_cli import web_server

    async def reaper(_registry) -> None:
        await asyncio.Event().wait()

    async def close_all() -> None:
        events.append("host_resources_stopped")

    monkeypatch.setattr(web_server, "run_reaper", reaper)
    monkeypatch.setattr(web_server.PTY_REGISTRY, "close_all", close_all)
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)


def test_web_lifespan_hosts_services_between_discovery_and_resource_teardown(
    monkeypatch,
) -> None:
    from hermes_cli import web_server

    events: list[str] = []
    manager = PluginManager()
    entered = threading.Event()
    stopped = threading.Event()

    class Service:
        def run(self, stop_event: threading.Event) -> None:
            events.append("service_running")
            entered.set()
            stop_event.wait()
            events.append("service_stopped")
            stopped.set()

        def health(self) -> BackgroundServiceHealth:
            return BackgroundServiceHealth(state="healthy", code="ready")

    def factory(context):
        assert context.host_kind == "web"
        assert not hasattr(context, "agent")
        events.append("factory_called")
        return Service()

    _register(manager, "lifecycle", factory)
    _install_test_manager(monkeypatch, manager, events)
    _isolate_lifespan_resources(monkeypatch, events)

    with TestClient(web_server.app) as client:
        assert entered.wait(timeout=1)
        host = web_server.app.state.plugin_background_services
        assert host.host_kind == "web"
        assert host.snapshot()[0].lifecycle == "running"
        response = client.get(
            "/api/status",
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )
        assert response.status_code == 200
        assert events.index("discovered") < events.index("factory_called")

    assert stopped.is_set()
    assert events.index("service_stopped") < events.index(
        "host_resources_stopped"
    )


def test_web_readiness_survives_service_factory_failure(monkeypatch) -> None:
    from hermes_cli import web_server

    events: list[str] = []
    manager = PluginManager()

    def broken_factory(_context):
        raise RuntimeError("factory failure must not stop web")

    _register(manager, "broken", broken_factory)
    _install_test_manager(monkeypatch, manager, events)
    _isolate_lifespan_resources(monkeypatch, events)

    with TestClient(web_server.app) as client:
        host = web_server.app.state.plugin_background_services
        _wait_until(lambda: host.snapshot()[0].lifecycle == "failed")
        snapshot = host.snapshot()[0]
        assert snapshot.failure_code == "factory_failed"
        response = client.get(
            "/api/status",
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )
        assert response.status_code == 200


def test_web_host_import_graph_is_workflow_agnostic() -> None:
    from hermes_cli import web_server

    tree = ast.parse(Path(web_server.__file__).read_text(encoding="utf-8"))
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
