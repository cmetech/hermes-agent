import asyncio
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from hermes_cli.plugin_configuration import (
    PluginConfigurationService,
    load_plugin_configuration,
)
from hermes_cli.plugins import LoadedPlugin, PluginManager, PluginManifest


PLUGIN_ID = "sample-connector"


@pytest.fixture(autouse=True)
def _file_keystore(monkeypatch):
    """Keep every API-module test away from the developer's OS keychain."""
    from hermes_cli import secret_keystore

    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
    secret_keystore.reset_backend_cache()
    try:
        yield
    finally:
        # This runs before monkeypatch restores the environment, so no cached
        # backend can survive with a mode or profile from this test.
        secret_keystore.reset_backend_cache()


def _schema(*, setup_actions: bool = True) -> dict:
    descriptor = {
        "version": 1,
        "fields": [
            {
                "id": "endpoint",
                "label": "Endpoint",
                "type": "string",
                "storage": "setting",
                "required": True,
                "documentation_url": "https://docs.example.test/endpoint",
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
        ],
    }
    if setup_actions:
        descriptor["setup_actions"] = [
            {
                "id": "connect",
                "label": "Connect",
                "help": "Complete connector setup.",
                "interactive": False,
                "documentation_url": "https://docs.example.test/connect",
            }
        ]
    return descriptor


def _write_plugin(home: Path, *, import_sentinel: Path | None = None) -> Path:
    plugin = home / "plugins" / PLUGIN_ID
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        yaml.safe_dump({
            "name": PLUGIN_ID,
            "version": "1.0.0",
            "description": "Sample connector",
            "config_schema": "config.schema.json",
        }),
        encoding="utf-8",
    )
    (plugin / "config.schema.json").write_text(json.dumps(_schema()), encoding="utf-8")
    sentinel_code = (
        f"from pathlib import Path\nPath({str(import_sentinel)!r}).write_text('imported')\n"
        if import_sentinel is not None
        else "def register(ctx):\n    return None\n"
    )
    (plugin / "__init__.py").write_text(sentinel_code, encoding="utf-8")
    return plugin


def _write_named_plugin(
    plugins_dir: Path,
    plugin_id: str,
    *,
    schema: dict | None = None,
    init_code: str = "def register(ctx):\n    return None\n",
) -> Path:
    plugin = plugins_dir / plugin_id
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        yaml.safe_dump({
            "name": plugin_id,
            "version": "1.0.0",
            "description": f"{plugin_id} connector",
            "config_schema": "config.schema.json",
        }),
        encoding="utf-8",
    )
    (plugin / "config.schema.json").write_text(
        json.dumps(schema or _schema()), encoding="utf-8"
    )
    (plugin / "__init__.py").write_text(init_code, encoding="utf-8")
    return plugin


def _write_config(home: Path, *, enabled=(), disabled=()) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {"enabled": list(enabled), "disabled": list(disabled)}
        }),
        encoding="utf-8",
    )


def _service(
    plugin: Path, *, enabled: bool = False
) -> tuple[PluginConfigurationService, PluginManager]:
    descriptor = load_plugin_configuration(plugin, "config.schema.json")
    assert descriptor is not None
    manager = PluginManager()
    manifest = PluginManifest(
        name=PLUGIN_ID,
        key=PLUGIN_ID,
        source="user",
        path=str(plugin),
        configuration=descriptor,
    )
    manager._plugins[PLUGIN_ID] = LoadedPlugin(manifest=manifest, enabled=enabled)
    return PluginConfigurationService(manager), manager


@pytest.fixture
def api(tmp_path, monkeypatch):
    from hermes_cli import web_server

    home = tmp_path / ".hermes"
    profile = home / "profiles" / "work"
    _write_config(home)
    _write_config(profile)
    plugin = _write_plugin(home)
    _write_plugin(profile)
    service, manager = _service(plugin)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.web_routers.plugin_configuration.get_plugin_configuration_service",
        lambda: service,
    )
    previous = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield client, home, profile, service, manager
    finally:
        client.close()
        if previous is None:
            delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = previous


