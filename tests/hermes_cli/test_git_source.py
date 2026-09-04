"""Credential-safe shared Git source behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli import git_source
from hermes_cli.git_source import (
    GitSourceError,
    ResolvedGitSource,
    canonical_git_source,
    checkout_exact_revision,
    git_head_revision,
    noninteractive_git_env,
    resolve_git_source,
    safe_git_error,
    scrub_cloned_origin,
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


def test_safe_git_error_strips_credential_urls_without_source_hint():
    result = _completed(
        stderr=(
            "fatal: redirect to "
            "https://bob:redirect-secret@mirror.example/repo.git?access_token=redirect-token"
        )
    )

    rendered = safe_git_error(result)

    assert "redirect-secret" not in rendered
    assert "redirect-token" not in rendered
    assert "bob:***@mirror.example" in rendered
    assert "access_token=***" in rendered


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
