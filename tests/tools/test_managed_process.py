"""Behavior contracts for the generic managed-process-tree primitive."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def _capture_spawn_error(
    expected_exception,
    spawn,
) -> tuple[type[OSError], int, object]:
    try:
        result = spawn()
    except expected_exception as exc:
        error = (type(exc), exc.errno, exc.filename)
        exc.__traceback__ = None
        return error
    if isinstance(result, ManagedProcessTree):
        result.close()
    pytest.fail(f"DID NOT RAISE {expected_exception!r}")


def _capture_exception(spawn) -> tuple[type[BaseException], tuple[object, ...]]:
    try:
        result = spawn()
    except BaseException as exc:
        error = (type(exc), exc.args)
        exc.__traceback__ = None
        return error
    if isinstance(result, ManagedProcessTree):
        result.close()
    pytest.fail("DID NOT RAISE")


class _RaisingExecutablePath:
    def __fspath__(self) -> str:
        raise RuntimeError("executable path conversion failed")


class _RaisingSignalTruth:
    def __bool__(self) -> bool:
        raise RuntimeError("restore_signals truth conversion failed")


def _environment_entries(output: bytes) -> frozenset[bytes]:
    return frozenset(line for line in output.splitlines() if line)


def _direct_environment(argv: list[str], env) -> tuple[int, frozenset[bytes]]:
    process = subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout, _ = process.communicate(timeout=5)
    return process.returncode, _environment_entries(stdout)


def _managed_environment(
    argv: list[str], env, inherited_descriptor: int
) -> tuple[int, frozenset[bytes]]:
    tree = ManagedProcessTree.spawn(
        argv,
        env=env,
        inherited_descriptors=[inherited_descriptor],
    )
    try:
        stdout, _ = tree.process.communicate(timeout=5)
        return tree.process.returncode, _environment_entries(stdout)
    finally:
        tree.close()


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
@pytest.mark.parametrize("open_flags", [os.O_WRONLY, os.O_RDWR])
def test_spawn_rejects_writable_regular_descriptor_without_child_or_ownership_change(
    monkeypatch,
    tmp_path,
    open_flags,
) -> None:
    path = tmp_path / "writable-descriptor"
    path.write_bytes(b"caller-owned")
    descriptor = os.open(path, open_flags)
    spawned: list[bool] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: spawned.append(True),
    )
    try:
        with pytest.raises(ValueError, match="read-only"):
            ManagedProcessTree.spawn(
                _sleep_argv(0.01),
                inherited_descriptors=[descriptor],
            )
        os.fstat(descriptor)
        assert spawned == []
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
def test_spawn_rejects_pipe_write_end_without_child_or_ownership_change(
    monkeypatch,
) -> None:
    read_fd, write_fd = os.pipe()
    spawned: list[bool] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: spawned.append(True),
    )
    try:
        with pytest.raises(ValueError, match="read-only"):
            ManagedProcessTree.spawn(
                _sleep_argv(0.01),
                inherited_descriptors=[write_fd],
            )
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
        assert spawned == []
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
def test_spawn_fails_closed_when_access_mode_inspection_fails(
    monkeypatch,
) -> None:
    import fcntl

    real_fcntl = fcntl.fcntl
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    spawned: list[bool] = []

    def fail_getfl(descriptor, operation, argument=0):
        if operation == fcntl.F_GETFL:
            raise OSError(errno.EIO, "inspection failed")
        return real_fcntl(descriptor, operation, argument)

    monkeypatch.setattr(fcntl, "fcntl", fail_getfl)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: spawned.append(True),
    )
    try:
        with pytest.raises(ValueError, match="could not be inspected"):
            ManagedProcessTree.spawn(
                _sleep_argv(0.01),
                inherited_descriptors=[read_fd],
            )
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
        assert spawned == []
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_spawn_accepts_read_only_regular_descriptor_at_exact_number(tmp_path) -> None:
    path = tmp_path / "read-only-descriptor"
    path.write_bytes(b"regular exact bytes")
    descriptor = os.open(path, os.O_RDONLY)
    code = f"import os,sys;sys.stdout.buffer.write(os.read({descriptor}, 4096))"
    tree = None
    try:
        tree = ManagedProcessTree.spawn(
            [sys.executable, "-c", code],
            inherited_descriptors=[descriptor],
        )
        stdout, _ = tree.process.communicate(timeout=5)
        assert stdout == b"regular exact bytes"
        os.fstat(descriptor)
    finally:
        if tree is not None:
            tree.close()
        os.close(descriptor)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_identity_is_pinned_across_close_and_number_reuse(
    monkeypatch,
) -> None:
    original_read, original_write = os.pipe()
    replacement_read, replacement_write = os.pipe()
    os.write(original_write, b"original")
    os.close(original_write)
    os.write(replacement_write, b"replacement")
    os.close(replacement_write)
    real_popen = subprocess.Popen
    handoff_replaced = threading.Event()
    handoff_barrier = threading.Barrier(2)

    def replace_in_competing_thread() -> None:
        handoff_barrier.wait(timeout=5)
        os.close(original_read)
        os.dup2(replacement_read, original_read)
        handoff_replaced.set()
        handoff_barrier.wait(timeout=5)

    def replace_after_validation(*args, **kwargs):
        handoff_barrier.wait(timeout=5)
        handoff_barrier.wait(timeout=5)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", replace_after_validation)
    code = f"import os,sys;sys.stdout.buffer.write(os.read({original_read}, 4096))"
    tree = None
    replacer = threading.Thread(target=replace_in_competing_thread)
    replacer.start()
    try:
        tree = ManagedProcessTree.spawn(
            [sys.executable, "-c", code],
            inherited_descriptors=[original_read],
        )
        stdout, _ = tree.process.communicate(timeout=5)
        assert handoff_replaced.is_set()
        assert stdout == b"original"
        assert os.read(original_read, 4096) == b"replacement"
    finally:
        replacer.join(timeout=5)
        assert not replacer.is_alive()
        if tree is not None:
            tree.close()
        os.close(original_read)
        os.close(replacement_read)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_multiple_inherited_descriptors_keep_their_exact_child_numbers() -> None:
    first_read, first_write = os.pipe()
    second_read, second_write = os.pipe()
    os.write(first_write, b"first")
    os.write(second_write, b"second")
    os.close(first_write)
    os.close(second_write)
    code = (
        "import os,sys;"
        f"sys.stdout.buffer.write(os.read({first_read},4096)+b'|'+"
        f"os.read({second_read},4096))"
    )
    tree = None
    try:
        tree = ManagedProcessTree.spawn(
            [sys.executable, "-c", code],
            inherited_descriptors=[first_read, second_read],
        )
        stdout, _ = tree.process.communicate(timeout=5)
        assert stdout == b"first|second"
    finally:
        if tree is not None:
            tree.close()
        os.close(first_read)
        os.close(second_read)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_concurrent_inherited_descriptor_launches_preserve_exact_identity() -> None:
    barrier = threading.Barrier(8)

    def launch(index: int) -> bytes:
        read_fd, write_fd = os.pipe()
        payload = f"payload-{index}".encode()
        os.write(write_fd, payload)
        os.close(write_fd)
        tree = None
        try:
            barrier.wait(timeout=5)
            code = f"import os,sys;sys.stdout.buffer.write(os.read({read_fd}, 4096))"
            tree = ManagedProcessTree.spawn(
                [sys.executable, "-c", code],
                inherited_descriptors=[read_fd],
            )
            stdout, _ = tree.process.communicate(timeout=5)
            return stdout
        finally:
            if tree is not None:
                tree.close()
            os.close(read_fd)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(launch, range(8)))

    assert results == [f"payload-{index}".encode() for index in range(8)]


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
    before = psutil.Process().num_fds()
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
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("explicit_none", [False, True])
def test_inherited_descriptor_default_executable_matches_direct_popen_failure(
    explicit_none,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    argv = ["/definitely/missing/hermes-managed-process-test"]
    direct_kwargs = {"executable": None} if explicit_none else {}
    try:
        direct_error = _capture_spawn_error(
            FileNotFoundError,
            lambda: subprocess.Popen(argv, **direct_kwargs),
        )
        managed_error = _capture_spawn_error(
            direct_error[0],
            lambda: ManagedProcessTree.spawn(
                argv,
                inherited_descriptors=[read_fd],
                **direct_kwargs,
            ),
        )
        assert managed_error == direct_error
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_empty_executable_matches_direct_fail_closed(
    tmp_path,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    effect = tmp_path / "must-not-run"
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
    ]
    try:
        direct_error = _capture_spawn_error(
            PermissionError,
            lambda: subprocess.Popen(argv, executable=""),
        )
        assert not effect.exists()
        managed_error = _capture_spawn_error(
            direct_error[0],
            lambda: ManagedProcessTree.spawn(
                argv,
                executable="",
                inherited_descriptors=[read_fd],
            ),
        )
        assert managed_error == direct_error
        assert not effect.exists()
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_missing_custom_executable_matches_direct_failure(
    tmp_path,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    effect = tmp_path / "must-not-run"
    missing = str(tmp_path / "missing-custom-executable")
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
    ]
    try:
        direct_error = _capture_spawn_error(
            FileNotFoundError,
            lambda: subprocess.Popen(argv, executable=missing),
        )
        assert not effect.exists()
        managed_error = _capture_spawn_error(
            direct_error[0],
            lambda: ManagedProcessTree.spawn(
                argv,
                executable=missing,
                inherited_descriptors=[read_fd],
            ),
        )
        assert managed_error == direct_error
        assert not effect.exists()
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_successful_custom_executable_preserves_argv() -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    argv = ["custom-python-argv-zero", "-c", "import sys;print(sys.argv)", "tail"]
    direct = subprocess.Popen(
        argv,
        executable=sys.executable,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    direct_stdout, _ = direct.communicate(timeout=5)
    tree = None
    try:
        tree = ManagedProcessTree.spawn(
            argv,
            executable=sys.executable,
            inherited_descriptors=[read_fd],
        )
        managed_stdout, _ = tree.process.communicate(timeout=5)
        assert tree.process.args == argv
        assert managed_stdout == direct_stdout
        assert tree.process.returncode == direct.returncode == 0
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        if tree is not None:
            tree.close()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, frozenset()),
        (
            {"PATH": "/usr/bin:/bin", "HERMES_DIAG": "plain"},
            frozenset({b"PATH=/usr/bin:/bin", b"HERMES_DIAG=plain"}),
        ),
    ],
    ids=["empty", "minimal"],
)
def test_inherited_descriptor_preserves_exact_explicit_environment(
    environment,
    expected,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    argv = ["/usr/bin/env"]
    try:
        direct = _direct_environment(argv, environment)
        managed = _managed_environment(argv, environment, read_fd)

        assert direct == (0, expected)
        assert managed == direct
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_preserves_mixed_non_utf8_environment(
    tmp_path,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    environment = {
        b"PATH": b"/usr/bin:/bin",
        "HERMES_PATHLIKE": Path(tmp_path / "pathlike-value"),
        b"HERMES_NON_UTF8": b"value-\xff",
    }
    expected = frozenset(
        {
            b"PATH=/usr/bin:/bin",
            b"HERMES_PATHLIKE=" + os.fsencode(tmp_path / "pathlike-value"),
            b"HERMES_NON_UTF8=value-\xff",
        }
    )
    try:
        direct = _direct_environment(["env"], environment)
        managed = _managed_environment(["env"], environment, read_fd)

        assert direct == (0, expected)
        assert managed == direct
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_preserves_distinct_mixed_keys_with_same_bytes() -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    environment = {
        b"PATH": b"/usr/bin:/bin",
        b"HERMES_DUPLICATE": b"bytes-entry",
        "HERMES_DUPLICATE": "text-entry",
    }
    expected = frozenset(
        {
            b"PATH=/usr/bin:/bin",
            b"HERMES_DUPLICATE=bytes-entry",
            b"HERMES_DUPLICATE=text-entry",
        }
    )
    try:
        direct = _direct_environment(["env"], environment)
        managed = _managed_environment(["env"], environment, read_fd)

        assert direct == (0, expected)
        assert managed == direct
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_path_resolution_rejects_ambiguous_mixed_path() -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    environment = {"PATH": "/usr/bin:/bin", b"PATH": b"/usr/bin:/bin"}
    direct_error = _capture_exception(
        lambda: subprocess.Popen(["env"], env=environment),
    )
    try:
        managed_error = _capture_exception(
            lambda: ManagedProcessTree.spawn(
                ["env"],
                env=environment,
                inherited_descriptors=[read_fd],
            ),
        )

        assert managed_error == direct_error
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_path_search_preserves_first_meaningful_error(
    tmp_path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    effect = tmp_path / "failed-path-candidate-must-not-run"
    blocked = first / "hermes-probe"
    blocked.write_text(f"#!/bin/sh\nprintf ran > {effect}\n")
    blocked.chmod(0o600)
    (second / "hermes-probe").symlink_to("hermes-probe")
    environment = {"PATH": os.pathsep.join((str(first), str(second)))}
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    try:
        direct_error = _capture_spawn_error(
            OSError,
            lambda: subprocess.Popen(["hermes-probe"], env=environment),
        )
        assert direct_error == (PermissionError, errno.EACCES, "hermes-probe")
        assert psutil.Process().num_fds() == before

        managed_error = _capture_spawn_error(
            OSError,
            lambda: ManagedProcessTree.spawn(
                ["hermes-probe"],
                env=environment,
                inherited_descriptors=[read_fd],
            ),
        )
        assert managed_error == direct_error
        assert not effect.exists()
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_path_search_continues_to_later_success(
    tmp_path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    blocked = first / "hermes-probe"
    blocked.write_text("#!/bin/sh\nexit 99\n")
    blocked.chmod(0o600)
    executable = second / "hermes-probe"
    executable.write_text("#!/bin/sh\nprintf later-success\nprintf ran > \"$1\"\n")
    executable.chmod(0o700)
    environment = {"PATH": os.pathsep.join((str(first), str(second)))}
    direct_effect = tmp_path / "direct-ran"
    managed_effect = tmp_path / "managed-ran"
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    tree = None
    try:
        direct = subprocess.Popen(
            ["hermes-probe", str(direct_effect)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        direct_stdout, _ = direct.communicate(timeout=5)
        assert (direct.returncode, direct_stdout) == (0, b"later-success")
        assert direct_effect.read_text() == "ran"
        assert psutil.Process().num_fds() == before

        tree = ManagedProcessTree.spawn(
            ["hermes-probe", str(managed_effect)],
            env=environment,
            inherited_descriptors=[read_fd],
        )
        managed_stdout, _ = tree.process.communicate(timeout=5)
        assert (tree.process.returncode, managed_stdout) == (0, b"later-success")
        assert managed_effect.read_text() == "ran"
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        if tree is not None:
            tree.close()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    ("path_order", "expected_error"),
    [
        (("not-a-directory", "missing"), errno.ENOENT),
        (("missing", "not-a-directory"), errno.ENOTDIR),
    ],
)
def test_inherited_descriptor_path_search_preserves_last_not_found_error(
    tmp_path,
    path_order,
    expected_error,
) -> None:
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("not a directory")
    path_entries = {
        "not-a-directory": not_a_directory,
        "missing": tmp_path / "missing",
    }
    environment = {
        "PATH": os.pathsep.join(str(path_entries[name]) for name in path_order)
    }
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    try:
        direct_error = _capture_spawn_error(
            OSError,
            lambda: subprocess.Popen(["hermes-probe"], env=environment),
        )
        assert direct_error[1:] == (expected_error, "hermes-probe")
        assert psutil.Process().num_fds() == before

        managed_error = _capture_spawn_error(
            direct_error[0],
            lambda: ManagedProcessTree.spawn(
                ["hermes-probe"],
                env=environment,
                inherited_descriptors=[read_fd],
            ),
        )
        assert managed_error == direct_error
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_inherited_descriptor_omitted_environment_matches_direct_inheritance(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LC_CTYPE", raising=False)
    monkeypatch.delenv("__CF_USER_TEXT_ENCODING", raising=False)
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    try:
        direct = _direct_environment(["/usr/bin/env"], None)
        managed = _managed_environment(["/usr/bin/env"], None, read_fd)

        assert managed == direct
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    "executable",
    [object(), _RaisingExecutablePath()],
    ids=["invalid-object", "raising-pathlike"],
)
def test_invalid_executable_setup_matches_direct_without_descriptor_leaks(
    tmp_path,
    executable,
) -> None:
    read_fd, write_fd = os.pipe()
    effect = tmp_path / "invalid-executable-must-not-run"
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
    ]
    direct_error = _capture_exception(
        lambda: subprocess.Popen(argv, executable=executable),
    )
    before = psutil.Process().num_fds()
    try:
        for _ in range(3):
            managed_error = _capture_exception(
                lambda: ManagedProcessTree.spawn(
                    argv,
                    executable=executable,
                    inherited_descriptors=[read_fd],
                ),
            )
            assert managed_error == direct_error
            assert psutil.Process().num_fds() == before
            assert not effect.exists()
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
def test_invalid_restore_signals_matches_direct_without_descriptor_leaks(
    tmp_path,
) -> None:
    read_fd, write_fd = os.pipe()
    effect = tmp_path / "invalid-restore-signals-must-not-run"
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
    ]
    restore_signals = _RaisingSignalTruth()
    direct_error = _capture_exception(
        lambda: subprocess.Popen(argv, restore_signals=restore_signals),
    )
    before = psutil.Process().num_fds()
    try:
        for _ in range(3):
            managed_error = _capture_exception(
                lambda: ManagedProcessTree.spawn(
                    argv,
                    restore_signals=restore_signals,
                    inherited_descriptors=[read_fd],
                ),
            )
            assert managed_error == direct_error
            assert psutil.Process().num_fds() == before
            assert not effect.exists()
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("executable_kind", ["pathlike", "bytes"])
def test_missing_non_string_executable_preserves_direct_filename_identity(
    tmp_path,
    executable_kind,
) -> None:
    read_fd, write_fd = os.pipe()
    effect = tmp_path / "missing-non-string-executable-must-not-run"
    missing_path = tmp_path / "missing-non-string-executable"
    executable = (
        missing_path
        if executable_kind == "pathlike"
        else os.fsencode(missing_path) + b"-\xff"
    )
    argv = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(effect)!r}).write_text('ran')",
    ]
    before = psutil.Process().num_fds()
    try:
        direct_error = _capture_spawn_error(
            FileNotFoundError,
            lambda: subprocess.Popen(argv, executable=executable),
        )
        managed_error = _capture_spawn_error(
            direct_error[0],
            lambda: ManagedProcessTree.spawn(
                argv,
                executable=executable,
                inherited_descriptors=[read_fd],
            ),
        )
        assert managed_error == direct_error
        assert type(managed_error[2]) is type(direct_error[2])
        assert psutil.Process().num_fds() == before
        assert not effect.exists()
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal contract")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    ("restore_signals", "parent_disposition"),
    [(False, signal.SIG_DFL), (True, signal.SIG_IGN)],
    ids=["preserve-default", "restore-default"],
)
def test_inherited_descriptor_signal_behavior_matches_direct_popen(
    restore_signals,
    parent_disposition,
) -> None:
    read_fd, write_fd = os.pipe()
    before = psutil.Process().num_fds()
    previous_disposition = signal.getsignal(signal.SIGPIPE)
    argv = ["/bin/sh", "-c", "kill -PIPE $$; printf survived"]
    tree = None
    try:
        signal.signal(signal.SIGPIPE, parent_disposition)
        direct = subprocess.Popen(
            argv,
            restore_signals=restore_signals,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        direct_stdout, _ = direct.communicate(timeout=5)
        tree = ManagedProcessTree.spawn(
            argv,
            restore_signals=restore_signals,
            inherited_descriptors=[read_fd],
        )
        managed_stdout, _ = tree.process.communicate(timeout=5)
        assert (tree.process.returncode, managed_stdout) == (
            direct.returncode,
            direct_stdout,
        )
        assert tree.identity.pid == tree.process.pid
        assert psutil.Process().num_fds() == before
        os.write(write_fd, b"x")
        assert os.read(read_fd, 1) == b"x"
    finally:
        signal.signal(signal.SIGPIPE, previous_disposition)
        if tree is not None:
            tree.close()
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
