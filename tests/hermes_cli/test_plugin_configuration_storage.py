import json
import inspect
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hermes_cli.plugin_configuration import (
    PluginConfigurationError,
    PluginConfigurationService,
    load_plugin_configuration,
)
from hermes_cli.plugins import LoadedPlugin, PluginManager, PluginManifest


def _set_enabled(plugin_ids):
    from hermes_cli.config import load_config, save_config

    config = load_config()
    config["plugins"] = {"enabled": list(plugin_ids), "disabled": []}
    save_config(config, preserve_keys={("plugins", "enabled")})


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
                {
                    "id": "namespace",
                    "label": "Namespace",
                    "type": "string",
                    "storage": "setting",
                    "validation": {"pattern": "^[a-z][a-z][a-z]$"},
                },
                {
                    "id": "retries",
                    "label": "Retries",
                    "type": "integer",
                    "storage": "setting",
                    "validation": {"minimum": 1, "maximum": 5},
                },
                {
                    "id": "mode",
                    "label": "Mode",
                    "type": "string",
                    "storage": "setting",
                    "default": "safe",
                    "validation": {"enum": ["safe", "fast"]},
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


def _runtime_plugins(home: Path, plugins: dict[str, list[dict] | None]):
    """Discover real enabled plugins that retain their own PluginContext."""

    plugin_root = home / "plugins"
    plugin_root.mkdir(parents=True)
    for plugin_id, fields in plugins.items():
        root = plugin_root / plugin_id
        root.mkdir()
        manifest = {
            "name": plugin_id,
            "kind": "standalone",
        }
        if fields is not None:
            manifest["config_schema"] = "config.schema.json"
            (root / "config.schema.json").write_text(
                json.dumps({"version": 1, "fields": fields}),
                encoding="utf-8",
            )
        (root / "plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "__init__.py").write_text(
            "_context = None\n"
            "def register(ctx):\n"
            "    global _context\n"
            "    _context = ctx\n"
            "def runtime_configuration():\n"
            "    return _context.configuration()\n",
            encoding="utf-8",
        )
    (home / "config.yaml").write_text(
        json.dumps({"plugins": {"enabled": sorted(plugins), "disabled": []}}),
        encoding="utf-8",
    )
    manager = PluginManager()
    manager.discover_and_load()
    modules = {name: manager._plugins[name].module for name in plugins}
    assert all(module is not None for module in modules.values())
    return manager, modules


def _runtime_fields(*, prefix: str = "") -> list[dict]:
    return [
        {
            "id": f"{prefix}origin",
            "label": "Origin",
            "type": "string",
            "storage": "setting",
            "required": True,
            "validation": {"format": "url"},
            "readiness": True,
        },
        {
            "id": f"{prefix}pat",
            "label": "Access token",
            "type": "string",
            "storage": "secret",
            "required": True,
            "validation": {"min_length": 4},
            "readiness": True,
        },
        {
            "id": f"{prefix}optional_path",
            "label": "Optional path",
            "type": "string",
            "storage": "setting",
            "validation": {"format": "path"},
        },
    ]


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
    _set_enabled(["sample-connector"])
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


def test_profile_only_env_writer_never_mirrors_into_process_or_child(tmp_path):
    from hermes_cli.config import save_env_value
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    key = "HERMES_PLUGIN_TEST_SECRET"
    token = set_hermes_home_override(tmp_path / "profile")
    try:
        save_env_value(
            key,
            "profile-secret",
            mirror_process_env=False,
            strict=True,
        )
        inherited = subprocess.check_output(
            [sys.executable, "-c", f"import os; print(os.getenv('{key}', ''))"],
            text=True,
        ).strip()
    finally:
        reset_hermes_home_override(token)

    assert os.environ.get(key) is None
    assert inherited == ""
    assert "profile-secret" in (tmp_path / "profile" / ".env").read_text()


def test_profile_only_env_writer_preserves_legacy_file_serialization(tmp_path):
    from hermes_cli.config import save_env_value
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    key = "HERMES_PLUGIN_SERIALIZATION_SECRET"
    token = set_hermes_home_override(tmp_path / "legacy")
    try:
        save_env_value(key, "space value")
    finally:
        reset_hermes_home_override(token)
        os.environ.pop(key, None)
    token = set_hermes_home_override(tmp_path / "profile-only")
    try:
        save_env_value(key, "space value", mirror_process_env=False, strict=True)
    finally:
        reset_hermes_home_override(token)

    assert (tmp_path / "legacy" / ".env").read_bytes() == (
        tmp_path / "profile-only" / ".env"
    ).read_bytes()


def test_profile_secret_reads_ignore_process_global_value(tmp_path, monkeypatch):
    from hermes_cli.plugin_configuration import _secret_storage_key

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv(
        _secret_storage_key("sample-connector", "token"), "wrong-profile-secret"
    )
    service, _ = _service(tmp_path)

    token = next(
        field
        for field in service.detail("sample-connector")["fields"]
        if field["id"] == "token"
    )

    assert token["is_set"] is False


def test_profile_only_env_writer_isolates_concurrent_context_profiles(tmp_path):
    from hermes_cli.config import save_env_value
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    key = "HERMES_PLUGIN_SHARED_SECRET"
    barrier = threading.Barrier(2)
    errors = []

    def write(profile, value):
        token = set_hermes_home_override(profile)
        try:
            barrier.wait()
            save_env_value(key, value, mirror_process_env=False, strict=True)
        except Exception as exc:
            errors.append(exc)
        finally:
            reset_hermes_home_override(token)

    first = threading.Thread(target=write, args=(tmp_path / "a", "secret-a"))
    second = threading.Thread(target=write, args=(tmp_path / "b", "secret-b"))
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    assert errors == []
    assert "secret-a" in (tmp_path / "a" / ".env").read_text()
    assert "secret-b" in (tmp_path / "b" / ".env").read_text()
    assert key not in os.environ


def test_update_rejects_overlapping_setting_and_secret_before_writes(
    tmp_path, monkeypatch
):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service, _ = _service(tmp_path)

    with pytest.raises(PluginConfigurationError, match="both settings and secrets"):
        service.update(
            "sample-connector",
            settings={"token": "setting-value"},
            secrets={"token": "secret-value"},
        )

    assert not (home / ".env").exists()
    assert not (home / "config.yaml").exists()


@pytest.mark.parametrize(
    ("field_id", "value"),
    [
        ("endpoint", 7),
        ("endpoint", "not-a-url"),
        ("endpoint", "ftp://wrong.test"),
        ("namespace", "Not Valid"),
        ("retries", 0),
        ("retries", 6),
    ],
)
def test_readiness_rejects_invalid_stored_values(
    tmp_path, monkeypatch, field_id, value
):
    from hermes_cli.config import load_config, save_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, _ = _service(tmp_path)
    config = load_config()
    config["plugins"] = {
        "enabled": ["sample-connector"],
        "entries": {"sample-connector": {"settings": {field_id: value}}},
    }
    save_config(
        config,
        preserve_keys={
            ("plugins", "entries", "sample-connector", "settings", field_id)
        },
    )

    status = service.readiness("sample-connector", platform="cli")

    assert status["ready"] is False
    assert f"invalid_configuration:{field_id}" in status["reasons"]
    projected = next(
        field
        for field in service.detail("sample-connector")["fields"]
        if field["id"] == field_id
    )
    assert "value" not in projected


def test_resolved_defaults_are_validated_and_projected(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, _ = _service(tmp_path)

    mode = next(
        field
        for field in service.detail("sample-connector")["fields"]
        if field["id"] == "mode"
    )

    assert mode["value"] == "safe"


def test_invalid_conditional_controller_fails_closed_without_dependent_reason(
    tmp_path, monkeypatch
):
    from hermes_cli.config import load_config, save_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, _ = _service(tmp_path)
    config = load_config()
    config["plugins"] = {
        "enabled": ["sample-connector"],
        "entries": {"sample-connector": {"settings": {"endpoint": 9}}},
    }
    save_config(
        config,
        preserve_keys={
            ("plugins", "entries", "sample-connector", "settings", "endpoint")
        },
    )

    status = service.readiness("sample-connector", platform="cli")

    assert status["reasons"] == [
        "invalid_configuration:endpoint",
        "authentication_required:token",
    ]


def test_active_profile_enablement_overrides_cached_loaded_state(tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(tmp_path / "enabled")
    try:
        descriptor_root = tmp_path / "descriptor"
        descriptor_root.mkdir()
        service, _ = _service(descriptor_root)
        _set_enabled(["sample-connector"])
        assert service.detail("sample-connector")["enabled"] is True
    finally:
        reset_hermes_home_override(token)

    token = set_hermes_home_override(tmp_path / "disabled")
    try:
        _set_enabled([])
        detail = service.detail("sample-connector")
    finally:
        reset_hermes_home_override(token)

    assert detail["enabled"] is False
    assert detail["readiness"]["reasons"] == ["plugin_not_enabled"]


def test_configuration_reads_preserve_an_existing_profile_tree(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        json.dumps(
            {
                "plugins": {
                    "enabled": ["sample-connector"],
                    "disabled": [],
                    "entries": {
                        "sample-connector": {
                            "settings": {"endpoint": "https://configured.test"}
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    descriptor_root = tmp_path / "descriptor"
    descriptor_root.mkdir()
    service, _ = _service(descriptor_root)
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = sorted(
        path.relative_to(home).as_posix() for path in home.rglob("*")
    )

    detail = service.detail("sample-connector", platform="cli")

    assert detail["enabled"] is True
    endpoint = next(field for field in detail["fields"] if field["id"] == "endpoint")
    assert endpoint["value"] == "https://configured.test"
    assert sorted(path.relative_to(home).as_posix() for path in home.rglob("*")) == before


def test_managed_persistence_noop_raises_stable_service_error(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    for subdir in ("cron", "sessions", "logs", "memories"):
        (home / subdir).mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    service, _ = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: True)

    with pytest.raises(PluginConfigurationError, match="could not be persisted"):
        service.update("sample-connector", settings={"endpoint": "https://valid.test"})
    with pytest.raises(PluginConfigurationError, match="could not be persisted"):
        service.update("sample-connector", secrets={"token": "valid-token"})


def test_managed_scope_targeted_noop_raises_stable_service_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    service, _ = _service(tmp_path)
    monkeypatch.setattr(
        "hermes_cli.managed_scope.managed_config_keys",
        lambda: {"plugins.entries.sample-connector"},
    )

    with pytest.raises(PluginConfigurationError, match="could not be persisted"):
        service.update("sample-connector", settings={"endpoint": "https://valid.test"})

    monkeypatch.setattr("hermes_cli.managed_scope.managed_config_keys", lambda: set())
    monkeypatch.setattr("hermes_cli.managed_scope.is_env_managed", lambda key: True)
    with pytest.raises(PluginConfigurationError, match="could not be persisted"):
        service.update("sample-connector", secrets={"token": "valid-token"})


def test_plugin_context_reads_its_current_profile_configuration_without_projection(
    tmp_path, monkeypatch
):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager, modules = _runtime_plugins(
        home, {"runtime-config-plugin": _runtime_fields()}
    )
    service = PluginConfigurationService(manager)
    service.update(
        "runtime-config-plugin",
        settings={"origin": "https://setting-sentinel.invalid"},
        secrets={"pat": "secret-sentinel-value"},
    )

    first = modules["runtime-config-plugin"].runtime_configuration()

    assert first.setting("origin") == "https://setting-sentinel.invalid"
    assert first.secret("pat") == "secret-sentinel-value"
    assert not hasattr(first, "__dict__")
    with pytest.raises(AttributeError):
        first.new_value = "mutable"
    safe_debug = f"{first!r} {first!s} {[first]}"
    assert "setting-sentinel" not in safe_debug
    assert "secret-sentinel" not in safe_debug
    for member_name in (
        "_setting_values",
        "_secret_values",
        "_PluginRuntimeConfiguration__setting_lookup",
        "_PluginRuntimeConfiguration__secret_lookup",
    ):
        with pytest.raises(
            AttributeError, match="^plugin runtime configuration member unavailable$"
        ):
            getattr(first, member_name)
    inspected = inspect.getmembers(first)
    assert {name for name, _value in inspected} == {"setting", "secret"}
    inspected_debug = repr(inspected)
    assert "setting-sentinel" not in inspected_debug
    assert "secret-sentinel" not in inspected_debug
    assert "setting-sentinel" not in repr((first, {"configuration": first}))
    assert "secret-sentinel" not in repr((first, {"configuration": first}))

    service.update(
        "runtime-config-plugin",
        settings={"origin": "https://fresh-setting.invalid"},
        secrets={"pat": "fresh-secret-value"},
    )
    second = modules["runtime-config-plugin"].runtime_configuration()
    assert second is not first
    assert second != first
    assert second.setting("origin") == "https://fresh-setting.invalid"
    assert second.secret("pat") == "fresh-secret-value"
    assert first.setting("origin") == "https://setting-sentinel.invalid"


def test_plugin_context_profile_switch_and_reload_reject_stale_generation(
    tmp_path, monkeypatch
):
    home_a = tmp_path / "profile-a"
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    manager_a, modules_a = _runtime_plugins(
        home_a, {"runtime-config-plugin": _runtime_fields()}
    )
    PluginConfigurationService(manager_a).update(
        "runtime-config-plugin",
        settings={"origin": "https://profile-a.invalid"},
        secrets={"pat": "profile-a-secret"},
    )
    old_module_a = modules_a["runtime-config-plugin"]
    assert (
        old_module_a.runtime_configuration().setting("origin")
        == "https://profile-a.invalid"
    )

    home_b = tmp_path / "profile-b"
    monkeypatch.setenv("HERMES_HOME", str(home_b))
    manager_b, modules_b = _runtime_plugins(
        home_b, {"runtime-config-plugin": _runtime_fields()}
    )
    PluginConfigurationService(manager_b).update(
        "runtime-config-plugin",
        settings={"origin": "https://profile-b.invalid"},
        secrets={"pat": "profile-b-secret"},
    )

    with pytest.raises(PluginConfigurationError) as stale_profile:
        old_module_a.runtime_configuration()
    assert str(stale_profile.value) == "plugin runtime configuration unavailable"
    assert "profile-a" not in str(stale_profile.value)
    assert "profile-b" not in str(stale_profile.value)
    assert (
        modules_b["runtime-config-plugin"].runtime_configuration().secret("pat")
        == "profile-b-secret"
    )

    monkeypatch.setenv("HERMES_HOME", str(home_a))
    manager_a.discover_and_load(force=True)
    with pytest.raises(
        PluginConfigurationError, match="^plugin runtime configuration unavailable$"
    ):
        old_module_a.runtime_configuration()
    current_module_a = manager_a._plugins["runtime-config-plugin"].module
    assert (
        current_module_a.runtime_configuration().setting("origin")
        == "https://profile-a.invalid"
    )


def test_plugin_context_fails_closed_for_disabled_descriptor_free_and_invalid_state(
    tmp_path, monkeypatch
):
    from hermes_cli.config import load_config, save_config

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager, modules = _runtime_plugins(
        home,
        {
            "configured-plugin": _runtime_fields(),
            "descriptor-free-plugin": None,
        },
    )
    service = PluginConfigurationService(manager)
    service.update(
        "configured-plugin",
        settings={"origin": "https://valid.invalid"},
        secrets={"pat": "valid-secret"},
    )

    with pytest.raises(
        PluginConfigurationError, match="^plugin runtime configuration unavailable$"
    ):
        modules["descriptor-free-plugin"].runtime_configuration()

    config = load_config()
    config["plugins"]["enabled"] = ["descriptor-free-plugin"]
    save_config(config, preserve_keys={("plugins", "enabled")})
    with pytest.raises(
        PluginConfigurationError, match="^plugin runtime configuration unavailable$"
    ):
        modules["configured-plugin"].runtime_configuration()

    config = load_config()
    config["plugins"]["enabled"] = ["configured-plugin", "descriptor-free-plugin"]
    config["plugins"].setdefault("entries", {}).setdefault(
        "configured-plugin", {}
    ).setdefault("settings", {})["origin"] = "not-an-http-origin"
    save_config(
        config,
        preserve_keys={
            ("plugins", "enabled"),
            ("plugins", "entries", "configured-plugin", "settings", "origin"),
        },
    )
    with pytest.raises(
        PluginConfigurationError, match="^plugin runtime configuration unavailable$"
    ):
        modules["configured-plugin"].runtime_configuration()


def test_plugin_context_lookup_is_storage_authorized_and_cross_plugin_isolated(
    tmp_path, monkeypatch
):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager, modules = _runtime_plugins(
        home,
        {
            "first-plugin": _runtime_fields(),
            "second-plugin": _runtime_fields(prefix="other_"),
            "unconfigured-plugin": _runtime_fields(prefix="missing_"),
        },
    )
    service = PluginConfigurationService(manager)
    service.update(
        "first-plugin",
        settings={"origin": "https://first.invalid"},
        secrets={"pat": "first-secret-sentinel"},
    )
    service.update(
        "second-plugin",
        settings={"other_origin": "https://second.invalid"},
        secrets={"other_pat": "second-secret-sentinel"},
    )
    configuration = modules["first-plugin"].runtime_configuration()

    for getter, field_id in (
        (configuration.setting, "pat"),
        (configuration.secret, "origin"),
        (configuration.setting, "optional_path"),
        (configuration.setting, "arbitrary.setting"),
        (configuration.secret, "ARBITRARY_ENV"),
        (configuration.setting, "other_origin"),
        (configuration.secret, "other_pat"),
    ):
        with pytest.raises(PluginConfigurationError) as unavailable:
            getter(field_id)
        assert str(unavailable.value) == "plugin configuration value unavailable"
        assert field_id not in str(unavailable.value)
        assert "first-secret-sentinel" not in repr(unavailable.value)
        assert "second-secret-sentinel" not in repr(unavailable.value)

    unconfigured = modules["unconfigured-plugin"].runtime_configuration()
    for getter, field_id in (
        (unconfigured.setting, "missing_origin"),
        (unconfigured.secret, "missing_pat"),
    ):
        with pytest.raises(PluginConfigurationError) as unavailable:
            getter(field_id)
        assert str(unavailable.value) == "plugin configuration value unavailable"


def test_plugin_context_secret_resolution_preserves_host_authority_precedence(
    tmp_path, monkeypatch
):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_cli.plugin_configuration import _secret_storage_key

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager, modules = _runtime_plugins(
        home, {"runtime-config-plugin": _runtime_fields()}
    )
    service = PluginConfigurationService(manager)
    service.update(
        "runtime-config-plugin",
        settings={"origin": "https://valid.invalid"},
        secrets={"pat": "profile-file-secret"},
    )
    key = _secret_storage_key("runtime-config-plugin", "pat")

    assert (
        modules["runtime-config-plugin"].runtime_configuration().secret("pat")
        == "profile-file-secret"
    )
    monkeypatch.setattr(
        "hermes_cli.env_loader.get_secret_source_values",
        lambda selected_home: {key: "external-secret"},
    )
    assert (
        modules["runtime-config-plugin"].runtime_configuration().secret("pat")
        == "external-secret"
    )

    scope_token = set_secret_scope({key: "installed-scope-secret"})
    try:
        assert (
            modules["runtime-config-plugin"].runtime_configuration().secret("pat")
            == "installed-scope-secret"
        )
        monkeypatch.setattr(
            "hermes_cli.managed_scope.load_managed_env",
            lambda: {key: "managed-secret"},
        )
        assert (
            modules["runtime-config-plugin"].runtime_configuration().secret("pat")
            == "managed-secret"
        )
    finally:
        reset_secret_scope(scope_token)
