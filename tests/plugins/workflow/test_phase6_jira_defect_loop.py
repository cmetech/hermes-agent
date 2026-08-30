from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from agent.structured_output import (
    StructuredOutputError,
    StructuredOutputRequest,
    StructuredOutputStrategy,
    normalize_schema,
    parse_validate_canonicalize,
)
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.loop import clean_loop_completion
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    verify_language_snapshot,
)
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.output_resolution import ResolvedNodeOutput
from plugins.workflow.resources import VariableContext
from plugins.workflow.schema import parse_workflow_source_bytes
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
        items_path: items
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
            "items_path": "items",
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
        items_path: items
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
    )


def _run_script(
    tmp_path: Path,
    node,
    *,
    outputs: dict[str, object],
    previous: dict[str, object | None] | None = None,
    extra_dependencies: tuple[str, ...] = (),
):
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
    run_directory = tmp_path / f"run-{node.id}-{len(list(tmp_path.iterdir()))}"
    run_directory.mkdir()
    result = ScriptExecutor().execute(
        NodeExecutionContext(
            run_id="reducer-test",
            run_directory=run_directory,
            node=runtime_node,
            attempt_id="attempt",
            variable_context=variables,
            output_resolver=variables.output_reference,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=6,
            max_artifact_bytes=0,
        )
    )
    text = ""
    if result.artifacts:
        text = (run_directory / result.artifacts[0].relative_path).read_text()
    return result, text


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
            "items_path": "items",
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
    assert fetch.options["retry"]["max_attempts"] == 1
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

    assert set(record_schema["required"]) == set(
        aggregate_schema["properties"]["records"]["items"]["required"]
    )
    assert aggregate_schema["properties"]["records"]["uniqueItems"] is True
    assert set(aggregate_schema["properties"]["counts"]["required"]) == {
        "fixed",
        *EXPECTED_OUTCOMES,
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
