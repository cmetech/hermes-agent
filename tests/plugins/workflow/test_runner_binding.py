from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from agent.structured_output import StructuredOutputStrategy
from hermes_cli.runtime_provider import (
    ExecutionRuntimeCapabilities,
    classify_execution_runtime,
    snapshot_configured_execution_routes,
)
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.api_admission import (
    ApiAdmissionAuthority,
    ApiAdmissionError,
    start_api_run,
)
from plugins.workflow.catalog_api import (
    build_workflow_catalog,
    build_workflow_detail,
)
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.runner_binding import (
    ExecutionCapabilityContext,
    RunnerCapabilities,
    WorkflowRunnerBinding,
    assess_package_execution,
    execution_capability_context,
)
import plugins.workflow.api_admission as api_admission_module
import plugins.workflow.showcase as showcase_module
import plugins.workflow.runner_binding as runner_binding_module
import plugins.workflow.scheduled_revalidation as scheduled_revalidation_module
import plugins.workflow.coordinator as coordinator_module
from plugins.workflow.dashboard.plugin_api import StartRunRequest
from plugins.workflow.models import ExecutionFence
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.trust import WorkflowResourceReadBudget
from plugins.workflow.store import RunStore
from plugins.workflow.schema import load_workflow
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


@pytest.mark.parametrize(
    (
        "surface",
        "entitlement",
        "runner_starts_request_mcp",
        "hermes_managed_tool_loop",
        "expected",
    ),
    [
        pytest.param(
            surface,
            AIEntitlementResolution(entitlement),
            runner_capable,
            runtime_capable,
            (
                surface == "background"
                and entitlement == "real"
                and runner_capable
                and runtime_capable
            ),
            id=f"{surface}-{entitlement}-{runner_capable}-{runtime_capable}",
        )
        for surface in ("background", "cli")
        for entitlement in ("real", "deterministic")
        for runner_capable in (False, True)
        for runtime_capable in (False, True)
    ],
)
def test_execution_capability_context_truth_table(
    surface: str,
    entitlement: AIEntitlementResolution,
    runner_starts_request_mcp: bool,
    hermes_managed_tool_loop: bool,
    expected: bool,
) -> None:
    runner_capabilities = RunnerCapabilities(
        starts_request_mcp=runner_starts_request_mcp
    )
    runtime_capabilities = ExecutionRuntimeCapabilities(
        api_mode=("chat_completions" if hermes_managed_tool_loop else "codex_app_server"),
        hermes_managed_tool_loop=hermes_managed_tool_loop,
    )

    context = execution_capability_context(
        surface=surface,
        entitlement=entitlement,
        runner_capabilities=runner_capabilities,
        runtime_capabilities=runtime_capabilities,
    )

    assert isinstance(context, ExecutionCapabilityContext)
    assert context.surface == surface
    assert context.entitlement == entitlement
    assert context.runner_capabilities == runner_capabilities
    assert context.runtime_capabilities == runtime_capabilities
    assert context.mcp_available is expected


def test_capability_records_and_binding_are_frozen_and_slotted() -> None:
    runner = object()
    deterministic_runner = object()
    binding = WorkflowRunnerBinding(
        real_runner=runner,
        deterministic_runner=deterministic_runner,
        real_capabilities=RunnerCapabilities(starts_request_mcp=True),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
        ),
    )

    assert binding.runner_for(AIEntitlementResolution("real")) is runner
    assert (
        binding.runner_for(AIEntitlementResolution("deterministic"))
        is deterministic_runner
    )
    assert binding.capabilities_for(AIEntitlementResolution("real")) == (
        RunnerCapabilities(starts_request_mcp=True)
    )
    assert binding.capabilities_for(AIEntitlementResolution("deterministic")) == (
        RunnerCapabilities(starts_request_mcp=False)
    )
    assert not hasattr(binding, "__dict__")
    with pytest.raises(FrozenInstanceError):
        binding.real_runner = object()  # type: ignore[misc]


def test_execution_context_seals_structured_output_decisions_into_identity(
    tmp_path: Path,
    workflow_writer,
) -> None:
    path = workflow_writer(
        tmp_path,
        name="sealed-structured-output",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a report",
                "output_format": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            }
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    direct_runtime = classify_execution_runtime(
        provider="openai-api",
        model_config={
            "provider": "openai-api",
            "default": "gpt-5.4",
            "base_url": "https://api.openai.com/v1",
        },
        provider_config={
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
        },
    )
    prompt_runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    direct_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=direct_runtime,
    )
    prompt_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=prompt_runtime,
    )

    direct_decision = next(
        iter(direct_context.structured_output_decisions(package).values())
    )

    assert direct_decision.strategy is StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    assert direct_decision.effective_provider == "openai"
    assert direct_decision.model == "gpt-5.4"
    assert direct_decision.schema_fingerprint == (
        package.language.structured_outputs["producer"].schema_fingerprint
    )
    assert direct_context.identity_digest != prompt_context.identity_digest


def _structured_package(home: Path, workflow_writer, *, name: str):
    path = workflow_writer(
        home / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a report",
                "output_format": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            }
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(path)


