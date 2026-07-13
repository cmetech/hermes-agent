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
