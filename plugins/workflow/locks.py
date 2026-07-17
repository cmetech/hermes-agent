"""Bounded reentrant locks for workflow state files."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
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


_process_locks: dict[str, threading.RLock] = {}
_process_locks_guard = threading.Lock()
_local = threading.local()


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _process_locks_guard:
        return _process_locks.setdefault(key, threading.RLock())


@contextmanager
def workflow_lock(path: Path, *, timeout_seconds: float = 5.0):
    """Acquire a bounded in-process and cross-process advisory lock."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    lock = _process_lock(path)
    if not lock.acquire(timeout=timeout_seconds):
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
            deadline = time.monotonic() + timeout_seconds
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
        lock.release()