def _structured_route_package(
    home: Path,
    workflow_writer,
    *,
    name: str,
    nodes: list[dict[str, object]],
):
    path = workflow_writer(
        home / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=nodes,
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(path)


def _runtime_binding(runtime, *, runtime_provider=None, configured_routes=None):
    return WorkflowRunnerBinding(
        real_runner=object(),
        deterministic_runner=object(),
        real_capabilities=RunnerCapabilities(starts_request_mcp=True),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=runtime,
        configured_provider_routes=configured_routes or {},
        runtime_capabilities_provider=runtime_provider,
    )


def _direct_openai_runtime():
    return classify_execution_runtime(
        provider="openai-api",
        model_config={
            "provider": "openai-api",
            "default": "gpt-5.4",
            "base_url": "https://api.openai.com/v1",
        },
        provider_config={
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
        },
    )


def test_package_identity_seals_complete_actual_structured_output_decisions(
    tmp_path: Path,
    workflow_writer,
) -> None:
    first = _structured_package(tmp_path / "first", workflow_writer, name="first")
    second_path = workflow_writer(
        tmp_path / "second",
        name="second",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a report",
                "output_format": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            }
        ],
    )
    second_path.with_name(f"{second_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    second = load_workflow(second_path)
    baseline_runtime = _direct_openai_runtime()

    def context(runtime):
        return execution_capability_context(
            surface="background",
            entitlement=AIEntitlementResolution("real"),
            runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
            runtime_capabilities=runtime,
        )

    baseline = context(baseline_runtime)
    changed_declaration = context(
        replace(
            baseline_runtime,
            structured_output_declaration_source="provider_profile",
        )
    )
    changed_provider = context(
        replace(baseline_runtime, effective_provider="different-provider")
    )
    changed_model = context(replace(baseline_runtime, model="different-model"))

    identity = baseline.identity_digest_for(first)
    assert identity == baseline.identity_digest_for(first)
    assert identity != baseline.identity_digest_for(second)
    assert identity != changed_declaration.identity_digest_for(first)
    assert identity != changed_provider.identity_digest_for(first)
    assert identity != changed_model.identity_digest_for(first)
    assert baseline.structured_output_decisions(first)["producer"].rationale == (
        baseline.structured_output_identity_material(first)[0]["rationale"]
    )


def test_per_node_provider_overrides_seal_distinct_truthful_decisions(
    tmp_path: Path,
    workflow_writer,
) -> None:
    path = workflow_writer(
        tmp_path,
        name="per-node-provider-decisions",
        nodes=[
            {
                "id": "anthropic-node",
                "prompt": "Return Anthropic JSON",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_format": {"type": "object"},
            },
            {
                "id": "openrouter-node",
                "prompt": "Return aggregator JSON",
                "provider": "openrouter",
                "model": "openai/gpt-5.4",
                "output_format": {"type": "object"},
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=_direct_openai_runtime(),
    )

    decisions = context.structured_output_decisions(package)
    metadata = {
        json.loads(value)["effective_provider"]: json.loads(value)
        for value in context.structured_output_run_metadata(package).values()
    }

    assert decisions["anthropic-node"].effective_provider == "anthropic"
    assert decisions["anthropic-node"].model == "claude-sonnet-4-6"
    assert decisions["anthropic-node"].strategy is (
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    )
    assert decisions["openrouter-node"].effective_provider == "openrouter"
    assert decisions["openrouter-node"].model == "openai/gpt-5.4"
    assert decisions["openrouter-node"].strategy is (
        StructuredOutputStrategy.PROMPT_JSON_SCHEMA
    )
    assert set(metadata) == {"anthropic", "openrouter"}
    assert metadata["anthropic"]["strategy"] == "native_json_schema"
    assert metadata["openrouter"]["strategy"] == "prompt_json_schema"


def test_unknown_node_provider_override_is_unsupported_and_blocks(
    tmp_path: Path,
    workflow_writer,
) -> None:
    path = workflow_writer(
        tmp_path,
        name="unsupported-node-provider",
        nodes=[
            {
                "id": "unsupported-node",
                "prompt": "Return JSON",
                "provider": "unconfigured-provider",
                "model": "unknown-model",
                "output_format": {"type": "object"},
            }
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=_direct_openai_runtime(),
    )

    compatibility, _risk = runner_binding_module.assess_package_execution(
        package, context
    )

    assert context.structured_output_decisions(package)[
        "unsupported-node"
    ].strategy is StructuredOutputStrategy.UNSUPPORTED
    assert compatibility.runnable is False
    assert any(
        finding.code == "structured_output_strategy_unsupported"
        for finding in compatibility.blocking_findings
    )


def test_catalog_exposes_bounded_schema_free_structured_output_summary(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    _structured_package(home, workflow_writer, name="catalog-structured-output")

    catalog, _truncated = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=_runtime_binding(_direct_openai_runtime()),
    )

    row = next(item for item in catalog if item["name"] == "catalog-structured-output")
    summary = row["structured_output_capability"]
    assert summary == {
        "mixed": False,
        "summary_count": 1,
        "summaries_truncated": False,
        "summaries": [
            {
                "strategy": "native_json_schema",
                "provider": "openai",
                "api_mode": "codex_responses",
                "adapter_version": 1,
            }
        ],
    }
    serialized = json.dumps(summary, sort_keys=True)
    assert "fingerprint" not in serialized
    assert "rationale" not in serialized


def test_catalog_exposes_deterministic_bounded_heterogeneous_summaries(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    _structured_route_package(
        home,
        workflow_writer,
        name="catalog-mixed-structured-output",
        nodes=[
            {
                "id": "direct",
                "prompt": "Return direct JSON",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_format": {"type": "object"},
            },
            {
                "id": "community",
                "prompt": "Return community JSON",
                "provider": "community",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            },
        ],
    )
    routes = snapshot_configured_execution_routes(
        {
            "providers": {
                "community": {
                    "api": "https://community.example.test/v1",
                    "transport": "chat_completions",
                }
            }
        }
    )
    binding = _runtime_binding(
        _direct_openai_runtime(),
        configured_routes=routes,
    )

    first, _first_truncated = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=binding,
    )
    second, _second_truncated = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=binding,
    )
    first_row = next(
        item for item in first if item["name"] == "catalog-mixed-structured-output"
    )
    second_row = next(
        item for item in second if item["name"] == "catalog-mixed-structured-output"
    )
    summary = first_row["structured_output_capability"]

    assert summary == {
        "mixed": True,
        "summary_count": 2,
        "summaries_truncated": False,
        "summaries": [
            {
                "strategy": "native_json_schema",
                "provider": "anthropic",
                "api_mode": "anthropic_messages",
                "adapter_version": 1,
            },
            {
                "strategy": "prompt_json_schema",
                "provider": "custom",
                "api_mode": "chat_completions",
                "adapter_version": 1,
            },
        ],
    }
    assert second_row["structured_output_capability"] == summary
    assert len(summary["summaries"]) <= 16
    serialized = json.dumps(summary, sort_keys=True)
    assert len(serialized) <= 4096
    assert all(
        forbidden not in serialized.lower()
        for forbidden in (
            "fingerprint",
            "rationale",
            "route",
            "credential",
            "token",
            "query",
        )
    )


def test_catalog_bounds_unique_structured_output_summaries(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    _structured_route_package(
        home,
        workflow_writer,
        name="catalog-bounded-structured-output",
        nodes=[
            {
                "id": f"node-{index:02d}",
                "prompt": "Return JSON",
                "provider": f"unconfigured-{index:02d}",
                "model": "unknown-model",
                "output_format": {"type": "object"},
            }
            for index in range(20)
        ],
    )

    catalog, _truncated = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=_runtime_binding(_direct_openai_runtime()),
    )
    row = next(
        item for item in catalog if item["name"] == "catalog-bounded-structured-output"
    )
    summary = row["structured_output_capability"]

    assert summary["mixed"] is True
    assert summary["summary_count"] == 20
    assert summary["summaries_truncated"] is True
    assert len(summary["summaries"]) == 16
    assert [item["provider"] for item in summary["summaries"]] == [
        f"unconfigured-{index:02d}" for index in range(16)
    ]
    assert len(json.dumps(summary, sort_keys=True)) <= 4096


def test_scheduled_admission_seals_complete_decision_and_detects_provider_drift(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    package = _structured_package(home, workflow_writer, name="sealed-provider-drift")
    current = {"runtime": _direct_openai_runtime()}
    binding = _runtime_binding(
        current["runtime"], runtime_provider=lambda: current["runtime"]
    )
    admitted_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    compatibility, risk = runner_binding_module.assess_package_execution(
        package, admitted_context
    )
    assert compatibility.runnable is True
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="runner-binding-test",
        risk_digest=risk.risk_digest,
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("", [], []),
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=package.definition.name,
        values={},
        idempotency_key="sealed-provider-drift",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=binding,
        schedule_at="2099-01-02T03:04:05Z",
        schedule_now_utc=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    run = store.load_run(str(admitted["run_id"]))
    decision_values = [
        value
        for key, value in run["run_metadata"].items()
        if key.startswith("structured_output_decision.")
    ]

    assert len(decision_values) == 1
    sealed = json.loads(decision_values[0])
    assert set(sealed) == {
        "strategy",
        "effective_provider",
        "model",
        "api_mode",
        "declaration_source",
        "adapter_version",
        "schema_fingerprint",
        "rationale",
    }
    assert sealed["strategy"] == "native_json_schema"
    assert run["run_metadata"]["execution_identity"] == (
        admitted_context.identity_digest_for(package)
    )
    assert run["run_metadata"]["execution_runtime_identity"] == (
        admitted_context.identity_digest
    )

    current["runtime"] = classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        provider_config={
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    fire_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    assert fire_context.identity_digest != admitted_context.identity_digest
    assert json.loads(decision_values[0]) == sealed
    with pytest.raises(
        scheduled_revalidation_module.ScheduledRunRevalidationError,
        match="execution capability changed",
    ):
        scheduled_revalidation_module.revalidate_scheduled_run(
            run,
            fire_context,
            hermes_home=home,
            run_directory=store.run_directory(str(admitted["run_id"])),
        )


def test_unsupported_structured_output_blocks_before_provider_request(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    package = _structured_package(home, workflow_writer, name="unsupported-output")
    unsupported_runtime = ExecutionRuntimeCapabilities(
        api_mode="chat_completions",
        hermes_managed_tool_loop=True,
        effective_provider="locked-provider",
        model="locked-model",
        declared_structured_output_strategy="unsupported",
        structured_output_declaration_source="provider_profile",
    )
    binding = _runtime_binding(unsupported_runtime)
    context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    compatibility, risk = runner_binding_module.assess_package_execution(
        package, context
    )
    assert compatibility.runnable is False
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="runner-binding-test",
        risk_digest=risk.risk_digest,
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider request must not occur")
        ),
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    with pytest.raises(ApiAdmissionError) as exc_info:
        start_api_run(
            store,
            hermes_home=home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name=package.definition.name,
            values={},
            idempotency_key="unsupported-output",
            concurrency_policy="queue",
            authority=_authority(),
            catalog_source="profile",
            runner_binding=binding,
        )

    assert exc_info.value.code == "workflow_compatibility_blocked"
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


def test_overlong_structured_decision_metadata_blocks_without_residue(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    package = _structured_route_package(
        home,
        workflow_writer,
        name="overlong-structured-metadata",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "model": "m" * 400,
                "output_format": {"type": "object"},
            }
        ],
    )
    binding = _runtime_binding(_direct_openai_runtime())
    context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    compatibility, risk = runner_binding_module.assess_package_execution(
        package, context
    )
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="runner-binding-test",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    assert compatibility.runnable is False
    assert any(
        finding.code == "structured_output_metadata_too_large"
        for finding in compatibility.blocking_findings
    )
    with pytest.raises(ApiAdmissionError) as exc_info:
        start_api_run(
            store,
            hermes_home=home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name=package.definition.name,
            values={},
            idempotency_key="overlong-structured-metadata",
            concurrency_policy="queue",
            authority=_authority(),
            catalog_source="profile",
            runner_binding=binding,
        )

    assert exc_info.value.code == "workflow_compatibility_blocked"
    assert store.list_runs() == ()
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


def test_caller_data_cannot_override_execution_capabilities() -> None:
    caller_data = {
        "surface": "background",
        "entitlement": "real",
        "runner_capabilities": {"starts_request_mcp": True},
        "runtime_capabilities": {"hermes_managed_tool_loop": True},
        "mcp_available": True,
    }

    with pytest.raises(TypeError):
        execution_capability_context(
            surface="background",
            entitlement=AIEntitlementResolution("deterministic"),
            runner_capabilities=RunnerCapabilities(starts_request_mcp=False),
            runtime_capabilities=ExecutionRuntimeCapabilities(
                api_mode="codex_app_server",
                hermes_managed_tool_loop=False,
            ),
            caller_data=caller_data,  # type: ignore[call-arg]
        )


def test_verified_showcase_cache_contains_authenticated_raw_material_only() -> None:
    showcase_module._clear_verified_showcase_cache_for_tests()
    verified = showcase_module.load_verified_showcase_packages(
        read_budget=WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )
    )

    package = verified["ai-extensions"]
    assert len(package.package_digest) == 64
    assert package.compilation.package is package.package
    assert package.compilation.dependency_manifest.root.workflow_name == (
        package.package.definition.name
    )
    assert package.compilation.covered_relative_paths
    assert all(
        isinstance(package.compilation.sealed_files[path], bytes)
        for path in package.compilation.covered_relative_paths
    )
    assert not hasattr(package, "compatibility")
    assert not hasattr(package, "risk")

    cache_entry = next(iter(showcase_module._VERIFIED_SHOWCASE_CACHE.values()))
    assert cache_entry.root == Path(showcase_module.__file__).with_name("showcases")
    assert cache_entry.resource_bytes
    assert all(isinstance(value, bytes) for value in cache_entry.resource_bytes.values())
    forbidden_tokens = {
        "CompatibilityReport",
        "WorkflowRiskSummary",
        "risk_digest",
        "blocking_findings",
        "runtime_capabilities",
    }
    assert not forbidden_tokens.intersection(repr(cache_entry))


def test_production_binding_factory_declares_real_and_deterministic_runners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(runner_binding_module, "production_workflow_runner_binding")
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: {
            "model": {
                "provider": "openai-codex",
                "default": "gpt-5.3-codex",
                "openai_runtime": "codex_app_server",
            }
        },
    )

    binding = runner_binding_module.production_workflow_runner_binding()

    assert binding.real_capabilities.starts_request_mcp is True
    assert binding.deterministic_capabilities.starts_request_mcp is False
    assert binding.runtime_capabilities == ExecutionRuntimeCapabilities(
        api_mode="codex_app_server",
        hermes_managed_tool_loop=False,
        effective_provider="openai-codex",
        model="gpt-5.3-codex",
    )
    assert binding.real_runner.__class__.__name__ == "PluginAgentRunner"
    assert binding.deterministic_runner.__class__.__name__ == (
        "DeterministicAgentRunner"
    )


def test_production_binding_refreshes_runtime_capabilities_per_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_source = {
        "current": {
            "model": {
                "provider": "openrouter",
                "default": "openai/gpt-5.3",
            }
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )

    binding = runner_binding_module.production_workflow_runner_binding()
    entitlement = AIEntitlementResolution("real")
    initial_capabilities = ExecutionRuntimeCapabilities(
        api_mode="chat_completions",
        hermes_managed_tool_loop=True,
        effective_provider="openrouter",
        model="openai/gpt-5.3",
        base_url_trust_class="aggregator",
    )
    changed_capabilities = ExecutionRuntimeCapabilities(
        api_mode="anthropic_messages",
        hermes_managed_tool_loop=True,
        effective_provider="anthropic",
        model="claude-sonnet-4-5",
        base_url_trust_class="trusted_direct",
        declared_structured_output_strategy="native_json_schema",
        structured_output_declaration_source="provider_profile",
    )

    assert binding.runtime_capabilities == initial_capabilities
    assert (
        binding.execution_context(
            surface="background",
            entitlement=entitlement,
        ).runtime_capabilities
        == initial_capabilities
    )

    config_source["current"] = {
        "model": {
            "provider": "anthropic",
            "default": "claude-sonnet-4-5",
        }
    }

    assert binding.runtime_capabilities == initial_capabilities
    assert (
        binding.execution_context(
            surface="background",
            entitlement=entitlement,
        ).runtime_capabilities
        == changed_capabilities
    )

    injected_binding = _binding(runtime_managed=True)
    config_source["current"] = {
        "model": {
            "provider": "openai-codex",
            "default": "gpt-5.3-codex",
            "openai_runtime": "codex_app_server",
        }
    }
    assert (
        injected_binding.execution_context(
            surface="background",
            entitlement=entitlement,
        ).runtime_capabilities
        == ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
        )
    )


def test_anthropic_override_uses_frozen_configured_proxy_route(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_source = {
        "current": {
            "model": {
                "provider": "anthropic",
                "default": "claude-sonnet-4-6",
                "base_url": "https://proxy.example.test/anthropic",
                "api_mode": "anthropic_messages",
            }
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="anthropic-proxy-route",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_format": {"type": "object"},
            }
        ],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    decision = context.structured_output_decisions(package)["producer"]
    identity = context.identity_digest_for(package)

    assert decision.strategy is StructuredOutputStrategy.PROMPT_JSON_SCHEMA
    assert decision.effective_provider == "anthropic"
    assert decision.api_mode == "anthropic_messages"

    config_source["current"] = {
        "model": {
            "provider": "anthropic",
            "default": "claude-sonnet-4-6",
        }
    }
    assert context.structured_output_decisions(package)["producer"] == decision
    assert context.identity_digest_for(package) == identity


def test_configured_custom_override_uses_actual_managed_route(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "community": {
                    "api": "https://community.example.test/v1",
                    "transport": "chat_completions",
                    "default_model": "community/model-v1",
                }
            },
        },
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="configured-community-route",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "community",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            }
        ],
    )
    context = runner_binding_module.production_workflow_runner_binding().execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    decision = context.structured_output_decisions(package)["producer"]
    compatibility, _risk = runner_binding_module.assess_package_execution(
        package, context
    )
    metadata = json.loads(
        next(iter(context.structured_output_run_metadata(package).values()))
    )

    assert decision.strategy is StructuredOutputStrategy.PROMPT_JSON_SCHEMA
    assert decision.effective_provider == "custom"
    assert decision.api_mode == "chat_completions"
    assert decision.declaration_source == "managed_loop_default"
    assert compatibility.runnable is True
    assert metadata["strategy"] == "prompt_json_schema"
    assert metadata["effective_provider"] == "custom"
    assert metadata["api_mode"] == "chat_completions"


