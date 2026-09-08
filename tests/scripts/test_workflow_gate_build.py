"""Platform policy tests use injected APIs, never a counterfeit host OS."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import psutil

from scripts import workflow_gate_build as build
from tools.managed_process import ManagedProcessTree, ProcessIdentity


PLAYWRIGHT_MANIFEST = {
    "name": "@playwright/test",
    "version": "1.62.1",
    "bin": {"playwright": "cli.js"},
}


def _write_playwright_package(
    root: Path, *, cli_source: str = "process.exit(0);\n"
) -> Path:
    package = root / "node_modules/@playwright/test"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps(PLAYWRIGHT_MANIFEST), encoding="utf-8"
    )
    (package / "cli.js").write_text(cli_source, encoding="utf-8")
    return package


def test_systemd_builder_reports_absence_instead_of_unwrapped_authority(monkeypatch):
    import shutil
    import tools.process_registry as registry_module

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert (
        registry_module._build_systemd_scope_argv(["/bin/sh", "-c", "fixture"], "owned")
        is None
    )


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.parametrize("use_pty", [False, True])
@pytest.mark.parametrize("binary_present", [False, True])
def test_registry_records_scope_only_from_exact_constructed_launch(
    tmp_path, monkeypatch, use_pty, binary_present
):
    import shutil
    from ptyprocess import PtyProcess
    import tools.process_registry as registry_module

    registry = registry_module.ProcessRegistry()
    monkeypatch.setattr(registry_module, "_is_supervised_gateway_process", lambda: True)
    # The availability probe succeeds; the later builder resolution can fail.
    monkeypatch.setattr(
        registry_module, "_systemd_run_user_scope_available", lambda: True
    )
    monkeypatch.setattr(
        shutil, "which", lambda name: "/fixture/systemd-run" if binary_present else None
    )
    monkeypatch.setattr(registry_module, "_find_shell", lambda: "/bin/sh")
    monkeypatch.setattr(registry_module.threading, "Thread", lambda **kwargs: Mock())
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)
    captured = []

    def launch(argv, **kwargs):
        captured.append((argv, kwargs))
        process = Mock(pid=4321)
        return (
            process
            if use_pty
            else SimpleNamespace(
                process=process, identity=ProcessIdentity(4321, None, 4321)
            )
        )

    monkeypatch.setattr(PtyProcess, "spawn", launch)
    monkeypatch.setattr(ManagedProcessTree, "spawn", launch)
    command = "printf '%s' 'literal;$(not-executed)'"
    session = registry.spawn_local(command, cwd=str(tmp_path), use_pty=use_pty)
    argv, kwargs = captured[0]
    assert argv[-3:] == ["/bin/sh", "-lic", f"set +m; {command}"]
    if binary_present:
        assert argv[:3] == ["/fixture/systemd-run", "--user", "--scope"]
        assert argv[argv.index("--unit") + 1] == f"hermes-worker-{session.id}"
        assert session.systemd_unit == argv[argv.index("--unit") + 1] + ".scope"
    else:
        assert argv == ["/bin/sh", "-lic", f"set +m; {command}"]
        assert session.systemd_unit == ""
    if not use_pty:
        assert kwargs["start_new_session"] is True


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("scope_stop", [True, False, "raises"])
@pytest.mark.parametrize("wait_mode", ["normal", "escalate", "fails"])
def test_scoped_registry_setup_failure_reaps_only_wrapper_and_reports_cleanup(
    tmp_path, monkeypatch, scope_stop, wait_mode
):
    import os
    import tools.process_registry as registry_module

    registry = registry_module.ProcessRegistry()
    setup_error = RuntimeError("reader failed")
    reader = Mock()
    reader.start.side_effect = setup_error
    monkeypatch.setattr(registry_module.threading, "Thread", lambda **kwargs: reader)
    monkeypatch.setattr(registry_module, "_is_supervised_gateway_process", lambda: True)
    monkeypatch.setattr(
        registry_module, "_systemd_run_user_scope_available", lambda: True
    )
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/fixture/systemd-run")
    stop_calls = []

    def stop(unit):
        stop_calls.append(unit)
        if scope_stop == "raises":
            raise OSError("scope stop unavailable")
        return scope_stop

    monkeypatch.setattr(registry_module, "_stop_systemd_unit", stop)
    spawn = ManagedProcessTree.spawn
    captured = []
    waits = []
    signals = []

    def real_wrapper(*args, **kwargs):
        assert args[0][:3] == ["/fixture/systemd-run", "--user", "--scope"]
        tree = spawn(
            [sys.executable, "-c", "import threading; threading.Event().wait(60)"],
            **kwargs,
        )
        wait = tree.process.wait
        captured.append((tree, tree.identity, wait))
        terminate, kill = tree.process.terminate, tree.process.kill

        def terminate_wrapper():
            signals.append("terminate")
            terminate()

        def kill_wrapper():
            signals.append("kill")
            kill()

        monkeypatch.setattr(tree.process, "terminate", terminate_wrapper)
        monkeypatch.setattr(tree.process, "kill", kill_wrapper)

        def bounded_wait(timeout=None):
            waits.append(timeout)
            if wait_mode == "fails" or (wait_mode == "escalate" and len(waits) == 1):
                raise subprocess.TimeoutExpired("owned wrapper", timeout)
            return wait(timeout=timeout)

        monkeypatch.setattr(tree.process, "wait", bounded_wait)
        return tree

    monkeypatch.setattr(ManagedProcessTree, "spawn", real_wrapper)
    killpg = Mock(wraps=getattr(os, "killpg"))
    monkeypatch.setattr(os, "killpg", killpg)
    try:
        with pytest.raises(RuntimeError) as caught:
            registry.spawn_local("fixture", cwd=str(tmp_path))
        tree, identity, wait = captured[0]
        assert stop_calls and stop_calls[0].startswith("hermes-worker-proc_")
        assert tree.identity is identity
        assert killpg.call_args_list == []
        assert registry._running == {}
        if scope_stop is True and wait_mode != "fails":
            assert caught.value is setup_error
        else:
            assert "cleanup failed" in str(caught.value)
            assert caught.value.__cause__ is setup_error
        expected = [tree.policy.term_grace_seconds]
        if wait_mode != "normal":
            expected.append(tree.policy.kill_grace_seconds)
        assert waits == expected
        assert signals == (
            ["terminate"] if wait_mode == "normal" else ["terminate", "kill"]
        )
        if wait_mode != "fails":
            assert tree.process.poll() is not None
    finally:
        for tree, _identity, wait in captured:
            tree.process.kill()
            wait(timeout=5)
            tree.process.stdout.close()


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("probe_race", [False, True])
def test_ordinary_registry_setup_failure_reaps_lingering_new_session_group(
    tmp_path, monkeypatch, probe_race
):
    import shutil
    import tools.process_registry as registry_module

    registry = registry_module.ProcessRegistry()
    monkeypatch.setattr(
        registry_module, "_is_supervised_gateway_process", lambda: probe_race
    )
    monkeypatch.setattr(
        registry_module, "_systemd_run_user_scope_available", lambda: True
    )
    monkeypatch.setattr(shutil, "which", lambda name: None)
    stopped = []
    monkeypatch.setattr(
        registry_module,
        "_stop_systemd_unit",
        lambda unit: stopped.append(unit) or False,
    )
    reader = Mock()
    reader.start.side_effect = RuntimeError("reader failed")
    monkeypatch.setattr(registry_module.threading, "Thread", lambda **kwargs: reader)
    spawn = ManagedProcessTree.spawn
    captured = []
    descendants = []
    child_code = "import threading; threading.Event().wait(60)"
    parent_code = (
        "import subprocess,sys;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print(p.pid,flush=True)"
    )

    def exited_wrapper(*args, **kwargs):
        tree = spawn([sys.executable, "-c", parent_code], **kwargs)
        captured.append(tree)
        descendants.append(psutil.Process(int(tree.process.stdout.readline())))
        tree.process.wait(timeout=5)
        return tree

    monkeypatch.setattr(ManagedProcessTree, "spawn", exited_wrapper)
    try:
        with pytest.raises(RuntimeError) as caught:
            registry.spawn_local("fixture", cwd=str(tmp_path))
        descendant = descendants[0]
        assert (
            not descendant.is_running() or descendant.status() == psutil.STATUS_ZOMBIE
        )
        assert captured[0].reaped
        assert str(caught.value) == "reader failed"
        assert stopped == []
    finally:
        for descendant in descendants:
            try:
                descendant.kill()
            except psutil.NoSuchProcess:
                pass
            descendant.wait(timeout=5)
        for tree in captured:
            tree.process.wait(timeout=5)
            tree.process.stdout.close()


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
    package = _write_playwright_package(tmp_path)
    manifest = package / "package.json"
    cli = package / "cli.js"
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


@pytest.mark.parametrize(
    ("identity", "manifest"),
    [
        (
            "missing-version",
            '{"name":"@playwright/test","bin":{"playwright":"cli.js"}}',
        ),
        (
            "wrong-version",
            '{"name":"@playwright/test","version":"0.0.0","bin":{"playwright":"cli.js"}}',
        ),
        ("missing-bin", '{"name":"@playwright/test","version":"1.62.1"}'),
        (
            "wrong-bin",
            '{"name":"@playwright/test","version":"1.62.1","bin":{"playwright":"elsewhere.js"}}',
        ),
        (
            "alternate-bin-target",
            '{"name":"@playwright/test","version":"1.62.1","bin":{"playwright":"./cli.js"}}',
        ),
        (
            "duplicate-name",
            '{"name":"foreign","name":"@playwright/test","version":"1.62.1","bin":{"playwright":"cli.js"}}',
        ),
        (
            "duplicate-version",
            '{"name":"@playwright/test","version":"0.0.0","version":"1.62.1","bin":{"playwright":"cli.js"}}',
        ),
        (
            "duplicate-bin",
            '{"name":"@playwright/test","version":"1.62.1","bin":{"other":"elsewhere.js"},"bin":{"playwright":"cli.js"}}',
        ),
        (
            "non-object-bin",
            '{"name":"@playwright/test","version":"1.62.1","bin":"cli.js"}',
        ),
        (
            "alternate-bin",
            '{"name":"@playwright/test","version":"1.62.1","bin":{"playwright-test":"cli.js"}}',
        ),
    ],
)
def test_local_playwright_rejects_unproven_manifest_identity(
    tmp_path, identity, manifest
):
    package = _write_playwright_package(tmp_path)
    (package / "package.json").write_text(manifest, encoding="utf-8")

    with pytest.raises((OSError, RuntimeError, ValueError)) as failure:
        build.local_playwright(tmp_path)

    assert len(str(failure.value)) <= 120, identity
    assert "0.0.0" not in str(failure.value)
    assert "elsewhere.js" not in str(failure.value)


@pytest.mark.parametrize(
    "identity",
    ["package-contained-link", "scope-contained-link", "scope-escaping-link"],
)
def test_local_playwright_rejects_linked_package_directory_chain(tmp_path, identity):
    package = _write_playwright_package(tmp_path)
    modules = tmp_path / "node_modules"
    scope = modules / "@playwright"
    if identity == "package-contained-link":
        destination = modules / "actual-playwright-test"
        package.rename(destination)
        package.symlink_to(destination, target_is_directory=True)
    else:
        destination = (
            modules / "actual-playwright-scope"
            if identity == "scope-contained-link"
            else tmp_path / "outside-playwright-scope"
        )
        scope.rename(destination)
        scope.symlink_to(destination, target_is_directory=True)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        build.local_playwright(tmp_path)


def test_local_playwright_rejects_zero_exit_canary_before_launch(tmp_path):
    marker = tmp_path / "canary-ran"
    package = _write_playwright_package(
        tmp_path,
        cli_source=(
            "require('node:fs').writeFileSync(process.env.PLAYWRIGHT_CANARY, "
            "'executed');\n"
        ),
    )
    (package / "package.json").write_text(
        '{"name":"@playwright/test","version":"0.0.0-review",'
        '"bin":{"playwright":"alternate.js"}}',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PLAYWRIGHT_CANARY"] = str(marker)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        executable = build.local_playwright(tmp_path)
        subprocess.run([shutil.which("node"), str(executable)], env=env, check=True)

    assert not marker.exists()


@pytest.mark.macos_only if sys.platform == "darwin" else pytest.mark.linux_only
@pytest.mark.parametrize("name", ["package.json", "cli.js"])
def test_local_playwright_rejects_fifo_before_reading(tmp_path, monkeypatch, name):
    package = _write_playwright_package(tmp_path)
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
    _write_playwright_package(source)
    (source / "apps/desktop/node_modules").mkdir(parents=True)
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
