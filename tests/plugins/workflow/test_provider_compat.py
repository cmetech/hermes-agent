from __future__ import annotations

from plugins.workflow.compat import CompatibilityLevel, assess_compatibility
from plugins.workflow.schema import load_workflow


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
