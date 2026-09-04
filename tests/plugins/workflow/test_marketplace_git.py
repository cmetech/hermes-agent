"""Real-Git tests for bounded workflow marketplace checkouts."""

from __future__ import annotations

import subprocess
from pathlib import Path
import sys
import time

import pytest

import plugins.workflow.marketplace.git as marketplace_git
import hermes_cli.git_source as shared_git_source
from hermes_cli.git_source import GitSourceCancelled
from plugins.workflow.marketplace.git import WorkflowGitFetcher
from plugins.workflow.marketplace.models import WorkflowMarketplaceSource
from plugins.workflow.marketplace.package import WorkflowMarketplaceError


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip().lower()


@pytest.fixture
def versioned_remote(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "marketplace@example.test")
    _git(work, "config", "user.name", "Marketplace Test")
    (work / "selected.txt").write_text("one\n", encoding="utf-8")
    (work / "ignored.txt").write_text("not sparse\n", encoding="utf-8")
    nested = work / "catalog"
    nested.mkdir()
    (nested / "selected.txt").write_text("nested\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "first")
    first = _git(work, "rev-parse", "HEAD")
    _git(work, "tag", "v1")

    _git(work, "switch", "-c", "release")
    (work / "selected.txt").write_text("release\n", encoding="utf-8")
    _git(work, "commit", "-am", "release")
    release = _git(work, "rev-parse", "HEAD")

    _git(work, "switch", "main")
    (work / "selected.txt").write_text("two\n", encoding="utf-8")
    _git(work, "commit", "-am", "second")
    main = _git(work, "rev-parse", "HEAD")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(work), str(remote))
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, {"first": first, "release": release, "main": main}


def _source(url: str, *, ref: str | None = None) -> WorkflowMarketplaceSource:
    return WorkflowMarketplaceSource.model_validate({
        "name": "company",
        "repositoryUrl": url,
        "ref": ref,
        "enabled": True,
    })


@pytest.mark.parametrize(
    ("ref_key", "ref", "content"),
    [
        ("main", None, "two\n"),
        ("release", "release", "release\n"),
        ("first", "v1", "one\n"),
        ("first", "exact", "one\n"),
    ],
)
def test_fetch_resolves_default_branch_branch_tag_and_exact_sha(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
    ref_key: str,
    ref: str | None,
    content: str,
) -> None:
    remote, commits = versioned_remote
    requested_ref = commits[ref_key] if ref == "exact" else ref
    destination = tmp_path / f"checkout-{ref_key}-{ref or 'default'}"

    checkout = WorkflowGitFetcher().fetch(
        _source(remote.as_uri(), ref=requested_ref),
        destination,
        sparse_paths=("selected.txt",),
    )

    assert checkout.resolved_commit == commits[ref_key]
    assert checkout.root.joinpath("selected.txt").read_text() == content
    assert not checkout.root.joinpath("ignored.txt").exists()
    assert _git(destination, "rev-parse", "HEAD") == commits[ref_key]


