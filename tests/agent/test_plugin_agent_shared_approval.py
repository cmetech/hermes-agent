from __future__ import annotations

import hashlib
import json
import threading

import pytest

import agent.plugin_agent as plugin_agent
from agent.plugin_agent import PluginAgentRunRequest


def _action_digest(command: str, description: str) -> str:
    return hashlib.sha256(
        json.dumps(
            ["approval", {"command": command, "description": description}],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_shared_approval_authority_consumes_one_matching_action_under_race() -> None:
    digest = _action_digest("publish artifact", "outward effect")
    authority = plugin_agent._ProviderAttemptAuthority(
        3,
        approved_action_digest=digest,
    )
    barrier = threading.Barrier(9)
    results: list[bool] = []
    failures: list[BaseException] = []

    def consume() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                plugin_agent._consume_shared_approved_action(
                    authority.descriptor,
                    digest,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    try:
        assert (
            plugin_agent._consume_shared_approved_action(
                authority.descriptor,
                "0" * 64,
            )
            is False
        )
        threads = [threading.Thread(target=consume) for _index in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        assert failures == []
        assert sum(results) == 1
        assert len(results) == 8
    finally:
        authority.close()


def test_worker_siblings_share_approval_consumption_instead_of_local_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent
    from tools.terminal_tool import _get_approval_callback

    command = "publish artifact"
    description = "outward effect"
    digest = _action_digest(command, description)
    authority = plugin_agent._ProviderAttemptAuthority(
        3,
        approved_action_digest=digest,
    )
    observed: list[str] = []

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **_kwargs):
            self.session_id = "inline-child"
            self.provider = "fake"
            self.model = "fake"
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0

        def run_conversation(self, _prompt, conversation_history=None):
            callback = _get_approval_callback()
            assert callback is not None
            observed.append(callback(command, description))
            return {"final_response": observed[-1], "api_calls": 0}

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {"provider": "fake", "base_url": "", "api_key": "x"},
    )
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    request = PluginAgentRunRequest(
        prompt="perform one outward effect",
        allowed_tools=(),
        approved_action_digest=digest,
        _provider_attempt_authority=authority.descriptor,
    )

    try:
        first = worker._run({
            "plugin_id": "workflow",
            "request": request.to_wire(),
        })
        second = worker._run({
            "plugin_id": "workflow",
            "request": request.to_wire(),
        })
    finally:
        authority.close()

    assert first["status"] == "completed"
    assert second["status"] == "paused"
    assert observed == ["once", "deny"]
    assert second["pending_interaction"]["action_digest"] == digest


def test_top_level_inline_exchange_owns_and_closes_shared_approval_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    digest = _action_digest("publish artifact", "outward effect")
    captured: dict[str, object] = {}

    def exchange_once(payload, **_kwargs):
        request = payload["request"]
        descriptor = request.get("_provider_attempt_authority")
        assert descriptor is not None
        captured["descriptor"] = descriptor
        assert plugin_agent._consume_shared_approved_action(descriptor, digest) is True
        return {
            "result": {
                "final_response": "done",
                "session_id": "parent",
                "provider": "",
                "model": "",
                "status": "completed",
                "pending_interaction": None,
                "usage": {},
                "audit": {},
                "structured_output": None,
            }
        }

    monkeypatch.setattr(plugin_agent, "_exchange_worker_once", exchange_once)
    payload = plugin_agent._request_payload(
        "workflow",
        PluginAgentRunRequest(
            prompt="coordinate children",
            approved_action_digest=digest,
            inline_agents={"reviewer": {"prompt": "review"}},
            workdir=tmp_path,
        ),
    )

    frame = plugin_agent._exchange_worker(
        payload,
        workdir=tmp_path,
        idle_timeout_seconds=5,
        wall_timeout_seconds=5,
    )

    assert frame["result"]["status"] == "completed"
    with pytest.raises(RuntimeError, match="authority unavailable"):
        plugin_agent._consume_shared_approved_action(
            captured["descriptor"],
            digest,
        )
