"""Cross-surface contracts for the source-vendored Ericsson GitLab plugin."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess

import pytest
import yaml

from tests.ericsson_connector_source import (
    SOURCE_DIR_ENV,
    SOURCE_SHA_ENV,
    resolve_ericsson_connector_source,
)


TOOL_NAMES = {
    "gitlab_resolve_project",
    "gitlab_list_group_projects",
    "gitlab_list_commits",
    "gitlab_read_commit",
    "gitlab_list_commit_comments",
    "gitlab_list_commit_discussions",
    "gitlab_list_merge_requests",
    "gitlab_list_merge_request_commits",
    "gitlab_list_merge_request_discussions",
    "gitlab_list_repository_tree",
    "gitlab_read_file",
    "gitlab_read_merge_request",
    "gitlab_list_pipelines",
    "gitlab_inspect_ci",
    "gitlab_create_branch",
    "gitlab_commit_changes",
    "gitlab_create_merge_request",
}
PLUGIN_SKILLS = {
    "ericsson-gitlab:repository-research",
    "ericsson-gitlab:merge-request-review",
    "ericsson-gitlab:ci-investigation",
    "ericsson-gitlab:gitlab-activity-digest",
}


def _vendor_source(home: Path) -> None:
    source = resolve_ericsson_connector_source()
    shutil.copytree(
        source.plugin,
        home / "plugins" / "ericsson-gitlab",
    )
    router = home / "skills" / "ericsson" / "gitlab"
    router.mkdir(parents=True)
    shutil.copy2(source.router_skill, router)


def _git_fixture(root: Path) -> str:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.test"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Task Tests"], cwd=root, check=True)
    plugin = root / "plugins" / "ericsson-gitlab"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: ericsson-gitlab\n", encoding="utf-8")
    (plugin / "config.schema.json").write_text("{}\n", encoding="utf-8")
    router = root / "skills" / "ericsson" / "gitlab"
    router.mkdir(parents=True)
    (router / "SKILL.md").write_text("router\n", encoding="utf-8")
    workflows = root / "workflows"
    workflows.mkdir()
    (workflows / "jira-to-gitlab.yml").write_text("name: fixture\n", encoding="utf-8")
    (workflows / "jira-to-gitlab.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    sets = root / "sets"
    sets.mkdir()
    (sets / "ericsson.json").write_text('{"name":"ericsson"}\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_source_resolver_skips_without_vendor_or_explicit_authority(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(SOURCE_DIR_ENV, raising=False)
    monkeypatch.delenv(SOURCE_SHA_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception, match="pre-vendor Task 12"):
        resolve_ericsson_connector_source(repo_root=tmp_path)


def test_source_resolver_requires_matching_clean_full_revision(tmp_path, monkeypatch):
    source = tmp_path / "source"
    revision = _git_fixture(source)
    monkeypatch.setenv(SOURCE_DIR_ENV, str(source))
    monkeypatch.setenv(SOURCE_SHA_ENV, revision)

    resolved = resolve_ericsson_connector_source(repo_root=tmp_path / "no-vendor")
    assert resolved.root == source.resolve()
    assert resolved.revision == revision
    assert resolved.vendored is False

    monkeypatch.setenv(SOURCE_SHA_ENV, "0" * 40)
    with pytest.raises(pytest.UsageError, match="revision does not match"):
        resolve_ericsson_connector_source(repo_root=tmp_path / "no-vendor")
    monkeypatch.setenv(SOURCE_SHA_ENV, revision)
    (source / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match="must be clean"):
        resolve_ericsson_connector_source(repo_root=tmp_path / "no-vendor")


def test_source_resolver_prefers_complete_manifest_pinned_vendor(tmp_path, monkeypatch):
    manifest = tmp_path / "capabilities" / "ericsson.json"
    workflow = (
        tmp_path / "capabilities" / "workflow-packages" / "ericsson" / "workflows"
    )
    plugin = tmp_path / "plugins" / "ericsson-gitlab"
    router = tmp_path / "skills" / "ericsson" / "gitlab"
    for directory in (manifest.parent, workflow, plugin, router):
        directory.mkdir(parents=True, exist_ok=True)
    revision = "a" * 40
    manifest.write_text(json.dumps({"vendoredFrom": revision}), encoding="utf-8")
    (workflow / "jira-to-gitlab.yml").write_text("name: fixture\n", encoding="utf-8")
    (workflow / "jira-to-gitlab.hermes.yaml").write_text(
        "language: fixture\n", encoding="utf-8"
    )
    (plugin / "plugin.yaml").write_text("name: fixture\n", encoding="utf-8")
    (plugin / "config.schema.json").write_text("{}\n", encoding="utf-8")
    (router / "SKILL.md").write_text("router\n", encoding="utf-8")
    monkeypatch.setenv(SOURCE_DIR_ENV, str(tmp_path / "must-not-be-read"))
    monkeypatch.setenv(SOURCE_SHA_ENV, "b" * 40)

    resolved = resolve_ericsson_connector_source(repo_root=tmp_path)
    assert resolved.vendored is True
    assert resolved.revision == revision
    assert resolved.workflow == workflow / "jira-to-gitlab.yml"

    manifest.write_text(json.dumps({"vendoredFrom": "short"}), encoding="utf-8")
    monkeypatch.delenv(SOURCE_DIR_ENV)
    monkeypatch.delenv(SOURCE_SHA_ENV)
    try:
        resolve_ericsson_connector_source(repo_root=tmp_path)
    except BaseException as exc:
        assert isinstance(exc, pytest.UsageError)
        assert "vendoredFrom" in str(exc) and "full SHA" in str(exc)
    else:
        pytest.fail("complete vendor with an invalid revision was accepted")


def test_source_resolver_rejects_explicit_nonrepository(tmp_path, monkeypatch):
    source = tmp_path / "not-a-repository"
    source.mkdir()
    monkeypatch.setenv(SOURCE_DIR_ENV, str(source))
    monkeypatch.setenv(SOURCE_SHA_ENV, "a" * 40)
    with pytest.raises(pytest.UsageError, match="not a Git repository"):
        resolve_ericsson_connector_source(repo_root=tmp_path / "no-vendor")


def _reachable_tool_names(definitions: list[dict]) -> set[str]:
    serialized = json.dumps(definitions, sort_keys=True)
    return {name for name in TOOL_NAMES if name in serialized}


def test_disabled_router_and_fresh_session_activation_are_cache_stable(
    tmp_path, monkeypatch
):
    """Disabled capabilities stay absent; activation affects only a fresh surface."""
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugin_configuration import PluginConfigurationService
    from hermes_cli.plugins import PluginManager
    from model_tools import get_tool_definitions
    from tools.registry import registry
    from tools.skills_tool import skill_view, skills_list

    home = tmp_path / "profile"
    home.mkdir()
    _vendor_source(home)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": [], "disabled": ["ericsson-gitlab"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])

    disabled_manager = PluginManager()
    disabled_manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", disabled_manager)
    disabled_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-gitlab"], quiet_mode=True
    )

    assert _reachable_tool_names(disabled_surface) == set()
    assert disabled_manager.list_plugin_skills("ericsson-gitlab") == []
    router_index = json.loads(skills_list())
    assert router_index["success"] is True
    assert "gitlab" in json.dumps(router_index).lower()
    router = json.loads(skill_view("ericsson/gitlab", preprocess=False))
    assert router["success"] is True
    router_text = router["content"]
    assert "enable" in router_text.lower()
    assert "configure" in router_text.lower()
    assert "fresh conversation" in router_text.lower()
    assert "ericsson-gitlab:repository-research" in router_text
    assert "ericsson-gitlab:gitlab-activity-digest" in router_text
    assert (
        json.loads(skill_view("ericsson-gitlab:repository-research", preprocess=False))[
            "success"
        ]
        is False
    )

    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["ericsson-gitlab"], "disabled": []}}),
        encoding="utf-8",
    )
    enabled_manager = PluginManager()
    enabled_manager.discover_and_load()
    PluginConfigurationService(enabled_manager).update(
        "ericsson-gitlab",
        settings={"origin": "https://gitlab.example.test"},
        secrets={"pat": "profile-only-token"},
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", enabled_manager)
    fresh_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-gitlab"], quiet_mode=True
    )

    # The already-created surface is an immutable per-conversation snapshot.
    assert _reachable_tool_names(disabled_surface) == set()
    assert _reachable_tool_names(fresh_surface) == TOOL_NAMES
    assert {
        f"ericsson-gitlab:{name}"
        for name in enabled_manager.list_plugin_skills("ericsson-gitlab")
    } == PLUGIN_SKILLS
    loaded = json.loads(
        skill_view("ericsson-gitlab:repository-research", preprocess=False)
    )
    assert loaded["success"] is True
    assert loaded["name"] == "ericsson-gitlab:repository-research"
    assert "profile-only-token" not in json.dumps(loaded)

    digest = json.loads(
        skill_view("ericsson-gitlab:gitlab-activity-digest", preprocess=False)
    )
    assert digest["success"] is True
    assert "lookback_hours=24" in digest["content"]
    assert "Do not call `cronjob`" in digest["content"]
    assert "return exactly `[SILENT]`" in digest["content"]
    assert "profile-only-token" not in json.dumps(digest)

    for name in TOOL_NAMES:
        registry.deregister(name)
