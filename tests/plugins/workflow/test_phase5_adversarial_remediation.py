"""Whole-bug-class invariants for the Phase 5 adversarial remediation."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path

import pytest

from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    _validate_request,
)
from hermes_cli.provider_capabilities import WorkflowProviderFeature
from plugins.workflow.admission import RunAdmissionRequest
import plugins.workflow.execution_semantics as execution_semantics
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowNode,
    freeze_value,
)
from plugins.workflow.notifications import _value_free_notification_payload
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowResolvedProviderRoute,
    public_provider_capability_projection,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
import providers
from providers import ProviderProfile
from run_agent import AIAgent
from tests.plugins.workflow.test_phase5_execution_context import (
    _compatibility_fixture,
    _decision_mutation,
    _node_option_mutation,
    _node_semantics_mutation,
    _route_mutation,
)


@pytest.fixture(autouse=True)
def _isolate_provider_registry():
    """Keep collision probes from contaminating later tests in this file."""
    registry = providers._REGISTRY.copy()
    aliases = providers._ALIASES.copy()
    registrations = providers._REGISTRATIONS.copy()
    collisions = list(providers._REGISTRATION_COLLISIONS)
    provider_list_cache = (
        None
        if providers._PROVIDER_LIST_CACHE is None
        else list(providers._PROVIDER_LIST_CACHE)
    )
    discovered = providers._discovered
    yield
    providers._REGISTRY.clear()
    providers._REGISTRY.update(registry)
    providers._ALIASES.clear()
    providers._ALIASES.update(aliases)
    providers._REGISTRATIONS.clear()
    providers._REGISTRATIONS.update(registrations)
    providers._REGISTRATION_COLLISIONS.clear()
    providers._REGISTRATION_COLLISIONS.extend(collisions)
    providers._PROVIDER_LIST_CACHE = provider_list_cache
    providers._discovered = discovered


def _route(
    node_id: str,
    *,
    role: str = "primary",
    inline_agent_id: str | None = None,
    marker: str = "1",
) -> WorkflowResolvedProviderRoute:
    suffix = role if role != "inline_agent" else f"inline_agent:{inline_agent_id}"
    return WorkflowResolvedProviderRoute(
        route_id=f"{node_id}:{suffix}",
        node_id=node_id,
        role=role,
        inline_agent_id=inline_agent_id,
        reference_kind="literal",
        requested_reference_sha256=marker * 64,
        provider=f"provider-{marker}",
        model=f"model-{marker}",
        api_mode="chat_completions",
        route_fingerprint=marker * 64,
        endpoint_sha256=marker * 64,
        registration_provenance_digest=marker * 64,
        provider_options={},
        config_scope="profile",
        base_url_trust_class="provider_default",
    )


def _authority(*routes: WorkflowResolvedProviderRoute) -> WorkflowProviderAuthority:
    return WorkflowProviderAuthority(
        config_fingerprint="a" * 64,
        routes={route.route_id: route for route in routes},
        obligations=(),
        warnings=(),
        authority_digest="b" * 64,
    )


class _CapturingRunner:
    starts_request_mcp = True

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
                "model_visible_prefix_digest": "c" * 64,
            },
        )


def _assert_sealed_route_identity(
    value: object,
    route: WorkflowResolvedProviderRoute,
) -> None:
    assert value == route.execution_runtime_identity().to_dict()
    assert value is not None
    assert set(value) == {
        "provider",
        "model",
        "api_mode",
        "base_url_trust_class",
        "endpoint_sha256",
        "registration_provenance_digest",
    }


def test_intended_authority_cannot_enter_worker_without_runtime_identity() -> None:
    request = PluginAgentRunRequest(
        prompt="sealed request",
        provider="openrouter",
        model="openai/gpt-5.4",
        intended_authority_digest="a" * 64,
    )

    with pytest.raises(
        ValueError,
        match="intended authority requires expected runtime identity",
    ):
        _validate_request(request)


def test_all_phase5_request_routes_use_their_exact_endpoint_bound_identity(
    tmp_path: Path,
) -> None:
    primary = _route("ask", marker="1")
    fallback = _route("ask", role="fallback", marker="2")
    inline = _route(
        "ask",
        role="inline_agent",
        inline_agent_id="reviewer",
        marker="3",
    )
    authority = _authority(primary, fallback, inline)
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=WorkflowNode(
            id="ask",
            node_type="prompt",
            value="hello",
            depends_on=(),
            source_index=0,
            source_line=1,
            options=freeze_value({
                "fallbackModel": "fallback",
                "allowed_tools": ("workflow_agent",),
                "agents": {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Review the result",
                    }
                },
            }),
        ),
        attempt_id="attempt-1",
        workflow_name="phase5-remediation",
        workflow_options=freeze_value({}),
        variable_context=VariableContext(workflow_id="run-1"),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        sealed_provider_route=primary,
        sealed_provider_authority=authority,
        intended_authority_digest="d" * 64,
        shared_context_compatibility_digest="f" * 64,
    )
    runner = _CapturingRunner()
    executor = AgentNodeExecutor(runner)

    result = executor.execute(context)

    assert result.status == "succeeded", result
    request = runner.requests[0]
    _assert_sealed_route_identity(request.expected_runtime_identity, primary)
    _assert_sealed_route_identity(
        request.sealed_fallback_route["expected_runtime_identity"],
        fallback,
    )
    _assert_sealed_route_identity(
        request.inline_agents["reviewer"]["expected_runtime_identity"],
        inline,
    )
    repair = executor._phase5_structured_repair_request(
        initial_request=request,
        repair_prompt="repair",
        remaining_provider_attempts=1,
        remaining_timeout_seconds=30.0,
    )
    _assert_sealed_route_identity(repair.expected_runtime_identity, primary)
    assert repair.intended_authority_digest == request.intended_authority_digest


def test_phase5_approval_primary_and_fallback_use_central_route_identity(
    tmp_path: Path,
) -> None:
    primary = _route("review", marker="4")
    fallback = _route("review", role="fallback", marker="5")
    authority = _authority(primary, fallback)
    run_directory = tmp_path / "approval-run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-approval",
        run_directory=run_directory,
        node=WorkflowNode(
            id="review",
            node_type="approval",
            value=freeze_value({
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            }),
            depends_on=(),
            source_index=0,
            source_line=1,
            options=freeze_value({"fallbackModel": "fallback"}),
        ),
        attempt_id="attempt-approval",
        workflow_options=freeze_value({}),
        variable_context=VariableContext(workflow_id="run-approval"),
        node_state=freeze_value({
            "approval_rework": {"reason": "missing evidence"},
        }),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        sealed_provider_route=primary,
        sealed_provider_authority=authority,
        intended_authority_digest="e" * 64,
    )
    runner = _CapturingRunner()

    result = ApprovalExecutor(runner).execute(context)

    assert result.status == "paused", result
    request = runner.requests[0]
    _assert_sealed_route_identity(request.expected_runtime_identity, primary)
    _assert_sealed_route_identity(
        request.sealed_fallback_route["expected_runtime_identity"],
        fallback,
    )


@pytest.mark.parametrize(
    ("semantic_class", "mutate"),
    (
        ("provider endpoint", _route_mutation("endpoint_sha256", "0" * 64)),
        ("tool policy", _node_option_mutation("allowed_tools", ("read_file",))),
        ("MCP", _node_option_mutation("mcp", "other-server")),
        ("skill", _node_option_mutation("skills", ("other-skill.md",))),
        ("hook", _node_semantics_mutation),
        (
            "inline agent",
            _node_option_mutation(
                "agents",
                {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Use a changed sealed instruction",
                    }
                },
            ),
        ),
        ("prompt configuration", _node_option_mutation("systemPrompt", "changed")),
        (
            "adapter",
            _decision_mutation(
                WorkflowProviderFeature.STRUCTURED_OUTPUT,
                adapter_version=2,
            ),
        ),
    ),
)
def test_shared_context_crosses_node_ids_but_not_cache_semantics(
    semantic_class: str,
    mutate,
) -> None:
    package, authority = _compatibility_fixture()
    baseline = execution_semantics.phase5_shared_context_compatibility_digest(
        package,
        authority,
        node_id="ask",
        sealed_closure_digest="7" * 64,
    )
    compatible_other_node = (
        execution_semantics.phase5_shared_context_compatibility_digest(
            package,
            authority,
            node_id="followup",
            sealed_closure_digest="7" * 64,
        )
    )
    changed_package, changed_authority, changed_closure = mutate(
        package,
        authority,
        "7" * 64,
    )

    assert compatible_other_node == baseline
    assert execution_semantics.phase5_shared_context_compatibility_digest(
        changed_package,
        changed_authority,
        node_id="ask",
        sealed_closure_digest=changed_closure,
    ) != baseline, semantic_class


def test_scheduler_restart_recovers_only_recorded_winner_identity(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "restart-home"
    store = RunStore(home)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="restart-identity")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="restart-identity",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    claim = store.claim_node(admitted.run_id, "start", "identity-owner")
    assert claim is not None
    store.mark_node_started(claim)
    identity = {
        "intended_authority_digest": "1" * 64,
        "model_visible_prefix_digest": "2" * 64,
        "shared_context_compatibility_digest": "3" * 64,
    }
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": "winning-session",
            "cache_fingerprint": "4" * 64,
            **identity,
        },
    )
    (store.run_directory(admitted.run_id) / "run.json").unlink()

    recovered = RunStore(home).load_run(admitted.run_id)
    predecessor = RunScheduler._predecessor_results(
        recovered,
        ("start",),
        {},
    )["start"]

    assert predecessor == {
        "session_id": "winning-session",
        "cache_fingerprint": "4" * 64,
        **identity,
    }


def _register_at_origin(profile: ProviderProfile, origin: str) -> None:
    context = providers._RegistrationContext(
        origin_kind=origin,
        distribution_id=f"{origin}-defensive-test",
        distribution_version="1",
        package_root=None,
    )
    token = providers._REGISTRATION_CONTEXT.set(context)
    try:
        providers.register_provider(profile)
    finally:
        providers._REGISTRATION_CONTEXT.reset(token)


@pytest.mark.parametrize("alias_origin", tuple(providers._ORIGIN_PRECEDENCE))
def test_canonical_provider_token_cannot_be_shadowed_by_any_alias_origin(
    alias_origin: str,
) -> None:
    providers._REGISTRY.clear()
    providers._ALIASES.clear()
    providers._REGISTRATIONS.clear()
    providers._REGISTRATION_COLLISIONS.clear()
    providers._discovered = True
    canonical = ProviderProfile(name="canonical-provider")
    shadow = ProviderProfile(name=f"shadow-{alias_origin}", aliases=("canonical-provider",))
    _register_at_origin(canonical, "bundled")

    _register_at_origin(shadow, alias_origin)

    assert providers.get_provider_profile("canonical-provider") is canonical
    assert providers.list_provider_registration_collisions()[-1].code == (
        "provider_alias_rejected_canonical"
    )


class _CanaryEvidenceStore:
    def __init__(self, private: dict[str, str]) -> None:
        self.private = private

    def get_run_status(self, _run_id: str, *, operator_scope=None):
        return {
            "provider_resolution_sha256": "9" * 64,
            "nodes": {
                "ask": {
                    "attempts": [{
                        "attempt_id": "attempt-1",
                        "state": "failed",
                        "error_code": "provider_capability_drift",
                        "error_message": "runtime authority changed",
                        "metadata": {
                            "intended_authority_digest": "8" * 64,
                            "expected_runtime_identity": {
                                "endpoint_sha256": self.private["endpoint_sha256"],
                                "registration_provenance_digest": self.private[
                                    "registration_provenance_digest"
                                ],
                            },
                            "audit": self.private,
                        },
                    }]
                }
            },
        }


def test_public_surface_and_log_closure_excludes_private_canaries(
    tmp_path: Path,
    caplog,
) -> None:
    private = {
        "credential": "sk-CREDENTIAL_CANARY",
        "base_url": "https://user:pass@example.invalid/v1?token=URL_CANARY",
        "endpoint_sha256": "6" * 64,
        "registration_provenance_digest": "7" * 64,
        "prompt": "PROMPT_CANARY",
        "command": "COMMAND_CANARY",
        "provider_response": "PROVIDER_PAYLOAD_CANARY",
        "feedback": "FEEDBACK_CANARY",
        "path": str(tmp_path / "ABSOLUTE_PATH_CANARY"),
    }
    unsafe_provider = private["base_url"]
    unsafe_model = private["credential"]
    route = replace(
        _route("ask", marker="6"),
        provider=unsafe_provider,
        model=unsafe_model,
        endpoint_sha256=private["endpoint_sha256"],
        registration_provenance_digest=private[
            "registration_provenance_digest"
        ],
    )
    authority = _authority(route)
    provider_projection = public_provider_capability_projection(
        authority,
        include_details=True,
    )
    evidence = EvidenceReader(_CanaryEvidenceStore(private)).query(
        "run-1",
        kind="attempts",
    )
    notification = _value_free_notification_payload({
        "run_id": "run-1",
        "kind": "failure",
        "provider_capability": provider_projection,
        "evidence": evidence,
    })
    from plugins.workflow.dashboard.plugin_api import (
        WorkflowProviderCapabilityProjection,
    )

    rest_payload = WorkflowProviderCapabilityProjection.model_validate(
        provider_projection
    ).model_dump(mode="json")
    desktop_backend_payload = json.loads(json.dumps(rest_payload))
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    class _BadClient:
        def close(self) -> None:
            raise RuntimeError(" ".join(private.values()))

    AIAgent._retire_adopted_prior_client(
        AIAgent.__new__(AIAgent),
        _BadClient(),
        retirement_kind="anthropic",
        candidate_safe=True,
    )
    public_channels = {
        "diagnostic/catalog/detail": provider_projection,
        "evidence": evidence,
        "notification": notification,
        "REST": rest_payload,
        "Desktop backend": desktop_backend_payload,
        "log": caplog.text,
    }
    for channel, value in public_channels.items():
        rendered = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
        for canary in private.values():
            assert canary not in rendered, channel
