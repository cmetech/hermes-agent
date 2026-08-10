import json
import sys
import time
from pathlib import Path

import pytest

from hermes_cli.plugin_configuration import (
    PluginConfigurationError,
    PluginConfigurationService,
)
from hermes_cli.plugins import PluginManager


def _plugin(tmp_path: Path, *, enabled: bool = True, source: str | None = None):
    root = tmp_path / "action-plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        "name: action-plugin\nconfig_schema: config.schema.json\n", encoding="utf-8"
    )
    (root / "config.schema.json").write_text(
        json.dumps({
            "version": 1,
            "fields": [
                {
                    "id": "token",
                    "label": "Token",
                    "type": "string",
                    "storage": "secret",
                }
            ],
            "setup_actions": [
                {"id": "auth", "label": "Authenticate"},
                {"id": "enroll", "label": "Enroll", "interactive": True},
            ],
        }),
        encoding="utf-8",
    )
    (root / "__init__.py").write_text(
        source
        or "def register(ctx):\n"
        "    ctx.register_setup_action('auth', lambda run: {'authenticated': True})\n",
        encoding="utf-8",
    )
    manager = PluginManager()
    manifest = manager._parse_manifest(root / "plugin.yaml", root, "user", "")
    assert manifest is not None
    if enabled:
        manager._load_plugin(manifest)
    else:
        from hermes_cli.plugins import LoadedPlugin

        manager._plugins["action-plugin"] = LoadedPlugin(
            manifest=manifest, enabled=False
        )
    from hermes_cli.config import load_config, save_config

    config = load_config()
    config["plugins"] = {
        "enabled": ["action-plugin"] if enabled else [],
        "disabled": [],
    }
    save_config(config, preserve_keys={("plugins", "enabled")})
    return PluginConfigurationService(manager), manager, manifest


def _wait(service, run_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = service.action_status(run_id)
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    raise AssertionError("setup action did not finish")


def test_actions_exist_only_after_enabled_plugin_import(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(tmp_path, enabled=False)
    with pytest.raises(PluginConfigurationError, match="unavailable"):
        service.start_action("action-plugin", "auth")

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    service, _, _ = _plugin(enabled_root)
    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])
    assert result["status"] == "succeeded"
    assert result["result"] == {"authenticated": True}


def test_registration_rejects_undeclared_action_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, manager, _ = _plugin(
        tmp_path,
        source="def register(ctx):\n    ctx.register_setup_action('shell', lambda run: {})\n",
    )
    assert manager._plugins["action-plugin"].enabled is False
    assert "undeclared setup action" in manager._plugins["action-plugin"].error
    with pytest.raises(PluginConfigurationError, match="unavailable"):
        service.start_action("action-plugin", "shell")


def test_unattended_invocation_rejects_interactive_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source="def register(ctx):\n    ctx.register_setup_action('enroll', lambda run: {})\n",
    )
    with pytest.raises(PluginConfigurationError, match="interactive"):
        service.start_action("action-plugin", "enroll", unattended=True)


def test_action_deadline_cancellation_output_and_public_shape_are_bounded(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    source = (
        "import time\n"
        "def register(ctx):\n"
        "    def auth(run):\n"
        "        while not run.cancelled:\n"
        "            time.sleep(0.005)\n"
        "        return {'secret': 'x' * 200000}\n"
        "    ctx.register_setup_action('auth', auth)\n"
    )
    service, _, _ = _plugin(tmp_path, source=source)
    with pytest.raises(PluginConfigurationError, match="deadline"):
        service.start_action("action-plugin", "auth", timeout_seconds=0)

    started = service.start_action("action-plugin", "auth", timeout_seconds=1)
    assert set(started) == {"run_id", "plugin_id", "action", "status"}
    cancelled = service.cancel_action(started["run_id"])
    assert set(cancelled) <= {
        "run_id",
        "plugin_id",
        "action",
        "status",
        "result",
        "error",
    }
    final = _wait(service, started["run_id"])
    assert final["status"] == "cancelled"
    assert len(json.dumps(final)) < 70_000


def test_registered_setup_state_contributes_to_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: {}, "
            "readiness=lambda config: False)\n"
        ),
    )
    status = service.readiness("action-plugin", platform="cli")
    assert status["ready"] is False
    assert status["reasons"] == ["setup_required:auth"]


def test_action_failure_never_returns_plugin_exception_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    def auth(run):\n"
            "        raise ValueError('token=must-not-escape')\n"
            "    ctx.register_setup_action('auth', auth)\n"
        ),
    )

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "failed"
    assert result["error"] == "setup action failed"
    assert "must-not-escape" not in json.dumps(result)


