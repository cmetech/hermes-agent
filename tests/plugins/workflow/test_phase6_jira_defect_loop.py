from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from agent.structured_output import (
    StructuredOutputError,
    StructuredOutputRequest,
    StructuredOutputStrategy,
    normalize_schema,
    parse_validate_canonicalize,
)
from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.conditions import evaluate_v3_condition
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult
from plugins.workflow.executors.loop import clean_loop_completion
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    verify_language_snapshot,
)
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    resolved_output_publication_identity,
)
from plugins.workflow.provider_authority import (
    ProviderAuthorityEnvironment,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler, evaluate_trigger_rule
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.topology import iter_scoped_workflow_nodes, primary_terminal_node


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_WORKFLOW = (
    REPO_ROOT
    / "capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml"
)
VENDORED_WORKFLOW = REPO_ROOT / "capabilities/workflows/jira-defect-loop.yml"
WRITE_APPROVALS = {
    "create-branch": "approve-branch",
    "commit-changes": "approve-commit",
    "create-merge-request": "approve-merge-request",
    "update-jira": "approve-jira-comment",
}
EXPECTED_OUTCOMES = (
    "not_found",
    "permission",
    "needs_info",
    "manual_review",
    "not_a_code_fix",
    "safely_skipped",
)


def _compile_inline(tmp_path: Path, workflow: str, sidecar: str = ""):
    path = tmp_path / "workflows" / "inline.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(workflow, encoding="utf-8")
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.encode(),
        source="test",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=6,
    )


def test_v6_seals_prompt_max_turns_and_exact_scoped_outward_id(tmp_path) -> None:
    compilation = _compile_inline(
        tmp_path,
        """name: scoped-contract
description: Scoped contract
nodes:
  - id: group
    loop_group:
      until: DONE
      max_iterations: 1
      nodes:
        - id: approve
          approval: {message: Approve exact write}
        - id: write
          prompt: Write once
          maxTurns: 2
          depends_on: [approve]
""",
        """language_compatibility: archon-2026-07
outward_action_nodes: [group/write]
outward_action_policy: approval_required
""",
    )

    scoped = {
        item.semantic_id: item.node
        for item in iter_scoped_workflow_nodes(compilation.package.definition)
    }
    assert scoped["group/write"].options["maxTurns"] == 2
    assert compilation.package.sidecar["outward_action_nodes"] == ("group/write",)


def test_v6_seals_exact_tool_call_contract_and_rejects_it_before_v6(tmp_path) -> None:
    workflow = """name: exact-tool-call
description: Exact tool-call contract
nodes:
  - id: fetch
    prompt: Fetch once
    allowed_tools: [jira_my_tickets]
    tool_call_contract:
      name: jira_my_tickets
      arguments: {max_results: 25}
      result:
        items_path: result.items
        select: [key]
        output_items_path: tickets
        output_count_path: count
        output_status_path: status
        empty_status: empty
        nonempty_status: ready
        max_items: 25
"""
    compilation = _compile_inline(
        tmp_path,
        workflow,
        "language_compatibility: archon-2026-07\n",
    )
    node = compilation.package.definition.nodes[0]
    assert _thaw(node.options["tool_call_contract"]) == {
        "name": "jira_my_tickets",
        "arguments": {"max_results": 25},
        "result": {
            "items_path": "result.items",
            "select": ["key"],
            "output_items_path": "tickets",
            "output_count_path": "count",
            "output_status_path": "status",
            "empty_status": "empty",
            "nonempty_status": "ready",
            "max_items": 25,
        },
    }

    path = tmp_path / "workflows" / "pre-v6.yaml"
    path.write_text(workflow, encoding="utf-8")
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="test",
        precedence=1,
    )
    with pytest.raises(Exception, match="tool_call_contract.*Phase 6"):
        compile_workflow(
            source,
            WorkflowCatalogSnapshot.capture((source,)),
            normalizer_version=5,
        )


@pytest.mark.parametrize(
    "items_path",
    [
        "result..items",
        ".result.items",
        "result/items",
        "a.b.c.d.e.f.g.h.i",
    ],
)
def test_v6_rejects_unbounded_or_nonportable_result_paths(
    tmp_path: Path, items_path: str
) -> None:
    with pytest.raises(Exception, match="tool_call_contract"):
        _compile_inline(
            tmp_path,
            f"""name: invalid-result-path
description: Invalid result path
nodes:
  - id: fetch
    prompt: Fetch once
    tool_call_contract:
      name: jira_my_tickets
      arguments: {{max_results: 25}}
      result:
        items_path: {items_path}
        select: [key]
        output_items_path: tickets
        output_count_path: count
        output_status_path: status
        empty_status: empty
        nonempty_status: ready
        max_items: 25
""",
            "language_compatibility: archon-2026-07\n",
        )


def test_v6_ai_contract_fields_change_fingerprint_and_reject_snapshot_tamper(
    tmp_path,
) -> None:
    def compilation(*, max_turns: int, max_results: int):
        return _compile_inline(
            tmp_path / f"turns-{max_turns}-results-{max_results}",
            f"""name: sealed-ai-contract
description: Sealed AI contract
nodes:
  - id: fetch
    prompt: Fetch once
    maxTurns: {max_turns}
    allowed_tools: [jira_my_tickets]
    tool_call_contract:
      name: jira_my_tickets
      arguments: {{max_results: {max_results}}}
      result:
        items_path: result.items
        select: [key]
        output_items_path: tickets
        output_count_path: count
        output_status_path: status
        empty_status: empty
        nonempty_status: ready
        max_items: 25
""",
            "language_compatibility: archon-2026-07\n",
        )

    original = compilation(max_turns=2, max_results=25).package
    changed_turns = compilation(max_turns=3, max_results=25).package
    changed_arguments = compilation(max_turns=2, max_results=24).package
    original_snapshot = make_language_snapshot(original, "a" * 64)

    assert original.language.normalized_definition_digest not in {
        changed_turns.language.normalized_definition_digest,
        changed_arguments.language.normalized_definition_digest,
    }
    assert original_snapshot.semantic_fingerprint not in {
        make_language_snapshot(changed_turns, "a" * 64).semantic_fingerprint,
        make_language_snapshot(changed_arguments, "a" * 64).semantic_fingerprint,
    }
    with pytest.raises(
        WorkflowLanguageCompatibilityError,
        match="does not match sealed package semantics",
    ):
        verify_language_snapshot(changed_turns, "a" * 64, original_snapshot)


