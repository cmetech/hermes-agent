from __future__ import annotations

import model_tools
import pytest
import run_agent
from agent.plugin_agent import PluginAgentRunRequest, _validate_request


_SIX_FIELD_RUNTIME_IDENTITY = {
    "provider": "sealed-provider",
    "model": "sealed-model",
    "api_mode": "chat_completions",
    "base_url_trust_class": "provider_default",
    "endpoint_sha256": "d" * 64,
    "registration_provenance_digest": "c" * 64,
}


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
            **_SIX_FIELD_RUNTIME_IDENTITY,
        },
        expected_runtime_route_fingerprint="c" * 64,
        expected_runtime_route_options={"effort": "high"},
    )

    decoded = PluginAgentRunRequest.from_wire(request.to_wire())

    _validate_request(decoded)
    assert decoded.intended_authority_digest == "a" * 64
    assert decoded.expected_model_visible_prefix_digest == "b" * 64
    assert decoded.expected_runtime_identity == request.expected_runtime_identity
    assert decoded.expected_runtime_route_fingerprint == "c" * 64
    assert decoded.expected_runtime_route_options == {"effort": "high"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {key: item for key, item in value.items() if key != "endpoint_sha256"},
        lambda value: {**value, "endpoint_sha256": ""},
        lambda value: {**value, "endpoint_sha256": 7},
        lambda value: {**value, "extra": "field"},
    ],
)
def test_plugin_agent_runtime_identity_reader_rejects_nonexact_six_field_codec(
    mutation,
) -> None:
    request = PluginAgentRunRequest(
        prompt="continue",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=mutation(_SIX_FIELD_RUNTIME_IDENTITY),
        expected_runtime_route_fingerprint="c" * 64,
    )

    with pytest.raises(ValueError, match="expected runtime identity is malformed"):
        _validate_request(request)


def test_phase5_runtime_identity_requires_route_fingerprint_to_avoid_guessing() -> None:
    request = PluginAgentRunRequest(
        prompt="continue",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=dict(_SIX_FIELD_RUNTIME_IDENTITY),
    )

    with pytest.raises(ValueError, match="route fingerprint"):
        _validate_request(request)


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
                **_SIX_FIELD_RUNTIME_IDENTITY,
            },
            expected_runtime_route_fingerprint="e" * 64,
            expected_runtime_route_options={},
        ).to_wire(),
    })

    assert constructed == []
    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["provider_attempts"] == 0


def test_phase5_worker_blocks_same_trust_endpoint_drift_before_any_side_effect(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    from tools import mcp_tool

    mcp_started = []
    agent_constructed = []
    provider_called = []
    admitted = runtime_provider.classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "sealed-model"},
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://endpoint-a.test/v1",
        },
    )

    class ForbiddenAgent:
        def __init__(self, **_kwargs):
            agent_constructed.append(True)
            raise AssertionError("agent constructed after endpoint drift")

    monkeypatch.setattr(run_agent, "AIAgent", ForbiddenAgent)
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "openrouter",
            "model": "sealed-model",
            "api_mode": "chat_completions",
            "base_url": "https://endpoint-b.test/v1",
            "api_key": "credential",
        },
    )
    monkeypatch.setattr(
        mcp_tool, "discover_mcp_tools", lambda: mcp_started.append(True)
    )

    result = worker._run(
        {
            "plugin_id": "workflow",
            "request": PluginAgentRunRequest(
                prompt="continue",
                provider="openrouter",
                model="sealed-model",
                allowed_tools=(),
                mcp_servers={"test": {"command": "unused"}},
                intended_authority_digest="a" * 64,
                expected_runtime_identity=(
                    runtime_provider.execution_runtime_identity(admitted).to_dict()
                ),
                expected_runtime_route_fingerprint="e" * 64,
                expected_runtime_route_options={},
            ).to_wire(),
        },
        provider_start_gate=lambda: provider_called.append(True),
    )

    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["mismatched_fields"] == ["endpoint_sha256"]
    assert mcp_started == []
    assert agent_constructed == []
    assert provider_called == []


