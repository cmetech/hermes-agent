"""Behavior contracts for profile-scoped registered peer resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli import peers
from hermes_cli.handoff.runs import RunsClient, RunsConnection, RunsDeadline


def _write_profile(
    home: Path,
    *,
    peer: str = "spark",
    url: str = "https://gateway.example.test/hermes",
    key: str = "profile-peer-key-123456",
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"bot_peers:\n  {peer}:\n    url: {url}\n",
        encoding="utf-8",
    )
    (home / ".env").write_text(
        f"HERMES_PEER_{peer.upper().replace('-', '_')}_KEY={key}\n",
        encoding="utf-8",
    )


def test_resolve_peer_uses_only_initiating_profile_registry_and_secret(
    tmp_path, monkeypatch
):
    initiating_home = tmp_path / "initiating"
    unrelated_home = tmp_path / "unrelated"
    _write_profile(initiating_home, key="initiating-profile-key")
    _write_profile(unrelated_home, key="unrelated-profile-key")
    monkeypatch.setenv("HERMES_PEER_SPARK_KEY", "ambient-process-key")
    monkeypatch.setenv("HERMES_HOME", str(unrelated_home))

    resolved = peers.resolve_peer(
        "spark", "reviewer", initiating_home=initiating_home
    )

    assert resolved.name == "spark"
    assert resolved.profile == "reviewer"
    assert resolved.profile_base_url == (
        "https://gateway.example.test/hermes/p/reviewer"
    )
    assert resolved.key == "initiating-profile-key"
    assert "initiating-profile-key" not in repr(resolved)
    assert "gateway.example.test" not in repr(resolved)
    assert os.environ["HERMES_HOME"] == str(unrelated_home)


def test_resolve_peer_digests_detect_destination_and_credential_changes(tmp_path):
    first_home = tmp_path / "first"
    retargeted_home = tmp_path / "retargeted"
    rotated_home = tmp_path / "rotated"
    _write_profile(first_home)
    _write_profile(retargeted_home, url="https://other.example.test/hermes")
    _write_profile(rotated_home, key="rotated-profile-key-123456")

    first = peers.resolve_peer("spark", "reviewer", initiating_home=first_home)
    replay = peers.resolve_peer("spark", "reviewer", initiating_home=first_home)
    retargeted = peers.resolve_peer(
        "spark", "reviewer", initiating_home=retargeted_home
    )
    rotated = peers.resolve_peer("spark", "reviewer", initiating_home=rotated_home)

    assert replay.origin_sha256 == first.origin_sha256
    assert replay.auth_scope_sha256 == first.auth_scope_sha256
    assert retargeted.origin_sha256 != first.origin_sha256
    assert retargeted.auth_scope_sha256 == first.auth_scope_sha256
    assert rotated.origin_sha256 == first.origin_sha256
    assert rotated.auth_scope_sha256 != first.auth_scope_sha256
    assert len(first.origin_sha256) == len(first.auth_scope_sha256) == 64


@pytest.mark.parametrize(
    "url",
    [
        "ftp://gateway.example.test",
        "http:///missing-host",
        "https://user@gateway.example.test",
        "https://user:password@gateway.example.test",
        "https://gateway.example.test?route=unsafe",
        "https://gateway.example.test#fragment",
        "https://gateway.example.test\\@other.example.test",
    ],
)
def test_resolve_peer_rejects_unsafe_registered_urls(tmp_path, url: str):
    home = tmp_path / "profile"
    _write_profile(home, url=url)

    with pytest.raises(ValueError):
        peers.resolve_peer("spark", "reviewer", initiating_home=home)


def test_resolve_peer_rejects_unknown_peer_and_unusable_credentials(tmp_path):
    home = tmp_path / "profile"
    _write_profile(home, key="short")

    with pytest.raises(LookupError):
        peers.resolve_peer("missing", "reviewer", initiating_home=home)
    with pytest.raises(PermissionError):
        peers.resolve_peer("spark", "reviewer", initiating_home=home)


def test_legacy_peer_helpers_preserve_bare_and_profile_target_behavior():
    entry = {"url": "http://spark.lan:8377/"}

    assert peers.peer_key_env("review-host") == "HERMES_PEER_REVIEW_HOST_KEY"
    assert peers.parse_peer_target("spark") == ("spark", None)
    assert peers.parse_peer_target("spark/Researcher") == ("spark", "Researcher")
    assert peers.peer_base_url(entry, None) == "http://spark.lan:8377"
    assert (
        peers.peer_base_url(entry, "researcher")
        == "http://spark.lan:8377/p/researcher"
    )


def test_shared_peer_dm_uses_canonical_session_and_bounded_chat(monkeypatch):
    client = RunsClient(
        RunsConnection("https://peer.example.test", "secret"), RunsDeadline(2)
    )
    calls = []
    monkeypatch.setattr(
        client,
        "ensure_session",
        lambda title, *, source: calls.append(("session", title, source))
        or "session-1",
    )
    monkeypatch.setattr(
        client,
        "chat",
        lambda session_id, message: calls.append(("chat", session_id, message))
        or {"session_id": session_id, "message": {"content": "done"}},
    )

    result = peers.peer_dm_request(client, "hello")

    assert result == {"session_id": "session-1", "reply": "done"}
    assert calls == [
        ("session", "Bot Chat", "bot_peer_dm"),
        ("chat", "session-1", "hello"),
    ]
    with pytest.raises(ValueError, match="message"):
        peers.peer_dm_request(client, "x" * 500_001)