def test_route_secret_changes_do_not_change_identity_or_leak_metadata(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_source = {
        "current": {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "private-route": {
                    "api": (
                        "https://alice:first-password@community.example.test/v1"
                        "?token=first-token&region=us-east-1"
                        "&api-version=2026-07-01#first-fragment"
                    ),
                    "transport": "chat_completions",
                }
            },
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="secret-free-route-identity",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "private-route",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            }
        ],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    first_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    first_identity = first_context.identity_digest_for(package)
    first_projection = repr(first_context.configured_provider_routes) + json.dumps(
        first_context.structured_output_run_metadata(package), sort_keys=True
    )

    config_source["current"]["providers"]["private-route"]["api"] = (
        "https://bob:second-password@community.example.test/v1"
        "?api-version=2026-07-01&token=second-token"
        "&region=us-east-1#second-fragment"
    )
    second_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    second_projection = repr(second_context.configured_provider_routes) + json.dumps(
        second_context.structured_output_run_metadata(package), sort_keys=True
    )

    assert second_context.identity_digest_for(package) == first_identity
    for secret in (
        "alice",
        "first-password",
        "first-token",
        "first-fragment",
        "bob",
        "second-password",
        "second-token",
        "second-fragment",
    ):
        assert secret not in first_projection
        assert secret not in second_projection