def test_catalog_lists_disabled_descriptor_without_importing_plugin_code(
    tmp_path, monkeypatch
):
    from hermes_cli import web_server

    home = tmp_path / ".hermes"
    sentinel = tmp_path / "imported"
    _write_config(home, disabled=[PLUGIN_ID])
    _write_plugin(home, import_sentinel=sentinel)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    manager = PluginManager()
    manager.discover_and_load()
    service = PluginConfigurationService(manager)
    monkeypatch.setattr(
        "hermes_cli.web_routers.plugin_configuration.get_plugin_configuration_service",
        lambda: service,
    )
    web_server.app.state.auth_required = False
    with TestClient(web_server.app) as client:
        client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
        response = client.get("/api/plugin-configurations")

    assert response.status_code == 200
    row = next(item for item in response.json() if item["plugin_id"] == PLUGIN_ID)
    assert row["enabled"] is False
    assert row["fields"][0]["id"] == "endpoint"
    assert row["setup_actions"][0]["available"] is False
    assert not sentinel.exists()


def test_detail_update_secret_clear_and_readiness_are_profile_scoped(api):
    client, home, profile, _service_instance, _manager = api

    assert (
        client.put(
            f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
            json={"enabled": True, "profile": "work"},
        ).status_code
        == 200
    )
    updated = client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}",
        json={
            "settings": {"endpoint": "https://work.example.test"},
            "secrets": {"token": "profile-secret"},
            "profile": "work",
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    token = next(field for field in payload["fields"] if field["id"] == "token")
    assert token == {
        "id": "token",
        "label": "Token",
        "type": "string",
        "storage": "secret",
        "required": True,
        "advanced": False,
        "readiness": True,
        "validation": {"min_length": 4},
        "is_set": True,
    }
    assert "profile-secret" not in updated.text
    assert not (home / ".env").exists()
    assert not (profile / ".env").exists()
    assert all(
        b"profile-secret" not in path.read_bytes()
        for path in profile.rglob("*")
        if path.is_file()
    )

    readiness = client.post(
        f"/api/plugin-configurations/{PLUGIN_ID}/readiness",
        json={"profile": "work"},
    )
    assert readiness.status_code == 200
    assert readiness.json() == {"plugin_id": PLUGIN_ID, "ready": True, "reasons": []}

    current = client.get(f"/api/plugin-configurations/{PLUGIN_ID}")
    current_endpoint = next(
        field for field in current.json()["fields"] if field["id"] == "endpoint"
    )
    assert "value" not in current_endpoint

    cleared = client.delete(
        f"/api/plugin-configurations/{PLUGIN_ID}/secrets/token",
        params={"profile": "work"},
    )
    assert cleared.status_code == 200
    cleared_token = next(
        field for field in cleared.json()["fields"] if field["id"] == "token"
    )
    assert cleared_token["is_set"] is False
    assert "profile-secret" not in cleared.text


def test_enabled_mutation_honors_explicit_disable_and_profile_isolation(api):
    client, home, profile, _service_instance, _manager = api

    enabled = client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
        json={"enabled": True, "profile": "work"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    disabled = client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
        json={"enabled": False, "profile": "work"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["setup_actions"][0]["available"] is False

    work_config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert PLUGIN_ID not in work_config["plugins"]["enabled"]
    assert PLUGIN_ID in work_config["plugins"]["disabled"]
    current_config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert PLUGIN_ID not in current_config["plugins"]["disabled"]


def test_setup_action_start_status_and_cancel_enforce_run_profile(api):
    client, _home, _profile, _service_instance, manager = api
    release = threading.Event()
    started = threading.Event()

    def handler(context):
        started.set()
        release.wait(2)
        return {"cancelled": context.cancelled}

    manager._setup_actions[PLUGIN_ID] = {
        "connect": {"handler": handler, "readiness": None}
    }
    client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
        json={"enabled": True, "profile": "work"},
    )

    response = client.post(
        f"/api/plugin-configurations/{PLUGIN_ID}/actions/connect",
        json={"profile": "work", "timeout_seconds": 10},
    )
    assert response.status_code == 202
    run = response.json()
    assert run["plugin_id"] == PLUGIN_ID
    assert run["action"] == "connect"
    assert started.wait(1)

    wrong_profile = client.get(f"/api/plugin-configurations/actions/{run['run_id']}")
    assert wrong_profile.status_code == 404
    assert wrong_profile.json() == {
        "detail": {
            "code": "run_not_found",
            "message": "Setup action run was not found.",
        }
    }

    status = client.get(
        f"/api/plugin-configurations/actions/{run['run_id']}",
        params={"profile": "work"},
    )
    assert status.status_code == 200
    assert status.json()["status"] in {"queued", "running"}

    cancelled = client.delete(
        f"/api/plugin-configurations/actions/{run['run_id']}",
        params={"profile": "work"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    release.set()


def test_unsuccessful_setup_action_has_stable_credential_free_diagnostic(api):
    client, _home, _profile, _service_instance, manager = api

    def handler(_context):
        raise RuntimeError("remote rejected super-secret-token")

    manager._setup_actions[PLUGIN_ID] = {
        "connect": {"handler": handler, "readiness": None}
    }
    client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
        json={"enabled": True},
    )
    response = client.post(
        f"/api/plugin-configurations/{PLUGIN_ID}/actions/connect",
        json={},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    for _ in range(100):
        status = client.get(f"/api/plugin-configurations/actions/{run_id}")
        if status.json()["status"] == "failed":
            break
        time.sleep(0.01)
    assert status.json()["status"] == "failed"
    assert status.json()["error"] == "setup action failed"
    assert "super-secret-token" not in status.text


@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        (
            f"/api/plugin-configurations/{PLUGIN_ID}",
            "put",
            {"unexpected": "credential-value"},
        ),
        (
            f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
            "put",
            {"enabled": True, "unexpected": "credential-value"},
        ),
        (
            f"/api/plugin-configurations/{PLUGIN_ID}/actions/connect",
            "post",
            {"timeout_seconds": 301},
        ),
    ],
)
def test_malformed_requests_fail_closed_without_echoing_values(api, path, method, body):
    client, *_rest = api
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": "invalid_request", "message": "Request body is invalid."}
    }
    assert "credential-value" not in response.text


def test_service_errors_are_stable_bounded_and_credential_free(api):
    client, *_rest = api
    response = client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}",
        json={"secrets": {"unknown-field": "credential-value"}},
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "code": "invalid_configuration",
            "message": "Plugin configuration is invalid.",
        }
    }
    assert "credential-value" not in response.text