def test_action_deadline_cancels_without_status_polling(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "import threading, time\n"
            "cancel_seen = threading.Event()\n"
            "def register(ctx):\n"
            "    def auth(run):\n"
            "        while not run.cancelled:\n"
            "            time.sleep(0.005)\n"
            "        cancel_seen.set()\n"
            "        return {}\n"
            "    ctx.register_setup_action('auth', auth)\n"
        ),
    )

    started = service.start_action("action-plugin", "auth", timeout_seconds=0.05)
    module = sys.modules["hermes_plugins.action_plugin"]

    assert module.cancel_seen.wait(0.5) is True
    assert service.action_status(started["run_id"])["status"] == "timed_out"


def test_action_result_redacts_resolved_secret_even_under_neutral_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    def auth(run):\n"
            "        return {'value': 'prefix-' + run.configuration['token']}\n"
            "    ctx.register_setup_action('auth', auth)\n"
        ),
    )
    service.update("action-plugin", secrets={"token": "resolved-secret"})

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "succeeded"
    assert result["result"] == {"value": "prefix-[redacted]"}
    assert "resolved-secret" not in json.dumps(result)


def test_action_result_redacts_secret_bearing_mapping_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    def auth(run):\n"
            "        return {run.configuration['token']: 'safe'}\n"
            "    ctx.register_setup_action('auth', auth)\n"
        ),
    )
    service.update("action-plugin", secrets={"token": "key-secret"})

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "succeeded"
    assert "key-secret" not in json.dumps(result)


def test_action_result_rejects_non_json_objects_without_stringifying(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "class SecretObject:\n"
            "    def __str__(self): return 'object-secret'\n"
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: {'value': SecretObject()})\n"
        ),
    )

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "failed"
    assert "object-secret" not in json.dumps(result)


def test_action_result_rejects_oversized_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: {'value': 'x' * 70000})\n"
        ),
    )

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "failed"
    assert result["error"] == "setup action failed"


def test_setup_run_is_bound_to_context_local_profile(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process"))
    token = set_hermes_home_override(tmp_path / "profile-a")
    try:
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        service, _, _ = _plugin(plugin_root)
        started = service.start_action("action-plugin", "auth")
    finally:
        reset_hermes_home_override(token)

    token = set_hermes_home_override(tmp_path / "profile-b")
    try:
        with pytest.raises(PluginConfigurationError, match="profile"):
            service.action_status(started["run_id"])
        with pytest.raises(PluginConfigurationError, match="profile"):
            service.cancel_action(started["run_id"])
    finally:
        reset_hermes_home_override(token)


def test_disabled_active_profile_cannot_use_cached_action_registration(
    tmp_path, monkeypatch
):
    from hermes_cli.config import load_config, save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process"))
    token = set_hermes_home_override(tmp_path / "enabled")
    try:
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        service, _, _ = _plugin(plugin_root)
    finally:
        reset_hermes_home_override(token)

    token = set_hermes_home_override(tmp_path / "disabled")
    try:
        config = load_config()
        config["plugins"] = {"enabled": [], "disabled": []}
        save_config(config, preserve_keys={("plugins", "enabled")})
        with pytest.raises(PluginConfigurationError, match="unavailable"):
            service.start_action("action-plugin", "auth")
    finally:
        reset_hermes_home_override(token)


def test_noncooperative_workers_are_capacity_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "import threading\n"
            "release = threading.Event()\n"
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: (release.wait(), {})[1])\n"
        ),
    )
    started = [
        service.start_action("action-plugin", "auth", timeout_seconds=1)
        for _ in range(8)
    ]

    with pytest.raises(PluginConfigurationError, match="capacity"):
        service.start_action("action-plugin", "auth", timeout_seconds=1)

    sys.modules["hermes_plugins.action_plugin"].release.set()
    for run in started:
        _wait(service, run["run_id"])


def test_terminal_runs_cancel_owned_timers_and_history_is_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(tmp_path)
    run_ids = []
    for _ in range(140):
        run = service.start_action("action-plugin", "auth", timeout_seconds=1)
        run_ids.append(run["run_id"])
        _wait(service, run["run_id"])

    assert all(
        record.timer is None or not record.timer.is_alive()
        for record in service._runs.values()
    )
    with pytest.raises(PluginConfigurationError, match="not found"):
        service.action_status(run_ids[0])
    assert len(service._runs) <= 128


