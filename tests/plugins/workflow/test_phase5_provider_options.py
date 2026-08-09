from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    _validate_request,
)
from agent.structured_output import (
    StructuredOutputRequest,
    StructuredOutputStrategy,
    normalize_schema,
)
from agent.transports.chat_completions import ChatCompletionsTransport
from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    ProviderCapabilityDecision,
    WorkflowProviderFeature,
    encode_provider_option_transport,
)
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.models import WorkflowStructuredOutput, freeze_value
from plugins.workflow.provider_authority import (
    WorkflowCapabilityObligation,
    WorkflowProviderAuthority,
    WorkflowResolvedProviderRoute,
)
from tests.plugins.workflow.test_phase5_execution_context import _context


def _route(
    role: str,
    *,
    provider: str = "openrouter",
    model: str = "openai/gpt-5.4",
    options: dict | None = None,
) -> WorkflowResolvedProviderRoute:
    route_id = f"ask:{role}"
    return WorkflowResolvedProviderRoute(
        route_id=route_id,
        node_id="ask",
        role=role,
        inline_agent_id=None,
        reference_kind="configured_alias",
        requested_reference_sha256=("1" if role == "primary" else "2") * 64,
        provider=provider,
        model=model,
        api_mode="chat_completions",
        route_fingerprint=("3" if role == "primary" else "4") * 64,
        endpoint_sha256="6" * 64,
        registration_provenance_digest="5" * 64,
        provider_options=options or {},
        config_scope="profile",
        base_url_trust_class="trusted_direct",
    )


def _decision(
    route: WorkflowResolvedProviderRoute,
    *,
    option: str,
    value,
    effective: dict,
) -> ProviderCapabilityDecision:
    return ProviderCapabilityDecision(
        feature=WorkflowProviderFeature.EFFORT_THINKING,
        disposition=CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS,
        provider=route.effective_provider,
        model=route.model,
        option=option,
        requested_semantics={"value": value},
        effective_semantics=effective,
        adapter_version=1,
        declaration_source="provider_profile",
        registration_provenance_digest=route.registration_provenance_digest,
        code="sealed-test-translation",
        rationale="test translation",
    )


def _authority(
    *routes: WorkflowResolvedProviderRoute,
    extra_obligations: tuple[WorkflowCapabilityObligation, ...] = (),
) -> WorkflowProviderAuthority:
    obligations = tuple(
        WorkflowCapabilityObligation(
            path=f"{route.route_id}.effort",
            route_id=route.route_id,
            decision=_decision(
                route,
                option="effort",
                value=route.provider_options["effort"],
                effective={
                    "request_field": (
                        "verbosity"
                        if "claude" in route.model
                        else "reasoning.effort"
                    )
                },
            ),
        )
        for route in routes
        if "effort" in route.provider_options
    ) + extra_obligations
    return WorkflowProviderAuthority(
        config_fingerprint="6" * 64,
        routes=MappingProxyType({route.route_id: route for route in routes}),
        obligations=obligations,
        warnings=(),
        authority_digest="7" * 64,
    )


def test_sealed_effort_encoder_emits_only_the_declared_request_field() -> None:
    route = _route("primary", options={"effort": "high"})
    transport = encode_provider_option_transport(
        route,
        _authority(route).obligations,
    )

    assert transport.reasoning_config == {"enabled": True, "effort": "high"}
    assert transport.request_overrides == {
        "extra_body": {"reasoning": {"effort": "high"}}
    }


def test_phase5_structured_repair_preserves_degraded_option_transport(tmp_path) -> None:
    route = _route("primary", options={"effort": "high"})
    encoded = encode_provider_option_transport(route, _authority(route).obligations)
    schema = normalize_schema({"type": "object"})
    structured = StructuredOutputRequest(
        schema=schema,
        strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
        adapter_version=1,
    )
    initial = PluginAgentRunRequest(
        prompt="primary",
        provider=route.provider,
        model=route.model,
        intended_authority_digest="a" * 64,
        expected_runtime_identity=route.execution_runtime_identity().to_dict(),
        expected_runtime_route_fingerprint=route.route_fingerprint,
        expected_runtime_route_options=dict(route.provider_options),
        reasoning_config=dict(encoded.reasoning_config),
        request_overrides=dict(encoded.request_overrides),
        structured_output=structured,
        allowed_tools=("read_file",),
        workdir=tmp_path,
        sealed_provider_attempt_grant=True,
    )

    repair = AgentNodeExecutor(_FallbackRunner())._phase5_structured_repair_request(
        initial_request=initial,
        repair_prompt="repair",
        remaining_provider_attempts=1,
        remaining_timeout_seconds=10,
    )

    assert repair.reasoning_config == {"enabled": True, "effort": "high"}
    assert repair.request_overrides == {
        "extra_body": {"reasoning": {"effort": "high"}}
    }
    assert repair.expected_runtime_route_options == {"effort": "high"}
    assert repair.allowed_tools == ()