@pytest.mark.parametrize(
    "changed_query",
    (
        "api-version=2026-08-01&region=us-east-1&token=second-token",
        "api-version=2026-07-01&region=eu-west-1&token=second-token",
    ),
)
def test_route_authority_changes_change_package_identity(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
    changed_query: str,
) -> None:
    config_source = {
        "current": {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "private-route": {
                    "api": (
                        "https://community.example.test/v1"
                        "?token=first-token&region=us-east-1"
                        "&api-version=2026-07-01"
                    ),
                    "transport": "chat_completions",
                }
            },
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="route-authority-identity",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "private-route",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            }
        ],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    first_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    config_source["current"]["providers"]["private-route"]["api"] = (
        f"https://community.example.test/v1?{changed_query}"
    )
    second_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    assert second_context.identity_digest_for(package) != (
        first_context.identity_digest_for(package)
    )


def test_unclassified_route_query_blocks_without_leaking_or_untracked_execution(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_source = {
        "current": {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "private-route": {
                    "api": (
                        "https://community.example.test/v1"
                        "?feature-mode=first-opaque-value"
                    ),
                    "transport": "chat_completions",
                }
            },
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="unclassified-route-query",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "private-route",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            }
        ],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    first_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    first_compatibility, _first_risk = assess_package_execution(
        package,
        first_context,
    )
    first_projection = repr(first_context.configured_provider_routes)

    config_source["current"]["providers"]["private-route"]["api"] = (
        "https://community.example.test/v1?feature-mode=second-opaque-value"
    )
    second_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    second_compatibility, _second_risk = assess_package_execution(
        package,
        second_context,
    )
    second_projection = repr(second_context.configured_provider_routes)

    assert first_compatibility.runnable is False
    assert second_compatibility.runnable is False
    assert any(
        finding.code == "structured_output_strategy_unsupported"
        for finding in first_compatibility.findings
    )
    assert any(
        finding.code == "structured_output_strategy_unsupported"
        for finding in second_compatibility.findings
    )
    assert first_context.identity_digest_for(package) == (
        second_context.identity_digest_for(package)
    )
    assert "first-opaque-value" not in first_projection
    assert "second-opaque-value" not in second_projection