def test_max_turns_is_rejected_before_v6_instead_of_only_reported(tmp_path) -> None:
    workflow = """name: pre-v6-turn-cap
description: Pre-v6 turn cap
nodes:
  - id: work
    prompt: Work
    maxTurns: 2
"""
    path = tmp_path / "workflows" / "pre-v6-turn-cap.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(workflow, encoding="utf-8")
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="test",
        precedence=1,
    )
    with pytest.raises(Exception, match="maxTurns.*Phase 6"):
        compile_workflow(
            source,
            WorkflowCatalogSnapshot.capture((source,)),
            normalizer_version=5,
        )


@pytest.mark.parametrize("value", [True, 0, -1, 91])
def test_v6_rejects_invalid_prompt_max_turns(tmp_path, value) -> None:
    with pytest.raises(Exception, match="maxTurns"):
        _compile_inline(
            tmp_path,
            f"""name: invalid-turn-cap
description: Invalid turn cap
nodes:
  - id: work
    prompt: Work
    maxTurns: {str(value).lower()}
""",
            "language_compatibility: archon-2026-07\n",
        )


def test_v6_rejects_unknown_scoped_outward_id(tmp_path) -> None:
    with pytest.raises(Exception, match="unknown node"):
        _compile_inline(
            tmp_path,
            """name: unknown-scoped-action
description: Unknown scoped action
nodes:
  - id: group
    loop_group:
      until: DONE
      max_iterations: 1
      nodes:
        - id: write
          prompt: Write
""",
            """language_compatibility: archon-2026-07
outward_action_nodes: [group/other]
""",
        )


def _compile():
    sidecar = PACKAGE_WORKFLOW.with_name("jira-defect-loop.hermes.yaml")
    source = parse_workflow_source_bytes(
        PACKAGE_WORKFLOW,
        workflow_bytes=PACKAGE_WORKFLOW.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        source="distribution",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=6,
    )


