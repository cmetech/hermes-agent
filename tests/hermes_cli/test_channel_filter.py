import json
from pathlib import Path
import pytest


def _brand(tmp_path, slug, allow):
    (tmp_path / "brands").mkdir(parents=True, exist_ok=True)
    (tmp_path / "brands" / f"{slug}.json").write_text(json.dumps(
        {"slug": slug, "curation": {"channels": {"allow": allow}}}))
    (tmp_path / "brand").mkdir(exist_ok=True)
    (tmp_path / "brand" / "active").write_text(slug)
    return tmp_path


def test_catalog_filtered_to_allowlist(tmp_path, monkeypatch):
    # Force the active brand + allowlist via OTTO_BRAND + a temp root is awkward
    # for web_server (fixed repo root), so drive the filter via the public helper
    # the catalog uses. Assert the FILTER function keeps only allowed ids.
    from hermes_cli import web_server as ws
    visible = {"telegram", "slack"}
    entries = [{"id": "telegram"}, {"id": "signal"}, {"id": "slack"}, {"id": "irc"}]
    kept = ws._apply_platform_visibility(entries, visible)
    assert [e["id"] for e in kept] == ["telegram", "slack"]


def test_catalog_no_filter_when_none(tmp_path):
    from hermes_cli import web_server as ws
    entries = [{"id": "telegram"}, {"id": "signal"}]
    assert ws._apply_platform_visibility(entries, None) == entries


def test_gateway_get_connected_excludes_hidden(monkeypatch):
    from gateway.config import GatewayConfig, PlatformConfig, Platform
    cfg = GatewayConfig()
    cfg.platforms = {
        Platform.TELEGRAM: PlatformConfig(enabled=True, token="t"),
        Platform.SIGNAL: PlatformConfig(enabled=True, token="t"),
    }
    # Force the visible set to exclude signal
    monkeypatch.setattr("gateway.config._brand_visible_platform_ids", lambda: {"telegram"})
    connected = cfg.get_connected_platforms()
    assert Platform.TELEGRAM in connected and Platform.SIGNAL not in connected


def test_gateway_no_filter_when_none(monkeypatch):
    from gateway.config import GatewayConfig, PlatformConfig, Platform
    cfg = GatewayConfig()
    cfg.platforms = {Platform.TELEGRAM: PlatformConfig(enabled=True, token="t"),
                     Platform.SIGNAL: PlatformConfig(enabled=True, token="t")}
    monkeypatch.setattr("gateway.config._brand_visible_platform_ids", lambda: None)
    connected = cfg.get_connected_platforms()
    assert Platform.TELEGRAM in connected and Platform.SIGNAL in connected