def test_configured_custom_anthropic_alias_preserves_custom_precedence(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "claude": {
                    "api": "https://community.example.test/anthropic",
                    "transport": "anthropic_messages",
                }
            },
        },
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="custom-claude-shadow",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "claude",
                "model": "community/claude",
                "output_format": {"type": "object"},
            }
        ],
    )
    context = runner_binding_module.production_workflow_runner_binding().execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    decision = context.structured_output_decisions(package)["producer"]

    assert decision.strategy is StructuredOutputStrategy.PROMPT_JSON_SCHEMA
    assert decision.effective_provider == "custom"
    assert decision.api_mode == "anthropic_messages"


def test_official_direct_anthropic_override_remains_native(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: {
            "model": {
                "provider": "anthropic",
                "default": "claude-sonnet-4-6",
                "base_url": "https://openrouter.ai/api/v1",
            },
            "providers": {
                "anthropic": {
                    "api": "https://proxy-that-cannot-shadow.example.test/v1",
                    "transport": "anthropic_messages",
                }
            },
        },
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="official-anthropic-route",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return JSON",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_format": {"type": "object"},
            }
        ],
    )
    context = runner_binding_module.production_workflow_runner_binding().execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    decision = context.structured_output_decisions(package)["producer"]

    assert decision.strategy is StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    assert decision.effective_provider == "anthropic"
    assert decision.api_mode == "anthropic_messages"


