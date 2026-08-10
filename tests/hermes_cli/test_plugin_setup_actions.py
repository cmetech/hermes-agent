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
