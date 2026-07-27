"""`/browser connect` must never adopt the enrolled corporate browser.

`/browser connect` publishes a PROCESS-GLOBAL ``BROWSER_CDP_URL``. Once it is
set, ``_navigation_session_key`` returns the bare task key for every URL and
``_session_cdp_url`` falls through to the override -- so every navigation,
including attacker-controlled public pages, runs in whatever browser answers on
that port. Pointing it at the enrolled profile reopens the exact process-global
leak the per-navigation routing was built to remove (BP-1).

Two independent halves, each tested here:
  (a) the SEEDED enrolled port is not ``/browser connect``'s default, so the
      shipped config cannot collide by accident;
  (b) the connect path REFUSES an enrolled port, so a hand-set collision (or the
      confluence skill's Edge on the reserved default) is still protected.

Design: docs/plans/2026-07-26-per-navigation-browser-profile-design.md
"""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
from unittest.mock import patch

import pytest

from hermes_cli import browser_connect
from tools import browser_profiles

CONNECT_PORT = browser_connect.DEFAULT_BROWSER_CDP_PORT  # 9222


def _config(monkeypatch, cfg):
    monkeypatch.setattr(browser_profiles, "_read_config", lambda: cfg)


class TestSeededPortDoesNotCollide:
    """(a) The default the product ships must not be 9222."""

    def test_module_default_is_not_the_connect_port(self):
        assert browser_profiles.DEFAULT_CDP_PORT != CONNECT_PORT

    def test_shipped_manifest_does_not_seed_the_connect_port(self):
        manifest = json.loads(
            (Path(__file__).resolve().parents[2] / "capabilities" / "ericsson.json")
            .read_text(encoding="utf-8")
        )
        seeded = manifest["configDefaults"]["browser"]["profiles"]["enrolled"]
        assert seeded["cdp_port"] != CONNECT_PORT, (
            "the shipped enrolled profile sits on /browser connect's port: a plain "
            "`/browser connect` would adopt the corporate browser process-wide"
        )


class TestEnrolledPortRegistry:
    def test_reserved_default_is_always_present(self, monkeypatch):
        _config(monkeypatch, {})
        assert browser_profiles.DEFAULT_CDP_PORT in browser_profiles.enrolled_cdp_ports()

    def test_configured_enrolled_port_is_reported_with_its_profile(self, monkeypatch):
        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": 9401}}}
        })
        assert browser_profiles.enrolled_cdp_ports()[9401] == "corp"

    def test_a_hand_set_collision_on_the_connect_port_is_reported(self, monkeypatch):
        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": CONNECT_PORT}}}
        })
        assert browser_profiles.enrolled_cdp_ports()[CONNECT_PORT] == "corp"

    def test_an_ephemeral_profile_never_reserves_a_port(self, monkeypatch):
        _config(monkeypatch, {
            "browser": {"profiles": {"throwaway": {"kind": "ephemeral", "cdp_port": 9402}}}
        })
        assert 9402 not in browser_profiles.enrolled_cdp_ports()


class TestConnectRefusal:
    def test_connect_port_is_allowed_with_the_shipped_defaults(self, monkeypatch):
        """The out-of-the-box `/browser connect` keeps working untouched."""
        _config(monkeypatch, {})
        assert browser_connect.enrolled_port_refusal(CONNECT_PORT) is None

    def test_reserved_default_is_refused_even_with_no_profile_configured(self, monkeypatch):
        _config(monkeypatch, {})
        refusal = browser_connect.enrolled_port_refusal(browser_profiles.DEFAULT_CDP_PORT)
        assert refusal and "Refusing to connect" in refusal

    def test_hand_set_collision_on_the_connect_port_is_refused(self, monkeypatch):
        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": CONNECT_PORT}}}
        })
        refusal = browser_connect.enrolled_port_refusal(CONNECT_PORT)
        assert refusal and "'corp'" in refusal

    def test_fails_open_when_the_registry_cannot_be_read(self, monkeypatch):
        """A broken config must not make /browser connect unusable."""
        def _boom():
            raise RuntimeError("config on fire")

        monkeypatch.setattr(browser_profiles, "load_profiles", _boom)
        # DEFAULT_CDP_PORT survives the failure (it is not config-derived), but
        # an arbitrary port must still be connectable.
        assert browser_connect.enrolled_port_refusal(9999) is None

    def test_free_port_search_skips_enrolled_ports(self, monkeypatch):
        """A squatted port must not push the debug browser onto the enrolled one."""
        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": 9224}}}
        })
        picked = browser_connect.find_free_debug_port(9223, attempts=3)
        assert picked != 9224


