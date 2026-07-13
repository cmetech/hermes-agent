"""Capability-staging seam: resolver + manifest-driven staging (P2b)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import capability_staging as cs


def _write(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def make_bundle(root: Path, version: str = "0.2.0", requires_env=None) -> Path:
    """A minimal on-disk capability bundle matching the ericsson repo layout."""
    b = root / "bundle"
    manifest = {
        "name": "ericsson", "version": version, "description": "t",
        "requiresEnv": requires_env if requires_env is not None else {},
        "disabledByDefault": {"skills": ["workflow-orchestrator"],
                               "toolsets": ["ericsson-jira"]},
        "skills": ["skills/ericsson/workflow-orchestrator"],
        "plugins": ["plugins/ericsson-jira"],
        "mcpServers": "mcp/mcp-servers.yaml",
        "mcpLocal": ["mcp/outlook-mcp"],
        "workflows": ["workflows/my-tickets-summary.yml"],
        "personas": [], "env": [],
    }
    _write(b / "sets/ericsson.json", json.dumps(manifest))
    _write(b / "skills/ericsson/workflow-orchestrator/SKILL.md", "---\nname: workflow-orchestrator\n---\n")
    _write(b / "skills/ericsson/workflow-orchestrator/scripts/workflow_ctl.py", "# ctl")
    _write(b / "plugins/ericsson-jira/plugin.yaml", "name: ericsson-jira\n")
    _write(b / "plugins/ericsson-jira/__init__.py", "")
    _write(b / "mcp/outlook-mcp/run_server.py", "# server")
    _write(b / "mcp/mcp-servers.yaml",
           "mcp_servers:\n  outlook:\n    command: python\n    args: [\"${CAPABILITY_DIR}/outlook-mcp/run_server.py\"]\n")
    _write(b / "workflows/my-tickets-summary.yml", "name: my-tickets-summary\n")
    return b


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def fake_config(monkeypatch):
    """In-memory config.yaml stand-in for load_config/save_config."""
    store = {"config": {}}
    saved = {"count": 0}

    def load_config():
        return json.loads(json.dumps(store["config"]))

    def save_config(cfg, **kw):
        store["config"] = cfg
        saved["count"] += 1

    import hermes_cli.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", load_config)
    monkeypatch.setattr(config_mod, "save_config", save_config)
    return store, saved


def test_resolver_env_override(tmp_path, home, monkeypatch):
    b = make_bundle(tmp_path)
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    got = cs.resolve_capability_bundle("ericsson", "https://example/nope.git", home)
    assert got == b


def test_resolver_env_override_wrong_set(tmp_path, home, monkeypatch):
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(tmp_path))  # no sets/ericsson.json
    assert cs.resolve_capability_bundle("ericsson", "https://example/e.git", home) is None


def test_resolver_git_clone_and_cache(tmp_path, home, monkeypatch):
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    src = make_bundle(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-C", str(src), "commit", "-qm", "init"], check=True)
    got = cs.resolve_capability_bundle("ericsson", str(src), home)  # local path URL
    assert got is not None and (got / "sets/ericsson.json").exists()
    assert str(got).startswith(str(home))          # cached under home
    # second resolve reuses the cache (pull path) and still succeeds
    again = cs.resolve_capability_bundle("ericsson", str(src), home)
    assert again == got


def test_resolver_bad_url_no_cache(home, monkeypatch):
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    assert cs.resolve_capability_bundle("ericsson", "/nonexistent/repo.git", home) is None


def test_stage_bundle_full(tmp_path, home, fake_config):
    store, saved = fake_config
    b = make_bundle(tmp_path)
    changed = cs.stage_bundle(b, "ericsson", home)
    assert changed is True
    assert (home / "skills/ericsson/workflow-orchestrator/SKILL.md").exists()
    assert (home / "skills/ericsson/workflow-orchestrator/scripts/workflow_ctl.py").exists()
    assert (home / "plugins/ericsson-jira/plugin.yaml").exists()
    assert (home / "plugins/outlook-mcp/run_server.py").exists()
    assert (home / "workflows/my-tickets-summary.yml").exists()
    cfg = store["config"]
    outlook = cfg["mcp_servers"]["outlook"]
    assert outlook["args"][0] == str(home / "plugins") + "/outlook-mcp/run_server.py"
    assert "workflow-orchestrator" in cfg["skills"]["disabled"]
    assert "ericsson-jira" in cfg["disabled_toolsets"]
    assert "ericsson-jira" in cfg["plugins"]["enabled"]


def test_stage_bundle_never_clobbers_user_mcp(tmp_path, home, fake_config):
    store, _ = fake_config
    store["config"] = {"mcp_servers": {"outlook": {"command": "custom"}}}
    b = make_bundle(tmp_path)
    cs.stage_bundle(b, "ericsson", home)
    assert store["config"]["mcp_servers"]["outlook"] == {"command": "custom"}


def test_stage_brand_capabilities_end_to_end(tmp_path, home, fake_config, monkeypatch):
    b = make_bundle(tmp_path, requires_env={"ERICSSON_ENV": "1"})
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    monkeypatch.setenv("ERICSSON_ENV", "1")
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({
        "slug": "otto",
        "capabilitySets": ["ericsson"],
        "capabilitySources": {"ericsson": "https://example/e.git"},
    }))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)
    assert (home / "skills/ericsson/workflow-orchestrator/SKILL.md").exists()
    stamp = json.loads((home / cs.STAGING_MANIFEST).read_text())
    assert stamp["sets"]["ericsson"]["version"] == "0.2.0"
    # idempotent second run: nothing re-copied (marker: delete a staged file; version match -> skip)
    (home / "workflows/my-tickets-summary.yml").unlink()
    cs.stage_brand_capabilities(home, root=fake_root)
    assert not (home / "workflows/my-tickets-summary.yml").exists()
    # version bump -> re-stages
    m = json.loads((b / "sets/ericsson.json").read_text())
    m["version"] = "0.3.0"
    (b / "sets/ericsson.json").write_text(json.dumps(m))
    cs.stage_brand_capabilities(home, root=fake_root)
    assert (home / "workflows/my-tickets-summary.yml").exists()


def test_requires_env_gate(tmp_path, home, fake_config, monkeypatch):
    b = make_bundle(tmp_path, requires_env={"ERICSSON_ENV": "1"})
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    monkeypatch.delenv("ERICSSON_ENV", raising=False)
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({
        "slug": "otto", "capabilitySets": ["ericsson"],
        "capabilitySources": {"ericsson": "u"},
    }))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)
    assert not (home / "skills").exists()          # gated: nothing staged


def test_empty_sets_still_noop(home, tmp_path, monkeypatch):
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({"slug": "otto", "capabilitySets": []}))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)   # must not raise, must not create anything
    assert list(home.iterdir()) == []
