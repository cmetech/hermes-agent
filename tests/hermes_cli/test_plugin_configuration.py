import json
import time
from pathlib import Path

import pytest


def _write_descriptor(plugin_dir: Path, descriptor: dict) -> Path:
    path = plugin_dir / "config.schema.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    return path


def _valid_descriptor() -> dict:
    return {
        "version": 1,
        "fields": [
            {
                "id": "endpoint",
                "label": "Service URL",
                "type": "string",
                "storage": "setting",
                "required": True,
                "help": "Base URL for the service.",
                "documentation_url": "https://docs.example.test/service",
                "default": "https://service.example.test",
                "platforms": ["cli", "desktop"],
                "validation": {
                    "format": "url",
                    "min_length": 8,
                    "max_length": 2048,
                    "pattern": "^https://",
                },
                "readiness": True,
            },
            {
                "id": "token",
                "label": "Access token",
                "type": "string",
                "storage": "secret",
                "required": True,
                "advanced": True,
                "visible_when": {
                    "field": "endpoint",
                    "equals": "https://service.example.test",
                },
                "validation": {"min_length": 8},
                "readiness": True,
            },
        ],
        "setup_actions": [
            {
                "id": "create-token",
                "label": "Create an access token",
                "help": "Open the provider documentation and create a token.",
                "interactive": True,
                "documentation_url": "https://docs.example.test/tokens",
            }
        ],
    }


def test_loads_immutable_v1_descriptor_and_projects_secrets_as_presence_only(tmp_path):
    from hermes_cli.plugin_configuration import (
        FieldStorage,
        load_plugin_configuration,
        project_plugin_configuration,
    )

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _write_descriptor(plugin_dir, _valid_descriptor())

    descriptor = load_plugin_configuration(plugin_dir, "config.schema.json")

    assert descriptor is not None
    assert descriptor.version == 1
    assert descriptor.fields[0].storage is FieldStorage.SETTING
    assert descriptor.fields[1].storage is FieldStorage.SECRET
    assert descriptor.fields[0].validation.format == "url"
    assert descriptor.fields[1].visible_when.field == "endpoint"
    assert descriptor.setup_actions[0].interactive is True

    projected = project_plugin_configuration(
        descriptor,
        settings={"endpoint": "https://configured.example.test"},
        secrets={"token": "never-return-this"},
    )
    assert projected["fields"][0]["value"] == "https://configured.example.test"
    assert projected["fields"][1]["is_set"] is True
    assert "value" not in projected["fields"][1]
    assert "never-return-this" not in json.dumps(projected)

    with pytest.raises((AttributeError, TypeError)):
        descriptor.fields += ()


@pytest.mark.parametrize(
    "reference",
    ["../outside.json", "/tmp/outside.json", "nested/../../outside.json"],
)
def test_rejects_descriptor_paths_outside_plugin_root(tmp_path, reference):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (tmp_path / "outside.json").write_text(
        json.dumps(_valid_descriptor()), encoding="utf-8"
    )

    assert load_plugin_configuration(plugin_dir, reference) is None


def test_rejects_symlinks_and_non_regular_schema_files(tmp_path):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    target = _write_descriptor(plugin_dir, _valid_descriptor())
    symlink = plugin_dir / "linked.json"
    try:
        symlink.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    assert load_plugin_configuration(plugin_dir, "linked.json") is None
    assert load_plugin_configuration(plugin_dir, ".") is None


def test_rejects_invalid_oversized_and_excessive_descriptors(tmp_path):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    schema = plugin_dir / "config.schema.json"

    schema.write_text("{not-json", encoding="utf-8")
    assert load_plugin_configuration(plugin_dir, schema.name) is None

    schema.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    assert load_plugin_configuration(plugin_dir, schema.name) is None

    excessive = _valid_descriptor()
    excessive["fields"] = [
        {
            "id": f"field-{index}",
            "label": "Field",
            "type": "string",
            "storage": "setting",
        }
        for index in range(100)
    ]
    schema.write_text(json.dumps(excessive), encoding="utf-8")
    assert load_plugin_configuration(plugin_dir, schema.name) is None

    schema.write_text(
        json.dumps(_valid_descriptor()) + (" " * 1_000_000), encoding="utf-8"
    )
    assert load_plugin_configuration(plugin_dir, schema.name) is None


