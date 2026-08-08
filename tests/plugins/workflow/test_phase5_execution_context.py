from __future__ import annotations

from dataclasses import replace

import pytest

from agent.plugin_agent import PluginAgentRunResult
from agent.structured_output import StructuredOutputStrategy, normalize_schema
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    ProviderCapabilityDecision,
    WorkflowProviderFeature,
)
import plugins.workflow.execution_semantics as semantics
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowStructuredOutput,
    freeze_value,
)
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowCapabilityObligation,
    WorkflowResolvedProviderRoute,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.sessions import (
    NodeSessionKey,
    NodeSessionRegistry,
    PersistentSessionRecoverySelection,
    SessionRegistryUpdateCandidate,
)


def _route(*, model: str = "sealed-model") -> WorkflowResolvedProviderRoute:
    return WorkflowResolvedProviderRoute(
        route_id="ask:primary",
        node_id="ask",
        role="primary",
        inline_agent_id=None,
        reference_kind="configured_alias",
        requested_reference_sha256="1" * 64,
        provider="sealed-provider",
        model=model,
        api_mode="chat_completions",
        route_fingerprint="2" * 64,
        endpoint_sha256="d" * 64,
        registration_provenance_digest="3" * 64,
        provider_options={"effort": "high"},
        config_scope="profile",
        base_url_trust_class="provider_default",
    )


def _authority(*, model: str = "sealed-model") -> WorkflowProviderAuthority:
    route = _route(model=model)
    return WorkflowProviderAuthority(
        config_fingerprint="4" * 64,
        routes={"ask:primary": route},
        obligations=(
            WorkflowCapabilityObligation(
                path="nodes[0].effort",
                route_id="ask:primary",
                decision=ProviderCapabilityDecision(
                    feature=WorkflowProviderFeature.EFFORT_THINKING,
                    disposition=(
                        CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS
                    ),
                    provider=route.provider,
                    model=route.model,
                    option="effort",
                    requested_semantics={"value": "high"},
                    effective_semantics={"request_field": "reasoning.effort"},
                    adapter_version=1,
                    declaration_source="provider_profile",
                    registration_provenance_digest=(
                        route.registration_provenance_digest
                    ),
                    code="test_effort_translation",
                    rationale="test fixture translation",
                ),
            ),
        ),
        warnings=(),
        authority_digest=("5" if model == "sealed-model" else "6") * 64,
    )


def test_intended_authority_identity_binds_sealed_route_and_full_closure() -> None:
    digest = semantics.phase5_node_intended_authority_digest

    baseline = digest(
        _authority(),
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )

    assert len(baseline) == 64
    assert baseline != digest(
        _authority(model="changed-model"),
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    assert baseline != digest(
        _authority(),
        node_id="ask",
        sealed_closure_digest="8" * 64,
    )
    with pytest.raises(ValueError, match="primary route"):
        digest(
            replace(_authority(), routes={}),
            node_id="ask",
            sealed_closure_digest="7" * 64,
        )


class _Runner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="done",
            session_id="session-1",
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


def _context(tmp_path, *, route=True, shared=False) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    node = WorkflowNode(
        id="ask",
        node_type="prompt",
        value="hello",
        depends_on=("before",) if shared else (),
        source_index=0,
        source_line=1,
        options=freeze_value({
            "provider": "authored-provider-must-not-run",
            "model": "@mutable-alias",
            "effort": "low",
            **({"context": "shared"} if shared else {}),
        }),
    )
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        workflow_name="phase5",
        workflow_options=freeze_value({}),
        variable_context=VariableContext(arguments="", workflow_id="run-1"),
        ai_entitlement=AIEntitlementResolution("real"),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        sealed_provider_route=_route() if route else None,
        sealed_provider_authority=_authority() if route else None,
        intended_authority_digest="a" * 64,
        predecessor_results=(
            {
                "before": {
                    "session_id": "predecessor-session",
                    "cache_fingerprint": semantics.phase5_session_cache_fingerprint(
                        "a" * 64,
                        "b" * 64,
                    ),
                    "intended_authority_digest": "a" * 64,
                    "model_visible_prefix_digest": "b" * 64,
                }
            }
            if shared
            else {}
        ),
    )


