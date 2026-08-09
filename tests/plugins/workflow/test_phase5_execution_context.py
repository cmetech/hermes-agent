from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    WorkflowDefinition,
    WorkflowLanguageProfile,
    WorkflowLanguageMetadata,
    WorkflowNode,
    WorkflowPackage,
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


def _compatibility_route(
    node_id: str,
    role: str = "primary",
    *,
    inline_agent_id: str | None = None,
) -> WorkflowResolvedProviderRoute:
    suffix = {
        "primary": "primary",
        "fallback": "fallback",
    }.get(role, f"inline_agent:{inline_agent_id}")
    return WorkflowResolvedProviderRoute(
        route_id=f"{node_id}:{suffix}",
        node_id=node_id,
        role=role,
        inline_agent_id=inline_agent_id,
        reference_kind="configured_alias",
        requested_reference_sha256="1" * 64,
        provider="sealed-provider",
        model=("fallback-model" if role == "fallback" else "sealed-model"),
        api_mode="chat_completions",
        route_fingerprint=("8" if role == "fallback" else "2") * 64,
        endpoint_sha256="d" * 64,
        registration_provenance_digest="3" * 64,
        provider_options={"effort": "high", "thinking": {"budget": 64}},
        config_scope="profile",
        base_url_trust_class="provider_default",
    )


def _compatibility_decision(
    route: WorkflowResolvedProviderRoute,
    feature: WorkflowProviderFeature,
    *,
    option: str | None,
    requested: dict[str, object],
    adapter_version: int = 1,
) -> ProviderCapabilityDecision:
    return ProviderCapabilityDecision(
        feature=feature,
        disposition=CapabilityDisposition.HERMES_ADAPTER,
        provider=route.provider,
        model=route.model,
        option=option,
        requested_semantics=requested,
        effective_semantics={"contract": feature.value, "version": 1},
        adapter_version=adapter_version,
        declaration_source="test",
        registration_provenance_digest=route.registration_provenance_digest,
        code=f"test_{feature.value}",
        rationale=f"test {feature.value} contract",
    )


