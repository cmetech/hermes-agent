from __future__ import annotations

import json

import pytest

from agent.plugin_agent_worker import _ToolCallAudit


CONTRACT = {
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


def _completed(audit: _ToolCallAudit, call_id: str, args: dict, result: object) -> None:
    audit.complete(call_id, "jira_my_tickets", args, json.dumps(result))


def test_exact_tool_call_contract_derives_first_occurrence_manifest() -> None:
    audit = _ToolCallAudit(CONTRACT, outward_action=False)
    args = {"max_results": 25}
    audit.start("one", "jira_my_tickets", args)
    _completed(
        audit,
        "one",
        args,
        {
            "items": [{"key": "ERIC-2"}, {"key": "ERIC-1"}, {"key": "ERIC-2"}],
            "private": "transient secret",
        },
    )

    evidence = audit.finalize(
        {"status": "ready", "count": 2, "tickets": [{"key": "ERIC-2"}, {"key": "ERIC-1"}]}
    )

    assert evidence == {
        "tool_call_contract_satisfied": True,
        "tool_call_count": 1,
    }
    assert "transient secret" not in repr(vars(audit))


def test_exact_tool_call_contract_bounds_projected_untrusted_values() -> None:
    audit = _ToolCallAudit(CONTRACT, outward_action=False)
    args = {"max_results": 25}
    audit.start("one", "jira_my_tickets", args)
    _completed(
        audit,
        "one",
        args,
        {"items": [{"key": "X" * 4097}]},
    )

    with pytest.raises(ValueError, match="tool_call_contract_violation"):
        audit.finalize(
            {
                "status": "ready",
                "count": 1,
                "tickets": [{"key": "X" * 4097}],
            }
        )


@pytest.mark.parametrize("mode", ["zero", "duplicate", "parallel", "wrong_args"])
def test_exact_tool_call_contract_rejects_every_non_exact_trace(mode: str) -> None:
    audit = _ToolCallAudit(CONTRACT, outward_action=False)
    exact = {"max_results": 25}
    if mode != "zero":
        args = {"max_results": 24} if mode == "wrong_args" else exact
        audit.start("one", "jira_my_tickets", args)
        if mode == "parallel":
            audit.start("two", "jira_my_tickets", exact)
        _completed(audit, "one", args, {"items": [{"key": "ERIC-1"}]})
        if mode == "duplicate":
            audit.start("two", "jira_my_tickets", exact)
            _completed(audit, "two", exact, {"items": [{"key": "ERIC-1"}]})
        elif mode == "parallel":
            _completed(audit, "two", exact, {"items": [{"key": "ERIC-2"}]})

    with pytest.raises(ValueError, match="tool_call_contract_violation"):
        audit.finalize(
            {"status": "ready", "count": 1, "tickets": [{"key": "ERIC-1"}]}
        )


@pytest.mark.parametrize(
    "output",
    [
        {"status": "ready", "count": 1, "tickets": [{"key": "OTHER-1"}]},
        {"status": "ready", "count": 2, "tickets": [{"key": "ERIC-1"}]},
        {"status": "empty", "count": 1, "tickets": [{"key": "ERIC-1"}]},
        {"status": "ready", "count": 1, "tickets": [{"key": "ERIC-1"}, {"key": "ERIC-1"}]},
    ],
)
def test_exact_tool_call_contract_rejects_schema_valid_manifest_lies(output: dict) -> None:
    audit = _ToolCallAudit(CONTRACT, outward_action=False)
    args = {"max_results": 25}
    audit.start("one", "jira_my_tickets", args)
    _completed(audit, "one", args, {"items": [{"key": "ERIC-1"}]})

    with pytest.raises(ValueError, match="tool_call_contract_violation"):
        audit.finalize(output)


def test_outward_tool_audit_marks_write_ambiguous_without_retaining_raw_result() -> None:
    audit = _ToolCallAudit(None, outward_action=True)
    audit.start("write", "gitlab_commit_changes", {"project": 1})
    audit.complete(
        "write",
        "gitlab_commit_changes",
        {"project": 1},
        json.dumps(
            {
                "category": "write_ambiguous",
                "detail": "secret result text that must not enter audit",
            }
        ),
    )

    assert audit.finalize({}) == {
        "write_ambiguous": True,
        "outcome_unknown": True,
        "reconciliation_required": True,
    }
