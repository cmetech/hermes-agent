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
    "ericsson-gitlab:release-research",
    "ericsson-gitlab:personal-inbox",
}
JIRA_TOOL_NAMES = {
    "jira_my_tickets",
    "jira_search_issues",
    "jira_get_issue",
    "jira_add_comment",
}
JIRA_PLUGIN_SKILLS = {
    "ericsson-jira:ticket-research",
    "ericsson-jira:defect-triage",
}
SHAREPOINT_TOOL_NAMES = {
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
SHAREPOINT_GRAPH_TOOL_NAMES = SHAREPOINT_TOOL_NAMES - {
    "sharepoint_audit_permissions"
}
SHAREPOINT_PLUGIN_SKILLS = {
    "ericsson-sharepoint:sharepoint-navigation",
    "ericsson-sharepoint:sharepoint-file-operations",
    "ericsson-sharepoint:sharepoint-permission-audit",
}


class _MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        from hermes_cli import secret_keystore

        try:
            del self.values[(service, name)]
        except KeyError as exc:
            raise secret_keystore._PasswordDeleteError("already absent") from exc


class _ServiceNativeAcl:
    """Native ACL seam for exercising service transactions on any host OS."""

    def __init__(self, permissions):
        self._permissions = permissions
        self._paths = {}
        self._next_handle = 1
        self.applied = []

    def open_handle(self, path, *, access, flags):
        handle = self._next_handle
        self._next_handle += 1
        self._paths[handle] = Path(path)
        return handle

    def close_handle(self, handle):
        assert handle in self._paths

    def handle_metadata(self, handle):
        path = self._paths[handle]
        info = path.lstat()
        attributes = (
            self._permissions._FILE_ATTRIBUTE_DIRECTORY if path.is_dir() else 0
        )
        return self._permissions._HandleMetadata(
            attributes=attributes,
            identity=self._permissions._FileIdentity(info.st_dev, info.st_ino),
        )

    def current_user(self):
        return self._permissions._CurrentUserSid("S-1-5-21-1-2-3-1001", 1, None)

    def read_acl(self, handle, current_user, security_information):
        directory = self._paths[handle].is_dir()
        return self._permissions._AclState(
            owner_matches=True,
            dacl_present=True,
            protected=True,
            ace_count=1,
            ace_type=self._permissions._ACCESS_ALLOWED_ACE_TYPE,
            ace_flags=(
                self._permissions._OBJECT_INHERIT_ACE
                | self._permissions._CONTAINER_INHERIT_ACE
                if directory
                else 0
            ),
            ace_mask=(
                self._permissions.DIRECTORY_PRIVATE_MASK
                if directory
                else self._permissions.FILE_PRIVATE_MASK
            ),
            ace_sid_matches=True,
        )

    def set_dacl(self, handle, sddl, security_information):
        self.applied.append(self._paths[handle])


def _vendor_source(home: Path) -> None:
    source = resolve_ericsson_connector_source()
    shutil.copytree(
        source.plugin,
        home / "plugins" / "ericsson-gitlab",
    )
    router = home / "skills" / "ericsson" / "gitlab"
    router.mkdir(parents=True)
    shutil.copy2(source.router_skill, router)


@pytest.mark.parametrize(
    ("plugin_id", "field_id"),
    [
        ("ericsson-arm", "token"),
        ("ericsson-jira", "pat"),
        ("ericsson-confluence", "pat"),
    ],
)
def test_disabled_descriptor_plugin_secret_round_trip_on_windows(
    tmp_path, monkeypatch, plugin_id, field_id
):
    from hermes_cli import secret_keystore, windows_permissions
    from hermes_cli.plugin_configuration import (
        PluginConfigurationService,
        _secret_storage_key,
    )
    from hermes_cli.plugins import PluginManager
    from hermes_cli.secret_authority import load_authority_registry

    repo_root = Path(__file__).resolve().parents[2]
    capability_manifest = json.loads(
        (repo_root / "capabilities" / "ericsson.json").read_text(encoding="utf-8")
    )
    plugin_entry = next(
        entry
        for entry in capability_manifest["plugins"]
        if isinstance(entry, dict) and entry.get("id") == plugin_id
    )
    assert plugin_entry["enabled"] is False

    home = tmp_path / plugin_id
    home.mkdir()
    shutil.copytree(
        repo_root / "plugins" / plugin_id,
        home / "plugins" / plugin_id,
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": [], "disabled": [plugin_id]}}),
        encoding="utf-8",
    )
    keyring = _MemoryKeyring()
    native = _ServiceNativeAcl(windows_permissions)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    monkeypatch.setattr(secret_keystore, "keyring", keyring)
    monkeypatch.setattr(secret_keystore, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_permissions, "_native_api", lambda: native)
    secret_keystore.reset_backend_cache()

    manager = PluginManager()
    manager.discover_and_load()
    service = PluginConfigurationService(manager)
    storage_key = _secret_storage_key(plugin_id, field_id)
    account = secret_keystore._os_account_name(storage_key, str(home.resolve()))
    first = f"{plugin_id}-{field_id}-synthetic-first"
    replacement = f"{plugin_id}-{field_id}-synthetic-replacement"

    first_detail = service.update(plugin_id, secrets={field_id: first})
    first_field = next(
        field for field in first_detail["fields"] if field["id"] == field_id
    )
    assert first_field["is_set"] is True
    assert "value" not in first_field
    assert first_detail["enabled"] is False
    assert secret_keystore.resolve_secret(storage_key) == first
    registry = load_authority_registry(home / "secrets")
    assert registry is not None
    assert dict(registry.entries) == {storage_key: secret_keystore.SecretAuthority.OS}
    assert keyring.values[(secret_keystore.SERVICE_NAME, account)] == first

    replacement_detail = service.update(
        plugin_id, secrets={field_id: replacement}
    )
    assert replacement_detail["enabled"] is False
    assert secret_keystore.resolve_secret(storage_key) == replacement
    registry = load_authority_registry(home / "secrets")
    assert registry is not None
    assert dict(registry.entries) == {storage_key: secret_keystore.SecretAuthority.OS}
    assert keyring.values[(secret_keystore.SERVICE_NAME, account)] == replacement

    cleared_detail = service.clear_secret(plugin_id, field_id)
    cleared_field = next(
        field for field in cleared_detail["fields"] if field["id"] == field_id
    )
    assert cleared_field["is_set"] is False
    assert cleared_detail["enabled"] is False
    assert (secret_keystore.SERVICE_NAME, account) not in keyring.values
    assert (
        secret_keystore.get_authority(storage_key)
        is secret_keystore.SecretAuthority.CLEARED
    )
    assert manager._plugins[plugin_id].enabled is False
    assert home / "secrets" in native.applied
    assert home / "secrets" / "keystore.lock" in native.applied
    status_json = json.dumps(
        [first_detail, replacement_detail, cleared_detail], sort_keys=True
    )
    assert first not in status_json
    assert replacement not in status_json
    assert first not in (home / "secrets" / "authority.json").read_text(
        encoding="utf-8"
    )
    assert replacement not in (home / "secrets" / "authority.json").read_text(
        encoding="utf-8"
    )
    secret_keystore.reset_backend_cache()


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


def test_source_resolver_accepts_actual_vendored_workflow_package(monkeypatch):
    monkeypatch.delenv(SOURCE_DIR_ENV, raising=False)
    monkeypatch.delenv(SOURCE_SHA_ENV, raising=False)
    repo_root = Path(__file__).resolve().parents[2]

    try:
        resolved = resolve_ericsson_connector_source(repo_root=repo_root)
    except BaseException as exc:
        pytest.fail(f"committed vendor was not accepted: {exc}")

    manifest = json.loads(
        (repo_root / "capabilities" / "ericsson.json").read_text(encoding="utf-8")
    )
    assert resolved.vendored is True
    assert resolved.revision == manifest["vendoredFrom"]
    assert resolved.workflow.name == "jira-to-gitlab.yaml"


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


def _reachable_jira_tool_names(definitions: list[dict]) -> set[str]:
    serialized = json.dumps(definitions, sort_keys=True)
    return {name for name in JIRA_TOOL_NAMES if name in serialized}


def _reachable_sharepoint_tool_names(definitions: list[dict]) -> set[str]:
    serialized = json.dumps(definitions, sort_keys=True)
    return {name for name in SHAREPOINT_TOOL_NAMES if name in serialized}


def test_sharepoint_router_actions_and_tools_follow_fresh_profile_boundary(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugin_configuration import PluginConfigurationService
    from hermes_cli.plugins import PluginManager
    from model_tools import get_tool_definitions
    from tools.registry import registry
    from tools.skills_tool import skill_view, skills_list

    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "sharepoint-profile"
    home.mkdir()
    shutil.copytree(
        repo_root / "plugins" / "ericsson-sharepoint",
        home / "plugins" / "ericsson-sharepoint",
    )
    shutil.copytree(
        repo_root / "skills" / "ericsson" / "sharepoint",
        home / "skills" / "ericsson" / "sharepoint",
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": [], "disabled": ["ericsson-sharepoint"]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])

    disabled_manager = PluginManager()
    disabled_manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", disabled_manager)
    disabled_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-sharepoint"], quiet_mode=True
    )
    assert _reachable_sharepoint_tool_names(disabled_surface) == set()
    assert disabled_manager.list_plugin_skills("ericsson-sharepoint") == []
    assert "sharepoint" in json.dumps(json.loads(skills_list())).lower()
    router = json.loads(skill_view("ericsson/sharepoint", preprocess=False))
    assert router["success"] is True
    assert "fresh conversation" in router["content"].lower()
    assert "ericsson-sharepoint:sharepoint-navigation" in router["content"]
    disabled_detail = PluginConfigurationService(disabled_manager).detail(
        "ericsson-sharepoint"
    )
    assert {action["id"] for action in disabled_detail["setup_actions"]} == {
        "authenticate",
        "test_connection",
        "enroll_browser",
        "clear_session",
    }
    assert not any(action["available"] for action in disabled_detail["setup_actions"])

    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": ["ericsson-sharepoint"], "disabled": []}}
        ),
        encoding="utf-8",
    )
    enabled_manager = PluginManager()
    enabled_manager.discover_and_load()
    service = PluginConfigurationService(enabled_manager)
    service.update(
        "ericsson-sharepoint",
        settings={
            "tenant_host": "tenant.sharepoint.com",
            "auth_mode": "azure_cli",
            "scopes": "https://graph.microsoft.com/.default",
            "authority_url": "https://login.microsoftonline.com",
            "browser_profile": "corp-sharepoint",
        },
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", enabled_manager)
    fresh_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-sharepoint"], quiet_mode=True
    )

    assert _reachable_sharepoint_tool_names(disabled_surface) == set()
    assert _reachable_sharepoint_tool_names(fresh_surface) == SHAREPOINT_GRAPH_TOOL_NAMES
    assert {
        f"ericsson-sharepoint:{name}"
        for name in enabled_manager.list_plugin_skills("ericsson-sharepoint")
    } == SHAREPOINT_PLUGIN_SKILLS
    assert all(
        action["available"]
        for action in service.detail("ericsson-sharepoint")["setup_actions"]
    )
    loaded = json.loads(
        skill_view("ericsson-sharepoint:sharepoint-navigation", preprocess=False)
    )
    assert loaded["success"] is True
    assert "tenant.sharepoint.com" not in json.dumps(loaded)

    for name in SHAREPOINT_TOOL_NAMES:
        registry.deregister(name)