def _compatibility_fixture() -> tuple[WorkflowPackage, WorkflowProviderAuthority]:
    semantic_options = {
        "provider": "sealed-provider",
        "model": "@primary",
        "effort": "high",
        "thinking": {"budget": 64},
        "allowed_tools": ("terminal", "mcp_echo"),
        "denied_tools": ("browser_navigate",),
        "mcp": "echo",
        "skills": ("review.md",),
        "agents": {
            "reviewer": {
                "description": "Review the answer",
                "prompt": "Review it",
                "model": "@primary",
                "tools": ("terminal",),
                "disallowedTools": ("browser_navigate",),
                "skills": ("review.md",),
                "maxTurns": 3,
            }
        },
        "systemPrompt": "Follow the sealed workflow policy.",
        "fallbackModel": "fallback-model",
        "maxBudgetUsd": 2.5,
        "sandbox": {"kind": "native", "network": False},
        "context": "shared",
        "retry": {"max_attempts": 2, "delay_ms": 5, "on_error": "transient"},
        "idle_timeout": 30.0,
        "persist_session": True,
        "when": "always",
        "trigger_rule": "all_success",
        "always_run": False,
    }
    node_a = WorkflowNode(
        id="ask",
        node_type="prompt",
        value="first user turn",
        depends_on=("seed-a",),
        source_index=0,
        source_line=10,
        options=freeze_value(semantic_options),
    )
    node_b = WorkflowNode(
        id="followup",
        node_type="prompt",
        value="second user turn",
        depends_on=("ask",),
        source_index=7,
        source_line=90,
        options=freeze_value(semantic_options),
    )
    definition = WorkflowDefinition(
        name="shared-context",
        description="shared context fixture",
        nodes=(node_a, node_b),
        options=freeze_value({"persist_sessions": True}),
        source_path=Path("definition.yaml"),
    )
    structured = WorkflowStructuredOutput(
        canonical_schema=freeze_value({
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }),
        schema_fingerprint="e" * 64,
        canonicalization_version=1,
    )
    hook_contract = freeze_value({
        "provider_portability": {
            "hooks": ({
                "event": "PreToolUse",
                "hermes_event": "pre_tool_call",
                "matcher": "terminal",
                "operations": ({"name": "permissionDecision", "value": "allow"},),
                "timeout_seconds": 5.0,
            },),
            "mcp_reference": "echo",
        }
    })
    language = WorkflowLanguageMetadata(
        declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        normalized_definition_digest="f" * 64,
        structured_outputs=freeze_value({
            "ask": structured,
            "followup": structured,
        }),
        node_semantics=freeze_value({
            "ask": hook_contract,
            "followup": hook_contract,
        }),
    )
    package = WorkflowPackage(
        source_definition=definition,
        definition=definition,
        root=Path("."),
        workflow_path=Path("definition.yaml"),
        sidecar_path=Path("definition.hermes.yaml"),
        sidecar=freeze_value({"language_compatibility": "archon-2026-07"}),
        source="test",
        precedence=0,
        language=language,
        compatibility_findings=(),
    )

    routes: dict[str, WorkflowResolvedProviderRoute] = {}
    obligations: list[WorkflowCapabilityObligation] = []
    decisions = (
        (WorkflowProviderFeature.EFFORT_THINKING, "effort", {"value": "high"}),
        (
            WorkflowProviderFeature.TOOL_RESTRICTIONS,
            "allowed_tools",
            {"hermes_schema_selection": True, "hermes_dispatch": True},
        ),
        (
            WorkflowProviderFeature.HOOKS,
            "PreToolUse",
            {"normalized": True, "events_supported": True},
        ),
        (
            WorkflowProviderFeature.MCP,
            "stdio",
            {
                "sealed_definition": True,
                "runtime_identity_digest": "9" * 64,
                "import_policy_version": 1,
            },
        ),
        (
            WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
            "inline_agents",
            {"declared_worker_tool": True, "shared_limits": True},
        ),
        (
            WorkflowProviderFeature.STRUCTURED_OUTPUT,
            "json_schema",
            {"schema_fingerprint": structured.schema_fingerprint},
        ),
        (
            WorkflowProviderFeature.FALLBACK_MODELS,
            None,
            {"route_resolved": True, "fresh_context": True},
        ),
        (
            WorkflowProviderFeature.COST_BUDGETS,
            "maxBudgetUsd",
            {"authoritative_settlement": True, "single_unsettled": True},
        ),
        (
            WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
            "sandbox",
            {"value": {"kind": "native", "network": False}},
        ),
    )
    for node_index, node_id in enumerate(("ask", "followup")):
        primary = _compatibility_route(node_id)
        fallback = _compatibility_route(node_id, "fallback")
        inline = _compatibility_route(
            node_id,
            "inline_agent",
            inline_agent_id="reviewer",
        )
        routes.update({
            primary.route_id: primary,
            fallback.route_id: fallback,
            inline.route_id: inline,
        })
        obligations.extend(
            WorkflowCapabilityObligation(
                path=f"nodes[{node_index}].{option or feature.value}",
                route_id=primary.route_id,
                decision=_compatibility_decision(
                    primary,
                    feature,
                    option=option,
                    requested=requested,
                ),
            )
            for feature, option, requested in decisions
        )
    authority = WorkflowProviderAuthority(
        config_fingerprint="4" * 64,
        routes=routes,
        obligations=tuple(obligations),
        warnings=(),
        authority_digest="5" * 64,
    )
    return package, authority


def _replace_compatibility_node(
    package: WorkflowPackage,
    node_id: str,
    **changes: object,
) -> WorkflowPackage:
    nodes = tuple(
        replace(node, **changes) if node.id == node_id else node
        for node in package.definition.nodes
    )
    definition = replace(package.definition, nodes=nodes)
    return replace(
        package,
        definition=definition,
        source_definition=replace(package.source_definition, nodes=nodes),
    )