def test_fetch_supports_a_direct_repository_subdirectory(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, commits = versioned_remote

    checkout = WorkflowGitFetcher().fetch(
        _source(f"{remote.as_uri()}#catalog"),
        tmp_path / "checkout",
        sparse_paths=("selected.txt",),
    )

    assert checkout.root == tmp_path / "checkout" / "catalog"
    assert checkout.root.joinpath("selected.txt").read_text() == "nested\n"
    assert checkout.resolved_commit == commits["main"]


def test_filter_rejection_falls_back_to_the_same_real_bare_repository(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, commits = versioned_remote

    class RejectFilterOnce(WorkflowGitFetcher):
        rejected = False

        def _run_git(self, arguments, **kwargs):
            if arguments[1] == "clone" and "--filter=blob:none" in arguments:
                self.rejected = True
                return subprocess.CompletedProcess(
                    arguments,
                    128,
                    "",
                    "fatal: server does not support filter capability",
                )
            return super()._run_git(arguments, **kwargs)

    fetcher = RejectFilterOnce()
    checkout = fetcher.fetch(
        _source(remote.as_uri()),
        tmp_path / "checkout",
        sparse_paths=("selected.txt",),
    )

    assert fetcher.rejected is True
    assert checkout.resolved_commit == commits["main"]
    assert checkout.root.joinpath("selected.txt").read_text() == "two\n"


def test_generic_clone_failure_does_not_trigger_unfiltered_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailClone(WorkflowGitFetcher):
        def _run_git(self, arguments, **kwargs):
            if "--filter=blob:none" not in arguments:
                raise AssertionError(
                    "generic failures must not retry without filtering"
                )
            return subprocess.CompletedProcess(
                arguments,
                128,
                "",
                "fatal: network connection was reset",
            )

    monkeypatch.setattr(marketplace_git, "resolve_git_executable", lambda: "git")
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="source_unavailable"):
        FailClone().fetch(
            _source("https://example.test/company/repo.git"),
            destination,
            sparse_paths=("selected.txt",),
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    ("diagnostic", "code"),
    [
        (
            "fatal: authentication failed; server does not support filter capability",
            "source_authentication_failed",
        ),
        (
            "fatal: network connection reset; server does not support filter capability",
            "source_unavailable",
        ),
        (
            "fatal: could not read Username; server does not support filter capability",
            "source_authentication_failed",
        ),
        (
            "fatal: returned error: 403; server does not support filter capability",
            "source_authentication_failed",
        ),
        (
            "fatal: invalid credentials; server does not support filter capability",
            "source_authentication_failed",
        ),
        (
            "fatal: server does not support filter capability\n"
            "fatal: arbitrary repository failure",
            "source_unavailable",
        ),
        (
            "error: arbitrary transport failure\n"
            "fatal: server does not support filter capability",
            "source_unavailable",
        ),
    ],
)
def test_mixed_filter_and_auth_or_network_failure_never_retries_unfiltered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: str,
    code: str,
) -> None:
    class MixedFailure(WorkflowGitFetcher):
        def _run_git(self, arguments, **kwargs):
            if "--filter=blob:none" not in arguments:
                raise AssertionError("mixed failure retried without filtering")
            return subprocess.CompletedProcess(arguments, 128, "", diagnostic)

    monkeypatch.setattr(marketplace_git, "resolve_git_executable", lambda: "git")

    with pytest.raises(WorkflowMarketplaceError) as error:
        MixedFailure().fetch(
            _source("https://example.test/company/repo.git"),
            tmp_path / "checkout",
            sparse_paths=("selected.txt",),
        )

    assert error.value.code == code


@pytest.mark.parametrize(
    "diagnostic",
    [
        "fatal: server does not support filter capability",
        "fatal: server does not support partial clone",
        "error: filter capability is not supported.",
        "remote: filtering is not supported by server",
        "fatal: unsupported filter capability",
    ],
)
def test_pure_recognized_filter_negotiation_failures_retry_unfiltered(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
    diagnostic: str,
) -> None:
    remote, commits = versioned_remote

    class RejectFilterOnce(WorkflowGitFetcher):
        retries = 0

        def _run_git(self, arguments, **kwargs):
            if arguments[1] == "clone" and "--filter=blob:none" in arguments:
                return subprocess.CompletedProcess(arguments, 128, "", diagnostic)
            if arguments[1] == "clone":
                self.retries += 1
            return super()._run_git(arguments, **kwargs)

    fetcher = RejectFilterOnce()
    checkout = fetcher.fetch(
        _source(remote.as_uri()),
        tmp_path / "checkout",
        sparse_paths=("selected.txt",),
    )

    assert fetcher.retries == 1
    assert checkout.resolved_commit == commits["main"]


def test_authentication_failure_is_redacted_and_partial_clone_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "checkout"

    class FailAuthentication(WorkflowGitFetcher):
        def _run_git(self, arguments, **kwargs):
            destination.mkdir()
            (destination / "partial").write_text("partial")
            return subprocess.CompletedProcess(
                arguments,
                128,
                "",
                (
                    "fatal: authentication failed for "
                    "https://alice:clone-secret@example.test/repo.git?token=query-secret"
                ),
            )

    monkeypatch.setattr(marketplace_git, "resolve_git_executable", lambda: "git")

    with pytest.raises(WorkflowMarketplaceError) as error:
        FailAuthentication().fetch(
            _source("https://example.test/company/repo.git"),
            destination,
            sparse_paths=("selected.txt",),
        )

    assert error.value.code == "source_authentication_failed"
    assert "clone-secret" not in str(error.value)
    assert "query-secret" not in str(error.value)
    assert not destination.exists()


