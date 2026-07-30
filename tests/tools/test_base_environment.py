"""Tests for BaseEnvironment unified execution model.

Tests _wrap_command(), _extract_cwd_from_output(), _embed_stdin_heredoc(),
init_session() failure handling, and the CWD marker contract.
"""

from unittest.mock import MagicMock

import pytest

from tools.environments.base import (
    BaseEnvironment,
    _BoundedOutputCollector,
    _RawSentinelFilter,
)


class _TestableEnv(BaseEnvironment):
    """Concrete subclass for testing base class methods."""

    def __init__(self, cwd="/tmp", timeout=10):
        super().__init__(cwd=cwd, timeout=timeout)

    def _run_bash(
        self,
        cmd_string,
        *,
        login=False,
        timeout=120,
        stdin_data=None,
        clean=False,
    ):
        raise NotImplementedError("Use mock")

    def cleanup(self):
        pass


def _ready_local_env(tmp_path, snapshot_text, *, inherited_env=None):
    """Build a real LocalEnvironment around a caller-controlled snapshot."""
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment.__new__(LocalEnvironment)
    BaseEnvironment.__init__(
        env,
        cwd=str(tmp_path),
        timeout=10,
        env=inherited_env,
    )
    snapshot = tmp_path / f"{env._session_id}-snapshot.sh"
    snapshot.write_text(snapshot_text)
    snapshot.chmod(0o600)
    env._snapshot_path = str(snapshot)
    env._set_session_mode("snapshot")
    return env


def _snapshot_update_script(env):
    """Extract the production-generated snapshot update from a wrapped command."""
    wrapped_lines = env._wrap_command(":", env.cwd).splitlines()
    snapshot_start = next(
        i for i, line in enumerate(wrapped_lines) if line == "__hermes_ec=$?"
    ) + 1
    snapshot_end = next(
        i for i, line in enumerate(wrapped_lines[snapshot_start:], snapshot_start)
        if line.startswith("builtin printf ")
    )
    return "\n".join(wrapped_lines[snapshot_start:snapshot_end])


def _bootstrap_script(env):
    """Capture the production-generated init_session bootstrap script."""
    captured = {}

    def fake_run_bash(cmd_string, **kwargs):
        if kwargs.get("login"):
            captured.setdefault("cmd", cmd_string)
            raise RuntimeError("stop after capture")
        mock = MagicMock()
        mock.poll.return_value = 0
        mock.returncode = 0
        mock.stdout = iter([])
        return mock

    env._run_bash = fake_run_bash  # type: ignore[assignment]
    env.init_session()
    return captured["cmd"]


class TestBoundedOutputCollector:
    def test_large_stream_retains_bounded_head_and_tail(self):
        collector = _BoundedOutputCollector(1_000)
        collector.append("HEAD-SENTINEL\n")
        for _ in range(2_000):
            collector.append("x" * 4_096)
        collector.append("\nTAIL-SENTINEL")

        rendered = collector.render()

        assert collector.total_chars > 8_000_000
        assert collector.buffered_chars <= 1_000
        assert len(rendered) <= 1_000
        assert rendered.startswith("HEAD-SENTINEL")
        assert rendered.endswith("TAIL-SENTINEL")
        assert "[OUTPUT TRUNCATED" in rendered

    def test_small_stream_is_unchanged(self):
        collector = _BoundedOutputCollector(100)
        collector.append("hello ")
        collector.append("world")

        assert collector.render() == "hello world"

    def test_required_status_suffix_stays_inside_limit(self):
        collector = _BoundedOutputCollector(120)
        collector.append("A" * 10_000)

        rendered = collector.render(suffix="\n[Command timed out after 1s]")

        assert len(rendered) <= 120
        assert rendered.endswith("[Command timed out after 1s]")
        assert "[OUTPUT TRUNCATED" in rendered


class TestRawSentinelFilter:
    def test_detects_and_strips_exact_sentinel_split_across_chunks(self):
        sentinel_filter = _RawSentinelFilter("__HERMES_GUARD_PASSED_nonce__")

        visible = "".join(
            (
                sentinel_filter.feed("before\n__HERMES_GUARD_"),
                sentinel_filter.feed("PASSED_nonce__\nafter"),
                sentinel_filter.finish(),
            )
        )

        assert visible == "beforeafter"
        assert sentinel_filter.seen is True

    def test_only_first_exact_sentinel_is_control_data(self):
        sentinel = "__HERMES_GUARD_PASSED_nonce__"
        sentinel_filter = _RawSentinelFilter(sentinel)

        visible = "".join(
            (
                sentinel_filter.feed(f"\n{sentinel}\n"),
                sentinel_filter.feed(f"\n{sentinel}\n"),
                sentinel_filter.finish(),
            )
        )

        assert visible == f"\n{sentinel}\n"
        assert sentinel_filter.seen is True

    def test_partial_sentinel_prefix_is_flushed_losslessly_at_eof(self):
        sentinel_filter = _RawSentinelFilter("__HERMES_GUARD_PASSED_nonce__")

        visible = sentinel_filter.feed("user output\n__HERMES_GUARD_")
        visible += sentinel_filter.finish()

        assert visible == "user output\n__HERMES_GUARD_"
        assert sentinel_filter.seen is False

    def test_sdk_normalized_sentinel_without_final_newline_is_control_data(self):
        sentinel = "__HERMES_GUARD_PASSED_nonce__"
        sentinel_filter = _RawSentinelFilter(sentinel)

        visible = sentinel_filter.feed(f"\n{sentinel}")
        visible += sentinel_filter.finish()

        assert visible == ""
        assert sentinel_filter.seen is True

    def test_nonmatching_final_sentinel_prefix_is_flushed_losslessly(self):
        sentinel = "__HERMES_GUARD_PASSED_nonce__"
        sentinel_filter = _RawSentinelFilter(sentinel)

        visible = sentinel_filter.feed(f"\n{sentinel[:-1]}")
        visible += sentinel_filter.finish()

        assert visible == f"\n{sentinel[:-1]}"
        assert sentinel_filter.seen is False

    @pytest.mark.parametrize("max_bytes", [1, 32])
    @pytest.mark.parametrize("stream_type", [bytes, str])
    def test_wait_detects_sentinel_before_bounded_rendering(
        self, monkeypatch, max_bytes, stream_type
    ):
        sentinel = "__HERMES_GUARD_PASSED_nonce__"
        pieces = [
            "A" * 100,
            "\n__HERMES_GUARD_",
            "PASSED_nonce__\n",
            "Z" * 100,
        ]
        proc = MagicMock()
        proc.poll.return_value = 125
        proc.returncode = 125
        proc.stdout = iter(
            piece.encode() if stream_type is bytes else piece for piece in pieces
        )
        monkeypatch.setattr(
            "tools.tool_output_limits.get_max_bytes", lambda: max_bytes
        )

        result = _TestableEnv()._wait_for_process(
            proc,
            bounded_capture=True,
            control_sentinel=sentinel,
        )

        assert result["control_seen"] is True
        assert result["returncode"] == 125
        assert sentinel not in result["output"]
        assert len(result["output"]) <= max_bytes


