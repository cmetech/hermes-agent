from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import tui_gateway.server as server
from hermes_cli.handoff.models import ChannelObservation, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.service import AdvanceResult, AgentHandoffService
from hermes_cli.handoff.store import HandoffStore


def _call(method: str, params: dict, *, rid: str = "r1") -> dict:
    return server._methods[method](rid, params)


def _result(response: dict) -> dict:
    assert "error" not in response, response
    return response["result"]


def _error_code(response: dict) -> str:
    assert "error" in response, response
    return response["error"]["data"]["code"]


@pytest.fixture
def profile_home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "ops"
    reviewer = root / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    reviewer.mkdir(parents=True)
    profile.joinpath("config.yaml").write_text(
        """handoff:
  agents:
    reviewer:
      default: hermes://local/reviewer
      endpoints:
        - hermes://local/reviewer
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(server, "_hermes_home", root)
    return profile


@pytest.fixture
def no_advance(monkeypatch):
    def _advance(self, handoff_id, *, budget_seconds=2.0):
        return AdvanceResult(self.get(handoff_id), None, False)

    monkeypatch.setattr(AgentHandoffService, "advance", _advance)


def test_agent_handoff_namespace_coexists_and_registers_once():
    expected = {
        "agent_handoff.create",
        "agent_handoff.get",
        "agent_handoff.list",
        "agent_handoff.evidence",
        "agent_handoff.command",
        "agent_handoff.directory",
    }
    assert expected <= server._methods.keys()
    assert {
        "handoff.request",
        "handoff.state",
        "handoff.fail",
    } <= server._methods.keys()
    assert len(expected) == len([name for name in server._methods if name in expected])


def test_directory_and_create_use_selected_profile_and_close_each_store(
    profile_home, no_advance, monkeypatch
):
    closed = []
    real_close = HandoffStore.close

    def _close(store):
        closed.append(store.path)
        real_close(store)

    monkeypatch.setattr(HandoffStore, "close", _close)

    directory = _result(_call("agent_handoff.directory", {"profile": "ops"}))
    created = _result(
        _call(
            "agent_handoff.create",
            {
                "profile": "ops",
                "target": "reviewer",
                "message": "Please review the release.",
                "request_id": "desktop-create-1",
            },
        )
    )

    assert directory == {
        "agents": [
            {
                "name": "reviewer",
                "default": "hermes://local/reviewer",
                "endpoints": ["hermes://local/reviewer"],
            }
        ]
    }
    assert created["endpoint"] == "hermes://local/reviewer"
    assert created["phase"] == "prepared"
    handoff_id = created["handoff_id"]
    _result(_call("agent_handoff.get", {"profile": "ops", "handoff_id": handoff_id}))
    _result(_call("agent_handoff.list", {"profile": "ops"}))
    _result(
        _call("agent_handoff.evidence", {"profile": "ops", "handoff_id": handoff_id})
    )
    _result(
        _call(
            "agent_handoff.command",
            {
                "profile": "ops",
                "handoff_id": handoff_id,
                "kind": "acknowledge",
                "command_id": "desktop-close-check",
            },
        )
    )
    assert closed == [profile_home / "handoffs.db"] * 6
    assert (profile_home / "handoffs.db").exists()
    assert not (profile_home.parent.parent / "handoffs.db").exists()


def test_create_replays_same_request_and_conflicting_reuse_fails(
    profile_home, no_advance
):
    params = {
        "profile": "ops",
        "target": "hermes://local/reviewer",
        "message": "Review this.",
        "request_id": "desktop-create-2",
    }

    first = _result(_call("agent_handoff.create", params))
    replay = _result(_call("agent_handoff.create", params))
    conflict = _call(
        "agent_handoff.create", {**params, "message": "Different payload."}
    )

    assert replay["handoff_id"] == first["handoff_id"]
    assert _error_code(conflict) == "handoff_conflict"
    with HandoffStore(profile_home / "handoffs.db") as store:
        snapshot = store.get(first["handoff_id"])
        assert snapshot.key_scope == "operator/ops"
        assert snapshot.handoff_key == "desktop-create-2"
        assert snapshot.spec.return_route == {
            "kind": "operator",
            "profile": "ops",
            "inbox_id": snapshot.spec.return_route["inbox_id"],
        }
        assert snapshot.spec.attribution == {"profile": "ops", "source": "desktop"}


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"target": "http://peer.invalid"}, "invalid_argument"),
        ({"target": "x" * 513}, "invalid_argument"),
        ({"message": "x" * 16001}, "invalid_argument"),
        ({"profile": "../ops"}, "profile_unavailable"),
        ({"actor": "admin"}, "invalid_argument"),
        ({"key_scope": "operator/admin"}, "invalid_argument"),
        ({"return_route": {}}, "invalid_argument"),
        ({"hop_count": 0}, "invalid_argument"),
        ({"profile_home": "/private/profile"}, "invalid_argument"),
        ({"url": "https://peer.invalid"}, "invalid_argument"),
        ({"credential": "secret"}, "invalid_argument"),
        ({"authorization": "Bearer secret"}, "invalid_argument"),
        ({"peer_token": "secret"}, "invalid_argument"),
        ({"transport_id": "socket-1"}, "invalid_argument"),
    ],
)
def test_create_rejects_renderer_authority_and_transport_fields(
    profile_home, no_advance, changes, code
):
    params = {
        "profile": "ops",
        "target": "reviewer",
        "message": "Review this.",
        "request_id": "desktop-invalid",
        **changes,
    }

    assert _error_code(_call("agent_handoff.create", params)) == code


def test_list_get_and_evidence_use_shared_projection(profile_home, no_advance):
    created = _result(
        _call(
            "agent_handoff.create",
            {
                "profile": "ops",
                "target": "reviewer",
                "message": "Review this.",
                "request_id": "desktop-read-1",
            },
        )
    )
    handoff_id = created["handoff_id"]

    listed = _result(_call("agent_handoff.list", {"profile": "ops", "limit": 10}))
    fetched = _result(
        _call("agent_handoff.get", {"profile": "ops", "handoff_id": handoff_id})
    )
    evidence = _result(
        _call(
            "agent_handoff.evidence",
            {"profile": "ops", "handoff_id": handoff_id, "limit": 10},
        )
    )

    assert listed["handoffs"] == [fetched]
    assert set(fetched) >= {
        "handoff_id",
        "endpoint",
        "mechanism",
        "phase",
        "age_seconds",
        "terminal_summary",
        "needs_attention",
    }
    assert fetched["actions"] == ["cancel"]
    assert evidence["handoff_id"] == handoff_id
    assert evidence["events"][0]["kind"] == "created"
    assert "prompt" not in repr((listed, fetched, evidence))


def test_command_derives_operator_actor_and_is_idempotent(profile_home, no_advance):
    created = _result(
        _call(
            "agent_handoff.create",
            {
                "profile": "ops",
                "target": "reviewer",
                "message": "Review this.",
                "request_id": "desktop-command-create",
            },
        )
    )
    params = {
        "profile": "ops",
        "handoff_id": created["handoff_id"],
        "kind": "cancel",
        "command_id": "desktop-command-1",
    }

    first = _result(_call("agent_handoff.command", params))
    replay = _result(_call("agent_handoff.command", params))
    conflict = _call("agent_handoff.command", {**params, "kind": "reconcile"})

    assert replay == first
    assert _error_code(conflict) == "handoff_conflict"
    with HandoffStore(profile_home / "handoffs.db") as store:
        command = store.get_command(created["handoff_id"], "desktop-command-1")
        assert command.payload["actor"] == "operator"


@pytest.mark.parametrize(
    "kind", ["respond", "message", "cancel", "reconcile", "acknowledge"]
)
def test_command_accepts_only_closed_operator_kinds(profile_home, no_advance, kind):
    created = _result(
        _call(
            "agent_handoff.create",
            {
                "profile": "ops",
                "target": "reviewer",
                "message": "Review this.",
                "request_id": f"desktop-{kind}-create",
            },
        )
    )
    params = {
        "profile": "ops",
        "handoff_id": created["handoff_id"],
        "kind": kind,
        "command_id": f"desktop-{kind}-1",
    }
    if kind == "respond":
        params.update({"request_id": "approval-1", "choice": "once"})
    elif kind == "message":
        params.update({"text": "More detail", "correlation_id": "follow-up-1"})

    response = _call("agent_handoff.command", params)

    if kind in {"respond", "message"}:
        assert _error_code(response) == "capability_mismatch"
    else:
        assert "error" not in response, response

    rejected = _call(
        "agent_handoff.command",
        {
            "profile": "ops",
            "handoff_id": created["handoff_id"],
            "kind": "steer",
            "command_id": "desktop-steer-1",
            "text": "forged",
        },
    )
    assert _error_code(rejected) == "invalid_argument"


def test_command_rejects_renderer_actor_and_cross_profile_ownership(
    profile_home, no_advance
):
    created = _result(
        _call(
            "agent_handoff.create",
            {
                "profile": "ops",
                "target": "reviewer",
                "message": "Review this.",
                "request_id": "desktop-owner-create",
            },
        )
    )
    forged = _call(
        "agent_handoff.command",
        {
            "profile": "ops",
            "handoff_id": created["handoff_id"],
            "kind": "cancel",
            "command_id": "desktop-owner-command",
            "actor": "admin",
        },
    )
    other_profile = _call(
        "agent_handoff.get",
        {"profile": "default", "handoff_id": created["handoff_id"]},
    )

    assert _error_code(forged) == "invalid_argument"
    assert _error_code(other_profile) == "handoff_not_found"

    malformed = _call(
        "agent_handoff.command",
        {
            "profile": "ops",
            "handoff_id": created["handoff_id"],
            "kind": [],
            "command_id": "desktop-malformed-command",
        },
    )
    assert _error_code(malformed) == "invalid_argument"


def test_needs_input_projection_advertises_only_exact_approval_choices(profile_home):
    spec = HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review this.",
        output_schema=None,
        deadline_at=None,
        attribution={"profile": "ops", "source": "desktop"},
        required_capabilities=frozenset({"approval"}),
        return_route={"kind": "operator", "profile": "ops", "inbox_id": "desktop"},
    )
    with HandoffStore(profile_home / "handoffs.db") as store:
        snapshot = store.create_or_get(
            "operator/ops", "approval", spec, spec.fingerprint
        )
        lease = store.claim_advance(
            snapshot.handoff_id,
            "test",
            now=datetime.now(timezone.utc),
            lease_seconds=30,
        )
        store.commit_binding(
            lease,
            "runs",
            {
                "profile": "reviewer",
                "mechanism": "runs",
                "capabilities": [
                    "approval",
                    "authoritative_status",
                    "durable_admission",
                ],
            },
            {},
        )
        store.journal_attempt(lease, "submit")
        store.commit_observation(
            lease,
            ChannelObservation(
                phase="needs_input",
                checkpoint={
                    "run_id": "run-1",
                    "approval_request_id": "approval-1",
                    "approval_choices": ["once", "deny"],
                },
            ),
        )
        store.release_advance(lease, next_advance_at=None)

    projected = _result(
        _call(
            "agent_handoff.get",
            {"profile": "ops", "handoff_id": snapshot.handoff_id},
        )
    )

    assert projected["needs_attention"] is True
    assert projected["approval"] == {
        "request_id": "approval-1",
        "choices": ["once", "deny"],
    }
    assert projected["actions"] == ["respond", "cancel", "acknowledge"]


def test_only_mutating_or_network_agent_handoff_handlers_use_rpc_pool():
    assert {"agent_handoff.create", "agent_handoff.command"} <= server._LONG_HANDLERS
    assert (
        not {
            "agent_handoff.get",
            "agent_handoff.list",
            "agent_handoff.evidence",
            "agent_handoff.directory",
        }
        & server._LONG_HANDLERS
    )
