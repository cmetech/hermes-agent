"""Platform policy tests use injected APIs, never a counterfeit host OS."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import workflow_gate_build as build


@pytest.mark.parametrize("fault", ["marker", "marker-write", "signals"])
def test_setup_failure_is_inside_owned_cleanup(tmp_path, monkeypatch, fault):
    created = []
    mkdtemp = build.tempfile.mkdtemp

    def temporary(**kwargs):
        result = mkdtemp(dir=tmp_path, **kwargs)
        created.append(Path(result))
        return result

    monkeypatch.setattr(build.tempfile, "mkdtemp", temporary)
    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path), "a" * 40])
    if fault == "marker":
        monkeypatch.setattr(
            build.secrets, "token_hex", Mock(side_effect=OSError("entropy unavailable"))
        )
    elif fault == "marker-write":
        monkeypatch.setattr(
            Path, "write_text", Mock(side_effect=PermissionError("marker unavailable"))
        )
    else:
        monkeypatch.setattr(
            build.signal,
            "signal",
            Mock(side_effect=ValueError("registration unavailable")),
        )
    assert build.main() != 0
    assert created and not created[0].exists()


def windows_runner(
    *, interrupted=False, taskkill_result=0, taskkill_error=None, child_code=0
):
    child = Mock(pid=1234)
    api = SimpleNamespace(
        CREATE_NEW_PROCESS_GROUP=0x200,
        CREATE_NO_WINDOW=0x8000000,
        Popen=Mock(return_value=child),
        run=Mock(),
    )
    api.run.side_effect = taskkill_error
    api.run.return_value = SimpleNamespace(returncode=taskkill_result)
    signals = SimpleNamespace(SIGINT=2, SIGTERM=15, signal=Mock())
    tree = SimpleNamespace(process=child, reaped=False)

    def close():
        if taskkill_error or taskkill_result:
            raise RuntimeError("job quiescence unavailable")
        tree.reaped = True

    tree.close = close
    api.tree = tree

    def spawn(*args, **kwargs):
        api.Popen(*args, **kwargs)
        return tree

    runner = build.ProcessRunner(
        windows=True,
        subprocess_api=api,
        signal_api=signals,
        which=lambda _name: "taskkill.exe",
        spawn=spawn,
    )

    def wait(**_kwargs):
        if interrupted:
            runner.interrupt(15, None)
        return child_code

    child.wait.side_effect = wait
    return runner, api, child, signals


def test_windows_launch_uses_creation_flags_and_only_supported_signals(tmp_path):
    runner, api, _child, signals = windows_runner()
    runner.register_signals()
    runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    args, kwargs = api.Popen.call_args
    assert args == (("node.exe", "local-cli.js"),)
    assert kwargs["creationflags"] == 0x8000200
    assert "start_new_session" not in kwargs
    assert [call.args[0] for call in signals.signal.call_args_list] == [2, 15]
    assert runner.quiescent


def test_windows_interrupt_kills_owned_tree_and_waits_before_cleanup(tmp_path):
    runner, api, child, _signals = windows_runner(interrupted=True)
    with pytest.raises(InterruptedError):
        runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    assert api.run.call_args.args[0] == ["taskkill.exe", "/PID", "1234", "/T", "/F"]
    assert api.run.call_args.kwargs["timeout"] == 10
    assert child.wait.call_args.kwargs["timeout"] == 10
    assert runner.quiescent


@pytest.mark.parametrize("fault", ["missing", "denied", "timeout"])
def test_windows_failed_tree_termination_never_authorizes_cleanup(tmp_path, fault):
    errors = {
        "missing": FileNotFoundError("taskkill"),
        "denied": None,
        "timeout": subprocess.TimeoutExpired("taskkill", 10),
    }
    runner, _api, child, _signals = windows_runner(
        interrupted=True, taskkill_result=1, taskkill_error=errors[fault]
    )
    with pytest.raises(RuntimeError, match="quiescent"):
        runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    assert not runner.quiescent
    assert child.kill.called  # best effort on our handle is not tree proof


def test_windows_missing_taskkill_fails_before_launch(tmp_path):
    runner, api, _child, _signals = windows_runner()
    runner.which = lambda _name: None
    with pytest.raises(RuntimeError, match="taskkill"):
        runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    api.Popen.assert_not_called()
    assert runner.quiescent


def test_windows_child_failure_propagates_after_proven_tree_termination(tmp_path):
    runner, api, _child, _signals = windows_runner(child_code=43)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    assert failure.value.returncode == 43
    assert api.run.call_args.args[0][-2:] == ["/T", "/F"]
    assert runner.quiescent


def test_signal_during_successful_tree_close_cannot_become_success(tmp_path):
    runner, api, _child, _signals = windows_runner()
    close = api.tree.close

    def interrupted_close():
        close()
        runner.interrupt(15, None)

    api.tree.close = interrupted_close
    with pytest.raises(InterruptedError):
        runner.run("node.exe", "local-cli.js", cwd=tmp_path)
    assert runner.quiescent


def test_uncertain_tree_preserves_owned_workspace_and_fails(tmp_path, monkeypatch):
    runner = SimpleNamespace(
        quiescent=False,
        interrupted=0,
        register_signals=Mock(),
        run=Mock(side_effect=RuntimeError("tree not quiescent")),
        finish=lambda: False,
    )
    monkeypatch.setattr(build, "ProcessRunner", lambda **_kwargs: runner)
    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path), "a" * 40])
    mkdtemp = build.tempfile.mkdtemp
    monkeypatch.setattr(
        build.tempfile, "mkdtemp", lambda **kwargs: mkdtemp(dir=tmp_path, **kwargs)
    )
    assert build.main() != 0
    roots = list(tmp_path.glob("hermes-workflow-gate-build-*"))
    assert len(roots) == 1 and (roots[0] / ".owner").is_file()


@pytest.mark.windows_only
def test_native_windows_owned_job_is_quiescent_after_command(tmp_path):
    runner = build.ProcessRunner(windows=True)
    runner.run(sys.executable, "-c", "print('owned child completed')", cwd=tmp_path)
    assert runner.quiescent