def _node_option_mutation(field: str, value: object):
    def mutate(package, authority, closure):
        node = next(node for node in package.definition.nodes if node.id == "ask")
        options = dict(node.options)
        options[field] = value
        return (
            _replace_compatibility_node(
                package,
                "ask",
                options=freeze_value(options),
            ),
            authority,
            closure,
        )

    return mutate


def _route_mutation(field: str, value: object, *, role: str = "primary"):
    def mutate(package, authority, closure):
        route_id = f"ask:{role}"
        routes = dict(authority.routes)
        routes[route_id] = replace(routes[route_id], **{field: value})
        return package, replace(authority, routes=routes), closure

    return mutate


def _decision_mutation(feature: WorkflowProviderFeature, **changes: object):
    def mutate(package, authority, closure):
        obligations = tuple(
            replace(item, decision=replace(item.decision, **changes))
            if item.route_id == "ask:primary" and item.decision.feature is feature
            else item
            for item in authority.obligations
        )
        return package, replace(authority, obligations=obligations), closure

    return mutate


def _node_semantics_mutation(package, authority, closure):
    semantics_by_node = dict(package.language.node_semantics)
    semantics = dict(semantics_by_node["ask"])
    portability = dict(semantics["provider_portability"])
    hooks = list(portability["hooks"])
    hooks[0] = freeze_value({**dict(hooks[0]), "matcher": "read_file"})
    portability["hooks"] = tuple(hooks)
    semantics["provider_portability"] = freeze_value(portability)
    semantics_by_node["ask"] = freeze_value(semantics)
    return (
        replace(
            package,
            language=replace(
                package.language,
                node_semantics=freeze_value(semantics_by_node),
            ),
        ),
        authority,
        closure,
    )


def _structured_output_mutation(package, authority, closure):
    outputs = dict(package.language.structured_outputs)
    outputs["ask"] = replace(
        outputs["ask"],
        schema_fingerprint="0" * 64,
        canonicalization_version=2,
    )
    return (
        replace(
            package,
            language=replace(
                package.language,
                structured_outputs=freeze_value(outputs),
            ),
        ),
        authority,
        closure,
    )


def _sealed_closure_mutation(package, authority, _closure):
    return package, authority, "6" * 64


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


def test_phase5_shared_compatibility_is_distinct_from_node_authority() -> None:
    package, authority = _compatibility_fixture()

    intended_a = semantics.phase5_node_intended_authority_digest(
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    intended_b = semantics.phase5_node_intended_authority_digest(
        authority,
        node_id="followup",
        sealed_closure_digest="7" * 64,
    )
    shared_a = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    shared_b = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="followup",
        sealed_closure_digest="7" * 64,
    )

    assert intended_a != intended_b
    assert shared_a == shared_b


