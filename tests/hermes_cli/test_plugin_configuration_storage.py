import json
from pathlib import Path

import pytest

from hermes_cli.plugin_configuration import (
    PluginConfigurationError,
    PluginConfigurationService,
    load_plugin_configuration,
)
from hermes_cli.plugins import LoadedPlugin, PluginManager, PluginManifest


def _descriptor(tmp_path: Path):
    plugin = tmp_path / "connector"
    plugin.mkdir()
    (plugin / "config.schema.json").write_text(
        json.dumps({
            "version": 1,
            "fields": [
                {
                    "id": "endpoint",
                    "label": "Endpoint",
                    "type": "string",
                    "storage": "setting",
                    "required": True,
                    "validation": {"format": "url"},
                    "readiness": True,
                },
                {
                    "id": "token",
                    "label": "Token",
                    "type": "string",
                    "storage": "secret",
                    "required": True,
                    "validation": {"min_length": 4},
                    "readiness": True,
                },
                {
                    "id": "desktop_name",
                    "label": "Desktop name",
                    "type": "string",
                    "storage": "setting",
                    "platforms": ["desktop"],
                    "required": True,
                    "readiness": True,
                },
                {
                    "id": "conditional_name",
                    "label": "Conditional name",
                    "type": "string",
                    "storage": "setting",
                    "required": True,
                    "visible_when": {
                        "field": "endpoint",
                        "equals": "https://needs-name.test",
                    },
                    "readiness": True,
                },
            ],
        }),
        encoding="utf-8",
    )
    return load_plugin_configuration(plugin, "config.schema.json")


def _service(tmp_path: Path, *, enabled: bool = True):
    descriptor = _descriptor(tmp_path)
    manager = PluginManager()
    manifest = PluginManifest(
        name="sample-connector", key="sample-connector", configuration=descriptor
    )
    manager._plugins[manifest.key] = LoadedPlugin(manifest=manifest, enabled=enabled)
    return PluginConfigurationService(manager), manager


def test_settings_and_write_only_secrets_use_active_profile_stores(
    tmp_path, monkeypatch
):
    home = tmp_path / "profile-a"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service, _ = _service(tmp_path)

    detail = service.update(
        "sample-connector",
        settings={"endpoint": "https://git.example.test"},
        secrets={"token": "top-secret"},
    )

    raw = (home / "config.yaml").read_text(encoding="utf-8")
    assert "plugins:" in raw
    assert "settings:" in raw
    assert "endpoint: https://git.example.test" in raw
    assert "top-secret" not in raw
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "top-secret" in env_text
    token = next(field for field in detail["fields"] if field["id"] == "token")
    assert token["is_set"] is True
    assert "value" not in token
    assert "top-secret" not in json.dumps(detail)


def test_descriptor_is_the_only_write_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, _ = _service(tmp_path)

    with pytest.raises(PluginConfigurationError, match="unknown field"):
        service.update(
            "sample-connector",
            settings={"endpoint": "https://ok.test", "arbitrary.key": "bad"},
        )
    with pytest.raises(PluginConfigurationError, match="storage"):
        service.update("sample-connector", secrets={"endpoint": "bad"})
    assert not (tmp_path / "profile" / "config.yaml").exists()
    assert not (tmp_path / "profile" / ".env").exists()


def test_profile_switch_isolates_reads_writes_and_secret_clear(tmp_path, monkeypatch):
    profile_a = tmp_path / "a"
    profile_b = tmp_path / "b"
    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    service, _ = _service(tmp_path)
    service.update(
        "sample-connector",
        settings={"endpoint": "https://a.test"},
        secrets={"token": "token-a"},
    )

    monkeypatch.setenv("HERMES_HOME", str(profile_b))
    unconfigured_b = service.detail("sample-connector")
    assert (
        next(f for f in unconfigured_b["fields"] if f["id"] == "token")["is_set"]
        is False
    )
    service.update(
        "sample-connector",
        settings={"endpoint": "https://b.test"},
        secrets={"token": "token-b"},
    )
    service.clear_secret("sample-connector", "token")

    detail_b = service.detail("sample-connector")
    assert (
        next(f for f in detail_b["fields"] if f["id"] == "endpoint")["value"]
        == "https://b.test"
    )
    assert next(f for f in detail_b["fields"] if f["id"] == "token")["is_set"] is False
    assert "token-a" in (profile_a / ".env").read_text(encoding="utf-8")
    assert "token-b" not in (profile_b / ".env").read_text(encoding="utf-8")


def test_readiness_is_backend_authored_from_all_required_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, manager = _service(tmp_path, enabled=False)
    status = service.readiness("sample-connector", platform="cli")
    assert status["ready"] is False
    assert status["reasons"] == ["plugin_not_enabled"]

    manager._plugins["sample-connector"].enabled = True
    service.update(
        "sample-connector",
        settings={"endpoint": "https://configured.test"},
        secrets={"token": "token-value"},
    )
    status = service.readiness("sample-connector", platform="cli")
    assert status["ready"] is True
    assert status["reasons"] == []

    service.update("sample-connector", settings={"endpoint": "https://needs-name.test"})
    conditional = service.readiness("sample-connector", platform="cli")
    assert conditional["ready"] is False
    assert conditional["reasons"] == ["configuration_required:conditional_name"]

    service.update("sample-connector", settings={"endpoint": "https://configured.test"})
    desktop = service.readiness("sample-connector", platform="desktop")
    assert desktop["ready"] is False
    assert desktop["reasons"] == ["configuration_required:desktop_name"]


def test_clearing_one_plugin_secret_preserves_another_plugin_slot(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, manager = _service(tmp_path)
    descriptor = manager._plugins["sample-connector"].manifest.configuration
    other_manifest = PluginManifest(
        name="other-connector", key="other-connector", configuration=descriptor
    )
    manager._plugins["other-connector"] = LoadedPlugin(
        manifest=other_manifest, enabled=True
    )
    service.update("sample-connector", secrets={"token": "sample-token"})
    service.update("other-connector", secrets={"token": "other-token"})

    service.clear_secret("sample-connector", "token")

    assert (
        next(
            field
            for field in service.detail("other-connector")["fields"]
            if field["id"] == "token"
        )["is_set"]
        is True
    )
