from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.handoff.directory import (
    AmbiguousAgentTarget,
    CONTROLLED_CONVERSATION_CAPABILITIES,
    load_agent_directory,
    resolve_agent_target,
)


def _write_config(
    home: Path,
    *,
    agents: dict | None = None,
    peers: dict | None = None,
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "handoff": {"agents": agents or {}},
            "bot_peers": peers or {},
        }),
        encoding="utf-8",
    )


def _agent(default: str, *endpoints: str) -> dict:
    return {"default": default, "endpoints": list(endpoints or (default,))}


def _relay_row(profile: str, connection_id: str) -> dict[str, object]:
    return {
        "profile": profile,
        "handle": profile,
        "connection_id": connection_id,
        "connection_label": connection_id,
        "title": "",
        "description": "",
    }


def test_default_config_has_an_empty_agent_directory():
    assert DEFAULT_CONFIG["handoff"] == {"agents": {}}


@pytest.mark.parametrize("raw", ["handoff: [\n", "[]\n", "true\n", "invalid\n"])
def test_malformed_config_fails_closed_before_legacy_resolution(tmp_path, raw):
    (tmp_path / "profiles" / "reviewer").mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="configuration is invalid"):
        resolve_agent_target("reviewer", initiating_home=tmp_path, relay_roster=[])

    assert config_path.read_text(encoding="utf-8") == raw
    assert list(tmp_path.glob("config.yaml.corrupt.*.bak")) == []


def test_valid_directory_exposes_name_default_and_canonical_endpoints(tmp_path):
    (tmp_path / "profiles" / "reviewer").mkdir(parents=True)
    _write_config(
        tmp_path,
        agents={
            "security-reviewer": _agent(
                "hermes://local/reviewer",
                "hermes://local/reviewer",
                "hermes://peer/spark/reviewer",
            )
        },
        peers={"spark": {"url": "https://peer.example.test"}},
    )

    entries = load_agent_directory(tmp_path)

    assert len(entries) == 1
    assert entries[0].name == "security-reviewer"
    assert entries[0].default.canonical == "hermes://local/reviewer"
    assert [endpoint.canonical for endpoint in entries[0].endpoints] == [
        "hermes://local/reviewer",
        "hermes://peer/spark/reviewer",
    ]


@pytest.mark.parametrize(
    "agents",
    [
        {"Bad Name": _agent("hermes://local/reviewer")},
        {"reviewer": _agent("hermes://local/reviewer"), 1: {}},
        {"reviewer": {"default": "hermes://local/reviewer"}},
        {
            "reviewer": {
                **_agent("hermes://local/reviewer"),
                "token": "secret",
            }
        },
        {
            "reviewer": _agent(
                "hermes://local/reviewer",
                "hermes://local/reviewer",
                "hermes://local/reviewer",
            )
        },
        {
            "reviewer": _agent(
                "hermes://local/reviewer", "hermes://local/other"
            )
        },
        {"reviewer": _agent("https://peer.example.test/reviewer")},
        {"reviewer": _agent("hermes://local/reviewer?token=secret")},
        {"reviewer": _agent("hermes://local/missing")},
        {"reviewer": _agent("hermes://peer/missing/reviewer")},
    ],
)
def test_directory_rejects_invalid_or_unresolved_entries(tmp_path, agents):
    (tmp_path / "profiles" / "reviewer").mkdir(parents=True)
    _write_config(
        tmp_path,
        agents=agents,
        peers={"spark": {"url": "https://peer.example.test"}},
    )

    with pytest.raises(ValueError):
        load_agent_directory(tmp_path)


