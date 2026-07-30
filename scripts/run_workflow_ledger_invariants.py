#!/usr/bin/env python3
"""Execute every executable invariant declared by the workflow ledger.

Each Python or JavaScript test file runs in its own subprocess.  Non-executable
fixture and runner references are retained as reference-only records; they
never receive a test result and therefore cannot satisfy an executed invariant.
"""

from __future__ import annotations

import argparse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

import psutil
import yaml


_DEFAULT_TIMEOUT_SECONDS = 900.0
_DEFAULT_OUTPUT_LIMIT_BYTES = 1_048_576
_POLL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 1.0
_NODE_DEPENDENCY_MAX_ENTRIES = 250_000
_NODE_DEPENDENCY_AUDIT_SECONDS = 60.0
_NODE_DEPENDENCY_CACHE_PATHS = frozenset({Path(".vite"), Path(".vite-temp")})
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


@dataclass(frozen=True)
class _AttemptDiagnostic:
    attempt: int
    result: str
    stdout: str
    stderr: str


class _AttemptRevalidationError(ValueError):
    def __init__(
        self,
        path: str,
        diagnostics: tuple[_AttemptDiagnostic, ...],
        cause: ValueError,
    ) -> None:
        super().__init__(str(cause))
        self.path = path
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class _InvariantDiagnostics:
    path: str
    diagnostics: tuple[_AttemptDiagnostic, ...]


class _GroupAttemptRevalidationError(ValueError):
    def __init__(
        self,
        diagnostics: tuple[_InvariantDiagnostics, ...],
        errors: tuple[_AttemptRevalidationError, ...],
    ) -> None:
        ordered_errors = tuple(sorted(errors, key=lambda error: error.path))
        super().__init__(str(ordered_errors[0]))
        self.diagnostics = diagnostics


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _windows_error(operation: str) -> OSError:
    code = getattr(ctypes, "get_last_error", lambda: 0)()
    return OSError(code, f"Windows Job Object {operation} failed")


def _configure_windows_job_api(kernel32: Any) -> None:
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], ctypes.c_void_p),
        "SetInformationJobObject": (
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": (
            [ctypes.c_void_p, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "TerminateJobObject": (
            [ctypes.c_void_p, wintypes.UINT],
            wintypes.BOOL,
        ),
        "CloseHandle": ([ctypes.c_void_p], wintypes.BOOL),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(kernel32, name)
        try:
            function.argtypes = argtypes
            function.restype = restype
        except AttributeError:
            # Test doubles are ordinary bound methods rather than ctypes
            # functions; their recorded composition is still authoritative.
            continue


class _WindowsJobContainment:
    """Windows process-tree containment with kill-on-close kernel ownership."""

    def __init__(self, kernel32: Any | None = None) -> None:
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_windows_job_api(kernel32)
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _windows_error("creation")
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise _windows_error("configuration")

    def assign(self, process_handle: int) -> None:
        if self._handle is None or not self._kernel32.AssignProcessToJobObject(
            self._handle, process_handle
        ):
            raise _windows_error("assignment")

    def terminate(self) -> None:
        if self._handle is not None and not self._kernel32.TerminateJobObject(
            self._handle, 1
        ):
            raise _windows_error("termination")

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._kernel32.CloseHandle(handle):
                raise _windows_error("close")


_WINDOWS_JOB_BOOTSTRAP = (
    "import subprocess, sys\n"
    "if sys.stdin.buffer.read(1) != b'1':\n"
    "    raise SystemExit(125)\n"
    "try:\n"
    "    child = subprocess.Popen(sys.argv[1:])\n"
    "except OSError as exc:\n"
    "    print(f'invariant bootstrap failed: {exc}', file=sys.stderr)\n"
    "    raise SystemExit(125)\n"
    "raise SystemExit(child.wait())\n"
)
_POSIX_GROUP_BOOTSTRAP = (
    "import os, signal, subprocess, sys\n"
    "status_fd = int(sys.argv[1])\n"
    "child = subprocess.Popen(sys.argv[2:])\n"
    "returncode = child.wait()\n"
    "os.write(status_fd, f'{returncode}\\n'.encode('ascii'))\n"
    "os.close(status_fd)\n"
    "while True:\n"
    "    signal.pause()\n"
)


def _kind(path: str) -> str:
    item = Path(path)
    if path.startswith("tests/") and item.suffix == ".py" and item.name.startswith(
        "test_"
    ):
        return "python"
    if path.startswith("apps/desktop/electron/") and item.name.endswith(".test.ts"):
        return "desktop-node"
    if path.startswith("apps/desktop/") and ".test." in item.name and item.suffix in {
        ".ts",
        ".tsx",
    }:
        return "desktop"
    if item.name.endswith(".test.mjs"):
        return "node"
    return "reference"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _external_executable(repo: Path, name: str) -> str:
    """Resolve one executable toolchain command outside the live repository."""
    located = shutil.which(name)
    if located is None:
        raise ValueError(f"--base-ref requires an external {name} executable")
    try:
        executable = Path(located).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve external {name} executable") from exc
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"--base-ref requires an executable external {name}")
    if _is_within(executable, repo.resolve()):
        raise ValueError(f"--base-ref refuses {name} executable inside --repo")
    return str(executable)


def _external_node_modules(repo: Path, relative_path: Path) -> Path | None:
    """Accept only a live-worktree link to one external Node dependency root."""
    candidate = repo / relative_path
    label = relative_path.as_posix()
    if not candidate.is_symlink():
        if candidate.exists():
            raise ValueError(
                f"--base-ref {label} must be an external symlink"
            )
        return None
    try:
        external = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            f"--base-ref {label} must resolve to an external directory"
        ) from exc
    if not external.is_dir() or _is_within(external, repo.resolve()):
        raise ValueError(
            f"--base-ref {label} must resolve to an external directory"
        )
    return external


_FileIdentity = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class _NodeDependencyLink:
    identity: _FileIdentity
    resolved: Path
    external_root_index: int | None
    external_relative: Path | None
    project_relative: Path | None
    target_is_directory: bool


