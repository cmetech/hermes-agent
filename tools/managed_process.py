"""Bounded ownership and cleanup for isolated child-process trees.

The owner records a PID identity at spawn time, terminates descendants before
their parent, escalates after bounded grace periods, and always waits on its
direct child.  It is deliberately independent of the terminal process
registry so plugins and other edge capabilities can use the same lifecycle
primitive without acquiring terminal-specific state.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
import platform
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Any, Callable

from hermes_cli._subprocess_compat import windows_hide_flags


logger = logging.getLogger(__name__)
_IS_WINDOWS = platform.system() == "Windows"


@dataclass(frozen=True)
class TerminationPolicy:
    """Finite deadlines for cooperative stop, TERM, KILL, and reaping."""

    cooperative_grace_seconds: float = 0.0
    term_grace_seconds: float = 2.0
    kill_grace_seconds: float = 1.0
    wait_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name in (
            "cooperative_grace_seconds",
            "term_grace_seconds",
            "kill_grace_seconds",
            "wait_timeout_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.kill_grace_seconds <= 0:
            raise ValueError("kill_grace_seconds must be positive")
        if self.wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")


@dataclass(frozen=True)
class ProcessResourceLimits:
    """Finite aggregate limits for one owned process tree."""

    max_rss_bytes: int = 2048 * 1024 * 1024
    max_cpu_seconds: float = 900.0
    max_descendants: int = 32

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_rss_bytes, bool)
            or not isinstance(self.max_rss_bytes, int)
            or self.max_rss_bytes <= 0
        ):
            raise ValueError("max_rss_bytes must be a positive integer")
        if (
            isinstance(self.max_cpu_seconds, bool)
            or not isinstance(self.max_cpu_seconds, int | float)
            or not math.isfinite(float(self.max_cpu_seconds))
            or self.max_cpu_seconds <= 0
        ):
            raise ValueError("max_cpu_seconds must be positive and finite")
        if (
            isinstance(self.max_descendants, bool)
            or not isinstance(self.max_descendants, int)
            or self.max_descendants < 0
        ):
            raise ValueError("max_descendants must be a non-negative integer")


@dataclass(frozen=True)
class ProcessIdentity:
    """Identity stable enough to reject a PID recycled after child exit."""

    pid: int
    start_time: int | None
    group_id: int | None

    @classmethod
    def capture(cls, pid: int) -> "ProcessIdentity":
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        try:
            from gateway.status import get_process_start_time

            start_time = get_process_start_time(pid)
        except Exception:
            start_time = None
        if _IS_WINDOWS:
            group_id = pid
        else:
            try:
                group_id = os.getpgid(pid)
            except (OSError, ProcessLookupError, PermissionError):
                group_id = None
        return cls(pid=pid, start_time=start_time, group_id=group_id)

    def is_current(self) -> bool:
        try:
            from gateway.status import _pid_exists

            if not _pid_exists(self.pid):
                return False
        except Exception:
            return False
        if self.start_time is None:
            return True
        return ProcessIdentity.capture(self.pid).start_time == self.start_time


class ManagedProcessTree:
    """Exclusive owner of one direct child and the tree rooted below it."""

    def __init__(
        self,
        process: subprocess.Popen,
        identity: ProcessIdentity,
        *,
        policy: TerminationPolicy | None = None,
    ) -> None:
        self.process = process
        self.identity = identity
        self.policy = policy or TerminationPolicy()
        self._lock = threading.Lock()
        self._reaped = False
        self._returncode: int | None = None

    @property
    def reaped(self) -> bool:
        return self._reaped

    def resource_violation(self, limits: ProcessResourceLimits) -> str | None:
        """Return a typed aggregate limit violation, failing closed on metrics."""
        try:
            import psutil

            root = psutil.Process(self.identity.pid)
            descendants = root.children(recursive=True)
            if len(descendants) > limits.max_descendants:
                return "descendant_limit"
            rss = 0
            cpu = 0.0
            for process in (root, *descendants):
                try:
                    rss += int(process.memory_info().rss)
                    times = process.cpu_times()
                    cpu += float(times.user) + float(times.system)
                except psutil.NoSuchProcess:
                    continue
            if rss > limits.max_rss_bytes:
                return "rss_limit"
            if cpu > limits.max_cpu_seconds:
                return "cpu_limit"
            return None
        except ImportError:
            return "resource_metrics_unavailable"
        except Exception as exc:
            try:
                import psutil

                if isinstance(exc, psutil.NoSuchProcess):
                    return None
            except ImportError:
                pass
            return "resource_metrics_unavailable"

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        policy: TerminationPolicy | None = None,
        **popen_kwargs: Any,
    ) -> "ManagedProcessTree":
        if isinstance(argv, (str, bytes)):
            raise TypeError("argv must be a sequence of strings, not a command string")
        if not isinstance(argv, Sequence):
            raise TypeError("argv must be a sequence of strings")
        if not argv:
            raise ValueError("argv must not be empty")
        if any(not isinstance(part, str) for part in argv):
            raise TypeError("every argv element must be a string")
        if any(not part for part in argv):
            raise ValueError("argv elements must not be empty")

        kwargs = dict(popen_kwargs)
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.STDOUT)
        if _IS_WINDOWS:
            kwargs.setdefault("creationflags", windows_hide_flags())
        else:
            kwargs.setdefault("start_new_session", True)

        process = subprocess.Popen(list(argv), **kwargs)
        try:
            identity = ProcessIdentity.capture(process.pid)
            return cls(process, identity, policy=policy)
        except BaseException:
            try:
                process.kill()
            finally:
                try:
                    process.wait(timeout=1)
                except Exception:
                    pass
            raise

    def terminate(self, reason: str = "shutdown") -> int | None:
        """Stop the owned tree and reap the direct child; safe to call twice."""
        del reason  # reserved for owner audit/logging without changing semantics
        with self._lock:
            if self._reaped:
                return self._returncode

            if self.process.poll() is not None:
                if _IS_WINDOWS:
                    self.terminate_existing(
                        self.identity,
                        term_grace_seconds=self.policy.term_grace_seconds,
                        kill_grace_seconds=self.policy.kill_grace_seconds,
                    )
                else:
                    if not self._terminate_owned_posix_group():
                        raise RuntimeError(
                            f"owned process group {self.identity.group_id} was not "
                            "cleaned within the bounded termination policy"
                        )
                return self._reap()

            # A PIPE-backed stdin is the coordinator lifeline used by isolated
            # workers. Closing it is the portable cooperative-cancel signal;
            # ordinary registry children use DEVNULL and skip this branch.
            stdin = getattr(self.process, "stdin", None)
            if stdin is not None and not getattr(stdin, "closed", False):
                try:
                    stdin.close()
                except (OSError, ValueError):
                    pass
            if self.policy.cooperative_grace_seconds:
                try:
                    returncode = self.process.wait(
                        timeout=self.policy.cooperative_grace_seconds
                    )
                except subprocess.TimeoutExpired:
                    pass
                else:
                    self._returncode = returncode
                    self._reaped = True
                    return returncode

            terminated = self.terminate_existing(
                self.identity,
                term_grace_seconds=self.policy.term_grace_seconds,
                kill_grace_seconds=self.policy.kill_grace_seconds,
            )
            if not terminated and self.process.poll() is None:
                # A synthetic/non-enumerable child (not an identity mismatch)
                # still has a waitable handle. This is also the final fallback
                # when host process enumeration is unavailable.
                live = ProcessIdentity.capture(self.process.pid)
                if (
                    self.identity.start_time is None
                    or live.start_time == self.identity.start_time
                ):
                    try:
                        self.process.kill()
                    except (OSError, ProcessLookupError, PermissionError):
                        pass

            try:
                self.process.wait(timeout=self.policy.wait_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                except (OSError, ProcessLookupError, PermissionError):
                    pass
                try:
                    self.process.wait(timeout=self.policy.kill_grace_seconds)
                except subprocess.TimeoutExpired:
                    logger.error(
                        "Direct child pid %d could not be reaped within bounded shutdown",
                        self.process.pid,
                    )
            if not _IS_WINDOWS:
                if not self._terminate_owned_posix_group():
                    raise RuntimeError(
                        f"owned process group {self.identity.group_id} was not "
                        "cleaned within the bounded termination policy"
                    )
            result = self._reap()
            if not self._reaped:
                raise RuntimeError(
                    f"owned child pid {self.process.pid} was not reaped within "
                    "the bounded termination policy"
                )
            return result

    def _terminate_owned_posix_group(self) -> bool:
        """Terminate group members that outlived the direct owned child."""
        if _IS_WINDOWS:
            return True
        group_id = self.identity.group_id
        if group_id is None or group_id <= 0 or group_id == os.getpgrp():
            return True

        def group_alive() -> bool:
            try:
                os.killpg(group_id, 0)  # windows-footgun: ok - POSIX-only method
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True

        if not group_alive():
            return True
        try:
            os.killpg(  # windows-footgun: ok - POSIX-only method
                group_id, signal.SIGTERM
            )
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.monotonic() + self.policy.term_grace_seconds
        while group_alive() and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        if group_alive():
            try:
                os.killpg(group_id, signal.SIGKILL)  # windows-footgun: ok
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            deadline = time.monotonic() + self.policy.kill_grace_seconds
            while group_alive() and time.monotonic() < deadline:
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return not group_alive()

    def close(self) -> int | None:
        return self.terminate("owner close")

    def _reap(self) -> int | None:
        try:
            self._returncode = self.process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            self._returncode = self.process.poll()
        self._reaped = self.process.poll() is not None
        return self._returncode

    @staticmethod
    def _proc_alive(proc: Any) -> bool:
        try:
            import psutil

            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False

    @classmethod
    def terminate_existing(
        cls,
        identity: ProcessIdentity,
        *,
        term_grace_seconds: float = 2.0,
        kill_grace_seconds: float = 1.0,
        is_windows: bool | None = None,
        subprocess_run: Callable[..., Any] | None = None,
        os_kill: Callable[[int, int], Any] | None = None,
        creationflags: int | None = None,
        proc_alive: Callable[[Any], bool] | None = None,
    ) -> bool:
        """Terminate a previously recorded identity without owning its handle.

        Optional callables are compatibility seams for current registry tests;
        production callers use the defaults.
        """
        if identity.start_time is not None:
            current = ProcessIdentity.capture(identity.pid)
            if (
                current.start_time is not None
                and current.start_time != identity.start_time
            ):
                logger.warning(
                    "Refusing to terminate host pid %d: start-time mismatch",
                    identity.pid,
                )
                return False
        # Legacy identities without a start-time baseline intentionally retain
        # Hermes' prior best-effort behavior. The OS primitive below decides
        # whether the PID still exists.

        windows = _IS_WINDOWS if is_windows is None else is_windows
        run = subprocess.run if subprocess_run is None else subprocess_run
        kill = os.kill if os_kill is None else os_kill
        if windows:
            try:
                completed = run(
                    ["taskkill", "/PID", str(identity.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=(
                        windows_hide_flags() if creationflags is None else creationflags
                    ),
                    stdin=subprocess.DEVNULL,
                )
                return completed.returncode == 0
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                try:
                    kill(identity.pid, signal.SIGTERM)
                    return True
                except (OSError, ProcessLookupError, PermissionError):
                    return False

        import psutil

        try:
            parent = psutil.Process(identity.pid)
        except psutil.NoSuchProcess:
            return False
        except (OSError, PermissionError):
            try:
                kill(identity.pid, signal.SIGTERM)
                return True
            except (OSError, ProcessLookupError, PermissionError):
                return False

        try:
            targets = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            targets = []
        targets.append(parent)
        sent = False
        for proc in targets:
            try:
                proc.terminate()
                sent = True
            except psutil.NoSuchProcess:
                pass
            except (psutil.AccessDenied, OSError):
                pass

        if term_grace_seconds <= 0:
            return sent
        alive = cls._proc_alive if proc_alive is None else proc_alive
        deadline = time.monotonic() + term_grace_seconds
        while time.monotonic() < deadline:
            if not any(alive(proc) for proc in targets):
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        for proc in targets:
            try:
                if alive(proc):
                    proc.kill()
                    sent = True
            except psutil.NoSuchProcess:
                pass
            except (psutil.AccessDenied, OSError):
                pass

        if kill_grace_seconds > 0:
            deadline = time.monotonic() + kill_grace_seconds
            while time.monotonic() < deadline and any(alive(proc) for proc in targets):
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return sent and not any(alive(proc) for proc in targets)


__all__ = ["ManagedProcessTree", "ProcessIdentity", "TerminationPolicy"]