@pytest.mark.parametrize(
    ("semantic_input", "mutate"),
    (
        pytest.param(
            "endpoint",
            _route_mutation("endpoint_sha256", "0" * 64),
            id="endpoint",
        ),
        pytest.param(
            "provider selector",
            _route_mutation("provider", "other-provider"),
            id="provider",
        ),
        pytest.param(
            "effective provider",
            _route_mutation("effective_provider", "effective-other"),
            id="effective-provider",
        ),
        pytest.param(
            "model",
            _route_mutation("model", "other-model"),
            id="model",
        ),
        pytest.param(
            "API mode",
            _route_mutation("api_mode", "responses"),
            id="api-mode",
        ),
        pytest.param(
            "provider options and reasoning",
            _route_mutation(
                "provider_options",
                {"effort": "low", "thinking": {"budget": 32}},
            ),
            id="provider-options-reasoning",
        ),
        pytest.param(
            "allowed tools",
            _node_option_mutation("allowed_tools", ("terminal",)),
            id="allowed-tools",
        ),
        pytest.param(
            "denied tools",
            _node_option_mutation("denied_tools", ("browser_navigate", "web_search")),
            id="denied-tools",
        ),
        pytest.param("hooks", _node_semantics_mutation, id="hooks"),
        pytest.param(
            "MCP definition and tool contract",
            _node_option_mutation("mcp", "other-echo"),
            id="mcp-definition-tools",
        ),
        pytest.param(
            "MCP import policy",
            _decision_mutation(
                WorkflowProviderFeature.MCP,
                requested_semantics={
                    "sealed_definition": True,
                    "runtime_identity_digest": "9" * 64,
                    "import_policy_version": 2,
                },
            ),
            id="mcp-import-policy",
        ),
        pytest.param(
            "skills",
            _node_option_mutation("skills", ("other-skill.md",)),
            id="skills",
        ),
        pytest.param(
            "inline agents",
            _node_option_mutation(
                "agents",
                {
                    "reviewer": {
                        "description": "Review the answer",
                        "prompt": "Review more strictly",
                        "model": "@primary",
                        "tools": ("terminal",),
                        "disallowedTools": ("browser_navigate",),
                        "skills": ("review.md",),
                        "maxTurns": 3,
                    }
                },
            ),
            id="inline-agents",
        ),
        pytest.param(
            "system prompt configuration",
            _node_option_mutation("systemPrompt", "Use the other sealed policy."),
            id="system-prompt",
        ),
        pytest.param(
            "fallback route",
            _route_mutation("model", "other-fallback", role="fallback"),
            id="fallback",
        ),
        pytest.param(
            "structured output schema",
            _structured_output_mutation,
            id="structured-output",
        ),
        pytest.param(
            "budget decision",
            _node_option_mutation("maxBudgetUsd", 3.5),
            id="budget",
        ),
        pytest.param(
            "sandbox decision",
            _node_option_mutation(
                "sandbox",
                {"kind": "native", "network": True},
            ),
            id="sandbox",
        ),
        pytest.param(
            "adapter version",
            _decision_mutation(
                WorkflowProviderFeature.STRUCTURED_OUTPUT,
                adapter_version=2,
            ),
            id="adapter-version",
        ),
        pytest.param(
            "registration provenance",
            _route_mutation("registration_provenance_digest", "0" * 64),
            id="registration-provenance",
        ),
        pytest.param(
            "route trust class",
            _route_mutation("base_url_trust_class", "custom"),
            id="route-trust-class",
        ),
        pytest.param(
            "sealed closure",
            _sealed_closure_mutation,
            id="sealed-closure",
        ),
    ),
)
def test_phase5_shared_compatibility_changes_for_cache_semantics(
    semantic_input,
    mutate,
) -> None:
    package, authority = _compatibility_fixture()
    baseline = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )

    changed_package, changed_authority, changed_closure = mutate(
        package,
        authority,
        "7" * 64,
    )

    assert baseline != semantics.phase5_shared_context_compatibility_digest(
        changed_package,
        changed_authority,
        node_id="ask",
        sealed_closure_digest=changed_closure,
    ), semantic_input


@pytest.mark.parametrize(
    ("structural_input", "changes"),
    (
        pytest.param("source path", {"source_path": Path("moved.yaml")}, id="path"),
        pytest.param("source index", {"source_index": 42}, id="source-index"),
        pytest.param("source line", {"source_line": 420}, id="source-line"),
        pytest.param(
            "dependency and graph position",
            {"depends_on": ("some-other-node",)},
            id="dependency-graph-position",
        ),
        pytest.param("node prompt", {"value": "different next user turn"}, id="prompt"),
    ),
)
def test_phase5_shared_compatibility_excludes_node_location_and_turn_fields(
    structural_input,
    changes,
) -> None:
    package, authority = _compatibility_fixture()
    baseline = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    if "source_path" in changes:
        definition = replace(package.definition, source_path=changes["source_path"])
        changed = replace(
            package,
            definition=definition,
            source_definition=replace(
                package.source_definition,
                source_path=changes["source_path"],
            ),
        )
    else:
        changed = _replace_compatibility_node(package, "ask", **changes)

    assert baseline == semantics.phase5_shared_context_compatibility_digest(
        changed,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    ), structural_input


