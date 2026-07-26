from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
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
    execution_capability_context,
)
import plugins.workflow.api_admission as api_admission_module
import plugins.workflow.showcase as showcase_module
import plugins.workflow.runner_binding as runner_binding_module
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
    assert set(package.__slots__) == {
        "scenario",
        "package",
        "package_digest",
        "bundle_digest",
    }
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
    )
    changed_capabilities = ExecutionRuntimeCapabilities(
        api_mode="anthropic_messages",
        hermes_managed_tool_loop=True,
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
        == initial_capabilities
    )


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
