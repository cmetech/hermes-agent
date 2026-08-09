from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pytest

from agent.plugin_agent import PluginAgentRunRequest, PluginAgentRunResult, _validate_request
from agent.plugin_agent_worker import _build_inline_agent_handler
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.models import WorkflowLanguageProfile, freeze_value
from plugins.workflow.resources import VariableContext
from tests.plugins.workflow.test_phase5_adversarial_remediation import (
    _CapturingRunner,
)
from tests.plugins.workflow.test_phase5_provider_authority import (
    _authority as _resolved_authority,
    _load_v5,
)


_RAW_MODEL_CONFIG = {
    "model": {
        "provider": "openrouter",
        "default": "openai/gpt-5.4",
        "base_url": "https://openrouter.ai/api/v1",
        "api_mode": "chat_completions",
    },
    "model_tiers": {
        "small": {
            "provider": "openrouter",
            "model": "openai/gpt-4.1-mini",
        },
        "large": {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4.1",
        },
    },
    "model_aliases": {
        "primary": {
            "provider": "openrouter",
            "model": "openai/gpt-5.4",
            "effort": "medium",
        },
        "recovery": {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
        },
    },
}


def _primary_phase5_request(tmp_path: Path, workflow_writer):
    path = workflow_writer(
        tmp_path / "package",
        model="@primary",
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "model": "@primary",
                "fallbackModel": "@recovery",
                "allowed_tools": ["workflow_agent"],
                "agents": {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Review the result",
                        "model": "large",
                    }
                },
            }
        ],
    )
    package = _load_v5(path)
    authority = _resolved_authority(package)
    primary = authority.routes["ask:primary"]
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=package.definition.nodes[0],
        attempt_id="attempt-1",
        workflow_name="phase5-identity-convergence",
        workflow_options=freeze_value({}),
        variable_context=VariableContext(workflow_id="run-1", normalizer_version=5),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        sealed_provider_route=primary,
        sealed_provider_authority=authority,
        intended_authority_digest="d" * 64,
        shared_context_compatibility_digest="f" * 64,
    )
    runner = _CapturingRunner()

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded", result
    return runner.requests[0], authority, context


