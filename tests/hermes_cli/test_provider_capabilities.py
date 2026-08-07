from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import requests

from hermes_cli.provider_capabilities import (
    CapabilityDisposition,
    WorkflowProviderFeature,
    capability_authority_digest,
    resolve_provider_capability,
)
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    classify_execution_runtime,
)
import providers
from providers import ProviderProfile


EXPECTED_FEATURES = {
    "structured_output",
    "session_resumption",
    "tool_restrictions",
    "hooks",
    "mcp",
    "skills_inline_agents",
    "effort_thinking",
    "fallback_models",
    "web_execution",
    "cost_budgets",
    "provider_native_sandbox",
}


@pytest.fixture
def isolated_provider_registry():
    providers.list_providers()
    registry = dict(providers._REGISTRY)
    aliases = dict(providers._ALIASES)
    registrations = dict(providers._REGISTRATIONS)
    collisions = list(providers._REGISTRATION_COLLISIONS)
    provider_list_cache = providers._PROVIDER_LIST_CACHE
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
    for module_name in tuple(sys.modules):
        if module_name.startswith("_hermes_user_provider_phase5_"):
            sys.modules.pop(module_name, None)


def _managed_runtime(provider: str = "custom") -> ExecutionRuntimeCapabilities:
    return ExecutionRuntimeCapabilities(
        api_mode="chat_completions",
        hermes_managed_tool_loop=True,
        effective_provider=provider,
        model="provider-model",
        base_url_trust_class="custom" if provider == "custom" else "unknown",
    )


def _resolve(
    runtime: ExecutionRuntimeCapabilities,
    feature: WorkflowProviderFeature,
    *,
    option: str | None = None,
    semantics: dict[str, object] | None = None,
):
    return resolve_provider_capability(
        runtime,
        feature=feature,
        option=option,
        requested_semantics=semantics or {},
    )


def test_feature_inventory_and_dispositions_are_exact() -> None:
    assert {feature.value for feature in WorkflowProviderFeature} == EXPECTED_FEATURES
    assert {value.value for value in CapabilityDisposition} == {
        "native",
        "hermes_adapter",
        "degraded_with_explicit_semantics",
        "unsupported",
    }


@pytest.mark.parametrize(
    ("feature", "option", "semantics", "expected"),
    [
        (
            WorkflowProviderFeature.STRUCTURED_OUTPUT,
            "json_schema",
            {"schema_fingerprint": "a" * 64},
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.SESSION_RESUMPTION,
            None,
            {
                "session_available": True,
                "cache_fingerprint_stable": True,
                "hermes_conversation_loop": True,
            },
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.TOOL_RESTRICTIONS,
            None,
            {"hermes_schema_selection": True, "hermes_dispatch": True},
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.HOOKS,
            "pre_tool_use",
            {
                "normalized": True,
                "events_supported": True,
                "lifecycle_available": True,
            },
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.MCP,
            "stdio",
            {
                "sealed_definition": True,
                "bounded_lifecycle": True,
                "dependency_available": True,
                "teardown_guaranteed": True,
            },
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
            "skills",
            {"current_turn_content": True},
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.EFFORT_THINKING,
            "effort",
            {"value": "high"},
            CapabilityDisposition.UNSUPPORTED,
        ),
        (
            WorkflowProviderFeature.FALLBACK_MODELS,
            None,
            {
                "route_resolved": True,
                "fresh_context": True,
                "shared_authorities": True,
            },
            CapabilityDisposition.HERMES_ADAPTER,
        ),
        (
            WorkflowProviderFeature.WEB_EXECUTION,
            "hermes_tool",
            {"service_available": True, "explicit_mapping": True},
            CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS,
        ),
        (
            WorkflowProviderFeature.COST_BUDGETS,
            None,
            {"authoritative_settlement": True},
            CapabilityDisposition.UNSUPPORTED,
        ),
        (
            WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
            "enabled",
            {"enabled": True},
            CapabilityDisposition.UNSUPPORTED,
        ),
    ],
)
def test_every_feature_request_receives_exactly_one_disposition(
    feature, option, semantics, expected
) -> None:
    decision = _resolve(
        _managed_runtime(),
        feature,
        option=option,
        semantics=semantics,
    )
    assert decision.feature is feature
    assert decision.disposition is expected
    assert isinstance(decision.disposition, CapabilityDisposition)


