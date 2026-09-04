"""Credential-safe, bounded Git checkout adapter for workflow marketplaces."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Callable, NoReturn
from urllib.parse import urlsplit

from pydantic import ValidationError

from hermes_cli._subprocess_compat import kill_process_tree, windows_hide_flags
from hermes_cli.git_source import (
    GitSourceError,
    checkout_exact_revision,
    git_head_revision,
    noninteractive_git_env,
    resolve_git_executable,
    resolve_git_source,
    safe_git_error,
    scrub_cloned_origin,
)

from .contract import load_package_contract
from .models import WorkflowMarketplaceSource, WorkflowPackageIndex
from .package import WorkflowMarketplaceError


_INDEX_PATH = ".well-known/hermes-workflows/index.json"
_EXACT_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_MAX_GIT_ERROR_BYTES = 4096
_DEFAULT_CHECKOUT_FILES = 2_100_000
_DEFAULT_CHECKOUT_BYTES = 34 * 1024 * 1024 * 1024
_DEFAULT_TEMPORARY_BYTES = 36 * 1024 * 1024 * 1024
_DEFAULT_TRAVERSAL_ENTRIES = 4_300_000
_FILTER_REJECTION_MARKERS = (
    "server does not support filter",
    "server does not support partial clone",
    "filter capability is not supported",
    "filtering is not supported by server",
    "unsupported filter capability",
)

Cancelled = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ResolvedCheckout:
    """One sparse working tree pinned to an independently verified commit."""

    root: Path
    repository_url: str
    resolved_commit: str


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _canonical_sparse_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
    ):
        _fail("source_sparse_path_invalid", "sparse checkout path is not canonical")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("source_sparse_path_invalid", "sparse checkout path is not canonical")
    if any(part.casefold() == ".git" for part in parts):
        _fail(
            "source_sparse_path_invalid",
            "sparse checkout paths must not contain repository metadata",
        )
    if any(re.match(r"^[A-Za-z]:", part) for part in parts):
        _fail("source_sparse_path_invalid", "sparse checkout path is not canonical")
    return value


def _literal_sparse_pattern(path: str) -> str:
    escaped = "".join(
        f"\\{character}" if character in "*?[" else character for character in path
    )
    return f"/{escaped}"


def _remove_checkout(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        path.unlink()
    else:
        shutil.rmtree(path)


def _check_cancelled(cancelled: Cancelled) -> None:
    try:
        is_cancelled = cancelled()
    except Exception as error:
        _fail("source_cancelled", f"source cancellation check failed: {error}")
    if is_cancelled:
        _fail("source_cancelled", "workflow marketplace Git operation was cancelled")


def _reject_url_credentials(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        _fail("source_invalid", "marketplace Git source URL is invalid")
    if parsed.password is not None:
        _fail(
            "source_credentials_forbidden",
            "marketplace Git source URLs must not contain passwords",
        )
    if parsed.scheme.casefold() not in {"http", "https"} and parsed.query:
        _fail(
            "source_credentials_forbidden",
            "marketplace Git source URLs must not contain credential queries",
        )


def _redacted_git_message(
    result: subprocess.CompletedProcess[str], source_url: str
) -> str:
    rendered = safe_git_error(result, source_url).strip()
    if not rendered:
        rendered = "Git operation failed without diagnostic output"
    encoded = rendered.encode("utf-8")[:_MAX_GIT_ERROR_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def _classify_git_failure(
    result: subprocess.CompletedProcess[str], source_url: str
) -> WorkflowMarketplaceError:
    message = _redacted_git_message(result, source_url)
    lowered = message.casefold()
    if any(
        marker in lowered
        for marker in (
            "authentication failed",
            "authorization failed",
            "permission denied",
            "could not read username",
            "publickey",
            "http 401",
            "http 403",
            "returned error: 401",
            "returned error: 403",
        )
    ):
        return WorkflowMarketplaceError("source_authentication_failed", message)
    return WorkflowMarketplaceError("source_unavailable", message)


def _filter_was_explicitly_rejected(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stderr or ''}\n{result.stdout or ''}".casefold()
    return any(marker in output for marker in _FILTER_REJECTION_MARKERS)


class WorkflowGitFetcher:
    """Fetch one exact sparse checkout without running repository content."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_checkout_files: int = _DEFAULT_CHECKOUT_FILES,
        max_checkout_bytes: int = _DEFAULT_CHECKOUT_BYTES,
        max_temporary_bytes: int = _DEFAULT_TEMPORARY_BYTES,
        max_traversal_entries: int = _DEFAULT_TRAVERSAL_ENTRIES,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if (
            max_checkout_files <= 0
            or max_checkout_bytes <= 0
            or max_temporary_bytes <= 0
            or max_traversal_entries <= 0
        ):
            raise ValueError("checkout limits must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_checkout_files = max_checkout_files
        self.max_checkout_bytes = max_checkout_bytes
        self.max_temporary_bytes = max_temporary_bytes
        self.max_traversal_entries = max_traversal_entries

    def _run_git(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        cancelled: Cancelled,
    ) -> subprocess.CompletedProcess[str]:
        _check_cancelled(cancelled)
        try:
            if os.name == "nt":
                process = subprocess.Popen(
                    arguments,
                    cwd=str(cwd) if cwd is not None else None,
                    stdin=(
                        subprocess.PIPE
                        if input_text is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=noninteractive_git_env(),
                    creationflags=windows_hide_flags(),
                )
            else:
                process = subprocess.Popen(
                    arguments,
                    cwd=str(cwd) if cwd is not None else None,
                    stdin=(
                        subprocess.PIPE
                        if input_text is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=noninteractive_git_env(),
                    process_group=0,
                )
        except OSError as error:
            _fail("source_git_unavailable", f"could not start Git: {error}")
        deadline = time.monotonic() + self.timeout_seconds
        first_communicate = True
        while True:
            try:
                is_cancelled = cancelled()
            except Exception:
                is_cancelled = True
            if is_cancelled:
                kill_process_tree(process)
                try:
                    process.communicate(timeout=1)
                except Exception:
                    pass
                _fail(
                    "source_cancelled",
                    "workflow marketplace Git operation was cancelled",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_process_tree(process)
                try:
                    process.communicate(timeout=1)
                except Exception:
                    pass
                _fail("source_timeout", "workflow marketplace Git operation timed out")
            try:
                stdout, stderr = process.communicate(
                    input=input_text if first_communicate else None,
                    timeout=min(0.1, remaining),
                )
                return subprocess.CompletedProcess(
                    arguments,
                    process.returncode,
                    stdout,
                    stderr,
                )
            except subprocess.TimeoutExpired:
                first_communicate = False

    def _configure_sparse_checkout(
        self,
        git_executable: str,
        destination: Path,
        paths: tuple[str, ...],
        *,
        cancelled: Cancelled,
    ) -> None:
        initialized = self._run_git(
            [git_executable, "sparse-checkout", "init", "--no-cone"],
            cwd=destination,
            cancelled=cancelled,
        )
        if initialized.returncode != 0:
            raise _classify_git_failure(initialized, "")
        patterns = "".join(
            f"{_literal_sparse_pattern(path)}\n" for path in sorted(set(paths))
        )
        configured = self._run_git(
            [
                git_executable,
                "sparse-checkout",
                "set",
                "--no-cone",
                "--stdin",
            ],
            cwd=destination,
            input_text=patterns,
            cancelled=cancelled,
        )
        if configured.returncode != 0:
            raise _classify_git_failure(configured, "")

    def _clone(
        self,
        git_executable: str,
        clone_url: str,
        ref: str | None,
        destination: Path,
        *,
        cancelled: Cancelled,
    ) -> None:
        base = [git_executable, "clone", "--depth", "1", "--no-checkout"]
        if ref is not None and _EXACT_COMMIT.fullmatch(ref) is None:
            base.extend(("--branch", ref))
        filtered = [*base, "--filter=blob:none", "--", clone_url, str(destination)]
        result = self._run_git(filtered, cancelled=cancelled)
        if result.returncode == 0:
            return
        if not _filter_was_explicitly_rejected(result):
            raise _classify_git_failure(result, clone_url)
        _remove_checkout(destination)
        fallback = self._run_git(
            [*base, "--", clone_url, str(destination)],
            cancelled=cancelled,
        )
        if fallback.returncode != 0:
            raise _classify_git_failure(fallback, clone_url)

    def _discover_index_paths(self, root: Path, checkout_root: Path) -> tuple[str, ...]:
        try:
            relative_root = root.relative_to(checkout_root)
        except ValueError:
            _fail("package_index_invalid", "repository catalog root escaped checkout")
        current = checkout_root
        for component in (
            *relative_root.parts,
            ".well-known",
            "hermes-workflows",
        ):
            current /= component
            try:
                parent_metadata = current.lstat()
            except FileNotFoundError:
                return ()
            except OSError:
                _fail(
                    "package_index_invalid",
                    "repository marketplace index parent is unreadable",
                )
            if stat.S_ISLNK(parent_metadata.st_mode):
                _fail(
                    "package_symlink_unsupported",
                    "repository marketplace index parents must not be symbolic links",
                )
            if not stat.S_ISDIR(parent_metadata.st_mode):
                _fail(
                    "package_index_invalid",
                    "repository marketplace index parent is not a directory",
                )
        index_path = root / _INDEX_PATH
        try:
            metadata = index_path.lstat()
        except FileNotFoundError:
            return ()
        except OSError:
            _fail("package_index_invalid", "repository marketplace index is unreadable")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(
                "package_symlink_unsupported",
                "repository marketplace index must not be a symbolic link",
            )
        if not stat.S_ISREG(metadata.st_mode):
            _fail("package_index_invalid", "repository marketplace index is not a file")
        limit = load_package_contract().resource_rules.max_index_bytes
        try:
            if metadata.st_size > limit:
                _fail(
                    "package_index_size_limit",
                    "repository marketplace index exceeds its byte limit",
                )
            flags = os.O_RDONLY
            for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
                flags |= getattr(os, option, 0)
            descriptor = os.open(index_path, flags)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    _fail(
                        "package_index_invalid",
                        "repository marketplace index is not a regular file",
                    )
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    raw = handle.read(limit + 1)
            finally:
                os.close(descriptor)
            if len(raw) > limit:
                _fail(
                    "package_index_size_limit",
                    "repository marketplace index exceeds its byte limit",
                )
            decoded = json.loads(raw)
            if isinstance(decoded, dict) and decoded.get("schemaVersion") not in (
                None,
                1,
            ):
                _fail(
                    "package_contract_unsupported",
                    "repository marketplace index contract version is unsupported",
                )
            index = WorkflowPackageIndex.model_validate(decoded)
        except WorkflowMarketplaceError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            _fail("package_index_invalid", "repository marketplace index is invalid")
        return tuple(entry.package_path for entry in index.packages)

    def _enforce_checkout_budget(self, root: Path) -> None:
        files = 0
        total_bytes = 0
        traversal_entries = 0
        if not root.exists():
            return
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = os.scandir(directory)
            except OSError as error:
                _fail("source_checkout_invalid", f"could not inspect checkout: {error}")
            with entries:
                for entry in entries:
                    if directory == root and entry.name == ".git":
                        continue
                    traversal_entries += 1
                    if traversal_entries > self.max_traversal_entries:
                        _fail(
                            "source_checkout_entry_limit",
                            "sparse checkout exceeds its traversal-entry limit",
                        )
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as error:
                        _fail(
                            "source_checkout_invalid",
                            f"could not inspect checked-out entry: {error}",
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
                    else:
                        files += 1
                        total_bytes += metadata.st_size
                        if files > self.max_checkout_files:
                            _fail(
                                "source_checkout_file_limit",
                                "sparse checkout exceeds its file-count limit",
                            )
                        if total_bytes > self.max_checkout_bytes:
                            _fail(
                                "source_checkout_size_limit",
                                "sparse checkout exceeds its byte limit",
                            )

    def _enforce_temporary_storage_budget(self, root: Path) -> None:
        total_bytes = 0
        traversal_entries = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = os.scandir(directory)
            except OSError as error:
                _fail("source_checkout_invalid", f"could not inspect checkout: {error}")
            with entries:
                for entry in entries:
                    traversal_entries += 1
                    if traversal_entries > self.max_traversal_entries:
                        _fail(
                            "source_temporary_entry_limit",
                            "temporary checkout exceeds its traversal-entry limit",
                        )
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as error:
                        _fail(
                            "source_checkout_invalid",
                            f"could not inspect temporary Git entry: {error}",
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
                    else:
                        total_bytes += metadata.st_size
                        if total_bytes > self.max_temporary_bytes:
                            _fail(
                                "source_temporary_size_limit",
                                "temporary Git checkout exceeds its byte limit",
                            )

    def fetch(
        self,
        source: WorkflowMarketplaceSource,
        destination: Path,
        *,
        sparse_paths: tuple[str, ...],
        cancelled: Cancelled = lambda: False,
    ) -> ResolvedCheckout:
        """Fetch one exact, sanitized checkout without executing package content."""

        if not source.enabled:
            _fail("source_disabled", f"marketplace source {source.name!r} is disabled")
        _reject_url_credentials(source.repository_url)
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            _fail(
                "source_destination_exists", "Git checkout destination already exists"
            )
        canonical_paths = tuple(_canonical_sparse_path(path) for path in sparse_paths)
        if not canonical_paths:
            _fail("source_sparse_path_invalid", "at least one sparse path is required")
        try:
            resolved = resolve_git_source(source.repository_url)
        except GitSourceError as error:
            _fail("source_invalid", str(error))
        subdirectory = (
            _canonical_sparse_path(resolved.subdirectory)
            if resolved.subdirectory is not None
            else None
        )
        prefixed_paths = tuple(
            f"{subdirectory}/{path}" if subdirectory else path
            for path in canonical_paths
        )
        git_executable = resolve_git_executable()
        if not git_executable:
            _fail("source_git_unavailable", "Git executable was not found")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _check_cancelled(cancelled)
            self._clone(
                git_executable,
                resolved.clone_url,
                source.ref,
                destination,
                cancelled=cancelled,
            )
            _check_cancelled(cancelled)
            scrub_cloned_origin(destination, git_executable, resolved.clone_url)
            self._configure_sparse_checkout(
                git_executable,
                destination,
                prefixed_paths,
                cancelled=cancelled,
            )
            _check_cancelled(cancelled)
            requested_revision = (
                source.ref.lower()
                if source.ref is not None and _EXACT_COMMIT.fullmatch(source.ref)
                else git_head_revision(destination, git_executable)
            )
            checkout_exact_revision(
                destination,
                git_executable,
                requested_revision,
            )
            _check_cancelled(cancelled)
            resolved_commit = git_head_revision(destination, git_executable)
            if resolved_commit != requested_revision:
                _fail(
                    "source_revision_mismatch",
                    "checked-out Git revision does not match the resolved commit",
                )
            root = destination / subdirectory if subdirectory else destination
            discovered = self._discover_index_paths(root, destination)
            if discovered:
                expanded = tuple(
                    sorted(set((*canonical_paths, *discovered, _INDEX_PATH)))
                )
                expanded_prefixed = tuple(
                    f"{subdirectory}/{path}" if subdirectory else path
                    for path in expanded
                )
                self._configure_sparse_checkout(
                    git_executable,
                    destination,
                    expanded_prefixed,
                    cancelled=cancelled,
                )
            _check_cancelled(cancelled)
            self._enforce_checkout_budget(destination)
            self._enforce_temporary_storage_budget(destination)
            return ResolvedCheckout(
                root=root,
                repository_url=source.repository_url,
                resolved_commit=resolved_commit,
            )
        except WorkflowMarketplaceError:
            _remove_checkout(destination)
            raise
        except GitSourceError as error:
            _remove_checkout(destination)
            completed = subprocess.CompletedProcess(
                args=(), returncode=1, stdout="", stderr=str(error)
            )
            raise _classify_git_failure(completed, source.repository_url) from error
        except BaseException:
            _remove_checkout(destination)
            raise


__all__ = ["ResolvedCheckout", "WorkflowGitFetcher"]
