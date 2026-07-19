from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from gateway.config import Platform
from gateway.plugin_delivery import GatewayPluginDeliveryPort
from gateway.session import SessionSource
from hermes_cli.plugin_invocation import DeliveryReceipt, PluginInvocationContext


def _source(*, profile: str = "default") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="user-1",
        thread_id="topic-7",
        profile=profile,
    )


def _invocation(*, profile: str = "default") -> PluginInvocationContext:
    return PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope=f"gateway:{profile}:telegram:chat-1:user-1",
        assurance="verified_adapter",
        return_route_capability=None,
    )


def test_server_minted_capability_survives_restart_and_plain_route_is_rejected(
    tmp_path,
) -> None:
    sends = []

    def send(route, text):
        sends.append((route, text))
        return DeliveryReceipt(status="delivered", transport_id="message-1")

    first = GatewayPluginDeliveryPort(
        tmp_path, profile="default", sender=send
    )
    with pytest.raises(TypeError, match="SessionSource"):
        first.mint_return_route("telegram:chat-1", _invocation())
    capability = first.mint_return_route(_source(), _invocation())
    assert "chat-1" not in capability
    database_bytes = first.database.read_bytes()
    assert capability.encode() not in database_bytes

    restarted = GatewayPluginDeliveryPort(
        tmp_path, profile="default", sender=send
    )
    receipt = restarted.deliver(capability, "workflow finished", "delivery-1")

    assert receipt.status == "delivered"
    assert sends[0][0]["chat_id"] == "chat-1"
    assert sends[0][0]["thread_id"] == "topic-7"


def test_capability_mint_rejects_wrong_principal_profile_and_local_boundaries(
    tmp_path,
) -> None:
    port = GatewayPluginDeliveryPort(
        tmp_path,
        profile="default",
        sender=lambda _route, _text: DeliveryReceipt(status="delivered"),
    )
    with pytest.raises(PermissionError, match="principal"):
        port.mint_return_route(
            _source(), replace(_invocation(), principal="gateway:telegram:attacker")
        )
    with pytest.raises(PermissionError, match="profile"):
        port.mint_return_route(_source(), _invocation(profile="other"))
    for boundary in ("cli", "tui"):
        with pytest.raises(PermissionError, match="verified Gateway"):
            port.mint_return_route(
                _source(), PluginInvocationContext.local_admin(boundary=boundary)
            )
    forged = port.deliver("gateway:telegram:chat-1", "text", "delivery-forged")
    assert forged.status == "unauthorized"


def test_send_success_receipt_loss_never_replays_same_delivery_key(
    tmp_path, monkeypatch
) -> None:
    sends = []

    def send(_route, _text):
        sends.append("sent")
        return DeliveryReceipt(status="delivered", transport_id="message-1")

    port = GatewayPluginDeliveryPort(tmp_path, profile="default", sender=send)
    capability = port.mint_return_route(_source(), _invocation())
    monkeypatch.setattr(
        port,
        "_complete_delivery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt lost")),
    )
    with pytest.raises(OSError, match="receipt lost"):
        port.deliver(capability, "workflow finished", "delivery-once")

    restarted = GatewayPluginDeliveryPort(
        tmp_path, profile="default", sender=send
    )
    uncertain = restarted.deliver(
        capability, "workflow finished", "delivery-once"
    )

    assert uncertain.status == "outcome_uncertain"
    assert sends == ["sent"]
    with sqlite3.connect(restarted.database) as connection:
        assert connection.execute(
            "SELECT state FROM plugin_delivery_receipts WHERE idempotency_key=?",
            ("delivery-once",),
        ).fetchone()[0] == "sending"


def test_expired_capability_is_not_delivered(tmp_path) -> None:
    sends = []
    port = GatewayPluginDeliveryPort(
        tmp_path,
        profile="default",
        sender=lambda _route, _text: sends.append(True)
        or DeliveryReceipt(status="delivered"),
    )
    capability = port.mint_return_route(
        _source(), _invocation(), ttl=timedelta(microseconds=1)
    )

    receipt = port.deliver(capability, "late", "delivery-expired")

    assert receipt.status == "unauthorized"
    assert sends == []


def test_explicit_retryable_failure_allows_one_safe_new_attempt(tmp_path) -> None:
    sends = []

    def send(_route, _text):
        sends.append("attempt")
        if len(sends) == 1:
            return DeliveryReceipt(status="retryable_failure", detail="offline")
        return DeliveryReceipt(status="delivered", transport_id="message-2")

    port = GatewayPluginDeliveryPort(tmp_path, profile="default", sender=send)
    capability = port.mint_return_route(_source(), _invocation())

    first = port.deliver(capability, "workflow finished", "delivery-retry")
    second = port.deliver(capability, "workflow finished", "delivery-retry")

    assert first.status == "retryable_failure"
    assert second.status == "delivered"
    assert sends == ["attempt", "attempt"]


def test_pre_send_storage_contention_is_retryable_without_transport_attempt(
    tmp_path, monkeypatch
) -> None:
    sends = []
    port = GatewayPluginDeliveryPort(
        tmp_path,
        profile="default",
        sender=lambda _route, _text: sends.append("attempt")
        or DeliveryReceipt(status="delivered"),
    )
    capability = port.mint_return_route(_source(), _invocation())

    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(port, "_connect", locked)

    receipt = port.deliver(capability, "workflow finished", "delivery-busy")

    assert receipt == DeliveryReceipt(
        status="retryable_failure", detail="delivery_store_unavailable"
    )
    assert sends == []


def test_gateway_invocation_uses_only_verified_session_source() -> None:
    from gateway.run import GatewayRunner

    captured = {}

    class Port:
        def mint_return_route(self, source, invocation):
            captured.update(source=source, invocation=invocation)
            return "minted-capability"

    runner = object.__new__(GatewayRunner)
    runner.plugin_delivery_port = Port()
    runner._active_profile_name = lambda: "default"

    invocation = runner._authenticated_plugin_invocation(_source())

    assert invocation.principal == "gateway:telegram:user-1"
    assert invocation.operator_scope == "gateway:default:telegram:chat-1:user-1"
    assert invocation.return_route_capability == "minted-capability"
    assert captured["source"] is not None

    source_without_identity = replace(_source(), user_id=None)
    with pytest.raises(PermissionError, match="user identity"):
        runner._authenticated_plugin_invocation(source_without_identity)


@pytest.mark.asyncio
async def test_real_gateway_middleware_passes_authenticated_context(monkeypatch) -> None:
    import gateway.run as gateway_run
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from tests.gateway.test_unknown_command import _make_event, _make_runner

    manager = PluginManager()
    manager._discovered = True
    seen = []
    PluginContext(
        PluginManifest(name="secure", key="secure", source="bundled"), manager
    ).register_authenticated_command(
        "secure-command",
        lambda args, invocation: seen.append((args, invocation))
        or f"verified:{invocation.principal}",
    )

    class Port:
        def mint_return_route(self, _source, _invocation):
            return "middleware-capability"

    runner = _make_runner()
    runner.plugin_delivery_port = Port()
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )

    result = await runner._handle_message(_make_event("/secure-command value"))

    assert result == "verified:gateway:telegram:u1"
    assert seen[0][0] == "value"
    assert seen[0][1].return_route_capability == "middleware-capability"
