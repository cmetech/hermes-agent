from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from hermes_cli.plugin_invocation import (
    DeliveryReceipt,
    PluginInvocationContext,
)
from hermes_cli.plugin_services import BackgroundServiceHealth
from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    get_plugin_command_handler,
    invoke_plugin_command,
)
from plugins.workflow.gateway_command import (
    decide_gateway_run,
    gateway_trigger_provenance,
)


def _context(manager: PluginManager) -> PluginContext:
    return PluginContext(
        PluginManifest(name="secure", key="secure", source="bundled"),
        manager,
    )


def test_authenticated_command_receives_only_frozen_verified_context() -> None:
    manager = PluginManager()
    seen = []
    _context(manager).register_authenticated_command(
        "secure-command",
        lambda args, invocation: seen.append((args, invocation)) or "ok",
    )
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )

    with patch("hermes_cli.plugins._plugin_manager", manager):
        assert invoke_plugin_command(
            "secure-command", "run demo", context=invocation
        ) == "ok"
        assert get_plugin_command_handler("secure-command") is None
        with pytest.raises(PermissionError, match="authenticated invocation"):
            invoke_plugin_command("secure-command", "run demo", context=None)

    assert seen == [("run demo", invocation)]
    with pytest.raises(FrozenInstanceError):
        invocation.principal = "forged"  # type: ignore[misc]


def test_existing_one_argument_plugin_commands_are_unchanged() -> None:
    manager = PluginManager()
    handler = lambda args: f"legacy:{args}"
    _context(manager).register_command("legacy-command", handler)
    with patch("hermes_cli.plugins._plugin_manager", manager):
        assert get_plugin_command_handler("legacy-command") is handler
        assert invoke_plugin_command(
            "legacy-command",
            "value",
            context=PluginInvocationContext.local_admin(boundary="cli"),
        ) == "legacy:value"


def test_gateway_service_context_receives_port_while_web_receives_none() -> None:
    manager = PluginManager()
    captured = {}
    stopped = threading.Event()

    class Service:
        def run(self, stop_event):
            stop_event.wait()
            stopped.set()

        def health(self):
            return BackgroundServiceHealth(state="healthy", code="ready")

    def factory(context):
        captured[context.host_kind] = context.delivery_port
        return Service()

    _context(manager).register_background_service(
        "consumer", factory, hosts={"gateway", "web"}
    )
    port = lambda capability, text, key: DeliveryReceipt(status="delivered")

    gateway = manager.start_background_services("gateway", delivery_port=port)
    web = manager.start_background_services("web")
    for _ in range(100):
        if {"gateway", "web"} <= captured.keys():
            break
        threading.Event().wait(0.01)

    assert captured == {"gateway": port, "web": None}
    assert gateway.shutdown(timeout=1)
    assert web.shutdown(timeout=1)
    assert stopped.is_set()


def test_workflow_gateway_start_and_gate_use_authenticated_context() -> None:
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope="gateway:default:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )
    provenance = gateway_trigger_provenance(
        invocation, intent_key="stable-request"
    )
    assert provenance.assurance == "verified_adapter"
    assert provenance.actor_id == invocation.principal
    assert provenance.return_route == "opaque-capability"

    captured = {}

    class Store:
        def approve_run(self, run_id, **kwargs):
            captured.update(run_id=run_id, **kwargs)
            return "approved"

    assert decide_gateway_run(
        Store(),
        decision="approved",
        run_id="run-1",
        interaction_id="gate-1",
        expected_version=7,
        response="yes",
        invocation=invocation,
    ) == "approved"
    assert captured["actor"] == invocation.principal
    assert captured["operator_scope"] == invocation.operator_scope
    assert captured["channel"] == "gateway"


def test_workflow_plugin_registers_authenticated_gateway_command() -> None:
    from plugins.workflow import register

    manager = PluginManager()
    register(
        PluginContext(
            PluginManifest(name="workflow", key="workflow", source="bundled"),
            manager,
        )
    )

    entry = manager._plugin_commands["workflow"]
    assert entry["authenticated"] is True
