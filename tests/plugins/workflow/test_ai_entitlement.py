from __future__ import annotations

from pathlib import Path
import copy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

import plugins.workflow.entitlement as entitlement_module
from plugins.workflow.entitlement import (
    AIEntitlementResolution,
    agent_backed_features,
    derive_ai_entitlement,
    validate_showcase_ai_contract,
)
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.ai import AgentNodeExecutor
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.loop import LoopExecutor
from plugins.workflow.models import WorkflowDefinition, WorkflowNode, freeze_value
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.resources import VariableContext
from plugins.workflow.showcase import ShowcaseCatalogError
from plugins.workflow.showcase import load_verified_showcase_package
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowResourceReadBudget
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    assess_package_execution,
    execution_capability_context,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "entitlement" / "v3.0.2.json"


def _definition(*nodes: WorkflowNode) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="entitlement-fixture",
        description="AI entitlement fixture",
        nodes=nodes,
        options=freeze_value({}),
        source_path=Path("fixture.yaml"),
    )


def _node(
    node_id: str,
    node_type: str,
    value: object,
    *,
    options: dict[str, object] | None = None,
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        node_type=node_type,
        value=freeze_value(value),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value(options or {}),
    )


def test_agent_backed_features_positively_classifies_every_runner_consumer() -> None:
    definition = _definition(
        _node("command", "command", "inspect", options={"agents": {"reviewer": {}}}),
        _node("prompt", "prompt", "summarize"),
        _node(
            "loop",
            "loop",
            {"prompt": "iterate", "until": "DONE", "max_iterations": 2},
        ),
        _node(
            "approval",
            "approval",
            {"message": "Review", "on_reject": {"prompt": "Revise"}},
        ),
    )

    assert agent_backed_features(definition) == frozenset(
        {"command", "prompt", "loop", "inline_agents", "approval_rework"}
    )


@pytest.mark.parametrize("feature", ["command", "prompt", "loop", "inline_agents"])
def test_non_ai_showcase_contract_rejects_each_real_agent_feature(feature: str) -> None:
    if feature == "loop":
        node = _node(
            "consumer",
            "loop",
            {"prompt": "iterate", "until": "DONE", "max_iterations": 1},
        )
    else:
        node_type = "command" if feature in {"command", "inline_agents"} else "prompt"
        options = {"agents": {"reviewer": {}}} if feature == "inline_agents" else {}
        node = _node("consumer", node_type, "run", options=options)

    with pytest.raises(ShowcaseCatalogError, match="requires_ai"):
        validate_showcase_ai_contract(
            "copied-non-ai",
            requires_ai=False,
            definition=_definition(node),
        )


def test_non_ai_approval_rework_is_deterministic_but_ai_showcase_is_allowed() -> None:
    approval = _definition(
        _node(
            "approval",
            "approval",
            {"message": "Review", "on_reject": {"prompt": "Revise"}},
        )
    )
    validate_showcase_ai_contract(
        "approval-gate", requires_ai=False, definition=approval
    )

    ai = _definition(_node("prompt", "prompt", "Use the verified model"))
    validate_showcase_ai_contract("ai-extensions", requires_ai=True, definition=ai)


def _fixture_request(case: dict[str, object]) -> RunAdmissionRequest:
    source = str(case["trigger_source"])
    key = str(case["idempotency_key"])
    if source == "desktop":
        provenance = TriggerProvenance.authenticated_api(
            source="desktop",
            assurance="local_admin_claim",
            intent_key=key,
            source_instance="desktop:v3.0.2",
            principal="local:operator",
        )
    else:
        provenance = TriggerProvenance.local_admin_claim(
            source="cli",
            intent_key=key,
            source_instance="cli:v3.0.2",
            claimed_actor="fixture-operator",
        )
    return RunAdmissionRequest(
        workflow_name=str(case["workflow_name"]),
        definition_digest=str(case["definition_digest"]),
        policy_digest=str(case["policy_digest"]),
        input_manifest_digest=str(case["input_manifest_digest"]),
        trigger_source=source,  # type: ignore[arg-type]
        idempotency_key=key,
        idempotency_namespace=str(case["idempotency_namespace"]),
        concurrency_key=str(case["concurrency_key"]),
        run_metadata=case["run_metadata"],  # type: ignore[arg-type]
        provenance=provenance,
    )


def test_v302_rest_and_cli_absence_fixtures_derive_without_mutation() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]

    for case in cases:
        metadata = case["run_metadata"]
        before = copy.deepcopy(metadata)
        resolution = derive_ai_entitlement(metadata)

        assert resolution.value == "deterministic"
        assert resolution.error_code is None
        assert metadata == before
        assert RunStore._start_digest(_fixture_request(case)) == case[
            "expected_start_digest"
        ]