@pytest.mark.parametrize("feature", list(WorkflowProviderFeature))
def test_missing_execution_adapter_blocks_every_capability(feature) -> None:
    decision = _resolve(
        _managed_runtime(),
        feature,
        option="json_schema" if feature is WorkflowProviderFeature.STRUCTURED_OUTPUT else None,
        semantics={"execution_adapter_available": False},
    )

    assert decision.disposition is CapabilityDisposition.UNSUPPORTED
    assert decision.code == f"{feature.value}_execution_adapter_unavailable"


@pytest.mark.parametrize(
    ("feature", "option", "semantics"),
    [
        (
            WorkflowProviderFeature.SESSION_RESUMPTION,
            None,
            {"session_available": False, "cache_fingerprint_stable": True,
             "hermes_conversation_loop": True},
        ),
        (
            WorkflowProviderFeature.TOOL_RESTRICTIONS,
            None,
            {"hermes_schema_selection": True, "hermes_dispatch": False},
        ),
        (
            WorkflowProviderFeature.HOOKS,
            "unsupported_event",
            {"normalized": True, "events_supported": False,
             "lifecycle_available": True},
        ),
        (
            WorkflowProviderFeature.MCP,
            "stdio",
            {"sealed_definition": True, "bounded_lifecycle": True,
             "dependency_available": True, "teardown_guaranteed": False},
        ),
        (
            WorkflowProviderFeature.SKILLS_INLINE_AGENTS,
            "inline_agents",
            {"declared_worker_tool": True, "shared_limits": False},
        ),
        (
            WorkflowProviderFeature.FALLBACK_MODELS,
            None,
            {"route_resolved": True, "fresh_context": False,
             "shared_authorities": True},
        ),
        (
            WorkflowProviderFeature.WEB_EXECUTION,
            "hermes_tool",
            {"service_available": False, "explicit_mapping": True},
        ),
    ],
)
def test_generic_adapters_require_every_runtime_precondition(
    feature, option, semantics
) -> None:
    assert _resolve(
        _managed_runtime(), feature, option=option, semantics=semantics
    ).disposition is CapabilityDisposition.UNSUPPORTED


def test_explicit_structured_output_prohibition_beats_trusted_route_appearance(
    isolated_provider_registry,
) -> None:
    providers.register_provider(
        ProviderProfile(
            name="phase5-explicit-unsupported",
            structured_output_strategy="unsupported",
        )
    )
    runtime = classify_execution_runtime(
        provider="phase5-explicit-unsupported",
        model_config={"provider": "phase5-explicit-unsupported", "default": "m"},
        provider_config={"api_mode": "chat_completions"},
    )
    runtime = replace(runtime, base_url_trust_class="trusted_direct")

    decision = _resolve(
        runtime,
        WorkflowProviderFeature.STRUCTURED_OUTPUT,
        option="json_schema",
        semantics={"schema_fingerprint": "b" * 64},
    )

    assert decision.disposition is CapabilityDisposition.UNSUPPORTED
    assert decision.code == "structured_output_explicitly_unsupported"


def test_malformed_profile_declaration_fails_closed(
    isolated_provider_registry,
) -> None:
    providers.register_provider(
        ProviderProfile(
            name="phase5-malformed-declaration",
            workflow_capabilities={
                "effort_thinking": {
                    "disposition": "probably_native",
                    "effective_semantics": {"request_field": "effort"},
                }
            },
        )
    )
    runtime = classify_execution_runtime(
        provider="phase5-malformed-declaration",
        model_config={"provider": "phase5-malformed-declaration", "default": "m"},
        provider_config={"api_mode": "chat_completions"},
    )

    decision = _resolve(
        runtime,
        WorkflowProviderFeature.EFFORT_THINKING,
        option="effort",
        semantics={"value": "high"},
    )

    assert decision.disposition is CapabilityDisposition.UNSUPPORTED
    assert decision.code == "provider_capability_declaration_invalid"