@dataclass
class _NodeDependencyRoot:
    relative_path: Path
    external: Path
    destination: Path
    project_root: Path | None
    identity: _FileIdentity
    entries: dict[Path, _FileIdentity] = field(default_factory=dict)
    links: dict[Path, _NodeDependencyLink] = field(default_factory=dict)
    materialized_directories: set[Path] = field(default_factory=lambda: {Path()})
    provisioned_links: list[Path] = field(default_factory=list)
    view_identity: _FileIdentity | None = None
    view_entries: dict[Path, _FileIdentity] = field(default_factory=dict)


def _file_identity(path: Path) -> _FileIdentity:
    details = path.lstat()
    return (
        details.st_dev,
        details.st_ino,
        stat.S_IFMT(details.st_mode),
        details.st_size,
        details.st_mtime_ns,
    )


def _dependency_project_root(external: Path, relative_path: Path) -> Path | None:
    """Infer an alternate checkout root only from an exact dependency suffix."""
    parts = relative_path.parts
    if not parts or tuple(external.parts[-len(parts) :]) != parts:
        return None
    project_root = external
    for _part in parts:
        project_root = project_root.parent
    return project_root


def _committed_project_target(
    sealed_repo: Path,
    relative_path: Path,
    label: str,
) -> Path:
    if (
        not relative_path.parts
        or ".git" in relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ValueError(
            f"--base-ref {label} project symlink has no committed target"
        )
    target = sealed_repo / relative_path
    if target.is_symlink() or not target.exists():
        raise ValueError(
            f"--base-ref {label} project symlink target is not committed"
        )
    inspected = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{relative_path.as_posix()}"],
        cwd=sealed_repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if inspected.returncode:
        raise ValueError(
            f"--base-ref {label} project symlink target is not committed"
        )
    return target


def _dependency_link_target(
    path: Path,
    identity: _FileIdentity,
    roots: list[_NodeDependencyRoot],
    sealed_repo: Path,
    source_repo: Path,
    label: str,
) -> _NodeDependencyLink:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"--base-ref {label} contains a broken symlink") from exc
    try:
        unchanged = _file_identity(path) == identity
    except OSError as exc:
        raise ValueError(f"--base-ref {label} symlink changed during audit") from exc
    if not unchanged:
        raise ValueError(f"--base-ref {label} symlink changed during audit")

    external_matches = [
        (len(root.external.parts), index, root)
        for index, root in enumerate(roots)
        if _is_within(resolved, root.external)
    ]
    if external_matches:
        _depth, index, root = max(external_matches, key=lambda item: item[0])
        return _NodeDependencyLink(
            identity=identity,
            resolved=resolved,
            external_root_index=index,
            external_relative=resolved.relative_to(root.external),
            project_relative=None,
            target_is_directory=resolved.is_dir(),
        )

    project_roots = {source_repo.resolve()}
    project_roots.update(
        root.project_root for root in roots if root.project_root is not None
    )
    project_matches = [
        project_root
        for project_root in project_roots
        if _is_within(resolved, project_root)
    ]
    if project_matches:
        project_root = max(project_matches, key=lambda item: len(item.parts))
        relative = resolved.relative_to(project_root)
        target = _committed_project_target(sealed_repo, relative, label)
        return _NodeDependencyLink(
            identity=identity,
            resolved=resolved,
            external_root_index=None,
            external_relative=None,
            project_relative=relative,
            target_is_directory=target.is_dir(),
        )
    raise ValueError(
        f"--base-ref {label} symlink escapes validated node_modules roots"
    )


def _materialize_dependency_ancestors(root: _NodeDependencyRoot, path: Path) -> None:
    parent = path.parent
    root.materialized_directories.add(Path())
    while parent.parts:
        root.materialized_directories.add(parent)
        parent = parent.parent


def _snapshot_dependency_entries(
    root: _NodeDependencyRoot,
    directory_root: Path,
    deadline: float,
    *,
    ignore_cache_entries: bool,
) -> dict[Path, _FileIdentity]:
    """Return a bounded lstat snapshot without traversing dependency symlinks."""
    entries_by_path: dict[Path, _FileIdentity] = {}
    stack = [Path()]
    entry_count = 0
    while stack:
        if time.monotonic() > deadline:
            raise ValueError("--base-ref node_modules revalidation exceeded time bound")
        relative_directory = stack.pop()
        with os.scandir(directory_root / relative_directory) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _NODE_DEPENDENCY_MAX_ENTRIES:
                    raise ValueError(
                        "--base-ref node_modules revalidation exceeded entry bound"
                    )
                if time.monotonic() > deadline:
                    raise ValueError(
                        "--base-ref node_modules revalidation exceeded time bound"
                    )
                relative = relative_directory / entry.name
                identity = _file_identity(directory_root / relative)
                if relative in _NODE_DEPENDENCY_CACHE_PATHS:
                    if not stat.S_ISDIR(identity[2]):
                        raise ValueError(
                            "--base-ref sealed dependency cache path changed before execution"
                        )
                    if ignore_cache_entries:
                        continue
                entries_by_path[relative] = identity
                if (
                    stat.S_ISDIR(identity[2])
                    and relative not in _NODE_DEPENDENCY_CACHE_PATHS
                ):
                    stack.append(relative)
    return entries_by_path