def test_existing_toolset_route_serialization_is_unchanged(api):
    from hermes_cli.web_routers.tools import get_toolsets

    client, *_rest = api
    direct = asyncio.run(get_toolsets())
    response = client.get("/api/tools/toolsets")

    assert response.status_code == 200
    assert response.content == json.dumps(
        direct, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def test_catalog_inventory_and_setup_actions_follow_the_requested_profile(
    tmp_path, monkeypatch
):
    """Disabled descriptors are static; runtime callbacks never cross profiles."""
    from hermes_cli import plugins, web_server

    home = tmp_path / ".hermes"
    profile_a = home / "profiles" / "profile-a"
    profile_b = home / "profiles" / "profile-b"
    bundled = tmp_path / "bundled-plugins"
    sentinel_a = tmp_path / "connector-a-imported"
    sentinel_b = tmp_path / "connector-b-imported"
    shared_init = """def register(ctx):
    ctx.register_setup_action('connect', lambda configuration: {'ok': True})
"""

    _write_config(home)
    _write_config(profile_a, enabled=["shared"], disabled=["connector-a"])
    _write_config(profile_b, enabled=["shared"], disabled=["connector-b"])
    _write_named_plugin(
        profile_a / "plugins",
        "connector-a",
        init_code=f"from pathlib import Path\nPath({str(sentinel_a)!r}).write_text('imported')\n",
    )
    _write_named_plugin(
        profile_b / "plugins",
        "connector-b",
        init_code=f"from pathlib import Path\nPath({str(sentinel_b)!r}).write_text('imported')\n",
    )
    _write_named_plugin(bundled, "shared", init_code=shared_init)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    manager = PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    monkeypatch.setattr(
        "hermes_cli.web_routers.plugin_configuration.get_plugin_configuration_service",
        lambda: PluginConfigurationService(),
    )

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(profile_a))
    try:
        manager.discover_and_load()
    finally:
        reset_hermes_home_override(token)

    previous = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    try:
        with TestClient(web_server.app) as client:
            client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
            catalog_a = client.get(
                "/api/plugin-configurations", params={"profile": "profile-a"}
            )
            catalog_b = client.get(
                "/api/plugin-configurations", params={"profile": "profile-b"}
            )
            detail_b = client.get(
                "/api/plugin-configurations/connector-b",
                params={"profile": "profile-b"},
            )
            absent_enable = client.put(
                "/api/plugin-configurations/connector-a/enabled",
                json={"enabled": True, "profile": "profile-b"},
            )
            absent_action = client.post(
                "/api/plugin-configurations/connector-a/actions/connect",
                json={"profile": "profile-b"},
            )
    finally:
        if previous is None:
            delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = previous

    assert catalog_a.status_code == catalog_b.status_code == 200
    rows_a = {row["plugin_id"]: row for row in catalog_a.json()}
    rows_b = {row["plugin_id"]: row for row in catalog_b.json()}
    assert "connector-a" in rows_a and "connector-b" not in rows_a
    assert "connector-b" in rows_b and "connector-a" not in rows_b
    assert rows_a["shared"]["setup_actions"][0]["available"] is True
    assert rows_b["shared"]["setup_actions"][0]["available"] is False
    assert detail_b.status_code == 200
    assert detail_b.json()["plugin_id"] == "connector-b"
    for response in (absent_enable, absent_action):
        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "code": "plugin_not_found",
                "message": "Plugin configuration was not found.",
            }
        }
    assert not sentinel_a.exists()
    assert not sentinel_b.exists()