def test_phase5_worker_checks_credential_free_route_before_every_side_effect(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import agent.vertex_adapter as vertex_adapter
    import hermes_cli.config as config_mod
    import hermes_cli.env_loader as env_loader
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_cli.timeouts as timeout_mod
    from tools import mcp_tool, registry as registry_mod, tool_search

    calls = []
    admitted_url = (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/"
        "admitted-private-project/locations/us-central1/endpoints/openapi"
    )
    runtime_url = (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/"
        "changed-private-project/locations/us-central1/endpoints/openapi"
    )
    admitted = runtime_provider.classify_execution_runtime(
        provider="vertex",
        model_config={"provider": "vertex", "default": "sealed-model"},
        provider_config={"base_url": admitted_url},
    )
    expected_identity = runtime_provider.execution_runtime_identity(admitted).to_dict()

    monkeypatch.setattr(
        config_mod,
        "read_raw_config_readonly",
        lambda: {
            "model": {"provider": "vertex", "default": "sealed-model"},
            "vertex": {
                "project_id": "changed-private-project",
                "region": "us-central1",
            },
        },
    )
    monkeypatch.setattr(
        env_loader, "load_hermes_dotenv", lambda: calls.append("dotenv")
    )
    monkeypatch.setattr(
        worker,
        "_finalize_authenticated_mcp_config",
        lambda *args, **kwargs: calls.append("mcp_finalize") or args[0],
    )
    monkeypatch.setattr(
        mcp_tool,
        "_interpolate_env_vars",
        lambda value: calls.append("mcp_interpolate") or value,
    )
    original_mcp_loader = mcp_tool._load_mcp_config
    original_tool_search_config = tool_search.load_config
    original_timeout = timeout_mod.get_provider_request_timeout
    original_generation = registry_mod.registry._generation
    monkeypatch.setattr(
        vertex_adapter,
        "get_vertex_config",
        lambda: calls.append("token_mint") or ("token", runtime_url),
    )

    def credential_refresh(**_kwargs):
        calls.append("credential_refresh")
        token, base_url = vertex_adapter.get_vertex_config()
        return {
            "provider": "vertex",
            "model": "sealed-model",
            "api_mode": "chat_completions",
            "base_url": base_url,
            "api_key": token,
        }

    monkeypatch.setattr(
        runtime_provider, "resolve_runtime_provider", credential_refresh
    )
    monkeypatch.setattr(
        run_agent,
        "AIAgent",
        lambda **_kwargs: calls.append("agent_constructed"),
    )

    result = worker._run(
        {
            "plugin_id": "workflow",
            "request": PluginAgentRunRequest(
                prompt="continue",
                provider="vertex",
                model="sealed-model",
                allowed_tools=(),
                mcp_servers={"test": {"command": "unused"}},
                intended_authority_digest="a" * 64,
                expected_runtime_identity=expected_identity,
                expected_runtime_route_fingerprint="e" * 64,
                expected_runtime_route_options={},
            ).to_wire(),
        },
        provider_start_gate=lambda: calls.append("provider_dispatch"),
    )

    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["mismatched_fields"] == ["endpoint_sha256"]
    assert calls == []
    assert mcp_tool._load_mcp_config is original_mcp_loader
    assert tool_search.load_config is original_tool_search_config
    assert timeout_mod.get_provider_request_timeout is original_timeout
    assert registry_mod.registry._generation == original_generation


def test_phase5_bedrock_auth_route_drift_rejects_before_every_side_effect(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.config as config_mod
    import hermes_cli.env_loader as env_loader
    import hermes_cli.runtime_provider as runtime_provider
    from tools import mcp_tool

    calls = []
    admitted = runtime_provider.classify_execution_runtime(
        provider="bedrock",
        model_config={
            "provider": "bedrock",
            "default": "global.anthropic.claude-sonnet-4-6",
        },
        provider_config={},
    )
    monkeypatch.setattr(
        config_mod,
        "read_raw_config_readonly",
        lambda: {
            "model": {
                "provider": "bedrock",
                "default": "global.anthropic.claude-sonnet-4-6",
            }
        },
    )
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "private-bearer")
    monkeypatch.setattr(
        env_loader, "load_hermes_dotenv", lambda: calls.append("dotenv")
    )
    monkeypatch.setattr(
        mcp_tool,
        "_interpolate_env_vars",
        lambda value: calls.append("mcp_interpolate") or value,
    )
    monkeypatch.setattr(
        worker,
        "_finalize_authenticated_mcp_config",
        lambda *args, **kwargs: calls.append("mcp_finalize") or args[0],
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: calls.append("credential_resolution")
        or {
            "provider": "bedrock",
            "model": "global.anthropic.claude-sonnet-4-6",
            "api_mode": "bedrock_converse",
            "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
            "api_key": "credential",
        },
    )

    result = worker._run({
        "plugin_id": "workflow",
        "request": PluginAgentRunRequest(
            prompt="continue",
            provider="bedrock",
            model="global.anthropic.claude-sonnet-4-6",
            allowed_tools=(),
            mcp_servers={"test": {"command": "unused"}},
            intended_authority_digest="a" * 64,
            expected_runtime_identity=(
                runtime_provider.execution_runtime_identity(admitted).to_dict()
            ),
            expected_runtime_route_fingerprint="e" * 64,
            expected_runtime_route_options={},
        ).to_wire(),
    })

    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["mismatched_fields"] == ["api_mode"]
    assert calls == []


def test_phase5_worker_passes_selected_alias_constraint_to_credential_resolver(
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.config as config_mod
    import hermes_cli.runtime_provider as runtime_provider
    from hermes_cli.workflow_model_resolution import (
        parse_workflow_model_config,
        resolve_workflow_model_reference,
    )

    config = {
        "model": {"provider": "openrouter", "default": "other-model"},
        "model_aliases": {
            "review": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "https://private-alias.example/anthropic",
                "options": {"effort": "medium"},
            }
        },
    }
    route = resolve_workflow_model_reference(
        parse_workflow_model_config(config),
        "@review",
        node_options={"effort": "high"},
    )
    expected = {
        "provider": "anthropic",
        "model": route.model,
        "api_mode": route.api_mode,
        "base_url_trust_class": route.base_url_trust_class,
        "endpoint_sha256": route.endpoint_sha256,
        "registration_provenance_digest": route.registration_provenance_digest,
    }
    captured = []
    monkeypatch.setattr(config_mod, "read_raw_config_readonly", lambda: config)

    def credential_resolver(**kwargs):
        captured.append(kwargs.get("route_constraint"))
        return {
            "provider": "anthropic",
            "model": route.model,
            "api_mode": "codex_responses",
            "base_url": "https://private-alias.example/anthropic",
            "api_key": "credential",
        }

    monkeypatch.setattr(
        runtime_provider, "resolve_runtime_provider", credential_resolver
    )

    result = worker._run({
        "plugin_id": "workflow",
        "request": PluginAgentRunRequest(
            prompt="continue",
            provider=route.provider,
            model=route.model,
            allowed_tools=(),
            intended_authority_digest="a" * 64,
            expected_runtime_identity=expected,
            expected_runtime_route_fingerprint=route.route_fingerprint,
            expected_runtime_route_options=dict(route.provider_options),
        ).to_wire(),
    })

    assert len(captured) == 1
    assert captured[0] is not None
    assert captured[0].route_fingerprint == route.route_fingerprint
    assert dict(route.provider_options) == {"effort": "high"}
    assert "private-alias.example" not in repr(captured[0])
    assert result["audit"]["mismatched_fields"] == ["api_mode"]


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