def _audit_node_dependency_roots(
    roots: list[_NodeDependencyRoot],
    sealed_repo: Path,
    source_repo: Path,
) -> None:
    deadline = time.monotonic() + _NODE_DEPENDENCY_AUDIT_SECONDS
    entry_count = 0
    for root in roots:
        stack = [Path()]
        label_root = root.relative_path.as_posix()
        while stack:
            if time.monotonic() > deadline:
                raise ValueError(
                    f"--base-ref {label_root} dependency audit exceeded time bound"
                )
            relative_directory = stack.pop()
            directory = root.external / relative_directory
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        entry_count += 1
                        if entry_count > _NODE_DEPENDENCY_MAX_ENTRIES:
                            raise ValueError(
                                "--base-ref node_modules dependency audit exceeded entry bound"
                            )
                        if time.monotonic() > deadline:
                            raise ValueError(
                                f"--base-ref {label_root} dependency audit exceeded time bound"
                            )
                        relative = relative_directory / entry.name
                        path = root.external / relative
                        try:
                            identity = _file_identity(path)
                        except OSError as exc:
                            raise ValueError(
                                f"--base-ref {label_root} dependency changed during audit"
                            ) from exc
                        root.entries[relative] = identity
                        mode = identity[2]
                        if relative in _NODE_DEPENDENCY_CACHE_PATHS:
                            if not stat.S_ISDIR(mode):
                                raise ValueError(
                                    f"--base-ref {label_root}/{relative.as_posix()} cache path must be a directory"
                                )
                        elif stat.S_ISLNK(mode):
                            label = f"{label_root}/{relative.as_posix()}"
                            root.links[relative] = _dependency_link_target(
                                path,
                                identity,
                                roots,
                                sealed_repo,
                                source_repo,
                                label,
                            )
                            _materialize_dependency_ancestors(root, relative)
                        elif stat.S_ISDIR(mode):
                            stack.append(relative)
                        elif not stat.S_ISREG(mode):
                            raise ValueError(
                                f"--base-ref {label_root} contains a non-file dependency entry"
                            )
            except OSError as exc:
                raise ValueError(
                    f"--base-ref cannot audit {label_root} dependency directory"
                ) from exc


def _dependency_view_target(
    link: _NodeDependencyLink,
    roots: list[_NodeDependencyRoot],
    sealed_repo: Path,
) -> Path:
    if link.project_relative is not None:
        return sealed_repo / link.project_relative
    if link.external_root_index is None or link.external_relative is None:
        raise AssertionError("audited dependency link has no target")
    return roots[link.external_root_index].destination / link.external_relative


def _build_node_dependency_directory(
    root: _NodeDependencyRoot,
    relative_directory: Path,
    roots: list[_NodeDependencyRoot],
    sealed_repo: Path,
    deadline: float,
) -> None:
    source = root.external / relative_directory
    destination = root.destination / relative_directory
    destination.mkdir()
    try:
        with os.scandir(source) as entries:
            for entry in entries:
                if time.monotonic() > deadline:
                    raise ValueError(
                        "--base-ref node_modules dependency view exceeded time bound"
                    )
                relative = relative_directory / entry.name
                source_path = root.external / relative
                destination_path = root.destination / relative
                identity = root.entries[relative]
                mode = identity[2]
                if relative in _NODE_DEPENDENCY_CACHE_PATHS:
                    destination_path.mkdir()
                elif stat.S_ISLNK(mode):
                    link = root.links[relative]
                    destination_path.symlink_to(
                        _dependency_view_target(link, roots, sealed_repo),
                        target_is_directory=link.target_is_directory,
                    )
                    root.provisioned_links.append(destination_path)
                elif stat.S_ISDIR(mode) and relative in root.materialized_directories:
                    _build_node_dependency_directory(
                        root,
                        relative,
                        roots,
                        sealed_repo,
                        deadline,
                    )
                else:
                    destination_path.symlink_to(
                        source_path,
                        target_is_directory=stat.S_ISDIR(mode),
                    )
                    root.provisioned_links.append(destination_path)
    except OSError as exc:
        raise ValueError(
            f"--base-ref cannot construct {root.relative_path.as_posix()} dependency view"
        ) from exc


def _revalidate_node_dependency_roots(
    roots: list[_NodeDependencyRoot],
    sealed_repo: Path,
) -> None:
    deadline = time.monotonic() + _NODE_DEPENDENCY_AUDIT_SECONDS
    allowed_external_roots = [root.external for root in roots]
    sealed_root = sealed_repo.resolve()
    for root in roots:
        label_root = root.relative_path.as_posix()
        try:
            if _file_identity(root.external) != root.identity:
                raise ValueError(
                    f"--base-ref external {label_root} changed before execution"
                )
            if set(
                _snapshot_dependency_entries(
                    root,
                    root.external,
                    deadline,
                    ignore_cache_entries=False,
                )
            ) != set(root.entries):
                raise ValueError(
                    f"--base-ref {label_root} dependency entries changed before execution"
                )
            for relative, identity in root.entries.items():
                if time.monotonic() > deadline:
                    raise ValueError(
                        "--base-ref node_modules revalidation exceeded time bound"
                    )
                path = root.external / relative
                if _file_identity(path) != identity:
                    raise ValueError(
                        f"--base-ref {label_root}/{relative.as_posix()} changed before execution"
                    )
            for relative, link in root.links.items():
                if time.monotonic() > deadline:
                    raise ValueError(
                        "--base-ref node_modules revalidation exceeded time bound"
                    )
                if (root.external / relative).resolve(strict=True) != link.resolved:
                    raise ValueError(
                        f"--base-ref {label_root} symlink changed before execution"
                    )
            if (
                root.view_identity is None
                or _file_identity(root.destination) != root.view_identity
                or _snapshot_dependency_entries(
                    root,
                    root.destination,
                    deadline,
                    ignore_cache_entries=True,
                )
                != root.view_entries
            ):
                raise ValueError(
                    f"--base-ref sealed {label_root} dependency view changed before execution"
                )
            for provisioned in root.provisioned_links:
                if time.monotonic() > deadline:
                    raise ValueError(
                        "--base-ref node_modules revalidation exceeded time bound"
                    )
                resolved = provisioned.resolve(strict=True)
                if not _is_within(resolved, sealed_root) and not any(
                    _is_within(resolved, external) for external in allowed_external_roots
                ):
                    raise ValueError(
                        f"--base-ref sealed {label_root} dependency view escaped"
                    )
        except OSError as exc:
            raise ValueError(
                f"--base-ref external {label_root} disappeared before execution"
            ) from exc