def _thaw(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


def _request(compilation, node_id: str) -> StructuredOutputRequest:
    declared = compilation.package.language.structured_outputs[node_id]
    return StructuredOutputRequest(
        schema=normalize_schema(_thaw(declared.canonical_schema)),
        strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
        adapter_version=1,
    )


def _validate(request: StructuredOutputRequest, value: object) -> object:
    return parse_validate_canonicalize(
        json.dumps(value, separators=(",", ":")), request
    ).value


def _resolved(node_id: str, value: object) -> ResolvedNodeOutput:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return ResolvedNodeOutput(
        canonical_bytes=data,
        value=value,
        text=data.decode(),
        media_type="application/json",
        sha256=hashlib.sha256(data).hexdigest(),
        node_id=node_id,
        attempt_id=f"{node_id}-attempt",
        publication_id="a" * 32,
        schema_fingerprint="f" * 64,
    )


def _run_script(
    tmp_path: Path,
    node,
    *,
    outputs: dict[str, object],
    previous: dict[str, object | None] | None = None,
    extra_dependencies: tuple[str, ...] = (),
    predecessor_results: dict[str, dict[str, object]] | None = None,
    preexisting_artifacts: dict[str, str] | None = None,
):
    compilation = _compile()
    structured_id = (
        f"process-ticket-manifest/{node.id}"
        if f"process-ticket-manifest/{node.id}"
        in compilation.package.language.structured_outputs
        else node.id
    )
    current = {key: _resolved(key, value) for key, value in outputs.items()}
    prior = {
        key: (_resolved(key, value) if value is not None else None)
        for key, value in (previous or {}).items()
    }
    variables = VariableContext(
        current_body_outputs=current,
        allowed_outer_outputs=current,
        previous_body_outputs=prior,
        normalizer_version=6,
    )
    runtime_node = replace(
        node,
        depends_on=tuple(dict.fromkeys((*node.depends_on, *extra_dependencies))),
    )
    if predecessor_results is None:
        predecessor_results = {}
        for dependency in runtime_node.depends_on:
            output = current.get(dependency)
            if output is None:
                predecessor_results[dependency] = {"state": "skipped"}
                continue
            predecessor_results[dependency] = {
                "state": "succeeded",
                "output_evidence": resolved_output_publication_identity(output),
                "output": output.value,
            }
    run_directory = tmp_path / f"run-{node.id}-{len(list(tmp_path.iterdir()))}"
    run_directory.mkdir()
    for relative_path, content in (preexisting_artifacts or {}).items():
        artifact = run_directory / "artifacts" / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(content, encoding="utf-8")
    result = ScriptExecutor().execute(
        NodeExecutionContext(
            run_id="reducer-test",
            run_directory=run_directory,
            node=runtime_node,
            attempt_id="attempt",
            variable_context=variables,
            predecessor_results=predecessor_results,
            output_resolver=variables.output_reference,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=6,
            structured_output=compilation.package.language.structured_outputs.get(
                structured_id
            ),
            max_artifact_bytes=0,
        )
    )
    text = ""
    if result.artifacts:
        text = (run_directory / result.artifacts[0].relative_path).read_text()
    return result, text


def _write_evidence(
    ticket_key: str,
    operation: str,
    *,
    object_id: str | None,
    web_url: str | None = None,
    warnings: list[str] | None = None,
    attention_needed: bool = False,
    reconciliation_status: str = "not_required",
) -> dict[str, object]:
    return {
        "ticket_key": ticket_key,
        "operation": operation,
        "status": "success",
        "object_id": object_id,
        "web_url": web_url,
        "warnings": warnings or [],
        "attention_needed": attention_needed,
        "reconciliation_status": reconciliation_status,
    }


def _terminal_predecessors(
    plan: dict[str, object],
    *,
    create_branch: dict[str, object] | None = None,
    commit_changes: dict[str, object] | None = None,
    create_merge_request: dict[str, object] | None = None,
    review_merge_request: dict[str, object] | None = None,
    update_jira: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    values = {
        "prepare-writes": plan,
        "create-branch": create_branch,
        "commit-changes": commit_changes,
        "create-merge-request": create_merge_request,
        "review-merge-request": review_merge_request,
        "update-jira": update_jira,
    }
    predecessors = {}
    for node_id, output in values.items():
        if output is None:
            predecessors[node_id] = {"state": "skipped"}
            continue
        resolved = _resolved(node_id, output)
        predecessors[node_id] = {
            "state": "succeeded",
            "output_evidence": resolved_output_publication_identity(resolved),
            "output": output,
        }
    return predecessors


def _set_terminal_predecessor(
    predecessors: dict[str, dict[str, object]],
    node_id: str,
    output: dict[str, object],
    *,
    state: str = "succeeded",
) -> None:
    resolved = _resolved(node_id, output)
    predecessors[node_id] = {
        "state": state,
        "output_evidence": resolved_output_publication_identity(resolved),
        "output": output,
    }


def _terminal_plan(
    *,
    outcome: str = "fixed",
    branch: int = 1,
    commit: int = 1,
    merge_request: int = 1,
    jira: int = 1,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    return {
        "ticket_key": "ERIC-1",
        "manifest_count": 1,
        "project_path": "team/service",
        "branch_name": "fix/ERIC-1",
        "branch_args": {
            "project": "team/service",
            "prefix": "fix",
            "ticket_key": "ERIC-1",
            "summary": "Fix defect",
            "source_ref": "main",
            "dry_run": False,
        },
        "commit_args": {
            "project": "team/service",
            "branch": "fix/ERIC-1",
            "commit_message": "Fix ERIC-1",
            "actions": [
                {"action": "update", "file_path": "src/app.py", "content": "fixed"}
            ],
            "dry_run": False,
        },
        "merge_request_args": {
            "project": "team/service",
            "source_branch": "fix/ERIC-1",
            "target_branch": "main",
            "title": "Fix ERIC-1",
            "description": "Fix defect",
            "remove_source_branch": False,
            "squash": False,
            "dry_run": False,
        },
        "jira_comment_args": {
            "key": "ERIC-1",
            "body": "Fixed in merge request",
            "dry_run": False,
        },
        "should_create_branch": branch,
        "should_commit": commit,
        "should_create_merge_request": merge_request,
        "should_comment_jira": jira,
        "branch_intent_digest": "a" * 64,
        "commit_intent_digest": "b" * 64,
        "merge_request_intent_digest": "c" * 64,
        "jira_comment_intent_digest": "d" * 64,
        "terminal_outcome": outcome,
        "warnings": warnings or [],
    }


def test_distributed_v6_workflow_seals_one_immutable_bounded_manifest() -> None:
    compilation = _compile()
    package = compilation.package
    nodes = {node.id: node for node in package.definition.nodes}
    fetch = nodes["fetch-ticket-manifest"]
    group = nodes["process-ticket-manifest"]

    assert PACKAGE_WORKFLOW.read_bytes() == VENDORED_WORKFLOW.read_bytes()
    assert PACKAGE_WORKFLOW.with_name("jira-defect-loop.hermes.yaml").read_bytes() == (
        VENDORED_WORKFLOW.with_name("jira-defect-loop.hermes.yaml").read_bytes()
    )
    assert fetch.options["allowed_tools"] == ("jira_my_tickets",)
    assert _thaw(fetch.options["tool_call_contract"]) == {
        "name": "jira_my_tickets",
        "arguments": {"max_results": 25},
        "result": {
            "items_path": "result.items",
            "select": ["key"],
            "output_items_path": "tickets",
            "output_count_path": "count",
            "output_status_path": "status",
            "empty_status": "empty",
            "nonempty_status": "ready",
            "max_items": 25,
        },
    }
    assert fetch.options["maxTurns"] == 2
    assert fetch.options["retry"]["max_attempts"] == 0
    assert '"max_results": 25' in fetch.value
    assert "exactly one" in fetch.value.lower()
    assert "first occurrence" in fetch.value.lower()
    assert "immutable" in fetch.value.lower()

    manifest = package.language.structured_outputs[
        "fetch-ticket-manifest"
    ].canonical_schema
    assert manifest["properties"]["tickets"]["maxItems"] == 25
    assert manifest["properties"]["tickets"]["uniqueItems"] is True
    assert group.value["max_iterations"] == 25
    assert group.value["until"] == "BATCH_COMPLETE"
    assert primary_terminal_node(group.value["nodes"]).id == "record-cumulative-state"
    assert nodes["publish-empty-json"].options["when"] == (
        "$fetch-ticket-manifest.output.count == 0"
    )
    assert nodes["publish-empty-markdown"].options["when"] == (
        "$fetch-ticket-manifest.output.count == 0"
    )
    assert nodes["publish-aggregate-json"].options["when"] == (
        "$fetch-ticket-manifest.output.count > 0"
    )

    body = {node.id: node for node in group.value["nodes"]}
    assert body["select-ticket"].node_type == "script"
    assert body["publish-ticket-record"].node_type == "script"
    assert body["record-cumulative-state"].node_type == "script"
    assert nodes["publish-empty-json"].node_type == "script"
    assert nodes["publish-empty-markdown"].node_type == "script"
    assert nodes["publish-aggregate-json"].node_type == "script"
    assert nodes["publish-aggregate-markdown"].node_type == "script"


def test_manifest_fetch_has_one_total_attempt_and_cannot_retry(
    tmp_path: Path, monkeypatch
) -> None:
    compilation = _compile()
    package = compilation.package
    store = RunStore(tmp_path / "home")
    model_config = parse_workflow_model_config({
        "model": {"provider": "openrouter", "default": "openai/gpt-5.4"}
    })
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=classify_execution_runtime(
            provider="openrouter",
            model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        ),
        model_config_snapshot=model_config,
        provider_authority_environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=True,
        ),
    )
    connector_capabilities = SimpleNamespace(
        ready_services=frozenset(package.definition.options["requires"]),
        available_tools=frozenset(
            tool
            for item in iter_scoped_workflow_nodes(package.definition)
            for tool in item.node.options.get("allowed_tools", ())
        ),
        fingerprint="a" * 64,
        scoped_fingerprint=lambda *_: "b" * 64,
    )
    monkeypatch.setattr(
        "plugins.workflow.scheduler.connector_capability_snapshot",
        lambda: connector_capabilities,
    )
    prepared = store.prepare_run_snapshot(
        package,
        compilation=compilation,
        provider_authority=execution_context.provider_authority(package),
        connector_capabilities=connector_capabilities,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="one-manifest-attempt",
            concurrency_key=package.definition.name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    calls = 0

    class EligibleFailure:
        def execute(self, _context):
            nonlocal calls
            calls += 1
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                metadata={"provider_attempts": 0, "provider_attempts_exact": True},
            )

    scheduler = RunScheduler(store, utcnow=lambda: now, jitter=lambda: 0.5)
    scheduler.executors["prompt"] = EligibleFailure()

    first = scheduler.advance(admitted.run_id)
    now += timedelta(minutes=1)
    second = scheduler.advance(admitted.run_id)

    assert calls == 1
    assert first["status"] == second["status"] == "failed"
    retry = first["nodes"]["fetch-ticket-manifest"]["attempts"][0]["metadata"]
    assert retry["requested_retries"] == 0
    assert retry["requested_total_attempts"] == 1
    assert retry["effective_total_attempts"] == 1


@pytest.mark.parametrize(
    ("tickets", "accepted"),
    [
        pytest.param([], True, id="empty"),
        pytest.param(["ERIC-1"], True, id="one"),
        pytest.param([f"ERIC-{index}" for index in range(1, 26)], True, id="twenty-five"),
        pytest.param(["ERIC-1", "ERIC-1"], False, id="duplicate"),
        pytest.param([f"ERIC-{index}" for index in range(1, 27)], False, id="over-limit"),
    ],
)
def test_manifest_schema_accepts_only_empty_or_unique_ordered_bounded_keys(
    tickets: list[str], accepted: bool
) -> None:
    request = _request(_compile(), "fetch-ticket-manifest")
    payload = {
        "status": "empty" if not tickets else "ready",
        "count": len(tickets),
        "tickets": [{"key": key} for key in tickets],
        "warnings": [],
    }

    if accepted:
        assert list(_validate(request, payload)["tickets"]) == payload["tickets"]
    else:
        with pytest.raises(StructuredOutputError):
            _validate(request, payload)


def test_manifest_schema_rejects_malformed_results() -> None:
    request = _request(_compile(), "fetch-ticket-manifest")
    with pytest.raises(StructuredOutputError):
        _validate(request, {"status": "ready", "tickets": [{"key": "ERIC-1"}]})


@pytest.mark.parametrize("outcome", EXPECTED_OUTCOMES)
def test_expected_ticket_outcomes_are_successful_bounded_terminal_records(
    outcome: str,
) -> None:
    compilation = _compile()
    request = _request(
        compilation,
        "process-ticket-manifest/publish-ticket-record",
    )
    record = {
        "ticket_key": "ERIC-1",
        "outcome": outcome,
        "status": "terminal",
        "project_path": None,
        "branch_name": None,
        "commit_id": None,
        "merge_request_url": None,
        "jira_comment_id": None,
        "warnings": [],
        "attention_needed": outcome in {"permission", "manual_review"},
        "reconciliation_status": "not_required",
    }

    assert _validate(request, record)["outcome"] == outcome


def test_each_outward_write_has_its_exact_current_approval_and_closed_failure_schema() -> None:
    compilation = _compile()
    package = compilation.package
    scoped = {
        item.semantic_id: item.node
        for item in iter_scoped_workflow_nodes(package.definition)
    }
    outward = set(package.sidecar["outward_action_nodes"])

    assert outward == {
        f"process-ticket-manifest/{node_id}" for node_id in WRITE_APPROVALS
    }
    assert package.sidecar["outward_action_policy"] == "approval_required"
    for write_id, approval_id in WRITE_APPROVALS.items():
        semantic_id = f"process-ticket-manifest/{write_id}"
        node = scoped[semantic_id]
        assert f"process-ticket-manifest/{approval_id}" in {
            f"process-ticket-manifest/{dependency}"
            for dependency in node.depends_on
        }
        assert node.options["retry"]["max_attempts"] == 1
        request = _request(compilation, semantic_id)
        with pytest.raises(StructuredOutputError):
            _validate(request, {"status": "ambiguous"})


def test_each_approval_and_write_is_conditioned_on_its_exact_plan_flag() -> None:
    compilation = _compile()
    scoped = {
        item.semantic_id: item.node
        for item in iter_scoped_workflow_nodes(compilation.package.definition)
    }
    expected = {
        "approve-branch": "$prepare-writes.output.should_create_branch == 1",
        "create-branch": "$prepare-writes.output.should_create_branch == 1",
        "approve-commit": (
            "$prepare-writes.output.should_commit == 1 && "
            "$create-branch.output.status == 'success'"
        ),
        "commit-changes": (
            "$prepare-writes.output.should_commit == 1 && "
            "$create-branch.output.status == 'success'"
        ),
        "approve-merge-request": (
            "$prepare-writes.output.should_create_merge_request == 1 && "
            "$commit-changes.output.status == 'success'"
        ),
        "create-merge-request": (
            "$prepare-writes.output.should_create_merge_request == 1 && "
            "$commit-changes.output.status == 'success'"
        ),
        "review-merge-request": (
            "$prepare-writes.output.should_create_merge_request == 1 && "
            "$create-merge-request.output.status == 'success'"
        ),
        "approve-jira-comment": (
            "$prepare-writes.output.should_comment_jira == 1 && "
            "$review-merge-request.output.status == 'success'"
        ),
        "update-jira": (
            "$prepare-writes.output.should_comment_jira == 1 && "
            "$review-merge-request.output.status == 'success'"
        ),
    }

    for node_id, condition in expected.items():
        assert scoped[f"process-ticket-manifest/{node_id}"].options["when"] == condition
    terminal = scoped["process-ticket-manifest/publish-ticket-record"]
    assert terminal.options["trigger_rule"] == "none_failed_min_one_success"
    assert set(terminal.depends_on) == {
        "prepare-writes",
        "create-branch",
        "commit-changes",
        "create-merge-request",
        "review-merge-request",
        "update-jira",
    }


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
@pytest.mark.parametrize("outcome", EXPECTED_OUTCOMES)
def test_nonwrite_outcomes_finish_without_approvals_or_write_outputs(
    tmp_path: Path, outcome: str
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    body = {node.id: node for node in group.value["nodes"]}
    plan = {
        "ticket_key": "ERIC-1",
        "manifest_count": 1,
        "project_path": None,
        "branch_name": None,
        "branch_args": {
            "project": "unused",
            "prefix": "unused",
            "ticket_key": "ERIC-1",
            "summary": "unused",
            "source_ref": "unused",
            "dry_run": False,
        },
        "commit_args": {
            "project": "unused",
            "branch": "unused",
            "commit_message": "unused",
            "actions": [
                {"action": "create", "file_path": "unused", "content": ""}
            ],
            "dry_run": False,
        },
        "merge_request_args": {
            "project": "unused",
            "source_branch": "unused",
            "target_branch": "unused",
            "title": "unused",
            "description": "",
            "remove_source_branch": False,
            "squash": False,
            "dry_run": False,
        },
        "jira_comment_args": {
            "key": "ERIC-1",
            "body": "unused",
            "dry_run": False,
        },
        "should_create_branch": 0,
        "should_commit": 0,
        "should_create_merge_request": 0,
        "should_comment_jira": 0,
        "branch_intent_digest": "a" * 64,
        "commit_intent_digest": "b" * 64,
        "merge_request_intent_digest": "c" * 64,
        "jira_comment_intent_digest": "d" * 64,
        "terminal_outcome": outcome,
        "warnings": [],
    }
    approval_condition = body["approve-branch"].options["when"]
    assert not evaluate_v3_condition(
        approval_condition, {"prepare-writes": _resolved("prepare-writes", plan)}
    )
    assert evaluate_trigger_rule(
        "none_failed_min_one_success",
        ["succeeded", *("skipped" for _ in range(4))],
    )

    result, text = _run_script(
        tmp_path,
        body["publish-ticket-record"],
        outputs={"prepare-writes": plan},
        predecessor_results=_terminal_predecessors(plan),
    )

    assert result.status == "succeeded"
    record = json.loads(text)
    assert record["outcome"] == outcome
    assert record["status"] == "terminal"
    assert record["commit_id"] is None
    assert record["merge_request_url"] is None


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_terminal_reducer_publishes_corroborated_write_identities_and_warnings(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    terminal = next(
        node for node in group.value["nodes"] if node.id == "publish-ticket-record"
    )
    plan = _terminal_plan(warnings=["plan warning"])
    branch = _write_evidence(
        "ERIC-1", "create_branch", object_id="fix/ERIC-1", warnings=["branch warning"]
    )
    commit = _write_evidence(
        "ERIC-1", "commit_changes", object_id="deadbeef", warnings=["commit warning"]
    )
    merge_request_url = "https://gitlab.example/team/service/-/merge_requests/17"
    merge_request = _write_evidence(
        "ERIC-1",
        "create_merge_request",
        object_id="17",
        web_url=merge_request_url,
        warnings=["merge request warning"],
    )
    review = {
        "ticket_key": "ERIC-1",
        "operation": "review_merge_request",
        "status": "success",
        "merge_request_id": "17",
        "merge_request_url": merge_request_url,
        "warnings": ["review warning"],
        "attention_needed": True,
        "reconciliation_status": "confirmed",
    }
    jira = _write_evidence(
        "ERIC-1",
        "add_jira_comment",
        object_id="10001",
        warnings=["jira warning"],
    )

    result, text = _run_script(
        tmp_path,
        terminal,
        outputs={"prepare-writes": plan},
        predecessor_results=_terminal_predecessors(
            plan,
            create_branch=branch,
            commit_changes=commit,
            create_merge_request=merge_request,
            review_merge_request=review,
            update_jira=jira,
        ),
    )

    assert result.status == "succeeded"
    assert json.loads(text) == {
        "ticket_key": "ERIC-1",
        "outcome": "fixed",
        "status": "terminal",
        "project_path": "team/service",
        "branch_name": "fix/ERIC-1",
        "commit_id": "deadbeef",
        "merge_request_url": merge_request_url,
        "jira_comment_id": "10001",
        "warnings": [
            "plan warning",
            "branch warning",
            "commit warning",
            "merge request warning",
            "review warning",
            "jira warning",
        ],
        "attention_needed": True,
        "reconciliation_status": "confirmed",
    }


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
@pytest.mark.parametrize(
    "case",
    (
        "missing_write",
        "failed_write_state",
        "failed_write_status",
        "ambiguous_write",
        "wrong_write_ticket",
        "wrong_write_operation",
        "missing_write_identity",
        "identity_mismatch",
        "url_mismatch",
        "missing_review",
    ),
)
def test_terminal_reducer_fails_closed_without_agreeing_merge_request_evidence(
    tmp_path: Path, case: str
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    terminal = next(
        node for node in group.value["nodes"] if node.id == "publish-ticket-record"
    )
    plan = _terminal_plan(jira=0)
    url = "https://gitlab.example/team/service/-/merge_requests/17"
    write = _write_evidence(
        "ERIC-1", "create_merge_request", object_id="17", web_url=url
    )
    review = {
        "ticket_key": "ERIC-1",
        "operation": "review_merge_request",
        "status": "success",
        "merge_request_id": "17",
        "merge_request_url": url,
        "warnings": [],
        "attention_needed": False,
        "reconciliation_status": "confirmed",
    }
    predecessors = _terminal_predecessors(
        plan,
        create_branch=_write_evidence(
            "ERIC-1", "create_branch", object_id="fix/ERIC-1"
        ),
        commit_changes=_write_evidence(
            "ERIC-1", "commit_changes", object_id="deadbeef"
        ),
        create_merge_request=write,
        review_merge_request=review,
    )
    if case == "missing_write":
        predecessors["create-merge-request"] = {"state": "skipped"}
    elif case == "failed_write_state":
        predecessors["create-merge-request"] = {"state": "failed"}
    elif case == "failed_write_status":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "status": "failed"},
        )
    elif case == "ambiguous_write":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "reconciliation_status": "failed_closed"},
        )
    elif case == "wrong_write_ticket":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "ticket_key": "ERIC-2"},
        )
    elif case == "wrong_write_operation":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "operation": "commit_changes"},
        )
    elif case == "missing_write_identity":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "object_id": None},
        )
    elif case == "identity_mismatch":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "object_id": "18"},
        )
    elif case == "url_mismatch":
        _set_terminal_predecessor(
            predecessors,
            "create-merge-request",
            {**write, "web_url": f"{url}-other"},
        )
    else:
        predecessors["review-merge-request"] = {"state": "skipped"}

    result, _text = _run_script(
        tmp_path,
        terminal,
        outputs={"prepare-writes": plan},
        predecessor_results=predecessors,
    )

    assert result.status == "failed"
    assert result.error_code == "process_exit"