def test_two_configured_routes_seal_distinct_decisions_and_route_identity(
    tmp_path: Path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_source = {
        "current": {
            "model": {"provider": "openrouter", "default": "openai/gpt-5.4"},
            "providers": {
                "community": {
                    "api": "https://community-one.example.test/v1",
                    "transport": "chat_completions",
                    "default_model": "community/model-v1",
                }
            },
        }
    }
    monkeypatch.setattr(
        runner_binding_module,
        "read_raw_config",
        lambda: config_source["current"],
    )
    package = _structured_route_package(
        tmp_path,
        workflow_writer,
        name="two-configured-routes",
        nodes=[
            {
                "id": "direct",
                "prompt": "Return direct JSON",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_format": {"type": "object"},
            },
            {
                "id": "community",
                "prompt": "Return community JSON",
                "provider": "community",
                "model": "community/model-v1",
                "output_format": {"type": "object"},
            },
        ],
    )
    binding = runner_binding_module.production_workflow_runner_binding()
    first_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )
    first_decisions = first_context.structured_output_decisions(package)
    first_identity = first_context.identity_digest_for(package)

    assert first_decisions["direct"].strategy is (
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA
    )
    assert first_decisions["community"].strategy is (
        StructuredOutputStrategy.PROMPT_JSON_SCHEMA
    )
    assert first_decisions["direct"].effective_provider == "anthropic"
    assert first_decisions["community"].effective_provider == "custom"

    config_source["current"]["providers"]["community"]["api"] = (
        "https://community-two.example.test/v1"
    )
    second_context = binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    assert first_context.identity_digest_for(package) == first_identity
    assert second_context.structured_output_decisions(package) == first_decisions
    assert second_context.identity_digest_for(package) != first_identity


def _binding(*, runtime_managed: bool, runner_capable: bool = True):
    return WorkflowRunnerBinding(
        real_runner=object(),
        deterministic_runner=object(),
        real_capabilities=RunnerCapabilities(
            starts_request_mcp=runner_capable
        ),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode=("chat_completions" if runtime_managed else "codex_app_server"),
            hermes_managed_tool_loop=runtime_managed,
        ),
    )


def test_execution_environment_finding_carries_effective_language_profile(
    tmp_path: Path,
    workflow_writer,
) -> None:
    workflow = workflow_writer(tmp_path)
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n"
        "execution_environment: isolated_backend_required\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    context = _binding(runtime_managed=True).execution_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
    )

    compatibility, _risk = runner_binding_module.assess_package_execution(
        package,
        context,
    )

    finding = next(
        item
        for item in compatibility.findings
        if item.code == "execution_environment_unavailable"
    )
    assert finding.effective_profile is package.language.effective_profile


def _healthy_coordinator(store: RunStore) -> None:
    lease = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="runner-binding-test",
            host_kind="web",
            host_instance_id="runner-binding-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert lease.is_leader


def _authority() -> ApiAdmissionAuthority:
    return ApiAdmissionAuthority(
        principal="runner-binding-test",
        namespace="runner-binding-test",
        operator_scope=None,
        source_instance="desktop:test",
        assurance="local_admin_claim",
        trigger_source="desktop",
    )