def _structured_context(tmp_path) -> NodeExecutionContext:
    context = _context(tmp_path)
    route = context.sealed_provider_route
    authority = context.sealed_provider_authority
    assert route is not None
    assert authority is not None
    schema = normalize_schema({
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    })
    structured_decision = ProviderCapabilityDecision(
        feature=WorkflowProviderFeature.STRUCTURED_OUTPUT,
        disposition=CapabilityDisposition.HERMES_ADAPTER,
        provider=route.effective_provider,
        model=route.model,
        option="json_schema",
        requested_semantics={"schema_fingerprint": schema.schema_fingerprint},
        effective_semantics={
            "strategy": StructuredOutputStrategy.PROMPT_JSON_SCHEMA.value,
            "schema_fingerprint": schema.schema_fingerprint,
        },
        adapter_version=1,
        declaration_source="test",
        registration_provenance_digest=route.registration_provenance_digest,
        code="test_structured_output",
        rationale="test structured-output adapter",
    )
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "output_format": dict(schema.canonical_schema),
        }),
    )
    return replace(
        context,
        node=node,
        variable_context=replace(context.variable_context, normalizer_version=5),
        sealed_provider_authority=replace(
            authority,
            obligations=(
                *authority.obligations,
                WorkflowCapabilityObligation(
                    path="nodes[0].output_format",
                    route_id=route.route_id,
                    decision=structured_decision,
                ),
            ),
        ),
        structured_output=WorkflowStructuredOutput(
            canonical_schema=schema.canonical_schema,
            schema_fingerprint=schema.schema_fingerprint,
        ),
        structured_output_decision=StructuredOutputCapabilityDecision(
            strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            effective_provider=route.effective_provider,
            model=route.model,
            api_mode=route.api_mode,
            declaration_source="test",
            adapter_version=1,
            schema_fingerprint=schema.schema_fingerprint,
            rationale="test structured-output adapter",
        ),
        max_provider_attempts=3,
    )
def test_phase5_executor_uses_only_sealed_route_and_returns_both_identities(
    tmp_path,
) -> None:
    runner = _Runner()

    result = AgentNodeExecutor(runner).execute(_context(tmp_path))

    assert result.status == "succeeded"
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.provider == "sealed-provider"
    assert request.model == "sealed-model"
    assert request.reasoning_config == {"enabled": True, "effort": "high"}
    assert request.intended_authority_digest == "a" * 64
    assert request.expected_runtime_identity == {
        "provider": "sealed-provider",
        "model": "sealed-model",
        "api_mode": "chat_completions",
        "base_url_trust_class": "provider_default",
        "endpoint_sha256": "d" * 64,
        "registration_provenance_digest": "3" * 64,
    }
    assert request.expected_runtime_route_fingerprint == "2" * 64
    assert request.expected_runtime_route_options == {"effort": "high"}
    assert result.metadata["intended_authority_digest"] == "a" * 64
    assert result.metadata["model_visible_prefix_digest"] == "9" * 64


def test_phase5_executor_blocks_missing_sealed_route_before_runner(tmp_path) -> None:
    runner = _Runner()

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, route=False))

    assert result.error_code == "provider_capability_drift"
    assert result.metadata["provider_attempts"] == 0
    assert runner.requests == []


