from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission_service import assess_workflow_admission
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    WorkflowRunnerBinding,
    execution_capability_context,
)
from plugins.workflow.schema import parse_workflow_source_bytes


def _compilation(tmp_path, workflow_writer, *, hook_event="Notification"):
    path = workflow_writer(
        tmp_path / "source/workflows",
        name="admission-parity",
        filename="admission-parity.yaml",
        model="@primary",
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "allowed_tools": ["Read"],
                "hooks": {
                    hook_event: [{
                        "response": (
                            {"continue": True}
                            if hook_event == "PreToolUse"
                            else {"suppressOutput": True}
                        )
                    }]
                },
            }
        ],
    )
    policy = b"language_compatibility: archon-2026-07\n"
    path.with_name("admission-parity.hermes.yaml").write_bytes(policy)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=policy,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _context(*, model="openai/gpt-5.4"):
    config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        },
        "model_aliases": {
            "primary": {"provider": "openrouter", "model": model}
        },
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=runtime,
        model_config_snapshot=config,
    )


def _binding(*, model="openai/gpt-5.4"):
    context = _context(model=model)
    return WorkflowRunnerBinding(
        real_runner=object(),
        deterministic_runner=object(),
        real_capabilities=context.runner_capabilities,
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=context.runtime_capabilities,
        model_config_snapshot=context.model_config_snapshot,
    )


def test_one_admission_result_owns_authority_compatibility_risk_and_summary(
    tmp_path, workflow_writer
):
    compilation = _compilation(tmp_path, workflow_writer)

    assessment = assess_workflow_admission(compilation, _context())

    assert assessment.package is compilation.package
    assert assessment.provider_authority is not None
    assert assessment.compatibility.runnable is False
    assert assessment.risk.package_digest == compilation.composite_digest
    assert assessment.capability_summary == {
        "schema_version": 1,
        "resolved_route_count": 1,
        "mixed_provider": False,
        "unsupported_count": 1,
        "degraded_count": 0,
        "warning_codes": ("model_reference_not_globally_portable",),
        "authority_digest": assessment.provider_authority.authority_digest,
    }
    assert assessment.next_actions == ("doctor",)
    assert {
        (item.path, item.code) for item in assessment.compatibility.blocking_findings
    } == {("nodes[0].hooks.Notification[0]", "hooks_precondition_unsatisfied")}
    assert not hasattr(assessment, "__dict__")
    with pytest.raises(FrozenInstanceError):
        assessment.risk = None  # type: ignore[misc]


def test_alias_drift_changes_the_whole_admission_identity(
    tmp_path, workflow_writer
):
    compilation = _compilation(tmp_path, workflow_writer)

    first = assess_workflow_admission(compilation, _context(model="openai/gpt-5.4"))
    second = assess_workflow_admission(compilation, _context(model="openai/gpt-5.5"))

    assert first.execution_identity != second.execution_identity
    assert first.risk.risk_digest != second.risk.risk_digest
    assert first.capability_summary != second.capability_summary


def test_common_admission_preserves_caller_owned_tool_availability(
    tmp_path, workflow_writer
):
    compilation = _compilation(tmp_path, workflow_writer)

    assessment = assess_workflow_admission(
        compilation,
        _context(),
        available_tools=frozenset(),
    )

    assert (
        "nodes[0].allowed_tools[0]",
        "unavailable_tool",
    ) in {
        (item.path, item.code)
        for item in assessment.compatibility.blocking_findings
    }


def test_model_resolution_failure_is_a_blocking_admission_result(
    tmp_path, workflow_writer
):
    compilation = _compilation(tmp_path, workflow_writer)
    missing_alias_context = _context()
    missing_alias_context = type(missing_alias_context)(
        surface=missing_alias_context.surface,
        entitlement=missing_alias_context.entitlement,
        runner_capabilities=missing_alias_context.runner_capabilities,
        runtime_capabilities=missing_alias_context.runtime_capabilities,
        mcp_available=missing_alias_context.mcp_available,
        configured_provider_routes=missing_alias_context.configured_provider_routes,
        model_config_snapshot=parse_workflow_model_config({
            "model": {
                "provider": "openrouter",
                "default": "openai/gpt-5.4",
            }
        }),
        provider_authority_environment=(
            missing_alias_context.provider_authority_environment
        ),
    )

    assessment = assess_workflow_admission(compilation, missing_alias_context)

    assert assessment.provider_authority is None
    assert assessment.execution_identity is None
    assert {
        (item.path, item.code)
        for item in assessment.compatibility.blocking_findings
    } >= {("nodes[0].model", "model_alias_unconfigured")}


