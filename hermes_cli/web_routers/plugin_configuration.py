"""Profile-scoped REST surface for standalone plugin configuration.

The descriptor and operation service remain the sole authority. This router
only parses bounded request shapes, selects the existing context-local profile
scope, and maps internal diagnostics to stable credential-free HTTP errors.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ValidationError

from hermes_cli.plugin_configuration import (
    PluginConfigurationError,
    get_plugin_configuration_service,
)
from hermes_cli.web_deps import late
from hermes_cli.web_models import (
    PluginConfigurationUpdate,
    PluginEnabledUpdate,
    PluginReadinessRequest,
    PluginSetupActionStart,
)


router = APIRouter(prefix="/api/plugin-configurations")
_config_profile_scope = late("_config_profile_scope")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_OPAQUE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_RequestModel = TypeVar("_RequestModel", bound=BaseModel)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _identifier(value: str) -> str:
    if len(value) > 64 or _IDENTIFIER.fullmatch(value) is None:
        raise _error(400, "invalid_request", "Request path is invalid.")
    return value


def _run_identifier(value: str) -> str:
    if len(value) > 128 or _OPAQUE_RUN_ID.fullmatch(value) is None:
        raise _error(400, "invalid_request", "Request path is invalid.")
    return value


def _request(model: type[_RequestModel], raw: Any) -> _RequestModel:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise _error(400, "invalid_request", "Request body is invalid.") from exc


def _service_error(exc: PluginConfigurationError) -> HTTPException:
    diagnostic = str(exc)
    if diagnostic in {
        "plugin configuration unavailable",
        "setup action run not found",
        "setup action run belongs to a different profile",
    }:
        code = (
            "run_not_found"
            if diagnostic.startswith("setup action run")
            else "plugin_not_found"
        )
        message = (
            "Setup action run was not found."
            if code == "run_not_found"
            else "Plugin configuration was not found."
        )
        return _error(404, code, message)
    if diagnostic == "setup action unavailable":
        return _error(
            409,
            "action_unavailable",
            "Setup action is unavailable until the next session.",
        )
    if "capacity exhausted" in diagnostic:
        return _error(429, "action_capacity", "Setup action capacity is exhausted.")
    if "could not be persisted" in diagnostic:
        return _error(
            500, "persistence_failed", "Plugin configuration could not be persisted."
        )
    return _error(400, "invalid_configuration", "Plugin configuration is invalid.")


def _detail(plugin_id: str, *, platform: str = "desktop") -> dict[str, Any]:
    try:
        return get_plugin_configuration_service().detail(plugin_id, platform=platform)
    except PluginConfigurationError as exc:
        raise _service_error(exc) from exc


@router.get("")
async def list_plugin_configurations(profile: str | None = Query(default=None)):
    with _config_profile_scope(profile):
        service = get_plugin_configuration_service()
        try:
            manager = service._plugin_manager()
            plugin_ids = sorted(
                loaded.manifest.key or loaded.manifest.name
                for loaded in manager._plugins.values()
                if loaded.manifest.configuration is not None
            )
            return [
                service.detail(plugin_id, platform="desktop")
                for plugin_id in plugin_ids
            ]
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.get("/actions/{run_id}")
async def get_plugin_setup_action(
    run_id: str,
    profile: str | None = Query(default=None),
):
    _run_identifier(run_id)
    with _config_profile_scope(profile):
        try:
            return get_plugin_configuration_service().action_status(run_id)
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.delete("/actions/{run_id}")
async def cancel_plugin_setup_action(
    run_id: str,
    profile: str | None = Query(default=None),
):
    _run_identifier(run_id)
    with _config_profile_scope(profile):
        try:
            return get_plugin_configuration_service().cancel_action(run_id)
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.get("/{plugin_id}")
async def get_plugin_configuration(
    plugin_id: str,
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    with _config_profile_scope(profile):
        return _detail(plugin_id)


@router.put("/{plugin_id}")
async def update_plugin_configuration(
    plugin_id: str,
    raw: Any = Body(...),
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    body = _request(PluginConfigurationUpdate, raw)
    secrets = {key: value.get_secret_value() for key, value in body.secrets.items()}
    with _config_profile_scope(body.profile or profile):
        try:
            return get_plugin_configuration_service().update(
                plugin_id,
                settings=body.settings,
                secrets=secrets,
            )
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.put("/{plugin_id}/enabled")
async def update_plugin_enabled(
    plugin_id: str,
    raw: Any = Body(...),
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    body = _request(PluginEnabledUpdate, raw)
    with _config_profile_scope(body.profile or profile):
        from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

        result = dashboard_set_agent_plugin_enabled(plugin_id, enabled=body.enabled)
        if not result.get("ok"):
            raise _error(404, "plugin_not_found", "Plugin configuration was not found.")
        return _detail(plugin_id)


@router.delete("/{plugin_id}/secrets/{field_id}")
async def clear_plugin_secret(
    plugin_id: str,
    field_id: str,
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    _identifier(field_id)
    with _config_profile_scope(profile):
        try:
            return get_plugin_configuration_service().clear_secret(plugin_id, field_id)
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.post("/{plugin_id}/readiness")
async def refresh_plugin_readiness(
    plugin_id: str,
    raw: Any = Body(default={}),
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    body = _request(PluginReadinessRequest, raw)
    with _config_profile_scope(body.profile or profile):
        try:
            return get_plugin_configuration_service().readiness(
                plugin_id, platform="desktop"
            )
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc


@router.post("/{plugin_id}/actions/{action_id}", status_code=202)
async def start_plugin_setup_action(
    plugin_id: str,
    action_id: str,
    raw: Any = Body(default={}),
    profile: str | None = Query(default=None),
):
    _identifier(plugin_id)
    _identifier(action_id)
    body = _request(PluginSetupActionStart, raw)
    with _config_profile_scope(body.profile or profile):
        try:
            return get_plugin_configuration_service().start_action(
                plugin_id,
                action_id,
                unattended=body.unattended,
                timeout_seconds=body.timeout_seconds,
            )
        except PluginConfigurationError as exc:
            raise _service_error(exc) from exc
