from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading

import pytest

from agent import secret_scope
from hermes_cli.handoff import AgentHandoffService, HandoffStore
from tests.hermes_cli.handoff import test_local_runs as local_gateway
from tools import bot_mode_dm, bot_mode_probe


class _TitleDB:
    def __init__(self, home: Path) -> None:
        self.db_path = str(home / "state.db")

    def get_session_title(self, _session_id: str) -> str:
        return "Bot Chat"


class _BotCaller:
    def __init__(self, home: Path) -> None:
        self._session_db = _TitleDB(home)
        self._session_title_hint = None
        self._bot_mode_protocol = True
        self._gateway_session_key = "agent:default:telegram:dm:42"
        self._current_turn_id = "turn-1"
        self.session_id = "bot-session-1"


class _RecordingAgent(local_gateway._Agent):
    def __init__(self, *, blocked: bool = False, approval: bool = False) -> None:
        super().__init__("review complete")
        self.approval = approval
        self.calls: list[str] = []
        self.interrupted = threading.Event()
        self.release = threading.Event()
        self.started = threading.Event()
        self.steers: list[str] = []
        if not blocked and not approval:
            self.release.set()

    def interrupt(self, _message=None) -> None:
        self.interrupted.set()
        self.release.set()

    def steer(self, text: str) -> bool:
        self.steers.append(text)
        return True

    def run_conversation(self, **kwargs):
        self.calls.append(kwargs["user_message"])
        self.started.set()
        if self.approval:
            from tools import approval

            session_key = approval.get_current_session_key()
            with approval._lock:
                notify = approval._gateway_notify_cbs[session_key]
            decision = approval._await_gateway_decision(
                session_key,
                notify,
                {
                    "command": "review release",
                    "description": "approve the review",
                    "pattern_key": "stage3-review",
                    "pattern_keys": ["stage3-review"],
                    "allow_permanent": False,
                    "allow_session": False,
                },
            )
            return {"final_response": f"approval:{decision['choice']}"}
        assert self.release.wait(timeout=10)
        if self.interrupted.is_set():
            return {"final_response": "interrupted", "interrupted": True}
        return {"final_response": self.output}


@pytest.fixture
def bot_profiles(tmp_path, monkeypatch):
    default_home = tmp_path / ".hermes"
    reviewer_home = default_home / "profiles" / "reviewer"
    reviewer_home.mkdir(parents=True)
    reviewer_home.joinpath("profile.yaml").write_text(
        "description: release reviewer\nui_meta:\n  hermes-bots: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    bot_mode_probe._reset_cache_for_tests()
    secret_scope.set_multiplex_active(True)
    yield default_home, reviewer_home
    secret_scope.set_multiplex_active(False)
    bot_mode_probe._reset_cache_for_tests()


async def _advance_until(service, handoff_id: str, phases: set[str]):
    snapshot = service.get(handoff_id)
    for _ in range(60):
        snapshot = (await asyncio.to_thread(service.advance, handoff_id)).snapshot
        if snapshot.phase in phases:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"handoff stayed in {snapshot.phase}")


