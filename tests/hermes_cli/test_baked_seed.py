import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from hermes_cli import capability_staging as cs
from plugins.workflow.schema import load_workflow
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest


REPO_ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    # a fake hermes-agent repo root with vendored capability content
    root = tmp_path / "repo"
    package = root / "capabilities/workflow-packages/ericsson"
    (package / "workflows").mkdir(parents=True)
    (package / "commands").mkdir()
    workflow = package / "workflows/w.yaml"
    workflow.write_text(
        "name: w\ndescription: baked package\nnodes:\n"
        "  - id: collect\n    command: collect\n"
    )
    (package / "commands/collect.md").write_text("Collect baked data.\n")
    digest = compute_package_digest(load_workflow(workflow)).sha256
    (package / "digests.json").write_text(json.dumps({
        "schemaVersion": 1,
        "packages": {"w": digest},
    }))
    (root / "capabilities/ericsson.json").write_text(json.dumps({
        "name": "ericsson", "mcpServersFile": "mcp-servers.yaml",
        "plugins": ["plugins/workflow"],
        "workflowPackages": [{
            "path": "capabilities/workflow-packages/ericsson",
            "digestManifest": "capabilities/workflow-packages/ericsson/digests.json",
        }]}))
    (root / "capabilities/ericsson-vendored-paths.json").write_text(
        json.dumps(["capabilities/workflow-packages/ericsson"])
    )
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
    (root / "plugins/workflow").mkdir(parents=True)
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

def test_seed_mcp_and_workflow_packages(tmp_path, fake_repo, fake_config):
    home = tmp_path / "home"; home.mkdir()
    cs.seed_baked_capabilities(home)
    cfg = fake_config["cfg"]
    assert cfg["mcp_servers"]["outlook"]["args"][0] == str(fake_repo / "plugins") + "/outlook-mcp/run_server.py"
    glean = cfg["mcp_servers"]["glean"]
    assert glean["enabled"] is False
    assert glean["url"] == "https://default.example.test/mcp"
    workflow = home / "workflows/ericsson/workflows/w.yaml"
    assert workflow.exists()
    assert (home / "workflows/ericsson/commands/collect.md").exists()
    digest = compute_package_digest(load_workflow(workflow)).sha256
    assert WorkflowTrustStore(home).check(digest) == "trusted"
    assert "workflow" in cfg["plugins"]["enabled"]
    assert cfg["plugins"]["entries"]["workflow"]["agent"] == {
        "allow_model_override": True,
        "allow_provider_override": True,
    }


def test_fresh_windows_checkout_seeds_workflow_and_mcp_defaults(
    tmp_path, monkeypatch, fake_config
):
    """Digest-bound capabilities must survive Git for Windows checkout.

    A managed Windows clone can inherit ``core.autocrlf=true`` from the user.
    The authenticated workflow package is byte-digested, so checkout-time CRLF
    conversion makes capability staging fail closed before it enables the
    workflow plugin or merges the Outlook and Glean defaults.
    """
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    shutil.copy2(REPO_ROOT / ".gitattributes", source / ".gitattributes")
    shutil.copytree(REPO_ROOT / "capabilities", source / "capabilities")
    manifest = json.loads(
        (source / "capabilities/ericsson.json").read_text(encoding="utf-8")
    )
    for plugin in manifest.get("plugins", []):
        if not isinstance(plugin, dict):
            continue
        relative = Path(plugin["path"])
        shutil.copytree(REPO_ROOT / relative, source / relative)

    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Capability Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "core.autocrlf", "true"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qm", "fixture"], check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=true",
            "clone",
            "-q",
            str(source),
            str(checkout),
        ],
        check=True,
    )

    monkeypatch.setattr(cs, "_repo_root", lambda: checkout)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir", lambda: checkout / "plugins"
    )
    home = tmp_path / "home"
    home.mkdir()

    cs.seed_baked_capabilities(home)

    cfg = fake_config["cfg"]
    assert "workflow" in cfg["plugins"]["enabled"]
    assert {"outlook", "glean"} <= set(cfg["mcp_servers"])


