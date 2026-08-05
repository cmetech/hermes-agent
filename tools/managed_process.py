"""Bounded ownership and cleanup for isolated child-process trees.

The owner records a PID identity at spawn time, terminates descendants before
their parent, escalates after bounded grace periods, and always waits on its
direct child.  It is deliberately independent of the terminal process
registry so plugins and other edge capabilities can use the same lifecycle
primitive without acquiring terminal-specific state.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import logging
import math
import operator
import os
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from typing import Any, Callable

from hermes_cli._subprocess_compat import windows_hide_flags


logger = logging.getLogger(__name__)
# os.name, not platform.system(): the latter spawns `cmd /c ver` on Windows
# and this runs at import time. See the note in hermes_cli/config.py.
_IS_WINDOWS = os.name == "nt"
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_MAX_INHERITED_DESCRIPTORS = 64
_DESCRIPTOR_BOOTSTRAP_ERROR_MAX_BYTES = 4096
_DESCRIPTOR_BOOTSTRAP_ENV_MAX_BYTES = 16 * 1024 * 1024
_POSIX_DESCRIPTOR_BOOTSTRAP = r"""
import ctypes
import errno
import os
import signal
import struct
import sys

status_fd = int(sys.argv[1])
environment_fd = int(sys.argv[2])
signal_states = tuple(
    (int(number), ignored == "1")
    for number, ignored in (
        item.split(":", 1) for item in sys.argv[3].split(",") if item
    )
)
use_argv_zero = sys.argv[4] == "1"
executable = sys.argv[7] if use_argv_zero else sys.argv[5]
pairs = tuple(
    (int(pin), int(target))
    for pin, target in (item.split(":", 1) for item in sys.argv[6].split(","))
)
argv = sys.argv[7:]
try:
    os.set_inheritable(status_fd, False)
    data = bytearray()
    while len(data) <= 16777216:
        chunk = os.read(environment_fd, min(65536, 16777217 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    os.close(environment_fd)
    if len(data) > 16777216:
        raise ValueError("environment transport exceeds its bound")
    if len(data) < 4:
        raise ValueError("environment transport is incomplete")
    count = struct.unpack_from("!I", data)[0]
    offset = 4
    environment_entries = []
    for _index in range(count):
        if offset + 8 > len(data):
            raise ValueError("environment transport is incomplete")
        key_size, value_size = struct.unpack_from("!II", data, offset)
        offset += 8
        end = offset + key_size + value_size
        if end > len(data):
            raise ValueError("environment transport is incomplete")
        key_end = offset + key_size
        key = bytes(data[offset:key_end])
        value = bytes(data[key_end:end])
        environment_entries.append(key + b"=" + value)
        offset = end
    if offset + 4 > len(data):
        raise ValueError("environment transport is incomplete")
    search_path_count = struct.unpack_from("!I", data, offset)[0]
    offset += 4
    executable_search_path = []
    for _index in range(search_path_count):
        if offset + 4 > len(data):
            raise ValueError("environment transport is incomplete")
        directory_size = struct.unpack_from("!I", data, offset)[0]
        offset += 4
        end = offset + directory_size
        if end > len(data):
            raise ValueError("environment transport is incomplete")
        executable_search_path.append(bytes(data[offset:end]))
        offset = end
    if offset != len(data):
        raise ValueError("environment transport has trailing data")
    for pin, target in pairs:
        os.dup2(pin, target, inheritable=True)
    for pin, _target in pairs:
        os.close(pin)
    for number, ignored in signal_states:
        signal.signal(number, signal.SIG_IGN if ignored else signal.SIG_DFL)
    executable_bytes = os.fsencode(executable)
    if os.path.dirname(executable_bytes):
        executable_candidates = (executable_bytes,)
    else:
        executable_candidates = tuple(
            os.path.join(directory, executable_bytes)
            for directory in executable_search_path
        )
    argv_bytes = tuple(os.fsencode(item) for item in argv)
    argv_vector = (ctypes.c_char_p * (len(argv_bytes) + 1))(*argv_bytes, None)
    environment_vector = (ctypes.c_char_p * (len(environment_entries) + 1))(
        *environment_entries, None
    )
    execve = ctypes.CDLL(None, use_errno=True).execve
    execve.argtypes = (
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_char_p),
    )
    execve.restype = ctypes.c_int
    saved_error = None
    last_error = None
    for candidate in executable_candidates:
        ctypes.set_errno(0)
        execve(candidate, argv_vector, environment_vector)
        error_number = ctypes.get_errno() or errno.EIO
        last_error = OSError(error_number, os.strerror(error_number), candidate)
        if (
            saved_error is None
            and error_number not in (errno.ENOENT, errno.ENOTDIR)
        ):
            saved_error = last_error
    if saved_error is not None:
        raise saved_error
    if last_error is not None:
        raise last_error
    raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), executable_bytes)