def _write_user_override(root: Path) -> Path:
    plugin = root / "phase5-openrouter"
    plugin.mkdir()
    (plugin / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        "    name='openrouter',\n"
        "    base_url='https://openrouter.ai/api/v1',\n"
        "    workflow_capabilities={\n"
        "        'cost_budgets': {'disposition': 'native'},\n"
        "        'provider_native_sandbox': {'disposition': 'native'},\n"
        "    },\n"
        "))\n",
        encoding="utf-8",
    )
    (plugin / "plugin.yaml").write_text(
        "name: phase5-openrouter-override\n"
        "kind: model-provider\n"
        "version: 1.0.0\n",
        encoding="utf-8",
    )
    return plugin


def test_user_openrouter_override_cannot_inherit_or_assert_billing_and_sandbox(
    tmp_path: Path,
    isolated_provider_registry,
) -> None:
    providers._import_plugin_dir(_write_user_override(tmp_path), "user")
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    assert runtime.registration_origin_kind == "user_plugin"

    budget = _resolve(
        runtime,
        WorkflowProviderFeature.COST_BUDGETS,
        semantics={
            "authoritative_settlement": True,
            "all_billable_routes_observed": True,
            "single_unsettled": True,
        },
    )
    sandbox = _resolve(
        runtime,
        WorkflowProviderFeature.PROVIDER_NATIVE_SANDBOX,
        option="enabled",
        semantics={"enabled": True},
    )

    assert budget.disposition is CapabilityDisposition.UNSUPPORTED
    assert budget.code == "authoritative_cost_unavailable"
    assert sandbox.disposition is CapabilityDisposition.UNSUPPORTED
    assert sandbox.code == "provider_native_sandbox_unavailable"


def test_incomplete_provenance_allows_only_generic_hermes_adapters(
    isolated_provider_registry,
) -> None:
    namespace = {"__name__": "phase5_dynamic_provider"}
    exec(
        compile(
            "from providers import register_provider\n"
            "from providers.base import ProviderProfile\n"
            "register_provider(ProviderProfile(\n"
            " name='phase5-incomplete',\n"
            " workflow_capabilities={'effort_thinking': {'disposition': 'native'}},\n"
            "))\n",
            "<phase5-dynamic-provider>",
            "exec",
        ),
        namespace,
    )
    runtime = classify_execution_runtime(
        provider="phase5-incomplete",
        model_config={"provider": "phase5-incomplete", "default": "m"},
        provider_config={"api_mode": "chat_completions"},
    )
    assert runtime.registration_provenance_complete is False

    effort = _resolve(
        runtime,
        WorkflowProviderFeature.EFFORT_THINKING,
        option="effort",
        semantics={"value": "high"},
    )
    tools = _resolve(
        runtime,
        WorkflowProviderFeature.TOOL_RESTRICTIONS,
        semantics={"hermes_schema_selection": True, "hermes_dispatch": True},
    )

    assert effort.disposition is CapabilityDisposition.UNSUPPORTED
    assert effort.code == "provider_declaration_provenance_incomplete"
    assert tools.disposition is CapabilityDisposition.HERMES_ADAPTER


def _write_declaring_plugin(root: Path, helper_body: str) -> Path:
    plugin = root / "phase5-decision-identity"
    plugin.mkdir(exist_ok=True)
    (plugin / "helper.py").write_text(helper_body, encoding="utf-8")
    (plugin / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "from .helper import marker\n"
        "class DeclaringProfile(ProviderProfile):\n"
        "    def declare_workflow_capability(self, feature, *, model, option):\n"
        "        marker()\n"
        "        if feature == 'effort_thinking':\n"
        "            return {\n"
        "                'disposition': 'degraded_with_explicit_semantics',\n"
        "                'options': ['effort'],\n"
        "                'effective_semantics': {'request_field': 'reasoning.effort'},\n"
        "                'adapter_version': 1,\n"
        "                'code': 'phase5_effort_translation',\n"
        "                'rationale': 'Pinned provider effort translation',\n"
        "            }\n"
        "        return None\n"
        "register_provider(DeclaringProfile(name='phase5-decision-identity'))\n",
        encoding="utf-8",
    )
    (plugin / "plugin.yaml").write_text(
        "name: phase5-decision-identity\n"
        "kind: model-provider\n"
        "version: 4.0.0\n",
        encoding="utf-8",
    )
    return plugin