def test_v6_script_predecessors_receive_authenticated_state_and_typed_output() -> None:
    succeeded = _resolved(
        "create-branch",
        _write_evidence("ERIC-1", "create_branch", object_id="fix/ERIC-1"),
    )

    results = RunScheduler._predecessor_results(
        {
            "nodes": {
                "create-branch": {"state": "succeeded"},
                "commit-changes": {"state": "skipped"},
            }
        },
        ("create-branch", "commit-changes"),
        {"create-branch": succeeded},
        include_output_values=True,
    )

    assert results["create-branch"]["state"] == "succeeded"
    assert results["create-branch"]["output_evidence"] == (
        resolved_output_publication_identity(succeeded)
    )
    assert results["create-branch"]["output"] == succeeded.value
    assert results["commit-changes"] == {"state": "skipped"}


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_terminal_reducer_accepts_a_corroborated_partial_write(tmp_path: Path) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    terminal = next(
        node for node in group.value["nodes"] if node.id == "publish-ticket-record"
    )
    plan = _terminal_plan(commit=0, merge_request=0, jira=0)
    branch = _write_evidence(
        "ERIC-1", "create_branch", object_id="fix/ERIC-1", warnings=["created"]
    )

    result, text = _run_script(
        tmp_path,
        terminal,
        outputs={"prepare-writes": plan},
        predecessor_results=_terminal_predecessors(plan, create_branch=branch),
    )

    assert result.status == "succeeded"
    record = json.loads(text)
    assert record["outcome"] == "fixed"
    assert record["branch_name"] == "fix/ERIC-1"
    assert record["commit_id"] is None
    assert record["merge_request_url"] is None
    assert record["jira_comment_id"] is None
    assert record["warnings"] == ["created"]
    assert record["reconciliation_status"] == "not_required"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
