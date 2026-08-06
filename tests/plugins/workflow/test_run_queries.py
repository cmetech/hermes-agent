from __future__ import annotations

import pytest

from plugins.workflow.actions import MUTATION_ACTIONS, available_actions, mutation_is_valid
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore


def _start(store: RunStore, package, key: str):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )


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


def test_run_pagination_filters_before_limit_and_keyset_has_no_gaps(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=20,
        max_nonterminal_runs=20,
        max_start_requests_per_minute=20,
    )
    package = load_workflow(workflow_writer(tmp_path / "package", name="keyset"))
    history = _start(store, package, "history")
    RunScheduler(store).advance(history.run_id)
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET updated_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
            (history.run_id,),
        )
    board_ids = [_start(store, package, f"board-{index}").run_id for index in range(6)]

    history_page = store.list_runs(view="history", limit=2)
    first = store.list_runs(view="board", limit=3)
    cursor = (first[-1]["updated_at"], first[-1]["run_id"])
    second = store.list_runs(view="board", limit=3, after=cursor)

    assert [run["run_id"] for run in history_page] == [history.run_id]
    traversed = [run["run_id"] for run in (*first, *second)]
    assert len(traversed) == len(set(traversed)) == 6
    assert set(traversed) == set(board_ids)


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


@pytest.mark.parametrize(
    ("status", "interaction", "health", "expected"),
    [
        ("running", None, "healthy", {"cancel"}),
        ("running", None, "stalled", {"cancel", "resume"}),
        ("queued", None, "waiting", {"cancel"}),
        ("waiting_retry", None, "retry_wait", {"cancel"}),
        ("recovery_pending", None, "operator_wait", {"resume", "cancel"}),
        (
            "paused",
            {"type": "approval"},
            "user_wait",
            {"approve", "reject", "cancel"},
        ),
        (
            "paused",
            {"type": "loop_input"},
            "user_wait",
            {"provide-input", "cancel"},
        ),
        (
            "paused",
            {
                "type": "loop_signal_confirmation",
                "interaction_id": "a" * 64,
                "message": "Accept or refine",
                "iteration": 1,
                "max_iterations": 2,
                "result_artifact": "nodes/refine/attempt/output.txt",
                "result_sha256": "b" * 64,
            },
            "user_wait",
            {"approve", "provide-input", "cancel"},
        ),
        (
            "paused",
            {
                "type": "loop_signal_confirmation",
                "interaction_id": "a" * 64,
                "message": "Accept final result",
                "iteration": 2,
                "max_iterations": 2,
                "result_artifact": "nodes/refine/attempt/output.txt",
                "result_sha256": "b" * 64,
            },
            "user_wait",
            {"approve", "cancel"},
        ),
        (
            "paused",
            {"type": "reconcile"},
            "user_wait",
            {"reconcile", "cancel"},
        ),
        ("interrupted", None, "interrupted", {"resume", "retry", "abandon"}),
        ("failed", None, "terminal", {"resume", "retry", "abandon"}),
        ("succeeded", None, "terminal", {"archive"}),
    ],
)
def test_action_table_advertises_exactly_valid_mutations(
    status, interaction, health, expected
) -> None:
    advertised = available_actions(status, interaction, health=health)
    advertised_mutations = set(advertised) & MUTATION_ACTIONS
    assert advertised_mutations == expected
    assert {
        action
        for action in MUTATION_ACTIONS
        if mutation_is_valid(
            action,
            status=status,
            pending_interaction=interaction,
            health=health,
        )
    } == expected


def test_archived_terminal_run_advertises_restore_only() -> None:
    advertised = available_actions(
        "succeeded", None, health="terminal", archived=True
    )
    assert set(advertised) & MUTATION_ACTIONS == {"restore"}
    assert mutation_is_valid(
        "restore", status="succeeded", health="terminal", archived=True
    )
