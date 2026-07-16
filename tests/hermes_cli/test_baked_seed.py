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
        "mcp_servers:\n"
        "  outlook:\n"
        "    command: python\n"
        "    args: [\"${CAPABILITY_DIR}/outlook-mcp/run_server.py\"]\n"
        "  glean:\n"
        "    enabled: false\n"
        "    url: https://default.example.test/mcp\n"
        "    headers:\n"
        "      Authorization: \"Bearer ${GLEAN_API_TOKEN}\"\n"
    )
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
    glean = cfg["mcp_servers"]["glean"]
    assert glean["enabled"] is False
    assert glean["url"] == "https://default.example.test/mcp"
    assert (home / "workflows/w.yml").exists()

def test_seed_never_clobbers_user_mcp(tmp_path, fake_repo, fake_config):
    fake_config["cfg"] = {"mcp_servers": {"outlook": {"command": "mine"}}}
    home = tmp_path / "home"; home.mkdir()
    cs.seed_baked_capabilities(home)
    assert fake_config["cfg"]["mcp_servers"]["outlook"] == {"command": "mine"}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("current_url", [None, "", "   "])
def test_seed_backfills_only_missing_or_blank_mcp_url(
    tmp_path, fake_repo, fake_config, current_url, enabled
):
    glean = {"enabled": enabled, "headers": {"X-User": "preserve"}}
    if current_url is not None:
        glean["url"] = current_url
    fake_config["cfg"] = {"mcp_servers": {"glean": glean}}

    cs.seed_baked_capabilities(tmp_path / "home")

    saved = fake_config["cfg"]["mcp_servers"]["glean"]
    assert saved["url"] == "https://default.example.test/mcp"
    assert saved["enabled"] is enabled
    assert saved["headers"] == {"X-User": "preserve"}


def test_seed_preserves_custom_mcp_url_and_absent_enabled(
    tmp_path, fake_repo, fake_config
):
    fake_config["cfg"] = {
        "mcp_servers": {
            "glean": {
                "url": "https://custom.example.test/mcp",
                "headers": {"X-User": "preserve"},
            }
        }
    }

    cs.seed_baked_capabilities(tmp_path / "home")
    first = json.loads(json.dumps(fake_config["cfg"]))
    cs.seed_baked_capabilities(tmp_path / "home")

    saved = fake_config["cfg"]["mcp_servers"]["glean"]
    assert saved["url"] == "https://custom.example.test/mcp"
    assert "enabled" not in saved
    assert saved["headers"] == {"X-User": "preserve"}
    assert fake_config["cfg"] == first

def test_seed_is_fail_safe_without_capabilities_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_repo_root", lambda: tmp_path / "nope")
    cs.seed_baked_capabilities(tmp_path / "home")   # must not raise