def _provision_external_node_modules(
    sealed_repo: Path,
    source_repo: Path,
    dependencies: list[tuple[Path, Path]],
) -> list[_NodeDependencyRoot]:
    """Build bounded dependency overlays with project links remapped to the seal."""
    roots: list[_NodeDependencyRoot] = []
    for relative_path, external in dependencies:
        destination = sealed_repo / relative_path
        label = relative_path.as_posix()
        if destination.exists() or destination.is_symlink():
            raise ValueError(
                f"--base-ref sealed tree already contains {label} authority"
            )
        try:
            identity = _file_identity(external)
        except OSError as exc:
            raise ValueError(f"external {label} disappeared before execution") from exc
        if not stat.S_ISDIR(identity[2]):
            raise ValueError(f"external {label} disappeared before execution")
        roots.append(
            _NodeDependencyRoot(
                relative_path=relative_path,
                external=external,
                destination=destination,
                project_root=_dependency_project_root(external, relative_path),
                identity=identity,
            )
        )
    _audit_node_dependency_roots(roots, sealed_repo, source_repo)
    deadline = time.monotonic() + _NODE_DEPENDENCY_AUDIT_SECONDS
    for root in roots:
        root.destination.parent.mkdir(parents=True, exist_ok=True)
        _build_node_dependency_directory(
            root,
            Path(),
            roots,
            sealed_repo,
            deadline,
        )
        try:
            root.view_identity = _file_identity(root.destination)
            root.view_entries = _snapshot_dependency_entries(
                root,
                root.destination,
                deadline,
                ignore_cache_entries=True,
            )
        except OSError as exc:
            raise ValueError(
                f"--base-ref cannot snapshot {root.relative_path.as_posix()} dependency view"
            ) from exc
    _revalidate_node_dependency_roots(roots, sealed_repo)
    return roots


def _configure_sealed_execution_environment(
    env: dict[str, str],
    repo: Path,
    *,
    node_path: str | None,
    cwd: Path,
) -> None:
    """Remove live-worktree import authority from a sealed child process."""
    repo_root = repo.resolve()
    preserved: list[str] = []
    for raw_path in env.get("PATH", "").split(os.pathsep):
        if not raw_path:
            continue
        try:
            resolved = Path(raw_path).resolve()
        except OSError:
            continue
        if not _is_within(resolved, repo_root):
            preserved.append(raw_path)
    if node_path is not None:
        preserved.insert(0, str(Path(node_path).parent))
    env["PATH"] = os.pathsep.join(preserved)
    # These variables can prepend live source or dependency paths ahead of
    # the detached checkout.  The sealed tree is self-contained except for
    # the one validated external dependency root we deliberately mount.
    env.pop("NODE_PATH", None)
    env.pop("PYTHONPATH", None)
    env.pop("INIT_CWD", None)
    env["PWD"] = str(cwd)


def _command(
    repo: Path,
    path: str,
    kind: str,
    *,
    node_path: str | None = None,
    npx_path: str | None = None,
) -> tuple[list[str], Path]:
    if kind == "python":
        # Keep the absolute virtualenv entry point.  Resolving the symlink to
        # uv's base interpreter would silently discard the venv/site-packages.
        command = [str(Path(sys.executable).absolute()), "-m", "pytest", "-q", path]
        if path == "tests/plugins/workflow/test_installed_distribution_e2e.py":
            command.extend(["-m", "integration"])
        return command, repo
    if kind == "desktop":
        relative = Path(path).relative_to("apps/desktop").as_posix()
        return [npx_path or "npx", "vitest", "run", relative], repo / "apps/desktop"
    if kind == "desktop-node":
        relative = Path(path).relative_to("apps/desktop").as_posix()
        return [npx_path or "npx", "tsx", "--test", relative], repo / "apps/desktop"
    if kind == "node":
        return [node_path or "node", "--test", path], repo
    raise AssertionError(f"unsupported executable invariant kind: {kind}")


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.lock = threading.Lock()

    def consume(self, stream: Any) -> None:
        try:
            while chunk := stream.read(65_536):
                with self.lock:
                    remaining = self.limit - len(self.data)
                    if remaining > 0:
                        self.data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.truncated = True
        finally:
            stream.close()

    def text(self) -> str:
        rendered = bytes(self.data).decode("utf-8", errors="replace")
        if self.truncated:
            rendered += "\n...[output truncated]"
        return rendered


def _snapshot_process_group(process_group: int) -> dict[int, float]:
    members: dict[int, float] = {}
    if os.name != "posix":
        return members
    for candidate in psutil.process_iter(["pid", "create_time"]):
        try:
            if os.getpgid(candidate.pid) == process_group:
                members[candidate.pid] = float(candidate.info["create_time"])
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
    return members


def _known_group_member_alive(
    process_group: int,
    known_members: dict[int, float],
) -> bool:
    for pid, created_at in known_members.items():
        try:
            candidate = psutil.Process(pid)
            if (
                candidate.create_time() == created_at
                and (os.name != "posix" or os.getpgid(pid) == process_group)
            ):
                return True
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
    return False


def _refresh_known_group_members(
    process_group: int,
    known_members: dict[int, float],
) -> None:
    if _known_group_member_alive(process_group, known_members):
        known_members.update(_snapshot_process_group(process_group))
    _refresh_known_descendants(known_members)


def _refresh_known_descendants(known_members: dict[int, float]) -> None:
    """Retain descendants of exact process identities, even after they call setsid."""
    for pid, created_at in list(known_members.items()):
        try:
            parent = psutil.Process(pid)
            if parent.create_time() != created_at:
                continue
            descendants = parent.children(recursive=True)
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
        for descendant in descendants:
            try:
                known_members[descendant.pid] = descendant.create_time()
            except (ProcessLookupError, PermissionError, psutil.Error):
                continue


def _known_process_identity_running(
    known_members: dict[int, float],
) -> bool:
    for pid, created_at in known_members.items():
        try:
            candidate = psutil.Process(pid)
            if (
                candidate.create_time() == created_at
                and candidate.status() != psutil.STATUS_ZOMBIE
            ):
                return True
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
    return False


