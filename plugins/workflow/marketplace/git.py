"""Credential-safe, bounded Git checkout adapter for workflow marketplaces."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Callable, NoReturn

from hermes_cli.git_source import (
    GitSourceCancelled,
    GitSourceError,
    GitSourceResourceLimit,
    GitSourceTimeout,
    checkout_exact_revision,
    git_head_revision,
    is_exact_revision,
    resolve_git_executable,
    resolve_git_source,
    run_git_bounded,
    safe_git_error,
    scrub_cloned_origin,
    validate_credential_free_git_source,
)

from .models import PACKAGE_ID_PATTERN, WorkflowMarketplaceSource
from .package import (
    WorkflowMarketplaceError,
    load_repository_index_document_if_present,
)


_INDEX_PATH = ".well-known/hermes-workflows/index.json"
_MAX_GIT_ERROR_BYTES = 4096
_DEFAULT_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
_DEFAULT_CHECKOUT_FILES = 2_100_000
_DEFAULT_CHECKOUT_BYTES = 34 * 1024 * 1024 * 1024
_DEFAULT_TEMPORARY_BYTES = 36 * 1024 * 1024 * 1024
_DEFAULT_TRAVERSAL_ENTRIES = 4_300_000
_FILTER_REJECTION_MESSAGES = frozenset({
    "server does not support filter",
    "server does not support filter capability",
    "server does not support partial clone",
    "filter capability is not supported",
    "filtering is not supported by server",
    "unsupported filter capability",
})
_GIT_DIAGNOSTIC_PREFIX = re.compile(r"^(?:(?:fatal|error|remote):\s*)+")

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
            "invalid credentials",
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
    lines = [
        line.strip().casefold()
        for output in (result.stderr or "", result.stdout or "")
        for line in output.splitlines()
        if line.strip()
    ]
    if len(lines) != 1:
        return False
    message = _GIT_DIAGNOSTIC_PREFIX.sub("", lines[0]).removesuffix(".")
    return message in _FILTER_REJECTION_MESSAGES


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
        max_git_output_bytes: int = _DEFAULT_GIT_OUTPUT_BYTES,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if (
            max_checkout_files <= 0
            or max_checkout_bytes <= 0
            or max_temporary_bytes <= 0
            or max_traversal_entries <= 0
            or max_git_output_bytes <= 0
        ):
            raise ValueError("checkout limits must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_checkout_files = max_checkout_files
        self.max_checkout_bytes = max_checkout_bytes
        self.max_temporary_bytes = max_temporary_bytes
        self.max_traversal_entries = max_traversal_entries
        self.max_git_output_bytes = max_git_output_bytes

    def _run_git(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        cancelled: Cancelled,
        storage_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return run_git_bounded(
                arguments,
                cwd=cwd,
                input_text=input_text,
                timeout=self.timeout_seconds,
                cancelled=cancelled,
                max_output_bytes=self.max_git_output_bytes,
                storage_root=storage_root,
                max_storage_bytes=(
                    self.max_temporary_bytes if storage_root is not None else None
                ),
                max_storage_entries=(
                    self.max_traversal_entries if storage_root is not None else None
                ),
            )
        except GitSourceCancelled as error:
            _fail("source_cancelled", str(error))
        except GitSourceTimeout as error:
            _fail("source_timeout", str(error))
        except GitSourceResourceLimit as error:
            code = (
                "source_output_limit"
                if error.kind == "output"
                else "source_temporary_size_limit"
            )
            _fail(code, str(error))
        except GitSourceError:
            _fail("source_git_unavailable", "could not start Git")

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
            storage_root=destination,
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
            storage_root=destination,
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
        if ref is not None and not is_exact_revision(ref):
            base.extend(("--branch", ref))
        filtered = [*base, "--filter=blob:none", "--", clone_url, str(destination)]
        result = self._run_git(filtered, cancelled=cancelled, storage_root=destination)
        if result.returncode == 0:
            return
        if not _filter_was_explicitly_rejected(result):
            raise _classify_git_failure(result, clone_url)
        _remove_checkout(destination)
        fallback = self._run_git(
            [*base, "--", clone_url, str(destination)],
            cancelled=cancelled,
            storage_root=destination,
        )
        if fallback.returncode != 0:
            raise _classify_git_failure(fallback, clone_url)

    def _discover_index_paths(
        self,
        root: Path,
        checkout_root: Path,
        *,
        selected_package_id: str | None,
    ) -> tuple[str, ...]:
        try:
            root.relative_to(checkout_root)
        except ValueError:
            _fail("package_index_invalid", "repository catalog root escaped checkout")
        try:
            index = load_repository_index_document_if_present(root)
        except WorkflowMarketplaceError as error:
            # Preserve the Git fetcher's established index-boundary diagnostic:
            # repository metadata is never a valid sparse package root.
            if error.code == "package_repository_metadata":
                _fail(
                    "package_index_invalid",
                    "repository marketplace index is invalid",
                )
            raise
        if index is None:
            if selected_package_id is None:
                return ()
            _fail(
                "package_index_invalid",
                "repository marketplace index is unavailable",
            )
        if selected_package_id is None:
            return tuple(entry.package_path for entry in index.packages)
        matches = [entry for entry in index.packages if entry.id == selected_package_id]
        if not matches:
            _fail(
                "catalog_package_not_found",
                "selected marketplace package was not found in the repository index",
            )
        if len(matches) != 1:
            _fail(
                "package_index_invalid",
                "repository marketplace package identity is ambiguous",
            )
        return (matches[0].package_path,)

    def _preflight_paths(
        self,
        git_executable: str,
        destination: Path,
        revision: str,
        paths: tuple[str, ...],
        *,
        cancelled: Cancelled,
    ) -> None:
        result = self._run_git(
            [git_executable, "ls-tree", "-r", "-l", "-z", revision, "--", *paths],
            cwd=destination,
            cancelled=cancelled,
            storage_root=destination,
        )
        if result.returncode != 0:
            raise _classify_git_failure(result, "")
        files = 0
        total_bytes = 0
        for record in result.stdout.split("\x00"):
            if not record:
                continue
            metadata, separator, _path = record.partition("\t")
            fields = metadata.split()
            if not separator or len(fields) != 4 or not fields[3].isdigit():
                _fail("source_checkout_invalid", "Git tree metadata is malformed")
            files += 1
            total_bytes += int(fields[3])
            if files > self.max_checkout_files:
                _fail(
                    "source_checkout_file_limit",
                    "selected Git tree exceeds its file-count limit",
                )
            if total_bytes > self.max_checkout_bytes:
                _fail(
                    "source_checkout_size_limit",
                    "selected Git tree exceeds its byte limit",
                )

    def _preflight_index_components(
        self,
        git_executable: str,
        destination: Path,
        revision: str,
        index_path: str,
        *,
        cancelled: Cancelled,
    ) -> None:
        treeish = revision
        components = index_path.split("/")
        for position, component in enumerate(components):
            result = self._run_git(
                [git_executable, "ls-tree", "-z", treeish, "--", component],
                cwd=destination,
                cancelled=cancelled,
                storage_root=destination,
            )
            if result.returncode != 0:
                raise _classify_git_failure(result, "")
            records = [record for record in result.stdout.split("\x00") if record]
            if not records:
                return
            if len(records) != 1:
                _fail("package_index_invalid", "Git index path is ambiguous")
            metadata, separator, path = records[0].partition("\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or path != component:
                _fail("package_index_invalid", "Git index path metadata is invalid")
            mode, entry_type, object_id = fields
            if mode == "120000":
                _fail(
                    "package_symlink_unsupported",
                    "repository marketplace index paths must not contain links",
                )
            is_leaf = position == len(components) - 1
            if is_leaf:
                if entry_type != "blob" or not mode.startswith("100"):
                    _fail(
                        "package_index_invalid",
                        "repository marketplace index is invalid",
                    )
                return
            if entry_type != "tree" or mode != "040000":
                _fail(
                    "package_index_invalid",
                    "repository marketplace index parent is invalid",
                )
            treeish = object_id

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
        selected_package_id: str | None = None,
        cancelled: Cancelled = lambda: False,
    ) -> ResolvedCheckout:
        """Fetch one exact, sanitized checkout without executing package content."""

        if not source.enabled:
            _fail("source_disabled", f"marketplace source {source.name!r} is disabled")
        try:
            validate_credential_free_git_source(source.repository_url)
        except GitSourceError as error:
            code = (
                "source_credentials_forbidden"
                if "credentials" in str(error).casefold()
                else "source_invalid"
            )
            _fail(code, str(error))
        if selected_package_id is not None and (
            not isinstance(selected_package_id, str)
            or re.fullmatch(PACKAGE_ID_PATTERN, selected_package_id) is None
        ):
            _fail(
                "catalog_identifier_invalid",
                "selected marketplace package identity is invalid",
            )
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            _fail(
                "source_destination_exists", "Git checkout destination already exists"
            )
        canonical_paths = tuple(_canonical_sparse_path(path) for path in sparse_paths)
        if not canonical_paths:
            _fail("source_sparse_path_invalid", "at least one sparse path is required")
        if selected_package_id is not None and canonical_paths != (_INDEX_PATH,):
            _fail(
                "source_sparse_path_invalid",
                "selected package fetch requires only the marketplace index path",
            )
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
                str(source.ref).lower()
                if is_exact_revision(source.ref)
                else git_head_revision(
                    destination,
                    git_executable,
                    cancelled=cancelled,
                    timeout=self.timeout_seconds,
                    max_output_bytes=self.max_git_output_bytes,
                    storage_root=destination,
                    max_storage_bytes=self.max_temporary_bytes,
                    max_storage_entries=self.max_traversal_entries,
                )
            )
            exact_requested = is_exact_revision(source.ref)

            def preflight_initial_paths() -> None:
                if _INDEX_PATH in canonical_paths:
                    index_path = (
                        f"{subdirectory}/{_INDEX_PATH}" if subdirectory else _INDEX_PATH
                    )
                    self._preflight_index_components(
                        git_executable,
                        destination,
                        requested_revision,
                        index_path,
                        cancelled=cancelled,
                    )
                self._preflight_paths(
                    git_executable,
                    destination,
                    requested_revision,
                    prefixed_paths,
                    cancelled=cancelled,
                )

            if not exact_requested:
                preflight_initial_paths()
            checkout_exact_revision(
                destination,
                git_executable,
                requested_revision,
                cancelled=cancelled,
                timeout=self.timeout_seconds,
                max_output_bytes=self.max_git_output_bytes,
                storage_root=destination,
                max_storage_bytes=self.max_temporary_bytes,
                max_storage_entries=self.max_traversal_entries,
                before_checkout=(preflight_initial_paths if exact_requested else None),
            )
            _check_cancelled(cancelled)
            resolved_commit = git_head_revision(
                destination,
                git_executable,
                cancelled=cancelled,
                timeout=self.timeout_seconds,
                max_output_bytes=self.max_git_output_bytes,
                storage_root=destination,
                max_storage_bytes=self.max_temporary_bytes,
                max_storage_entries=self.max_traversal_entries,
            )
            if resolved_commit != requested_revision:
                _fail(
                    "source_revision_mismatch",
                    "checked-out Git revision does not match the resolved commit",
                )
            root = destination / subdirectory if subdirectory else destination
            discovered = self._discover_index_paths(
                root,
                destination,
                selected_package_id=selected_package_id,
            )
            if discovered:
                discovered = tuple(_canonical_sparse_path(path) for path in discovered)
                expanded = (
                    tuple(sorted((_INDEX_PATH, discovered[0])))
                    if selected_package_id is not None
                    else tuple(
                        sorted(set((*canonical_paths, *discovered, _INDEX_PATH)))
                    )
                )
                expanded_prefixed = tuple(
                    f"{subdirectory}/{path}" if subdirectory else path
                    for path in expanded
                )
                self._preflight_paths(
                    git_executable,
                    destination,
                    resolved_commit,
                    expanded_prefixed,
                    cancelled=cancelled,
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
        except GitSourceCancelled as error:
            _remove_checkout(destination)
            _fail("source_cancelled", str(error))
        except GitSourceTimeout as error:
            _remove_checkout(destination)
            _fail("source_timeout", str(error))
        except GitSourceResourceLimit as error:
            _remove_checkout(destination)
            code = (
                "source_output_limit"
                if error.kind == "output"
                else "source_temporary_size_limit"
            )
            _fail(code, str(error))
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
