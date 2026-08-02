"""Behavior contracts for the generic managed-process-tree primitive."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
import psutil

from tools.managed_process import (
    ManagedProcessTree,
    ProcessIdentity,
    ProcessResourceLimits,
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


@pytest.mark.parametrize(
    ("inherited_descriptors", "expected_exception", "message"),
    [
        ([0], ValueError, "standard"),
        ([1], ValueError, "standard"),
        ([2], ValueError, "standard"),
        ([-1], ValueError, "standard"),
        ([3, 3], ValueError, "unique"),
        ([True], TypeError, "integer"),
        ([3.0], TypeError, "integer"),
        (["3"], TypeError, "integer"),
        (list(range(3, 68)), ValueError, "at most 64"),
    ],
)
def test_spawn_rejects_invalid_inherited_descriptor_contract(
    inherited_descriptors,
    expected_exception,
    message,
) -> None:
    with pytest.raises(expected_exception, match=message):
        ManagedProcessTree.spawn(
            _sleep_argv(0.01),
            inherited_descriptors=inherited_descriptors,
        )


def test_spawn_rejects_raw_pass_fds_escape_hatch() -> None:
    with pytest.raises(TypeError, match="inherited_descriptors"):
        ManagedProcessTree.spawn(_sleep_argv(0.01), pass_fds=())


def test_spawn_rejects_closed_inherited_descriptor() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        with pytest.raises(ValueError, match="closed"):
            ManagedProcessTree.spawn(
                _sleep_argv(0.01),
                inherited_descriptors=[read_fd],
            )
    finally:
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_spawn_inherits_only_exact_nominated_read_only_descriptor() -> None:
    inherited_read, inherited_write = os.pipe()
    unrelated = os.open(__file__, os.O_RDONLY)
    os.set_inheritable(unrelated, True)
    payload = b"exact nominated bytes"
    os.write(inherited_write, payload)
    os.close(inherited_write)
    code = (
        "import os,sys;"
        f"data=os.read({inherited_read},4096);"
        f"\ntry: os.fstat({unrelated})\nexcept OSError: unrelated='closed'"
        "\nelse: unrelated='open'"
        "\nprint(data.decode(), unrelated, os.getsid(0)==os.getpid(), sep='|')"
    )
    tree = None
    try:
        tree = ManagedProcessTree.spawn(
            [sys.executable, "-c", code],
            inherited_descriptors=[inherited_read],
            close_fds=False,
        )
        os.fstat(inherited_read)
        stdout, _ = tree.process.communicate(timeout=5)
        assert stdout.decode().strip() == "exact nominated bytes|closed|True"
    finally:
        if tree is not None:
            tree.close()
        os.close(inherited_read)
        os.close(unrelated)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_spawn_preserves_process_tree_termination() -> None:
    read_fd, write_fd = os.pipe()
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import os,subprocess,sys,time;"
        f"os.read({read_fd},1);"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "print(p.pid,flush=True);time.sleep(60)"
    )
    os.write(write_fd, b"x")
    os.close(write_fd)
    tree = ManagedProcessTree.spawn(
        [sys.executable, "-c", parent_code],
        inherited_descriptors=[read_fd],
        policy=TerminationPolicy(
            cooperative_grace_seconds=0,
            term_grace_seconds=0.2,
            kill_grace_seconds=0.5,
            wait_timeout_seconds=1.0,
        ),
    )
    os.close(read_fd)
    assert tree.process.stdout is not None
    descendant_pid = int(tree.process.stdout.readline().decode().strip())
    try:
        tree.terminate("descriptor tree cleanup")
        assert tree.reaped is True
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
    finally:
        if not tree.reaped:
            tree.close()


def test_spawn_failure_does_not_close_caller_owned_inherited_descriptor(
    monkeypatch,
) -> None:
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    try:
        with pytest.raises(OSError, match="spawn failed"):
            ManagedProcessTree.spawn(
                _sleep_argv(),
                inherited_descriptors=[read_fd],
            )
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_windows_nonempty_inherited_descriptors_fail_before_job_creation(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    read_fd, write_fd = os.pipe()
    created: list[bool] = []
    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "create",
        classmethod(lambda cls: created.append(True)),
    )
    try:
        with pytest.raises(ValueError, match="Windows"):
            ManagedProcessTree.spawn(
                _sleep_argv(),
                inherited_descriptors=[read_fd],
            )
        assert created == []
    finally:
        os.close(read_fd)
        os.close(write_fd)


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group contract")
def test_posix_group_permission_failure_is_not_reported_as_cleaned(monkeypatch) -> None:
    tree = ManagedProcessTree.__new__(ManagedProcessTree)
    tree.identity = ProcessIdentity(pid=43210, start_time=None, group_id=43210)
    tree.policy = TerminationPolicy(
        term_grace_seconds=0.01,
        kill_grace_seconds=0.01,
        wait_timeout_seconds=0.01,
    )
    monkeypatch.setattr(os, "getpgrp", lambda: 1)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda group_id, sig: (
            None
            if sig == 0
            else (_ for _ in ()).throw(PermissionError(group_id))
        ),
    )

    assert tree._terminate_owned_posix_group() is False


def test_windows_legacy_tree_taskkill_is_not_proof_of_quiescence(monkeypatch) -> None:
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
    assert ManagedProcessTree.terminate_existing(identity) is False
    assert calls == [["taskkill", "/PID", "77", "/T", "/F"]]


def test_windows_taskkill_unavailable_root_signal_is_outcome_uncertain(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 7, pid)),
    )

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("taskkill unavailable")

    identity = ProcessIdentity(pid=77, start_time=7, group_id=77)
    assert ManagedProcessTree.terminate_existing(
        identity,
        subprocess_run=unavailable,
        os_kill=lambda pid, sig: signalled.append((pid, sig)),
    ) is False
    assert signalled == [(77, signal.SIGTERM)]


def test_windows_spawn_assigns_job_before_returning_owner(monkeypatch) -> None:
    import tools.managed_process as managed

    assigned: list[int] = []
    resumed: list[int] = []
    popen_kwargs: dict[str, object] = {}

    class FakeJob:
        name = "Local\\HermesManagedProcess-test"

        def assign(self, handle):
            assigned.append(handle)

        def resume_process(self, pid):
            resumed.append(pid)

        def close(self):
            pass

    class FakeProcess:
        pid = 77
        _handle = 88

    job = FakeJob()
    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(managed._WindowsJob, "create", classmethod(lambda cls: job))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **kwargs: popen_kwargs.update(kwargs) or FakeProcess(),
    )
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 7, pid)),
    )

    tree = ManagedProcessTree.spawn(_sleep_argv())

    assert assigned == [88]
    assert resumed == [77]
    assert int(popen_kwargs["creationflags"]) & managed._CREATE_SUSPENDED
    assert tree.identity.job_name == job.name
    assert tree._windows_job is job


def test_windows_job_assignment_failure_kills_child_and_closes_job(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    events: list[str] = []

    class FailingJob:
        name = "Local\\HermesManagedProcess-test"

        def assign(self, _handle):
            raise OSError("assignment failed")

        def close(self):
            events.append("job_closed")

    class FakeProcess:
        pid = 77
        _handle = 88

        def kill(self):
            events.append("child_killed")

        def wait(self, timeout):
            assert timeout == 1
            events.append("child_reaped")

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "create",
        classmethod(lambda cls: FailingJob()),
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    with pytest.raises(OSError, match="assignment failed"):
        ManagedProcessTree.spawn(_sleep_argv())

    assert events == ["child_killed", "child_reaped", "job_closed"]


def test_windows_spawn_failure_closes_job_when_child_kill_is_uncertain(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    events: list[str] = []

    class FailingJob:
        name = "Local\\HermesManagedProcess-test"

        def assign(self, _handle):
            raise OSError("assignment failed")

        def close(self):
            events.append("job_closed")

    class UnkillableProcess:
        pid = 77
        _handle = 88

        def kill(self):
            events.append("child_kill_uncertain")
            raise OSError("kill failed")

        def wait(self, timeout):
            assert timeout == 1
            events.append("child_waited")
            raise subprocess.TimeoutExpired("child", timeout)

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "create",
        classmethod(lambda cls: FailingJob()),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: UnkillableProcess(),
    )

    with pytest.raises(OSError, match="assignment failed"):
        ManagedProcessTree.spawn(_sleep_argv())

    assert events == ["child_kill_uncertain", "child_waited", "job_closed"]


def test_windows_existing_named_job_requires_query_proven_quiescence(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    events: list[object] = []

    class FakeJob:
        def terminate_and_wait(self, timeout):
            events.append(("terminated", timeout))
            return True

        def close(self):
            events.append("closed")

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "open",
        classmethod(lambda cls, name: FakeJob()),
    )
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(
            lambda cls, pid: ProcessIdentity(
                pid, 7, pid, "Local\\HermesManagedProcess-test"
            )
        ),
    )
    identity = ProcessIdentity(
        pid=77,
        start_time=7,
        group_id=77,
        job_name="Local\\HermesManagedProcess-test",
    )

    assert ManagedProcessTree.terminate_existing(
        identity,
        term_grace_seconds=2,
        kill_grace_seconds=1,
    ) is True
    assert events == [("terminated", 3), "closed"]


def test_windows_named_job_remains_authoritative_after_root_pid_reuse(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    events: list[str] = []

    class FakeJob:
        def terminate_and_wait(self, _timeout):
            events.append("terminated")
            return True

        def close(self):
            events.append("closed")

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "open",
        classmethod(lambda cls, name: FakeJob()),
    )
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 99, pid)),
    )
    identity = ProcessIdentity(
        pid=77,
        start_time=7,
        group_id=77,
        job_name="Local\\HermesManagedProcess-test",
    )

    assert ManagedProcessTree.terminate_existing(identity) is True
    assert events == ["terminated", "closed"]


def test_windows_existing_tree_accepts_zero_active_job_members_as_quiescent(
    monkeypatch,
) -> None:
    import tools.managed_process as managed

    class FakeJob:
        def active_processes(self):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        managed._WindowsJob,
        "open",
        classmethod(lambda cls, name: FakeJob()),
    )
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)
    identity = ProcessIdentity(
        pid=77,
        start_time=7,
        group_id=77,
        job_name="Local\\HermesManagedProcess-test",
    )

    assert ManagedProcessTree.existing_tree_active(identity) is False


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows Job Object contract")
@pytest.mark.live_system_guard_bypass
def test_windows_job_kills_detached_grandchild_after_parent_exit() -> None:
    grandchild_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys;"
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild_code!r}],"
        "creationflags=subprocess.DETACHED_PROCESS,"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL);"
        "print(p.pid,flush=True)"
    )
    tree = ManagedProcessTree.spawn([sys.executable, "-c", parent_code])
    assert tree.process.stdout is not None
    grandchild_pid = int(tree.process.stdout.readline().decode().strip())
    grandchild = psutil.Process(grandchild_pid)
    tree.process.wait(timeout=5)

    try:
        assert tree.tree_active() is True
        tree.close()
        assert tree.tree_active() is False
        grandchild.wait(timeout=5)
        assert not grandchild.is_running()
    finally:
        if grandchild.is_running():
            grandchild.kill()


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group contract")
@pytest.mark.live_system_guard_bypass
def test_close_cleans_descendant_after_direct_child_already_exited() -> None:
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print(p.pid, flush=True)"
    )
    tree = ManagedProcessTree.spawn([sys.executable, "-c", parent_code])
    assert tree.process.stdout is not None
    descendant_pid = int(tree.process.stdout.readline().decode().strip())
    tree.process.wait(timeout=2)

    try:
        tree.close()
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
            pytest.fail(
                f"descendant pid {descendant_pid} survived exited-parent cleanup"
            )
    finally:
        try:
            psutil.Process(descendant_pid).kill()
        except psutil.NoSuchProcess:
            pass


def test_spawn_failure_does_not_return_a_partial_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    with pytest.raises(OSError, match="spawn failed"):
        ManagedProcessTree.spawn(_sleep_argv())


@pytest.mark.live_system_guard_bypass
def test_process_tree_resource_limits_include_descendants() -> None:
    code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "time.sleep(30)"
    )
    tree = ManagedProcessTree.spawn([sys.executable, "-c", code])
    try:
        limits = ProcessResourceLimits(max_descendants=0)
        deadline = time.monotonic() + 2
        violation = None
        while violation is None and time.monotonic() < deadline:
            violation = tree.resource_violation(limits)
            time.sleep(0.01)
        assert violation == "descendant_limit"
    finally:
        tree.close()
