#!/usr/bin/env python3
"""Validate and compare Hermes' machine-readable customization ledger."""

from __future__ import annotations

import argparse
import ast
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
import difflib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Any, BinaryIO, Sequence

import yaml


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_MAX_EVIDENCE_ID_LENGTH = 512
_MAX_REPOSITORY_PATH_BYTES = 4096
_MAX_OWNED_SYMBOL_BYTES = 256
_MAX_OWNED_INVARIANT_LENGTH = 512
_MAX_OWNED_INVARIANTS = 128
_MAX_SCANNED_SOURCE_BYTES = 4 * 1024 * 1024
_PARSER_PROTOCOL = 1
_MAX_PARSER_BATCH_BYTES = 16 * 1024 * 1024
_MAX_PARSER_OUTPUT_BYTES = 16 * 1024 * 1024
_PARSER_TIMEOUT_SECONDS = 60.0
_MAX_PARSER_REQUEST_ID_BYTES = 128
_CAPTURE_CLEANUP_SECONDS = 1.0
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_NON_PYTHON_HELPER = _REPOSITORY_ROOT / "scripts" / "extract_non_python_symbols.mjs"
_PARSER_LANGUAGES = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdown": "markdown",
    ".mkdn": "markdown",
}
_EXPECTED_PARSER_VERSIONS = {
    "typescript": "6.0.3",
    "unified": "11.0.5",
    "remark-parse": "11.0.0",
    "micromark": "4.0.2",
}
_PARSER_VERSION_KEY = tuple(sorted(_EXPECTED_PARSER_VERSIONS.items()))
_PARSER_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OVERLAP_POLICIES = frozenset({"owned_symbol", "any_owned_file"})
_MACHINE_SYMBOL = re.compile(r"^\S+$")
_EPHEMERAL_COVERAGE_PATHS = frozenset({".superpowers/sdd/progress.md"})
_REQUIRED = {
    "id", "change_class", "owner", "files", "owned_symbols", "tests",
    "expected_commit_subject", "upstream_candidate", "merge_guidance",
    "removal_condition", "last_verified_upstream",
}

_WINDOWS_JOB_BOOTSTRAP = (
    "import subprocess, sys\n"
    "if sys.stdin.buffer.read(1) != b'1':\n"
    "    raise SystemExit(125)\n"
    "try:\n"
    "    child = subprocess.Popen(sys.argv[1:])\n"
    "except OSError:\n"
    "    raise SystemExit(125)\n"
    "raise SystemExit(child.wait())\n"
)


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


