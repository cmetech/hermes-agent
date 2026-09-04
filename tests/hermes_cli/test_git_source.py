"""Credential-safe shared Git source behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path
import sys
import time

import pytest

from hermes_cli import git_source
from hermes_cli.git_source import (
    GitSourceCancelled,
    GitSourceError,
    GitSourceResourceLimit,
    ResolvedGitSource,
    canonical_git_source,
    checkout_exact_revision,
    git_head_revision,
    is_exact_revision,
    noninteractive_git_env,
    resolve_git_source,
    run_git_bounded,
    safe_git_error,
    scrub_cloned_origin,
    validate_credential_free_git_source,
)


def _completed(*, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git", "operation"],
        returncode=1,
        stdout=stdout,
        stderr=stderr,
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_resolve_git_source_supports_private_ssh_and_tree_subdirectory():
    assert resolve_git_source(
        "git@gitlab.example:team/repo.git#packages/support"
    ) == ResolvedGitSource(
        clone_url="git@gitlab.example:team/repo.git",
        subdirectory="packages/support",
    )


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (
            "https://github.com/owner/repo/tree/main/packages/support",
            ResolvedGitSource("https://github.com/owner/repo.git", "packages/support"),
        ),
        (
            "https://gitlab.example/team/repo.git/packages/support",
            ResolvedGitSource(
                "https://gitlab.example/team/repo.git", "packages/support"
            ),
        ),
        (
            "https://alice:secret@example.test/team/repo.git?token=abc#pkg",
            ResolvedGitSource(
                "https://alice:secret@example.test/team/repo.git?token=abc", "pkg"
            ),
        ),
    ],
)
def test_resolve_git_source_preserves_supported_clone_forms(identifier, expected):
    assert resolve_git_source(identifier) == expected


def test_resolve_git_source_rejects_empty_input():
    with pytest.raises(GitSourceError, match="Git source must not be empty"):
        resolve_git_source("   ")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a" * 40, True),
        ("A" * 40, True),
        ("a" * 39, False),
        ("main", False),
        (None, False),
    ],
)
def test_exact_revision_recognition_is_shared(value, expected):
    assert is_exact_revision(value) is expected


@pytest.mark.parametrize(
    "identifier",
    [
        "https://alice:secret@example.test/team/repo.git",
        "ssh://git:secret@example.test/team/repo.git",
        "ssh://git@example.test/team/repo.git?token=secret",
        "ssh://git@example.test/team/repo.git#token=secret",
        "git@example.test:team/repo.git?token=secret",
        "git@example.test:team/repo.git#access_token=secret",
        "owner/repo?token=secret",
        "owner/repo#access_token=secret",
        "alice:secret@example.test",
    ],
)
def test_credential_free_source_validation_rejects_without_echoing_secret(
    identifier,
):
    with pytest.raises(GitSourceError) as error:
        validate_credential_free_git_source(identifier)

    assert "secret" not in str(error.value)
    assert "alice" not in str(error.value)


def test_credential_free_source_validation_preserves_supported_auth_seams():
    assert (
        validate_credential_free_git_source(
            "git@gitlab.example:team/repo.git#packages/support"
        )
        == "git@gitlab.example:team/repo.git#packages/support"
    )
    assert (
        validate_credential_free_git_source(
            "https://example.test/team/repo.git?ref=main"
        )
        == "https://example.test/team/repo.git?ref=main"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "fatal: https://git@example.test/team/repo.git was rejected",
            True,
        ),
        (
            "fatal: https://example.test/team/repo.git?token=status-secret failed",
            True,
        ),
        (
            "fatal: https://example.test/team/repo.git#access_token=status-secret failed",
            True,
        ),
        (
            "fatal: ssh://git:status-secret@example.test/team/repo.git failed",
            True,
        ),
        (
            "fatal: ssh://git@example.test/team/repo.git?token=status-secret failed",
            True,
        ),
        (
            "fatal: git@example.test:team/repo.git#access_token=status-secret failed",
            True,
        ),
        (
            "fatal: alice:status-secret@example.test/team/repo.git failed",
            True,
        ),
        (
            "fatal: owner/repo?private_token=status-secret failed",
            True,
        ),
        ("fatal: api_key=status-secret", True),
        (
            "fatal: ssh://git@example.test/team/repo.git was unavailable",
            False,
        ),
        (
            "fatal: git@example.test:team/repo.git was unavailable",
            False,
        ),
        ("repository unavailable; retry later", False),
    ],
)
def test_persisted_git_message_credential_predicate_is_strict_and_unambiguous(
    message,
    expected,
):
    assert git_source.git_text_contains_credentials(message) is expected


def test_safe_git_error_removes_embedded_credentials_and_query_tokens():
    source = "https://alice:secret@example.test/repo.git?token=abc"
    result = _completed(stderr=f"fatal: unable to access '{source}': denied")

    rendered = safe_git_error(result, source)

    assert rendered == (
        "fatal: unable to access 'https://example.test/repo.git': denied"
    )
    assert "alice" not in rendered
    assert "secret" not in rendered
    assert "token=abc" not in rendered


def test_safe_git_error_scrubs_every_http_url_and_preserves_delimiters():
    result = _completed(
        stderr=(
            "fatal: redirect from "
            "(https://alice:first-secret@origin.example/repo.git"
            "?private_token=opaque-secret#opaque-fragment), to "
            "[https://bob:second-secret@mirror.example/repo.git"
            "?sig=opaque-signature#other-fragment]. retry later"
        )
    )

    rendered = safe_git_error(result)

    assert rendered == (
        "fatal: redirect from (https://origin.example/repo.git), to "
        "[https://mirror.example/repo.git]. retry later"
    )


def test_safe_git_error_preserves_ssh_usernames_and_unrelated_text():
    result = _completed(
        stderr=(
            "fatal: ssh://git@gitlab.example/team/repo.git and "
            "git@github.example:team/repo.git were unavailable"
        )
    )

    assert safe_git_error(result) == (
        "fatal: ssh://git@gitlab.example/team/repo.git and "
        "git@github.example:team/repo.git were unavailable"
    )


def test_canonical_git_source_never_contains_http_credentials_or_query_tokens():
    assert (
        canonical_git_source(
            "https://alice:secret@example.test/team/repo.git?token=abc",
            "packages/support",
        )
        == "https://example.test/team/repo.git#packages/support"
    )


def test_noninteractive_git_env_keeps_existing_credential_seams():
    source_env = {
        "GH_TOKEN": "secret-token",
        "GIT_ASKPASS": "/opt/git/askpass",
        "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
    }

    result = noninteractive_git_env(source_env)

    assert result == {
        **source_env,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
    }
    assert result is not source_env


def test_git_head_revision_uses_bounded_noninteractive_subprocess(
    monkeypatch, tmp_path
):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "A" * 40 + "\n", "")

    monkeypatch.setattr(git_source.subprocess, "run", run)

    assert git_head_revision(tmp_path, "/resolved/git") == "a" * 40
    assert calls == [
        (
            ["/resolved/git", "rev-parse", "HEAD"],
            {
                "cwd": str(tmp_path),
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 15,
                "stdin": subprocess.DEVNULL,
                "env": noninteractive_git_env(),
            },
        )
    ]


def test_checkout_exact_revision_uses_fetch_then_detached_checkout(
    monkeypatch, tmp_path
):
    revision = "a" * 40
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(git_source.subprocess, "run", run)
    monkeypatch.setattr(git_source, "git_head_revision", lambda _repo, _git: revision)

    checkout_exact_revision(tmp_path, "/resolved/git", revision)

    assert [argv for argv, _kwargs in calls] == [
        ["/resolved/git", "fetch", "--depth", "1", "origin", revision],
        ["/resolved/git", "checkout", "--detach", revision],
    ]
    for _argv, kwargs in calls:
        assert kwargs == {
            "cwd": str(tmp_path),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 60,
            "stdin": subprocess.DEVNULL,
            "env": noninteractive_git_env(),
        }


def test_bounded_git_runner_kills_process_at_output_limit() -> None:
    started = time.monotonic()

    with pytest.raises(GitSourceResourceLimit) as error:
        run_git_bounded(
            [
                sys.executable,
                "-c",
                "import sys; chunk=b'x'*4096;\nwhile True: sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()",
            ],
            timeout=5,
            max_output_bytes=1024,
        )

    assert error.value.kind == "output"
    retained = error.value.result.stdout + error.value.result.stderr
    assert len(retained.encode("utf-8")) <= 1024
    assert time.monotonic() - started < 1


def test_bounded_git_runner_rejects_fast_finite_output_past_limit() -> None:
    with pytest.raises(GitSourceResourceLimit) as error:
        run_git_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*4096)"],
            timeout=5,
            max_output_bytes=1024,
        )

    retained = error.value.result.stdout + error.value.result.stderr
    assert error.value.kind == "output"
    assert len(retained.encode("utf-8")) <= 1024


def test_bounded_git_runner_kills_process_while_storage_crosses_limit(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "growing.bin"
    started = time.monotonic()

    with pytest.raises(GitSourceResourceLimit) as error:
        run_git_bounded(
            [
                sys.executable,
                "-c",
                (
                    "import os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                    "f=p.open('wb'); chunk=b'x'*4096; "
                    'exec("while True:\\n f.write(chunk); f.flush(); os.fsync(f.fileno()); time.sleep(0.005)")'
                ),
                str(payload),
            ],
            timeout=5,
            max_output_bytes=1024,
            storage_root=tmp_path,
            max_storage_bytes=1024,
            max_storage_entries=16,
        )

    size_after_kill = payload.stat().st_size
    time.sleep(0.05)
    assert error.value.kind == "storage"
    assert payload.stat().st_size == size_after_kill
    assert time.monotonic() - started < 1


def test_bounded_git_runner_rejects_fast_storage_growth_past_limit(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "finite.bin"

    with pytest.raises(GitSourceResourceLimit) as error:
        run_git_bounded(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x'*4096)",
                str(payload),
            ],
            timeout=5,
            max_output_bytes=1024,
            storage_root=tmp_path,
            max_storage_bytes=1024,
            max_storage_entries=16,
        )

    assert error.value.kind == "storage"


def test_exact_checkout_uses_cancellable_bounded_runner_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    calls: list[tuple[list[str], dict[str, object]]] = []
    cancelled = lambda: False

    def bounded(arguments, **kwargs):
        calls.append((arguments, kwargs))
        stdout = f"{revision}\n" if arguments[1:3] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    monkeypatch.setattr(git_source, "run_git_bounded", bounded)

    checkout_exact_revision(
        tmp_path,
        "/resolved/git",
        revision,
        cancelled=cancelled,
        timeout=0.25,
        max_output_bytes=2048,
        storage_root=tmp_path,
        max_storage_bytes=4096,
        max_storage_entries=32,
    )

    assert [call[0][1] for call in calls] == ["fetch", "checkout", "rev-parse"]
    for _arguments, kwargs in calls:
        assert kwargs["cancelled"] is cancelled
        assert kwargs["timeout"] == 0.25
        assert kwargs["max_output_bytes"] == 2048
        assert kwargs["storage_root"] == tmp_path
        assert kwargs["max_storage_bytes"] == 4096
        assert kwargs["max_storage_entries"] == 32


def test_exact_checkout_runs_preflight_after_fetch_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    events: list[str] = []

    def bounded(arguments, **kwargs):
        command = arguments[1]
        events.append(command)
        stdout = f"{revision}\n" if command == "rev-parse" else ""
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    monkeypatch.setattr(git_source, "run_git_bounded", bounded)

    checkout_exact_revision(
        tmp_path,
        "/resolved/git",
        revision,
        cancelled=lambda: False,
        before_checkout=lambda: events.append("preflight"),
    )

    assert events == ["fetch", "preflight", "checkout", "rev-parse"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable fixture")
def test_exact_checkout_cancellation_is_prompt_and_process_tree_safe(
    tmp_path: Path,
) -> None:
    fake_git = tmp_path / "slow-git"
    fake_git.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n")
    fake_git.chmod(0o700)
    cancel_at = time.monotonic() + 0.05
    started = time.monotonic()

    with pytest.raises(GitSourceCancelled):
        checkout_exact_revision(
            tmp_path,
            str(fake_git),
            "a" * 40,
            cancelled=lambda: time.monotonic() >= cancel_at,
            timeout=5,
        )

    assert time.monotonic() - started < 1


def test_scrub_cloned_origin_removes_credentials_from_real_git_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    source = "https://alice:secret@example.test/team/repo.git?token=abc"
    _git(repo, "remote", "add", "origin", source)

    scrub_cloned_origin(repo, "git", source)

    assert _git(repo, "remote", "get-url", "origin") == (
        "https://example.test/team/repo.git"
    )
    assert "secret" not in (repo / ".git" / "config").read_text(encoding="utf-8")