def _phase5_requests(tmp_path: Path, workflow_writer) -> dict[str, PluginAgentRunRequest]:
    primary, authority, context = _primary_phase5_request(tmp_path, workflow_writer)
    executor = AgentNodeExecutor(_CapturingRunner())
    repair = executor._phase5_structured_repair_request(
        initial_request=primary,
        repair_prompt="repair",
        remaining_provider_attempts=1,
        remaining_timeout_seconds=30.0,
    )

    approval_runner = _CapturingRunner()
    approval_context = replace(
        context,
        node=replace(
            context.node,
            node_type="approval",
            value=freeze_value({
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            }),
            options=freeze_value({"fallbackModel": "@recovery"}),
        ),
        node_state=freeze_value({
            "approval_rework": {"reason": "missing evidence"},
        }),
    )
    approval_result = ApprovalExecutor(approval_runner).execute(approval_context)
    assert approval_result.status == "paused", approval_result
    approval = approval_runner.requests[0]

    inline_requests: list[PluginAgentRunRequest] = []

    class InlineRunner:
        def run(self, request, **_kwargs):
            inline_requests.append(request)
            return PluginAgentRunResult(
                final_response="done",
                session_id="inline-session",
                provider=request.provider or "",
                model=request.model or "",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    handler = _build_inline_agent_handler(
        plugin_id="workflow",
        definitions={
            name: dict(definition)
            for name, definition in primary.inline_agents.items()
        },
        workdir=tmp_path,
        parent_request=primary,
        runner_factory=lambda _plugin_id: InlineRunner(),
        emit_progress=lambda **_payload: None,
        pause=lambda _descriptor: None,
    )
    inline_result = handler({"agent_id": "reviewer", "task": "Inspect"})
    assert inline_result["status"] == "completed"

    return {
        "primary": primary,
        "approval": approval,
        "repair": repair,
        "inline": inline_requests[0],
    }


def _captured_fallback_request(
    primary: PluginAgentRunRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> PluginAgentRunRequest:
    import agent.plugin_agent as plugin_agent
    import agent.plugin_agent_worker as worker
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    captured: list[PluginAgentRunRequest] = []
    expected = runtime_provider.execution_runtime_identity_from_sealed_route(
        primary.expected_runtime_identity or {}
    )
    constraint = runtime_provider.CredentialFreeExecutionRouteConstraint(
        route_fingerprint=primary.expected_runtime_route_fingerprint or "",
        requested_provider=primary.provider or "",
        model=primary.model or "",
        api_mode=expected.api_mode,
        base_url="https://openrouter.ai/api/v1",
        provider_config={},
        identity=expected,
    )

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
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._interrupt_requested = False

        def seal_model_visible_prefix(self):
            return "8" * 64

        def run_conversation(self, _prompt, conversation_history=None):
            return {"failed": True, "api_calls": 1, "final_response": ""}

    class CapturingFallbackRunner:
        def __init__(self, plugin_id):
            assert plugin_id == "workflow"

        def run(self, request, **_kwargs):
            captured.append(request)
            return PluginAgentRunResult(
                final_response="fallback complete",
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
    monkeypatch.setattr(plugin_agent, "PluginAgentRunner", CapturingFallbackRunner)
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
            "provider": expected.provider,
            "model": expected.model,
            "api_mode": expected.api_mode,
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "private-test-key",
        },
    )
    request = replace(
        primary,
        allowed_tools=(),
        denied_tools=(),
        inline_agents={},
        skills=(),
        hooks=(),
        mcp_servers={},
    )

    result = worker._run({"plugin_id": "workflow", "request": request.to_wire()})

    assert result["status"] == "completed"
    assert len(captured) == 1
    return captured[0]


def test_phase5_request_cannot_downgrade_by_removing_both_authority_fields(
    tmp_path: Path,
    workflow_writer,
) -> None:
    request, _authority, _context = _primary_phase5_request(tmp_path, workflow_writer)
    downgraded = replace(
        request,
        intended_authority_digest=None,
        expected_runtime_identity=None,
        expected_runtime_route_fingerprint=None,
        expected_runtime_route_options=None,
    )

    with pytest.raises(ValueError, match="sealed runtime authority"):
        _validate_request(downgraded)


def test_every_phase5_constructor_marks_and_carries_sealed_runtime_authority(
    tmp_path: Path,
    workflow_writer,
) -> None:
    requests = _phase5_requests(tmp_path, workflow_writer)

    assert set(requests) == {"primary", "approval", "repair", "inline"}
    for request in requests.values():
        assert request.sealed_runtime_authority_required is True
        assert request.intended_authority_digest is not None
        assert request.expected_runtime_identity is not None
        assert request.expected_runtime_route_fingerprint is not None
        assert request.expected_runtime_route_options is not None
        _validate_request(request)


def test_fallback_constructor_inherits_required_sealed_runtime_authority(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _authority, _context = _primary_phase5_request(tmp_path, workflow_writer)
    fallback = _captured_fallback_request(primary, monkeypatch)

    assert fallback.sealed_runtime_authority_required is True
    assert fallback.intended_authority_digest == primary.intended_authority_digest
    assert fallback.expected_runtime_identity is not None
    assert fallback.expected_runtime_route_fingerprint is not None
    assert fallback.expected_runtime_route_options is not None
    for _mutation, changed in _structural_mutations(fallback):
        with pytest.raises((TypeError, ValueError), match="authority|identity|route"):
            _validate_request(changed)


def _structural_mutations(
    request: PluginAgentRunRequest,
) -> tuple[tuple[str, PluginAgentRunRequest], ...]:
    identity = dict(request.expected_runtime_identity or {})
    mutations: list[tuple[str, PluginAgentRunRequest]] = [
        (
            "both-authority-fields-removed",
            replace(
                request,
                intended_authority_digest=None,
                expected_runtime_identity=None,
                expected_runtime_route_fingerprint=None,
                expected_runtime_route_options=None,
            ),
        ),
        ("intended-authority-removed", replace(request, intended_authority_digest=None)),
        ("runtime-identity-removed", replace(request, expected_runtime_identity=None)),
        (
            "route-fingerprint-removed",
            replace(request, expected_runtime_route_fingerprint=None),
        ),
        (
            "route-options-removed",
            replace(request, expected_runtime_route_options=None),
        ),
        (
            "intended-authority-malformed",
            replace(request, intended_authority_digest="not-a-digest"),
        ),
        (
            "route-fingerprint-malformed",
            replace(request, expected_runtime_route_fingerprint="not-a-digest"),
        ),
        (
            "route-options-malformed",
            replace(request, expected_runtime_route_options={"effort": float("nan")}),
        ),
    ]
    for field in (
        "provider",
        "model",
        "api_mode",
        "base_url_trust_class",
        "endpoint_sha256",
        "registration_provenance_digest",
    ):
        changed = dict(identity)
        changed[field] = ""
        mutations.append(
            (f"runtime-identity-{field}-malformed", replace(request, expected_runtime_identity=changed))
        )
    return tuple(mutations)


def test_constructor_by_structural_mutation_matrix_blocks_during_validation(
    tmp_path: Path,
    workflow_writer,
) -> None:
    requests = _phase5_requests(tmp_path, workflow_writer)

    for constructor, request in requests.items():
        for mutation, changed in _structural_mutations(request):
            with pytest.raises((TypeError, ValueError), match="authority|identity|route"):
                _validate_request(changed)


def test_generic_request_may_still_omit_sealed_runtime_authority() -> None:
    request = PluginAgentRunRequest(prompt="legacy or generic plugin request")

    _validate_request(request)
    assert request.sealed_runtime_authority_required is False


def _live_route_mutations(
    request: PluginAgentRunRequest,
) -> tuple[tuple[str, PluginAgentRunRequest], ...]:
    identity = dict(request.expected_runtime_identity or {})
    mutations: list[tuple[str, PluginAgentRunRequest]] = [
        ("request-provider", replace(request, provider="phase5-mutated-provider")),
        ("request-model", replace(request, model="phase5-mutated-model")),
        (
            "route-fingerprint",
            replace(request, expected_runtime_route_fingerprint="0" * 64),
        ),
        (
            "route-options",
            replace(request, expected_runtime_route_options={"effort": "high"}),
        ),
    ]
    for field in (
        "provider",
        "model",
        "api_mode",
        "base_url_trust_class",
        "endpoint_sha256",
        "registration_provenance_digest",
    ):
        changed = dict(identity)
        changed[field] = (
            "0" * 64
            if field in {"endpoint_sha256", "registration_provenance_digest"}
            else f"phase5-mutated-{field}"
        )
        mutations.append(
            (f"runtime-identity-{field}", replace(request, expected_runtime_identity=changed))
        )
    return tuple(mutations)


def test_valid_route_mutations_fail_before_session_credentials_or_transport(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.plugin_agent_worker as worker
    import hermes_cli.config as config
    import hermes_cli.managed_scope as managed_scope
    import hermes_state

    side_effects: list[str] = []

    class ForbiddenSessionDB:
        def __init__(self, *_args, **_kwargs):
            side_effects.append("session")
            raise AssertionError("drifted request reached SessionDB")

    monkeypatch.setattr(config, "read_raw_config_readonly", lambda: _RAW_MODEL_CONFIG)
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})
    monkeypatch.setattr(hermes_state, "SessionDB", ForbiddenSessionDB)

    requests = _phase5_requests(tmp_path, workflow_writer)
    for constructor, request in requests.items():
        for mutation, changed in _live_route_mutations(request):
            result = worker._run({
                "plugin_id": "workflow",
                "request": changed.to_wire(),
            })
            assert result["status"] == "failed", (constructor, mutation, result)
            assert result["audit"]["failure_kind"] == "provider_capability_drift", (
                constructor,
                mutation,
                result,
            )
            assert result["audit"]["provider_attempts"] == 0
            assert result["audit"]["model_calls"] == 0
            assert result["audit"]["known_no_effect"] is True
            assert side_effects == []
