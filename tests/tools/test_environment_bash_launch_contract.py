"""Behavioral coverage for hygienic BaseEnvironment shell launches."""

import asyncio
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.environments import docker as docker_mod
from tools.environments import local as local_mod
from tools.environments import modal as modal_mod
from tools.environments import singularity as singularity_mod
from tools.environments import ssh as ssh_mod
from tools.environments.base import _RawSentinelFilter
from tools.environments.daytona import DaytonaEnvironment


_OUTER_TRACE_SENTINEL = (
    "__HERMES_SNAPSHOT_GUARD_PASSED_0123456789abcdef0123456789abcdef__"
)
_OUTER_TRACE_WRAPPER = (
    f"exit 125; : WRAPPER_MUST_NOT_LEAK; : {_OUTER_TRACE_SENTINEL}; "
    "printf USER_EFFECT"
)


def _assert_clean_child_under_inherited_xtrace(shell_command, *, script_stdin=None):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("real Bash is required for xtrace launch coverage")
    run_env = dict(os.environ)
    run_env.update(SHELLOPTS="xtrace", PS4="OUTER_XTRACE:")
    completed = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", shell_command],
        input=script_stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=run_env,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.endswith("CLEAN")


def _assert_outer_trace_cannot_attest(
    shell_command,
    *,
    script_stdin=None,
    shell_command_from_stdin=False,
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("real Bash is required for xtrace launch coverage")
    dynamic_ps4 = (
        "$(if [[ \"$BASH_COMMAND $BASH_EXECUTION_STRING\" =~ "
        "(__HERMES_SNAPSHOT_GUARD_PASSED_[0-9a-f]{32}__) ]]; then "
        "printf \"\\n%s\\nX\" \"${BASH_REMATCH[1]}\"; fi)"
    )
    run_env = dict(os.environ)
    run_env.update(SHELLOPTS="xtrace", PS4=dynamic_ps4)
    args = [bash, "--noprofile", "--norc"]
    if shell_command_from_stdin:
        args.append("-s")
        stdin = shell_command
    else:
        args.extend(["-c", shell_command])
        stdin = script_stdin
    completed = subprocess.run(
        args,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=run_env,
        timeout=10,
    )
    sentinel_filter = _RawSentinelFilter(_OUTER_TRACE_SENTINEL)
    visible = sentinel_filter.feed(completed.stdout) + sentinel_filter.finish()

    assert completed.returncode == 125, visible
    assert sentinel_filter.seen is False, visible
    assert _OUTER_TRACE_SENTINEL not in visible
    assert "WRAPPER_MUST_NOT_LEAK" not in visible
    assert "USER_EFFECT" not in visible


def test_local_clean_launch_suppresses_startup_hooks(monkeypatch):
    captured = {}
    proc = MagicMock(pid=123)
    monkeypatch.setattr(local_mod, "_find_bash", lambda: "/bin/bash")
    monkeypatch.setattr(local_mod.subprocess, "Popen", lambda args, **kw: captured.update(args=args, kwargs=kw) or proc)
    monkeypatch.setattr(local_mod.os, "getpgid", lambda pid: pid)
    env = local_mod.LocalEnvironment.__new__(local_mod.LocalEnvironment)
    env.cwd = "/tmp"
    env.env = {
        "BASH_ENV": "/malicious",
        "ENV": "/malicious",
        "SHELLOPTS": "xtrace",
    }

    assert env._run_bash("protected-script", clean=True) is proc
    assert captured["args"] == [
        "/bin/bash", "--noprofile", "--norc", "+x", "-c", "protected-script",
    ]
    assert captured["kwargs"]["env"]["BASH_ENV"] == "/dev/null"
    assert captured["kwargs"]["env"]["ENV"] == "/dev/null"
    assert captured["kwargs"]["env"]["SHELLOPTS"] == ""


def test_docker_clean_launch_suppresses_startup_hooks(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(docker_mod, "_popen_bash", lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel)
    env = docker_mod.DockerEnvironment.__new__(docker_mod.DockerEnvironment)
    env._docker_exe = "docker"
    env._container_id = "container-id"
    env._init_env_args = []

    assert env._run_bash("protected-script", clean=True) is sentinel
    assert captured["cmd"] == [
        "docker", "exec", "-e", "BASH_ENV=/dev/null", "-e", "ENV=/dev/null",
        "-e", "SHELLOPTS=", "container-id", "bash", "--noprofile", "--norc",
        "+x", "-c", "protected-script",
    ]
    assert captured["stdin"] is None


def test_singularity_clean_launch_suppresses_startup_hooks(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(singularity_mod, "_popen_bash", lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel)
    env = singularity_mod.SingularityEnvironment.__new__(singularity_mod.SingularityEnvironment)
    env._instance_started = True
    env.executable = "apptainer"
    env.instance_id = "instance-id"

    assert env._run_bash("protected-script", clean=True) is sentinel
    assert captured["cmd"] == [
        "apptainer", "exec", "instance://instance-id", "env",
        "BASH_ENV=/dev/null", "ENV=/dev/null", "SHELLOPTS=", "bash",
        "--noprofile", "--norc", "+x", "-c", "protected-script",
    ]
    assert captured["stdin"] is None


def test_ssh_clean_launch_suppresses_startup_hooks(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(ssh_mod, "_popen_bash", lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel)
    env = ssh_mod.SSHEnvironment.__new__(ssh_mod.SSHEnvironment)
    env._build_ssh_command = lambda: ["ssh", "remote"]

    assert env._run_bash("protected-script", clean=True) is sentinel
    assert captured["cmd"][:2] == ["ssh", "remote"]
    assert captured["cmd"][-1].startswith("POSIXLY_CORRECT=1 &&\n\\set +x")
    assert "command env BASH_ENV=/dev/null" in captured["cmd"][-1]
    assert "__hermes_outer_script=$(\\printf" in captured["cmd"][-1]
    assert captured["cmd"][-1].endswith(
        'bash --noprofile --norc +x -c "$__hermes_outer_script"'
    )
    assert "protected-script" not in captured["cmd"][-1]
    assert captured["stdin"] is None


def test_ssh_command_string_clears_readonly_shellopts_in_real_bash(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ssh_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or object(),
    )
    env = ssh_mod.SSHEnvironment.__new__(ssh_mod.SSHEnvironment)
    env._build_ssh_command = lambda: ["ssh", "remote"]
    protected_script = "case $- in *x*) exit 91;; esac; printf CLEAN"

    env._run_bash(protected_script, clean=True)

    _assert_clean_child_under_inherited_xtrace(captured["cmd"][-1])


def test_ssh_outer_trace_cannot_expose_or_attest_wrapper(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ssh_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or object(),
    )
    env = ssh_mod.SSHEnvironment.__new__(ssh_mod.SSHEnvironment)
    env._build_ssh_command = lambda: ["ssh", "remote"]

    env._run_bash(_OUTER_TRACE_WRAPPER, clean=True)

    _assert_outer_trace_cannot_attest(
        captured["cmd"][-1],
        script_stdin=captured["stdin"],
    )


def test_ssh_encoded_wrapper_preserves_user_stdin(monkeypatch):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("real Bash is required for SSH stdin coverage")
    captured = {}
    monkeypatch.setattr(
        ssh_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or object(),
    )
    env = ssh_mod.SSHEnvironment.__new__(ssh_mod.SSHEnvironment)
    env._build_ssh_command = lambda: ["ssh", "remote"]
    script = "read value; printf 'STDIN=%s' \"$value\""

    env._run_bash(script, clean=True, stdin_data="preserved-input\n")
    completed = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", captured["cmd"][-1]],
        input=captured["stdin"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stdout
    assert completed.stdout == "STDIN=preserved-input"
    assert "preserved-input" not in captured["cmd"][-1]


def test_modal_clean_launch_uses_sdk_env_without_stdin_transport():
    process = MagicMock()
    process.stdout.read.aio = AsyncMock(return_value=b"stdout")
    process.stderr.read.aio = AsyncMock(return_value=b"")
    process.wait.aio = AsyncMock(return_value=0)
    sandbox = MagicMock()
    sandbox.exec.aio = AsyncMock(return_value=process)
    worker = MagicMock()
    worker.run_coroutine.side_effect = lambda coro, timeout: asyncio.run(coro)
    env = modal_mod.ModalEnvironment.__new__(modal_mod.ModalEnvironment)
    env._sandbox = sandbox
    env._worker = worker

    handle = env._run_bash("protected-script", clean=True)
    assert handle.wait(2) == 0
    sandbox.exec.aio.assert_awaited_once_with(
        "bash", "--noprofile", "--norc", "+x", "-c", "protected-script",
        timeout=120,
        env={"BASH_ENV": "/dev/null", "ENV": "/dev/null", "SHELLOPTS": ""},
    )
    process.stdin.write.assert_not_called()


def test_daytona_clean_launch_uses_inner_hygienic_bash():
    sandbox = MagicMock()
    sandbox.process.exec.return_value = SimpleNamespace(result="stdout", exit_code=0)
    env = DaytonaEnvironment.__new__(DaytonaEnvironment)
    env._sandbox = sandbox
    env._lock = threading.Lock()

    handle = env._run_bash("protected-script", clean=True)
    assert handle.wait(2) == 0
    shell_cmd = sandbox.process.exec.call_args.args[0]
    assert shell_cmd.startswith("POSIXLY_CORRECT=1 &&\n\\set +x")
    assert "command env BASH_ENV=/dev/null" in shell_cmd
    assert shell_cmd.endswith(
        "bash --noprofile --norc +x -c protected-script"
    )
    sandbox.process.exec.assert_called_once_with(shell_cmd, timeout=120)


def test_daytona_command_string_clears_readonly_shellopts_in_real_bash():
    sandbox = MagicMock()
    sandbox.process.exec.return_value = SimpleNamespace(result="", exit_code=0)
    env = DaytonaEnvironment.__new__(DaytonaEnvironment)
    env._sandbox = sandbox
    env._lock = threading.Lock()
    protected_script = "case $- in *x*) exit 91;; esac; printf CLEAN"

    handle = env._run_bash(protected_script, clean=True)
    assert handle.wait(2) == 0
    shell_command = sandbox.process.exec.call_args.args[0]

    _assert_clean_child_under_inherited_xtrace(shell_command)


def test_daytona_outer_trace_cannot_expose_or_attest_wrapper():
    sandbox = MagicMock()
    sandbox.process.exec.return_value = SimpleNamespace(result="", exit_code=0)
    env = DaytonaEnvironment.__new__(DaytonaEnvironment)
    env._sandbox = sandbox
    env._lock = threading.Lock()

    handle = env._run_bash(_OUTER_TRACE_WRAPPER, clean=True)
    assert handle.wait(2) == 0
    decoded_sdk_script = sandbox.process.exec.call_args.args[0]

    _assert_outer_trace_cannot_attest(
        decoded_sdk_script,
        shell_command_from_stdin=True,
    )