def test_phase5_structured_repair_runtime_drift_blocks_before_agent_construction(
    tmp_path,
    monkeypatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider

    context = _structured_context(tmp_path)
    route = context.sealed_provider_route
    assert route is not None
    expected_identity = route.execution_runtime_identity()
    constraint = runtime_provider.CredentialFreeExecutionRouteConstraint(
        route_fingerprint=route.route_fingerprint,
        requested_provider=route.provider,
        model=route.model,
        api_mode=route.api_mode,
        base_url="https://sealed.example/v1",
        provider_config={},
        identity=expected_identity,
    )
    agent_constructions: list[str] = []
    provider_calls: list[str] = []

    class ForbiddenRepairAgent:
        def __init__(self, **_kwargs):
            agent_constructions.append("repair")
            raise AssertionError("repair agent constructed after route drift")

    monkeypatch.setattr(worker, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runtime_provider,
        "select_credential_free_execution_route",
        lambda *_args, **_kwargs: constraint,
    )
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": route.effective_provider,
            "model": route.model,
            "api_mode": route.api_mode,
            "base_url": "https://sealed.example/v1",
            "api_key": "private",
        },
    )
    monkeypatch.setattr(
        runtime_provider,
        "classify_resolved_execution_runtime",
        lambda _runtime: runtime_provider.ExecutionRuntimeCapabilities(
            api_mode=expected_identity.api_mode,
            hermes_managed_tool_loop=True,
            effective_provider=expected_identity.provider,
            model=expected_identity.model,
            base_url_trust_class=expected_identity.base_url_trust_class,
            endpoint_sha256=expected_identity.endpoint_sha256,
            registration_provenance_digest="f" * 64,
            registration_provenance_complete=True,
        ),
    )
    monkeypatch.setattr("run_agent.AIAgent", ForbiddenRepairAgent)

    class DriftRepairRunner:
        def __init__(self) -> None:
            self.requests = []
            self.cancellation_callbacks = []

        def run(self, request, **kwargs):
            self.requests.append(request)
            self.cancellation_callbacks.append(kwargs.get("is_cancelled"))
            if len(self.requests) == 1:
                agent_constructions.append("primary")
                provider_calls.append("primary")
                evidence = {
                    "provider_attempts": 1,
                    "model_calls": 1,
                    "strategy": request.structured_output.strategy.value,
                    "adapter_version": request.structured_output.adapter_version,
                    "schema_fingerprint": (
                        request.structured_output.schema.schema_fingerprint
                    ),
                    "declaration_source": "test",
                }
                return PluginAgentRunResult(
                    final_response='{"answer":"invalid"} trailing',
                    session_id="primary-session",
                    provider=expected_identity.provider,
                    model=expected_identity.model,
                    status="completed",
                    pending_interaction=None,
                    usage={},
                    audit={
                        **evidence,
                        "api_mode": expected_identity.api_mode,
                        "intended_authority_digest": (
                            request.intended_authority_digest
                        ),
                        "model_visible_prefix_digest": "9" * 64,
                    },
                    structured_output=evidence,
                )
            return PluginAgentRunResult.from_wire(
                worker._run(
                    {"plugin_id": "workflow", "request": request.to_wire()},
                    provider_start_gate=lambda: provider_calls.append("repair"),
                )
            )

    cancelled = lambda: False
    runner = DriftRepairRunner()
    result = AgentNodeExecutor(runner).execute(
        replace(context, is_cancelled=cancelled)
    )

    assert result.status == "failed"
    assert result.error_code == "provider_capability_drift"
    assert result.metadata["archon_terminal_failure"] is True
    assert agent_constructions == ["primary"]
    assert provider_calls == ["primary"]
    assert len(runner.requests) == 2
    assert runner.requests[1].expected_runtime_identity == (
        runner.requests[0].expected_runtime_identity
    )
    assert runner.requests[1].reasoning_config == runner.requests[0].reasoning_config
    assert runner.requests[1].request_overrides == runner.requests[0].request_overrides
    assert runner.requests[1].session_id is None
    assert runner.requests[1].expected_model_visible_prefix_digest is None
    assert runner.requests[1].expected_mcp_runtime_identity_digest is None
    assert runner.requests[1].allowed_tools == ()
    assert result.metadata["provider_attempts"] == 0
    assert result.metadata["provider_attempts_exact"] is True
    assert runner.cancellation_callbacks == [cancelled, cancelled]


def test_phase5_budget_exhaustion_is_terminal_and_never_repairs(tmp_path) -> None:
    context = _structured_context(tmp_path)

    class ExhaustedRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response='{"answer":"invalid"} trailing',
                session_id="primary-session",
                provider=request.expected_runtime_identity["provider"],
                model=request.model or "",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    **evidence,
                    "api_mode": request.expected_runtime_identity["api_mode"],
                    "failure_kind": "budget_exhausted",
                    "intended_authority_digest": request.intended_authority_digest,
                    "model_visible_prefix_digest": "9" * 64,
                },
                structured_output=evidence,
            )

    runner = ExhaustedRunner()
    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "budget_exhausted"
    assert result.metadata["archon_terminal_failure"] is True
    assert len(runner.requests) == 1


def test_phase5_repair_decision_contradiction_blocks_before_repair_launch(
    tmp_path,
) -> None:
    context = _structured_context(tmp_path)
    admitted = context.structured_output_decision
    assert admitted is not None
    mutated = replace(admitted, effective_provider="mutated-provider")

    class ContradictoryDecisionRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 1:
                object.__setattr__(context, "structured_output_decision", mutated)
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": "test",
            }
            return PluginAgentRunResult(
                final_response=(
                    '{"answer":"invalid"} trailing'
                    if len(self.requests) == 1
                    else '{"answer":"repaired"}'
                ),
                session_id=f"session-{len(self.requests)}",
                provider=mutated.effective_provider,
                model=mutated.model,
                status="completed",
                pending_interaction=None,
                usage={},
                audit={
                    **evidence,
                    "api_mode": mutated.api_mode,
                    "intended_authority_digest": request.intended_authority_digest,
                    "model_visible_prefix_digest": "9" * 64,
                },
                structured_output=evidence,
            )

    runner = ContradictoryDecisionRunner()
    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "provider_capability_drift"
    assert result.metadata["repair_disposition"] == (
        "ineligible_provider_capability_drift"
    )
    assert result.metadata["provider_attempts"] == 0
    assert len(runner.requests) == 1


