from __future__ import annotations

from agent.structured_output import StructuredOutputStrategy
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.compat import CompatibilityLevel, assess_compatibility
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow


def _package(workflow_writer, tmp_path):
    return load_workflow(
        workflow_writer(
            tmp_path,
            provider="custom",
            model="custom-model",
            modelReasoningEffort="high",
            webSearchMode="auto",
            fallbackModel="fallback-model",
            betas=["feature-x"],
            sandbox={"enabled": True},
            nodes=[
                {
                    "id": "agent",
                    "prompt": "x",
                    "effort": "high",
                    "thinking": "adaptive",
                    "maxBudgetUsd": 1,
                }
            ],
        )
    )


def test_every_provider_control_requires_its_exact_advertised_capability(
    workflow_writer, tmp_path
):
    report = assess_compatibility(
        _package(workflow_writer, tmp_path),
        provider_capabilities={"custom": set()},
    )

    assert report.runnable is False
    assert {finding.path for finding in report.blocking_findings} >= {
        "modelReasoningEffort",
        "webSearchMode",
        "fallbackModel",
        "betas",
        "sandbox",
        "nodes[0].effort",
        "nodes[0].thinking",
        "nodes[0].maxBudgetUsd",
    }


def test_fully_advertised_provider_controls_are_mapped(workflow_writer, tmp_path):
    report = assess_compatibility(
        _package(workflow_writer, tmp_path),
        provider_capabilities={
            "custom": {
                "reasoning_effort",
                "thinking",
                "budget",
                "web_execution",
                "fallback_model",
                "betas",
                "sandbox",
            }
        },
    )

    assert report.runnable is True
    assert report.level is CompatibilityLevel.MAPPED
    assert not report.blocking_findings


def _structured_package(workflow_writer, tmp_path):
    path = workflow_writer(
        tmp_path,
        name="structured-provider-compat",
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


def _decision(package, strategy):
    output = package.language.structured_outputs["producer"]
    return StructuredOutputCapabilityDecision(
        strategy=strategy,
        effective_provider="locked-provider",
        model="locked-model",
        api_mode="chat_completions",
        declaration_source="explicit_unsupported",
        adapter_version=1,
        schema_fingerprint=output.schema_fingerprint,
        rationale="provider explicitly forbids structured-output adaptation",
    )


def test_explicit_unsupported_structured_output_strategy_blocks_admission(
    workflow_writer, tmp_path
):
    package = _structured_package(workflow_writer, tmp_path)
    decision = _decision(package, StructuredOutputStrategy.UNSUPPORTED)

    report = assess_compatibility(
        package,
        structured_output_decisions={decision.schema_fingerprint: decision},
    )

    finding = next(
        item
        for item in report.findings
        if item.code == "structured_output_strategy_unsupported"
    )
    assert report.runnable is False
    assert finding.path == "nodes[0].output_format"
    assert finding.blocking is True


def test_prompt_structured_output_strategy_is_admitted(workflow_writer, tmp_path):
    package = _structured_package(workflow_writer, tmp_path)
    decision = _decision(package, StructuredOutputStrategy.PROMPT_JSON_SCHEMA)

    report = assess_compatibility(
        package,
        structured_output_decisions={decision.schema_fingerprint: decision},
    )

    assert report.runnable is True
    assert not any(
        item.code == "structured_output_strategy_unsupported"
        for item in report.findings
    )
