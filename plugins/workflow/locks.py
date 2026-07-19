"""Bounded reentrant locks for workflow state files."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:  # pragma: no cover - platform import
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
try:  # pragma: no cover - platform import
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None


class WorkflowLockTimeout(TimeoutError):
    """Raised when a workflow state lock cannot be acquired in time."""


@dataclass
class _ProcessLockEntry:
    lock: threading.RLock = field(default_factory=threading.RLock)
    owners: int = 0
    waiters: int = 0


_process_locks: dict[str, _ProcessLockEntry] = {}
_process_locks_guard = threading.Lock()
_local = threading.local()


def _reserve_process_lock(key: str) -> _ProcessLockEntry:
    with _process_locks_guard:
        entry = _process_locks.setdefault(key, _ProcessLockEntry())
        entry.waiters += 1
        return entry


def _acquire_process_lock(
    key: str, entry: _ProcessLockEntry, *, timeout: float
) -> bool:
    acquired = entry.lock.acquire(timeout=timeout)
    with _process_locks_guard:
        entry.waiters -= 1
        if acquired:
            entry.owners += 1
        elif entry.owners == 0 and entry.waiters == 0:
            if _process_locks.get(key) is entry:
                del _process_locks[key]
    return acquired


def _release_process_lock(key: str, entry: _ProcessLockEntry) -> None:
    entry.lock.release()
    with _process_locks_guard:
        entry.owners -= 1
        if entry.owners == 0 and entry.waiters == 0:
            if _process_locks.get(key) is entry:
                del _process_locks[key]


@contextmanager
def workflow_lock(path: Path, *, timeout_seconds: float = 5.0):
    """Acquire a bounded in-process and cross-process advisory lock."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    entry = _reserve_process_lock(key)
    deadline = time.monotonic() + timeout_seconds
    if not _acquire_process_lock(
        key, entry, timeout=max(0.0, deadline - time.monotonic())
    ):
        raise WorkflowLockTimeout(f"timed out acquiring workflow lock: {path}")
    depths = getattr(_local, "depths", {})
    depth = depths.get(key, 0)
    depths[key] = depth + 1
    _local.depths = depths
    handle = None
    try:
        if depth == 0:
            handle = path.open("a+b")
            if os.name == "nt":
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    elif msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise WorkflowLockTimeout(
                            f"timed out acquiring workflow lock: {path}"
                        )
                    time.sleep(0.01)
        yield
    finally:
        if handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()
        depths[key] -= 1
        if depths[key] == 0:
            del depths[key]
        _release_process_lock(key, entry)