@pytest.mark.parametrize(
    "case",
    (
        "missing_enabled",
        "partial_write_chain",
        "failed_state",
        "permission_status",
        "failed_status",
        "wrong_ticket",
        "wrong_operation",
        "failed_reconciliation",
    ),
)
def test_terminal_reducer_fails_closed_on_uncorroborated_write_evidence(
    tmp_path: Path, case: str
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    terminal = next(
        node for node in group.value["nodes"] if node.id == "publish-ticket-record"
    )
    plan = (
        _terminal_plan()
        if case == "partial_write_chain"
        else _terminal_plan(commit=0, merge_request=0, jira=0)
    )
    branch = _write_evidence("ERIC-1", "create_branch", object_id="fix/ERIC-1")
    predecessors = _terminal_predecessors(
        plan,
        create_branch=branch,
        commit_changes=(
            _write_evidence("ERIC-1", "commit_changes", object_id="deadbeef")
            if case == "partial_write_chain"
            else None
        ),
    )
    if case == "missing_enabled":
        predecessors["create-branch"] = {"state": "skipped"}
    elif case == "partial_write_chain":
        pass
    elif case == "failed_state":
        predecessors["create-branch"] = {"state": "failed"}
    elif case == "permission_status":
        _set_terminal_predecessor(
            predecessors,
            "create-branch",
            {**branch, "status": "permission"},
        )
    elif case == "failed_status":
        _set_terminal_predecessor(
            predecessors,
            "create-branch",
            {**branch, "status": "failed"},
        )
    elif case == "wrong_ticket":
        _set_terminal_predecessor(
            predecessors,
            "create-branch",
            {**branch, "ticket_key": "ERIC-2"},
        )
    elif case == "wrong_operation":
        _set_terminal_predecessor(
            predecessors,
            "create-branch",
            {**branch, "operation": "commit_changes"},
        )
    else:
        _set_terminal_predecessor(
            predecessors,
            "create-branch",
            {**branch, "reconciliation_status": "failed_closed"},
        )

    result, _text = _run_script(
        tmp_path,
        terminal,
        outputs={"prepare-writes": plan},
        predecessor_results=predecessors,
    )

    assert result.status == "failed"
    assert result.error_code == "process_exit"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_terminal_reducer_bounds_ordered_warning_aggregation(tmp_path: Path) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    terminal = next(
        node for node in group.value["nodes"] if node.id == "publish-ticket-record"
    )
    plan_warnings = [f"plan-{index}" for index in range(25)]
    branch_warnings = [f"branch-{index}" for index in range(25)]
    commit_warnings = [f"commit-{index}" for index in range(25)]
    plan = _terminal_plan(
        merge_request=0,
        jira=0,
        warnings=plan_warnings,
    )
    branch = _write_evidence(
        "ERIC-1",
        "create_branch",
        object_id="fix/ERIC-1",
        warnings=branch_warnings,
    )
    commit = _write_evidence(
        "ERIC-1",
        "commit_changes",
        object_id="deadbeef",
        warnings=commit_warnings,
    )

    result, text = _run_script(
        tmp_path,
        terminal,
        outputs={"prepare-writes": plan},
        predecessor_results=_terminal_predecessors(
            plan,
            create_branch=branch,
            commit_changes=commit,
        ),
    )

    assert result.status == "succeeded"
    assert json.loads(text)["warnings"] == plan_warnings + branch_warnings


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_final_json_and_markdown_preserve_full_terminal_write_evidence(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    nodes = {node.id: node for node in compilation.package.definition.nodes}
    record = {
        "ticket_key": "ERIC-1",
        "outcome": "fixed",
        "status": "terminal",
        "project_path": "team/service",
        "branch_name": "fix/ERIC-1",
        "commit_id": "deadbeef",
        "merge_request_url": "https://gitlab.example/team/service/-/merge_requests/17",
        "jira_comment_id": "10001",
        "warnings": ["review warning"],
        "attention_needed": True,
        "reconciliation_status": "confirmed",
    }
    manifest = {
        "status": "ready",
        "count": 1,
        "tickets": [{"key": "ERIC-1"}],
        "warnings": [],
    }
    aggregate = {
        "manifest_count": 1,
        "completed_count": 1,
        "records": [record],
        "counts": {
            "fixed": 1,
            "not_found": 0,
            "permission": 0,
            "needs_info": 0,
            "manual_review": 0,
            "not_a_code_fix": 0,
            "safely_skipped": 0,
        },
        "warnings": ["review warning"],
        "completion_marker": "",
    }

    json_result, json_text = _run_script(
        tmp_path,
        nodes["publish-aggregate-json"],
        outputs={
            "fetch-ticket-manifest": manifest,
            "process-ticket-manifest": aggregate,
        },
    )
    assert json_result.status == "succeeded"
    published = json.loads(json_text)
    assert published["records"] == [record]

    markdown_result, markdown = _run_script(
        tmp_path,
        nodes["publish-aggregate-markdown"],
        outputs={"publish-aggregate-json": published},
    )
    assert markdown_result.status == "succeeded"
    for value in (
        "team/service",
        "fix/ERIC-1",
        "deadbeef",
        "https://gitlab.example/team/service/-/merge_requests/17",
        "10001",
        "confirmed",
        "review warning",
    ):
        assert value in markdown


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_top_level_artifact_free_aggregate_ignores_unchanged_group_artifacts(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    nodes = {node.id: node for node in compilation.package.definition.nodes}
    manifest = {
        "status": "ready",
        "count": 1,
        "tickets": [{"key": "ERIC-1"}],
        "warnings": [],
    }
    record = {
        "ticket_key": "ERIC-1",
        "outcome": "needs_info",
        "status": "terminal",
        "project_path": None,
        "branch_name": None,
        "commit_id": None,
        "merge_request_url": None,
        "jira_comment_id": None,
        "warnings": [],
        "attention_needed": False,
        "reconciliation_status": "not_required",
    }
    aggregate = {
        "manifest_count": 1,
        "completed_count": 1,
        "records": [record],
        "counts": {
            "fixed": 0,
            "not_found": 0,
            "permission": 0,
            "needs_info": 1,
            "manual_review": 0,
            "not_a_code_fix": 0,
            "safely_skipped": 0,
        },
        "warnings": [],
        "completion_marker": "",
    }
    relative = (
        "loop-groups/process-ticket-manifest/iterations/0001/"
        "record-cumulative-state/output.json"
    )
    group_output = json.dumps(aggregate, sort_keys=True)

    result, text = _run_script(
        tmp_path,
        nodes["publish-aggregate-json"],
        outputs={
            "fetch-ticket-manifest": manifest,
            "process-ticket-manifest": aggregate,
        },
        preexisting_artifacts={relative: group_output},
    )

    assert result.status == "succeeded"
    assert json.loads(text)["records"] == [record]
    run_directory = next(tmp_path.glob("run-publish-aggregate-json-*"))
    assert (run_directory / "artifacts" / relative).read_text(
        encoding="utf-8"
    ) == group_output


def test_write_plan_preserves_exact_connector_arguments_and_prompts_use_them() -> None:
    compilation = _compile()
    scoped = {
        item.semantic_id: item.node
        for item in iter_scoped_workflow_nodes(compilation.package.definition)
    }
    prepare = compilation.package.language.structured_outputs[
        "process-ticket-manifest/prepare-writes"
    ].canonical_schema
    required = set(prepare["required"])

    assert {
        "branch_args",
        "commit_args",
        "merge_request_args",
        "jira_comment_args",
    } <= required
    action = prepare["properties"]["commit_args"]["properties"]["actions"][
        "items"
    ]
    assert {"action", "file_path", "content", "last_commit_id"} <= set(
        action["properties"]
    )
    assert "content_digest" not in action["properties"]

    for node_id, args_field in (
        ("create-branch", "branch_args"),
        ("commit-changes", "commit_args"),
        ("create-merge-request", "merge_request_args"),
        ("update-jira", "jira_comment_args"),
    ):
        prompt = str(scoped[f"process-ticket-manifest/{node_id}"].value)
        assert f"$prepare-writes.output.{args_field}" in prompt


def test_terminal_and_aggregate_schemas_retain_full_records_and_close_counts() -> None:
    compilation = _compile()
    record_schema = compilation.package.language.structured_outputs[
        "process-ticket-manifest/publish-ticket-record"
    ].canonical_schema
    aggregate_schema = compilation.package.language.structured_outputs[
        "publish-aggregate-json"
    ].canonical_schema
    cumulative_schema = compilation.package.language.structured_outputs[
        "process-ticket-manifest/record-cumulative-state"
    ].canonical_schema

    assert set(record_schema["required"]) == set(
        aggregate_schema["properties"]["records"]["items"]["required"]
    )
    assert aggregate_schema["properties"]["records"]["uniqueItems"] is True
    assert set(aggregate_schema["properties"]["counts"]["required"]) == {
        "fixed",
        *EXPECTED_OUTCOMES,
    }
    assert set(cumulative_schema["required"]) == {
        "manifest_count",
        "completed_count",
        "records",
        "counts",
        "warnings",
        "completion_marker",
    }
    cumulative_records = cumulative_schema["properties"]["records"]
    assert cumulative_records["minItems"] == 1
    assert cumulative_records["maxItems"] == 25
    assert cumulative_records["uniqueItems"] is True
    assert set(cumulative_records["items"]["required"]) == set(
        record_schema["required"]
    )
    assert set(cumulative_schema["properties"]["completion_marker"]["enum"]) == {
        "",
        "<promise>BATCH_COMPLETE</promise>",
    }


def test_marker_stripping_leaves_valid_ordered_json() -> None:
    payload = {
        "manifest_count": 2,
        "completed_count": 2,
        "records": [
            {"ticket_key": "ERIC-2", "outcome": "needs_info"},
            {"ticket_key": "ERIC-1", "outcome": "manual_review"},
        ],
        "counts": {"needs_info": 1, "manual_review": 1},
    }
    completed, cleaned = clean_loop_completion(
        json.dumps(payload, separators=(",", ":"))
        + "\n<promise>BATCH_COMPLETE</promise>",
        "BATCH_COMPLETE",
    )

    assert completed is True
    assert json.loads(cleaned) == payload


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_real_reducers_select_and_record_exactly_one_manifest_key_in_order(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    body = {node.id: node for node in group.value["nodes"]}
    manifest = {
        "status": "ready",
        "count": 2,
        "tickets": [{"key": "ERIC-2"}, {"key": "ERIC-1"}],
        "warnings": [],
    }

    first_result, first_text = _run_script(
        tmp_path,
        body["select-ticket"],
        outputs={"fetch-ticket-manifest": manifest},
        previous={"record-cumulative-state": None},
        extra_dependencies=("fetch-ticket-manifest",),
    )
    assert first_result.status == "succeeded"
    assert json.loads(first_text)["ticket_key"] == "ERIC-2"

    first_record = {
        "ticket_key": "ERIC-2",
        "outcome": "needs_info",
        "status": "terminal",
        "project_path": None,
        "branch_name": None,
        "commit_id": None,
        "merge_request_url": None,
        "jira_comment_id": None,
        "warnings": ["waiting"],
        "attention_needed": False,
        "reconciliation_status": "not_required",
    }
    record_result, record_text = _run_script(
        tmp_path,
        body["record-cumulative-state"],
        outputs={
            "select-ticket": {
                "ticket_key": "ERIC-2",
                "manifest_count": 2,
                "index": 0,
            },
            "publish-ticket-record": first_record,
        },
        previous={"record-cumulative-state": None},
    )
    assert record_result.status == "succeeded"
    completed, cleaned = clean_loop_completion(record_text, "BATCH_COMPLETE")
    assert completed is False
    assert json.loads(cleaned)["records"] == [first_record]

    second_result, second_text = _run_script(
        tmp_path,
        body["select-ticket"],
        outputs={"fetch-ticket-manifest": manifest},
        previous={"record-cumulative-state": json.loads(cleaned)},
        extra_dependencies=("fetch-ticket-manifest",),
    )
    assert second_result.status == "succeeded"
    assert json.loads(second_text)["ticket_key"] == "ERIC-1"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_real_reducers_reject_schema_valid_order_and_count_mismatches(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    nodes = {node.id: node for node in compilation.package.definition.nodes}
    group = nodes["process-ticket-manifest"]
    body = {node.id: node for node in group.value["nodes"]}
    records = [
        {
            "ticket_key": key,
            "outcome": "safely_skipped",
            "status": "terminal",
            "project_path": None,
            "branch_name": None,
            "commit_id": None,
            "merge_request_url": None,
            "jira_comment_id": None,
            "warnings": [],
            "attention_needed": False,
            "reconciliation_status": "not_required",
        }
        for key in ("ERIC-1", "ERIC-2")
    ]
    aggregate = {
        "manifest_count": 2,
        "completed_count": 2,
        "records": records,
        "counts": {
            "fixed": 0,
            "not_found": 0,
            "permission": 0,
            "needs_info": 0,
            "manual_review": 0,
            "not_a_code_fix": 0,
            "safely_skipped": 2,
        },
        "warnings": [],
    }
    result, _text = _run_script(
        tmp_path,
        nodes["publish-aggregate-json"],
        outputs={
            "fetch-ticket-manifest": {
                "status": "ready",
                "count": 2,
                "tickets": [{"key": "ERIC-2"}, {"key": "ERIC-1"}],
                "warnings": [],
            },
            "process-ticket-manifest": aggregate,
        },
    )
    assert result.status == "failed"
    assert result.error_code == "process_exit"

    wrong_key, _text = _run_script(
        tmp_path,
        body["record-cumulative-state"],
        outputs={
            "select-ticket": {
                "ticket_key": "ERIC-2",
                "manifest_count": 2,
                "index": 0,
            },
            "publish-ticket-record": records[0],
        },
        previous={"record-cumulative-state": None},
    )
    assert wrong_key.status == "failed"
    assert wrong_key.error_code == "process_exit"

    wrong_count, _text = _run_script(
        tmp_path,
        body["record-cumulative-state"],
        outputs={
            "select-ticket": {
                "ticket_key": "ERIC-2",
                "manifest_count": 2,
                "index": 1,
            },
            "publish-ticket-record": records[1],
        },
        previous={
            "record-cumulative-state": {
                "manifest_count": 2,
                "completed_count": 1,
                "records": [records[0]],
                "counts": {
                    "fixed": 0,
                    "not_found": 0,
                    "permission": 0,
                    "needs_info": 0,
                    "manual_review": 0,
                    "not_a_code_fix": 0,
                    "safely_skipped": 0,
                },
                "warnings": [],
                "completion_marker": "",
            }
        },
    )
    assert wrong_count.status == "failed"
    assert wrong_count.error_code == "process_exit"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_cumulative_sink_schema_rejects_adversarial_full_record_and_marker_outputs(
    tmp_path: Path,
) -> None:
    compilation = _compile()
    group = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "process-ticket-manifest"
    )
    sink = next(node for node in group.value["nodes"] if node.id == "record-cumulative-state")
    record = {
        "ticket_key": "ERIC-1",
        "outcome": "safely_skipped",
        "status": "terminal",
        "project_path": None,
        "branch_name": None,
        "commit_id": None,
        "merge_request_url": None,
        "jira_comment_id": None,
        "warnings": [],
        "attention_needed": False,
        "reconciliation_status": "not_required",
    }
    counts = {
        "fixed": 0,
        "not_found": 0,
        "permission": 0,
        "needs_info": 0,
        "manual_review": 0,
        "not_a_code_fix": 0,
        "safely_skipped": 1,
    }
    valid = {
        "manifest_count": 1,
        "completed_count": 1,
        "records": [record],
        "counts": counts,
        "warnings": [],
        "completion_marker": "",
    }
    missing_full_field = json.loads(json.dumps(valid))
    del missing_full_field["records"][0]["status"]
    duplicate = json.loads(json.dumps(valid))
    duplicate["manifest_count"] = 2
    duplicate["completed_count"] = 2
    duplicate["records"] = [record, record]
    duplicate["counts"]["safely_skipped"] = 2
    invalid_marker = {**valid, "completion_marker": "BATCH_COMPLETE"}

    for index, payload in enumerate(
        (missing_full_field, duplicate, invalid_marker, {})
    ):
        rendered = json.dumps(payload, separators=(",", ":"))
        adversarial = replace(
            sink,
            value=f"console.log({json.dumps(rendered)})",
            depends_on=(),
        )
        result, _text = _run_script(tmp_path, adversarial, outputs={})

        assert result.status == "failed", index
        assert result.error_code == "structured_output_invalid", index


def test_workflow_declares_only_existing_jira_gitlab_tools_and_typed_history() -> None:
    compilation = _compile()
    scoped = tuple(iter_scoped_workflow_nodes(compilation.package.definition))
    tools = {
        tool
        for item in scoped
        for tool in item.node.options.get("allowed_tools", ())
    }
    output_types = {
        item.node.options.get("output_type")
        for item in scoped
        if item.node.options.get("output_type")
    }

    assert tools == {
        "jira_my_tickets",
        "jira_get_issue",
        "jira_add_comment",
        "gitlab_resolve_project",
        "gitlab_list_repository_tree",
        "gitlab_read_file",
        "gitlab_create_branch",
        "gitlab_commit_changes",
        "gitlab_create_merge_request",
        "gitlab_read_merge_request",
    }
    assert output_types == {
        "JiraDefectTicketRecord",
        "JiraDefectAggregateJson",
        "JiraDefectAggregateMarkdown",
    }
    assert not any(
        token in tool.lower()
        for tool in tools
        for token in ("email", "outlook", "spreadsheet", "excel")
    )