@pytest.mark.parametrize(
    ("control", "value"),
    (
        pytest.param("context", "fresh", id="context"),
        pytest.param(
            "retry",
            {"max_attempts": 4, "delay_ms": 100, "on_error": "all"},
            id="retry",
        ),
        pytest.param("idle_timeout", 99.0, id="timeout"),
        pytest.param("persist_session", False, id="persistence"),
        pytest.param("when", "success", id="schedule-when"),
        pytest.param("trigger_rule", "all_done", id="schedule-trigger"),
        pytest.param("always_run", True, id="schedule-always-run"),
    ),
)
def test_phase5_shared_compatibility_excludes_execution_controls(control, value) -> None:
    package, authority = _compatibility_fixture()
    baseline = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    changed_package, _, _ = _node_option_mutation(control, value)(
        package,
        authority,
        "7" * 64,
    )

    assert baseline == semantics.phase5_shared_context_compatibility_digest(
        changed_package,
        authority,
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
        shared_context_compatibility_digest="c" * 64,
        predecessor_results=(
            {
                "before": {
                    "session_id": "predecessor-session",
                    "cache_fingerprint": semantics.phase5_session_cache_fingerprint(
                        "d" * 64,
                        "b" * 64,
                    ),
                    "intended_authority_digest": "d" * 64,
                    "model_visible_prefix_digest": "b" * 64,
                    "shared_context_compatibility_digest": "c" * 64,
                }
            }
            if shared
            else {}
        ),
    )


def _distinct_node_shared_context(tmp_path) -> tuple[NodeExecutionContext, str]:
    package, authority = _compatibility_fixture()
    for node_id in ("ask", "followup"):
        original = next(
            node for node in package.definition.nodes if node.id == node_id
        )
        options = {
            key: value
            for key, value in original.options.items()
            if key
            not in {
                "agents",
                "effort",
                "fallbackModel",
                "maxBudgetUsd",
                "mcp",
                "sandbox",
                "skills",
                "thinking",
            }
        }
        package = _replace_compatibility_node(
            package,
            node_id,
            options=freeze_value(options),
        )
    authority = replace(
        authority,
        routes={
            route_id: replace(route, provider_options={})
            for route_id, route in authority.routes.items()
        },
    )
    node = next(node for node in package.definition.nodes if node.id == "followup")
    closure = "7" * 64
    predecessor_intended = semantics.phase5_node_intended_authority_digest(
        authority,
        node_id="ask",
        sealed_closure_digest=closure,
    )
    current_intended = semantics.phase5_node_intended_authority_digest(
        authority,
        node_id="followup",
        sealed_closure_digest=closure,
    )
    predecessor_shared = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest=closure,
    )
    current_shared = semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="followup",
        sealed_closure_digest=closure,
    )
    prefix = "b" * 64
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    return (
        NodeExecutionContext(
            run_id="run-1",
            run_directory=run_directory,
            node=node,
            attempt_id="attempt-followup",
            workflow_name=package.definition.name,
            workflow_options=package.definition.options,
            variable_context=VariableContext(
                arguments="",
                workflow_id="run-1",
                normalizer_version=5,
            ),
            predecessor_results={
                "ask": {
                    "session_id": "predecessor-session",
                    "cache_fingerprint": semantics.phase5_session_cache_fingerprint(
                        predecessor_intended,
                        prefix,
                    ),
                    "intended_authority_digest": predecessor_intended,
                    "model_visible_prefix_digest": prefix,
                    "shared_context_compatibility_digest": predecessor_shared,
                }
            },
            ai_entitlement=AIEntitlementResolution("real"),
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=5,
            sealed_provider_route=authority.routes["followup:primary"],
            sealed_provider_authority=authority,
            intended_authority_digest=current_intended,
            shared_context_compatibility_digest=current_shared,
        ),
        predecessor_intended,
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

    assert result.status == "succeeded", result
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


def test_phase5_repair_budget_exhaustion_preserves_exact_primary_accounting(
    tmp_path,
) -> None:
    context = _structured_context(tmp_path)

    class RepairBudgetExhaustedRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 2:
                return PluginAgentRunResult(
                    final_response="",
                    session_id=None,
                    provider=request.expected_runtime_identity["provider"],
                    model=request.model or "",
                    status="failed",
                    pending_interaction=None,
                    usage={},
                    audit={
                        "failure_kind": "budget_exhausted",
                        "provider_attempts": 0,
                        "model_calls": 0,
                        "known_no_effect": True,
                    },
                )
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
                status="completed",
                pending_interaction=None,
                usage={},
                audit={
                    **evidence,
                    "api_mode": request.expected_runtime_identity["api_mode"],
                    "intended_authority_digest": request.intended_authority_digest,
                    "model_visible_prefix_digest": "9" * 64,
                },
                structured_output=evidence,
            )

    runner = RepairBudgetExhaustedRunner()
    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "budget_exhausted"
    assert result.metadata["archon_terminal_failure"] is True
    assert result.metadata["provider_attempts"] == 0
    assert result.metadata["provider_attempts_exact"] is True
    assert result.metadata["audit"]["provider_attempts"] == 1
    assert result.metadata["audit"]["provider_attempts_exact"] is True
    assert result.metadata["audit"]["model_calls"] == 1
    assert result.metadata["audit"]["model_calls_exact"] is True
    assert len(runner.requests) == 2


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