def test_setup_readiness_callback_timeout_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "import time\n"
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: {}, "
            "readiness=lambda config: (time.sleep(1), True)[1])\n"
        ),
    )

    started = time.monotonic()
    status = service.readiness("action-plugin", platform="cli")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert status["ready"] is False
    assert status["reasons"] == ["setup_required:auth"]


def test_action_output_enforces_aggregate_byte_budget_during_projection(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "from collections.abc import Mapping\n"
            "items_seen = 0\n"
            "class ManyStrings(Mapping):\n"
            "    def __len__(self): return 100\n"
            "    def __iter__(self): return iter(())\n"
            "    def __getitem__(self, key): raise KeyError(key)\n"
            "    def items(self):\n"
            "        global items_seen\n"
            "        for index in range(100):\n"
            "            items_seen += 1\n"
            "            yield f'item-{index}', 'x' * 1000\n"
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: ManyStrings())\n"
        ),
    )

    result = _wait(service, service.start_action("action-plugin", "auth")["run_id"])

    assert result["status"] == "failed"
    assert sys.modules["hermes_plugins.action_plugin"].items_seen < 100


def test_near_boundary_mapping_is_rejected_by_traversal_not_final_encoding():
    from collections.abc import Mapping

    from hermes_cli.plugin_configuration import _bounded_public_value

    class NearBoundaryMapping(Mapping):
        def __len__(self):
            return 128

        def __iter__(self):
            return (f"k{index:03d}" for index in range(128))

        def __getitem__(self, key):
            return "x" * 501

        def items(self):
            for index in range(128):
                yield f"k{index:03d}", "x" * 501

    with pytest.raises(PluginConfigurationError, match="byte limit"):
        _bounded_public_value(NearBoundaryMapping())


def test_context_local_secret_scope_is_delivered_to_setup_action(tmp_path, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_cli.plugin_configuration import _secret_storage_key
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process"))
    profile_token = set_hermes_home_override(tmp_path / "profile")
    try:
        service, _, _ = _plugin(
            tmp_path,
            source=(
                "def register(ctx):\n"
                "    ctx.register_setup_action('auth', lambda run: "
                "{'received': run.configuration['token'] == 'scope-only-secret'})\n"
            ),
        )
        secret_token = set_secret_scope({
            _secret_storage_key("action-plugin", "token"): "scope-only-secret"
        })
        try:
            result = _wait(
                service,
                service.start_action("action-plugin", "auth")["run_id"],
            )
        finally:
            reset_secret_scope(secret_token)
    finally:
        reset_hermes_home_override(profile_token)

    assert result["status"] == "succeeded"
    assert result["result"] == {"received": True}


def test_connector_secret_authority_precedence_is_managed_scope_external_file(
    tmp_path, monkeypatch
):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_cli.plugin_configuration import _secret_storage_key

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "expected = ''\n"
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: "
            "{'expected_won': run.configuration['token'] == expected})\n"
        ),
    )
    service.update("action-plugin", secrets={"token": "profile-file-secret"})
    key = _secret_storage_key("action-plugin", "token")
    module = sys.modules["hermes_plugins.action_plugin"]

    def assert_authority(value):
        module.expected = value
        result = _wait(
            service,
            service.start_action("action-plugin", "auth")["run_id"],
        )
        assert result["status"] == "succeeded"
        assert result["result"] == {"expected_won": True}

    assert_authority("profile-file-secret")
    monkeypatch.setattr(
        "hermes_cli.env_loader.get_secret_source_values",
        lambda home: {key: "external-secret"},
    )
    assert_authority("external-secret")

    secret_token = set_secret_scope({key: "scoped-secret"})
    try:
        assert_authority("scoped-secret")
    finally:
        reset_secret_scope(secret_token)

    monkeypatch.setattr(
        "hermes_cli.managed_scope.load_managed_env",
        lambda: {key: "managed-secret"},
    )
    secret_token = set_secret_scope({key: "scoped-secret"})
    try:
        assert_authority("managed-secret")
    finally:
        reset_secret_scope(secret_token)


def test_new_profile_file_secret_fills_installed_scope_miss(tmp_path, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    service, _, _ = _plugin(
        tmp_path,
        source=(
            "def register(ctx):\n"
            "    ctx.register_setup_action('auth', lambda run: "
            "{'file_seen': run.configuration['token'] == 'new-file-secret'})\n"
        ),
    )
    secret_token = set_secret_scope({})
    try:
        service.update("action-plugin", secrets={"token": "new-file-secret"})
        result = _wait(
            service,
            service.start_action("action-plugin", "auth")["run_id"],
        )
    finally:
        reset_secret_scope(secret_token)

    assert result["status"] == "succeeded"
    assert result["result"] == {"file_seen": True}
