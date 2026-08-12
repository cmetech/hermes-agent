"""Bounded, race-resistant YAML and JSON mapping acquisition.

This module is deliberately dependency-light so catalog generation and the
repository manifest linter can share exactly one static ingestion boundary.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import yaml
from yaml.composer import ComposerError
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class SourceErrorCode(StrEnum):
    VALIDATION = "validation"
    MISSING_SOURCE = "missing_source"
    UNSAFE_SOURCE = "unsafe_source"
    SAFE_OPEN_UNAVAILABLE = "safe_open_unavailable"
    IO_ERROR = "io_error"
    BYTE_LIMIT = "byte_limit"
    INVALID_YAML = "invalid_yaml"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    MERGE_KEY = "merge_key"
    PARSER_LIMIT = "parser_limit"
    KEY_TYPE = "key_type"
    CYCLE = "cycle"
    STRUCTURE_LIMIT = "structure_limit"


class SourceError(ValueError):
    """A fixed, structured source error compatible with legacy ValueError use."""

    def __init__(
        self,
        message: str,
        *,
        code: SourceErrorCode = SourceErrorCode.VALIDATION,
        label: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.label = label


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceContract:
    label: str
    max_bytes: int
    max_graph_entries: int = 2_048
    max_depth: int = 24
    max_aliases: int | None = None
    optional: bool = False


WORKFLOW_SIDECAR_CONTRACT = SourceContract(
    label="workflow sidecar",
    max_bytes=65_536,
    max_aliases=128,
    optional=True,
)
WORKFLOW_METADATA_CONTRACT = SourceContract(
    label="workflow metadata",
    max_bytes=512 * 1_024,
    max_aliases=128,
)
CONFIG_SCHEMA_CONTRACT = SourceContract(
    label="plugin config schema",
    max_bytes=512 * 1_024,
)
JSON_MAX_NUMBER_CHARS = 128


_MESSAGES: dict[str, dict[SourceErrorCode, str]] = {
    "workflow sidecar": {
        SourceErrorCode.MISSING_SOURCE: "workflow sidecar is missing",
        SourceErrorCode.UNSAFE_SOURCE: "workflow sidecar is not a safe regular file",
        SourceErrorCode.SAFE_OPEN_UNAVAILABLE: "workflow sidecar is not a safe regular file",
        SourceErrorCode.IO_ERROR: "workflow sidecar is not a safe regular file",
        SourceErrorCode.BYTE_LIMIT: "workflow sidecar exceeds safe byte limit",
        SourceErrorCode.INVALID_YAML: "workflow sidecar is not valid bounded YAML",
        SourceErrorCode.DUPLICATE_KEY: "workflow sidecar contains duplicate mapping key",
        SourceErrorCode.MERGE_KEY: "workflow sidecar YAML merge keys are not supported",
        SourceErrorCode.PARSER_LIMIT: "workflow sidecar exceeds safe YAML composition limits",
        SourceErrorCode.KEY_TYPE: "workflow sidecar field names must be strings",
        SourceErrorCode.CYCLE: "workflow sidecar structure must not contain cycles",
        SourceErrorCode.STRUCTURE_LIMIT: "workflow sidecar exceeds safe structure limits",
    },
    "workflow metadata": {
        SourceErrorCode.MISSING_SOURCE: "workflow metadata is missing",
        SourceErrorCode.UNSAFE_SOURCE: "workflow metadata could not be read safely",
        SourceErrorCode.SAFE_OPEN_UNAVAILABLE: "workflow metadata could not be read safely",
        SourceErrorCode.IO_ERROR: "workflow metadata could not be read safely",
        SourceErrorCode.BYTE_LIMIT: "workflow metadata exceeds safe byte limit",
        SourceErrorCode.INVALID_YAML: "workflow metadata is not valid bounded YAML",
        SourceErrorCode.DUPLICATE_KEY: "workflow metadata contains duplicate mapping key",
        SourceErrorCode.MERGE_KEY: "workflow metadata YAML merge keys are not supported",
        SourceErrorCode.PARSER_LIMIT: "workflow metadata exceeds safe YAML composition limits",
        SourceErrorCode.KEY_TYPE: "workflow metadata field names must be strings",
        SourceErrorCode.CYCLE: "workflow metadata structure must not contain cycles",
        SourceErrorCode.STRUCTURE_LIMIT: "workflow metadata exceeds safe structure limits",
    },
    "plugin config schema": {
        SourceErrorCode.MISSING_SOURCE: "plugin config schema is missing",
        SourceErrorCode.UNSAFE_SOURCE: "plugin config schema could not be read safely",
        SourceErrorCode.SAFE_OPEN_UNAVAILABLE: "plugin config schema could not be read safely",
        SourceErrorCode.IO_ERROR: "plugin config schema could not be read safely",
        SourceErrorCode.BYTE_LIMIT: "plugin config schema exceeds safe byte limit",
        SourceErrorCode.INVALID_JSON: "plugin config schema is not valid bounded JSON",
        SourceErrorCode.DUPLICATE_KEY: "plugin config schema contains duplicate mapping key",
        SourceErrorCode.PARSER_LIMIT: "plugin config schema exceeds safe JSON parsing limits",
        SourceErrorCode.KEY_TYPE: "plugin config schema field names must be strings",
        SourceErrorCode.CYCLE: "plugin config schema structure must not contain cycles",
        SourceErrorCode.STRUCTURE_LIMIT: "plugin config schema exceeds safe structure limits",
    },
}


def _source_error(contract: SourceContract, code: SourceErrorCode) -> SourceError:
    message = _MESSAGES.get(contract.label, {}).get(
        code, f"{contract.label} failed bounded validation"
    )
    return SourceError(message, code=code, label=contract.label)


_POSIX_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", None)
_POSIX_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_POSIX_O_DIRECTORY = getattr(os, "O_DIRECTORY", None)
_O_BINARY = getattr(os, "O_BINARY", 0x8000)

_WIN_GENERIC_READ = 0x80000000
_WIN_FILE_SHARE_READ = 0x1
_WIN_FILE_SHARE_WRITE = 0x2
_WIN_FILE_SHARE_DELETE = 0x4
_WIN_OPEN_EXISTING = 3
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x10
_WIN_FILE_ATTRIBUTE_NORMAL = 0x80
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WIN_FILE_TYPE_DISK = 0x1
_WIN_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WIN_OBJ_CASE_INSENSITIVE = 0x40
_WIN_FILE_OPEN = 1
_WIN_FILE_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_DIRECTORY_FILE = 0x1
_WIN_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_WIN_FILE_NON_DIRECTORY_FILE = 0x40
_WIN_FILE_GENERIC_READ = 0x00120089
MAX_WIN32_COMPONENT_UTF16_BYTES = 65_532


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("MaximumLength", ctypes.c_uint16),
        ("Buffer", ctypes.c_wchar_p),
    ]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", ctypes.c_uint32),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IO_STATUS_BLOCK_VALUE(ctypes.Union):
    _fields_ = [("Status", ctypes.c_long), ("Pointer", ctypes.c_void_p)]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("value", _IO_STATUS_BLOCK_VALUE), ("Information", ctypes.c_size_t)]


def _win_error(code: int) -> SourceError:
    if code in {2, 3}:
        kind = SourceErrorCode.MISSING_SOURCE
    elif code == 5:
        kind = SourceErrorCode.UNSAFE_SOURCE
    else:
        kind = SourceErrorCode.IO_ERROR
    return SourceError("source acquisition failed", code=kind)


class _Win32Api:
    """Small injectable wrapper around the Win32 same-handle file API."""

    def __init__(self, *, kernel32: Any, msvcrt: Any, ntdll: Any | None = None) -> None:
        from ctypes import wintypes

        self.kernel32 = kernel32
        self.msvcrt = msvcrt
        self.ntdll = ntdll
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.GetFileType.restype = wintypes.DWORD
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        if ntdll is not None:
            ntdll.NtCreateFile.argtypes = [
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.DWORD,
                ctypes.POINTER(_OBJECT_ATTRIBUTES),
                ctypes.POINTER(_IO_STATUS_BLOCK),
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            ntdll.NtCreateFile.restype = ctypes.c_long
            ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
            ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    def open_reparse(self, path: Path) -> int:
        rendered = str(path).replace("/", "\\")
        handle = self.kernel32.CreateFileW(
            rendered,
            _WIN_GENERIC_READ,
            _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE | _WIN_FILE_SHARE_DELETE,
            None,
            _WIN_OPEN_EXISTING,
            _WIN_FILE_FLAG_OPEN_REPARSE_POINT | _WIN_FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == _WIN_INVALID_HANDLE_VALUE:
            raise _win_error(int(self.kernel32.GetLastError()))
        return int(handle)

    def inspect(self, handle: int) -> SimpleNamespace:
        file_type = int(self.kernel32.GetFileType(handle))
        if file_type == 0:
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.IO_ERROR
            )
        information = _BY_HANDLE_FILE_INFORMATION()
        if not self.kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.IO_ERROR
            )
        return SimpleNamespace(
            file_type=file_type,
            attributes=int(information.dwFileAttributes),
        )

    def open_directory(self, path: Path) -> int:
        handle = self.open_reparse(path)
        try:
            identity = self.inspect(handle)
            if (
                identity.file_type != _WIN_FILE_TYPE_DISK
                or not identity.attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                or identity.attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.UNSAFE_SOURCE
                )
            return handle
        except BaseException:
            try:
                self.close_handle(handle)
            except OSError:
                pass
            raise

    def open_relative(self, parent: int, name: str, *, directory: bool) -> int:
        if self.ntdll is None:
            raise SourceError(
                "safe source acquisition is unavailable",
                code=SourceErrorCode.SAFE_OPEN_UNAVAILABLE,
            )
        from ctypes import wintypes

        encoded_length = len(name.encode("utf-16-le"))
        if encoded_length > MAX_WIN32_COMPONENT_UTF16_BYTES:
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.UNSAFE_SOURCE
            )
        buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _UNICODE_STRING(
            Length=encoded_length,
            MaximumLength=encoded_length + 2,
            Buffer=ctypes.cast(buffer, ctypes.c_wchar_p),
        )
        attributes = _OBJECT_ATTRIBUTES(
            Length=ctypes.sizeof(_OBJECT_ATTRIBUTES),
            RootDirectory=parent,
            ObjectName=ctypes.pointer(unicode_name),
            Attributes=_WIN_OBJ_CASE_INSENSITIVE,
            SecurityDescriptor=None,
            SecurityQualityOfService=None,
        )
        status_block = _IO_STATUS_BLOCK()
        handle = wintypes.HANDLE()
        options = (
            _WIN_FILE_OPEN_REPARSE_POINT
            | _WIN_FILE_SYNCHRONOUS_IO_NONALERT
            | (_WIN_FILE_DIRECTORY_FILE if directory else _WIN_FILE_NON_DIRECTORY_FILE)
        )
        status = int(
            self.ntdll.NtCreateFile(
                ctypes.byref(handle),
                _WIN_FILE_GENERIC_READ,
                ctypes.byref(attributes),
                ctypes.byref(status_block),
                None,
                _WIN_FILE_ATTRIBUTE_NORMAL,
                _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE | _WIN_FILE_SHARE_DELETE,
                _WIN_FILE_OPEN,
                options,
                None,
                0,
            )
        )
        if status < 0:
            error = int(self.ntdll.RtlNtStatusToDosError(status))
            raise _win_error(error)
        return int(handle.value)

    def descriptor_from_handle(self, handle: int) -> int:
        try:
            return int(self.msvcrt.open_osfhandle(handle, os.O_RDONLY | _O_BINARY))
        except OSError as exc:
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.IO_ERROR
            ) from exc

    def close_handle(self, handle: int) -> None:
        if not self.kernel32.CloseHandle(handle):
            raise OSError(int(self.kernel32.GetLastError()), "handle close failed")


def _win32_api() -> _Win32Api:
    import msvcrt

    return _Win32Api(
        kernel32=ctypes.windll.kernel32,
        msvcrt=msvcrt,
        ntdll=ctypes.windll.ntdll,
    )


def _platform_name() -> str:
    return os.name


def _fstat_descriptor(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _read_descriptor(descriptor: int, size: int) -> bytes:
    return os.read(descriptor, size)


def _close_descriptor(descriptor: int) -> None:
    os.close(descriptor)


def _retry(operation: Any) -> Any:
    for attempt in range(3):
        try:
            return operation()
        except InterruptedError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _posix_open_regular(path: Path) -> int:
    if _POSIX_O_NOFOLLOW is None:
        raise SourceError(
            "safe source acquisition is unavailable",
            code=SourceErrorCode.SAFE_OPEN_UNAVAILABLE,
        )
    flags = os.O_RDONLY | os.O_NONBLOCK | _POSIX_O_NOFOLLOW | _POSIX_O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except InterruptedError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            code = SourceErrorCode.MISSING_SOURCE
        elif exc.errno in {errno.EACCES, errno.ELOOP, errno.ENOTDIR, errno.EISDIR}:
            code = SourceErrorCode.UNSAFE_SOURCE
        else:
            code = SourceErrorCode.IO_ERROR
        raise SourceError("source acquisition failed", code=code) from exc
    if not _POSIX_O_CLOEXEC:
        try:
            inheritable = os.get_inheritable(descriptor)
            if inheritable:
                os.set_inheritable(descriptor, False)
        except OSError as exc:
            try:
                os.close(descriptor)
            finally:
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.IO_ERROR
                ) from exc
    return descriptor


def _posix_open_component(parent: int | None, name: str, flags: int) -> int:
    try:
        return os.open(name, flags, dir_fd=parent)
    except InterruptedError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            code = SourceErrorCode.MISSING_SOURCE
        elif exc.errno in {
            errno.EACCES,
            errno.ELOOP,
            errno.ENOTDIR,
            errno.EISDIR,
        }:
            code = SourceErrorCode.UNSAFE_SOURCE
        else:
            code = SourceErrorCode.IO_ERROR
        raise SourceError("source acquisition failed", code=code) from exc


def _posix_open_relative_regular(directory: Path, basename: str) -> int:
    if _POSIX_O_NOFOLLOW is None or _POSIX_O_DIRECTORY is None:
        raise SourceError(
            "safe source acquisition is unavailable",
            code=SourceErrorCode.SAFE_OPEN_UNAVAILABLE,
        )
    directory_flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | _POSIX_O_NOFOLLOW
        | _POSIX_O_CLOEXEC
        | _POSIX_O_DIRECTORY
    )
    file_flags = os.O_RDONLY | os.O_NONBLOCK | _POSIX_O_NOFOLLOW | _POSIX_O_CLOEXEC
    parts = directory.parts
    if directory.is_absolute():
        first, remaining = directory.anchor, parts[1:]
    else:
        first, remaining = ".", parts
    current: int | None = _retry(
        lambda: _posix_open_component(None, first, directory_flags)
    )
    try:
        for component in remaining:
            child = _retry(
                lambda component=component: _posix_open_component(
                    current, component, directory_flags
                )
            )
            try:
                _close_descriptor(current)
            except OSError:
                current = None
                try:
                    _close_descriptor(child)
                except OSError:
                    pass
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.IO_ERROR
                ) from None
            current = child
        descriptor = _retry(
            lambda: _posix_open_component(current, basename, file_flags)
        )
        try:
            _close_descriptor(current)
        except OSError:
            current = None
            try:
                _close_descriptor(descriptor)
            except OSError:
                pass
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.IO_ERROR
            ) from None
        current = None
        return descriptor
    except BaseException:
        if current is not None:
            try:
                _close_descriptor(current)
            except OSError:
                pass
            current = None
        raise
    finally:
        if current is not None:
            try:
                _close_descriptor(current)
            except OSError:
                pass


def _win32_open_regular(path: Path) -> int:
    api = _win32_api()
    handle = api.open_reparse(path)
    primary: BaseException | None = None
    try:
        identity = api.inspect(handle)
        if (
            identity.file_type != _WIN_FILE_TYPE_DISK
            or identity.attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
            or identity.attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise SourceError(
                "source acquisition failed", code=SourceErrorCode.UNSAFE_SOURCE
            )
        descriptor = api.descriptor_from_handle(handle)
        handle = -1
        return descriptor
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if handle != -1:
            try:
                api.close_handle(handle)
            except OSError:
                if primary is None:
                    raise SourceError(
                        "source acquisition failed", code=SourceErrorCode.IO_ERROR
                    ) from None


def _win32_open_relative_regular(directory: Path, basename: str) -> int:
    api = _win32_api()
    parts = directory.parts
    if directory.is_absolute():
        first, remaining = directory.anchor, parts[1:]
    else:
        first, remaining = ".", parts
    current: int | None = api.open_directory(Path(first))
    try:
        for component in remaining:
            child = api.open_relative(current, component, directory=True)
            child_primary: BaseException | None = None
            try:
                identity = api.inspect(child)
                if (
                    identity.file_type != _WIN_FILE_TYPE_DISK
                    or not identity.attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                    or identity.attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise SourceError(
                        "source acquisition failed",
                        code=SourceErrorCode.UNSAFE_SOURCE,
                    )
            except BaseException as exc:
                child_primary = exc
                raise
            finally:
                if child_primary is not None:
                    try:
                        api.close_handle(child)
                    except OSError:
                        pass
            try:
                api.close_handle(current)
            except OSError:
                current = None
                try:
                    api.close_handle(child)
                except OSError:
                    pass
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.IO_ERROR
                ) from None
            current = child
        handle = api.open_relative(current, basename, directory=False)
        handle_primary: BaseException | None = None
        try:
            identity = api.inspect(handle)
            if (
                identity.file_type != _WIN_FILE_TYPE_DISK
                or identity.attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY
                or identity.attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.UNSAFE_SOURCE
                )
            descriptor = api.descriptor_from_handle(handle)
            handle = -1
            try:
                api.close_handle(current)
            except OSError:
                current = None
                try:
                    _close_descriptor(descriptor)
                except OSError:
                    pass
                raise SourceError(
                    "source acquisition failed", code=SourceErrorCode.IO_ERROR
                ) from None
            current = None
            return descriptor
        except BaseException as exc:
            handle_primary = exc
            raise
        finally:
            if handle != -1:
                try:
                    api.close_handle(handle)
                except OSError:
                    if handle_primary is None:
                        raise SourceError(
                            "source acquisition failed",
                            code=SourceErrorCode.IO_ERROR,
                        ) from None
    except BaseException:
        if current is not None:
            try:
                api.close_handle(current)
            except OSError:
                pass
            current = None
        raise
    finally:
        if current is not None:
            try:
                api.close_handle(current)
            except OSError:
                pass


def _open_source_descriptor(path: Path) -> int:
    if _platform_name() == "nt":
        return _win32_open_regular(path)
    return _posix_open_regular(path)


def _open_relative_source_descriptor(directory: Path, basename: str) -> int:
    if _platform_name() == "nt":
        return _win32_open_relative_regular(directory, basename)
    return _posix_open_relative_regular(directory, basename)


def _normalize_acquisition_error(
    exc: SourceError | OSError | UnicodeError, contract: SourceContract
) -> SourceError:
    if isinstance(exc, SourceError):
        code = exc.code
    elif isinstance(exc, FileNotFoundError):
        code = SourceErrorCode.MISSING_SOURCE
    elif isinstance(exc, PermissionError):
        code = SourceErrorCode.UNSAFE_SOURCE
    elif isinstance(exc, UnicodeError):
        code = SourceErrorCode.UNSAFE_SOURCE
    else:
        code = SourceErrorCode.IO_ERROR
    return _source_error(contract, code)


def _read_bounded(
    path: Path,
    contract: SourceContract,
    *,
    opener: Any | None = None,
    retry_opener: bool = True,
) -> tuple[int, bytes] | None:
    acquire = _open_source_descriptor if opener is None else opener
    try:
        descriptor = _retry(lambda: acquire(path)) if retry_opener else acquire(path)
    except (SourceError, OSError, UnicodeError) as exc:
        error = _normalize_acquisition_error(exc, contract)
        if error.code is SourceErrorCode.MISSING_SOURCE and contract.optional:
            return None
        raise error from None
    try:
        try:
            metadata = _retry(lambda: _fstat_descriptor(descriptor))
        except (SourceError, OSError) as exc:
            raise _normalize_acquisition_error(exc, contract) from None
        if not stat.S_ISREG(metadata.st_mode):
            raise _source_error(contract, SourceErrorCode.UNSAFE_SOURCE)
        if metadata.st_size > contract.max_bytes:
            raise _source_error(contract, SourceErrorCode.BYTE_LIMIT)
        capacity = contract.max_bytes + 1
        chunks: list[bytes] = []
        acquired_size = 0
        while acquired_size < capacity:
            request_size = capacity - acquired_size
            if request_size <= 0:
                break
            try:
                chunk = _retry(lambda: _read_descriptor(descriptor, request_size))
            except (SourceError, OSError) as exc:
                raise _normalize_acquisition_error(exc, contract) from None
            if not chunk:
                break
            if len(chunk) > request_size:
                chunk = chunk[:request_size]
            chunks.append(chunk)
            acquired_size += len(chunk)
        content = b"".join(chunks)
        if len(content) > contract.max_bytes:
            raise _source_error(contract, SourceErrorCode.BYTE_LIMIT)
        return descriptor, content
    except BaseException:
        try:
            _close_descriptor(descriptor)
        except OSError:
            pass
        raise


class _BoundedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream: str, contract: SourceContract) -> None:
        self._contract = contract
        self._graph_entries = 0
        self._alias_events = 0
        super().__init__(stream)

    def _limit(self) -> NoReturn:
        raise _source_error(self._contract, SourceErrorCode.PARSER_LIMIT)

    def _register(self, *, depth: int, count: bool) -> None:
        if not count:
            return
        if depth > self._contract.max_depth:
            self._limit()
        self._graph_entries += 1
        if self._graph_entries > self._contract.max_graph_entries:
            self._limit()

    def compose_document(self):
        self.get_event()
        node = self._compose_bounded_node(None, None, depth=0, count=True)
        self.get_event()
        self.anchors = {}
        return node

    def _compose_bounded_node(self, parent, index, *, depth: int, count: bool):
        self._register(depth=depth, count=count)
        if self.check_event(AliasEvent):
            event = self.get_event()
            self._alias_events += 1
            if (
                self._contract.max_aliases is not None
                and self._alias_events > self._contract.max_aliases
            ):
                self._limit()
            anchor = event.anchor
            if anchor not in self.anchors:
                raise ComposerError(
                    None, None, f"found undefined alias {anchor!r}", event.start_mark
                )
            return self.anchors[anchor]
        event = self.peek_event()
        anchor = event.anchor
        if anchor is not None and anchor in self.anchors:
            raise ComposerError(
                f"found duplicate anchor {anchor!r}; first occurrence",
                self.anchors[anchor].start_mark,
                "second occurrence",
                event.start_mark,
            )
        self.descend_resolver(parent, index)
        if self.check_event(ScalarEvent):
            node = self.compose_scalar_node(anchor)
        elif self.check_event(SequenceStartEvent):
            node = self._compose_sequence(anchor, depth)
        elif self.check_event(MappingStartEvent):
            node = self._compose_mapping(anchor, depth)
        else:
            raise ComposerError(None, None, "unexpected YAML event", event.start_mark)
        self.ascend_resolver()
        return node

    def _compose_sequence(self, anchor: str | None, depth: int) -> SequenceNode:
        start_event = self.get_event()
        tag = start_event.tag
        if tag is None or tag == "!":
            tag = self.resolve(SequenceNode, None, start_event.implicit)
        node = SequenceNode(
            tag,
            [],
            start_event.start_mark,
            None,
            flow_style=start_event.flow_style,
        )
        if anchor is not None:
            self.anchors[anchor] = node
        index = 0
        while not self.check_event(SequenceEndEvent):
            node.value.append(
                self._compose_bounded_node(node, index, depth=depth + 1, count=True)
            )
            index += 1
        end_event = self.get_event()
        node.end_mark = end_event.end_mark
        return node

    def _compose_mapping(self, anchor: str | None, depth: int) -> MappingNode:
        start_event = self.get_event()
        tag = start_event.tag
        if tag is None or tag == "!":
            tag = self.resolve(MappingNode, None, start_event.implicit)
        node = MappingNode(
            tag,
            [],
            start_event.start_mark,
            None,
            flow_style=start_event.flow_style,
        )
        if anchor is not None:
            self.anchors[anchor] = node
        keys: set[str] = set()
        while not self.check_event(MappingEndEvent):
            key = self._compose_bounded_node(node, None, depth=depth, count=False)
            if not isinstance(key, ScalarNode):
                raise _source_error(self._contract, SourceErrorCode.KEY_TYPE)
            if key.tag == "tag:yaml.org,2002:merge":
                raise _source_error(self._contract, SourceErrorCode.MERGE_KEY)
            if key.tag != "tag:yaml.org,2002:str":
                raise _source_error(self._contract, SourceErrorCode.KEY_TYPE)
            if key.value in keys:
                raise _source_error(self._contract, SourceErrorCode.DUPLICATE_KEY)
            keys.add(key.value)
            value = self._compose_bounded_node(node, key, depth=depth + 1, count=True)
            node.value.append((key, value))
        end_event = self.get_event()
        node.end_mark = end_event.end_mark
        return node


def _load_bounded_yaml(text: str, contract: SourceContract) -> dict[str, Any]:
    if contract is WORKFLOW_SIDECAR_CONTRACT:
        # Preserve the established diagnostic for inputs far beyond Python's
        # historical recursion boundary, while still rejecting adjacent
        # contract-depth overflow as a parser composition limit.
        flow_depth = 0
        for token in yaml.scan(text):
            if isinstance(
                token,
                yaml.tokens.FlowSequenceStartToken | yaml.tokens.FlowMappingStartToken,
            ):
                flow_depth += 1
                if flow_depth > 256:
                    raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
            elif isinstance(
                token,
                yaml.tokens.FlowSequenceEndToken | yaml.tokens.FlowMappingEndToken,
            ):
                flow_depth -= 1
    loader = _BoundedSafeLoader(text, contract)
    try:
        value = loader.get_single_data()
    finally:
        loader.dispose()
    if value is None:
        raise _source_error(contract, SourceErrorCode.INVALID_YAML)
    if not isinstance(value, dict):
        raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
    _validate_graph(value, contract)
    return value


class _BoundedJsonParser:
    def __init__(self, text: str, contract: SourceContract) -> None:
        self.text = text
        self.contract = contract
        self.index = 0
        self.entries = 0

    def _invalid(self) -> NoReturn:
        raise _source_error(self.contract, SourceErrorCode.INVALID_JSON)

    def _limit(self) -> NoReturn:
        raise _source_error(self.contract, SourceErrorCode.PARSER_LIMIT)

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _register(self, depth: int) -> None:
        if depth > self.contract.max_depth:
            self._limit()
        self.entries += 1
        if self.entries > self.contract.max_graph_entries:
            self._limit()

    def parse(self) -> Any:
        self._space()
        value = self._value(0)
        self._space()
        if self.index != len(self.text):
            self._invalid()
        return value

    def _value(self, depth: int) -> Any:
        self._register(depth)
        self._space()
        if self.index >= len(self.text):
            self._invalid()
        token = self.text[self.index]
        if token == "{":
            return self._object(depth)
        if token == "[":
            return self._array(depth)
        if token == '"':
            return self._string()
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(literal, self.index):
                self.index += len(literal)
                return value
        if token == "-" or "0" <= token <= "9":
            return self._number()
        self._invalid()

    def _object(self, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self.index += 1
        self._space()
        if self.index < len(self.text) and self.text[self.index] == "}":
            self.index += 1
            return result
        while True:
            self._space()
            if self.index >= len(self.text) or self.text[self.index] != '"':
                self._invalid()
            key = self._string()
            if key in result:
                raise _source_error(self.contract, SourceErrorCode.DUPLICATE_KEY)
            self._space()
            if self.index >= len(self.text) or self.text[self.index] != ":":
                self._invalid()
            self.index += 1
            value = self._value(depth + 1)
            self._append_value(result, key, value)
            self._space()
            if self.index >= len(self.text):
                self._invalid()
            delimiter = self.text[self.index]
            self.index += 1
            if delimiter == "}":
                return result
            if delimiter != ",":
                self._invalid()

    def _array(self, depth: int) -> list[Any]:
        result: list[Any] = []
        self.index += 1
        self._space()
        if self.index < len(self.text) and self.text[self.index] == "]":
            self.index += 1
            return result
        while True:
            value = self._value(depth + 1)
            self._append_value(result, None, value)
            self._space()
            if self.index >= len(self.text):
                self._invalid()
            delimiter = self.text[self.index]
            self.index += 1
            if delimiter == "]":
                return result
            if delimiter != ",":
                self._invalid()

    def _append_value(self, container: Any, key: str | None, value: Any) -> None:
        if isinstance(container, list):
            container.append(value)
        else:
            container[key] = value

    def _string(self) -> str:
        if self.text[self.index] != '"':
            self._invalid()
        self.index += 1
        pieces: list[str] = []
        while self.index < len(self.text):
            character = self.text[self.index]
            self.index += 1
            if character == '"':
                return "".join(pieces)
            if ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF:
                self._invalid()
            if character != "\\":
                pieces.append(character)
                continue
            if self.index >= len(self.text):
                self._invalid()
            escape = self.text[self.index]
            self.index += 1
            simple = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if escape in simple:
                pieces.append(simple[escape])
                continue
            if escape != "u":
                self._invalid()
            first = self._unicode_escape()
            if 0xD800 <= first <= 0xDBFF:
                if not self.text.startswith("\\u", self.index):
                    self._invalid()
                self.index += 2
                second = self._unicode_escape()
                if not 0xDC00 <= second <= 0xDFFF:
                    self._invalid()
                codepoint = 0x10000 + ((first - 0xD800) << 10) + second - 0xDC00
                pieces.append(chr(codepoint))
            elif 0xDC00 <= first <= 0xDFFF:
                self._invalid()
            else:
                pieces.append(chr(first))
        self._invalid()

    def _unicode_escape(self) -> int:
        end = self.index + 4
        if end > len(self.text):
            self._invalid()
        token = self.text[self.index : end]
        if any(character not in "0123456789abcdefABCDEF" for character in token):
            self._invalid()
        self.index = end
        return int(token, 16)

    def _number(self) -> int | float:
        start = self.index
        if self.text[self.index] == "-":
            self.index += 1
            if self.index >= len(self.text):
                self._invalid()
        if self.text[self.index] == "0":
            self.index += 1
        elif self.text[self.index] in "123456789":
            while self.index < len(self.text) and "0" <= self.text[self.index] <= "9":
                self.index += 1
        else:
            self._invalid()
        floating = False
        if self.index < len(self.text) and self.text[self.index] == ".":
            floating = True
            self.index += 1
            fraction = self.index
            while self.index < len(self.text) and "0" <= self.text[self.index] <= "9":
                self.index += 1
            if self.index == fraction:
                self._invalid()
        if self.index < len(self.text) and self.text[self.index] in "eE":
            floating = True
            self.index += 1
            if self.index < len(self.text) and self.text[self.index] in "+-":
                self.index += 1
            exponent = self.index
            while self.index < len(self.text) and "0" <= self.text[self.index] <= "9":
                self.index += 1
            if self.index == exponent:
                self._invalid()
        token = self.text[start : self.index]
        if len(token) > JSON_MAX_NUMBER_CHARS:
            self._limit()
        try:
            return float(token) if floating else int(token)
        except (OverflowError, ValueError):
            self._invalid()


def _validate_graph(value: object, contract: SourceContract) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    completed: set[int] = set()
    entries = 1
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            identity = id(item)
            active.remove(identity)
            completed.add(identity)
            continue
        if depth > contract.max_depth:
            raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
        if isinstance(item, dict):
            child_count = len(item)
        elif isinstance(item, list):
            child_count = len(item)
        else:
            continue
        identity = id(item)
        if identity in active:
            raise _source_error(contract, SourceErrorCode.CYCLE)
        if identity in completed:
            continue
        if child_count and depth >= contract.max_depth:
            raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
        if child_count > contract.max_graph_entries - entries:
            raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
        entries += child_count
        if isinstance(item, dict) and any(not isinstance(key, str) for key in item):
            raise _source_error(contract, SourceErrorCode.KEY_TYPE)
        active.add(identity)
        stack.append((item, depth, True))
        nested = tuple(item.values()) if isinstance(item, dict) else tuple(item)
        stack.extend((child, depth + 1, False) for child in reversed(nested))


def _load_with_parser(
    path: Path,
    contract: SourceContract,
    *,
    kind: str,
    opener: Any | None = None,
    retry_opener: bool = True,
) -> dict[str, Any] | None:
    acquired = _read_bounded(
        path,
        contract,
        opener=opener,
        retry_opener=retry_opener,
    )
    if acquired is None:
        return None
    descriptor, content = acquired
    primary: BaseException | None = None
    try:
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            code = (
                SourceErrorCode.INVALID_YAML
                if kind == "yaml"
                else SourceErrorCode.INVALID_JSON
            )
            raise _source_error(contract, code) from None
        try:
            if kind == "yaml":
                return _load_bounded_yaml(text, contract)
            value = _BoundedJsonParser(text, contract).parse()
            if not isinstance(value, dict):
                raise _source_error(contract, SourceErrorCode.STRUCTURE_LIMIT)
            _validate_graph(value, contract)
            return value
        except SourceError:
            raise
        except (RecursionError, ValueError, yaml.YAMLError):
            code = (
                SourceErrorCode.INVALID_YAML
                if kind == "yaml"
                else SourceErrorCode.INVALID_JSON
            )
            raise _source_error(contract, code) from None
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            _close_descriptor(descriptor)
        except OSError:
            if primary is None:
                raise _source_error(contract, SourceErrorCode.IO_ERROR) from None


def load_yaml_mapping(path: Path, contract: SourceContract) -> dict[str, Any] | None:
    return _load_with_parser(path, contract, kind="yaml")


def load_json_mapping(path: Path, contract: SourceContract) -> dict[str, Any] | None:
    return _load_with_parser(path, contract, kind="json")


def load_json_mapping_relative(
    directory: Path, basename: str, contract: SourceContract
) -> dict[str, Any] | None:
    return _load_with_parser(
        directory / basename,
        contract,
        kind="json",
        opener=lambda _path: _open_relative_source_descriptor(directory, basename),
        retry_opener=False,
    )