def test_option_encoder_compares_decisions_to_effective_provider() -> None:
    route = replace(
        _route(
            "primary",
            provider="openai-api",
            model="gpt-5.4",
            options={"effort": "high"},
        ),
        effective_provider="openai",
    )

    transport = encode_provider_option_transport(
        route,
        _authority(route).obligations,
    )

    assert transport.reasoning_config == {"enabled": True, "effort": "high"}


def test_missing_option_encoder_fails_closed() -> None:
    route = _route("primary", options={"thinking": {"type": "adaptive"}})
    decision = _decision(
        route,
        option="thinking",
        value={"type": "adaptive"},
        effective={"request_field": "unknown.thinking"},
    )

    try:
        encode_provider_option_transport(
            route,
            (WorkflowCapabilityObligation("thinking", route.route_id, decision),),
        )
    except ValueError as exc:
        assert str(exc) == "provider_option_encoder_unavailable"
    else:
        raise AssertionError("an accepted option without an encoder must block")


def test_sealed_effort_translation_reaches_exact_openrouter_request_field() -> None:
    from providers import get_provider_profile

    route = _route("primary", options={"effort": "high"})
    encoded = encode_provider_option_transport(route, _authority(route).obligations)
    kwargs = ChatCompletionsTransport().build_kwargs(
        model=route.model,
        messages=[{"role": "user", "content": "hello"}],
        provider_name=route.provider,
        base_url="https://openrouter.ai/api/v1",
        provider_profile=get_provider_profile("openrouter"),
        reasoning_config=dict(encoded.reasoning_config),
        request_overrides=dict(encoded.request_overrides),
        supports_reasoning=False,
    )

    assert kwargs["extra_body"]["reasoning"] == {"effort": "high"}
    assert "verbosity" not in kwargs

    adaptive = _route(
        "primary",
        model="anthropic/claude-sonnet-4.6",
        options={"effort": "high"},
    )
    encoded = encode_provider_option_transport(
        adaptive, _authority(adaptive).obligations
    )
    kwargs = ChatCompletionsTransport().build_kwargs(
        model=adaptive.model,
        messages=[{"role": "user", "content": "hello"}],
        provider_name=adaptive.provider,
        base_url="https://openrouter.ai/api/v1",
        provider_profile=get_provider_profile("openrouter"),
        reasoning_config=dict(encoded.reasoning_config),
        request_overrides=dict(encoded.request_overrides),
        supports_reasoning=False,
    )

    assert kwargs["verbosity"] == "high"
    assert "reasoning" not in kwargs.get("extra_body", {})


class _FallbackRunner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="done",
            session_id="session",
            provider=request.provider or "",
            model=request.model or "",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": 1,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


def test_phase5_fallback_is_sealed_as_a_fresh_worker_route(tmp_path) -> None:
    primary = _route("primary", options={"effort": "medium"})
    fallback = _route(
        "fallback",
        model="anthropic/claude-sonnet-4.6",
        options={"effort": "high"},
    )
    authority = _authority(primary, fallback)
    context = _context(tmp_path)
    node = replace(
        context.node,
        options=freeze_value({**dict(context.node.options), "fallbackModel": "@raw"}),
    )
    runner = _FallbackRunner()

    result = AgentNodeExecutor(runner).execute(
        replace(
            context,
            node=node,
            sealed_provider_route=primary,
            sealed_provider_authority=authority,
        )
    )

    assert result.status == "succeeded"
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.fallback_model is None
    assert request.sealed_fallback_route["provider"] == "openrouter"
    assert request.sealed_fallback_route["model"] == "anthropic/claude-sonnet-4.6"
    assert request.sealed_fallback_route["context_mode"] == "fresh"
    assert request.sealed_fallback_route["expected_runtime_route_fingerprint"] == (
        fallback.route_fingerprint
    )
    assert request.sealed_fallback_route["expected_runtime_route_options"] == {
        "effort": "high"
    }
    assert request.sealed_fallback_route["request_overrides"] == {"verbosity": "high"}


