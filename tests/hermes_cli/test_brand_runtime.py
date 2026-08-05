import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import brand_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_KEYS = [
    "schemaVersion", "slug", "displayName", "appId", "scheme",
    "schemes", "homeDir", "releasesRepo", "updateCommand", "gateway",
]


def _real_brand_descriptors():
    for descriptor_path in sorted((REPO_ROOT / "brands").glob("*.json")):
        if descriptor_path.name == "schema.json" or descriptor_path.name.startswith("_"):
            continue
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if isinstance(descriptor.get("slug"), str):
            yield descriptor_path, descriptor


def test_every_real_brand_exposes_ungated_ericsson_onboarding():
    for descriptor_path, descriptor in _real_brand_descriptors():
        # Only the obsolete Ericsson set gate is forbidden. Generic
        # capabilityRequiresEnv support remains valid for other future sets.
        assert not descriptor.get("capabilityRequiresEnv", {}).get("ericsson"), descriptor_path
        skill_curation = descriptor.get("curation", {}).get("skills", {})
        assert "onboard-ericsson-capabilities" not in skill_curation.get("exclude", []), descriptor_path
        assert "onboard-ericsson-capabilities" not in skill_curation.get("disabledByDefault", []), descriptor_path


def test_capability_env_injection_comment_has_no_obsolete_ericsson_gate():
    config_source = (REPO_ROOT / "hermes_cli" / "config.py").read_text(encoding="utf-8")
    assert "e.g. ERICSSON_ENV" not in config_source