@pytest.mark.asyncio
async def test_canonical_bot_handoff_uses_real_profile_runs_and_stable_admission(
    bot_profiles,
):
    default_home, _reviewer_home = bot_profiles
    destination = _RecordingAgent()
    async with local_gateway._gateway(
        bot_profiles, agent=lambda **_kwargs: destination
    ) as (adapter, server):
        caller = _BotCaller(default_home)
        first = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Review the release.",
                tool_call_id="call-stage3-local",
                agent=caller,
            )
        )

        service = AgentHandoffService(HandoffStore(default_home / "handoffs.db"))
        completed = await _advance_until(service, first["handoff_id"], {"succeeded"})
        replay = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Review the release.",
                tool_call_id="call-stage3-local",
                agent=caller,
            )
        )
        conflict = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Different payload.",
                tool_call_id="call-stage3-local",
                agent=caller,
            )
        )

        assert first["status"] == "prepared"
        assert replay["handoff_id"] == first["handoff_id"]
        assert "error" in conflict
        assert completed.mechanism == "runs"
        assert (
            completed.checkpoint["idempotency_key"] == f"handoff-{completed.handoff_id}"
        )
        assert completed.spec.return_route == {
            "kind": "bot",
            "profile": "default",
            "session_id": "bot-session-1",
            "session_key": "agent:default:telegram:dm:42",
            "tool_call_id": "call-stage3-local",
            "delivery_policy": "wake",
            "hop_count": 0,
        }
        assert destination.calls == [
            "Message from 🤖 hermes (@hermes): Review the release."
        ]
        assert (
            adapter._run_idempotency_store._conn.execute(
                "SELECT count(*) FROM run_idempotency"
            ).fetchone()[0]
            == 1
        )

        sessions = await asyncio.to_thread(
            local_gateway._json_request,
            f"http://127.0.0.1:{server.port}/p/reviewer/api/sessions"
            "?title=Bot%20Chat&include_hidden=1",
            local_gateway.TARGET_KEY,
        )
        assert [(item["id"], item["title"]) for item in sessions["data"]] == [
            (completed.checkpoint["session_id"], "Bot Chat")
        ]
        service.store.close()


@pytest.mark.asyncio
async def test_active_bot_handoff_delivers_follow_up_and_cooperative_stop(
    bot_profiles,
):
    default_home, _reviewer_home = bot_profiles
    destination = _RecordingAgent(blocked=True)
    async with local_gateway._gateway(
        bot_profiles, agent=lambda **_kwargs: destination
    ) as (adapter, _server):
        caller = _BotCaller(default_home)
        created = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Start the review.",
                tool_call_id="call-stage3-active",
                agent=caller,
            )
        )
        service = AgentHandoffService(HandoffStore(default_home / "handoffs.db"))
        active = await _advance_until(service, created["handoff_id"], {"active"})
        assert await asyncio.to_thread(destination.started.wait, 3)

        follow_up = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Check the migration note.",
                handoff_id=active.handoff_id,
                tool_call_id="call-stage3-follow-up",
                agent=caller,
            )
        )
        assert follow_up["handoff_id"] == active.handoff_id
        assert destination.steers == ["Check the migration note."]

        service.command(
            active.handoff_id,
            "cancel",
            command_id="cancel-stage3-active",
            actor="bot",
        )
        terminal = await _advance_until(service, active.handoff_id, {"cancelled"})
        await local_gateway._wait_for_run_task(adapter, terminal.checkpoint["run_id"])

        assert destination.interrupted.is_set()
        assert service.store.attention(active.handoff_id, limit=10)[0].method == "wake"
        service.store.close()


@pytest.mark.asyncio
async def test_bot_handoff_round_trips_exact_remote_approval_choice(bot_profiles):
    default_home, _reviewer_home = bot_profiles
    destination = _RecordingAgent(approval=True)
    async with local_gateway._gateway(
        bot_profiles, agent=lambda **_kwargs: destination
    ):
        caller = _BotCaller(default_home)
        created = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Review with approval.",
                tool_call_id="call-stage3-approval",
                agent=caller,
            )
        )
        service = AgentHandoffService(HandoffStore(default_home / "handoffs.db"))
        waiting = await _advance_until(service, created["handoff_id"], {"needs_input"})

        assert waiting.checkpoint["approval_choices"] == ("once", "deny")
        answered = json.loads(
            await asyncio.to_thread(
                bot_mode_dm.message_agent_tool,
                target="hermes://local/reviewer",
                message="Once",
                handoff_id=waiting.handoff_id,
                tool_call_id="call-stage3-approval-answer",
                agent=caller,
            )
        )
        completed = await _advance_until(service, waiting.handoff_id, {"succeeded"})

        assert answered["handoff_id"] == waiting.handoff_id
        assert completed.terminal_result["text"] == "approval:once"
        assert (
            service.store.get_command(
                waiting.handoff_id, "call-stage3-approval-answer"
            ).delivery_state
            == "delivered"
        )
        service.store.close()
