"""The desktop backend must start the gateway when nothing else will.

The gateway hosts the kanban dispatcher and cron. Its only autostart path
(install.ps1's Start-GatewayIfConfigured) fires solely when a MESSAGING token
is present, so a desktop install that uses kanban but no messaging never gets a
dispatcher -- the board looks alive and is structurally inert, permanently and
silently. These tests pin the four guards that decide whether to start one.
"""

from __future__ import annotations

import pytest

from hermes_cli import web_server
from hermes_cli.web_server import _maybe_autostart_gateway, _should_autostart_gateway


def _live_web_server():
    """Resolve hermes_cli.web_server FRESH, at call time.

    Sibling suites evict ``hermes_cli.*`` from ``sys.modules`` between tests
    (tests/hermes_cli/test_kanban_default_assignee.py and
    test_kanban_cli_dispatch_passthrough.py both do; test_kanban_core_
    functionality.py documents the same hazard). After such an eviction the
    module object bound at import time above is a STALE copy: patching
    ``hermes_cli.web_server._spawn_gateway_restart`` then lands on the fresh
    module while the stale function's globals still hold the real one — so the
    test silently exercises the REAL spawn and launches an actual
    ``hermes gateway restart`` subprocess. Resolving here keeps the object we
    patch and the function we call the same one.
    """
    from hermes_cli import web_server as live

    return live


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

    def test_no_start_when_the_probe_could_not_tell(self):
        """`running=False` + `probe_error` means "could not tell", NOT "down".

        The spawn goes through `hermes gateway restart`, which STOPS a running
        gateway first -- so acting on an unreadable probe (a locked
        gateway.lock, a permissions hiccup) kills a perfectly healthy gateway
        mid-turn on every desktop launch. Fail OPEN, like the kanban
        dispatcher warning does for the same signal.
        """
        assert not _should_autostart_gateway(
            is_desktop=True,
            in_gateway=False,
            autostart_enabled=True,
            gateway_running=False,
            probe_failed=True,
        )


class TestMaybeAutostartGateway:
    """End-to-end guard behaviour.

    Every test resolves the module through ``_live_web_server()`` and patches
    the spawn on THAT object. Binding the module at import time is unsafe here:
    sibling suites evict ``hermes_cli.*`` from ``sys.modules``, after which a
    patch aimed at the fresh module never reaches the stale function actually
    under test, and the assertion silently exercises the REAL
    ``hermes gateway restart``.
    """

    def _patch(
        self,
        monkeypatch,
        *,
        running: bool,
        cfg: dict,
        desktop: str = "1",
        probe_error: bool = False,
        calls: list | None = None,
    ):
        monkeypatch.setenv("HERMES_DESKTOP", desktop)
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
        monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)

        def _resolve(*a, **k):
            if calls is not None:
                calls.append(k)
            return type(
                "L",
                (),
                {"running": running, "pid": None, "probe_error": probe_error},
            )()

        monkeypatch.setattr("gateway.status.resolve_gateway_liveness", _resolve)

        return _live_web_server()

    @staticmethod
    def _record_spawn(monkeypatch, live, sink: list):
        monkeypatch.setattr(
            live,
            "_spawn_gateway_restart",
            lambda *a, **k: sink.append(True) or (None, False),
        )

    def test_spawns_when_no_gateway_is_running(self, monkeypatch):
        spawned: list = []
        live = self._patch(monkeypatch, running=False, cfg={})
        self._record_spawn(monkeypatch, live, spawned)
        assert live._maybe_autostart_gateway() is True
        assert spawned == [True]

    def test_does_not_spawn_when_config_disables_it(self, monkeypatch):
        spawned: list = []
        live = self._patch(
            monkeypatch,
            running=False,
            cfg={"gateway": {"autostart_with_desktop": False}},
        )
        self._record_spawn(monkeypatch, live, spawned)
        assert live._maybe_autostart_gateway() is False
        assert spawned == []

    def test_does_not_spawn_when_the_probe_could_not_tell(self, monkeypatch):
        """A raising PID probe must not be read as "no gateway".

        ``_spawn_gateway_restart`` runs ``hermes gateway restart``, which stops
        the running gateway first -- so this would kill a healthy gateway on
        every desktop launch for as long as the probe stayed unreadable.
        """
        spawned: list = []
        live = self._patch(monkeypatch, running=False, cfg={}, probe_error=True)
        self._record_spawn(monkeypatch, live, spawned)
        assert live._maybe_autostart_gateway() is False
        assert spawned == []

    def test_probes_gateway_health_url_when_configured(self, monkeypatch):
        """Rung 2 covers a cross-container gateway with no local PID file.

        Without it, a GATEWAY_HEALTH_URL deployment reports gateway_running
        true on /api/status while autostart sees "nothing running" and spawns a
        second local gateway on every launch.
        """
        calls: list = []
        spawned: list = []
        live = self._patch(monkeypatch, running=True, cfg={}, calls=calls)
        monkeypatch.setattr(live, "_GATEWAY_HEALTH_URL", "http://gw:8080/health")
        self._record_spawn(monkeypatch, live, spawned)
        assert live._maybe_autostart_gateway() is False
        assert calls and calls[0]["health_probe"] is live._probe_gateway_health

    def test_omits_the_health_probe_when_no_url_is_configured(self, monkeypatch):
        calls: list = []
        spawned: list = []
        live = self._patch(monkeypatch, running=False, cfg={}, calls=calls)
        monkeypatch.setattr(live, "_GATEWAY_HEALTH_URL", None)
        self._record_spawn(monkeypatch, live, spawned)
        assert live._maybe_autostart_gateway() is True
        assert calls and calls[0]["health_probe"] is None

    def test_is_fail_safe(self, monkeypatch):
        """A broken probe must never stop the backend from starting."""
        monkeypatch.setenv("HERMES_DESKTOP", "1")
        monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
        spawned: list = []
        live = _live_web_server()
        self._record_spawn(monkeypatch, live, spawned)

        def boom(*a, **k):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr("gateway.status.resolve_gateway_liveness", boom)
        assert live._maybe_autostart_gateway() is False
        assert spawned == []