def test_same_raw_cache_recomputes_capable_and_incapable_showcase_projections(
    tmp_path: Path,
) -> None:
    showcase_module._clear_verified_showcase_cache_for_tests()
    home = tmp_path / "home"
    capable = _binding(runtime_managed=True)
    incapable = _binding(runtime_managed=False)

    capable_catalog, _ = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=capable,
    )
    cache_entry = next(iter(showcase_module._VERIFIED_SHOWCASE_CACHE.values()))
    capable_detail = build_workflow_detail(
        "ai-extensions",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="showcase",
        runner_binding=capable,
    )
    incapable_catalog, _ = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=incapable,
    )
    incapable_detail = build_workflow_detail(
        "ai-extensions",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="showcase",
        runner_binding=incapable,
    )

    def ai_row(items):
        return next(item for item in items if item["name"] == "ai-extensions")

    assert next(iter(showcase_module._VERIFIED_SHOWCASE_CACHE.values())) is cache_entry
    assert ai_row(capable_catalog)["compatibility"]["runnable"] is True
    assert ai_row(capable_catalog)["run_support"] == {
        "supported": True,
        "reason": "supported",
    }
    assert capable_detail["compatibility"]["runnable"] is True
    assert ai_row(incapable_catalog)["compatibility"]["runnable"] is False
    assert incapable_detail["compatibility"]["runnable"] is False
    assert capable_detail["risk_summary"]["risk_digest"] != (
        incapable_detail["risk_summary"]["risk_digest"]
    )


def test_showcase_admission_reuses_signature_checked_verified_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    showcase_module._clear_verified_showcase_cache_for_tests()
    home = tmp_path / "home"
    capable = _binding(runtime_managed=True)
    initial_catalog, _ = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=capable,
    )
    cache_entry = next(iter(showcase_module._VERIFIED_SHOWCASE_CACHE.values()))
    generation = showcase_module._VERIFIED_SHOWCASE_CACHE_GENERATION
    verification_calls = 0
    projection_calls = 0
    original_verify = showcase_module._verify_and_cache_showcase_packages
    original_assess = api_admission_module.assess_package_execution

    def counted_verify(*args, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        return original_verify(*args, **kwargs)

    def counted_assess(*args, **kwargs):
        nonlocal projection_calls
        projection_calls += 1
        return original_assess(*args, **kwargs)

    monkeypatch.setattr(
        showcase_module,
        "_verify_and_cache_showcase_packages",
        counted_verify,
    )
    monkeypatch.setattr(
        api_admission_module,
        "assess_package_execution",
        counted_assess,
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("authenticated ascii skill", ["ascii-art"], []),
    )
    store = RunStore(home)
    _healthy_coordinator(store)

    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="ai-extensions",
        values={},
        idempotency_key="cached-capable-ai",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="showcase",
        runner_binding=capable,
    )
    subsequent_catalog, _ = build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
        runner_binding=capable,
    )
    detail = build_workflow_detail(
        "ai-extensions",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="showcase",
        runner_binding=capable,
    )

    def ai_row(items):
        return next(item for item in items if item["name"] == "ai-extensions")

    assert verification_calls == 0
    assert next(iter(showcase_module._VERIFIED_SHOWCASE_CACHE.values())) is cache_entry
    assert showcase_module._VERIFIED_SHOWCASE_CACHE_GENERATION == generation
    assert projection_calls == 1
    assert admitted["status"] in {"queued", "running"}
    assert ai_row(initial_catalog)["compatibility"]["runnable"] is True
    assert ai_row(subsequent_catalog)["compatibility"]["runnable"] is True
    assert detail["compatibility"]["runnable"] is True


def test_showcase_admission_uses_server_binding_and_incapable_is_zero_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("authenticated ascii skill", ["ascii-art"], []),
    )
    capable_store = RunStore(tmp_path / "capable")
    _healthy_coordinator(capable_store)
    admitted = start_api_run(
        capable_store,
        hermes_home=capable_store.hermes_home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="ai-extensions",
        values={},
        idempotency_key="capable-ai",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="showcase",
        runner_binding=_binding(runtime_managed=True),
    )
    assert admitted["status"] in {"queued", "running"}

    incapable_store = RunStore(tmp_path / "incapable")
    _healthy_coordinator(incapable_store)
    with pytest.raises(ApiAdmissionError) as exc_info:
        start_api_run(
            incapable_store,
            hermes_home=incapable_store.hermes_home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="ai-extensions",
            values={},
            idempotency_key="incapable-ai",
            concurrency_policy="queue",
            authority=_authority(),
            catalog_source="showcase",
            runner_binding=_binding(runtime_managed=False),
        )
    assert exc_info.value.code == "workflow_compatibility_blocked"
    assert list(incapable_store.runs_root.rglob("run.json")) == []
    assert list(incapable_store.staging_root.iterdir()) == []