def test_bundled_ericsson_manifest_lists_onboarding_skill_exactly_once():
    manifest = json.loads(
        (REPO_ROOT / "capabilities" / "ericsson.json").read_text(encoding="utf-8")
    )
    skill = "skills/ericsson/onboard-ericsson-capabilities"
    assert manifest["skills"].count(skill) == 1
    assert (REPO_ROOT / skill / "SKILL.md").is_file()


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
    # Pins the Plan-6 "empty sets -> nothing staged" invariant against a FIXTURE
    # brand — the live otto/loop24 descriptors now ship a non-empty capabilitySets
    # (["ericsson"]) with a real https source, so they can no longer serve as the
    # fixture here without risking a live network resolve during pytest.
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    root = tmp_path / "repo"
    (root / "brands").mkdir(parents=True)
    (root / "brands" / "empty.json").write_text(
        json.dumps({"slug": "empty", "capabilitySets": [], "personaSets": []}),
        encoding="utf-8",
    )
    (root / "brand").mkdir(parents=True, exist_ok=True)
    (root / "brand" / "active").write_text("empty", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.stage_brand_capabilities(home, root=root)
    assert not (home / capability_staging.STAGING_MANIFEST).exists()
    assert list(home.iterdir()) == []


def test_nonempty_sets_unresolvable_source_is_noop(tmp_path, monkeypatch):
    # A fixture brand WITH a capability set whose source is an unresolvable LOCAL
    # path (never a network URL): `git clone` against a nonexistent local path
    # fails instantly, so this exercises the "resolver returns None" fault-safe
    # path with zero network I/O, even under GIT_ALLOW_PROTOCOL=file.
    monkeypatch.delenv("OTTO_BRAND", raising=False)
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    monkeypatch.delenv("ERICSSON_ENV", raising=False)
    root = tmp_path / "repo"
    (root / "brands").mkdir(parents=True)
    (root / "brands" / "x.json").write_text(
        json.dumps({
            "slug": "x",
            "capabilitySets": ["ericsson"],
            "capabilitySources": {"ericsson": "/nonexistent/local/path.git"},
        }),
        encoding="utf-8",
    )
    (root / "brand").mkdir(parents=True, exist_ok=True)
    (root / "brand" / "active").write_text("x", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.stage_brand_capabilities(home, root=root)  # must not raise, no network
    assert not (home / capability_staging.STAGING_MANIFEST).exists()
    assert not (home / "skills").exists()
    assert not (home / "plugins").exists()


def test_resolve_capability_bundle_stub_returns_none(tmp_path):
    # With no source_url, resolver returns None (no cache to fall back to)
    assert capability_staging.resolve_capability_bundle("ericsson", None, tmp_path) is None


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


def test_descriptor_env_gate_blocks_before_resolve(tmp_path, monkeypatch):
    # A descriptor-level capabilityRequiresEnv gate must block BEFORE any
    # resolve_capability_bundle call (i.e. before any network I/O), not just
    # after resolution via the in-repo manifest's requiresEnv.
    monkeypatch.delenv("ERICSSON_ENV", raising=False)
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    root = tmp_path / "repo"
    (root / "brands").mkdir(parents=True)
    (root / "brands" / "g.json").write_text(
        json.dumps({
            "slug": "g",
            "capabilitySets": ["ericsson"],
            "capabilitySources": {"ericsson": "https://example.invalid/x.git"},
            "capabilityRequiresEnv": {"ericsson": {"ERICSSON_ENV": "1"}},
        }),
        encoding="utf-8",
    )
    (root / "brand").mkdir(parents=True, exist_ok=True)
    (root / "brand" / "active").write_text("g", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    def _boom(*a, **k):
        raise AssertionError("resolve called")

    monkeypatch.setattr(capability_staging, "resolve_capability_bundle", _boom)
    capability_staging.stage_brand_capabilities(home, root=root)  # must not raise
    assert list(home.iterdir()) == []

    # gate met -> resolve IS called (returning None here still must not crash)
    monkeypatch.setenv("ERICSSON_ENV", "1")
    monkeypatch.setattr(capability_staging, "resolve_capability_bundle", lambda *a, **k: None)
    capability_staging.stage_brand_capabilities(home, root=root)  # must not raise
    assert not (home / capability_staging.STAGING_MANIFEST).exists()


def test_run_brand_startup_is_fault_safe(tmp_path, monkeypatch):
    # Point at a non-existent brand so load_brand raises internally; run_brand_startup
    # must swallow it and not propagate.
    monkeypatch.setenv("OTTO_BRAND", "does-not-exist")
    capability_staging.run_brand_startup(tmp_path)  # must not raise


def test_run_brand_startup_writes_brand_json(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTO_BRAND", "otto")
    # otto.json now ships a real capabilitySets entry (ericsson) with a live
    # https source; force the resolver's env-override path (checked before any
    # source_url/git-clone logic) so this never attempts a network git clone.
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(tmp_path / "no-such-capability-source"))
    home = tmp_path / "home"
    home.mkdir()
    capability_staging.run_brand_startup(home)
    assert (home / "brand.json").exists()


def test_cli_startup_writes_brand_json(tmp_path):
    # Drive the real CLI once against a temp home; brand.json must appear.
    # `hermes` always exists (the branded alias routes to the same main()).
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path)
    env.pop("OTTO_BRAND", None)  # let the brand/active marker decide (otto on this branch)
    # The active brand now ships a real capabilitySets entry with a live https
    # source; force the resolver's env-override path so this subprocess never
    # attempts a network git clone.
    env["OTTO_CAPABILITY_SOURCE"] = str(tmp_path / "no-such-capability-source")
    hermes = Path(sys.executable).parent / "hermes"
    assert hermes.exists(), f"expected {hermes} in the venv"
    # v0.20.0 made `--version` an everywhere fast path (_startup_fast) that
    # exits before main()'s brand-startup seam — by design, brand.json is
    # skipped on fast paths. Drive `--help` instead: it reaches main() (and
    # therefore run_brand_startup) before argparse prints help and exits.
    subprocess.run([str(hermes), "--help"], env=env, capture_output=True, timeout=120)
    assert (tmp_path / "brand.json").exists()
    data = json.loads((tmp_path / "brand.json").read_text())
    assert data["schemes"][-1] == "hermes"
