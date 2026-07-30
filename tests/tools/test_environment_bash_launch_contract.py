"""Behavioral coverage for BaseEnvironment's protected launch contract."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tools.environments import docker as docker_mod
from tools.environments import local as local_mod
from tools.environments import modal as modal_mod
from tools.environments import singularity as singularity_mod
from tools.environments import ssh as ssh_mod
from tools.environments.daytona import DaytonaEnvironment


def test_local_applies_script_stdin_and_process_cwd(monkeypatch, tmp_path):
    captured = {}
    proc = MagicMock(pid=123)

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(local_mod, "_find_bash", lambda: "/bin/bash")
    monkeypatch.setattr(local_mod, "_resolve_shell_init_files", lambda: [])
    monkeypatch.setattr(local_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_mod, "_pipe_stdin", lambda handle, data: captured.update(stdin=data))
    monkeypatch.setattr(local_mod.os, "getpgid", lambda pid: pid)

    env = local_mod.LocalEnvironment.__new__(local_mod.LocalEnvironment)
    env.cwd = "/default"
    env.env = {}

    target = str(tmp_path)
    assert env._run_bash(
        "protected-script", login=True, script_stdin=True, cwd=target
    ) is proc
    assert captured["args"] == ["/bin/bash", "-l", "-s"]
    assert captured["stdin"] == "protected-script"
    assert captured["kwargs"]["cwd"] == target


def test_docker_applies_script_stdin_and_process_cwd(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        docker_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel,
    )
    env = docker_mod.DockerEnvironment.__new__(docker_mod.DockerEnvironment)
    env._docker_exe = "docker"
    env._container_id = "container-id"
    env._init_env_args = ["-e", "PROFILE=value"]

    assert env._run_bash("protected-script", login=True, script_stdin=True, cwd="/target") is sentinel
    assert captured["cmd"] == [
        "docker",
        "exec",
        "-i",
        "-w",
        "/target",
        "-e",
        "PROFILE=value",
        "container-id",
        "bash",
        "-l",
        "-s",
    ]
    assert captured["stdin"] == "protected-script"


def test_singularity_applies_script_stdin_and_process_cwd(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        singularity_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel,
    )
    env = singularity_mod.SingularityEnvironment.__new__(
        singularity_mod.SingularityEnvironment
    )
    env._instance_started = True
    env.executable = "apptainer"
    env.instance_id = "instance-id"

    assert env._run_bash("protected-script", login=True, script_stdin=True, cwd="/target") is sentinel
    assert captured["cmd"] == [
        "apptainer",
        "exec",
        "--pwd",
        "/target",
        "instance://instance-id",
        "bash",
        "-l",
        "-s",
    ]
    assert captured["stdin"] == "protected-script"


def test_ssh_applies_script_stdin_and_process_cwd(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        ssh_mod,
        "_popen_bash",
        lambda cmd, stdin: captured.update(cmd=cmd, stdin=stdin) or sentinel,
    )
    env = ssh_mod.SSHEnvironment.__new__(ssh_mod.SSHEnvironment)
    env._build_ssh_command = lambda: ["ssh", "remote"]

    assert env._run_bash(
        "protected-script", login=True, script_stdin=True, cwd="/target path"
    ) is sentinel
    assert captured["cmd"][:2] == ["ssh", "remote"]
    launcher = captured["cmd"][-1]
    assert "protected-script" not in launcher
    assert "bash -l -s" in launcher
    assert "/target path" in launcher
    assert "unset -f builtin unset set cd" in launcher
    assert captured["stdin"] == "protected-script"


def test_modal_writes_script_to_stdin_and_applies_process_cwd():
    process = MagicMock()
    process.stdin.drain.aio = AsyncMock()
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

    handle = env._run_bash(
        "protected-script", login=True, script_stdin=True, cwd="/target"
    )
    assert handle.wait(2) == 0
    sandbox.exec.aio.assert_awaited_once_with(
        "bash", "-l", "-s", timeout=120, workdir="/target"
    )
    process.stdin.write.assert_called_once_with("protected-script")
    process.stdin.write_eof.assert_called_once_with()
    assert process.stdin.drain.aio.await_count == 2


def test_daytona_uses_inner_login_stdin_shell_and_process_cwd():
    sandbox = MagicMock()
    sandbox.process.exec.return_value = SimpleNamespace(result="stdout", exit_code=0)
    env = DaytonaEnvironment.__new__(DaytonaEnvironment)
    env._sandbox = sandbox
    env._lock = threading.Lock()

    handle = env._run_bash(
        "protected-script", login=True, script_stdin=True, cwd="/target"
    )
    assert handle.wait(2) == 0
    shell_cmd = sandbox.process.exec.call_args.args[0]
    assert shell_cmd.startswith("bash -l -s <<'HERMES_BOOTSTRAP_")
    assert "\nprotected-script\n" in shell_cmd
    assert "bash -l -c" not in shell_cmd
    sandbox.process.exec.assert_called_once_with(shell_cmd, cwd="/target", timeout=120)
