from __future__ import annotations

from dataclasses import asdict, replace

from agent.cost_budget import AuthoritativeCostBudget
from agent.plugin_agent import PluginAgentRunRequest
from agent.provider_attempts import (
    reserve_provider_transport_attempt,
    settle_provider_transport_attempt,
)
from agent.plugin_agent_worker import _run
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.models import freeze_value
from plugins.workflow.provider_authority import ProviderAuthorityEnvironment
from tests.plugins.workflow.test_phase5_provider_authority import (
    _authority,
    _environment,
    _load_v5,
)
from tests.plugins.workflow.test_phase5_execution_context import _Runner, _context


def test_openrouter_budget_stays_unsupported_even_when_local_seams_exist(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="openai/gpt-5.4",
        nodes=[{"id": "ask", "prompt": "hello", "maxBudgetUsd": 1}],
    )
    package = _load_v5(path)
    environment = replace(_environment(), authoritative_cost_available=True)

    authority = _authority(package, environment=environment)
    report = assess_compatibility(package, provider_authority=authority)

    codes = {
        item.code
        for item in report.blocking_findings
        if item.path == "nodes[0].maxBudgetUsd"
    }
    assert "authoritative_cost_unavailable" in codes
    assert report.runnable is False


def test_environment_cost_fact_is_boolean_and_cannot_smuggle_provider_contract():
    values = asdict(_environment())
    values["authoritative_cost_available"] = "openrouter-usage-cost"

    try:
        ProviderAuthorityEnvironment(**values)
    except TypeError as exc:
        assert "boolean" in str(exc)
    else:
        raise AssertionError("provider contract must not come from environment data")


def test_phase5_execution_defense_blocks_budget_without_provider_contract(tmp_path):
    runner = _Runner()
    context = _context(tmp_path)
    node = replace(
        context.node,
        options=freeze_value({
            **dict(context.node.options),
            "maxBudgetUsd": 1,
        }),
    )

    result = AgentNodeExecutor(runner).execute(replace(context, node=node))

    assert result.error_code == "authoritative_cost_unavailable"
    assert result.metadata == {
        "provider_attempts": 0,
        "known_no_effect": True,
        "archon_terminal_failure": True,
    }
    assert runner.requests == []


def test_worker_settles_one_authoritative_overrun_and_returns_terminal_evidence(
    monkeypatch, tmp_path
):
    import hermes_cli.runtime_provider as runtime_provider
    import hermes_state
    import run_agent

    class FakeDB:
        pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = "cost-session"
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self.tools = []
            self.valid_tool_names = set()
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self._api_call_count = 1
            self._interrupt_requested = False

        def run_conversation(self, _prompt, conversation_history=None):
            reserve_provider_transport_attempt(self)
            settle_provider_transport_attempt(self, {"cost": "1.25"})
            raise AssertionError("exhausted settlement must be terminal")

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(hermes_state, "SessionDB", FakeDB)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "synthetic",
            "model": "model",
            "api_mode": "chat_completions",
            "base_url": "https://synthetic.invalid/v1",
            "api_key": "private",
        },
    )
    authority = AuthoritativeCostBudget(
        limit_usd="1",
        provider="synthetic",
        model="model",
        strategy="synthetic_authoritative_v1",
    )
    contract = {
        "provider": "synthetic",
        "strategy": "synthetic_authoritative_v1",
        "billing_mode": "shared_credit",
        "covered_outcomes": [
            "success",
            "stream",
            "charged_error",
            "disconnect",
            "timeout",
            "cancellation",
        ],
    }
    request = PluginAgentRunRequest(
        prompt="work",
        provider="synthetic",
        model="model",
        allowed_tools=(),
        workdir=tmp_path,
        max_budget_usd=1,
        _cost_budget_authority=authority.descriptor,
        _cost_budget_contract=contract,
        max_api_attempts=2,
        sealed_provider_attempt_grant=True,
    )
    try:
        result = _run({"plugin_id": "workflow", "request": request.to_wire()})
    finally:
        authority.close()

    assert result["status"] == "failed"
    assert result["audit"]["failure_kind"] == "budget_exhausted"
    assert result["audit"]["provider_attempts"] == 1
    assert result["audit"]["cost_budget"]["settled_cost_usd"] == "1.25"
    assert result["audit"]["cost_budget"]["overage_usd"] == "0.25"
    assert result["audit"]["cost_budget"]["settlement_count"] == 1