def test_ai_showcase_can_be_scheduled_with_capable_server_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda *_args, **_kwargs: ("authenticated ascii skill", ["ascii-art"], []),
    )
    store = RunStore(tmp_path / "scheduled-ai")
    _healthy_coordinator(store)
    schedule_at = "2099-01-02T03:04:05Z"

    admitted = start_api_run(
        store,
        hermes_home=store.hermes_home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="ai-extensions",
        values={},
        idempotency_key="scheduled-capable-ai",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="showcase",
        runner_binding=_binding(runtime_managed=True),
        schedule_at=schedule_at,
        schedule_now_utc=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    run = store.get_run_status(str(admitted["run_id"]))
    assert admitted["status"] == "queued"
    assert run["status"] == "queued"
    assert run["started_at"] is None
    assert run["run_metadata"]["showcase_id"] == "ai-extensions"
    assert run["run_metadata"]["schedule_at"] == schedule_at


def test_package_mcp_capability_is_general_for_trusted_user_workflow(
    tmp_path: Path,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    workflow = workflow_writer(
        home / "workflows",
        name="trusted-user-mcp",
        filename="trusted-user-mcp.yaml",
        nodes=[
            {
                "id": "inspect",
                "command": "inspect-evidence",
                "mcp": "echo.yaml",
                "allowed_tools": ["mcp__node_echo__echo"],
            }
        ],
    )
    (home / "commands").mkdir(parents=True)
    (home / "commands/inspect-evidence.md").write_text(
        "Inspect the synthetic evidence.", encoding="utf-8"
    )
    (home / "mcp").mkdir(parents=True)
    (home / "mcp/echo.yaml").write_text(
        "command: python\nargs: [mcp/echo-server.py]\nenv: {}\n",
        encoding="utf-8",
    )
    (home / "mcp/echo-server.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    capable = _binding(runtime_managed=True)
    incapable = _binding(runtime_managed=False)
    capable_compatibility = assess_compatibility(package, mcp_available=True)
    capable_risk = build_risk_summary(package, capable_compatibility)
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="runner-binding-test",
        risk_digest=capable_risk.risk_digest,
    )

    capable_detail = build_workflow_detail(
        "trusted-user-mcp",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="profile",
        runner_binding=capable,
    )
    incapable_detail = build_workflow_detail(
        "trusted-user-mcp",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="profile",
        runner_binding=incapable,
    )

    assert capable_detail["compatibility"]["runnable"] is True
    assert capable_detail["trust_state"] == "trusted"
    assert incapable_detail["compatibility"]["runnable"] is False
    assert incapable_detail["trust_state"] == "untrusted"

    store = RunStore(home)
    _healthy_coordinator(store)
    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="trusted-user-mcp",
        values={},
        idempotency_key="trusted-user-mcp",
        concurrency_policy="queue",
        authority=_authority(),
        catalog_source="profile",
        runner_binding=capable,
    )
    assert admitted["run_id"]

    incapable_home = tmp_path / "incapable-home"
    for directory in ("workflows", "commands", "mcp"):
        shutil.copytree(home / directory, incapable_home / directory)
    incapable_package = load_workflow(
        incapable_home / "workflows/trusted-user-mcp.yaml"
    )
    incapable_compatibility = assess_compatibility(
        incapable_package,
        mcp_available=False,
    )
    incapable_risk = build_risk_summary(
        incapable_package,
        incapable_compatibility,
    )
    WorkflowTrustStore(incapable_home).trust(
        compute_package_digest(incapable_package).sha256,
        actor="runner-binding-test",
        risk_digest=incapable_risk.risk_digest,
    )
    incapable_store = RunStore(incapable_home)
    _healthy_coordinator(incapable_store)

    with pytest.raises(ApiAdmissionError) as exc_info:
        start_api_run(
            incapable_store,
            hermes_home=incapable_home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="trusted-user-mcp",
            values={},
            idempotency_key="trusted-user-mcp-incapable",
            concurrency_policy="queue",
            authority=_authority(),
            catalog_source="profile",
            runner_binding=incapable,
        )
    assert exc_info.value.code == "workflow_compatibility_blocked"
    assert list(incapable_store.runs_root.rglob("run.json")) == []
    assert list(incapable_store.staging_root.iterdir()) == []


def test_coordinator_uses_the_same_production_binding_and_request_has_no_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(runtime_managed=True)
    calls = 0

    def production_binding() -> WorkflowRunnerBinding:
        nonlocal calls
        calls += 1
        return binding

    monkeypatch.setattr(
        runner_binding_module,
        "production_workflow_runner_binding",
        production_binding,
    )

    service = coordinator_module.WorkflowCoordinatorService(
        BackgroundServiceContext(host_kind="web", host_instance_id="binding-test"),
        hermes_home=tmp_path,
    )
    scheduler = service._scheduler(
        RunStore(tmp_path / "production"),
        fence=ExecutionFence("runner-binding-test", 1),
    )

    assert calls == 1
    assert scheduler.runner_binding is binding
    assert scheduler.executors["command"].agent_runner is binding.real_runner
    assert scheduler.executors["approval"].deterministic_runner is (
        binding.deterministic_runner
    )

    injected = _binding(runtime_managed=False)
    injected_service = coordinator_module.WorkflowCoordinatorService(
        BackgroundServiceContext(host_kind="web", host_instance_id="injected-test"),
        hermes_home=tmp_path,
        runner_binding=injected,
    )
    injected_scheduler = injected_service._scheduler(
        RunStore(tmp_path / "injected"),
        fence=ExecutionFence("injected-binding-test", 1),
    )
    assert calls == 1
    assert injected_scheduler.runner_binding is injected
    forbidden = {
        "runner",
        "runner_binding",
        "runner_capabilities",
        "runtime_capabilities",
        "mcp_available",
    }
    assert forbidden.isdisjoint(StartRunRequest.model_fields)