def test_resolution_order_and_compatibility_intent_are_deterministic(tmp_path):
    (tmp_path / "profiles" / "reviewer").mkdir(parents=True)
    (tmp_path / "profiles" / "local-only").mkdir()
    _write_config(
        tmp_path,
        agents={
            "reviewer": _agent("hermes://peer/spark/reviewer"),
        },
        peers={"spark": {"url": "https://peer.example.test"}},
    )
    relay = [_relay_row("remote-only", "cloud")]

    explicit = resolve_agent_target(
        "hermes://local/local-only", initiating_home=tmp_path, relay_roster=relay
    )
    alias = resolve_agent_target(
        "reviewer", initiating_home=tmp_path, relay_roster=relay
    )
    legacy_peer = resolve_agent_target(
        "spark/reviewer", initiating_home=tmp_path, relay_roster=relay
    )
    local = resolve_agent_target(
        "local-only", initiating_home=tmp_path, relay_roster=relay
    )
    bare_peer = resolve_agent_target(
        "spark", initiating_home=tmp_path, relay_roster=relay
    )
    relayed = resolve_agent_target(
        "remote-only", initiating_home=tmp_path, relay_roster=relay
    )

    assert explicit.source == "explicit"
    assert explicit.endpoint.canonical == "hermes://local/local-only"
    assert explicit.required_capabilities == CONTROLLED_CONVERSATION_CAPABILITIES
    assert alias.source == "directory"
    assert alias.endpoint.canonical == "hermes://peer/spark/reviewer"
    assert alias.required_capabilities == CONTROLLED_CONVERSATION_CAPABILITIES
    assert legacy_peer.source == "legacy_peer"
    assert legacy_peer.endpoint.canonical == "hermes://peer/spark/reviewer"
    assert legacy_peer.required_capabilities == frozenset()
    assert local.source == "legacy_local"
    assert local.endpoint.canonical == "hermes://local/local-only"
    assert local.required_capabilities == frozenset()
    assert bare_peer.source == "legacy_bare_peer"
    assert bare_peer.endpoint is None
    assert bare_peer.peer == "spark"
    assert bare_peer.profile is None
    assert bare_peer.required_capabilities == frozenset()
    assert relayed.source == "relay"
    assert relayed.endpoint is None
    assert relayed.relay_target == "remote-only@cloud"
    assert relayed.required_capabilities == frozenset()


def test_named_profile_reads_only_its_own_directory_and_peer_registry(tmp_path):
    root = tmp_path / "root"
    initiating_home = root / "profiles" / "sender"
    (root / "profiles" / "reviewer").mkdir(parents=True)
    _write_config(
        root,
        agents={"root-only": _agent("hermes://local/reviewer")},
    )
    _write_config(
        initiating_home,
        agents={"remote": _agent("hermes://peer/spark/reviewer")},
        peers={"spark": {"url": "https://peer.example.test"}},
    )

    resolved = resolve_agent_target("remote", initiating_home=initiating_home)

    assert resolved.endpoint.canonical == "hermes://peer/spark/reviewer"
    with pytest.raises(LookupError):
        resolve_agent_target("root-only", initiating_home=initiating_home)


def test_ambiguous_relay_target_returns_stable_qualified_choices(tmp_path):
    _write_config(tmp_path)
    roster = [_relay_row("reviewer", "cloud"), _relay_row("reviewer", "lab")]

    with pytest.raises(AmbiguousAgentTarget) as raised:
        resolve_agent_target(
            "reviewer", initiating_home=tmp_path, relay_roster=roster
        )

    assert raised.value.choices == ("reviewer@cloud", "reviewer@lab")


@pytest.mark.parametrize(
    "target",
    [
        "https://peer.example.test/reviewer",
        "hermes://local/reviewer?token=secret",
        "hermes://peer/user:pass@spark/reviewer",
        "spark/../reviewer",
        "unknown",
    ],
)
def test_resolution_rejects_unsafe_noncanonical_and_unknown_targets(
    tmp_path, target
):
    (tmp_path / "profiles" / "reviewer").mkdir(parents=True)
    _write_config(
        tmp_path,
        peers={"spark": {"url": "https://peer.example.test"}},
    )

    with pytest.raises((LookupError, ValueError)):
        resolve_agent_target(target, initiating_home=tmp_path, relay_roster=[])