def test_fetch_rejects_ssh_password_even_when_source_model_was_built_directly(
    tmp_path: Path,
) -> None:
    class CloneMustNotRun(WorkflowGitFetcher):
        def _clone(self, *args, **kwargs):
            raise AssertionError("credential-bearing source reached Git")

    with pytest.raises(WorkflowMarketplaceError, match="source_credentials_forbidden"):
        CloneMustNotRun().fetch(
            WorkflowMarketplaceSource.model_construct(
                name="company",
                repository_url="ssh://git:ssh-secret@example.test/team/repo.git",
                ref=None,
                enabled=True,
            ),
            tmp_path / "checkout",
            sparse_paths=("selected.txt",),
        )


@pytest.mark.parametrize("code", ["source_cancelled", "source_timeout"])
def test_cancel_or_timeout_cleans_partial_checkout(
    tmp_path: Path,
    code: str,
) -> None:
    destination = tmp_path / "checkout"

    class SlowClone(WorkflowGitFetcher):
        def _clone(self, git_executable, clone_url, ref, destination, *, cancelled):
            destination.mkdir()
            (destination / "partial").write_text("partial")
            self._run_git(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cancelled=cancelled,
            )

    started = time.monotonic()
    cancel_after = started + 0.05
    cancelled = (
        (lambda: time.monotonic() >= cancel_after)
        if code == "source_cancelled"
        else (lambda: False)
    )
    fetcher = SlowClone(timeout_seconds=2 if code == "source_cancelled" else 0.05)

    with pytest.raises(WorkflowMarketplaceError) as error:
        fetcher.fetch(
            _source("https://example.test/company/repo.git"),
            destination,
            sparse_paths=("selected.txt",),
            cancelled=cancelled,
        )

    assert error.value.code == code
    assert time.monotonic() - started < 1
    assert not destination.exists()


def test_timeout_uses_shared_process_tree_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    shared_cleanup = shared_git_source.kill_process_tree

    def record_cleanup(process) -> None:
        calls.append(process.pid)
        shared_cleanup(process)

    monkeypatch.setattr(shared_git_source, "kill_process_tree", record_cleanup)
    fetcher = WorkflowGitFetcher(timeout_seconds=0.05)

    with pytest.raises(WorkflowMarketplaceError, match="source_timeout"):
        fetcher._run_git(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cancelled=lambda: False,
        )

    assert len(calls) == 1


def test_fetcher_kills_running_command_at_bounded_output_limit() -> None:
    fetcher = WorkflowGitFetcher(
        timeout_seconds=5,
        max_git_output_bytes=1024,
    )
    started = time.monotonic()

    with pytest.raises(WorkflowMarketplaceError) as error:
        fetcher._run_git(
            [
                sys.executable,
                "-c",
                "import sys; chunk=b'x'*4096;\nwhile True: sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()",
            ],
            cancelled=lambda: False,
        )

    assert error.value.code == "source_output_limit"
    assert time.monotonic() - started < 1


