from __future__ import annotations

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def test_run_summary_is_stable_redacted_and_filterable(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="query-demo"))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="query-demo",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="raw-secret-key",
            concurrency_key="query-demo",
        ),
        immutable_snapshot=prepared,
    )

    summary = store.get_run_status(admitted.run_id)
    listed = store.list_runs(workflow="query-demo", status="running", limit=1)

    assert listed == (summary,)
    assert summary["action"] == "status"
    assert summary["progress"] == {
        "kind": "graph",
        "completed_nodes": 0,
        "total_nodes": 1,
    }
    assert summary["idempotency_key_digest"] != "raw-secret-key"
    assert "raw-secret-key" not in str(summary)
    assert summary["health"] == "healthy"

    store.append_event(
        admitted.run_id,
        "diagnostic",
        {"message": "safe", "api_key": "secret-value", "password": "also-secret"},
    )
    tail = store.tail_events(admitted.run_id, limit=10)
    assert "secret-value" not in str(tail)
    assert "also-secret" not in str(tail)
    assert tail[-1]["payload"]["api_key"] == "[REDACTED]"


def test_conversation_scope_filters_lists_and_explicit_run_ids(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package", name="scoped"))

    def start(key, scope):
        prepared = store.prepare_run_snapshot(package)
        return store.start_run(
            RunAdmissionRequest(
                workflow_name="scoped",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="chat",
                idempotency_key=key,
                concurrency_key="scoped",
                concurrency_policy="allow",
                operator_scope=scope,
            ),
            immutable_snapshot=prepared,
        )

    first = start("first", "user-a/conversation-1")
    second = start("second", "user-b/conversation-2")

    listed = store.list_runs(operator_scope="user-a/conversation-1")
    assert [run["run_id"] for run in listed] == [first.run_id]
    assert "user-a/conversation-1" not in str(listed)
    with pytest.raises(KeyError):
        store.get_run_status(second.run_id, operator_scope="user-a/conversation-1")
    with pytest.raises(KeyError):
        store.tail_events(second.run_id, operator_scope="user-a/conversation-1")
    with pytest.raises(KeyError):
        store.cancel_run(second.run_id, operator_scope="user-a/conversation-1")
    assert (
        store.cancel_run(first.run_id, operator_scope="user-a/conversation-1")["status"]
        == "cancelled"
    )