@pytest.mark.parametrize("case_index", [0, 1], ids=("rest", "cli"))
def test_v302_same_key_resubmission_joins_existing_without_metadata_upgrade(
    tmp_path: Path, workflow_writer, case_index: int
) -> None:
    case = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"][case_index]
    package = load_workflow(
        workflow_writer(tmp_path / "package", name=str(case["workflow_name"]))
    )
    store = RunStore(tmp_path / "home")
    first_snapshot = store.prepare_run_snapshot(package)
    request = replace(
        _fixture_request(case),
        definition_digest=first_snapshot.definition_digest,
        policy_digest=first_snapshot.policy_digest,
        input_manifest_digest=first_snapshot.input_manifest_digest,
    )
    first = store.start_run(request, immutable_snapshot=first_snapshot)
    second_snapshot = store.prepare_run_snapshot(package)
    second = store.start_run(request, immutable_snapshot=second_snapshot)

    assert first.disposition == "created"
    assert second.disposition == "existing"
    assert second.run_id == first.run_id
    assert store.get_run_status(first.run_id)["run_metadata"] == case["run_metadata"]


def test_absent_ordinary_run_is_real_but_malformed_value_is_deterministic() -> None:
    assert derive_ai_entitlement({}).value == "real"
    assert derive_ai_entitlement({"ai_entitlement": "REAL"}).value == (
        "deterministic"
    )
    assert derive_ai_entitlement({"showcase_id": "partial"}).value == (
        "deterministic"
    )


def test_only_verified_ai_admission_writes_explicit_real_entitlement() -> None:
    identity = {
        "showcase_id": "fixture",
        "showcase_version": "1.0.0",
        "bundle_digest": "a" * 64,
        "risk_digest": "b" * 64,
    }

    legacy_non_ai = entitlement_module.verified_showcase_run_metadata(
        **identity,
        requires_ai=False,
        include_verified_marker=False,
    )
    current_non_ai = entitlement_module.verified_showcase_run_metadata(
        **identity,
        requires_ai=False,
        include_verified_marker=True,
    )
    verified_ai = entitlement_module.verified_showcase_run_metadata(
        **identity,
        requires_ai=True,
        include_verified_marker=False,
    )

    assert legacy_non_ai == identity
    assert current_non_ai == {
        **identity,
        "showcase_provenance": "verified_bundled",
    }
    assert verified_ai == {
        **identity,
        "showcase_provenance": "verified_bundled",
        "ai_entitlement": "real",
    }


def test_exact_verified_requires_ai_scenario_corroborates_real_entitlement() -> None:
    read_budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    verified = load_verified_showcase_package(
        "ai-extensions",
        read_budget=read_budget,
    )
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
        ),
    )
    _compatibility, risk = assess_package_execution(
        verified.package,
        execution_context,
        read_budget=read_budget,
    )
    metadata = entitlement_module.verified_showcase_run_metadata(
        showcase_id=verified.scenario.id,
        showcase_version=verified.scenario.package_version,
        bundle_digest=verified.bundle_digest,
        risk_digest=risk.risk_digest,
        requires_ai=True,
        include_verified_marker=False,
    )

    resolution = derive_ai_entitlement(
        metadata,
        definition_digest=risk.package_digest,
        execution_context=execution_context,
    )

    assert resolution == AIEntitlementResolution("real")


def test_exact_verified_v4_entitlement_uses_composite_identity(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    workflow = workflow_writer(
        tmp_path / "v4-entitlement/workflows",
        name="v4-entitlement",
        nodes=[{"id": "inspect", "prompt": "Inspect"}],
    )
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="showcase",
        precedence=3,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
        ),
    )
    _compatibility, risk = assess_package_execution(
        compilation.package,
        execution_context,
        compilation=compilation,
    )
    scenario = SimpleNamespace(
        id="v4-entitlement",
        package_version="1.0.0",
        requires_ai=True,
        verified_bundled_provenance=True,
    )
    verified = SimpleNamespace(
        scenario=scenario,
        package=compilation.package,
        compilation=compilation,
        bundle_digest="a" * 64,
    )
    monkeypatch.setattr(
        "plugins.workflow.showcase.load_verified_showcase_package",
        lambda *_args, **_kwargs: verified,
    )
    metadata = entitlement_module.verified_showcase_run_metadata(
        showcase_id=scenario.id,
        showcase_version=scenario.package_version,
        bundle_digest=verified.bundle_digest,
        risk_digest=risk.risk_digest,
        requires_ai=True,
        include_verified_marker=False,
    )

    resolution = derive_ai_entitlement(
        metadata,
        definition_digest=compilation.composite_digest,
        execution_context=execution_context,
    )

    assert resolution == AIEntitlementResolution("real")


