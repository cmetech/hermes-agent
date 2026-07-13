import json
from pathlib import Path

import pytest

from hermes_cli import brand_config

CANONICAL_KEYS = [
    "schemaVersion", "slug", "displayName", "appId", "scheme",
    "schemes", "homeDir", "releasesRepo", "updateCommand", "gateway",
]


def test_brand_json_payload_otto_keys_and_values():
    p = brand_config.brand_json_payload("otto")
    assert list(p.keys()) == CANONICAL_KEYS
    assert p["schemaVersion"] == 1
    assert p["slug"] == "otto"
    assert p["displayName"] == "OTTO"
    assert p["appId"] == "io.cmetech.otto"
    assert p["scheme"] == "otto"
    assert p["schemes"] == ["otto", "hermes"]
    assert p["homeDir"] == ".otto"
    assert p["releasesRepo"] == "cmetech/otto"
    assert p["updateCommand"] == "otto update"
    assert p["gateway"] == "otto"


def test_brand_json_payload_loop24():
    p = brand_config.brand_json_payload("loop24")
    assert p["slug"] == "loop24"
    assert p["scheme"] == "loop24"
    assert p["schemes"] == ["loop24", "hermes"]
    assert p["homeDir"] == ".loop24"
    assert p["releasesRepo"] == "cmetech/loop24"


def test_write_brand_json_writes_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTO_BRAND", "otto")
    home = tmp_path / "home"
    home.mkdir()
    brand_config.write_brand_json(home)
    target = home / "brand.json"
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["slug"] == "otto"
    assert data["schemes"] == ["otto", "hermes"]
    # write-if-changed: a second write with identical content leaves mtime untouched
    first_mtime = target.stat().st_mtime_ns
    brand_config.write_brand_json(home)
    assert target.stat().st_mtime_ns == first_mtime


from hermes_cli import capability_staging


def test_stage_empty_sets_is_noop(tmp_path, monkeypatch):
    # otto.json ships empty capabilitySets/personaSets → nothing staged, no manifest.
    monkeypatch.setenv("OTTO_BRAND", "otto")
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.stage_brand_capabilities(home)
    assert not (home / capability_staging.STAGING_MANIFEST).exists()


def test_resolve_capability_bundle_stub_returns_none():
    assert capability_staging.resolve_capability_bundle("ericsson") is None


def test_stage_nonempty_sets_no_resolver_no_crash(tmp_path, monkeypatch):
    # A fixture brand WITH a capability set: the stub resolver returns None, so nothing
    # stages and no manifest is written — but it must not raise.
    root = tmp_path / "repo"
    (root / "brands").mkdir(parents=True)
    (root / "brands" / "fixture-cap.json").write_text(
        json.dumps({"slug": "fixture-cap", "capabilitySets": ["ericsson"], "personaSets": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OTTO_BRAND", "fixture-cap")
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.stage_brand_capabilities(home, root=root)  # must not raise
    assert not (home / capability_staging.STAGING_MANIFEST).exists()


def test_run_brand_startup_is_fault_safe(tmp_path, monkeypatch):
    # Point at a non-existent brand so load_brand raises internally; run_brand_startup
    # must swallow it and not propagate.
    monkeypatch.setenv("OTTO_BRAND", "does-not-exist")
    capability_staging.run_brand_startup(tmp_path)  # must not raise


def test_run_brand_startup_writes_brand_json(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTO_BRAND", "otto")
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.run_brand_startup(home)
    assert (home / "brand.json").exists()