def test_json_conversion_errors_fail_closed(tmp_path):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    schema = plugin_dir / "config.schema.json"
    schema.write_text(
        '{"version":1,"fields":[],"unknown":' + ("9" * 5000) + "}",
        encoding="utf-8",
    )

    assert load_plugin_configuration(plugin_dir, schema.name) is None


def test_visible_when_accepts_null_as_a_json_scalar(tmp_path):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    descriptor["fields"][1]["visible_when"] = {"field": "endpoint", "equals": None}
    _write_descriptor(plugin_dir, descriptor)

    loaded = load_plugin_configuration(plugin_dir, "config.schema.json")

    assert loaded is not None
    assert loaded.fields[1].visible_when.equals is None


def test_adversarial_pattern_fails_closed_without_backtracking(tmp_path):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    descriptor["fields"][0].pop("default")
    descriptor["fields"][0]["validation"] = {"pattern": "^(a+)+$"}
    _write_descriptor(plugin_dir, descriptor)

    started = time.monotonic()
    loaded = load_plugin_configuration(plugin_dir, "config.schema.json")
    elapsed = time.monotonic() - started

    assert loaded is None
    assert elapsed < 1.0


@pytest.mark.parametrize(
    ("pattern", "default"),
    [(r"\$", "$"), (r"\^", "^"), (r"USD\$", "USD$")],
    ids=["dollar", "caret", "dollar-after-literal"],
)
def test_escaped_outer_anchor_characters_remain_literals(tmp_path, pattern, default):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    descriptor["fields"][0]["default"] = default
    descriptor["fields"][0]["validation"] = {"pattern": pattern}
    _write_descriptor(plugin_dir, descriptor)

    loaded = load_plugin_configuration(plugin_dir, "config.schema.json")

    assert loaded is not None
    assert loaded.fields[0].validation.pattern == pattern


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["fields"][0].update(label="URL\x1b[31m"),
        lambda data: data["fields"][0].update(help="Line one\nLine two"),
        lambda data: data["setup_actions"][0].update(label="Token\u202e"),
        lambda data: data["setup_actions"][0].update(help="Hidden\x7f"),
    ],
    ids=["field-label", "field-help", "setup-label", "setup-help"],
)
def test_display_metadata_rejects_terminal_and_format_controls(tmp_path, mutate):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    mutate(descriptor)
    _write_descriptor(plugin_dir, descriptor)

    assert load_plugin_configuration(plugin_dir, "config.schema.json") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["fields"][0].update(documentation_url="javascript:alert(1)"),
        lambda data: data["fields"][0].update(documentation_url="https:///missing"),
        lambda data: data["setup_actions"][0].update(
            documentation_url="file:///tmp/help"
        ),
    ],
    ids=["field-script-scheme", "field-missing-authority", "setup-file-scheme"],
)
def test_documentation_urls_require_http_or_https_authority(tmp_path, mutate):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    mutate(descriptor)
    _write_descriptor(plugin_dir, descriptor)

    assert load_plugin_configuration(plugin_dir, "config.schema.json") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["fields"].append(dict(data["fields"][0])),
        lambda data: data["fields"][0].update(storage="database"),
        lambda data: data["fields"][0].update(command=["dangerous"]),
        lambda data: data["fields"][0]["validation"].update(script="dangerous"),
        lambda data: data["setup_actions"][0].update(module="plugin.actions"),
        lambda data: data["fields"][1].update(default="secret-default"),
        lambda data: data["fields"][0].update(default=42),
        lambda data: data["fields"][0]["validation"].update(
            enum=["http://only.example"]
        ),
        lambda data: data["fields"][0].update(
            default="not a URL", validation={"format": "url"}
        ),
        lambda data: data["fields"][1].update(
            visible_when={"field": "missing", "equals": True}
        ),
        lambda data: data["fields"][0].update(readiness="yes"),
    ],
    ids=[
        "duplicate-field-id",
        "unknown-storage",
        "executable-field-key",
        "executable-validation-key",
        "executable-setup-action-key",
        "secret-default",
        "wrong-default-type",
        "default-outside-enum",
        "default-with-invalid-format",
        "visibility-unknown-field",
        "non-boolean-readiness",
    ],
)
def test_invalid_descriptors_fail_closed(tmp_path, mutate):
    from hermes_cli.plugin_configuration import load_plugin_configuration

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    descriptor = _valid_descriptor()
    mutate(descriptor)
    _write_descriptor(plugin_dir, descriptor)

    assert load_plugin_configuration(plugin_dir, "config.schema.json") is None


