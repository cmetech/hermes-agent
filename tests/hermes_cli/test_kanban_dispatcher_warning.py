"""Which columns depend on a running dispatcher.

The create endpoint warned only for ready+assigned tasks, on the reasoning
that "triage/todo are expected to wait". Auto-decompose (kanban.auto_decompose,
default True) made that false: the dispatcher tick is what sweeps Triage, so a
triage card with no dispatcher is stalled, not queued -- and this warning was
the only thing that could have said so.
"""

from __future__ import annotations

import pytest

from plugins.kanban.dashboard.plugin_api import _task_needs_dispatcher


@pytest.mark.parametrize("status", ["triage", "todo"])
def test_triage_and_todo_need_a_dispatcher(status):
    assert _task_needs_dispatcher(status, None)


def test_ready_with_an_assignee_needs_a_dispatcher():
    assert _task_needs_dispatcher("ready", "default")


def test_ready_without_an_assignee_does_not():
    """Unassigned ready tasks are skipped by the dispatcher regardless, so a
    dispatcher warning would misdirect: the missing piece is the assignee."""
    assert not _task_needs_dispatcher("ready", None)


@pytest.mark.parametrize("status", ["running", "blocked", "review", "done"])
def test_terminal_and_in_flight_states_do_not(status):
    assert not _task_needs_dispatcher(status, "default")
