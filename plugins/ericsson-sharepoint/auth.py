"""SharePoint setup actions over Hermes Graph and browser authorities."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from .models import SharePointConfiguration, SharePointSetupError


_OWNED_SESSIONS: dict[str, Any] = {}
_OWNED_SESSIONS_LOCK = threading.Lock()
_AUTH_PROBE_JS = """(async () => {
  try {
    const response = await fetch('/_api/web?$select=Title', {
      credentials: 'same-origin',
      headers: {'Accept': 'application/json;odata=nometadata'}
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return Boolean(payload && payload.Title != null);
  } catch (_) {
    return false;
  }
})()"""


def build_identity_config(
    config: SharePointConfiguration,
    *,
    identity_config_type=None,
):
    if identity_config_type is None:
        from tools.microsoft_graph_identity import GraphIdentityConfig

        identity_config_type = GraphIdentityConfig
    return identity_config_type(
        mode=config.auth_mode,
        tenant_id=config.tenant_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
        scopes=config.scopes,
        authority_url=config.authority_url,
        account_id=config.account_id,
        cache_path=config.cache_path,
        azure_cli_enabled=config.azure_cli_enabled,
    )


def _default_provider_factory(identity_config, *, interactive_allowed: bool):
    from tools.microsoft_graph_identity import create_graph_token_provider

    return create_graph_token_provider(
        identity_config, interactive_allowed=interactive_allowed
    )


def _default_readiness_probe(identity_config, **facts):
    from tools.microsoft_graph_identity import inspect_identity_readiness

    return inspect_identity_readiness(identity_config, **facts)


def _default_client_factory(provider, **kwargs):
    from tools.microsoft_graph_client import MicrosoftGraphClient

    return MicrosoftGraphClient(provider, **kwargs)


def _browser_authority():
    from tools import browser_profiles, browser_session_manager, browser_session_registry

    return browser_profiles, browser_session_registry, browser_session_manager


def _browser_enrolled(config: SharePointConfiguration) -> bool:
    try:
        profiles, _, _ = _browser_authority()
        profile = profiles.get_profile(config.browser_profile)
        return bool(
            profile is not None
            and profile.is_enrolled
            and profiles.is_origin_trusted(profile, config.tenant_origin)
        )
    except Exception:
        return False


def _cached_delegated_account(config: SharePointConfiguration) -> bool:
    if config.auth_mode not in {"auto", "delegated_msal"}:
        return False
    try:
        provider = _default_provider_factory(
            build_identity_config(config), interactive_allowed=False
        )
        checker = getattr(provider, "has_cached_account", None)
        return bool(checker is not None and checker())
    except Exception:
        return False


def identity_status(
    config: SharePointConfiguration,
    *,
    readiness_probe: Callable[..., Any] = _default_readiness_probe,
    delegated_cache_has_account: bool | None = None,
    azure_cli_authenticated: bool = False,
    browser_enrolled: bool | None = None,
    identity_config_type=None,
) -> dict[str, Any]:
    cache_ready = (
        _cached_delegated_account(config)
        if delegated_cache_has_account is None
        else bool(delegated_cache_has_account)
    )
    identity_config = build_identity_config(
        config, identity_config_type=identity_config_type
    )
    try:
        readiness = readiness_probe(
            identity_config,
            delegated_cache_has_account=cache_ready,
            azure_cli_authenticated=bool(azure_cli_authenticated),
        )
    except TypeError:
        readiness = readiness_probe(identity_config)
    audit_ready = _browser_enrolled(config) if browser_enrolled is None else browser_enrolled
    return {
        "graph": {
            "status": str(readiness.status),
            "mode": readiness.mode,
            "missing_fields": list(readiness.missing_fields),
        },
        "audit": (
            {"status": "ready", "browser_profile": config.browser_profile}
            if audit_ready
            else {
                "status": "browser_enrollment_required",
                "browser_profile": config.browser_profile,
            }
        ),
        "delegated_cache": "configured" if cache_ready else "not_configured",
    }


def _configuration(run) -> SharePointConfiguration:
    values = dict(run.configuration)
    profile_home = values.pop("profile_home", None)
    return SharePointConfiguration.from_mapping(values, profile_home=profile_home)


def _run(coroutine):
    return asyncio.run(coroutine)


def authenticate(
    run,
    *,
    provider_factory=_default_provider_factory,
    identity_config_type=None,
) -> dict[str, Any]:
    if run.cancelled:
        raise SharePointSetupError("setup action was cancelled")
    config = _configuration(run)
    provider = provider_factory(
        build_identity_config(config, identity_config_type=identity_config_type),
        interactive_allowed=True,
    )
    interactive = getattr(provider, "authenticate_interactively", None)
    if interactive is None:
        raise SharePointSetupError(
            "the configured authentication mode has no interactive sign-in action"
        )
    result = _run(interactive())
    return {
        "authenticated": result.get("authenticated") is True,
        "account": result.get("account"),
    }


def test_connection(
    run,
    *,
    provider_factory=_default_provider_factory,
    client_factory=_default_client_factory,
    identity_config_type=None,
) -> dict[str, Any]:
    if run.cancelled:
        raise SharePointSetupError("setup action was cancelled")
    config = _configuration(run)
    provider = provider_factory(
        build_identity_config(config, identity_config_type=identity_config_type),
        interactive_allowed=False,
    )
    client = client_factory(provider, timeout=config.timeout_seconds)
    _run(client.get_json(f"/sites/{config.tenant_host}"))
    return {"connected": True, "tenant_host": config.tenant_host}


def _owned_key(config: SharePointConfiguration) -> str:
    return f"ericsson-sharepoint::{config.browser_profile}"


def enroll_browser(
    run,
    *,
    browser_profiles=None,
    browser_registry=None,
    browser_manager=None,
) -> dict[str, Any]:
    del browser_registry
    if run.cancelled:
        raise SharePointSetupError("setup action was cancelled")
    config = _configuration(run)
    if browser_profiles is None or browser_manager is None:
        profiles, _, manager = _browser_authority()
        browser_profiles = browser_profiles or profiles
        browser_manager = browser_manager or manager
    profile = browser_profiles.get_profile(config.browser_profile)
    if (
        profile is None
        or not profile.is_enrolled
        or not browser_profiles.is_origin_trusted(profile, config.tenant_origin)
    ):
        raise SharePointSetupError(
            "browser profile must be enrolled and trust the SharePoint tenant origin"
        )
    key = _owned_key(config)
    session = browser_manager.acquire(
        profile=config.browser_profile,
        headless=False,
        session_key=key,
        attach_global=False,
    )
    try:
        authenticated = session.signin(
            config.tenant_origin,
            _AUTH_PROBE_JS,
            timeout=min(300, int(config.timeout_seconds)),
            poll_s=2.0,
        )
        if not authenticated:
            raise SharePointSetupError("SharePoint browser sign-in did not complete")
        with _OWNED_SESSIONS_LOCK:
            previous = _OWNED_SESSIONS.get(key)
            _OWNED_SESSIONS[key] = session
        if previous is not None and previous is not session:
            previous.release()
    except Exception:
        session.release()
        raise
    return {"enrolled": True, "browser_profile": config.browser_profile}


def clear_session(run, *, browser_registry=None) -> dict[str, Any]:
    config = _configuration(run)
    if browser_registry is None:
        _, browser_registry, _ = _browser_authority()
    key = _owned_key(config)
    if browser_registry.profile_for(key) != config.browser_profile:
        return {"cleared": False, "reason": "session_not_owned"}
    with _OWNED_SESSIONS_LOCK:
        session = _OWNED_SESSIONS.pop(key, None)
    if session is None or getattr(session.profile, "name", None) != config.browser_profile:
        return {"cleared": False, "reason": "session_not_owned"}
    session.release()
    return {"cleared": True, "browser_profile": config.browser_profile}


def graph_ready(
    configuration,
    *,
    readiness_probe: Callable[..., Any] = _default_readiness_probe,
    identity_config_type=None,
) -> bool:
    try:
        config = SharePointConfiguration.from_runtime(configuration)
        # Azure CLI owns its credential cache outside Hermes. Selecting it
        # explicitly (or opting it into auto selection) is the durable
        # readiness signal; token validity is still checked by test_connection
        # and every Graph request.
        azure_cli_configured = (
            config.auth_mode == "azure_cli" or config.azure_cli_enabled
        )
        return (
            identity_status(
                config,
                readiness_probe=readiness_probe,
                azure_cli_authenticated=azure_cli_configured,
                identity_config_type=identity_config_type,
            )["graph"]["status"]
            == "ready"
        )
    except Exception:
        return False