def test_old_manifest_without_configuration_descriptor_is_unchanged(tmp_path):
    from hermes_cli.plugins import PluginManager

    plugin_dir = tmp_path / "legacy"
    plugin_dir.mkdir()
    manifest_file = plugin_dir / "plugin.yaml"
    manifest_file.write_text("name: legacy\nversion: '1.0'\n", encoding="utf-8")

    manifest = PluginManager()._parse_manifest(manifest_file, plugin_dir, "user", "")

    assert manifest is not None
    assert manifest.name == "legacy"
    assert manifest.kind == "standalone"
    assert manifest.configuration is None


def test_disabled_manifest_descriptor_is_discovered_without_import_and_disable_wins(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins

    bundled_dir = tmp_path / "bundled"
    user_dir = tmp_path / "plugins"
    plugin_dir = user_dir / "static-config"
    bundled_dir.mkdir()
    plugin_dir.mkdir(parents=True)
    sentinel = tmp_path / "imported"
    (plugin_dir / "plugin.yaml").write_text(
        "name: static-config\nkind: standalone\nconfig_schema: config.schema.json\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    _write_descriptor(plugin_dir, _valid_descriptor())

    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled_dir)
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(plugins, "_get_enabled_plugins", lambda: {"static-config"})
    monkeypatch.setattr(plugins, "_get_disabled_plugins", lambda: {"static-config"})
    monkeypatch.setattr(plugins.PluginManager, "_scan_entry_points", lambda self: [])

    manager = plugins.PluginManager()
    manager.discover_and_load()

    loaded = manager.list_plugins()[0]
    assert loaded["kind"] == "standalone"
    assert loaded["configuration"]["fields"][0]["id"] == "endpoint"
    assert loaded["enabled"] is False
    assert loaded["error"] == "disabled via config"
    assert not sentinel.exists()


def test_disabled_bundled_manifest_descriptor_is_discovered_without_import(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins

    bundled_dir = tmp_path / "bundled"
    plugin_dir = bundled_dir / "bundled-static"
    plugin_dir.mkdir(parents=True)
    hermes_home = tmp_path / "home"
    (hermes_home / "plugins").mkdir(parents=True)
    sentinel = tmp_path / "bundled-imported"
    (plugin_dir / "plugin.yaml").write_text(
        "name: bundled-static\nkind: standalone\nconfig_schema: config.schema.json\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    _write_descriptor(plugin_dir, _valid_descriptor())

    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled_dir)
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(plugins, "_get_enabled_plugins", lambda: {"bundled-static"})
    monkeypatch.setattr(plugins, "_get_disabled_plugins", lambda: {"bundled-static"})
    monkeypatch.setattr(plugins.PluginManager, "_scan_entry_points", lambda self: [])

    manager = plugins.PluginManager()
    manager.discover_and_load()

    loaded = manager.list_plugins()[0]
    assert loaded["source"] == "bundled"
    assert loaded["kind"] == "standalone"
    assert loaded["configuration"]["fields"][0]["id"] == "endpoint"
    assert loaded["enabled"] is False
    assert loaded["error"] == "disabled via config"
    assert not sentinel.exists()


def test_connector_capability_snapshot_is_immutable_credential_free_and_deterministic(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import (
        ConnectorCapabilitySnapshot,
        PluginConfigurationService,
        _secret_storage_key,
        connector_capability_snapshot,
        load_plugin_configuration,
    )
    from tools.registry import registry

    plugin_root = tmp_path / "connector"
    plugin_root.mkdir()
    _write_descriptor(plugin_root, _valid_descriptor())
    descriptor = load_plugin_configuration(plugin_root, "config.schema.json")
    manifest = SimpleNamespace(
        key="generic-connector",
        name="generic-connector",
        configuration=descriptor,
        kind="standalone",
        source="user",
    )
    loaded = SimpleNamespace(
        manifest=manifest,
        enabled=True,
        error=None,
        tools_registered=["generic_connector_read"],
    )

    class Manager:
        _plugin_tool_names = {"generic_connector_read"}
        _discovery_profile_id = PluginConfigurationService._profile_id()
        static_inventory_calls = 0

        @classmethod
        def static_plugin_inventory(cls, *, max_visits=None):
            cls.static_inventory_calls += 1
            return [manifest]

        @staticmethod
        def loaded_plugins():
            return [loaded]

        @staticmethod
        def setup_action_registrations(_plugin_id):
            return {"create-token": {"readiness": lambda _config: True}}

    settings = {"endpoint": "https://configured.example.test"}
    secrets = {_secret_storage_key("generic-connector", "token"): "NEVER_RETURN_TOKEN"}
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Manager())
    monkeypatch.setattr(
        PluginConfigurationService,
        "_settings",
        staticmethod(lambda _plugin_id: dict(settings)),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_profile_secret_values",
        staticmethod(lambda: dict(secrets)),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_is_enabled",
        staticmethod(lambda _loaded: True),
    )
    monkeypatch.setattr(
        registry,
        "get_all_tool_names",
        lambda: ["read_file", "generic_connector_read"],
    )

    first = connector_capability_snapshot()
    second = connector_capability_snapshot()

    assert Manager.static_inventory_calls == 2
    assert (
        first
        == second
        == ConnectorCapabilitySnapshot(
            ready_services=frozenset({"generic-connector"}),
            available_tools=frozenset({"generic_connector_read", "read_file"}),
            fingerprint=first.fingerprint,
        )
    )
    assert len(first.fingerprint) == 64
    assert set(first.fingerprint) <= set("0123456789abcdef")
    serialized = repr(first)
    assert "configured.example.test" not in serialized
    assert "NEVER_RETURN_TOKEN" not in serialized
    with pytest.raises((AttributeError, TypeError)):
        first.fingerprint = "changed"  # type: ignore[misc]

    settings["endpoint"] = "https://changed.example.test"
    changed_setting = connector_capability_snapshot()
    assert changed_setting.fingerprint != first.fingerprint
    assert changed_setting.ready_services == first.ready_services
    assert changed_setting.available_tools == first.available_tools

    settings["endpoint"] = "https://service.example.test"
    secrets.clear()
    missing_secret = connector_capability_snapshot()
    assert missing_secret.fingerprint != changed_setting.fingerprint
    assert missing_secret.ready_services == frozenset()


def test_connector_capability_snapshot_honors_runtime_profile_generation(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import (
        PluginConfigurationService,
        connector_capability_snapshot,
        load_plugin_configuration,
    )
    from tools.registry import registry

    plugin_root = tmp_path / "connector"
    plugin_root.mkdir()
    _write_descriptor(plugin_root, _valid_descriptor())
    descriptor = load_plugin_configuration(plugin_root, "config.schema.json")
    manifest = SimpleNamespace(
        key="generic-connector",
        name="generic-connector",
        configuration=descriptor,
        kind="standalone",
        source="user",
    )
    loaded = SimpleNamespace(
        manifest=manifest,
        enabled=True,
        error=None,
        tools_registered=["generic_connector_read"],
    )

    class Manager:
        _plugin_tool_names = {"generic_connector_read"}
        _discovery_profile_id = "a different profile generation"

        @staticmethod
        def static_plugin_inventory(*, max_visits=None):
            return [manifest]

        @staticmethod
        def loaded_plugins():
            return [loaded]

        @staticmethod
        def setup_action_registrations(_plugin_id):
            raise AssertionError("stale profile callbacks must not run")

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Manager())
    monkeypatch.setattr(
        PluginConfigurationService,
        "_settings",
        staticmethod(lambda _plugin_id: {}),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_profile_secret_values",
        staticmethod(lambda: {}),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_is_enabled",
        staticmethod(lambda _loaded: True),
    )
    monkeypatch.setattr(
        registry,
        "get_all_tool_names",
        lambda: ["read_file", "generic_connector_read"],
    )

    snapshot = connector_capability_snapshot()

    assert snapshot.ready_services == frozenset()
    assert snapshot.available_tools == frozenset({"read_file"})


def test_connector_capability_scoped_fingerprint_ignores_unrelated_services():
    from hermes_cli.plugin_configuration import ConnectorCapabilitySnapshot

    admitted = ConnectorCapabilitySnapshot(
        ready_services=frozenset({"service-a", "service-b"}),
        available_tools=frozenset({"tool_a", "tool_b"}),
        fingerprint="0" * 64,
        _service_fingerprints=(("service-a", "a" * 64), ("service-b", "b" * 64)),
    )
    unrelated_drift = ConnectorCapabilitySnapshot(
        ready_services=admitted.ready_services,
        available_tools=admitted.available_tools,
        fingerprint="1" * 64,
        _service_fingerprints=(("service-a", "a" * 64), ("service-b", "c" * 64)),
    )
    required_drift = ConnectorCapabilitySnapshot(
        ready_services=admitted.ready_services,
        available_tools=admitted.available_tools,
        fingerprint="2" * 64,
        _service_fingerprints=(("service-a", "d" * 64), ("service-b", "b" * 64)),
    )
    scope = (frozenset({"service-a"}), frozenset({"tool_a"}))

    sealed = admitted.scoped_fingerprint(*scope)

    assert unrelated_drift.scoped_fingerprint(*scope) == sealed
    assert required_drift.scoped_fingerprint(*scope) != sealed
    assert (
        admitted.scoped_fingerprint(
            frozenset({"service-a"}),
            frozenset({"different_tool"}),
        )
        != sealed
    )


def test_connector_capability_snapshot_fails_closed_at_inventory_bound(monkeypatch):
    from types import SimpleNamespace

    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import connector_capability_snapshot
    from tools.registry import registry

    manifests = [
        SimpleNamespace(
            key=f"connector-{index}",
            name=f"connector-{index}",
            configuration=object(),
        )
        for index in range(257)
    ]

    class Manager:
        _plugin_tool_names = {"plugin_tool"}
        _discovery_profile_id = None

        @staticmethod
        def static_plugin_inventory(*, max_visits=None):
            return manifests

        @staticmethod
        def loaded_plugins():
            return []

    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Manager())
    monkeypatch.setattr(
        registry,
        "get_all_tool_names",
        lambda: ["read_file", "plugin_tool"],
    )

    snapshot = connector_capability_snapshot()

    assert snapshot.ready_services == frozenset()
    assert snapshot.available_tools == frozenset()
    assert len(snapshot.fingerprint) == 64


def test_connector_capability_snapshot_fails_closed_on_static_scan_capacity(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import connector_capability_snapshot
    from tools.registry import registry

    bundled = tmp_path / "bundled"
    bundled.mkdir()
    for index in range(4097):
        (bundled / f"empty-{index:03d}").mkdir()
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)

    manager = plugins.PluginManager()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(manager, "_scan_entry_points", lambda **_kwargs: [])
    monkeypatch.setattr(registry, "get_all_tool_names", lambda: ["read_file"])

    first = connector_capability_snapshot()
    second = connector_capability_snapshot()

    assert first == second
    assert first.ready_services == frozenset()
    assert first.available_tools == frozenset()
    assert len(first.fingerprint) == 64


def test_connector_capability_snapshot_allows_raw_breadth_above_manifest_bound(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import connector_capability_snapshot
    from tools.registry import registry

    class EntryPoints(list):
        def select(self, *, group):
            return type(self)(ep for ep in self if ep.group == group)

    bundled = tmp_path / "bundled"
    bundled.mkdir()
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    manager = plugins.PluginManager()
    raw_entry_points = EntryPoints(
        SimpleNamespace(
            name=f"unrelated-{index}",
            value="module:load",
            group="unrelated.plugins",
        )
        for index in range(257)
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(
        plugins.importlib.metadata,
        "entry_points",
        lambda: raw_entry_points,
    )
    monkeypatch.setattr(registry, "get_all_tool_names", lambda: ["read_file"])

    snapshot = connector_capability_snapshot()

    assert snapshot.ready_services == frozenset()
    assert snapshot.available_tools == frozenset({"read_file"})


def test_connector_capability_snapshots_share_the_four_worker_readiness_limit(
    tmp_path, monkeypatch
):
    import threading
    from types import SimpleNamespace

    from hermes_cli import plugin_configuration as configuration_module
    from hermes_cli import plugins
    from hermes_cli.plugin_configuration import (
        PluginConfigurationService,
        _secret_storage_key,
        connector_capability_snapshot,
        load_plugin_configuration,
    )
    from tools.registry import registry

    plugin_root = tmp_path / "connector"
    plugin_root.mkdir()
    _write_descriptor(plugin_root, _valid_descriptor())
    descriptor = load_plugin_configuration(plugin_root, "config.schema.json")
    manifest = SimpleNamespace(
        key="generic-connector",
        name="generic-connector",
        configuration=descriptor,
        kind="standalone",
        source="user",
    )
    loaded = SimpleNamespace(
        manifest=manifest,
        enabled=True,
        error=None,
        tools_registered=["generic_connector_read"],
    )
    release = threading.Event()
    finished = threading.Event()
    lock = threading.Lock()
    active = 0
    started = 0
    maximum_active = 0

    def blocking_readiness(_configuration):
        nonlocal active, started, maximum_active
        with lock:
            active += 1
            started += 1
            maximum_active = max(maximum_active, active)
        try:
            release.wait(2)
            return True
        finally:
            with lock:
                active -= 1
                if active == 0:
                    finished.set()

    shared_service = PluginConfigurationService()

    class Manager:
        _plugin_tool_names = {"generic_connector_read"}
        _discovery_profile_id = shared_service._profile_id()

        @staticmethod
        def static_plugin_inventory(*, max_visits=None):
            return [manifest]

        @staticmethod
        def loaded_plugins():
            return [loaded]

        @staticmethod
        def setup_action_registrations(_plugin_id):
            return {"probe": {"readiness": blocking_readiness}}

    monkeypatch.setattr(configuration_module, "_configuration_service", shared_service)
    monkeypatch.setattr(configuration_module, "_READINESS_TIMEOUT", 0.05)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Manager())
    monkeypatch.setattr(
        PluginConfigurationService,
        "_settings",
        staticmethod(lambda _plugin_id: {"endpoint": "https://service.example.test"}),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_profile_secret_values",
        staticmethod(
            lambda: {
                _secret_storage_key("generic-connector", "token"): "present-secret"
            }
        ),
    )
    monkeypatch.setattr(
        PluginConfigurationService,
        "_is_enabled",
        staticmethod(lambda _loaded: True),
    )
    monkeypatch.setattr(
        registry,
        "get_all_tool_names",
        lambda: ["read_file", "generic_connector_read"],
    )

    try:
        snapshots = [connector_capability_snapshot() for _ in range(6)]
        assert started == maximum_active == 4
        assert all(snapshot.ready_services == frozenset() for snapshot in snapshots)
    finally:
        release.set()
        assert finished.wait(1), "blocking readiness workers did not exit"