def test_unstamped_partial_discovery_callbacks_fail_closed():
    manager = PluginManager()
    manager._setup_actions[PLUGIN_ID] = {
        "connect": {"handler": lambda _context: {}, "readiness": None}
    }

    assert manager.setup_action_registrations(PLUGIN_ID) == {}


def test_desktop_readiness_is_authoritative_after_every_mutation(api):
    client, _home, _profile, service, manager = api
    plugin = manager._plugins[PLUGIN_ID]
    descriptor = plugin.manifest.configuration
    assert descriptor is not None

    from dataclasses import replace
    from hermes_cli.plugin_configuration import (
        PluginConfigurationField,
        ReadinessContribution,
    )

    desktop_field = PluginConfigurationField(
        id="desktop_name",
        label="Desktop name",
        type="string",
        storage=descriptor.fields[0].storage,
        required=True,
        platforms=("desktop",),
        readiness=ReadinessContribution(enabled=True),
    )
    plugin.manifest.configuration = replace(
        descriptor,
        fields=(
            *(replace(field, required=False) for field in descriptor.fields),
            desktop_field,
        ),
    )
    manager._plugins[PLUGIN_ID] = plugin

    client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled", json={"enabled": True}
    )
    responses = [
        client.get(f"/api/plugin-configurations/{PLUGIN_ID}"),
        client.put(
            f"/api/plugin-configurations/{PLUGIN_ID}",
            json={"settings": {"endpoint": "https://desktop.example.test"}},
        ),
        client.put(
            f"/api/plugin-configurations/{PLUGIN_ID}",
            json={"secrets": {"token": "desktop-secret"}},
        ),
        client.delete(f"/api/plugin-configurations/{PLUGIN_ID}/secrets/token"),
        client.post(f"/api/plugin-configurations/{PLUGIN_ID}/readiness", json={}),
    ]
    expected = ["configuration_required:desktop_name"]
    for index, response in enumerate(responses):
        assert response.status_code == 200
        payload = response.json()
        readiness = payload if index == len(responses) - 1 else payload["readiness"]
        assert readiness["reasons"] == expected