def _evict_decision_plugin() -> None:
    for module_name in tuple(sys.modules):
        if module_name.startswith("_hermes_user_provider_phase5_decision_identity"):
            sys.modules.pop(module_name, None)


def test_same_version_helper_mutation_changes_decision_authority_identity(
    tmp_path: Path,
    isolated_provider_registry,
) -> None:
    plugin = _write_declaring_plugin(tmp_path, "def marker():\n    return 'first'\n")
    providers._import_plugin_dir(plugin, "user")
    first_runtime = classify_execution_runtime(
        provider="phase5-decision-identity",
        model_config={"provider": "phase5-decision-identity", "default": "m"},
        provider_config={"api_mode": "chat_completions"},
    )
    first = _resolve(
        first_runtime,
        WorkflowProviderFeature.EFFORT_THINKING,
        option="effort",
        semantics={"value": "high"},
    )

    (plugin / "helper.py").write_text(
        "def marker():\n    return 'second-with-different-source'\n",
        encoding="utf-8",
    )
    _evict_decision_plugin()
    providers._import_plugin_dir(plugin, "user")
    second_runtime = classify_execution_runtime(
        provider="phase5-decision-identity",
        model_config={"provider": "phase5-decision-identity", "default": "m"},
        provider_config={"api_mode": "chat_completions"},
    )
    second = _resolve(
        second_runtime,
        WorkflowProviderFeature.EFFORT_THINKING,
        option="effort",
        semantics={"value": "high"},
    )

    assert first.registration_provenance_digest != second.registration_provenance_digest
    assert capability_authority_digest([first]) != capability_authority_digest([second])


def test_openrouter_declaration_is_model_aware_and_budget_remains_unsupported() -> None:
    mandatory_runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "anthropic/claude-sonnet-4.6",
        },
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    translated = _resolve(
        mandatory_runtime,
        WorkflowProviderFeature.EFFORT_THINKING,
        option="effort",
        semantics={"value": "high"},
    )
    budget = _resolve(
        mandatory_runtime,
        WorkflowProviderFeature.COST_BUDGETS,
        semantics={"authoritative_settlement": True},
    )

    assert translated.disposition is CapabilityDisposition.DEGRADED_WITH_EXPLICIT_SEMANTICS
    assert translated.effective_semantics["request_field"] == "verbosity"
    assert translated.effective_semantics["thinking_mode"] == "adaptive"
    assert budget.disposition is CapabilityDisposition.UNSUPPORTED
    assert budget.code == "authoritative_cost_unavailable"


def test_decisions_are_frozen_bounded_json_safe_and_secret_free() -> None:
    decision = _resolve(
        _managed_runtime(),
        WorkflowProviderFeature.TOOL_RESTRICTIONS,
        semantics={
            "hermes_schema_selection": True,
            "hermes_dispatch": True,
            "api_key": "do-not-serialize",
        },
    )

    assert decision.disposition is CapabilityDisposition.UNSUPPORTED
    assert decision.code == "provider_capability_request_invalid"
    payload = decision.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert len(encoded.encode("utf-8")) <= 4096
    assert "do-not-serialize" not in encoded
    with pytest.raises((FrozenInstanceError, AttributeError)):
        decision.code = "mutated"
    with pytest.raises(TypeError):
        decision.requested_semantics["new"] = True


def test_capability_resolution_is_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("capability resolution performed runtime I/O")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr("hermes_cli.auth.resolve_api_key_provider_credentials", forbidden)

    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        provider_config={"api_mode": "chat_completions"},
    )
    decision = _resolve(
        runtime,
        WorkflowProviderFeature.TOOL_RESTRICTIONS,
        semantics={"hermes_schema_selection": True, "hermes_dispatch": True},
    )

    assert decision.disposition is CapabilityDisposition.HERMES_ADAPTER
