"""Tests for capability_staging.seed_brand_defaults — seed-once disabled-by-default.

The active brand descriptor's `curation.skills.disabledByDefault` /
`curation.tools.disabledByDefault` are seeded into config.yaml ONCE (tracked by a
marker), so a user who later re-enables a seeded skill/toolset is NOT re-disabled
on the next startup. Fail-safe: never raises.
"""
import json

import pytest

from hermes_cli import capability_staging as cs


@pytest.fixture
def fake_config(monkeypatch):
    store = {"cfg": {}}
    import hermes_cli.config as c
    monkeypatch.setattr(c, "load_config", lambda: json.loads(json.dumps(store["cfg"])))
    monkeypatch.setattr(c, "save_config", lambda cfg, **k: store.__setitem__("cfg", cfg))
    return store


def _brand(monkeypatch, descriptor):
    monkeypatch.setattr(cs.brand_config, "resolve_active_brand", lambda root=None: "testbrand")
    monkeypatch.setattr(cs.brand_config, "load_brand", lambda slug, root=None: descriptor)


def test_seeds_disabled_by_default_skills_and_toolsets(tmp_path, monkeypatch, fake_config):
    _brand(monkeypatch, {"curation": {
        "skills": {"disabledByDefault": ["gateway-toolcall-parity"]},
        "tools": {"disabledByDefault": ["homeassistant"]},
    }})
    home = tmp_path / "home"; home.mkdir()
    cs.seed_brand_defaults(home)
    cfg = fake_config["cfg"]
    assert "gateway-toolcall-parity" in cfg["skills"]["disabled"]
    assert "homeassistant" in cfg["disabled_toolsets"]
    assert (home / cs.BRAND_DEFAULTS_MARKER).exists()


def test_seed_is_once_respects_user_reenable(tmp_path, monkeypatch, fake_config):
    _brand(monkeypatch, {"curation": {
        "skills": {"disabledByDefault": ["gateway-toolcall-parity"]}}})
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)                       # first run → seeded
    assert "gateway-toolcall-parity" in fake_config["cfg"]["skills"]["disabled"]

    # user enables it (removes from the disabled list)
    fake_config["cfg"]["skills"]["disabled"] = []
    cs.seed_brand_defaults(home)                       # second run → must NOT re-disable
    assert "gateway-toolcall-parity" not in fake_config["cfg"]["skills"]["disabled"]


def test_does_not_clobber_existing_user_disabled(tmp_path, monkeypatch, fake_config):
    fake_config["cfg"] = {"skills": {"disabled": ["some-other-skill"]}}
    _brand(monkeypatch, {"curation": {
        "skills": {"disabledByDefault": ["gateway-toolcall-parity"]}}})
    home = tmp_path / "home"; home.mkdir()
    cs.seed_brand_defaults(home)
    disabled = fake_config["cfg"]["skills"]["disabled"]
    assert "some-other-skill" in disabled and "gateway-toolcall-parity" in disabled


def test_empty_descriptor_is_noop(tmp_path, monkeypatch, fake_config):
    _brand(monkeypatch, {"curation": {"skills": {"disabledByDefault": []}}})
    home = tmp_path / "home"; home.mkdir()
    cs.seed_brand_defaults(home)
    assert fake_config["cfg"] == {}
    assert not (home / cs.BRAND_DEFAULTS_MARKER).exists()


def test_fail_safe_on_bad_descriptor(tmp_path, monkeypatch, fake_config):
    def boom(slug, root=None):
        raise RuntimeError("no descriptor")
    monkeypatch.setattr(cs.brand_config, "resolve_active_brand", lambda root=None: "testbrand")
    monkeypatch.setattr(cs.brand_config, "load_brand", boom)
    cs.seed_brand_defaults(tmp_path / "home")          # must not raise
