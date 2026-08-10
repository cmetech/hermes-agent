"""Distribution-level contracts for optional standalone capability plugins."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from hermes_cli import capability_staging as staging


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


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