def _signal_verified_escaped_descendants(
    process_group: int,
    known_members: dict[int, float],
    sent_signal: int,
) -> None:
    """Signal tracked descendants only when their PID/create-time identity survives."""
    for pid, created_at in known_members.items():
        if pid == process_group:
            continue
        try:
            candidate = psutil.Process(pid)
            if candidate.create_time() != created_at:
                continue
            if os.getpgid(pid) == process_group:
                continue
            os.kill(pid, sent_signal)
        except (ProcessLookupError, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (PermissionError, psutil.Error):
            raise


def _require_owned_process_group(
    process_group: int,
    known_members: dict[int, float],
    *,
    leader_identity_reserved: bool = False,
) -> bool:
    if leader_identity_reserved:
        known_members.update(_snapshot_process_group(process_group))
        return True
    if _known_group_member_alive(process_group, known_members):
        return True
    leader_created_at = known_members.get(process_group)
    if leader_created_at is not None:
        try:
            leader = psutil.Process(process_group)
            if leader.create_time() == leader_created_at:
                # An unreaped original leader still reserves both its PID and
                # the equal PGID even when Darwin no longer answers getpgid()
                # for the zombie.  Snapshot descendants while that kernel
                # identity proves the numeric group cannot have been reused.
                known_members.update(_snapshot_process_group(process_group))
                return True
        except (ProcessLookupError, PermissionError, psutil.Error):
            pass
    if not _snapshot_process_group(process_group):
        return False
    raise RuntimeError(
        f"process group {process_group} exists without an owned process identity"
    )


def _process_finished_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    if process.returncode is not None:
        return True
    if os.name == "posix" and all(
        hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        try:
            return os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            ) is not None
        except ChildProcessError:
            return process.poll() is not None
    return process.poll() is not None


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    process_group: int,
    known_members: dict[int, float],
    windows_job: _WindowsJobContainment | None = None,
    leader_identity_reserved: bool = False,
) -> None:
    if os.name != "posix":
        if windows_job is None:
            raise RuntimeError("Windows cleanup requires Job Object containment")
        try:
            windows_job.terminate()
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        finally:
            windows_job.close()
        return

    # start_new_session=True gave this attempt a process group whose PGID is
    # the leader PID.  Tear down that owned group immediately at every leader
    # completion.  Descendant discovery is not a safe prerequisite here: a
    # short-lived intermediate can exit before psutil observes its resistant
    # grandchild, while the kernel still retains the original group identity.
    group_owned = _require_owned_process_group(
        process_group,
        known_members,
        leader_identity_reserved=leader_identity_reserved,
    )
    if group_owned:
        known_members.update(_snapshot_process_group(process_group))
    _refresh_known_descendants(known_members)
    leader_identity_reserved = leader_identity_reserved or getattr(
        process, "returncode", None
    ) is None
    if group_owned:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            group_owned = False
        except PermissionError:
            # Darwin may report EPERM rather than ESRCH for an already-empty
            # process group.  Confirm emptiness by identity before treating that as
            # successful cleanup; a populated inaccessible group remains an error.
            if _snapshot_process_group(process_group):
                raise
            group_owned = False
    _signal_verified_escaped_descendants(
        process_group, known_members, signal.SIGTERM
    )

    deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        _refresh_known_descendants(known_members)
        if not _known_process_identity_running(known_members):
            break
        time.sleep(_POLL_SECONDS)
    else:
        _refresh_known_descendants(known_members)
        if group_owned:
            try:
                group_owned = _require_owned_process_group(
                    process_group,
                    known_members,
                    leader_identity_reserved=leader_identity_reserved,
                )
            except Exception:
                _signal_verified_escaped_descendants(
                    process_group, known_members, signal.SIGKILL
                )
                raise
        if group_owned:
            known_members.update(_snapshot_process_group(process_group))
            _refresh_known_descendants(known_members)
        if group_owned:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if _snapshot_process_group(process_group):
                    raise
        _signal_verified_escaped_descendants(
            process_group, known_members, signal.SIGKILL
        )
    process.wait()