_PROFILE_INGRESS_CASES = [
    ("get", "/api/plugin-configurations", None, "query"),
    ("get", f"/api/plugin-configurations/{PLUGIN_ID}", None, "query"),
    ("put", f"/api/plugin-configurations/{PLUGIN_ID}", {"settings": {}}, "body"),
    (
        "put",
        f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
        {"enabled": True},
        "body",
    ),
    ("delete", f"/api/plugin-configurations/{PLUGIN_ID}/secrets/token", None, "query"),
    ("post", f"/api/plugin-configurations/{PLUGIN_ID}/readiness", {}, "body"),
    ("post", f"/api/plugin-configurations/{PLUGIN_ID}/actions/connect", {}, "body"),
    ("get", "/api/plugin-configurations/actions/run-one", None, "query"),
    ("delete", "/api/plugin-configurations/actions/run-one", None, "query"),
]


@pytest.mark.parametrize(("method", "path", "body", "ingress"), _PROFILE_INGRESS_CASES)
@pytest.mark.parametrize("profile", ["../credential-value", "x" * 129])
def test_profile_ingress_rejects_invalid_values_with_fixed_error(
    api, method, path, body, ingress, profile
):
    client, *_rest = api
    kwargs = {"params": {"profile": profile}}
    if body is not None:
        kwargs["json"] = {**body, **({"profile": profile} if ingress == "body" else {})}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": "invalid_profile", "message": "Profile is invalid."}
    }
    assert "credential-value" not in response.text
    assert profile not in response.text


@pytest.mark.parametrize(("method", "path", "body", "ingress"), _PROFILE_INGRESS_CASES)
def test_profile_ingress_rejects_missing_profile_with_fixed_error(
    api, method, path, body, ingress
):
    client, *_rest = api
    profile = "missing-profile"
    kwargs = {"params": {"profile": profile}}
    if body is not None:
        kwargs["json"] = {**body, **({"profile": profile} if ingress == "body" else {})}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "profile_not_found",
            "message": "Profile was not found.",
        }
    }
    assert profile not in response.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", f"/api/plugin-configurations/{PLUGIN_ID}", {"settings": {}}),
        (
            "put",
            f"/api/plugin-configurations/{PLUGIN_ID}/enabled",
            {"enabled": True},
        ),
        ("post", f"/api/plugin-configurations/{PLUGIN_ID}/readiness", {}),
        ("post", f"/api/plugin-configurations/{PLUGIN_ID}/actions/connect", {}),
    ],
)
def test_body_profile_does_not_hide_invalid_query_profile(api, method, path, body):
    client, *_rest = api
    response = getattr(client, method)(
        path,
        params={"profile": "../credential-value"},
        json={**body, "profile": "work"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": "invalid_profile", "message": "Profile is invalid."}
    }
    assert "credential-value" not in response.text


def test_body_profile_does_not_hide_missing_query_profile(api):
    client, *_rest = api
    response = client.put(
        f"/api/plugin-configurations/{PLUGIN_ID}",
        params={"profile": "missing-profile"},
        json={"settings": {}, "profile": "work"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "profile_not_found",
            "message": "Profile was not found.",
        }
    }
    assert "missing-profile" not in response.text
