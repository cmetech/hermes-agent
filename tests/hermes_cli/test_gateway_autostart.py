"""The desktop backend must start the gateway when nothing else will.

The gateway hosts the kanban dispatcher and cron. Its only autostart path
(install.ps1's Start-GatewayIfConfigured) fires solely when a MESSAGING token
is present, so a desktop install that uses kanban but no messaging never gets a
dispatcher -- the board looks alive and is structurally inert, permanently and
silently. These tests pin the four guards that decide whether to start one.
"""

from __future__ import annotations

import pytest

from hermes_cli.web_server import _maybe_autostart_gateway, _should_autostart_gateway


class TestShouldAutostartGateway:
    def test_starts_when_desktop_and_nothing_is_running(self):
        assert _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=False,
        )

    def test_no_start_when_a_gateway_is_already_running(self):
        """Idempotent across backend restarts; the gateway also refuses to
        double-run, but we must not spawn a process just to have it exit."""
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=True,
        )

    def test_no_start_when_we_are_the_gateway(self):
        """The backend runs INSIDE the gateway in some deployments. Without
        this guard it would spawn itself (the same hazard _spawn_hermes_action
        already scrubs _HERMES_GATEWAY for)."""
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=True,
            autostart_enabled=True,
            gateway_running=False,
        )

    def test_no_start_when_disabled_by_config(self):
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=False,
            gateway_running=False,
        )

    def test_no_start_for_a_server_dashboard(self):
        """`hermes dashboard` on a server relies on its own gateway; this
        change must not alter that deployment."""
        assert not _should_autostart_gateway(
            is_desktop=False,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=False,
        )


class TestMaybeAutostartGateway:
    def _patch(self, monkeypatch, *, running: bool, cfg: dict, desktop: str = "1"):
        monkeypatch.setenv("HERMES_DESKTOP", desktop)
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
        monkeypatch.setattr(
            "hermes_cli.config.load_config", lambda *a, **k: cfg
        )
        monkeypatch.setattr(
            "gateway.status.resolve_gateway_liveness",
            lambda *a, **k: type("L", (), {"running": running, "pid": None})(),
        )

    def test_spawns_when_no_gateway_is_running(self, monkeypatch):
        spawned = []
        self._patch(monkeypatch, running=False, cfg={})
        monkeypatch.setattr(
            "hermes_cli.web_server._spawn_gateway_restart",
            lambda *a, **k: spawned.append(True) or (None, False),
        )
        assert _maybe_autostart_gateway() is True
        assert spawned == [True]

    def test_does_not_spawn_when_config_disables_it(self, monkeypatch):
        spawned = []
        self._patch(
            monkeypatch,
            running=False,
            cfg={"gateway": {"autostart_with_desktop": False}},
        )
        monkeypatch.setattr(
            "hermes_cli.web_server._spawn_gateway_restart",
            lambda *a, **k: spawned.append(True) or (None, False),
        )
        assert _maybe_autostart_gateway() is False
        assert spawned == []

    def test_is_fail_safe(self, monkeypatch):
        """A broken probe must never stop the backend from starting."""
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)

        def boom(*a, **k):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr("gateway.status.resolve_gateway_liveness", boom)
        assert _maybe_autostart_gateway() is False