def test_phase5_shared_context_uses_compatible_distinct_node_authorities(
    tmp_path,
) -> None:
    runner = _Runner()
    context, predecessor_intended = _distinct_node_shared_context(tmp_path)

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded", result
    request = runner.requests[0]
    assert request.context_mode == "shared"
    assert request.session_id == "predecessor-session"
    assert request.intended_authority_digest == context.intended_authority_digest
    assert request.intended_authority_digest != predecessor_intended
    assert request.expected_model_visible_prefix_digest == "b" * 64
    assert context.predecessor_results["ask"]["cache_fingerprint"] == (
        semantics.phase5_session_cache_fingerprint(
            predecessor_intended,
            "b" * 64,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    ("shared_compatibility", "predecessor_cache"),
)
def test_phase5_shared_context_mismatch_blocks_before_worker(
    tmp_path,
    mutation,
) -> None:
    runner = _Runner()
    context, _ = _distinct_node_shared_context(tmp_path)
    predecessor = dict(context.predecessor_results["ask"])
    if mutation == "shared_compatibility":
        predecessor["shared_context_compatibility_digest"] = "0" * 64
    else:
        predecessor["cache_fingerprint"] = semantics.phase5_session_cache_fingerprint(
            str(context.intended_authority_digest),
            str(predecessor["model_visible_prefix_digest"]),
        )
    context = replace(context, predecessor_results={"ask": predecessor})

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "context_incompatible"
    assert runner.requests == []


def test_phase5_fresh_context_never_consumes_predecessor_session(tmp_path) -> None:
    runner = _Runner()
    context, _ = _distinct_node_shared_context(tmp_path)
    context = replace(
        context,
        node=replace(
            context.node,
            options=freeze_value({**dict(context.node.options), "context": "fresh"}),
        ),
        predecessor_results={
            "ask": {
                "session_id": "must-not-be-used",
                "cache_fingerprint": "malformed",
            }
        },
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded", result
    request = runner.requests[0]
    assert request.context_mode == "fresh"
    assert request.session_id is None
    assert request.expected_model_visible_prefix_digest is None


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
