from __future__ import annotations

from dataclasses import replace

import pytest

from agent.plugin_agent import PluginAgentRunResult
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
    assert result.metadata["intended_authority_digest"] == "a" * 64
    assert result.metadata["model_visible_prefix_digest"] == "9" * 64


def test_phase5_executor_blocks_missing_sealed_route_before_runner(tmp_path) -> None:
    runner = _Runner()

    result = AgentNodeExecutor(runner).execute(_context(tmp_path, route=False))

    assert result.error_code == "provider_capability_drift"
    assert result.metadata["provider_attempts"] == 0
    assert runner.requests == []


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
