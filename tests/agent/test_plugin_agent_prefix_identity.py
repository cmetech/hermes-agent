from __future__ import annotations

import model_tools
import pytest
import run_agent
from agent.plugin_agent import PluginAgentRunRequest, _validate_request


def test_model_visible_prefix_digest_binds_exact_prompt_tool_schema_and_order() -> None:
    digest = model_tools.model_visible_prefix_digest
    first_tools = [
        {
            "type": "function",
            "function": {
                "name": "inspect",
                "description": "Inspect one object.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish the task.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    baseline = digest("system bytes", first_tools)

    assert len(baseline) == 64
    assert digest("changed system bytes", first_tools) != baseline
    changed_description = [dict(first_tools[0]), first_tools[1]]
    changed_description[0] = {
        **changed_description[0],
        "function": {
            **changed_description[0]["function"],
            "description": "Changed description.",
        },
    }
    assert digest("system bytes", changed_description) != baseline
    assert digest("system bytes", list(reversed(first_tools))) != baseline
    assert digest("system bytes", first_tools) == digest(
        "system bytes",
        [
            {
                "function": {
                    "parameters": {
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                        "type": "object",
                    },
                    "description": "Inspect one object.",
                    "name": "inspect",
                },
                "type": "function",
            },
            first_tools[1],
        ],
    )


def test_agent_seals_one_render_and_detects_later_prefix_mutation() -> None:
    class Candidate:
        _cached_system_prompt = None
        tools = [{"type": "function", "function": {"name": "inspect"}}]
        render_calls = 0

        def _build_system_prompt(self):
            self.render_calls += 1
            return "rendered once"

    candidate = Candidate()

    digest = run_agent.AIAgent.seal_model_visible_prefix(candidate)

    assert candidate.render_calls == 1
    assert candidate._cached_system_prompt == "rendered once"
    assert run_agent.AIAgent.verify_model_visible_prefix(candidate) == digest
    candidate.tools = [
        {
            "type": "function",
            "function": {"name": "inspect", "description": "schema drift"},
        }
    ]
    with pytest.raises(RuntimeError, match="model-visible prefix changed"):
        run_agent.AIAgent.verify_model_visible_prefix(candidate)


def test_plugin_agent_request_round_trips_both_phase5_session_identities() -> None:
    request = PluginAgentRunRequest(
        prompt="continue",
        context_mode="shared",
        session_id="session-1",
        intended_authority_digest="a" * 64,
        expected_model_visible_prefix_digest="b" * 64,
        expected_runtime_identity={
            "provider": "sealed-provider",
            "model": "sealed-model",
            "api_mode": "chat_completions",
            "base_url_trust_class": "provider_default",
            "registration_provenance_digest": "c" * 64,
        },
    )

    decoded = PluginAgentRunRequest.from_wire(request.to_wire())

    _validate_request(decoded)
    assert decoded.intended_authority_digest == "a" * 64
    assert decoded.expected_model_visible_prefix_digest == "b" * 64
    assert decoded.expected_runtime_identity == request.expected_runtime_identity


def test_phase5_worker_blocks_runtime_identity_drift_before_agent_construction(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider

    constructed = []

    class ForbiddenAgent:
        def __init__(self, **_kwargs):
            constructed.append(True)
            raise AssertionError("agent constructed after runtime identity drift")

    real_classify = runtime_provider.classify_resolved_execution_runtime

    def changed_runtime(runtime, **kwargs):
        classified = real_classify(runtime, **kwargs)
        return type(classified)(
            **{
                **{
                    name: getattr(classified, name)
                    for name in classified.__dataclass_fields__
                },
                "api_mode": "codex_responses",
            }
        )

    monkeypatch.setattr(run_agent, "AIAgent", ForbiddenAgent)
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "sealed-provider",
            "model": "sealed-model",
            "api_mode": "chat_completions",
            "base_url": "",
            "api_key": "credential",
        },
    )
    monkeypatch.setattr(
        runtime_provider, "classify_resolved_execution_runtime", changed_runtime
    )

    result = worker._run({
        "plugin_id": "workflow",
        "request": PluginAgentRunRequest(
            prompt="continue",
            provider="sealed-provider",
            model="sealed-model",
            allowed_tools=(),
            intended_authority_digest="a" * 64,
            expected_runtime_identity={
                "provider": "sealed-provider",
                "model": "sealed-model",
                "api_mode": "chat_completions",
                "base_url_trust_class": "provider_default",
                "registration_provenance_digest": "c" * 64,
            },
        ).to_wire(),
    })

    assert constructed == []
    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["provider_attempts"] == 0


def test_prefix_expectation_without_sealed_authority_is_rejected() -> None:
    request = PluginAgentRunRequest(
        prompt="continue",
        context_mode="shared",
        session_id="session-1",
        expected_model_visible_prefix_digest="b" * 64,
    )

    with pytest.raises(ValueError, match="intended authority"):
        _validate_request(request)


def test_phase5_worker_checks_runtime_prefix_before_shared_session_lookup(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state

    session_reads = []
    provider_starts = []

    class FakeDB:
        def get_existing_session_conversation(self, session_id):
            session_reads.append(session_id)
            raise AssertionError("session lookup preceded runtime prefix identity")

        def close(self):
            return None

    class FakeAgent:
        def __init__(self, **_kwargs):
            self.session_id = "session-1"
            self.provider = "sealed-provider"
            self.model = "sealed-model"
            self.tools = []
            self.valid_tool_names = set()
            self._cached_system_prompt = "sealed prompt"

        def seal_model_visible_prefix(self):
            return "c" * 64

        def run_conversation(self, *_args, **_kwargs):
            provider_starts.append(True)
            raise AssertionError("provider started after prefix mismatch")

    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "sealed-provider",
            "model": "sealed-model",
            "api_mode": "chat_completions",
            "base_url": "",
            "api_key": "credential",
        },
    )

    result = worker._run({
        "plugin_id": "workflow",
        "request": PluginAgentRunRequest(
            prompt="continue",
            provider="sealed-provider",
            model="sealed-model",
            context_mode="shared",
            session_id="session-1",
            allowed_tools=(),
            intended_authority_digest="a" * 64,
            expected_model_visible_prefix_digest="b" * 64,
        ).to_wire(),
    })

    assert session_reads == []
    assert provider_starts == []
    assert result["status"] == "failed"
    assert result["audit"] == {
        "plugin_id": "workflow",
        "failure_kind": "cache_fingerprint_changed",
        "provider_attempts": 0,
        "model_calls": 0,
        "known_no_effect": True,
        "intended_authority_digest": "a" * 64,
        "model_visible_prefix_digest": "c" * 64,
    }


def test_conversation_reuses_worker_sealed_prompt_without_db_restore() -> None:
    from agent.conversation_loop import _restore_or_build_system_prompt

    class ForbiddenDB:
        def get_session(self, _session_id):
            raise AssertionError("sealed prompt was replaced from session storage")

    class Candidate:
        session_id = "session-1"
        _session_db = ForbiddenDB()
        _cached_system_prompt = "exact worker-rendered bytes"
        _model_visible_prefix_digest = "a" * 64
        verified = False

        def verify_model_visible_prefix(self):
            self.verified = True
            return self._model_visible_prefix_digest

        def _build_system_prompt(self, _system_message=None):
            raise AssertionError("sealed prompt was rebuilt")

    candidate = Candidate()

    _restore_or_build_system_prompt(candidate, None, [{"role": "user"}])

    assert candidate.verified is True
    assert candidate._cached_system_prompt == "exact worker-rendered bytes"
