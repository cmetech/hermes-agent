import json
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