def test_phase5_fallback_consumes_its_own_structured_output_strategy(tmp_path) -> None:
    schema = normalize_schema({
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    })
    primary = _route("primary")
    fallback = replace(
        _route(
            "fallback",
            provider="openai-api",
            model="gpt-5.4",
        ),
        effective_provider="openai",
    )

    def structured_obligation(
        route: WorkflowResolvedProviderRoute,
        disposition: CapabilityDisposition,
        strategy: StructuredOutputStrategy,
        declaration_source: str,
    ) -> WorkflowCapabilityObligation:
        return WorkflowCapabilityObligation(
            path="nodes[0].output_format",
            route_id=route.route_id,
            decision=ProviderCapabilityDecision(
                feature=WorkflowProviderFeature.STRUCTURED_OUTPUT,
                disposition=disposition,
                provider=route.effective_provider,
                model=route.model,
                option="json_schema",
                requested_semantics={
                    "schema_fingerprint": schema.schema_fingerprint,
                },
                effective_semantics={
                    "strategy": strategy.value,
                    "schema_fingerprint": schema.schema_fingerprint,
                },
                adapter_version=(
                    1
                    if disposition is not CapabilityDisposition.NATIVE
                    else None
                ),
                declaration_source=declaration_source,
                registration_provenance_digest=(
                    route.registration_provenance_digest
                ),
                code="sealed-structured-output-test",
                rationale="test route-specific structured output",
            ),
        )

    authority = _authority(
        primary,
        fallback,
        extra_obligations=(
            structured_obligation(
                primary,
                CapabilityDisposition.HERMES_ADAPTER,
                StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
                "managed_loop_default",
            ),
            structured_obligation(
                fallback,
                CapabilityDisposition.NATIVE,
                StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
                "provider_profile",
            ),
        ),
    )
    context = _context(tmp_path)
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "fallbackModel": "@recovery",
            "output_format": dict(schema.canonical_schema),
        }),
    )
    declared = WorkflowStructuredOutput(
        canonical_schema=schema.canonical_schema,
        schema_fingerprint=schema.schema_fingerprint,
    )
    primary_decision = StructuredOutputCapabilityDecision(
        strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
        effective_provider=primary.provider,
        model=primary.model,
        api_mode=primary.api_mode,
        declaration_source="managed_loop_default",
        adapter_version=1,
        schema_fingerprint=schema.schema_fingerprint,
        rationale="test primary strategy",
    )

    class FallbackStructuredRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            from agent.plugin_agent import _validate_request

            self.requests.append(request)
            _validate_request(request)
            evidence = {
                "strategy": StructuredOutputStrategy.NATIVE_JSON_SCHEMA.value,
                "adapter_version": 1,
                "schema_fingerprint": schema.schema_fingerprint,
                "declaration_source": "provider_profile",
                "provider_attempts": 2,
                "model_calls": 1,
            }
            return PluginAgentRunResult(
                final_response='{"answer":"ok"}',
                session_id="fallback-session",
                provider=fallback.effective_provider,
                model=fallback.model,
                status="completed",
                pending_interaction=None,
                usage={},
                audit={
                    **evidence,
                    "api_mode": fallback.api_mode,
                    "fallback_used": True,
                    "fallback_context": "fresh",
                    "intended_authority_digest": "a" * 64,
                    "model_visible_prefix_digest": "9" * 64,
                },
                structured_output=evidence,
            )

    runner = FallbackStructuredRunner()
    result = AgentNodeExecutor(runner).execute(
        replace(
            context,
            node=node,
            sealed_provider_route=primary,
            sealed_provider_authority=authority,
            structured_output=declared,
            structured_output_decision=primary_decision,
            variable_context=replace(
                context.variable_context,
                normalizer_version=5,
            ),
        )
    )

    assert result.status == "succeeded"
    assert runner.requests[0].sealed_fallback_route["provider"] == "openai-api"
    assert runner.requests[0].sealed_fallback_route["expected_runtime_identity"][
        "provider"
    ] == "openai"
    sealed_fallback = runner.requests[0].sealed_fallback_route
    assert sealed_fallback["expected_runtime_route_fingerprint"] == (
        fallback.route_fingerprint
    )
    assert sealed_fallback["structured_output"].strategy is (
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    )


