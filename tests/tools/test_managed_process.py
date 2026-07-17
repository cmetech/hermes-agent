"""Behavior contracts for the generic managed-process-tree primitive."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
import psutil

from tools.managed_process import (
    ManagedProcessTree,
    ProcessIdentity,
    TerminationPolicy,
)


def _sleep_argv(seconds: float = 30.0) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_termination_policy_resolves_to_bounded_deadlines() -> None:
    policy = TerminationPolicy()

    assert policy.cooperative_grace_seconds >= 0
    assert policy.term_grace_seconds >= 0
    assert policy.kill_grace_seconds > 0
    assert policy.wait_timeout_seconds > 0
    assert all(value is not None for value in (
        policy.cooperative_grace_seconds,
        policy.term_grace_seconds,
        policy.kill_grace_seconds,
        policy.wait_timeout_seconds,
    ))


@pytest.mark.parametrize(
    "argv",
    ["echo unsafe", [], [""], [sys.executable, 3]],
)
def test_spawn_requires_a_nonempty_string_argv(argv) -> None:
    with pytest.raises((TypeError, ValueError)):
        ManagedProcessTree.spawn(argv)


@pytest.mark.live_system_guard_bypass
def test_spawn_records_identity_and_close_reaps_child() -> None:
    tree = ManagedProcessTree.spawn(_sleep_argv())

    assert tree.identity.pid == tree.process.pid
    assert tree.identity.start_time is not None
    assert tree.identity.group_id is not None
    assert tree.reaped is False

    tree.close()

    assert tree.reaped is True
    assert tree.process.poll() is not None


@pytest.mark.live_system_guard_bypass
def test_terminate_is_idempotent_and_reaps_already_exited_child() -> None:
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "pass"],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0,
            term_grace_seconds=0.1,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=1.0,
        ),
    )
    tree.process.wait(timeout=2)

    first = tree.terminate("test cleanup")
    second = tree.terminate("duplicate cleanup")

    assert first == tree.process.returncode
    assert second == first
    assert tree.reaped is True


@pytest.mark.live_system_guard_bypass
def test_terminate_closes_parent_lifeline_for_cooperative_exit() -> None:
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); raise SystemExit(0)"],
        stdin=subprocess.PIPE,
        policy=TerminationPolicy(
            cooperative_grace_seconds=1.0,
            term_grace_seconds=0.1,
            kill_grace_seconds=0.2,
            wait_timeout_seconds=1.0,
        ),
    )

    assert tree.terminate("cooperative cancel") == 0
    assert tree.reaped is True


def test_identity_guard_refuses_recycled_pid(monkeypatch) -> None:
    identity = ProcessIdentity(pid=43210, start_time=100, group_id=43210)
    signalled: list[tuple[int, int]] = []

    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 101, pid)),
    )
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    assert ManagedProcessTree.terminate_existing(identity) is False
    assert signalled == []


def test_windows_existing_tree_uses_taskkill_and_closes_handle(monkeypatch) -> None:
    import tools.managed_process as managed

    calls: list[list[str]] = []

    class _Completed:
        returncode = 0

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 7, pid)),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or _Completed(),
    )

    identity = ProcessIdentity(pid=77, start_time=7, group_id=77)
    assert ManagedProcessTree.terminate_existing(identity) is True
    assert calls == [["taskkill", "/PID", "77", "/T", "/F"]]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group contract")
@pytest.mark.live_system_guard_bypass
def test_terminate_cleans_descendants_and_reaps_direct_child() -> None:
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "print(p.pid, flush=True);"
        "time.sleep(60)"
    )
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", parent_code],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0,
            term_grace_seconds=0.2,
            kill_grace_seconds=0.5,
            wait_timeout_seconds=1.0,
        ),
    )
    assert tree.process.stdout is not None
    descendant_pid = int(tree.process.stdout.readline().decode().strip())

    tree.terminate("test descendant cleanup")

    assert tree.reaped is True
    assert tree.process.poll() is not None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            descendant = psutil.Process(descendant_pid)
            if (
                not descendant.is_running()
                or descendant.status() == psutil.STATUS_ZOMBIE
            ):
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"descendant pid {descendant_pid} survived managed cleanup")


def test_spawn_failure_does_not_return_a_partial_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    with pytest.raises(OSError, match="spawn failed"):
        ManagedProcessTree.spawn(_sleep_argv())