except BaseException as exc:
    error_number = getattr(exc, "errno", None) or errno.EIO
    message = os.strerror(error_number).encode("utf-8", "replace")[:4096]
    try:
        os.write(status_fd, str(error_number).encode("ascii") + b":" + message)
    except BaseException:
        pass
    os._exit(127)
"""


@dataclass(frozen=True, slots=True)
class InheritedDescriptorIdentity:
    """Path-free POSIX object identity expected at descriptor pinning."""

    device: int
    inode: int
    file_type: int

    def __post_init__(self) -> None:
        for name in ("device", "inode", "file_type"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @classmethod
    def capture(cls, descriptor: int) -> "InheritedDescriptorIdentity":
        """Capture one open descriptor without retaining path authority."""
        if type(descriptor) is not int:
            raise TypeError("descriptor must be an integer")
        observed = os.fstat(descriptor)
        return cls(
            device=int(observed.st_dev),
            inode=int(observed.st_ino),
            file_type=int(stat.S_IFMT(observed.st_mode)),
        )


def _serialize_descriptor_bootstrap_environment(
    environment: object,
    target_executable: str,
) -> tuple[bytes, dict[bytes, bytes] | None]:
    """Snapshot final-exec bytes and original-mapping search authority."""
    if environment is None:
        entries = tuple(os.environb.items())
        bootstrap_environment = None
    else:
        entries = tuple(
            (os.fsencode(key), os.fsencode(value))
            for key, value in environment.items()
        )
        bootstrap_environment = dict(entries)

    payload = bytearray(struct.pack("!I", len(entries)))
    for key, value in entries:
        if b"=" in key:
            raise ValueError("illegal environment variable name")
        if b"\0" in key or b"\0" in value:
            raise ValueError("embedded null byte")
        payload.extend(struct.pack("!II", len(key), len(value)))
        payload.extend(key)
        payload.extend(value)
        if len(payload) > _DESCRIPTOR_BOOTSTRAP_ENV_MAX_BYTES:
            raise ValueError("environment is too large for descriptor launch")
    executable_search_path: tuple[bytes, ...] = ()
    if not os.path.dirname(os.fsencode(target_executable)):
        executable_search_path = tuple(
            os.fsencode(directory) for directory in os.get_exec_path(environment)
        )
    payload.extend(struct.pack("!I", len(executable_search_path)))
    if len(payload) > _DESCRIPTOR_BOOTSTRAP_ENV_MAX_BYTES:
        raise ValueError("environment is too large for descriptor launch")
    for directory in executable_search_path:
        if b"\0" in directory:
            raise ValueError("embedded null byte")
        payload.extend(struct.pack("!I", len(directory)))
        payload.extend(directory)
        if len(payload) > _DESCRIPTOR_BOOTSTRAP_ENV_MAX_BYTES:
            raise ValueError("environment is too large for descriptor launch")
    return bytes(payload), bootstrap_environment


def _write_descriptor_payload(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "environment transport write failed")
        view = view[written:]


def _close_descriptors(descriptors: Sequence[int]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _pin_read_only_descriptors(
    descriptors: tuple[int, ...],
    expected_identities: tuple[InheritedDescriptorIdentity, ...] = (),
) -> tuple[tuple[int, int], ...]:
    """Own stable POSIX duplicates without changing ownership of caller fds."""
    import fcntl

    pins: list[tuple[int, int]] = []
    next_descriptor = max(descriptors, default=2) + 1
    try:
        for index, descriptor in enumerate(descriptors):
            try:
                pin = int(
                    fcntl.fcntl(
                        descriptor,
                        fcntl.F_DUPFD_CLOEXEC,
                        next_descriptor,
                    )
                )
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    raise ValueError(
                        f"inherited descriptor {descriptor} is closed"
                    ) from exc
                raise ValueError(
                    f"inherited descriptor {descriptor} could not be pinned"
                ) from exc
            pins.append((pin, descriptor))
            next_descriptor = pin + 1
            if expected_identities:
                try:
                    pinned_identity = InheritedDescriptorIdentity.capture(pin)
                except OSError as exc:
                    raise ValueError(
                        f"inherited descriptor {descriptor} identity could not be inspected"
                    ) from exc
                if pinned_identity != expected_identities[index]:
                    raise ValueError(
                        f"inherited descriptor {descriptor} identity changed before pinning"
                    )
            try:
                flags = int(fcntl.fcntl(pin, fcntl.F_GETFL))
            except OSError as exc:
                raise ValueError(
                    f"inherited descriptor {descriptor} access mode could not be inspected"
                ) from exc
            if flags & os.O_ACCMODE != os.O_RDONLY:
                raise ValueError(
                    f"inherited descriptor {descriptor} must be read-only"
                )
        return tuple(pins)
    except BaseException:
        _close_descriptors(tuple(pin for pin, _target in pins))
        raise


def _read_descriptor_bootstrap_error(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _DESCRIPTOR_BOOTSTRAP_ERROR_MAX_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _open_internal_pipe_above(minimum: int) -> tuple[int, int]:
    """Create a CLOEXEC status pipe that cannot alias a nominated target fd."""
    import fcntl

    read_descriptor, write_descriptor = os.pipe()
    safe_read: int | None = None
    safe_write: int | None = None
    try:
        safe_read = int(
            fcntl.fcntl(
                read_descriptor,
                fcntl.F_DUPFD_CLOEXEC,
                minimum,
            )
        )
        safe_write = int(
            fcntl.fcntl(
                write_descriptor,
                fcntl.F_DUPFD_CLOEXEC,
                safe_read + 1,
            )
        )
        return safe_read, safe_write
    except BaseException:
        _close_descriptors(
            tuple(
                descriptor
                for descriptor in (safe_read, safe_write)
                if descriptor is not None
            )
        )
        raise
    finally:
        _close_descriptors((read_descriptor, write_descriptor))


def _descriptor_bootstrap_signal_states(restore_signals: bool) -> str:
    """Serialize final-exec dispositions that Python startup may overwrite."""
    states: list[str] = []
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        value = getattr(signal, name, None)
        if value is None:
            continue
        disposition = (
            signal.SIG_DFL if restore_signals else signal.getsignal(value)
        )
        ignored = disposition == signal.SIG_IGN
        states.append(f"{int(value)}:{'1' if ignored else '0'}")
    return ",".join(states)


class _WindowsJob:
    """Owned named Win32 Job Object with kill-on-last-handle-close."""

    def __init__(self, handle: int, name: str) -> None:
        self.handle = handle
        self.name = name

    @staticmethod
    def _kernel32():
        import ctypes

        return ctypes.WinDLL("kernel32", use_last_error=True)

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        import ctypes

        error = ctypes.get_last_error()
        raise OSError(error, f"{operation} failed with Win32 error {error}")

    @classmethod
    def create(cls) -> "_WindowsJob":
        import ctypes
        from ctypes import wintypes

        name = f"Local\\HermesManagedProcess-{uuid.uuid4().hex}"
        kernel32 = cls._kernel32()
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, name)
        if not handle:
            cls._raise_last_error("CreateJobObjectW")
        job = cls(int(handle), name)
        try:
            job._enable_kill_on_close()
        except BaseException:
            job.close()
            raise
        return job

    @classmethod
    def open(cls, name: str) -> "_WindowsJob | None":
        import ctypes
        from ctypes import wintypes

        kernel32 = cls._kernel32()
        kernel32.OpenJobObjectW.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.OpenJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.OpenJobObjectW(
            _JOB_OBJECT_QUERY | _JOB_OBJECT_TERMINATE,
            False,
            name,
        )
        if handle:
            return cls(int(handle), name)
        if ctypes.get_last_error() == 2:
            return None
        cls._raise_last_error("OpenJobObjectW")

    def _enable_kill_on_close(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
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

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        kernel32 = self._kernel32()
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        if not kernel32.SetInformationJobObject(
            self.handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise_last_error("SetInformationJobObject")

    def assign(self, process_handle: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = self._kernel32()
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(self.handle, process_handle):
            self._raise_last_error("AssignProcessToJobObject")

    @classmethod
    def resume_process(cls, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        class ThreadEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel32 = cls._kernel32()
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if int(snapshot or 0) == ctypes.c_void_p(-1).value:
            cls._raise_last_error("CreateToolhelp32Snapshot")
        resumed = False
        try:
            kernel32.Thread32First.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ThreadEntry32),
            ]
            kernel32.Thread32First.restype = wintypes.BOOL
            kernel32.Thread32Next.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ThreadEntry32),
            ]
            kernel32.Thread32Next.restype = wintypes.BOOL
            kernel32.OpenThread.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenThread.restype = wintypes.HANDLE
            kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
            kernel32.ResumeThread.restype = wintypes.DWORD
            entry = ThreadEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            present = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
            while present:
                if int(entry.th32OwnerProcessID) == pid:
                    thread = kernel32.OpenThread(
                        _THREAD_SUSPEND_RESUME,
                        False,
                        entry.th32ThreadID,
                    )
                    if not thread:
                        cls._raise_last_error("OpenThread")
                    try:
                        if kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                            cls._raise_last_error("ResumeThread")
                        resumed = True
                    finally:
                        kernel32.CloseHandle(thread)
                    break
                present = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        if not resumed:
            raise OSError(f"no resumable primary thread found for pid {pid}")

    def active_processes(self) -> int:
        import ctypes
        from ctypes import wintypes

        class BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        information = BasicAccountingInformation()
        returned = wintypes.DWORD()
        kernel32 = self._kernel32()
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        if not kernel32.QueryInformationJobObject(
            self.handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        ):
            self._raise_last_error("QueryInformationJobObject")
        return int(information.ActiveProcesses)

    def terminate_and_wait(self, timeout_seconds: float) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = self._kernel32()
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        if not kernel32.TerminateJobObject(self.handle, 1):
            self._raise_last_error("TerminateJobObject")
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            if self.active_processes() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def close(self) -> None:
        if not self.handle:
            return
        import ctypes
        from ctypes import wintypes

        handle = self.handle
        self.handle = 0
        kernel32 = self._kernel32()
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(handle):
            self._raise_last_error("CloseHandle")


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
    job_name: str | None = None

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
        windows_job: _WindowsJob | None = None,
    ) -> None:
        self.process = process
        self.identity = identity
        self.policy = policy or TerminationPolicy()
        self._lock = threading.Lock()
        self._reaped = False
        self._returncode: int | None = None
        self._windows_job = windows_job
        self._windows_tree_confirmed = False

    @property
    def reaped(self) -> bool:
        return self._reaped

    def __del__(self) -> None:
        job = getattr(self, "_windows_job", None)
        if job is None:
            return
        self._windows_job = None
        try:
            job.close()
        except Exception:
            pass

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
        inherited_descriptors: Sequence[int] = (),
        inherited_descriptor_identities: Sequence[InheritedDescriptorIdentity] = (),
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

        if isinstance(inherited_descriptors, (str, bytes)) or not isinstance(
            inherited_descriptors, Sequence
        ):
            raise TypeError("inherited_descriptors must be a sequence of integers")
        inherited = tuple(inherited_descriptors)
        if len(inherited) > _MAX_INHERITED_DESCRIPTORS:
            raise ValueError("inherited_descriptors accepts at most 64 descriptors")
        if any(type(descriptor) is not int for descriptor in inherited):
            raise TypeError("every inherited descriptor must be an integer")
        if any(descriptor <= 2 for descriptor in inherited):
            raise ValueError("standard and negative descriptors cannot be inherited")
        if len(set(inherited)) != len(inherited):
            raise ValueError("inherited descriptors must be unique")
        if isinstance(inherited_descriptor_identities, (str, bytes)) or not isinstance(
            inherited_descriptor_identities,
            Sequence,
        ):
            raise TypeError("inherited_descriptor_identities must be a sequence")
        expected_identities = tuple(inherited_descriptor_identities)
        if expected_identities and len(expected_identities) != len(inherited):
            raise ValueError(
                "inherited descriptor identities must align with descriptors"
            )
        if any(
            not isinstance(identity, InheritedDescriptorIdentity)
            for identity in expected_identities
        ):
            raise TypeError(
                "every inherited descriptor identity must be an "
                "InheritedDescriptorIdentity"
            )
        if "pass_fds" in popen_kwargs:
            raise TypeError(
                "pass_fds is not supported; use inherited_descriptors instead"
            )
        if _IS_WINDOWS and inherited:
            raise ValueError("inherited descriptors are not supported on native Windows")

        kwargs = dict(popen_kwargs)
        if inherited and kwargs.get("shell"):
            raise ValueError("shell=True is not supported with inherited descriptors")
        if inherited and kwargs.get("preexec_fn") is not None:
            raise ValueError(
                "preexec_fn is not supported with inherited descriptors"
            )
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.STDOUT)
        launch_argv = list(argv)
        pinned: tuple[tuple[int, int], ...] = ()
        status_read: int | None = None
        status_write: int | None = None
        environment_read: int | None = None
        environment_write: int | None = None
        environment_payload = b""
        resolved_executable: object = argv[0]
        use_argv_zero = True
        executable = ""
        restore_signals = True
        bootstrap_signal_states = ""
        if inherited and not _IS_WINDOWS:
            raw_executable = kwargs.pop("executable", None)
            use_argv_zero = raw_executable is None
            if not use_argv_zero:
                executable = os.fsdecode(os.fspath(raw_executable))
                resolved_executable = raw_executable
            restore_signals = bool(
                operator.index(kwargs.get("restore_signals", True))
            )
            kwargs["restore_signals"] = restore_signals
            bootstrap_signal_states = _descriptor_bootstrap_signal_states(
                restore_signals
            )
            target_executable = argv[0] if use_argv_zero else executable
            environment_payload, bootstrap_environment = (
                _serialize_descriptor_bootstrap_environment(
                    kwargs.get("env"),
                    target_executable,
                )
            )
            if "env" in kwargs:
                kwargs["env"] = bootstrap_environment

        windows_job = None
        process = None
        try:
            if _IS_WINDOWS:
                kwargs["creationflags"] = (
                    int(kwargs.get("creationflags", windows_hide_flags()))
                    | _CREATE_SUSPENDED
                )
            else:
                kwargs.setdefault("start_new_session", True)
            if inherited:
                pinned = _pin_read_only_descriptors(
                    inherited,
                    expected_identities,
                )
                status_read, status_write = _open_internal_pipe_above(
                    max(
                        *inherited,
                        *(pin for pin, _target in pinned),
                    )
                    + 1
                )
                environment_read, environment_write = _open_internal_pipe_above(
                    max(status_read, status_write) + 1
                )
                mapping = ",".join(
                    f"{pin}:{target}" for pin, target in pinned
                )
                launch_argv = [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _POSIX_DESCRIPTOR_BOOTSTRAP,
                    str(status_write),
                    str(environment_read),
                    bootstrap_signal_states,
                    "1" if use_argv_zero else "0",
                    executable,
                    mapping,
                    *argv,
                ]
                kwargs["close_fds"] = True
                kwargs["pass_fds"] = (
                    *(pin for pin, _target in pinned),
                    status_write,
                    environment_read,
                )

            windows_job = _WindowsJob.create() if _IS_WINDOWS else None
            # noqa: subprocess-stdin — stdin IS set: kwargs.setdefault("stdin",
            # subprocess.DEVNULL) above. The scanner reads the call text only and
            # cannot see kwargs populated earlier in the function.
            process = subprocess.Popen(launch_argv, **kwargs)
            if pinned:
                assert environment_read is not None
                assert environment_write is not None
                os.close(environment_read)
                environment_read = None
                try:
                    _write_descriptor_payload(
                        environment_write,
                        environment_payload,
                    )
                finally:
                    os.close(environment_write)
                    environment_write = None
                _close_descriptors(tuple(pin for pin, _target in pinned))
                pinned = ()
                assert status_read is not None
                assert status_write is not None
                os.close(status_write)
                status_write = None
                try:
                    bootstrap_error = _read_descriptor_bootstrap_error(status_read)
                finally:
                    os.close(status_read)
                    status_read = None
                if bootstrap_error:
                    number, separator, message = bootstrap_error.partition(b":")
                    error_number = (
                        int(number)
                        if separator and number.isdigit()
                        else errno.EIO
                    )
                    raise OSError(
                        error_number,
                        message.decode("utf-8", "replace"),
                        resolved_executable,
                    )
                # The bootstrap execs in place; keep Popen's public identity
                # compatible with a direct launch rather than exposing internals.
                process.args = list(argv)
            if windows_job is not None:
                windows_job.assign(int(process._handle))
                windows_job.resume_process(process.pid)
            identity = ProcessIdentity.capture(process.pid)
            if windows_job is not None:
                identity = ProcessIdentity(
                    pid=identity.pid,
                    start_time=identity.start_time,
                    group_id=identity.group_id,
                    job_name=windows_job.name,
                )
            return cls(
                process,
                identity,
                policy=policy,
                windows_job=windows_job,
            )
        except BaseException:
            if process is not None:
                try:
                    process.kill()
                except BaseException:
                    pass
                try:
                    process.wait(timeout=1)
                except BaseException:
                    pass
            if windows_job is not None:
                try:
                    windows_job.close()
                except BaseException:
                    pass
            raise
        finally:
            _close_descriptors(tuple(pin for pin, _target in pinned))
            if status_read is not None:
                try:
                    os.close(status_read)
                except OSError:
                    pass
            if status_write is not None:
                try:
                    os.close(status_write)
                except OSError:
                    pass
            if environment_read is not None:
                try:
                    os.close(environment_read)
                except OSError:
                    pass
            if environment_write is not None:
                try:
                    os.close(environment_write)
                except OSError:
                    pass

    def tree_active(self) -> bool:
        """Return true while any owned process is live or quiescence is uncertain."""
        if _IS_WINDOWS:
            if self._windows_job is None:
                return not self._windows_tree_confirmed
            try:
                return self._windows_job.active_processes() > 0
            except OSError:
                return True
        if self.process.poll() is None:
            return True
        group_id = self.identity.group_id
        if group_id is None or group_id <= 0 or group_id == os.getpgrp():
            return False
        try:
            os.killpg(group_id, 0)  # windows-footgun: ok - POSIX branch
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def existing_tree_active(cls, identity: ProcessIdentity) -> bool | None:
        """Query a persisted Windows job; return None when proof is unavailable."""
        if not _IS_WINDOWS or not identity.job_name:
            return None
        try:
            job = _WindowsJob.open(identity.job_name)
        except OSError:
            return None
        if job is None:
            return None if identity.is_current() else False
        try:
            return job.active_processes() > 0
        except OSError:
            return None
        finally:
            try:
                job.close()
            except OSError:
                pass

    def _terminate_owned_windows_job(self) -> bool:
        job = self._windows_job
        if job is None:
            return self._windows_tree_confirmed
        confirmed = False
        try:
            confirmed = job.terminate_and_wait(
                self.policy.term_grace_seconds + self.policy.kill_grace_seconds
            )
            return confirmed
        except OSError:
            return False
        finally:
            self._windows_tree_confirmed = confirmed
            self._windows_job = None
            try:
                job.close()
            except OSError:
                self._windows_tree_confirmed = False

    def terminate(self, reason: str = "shutdown") -> int | None:
        """Stop the owned tree and reap the direct child; safe to call twice."""
        del reason  # reserved for owner audit/logging without changing semantics
        with self._lock:
            if self._reaped:
                return self._returncode

            if self.process.poll() is not None:
                if _IS_WINDOWS:
                    cleaned = self._terminate_owned_windows_job()
                else:
                    cleaned = self._terminate_owned_posix_group()
                if not cleaned:
                    raise RuntimeError(
                        f"owned process tree {self.identity.pid} was not proven "
                        "quiescent within the bounded termination policy"
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

            terminated = (
                self._terminate_owned_windows_job()
                if _IS_WINDOWS
                else self.terminate_existing(
                    self.identity,
                    term_grace_seconds=self.policy.term_grace_seconds,
                    kill_grace_seconds=self.policy.kill_grace_seconds,
                )
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
            elif not terminated:
                self._reap()
                raise RuntimeError(
                    f"owned Windows job {self.identity.job_name or self.identity.pid} "
                    "was not proven quiescent within the bounded termination policy"
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
        self._reaped = self.process.poll() is not None and (
            not _IS_WINDOWS or self._windows_tree_confirmed
        )
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
        windows = _IS_WINDOWS if is_windows is None else is_windows
        if windows and identity.job_name:
            try:
                job = _WindowsJob.open(identity.job_name)
            except OSError:
                return False
            if job is None:
                return not identity.is_current()
            try:
                return job.terminate_and_wait(
                    term_grace_seconds + kill_grace_seconds
                )
            except OSError:
                return False
            finally:
                try:
                    job.close()
                except OSError:
                    pass

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

        run = subprocess.run if subprocess_run is None else subprocess_run
        kill = os.kill if os_kill is None else os_kill
        if windows:
            try:
                run(
                    ["taskkill", "/PID", str(identity.pid), "/T", "/F"],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=10,
                    creationflags=(
                        windows_hide_flags() if creationflags is None else creationflags
                    ),
                    stdin=subprocess.DEVNULL,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                try:
                    kill(identity.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError, PermissionError):
                    pass
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