class TestCliConnectRefusesTheEnrolledBrowser:
    def _connect(self, monkeypatch, url_suffix, port):
        from cli import HermesCLI

        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": port}}}
        })
        cli = HermesCLI.__new__(HermesCLI)
        cli._pending_input = Queue()
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        buf = StringIO()
        with (
            patch("hermes_cli.cli_commands_mixin.discover_local_cdp_url",
                  return_value=f"http://127.0.0.1:{port}"),
            patch("hermes_cli.cli_commands_mixin.is_browser_debug_ready", return_value=True),
            patch("hermes_cli.cli_commands_mixin.launch_chrome_debug") as launch,
            patch("tools.browser_tool.cleanup_all_browsers"),
            patch("tools.browser_tool._ensure_cdp_supervisor"),
            redirect_stdout(buf),
        ):
            cli._handle_browser_command(f"/browser connect{url_suffix}")
        return buf.getvalue(), launch

    def test_explicit_url_on_the_enrolled_port_is_refused(self, monkeypatch):
        out, launch = self._connect(monkeypatch, " http://127.0.0.1:9401", 9401)
        assert "Refusing to connect" in out
        assert os.environ.get("BROWSER_CDP_URL") is None, (
            "the corporate browser became the process-global CDP endpoint"
        )
        launch.assert_not_called()

    def test_default_url_is_refused_when_a_profile_squats_the_connect_port(self, monkeypatch):
        out, _ = self._connect(monkeypatch, "", CONNECT_PORT)
        assert "Refusing to connect" in out
        assert os.environ.get("BROWSER_CDP_URL") is None

    def test_ordinary_connect_still_works(self, monkeypatch):
        """The refusal must not break the normal path."""
        from cli import HermesCLI

        _config(monkeypatch, {})
        cli = HermesCLI.__new__(HermesCLI)
        cli._pending_input = Queue()
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        buf = StringIO()
        try:
            with (
                patch("hermes_cli.cli_commands_mixin.discover_local_cdp_url",
                      return_value=f"http://127.0.0.1:{CONNECT_PORT}"),
                patch("hermes_cli.cli_commands_mixin.is_browser_debug_ready", return_value=True),
                patch("tools.browser_tool.cleanup_all_browsers"),
                patch("tools.browser_tool._ensure_cdp_supervisor"),
                redirect_stdout(buf),
            ):
                cli._handle_browser_command("/browser connect")
            assert "Refusing to connect" not in buf.getvalue()
            assert os.environ.get("BROWSER_CDP_URL") == f"http://127.0.0.1:{CONNECT_PORT}"
        finally:
            os.environ.pop("BROWSER_CDP_URL", None)

    def test_connect_note_no_longer_claims_an_isolated_profile(self, monkeypatch):
        """The note claimed isolation it never verified -- and the enrolled
        browser is the user's real everyday one."""
        from cli import HermesCLI

        _config(monkeypatch, {})
        cli = HermesCLI.__new__(HermesCLI)
        cli._pending_input = Queue()
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        try:
            with (
                patch("hermes_cli.cli_commands_mixin.discover_local_cdp_url",
                      return_value=f"http://127.0.0.1:{CONNECT_PORT}"),
                patch("hermes_cli.cli_commands_mixin.is_browser_debug_ready", return_value=True),
                patch("tools.browser_tool.cleanup_all_browsers"),
                patch("tools.browser_tool._ensure_cdp_supervisor"),
                redirect_stdout(StringIO()),
            ):
                cli._handle_browser_command("/browser connect")
        finally:
            os.environ.pop("BROWSER_CDP_URL", None)
        note = cli._pending_input.get_nowait()
        assert "typically a Hermes-managed isolated debug" not in note
        assert "NOT verified to be an isolated debug profile" in note


class TestGatewayConnectRefusesTheEnrolledBrowser:
    def test_rpc_refuses_an_enrolled_port(self, monkeypatch):
        from tui_gateway import server

        _config(monkeypatch, {
            "browser": {"profiles": {"corp": {"kind": "enrolled", "cdp_port": 9401}}}
        })
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        resp = server.handle_request({
            "id": "1",
            "method": "browser.manage",
            "params": {"action": "connect", "url": "http://127.0.0.1:9401"},
        })
        assert "error" in resp
        assert "Refusing to connect" in resp["error"]["message"]
        assert os.environ.get("BROWSER_CDP_URL") is None


@pytest.mark.parametrize("port", [browser_profiles.DEFAULT_CDP_PORT])
def test_reserved_default_never_becomes_the_global_override(port, monkeypatch):
    """Mutation guard: deleting the refusal makes this fail."""
    _config(monkeypatch, {})
    assert browser_connect.enrolled_port_refusal(port) is not None