def test_phase5_sandbox_blocks_before_runner_with_isolation_recommendation(tmp_path) -> None:
    runner = _FallbackRunner()
    context = _context(tmp_path)
    node = replace(
        context.node,
        options=freeze_value({**dict(context.node.options), "sandbox": {"enabled": True}}),
    )

    result = AgentNodeExecutor(runner).execute(replace(context, node=node))

    assert result.error_code == "provider_native_sandbox_unavailable"
    assert result.metadata["provider_attempts"] == 0
    assert result.metadata["recommendation"] == (
        "execution_environment: isolated_backend_required"
    )
    assert "resource" not in result.error_message.lower()
    assert runner.requests == []


def test_worker_runs_sealed_fallback_in_fresh_child_context(monkeypatch, tmp_path) -> None:
    import agent.plugin_agent as plugin_agent
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    captured = []

    class FakeDB:
        def close(self):
            pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "primary-session"
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 1
            self.session_output_tokens = 2
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._interrupt_requested = False

        def seal_model_visible_prefix(self):
            return "8" * 64

        def run_conversation(self, _prompt, conversation_history=None):
            return {"failed": True, "api_calls": 1, "final_response": ""}

    class ChildRunner:
        def __init__(self, plugin_id):
            assert plugin_id == "workflow"

        def run(self, request, **kwargs):
            captured.append((request, kwargs))
            return PluginAgentRunResult(
                final_response="fallback done",
                session_id="fallback-session",
                provider=request.provider or "",
                model=request.model or "",
                status="completed",
                pending_interaction=None,
                usage={"input_tokens": 3, "output_tokens": 4},
                audit={
                    "provider_attempts": 2,
                    "model_calls": 1,
                    "intended_authority_digest": request.intended_authority_digest,
                    "model_visible_prefix_digest": "9" * 64,
                },
            )

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(plugin_agent, "PluginAgentRunner", ChildRunner)
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    runtime = {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "secret",
    }
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: runtime,
    )
    identity = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "api_mode": "chat_completions",
        "base_url_trust_class": "trusted_direct",
        "endpoint_sha256": "6" * 64,
        "registration_provenance_digest": "5" * 64,
    }
    expected_primary_identity = runtime_provider.execution_runtime_identity(
        runtime_provider.classify_resolved_execution_runtime(runtime)
    )
    primary_identity = expected_primary_identity.to_dict()

    constraint = runtime_provider.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="9" * 64,
        requested_provider="openrouter",
        model="openai/gpt-5.4",
        api_mode="chat_completions",
        base_url=runtime["base_url"],
        provider_config={},
        identity=expected_primary_identity,
    )

    monkeypatch.setattr(
        runtime_provider,
        "select_credential_free_execution_route",
        lambda *_args, **_kwargs: constraint,
    )
    structured_schema = normalize_schema({"type": "object"})
    request = PluginAgentRunRequest(
        prompt="immutable user turn",
        provider="openrouter",
        model="openai/gpt-5.4",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=primary_identity,
        expected_runtime_route_fingerprint="9" * 64,
        expected_runtime_route_options={},
        allowed_tools=(),
        sandbox_policy={"mode": "provider_native"},
        workdir=tmp_path,
        sealed_provider_attempt_grant=True,
        structured_output=StructuredOutputRequest(
            schema=structured_schema,
            strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            adapter_version=1,
        ),
        sealed_fallback_route={
            "provider": "openrouter",
            "effective_provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "context_mode": "fresh",
            "expected_runtime_route_fingerprint": "7" * 64,
            "expected_runtime_route_options": {},
            "expected_runtime_identity": identity,
            "reasoning_config": {"enabled": True, "effort": "high"},
            "request_overrides": {"verbosity": "high"},
            "structured_output": StructuredOutputRequest(
                schema=structured_schema,
                strategy=StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
                adapter_version=1,
            ),
        },
    )

    result = worker._run({"plugin_id": "workflow", "request": request.to_wire()})

    assert result["status"] == "completed"
    assert result["final_response"] == "fallback done"
    assert result["audit"]["fallback_used"] is True
    assert result["audit"]["fallback_context"] == "fresh"
    assert result["usage"]["input_tokens"] == 4
    assert result["usage"]["output_tokens"] == 6
    child, kwargs = captured[0]
    assert child.prompt == "immutable user turn"
    assert child.context_mode == "fresh"
    assert child.session_id is None
    assert child.provider == "openrouter"
    assert child.model == "anthropic/claude-sonnet-4.6"
    assert child.intended_authority_digest == request.intended_authority_digest
    assert child.expected_runtime_identity == identity
    assert child.expected_runtime_route_fingerprint == "7" * 64
    assert child.expected_runtime_route_options == {}
    assert child.reasoning_config == {"enabled": True, "effort": "high"}
    assert child.request_overrides == {"verbosity": "high"}
    assert child.structured_output is not None
    assert child.structured_output.strategy is (
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    )
    assert child.fallback_model is None
    assert child.sealed_fallback_route is None
    assert child.sandbox_policy == {"mode": "provider_native"}
    _validate_request(child)
    assert kwargs["is_cancelled"]() is False