def test_jira_router_and_tools_follow_fresh_enabled_session_boundary(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugin_configuration import PluginConfigurationService
    from hermes_cli.plugins import PluginManager
    from model_tools import get_tool_definitions
    from tools.registry import registry
    from tools.skills_tool import skill_view, skills_list

    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "jira-profile"
    home.mkdir()
    shutil.copytree(repo_root / "plugins" / "ericsson-jira", home / "plugins" / "ericsson-jira")
    shutil.copytree(
        repo_root / "skills" / "ericsson" / "jira",
        home / "skills" / "ericsson" / "jira",
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": [], "disabled": ["ericsson-jira"]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])

    disabled_manager = PluginManager()
    disabled_manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", disabled_manager)
    disabled_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-jira"], quiet_mode=True
    )
    assert _reachable_jira_tool_names(disabled_surface) == set()
    assert disabled_manager.list_plugin_skills("ericsson-jira") == []
    assert "jira" in json.dumps(json.loads(skills_list())).lower()
    router = json.loads(skill_view("ericsson/jira", preprocess=False))
    assert router["success"] is True
    assert "enable" in router["content"].lower()
    assert "configure" in router["content"].lower()
    assert "fresh conversation" in router["content"].lower()
    assert "ericsson-jira:ticket-research" in router["content"]
    assert json.loads(
        skill_view("ericsson-jira:ticket-research", preprocess=False)
    )["success"] is False

    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": ["ericsson-jira"], "disabled": []}}
        ),
        encoding="utf-8",
    )
    enabled_manager = PluginManager()
    enabled_manager.discover_and_load()
    PluginConfigurationService(enabled_manager).update(
        "ericsson-jira",
        settings={
            "base_url": "https://jira.example.test",
            "auth_mode": "bearer",
        },
        secrets={"pat": "profile-only-jira-token"},
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", enabled_manager)
    fresh_surface = get_tool_definitions(
        enabled_toolsets=["skills", "ericsson-jira"], quiet_mode=True
    )

    assert _reachable_jira_tool_names(disabled_surface) == set()
    assert _reachable_jira_tool_names(fresh_surface) == JIRA_TOOL_NAMES
    assert {
        f"ericsson-jira:{name}"
        for name in enabled_manager.list_plugin_skills("ericsson-jira")
    } == JIRA_PLUGIN_SKILLS
    loaded = json.loads(
        skill_view("ericsson-jira:ticket-research", preprocess=False)
    )
    assert loaded["success"] is True
    assert "profile-only-jira-token" not in json.dumps(loaded)

    for name in JIRA_TOOL_NAMES:
        registry.deregister(name)


def test_jira_comment_session_approval_reprompts_for_different_arguments(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugins import PluginManager, resolve_pre_tool_admission
    from tools import approval
    from tools.registry import registry

    repo_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "jira-approval-profile"
    home.mkdir()
    shutil.copytree(
        repo_root / "plugins" / "ericsson-jira",
        home / "plugins" / "ericsson-jira",
    )
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": ["ericsson-jira"], "disabled": []}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    cached = set()
    prompts = []
    monkeypatch.setattr(
        approval, "get_current_session_key", lambda default="default": "jira-session"
    )
    monkeypatch.setattr(approval, "is_approved", lambda _session, key: key in cached)
    monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False, raising=False)
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
    monkeypatch.setattr(
        approval,
        "prompt_dangerous_approval",
        lambda target, reason, **_kwargs: prompts.append((target, reason)) or "session",
    )
    monkeypatch.setattr(
        approval, "approve_session", lambda _session, key: cached.add(key)
    )
    monkeypatch.setattr(approval, "approve_permanent", lambda _key: None)
    monkeypatch.setattr(approval, "save_permanent_allowlist", lambda _values: None)

    first = resolve_pre_tool_admission(
        "jira_add_comment",
        {"key": "ABC-1", "body": "first body"},
        tool_call_id="jira-call-1",
    )
    second = resolve_pre_tool_admission(
        "jira_add_comment",
        {"key": "XYZ-9", "body": "second body"},
        tool_call_id="jira-call-2",
    )

    assert first.block_message is None and second.block_message is None
    assert len(prompts) == 2
    assert "ABC-1" in prompts[0][1] and "first body" in prompts[0][1]
    assert "XYZ-9" in prompts[1][1] and "second body" in prompts[1][1]
    assert len(cached) == 2

    for name in JIRA_TOOL_NAMES:
        registry.deregister(name)


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