def test_upgrade_repairs_crlf_only_authenticated_workflow_bytes(
    tmp_path, monkeypatch, fake_config
):
    """An existing managed checkout is repaired only from its tracked HEAD bytes."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "capabilities", checkout / "capabilities")
    (checkout / "plugins/workflow").mkdir(parents=True)

    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Capability Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    package_root = checkout / "capabilities/workflow-packages/ericsson"
    for path in package_root.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

    monkeypatch.setattr(cs, "_repo_root", lambda: checkout)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir", lambda: checkout / "plugins"
    )
    home = tmp_path / "home"
    home.mkdir()

    cs.seed_baked_capabilities(home)

    assert (home / "workflows/ericsson/workflows/inbox-digest.yaml").is_file()
    assert b"\r\n" not in (
        package_root / "workflows/inbox-digest.yaml"
    ).read_bytes()
    assert subprocess.run(
        ["git", "-C", str(checkout), "config", "--get", "core.autocrlf"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "false"


def test_upgrade_with_invalid_workflow_bytes_still_seeds_independent_defaults(
    tmp_path, fake_repo, fake_config
):
    """A failed package verification must not abort unrelated migrations.

    Existing Git-for-Windows installs can retain CRLF bytes for authenticated
    resources that were unchanged when LF attributes were added. The package
    must still fail closed, while the separately trusted plugin activation and
    MCP defaults continue to migrate an existing config.
    """
    fake_config["cfg"] = {
        "plugins": {"enabled": []},
        "mcp_servers": {},
    }
    command = fake_repo / "capabilities/workflow-packages/ericsson/commands/collect.md"
    command.write_bytes(command.read_bytes().replace(b"\n", b"\r\n"))
    home = tmp_path / "home"
    home.mkdir()

    cs.seed_baked_capabilities(home)

    cfg = fake_config["cfg"]
    assert "workflow" in cfg["plugins"]["enabled"]
    assert {"outlook", "glean"} <= set(cfg["mcp_servers"])
    assert not (home / "workflows/ericsson").exists()


def test_upgrade_migration_makes_workflow_cli_discoverable(
    tmp_path, monkeypatch, fake_repo
):
    """Exercise the real config-to-plugin-discovery path used before argparse."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: []\nmcp_servers: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    command = fake_repo / "capabilities/workflow-packages/ericsson/commands/collect.md"
    command.write_bytes(command.read_bytes().replace(b"\n", b"\r\n"))

    cs.seed_baked_capabilities(home)

    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir", lambda: REPO_ROOT / "plugins"
    )
    from hermes_cli.plugins import PluginManager

    manager = PluginManager()
    manager.discover_and_load()
    assert "workflow" in manager._cli_commands


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


def test_seed_backfills_explicit_null_mcp_url_without_clobbering_user_fields(
    tmp_path, fake_repo, fake_config
):
    fake_config["cfg"] = {
        "mcp_servers": {
            "glean": {
                "enabled": True,
                "url": None,
                "headers": {"X-User": "preserve"},
            }
        }
    }

    cs.seed_baked_capabilities(tmp_path / "home")

    saved = fake_config["cfg"]["mcp_servers"]["glean"]
    assert saved["url"] == "https://default.example.test/mcp"
    assert saved["enabled"] is True
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


def test_repo_root_uses_wheel_data_prefix_when_source_tree_has_no_capabilities(
    tmp_path, monkeypatch
):
    source = tmp_path / "site-packages" / "hermes_cli" / "capability_staging.py"
    prefix = tmp_path / "venv"
    (prefix / "capabilities").mkdir(parents=True)
    monkeypatch.setattr(cs, "__file__", str(source))
    monkeypatch.setattr(cs.sys, "prefix", str(prefix))

    assert cs._repo_root() == prefix
