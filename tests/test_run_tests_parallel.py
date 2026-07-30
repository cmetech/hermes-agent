"""Verify scripts/run_tests_parallel.py kills test-spawned grandchildren.

Setup
-----
A test in this file spawns a long-lived Python grandchild that writes
its PID + a nonce to a tempfile, then exits without cleaning up.
With the old ``subprocess.run`` runner, that grandchild would orphan
and outlive the test (and the whole runner). With the current Popen +
``start_new_session`` + ``_kill_tree`` runner, the grandchild gets
SIGKILL'd via process-group kill when its file's pytest exits.

The leaker test always passes — its only job is to spawn a grandchild
and walk away. The verifier runs the runner over the leaker file in a
subprocess, then waits for the grandchild PID to disappear from the
kernel's process table.

POSIX-only: Windows has its own grandchild lifecycle (no shared session,
``taskkill /F /T`` semantics). Marked accordingly.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from scripts import run_tests_parallel


# Both tests share the same handoff file: the leaker writes here, the
# verifier reads here. We park it in $TMPDIR with a unique-per-run name
# so concurrent invocations of the suite don't clobber each other.
_HANDOFF_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "hermes-isolation-probe"
_HANDOFF_DIR.mkdir(exist_ok=True)


def _handoff_path_for(nonce: str) -> Path:
    return _HANDOFF_DIR / f"grandchild-{nonce}.json"


def _pid_alive(pid: int) -> bool:
    """POSIX: send signal 0 to probe whether ``pid`` is still alive.

    ``os.kill(pid, 0)`` raises ``ProcessLookupError`` if the process is
    gone, ``PermissionError`` if it exists but we can't signal it
    (someone else's pid). We treat PermissionError as "alive" because
    the process exists and that's all we need to know.
    """
    if sys.platform == "win32":  # pragma: no cover — POSIX-only test
        # On Windows we'd use OpenProcess + GetExitCodeProcess; this
        # test is skipped on Windows so the path is unreachable.
        raise RuntimeError("_pid_alive POSIX-only")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only probe")
@pytest.mark.live_system_guard_bypass
def test_grandchild_leak_is_killed_by_runner(tmp_path: Path) -> None:
    """Run the parallel runner over a probe file and verify cleanup.

    1. Materialize a probe file that spawns a long-lived grandchild and
       writes its PID to disk before exiting.
    2. Invoke ``scripts/run_tests_parallel.py`` against the probe file.
    3. Wait for the grandchild PID to vanish (poll for ~5s).
    4. Assert the runner exited cleanly AND the grandchild is dead.
    """
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    assert runner.exists(), f"runner missing at {runner}"

    # Probe lives in a temp dir, NOT under tests/, so the regular suite
    # never picks it up — only our explicit invocation does.
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = probe_dir / "test_probe_leaker.py"
    nonce = f"{os.getpid()}-{int(time.time() * 1000)}"
    handoff = _handoff_path_for(nonce)
    if handoff.exists():
        handoff.unlink()

    probe_src = textwrap.dedent(f"""
        import json, os, subprocess, sys, time
        from pathlib import Path

        HANDOFF = Path({str(handoff)!r})

        def test_spawns_grandchild_and_walks_away():
            # Long-lived grandchild: detached, ignores SIGTERM (we want
            # SIGKILL or process-group kill to be the only thing that
            # works, simulating a misbehaving server).
            child = subprocess.Popen(
                [
                    sys.executable, "-c",
                    "import os, signal, sys, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "sys.stdout.write(f'gc-pgid={{os.getpgid(0)}} gc-pid={{os.getpid()}}\\\\n'); "
                    "sys.stdout.flush(); "
                    "time.sleep(600)",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # IMPORTANT: do NOT pass start_new_session here. We want
                # the grandchild to inherit the pytest subprocess's
                # process group, so when the runner kills the group the
                # grandchild dies too.
            )
            # Read the first line so we can record gc's pgid in the
            # handoff, then walk away — don't close the pipe (would
            # signal EOF and let the child see SIGPIPE on next write).
            first_line = child.stdout.readline().decode().strip()
            HANDOFF.write_text(json.dumps({{
                "pid": child.pid,
                "diag": first_line,
                "test_pid": os.getpid(),
                "test_pgid": os.getpgid(0),
            }}))
            assert child.pid > 0
    """).strip()
    probe.write_text(probe_src + "\n")

    # Run the parallel runner against just the probe file. The runner
    # discovers under ``tests/`` by default, so we override via --paths.
    proc = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--paths",
            str(probe_dir),
            "-j",
            "1",
            # Tight per-file timeout: the probe finishes in <1s, no
            # need for 10min.
            "--file-timeout",
            "30",
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )

    assert handoff.exists(), (
        f"probe never wrote handoff file; runner output:\n{proc.stdout}"
    )
    handoff_data = json.loads(handoff.read_text())
    grandchild_pid = handoff_data["pid"]
    diag = handoff_data.get("diag", "(no diag)")
    test_pid = handoff_data.get("test_pid")
    test_pgid = handoff_data.get("test_pgid")
    handoff.unlink()

    # The runner must have exited cleanly (probe test passes).
    assert proc.returncode == 0, (
        f"runner exited {proc.returncode}; output:\n{proc.stdout}"
    )

    # The grandchild must be gone. Poll for a bit because process-group
    # SIGKILL + reaping isn't synchronous; on a loaded box it can take
    # a beat.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(grandchild_pid):
            break
        time.sleep(0.05)
    else:
        # Test cleanup: kill the leaked grandchild ourselves so a
        # FAILED assertion doesn't leave a sleep(600) running.
        try:
            os.kill(grandchild_pid, 9)
        except ProcessLookupError:
            pass
        pytest.fail(
            f"grandchild PID {grandchild_pid} survived runner exit; "
            f"diag={diag!r} test_pid={test_pid} test_pgid={test_pgid}; "
            f"runner output:\n{proc.stdout}"
        )


# ── Bare pytest-flag passthrough ─────────────────────────────────────────────
#
# The runner routes any token starting with ``-`` that isn't one of its own
# options (``-j``/``--jobs``, ``--paths``, ``--slice``, ``--file-timeout``,
# ``--generate-slices``, ``--files``, ``--include-integration``) straight
# through to each per-file pytest invocation — no ``--`` separator required.
# Before this, a bare ``-q`` errored out with "unrecognized arguments",
# forcing a retry on every run. These tests are behavior contracts, not
# snapshots: they assert that bare flags reach pytest and that value-taking
# flags (``-k expr``) keep their value instead of having it stolen by the
# positional-path discovery.


def _make_probe_dir(tmp_path: Path) -> Path:
    """Two trivial passing tests, one named test_alpha, one test_beta."""
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "test_flagprobe.py").write_text(
        "def test_alpha():\n    assert True\n\n"
        "def test_beta():\n    assert True\n"
    )
    return probe_dir


def _run_runner(probe_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    return subprocess.run(
        [sys.executable, str(runner), "--paths", str(probe_dir),
         "-j", "1", "--file-timeout", "30", *extra],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("env_workers", "cli_args", "expected_jobs"),
    [
        pytest.param(
            None,
            (),
            max(1, (os.cpu_count() or 1) // 2),
            id="resource-safe-default",
        ),
        pytest.param("3", (), 3, id="environment-override"),
        pytest.param("3", ("-j", "2"), 2, id="cli-override"),
    ],
)
def test_worker_selection_policy(
    tmp_path: Path,
    env_workers: str | None,
    cli_args: tuple[str, ...],
    expected_jobs: int,
) -> None:
    """Reserve CPU for test-owned concurrency; preserve exact opt-in overrides."""
    probe_dir = _make_probe_dir(tmp_path)
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests.sh"
    env = os.environ.copy()
    if env_workers is None:
        env.pop("HERMES_TEST_WORKERS", None)
    else:
        env["HERMES_TEST_WORKERS"] = env_workers

    proc = subprocess.run(
        [
            str(runner),
            str(probe_dir),
            "--file-retries",
            "0",
            *cli_args,
            "-q",
        ],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout
    announced = re.search(r"running with -j (?P<jobs>\d+)(?:\s|$)", proc.stdout)
    assert announced is not None, proc.stdout
    assert int(announced.group("jobs")) == expected_jobs, proc.stdout


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_canonical_wrapper_help_exits_without_running_tests(
    tmp_path: Path,
    help_flag: str,
) -> None:
    """Runner help remains reachable through the canonical clean wrapper."""
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests.sh"
    nonexistent = tmp_path / "does-not-exist"

    started = time.monotonic()
    proc = subprocess.run(
        [str(runner), str(nonexistent), help_flag],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )
    elapsed = time.monotonic() - started

    normalized_output = " ".join(proc.stdout.split())
    expected_default = (
        "Parallel worker count (default: $HERMES_TEST_WORKERS or half the "
        "known logical CPUs, minimum 1; unknown CPU count uses 1)"
    )
    assert proc.returncode == 0, proc.stdout
    assert expected_default in normalized_output, proc.stdout
    assert elapsed < 5, proc.stdout
    assert "No test files found" not in proc.stdout, proc.stdout
    assert "No test files to run" not in proc.stdout, proc.stdout
    assert "Discovered " not in proc.stdout, proc.stdout
    assert "=== Summary" not in proc.stdout, proc.stdout


@pytest.mark.parametrize(
    ("cpu_count", "expected_jobs"),
    [
        pytest.param(None, 1, id="unknown-is-conservative"),
        pytest.param(1, 1, id="single-cpu-clamps-to-one"),
        pytest.param(2, 1, id="two-cpus-reserve-one"),
        pytest.param(3, 1, id="odd-count-rounds-down"),
        pytest.param(4, 2, id="even-count-halves"),
        pytest.param(5, 2, id="larger-odd-count-rounds-down"),
        pytest.param(28, 14, id="measured-host-halves"),
    ],
)
def test_default_worker_count_policy(
    cpu_count: int | None,
    expected_jobs: int,
) -> None:
    """Unknown/low CPU counts fail safe; known hosts reserve half their CPUs."""
    assert run_tests_parallel._default_worker_count(cpu_count) == expected_jobs


def test_bare_q_flag_passes_through(tmp_path: Path) -> None:
    """A bare ``-q`` (no ``--``) runs clean instead of erroring out."""
    probe_dir = _make_probe_dir(tmp_path)
    proc = _run_runner(probe_dir, "-q")
    assert proc.returncode == 0, proc.stdout
    assert "unrecognized arguments" not in proc.stdout


def test_bare_value_flag_keeps_its_value(tmp_path: Path) -> None:
    """``-k test_alpha`` reaches pytest as a selector, not as a path.

    The value token (``test_alpha``) must NOT be swallowed by the runner's
    positional-path discovery — if it were, discovery would look for a path
    named ``test_alpha``, find nothing, and the run would degrade. We assert
    the run succeeds AND only one of the two tests was selected (proving the
    ``-k`` filter actually applied inside pytest).
    """
    probe_dir = _make_probe_dir(tmp_path)
    proc = _run_runner(probe_dir, "-k", "test_alpha")
    assert proc.returncode == 0, proc.stdout
    # Exactly one test selected: the per-file summary shows "1✓" (1 passed).
    # test_beta is deselected by the -k filter.
    assert "1✓" in proc.stdout or "1 passed" in proc.stdout, proc.stdout
    assert "2✓" not in proc.stdout, (
        f"both tests ran — -k filter did not apply:\n{proc.stdout}"
    )


def test_explicit_double_dash_still_works(tmp_path: Path) -> None:
    """The legacy ``--`` separator keeps working alongside bare flags."""
    probe_dir = _make_probe_dir(tmp_path)
    proc = _run_runner(probe_dir, "-q", "--", "--tb=short")
    assert proc.returncode == 0, proc.stdout
    assert "unrecognized arguments" not in proc.stdout


def test_positional_path_not_treated_as_flag(tmp_path: Path) -> None:
    """A positional path arg still overrides discovery (not routed to pytest)."""
    probe_dir = _make_probe_dir(tmp_path)
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    # Pass the probe dir positionally (no --paths), plus a bare -q.
    proc = subprocess.run(
        [sys.executable, str(runner), str(probe_dir), "-j", "1",
         "--file-timeout", "30", "-q"],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout
    # Discovery found the probe file (2 tests), proving the positional path
    # was consumed as a root, not forwarded to pytest as a bad flag.
    assert "test_flagprobe.py" in proc.stdout, proc.stdout


def test_file_retry_self_heals_and_prints_both_attempts(tmp_path: Path) -> None:
    """A pass-on-retry is green, loud, and retains the failing traceback."""
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    marker = tmp_path / "ran-once"
    probe = tmp_path / "test_flaky_probe.py"
    probe.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            def test_flaky_once():
                marker = Path({str(marker)!r})
                if not marker.exists():
                    marker.write_text("failed once")
                    assert False, "simulated first-attempt flake"
                assert True
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--files",
            str(probe),
            "--file-retries",
            "1",
            "-j",
            "1",
            "-q",
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout
    assert "FLAKY file" in proc.stdout
    assert "simulated first-attempt flake" in proc.stdout
    assert "first-attempt output" in proc.stdout
    assert "retry output" in proc.stdout


def test_file_retry_does_not_launder_deterministic_failure(tmp_path: Path) -> None:
    """A real regression fails both attempts and the runner remains red."""
    repo_root = Path(__file__).resolve().parent.parent
    runner = repo_root / "scripts" / "run_tests_parallel.py"
    probe = tmp_path / "test_red_probe.py"
    probe.write_text(
        "def test_always_red():\n    assert False, 'deterministic regression'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--files",
            str(probe),
            "--file-retries",
            "1",
            "-j",
            "1",
            "-q",
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 1, proc.stdout
    assert "deterministic regression" in proc.stdout
    assert "FLAKY file" not in proc.stdout
