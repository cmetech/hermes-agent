"""Profile-scoped agent directory and Bot target resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from hermes_cli.config import load_config_readonly, read_user_config_raw
from hermes_cli.peers import load_peer_registry, parse_peer_target, valid_peer_name
from hermes_constants import named_profile_is_deleted

from .models import HandoffEndpoint


_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CONTROLLED_CONVERSATION_CAPABILITIES = frozenset({"cancellation", "follow_up"})
ResolutionSource = Literal[
    "explicit",
    "directory",
    "legacy_peer",
    "legacy_local",
    "legacy_bare_peer",
    "relay",
]


@dataclass(frozen=True, slots=True)
class AgentDirectoryEntry:
    name: str
    default: HandoffEndpoint
    endpoints: tuple[HandoffEndpoint, ...]


@dataclass(frozen=True, slots=True)
class ResolvedAgentTarget:
    source: ResolutionSource
    endpoint: HandoffEndpoint | None
    required_capabilities: frozenset[str]
    peer: str | None = None
    profile: str | None = None
    relay_target: str | None = None


class AmbiguousAgentTarget(ValueError):
    def __init__(self, choices: Sequence[str]) -> None:
        self.choices = tuple(sorted(set(choices)))
        super().__init__("agent target is ambiguous")


def _root(home: Path) -> Path:
    return home.parent.parent if home.parent.name == "profiles" else home


def _local_profiles(home: Path) -> tuple[str, ...]:
    root = _root(home)
    names = ["default"]
    profiles = root / "profiles"
    try:
        for child in sorted(profiles.iterdir()):
            if (
                child.is_dir()
                and _AGENT_NAME.fullmatch(child.name)
                and not named_profile_is_deleted(child)
            ):
                names.append(child.name)
    except OSError:
        pass
    return tuple(names)


def _peer_names(home: Path) -> frozenset[str]:
    registry = load_peer_registry(initiating_home=home)
    return frozenset(
        name
        for name, entry in registry.items()
        if valid_peer_name(name)
        and isinstance(entry, Mapping)
        and isinstance(entry.get("url"), str)
        and bool(str(entry["url"]).strip())
    )


def _validate_config_source(home: Path) -> None:
    try:
        read_user_config_raw(home / "config.yaml", require_mapping=True)
    except Exception as exc:
        raise ValueError("handoff directory configuration is invalid") from exc


def _validate_endpoint(
    endpoint: HandoffEndpoint,
    *,
    local_profiles: frozenset[str],
    peer_names: frozenset[str],
) -> None:
    if endpoint.kind == "local" and endpoint.profile not in local_profiles:
        raise ValueError("handoff directory references an unknown local profile")
    if endpoint.kind == "peer" and endpoint.peer not in peer_names:
        raise ValueError("handoff directory references an unknown peer")


def load_agent_directory(
    initiating_home: str | Path,
) -> tuple[AgentDirectoryEntry, ...]:
    home = Path(initiating_home).expanduser().resolve()
    _validate_config_source(home)
    config = load_config_readonly(config_path=home / "config.yaml") or {}
    handoff = config.get("handoff")
    if not isinstance(handoff, Mapping):
        raise ValueError("handoff directory configuration is invalid")
    agents = handoff.get("agents")
    if not isinstance(agents, Mapping):
        raise ValueError("handoff agent directory is invalid")
    local_profiles = frozenset(_local_profiles(home))
    peer_names = _peer_names(home)
    entries: list[AgentDirectoryEntry] = []
    if not all(
        isinstance(name, str) and _AGENT_NAME.fullmatch(name) for name in agents
    ):
        raise ValueError("handoff agent name is invalid")
    for name in sorted(agents):
        raw = agents[name]
        if not isinstance(raw, Mapping) or set(raw) != {"default", "endpoints"}:
            raise ValueError("handoff agent entry is invalid")
        values = raw["endpoints"]
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 16
            or not all(isinstance(value, str) for value in values)
        ):
            raise ValueError("handoff agent endpoints are invalid")
        endpoints = tuple(HandoffEndpoint.parse(value) for value in values)
        if len({endpoint.canonical for endpoint in endpoints}) != len(endpoints):
            raise ValueError("handoff agent endpoints contain duplicates")
        default = HandoffEndpoint.parse(raw["default"])
        if default not in endpoints:
            raise ValueError("handoff agent default must be one of its endpoints")
        for endpoint in endpoints:
            _validate_endpoint(
                endpoint,
                local_profiles=local_profiles,
                peer_names=peer_names,
            )
        entries.append(AgentDirectoryEntry(name, default, endpoints))
    return tuple(entries)


def _resolved(
    source: ResolutionSource,
    endpoint: HandoffEndpoint,
    *,
    controlled: bool,
) -> ResolvedAgentTarget:
    return ResolvedAgentTarget(
        source=source,
        endpoint=endpoint,
        required_capabilities=(
            CONTROLLED_CONVERSATION_CAPABILITIES if controlled else frozenset()
        ),
        peer=endpoint.peer,
        profile=endpoint.profile,
    )


def resolve_agent_target(
    target: str,
    *,
    initiating_home: str | Path,
    relay_roster: list[dict] | None = None,
) -> ResolvedAgentTarget:
    if not isinstance(target, str) or not target.strip():
        raise ValueError("agent target is required")
    home = Path(initiating_home).expanduser().resolve()
    _validate_config_source(home)
    raw = target.strip()
    local_profiles = frozenset(_local_profiles(home))
    peer_names = _peer_names(home)

    if "://" in raw:
        endpoint = HandoffEndpoint.parse(raw)
        _validate_endpoint(
            endpoint, local_profiles=local_profiles, peer_names=peer_names
        )
        return _resolved("explicit", endpoint, controlled=True)

    friendly = raw.lstrip("@")
    by_name = {entry.name: entry for entry in load_agent_directory(home)}
    entry = by_name.get(friendly.casefold())
    if entry is not None:
        return _resolved("directory", entry.default, controlled=True)

    if "/" in friendly:
        peer, profile = parse_peer_target(friendly)
        if not valid_peer_name(peer) or peer not in peer_names or profile is None:
            raise LookupError("agent peer target is not registered")
        endpoint = HandoffEndpoint.parse(f"hermes://peer/{peer}/{profile}")
        return _resolved("legacy_peer", endpoint, controlled=False)

    local = next(
        (
            profile
            for profile in local_profiles
            if friendly.casefold()
            == ("hermes" if profile == "default" else profile).casefold()
        ),
        None,
    )
    if local is not None:
        return _resolved(
            "legacy_local",
            HandoffEndpoint.parse(f"hermes://local/{local}"),
            controlled=False,
        )

    peer = friendly.casefold()
    if peer in peer_names:
        return ResolvedAgentTarget(
            source="legacy_bare_peer",
            endpoint=None,
            required_capabilities=frozenset(),
            peer=peer,
        )

    from tools.bot_relay import read_remote_roster, resolve_remote_target

    roster = read_remote_roster(_root(home)) if relay_roster is None else relay_roster
    match = resolve_remote_target(friendly, roster)
    if match == "ambiguous":
        wanted = friendly.casefold()
        choices = (
            f"{row['handle']}@{row['connection_id']}"
            for row in roster
            if wanted in {str(row["handle"]).casefold(), str(row["profile"]).casefold()}
        )
        raise AmbiguousAgentTarget(tuple(choices))
    if isinstance(match, Mapping):
        relay_target = f"{match['handle']}@{match['connection_id']}"
        return ResolvedAgentTarget(
            source="relay",
            endpoint=None,
            required_capabilities=frozenset(),
            profile=str(match["profile"]),
            relay_target=relay_target,
        )
    raise LookupError("agent target was not found")


__all__ = [
    "AgentDirectoryEntry",
    "AmbiguousAgentTarget",
    "CONTROLLED_CONVERSATION_CAPABILITIES",
    "ResolvedAgentTarget",
    "load_agent_directory",
    "resolve_agent_target",
]
