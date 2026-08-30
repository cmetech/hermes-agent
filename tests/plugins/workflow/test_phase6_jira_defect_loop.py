from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.structured_output import (
    StructuredOutputError,
    StructuredOutputRequest,
    StructuredOutputStrategy,
    normalize_schema,
    parse_validate_canonicalize,
)
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.executors.loop import clean_loop_completion
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
