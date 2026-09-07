"""Platform policy tests use injected APIs, never a counterfeit host OS."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import psutil

from scripts import workflow_gate_build as build
from tools.managed_process import ManagedProcessTree, ProcessIdentity


def test_windows_job_does_not_infer_posix_group_from_new_session_option(monkeypatch):
    import tools.managed_process as managed

    job = Mock(name="owned job")
    job.name = "Local\\HermesManagedProcess-test"
    child = Mock(pid=77, _handle=88)
    monkeypatch.setattr(managed, "_IS_WINDOWS", True)
    monkeypatch.setattr(managed._WindowsJob, "create", lambda: job)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(
        ProcessIdentity, "capture", lambda pid: ProcessIdentity(pid, 7, None)
    )

    tree = ManagedProcessTree.spawn(["owned-child"], start_new_session=True)

    assert tree.identity == ProcessIdentity(77, 7, None, job.name)


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize(
    ("launch", "owns_group"),
    [
        ({"start_new_session": True}, True),
        ({}, True),
        ({"start_new_session": False}, False),
        ({"start_new_session": 0}, False),
        ({"start_new_session": False, "process_group": 0}, False),
    ],
)
def test_spawn_retains_new_session_after_leader_exits_before_capture(
    monkeypatch, launch, owns_group
) -> None:
    real_popen = subprocess.Popen
    children = []

    def exited_before_capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        process.wait(timeout=5)  # force the real leader-exit/getpgid boundary
        return process

    monkeypatch.setattr(subprocess, "Popen", exited_before_capture)
    descendant_code = "import threading; threading.Event().wait(60)"
    parent_code = (
        "import subprocess,sys;"
        f"p=subprocess.Popen([sys.executable,'-c',{descendant_code!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print(p.pid,flush=True)"
    )
    tree = ManagedProcessTree.spawn([sys.executable, "-c", parent_code], **launch)
    descendant = psutil.Process(int(tree.process.stdout.readline().decode("ascii")))
    try:
        assert tree.process.poll() == 0
        assert ProcessIdentity.capture(tree.process.pid).group_id is None
        assert descendant.is_running()
        tree.close()
        try:
            alive = (
                descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            alive = False
        assert alive is not owns_group, (tree.identity, tree.reaped, alive)
        assert tree.identity.group_id == (tree.process.pid if owns_group else None)
        assert tree.reaped
    finally:
        try:
            if descendant.is_running():
                descendant.kill()
        except psutil.NoSuchProcess:
            pass
        descendant.wait(timeout=5)
        for child in children:
            child.wait(timeout=5)
            child.stdout.close()


@pytest.mark.parametrize(
    "identity",
    [
        "regular",
        "cli-contained-link",
        "cli-escaping-link",
        "manifest-contained-link",
        "manifest-escaping-link",
        "manifest-malformed",
        "manifest-list",
        "manifest-wrong-name",
        "manifest-missing",
        "cli-missing",
        "manifest-directory",
        "cli-directory",
        "package-escape",
    ],
)
def test_local_playwright_requires_regular_contained_identity_files(tmp_path, identity):
    package = tmp_path / "node_modules/@playwright/test"
    package.mkdir(parents=True)
    manifest = package / "package.json"
    cli = package / "cli.js"
    manifest.write_text('{"name":"@playwright/test"}', encoding="utf-8")
    cli.write_text("process.exit(0);", encoding="utf-8")
    target = cli if identity.startswith("cli-") else manifest
    if identity.endswith("link"):
        destination = (package if "contained" in identity else tmp_path) / "actual"
        target.rename(destination)
        target.symlink_to(destination)
    elif identity.endswith("missing") or identity.endswith("directory"):
        target.unlink()
        if identity.endswith("directory"):
            target.mkdir()
    elif identity == "manifest-malformed":
        manifest.write_text("{", encoding="utf-8")
    elif identity == "manifest-list":
        manifest.write_text("[]", encoding="utf-8")
    elif identity == "manifest-wrong-name":
        manifest.write_text('{"name":"foreign"}', encoding="utf-8")
    elif identity == "package-escape":
        outside = tmp_path / "outside-package"
        package.rename(outside)
        package.symlink_to(outside, target_is_directory=True)
    if identity == "regular":
        assert build.local_playwright(tmp_path) == cli
    else:
        with pytest.raises((OSError, RuntimeError, ValueError)):
            build.local_playwright(tmp_path)


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.parametrize("name", ["package.json", "cli.js"])
def test_local_playwright_rejects_fifo_before_reading(tmp_path, monkeypatch, name):
    package = tmp_path / "node_modules/@playwright/test"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name":"@playwright/test"}', encoding="utf-8"
    )
    (package / "cli.js").write_text("process.exit(0);", encoding="utf-8")
    target = package / name
    target.unlink()
    getattr(build.os, "mkfifo")(target)
    real_read = Path.read_text

    def no_fifo_read(path, *args, **kwargs):
        assert path != target, "resolver attempted to open a non-regular identity"
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_fifo_read)
    with pytest.raises(RuntimeError):
        build.local_playwright(tmp_path)


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
def test_gate_cleanup_waits_for_immediate_parent_lingering_descendant(
    tmp_path, monkeypatch
):
    mkdtemp = build.tempfile.mkdtemp
    monkeypatch.setattr(
        build.tempfile, "mkdtemp", lambda **kwargs: mkdtemp(dir=tmp_path, **kwargs)
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "committed.txt").write_text("exact source\n", encoding="utf-8")
    for command in (
        ["git", "init", "-b", "fixture"],
        ["git", "config", "user.name", "Gate Fixture"],
        ["git", "config", "user.email", "fixture@localhost"],
        ["git", "add", "."],
        ["git", "commit", "-m", "fixture"],
    ):
        subprocess.run(command, cwd=source, check=True, capture_output=True)
    sha = (
        subprocess
        .check_output(["git", "rev-parse", "HEAD"], cwd=source)
        .decode("ascii")
        .strip()
    )
    package = source / "node_modules/@playwright/test"
    package.mkdir(parents=True)
    (source / "apps/desktop/node_modules").mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name":"@playwright/test"}', encoding="utf-8"
    )
    (package / "cli.js").write_text("process.exit(0);\n", encoding="utf-8")
    descendant_file = tmp_path / "descendant.pid"
    executable = tmp_path / "npm"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import subprocess,sys\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"Path({str(descendant_file)!r}).write_text(str(p.pid), encoding='ascii')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    real_which = build.shutil.which
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name: str(executable) if name == "npm" else real_which(name),
    )
    real_popen = subprocess.Popen

    def parent_exited(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if args[0][0] == str(executable):
            process.wait(timeout=5)
        return process

    monkeypatch.setattr(subprocess, "Popen", parent_exited)
    real_remove = build.shutil.rmtree
    removed = []

    def remove_only_after_quiescence(root, *args, **kwargs):
        assert Path(root).is_dir()
        try:
            descendant = (
                psutil.Process(int(descendant_file.read_text(encoding="ascii")))
                if descendant_file.exists()
                else None
            )
            alive = (
                descendant is not None
                and descendant.is_running()
                and descendant.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.NoSuchProcess:
            alive = False
        assert not alive, "cleanup attempted with live descendant"
        removed.append(Path(root))
        return real_remove(root, *args, **kwargs)

    monkeypatch.setattr(build.shutil, "rmtree", remove_only_after_quiescence)
    monkeypatch.setattr(sys, "argv", ["worker", str(source), sha])
    try:
        assert build.main() == 0
        assert removed and not removed[0].exists()
    finally:
        if descendant_file.exists():
            try:
                descendant = psutil.Process(
                    int(descendant_file.read_text(encoding="ascii"))
                )
                descendant.kill()
                descendant.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass


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
