"""MSAL device-code auth for Microsoft Graph (Teams tools).

- Public client (Azure CLI's well-known client id — no app registration),
  scope https://graph.microsoft.com/.default, authority organizations.
- Serializable token cache at $HERMES_HOME/ericsson/msal_token_cache.json.
- msal is imported LAZILY so the plugin loads even if msal is absent.
- Device flow is two-step for chat UX: start_device_flow() returns the code
  message immediately (module-level pending flow survives because the plugin
  lives in the persistent Hermes process); complete_device_flow() polls.
"""
from __future__ import annotations

import os
from pathlib import Path

CLIENT_ID = os.environ.get("ERICSSON_GRAPH_CLIENT_ID",
                           "04b07795-8ddb-461a-bbee-02f9e1bf7b46")  # Azure CLI public client
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["https://graph.microsoft.com/.default"]

_PENDING_FLOW = None


class AuthRequired(RuntimeError):
    pass


def cache_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "ericsson" / "msal_token_cache.json"


def _app():
    import msal
    cache = msal.SerializableTokenCache()
    p = cache_path()
    if p.exists():
        cache.deserialize(p.read_text())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY,
                                        token_cache=cache)
    return app, cache


def _persist(cache) -> None:
    if cache.has_state_changed:
        p = cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cache.serialize())


def get_token() -> str:
    """Silent token from cache. Raises AuthRequired with next-step guidance."""
    if not cache_path().exists():
        raise AuthRequired("Not signed in to Microsoft Graph — run the "
                           "teams_auth tool to sign in with a device code.")
    app, cache = _app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    _persist(cache)
    if not result or "access_token" not in result:
        raise AuthRequired("Graph session expired — run the teams_auth tool "
                           "to sign in again.")
    return result["access_token"]


def start_device_flow() -> dict:
    global _PENDING_FLOW
    app, _cache = _app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise AuthRequired(f"could not start device flow: {flow.get('error_description', flow)}")
    _PENDING_FLOW = (app, _cache, flow)
    return {"message": flow["message"], "verification_uri": flow["verification_uri"],
            "user_code": flow["user_code"]}


def complete_device_flow() -> dict:
    global _PENDING_FLOW
    if _PENDING_FLOW is None:
        raise AuthRequired("no device flow in progress — call teams_auth first")
    app, cache, flow = _PENDING_FLOW
    flow["expires_at"] = 0  # poll once; the tool is re-invoked to retry
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        _persist(cache)
        _PENDING_FLOW = None
        return {"ok": True, "account": result.get("id_token_claims", {}).get("preferred_username")}
    err = result.get("error")
    if err in ("authorization_pending", "slow_down"):
        return {"ok": False, "pending": True,
                "detail": result.get("error_description", "authorization pending — "
                                     "finish signing in, then run teams_auth complete again")}
    _PENDING_FLOW = None
    return {"ok": False, "pending": False,
            "error": result.get("error_description") or err or "device flow failed",
            "hint": "run teams_auth again to restart sign-in"}
