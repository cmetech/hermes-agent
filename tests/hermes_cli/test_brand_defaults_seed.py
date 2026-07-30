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
    # Seeding now has two sources — the descriptor's curation lists and the
    # capability manifests' configDefaults. This test owns the descriptor path,
    # so the manifest side is stubbed empty; otherwise the shipped Ericsson
    # browser defaults would seed and "no-op" would no longer mean anything.
    _brand(monkeypatch, {"curation": {"skills": {"disabledByDefault": []}}})
    monkeypatch.setattr(cs, "_capability_config_defaults", lambda root=None: {})
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


# ---------------------------------------------------------------------------
# configDefaults from the vendored capability manifests
# ---------------------------------------------------------------------------
# Reaching an internal site used to require hand-editing YAML: a
# browser.profiles.enrolled block, correctly indented, with trusted_origins as a
# genuine LIST. `config set` cannot create a list, and getting it wrong is
# silent -- browser_profiles.py fails closed on a non-list, so the profile
# trusts nothing and every later step fails with a private-address block that
# looks like an mTLS problem. Seeding removes that step.
#
# Design: docs/plans/2026-07-26-enrolled-browser-profile-seeding-design.md

ENROLLED_DEFAULTS = {
    "browser": {
        # Seeded EMPTY, not omitted: the Settings switch only renders for a
        # key present in the config, and empty is what reads as "no profile".
        "default_profile": "",
        "profiles": {
            "enrolled": {
                "cdp_port": 9333,
                "executable": "auto",
                "headed": True,
                "kind": "enrolled",
                "trusted_origins": ["https://*.ericsson.com", "https://*.ericsson.net"],
                "user_data_dir": "${HERMES_HOME}/browser-profiles/enrolled",
            }
        }
    }
}


def _capability_defaults(monkeypatch, defaults):
    monkeypatch.setattr(cs, "_capability_config_defaults", lambda root=None: defaults)


def test_seeds_the_enrolled_profile_from_the_capability_manifest(tmp_path, monkeypatch, fake_config):
    _brand(monkeypatch, {})
    _capability_defaults(monkeypatch, ENROLLED_DEFAULTS)
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)

    profile = fake_config["cfg"]["browser"]["profiles"]["enrolled"]
    assert profile["kind"] == "enrolled"
    assert profile["cdp_port"] == 9333
    # A LIST, not a comma-joined string -- the exact mistake the runbook warns
    # about, and the one that makes the profile trust nothing.
    assert profile["trusted_origins"] == ["https://*.ericsson.com", "https://*.ericsson.net"]
    assert isinstance(profile["trusted_origins"], list)


def test_seeds_the_activation_key_empty_so_the_toggle_renders(tmp_path, monkeypatch, fake_config):
    """Seeded but inert — and the key must EXIST, or the switch never appears.

    v4.2.0 shipped without this and the toggle was unreachable.
    sectionFieldEntries omits any key absent from both the served schema and the
    config, and browser.default_profile is in neither: not in DEFAULT_CONFIG (so
    not in CONFIG_SCHEMA), and deliberately not seeded. The result was a feature
    that could only be enabled by the hand-editing it existed to remove.

    Empty is what keeps it inert: default_profile_name() returns `name or None`,
    so "" resolves to no profile. Assert emptiness, NOT absence -- absence is
    what broke it.
    """
    _brand(monkeypatch, {})
    _capability_defaults(monkeypatch, ENROLLED_DEFAULTS)
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)

    browser = fake_config["cfg"]["browser"]
    assert "default_profile" in browser, "the toggle cannot render without the key"
    assert browser["default_profile"] == "", "seeded active would route all browsing through the corporate profile"


def test_never_overwrites_a_value_the_user_already_set(tmp_path, monkeypatch, fake_config):
    fake_config["cfg"] = {
        "browser": {"profiles": {"enrolled": {"trusted_origins": ["https://mine.example"]}}}
    }
    _brand(monkeypatch, {})
    _capability_defaults(monkeypatch, ENROLLED_DEFAULTS)
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)

    profile = fake_config["cfg"]["browser"]["profiles"]["enrolled"]
    assert profile["trusted_origins"] == ["https://mine.example"]
    # Untouched keys still seed alongside it.
    assert profile["kind"] == "enrolled"


def test_seed_is_once_so_a_trimmed_origin_list_stays_trimmed(tmp_path, monkeypatch, fake_config):
    """This is the reason seeding reuses seed_brand_defaults' semantics.

    trusted_origins governs private-network access. A user who deliberately
    removes an origin must not have it restored on the next launch.
    """
    _brand(monkeypatch, {})
    _capability_defaults(monkeypatch, ENROLLED_DEFAULTS)
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)
    fake_config["cfg"]["browser"]["profiles"]["enrolled"]["trusted_origins"] = []

    cs.seed_brand_defaults(home)

    assert fake_config["cfg"]["browser"]["profiles"]["enrolled"]["trusted_origins"] == []


def test_absent_capability_defaults_seed_nothing(tmp_path, monkeypatch, fake_config):
    _brand(monkeypatch, {})
    _capability_defaults(monkeypatch, {})
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)

    assert fake_config["cfg"] == {}
    assert not (home / cs.BRAND_DEFAULTS_MARKER).exists()


def test_fail_safe_when_reading_the_manifest_raises(tmp_path, monkeypatch, fake_config):
    def boom(root=None):
        raise RuntimeError("unreadable manifest")

    _brand(monkeypatch, {})
    monkeypatch.setattr(cs, "_capability_config_defaults", boom)
    home = tmp_path / "home"; home.mkdir()

    cs.seed_brand_defaults(home)  # must not raise

    assert fake_config["cfg"] == {}


def test_the_vendored_ericsson_manifest_really_carries_the_enrolled_profile():
    """The tests above stub the manifest; this one reads the shipped file.

    Without it a typo in capabilities/ericsson.json would leave every install
    unseeded while the whole suite stayed green.
    """
    defaults = cs._capability_config_defaults()

    profile = defaults["browser"]["profiles"]["enrolled"]
    assert profile["kind"] == "enrolled"
    assert profile["executable"] == "auto"
    assert profile["headed"] is True
    assert profile["trusted_origins"] == ["https://*.ericsson.com", "https://*.ericsson.net"]
    # NOT /browser connect's port (BP-1): sharing it lets a plain
    # `/browser connect` adopt the corporate browser as the process-global CDP
    # endpoint, so every untrusted page would run inside the user's live SSO
    # session.
    from hermes_cli.browser_connect import DEFAULT_BROWSER_CDP_PORT
    assert profile["cdp_port"] != DEFAULT_BROWSER_CDP_PORT
    # Activation is the user's decision, made through the Settings toggle.
    assert defaults["browser"]["default_profile"] == ""
