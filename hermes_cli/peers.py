"""Shared registered-peer configuration and credential resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit

from hermes_cli.profiles import validate_profile_name

if TYPE_CHECKING:
    from .handoff.runs import RunsClient


_PEER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PROFILE_TARGET_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
BOT_CHAT_TITLE = "Bot Chat"
MAX_PEER_DM_BYTES = 500_000


@dataclass(frozen=True, slots=True)
class ResolvedPeer:
    """Private transport material for one registered profile route."""

    name: str
    profile: str
    profile_base_url: str = field(repr=False)
    origin_sha256: str = field(repr=False)
    auth_scope_sha256: str = field(repr=False)
    key: str = field(repr=False)


def peer_key_env(name: str) -> str:
    return f"HERMES_PEER_{name.upper().replace('-', '_')}_KEY"


def valid_peer_name(name: object) -> bool:
    return isinstance(name, str) and bool(_PEER_NAME_RE.fullmatch(name))


def load_peer_registry(*, initiating_home: str | Path | None = None) -> dict:
    from hermes_cli.config import load_config

    config_path = (
        Path(initiating_home) / "config.yaml"
        if initiating_home is not None
        else None
    )
    config = load_config(config_path=config_path) or {}
    peers = config.get("bot_peers")
    return dict(peers) if isinstance(peers, Mapping) else {}


def parse_peer_target(target: str) -> tuple[str, str | None]:
    """Parse the legacy CLI ``<peer>[/<profile>]`` target shape."""
    raw = (target or "").strip()
    peer, _, profile = raw.partition("/")
    peer = peer.strip()
    profile = profile.strip() or None
    if not peer:
        raise ValueError("Peer name required (hermes peer dm <peer>[/<agent>] ...)")
    if profile and not _PROFILE_TARGET_RE.match(profile):
        raise ValueError(f"Invalid agent/profile name: {profile!r}")
    return peer, profile


def peer_base_url(peer: Mapping[str, object], profile: str | None) -> str:
    """Retain the existing peer CLI base/profile URL behavior."""
    url = str(peer.get("url") or "").rstrip("/")
    if profile:
        return f"{url}/p/{quote(profile, safe='')}"
    return url


def _registered_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("registered peer URL is invalid")
    candidate = value.strip().rstrip("/")
    if (
        not candidate
        or "\\" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        raise ValueError("registered peer URL is invalid")
    try:
        parsed = urlsplit(candidate)
        parsed.port
    except ValueError as exc:
        raise ValueError("registered peer URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("registered peer URL is invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _scope_digest(label: str, *parts: str) -> str:
    payload = "\0".join((f"hermes-peer-{label}-v1", *parts)).encode("utf-8")
    return sha256(payload).hexdigest()


def resolve_peer(
    name: str,
    profile: str,
    *,
    initiating_home: str | Path,
) -> ResolvedPeer:
    """Resolve one peer from an explicit initiating profile without ambient secrets."""
    if not isinstance(name, str) or not _PEER_NAME_RE.fullmatch(name):
        raise ValueError("peer name is invalid")
    try:
        validate_profile_name(profile)
    except ValueError as exc:
        raise ValueError("peer profile is invalid") from exc

    entry = load_peer_registry(initiating_home=initiating_home).get(name)
    if not isinstance(entry, Mapping) or not entry.get("url"):
        raise LookupError(f"no registered peer named {name!r}")
    base_url = _registered_base_url(entry["url"])

    from agent.secret_scope import build_profile_secret_scope
    from hermes_cli.auth import has_usable_secret

    key = str(
        build_profile_secret_scope(Path(initiating_home)).get(peer_key_env(name))
        or ""
    ).strip()
    if not has_usable_secret(key, min_length=16) or any(
        char in key for char in "\r\n\0"
    ):
        raise PermissionError("registered peer credential is missing or unusable")

    profile_base_url = f"{base_url}/p/{quote(profile, safe='')}"
    return ResolvedPeer(
        name=name,
        profile=profile,
        profile_base_url=profile_base_url,
        origin_sha256=_scope_digest("origin", base_url, profile),
        auth_scope_sha256=_scope_digest("auth", name, profile, key),
        key=key,
    )


def ensure_peer_bot_chat(client: RunsClient) -> str:
    return client.ensure_session(BOT_CHAT_TITLE, source="bot_peer_dm")


def peer_dm_request(
    client: RunsClient, message: str, *, session_id: str | None = None
) -> dict[str, str]:
    if (
        not isinstance(message, str)
        or not message
        or "\0" in message
        or len(message.encode("utf-8")) > MAX_PEER_DM_BYTES
    ):
        raise ValueError("peer message is invalid")
    session_id = session_id or ensure_peer_bot_chat(client)
    response = client.chat(session_id, message)
    response_session_id = response.get("session_id")
    if isinstance(response_session_id, str):
        session_id = response_session_id
    payload = response.get("message")
    reply = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(reply, str):
        raise ValueError("peer message response is invalid")
    return {"session_id": session_id, "reply": reply}


__all__ = [
    "ResolvedPeer",
    "ensure_peer_bot_chat",
    "load_peer_registry",
    "parse_peer_target",
    "peer_base_url",
    "peer_key_env",
    "peer_dm_request",
    "resolve_peer",
    "valid_peer_name",
]