@pytest.mark.parametrize(
    "metadata",
    [
        {"ai_entitlement": "real"},
        {
            "ai_entitlement": "real",
            "showcase_provenance": "verified_bundled",
            "showcase_id": "approval-gate",
            "showcase_version": "1.0.0",
            "bundle_digest": "a" * 64,
            "risk_digest": "b" * 64,
        },
        {
            "ai_entitlement": "real",
            "showcase_provenance": "verified_bundled",
            "showcase_id": "missing-scenario",
            "showcase_version": "1.0.0",
            "bundle_digest": "a" * 64,
            "risk_digest": "b" * 64,
        },
        {
            "ai_entitlement": "real",
            "showcase_provenance": "verified_bundled",
            "showcase_id": "ai-extensions",
            "showcase_version": "changed",
            "bundle_digest": "a" * 64,
            "risk_digest": "b" * 64,
        },
    ],
    ids=("missing-identity", "non-ai", "missing", "changed"),
)
def test_explicit_real_requires_exact_verified_ai_corroboration(
    metadata: dict[str, str],
) -> None:
    resolution = derive_ai_entitlement(metadata, definition_digest="c" * 64)

    assert resolution.value == "deterministic"
    assert resolution.error_code == "execution_integrity"


class _ForbiddenRealRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("real PluginAgentRunner path must not be selected")


def _execution_context(
    tmp_path: Path,
    node: WorkflowNode,
    *,
    resolution: AIEntitlementResolution,
    node_state: dict[str, object] | None = None,
) -> NodeExecutionContext:
    run_directory = tmp_path / node.id
    run_directory.mkdir()
    return NodeExecutionContext(
        run_id=f"run-{node.id}",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        workflow_name="deterministic-fixture",
        workflow_options=freeze_value({}),
        variable_context=VariableContext(workflow_id=f"run-{node.id}"),
        node_state=freeze_value(node_state or {}),
        ai_entitlement=resolution,
    )


@pytest.mark.parametrize("node_type", ["command", "prompt"])
def test_agent_executor_uses_shared_deterministic_runner_for_command_and_prompt(
    tmp_path: Path, node_type: str
) -> None:
    runner = _ForbiddenRealRunner()
    node = _node("agent", node_type, "inspect" if node_type == "command" else "work")
    context = _execution_context(
        tmp_path,
        node,
        resolution=AIEntitlementResolution("deterministic"),
    )
    if node_type == "command":
        commands = context.run_directory / "commands"
        commands.mkdir()
        (commands / "inspect.md").write_text("Inspect fictional evidence", encoding="utf-8")

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.metadata["audit"]["deterministic"] is True
    assert runner.calls == 0


def test_loop_executor_uses_shared_deterministic_runner(tmp_path: Path) -> None:
    runner = _ForbiddenRealRunner()
    node = _node(
        "loop",
        "loop",
        {
            "prompt": "Work without a model",
            "until": "DETERMINISTIC_COMPLETE",
            "max_iterations": 1,
        },
    )
    context = _execution_context(
        tmp_path,
        node,
        resolution=AIEntitlementResolution("deterministic"),
    )

    result = LoopExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert runner.calls == 0


def test_inline_agents_cannot_escape_deterministic_parent_entitlement(
    tmp_path: Path,
) -> None:
    runner = _ForbiddenRealRunner()
    node = _node(
        "inline",
        "prompt",
        "delegate safely",
        options={
            "agents": {
                "reviewer": {
                    "description": "Review fictional evidence",
                    "prompt": "Review without a model",
                }
            }
        },
    )
    context = _execution_context(
        tmp_path,
        node,
        resolution=AIEntitlementResolution("deterministic"),
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "succeeded"
    assert result.metadata["audit"]["deterministic"] is True
    assert runner.calls == 0


def test_approval_rework_uses_shared_deterministic_runner(tmp_path: Path) -> None:
    runner = _ForbiddenRealRunner()
    node = _node(
        "approval",
        "approval",
        {"message": "Review", "on_reject": {"prompt": "Revise: $REJECTION_REASON"}},
    )
    context = _execution_context(
        tmp_path,
        node,
        resolution=AIEntitlementResolution("deterministic"),
        node_state={"approval_rework": {"reason": "be more cautious"}},
    )

    result = ApprovalExecutor(runner).execute(context)

    assert result.status == "paused"
    assert len(result.artifacts) == 1
    assert runner.calls == 0


def test_integrity_failure_stops_before_any_runner_selection(tmp_path: Path) -> None:
    runner = _ForbiddenRealRunner()
    context = _execution_context(
        tmp_path,
        _node("blocked", "prompt", "must not run"),
        resolution=AIEntitlementResolution(
            "deterministic",
            error_code="execution_integrity",
            error_message="uncorroborated entitlement",
        ),
    )

    result = AgentNodeExecutor(runner).execute(context)

    assert result.status == "failed"
    assert result.error_code == "execution_integrity"
    assert runner.calls == 0