def test_phase5_shared_context_requires_and_forwards_both_predecessor_identities(
    tmp_path,
) -> None:
    runner = _Runner()

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, shared=True))

    assert result.status == "succeeded"
    request = runner.requests[0]
    assert request.context_mode == "shared"
    assert request.session_id == "predecessor-session"
    assert request.intended_authority_digest == "a" * 64
    assert request.expected_model_visible_prefix_digest == "b" * 64


def test_runtime_prefix_drift_selects_fresh_context_before_provider_use(
    tmp_path,
) -> None:
    class DriftRunner(_Runner):
        def run(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 1:
                return PluginAgentRunResult(
                    final_response="",
                    session_id="",
                    provider="",
                    model="",
                    status="failed",
                    pending_interaction=None,
                    usage={},
                    audit={
                        "failure_kind": "cache_fingerprint_changed",
                        "provider_attempts": 0,
                        "model_calls": 0,
                        "known_no_effect": True,
                        "intended_authority_digest": "a" * 64,
                        "model_visible_prefix_digest": "c" * 64,
                    },
                )
            return PluginAgentRunResult(
                final_response="done",
                session_id="fresh-session",
                provider=request.provider or "",
                model=request.model or "",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={
                    "provider_attempts": 1,
                    "model_calls": 1,
                    "intended_authority_digest": (
                        request.intended_authority_digest
                    ),
                    "model_visible_prefix_digest": "c" * 64,
                },
            )

    runner = DriftRunner()

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, shared=True))

    assert result.status == "succeeded"
    assert len(runner.requests) == 2
    assert runner.requests[0].context_mode == "shared"
    assert runner.requests[1].context_mode == "fresh"
    assert runner.requests[1].session_id is None
    assert runner.requests[1].expected_model_visible_prefix_digest is None
    assert "runtime model-visible prefix changed; fresh context selected" in (
        result.metadata["warnings"]
    )


def test_phase5_node_session_persists_both_identities_without_changing_legacy_rows(
    tmp_path,
) -> None:
    registry = NodeSessionRegistry(tmp_path / "home")
    key = NodeSessionKey("phase5", "ask", "scope", "sealed-provider", "default")

    assert registry.compare_and_set_or_observe(
        key,
        0,
        "session-1",
        "c" * 64,
        intended_authority_digest="a" * 64,
        model_visible_prefix_digest="b" * 64,
    ) == "stale_entry_replaced"
    record = registry.get(key)

    assert record is not None
    assert record.intended_authority_digest == "a" * 64
    assert record.model_visible_prefix_digest == "b" * 64

    legacy_key = NodeSessionKey(
        "legacy", "ask", "scope", "legacy-provider", "default"
    )
    assert registry.compare_and_set_or_observe(
        legacy_key,
        0,
        "legacy-session",
        "legacy-cache-fingerprint",
    ) == "stale_entry_replaced"
    legacy = registry.get(legacy_key)
    assert legacy is not None
    assert legacy.intended_authority_digest is None
    assert legacy.model_visible_prefix_digest is None


def test_phase5_recovery_and_registry_obligations_require_paired_identities() -> None:
    key = NodeSessionKey("phase5", "ask", "scope", "sealed-provider", "default")

    recovery = PersistentSessionRecoverySelection(
        key=key,
        expected_generation=1,
        missing_session_id="missing",
        cache_fingerprint="c" * 64,
        run_id="run-1",
        attempt_id="attempt-1",
        intended_authority_digest="a" * 64,
        model_visible_prefix_digest="b" * 64,
    )
    update = SessionRegistryUpdateCandidate(
        key=key,
        expected_generation=1,
        new_session_id="new",
        cache_fingerprint="c" * 64,
        winning_run_id="run-1",
        winning_node_id="ask",
        winning_attempt_id="attempt-1",
        intended_authority_digest="a" * 64,
        model_visible_prefix_digest="b" * 64,
    )

    assert recovery.model_visible_prefix_digest == "b" * 64
    assert update.intended_authority_digest == "a" * 64
    with pytest.raises(ValueError, match="paired"):
        replace(update, model_visible_prefix_digest=None)
