"""Distribution-level contracts for optional standalone capability plugins."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import yaml

from hermes_cli import capability_staging as staging
from tests.ericsson_connector_source import resolve_ericsson_connector_source


EXPECTED_GITLAB_READ_EXPLORATION_TOOLS = {
    "gitlab_list_group_projects",
    "gitlab_list_commits",
    "gitlab_read_commit",
    "gitlab_list_commit_comments",
    "gitlab_list_commit_discussions",
    "gitlab_list_merge_requests",
    "gitlab_list_merge_request_commits",
    "gitlab_list_merge_request_discussions",
}


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_checked_in_gitlab_bundle_exposes_read_exploration_and_digest() -> None:
    """The shipped bundle, not only its authority repo, carries the UAT surface."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_root = repo_root / "plugins" / "ericsson-gitlab"
    manifest = yaml.safe_load((plugin_root / "plugin.yaml").read_text(encoding="utf-8"))

    assert EXPECTED_GITLAB_READ_EXPLORATION_TOOLS <= set(manifest["provides_tools"])
    assert (plugin_root / "skills" / "gitlab-activity-digest" / "SKILL.md").is_file()


def test_baked_distribution_exposes_but_does_not_enable_standalone_plugins(
    tmp_path, monkeypatch
):
    distribution = tmp_path / "distribution"
    home = tmp_path / "profile"
    home.mkdir()
    standalone_ids = (
        "ericsson-jira",
        "ericsson-gitlab",
        "ericsson-sharepoint",
        "ericsson-confluence",
    )
    manifest = {
        "name": "distribution-fixture",
        "plugins": [
            "plugins/workflow",
            *[
                {
                    "path": f"plugins/{plugin_id}",
                    "id": plugin_id,
                    "enabled": False,
                }
                for plugin_id in standalone_ids
            ],
        ],
    }
    _write(
        distribution / "capabilities/distribution-fixture.json",
        json.dumps(manifest),
    )
    _write(
        distribution / "plugins/workflow/plugin.yaml",
        "name: workflow\nkind: backend\n",
    )
    _write(distribution / "plugins/workflow/__init__.py", "")
    sentinel = tmp_path / "standalone-imported"
    for plugin_id in standalone_ids:
        _write(
            distribution / f"plugins/{plugin_id}/plugin.yaml",
            f"name: {plugin_id}\nkind: standalone\n",
        )
        _write(
            distribution / f"plugins/{plugin_id}/__init__.py",
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('imported')\n",
        )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(staging, "_repo_root", lambda: distribution)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: distribution / "plugins",
    )
    staging.seed_baked_capabilities(home)

    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["plugins"]["enabled"] == ["workflow"]

    from hermes_cli import plugins

    monkeypatch.setattr(plugins.PluginManager, "_scan_entry_points", lambda self: [])
    manager = plugins.PluginManager()
    manager.discover_and_load()
    loaded = {item["name"]: item for item in manager.list_plugins()}
    for plugin_id in standalone_ids:
        assert loaded[plugin_id]["kind"] == "standalone"
        assert loaded[plugin_id]["enabled"] is False
    assert not sentinel.exists()


def test_actual_gitlab_source_stages_disabled_with_complete_static_assets(
    tmp_path, monkeypatch
):
    """The approved source bundle is discoverable without importing its module."""
    source = resolve_ericsson_connector_source()
    distribution = tmp_path / "distribution"
    home = tmp_path / "profile"
    home.mkdir()
    shutil.copytree(
        source.plugin,
        distribution / "plugins" / "ericsson-gitlab",
    )
    _write(
        distribution / "capabilities" / "ericsson.json",
        json.dumps({
            "name": "ericsson",
            "plugins": [
                {
                    "path": "plugins/ericsson-gitlab",
                    "id": "ericsson-gitlab",
                    "enabled": False,
                }
            ],
        }),
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(staging, "_repo_root", lambda: distribution)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: distribution / "plugins",
    )
    loaded_connector_modules = frozenset(
        name
        for name in sys.modules
        if name.startswith("hermes_plugins.ericsson_gitlab")
    )

    staging.seed_baked_capabilities(home)

    from hermes_cli import plugins

    monkeypatch.setattr(plugins.PluginManager, "_scan_entry_points", lambda self: [])
    manager = plugins.PluginManager()
    manager.discover_and_load()
    loaded = manager.list_plugins()
    gitlab = next(item for item in loaded if item["name"] == "ericsson-gitlab")
    assert gitlab["enabled"] is False
    assert gitlab["configuration"]["version"] == 1
    newly_loaded_connector_modules = {
        name
        for name in sys.modules
        if name.startswith("hermes_plugins.ericsson_gitlab")
    } - loaded_connector_modules
    assert newly_loaded_connector_modules == set()
    plugin_root = distribution / "plugins" / "ericsson-gitlab"
    assert {
        "plugin.yaml",
        "config.schema.json",
        "__init__.py",
        "auth.py",
        "client.py",
        "models.py",
        "operations.py",
        "tools.py",
    } <= {path.name for path in plugin_root.iterdir() if path.is_file()}
    assert {
        path.parent.name
        for path in plugin_root.glob("skills/*/SKILL.md")
    } == {
        "repository-research",
        "merge-request-review",
        "ci-investigation",
        "gitlab-activity-digest",
    }
