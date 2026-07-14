import json, sys
from pathlib import Path
import pytest
from hermes_cli import capability_staging as cs

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    # a fake hermes-agent repo root with vendored capability content
    root = tmp_path / "repo"; (root / "capabilities/workflows").mkdir(parents=True)
    (root / "capabilities/ericsson.json").write_text(json.dumps({
        "name": "ericsson", "mcpServersFile": "mcp-servers.yaml",
        "workflows": ["capabilities/workflows/w.yml"]}))
    (root / "capabilities/mcp-servers.yaml").write_text(
        "mcp_servers:\n  outlook:\n    command: python\n    args: [\"${CAPABILITY_DIR}/outlook-mcp/run_server.py\"]\n")
    (root / "capabilities/workflows/w.yml").write_text("name: w\n")
    monkeypatch.setattr(cs, "_repo_root", lambda: root)
    monkeypatch.setattr("hermes_cli.plugins.get_bundled_plugins_dir", lambda: root / "plugins")
    return root

@pytest.fixture
def fake_config(monkeypatch):
    store = {"cfg": {}}
    import hermes_cli.config as c
    monkeypatch.setattr(c, "load_config", lambda: json.loads(json.dumps(store["cfg"])))
    monkeypatch.setattr(c, "save_config", lambda cfg, **k: store.__setitem__("cfg", cfg))
    return store

def test_seed_mcp_and_workflows(tmp_path, fake_repo, fake_config):
    home = tmp_path / "home"; home.mkdir()
    cs.seed_baked_capabilities(home)
    cfg = fake_config["cfg"]
    assert cfg["mcp_servers"]["outlook"]["args"][0] == str(fake_repo / "plugins") + "/outlook-mcp/run_server.py"
    assert (home / "workflows/w.yml").exists()

def test_seed_never_clobbers_user_mcp(tmp_path, fake_repo, fake_config):
    fake_config["cfg"] = {"mcp_servers": {"outlook": {"command": "mine"}}}
    home = tmp_path / "home"; home.mkdir()
    cs.seed_baked_capabilities(home)
    assert fake_config["cfg"]["mcp_servers"]["outlook"] == {"command": "mine"}

def test_seed_is_fail_safe_without_capabilities_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_repo_root", lambda: tmp_path / "nope")
    cs.seed_baked_capabilities(tmp_path / "home")   # must not raise
