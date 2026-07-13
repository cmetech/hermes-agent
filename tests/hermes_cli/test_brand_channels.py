import json, os
from pathlib import Path
import pytest
from hermes_cli import brand_config as bc

def _brand_root(tmp_path, slug, channels=None):
    (tmp_path / "brands").mkdir(parents=True, exist_ok=True)
    desc = {"slug": slug, "curation": {"channels": {"allow": channels}} if channels is not None else {}}
    (tmp_path / "brands" / f"{slug}.json").write_text(json.dumps(desc))
    return tmp_path

def test_resolve_active_brand_env_wins(tmp_path, monkeypatch):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "active").write_text("loop24\n")
    monkeypatch.setenv("OTTO_BRAND", "otto")
    assert bc.resolve_active_brand(root=tmp_path) == "otto"

def test_resolve_active_brand_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "active").write_text("loop24\n")
    assert bc.resolve_active_brand(root=tmp_path) == "loop24"

def test_resolve_active_brand_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    assert bc.resolve_active_brand(root=tmp_path) == "otto"

def test_get_channel_allowlist_returns_set(tmp_path):
    root = _brand_root(tmp_path, "loop24", ["telegram", "slack"])
    assert bc.get_channel_allowlist("loop24", root=root) == {"telegram", "slack"}

def test_get_channel_allowlist_empty_is_none(tmp_path):
    root = _brand_root(tmp_path, "x", [])           # empty allow → None (show all)
    assert bc.get_channel_allowlist("x", root=root) is None

def test_get_channel_allowlist_missing_is_none(tmp_path):
    root = _brand_root(tmp_path, "y", None)          # no channels field → None
    assert bc.get_channel_allowlist("y", root=root) is None

def test_get_channel_allowlist_missing_file_is_none(tmp_path):
    assert bc.get_channel_allowlist("nope", root=tmp_path) is None   # never raises

def test_get_channel_allowlist_malformed_non_dict_json_is_none(tmp_path):
    (tmp_path / "brands").mkdir(parents=True, exist_ok=True)
    (tmp_path / "brands" / "bad.json").write_text('["telegram"]')   # top-level list, not an object
    assert bc.get_channel_allowlist("bad", root=tmp_path) is None    # must not raise

def test_get_channel_allowlist_non_list_allow_is_none(tmp_path):
    (tmp_path / "brands").mkdir(parents=True, exist_ok=True)
    (tmp_path / "brands" / "z.json").write_text(json.dumps(
        {"slug": "z", "curation": {"channels": {"allow": "telegram"}}}))  # string, not a list
    assert bc.get_channel_allowlist("z", root=tmp_path) is None

def test_visible_config_override_wins(tmp_path, monkeypatch):
    root = _brand_root(tmp_path, "otto", ["telegram"])
    (tmp_path / "brand").mkdir(); (tmp_path / "brand" / "active").write_text("otto")
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    cfg = {"messaging": {"allowed_platforms": ["slack", "discord"]}}
    assert bc.visible_platform_ids(cfg, root=root) == {"slack", "discord"}

def test_visible_descriptor_when_no_override(tmp_path, monkeypatch):
    root = _brand_root(tmp_path, "otto", ["telegram", "email"])
    (tmp_path / "brand").mkdir(); (tmp_path / "brand" / "active").write_text("otto")
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    assert bc.visible_platform_ids({}, root=root) == {"telegram", "email"}

def test_visible_none_when_no_allowlist(tmp_path, monkeypatch):
    root = _brand_root(tmp_path, "otto", None)
    (tmp_path / "brand").mkdir(); (tmp_path / "brand" / "active").write_text("otto")
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    assert bc.visible_platform_ids({}, root=root) is None

def test_visible_scalar_override_falls_through_not_empty(tmp_path, monkeypatch):
    root = _brand_root(tmp_path, "otto", ["telegram"])
    (tmp_path / "brand").mkdir(exist_ok=True); (tmp_path / "brand" / "active").write_text("otto")
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    cfg = {"messaging": {"allowed_platforms": "telegram"}}  # scalar, NOT a list
    # must ignore the malformed override and fall through to the descriptor allowlist,
    # NOT return a char-set / empty set (fail OPEN, never hide everything)
    assert bc.visible_platform_ids(cfg, root=root) == {"telegram"}