def test_worker_treats_provider_capability_drift_as_terminal(
    monkeypatch, tmp_path
) -> None:
    import agent.codex_runtime as codex_runtime
    import agent.plugin_agent as plugin_agent
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    drift = run_agent.ProviderCapabilityDriftError()
    side_effects: list[str] = []

    class FakeDB:
        def close(self):
            pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "primary-session"
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self._codex_session = self.Session()
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._interrupt_requested = False
            self._api_call_count = 0

        class Session:
            def run_turn(self, **_kwargs):
                side_effects.append("transport")
                raise AssertionError("provider transport must remain unreachable")

            def close(self):
                side_effects.append("session_recovery")

        def _assert_execution_route_constraint(self, _transport=None):
            raise drift

        def seal_model_visible_prefix(self):
            return "8" * 64

        def run_conversation(self, _prompt, conversation_history=None):
            return codex_runtime.run_codex_app_server_turn(
                self,
                user_message=_prompt,
                original_user_message=_prompt,
                messages=[{"role": "user", "content": _prompt}],
                effective_task_id="workflow-task",
            )

    class ChildRunner:
        def __init__(self, plugin_id):
            assert plugin_id == "workflow"

        def run(self, request, **kwargs):
            side_effects.append("fallback")
            return PluginAgentRunResult(
                final_response="must not run",
                session_id="fallback-session",
                provider=request.provider or "",
                model=request.model or "",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(plugin_agent, "PluginAgentRunner", ChildRunner)
    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    runtime = {
        "provider": "openrouter",
        "model": "openai/gpt-5.4",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "private-credential",
    }
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: runtime,
    )
    expected_primary_identity = runtime_provider.execution_runtime_identity(
        runtime_provider.classify_resolved_execution_runtime(runtime)
    )
    primary_identity = expected_primary_identity.to_dict()

    constraint = runtime_provider.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="9" * 64,
        requested_provider="openrouter",
        model="openai/gpt-5.4",
        api_mode="chat_completions",
        base_url=runtime["base_url"],
        provider_config={},
        identity=expected_primary_identity,
    )

    monkeypatch.setattr(
        runtime_provider,
        "select_credential_free_execution_route",
        lambda *_args, **_kwargs: constraint,
    )
    request = PluginAgentRunRequest(
        prompt="immutable user turn",
        provider="openrouter",
        model="openai/gpt-5.4",
        intended_authority_digest="a" * 64,
        expected_runtime_identity=primary_identity,
        expected_runtime_route_fingerprint="9" * 64,
        expected_runtime_route_options={},
        allowed_tools=(),
        workdir=tmp_path,
        sealed_provider_attempt_grant=True,
        sealed_fallback_route={
            "provider": "openrouter",
            "effective_provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "context_mode": "fresh",
            "expected_runtime_route_fingerprint": "7" * 64,
            "expected_runtime_route_options": {},
            "expected_runtime_identity": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "api_mode": "chat_completions",
                "base_url_trust_class": "trusted_direct",
                "endpoint_sha256": "6" * 64,
                "registration_provenance_digest": "5" * 64,
            },
            "reasoning_config": {},
            "request_overrides": {},
            "structured_output": None,
        },
    )

    result = worker._run({"plugin_id": "workflow", "request": request.to_wire()})

    assert result["status"] == "failed"
    assert result["final_response"] == ""
    assert result["audit"]["failure_kind"] == "provider_capability_drift"
    assert result["audit"]["provider_attempts"] == 0
    assert result["audit"]["model_calls"] == 0
    assert result["audit"]["known_no_effect"] is True
    assert side_effects == []
    public_result = str(result)
    assert "private-credential" not in public_result
    assert "https://openrouter.ai/api/v1" not in public_result
