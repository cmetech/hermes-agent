"""Distribution-level contracts for optional standalone capability plugins."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import yaml

from hermes_cli import capability_staging as staging
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.schema import parse_workflow_source_bytes
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
REQUIRED_JIRA_TOOLS = {
    "jira_my_tickets",
    "jira_search_issues",
    "jira_get_issue",
    "jira_add_comment",
    "jira_list_fields",
    "jira_get_project",
    "jira_list_transitions",
    "jira_search_assignable_users",
    "jira_transition_issue",
    "jira_assign_issue",
    "jira_update_fields",
    "jira_manage_labels",
    "jira_create_issue",
    "jira_list_link_types",
    "jira_link_issues",
}
EXPECTED_SHAREPOINT_TOOLS = {
    "sharepoint_resolve_url",
    "sharepoint_get_item",
    "sharepoint_list_items",
    "sharepoint_download",
    "sharepoint_list_owned_sites",
    "sharepoint_audit_permissions",
    "sharepoint_upload",
    "sharepoint_create_folder",
    "sharepoint_move_item",
    "sharepoint_copy_item",
    "sharepoint_recycle_item",
}
JIRA_LIFECYCLE_MIGRATION = "ericsson-jira-backend-to-standalone-v1"


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


def test_checked_in_jira_bundle_is_standalone_and_complete() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_root = repo_root / "plugins" / "ericsson-jira"
    descriptor = yaml.safe_load(
        (plugin_root / "plugin.yaml").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (repo_root / "capabilities" / "ericsson.json").read_text(encoding="utf-8")
    )

    assert descriptor["kind"] == "standalone"
    assert REQUIRED_JIRA_TOOLS <= set(descriptor["provides_tools"])
    assert (plugin_root / "config.schema.json").is_file()
    assert {path.parent.name for path in plugin_root.glob("skills/*/SKILL.md")} == {
        "ticket-research",
        "defect-triage",
    }
    jira = next(
        entry
        for entry in manifest["plugins"]
        if isinstance(entry, dict) and entry.get("id") == "ericsson-jira"
    )
    assert jira == {
        "path": "plugins/ericsson-jira",
        "id": "ericsson-jira",
        "enabled": False,
        "lifecycleMigration": {
            "id": JIRA_LIFECYCLE_MIGRATION,
            "from": "auto_seeded_backend",
        },
    }


def test_checked_in_sharepoint_bundle_is_disabled_complete_and_profiled() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_root = repo_root / "plugins" / "ericsson-sharepoint"
    descriptor = yaml.safe_load(
        (plugin_root / "plugin.yaml").read_text(encoding="utf-8")
    )
    configuration = json.loads(
        (plugin_root / "config.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (repo_root / "capabilities" / "ericsson.json").read_text(encoding="utf-8")
    )

    assert descriptor["kind"] == "standalone"
    assert set(descriptor["provides_tools"]) == EXPECTED_SHAREPOINT_TOOLS
    assert {action["id"] for action in configuration["setup_actions"]} == {
        "authenticate",
        "test_connection",
        "enroll_browser",
        "clear_session",
    }
    assert {path.parent.name for path in plugin_root.glob("skills/*/SKILL.md")} == {
        "sharepoint-navigation",
        "sharepoint-file-operations",
        "sharepoint-permission-audit",
    }
    sharepoint = next(
        entry
        for entry in manifest["plugins"]
        if isinstance(entry, dict) and entry.get("id") == "ericsson-sharepoint"
    )
    assert sharepoint == {
        "path": "plugins/ericsson-sharepoint",
        "id": "ericsson-sharepoint",
        "enabled": False,
    }
    workflow = repo_root / "capabilities/workflows/sharepoint-document-intake.yml"
    assert workflow.is_file()
    assert workflow.with_name("sharepoint-document-intake.hermes.yaml").is_file()


def test_fresh_profile_stages_authenticated_v5_and_v6_jira_workflows(
    tmp_path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "jira-profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(staging, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: repo_root / "plugins",
    )

    staging.seed_baked_capabilities(home)
    workflow_root = home / "workflows/ericsson/workflows"
    discovered = {}
    for filename, normalizer in (
        ("my-tickets-summary.yaml", None),
        ("jira-single-ticket-showcase.yaml", None),
        ("jira-to-gitlab.yaml", None),
        ("jira-defect-loop.yaml", 6),
    ):
        path = workflow_root / filename
        sidecar = path.with_name(f"{path.stem}.hermes.yaml")
        source = parse_workflow_source_bytes(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=sidecar.read_bytes() if sidecar.is_file() else None,
            source="staged",
            precedence=1,
        )
        compilation = compile_workflow(
            source,
            WorkflowCatalogSnapshot.capture((source,)),
            **({"normalizer_version": normalizer} if normalizer else {}),
        )
        discovered[compilation.package.definition.name] = compilation.package

    assert {
        "my-tickets-summary",
        "jira-single-ticket-showcase",
        "jira-to-gitlab",
        "jira-defect-loop",
    } <= set(discovered)
    for name in (
        "my-tickets-summary",
        "jira-single-ticket-showcase",
        "jira-to-gitlab",
        "jira-defect-loop",
    ):
        assert "ericsson-jira" in discovered[name].definition.options["requires"]
    assert "ericsson-gitlab" in discovered["jira-defect-loop"].definition.options[
        "requires"
    ]


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


def test_real_upgraded_profile_deseeds_jira_once_and_retains_configuration(
    tmp_path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "upgraded-profile"
    home.mkdir()
    config = {
        "plugins": {
            "enabled": ["workflow", "ericsson-jira", "ericsson-teams"],
            "disabled": [],
            "entries": {
                "ericsson-jira": {
                    "settings": {
                        "base_url": "https://jira.example.test",
                        "auth_mode": "bearer",
                    }
                }
            },
        },
        "users": {"legacy-person": {"plugins": ["ericsson-jira"]}},
    }
    (home / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    secret_bytes = b"HERMES_PLUGIN_ERICSSON_JIRA_PAT=retained-secret\n"
    (home / ".env").write_bytes(secret_bytes)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(staging, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: repo_root / "plugins",
    )

    staging.seed_baked_capabilities(home)

    migrated = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert migrated["plugins"]["enabled"] == [
        "workflow",
        "ericsson-teams",
        "ericsson-connector-cli",
    ]
    assert migrated["plugins"]["disabled"] == []
    assert migrated["plugins"]["lifecycle_migrations_applied"] == [
        JIRA_LIFECYCLE_MIGRATION
    ]
    assert migrated["plugins"]["entries"]["ericsson-jira"] == (
        config["plugins"]["entries"]["ericsson-jira"]
    )
    assert migrated["users"] == config["users"]
    assert (home / ".env").read_bytes() == secret_bytes

    migrated["plugins"]["enabled"].append("ericsson-jira")
    (home / "config.yaml").write_text(
        yaml.safe_dump(migrated, sort_keys=False), encoding="utf-8"
    )
    staging.seed_baked_capabilities(home)
    restaged = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert restaged["plugins"]["enabled"] == [
        "workflow",
        "ericsson-teams",
        "ericsson-connector-cli",
        "ericsson-jira",
    ]
    assert restaged["plugins"]["lifecycle_migrations_applied"] == [
        JIRA_LIFECYCLE_MIGRATION
    ]
