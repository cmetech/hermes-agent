from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    connection = kb.connect()
    yield connection
    connection.close()


def _snapshot(conn, task_id):
    task = kb.get_task(conn, task_id)
    latest = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    return task, latest


def test_matching_and_stale_status_preconditions(conn):
    task_id = kb.create_task(conn, title="CAS")
    task, event_id = _snapshot(conn, task_id)
    precondition = kb.TaskMutationPrecondition(
        expected_status=task.status,
        expected_current_run_id=None,
        expected_event_id=event_id,
    )
    assert kb.set_task_status(conn, task_id, "todo", precondition=precondition)

    with pytest.raises(kb.TaskMutationConflict) as caught:
        kb.set_task_status(conn, task_id, "ready", precondition=precondition)
    assert caught.value.current.task_id == task_id
    assert caught.value.current.status == "todo"


def test_assign_and_comment_are_append_only_under_preconditions(conn):
    task_id = kb.create_task(conn, title="Mutations")
    task, event_id = _snapshot(conn, task_id)
    precondition = kb.TaskMutationPrecondition(
        expected_status=task.status, expected_event_id=event_id
    )
    assert kb.assign_task(conn, task_id, "alice", precondition=precondition)
    with pytest.raises(kb.TaskMutationConflict):
        kb.add_comment(conn, task_id, "operator", "stale", precondition=precondition)
    assert kb.list_comments(conn, task_id) == []


def test_running_is_claim_only(conn):
    task_id = kb.create_task(conn, title="No direct running")
    with pytest.raises(ValueError, match="claim"):
        kb.set_task_status(conn, task_id, "running")