class TestWrapCommand:
    def test_basic_shape(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hello", "/tmp")

        assert "source" in wrapped
        assert "cd -- /tmp" in wrapped or "cd -- '/tmp'" in wrapped
        assert "eval 'echo hello'" in wrapped
        assert "__hermes_ec=$?" in wrapped
        assert "export -p >" in wrapped
        # cwd travels via the stdout marker only — no temp-file write.
        assert "pwd -P >" not in wrapped
        assert env._cwd_marker in wrapped
        assert "exit $__hermes_ec" in wrapped

    def test_no_snapshot_skips_source(self):
        env = _TestableEnv()
        env._snapshot_ready = False
        wrapped = env._wrap_command("echo hello", "/tmp")

        assert "source" not in wrapped

    def test_single_quote_escaping(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo 'hello world'", "/tmp")

        assert "eval 'echo '\\''hello world'\\'''" in wrapped

    def test_tilde_not_quoted(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~")

        assert "cd -- ~" in wrapped
        assert "cd -- '~'" not in wrapped

    def test_tilde_subpath_with_spaces_uses_home_and_quotes_suffix(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~/my repo")

        assert 'cd -- "$HOME"/\'my repo\'' in wrapped
        assert "cd -- ~/my repo" not in wrapped

    def test_tilde_slash_maps_to_home(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~/")

        assert 'cd -- "$HOME"' in wrapped
        assert "cd -- ~/" not in wrapped

    def test_hyphen_prefixed_workdir_is_passed_after_double_dash(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("pwd", "-demo")

        assert "builtin cd -- -demo || { POSIXLY_CORRECT=1; \\exit 125; }" in wrapped

    def test_ready_cd_failure_uses_guard_failure(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "/nonexistent")

        assert (
            "builtin cd -- /nonexistent || "
            "{ POSIXLY_CORRECT=1; \\exit 125; }"
        ) in wrapped

    def test_degraded_cd_failure_exit_126(self):
        env = _TestableEnv()
        env._snapshot_ready = False
        wrapped = env._wrap_command("ls", "/nonexistent")

        assert "builtin cd -- /nonexistent || builtin exit 126" in wrapped


class TestAtomicSnapshotWrite:
    """Regression for #38249: concurrent terminal calls in one session both
    source AND rewrite the shared env snapshot. A non-atomic ``export -p >
    snap`` truncates-then-writes in place, so a concurrent ``source snap`` can
    read a half-written file and embed ``declare -x``/``export`` fragments into
    PATH, breaking ``ls``/``git``/``tr`` with command-not-found. The write must
    assemble in a temp file and ``mv -f`` it into place (mv is atomic on POSIX
    same-fs), so a reader sees the old-or-new complete file, never a torn one.
    """

    def test_wrap_command_uses_atomic_temp_then_mv(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hi", "/tmp")
        # Env dump goes to a temp file, not directly over the live snapshot.
        assert "export -p > " in wrapped
        assert ".tmp." in wrapped
        # Then an atomic rename onto the real snapshot path.
        assert "mv -f " in wrapped
        # The env-dump must NOT write the live snapshot in place (the bug).
        snap = env._snapshot_path
        assert f"export -p > {snap} " not in wrapped
        assert f"export -p > '{snap}'" not in wrapped

    def test_temp_path_uses_collision_safe_mktemp_template(self):
        """The temp name must come from collision-safe creation, not a shell
        PID variable. macOS ships Bash 3.2, where ``$BASHPID`` is unset; all
        ``&``-launched writers would otherwise resolve the same temp path."""
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hi", "/tmp")
        assert "mktemp " in wrapped
        assert ".tmp.XXXXXX" in wrapped
        assert "$BASHPID" not in wrapped
        assert ".tmp.$$" not in wrapped

    def test_temp_template_with_spaces_is_quoted(self):
        """The mktemp template remains one shell word when the snapshot path
        contains spaces (including rewritten Windows/Git-Bash paths)."""
        env = _TestableEnv()
        env._snapshot_ready = True
        env._snapshot_path = "/tmp/has space/hermes-snap-x.sh"
        wrapped = env._wrap_command("echo hi", "/tmp")
        assert "mktemp '/tmp/has space/hermes-snap-x.sh.tmp.XXXXXX'" in wrapped
        assert "mktemp /tmp/has space/hermes-snap-x.sh.tmp.XXXXXX" not in wrapped

    def test_wrap_command_mv_chained_on_export_success(self):
        """A failed/partial ``export -p`` must NOT mv a torn temp over a good
        snapshot.  The mv is chained with ``&&`` on the export, and the temp is
        removed on failure."""
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hi", "/tmp")
        assert r"\builtin export -p > " in wrapped
        assert r"&& \builtin command mv -f " in wrapped
        assert "rm -f " in wrapped  # temp cleanup on failure

    def test_init_session_bootstrap_also_uses_collision_safe_temp(self):
        """The init_session bootstrap (first snapshot write) is the same shared
        file a concurrent command could source, so it needs the same atomic,
        collision-safe temp creation."""
        env = _TestableEnv()
        captured = {}

        def fake_run_bash(cmd_string, **kwargs):
            if kwargs.get("login"):
                captured.setdefault("cmd", cmd_string)
                raise RuntimeError("stop after capture")
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        env._run_bash = fake_run_bash  # type: ignore[assignment]
        try:
            env.init_session()
        except Exception:
            pass
        boot = captured.get("cmd", "")
        assert ".tmp." in boot and "mv -f " in boot, boot
        assert "mktemp " in boot and ".tmp.XXXXXX" in boot
        assert "$BASHPID" not in boot
        assert ".tmp.$$" not in boot

    def test_snapshot_writes_use_private_umask_after_user_command(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hi", "/tmp")

        assert "umask 077" in wrapped
        assert wrapped.index("eval 'echo hi'") < wrapped.index("umask 077")
        assert wrapped.index("umask 077") < wrapped.index("export -p >")

    def test_failed_snapshot_update_under_errexit_preserves_wrapper_contract(
        self, tmp_path
    ):
        """Snapshot publication is metadata bookkeeping: even under a user's
        ``set -e``, its failure must not skip the CWD marker or replace the
        successful user-command status."""
        import shlex
        import shutil

        snap = tmp_path / "snapshot.sh"
        snap.write_text("export GOOD=1\n")
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        failing_mktemp = fake_bin / "mktemp"
        failing_mktemp.write_text("#!/bin/sh\nexit 23\n")
        failing_mktemp.chmod(0o755)
        rm = shutil.which("rm")
        assert rm
        (fake_bin / "rm").symlink_to(rm)
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        env._snapshot_ready = True
        wrapped = env._wrap_command(
            "set -e; printf 'USER_COMMAND_COMPLETED\\n'", str(tmp_path)
        )

        proc = self._run_real_bash(
            f"PATH={shlex.quote(str(fake_bin))}; export PATH\n{wrapped}"
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "USER_COMMAND_COMPLETED" in proc.stdout
        assert env._cwd_marker in proc.stdout
        assert snap.read_text() == "export GOOD=1\n"

    def test_readonly_shadow_functions_fail_snapshot_update_closed(self, tmp_path):
        """If the sanitizer cannot remove its dispatcher shadows, no protected
        operation may run; wrapper marker/status behavior remains intact."""
        import shlex

        _q = shlex.quote
        snap = tmp_path / "snapshot.sh"
        snap.write_text("export GOOD=1\n")
        marker = tmp_path / "intercepted"
        shadow = "\n".join(
            f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
            for name in ("builtin", "set", "unset")
        )
        user_command = (
            "set -e\n"
            f"{shadow}\n"
            "readonly -f builtin set unset\n"
            "printf 'USER_COMMAND_COMPLETED\\n'"
        )
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        env._snapshot_ready = True

        proc = self._run_real_bash(env._wrap_command(user_command, str(tmp_path)))

        assert proc.returncode == 125, proc.stdout + proc.stderr
        assert "USER_COMMAND_COMPLETED" in proc.stdout
        assert not marker.exists(), "failed sanitizer allowed an intercepted operation"
        assert snap.read_text() == "export GOOD=1\n"

    def test_init_session_bootstrap_uses_private_umask(self):
        env = _TestableEnv()
        captured = {}

        def fake_run_bash(cmd_string, **kwargs):
            if kwargs.get("login"):
                captured.setdefault("cmd", cmd_string)
                raise RuntimeError("stop after capture")
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        env._run_bash = fake_run_bash  # type: ignore[assignment]
        try:
            env.init_session()
        except Exception:
            pass
        boot = captured.get("cmd", "")
        assert "umask 077" in boot
        assert boot.index("umask 077") < boot.index("export -p >")

    def test_bootstrap_assembly_failure_preserves_existing_snapshot(self, tmp_path):
        """Any failed assembly step must leave the prior snapshot untouched,
        remove its private temp, and make bootstrap report failure."""
        from pathlib import Path
        import shlex
        import shutil

        snap = tmp_path / "snapshot.sh"
        snap.write_text("export GOOD=1\n")
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in ("mktemp", "mv", "rm"):
            target = shutil.which(name)
            assert target
            (fake_bin / name).symlink_to(target)
        failing_awk = fake_bin / "awk"
        failing_awk.write_text("#!/bin/sh\nexit 23\n")
        failing_awk.chmod(0o755)

        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        bootstrap = _bootstrap_script(env)
        proc = self._run_real_bash(
            "exit () { printf 'exit-shadowed\\n'; return 0; }\n"
            f"PATH={shlex.quote(str(fake_bin))}; export PATH\n{bootstrap}"
        )

        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "exit-shadowed" not in proc.stdout
        assert snap.read_text() == "export GOOD=1\n"
        assert not list(Path(tmp_path).glob("snapshot.sh.tmp.*"))

    def test_bootstrap_bypasses_login_shell_operation_functions(self, tmp_path):
        """Login profiles may define functions with coreutils names. Hermes
        must bypass them internally while preserving unrelated functions."""
        import shlex

        _q = shlex.quote
        snap = tmp_path / "snapshot.sh"
        marker = tmp_path / "shadowed"
        shadow = "\n".join(
            f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
            for name in (
                "command",
                "mktemp",
                "mv",
                "rm",
                "umask",
                "export",
            )
        )
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        bootstrap = _bootstrap_script(env)

        proc = self._run_real_bash(
            f"export BOOTSTRAP_SENTINEL=1\n{shadow}\n{bootstrap}"
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not marker.exists(), "login-shell function intercepted bootstrap"
        check = self._run_real_bash(
            f"source {_q(str(snap))} >/dev/null 2>&1 && "
            "[ \"$BOOTSTRAP_SENTINEL\" = 1 ] && "
            "declare -F command >/dev/null && echo OK || echo BROKEN"
        )
        assert "OK" in check.stdout, check.stdout + check.stderr

    @pytest.mark.parametrize("exit_trap", [False, True])
    def test_dispatcher_functions_reject_lossy_bootstrap_and_use_fallback(
        self, tmp_path, exit_trap
    ):
        """Protected dispatcher profiles degrade explicitly without
        publishing a lossy snapshot or selecting per-command login."""
        import shlex
        import os
        import shlex
        import subprocess

        _q = shlex.quote
        snap = tmp_path / "snapshot.sh"
        marker = tmp_path / "intercepted"
        forgery_marker = tmp_path / "forgery-attempted"
        shadow = (
            f"builtin () {{ printf '%s\\n' builtin >> {_q(str(marker))}; return 91; }}\n"
            + "\n".join(
                f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
                for name in ("set", "unset", "cd")
            )
        )
        nonce_forgery = (
            "if [[ $BASH_EXECUTION_STRING =~ "
            "(__HERMES_SNAPSHOT_PROFILE_FALLBACK_[0-9a-f]{32}__) ]]; then\n"
            f"  printf 'attempted\\n' > {_q(str(forgery_marker))}\n"
            "  readonly __hermes_snapshot_profile_fallback_marker="
            "\"${BASH_REMATCH[1]}\"\n"
            "  readonly -p\n"
            "  exit 78\n"
            "fi\n"
        )
        login_profile = (
            "export PROFILE_SENTINEL=profile-loaded\n"
            f"{nonce_forgery}{shadow}\n"
            "ordinary () { printf 'ordinary-loaded\\n'; }\n"
            + ("trap 'exit 0' EXIT\n" if exit_trap else "")
        )
        login_calls = []

        class LoginProfileEnv(_TestableEnv):
            def _run_bash(
                self,
                cmd_string,
                *,
                login=False,
                timeout=120,
                stdin_data=None,
                clean=False,
            ):
                login_calls.append(login)
                script = f"{login_profile}{cmd_string}" if login else cmd_string
                proc = subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                )
                if stdin_data is not None:
                    assert proc.stdin is not None
                    try:
                        proc.stdin.write(stdin_data)
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
                return proc

        env = LoginProfileEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        target_cwd = tmp_path / "target-cwd"
        target_cwd.mkdir()

        env.init_session()

        assert env._snapshot_ready is False
        assert env._prefer_nonlogin is True
        assert env._session_mode == "degraded_nonlogin"
        assert not snap.exists(), "bootstrap published a lossy function snapshot"
        assert not marker.exists(), "presence detection invoked a shadow function"
        assert not forgery_marker.exists(), "profile read the bootstrap nonce"

        result = env.execute(
            "printf 'ENV=%s\\n' \"$PROFILE_SENTINEL\"; "
            "ordinary; "
            "declare -F builtin set unset cd >/dev/null && printf 'FUNCTIONS=present\\n'; "
            "printf 'PWD=%s\\n' \"$PWD\"",
            cwd=str(target_cwd),
        )

        assert result["returncode"] == 0, result["output"]
        assert "clean non-login shell" in result["output"]
        assert "ENV=" in result["output"]
        assert "ordinary-loaded" not in result["output"]
        assert "FUNCTIONS=present" not in result["output"]
        assert f"PWD={target_cwd}" in result["output"]
        assert env.cwd == str(target_cwd)
        assert login_calls.count(True) == 1
        assert login_calls[-1] is False
        assert not snap.exists(), "fallback execute published a lossy snapshot"
        assert not marker.exists(), "snapshot internals invoked a profile function"
        assert not forgery_marker.exists(), "fallback exposed a bootstrap nonce"

        bootstrap = _bootstrap_script(_TestableEnv(cwd=str(tmp_path)))
        assert "__HERMES_SNAPSHOT_PROFILE_FALLBACK_" not in bootstrap

    def test_readonly_shadow_functions_fail_bootstrap_closed(self, tmp_path):
        """A login profile can make dispatcher shadows readonly. Bootstrap
        must reject the snapshot instead of invoking those functions."""
        import shlex

        _q = shlex.quote
        snap = tmp_path / "snapshot.sh"
        snap.write_text("export GOOD=1\n")
        marker = tmp_path / "intercepted"
        shadow = "\n".join(
            f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
            for name in ("builtin", "set", "unset")
        )
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        bootstrap = _bootstrap_script(env)

        proc = self._run_real_bash(
            f"{shadow}\nreadonly -f builtin set unset\n{bootstrap}"
        )

        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert not marker.exists(), "failed sanitizer allowed an intercepted operation"
        assert snap.read_text() == "export GOOD=1\n"

    @staticmethod
    def _run_real_bash(script):
        import subprocess
        return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)


class TestAtomicSnapshotConcurrencyBehavioral:
    """Behavioral regression for #38249 — actually EXECUTES the generated
    snapshot write/read concurrently and asserts the file never tears.

    The string-inspection tests prove the right script is emitted; this proves
    the emitted script's guarantee holds under real concurrency: N concurrent
    writers + readers, and the snapshot is ALWAYS a complete, parseable env
    dump — never truncated mid-line with a ``declare -x`` / ``export`` fragment
    that would corrupt PATH. The writer loop executes the snapshot-update
    fragment generated by :meth:`BaseEnvironment._wrap_command`, rather than a
    test-local copy of the implementation.
    """

    def _run(self, script):
        import subprocess
        return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)

    def test_concurrent_writes_never_tear_the_snapshot(self, tmp_path):
        import shutil
        if not shutil.which("bash"):
            import pytest
            pytest.skip("bash required")
        import shlex
        snap = str(tmp_path / "hermes-snap-x.sh")
        _q = shlex.quote
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = snap
        env._snapshot_ready = True
        snapshot_update = _snapshot_update_script(env)

        # One writer iteration executes the exact atomic sequence emitted by
        # production, under the system's real Bash.
        writers = [
            (
                f"export HERMES_SNAPSHOT_WRITER={writer_id}; "
                "for i in $(seq 1 80); do "
                "export BIG_$i=$(head -c 600 /dev/zero | tr '\\0' x); "
                f"if ! {{ {snapshot_update}; }}; then exit 91; fi; "
                "done"
            )
            for writer_id in range(1, 5)
        ]
        # Reader: repeatedly source the snapshot and check PATH never absorbs
        # an `export `/`declare -x` fragment (the corruption signature).
        reader = (
            "export PATH=/usr/bin:/bin; "
            "for i in $(seq 1 160); do "
            f"( if ! source {_q(snap)} >/dev/null 2>&1; then echo BROKEN; exit; fi; "
            "case \"$PATH\" in *'declare -x'*|*'export '*) echo CORRUPT;; esac ); "
            "done"
        )
        self._run(f"echo 'export HERMES_SNAPSHOT_SEED=1' > {_q(snap)}")
        # 4 concurrent writers + 4 readers, repeated.
        jobs = writers + [reader] * 4
        launch = " ".join(
            f"( {job} ) & __hermes_test_pids=\"$__hermes_test_pids $!\";"
            for job in jobs
        )
        wait_each = (
            "__hermes_test_status=0; "
            "for pid in $__hermes_test_pids; do "
            "wait \"$pid\" || __hermes_test_status=1; done; "
            "exit $__hermes_test_status"
        )
        procs = [
            self._run(f"__hermes_test_pids=''; {launch} {wait_each}")
            for _ in range(3)
        ]
        failures = [
            f"rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr!r}"
            for p in procs
            if p.returncode != 0 or "CORRUPT" in p.stdout or "BROKEN" in p.stdout
        ]
        assert not failures, "snapshot writer/read failure: " + "; ".join(failures)
        final = self._run(
            f"source {_q(snap)} >/dev/null 2>&1 && "
            "[ -n \"${HERMES_SNAPSHOT_WRITER:-}\" ] && "
            "[ -z \"${HERMES_SNAPSHOT_SEED+x}\" ] && echo OK || echo BROKEN"
        )
        assert "OK" in final.stdout, f"final snapshot not sourceable: {final.stdout} {final.stderr}"

    def test_failed_export_does_not_destroy_good_snapshot(self, tmp_path):
        """If ``export -p`` fails, the ``&&``-chained mv must NOT clobber the
        existing good snapshot."""
        import shutil
        if not shutil.which("bash"):
            import pytest
            pytest.skip("bash required")
        import shlex
        snap = str(tmp_path / "snap.sh")
        _q = shlex.quote
        self._run(f"echo 'export GOOD=1' > {_q(snap)}")  # seed good snapshot
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        marker = tmp_path / "mktemp-ran"
        bad_temp = tmp_path / "missing" / "snapshot.tmp"
        fake_mktemp = fake_bin / "mktemp"
        fake_mktemp.write_text(
            "#!/bin/sh\n"
            f": > {_q(str(marker))}\n"
            f"printf '%s\\n' {_q(str(bad_temp))}\n"
        )
        fake_mktemp.chmod(0o755)
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = snap
        env._snapshot_ready = True
        snapshot_update = _snapshot_update_script(env)

        out = self._run(f"PATH={_q(str(fake_bin))}; {snapshot_update}")

        assert out.returncode != 0, out.stdout + out.stderr
        assert marker.exists(), "production-generated mktemp path did not execute"
        out = self._run(f"cat {_q(snap)}")
        assert "export GOOD=1" in out.stdout, "good snapshot was destroyed by a failed export"

    def test_snapshot_operations_bypass_persisted_functions_and_aliases(self, tmp_path):
        """User shell customizations may use internal command names, but they
        must not intercept Hermes' allocator, rename, or cleanup operations."""
        import shlex

        _q = shlex.quote
        for kind in ("functions", "aliases"):
            case_dir = tmp_path / kind
            case_dir.mkdir()
            snap = case_dir / "snapshot.sh"
            marker = case_dir / "shadowed"
            if kind == "functions":
                shadow = "\n".join(
                    f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
                    for name in ("command", "mktemp", "mv", "rm", "umask", "export")
                )
            else:
                shadow = "\n".join(
                    f"alias {name}={_q(f'printf {name} >> {_q(str(marker))}; false #')}"
                    for name in ("command", "mktemp", "mv", "rm", "umask", "export")
                )
            snap.write_text(
                f"builtin export SHADOW_SEED=1\n{shadow}\nshopt -s expand_aliases\n"
            )
            env = _TestableEnv(cwd=str(case_dir))
            env._snapshot_path = str(snap)
            env._snapshot_ready = True

            proc = self._run(
                env._wrap_command("builtin export UPDATED=1", str(case_dir))
            )

            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert not marker.exists(), f"{kind} intercepted an internal operation"
            check = self._run(
                f"source {_q(str(snap))} >/dev/null 2>&1 && "
                "[ \"$UPDATED\" = 1 ] && echo OK || echo BROKEN"
            )
            assert "OK" in check.stdout, f"{kind} prevented snapshot replacement: {check.stdout}"

    def test_snapshot_update_bypasses_builtin_set_and_unset_functions(self, tmp_path):
        """The internal update boundary must neutralize functions that can
        intercept the builtins used to bypass all other shell shadows."""
        import shlex

        _q = shlex.quote
        snap = tmp_path / "snapshot.sh"
        marker = tmp_path / "shadowed"
        snap.write_text("export GOOD=1\n")
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snap)
        env._snapshot_ready = True
        snapshot_update = _snapshot_update_script(env)
        shadow = "\n".join(
            f"{name} () {{ printf '%s\\n' {name} >> {_q(str(marker))}; return 0; }}"
            for name in ("builtin", "set", "unset")
        )

        proc = self._run(
            f"{shadow}\nexport UPDATED=1\n{snapshot_update}\n"
            "__hermes_test_update_rc=$?\n"
            "declare -F builtin set unset >/dev/null && exit 92\n"
            "exit $__hermes_test_update_rc"
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not marker.exists(), "shell function intercepted snapshot update"
        check = self._run(
            f"source {_q(str(snap))} >/dev/null 2>&1 && "
            '[ "$UPDATED" = 1 ] && echo OK || echo BROKEN'
        )
        assert "OK" in check.stdout, check.stdout + check.stderr


class TestSnapshotFileModes:
    """Snapshot metadata files are private without changing user command umask."""

    def test_snapshot_and_cwd_files_are_0600(self, tmp_path):
        import os
        from pathlib import Path
        import shutil
        import stat
        import subprocess
        if not shutil.which("bash"):
            import pytest
            pytest.skip("bash required")

        class ExecutableEnv(BaseEnvironment):
            def __init__(self, temp_dir):
                self._temp_dir = str(temp_dir)
                super().__init__(cwd=str(temp_dir), timeout=10)

            def get_temp_dir(self):
                return self._temp_dir

            def _run_bash(
                self,
                cmd_string,
                *,
                login=False,
                timeout=120,
                stdin_data=None,
                clean=False,
            ):
                args = ["/bin/bash", "-lc" if login else "-c", cmd_string]
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                )
                proc.communicate(input=stdin_data, timeout=timeout)
                return proc

            def cleanup(self):
                pass

        old_umask = os.umask(0o022)
        try:
            env = ExecutableEnv(tmp_path)
            env.init_session()

            user_file = tmp_path / "user-created.txt"
            env.execute(f"touch {user_file}")

            assert stat.S_IMODE(user_file.stat().st_mode) == 0o644
            assert stat.S_IMODE(Path(env._snapshot_path).stat().st_mode) == 0o600
            # The cwd temp file is no longer written (cwd travels via the
            # stdout marker for every backend) — nothing to leak on disk.
            assert not Path(env._cwd_file).exists()
        finally:
            os.umask(old_umask)


class TestExtractCwdFromOutput:
    def test_happy_path(self):
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"hello\n{marker}/home/user{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert env.cwd == "/home/user"
        assert marker not in result["output"]

    def test_missing_marker(self):
        env = _TestableEnv()
        result = {"output": "hello world\n"}
        env._extract_cwd_from_output(result)

        assert env.cwd == "/tmp"  # unchanged

    def test_marker_in_command_output(self):
        """If the marker appears in command output AND as the real marker,
        rfind grabs the last (real) one."""
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"user typed {marker} in their output\nreal output\n{marker}/correct/path{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert env.cwd == "/correct/path"

    def test_output_cleaned(self):
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"hello\n{marker}/tmp{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert "hello" in result["output"]
        assert marker not in result["output"]


class TestEmbedStdinHeredoc:
    def test_heredoc_format(self):
        result = BaseEnvironment._embed_stdin_heredoc("cat", "hello world")

        assert result.startswith("cat << '")
        assert "hello world" in result
        assert "HERMES_STDIN_" in result

    def test_unique_delimiter_each_call(self):
        r1 = BaseEnvironment._embed_stdin_heredoc("cat", "data")
        r2 = BaseEnvironment._embed_stdin_heredoc("cat", "data")

        # Extract delimiters
        d1 = r1.split("'")[1]
        d2 = r2.split("'")[1]
        assert d1 != d2  # UUID-based, should be unique


class TestInitSessionFailure:
    def test_clean_launch_suppresses_inherited_xtrace_before_attestation(
        self, tmp_path
    ):
        token = "INHERITED_XTRACE_MUST_NOT_LEAK"
        env = _ready_local_env(
            tmp_path,
            "export PROFILE_SENTINEL=loaded\n",
            inherited_env={"SHELLOPTS": "xtrace", "PS4": token},
        )
        try:
            result = env.execute("printf 'USER_OUTPUT'")

            assert result == {"output": "USER_OUTPUT", "returncode": 0}
            assert token not in result["output"]
            assert env._session_mode == "snapshot"
        finally:
            env.cleanup()

    def test_snapshot_xtrace_cannot_forge_guard_before_sanitizer_failure(
        self, tmp_path
    ):
        user_effect = tmp_path / "must-not-run"
        snapshot = """if [[ $BASH_EXECUTION_STRING =~ (__HERMES_SNAPSHOT_GUARD_PASSED_[0-9a-f]{32}__) ]]; then
    PS4=$'\\n'"${BASH_REMATCH[1]}"$'\\n'
fi
BASH_XTRACEFD=1
set -x
builtin () { return 0; }
readonly -f builtin
"""
        env = _ready_local_env(tmp_path, snapshot)
        try:
            result = env.execute(f"touch {user_effect}")

            assert result["returncode"] == 125
            assert "__HERMES_SNAPSHOT_GUARD_PASSED_" not in result["output"]
            assert not user_effect.exists()
            assert env._session_mode == "degraded_nonlogin"
        finally:
            env.cleanup()

    def test_debug_trap_cannot_forge_guard_on_missing_cwd(self, tmp_path):
        user_effect = tmp_path / "must-not-run"
        missing_cwd = tmp_path / "missing-cwd"
        snapshot = """if [[ $BASH_EXECUTION_STRING =~ (__HERMES_SNAPSHOT_GUARD_PASSED_[0-9a-f]{32}__) ]]; then
    __hermes_attack_token=${BASH_REMATCH[1]}
fi
__hermes_debug_attack () {
    PS4=$'\\n'"$__hermes_attack_token"$'\\n'
    set -x
}
trap __hermes_debug_attack DEBUG
"""
        env = _ready_local_env(tmp_path, snapshot)
        try:
            result = env.execute(f"touch {user_effect}", cwd=str(missing_cwd))

            assert result["returncode"] == 125
            assert "__HERMES_SNAPSHOT_GUARD_PASSED_" not in result["output"]
            assert not user_effect.exists()
            assert env._session_mode == "degraded_nonlogin"
        finally:
            env.cleanup()

    def test_user_command_can_enable_xtrace_after_snapshot_setup(self, tmp_path):
        env = _ready_local_env(tmp_path, "export PROFILE_SENTINEL=loaded\n")
        try:
            result = env.execute(
                "PS4='USER_XTRACE:'; set -x; printf 'USER_OUTPUT'"
            )

            assert result["returncode"] == 0
            assert "USER_XTRACE:" in result["output"]
            assert "USER_OUTPUT" in result["output"]
            assert env._session_mode == "snapshot"
        finally:
            env.cleanup()

    def test_legitimate_exit_125_preserves_ready_snapshot(self, tmp_path):
        import os
        import re
        import subprocess

        class RealBashEnv(_TestableEnv):
            def _run_bash(
                self, cmd_string, *, login=False, timeout=120,
                stdin_data=None, clean=False,
            ):
                run_env = dict(os.environ)
                if clean:
                    run_env.update(BASH_ENV="/dev/null", ENV="/dev/null")
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", cmd_string],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                    env=run_env,
                )

        snapshot = tmp_path / "snapshot.sh"
        snapshot.write_text("export PROFILE_SENTINEL=loaded\n")
        snapshot.chmod(0o600)
        env = RealBashEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snapshot)
        env._set_session_mode("snapshot")

        result = env.execute(
            "if [[ $BASH_EXECUTION_STRING =~ "
            "(__HERMES_SNAPSHOT_GUARD_PASSED_[0-9a-f]{32}__) ]]; then "
            "printf '\\n%s\\n' \"${BASH_REMATCH[1]}\"; fi; "
            "sh -c 'exit 125'"
        )

        assert result["returncode"] == 125
        replayed_sentinels = re.findall(
            r"__HERMES_SNAPSHOT_GUARD_PASSED_[0-9a-f]{32}__",
            result["output"],
        )
        assert len(replayed_sentinels) == 1
        assert env._session_mode == "snapshot"
        assert env._snapshot_ready is True

    def test_readonly_dispatcher_source_failure_demotes_without_marker(
        self, tmp_path
    ):
        import os
        import subprocess

        class RealBashEnv(_TestableEnv):
            def _run_bash(
                self, cmd_string, *, login=False, timeout=120,
                stdin_data=None, clean=False,
            ):
                run_env = dict(os.environ)
                if clean:
                    run_env.update(BASH_ENV="/dev/null", ENV="/dev/null")
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", cmd_string],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                    env=run_env,
                )

        snapshot = tmp_path / "snapshot.sh"
        snapshot.write_text(
            "builtin () { return 0; }\n"
            "readonly -f builtin\n"
        )
        snapshot.chmod(0o600)
        user_effect = tmp_path / "must-not-run"
        env = RealBashEnv(cwd=str(tmp_path))
        env._snapshot_path = str(snapshot)
        env._set_session_mode("snapshot")

        result = env.execute(f"touch {user_effect}")

        assert result["returncode"] == 125
        assert "source or sanitizer guard" in result["output"]
        assert "__HERMES_SNAPSHOT_GUARD_PASSED_" not in result["output"]
        assert not user_effect.exists()
        assert env._session_mode == "degraded_nonlogin"

    def test_real_source_guard_failure_demotes_and_hides_internal_marker(
        self, tmp_path
    ):
        import os
        import subprocess

        class RealBashEnv(_TestableEnv):
            def _run_bash(
                self, cmd_string, *, login=False, timeout=120,
                stdin_data=None, clean=False,
            ):
                run_env = dict(os.environ)
                if clean:
                    run_env.update(BASH_ENV="/dev/null", ENV="/dev/null")
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", cmd_string],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                    env=run_env,
                )

        user_effect = tmp_path / "must-not-run"
        env = RealBashEnv(cwd=str(tmp_path))
        env._snapshot_path = str(tmp_path / "missing-snapshot.sh")
        env._set_session_mode("snapshot")

        result = env.execute(f"touch {user_effect}")

        assert result["returncode"] == 125
        assert "source or sanitizer guard" in result["output"]
        assert "__HERMES_SNAPSHOT_GUARD_PASSED_" not in result["output"]
        assert not user_effect.exists()
        assert env._session_mode == "degraded_nonlogin"

    @pytest.mark.parametrize("snapshot_exit", [124, 130])
    def test_snapshot_setup_exit_uses_guard_failure_not_external_stop(
        self, tmp_path, snapshot_exit
    ):
        """A snapshot can choose public timeout/interrupt-looking statuses.

        Exiting while the snapshot is sourced happens before Hermes' guard
        attestation and is a setup failure, not proof that the process waiter
        performed an external timeout or interrupt.
        """
        user_effect = tmp_path / "must-not-run"
        env = _ready_local_env(tmp_path, f"exit {snapshot_exit}\n")
        try:
            result = env.execute(f"touch {user_effect}")

            assert result["returncode"] == 125
            assert "source or sanitizer guard" in result["output"]
            assert not user_effect.exists()
            assert env._session_mode == "degraded_nonlogin"
            assert env._snapshot_ready is False
        finally:
            env.cleanup()

    def test_real_waiter_interrupt_preserves_ready_snapshot_and_private_state(
        self, tmp_path
    ):
        from tools.interrupt import set_interrupt

        env = _ready_local_env(tmp_path, "export PROFILE_SENTINEL=loaded\n")
        try:
            set_interrupt(True)
            result = env.execute("sleep 1")

            assert result["returncode"] == 130
            assert "[Command interrupted]" in result["output"]
            assert set(result) == {"output", "returncode"}
            assert env._session_mode == "snapshot"
            assert env._snapshot_ready is True
        finally:
            set_interrupt(False)
            env.cleanup()

    def test_interrupted_first_bootstrap_recovers_after_interrupt_clear(self, tmp_path):
        """A cancelled initial snapshot must not permanently refuse later work."""
        from tools.environments.local import LocalEnvironment
        from tools.interrupt import set_interrupt

        set_interrupt(True)
        env = LocalEnvironment(cwd=str(tmp_path), timeout=10)
        try:
            set_interrupt(False)
            result = env.execute("printf RECOVERED")

            assert result["returncode"] == 0
            assert result["output"].endswith("RECOVERED")
            assert set(result) == {"output", "returncode"}
            assert env._session_mode == "degraded_nonlogin"
        finally:
            set_interrupt(False)
            env.cleanup()

    def test_natural_bootstrap_exit_130_still_uses_fail_closed_recovery(self):
        """Public 130 without waiter provenance is not a cancellation."""
        env = _TestableEnv()
        calls = []
        wait_results = iter(
            (
                {"output": "", "returncode": 0},
                {"output": "", "returncode": 130},
                {"output": "", "returncode": 0},
                {"output": "", "returncode": 0},
            )
        )

        def fake_run_bash(command, **kwargs):
            calls.append((command, kwargs))
            return object()

        env._run_bash = fake_run_bash  # type: ignore[assignment]
        env._wait_for_process = lambda *_args, **_kwargs: next(wait_results)  # type: ignore[assignment]

        env.init_session()

        assert env._session_mode == "degraded_nonlogin"
        assert "bootstrap failed with exit code 130" in env._session_diagnostic
        assert "initialization was interrupted" not in env._session_diagnostic
        assert [kwargs.get("clean") for _command, kwargs in calls] == [
            True,
            False,
            True,
            True,
        ]

    def test_real_waiter_timeout_preserves_ready_snapshot_and_private_state(
        self, tmp_path
    ):
        env = _ready_local_env(tmp_path, "export PROFILE_SENTINEL=loaded\n")
        try:
            result = env.execute("sleep 1", timeout=0.01)

            assert result["returncode"] == 124
            assert "[Command timed out" in result["output"]
            assert set(result) == {"output", "returncode"}
            assert env._session_mode == "snapshot"
            assert env._snapshot_ready is True
        finally:
            env.cleanup()

    def test_normal_profile_snapshot_preserves_env_functions_cd_and_tilde_cwd(
        self, tmp_path
    ):
        import os
        import shlex
        import subprocess

        profile_home = tmp_path / "profile home"
        target = profile_home / "target path"
        target.mkdir(parents=True)
        profile = (
            f"export HOME={shlex.quote(str(profile_home))}\n"
            "export PROFILE_SENTINEL=loaded\n"
            "ordinary () { printf 'ordinary-loaded\\n'; }\n"
            "cd () { printf 'cd-function-called\\n'; builtin cd \"$@\"; }\n"
        )

        class ProfileEnv(_TestableEnv):
            def _run_bash(
                self, cmd_string, *, login=False, timeout=120,
                stdin_data=None, clean=False,
            ):
                script = f"{profile}{cmd_string}" if login else cmd_string
                run_env = dict(os.environ)
                if clean:
                    run_env.update(BASH_ENV="/dev/null", ENV="/dev/null")
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                    env=run_env,
                )

        env = ProfileEnv(cwd=str(tmp_path))
        env._snapshot_path = str(tmp_path / "snapshot.sh")
        env.init_session()

        assert env._session_mode == "snapshot"
        result = env.execute(
            "printf 'ENV=%s\\n' \"$PROFILE_SENTINEL\"; ordinary; "
            "declare -F cd >/dev/null && printf 'CD=present\\n'; pwd",
            cwd="~/target path",
        )
        assert result["returncode"] == 0, result["output"]
        assert "ENV=loaded" in result["output"]
        assert "ordinary-loaded" in result["output"]
        assert "CD=present" in result["output"]
        assert "cd-function-called" not in result["output"]
        assert str(target) in result["output"]

    def test_profile_drains_stdin_and_returns_zero_but_never_enables_snapshot(
        self, tmp_path
    ):
        import subprocess

        class DrainingProfileEnv(_TestableEnv):
            def _run_bash(
                self, cmd_string, *, login=False, timeout=120,
                stdin_data=None, clean=False,
            ):
                script = (
                    "cat /dev/stdin >/dev/null; exit 0; " + cmd_string
                    if login
                    else cmd_string
                )
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                )

        env = DrainingProfileEnv(cwd=str(tmp_path))
        env.init_session()

        assert env._session_mode == "degraded_nonlogin"
        assert env._snapshot_ready is False
        assert not (tmp_path / env._snapshot_path).exists()
        result = env.execute("pwd")
        assert "clean non-login shell" in result["output"]
        assert str(tmp_path) in result["output"]

    def test_success_status_without_snapshot_artifact_degrades(self, tmp_path):
        """A profile may drain the bootstrap and return zero; READY still
        requires an independent artifact validator."""
        env = _TestableEnv(cwd=str(tmp_path))
        calls = []
        returncodes = iter((0, 0, 1, 0, 0))  # preclean, capture, validate, cleanup, probe

        def mock_run_bash(cmd, **kwargs):
            calls.append((cmd, kwargs))
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = next(returncodes)
            mock.stdout = iter([])
            return mock

        env._run_bash = mock_run_bash
        env.init_session()

        assert env._session_mode == "degraded_nonlogin"
        assert env._snapshot_ready is False
        assert env._prefer_nonlogin is True
        assert [call[1].get("clean") for call in calls] == [
            True, False, True, True, True
        ]
        assert "rm -f" in calls[-2][0]

    def test_ready_source_failure_stops_command_and_demotes(self, tmp_path):
        env = _TestableEnv(cwd=str(tmp_path))
        env._snapshot_ready = True
        env._session_mode = "snapshot"
        captured = {}

        def mock_run_bash(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 125
            assert "__HERMES_SNAPSHOT_GUARD_PASSED_" in cmd
            mock.stdout = iter([])
            return mock

        env._run_bash = mock_run_bash
        result = env.execute("touch SHOULD_NOT_RUN")

        assert "source" in captured["cmd"]
        assert captured["cmd"].index("source") < captured["cmd"].index("SHOULD_NOT_RUN")
        assert "|| true" not in captured["cmd"].split("SHOULD_NOT_RUN", 1)[0]
        assert captured["kwargs"]["clean"] is True
        assert env._session_mode == "degraded_nonlogin"
        assert env._snapshot_ready is False
        assert result["returncode"] == 125
        assert "snapshot" in result["output"].lower()

    def test_post_source_cwd_uses_protected_builtin_and_preserves_cdpath(self):
        env = _TestableEnv(cwd="~")
        env._snapshot_ready = True
        wrapped = env._wrap_command("declare -F cd >/dev/null", "~/target path")

        source_at = wrapped.index("source")
        sanitizer_at = wrapped.index(
            "__hermes_snapshot_reject_dispatcher_functions=1", source_at
        )
        cd_at = wrapped.index("builtin cd --")
        command_at = wrapped.index("declare -F cd")
        assert source_at < sanitizer_at < cd_at < command_at
        assert "CDPATH=" in wrapped
        assert '"$HOME"/\'target path\'' in wrapped

    def test_snapshot_ready_false_on_failure(self):
        env = _TestableEnv()

        def failing_run_bash(*args, **kwargs):
            raise RuntimeError("bash not found")

        env._run_bash = failing_run_bash
        env.init_session()

        assert env._snapshot_ready is False

    def test_snapshot_ready_false_on_nonzero_bootstrap_exit(self):
        """A non-zero bootstrap result should trigger fallback mode."""
        env = _TestableEnv()

        def mock_run_bash(*args, **kwargs):
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 127
            mock.stdout = iter([])
            return mock

        env._run_bash = mock_run_bash
        env.init_session()

        assert env._snapshot_ready is False
        assert env._session_mode == "unavailable"
        result = env.execute("touch MUST_NOT_RUN")
        assert result["returncode"] == 125
        assert "profile snapshot unavailable" in result["output"]
        assert "Using a clean non-login shell" not in result["output"]
        assert "refusing command execution" in result["output"]

    def test_profile_exit_78_before_bootstrap_is_not_safe_rejection(self, tmp_path):
        """Status 78 alone is not proof that Hermes reached its rejection
        path; a profile can terminate with that status before bootstrap runs."""
        import subprocess

        login_calls = []

        class EarlyExitEnv(_TestableEnv):
            def _run_bash(
                self,
                cmd_string,
                *,
                login=False,
                timeout=120,
                stdin_data=None,
                clean=False,
            ):
                login_calls.append(login)
                # The login profile exits before the shell can read a bootstrap
                # sent on stdin.  A bare 78 must not authenticate rejection.
                script = "exit 78" if login else cmd_string
                return subprocess.Popen(
                    ["/bin/bash", "--noprofile", "--norc", "-c", script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self.cwd,
                )

        env = EarlyExitEnv(cwd=str(tmp_path))

        env.init_session()

        assert env._snapshot_ready is False
        assert env._prefer_nonlogin is True
        assert env._session_mode == "degraded_nonlogin"
        assert login_calls == [False, True, False, False]

    def test_clean_nonlogin_when_snapshot_not_ready(self):
        env = _TestableEnv()
        env._snapshot_ready = False

        calls = []
        def mock_run_bash(cmd, **kwargs):
            calls.append(kwargs)
            # Return a mock process handle
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        env._run_bash = mock_run_bash
        env.execute("echo test")

        assert len(calls) == 1
        assert calls[0].get("login", False) is False
        assert calls[0]["clean"] is True

    def test_prefer_nonlogin_when_login_bash_is_dead(self):
        """Login snapshot failure + working non-login probe → don't use bash -l."""
        env = _TestableEnv()

        def mock_run_bash(cmd, **kwargs):
            login = kwargs.get("login", False)
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.stdout = iter([])
            if login:
                mock.returncode = 1
            else:
                mock.returncode = 0
            return mock

        env._run_bash = mock_run_bash
        env.init_session()

        assert env._snapshot_ready is False
        assert env._prefer_nonlogin is True

        calls = []

        def track_run_bash(cmd, **kwargs):
            login = kwargs.get("login", False)
            calls.append({"login": login})
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        env._run_bash = track_run_bash
        env.execute("echo test")

        assert calls[0]["login"] is False


class TestCwdMarker:
    def test_marker_contains_session_id(self):
        env = _TestableEnv()
        assert env._session_id in env._cwd_marker

    def test_unique_per_instance(self):
        env1 = _TestableEnv()
        env2 = _TestableEnv()
        assert env1._cwd_marker != env2._cwd_marker