def test_fetcher_kills_running_command_when_temporary_storage_crosses_limit(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "growing.bin"
    fetcher = WorkflowGitFetcher(
        timeout_seconds=5,
        max_temporary_bytes=1024,
        max_traversal_entries=16,
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        fetcher._run_git(
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
            cancelled=lambda: False,
            storage_root=tmp_path,
        )

    size_after_kill = payload.stat().st_size
    time.sleep(0.05)
    assert error.value.code == "source_temporary_size_limit"
    assert payload.stat().st_size == size_after_kill


def test_preflight_rejects_selected_blob_before_exact_checkout(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, _ = versioned_remote

    def checkout_must_not_run(*args, **kwargs):
        raise AssertionError("oversized selected blob reached checkout")

    monkeypatch.setattr(
        marketplace_git,
        "checkout_exact_revision",
        checkout_must_not_run,
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        WorkflowGitFetcher(max_checkout_bytes=1).fetch(
            _source(remote.as_uri()),
            tmp_path / "checkout",
            sparse_paths=("selected.txt",),
        )

    assert error.value.code == "source_checkout_size_limit"


def test_exact_sha_is_preflighted_before_worktree_materialization(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, commits = versioned_remote
    destination = tmp_path / "checkout"

    class StopAtPreflight(WorkflowGitFetcher):
        def _preflight_paths(self, *args, **kwargs):
            assert not destination.joinpath("selected.txt").exists()
            raise WorkflowMarketplaceError("preflight_stop", "stopped at preflight")

    with pytest.raises(WorkflowMarketplaceError) as error:
        StopAtPreflight().fetch(
            _source(remote.as_uri(), ref=commits["first"]),
            destination,
            sparse_paths=("selected.txt",),
        )

    assert error.value.code == "preflight_stop"


def test_exact_checkout_receives_fetcher_cancellation_and_limits(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, _ = versioned_remote
    captured: dict[str, object] = {}
    cancelled = lambda: False

    def cancel_exact_checkout(_root, _git, _revision, **kwargs):
        captured.update(kwargs)
        raise GitSourceCancelled("cancelled")

    monkeypatch.setattr(
        marketplace_git,
        "checkout_exact_revision",
        cancel_exact_checkout,
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        WorkflowGitFetcher(timeout_seconds=0.25).fetch(
            _source(remote.as_uri()),
            tmp_path / "checkout",
            sparse_paths=("selected.txt",),
            cancelled=cancelled,
        )

    assert error.value.code == "source_cancelled"
    assert captured["cancelled"] is cancelled
    assert captured["timeout"] == 0.25
    assert captured["storage_root"] == tmp_path / "checkout"


def test_cancelled_before_clone_does_not_create_checkout(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, _ = versioned_remote
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="source_cancelled"):
        WorkflowGitFetcher().fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=("selected.txt",),
            cancelled=lambda: True,
        )

    assert not destination.exists()


def test_checked_out_file_budget_is_enforced(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, _ = versioned_remote
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="source_checkout_file_limit"):
        WorkflowGitFetcher(max_checkout_files=1).fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=("selected.txt", "ignored.txt"),
        )

    assert not destination.exists()


def test_temporary_storage_budget_includes_git_objects(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
) -> None:
    remote, _ = versioned_remote
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="source_temporary_size_limit"):
        WorkflowGitFetcher(max_temporary_bytes=1).fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=("selected.txt",),
        )

    assert not destination.exists()


def test_index_symlink_is_rejected_before_sparse_path_discovery(tmp_path: Path) -> None:
    work = tmp_path / "work"
    index_dir = work / ".well-known" / "hermes-workflows"
    index_dir.mkdir(parents=True)
    outside = tmp_path / "outside-index.json"
    outside.write_text('{"schemaVersion":1,"packages":[]}\n', encoding="utf-8")
    try:
        (index_dir / "index.json").symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit file symlinks")
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "marketplace@example.test")
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "symlink")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(work), str(remote))
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="package_symlink_unsupported"):
        WorkflowGitFetcher().fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=(".well-known",),
        )

    assert not destination.exists()


def test_index_parent_symlink_is_rejected_before_sparse_path_discovery(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside-well-known"
    (outside / "hermes-workflows").mkdir(parents=True)
    (outside / "hermes-workflows" / "index.json").write_text(
        '{"schemaVersion":1,"packages":[]}\n', encoding="utf-8"
    )
    try:
        (work / ".well-known").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "marketplace@example.test")
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "parent symlink")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(work), str(remote))
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="package_symlink_unsupported"):
        WorkflowGitFetcher().fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=(".well-known",),
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "sparse_path",
    ["../escape", "/absolute", "bad\\path", ".git/config", ""],
)
def test_sparse_paths_must_be_canonical_and_never_create_a_checkout(
    tmp_path: Path,
    versioned_remote: tuple[Path, dict[str, str]],
    sparse_path: str,
) -> None:
    remote, _ = versioned_remote
    destination = tmp_path / "checkout"

    with pytest.raises(WorkflowMarketplaceError, match="source_sparse_path_invalid"):
        WorkflowGitFetcher().fetch(
            _source(remote.as_uri()),
            destination,
            sparse_paths=(sparse_path,),
        )

    assert not destination.exists()