class _JobObjectIoCounters(ctypes.Structure):
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
        ("IoInfo", _JobObjectIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _windows_job_error(operation: str) -> OSError:
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
            continue


class _WindowsJobContainment:
    """Win32 process-tree ownership with kill-on-last-handle-close."""

    def __init__(self, kernel32: Any | None = None) -> None:
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_windows_job_api(kernel32)
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _windows_job_error("creation")
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise _windows_job_error("configuration")

    def assign(self, process_handle: int) -> None:
        if self._handle is None or not self._kernel32.AssignProcessToJobObject(
            self._handle, process_handle
        ):
            raise _windows_job_error("assignment")

    def terminate(self) -> None:
        if self._handle is not None and not self._kernel32.TerminateJobObject(
            self._handle, 1
        ):
            raise _windows_job_error("termination")

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._kernel32.CloseHandle(handle):
                raise _windows_job_error("close")


def _is_windows() -> bool:
    return os.name == "nt"


@dataclass(frozen=True)
class _ParserRequest:
    request_id: str
    path: str
    language: str
    blob_oid: str
    source: bytes
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class _CompletedCapture:
    returncode: int
    stdout: bytes
    stderr: bytes


class _BoundedCaptureFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parser_error(code: str, path: str | None = None) -> ValueError:
    safe_code = code if _PARSER_ERROR_CODE.fullmatch(code) else "internal_failure"
    message = f"non-Python parser {safe_code}"
    if path is not None:
        portable = PurePosixPath(path)
        if (
            path
            and not portable.is_absolute()
            and ".." not in portable.parts
            and portable.as_posix() == path
        ):
            display_path = path if len(path) <= 440 else f"{path[:437]}..."
            message += f" at {json.dumps(display_path, ensure_ascii=True)}"
    return ValueError(message[:512])


def _parser_language(path: str) -> str | None:
    return _PARSER_LANGUAGES.get(PurePosixPath(path).suffix.lower())


def _validated_parser_metadata(request: _ParserRequest) -> None:
    if (
        not isinstance(request.request_id, str)
        or not request.request_id
        or len(request.request_id.encode("utf-8")) > _MAX_PARSER_REQUEST_ID_BYTES
        or not isinstance(request.path, str)
        or not request.path
        or len(request.path.encode("utf-8")) > _MAX_REPOSITORY_PATH_BYTES
        or not isinstance(request.symbols, tuple)
        or not all(
            isinstance(symbol, str)
            and bool(symbol)
            and len(symbol.encode("utf-8")) <= _MAX_OWNED_SYMBOL_BYTES
            for symbol in request.symbols
        )
    ):
        raise _parser_error("invalid_request", request.path if isinstance(request.path, str) else None)


def _run_bounded_capture(
    argv: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> _CompletedCapture:
    """Run a child with bounded combined output and sanitized failure states."""
    timeout_seconds = max(timeout_seconds, 0)
    deadline = time.monotonic() + timeout_seconds
    cleanup_budget = min(_CAPTURE_CLEANUP_SECONDS, timeout_seconds / 10)
    work_deadline = deadline - cleanup_budget
    windows_job: _WindowsJobContainment | None = None
    command = list(argv)
    payload = input_bytes or b""
    process_options: dict[str, Any] = {}
    if _is_windows():
        try:
            windows_job = _WindowsJobContainment()
        except Exception as exc:
            raise _BoundedCaptureFailure("containment_failure") from exc
        command = [sys.executable, "-c", _WINDOWS_JOB_BOOTSTRAP, *command]
        payload = b"1" + payload
    elif os.name == "posix":
        process_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_options,
        )
    except Exception as exc:
        if windows_job is not None:
            try:
                windows_job.close()
            except Exception:
                pass
        raise _BoundedCaptureFailure("start_failure") from exc

    if windows_job is not None:
        try:
            windows_job.assign(int(process._handle))
        except Exception as exc:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(
                    timeout=min(
                        cleanup_budget, max(0, deadline - time.monotonic())
                    )
                )
            except Exception:
                pass
            try:
                windows_job.close()
            except Exception:
                pass
            raise _BoundedCaptureFailure("containment_failure") from exc

    def close_windows_containment() -> None:
        nonlocal windows_job
        if windows_job is not None:
            containment, windows_job = windows_job, None
            try:
                containment.close()
            except Exception as exc:
                raise _BoundedCaptureFailure("containment_failure") from exc

    def terminate_process_group() -> None:
        try:
            if windows_job is not None:
                windows_job.terminate()
            elif os.name == "posix" and hasattr(process, "pid"):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            raise _BoundedCaptureFailure("kill_failure") from exc

    def kill_and_reap() -> int:
        failure: _BoundedCaptureFailure | None = None
        reaped_returncode = -1
        try:
            terminate_process_group()
        except _BoundedCaptureFailure as exc:
            failure = exc
        try:
            reaped_returncode = process.wait(
                timeout=min(cleanup_budget, max(0, deadline - time.monotonic()))
            )
        except subprocess.TimeoutExpired as exc:
            if failure is None:
                failure = _BoundedCaptureFailure("wait_failure")
                failure.__cause__ = exc
        except Exception as exc:
            if failure is None:
                failure = _BoundedCaptureFailure("wait_failure")
                failure.__cause__ = exc
        if failure is not None:
            raise failure
        return reaped_returncode

    if process.stdin is None or process.stdout is None or process.stderr is None:
        try:
            kill_and_reap()
        except Exception:
            pass
        try:
            close_windows_containment()
        except Exception:
            pass
        raise _BoundedCaptureFailure("stream_failure")

    captured = {"stdout": bytearray(), "stderr": bytearray()}
    captured_bytes = 0
    capture_lock = threading.Lock()
    output_exceeded = threading.Event()
    stream_failed = threading.Event()

    def kill_for_failure() -> None:
        try:
            terminate_process_group()
        except _BoundedCaptureFailure:
            stream_failed.set()

    def write_and_close(stream: BinaryIO, payload: bytes) -> None:
        try:
            stream.write(payload)
        except BrokenPipeError:
            pass
        except Exception:
            stream_failed.set()
            kill_for_failure()
        finally:
            try:
                stream.close()
            except Exception:
                stream_failed.set()
                kill_for_failure()

    def drain(name: str, stream: BinaryIO) -> None:
        nonlocal captured_bytes
        try:
            while chunk := stream.read(64 * 1024):
                with capture_lock:
                    if captured_bytes + len(chunk) > output_limit_bytes:
                        output_exceeded.set()
                        kill_for_failure()
                        return
                    captured[name].extend(chunk)
                    captured_bytes += len(chunk)
        except Exception:
            stream_failed.set()
            kill_for_failure()

    writer = threading.Thread(
        target=write_and_close,
        args=(process.stdin, payload),
        daemon=True,
    )
    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    threads = [writer, *readers]
    started: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started.append(thread)
    except Exception as exc:
        try:
            kill_and_reap()
        except Exception:
            pass
        cleanup_deadline = min(
            deadline, time.monotonic() + cleanup_budget
        )
        for thread in started:
            try:
                thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
            except Exception:
                pass
        try:
            close_windows_containment()
        except Exception:
            pass
        raise _BoundedCaptureFailure("thread_failure") from exc

    failure: _BoundedCaptureFailure | None = None
    returncode = -1
    try:
        returncode = process.wait(timeout=max(0, work_deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        try:
            returncode = kill_and_reap()
        except _BoundedCaptureFailure as exc:
            failure = exc
        if failure is None:
            failure = _BoundedCaptureFailure("timeout")
    except Exception as exc:
        try:
            kill_and_reap()
        except Exception:
            pass
        failure = _BoundedCaptureFailure("wait_failure")
        failure.__cause__ = exc

    join_deadline = (
        work_deadline
        if failure is None
        else min(deadline, time.monotonic() + cleanup_budget)
    )
    for thread in threads:
        try:
            thread.join(timeout=max(0, join_deadline - time.monotonic()))
        except Exception as exc:
            if failure is None:
                failure = _BoundedCaptureFailure("thread_failure")
                failure.__cause__ = exc

    unfinished = [thread for thread in threads if thread.is_alive()]
    if unfinished:
        try:
            kill_and_reap()
        except _BoundedCaptureFailure as exc:
            if failure is None:
                failure = exc
        cleanup_deadline = min(deadline, time.monotonic() + cleanup_budget)
        for thread in unfinished:
            try:
                thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
            except Exception as exc:
                if failure is None:
                    failure = _BoundedCaptureFailure("thread_failure")
                    failure.__cause__ = exc
        if failure is None:
            failure = _BoundedCaptureFailure("timeout")

    try:
        close_windows_containment()
    except _BoundedCaptureFailure as exc:
        if failure is None:
            failure = exc

    if failure is not None:
        raise failure
    if output_exceeded.is_set():
        raise _BoundedCaptureFailure("output_limit")
    if stream_failed.is_set():
        raise _BoundedCaptureFailure("stream_failure")
    return _CompletedCapture(
        returncode=returncode,
        stdout=bytes(captured["stdout"]),
        stderr=bytes(captured["stderr"]),
    )


def _validated_parser_source(source: bytes, path: str | None = None) -> None:
    if len(source) > _MAX_SCANNED_SOURCE_BYTES:
        raise _parser_error("input_limit", path)
    if b"\0" in source:
        raise _parser_error("invalid_source", path)
    try:
        source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _parser_error("invalid_utf8", path) from exc


def _is_utf8_boundary(source: bytes, offset: int) -> bool:
    return offset in {0, len(source)} or source[offset] & 0b1100_0000 != 0b1000_0000


def _json_record(line: bytes) -> dict[str, Any]:
    def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    value = json.loads(line.decode("utf-8", errors="strict"), object_pairs_hook=exact_object)
    if not isinstance(value, dict):
        raise ValueError("record is not an object")
    return value


def _ndjson_records(payload: bytes) -> list[dict[str, Any]]:
    if not payload.endswith(b"\n"):
        raise ValueError("missing LF record terminator")
    records: list[dict[str, Any]] = []
    for framed_line in payload[:-1].split(b"\n"):
        line = framed_line[:-1] if framed_line.endswith(b"\r") else framed_line
        if b"\r" in line:
            raise ValueError("invalid record separator")
        records.append(_json_record(line))
    return records


def _parser_process_error(
    stdout: bytes,
    request_by_id: dict[str, _ParserRequest],
) -> ValueError:
    try:
        records = _ndjson_records(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _parser_error("process_failure")
    if not records:
        return _parser_error("process_failure")
    record = records[-1]
    if (
        set(record) != {"type", "id", "code", "detail"}
        or record.get("type") != "error"
        or not isinstance(record.get("code"), str)
        or not _PARSER_ERROR_CODE.fullmatch(record["code"])
        or not isinstance(record.get("detail"), str)
    ):
        return _parser_error("process_failure")
    request_id = record.get("id")
    if request_id is None:
        return _parser_error(record["code"])
    if not isinstance(request_id, str) or request_id not in request_by_id:
        return _parser_error("invalid_response")
    return _parser_error(record["code"], request_by_id[request_id].path)


def _run_parser_batch(
    requests: Sequence[_ParserRequest],
) -> dict[str, dict[str, list[tuple[int, int]]]]:
    """Invoke and fully validate one attested non-Python parser batch."""
    try:
        node = shutil.which("node")
        if node is None:
            raise _BoundedCaptureFailure("unavailable")
        if not _NON_PYTHON_HELPER.is_file():
            raise _BoundedCaptureFailure("unavailable")
        request_by_id: dict[str, _ParserRequest] = {}
        payload_records: list[dict[str, Any]] = [
            {"type": "hello", "protocol": _PARSER_PROTOCOL}
        ]
        decoded_bytes = 0
        for request in requests:
            _validated_parser_metadata(request)
            _validated_parser_source(request.source, request.path)
            if request.request_id in request_by_id:
                raise _BoundedCaptureFailure("invalid_request")
            if _parser_language(request.path) != request.language:
                raise _BoundedCaptureFailure("invalid_request")
            decoded_bytes += len(request.source)
            if decoded_bytes > _MAX_PARSER_BATCH_BYTES:
                raise _BoundedCaptureFailure("input_limit")
            request_by_id[request.request_id] = request
            payload_records.append({
                "type": "parse",
                "id": request.request_id,
                "path": request.path,
                "language": request.language,
                "source_base64": base64.b64encode(request.source).decode("ascii"),
                "symbols": list(request.symbols),
            })
        payload = b"".join(
            json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            + b"\n"
            for record in payload_records
        )
        completed = _run_bounded_capture(
            [node, str(_NON_PYTHON_HELPER)],
            cwd=_REPOSITORY_ROOT,
            input_bytes=payload,
            timeout_seconds=_PARSER_TIMEOUT_SECONDS,
            output_limit_bytes=_MAX_PARSER_OUTPUT_BYTES,
        )
        if completed.returncode != 0:
            raise _parser_process_error(completed.stdout, request_by_id)
        try:
            records = _ndjson_records(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise _BoundedCaptureFailure("malformed_output") from exc
        if len(records) != len(requests) + 1:
            raise _BoundedCaptureFailure("response_count")
        hello = records[0]
        if (
            set(hello) != {"type", "protocol", "versions"}
            or hello.get("type") != "hello"
            or type(hello.get("protocol")) is not int
            or hello.get("protocol") != _PARSER_PROTOCOL
            or hello.get("versions") != _EXPECTED_PARSER_VERSIONS
        ):
            raise _BoundedCaptureFailure("attestation_failure")

        results: dict[str, dict[str, list[tuple[int, int]]]] = {}
        for record in records[1:]:
            if set(record) != {"type", "id", "spans"} or record.get("type") != "result":
                raise _BoundedCaptureFailure("invalid_response")
            request_id = record.get("id")
            if (
                not isinstance(request_id, str)
                or request_id not in request_by_id
                or request_id in results
            ):
                raise _BoundedCaptureFailure("invalid_response")
            request = request_by_id[request_id]
            raw_spans = record.get("spans")
            if not isinstance(raw_spans, dict) or set(raw_spans) != set(request.symbols):
                raise _BoundedCaptureFailure("invalid_response")
            validated: dict[str, list[tuple[int, int]]] = {}
            for symbol in request.symbols:
                raw_symbol_spans = raw_spans[symbol]
                if not isinstance(raw_symbol_spans, list):
                    raise _BoundedCaptureFailure("invalid_span")
                symbol_spans: list[tuple[int, int]] = []
                previous_end = -1
                for raw_span in raw_symbol_spans:
                    if not isinstance(raw_span, list) or len(raw_span) != 2:
                        raise _BoundedCaptureFailure("invalid_span")
                    start, end = raw_span
                    if (
                        isinstance(start, bool)
                        or isinstance(end, bool)
                        or not isinstance(start, int)
                        or not isinstance(end, int)
                        or not 0 <= start < end <= len(request.source)
                        or not _is_utf8_boundary(request.source, start)
                        or not _is_utf8_boundary(request.source, end)
                        or start < previous_end
                    ):
                        raise _BoundedCaptureFailure("invalid_span")
                    symbol_spans.append((start, end))
                    previous_end = end
                validated[symbol] = symbol_spans
            results[request_id] = validated
        if set(results) != set(request_by_id):
            raise _BoundedCaptureFailure("invalid_response")
        return results
    except ValueError as exc:
        if str(exc).startswith("non-Python parser"):
            raise
        raise _parser_error("invalid_response") from exc
    except _BoundedCaptureFailure as exc:
        raise _parser_error(exc.code) from exc
    except Exception as exc:
        raise _parser_error("internal_failure") from exc


class _NonPythonSymbolResolver:
    def __init__(self) -> None:
        self._cache: dict[
            tuple[str, str, tuple[str, ...], int, tuple[tuple[str, str], ...]],
            dict[str, list[tuple[int, int]]],
        ] = {}

    @staticmethod
    def _key(
        request: _ParserRequest,
    ) -> tuple[str, str, tuple[str, ...], int, tuple[tuple[str, str], ...]]:
        return (
            request.blob_oid,
            request.language,
            request.symbols,
            _PARSER_PROTOCOL,
            _PARSER_VERSION_KEY,
        )

    def _store(
        self,
        results: dict[str, dict[str, list[tuple[int, int]]]],
        requests: Sequence[_ParserRequest],
    ) -> None:
        if set(results) != {request.request_id for request in requests}:
            raise _parser_error("invalid_response")
        for request in requests:
            result = results[request.request_id]
            if set(result) != set(request.symbols):
                raise _parser_error("invalid_response")
            self._cache[self._key(request)] = {
                symbol: list(result[symbol]) for symbol in request.symbols
            }

    def _cached_result(
        self, request: _ParserRequest
    ) -> dict[str, list[tuple[int, int]]]:
        return {
            symbol: list(spans)
            for symbol, spans in self._cache[self._key(request)].items()
        }

    def spans(
        self, requests: Sequence[_ParserRequest]
    ) -> dict[str, dict[str, list[tuple[int, int]]]]:
        canonical: list[_ParserRequest] = []
        request_ids: set[str] = set()
        for request in requests:
            if request.request_id in request_ids:
                raise _parser_error("duplicate_request")
            request_ids.add(request.request_id)
            if (
                _parser_language(request.path) != request.language
            ):
                raise _parser_error("invalid_request", request.path)
            _validated_parser_metadata(request)
            _validated_parser_source(request.source, request.path)
            canonical.append(
                replace(request, symbols=tuple(sorted(set(request.symbols))))
            )

        uncached: list[_ParserRequest] = []
        scheduled: set[
            tuple[str, str, tuple[str, ...], int, tuple[tuple[str, str], ...]]
        ] = set()
        for request in canonical:
            key = self._key(request)
            if key not in self._cache and key not in scheduled:
                uncached.append(request)
                scheduled.add(key)

        pending: list[_ParserRequest] = []
        pending_bytes = 0
        for request in uncached:
            if pending and pending_bytes + len(request.source) > _MAX_PARSER_BATCH_BYTES:
                self._store(_run_parser_batch(pending), pending)
                pending, pending_bytes = [], 0
            pending.append(request)
            pending_bytes += len(request.source)
        if pending:
            self._store(_run_parser_batch(pending), pending)
        return {
            request.request_id: self._cached_result(request) for request in canonical
        }


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if check and proc.returncode:
        raise ValueError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _resolve_commit(repo: Path, revision: str, label: str) -> str:
    if not revision or ".." in revision:
        raise ValueError(f"{label} is not a local commit: {revision or '<empty>'}")
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise ValueError(f"{label} is not a local commit: {revision}")
    return proc.stdout.strip()


def _resolved_diff_endpoints(repo: Path, diff_range: str) -> tuple[str, str]:
    if "..." in diff_range:
        if diff_range.count("...") != 1:
            raise ValueError(f"malformed diff range: {diff_range}")
        left_raw, right_raw = diff_range.split("...", 1)
        left_tip = _resolve_commit(repo, left_raw, "range left")
        right = _resolve_commit(repo, right_raw, "range right")
        proc = subprocess.run(
            ["git", "merge-base", left_tip, right],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode or not proc.stdout.strip():
            raise ValueError("triple-dot range has no merge base")
        return proc.stdout.strip(), right
    if ".." in diff_range:
        if diff_range.count("..") != 1:
            raise ValueError(f"malformed diff range: {diff_range}")
        left_raw, right_raw = diff_range.split("..", 1)
        return (
            _resolve_commit(repo, left_raw, "range left"),
            _resolve_commit(repo, right_raw, "range right"),
        )
    right = _resolve_commit(repo, diff_range, "range right")
    return _resolve_commit(repo, f"{right}^", "range left"), right


def _blob_bytes(repo: Path, revision: str, path: str) -> tuple[str, bytes]:
    oid = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}:{path}"],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if oid.returncode:
        raise ValueError("cannot resolve committed Git blob identity")
    try:
        blob_oid = oid.stdout.strip().decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("cannot resolve committed Git blob identity") from exc
    if not blob_oid:
        raise ValueError("cannot resolve committed Git blob identity")
    object_type = subprocess.run(
        ["git", "cat-file", "-t", blob_oid],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if object_type.returncode or object_type.stdout.strip() != b"blob":
        raise ValueError("committed Git object is not a blob")
    object_size = subprocess.run(
        ["git", "cat-file", "-s", blob_oid],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    try:
        blob_size = int(object_size.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("cannot resolve committed Git blob size") from exc
    if object_size.returncode or blob_size < 0:
        raise ValueError("cannot resolve committed Git blob size")
    if _parser_language(path) is not None and blob_size > _MAX_SCANNED_SOURCE_BYTES:
        raise _parser_error("input_limit", path)
    blob = subprocess.run(
        ["git", "cat-file", "blob", blob_oid],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if blob.returncode or len(blob.stdout) != blob_size:
        raise ValueError("cannot read committed Git blob")
    return blob_oid, blob.stdout


def _blob_text(repo: Path, revision: str, path: str) -> str:
    try:
        _blob_oid, source = _blob_bytes(repo, revision, path)
    except ValueError:
        # Existing diff callers model a path absent at one endpoint as empty
        # source. The byte-authority API itself remains fail-closed.
        if not _existed_at(repo, revision, path):
            return ""
        raise
    return source.decode("utf-8", errors="strict")


def _collect_manifest_parser_requests(
    entries: Sequence[dict[str, Any]], repo: Path, revision: str
) -> list[_ParserRequest]:
    symbols_by_path: dict[str, set[str]] = {}
    for entry in entries:
        for path in entry["files"]:
            if _parser_language(path) is not None:
                symbols_by_path.setdefault(path, set()).update(
                    _strict_owned_symbols(entry)
                )
    requests: list[_ParserRequest] = []
    for request_index, path in enumerate(sorted(symbols_by_path)):
        blob_oid, source = _blob_bytes(repo, revision, path)
        language = _parser_language(path)
        assert language is not None
        requests.append(
            _ParserRequest(
                request_id=f"request-{request_index:08x}",
                path=path,
                language=language,
                blob_oid=blob_oid,
                source=source,
                symbols=tuple(sorted(symbols_by_path[path])),
            )
        )
    return requests


def _is_shallow_clone(repo: Path) -> bool:
    """True when this clone lacks the history the commit assertions need.

    ``actions/checkout`` fetches depth 1 by default, so neither
    ``coverage.base_commit`` nor any entry's ``last_verified_upstream`` exists
    locally in CI. Those assertions then fail for a reason that has nothing to
    do with the ledger being wrong -- the first CI run on this fork's
    development branch reported "coverage base is not a local commit" and took
    the whole merge gate down with it.

    Deepening the checkout was the alternative and was rejected: the pack is
    ~400 MiB and eight parallel test slices would each pay a full-history fetch
    to validate two pinned commits. Skipping only the history-dependent
    assertions keeps every schema, path, and field check running everywhere,
    and the skipped ones still run on any full clone -- a developer's checkout
    and the merge skill, which are the contexts that actually gate a merge.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() == "true"


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    ).returncode == 0


def _contained(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError(f"ledger path is not repository-contained: {raw}") from exc
    return path


def _repository_relative_path(raw: str) -> PurePosixPath:
    """Validate a ledger path without consulting the checkout filesystem."""
    portable = PurePosixPath(raw)
    if (
        portable.is_absolute()
        or ".." in portable.parts
        or portable.as_posix() != raw
    ):
        raise ValueError(f"path must be normalized repository-relative POSIX: {raw}")
    return portable


def _manifest_repository_path(manifest_path: Path, repo: Path) -> str:
    """Return the manifest's lexical path inside ``repo`` without following links."""
    absolute_repo = Path(os.path.abspath(repo))
    absolute_manifest = Path(os.path.abspath(manifest_path))
    try:
        relative = absolute_manifest.relative_to(absolute_repo)
    except ValueError as exc:
        raise ValueError(
            f"manifest path is not repository-contained: {manifest_path}"
        ) from exc
    return _repository_relative_path(relative.as_posix()).as_posix()


def _tree_entry(
    repo: Path,
    revision: str,
    path: str,
) -> tuple[str, str] | None:
    """Return the exact Git-tree mode and object type for one lexical path."""
    proc = subprocess.run(
        ["git", "ls-tree", "-z", revision, "--", path],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise ValueError(f"cannot inspect {path} at source revision")
    entries = [entry for entry in proc.stdout.split(b"\0") if entry]
    if not entries:
        return None
    if len(entries) != 1:
        raise ValueError(f"cannot determine exact tree entry for {path}")
    header, separator, name = entries[0].partition(b"\t")
    fields = header.split()
    if separator != b"\t" or len(fields) != 3 or name.decode("utf-8") != path:
        raise ValueError(f"cannot determine exact tree entry for {path}")
    return fields[0].decode("ascii"), fields[1].decode("ascii")


def _require_regular_file_at(repo: Path, revision: str, path: str) -> None:
    entry = _tree_entry(repo, revision, path)
    if entry is None:
        raise ValueError(f"ledger path {path} does not exist at source revision")
    mode, object_type = entry
    if object_type != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(
            f"ledger path {path} must be a regular file at source revision"
        )


def load_and_validate_manifest(
    manifest_path: Path,
    repo: Path,
    *,
    check_git: bool = True,
    source_revision: str = "HEAD",
    strict: bool = False,
) -> dict[str, Any]:
    resolved_source_revision = _resolve_commit(repo, source_revision, "source revision")
    if strict:
        manifest_repository_path = _manifest_repository_path(manifest_path, repo)
        _require_regular_file_at(
            repo, resolved_source_revision, manifest_repository_path
        )
        _manifest_oid, manifest_bytes = _blob_bytes(
            repo, resolved_source_revision, manifest_repository_path
        )
        manifest_source = manifest_bytes.decode("utf-8", errors="strict")
    else:
        manifest_source = manifest_path.read_text(encoding="utf-8")
    data = yaml.safe_load(manifest_source)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    # Resolved once: the commit-existence and ancestry assertions below are
    # unevaluable without full history. See _is_shallow_clone.
    shallow = check_git and _is_shallow_clone(repo)
    if shallow and strict:
        raise ValueError("strict validation requires complete local Git history")
    if shallow:
        print(
            f"note: {manifest_path.name}: shallow clone -- skipping commit-history "
            "assertions (schema, paths and fields are still enforced)",
            file=sys.stderr,
        )
    entries = data.get("upstream_changes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest upstream_changes must be a non-empty list")
    source_revision_files: dict[str, str] = {}

    def revision_source(path: str) -> str:
        if path not in source_revision_files:
            source_revision_files[path] = _blob_text(
                repo, resolved_source_revision, path
            )
        return source_revision_files[path]

    coverage = data.get("coverage")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise ValueError("manifest coverage must be a mapping")
        base_commit = coverage.get("base_commit")
        if not isinstance(base_commit, str) or not _HEX40.fullmatch(base_commit):
            raise ValueError("coverage.base_commit must be exact 40-hex")
        exclusions = coverage.get("excluded_commits", [])
        if not isinstance(exclusions, list):
            raise ValueError("coverage.excluded_commits must be a list")
        excluded_shas: set[str] = set()
        for exclusion in exclusions:
            if not isinstance(exclusion, dict):
                raise ValueError("every excluded commit must be a mapping")
            sha = exclusion.get("commit")
            reason = exclusion.get("reason")
            if not isinstance(sha, str) or not _HEX40.fullmatch(sha):
                raise ValueError("excluded commit must be exact 40-hex")
            if sha in excluded_shas:
                raise ValueError(f"duplicate excluded commit: {sha}")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"excluded commit {sha} must have a non-empty reason")
            excluded_shas.add(sha)
        if check_git and not shallow:
            coverage_base = _resolve_commit(
                repo, coverage["base_commit"], "coverage base"
            )
            if not _is_ancestor(repo, coverage_base, resolved_source_revision):
                raise ValueError(
                    "coverage base is not an ancestor of source revision"
                )
            _validate_coverage_commits(
                coverage, repo, resolved_source_revision
            )
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("every upstream change must be a mapping")
        missing = sorted(_REQUIRED - set(entry))
        if missing:
            raise ValueError(f"entry is missing required fields: {', '.join(missing)}")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
            raise ValueError(f"duplicate or invalid customization id: {entry_id!r}")
        if len(entry_id) > _MAX_EVIDENCE_ID_LENGTH:
            raise ValueError(
                f"{entry_id[:32]!r}... id must be at most "
                f"{_MAX_EVIDENCE_ID_LENGTH} characters"
            )
        ids.add(entry_id)
        baseline = entry.get("last_verified_upstream")
        if not isinstance(baseline, str) or not _HEX40.fullmatch(baseline):
            raise ValueError(f"{entry_id}.last_verified_upstream must be exact 40-hex")
        for field in ("files", "tests"):
            values = entry.get(field)
            if not isinstance(values, list) or not values:
                raise ValueError(f"{entry_id}.{field} must be a non-empty list")
            for raw in values:
                if not isinstance(raw, str):
                    raise ValueError(f"{entry_id}.{field} paths must be strings")
                if len(raw.encode("utf-8")) > _MAX_REPOSITORY_PATH_BYTES:
                    raise ValueError(
                        f"{entry_id}.{field} path must be at most "
                        f"{_MAX_REPOSITORY_PATH_BYTES} bytes"
                    )
                try:
                    _repository_relative_path(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"{entry_id}.{field} {exc}"
                    ) from exc
                if strict:
                    _require_regular_file_at(repo, resolved_source_revision, raw)
                else:
                    path = _contained(repo, raw)
                    if not path.is_file():
                        raise ValueError(f"ledger path does not exist: {raw}")
        symbols = entry.get("owned_symbols")
        if not isinstance(symbols, list) or not symbols or not all(
            isinstance(value, str) and value for value in symbols
        ):
            raise ValueError(f"{entry_id}.owned_symbols must contain names")
        overlap_policy = entry.get("overlap_policy", "owned_symbol")
        if not isinstance(overlap_policy, str):
            raise ValueError(f"{entry_id}.overlap_policy must be a string")
        if overlap_policy not in _OVERLAP_POLICIES:
            raise ValueError(
                f"{entry_id}.overlap_policy must be owned_symbol or any_owned_file"
            )
        invariants = entry.get("owned_invariants", [])
        if not isinstance(invariants, list):
            raise ValueError(f"{entry_id}.owned_invariants must be a list")
        if len(invariants) > _MAX_OWNED_INVARIANTS:
            raise ValueError(
                f"{entry_id}.owned_invariants must be a list with at most "
                f"{_MAX_OWNED_INVARIANTS} items"
            )
        if not all(
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= _MAX_OWNED_INVARIANT_LENGTH
            for value in invariants
        ):
            raise ValueError(
                f"{entry_id}.owned_invariants must contain bounded non-empty prose"
            )
        strict_symbols = _strict_owned_symbols(entry)
        if "overlap_policy" in entry and len(strict_symbols) != len(symbols):
            phrase = next(symbol for symbol in symbols if symbol not in strict_symbols)
            raise ValueError(
                f"{entry_id}.owned_symbols must contain exact machine-locatable "
                f"identifiers; move prose to owned_invariants: {phrase!r}"
            )
        for field in ("expected_commit_subject", "merge_guidance", "removal_condition"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"{entry_id}.{field} must be non-empty")
        if check_git and not shallow:
            if not _git(repo, "cat-file", "-e", f"{baseline}^{{commit}}", check=False):
                # cat-file is quiet on success and failure, so inspect status separately.
                proc = subprocess.run(
                    ["git", "cat-file", "-e", f"{baseline}^{{commit}}"], cwd=repo
                )
                if proc.returncode:
                    raise ValueError(f"{entry_id} baseline is not a local commit")
            if not _is_ancestor(repo, baseline, resolved_source_revision):
                raise ValueError(
                    f"{entry_id} baseline is not an ancestor of source revision"
                )

    parser_requests = _collect_manifest_parser_requests(
        entries, repo, resolved_source_revision
    )
    parser_results = _NonPythonSymbolResolver().spans(parser_requests)
    parser_spans = {
        request.path: parser_results[request.request_id]
        for request in parser_requests
    }
    for entry in entries:
        entry_id = entry["id"]
        for symbol in _strict_owned_symbols(entry):
            found = False
            for raw in entry["files"]:
                if _parser_language(raw) is not None:
                    found = bool(parser_spans[raw][symbol])
                else:
                    found = _source_contains_symbol(
                        revision_source(raw), symbol, path=raw
                    )
                if found:
                    break
            if not found:
                raise ValueError(
                    f"{entry_id} owned symbol {symbol!r} does not exist in "
                    "declared files at source revision"
                )
    return data


def _changed_paths(repo: Path, diff_range: str) -> list[tuple[str, list[str]]]:
    rows = []
    for line in _git(repo, "diff", "--name-status", "-M", diff_range).splitlines():
        parts = line.split("\t")
        status = parts[0]
        rows.append((status, parts[1:]))
    return rows


def _existed_at(repo: Path, revision: str, path: str) -> bool:
    # Only an exact zero-entry tree lookup means "absent".  Object corruption,
    # unreadable repositories, and other Git execution failures remain errors.
    return _tree_entry(repo, revision, path) is not None


def _validate_coverage_commits(
    coverage: dict[str, Any], repo: Path, right: str, *, left: str | None = None
) -> list[str]:
    base = _resolve_commit(repo, left or coverage["base_commit"], "range left")
    right = _resolve_commit(repo, right, "range right")
    commits = _git(repo, "rev-list", "--reverse", f"{base}..{right}").splitlines()
    in_range = set(commits)
    excluded = {item["commit"] for item in coverage.get("excluded_commits", [])}
    return [commit for commit in commits if commit not in (excluded & in_range)]


def _coverage_changes(
    data: dict[str, Any], repo: Path, diff_range: str
) -> tuple[list[tuple[str, list[str]]], list[str]] | None:
    coverage = data.get("coverage")
    if coverage is None:
        return None
    left, right = _resolved_diff_endpoints(repo, diff_range)
    commits = _validate_coverage_commits(coverage, repo, right, left=left)
    changes: list[tuple[str, list[str]]] = []
    for commit in commits:
        changes.extend(_changed_paths(repo, f"{commit}^..{commit}"))
    return changes, commits


def validate_diff_coverage(data: dict[str, Any], repo: Path, diff_range: str) -> None:
    range_left, range_right = _resolved_diff_endpoints(repo, diff_range)
    resolved_range = f"{range_left}..{range_right}"
    covered = {
        path
        for entry in data["upstream_changes"]
        for field in ("files", "tests")
        for path in entry[field]
    }
    always_ignored_prefixes = (
        "docs/", "tests/", "skills/", "optional-skills/", "capabilities/", "brands/",
    )
    additive_prefixes = ("plugins/", "scripts/")
    scoped = _coverage_changes(data, repo, diff_range)
    changes = scoped[0] if scoped is not None else _changed_paths(repo, resolved_range)
    baseline = range_left
    missing: set[str] = set()
    for _status, paths in changes:
        for path in paths:
            name = Path(path).name
            is_colocated_test = (
                "/__tests__/" in f"/{path}"
                or ".test." in name
                or ".spec." in name
            )
            if (
                path in covered
                or path in _EPHEMERAL_COVERAGE_PATHS
                or path.startswith(always_ignored_prefixes)
                or (
                    path.startswith(additive_prefixes)
                    and not _existed_at(repo, baseline, path)
                )
                or is_colocated_test
            ):
                continue
            missing.add(path)
    if missing:
        raise ValueError(
            "upstream-owned files missing from customization ledger: "
            + ", ".join(sorted(missing))
        )
    changed = {path for _status, paths in changes for path in paths}
    dirty = {
        line[3:] for line in _git(repo, "status", "--porcelain").splitlines()
        if len(line) > 3
    }
    for entry in data["upstream_changes"]:
        touched = changed & set(entry["files"])
        if not touched or touched & dirty:
            # A planned boundary may be validated before its commit; the exact
            # subject becomes mandatory as soon as the files are clean.
            continue
        if scoped is None:
            subjects = _git(
                repo,
                "log",
                "--format=%s",
                resolved_range,
                "--",
                *sorted(touched),
            ).splitlines()
        else:
            subjects = [
                _git(repo, "show", "-s", "--format=%s", commit).strip()
                for commit in scoped[1]
                if set(
                    path
                    for _status, paths in _changed_paths(repo, f"{commit}^..{commit}")
                    for path in paths
                )
                & touched
            ]
        expected = entry["expected_commit_subject"]
        if expected not in subjects:
            raise ValueError(
                f"{entry['id']} is not isolated in expected commit subject: {expected}"
            )


class _PythonSymbolCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.spans: dict[str, list[tuple[int, int]]] = {}

    def _record(self, symbol: str, node: ast.AST) -> None:
        if not hasattr(node, "lineno"):
            return
        start = node.lineno
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min(
                [start, *(decorator.lineno for decorator in node.decorator_list)]
            )
        self.spans.setdefault(symbol, []).append(
            (start, getattr(node, "end_lineno", node.lineno))
        )

    def _record_scoped(self, name: str, node: ast.AST) -> None:
        self._record(name, node)
        if self.scope:
            self._record(".".join([*self.scope, name]), node)

    def _visit_definition(self, node: ast.AST, name: str) -> None:
        self._record_scoped(name, node)
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node, node.name)

    def visit_Name(self, node: ast.Name) -> None:
        self._record(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = [node.attr]
        value = node.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
            self._record(".".join(reversed(parts)), node)
            if value.id in {"self", "cls"} and self.scope:
                self._record(".".join([self.scope[0], *reversed(parts[:-1])]), node)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._record(node.arg, node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._record(node.value, node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assignment_target(target)
            if isinstance(target, ast.Name):
                for child in ast.walk(node.value):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        self._record(f"{target.id}.{child.value}", child)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assignment_target(node.target)
        self.generic_visit(node)

    def _record_assignment_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name) and self.scope:
            self._record(".".join([*self.scope, target.id]), target)


def _blank(result: list[str], source: str, start: int, end: int) -> None:
    for index in range(start, end):
        result[index] = "\n" if source[index] == "\n" else " "


def _powershell_without_comments(source: str) -> str:
    """Remove PowerShell comments using PowerShell's backtick escape rules."""
    result = list(source)
    index = 0
    mode = "code"
    while index < len(source):
        if mode in {"here_single", "here_double"}:
            marker = "'@" if mode == "here_single" else '"@'
            line_start = index == 0 or source[index - 1] == "\n"
            if line_start:
                marker_at = index
                while marker_at < len(source) and source[marker_at] in " \t":
                    marker_at += 1
                if source.startswith(marker, marker_at):
                    after = marker_at + len(marker)
                    line_end = source.find("\n", after)
                    line_end = len(source) if line_end < 0 else line_end
                    if not source[after:line_end].strip():
                        mode = "code"
                        index = after
                        continue
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if mode == "block":
            end = source.find("#>", index)
            end = len(source) if end < 0 else end + 2
            _blank(result, source, index, end)
            index = end
            mode = "code"
            continue
        char = source[index]
        if mode == "single":
            if source.startswith("''", index):
                index += 2
            else:
                if char == "'":
                    mode = "code"
                index += 1
            continue
        if mode == "double":
            if char == "`":
                index += min(2, len(source) - index)
            else:
                if char == '"':
                    mode = "code"
                index += 1
            continue
        if source.startswith("<#", index):
            mode = "block"
            continue
        if source.startswith("@'", index) or source.startswith('@"', index):
            line_end = source.find("\n", index + 2)
            line_end = len(source) if line_end < 0 else line_end
            if not source[index + 2 : line_end].strip():
                mode = "here_single" if source[index + 1] == "'" else "here_double"
                index += 2
            else:
                index += 1
        elif char == "#":
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            _blank(result, source, index, end)
            index = end
        elif char == "`":
            index += min(2, len(source) - index)
        elif char == "'":
            mode = "single"
            index += 1
        elif char == '"':
            mode = "double"
            index += 1
        else:
            index += 1
    return "".join(result)


def _shell_heredoc_at(line: str, index: int) -> tuple[int, str, bool] | None:
    if not line.startswith("<<", index) or line.startswith("<<<", index):
        return None
    cursor = index + 2
    strip_tabs = cursor < len(line) and line[cursor] == "-"
    if strip_tabs:
        cursor += 1
    while cursor < len(line) and line[cursor] in " \t":
        cursor += 1
    delimiter: list[str] = []
    quote = ""
    while cursor < len(line):
        char = line[cursor]
        if quote:
            if char == quote:
                quote = ""
                cursor += 1
            elif quote == '"' and char == "\\" and cursor + 1 < len(line):
                delimiter.append(line[cursor + 1])
                cursor += 2
            else:
                delimiter.append(char)
                cursor += 1
            continue
        if char in "'\"":
            quote = char
            cursor += 1
        elif char == "\\" and cursor + 1 < len(line):
            delimiter.append(line[cursor + 1])
            cursor += 2
        elif char.isspace() or char in ";|&()<>":
            break
        else:
            delimiter.append(char)
            cursor += 1
    if quote or not delimiter:
        return None
    return cursor, "".join(delimiter), strip_tabs


def _shell_without_comments(source: str) -> str:
    """Remove shell comments while retaining parameter trims and here-doc bodies."""
    result = list(source)
    stack: list[tuple[str, str | None]] = [("code", None)]
    heredocs: list[tuple[str, bool]] = []
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        if heredocs:
            delimiter, strip_tabs = heredocs[0]
            candidate = body.lstrip("\t") if strip_tabs else body
            if candidate == delimiter:
                heredocs.pop(0)
            offset += len(line)
            continue
        index = 0
        comment_at: int | None = None
        while index < len(body):
            mode, closer = stack[-1]
            char = body[index]
            if mode == "single":
                if char == "'":
                    stack.pop()
                index += 1
                continue
            if mode == "double":
                if char == "\\":
                    index += min(2, len(body) - index)
                elif char == '"':
                    stack.pop()
                    index += 1
                elif char == "`":
                    stack.append(("code", "`"))
                    index += 1
                else:
                    index += 1
                continue
            if closer is not None and char == closer:
                stack.pop()
                index += 1
            elif char == "\\":
                index += min(2, len(body) - index)
            elif char == "'":
                stack.append(("single", None))
                index += 1
            elif char == '"':
                stack.append(("double", None))
                index += 1
            elif char == "`":
                stack.append(("code", "`"))
                index += 1
            elif char == "#" and (
                index == 0 or body[index - 1].isspace() or body[index - 1] in ";|&()"
            ):
                comment_at = index
                break
            elif body.startswith("<<", index):
                parsed = _shell_heredoc_at(body, index)
                if parsed is None:
                    index += 1
                else:
                    index, delimiter, strip_tabs = parsed
                    heredocs.append((delimiter, strip_tabs))
            else:
                index += 1
        if comment_at is not None:
            _blank(result, source, offset + comment_at, offset + len(body))
        offset += len(line)
    return "".join(result)


def _css_without_comments(source: str) -> str:
    result = list(source)
    index = 0
    quote = ""
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += min(2, len(source) - index)
            else:
                if char == quote:
                    quote = ""
                index += 1
        elif char in {"'", '"'}:
            quote = char
            index += 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            _blank(result, source, index, end)
            index = end
        else:
            index += 1
    return "".join(result)


def _toml_without_comments(source: str) -> str:
    result = list(source)
    index = 0
    mode = "code"
    while index < len(source):
        if mode == "code":
            if source.startswith("'''", index):
                mode = "multiline_literal"
                index += 3
            elif source.startswith('\"\"\"', index):
                mode = "multiline_basic"
                index += 3
            elif source[index] == "'":
                mode = "literal"
                index += 1
            elif source[index] == '"':
                mode = "basic"
                index += 1
            elif source[index] == "#":
                end = source.find("\n", index)
                end = len(source) if end < 0 else end
                _blank(result, source, index, end)
                index = end
            else:
                index += 1
        elif mode == "multiline_literal":
            if source.startswith("'''", index):
                while index < len(source) and source[index] == "'":
                    index += 1
                mode = "code"
            else:
                index += 1
        elif mode == "multiline_basic":
            if source.startswith('\"\"\"', index):
                while index < len(source) and source[index] == '"':
                    index += 1
                mode = "code"
            elif source[index] == "\\":
                index += min(2, len(source) - index)
            else:
                index += 1
        elif mode == "literal":
            if source[index] == "'":
                mode = "code"
            index += 1
        else:
            if source[index] == "\\":
                index += min(2, len(source) - index)
            else:
                if source[index] == '"':
                    mode = "code"
                index += 1
    return "".join(result)


def _semantic_strings(value: Any) -> list[str]:
    strings: list[str] = []
    pending = [value]
    seen_containers: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            strings.append(item)
            continue
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            for key, child in item.items():
                if isinstance(key, str):
                    strings.append(key)
                pending.append(child)
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            pending.extend(item)
    return strings


def _line_span_for_offsets(source: str, start: int, end: int) -> tuple[int, int]:
    return (
        source.count("\n", 0, start) + 1,
        source.count("\n", 0, end) + 1,
    )


def _json_string_token_offsets(source: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    index = 0
    while index < len(source):
        if source[index] != '"':
            index += 1
            continue
        start = index
        index += 1
        while index < len(source):
            if source[index] == "\\":
                index += min(2, len(source) - index)
            elif source[index] == '"':
                index += 1
                break
            else:
                index += 1
        decoded = json.loads(source[start:index])
        tokens.append((decoded, start, index))
    return tokens


def _toml_string_end(source: str, start: int) -> int:
    quote = source[start]
    multiline = source.startswith(quote * 3, start)
    index = start + (3 if multiline else 1)
    while index < len(source):
        if quote == '"' and source[index] == "\\":
            index += min(2, len(source) - index)
            continue
        if multiline and source.startswith(quote * 3, index):
            while index < len(source) and source[index] == quote:
                index += 1
            return index
        if not multiline and source[index] == quote:
            return index + 1
        index += 1
    return len(source)


def _toml_string_token_offsets(source: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    index = 0
    while index < len(source):
        if source[index] == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source[index] not in "'\"":
            index += 1
            continue
        start = index
        index = _toml_string_end(source, start)
        token = source[start:index]
        decoded = tomllib.loads(f"value = {token}\n")["value"]
        tokens.append((decoded, start, index))
    return tokens


def _toml_bare_key_token_offsets(source: str) -> list[tuple[str, int, int]]:
    """Locate TOML bare keys without making scalar values searchable."""
    searchable = list(_toml_without_comments(source))
    index = 0
    while index < len(source):
        if searchable[index] not in "'\"":
            index += 1
            continue
        end = _toml_string_end(source, index)
        _blank(searchable, source, index, end)
        index = end

    tokens: list[tuple[str, int, int]] = []
    offset = 0
    key_expression = r"[A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*"
    for line in "".join(searchable).splitlines(keepends=True):
        body = line.rstrip("\r\n")
        regions: list[tuple[int, int]] = []
        table = re.match(
            rf"^[ \t]*\[\[?[ \t]*({key_expression})[ \t]*\]\]?[ \t]*$",
            body,
        )
        if table:
            regions.append(table.span(1))
        for assignment in re.finditer(
            rf"(?:^|[{{,]])[ \t]*({key_expression})[ \t]*=",
            body,
        ):
            regions.append(assignment.span(1))
        for start, end in regions:
            for token in re.finditer(r"[A-Za-z0-9_-]+", body[start:end]):
                absolute = offset + start + token.start()
                tokens.append((token.group(), absolute, absolute + len(token.group())))
        offset += len(line)
    return tokens


def _exact_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])")


def _powershell_pattern(symbol: str) -> re.Pattern[str]:
    """Exact-match pattern for PowerShell sources.

    PowerShell spells a variable reference as ``$Name`` — the sigil belongs to
    the reference syntax, not the identifier — so a preceding ``$`` must not
    block a match the way it does for JS-style ``$name`` identifiers. Without
    this, an owned symbol like ``InheritedPythonEnvVars`` (only ever written as
    ``$InheritedPythonEnvVars`` in .ps1) can never be found and manifest
    validation fails against a tree that plainly contains it.
    """
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_$])")


def _parse_error(path: str, kind: str, exc: Exception) -> ValueError:
    detail = " ".join(str(exc).split())[:240]
    return ValueError(f"cannot parse {path} as {kind}: {detail}")


def _structured_offset_spans(
    source: str,
    symbols: list[str],
    *,
    path: str,
    suffix: str,
) -> dict[str, list[tuple[int, int]]]:
    """Find JSON/TOML owned scalar spans by exact source offsets."""
    _validate_scannable_source(source, path)
    spans = {symbol: [] for symbol in symbols}
    if suffix == ".toml":
        try:
            parsed = tomllib.loads(source)
        except tomllib.TOMLDecodeError as exc:
            raise _parse_error(path, "TOML", exc) from exc
        decoded_tokens = [
            *_toml_string_token_offsets(source),
            *_toml_bare_key_token_offsets(source),
        ]
    elif suffix == ".json":
        try:
            parsed = json.loads(source)
        except json.JSONDecodeError as exc:
            raise _parse_error(path, "JSON", exc) from exc
        decoded_tokens = _json_string_token_offsets(source)
    else:
        raise AssertionError(f"unsupported structured suffix: {suffix}")
    semantic = _semantic_strings(parsed)
    for symbol in symbols:
        pattern = _exact_pattern(symbol)
        if not any(pattern.search(value) for value in semantic):
            continue
        for value, start, end in decoded_tokens:
            if pattern.search(value):
                spans[symbol].append((start, end))
    return spans


def _structured_spans(
    source: str,
    symbols: list[str],
    *,
    path: str,
    suffix: str,
) -> dict[str, list[tuple[int, int]]]:
    spans = {symbol: [] for symbol in symbols}
    if suffix in {".yaml", ".yml"}:
        try:
            documents = list(yaml.safe_load_all(source))
            nodes = list(yaml.compose_all(source, Loader=yaml.SafeLoader))
        except yaml.YAMLError as exc:
            raise _parse_error(path, "YAML", exc) from exc
        semantic = _semantic_strings(documents)
        for symbol in symbols:
            pattern = _exact_pattern(symbol)
            if not any(pattern.search(value) for value in semantic):
                continue
            for document in nodes:
                if document is None:
                    continue
                for node in _walk_yaml_nodes(document):
                    if (
                        isinstance(node, yaml.nodes.ScalarNode)
                        and node.tag == "tag:yaml.org,2002:str"
                        and pattern.search(node.value)
                    ):
                        end_line = (
                            node.end_mark.line + 1
                            if node.end_mark.column
                            else max(node.start_mark.line + 1, node.end_mark.line)
                        )
                        spans[symbol].append(
                            (node.start_mark.line + 1, end_line)
                        )
        return spans
    return {
        symbol: [
            _line_span_for_offsets(source, start, end)
            for start, end in offsets
        ]
        for symbol, offsets in _structured_offset_spans(
            source,
            symbols,
            path=path,
            suffix=suffix,
        ).items()
    }


def _walk_yaml_nodes(node: yaml.nodes.Node) -> list[yaml.nodes.Node]:
    nodes: list[yaml.nodes.Node] = []
    pending = [node]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        nodes.append(current)
        if isinstance(current, yaml.nodes.MappingNode):
            for key, value in reversed(current.value):
                pending.extend((value, key))
        elif isinstance(current, yaml.nodes.SequenceNode):
            pending.extend(reversed(current.value))
    return nodes


def _searchable_non_python(source: str, suffix: str) -> str:
    if suffix in {".ps1", ".psm1", ".psd1"}:
        return _powershell_without_comments(source)
    if suffix in {".sh", ".bash"}:
        return _shell_without_comments(source)
    if suffix in {".css", ".scss", ".less"}:
        return _css_without_comments(source)
    return source


def _validate_scannable_source(source: str, path: str) -> None:
    if len(source) > _MAX_SCANNED_SOURCE_BYTES or len(
        source.encode("utf-8", errors="replace")
    ) > _MAX_SCANNED_SOURCE_BYTES:
        raise ValueError(
            f"cannot scan {path}: source exceeds {_MAX_SCANNED_SOURCE_BYTES} bytes"
        )


def _symbol_spans(
    source: str,
    symbols: list[str],
    *,
    path: str,
) -> dict[str, list[tuple[int, int]]]:
    spans = {symbol: [] for symbol in symbols}
    _validate_scannable_source(source, path)
    suffix = Path(path).suffix.lower()
    if _parser_language(path) is not None:
        raise ValueError("non-Python parser resolution is required")
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return spans
        collector = _PythonSymbolCollector()
        collector.visit(tree)
        for symbol in symbols:
            spans[symbol].extend(collector.spans.get(symbol, []))
        return spans
    if suffix in {".yaml", ".yml", ".toml", ".json"}:
        return _structured_spans(source, symbols, path=path, suffix=suffix)
    searchable = _searchable_non_python(source, suffix)
    for symbol in symbols:
        if "." in symbol:
            owner, member = symbol.split(".", 1)
            declaration = re.search(
                rf"\b(?:interface|class|type|namespace)\s+{re.escape(owner)}\b[^{{]{{0,512}}\{{",
                searchable,
            )
            if declaration:
                depth = 1
                cursor = declaration.end()
                while cursor < len(searchable) and depth:
                    depth += (searchable[cursor] == "{") - (searchable[cursor] == "}")
                    cursor += 1
                body = searchable[declaration.end() : cursor - 1]
                member_match = re.search(
                    rf"(?<![A-Za-z0-9_$]){re.escape(member)}(?![A-Za-z0-9_$])",
                    body,
                )
                if member_match:
                    offset = declaration.end() + member_match.start()
                    line = searchable.count("\n", 0, offset) + 1
                    spans[symbol].append((line, line))
        if suffix in {".ps1", ".psm1", ".psd1"}:
            pattern = _powershell_pattern(symbol)
        else:
            pattern = _exact_pattern(symbol)
        for match in pattern.finditer(searchable):
            line = searchable.count("\n", 0, match.start()) + 1
            spans[symbol].append((line, line))
    return spans


def _source_contains_symbol(source: str, symbol: str, *, path: str) -> bool:
    return bool(_symbol_spans(source, [symbol], path=path)[symbol])


def _strict_owned_symbols(entry: dict[str, Any]) -> list[str]:
    return [
        symbol
        for symbol in entry["owned_symbols"]
        if len(symbol.encode("utf-8")) <= _MAX_OWNED_SYMBOL_BYTES
        and bool(_MACHINE_SYMBOL.fullmatch(symbol))
    ]


def _changed_line_numbers(diff: str) -> tuple[set[int], set[int]]:
    old_lines: set[int] = set()
    new_lines: set[int] = set()
    old_no = new_no = 0
    for line in diff.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(2))
        elif line.startswith("-") and not line.startswith("---"):
            old_lines.add(old_no)
            old_no += 1
        elif line.startswith("+") and not line.startswith("+++"):
            new_lines.add(new_no)
            new_no += 1
        elif not line.startswith("\\"):
            old_no += 1
            new_no += 1
    return old_lines, new_lines


def _changed_character_ranges(
    old_source: str,
    new_source: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return bounded exact-source ranges changed on each side of a diff."""
    matcher = difflib.SequenceMatcher(a=old_source, b=new_source)
    old_ranges: list[tuple[int, int]] = []
    new_ranges: list[tuple[int, int]] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag != "equal":
            old_ranges.append((old_start, old_end))
            new_ranges.append((new_start, new_end))
    return old_ranges, new_ranges


_WORD_DIFF_HUNK = re.compile(
    rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)
_HEX_BYTE_RUN = re.compile(rb"(?:[0-9a-f]{2})+")


def _byte_line_boundaries(source: bytes) -> list[int]:
    boundaries = [0]
    boundaries.extend(
        offset + 1 for offset, value in enumerate(source) if value == 0x0A
    )
    if boundaries[-1] != len(source):
        boundaries.append(len(source))
    return boundaries


def _hunk_byte_bounds(
    boundaries: Sequence[int], line_start: int, line_count: int
) -> tuple[int, int]:
    line_total = len(boundaries) - 1
    if line_count == 0:
        if not 0 <= line_start <= line_total:
            raise ValueError("malformed Git character diff")
        offset = boundaries[line_start]
        return offset, offset
    if line_start < 1:
        raise ValueError("malformed Git character diff")
    first = line_start - 1
    after = first + line_count
    if first >= line_total or after > line_total:
        raise ValueError("malformed Git character diff")
    return boundaries[first], boundaries[after]


def _coalesce_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _git_changed_byte_ranges(
    repo: Path,
    left: str,
    right: str,
    path: str,
    old_source: bytes,
    new_source: bytes,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return exact changed byte ranges from Git's bounded porcelain protocol."""
    _validated_parser_source(old_source)
    _validated_parser_source(new_source)
    if old_source == new_source:
        return [], []
    old_encoded = old_source.hex().encode("ascii") + b"\n"
    new_encoded = new_source.hex().encode("ascii") + b"\n"
    with tempfile.TemporaryDirectory(prefix="hermes-character-diff-") as temp_dir:
        old_path = Path(temp_dir) / "old.hex"
        new_path = Path(temp_dir) / "new.hex"
        old_path.write_bytes(old_encoded)
        new_path.write_bytes(new_encoded)
        argv = [
            "git",
            "diff",
            "--no-index",
            "--no-ext-diff",
            "--no-color",
            "--text",
            "--unified=0",
            "--word-diff=porcelain",
            "--word-diff-regex=[0-9a-f]{2}",
            str(old_path),
            str(new_path),
        ]
        try:
            diff = _run_bounded_capture(
                argv,
                cwd=repo,
                timeout_seconds=60,
                output_limit_bytes=64 * 1024 * 1024,
            )
        except _BoundedCaptureFailure as exc:
            raise ValueError(f"Git character diff {exc.code}") from exc
    if diff.returncode not in {0, 1}:
        raise ValueError("Git character diff process failure")
    if diff.returncode == 0:
        if old_source != new_source or diff.stdout:
            raise ValueError("malformed Git character diff")
        return [], []
    old_boundaries = _byte_line_boundaries(old_encoded)
    new_boundaries = _byte_line_boundaries(new_encoded)

    old_ranges: list[tuple[int, int]] = []
    new_ranges: list[tuple[int, int]] = []
    old_hunks: list[tuple[int, int]] = []
    new_hunks: list[tuple[int, int]] = []
    old_cursor = new_cursor = 0
    old_end = new_end = 0
    in_hunk = False
    saw_hunk = False
    saw_changed_token_run = False
    newline_sides: set[str] = set()

    def finish_hunk() -> None:
        if in_hunk and (old_cursor != old_end or new_cursor != new_end):
            raise ValueError("malformed Git character diff")

    records = diff.stdout.split(b"\n")
    if records and records[-1] == b"":
        records.pop()
    for record in records:
        match = _WORD_DIFF_HUNK.fullmatch(record)
        if match:
            finish_hunk()
            old_start = int(match.group(1))
            old_count = int(match.group(2) or b"1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or b"1")
            old_cursor, old_end = _hunk_byte_bounds(
                old_boundaries, old_start, old_count
            )
            new_cursor, new_end = _hunk_byte_bounds(
                new_boundaries, new_start, new_count
            )
            old_hunks.append((old_cursor, old_end))
            new_hunks.append((new_cursor, new_end))
            in_hunk = True
            saw_hunk = True
            newline_sides.clear()
            continue
        if not in_hunk:
            continue
        if record == b"~":
            if not newline_sides:
                raise ValueError("malformed Git character diff")
            advanced: set[str] = set()
            if old_cursor < old_end and old_encoded[old_cursor:old_cursor + 1] == b"\n":
                advanced.add("old")
            if new_cursor < new_end and new_encoded[new_cursor:new_cursor + 1] == b"\n":
                advanced.add("new")
            if not advanced:
                raise ValueError("malformed Git character diff")
            if "old" in advanced:
                old_cursor += 1
            if "new" in advanced:
                new_cursor += 1
            newline_sides.clear()
            continue
        marker = record[:1]
        if marker not in {b" ", b"-", b"+"}:
            raise ValueError("malformed Git character diff")
        payload = record[1:]
        if not _HEX_BYTE_RUN.fullmatch(payload):
            raise ValueError("malformed Git character diff")
        if marker in {b"-", b"+"}:
            saw_changed_token_run = True
        if marker in {b" ", b"-"}:
            newline_sides.add("old")
            end = old_cursor + len(payload)
            if end > old_end or old_encoded[old_cursor:end] != payload:
                raise ValueError("malformed Git character diff")
            if marker == b"-":
                old_ranges.append((old_cursor // 2, end // 2))
            old_cursor = end
        if marker in {b" ", b"+"}:
            newline_sides.add("new")
            end = new_cursor + len(payload)
            if end > new_end or new_encoded[new_cursor:end] != payload:
                raise ValueError("malformed Git character diff")
            if marker == b"+":
                new_ranges.append((new_cursor // 2, end // 2))
            new_cursor = end
    finish_hunk()
    exact_old_coverage = (
        bool(old_hunks)
        and old_hunks[0][0] == 0
        and old_hunks[-1][1] == len(old_encoded)
        and all(
            start < end and (index == 0 or old_hunks[index - 1][1] == start)
            for index, (start, end) in enumerate(old_hunks)
        )
    )
    exact_new_coverage = (
        bool(new_hunks)
        and new_hunks[0][0] == 0
        and new_hunks[-1][1] == len(new_encoded)
        and all(
            start < end and (index == 0 or new_hunks[index - 1][1] == start)
            for index, (start, end) in enumerate(new_hunks)
        )
    )
    if (
        not saw_hunk
        or not saw_changed_token_run
        or not exact_old_coverage
        or not exact_new_coverage
    ):
        raise ValueError("malformed Git character diff")
    return _coalesce_ranges(old_ranges), _coalesce_ranges(new_ranges)


def _ranges_overlap(
    spans: list[tuple[int, int]],
    changed_ranges: list[tuple[int, int]],
) -> bool:
    return any(
        start < changed_end and changed_start < end
        for start, end in spans
        for changed_start, changed_end in changed_ranges
    )


def _blob_bytes_or_empty(repo: Path, revision: str, path: str) -> tuple[str, bytes]:
    if not _existed_at(repo, revision, path):
        return f"absent:{revision}:{path}", b""
    return _blob_bytes(repo, revision, path)


def _collect_overlap_parser_requests(
    entries: Sequence[dict[str, Any]],
    repo: Path,
    left: str,
    right: str,
    changed_paths: set[str],
) -> tuple[
    list[_ParserRequest],
    dict[tuple[str, str], bytes],
    dict[tuple[str, str], str],
]:
    symbols_by_path: dict[str, set[str]] = {}
    for entry in entries:
        entry_symbols = _strict_owned_symbols(entry)
        for path in sorted(set(entry["files"]) & changed_paths):
            if _parser_language(path) is not None:
                symbols_by_path.setdefault(path, set()).update(entry_symbols)
    requests: list[_ParserRequest] = []
    sources: dict[tuple[str, str], bytes] = {}
    request_ids: dict[tuple[str, str], str] = {}
    request_index = 0
    for path in sorted(symbols_by_path):
        language = _parser_language(path)
        assert language is not None
        for revision in (left, right):
            blob_oid, source = _blob_bytes_or_empty(repo, revision, path)
            sources[(revision, path)] = source
            request_id = f"request-{request_index:08x}"
            request_ids[(revision, path)] = request_id
            requests.append(
                _ParserRequest(
                    request_id=request_id,
                    path=path,
                    language=language,
                    blob_oid=blob_oid,
                    source=source,
                    symbols=tuple(sorted(symbols_by_path[path])),
                )
            )
            request_index += 1
    return requests, sources, request_ids


def _owned_symbol_hits(
    entry: dict[str, Any],
    repo: Path,
    left: str,
    right: str,
    paths: list[str],
    *,
    parser_spans: dict[str, dict[str, list[tuple[int, int]]]],
    parser_changed_ranges: dict[
        str, tuple[list[tuple[int, int]], list[tuple[int, int]]]
    ],
) -> list[str]:
    hits: set[str] = set()
    symbols = _strict_owned_symbols(entry)
    for path in paths:
        if _parser_language(path) is not None:
            old_ranges, new_ranges = parser_changed_ranges[path]
            old_spans = parser_spans[f"{left}:{path}"]
            new_spans = parser_spans[f"{right}:{path}"]
            for symbol in symbols:
                if _ranges_overlap(old_spans[symbol], old_ranges) or _ranges_overlap(
                    new_spans[symbol], new_ranges
                ):
                    hits.add(symbol)
            continue
        diff = _git(repo, "diff", "--unified=0", f"{left}..{right}", "--", path)
        old_changed, new_changed = _changed_line_numbers(diff)
        new_source = _blob_text(repo, right, path)
        old_source = _blob_text(repo, left, path)
        suffix = Path(path).suffix.lower()
        if suffix in {".json", ".toml"}:
            _validate_scannable_source(old_source, path)
            _validate_scannable_source(new_source, path)
            old_ranges, new_ranges = _changed_character_ranges(
                old_source,
                new_source,
            )
            old_spans = _structured_offset_spans(
                old_source,
                symbols,
                path=path,
                suffix=suffix,
            )
            new_spans = _structured_offset_spans(
                new_source,
                symbols,
                path=path,
                suffix=suffix,
            )
            for symbol in symbols:
                if _ranges_overlap(old_spans[symbol], old_ranges) or _ranges_overlap(
                    new_spans[symbol], new_ranges
                ):
                    hits.add(symbol)
            continue
        for symbol, spans in _symbol_spans(new_source, symbols, path=path).items():
            if any(any(start <= line <= end for line in new_changed) for start, end in spans):
                hits.add(symbol)
        for symbol, spans in _symbol_spans(old_source, symbols, path=path).items():
            if any(any(start <= line <= end for line in old_changed) for start, end in spans):
                hits.add(symbol)
    return sorted(hits)


def classify_upstream_overlaps(
    entries: Sequence[dict[str, Any]],
    repo: Path,
    diff_range: str,
    *,
    resolver: _NonPythonSymbolResolver | None = None,
) -> list[dict[str, Any]]:
    left, right = _resolved_diff_endpoints(repo, diff_range)
    changes = _changed_paths(repo, f"{left}..{right}")
    changed = {path for _status, paths in changes for path in paths}
    ledger_owned_paths = {
        path
        for entry in entries
        for path in entry["files"]
    }
    candidate_paths = changed & ledger_owned_paths
    parser_requests, parser_sources, parser_request_ids = _collect_overlap_parser_requests(
        entries, repo, left, right, candidate_paths
    )
    parser_results = (resolver or _NonPythonSymbolResolver()).spans(parser_requests)
    parser_spans = {
        f"{revision}:{path}": parser_results[request_id]
        for (revision, path), request_id in parser_request_ids.items()
    }
    parser_changed_ranges = {
        path: _git_changed_byte_ranges(
            repo,
            left,
            right,
            path,
            parser_sources[(left, path)],
            parser_sources[(right, path)],
        )
        for path in sorted(candidate_paths)
        if _parser_language(path) is not None
    }

    overlaps: list[dict[str, Any]] = []
    for entry in entries:
        owned_files = set(entry["files"])
        same_files = sorted(changed & owned_files)
        symbols = _strict_owned_symbols(entry)
        owned_hits = _owned_symbol_hits(
            entry,
            repo,
            left,
            right,
            same_files,
            parser_spans=parser_spans,
            parser_changed_ranges=parser_changed_ranges,
        )
        if owned_hits:
            classification = "owned_symbol"
            rationale = f"owned symbols changed: {', '.join(owned_hits)}"
        elif same_files:
            classification = "same_file"
            rationale = f"same ledger-owned file changed: {', '.join(same_files)}"
        else:
            classification = "none"
            rationale = "no ledger-owned file overlap detected"
        overlaps.append({
            "id": entry["id"],
            "change_class": entry["change_class"],
            "files": entry["files"],
            "owned_symbols": symbols,
            "owned_invariants": [
                *entry.get("owned_invariants", []),
                *[
                    symbol
                    for symbol in entry["owned_symbols"]
                    if symbol not in symbols
                ],
            ],
            "overlap_policy": entry.get("overlap_policy", "owned_symbol"),
            "expected_commit_subject": entry["expected_commit_subject"],
            "classification": classification,
            "rationale": rationale,
            "merge_guidance": entry["merge_guidance"],
            "removal_condition": entry["removal_condition"],
            "tests": entry["tests"],
            "decision_required": bool(
                classification == "owned_symbol"
                or (
                    classification == "same_file"
                    and entry.get("overlap_policy", "owned_symbol")
                    == "any_owned_file"
                )
            ),
            "acknowledged": False,
        })
    return overlaps


def classify_upstream_overlap(
    entry: dict[str, Any], repo: Path, diff_range: str
) -> dict[str, Any]:
    return classify_upstream_overlaps([entry], repo, diff_range)[0]


def _set_verified_upstream(path: Path, data: dict[str, Any], repo: Path, sha: str) -> None:
    if not _HEX40.fullmatch(sha):
        raise ValueError("--set-verified-upstream requires exact 40-hex")
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode:
        raise ValueError("verified upstream is not a local commit")
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo).returncode:
        raise ValueError("verified upstream is not an ancestor of HEAD")
    for entry in data["upstream_changes"]:
        entry["last_verified_upstream"] = sha
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_json_atomically(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_chmod = getattr(os, "fchmod", None)
            if descriptor_chmod is not None:
                descriptor_chmod(stream.fileno(), 0o600)
            else:
                os.chmod(temp_path, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/upstream-customizations/workflow-orchestration.yaml"),
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--diff")
    parser.add_argument("--upstream-diff")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--set-verified-upstream")
    parser.add_argument("--print-verified-upstream", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    try:
        if args.set_verified_upstream:
            checkout_data = yaml.safe_load(
                args.manifest.read_text(encoding="utf-8")
            )
            _set_verified_upstream(
                args.manifest, checkout_data, repo, args.set_verified_upstream
            )
        data = load_and_validate_manifest(
            args.manifest,
            repo,
            source_revision=args.base_ref,
            strict=args.strict,
        )
        if args.print_verified_upstream:
            baselines = {
                entry["last_verified_upstream"]
                for entry in data["upstream_changes"]
            }
            if len(baselines) != 1:
                raise ValueError(
                    "ledger entries do not share one verified upstream baseline"
                )
            print(next(iter(baselines)))
            return 0
        if args.diff:
            validate_diff_coverage(data, repo, args.diff)
        if args.upstream_diff:
            previous: dict[str, Any] = {}
            if args.report and args.report.exists():
                try:
                    previous = json.loads(args.report.read_text(encoding="utf-8"))
                except Exception:
                    previous = {}
            old_ack = {
                item.get("id"): bool(item.get("acknowledged"))
                for item in previous.get("overlaps", [])
                if isinstance(item, dict)
            }
            overlaps = classify_upstream_overlaps(
                data["upstream_changes"], repo, args.upstream_diff
            )
            for item in overlaps:
                item["acknowledged"] = old_ack.get(item["id"], False)
            report = {"schema_version": 1, "range": args.upstream_diff, "overlaps": overlaps}
            if args.report:
                _write_json_atomically(args.report, report)
            else:
                print(json.dumps(report, indent=2))
            if any(item["decision_required"] for item in overlaps):
                return 2
    except ValueError as exc:
        print(f"customization ledger error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
