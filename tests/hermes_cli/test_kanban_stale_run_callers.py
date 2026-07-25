"""Callers of the ``expected_run_id`` mutations must handle a stale run.

``cc7df69aa`` changed the contract: a stale ``expected_run_id`` used to make
``complete_task`` / ``block_task`` / ``schedule_task`` / ``heartbeat_worker``
return falsy, and every caller had an ``if not ...:`` branch for that. It now
raises :class:`kanban_db.TaskMutationConflict` instead, which made those
branches unreachable for the stale case and left the CLI printing a raw Python
traceback.

The existing suite only covered ``kanban_db`` itself, which is exactly why the
caller gap survived. These tests sit at the caller boundary on purpose.

They drive a REAL database rather than mocking the mutation, so they exercise
the same raise path production hits: claim a task, kill the worker, let the
crash detector reclaim it, then act as the superseded run.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # detect_crashed_workers has a 30s grace window by default to prevent a
    # multi-dispatcher reap race. These tests claim and reclaim in the same
    # instant, so pin it to 0 -- the grace period itself is covered by
    # dedicated tests in test_kanban_db.py.
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def stale_run(kanban_home, monkeypatch):
    """A task whose current run has superseded this worker's run.

    Returns ``(task_id, superseded_run_id, current_run_id)`` and points the
    ``HERMES_KANBAN_*`` env vars at the SUPERSEDED run, which is what
    ``_worker_run_id_for`` / ``_worker_run_id`` read to build expected_run_id.
    """
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="stale caller guard", assignee="worker")

        kb.claim_task(conn, tid)
        first = kb.latest_run(conn, tid)

        # Crash the first worker so the task legitimately gets a second run.
        kb._set_worker_pid(conn, tid, 98765)
        monkeypatch.setattr(kb, "_pid_alive", lambda pid: False)
        assert kb.detect_crashed_workers(conn) == [tid]

        kb.claim_task(conn, tid)
        current = kb.latest_run(conn, tid)
        assert current.id != first.id
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(first.id))
    return tid, first.id, current.id


def _assert_stale_message(text: str, action: str, task_id: str) -> None:
    assert "Traceback" not in text, f"stale run must not surface a traceback:\n{text}"
    assert f"cannot {action} {task_id}" in text
    # Must be distinguishable from the generic "unknown id / terminal state"
    # message -- telling those apart is the entire point of the exception.
    assert "no longer the current run" in text
    assert "unknown id" not in text


# ---------------------------------------------------------------------------
# CLI — all four sites previously raised a raw traceback
# ---------------------------------------------------------------------------

def test_cli_complete_stale_run_exits_nonzero_without_traceback(stale_run, capsys):
    tid, _, _ = stale_run

    rc = kc._cmd_complete(
        argparse.Namespace(task_ids=[tid], summary="late", result=None, metadata=None)
    )

    assert rc == 1
    _assert_stale_message(capsys.readouterr().err, "complete", tid)


def test_cli_heartbeat_stale_run_reports_cleanly(stale_run, capsys):
    """heartbeat_worker is the ONE mutation that does not use preconditions.

    It resolves staleness with a conditional UPDATE (``AND current_run_id = ?``)
    and returns False, so it cannot raise TaskMutationConflict and its original
    ``if not ok:`` branch is still live and still correct. Pinned here because
    the asymmetry is easy to "tidy away" into the raise-based shape, and because
    the caller-side guard in _cmd_heartbeat is deliberately defensive rather
    than currently-reachable.
    """
    tid, _, _ = stale_run

    rc = kc._cmd_heartbeat(argparse.Namespace(task_id=tid, note=None))

    err = capsys.readouterr().err
    assert rc == 1
    assert "Traceback" not in err
    assert f"cannot heartbeat {tid}" in err


def test_cli_block_stale_run_exits_nonzero_without_traceback(stale_run, capsys):
    tid, _, _ = stale_run

    rc = kc._cmd_block(argparse.Namespace(task_id=tid, ids=[], reason=None, kind=None))

    assert rc == 1
    _assert_stale_message(capsys.readouterr().err, "block", tid)


def test_cli_schedule_stale_run_exits_nonzero_without_traceback(stale_run, capsys):
    tid, _, _ = stale_run

    rc = kc._cmd_schedule(argparse.Namespace(task_id=tid, ids=[], reason=None))

    assert rc == 1
    _assert_stale_message(capsys.readouterr().err, "schedule", tid)


def test_cli_stale_run_message_names_the_winning_state(stale_run, capsys):
    """exc.current is a snapshot of who won; a message that omits it is useless."""
    tid, _, current_run = stale_run

    kc._cmd_block(argparse.Namespace(task_id=tid, ids=[], reason=None, kind=None))

    err = capsys.readouterr().err
    assert "running" in err
    assert str(current_run) in err


def test_cli_stale_run_leaves_the_task_untouched(stale_run, capsys):
    """The mutation is rejected, so the winning run must still own the task."""
    tid, _, current_run = stale_run

    assert kc._cmd_complete(
        argparse.Namespace(task_ids=[tid], summary="late", result=None, metadata=None)
    ) == 1

    conn = kb.connect()
    try:
        task = kb.get_task(conn, tid)
    finally:
        conn.close()
    assert task.status == "running"
    assert task.current_run_id == current_run


# ---------------------------------------------------------------------------
# Agent tools — caught, but degraded to a bare exception string + stack trace
# ---------------------------------------------------------------------------

def _handler_result(name, args):
    from tools import kanban_tools

    return getattr(kanban_tools, name)(args)


@pytest.mark.parametrize(
    ("handler", "tool", "extra_args"),
    [
        ("_handle_complete", "kanban_complete", {"summary": "late"}),
        # kanban_block rejects a missing reason before it ever reaches the
        # mutation, so supply one or this asserts the wrong error.
        ("_handle_block", "kanban_block", {"reason": "needs input"}),
    ],
)
def test_agent_tool_stale_run_returns_actionable_tool_error(stale_run, handler, tool, extra_args):
    tid, _, _ = stale_run

    out = _handler_result(handler, {"task_id": tid, **extra_args})

    assert f"{tool}: your run is no longer the current run for {tid}" in out
    # The outer catch-all would have surfaced str(exc) verbatim instead.
    assert "stale mutation for" not in out
    # A stale run is recoverable; the message has to say what to do next or the
    # model tends to treat it as terminal and abandon the task.
    assert "kanban_get" in out


def test_agent_tool_stale_run_does_not_log_a_stack_trace(stale_run, caplog):
    """logger.exception on a routine outcome buries real errors in the log."""
    tid, _, _ = stale_run

    with caplog.at_level(logging.ERROR, logger="tools.kanban_tools"):
        _handler_result("_handle_complete", {"task_id": tid, "summary": "late"})

    assert not [r for r in caplog.records if r.exc_info], (
        "a superseded run is expected, not exceptional; it must not emit a traceback"
    )


def test_agent_tool_current_run_still_succeeds(stale_run, monkeypatch):
    """The guard must reject only the STALE run, never the legitimate one."""
    tid, _, current_run = stale_run
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(current_run))

    out = _handler_result("_handle_heartbeat", {"task_id": tid})

    assert "no longer the current run" not in out
    assert "error" not in out


def test_heartbeat_worker_returns_false_rather_than_raising(stale_run):
    """Pin the asymmetry at its source, so a refactor has to notice it.

    complete_task / block_task / schedule_task all route expected_run_id through
    _with_expected_run -> _check_mutation_precondition, which raises. heartbeat
    does not. If this ever starts raising, the ``if not ok`` branches in
    _cmd_heartbeat and _handle_heartbeat become unreachable the same way the
    others did -- their defensive TaskMutationConflict guards are what keep that
    from resurfacing as a traceback.
    """
    tid, superseded, _ = stale_run

    conn = kb.connect()
    try:
        assert kb.heartbeat_worker(conn, tid, expected_run_id=superseded) is False
    finally:
        conn.close()