def test_cli_show_and_validate_consume_the_common_phase5_assessment(
    tmp_path, workflow_writer, monkeypatch
):
    import argparse

    import plugins.workflow.cli as cli_module
    compilation = _compilation(tmp_path, workflow_writer, hook_event="PreToolUse")
    assessment = assess_workflow_admission(compilation, _context())
    emitted = []

    monkeypatch.setattr(cli_module, "_resolve", lambda *_args: compilation.package)
    monkeypatch.setattr(cli_module, "_resolve_compilation", lambda *_args: compilation)
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate: assessment,
    )
    monkeypatch.setattr(cli_module, "_cron_jobs", lambda: ())
    monkeypatch.setattr(
        cli_module,
        "_emit",
        lambda payload, *, as_json: emitted.append(payload),
    )

    assert cli_module._cmd_show(
        argparse.Namespace(name="admission-parity", json=True, topology=None)
    ) == 0
    assert emitted[0]["compatibility"]["blocking_count"] == 0

    assert cli_module._cmd_validate(
        argparse.Namespace(name="admission-parity", json=True)
    ) == 0
    assert emitted[1]["valid"] is True
    assert "provider_authority_missing" not in {
        item["code"] for item in emitted[1]["issues"]
    }


def test_cli_gateway_rest_doctor_catalog_and_detail_share_blocking_decision(
    tmp_path, workflow_writer, monkeypatch
):
    import argparse

    from hermes_cli.plugin_invocation import PluginInvocationContext
    from plugins.workflow.api_admission import (
        ApiAdmissionAuthority,
        ApiAdmissionError,
        start_api_run,
    )
    import plugins.workflow.api_admission as api_module
    import plugins.workflow.catalog_api as catalog_module
    import plugins.workflow.cli as cli_module
    import plugins.workflow.gateway_command as gateway_module
    from plugins.workflow.compat import WorkflowCompatibilityBlockedError
    from plugins.workflow.store import RunStore

    compilation = _compilation(tmp_path, workflow_writer)
    context = _context()
    binding = _binding()
    expected = {("nodes[0].hooks.Notification[0]", "hooks_precondition_unsatisfied")}

    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate: assess_workflow_admission(candidate, context),
    )
    monkeypatch.setattr(cli_module, "_resolve_compilation", lambda *_args: compilation)
    cli_args = argparse.Namespace(
        name="admission-parity",
        hermes_home=str(tmp_path / "home"),
    )
    with pytest.raises(WorkflowCompatibilityBlockedError) as cli_error:
        cli_module._cmd_run(cli_args)

    invocation = PluginInvocationContext(
        boundary="gateway",
        assurance="verified_adapter",
        principal="telegram:user:1",
        operator_scope="telegram:user:1",
        return_route_capability="telegram:chat:1",
    )
    gateway_args = argparse.Namespace(
        name="admission-parity",
        arguments="",
        idempotency_key="gateway-v5",
        concurrency_key=None,
    )
    with pytest.raises(WorkflowCompatibilityBlockedError) as gateway_error:
        gateway_module._start_gateway_run(
            gateway_args,
            invocation,
            hermes_home=tmp_path / "home",
            workdir=tmp_path,
        )

    monkeypatch.setattr(api_module, "_catalog_compilation", lambda *_a, **_k: compilation)
    original_trust_snapshot = api_module.WorkflowTrustStore.snapshot_read_only
    monkeypatch.setattr(
        api_module.WorkflowTrustStore,
        "snapshot_read_only",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("trust must not run before capability blocking")
        ),
    )
    with pytest.raises(ApiAdmissionError) as api_error:
        start_api_run(
            RunStore(tmp_path / "api-home"),
            hermes_home=tmp_path / "api-home",
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="admission-parity",
            values={},
            idempotency_key="api-v5",
            concurrency_policy="queue",
            authority=ApiAdmissionAuthority(
                principal="desktop:user",
                namespace="desktop:user",
                operator_scope=None,
                source_instance="desktop:test",
                assurance="local_admin_claim",
                trigger_source="desktop",
            ),
            runner_binding=binding,
        )
    monkeypatch.setattr(
        api_module.WorkflowTrustStore,
        "snapshot_read_only",
        original_trust_snapshot,
    )

    doctor = cli_module.doctor_package(
        compilation.package,
        hermes_home=tmp_path / "doctor-home",
        compilation=compilation,
        available_tools=frozenset(),
        execution_context=context,
    )
    monkeypatch.setattr(
        catalog_module,
        "_discover_catalog_compilations",
        lambda *_args: ([compilation], False),
    )
    catalog, _truncated = catalog_module.build_workflow_catalog(
        hermes_home=tmp_path / "catalog-home",
        workdir=tmp_path,
        runner_binding=binding,
    )
    detail = catalog_module.build_workflow_detail(
        "admission-parity",
        hermes_home=tmp_path / "catalog-home",
        workdir=tmp_path,
        runner_binding=binding,
    )

    def blocking(report):
        return {(item.path, item.code) for item in report.blocking_findings}

    assert blocking(cli_error.value.report) == expected
    assert blocking(gateway_error.value.report) == expected
    assert api_error.value.code == "workflow_compatibility_blocked"
    assert {
        (item.path, item.code) for item in doctor.findings if item.blocking
    } >= expected | {("nodes[0].allowed_tools[0]", "unavailable_tool")}
    assert {
        (item["path"], item["code"])
        for item in detail["compatibility"]["findings"]
        if item["blocking"]
    } == expected
    assert catalog[0]["provider_capability"] == detail["provider_capability"]