def _execute_attempt(
    repo: Path,
    path: str,
    kind: str,
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
    cancel_event: threading.Event,
    node_path: str | None = None,
    npx_path: str | None = None,
    source_repo: Path | None = None,
) -> dict[str, Any]:
    command, cwd = _command(
        repo,
        path,
        kind,
        node_path=node_path,
        npx_path=npx_path,
    )
    env = os.environ.copy()
    for inherited in (
        "HERMES_PYTHON",
        "PYTEST_ADDOPTS",
        "PYTHON_BIN",
        "WORKFLOW_MERGE_GATE_FAST",
    ):
        env.pop(inherited, None)
    env.update(
        {
            "HERMES_OFFLINE": "1",
            "NOUS_API_KEY": "",
            "OPENAI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "PYTHONUTF8": "1",
            "WORKFLOW_LEDGER_EXECUTION_ACTIVE": "1",
        }
    )
    if source_repo is not None:
        _configure_sealed_execution_environment(
            env,
            source_repo,
            node_path=node_path,
            cwd=cwd,
        )
    started = time.monotonic_ns()
    windows_job: _WindowsJobContainment | None = None
    status_read_fd: int | None = None
    status_write_fd: int | None = None
    if os.name != "posix":
        try:
            windows_job = _WindowsJobContainment()
        except OSError as exc:
            duration_ms = (time.monotonic_ns() - started) // 1_000_000
            return {
                "result": "infrastructure_error",
                "duration_ms": duration_ms,
                "output_truncated": False,
                "_stdout": "",
                "_stderr": str(exc),
            }
        command = [
            str(Path(sys.executable).absolute()),
            "-c",
            _WINDOWS_JOB_BOOTSTRAP,
            *command,
        ]
    else:
        status_read_fd, status_write_fd = os.pipe()
        os.set_blocking(status_read_fd, False)
        command = [
            str(Path(sys.executable).absolute()),
            "-c",
            _POSIX_GROUP_BOOTSTRAP,
            str(status_write_fd),
            *command,
        ]
    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_options["start_new_session"] = True
        popen_options["pass_fds"] = (status_write_fd,)
    else:
        popen_options["stdin"] = subprocess.PIPE
    try:
        process = subprocess.Popen(command, **popen_options)
    except OSError as exc:
        if status_read_fd is not None:
            os.close(status_read_fd)
        if status_write_fd is not None:
            os.close(status_write_fd)
        if windows_job is not None:
            windows_job.close()
        duration_ms = (time.monotonic_ns() - started) // 1_000_000
        return {
            "result": "infrastructure_error",
            "duration_ms": duration_ms,
            "output_truncated": False,
            "_stdout": "",
            "_stderr": str(exc),
        }
    if status_write_fd is not None:
        os.close(status_write_fd)
    if windows_job is not None:
        try:
            windows_job.assign(int(process._handle))  # type: ignore[attr-defined]
            assert process.stdin is not None
            process.stdin.write(b"1")
            process.stdin.close()
        except (OSError, BrokenPipeError) as exc:
            process.kill()
            process.wait()
            windows_job.close()
            duration_ms = (time.monotonic_ns() - started) // 1_000_000
            return {
                "result": "infrastructure_error",
                "duration_ms": duration_ms,
                "output_truncated": False,
                "_stdout": "",
                "_stderr": str(exc),
            }
    process_group = process.pid
    try:
        known_group_members = {process.pid: psutil.Process(process.pid).create_time()}
    except (ProcessLookupError, psutil.Error):
        known_group_members = {}
    stdout = _BoundedCapture(output_limit_bytes)
    stderr = _BoundedCapture(output_limit_bytes)
    readers = [
        threading.Thread(target=stdout.consume, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.consume, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    cleanup_error: Exception | None = None
    leader_identity_reserved = False
    target_returncode: int | None = None
    status_bytes = bytearray()
    try:
        while True:
            _refresh_known_group_members(process_group, known_group_members)
            if status_read_fd is not None:
                try:
                    status_bytes.extend(os.read(status_read_fd, 64))
                except BlockingIOError:
                    pass
                if b"\n" in status_bytes:
                    target_returncode = int(status_bytes.split(b"\n", 1)[0])
                    break
            if _process_finished_without_reaping(process):
                leader_identity_reserved = (
                    os.name == "posix"
                    and hasattr(os, "WNOWAIT")
                    and process.returncode is None
                )
                break
            if cancel_event.is_set():
                raise CancelledError()
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_POLL_SECONDS)
        try:
            _terminate_process_group(
                process,
                process_group,
                known_group_members,
                windows_job,
                leader_identity_reserved,
            )
        except Exception as exc:  # cleanup failures are evidence, never silent leaks
            cleanup_error = exc
            if process.poll() is None:
                process.kill()
                process.wait()
    except BaseException:
        _terminate_process_group(
            process,
            process_group,
            known_group_members,
            windows_job,
            leader_identity_reserved,
        )
        raise
    finally:
        if status_read_fd is not None:
            os.close(status_read_fd)
        for reader in readers:
            reader.join(timeout=_TERMINATE_GRACE_SECONDS)
    duration_ms = (time.monotonic_ns() - started) // 1_000_000
    returncode = (
        target_returncode if target_returncode is not None else process.returncode
    )
    supervisor_failed = (
        status_read_fd is not None and target_returncode is None and not timed_out
    )
    if cleanup_error is not None or supervisor_failed:
        result = "infrastructure_error"
    elif timed_out:
        result = "timed_out"
    elif returncode == 0:
        result = "passed"
    elif returncode is not None and returncode < 0:
        result = "signaled"
    elif returncode == 1:
        result = "failed"
    else:
        result = "infrastructure_error"
    record = {
        "result": result,
        "duration_ms": duration_ms,
        "output_truncated": stdout.truncated or stderr.truncated,
        "_stdout": stdout.text(),
        "_stderr": stderr.text(),
    }
    if cleanup_error is not None:
        record["_stderr"] += f"\nprocess-group cleanup failed: {cleanup_error}"
    if result == "signaled" and returncode is not None:
        record["termination_signal"] = -returncode
    return record


def _execute(
    repo: Path,
    path: str,
    kind: str,
    platform: str,
    timeout_seconds: float,
    output_limit_bytes: int,
    cancel_event: threading.Event,
    *,
    node_path: str | None = None,
    npx_path: str | None = None,
    source_repo: Path | None = None,
    before_retry: Callable[[], None] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []

    def execute_attempt() -> dict[str, Any]:
        attempt = _execute_attempt(
            repo,
            path,
            kind,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            cancel_event=cancel_event,
            node_path=node_path,
            npx_path=npx_path,
            source_repo=source_repo,
        )
        attempts.append(attempt)
        if attempt["result"] == "failed" and before_retry is not None:
            try:
                before_retry()
            except ValueError as exc:
                raise _AttemptRevalidationError(
                    path,
                    _nonpassing_attempt_diagnostics(attempts),
                    exc,
                ) from exc
        return attempt

    execute_attempt()
    if attempts[0]["result"] == "failed":
        if cancel_event.is_set():
            raise CancelledError()
        execute_attempt()
    digest = hashlib.sha256(path.encode()).hexdigest()
    result = "passed" if attempts[-1]["result"] == "passed" else "failed"
    return {
        "kind": "executed",
        "name": f"ledger invariant {digest}",
        "path": path,
        "result": result,
        "duration_ms": sum(int(attempt["duration_ms"]) for attempt in attempts),
        "platform": platform,
        "attempts": [
            {
                "attempt": index,
                "result": attempt["result"],
                "duration_ms": attempt["duration_ms"],
                "output_truncated": attempt["output_truncated"],
                **(
                    {"termination_signal": attempt["termination_signal"]}
                    if "termination_signal" in attempt
                    else {}
                ),
            }
            for index, attempt in enumerate(attempts, start=1)
        ],
        "flaky_on_first_attempt": (
            len(attempts) == 2
            and attempts[0]["result"] == "failed"
            and result == "passed"
        ),
        "_nonpassing_attempt_diagnostics": _nonpassing_attempt_diagnostics(
            attempts
        ),
    }


def _nonpassing_attempt_diagnostics(
    attempts: list[dict[str, Any]],
) -> tuple[_AttemptDiagnostic, ...]:
    return tuple(
        _AttemptDiagnostic(
            attempt=index,
            result=str(attempt["result"]),
            stdout=str(attempt["_stdout"]),
            stderr=str(attempt["_stderr"]),
        )
        for index, attempt in enumerate(attempts, start=1)
        if attempt["result"] != "passed"
    )


def _emit_nonpassing_attempt_diagnostics(
    path: str,
    diagnostics: tuple[_AttemptDiagnostic, ...],
) -> None:
    for diagnostic in diagnostics:
        print(
            f"ledger invariant nonpassing attempt: {path} "
            f"(attempt {diagnostic.attempt}: {diagnostic.result})",
            file=sys.stderr,
        )
        if diagnostic.stdout:
            print(diagnostic.stdout, file=sys.stderr)
        if diagnostic.stderr:
            print(diagnostic.stderr, file=sys.stderr)


def _run_group(
    repo: Path,
    paths: list[str],
    kind: str,
    platform: str,
    workers: int,
    timeout_seconds: float,
    output_limit_bytes: int,
    *,
    node_path: str | None = None,
    npx_path: str | None = None,
    source_repo: Path | None = None,
    before_retry: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not paths:
        return results
    cancel_event = threading.Event()
    before_retry_lock = threading.Lock()

    def revalidate_before_retry() -> None:
        if before_retry is not None:
            with before_retry_lock:
                before_retry()

    executor = ThreadPoolExecutor(max_workers=min(workers, len(paths)))
    futures: dict[Any, str] = {}
    try:
        futures = {
            executor.submit(
                _execute,
                repo,
                path,
                kind,
                platform,
                timeout_seconds,
                output_limit_bytes,
                cancel_event,
                node_path=node_path,
                npx_path=npx_path,
                source_repo=source_repo,
                before_retry=revalidate_before_retry,
            ): path
            for path in paths
        }
        for future in as_completed(futures):
            results.append(future.result())
    except _AttemptRevalidationError as primary_error:
        cancel_event.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        results_by_path = {str(item["path"]): item for item in results}
        errors_by_path = {primary_error.path: primary_error}
        for future, path in futures.items():
            if future.cancelled():
                continue
            try:
                result = future.result()
            except _AttemptRevalidationError as exc:
                errors_by_path[exc.path] = exc
            except CancelledError:
                continue
            else:
                results_by_path[path] = result
        diagnostic_sets = {
            path: _InvariantDiagnostics(
                path=path,
                diagnostics=item.get("_nonpassing_attempt_diagnostics", ()),
            )
            for path, item in results_by_path.items()
            if item.get("_nonpassing_attempt_diagnostics")
        }
        for path, error in errors_by_path.items():
            diagnostic_sets[path] = _InvariantDiagnostics(
                path=path,
                diagnostics=error.diagnostics,
            )
        raise _GroupAttemptRevalidationError(
            tuple(
                diagnostic_sets[path]
                for path in sorted(diagnostic_sets)
            ),
            tuple(errors_by_path.values()),
        ) from primary_error
    except BaseException:
        cancel_event.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return results


def _manifest_relative_to_repo(repo: Path, manifest: Path) -> Path:
    """Return a lexical repository-relative manifest path without resolving links."""
    candidate = manifest if manifest.is_absolute() else Path.cwd() / manifest
    candidate = Path(os.path.abspath(candidate))
    repo = Path(os.path.abspath(repo))
    try:
        relative = candidate.relative_to(repo)
    except ValueError as exc:
        raise ValueError("--base-ref manifest must be inside --repo") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("--base-ref manifest must be a normalized repository file")
    return relative


def _tracked_symlinks_at(repo: Path, revision: str) -> list[str]:
    """Find symlink entries without traversing any checkout path."""
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "-z", revision],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise ValueError("cannot inspect tracked tree for --base-ref")
    paths: list[str] = []
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        header, separator, path = entry.partition(b"\t")
        if separator != b"\t":
            raise ValueError("cannot inspect tracked tree for --base-ref")
        if header.split(maxsplit=1)[0] == b"120000":
            paths.append(path.decode("utf-8", errors="surrogateescape"))
    return paths


@contextmanager
def _sealed_execution_tree(
    repo: Path,
    base_revision: str,
    manifest: Path,
):
    """Yield a detached committed tree and unregister it on every exit path."""
    manifest_relative = _manifest_relative_to_repo(repo, manifest)
    symlinks = _tracked_symlinks_at(repo, base_revision)
    if symlinks:
        raise ValueError(
            "--base-ref refuses tracked symlink authority: " + ", ".join(symlinks[:8])
        )
    temporary = tempfile.TemporaryDirectory(prefix="workflow-ledger-")
    worktree = Path(temporary.name) / "base"
    added = False
    cleanup_error: str | None = None
    try:
        added_process = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), base_revision],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if added_process.returncode:
            raise ValueError(
                "cannot create sealed ledger worktree: "
                + (added_process.stderr.strip() or "git worktree add failed")
            )
        added = True
        sealed_manifest = worktree / manifest_relative
        if sealed_manifest.is_symlink() or not sealed_manifest.is_file():
            raise ValueError(
                "--base-ref manifest must be a committed regular file at the tested base"
            )
        yield worktree, sealed_manifest
    finally:
        if added:
            removed_process = subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            if removed_process.returncode:
                cleanup_error = (
                    removed_process.stderr.strip() or "cannot unregister sealed ledger worktree"
                )
        try:
            temporary.cleanup()
        except OSError as exc:
            cleanup_error = cleanup_error or str(exc)
        if cleanup_error:
            raise RuntimeError(f"sealed ledger worktree cleanup failed: {cleanup_error}")


