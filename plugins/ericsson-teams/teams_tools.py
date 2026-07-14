"""Pure Teams/Graph helpers — no Hermes imports. Auth via graph_auth."""
from __future__ import annotations

import os
import re
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_auth  # noqa: E402

GRAPH = "https://graph.microsoft.com/v1.0"
_TAG_RE = re.compile(r"<[^>]+>")


class TeamsError(RuntimeError):
    pass


def check_available() -> bool:
    # Always available; the tools guide the user to teams_auth (device-code) when not signed in.
    return True


def _get(path, **params):
    return _request("GET", path, params=params or None)


def _request(method, path, **kw):
    token = graph_auth.get_token()
    with httpx.Client(base_url=GRAPH, timeout=30,
                       headers={"Authorization": f"Bearer {token}"}) as c:
        r = c.request(method, path, **kw)
    if r.status_code == 401:
        raise TeamsError("Graph token rejected (401) — run the teams_auth tool to sign in again")
    if r.status_code >= 400:
        raise TeamsError(f"Graph API error {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def teams_list() -> list[dict]:
    data = _get("/me/joinedTeams")
    return [{"id": t["id"], "name": t.get("displayName")} for t in data.get("value", [])]


def teams_channels(team_id: str) -> list[dict]:
    data = _get(f"/teams/{team_id}/channels")
    return [{"id": c["id"], "name": c.get("displayName")} for c in data.get("value", [])]


def teams_read(team_id: str, channel_id: str, limit: int = 20) -> list[dict]:
    data = _get(f"/teams/{team_id}/channels/{channel_id}/messages", **{"$top": limit})
    out = []
    for m in data.get("value", []):
        sender = ((m.get("from") or {}).get("user") or {}).get("displayName")
        html = (m.get("body") or {}).get("content", "")
        out.append({"id": m["id"], "from": sender,
                     "text": _TAG_RE.sub("", html).strip(),
                     "created": m.get("createdDateTime")})
    return out


def teams_send(team_id: str, channel_id: str, text: str) -> dict:
    data = _request("POST", f"/teams/{team_id}/channels/{channel_id}/messages",
                     json={"body": {"content": text}})
    return {"ok": True, "id": data.get("id")}


def teams_reply(team_id: str, channel_id: str, message_id: str, text: str) -> dict:
    data = _request("POST",
                     f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
                     json={"body": {"content": text}})
    return {"ok": True, "id": data.get("id")}


_STR = {"type": "string"}
SCHEMAS = {
    "teams_auth": {
        "name": "teams_auth",
        "description": "Sign in to Microsoft Graph for the Teams tools (device-code flow). Call with no args to get the code; after the user signs in at the URL, call with complete=true.",
        "parameters": {"type": "object", "properties": {
            "complete": {"type": "boolean", "description": "true to finish a pending sign-in"}}},
    },
    "teams_list": {
        "name": "teams_list", "description": "List Microsoft Teams the user has joined.",
        "parameters": {"type": "object", "properties": {}},
    },
    "teams_channels": {
        "name": "teams_channels", "description": "List channels in a team.",
        "parameters": {"type": "object", "properties": {"team_id": _STR},
                        "required": ["team_id"]},
    },
    "teams_read": {
        "name": "teams_read", "description": "Read recent messages from a Teams channel (HTML stripped).",
        "parameters": {"type": "object", "properties": {
            "team_id": _STR, "channel_id": _STR,
            "limit": {"type": "integer", "description": "Max messages (default 20)"}},
            "required": ["team_id", "channel_id"]},
    },
    "teams_send": {
        "name": "teams_send", "description": "Post a message to a Teams channel.",
        "parameters": {"type": "object", "properties": {
            "team_id": _STR, "channel_id": _STR, "text": _STR},
            "required": ["team_id", "channel_id", "text"]},
    },
    "teams_reply": {
        "name": "teams_reply", "description": "Reply to a specific Teams channel message.",
        "parameters": {"type": "object", "properties": {
            "team_id": _STR, "channel_id": _STR, "message_id": _STR, "text": _STR},
            "required": ["team_id", "channel_id", "message_id", "text"]},
    },
}