def _execute_manifest_invariants(
    repo: Path,
    manifest_path: Path,
    *,
    platform: str,
    timeout_seconds: float,
    output_limit_bytes: int,
    source_repo: Path | None = None,
) -> list[dict[str, Any]]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    paths = sorted(
        {
            path
            for entry in manifest["upstream_changes"]
            for path in entry["tests"]
        }
    )
    by_kind = {
        kind: [path for path in paths if _kind(path) == kind]
        for kind in ("python", "desktop", "desktop-node", "node")
    }
    node_path: str | None = None
    npx_path: str | None = None
    dependency_roots: list[_NodeDependencyRoot] = []
    needs_node = any(by_kind[kind] for kind in ("desktop", "desktop-node", "node"))
    needs_desktop_toolchain = any(
        by_kind[kind] for kind in ("desktop", "desktop-node")
    )
    if source_repo is not None and needs_node:
        node_path = _external_executable(source_repo, "node")
    if source_repo is not None and needs_desktop_toolchain:
        npx_path = _external_executable(source_repo, "npx")
        external_root_node_modules = _external_node_modules(
            source_repo,
            Path("node_modules"),
        )
        if external_root_node_modules is None:
            raise ValueError(
                "--base-ref desktop invariants require an external root node_modules symlink"
            )
        external_desktop_node_modules = _external_node_modules(
            source_repo,
            Path("apps/desktop/node_modules"),
        )
        if external_desktop_node_modules is None:
            raise ValueError(
                "--base-ref desktop invariants require an external desktop node_modules symlink"
            )
        dependency_roots = _provision_external_node_modules(
            repo,
            source_repo,
            [
                (Path("node_modules"), external_root_node_modules),
                (
                    Path("apps/desktop/node_modules"),
                    external_desktop_node_modules,
                ),
            ],
        )
    results: list[dict[str, Any]] = []
    group_options = (timeout_seconds, output_limit_bytes)
    def run_group_and_revalidate(kind: str) -> list[dict[str, Any]]:
        group_raised = False
        try:
            return _run_group(
                repo,
                by_kind[kind],
                kind,
                platform,
                2,
                *group_options,
                node_path=node_path,
                npx_path=npx_path,
                source_repo=source_repo,
                before_retry=(
                    lambda: _revalidate_node_dependency_roots(
                        dependency_roots,
                        repo,
                    )
                    if dependency_roots
                    else None
                ),
            )
        except BaseException:
            group_raised = True
            raise
        finally:
            if dependency_roots and group_raised:
                try:
                    _revalidate_node_dependency_roots(dependency_roots, repo)
                except BaseException:
                    # A process/control-flow failure is the primary outcome.
                    # Do not replace it with a later audit error from cleanup.
                    pass
            elif dependency_roots:
                _revalidate_node_dependency_roots(dependency_roots, repo)

    for kind in ("python", "desktop", "desktop-node", "node"):
        results.extend(run_group_and_revalidate(kind))
    executed = {item["path"] for item in results}
    for path in paths:
        if path in executed:
            continue
        digest = hashlib.sha256(path.encode()).hexdigest()
        results.append(
            {
                "kind": "reference",
                "name": f"ledger reference {digest}",
                "path": path,
                "reason": "non-executable invariant reference",
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", "--report-path", dest="output", type=Path, required=True)
    parser.add_argument("--platform", default=sys.platform)
    parser.add_argument("--base-ref")
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--output-limit-bytes", type=int, default=_DEFAULT_OUTPUT_LIMIT_BYTES
    )
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= _DEFAULT_TIMEOUT_SECONDS:
        parser.error(
            f"--timeout-seconds must be in (0, {_DEFAULT_TIMEOUT_SECONDS:g}]"
        )
    if not 1 <= args.output_limit_bytes <= _DEFAULT_OUTPUT_LIMIT_BYTES:
        parser.error(
            "--output-limit-bytes must be in "
            f"[1, {_DEFAULT_OUTPUT_LIMIT_BYTES}]"
        )

    if args.repo is None:
        resolved_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
        if resolved_root.returncode:
            parser.error("--repo is required outside a Git worktree")
        repo = Path(os.path.abspath(resolved_root.stdout.strip()))
    else:
        repo = Path(os.path.abspath(args.repo))
    base_revision: str | None = None
    if args.base_ref is not None:
        base = subprocess.run(
            ["git", "rev-parse", "--verify", f"{args.base_ref}^{{commit}}"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if base.returncode or head.returncode:
            parser.error("--base-ref must resolve to a local commit")
        if base.stdout.strip() != head.stdout.strip():
            parser.error("--base-ref must resolve to the tested checkout HEAD")
        base_revision = base.stdout.strip()
        tracked_status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if tracked_status.returncode:
            parser.error("cannot verify tracked changes for --base-ref")
        if tracked_status.stdout.strip():
            parser.error(
                "--base-ref requires a checkout with no tracked changes"
            )
    manifest_path = args.manifest or (
        repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    )
    try:
        if base_revision is None:
            results = _execute_manifest_invariants(
                repo,
                manifest_path,
                platform=args.platform,
                timeout_seconds=args.timeout_seconds,
                output_limit_bytes=args.output_limit_bytes,
            )
        else:
            with _sealed_execution_tree(
                repo,
                base_revision,
                manifest_path,
            ) as (sealed_repo, sealed_manifest):
                results = _execute_manifest_invariants(
                    sealed_repo,
                    sealed_manifest,
                    platform=args.platform,
                    timeout_seconds=args.timeout_seconds,
                    output_limit_bytes=args.output_limit_bytes,
                    source_repo=repo,
                )
    except _GroupAttemptRevalidationError as exc:
        for item in exc.diagnostics:
            _emit_nonpassing_attempt_diagnostics(
                item.path,
                tuple(sorted(item.diagnostics, key=lambda attempt: attempt.attempt)),
            )
        parser.error(str(exc))
    except _AttemptRevalidationError as exc:
        _emit_nonpassing_attempt_diagnostics(exc.path, exc.diagnostics)
        parser.error(str(exc))
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    results.sort(key=lambda item: item["path"])
    failed = [item for item in results if item.get("result") == "failed"]
    serializable = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in results
    ]
    args.output.write_text(
        json.dumps(serializable, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for item in results:
        if item.get("result") == "failed":
            print(f"ledger invariant failed: {item['path']}", file=sys.stderr)
        _emit_nonpassing_attempt_diagnostics(
            item["path"], item.get("_nonpassing_attempt_diagnostics", ())
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
