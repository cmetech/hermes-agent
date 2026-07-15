#!/usr/bin/env python3
"""Native Windows persistence for sanitized Ericsson onboarding checkpoints.

This module is intentionally importable on non-Windows hosts so its adapter boundary
can be tested there. Native calls are constructed only when ``Win32Adapter()`` is
instantiated on Windows.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterator


FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_APPEND_DATA = 0x0004
FILE_LIST_DIRECTORY = 0x0001
FILE_TRAVERSE = 0x0020
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
SYNCHRONIZE = 0x00100000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
SECURE_SHARE_MODE = FILE_SHARE_READ | FILE_SHARE_WRITE

OPEN_EXISTING = 3

FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
FILE_DISPOSITION_INFO_CLASS = 4
FILE_RENAME_INFO_CLASS = 3
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_ID_INFO_CLASS = 18
FILE_STANDARD_INFO_CLASS = 1

OBJ_CASE_INSENSITIVE = 0x00000040
OBJ_DONT_REPARSE = 0x00001000
FILE_OPEN = 1
FILE_CREATE = 2
FILE_OPEN_IF = 3
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REPARSE_POINT = 0x00200000
STATUS_REPARSE_POINT_ENCOUNTERED = ctypes.c_int32(0xC000050B).value

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_HANDLE = 6
ERROR_ALREADY_EXISTS = 183
ERROR_FILE_EXISTS = 80
ERROR_LOCK_VIOLATION = 33
ERROR_STOPPED_ON_SYMLINK = 681
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_FUNCTION = 1

DRIVE_FIXED = 3
FILE_PERSISTENT_ACLS = 0x00000008

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SDDL_REVISION_1 = 1
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1

_LOCK_TIMEOUT_SECONDS = 0.5
_LOCK_RETRY_SECONDS = 0.02
_MAX_PAYLOAD_BYTES = 64 * 1024
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I
)

ANCESTOR_DIRECTORY_ACCESS = (
    FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE
)
PRIVATE_DIRECTORY_ACCESS = (
    ANCESTOR_DIRECTORY_ACCESS
    | FILE_WRITE_ATTRIBUTES
    | READ_CONTROL
    | WRITE_DAC
    | WRITE_OWNER
)


class WindowsStateError(Exception):
    """Safe, user-displayable native persistence failure."""


class WindowsCallError(OSError):
    def __init__(self, operation: str, code: int) -> None:
        super().__init__(code, operation)
        self.operation = operation
        self.winerror = code


@dataclass(frozen=True)
class FileIdentity:
    volume_serial: int
    file_index: int


@dataclass
class WinHandle:
    raw: int
    path: Path
    identity: FileIdentity
    directory: bool
    closed: bool = False


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOL)]


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


class _FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", _FILE_ID_128),
    ]


class _FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", wintypes.BOOL),
        ("Directory", wintypes.BOOL),
    ]


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [
        ("Status", ctypes.c_ssize_t),
        ("Information", ctypes.c_size_t),
    ]


def _file_rename_info_type(pointer_size: int) -> type[ctypes.Structure]:
    if pointer_size not in {4, 8}:
        raise ValueError("unsupported Windows pointer size")
    handle_type = ctypes.c_uint32 if pointer_size == 4 else ctypes.c_uint64

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_int32),
            ("RootDirectory", handle_type),
            ("FileNameLength", ctypes.c_uint32),
            ("FileName", ctypes.c_uint16 * 1),
        ]

    return FileRenameInfo


def _marshal_rename_info(
    destination_directory: int,
    name: str,
    *,
    replace: bool,
    pointer_size: int | None = None,
) -> tuple[ctypes.Array, type[ctypes.Structure]]:
    encoded = name.encode("utf-16-le")
    information_type = _file_rename_info_type(
        ctypes.sizeof(ctypes.c_void_p) if pointer_size is None else pointer_size
    )
    # Windows requires sizeof(FILE_RENAME_INFO) plus all filename bytes. Include an
    # additional zero WCHAR for defensive termination; FileNameLength excludes it.
    size = ctypes.sizeof(information_type) + len(encoded) + 2
    buffer = ctypes.create_string_buffer(size)
    information = information_type.from_buffer(buffer)
    information.ReplaceIfExists = bool(replace)
    information.RootDirectory = destination_directory
    information.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + information_type.FileName.offset,
        encoded,
        len(encoded),
    )
    return buffer, information_type


def _validate_private_acl(
    owner_sid: str, dacl_sddl: str, current_sid: str, *, directory: bool
) -> None:
    if owner_sid != current_sid:
        raise WindowsStateError(
            "The onboarding state owner is not the current Windows user."
        )
    if not dacl_sddl.startswith("D:P"):
        raise WindowsStateError("Unable to verify private onboarding state ACLs.")
    ace_text = dacl_sddl[3:]
    aces = re.findall(r"\([^()]+\)", ace_text)
    if "".join(aces) != ace_text:
        raise WindowsStateError("Unable to verify private onboarding state ACLs.")
    inheritance = "OICI" if directory else ""
    expected = {
        f"(A;{inheritance};FA;;;SY)",
        f"(A;{inheritance};FA;;;{current_sid})",
    }
    if len(aces) != 2 or set(aces) != expected:
        raise WindowsStateError("Unable to verify private onboarding state ACLs.")


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class Kernel32API:
    """Small ctypes boundary; every security decision above it uses native handles."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsStateError(
                "The native Windows onboarding-state adapter is unavailable here."
            )
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        self._user_sid: str | None = None
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        k32 = self.kernel32
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)
        ]
        k32.GetFileInformationByHandle.restype = wintypes.BOOL
        k32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k32.GetDriveTypeW.restype = wintypes.UINT
        k32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
        ]
        k32.GetVolumeInformationW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        k32.ReadFile.restype = wintypes.BOOL
        k32.WriteFile.argtypes = list(k32.ReadFile.argtypes)
        k32.WriteFile.restype = wintypes.BOOL
        k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k32.FlushFileBuffers.restype = wintypes.BOOL
        k32.LockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(_OVERLAPPED),
        ]
        k32.LockFileEx.restype = wintypes.BOOL
        k32.UnlockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(_OVERLAPPED),
        ]
        k32.UnlockFileEx.restype = wintypes.BOOL
        k32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        k32.SetFileInformationByHandle.restype = wintypes.BOOL
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p
        self.ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
            ctypes.POINTER(_OBJECT_ATTRIBUTES), ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
            wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
        ]
        self.ntdll.NtCreateFile.restype = ctypes.c_long
        self.ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
        self.ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    @staticmethod
    def _raise(operation: str) -> None:
        raise WindowsCallError(operation, ctypes.get_last_error())

    def create_file(self, path, access, share, creation, flags):
        handle = self.kernel32.CreateFileW(
            str(path), access, share, None, creation, flags, None
        )
        if handle == wintypes.HANDLE(-1).value:
            self._raise("open Windows onboarding state object")
        return int(handle)

    def create_relative(
        self,
        parent: int,
        name: str,
        *,
        access: int,
        share: int,
        disposition: int,
        directory: bool,
        private: bool,
    ) -> int:
        if not name or any(character in name for character in "\\/:\x00"):
            raise WindowsStateError("The onboarding state path component is invalid.")
        name_buffer = ctypes.create_unicode_buffer(name)
        name_bytes = len(name.encode("utf-16-le"))
        unicode_name = _UNICODE_STRING(
            name_bytes, name_bytes, ctypes.cast(name_buffer, wintypes.LPWSTR)
        )
        descriptor = self._private_descriptor(directory) if private else ctypes.c_void_p()
        attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            parent,
            ctypes.pointer(unicode_name),
            OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE,
            descriptor,
            None,
        )
        io_status = _IO_STATUS_BLOCK()
        handle = wintypes.HANDLE()
        options = (
            FILE_OPEN_REPARSE_POINT
            | FILE_SYNCHRONOUS_IO_NONALERT
            | (FILE_DIRECTORY_FILE if directory else FILE_NON_DIRECTORY_FILE)
        )
        try:
            status = self.ntdll.NtCreateFile(
                ctypes.byref(handle), access, ctypes.byref(attributes),
                ctypes.byref(io_status), None, FILE_ATTRIBUTE_NORMAL, share,
                disposition, options, None, 0,
            )
            if status < 0:
                if ctypes.c_int32(status).value == STATUS_REPARSE_POINT_ENCOUNTERED:
                    raise WindowsStateError(
                        "Refusing a symbolic link or reparse point in the onboarding state path."
                    )
                code = int(self.ntdll.RtlNtStatusToDosError(status))
                if code == ERROR_STOPPED_ON_SYMLINK:
                    raise WindowsStateError(
                        "Refusing a symbolic link or reparse point in the onboarding state path."
                    )
                raise WindowsCallError("open relative Windows onboarding object", code)
            return int(handle.value)
        finally:
            if descriptor:
                self.kernel32.LocalFree(descriptor)

    def validate_local_volume(self, root: Path) -> None:
        if self.kernel32.GetDriveTypeW(str(root)) != DRIVE_FIXED:
            raise WindowsStateError(
                "The active profile must be on a fixed local Windows volume."
            )
        flags = wintypes.DWORD()
        if not self.kernel32.GetVolumeInformationW(
            str(root), None, 0, None, None, ctypes.byref(flags), None, 0
        ):
            self._raise("inspect the Windows onboarding state volume")
        if not flags.value & FILE_PERSISTENT_ACLS:
            raise WindowsStateError(
                "The active profile volume cannot enforce private onboarding ACLs."
            )

    def file_attributes(self, handle: int) -> int:
        information = _FILE_ATTRIBUTE_TAG_INFO()
        if not self.kernel32.GetFileInformationByHandleEx(
            handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise("inspect Windows onboarding state object")
        return int(information.FileAttributes)

    def file_identity(self, handle: int) -> FileIdentity:
        information = _FILE_ID_INFO()
        if not self.kernel32.GetFileInformationByHandleEx(
            handle, FILE_ID_INFO_CLASS, ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise("identify Windows onboarding state object")
        index = int.from_bytes(bytes(information.FileId.Identifier), "little")
        return FileIdentity(int(information.VolumeSerialNumber), index)

    def file_standard(self, handle: int) -> tuple[int, bool, bool]:
        information = _FILE_STANDARD_INFO()
        if not self.kernel32.GetFileInformationByHandleEx(
            handle, FILE_STANDARD_INFO_CLASS, ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise("inspect Windows onboarding state link state")
        return (
            int(information.NumberOfLinks),
            bool(information.DeletePending),
            bool(information.Directory),
        )

    def close_handle(self, handle: int) -> None:
        if not self.kernel32.CloseHandle(handle):
            self._raise("close Windows onboarding state handle")

    def read_all(self, handle: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            buffer = ctypes.create_string_buffer(8192)
            read = wintypes.DWORD()
            if not self.kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
                self._raise("read Windows onboarding state")
            if not read.value:
                break
            total += read.value
            if total > _MAX_PAYLOAD_BYTES:
                raise WindowsStateError("Saved onboarding state exceeds the safe size limit.")
            chunks.append(buffer.raw[: read.value])
        return b"".join(chunks)

    def write_all(self, handle: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            chunk = content[offset : offset + 8192]
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not self.kernel32.WriteFile(
                handle, buffer, len(chunk), ctypes.byref(written), None
            ):
                self._raise("write Windows onboarding state")
            if not written.value:
                raise WindowsCallError("write Windows onboarding state", 0)
            offset += written.value

    def flush(self, handle: int, *, directory: bool = False) -> None:
        if self.kernel32.FlushFileBuffers(handle):
            return
        code = ctypes.get_last_error()
        if directory and code in {
            ERROR_ACCESS_DENIED, ERROR_INVALID_HANDLE, ERROR_NOT_SUPPORTED,
            ERROR_INVALID_FUNCTION,
        }:
            return
        raise WindowsCallError("flush Windows onboarding state", code)

    def rename_handle(
        self, source: int, destination_directory: int, name: str, *, replace: bool
    ) -> None:
        buffer, _ = _marshal_rename_info(
            destination_directory, name, replace=replace
        )
        if not self.kernel32.SetFileInformationByHandle(
            source, FILE_RENAME_INFO_CLASS, buffer, len(buffer)
        ):
            raise WindowsCallError("publish Windows onboarding state", ctypes.get_last_error())

    def delete_handle(self, handle: int) -> None:
        disposition = _FILE_DISPOSITION_INFO(True)
        if not self.kernel32.SetFileInformationByHandle(
            handle, FILE_DISPOSITION_INFO_CLASS, ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            self._raise("remove Windows onboarding state")

    def try_lock(self, handle: int) -> bool:
        overlapped = _OVERLAPPED()
        flags = LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY
        if self.kernel32.LockFileEx(
            handle, flags, 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(overlapped)
        ):
            return True
        code = ctypes.get_last_error()
        if code == ERROR_LOCK_VIOLATION:
            return False
        raise WindowsCallError("acquire Windows onboarding state lock", code)

    def unlock(self, handle: int) -> None:
        overlapped = _OVERLAPPED()
        if not self.kernel32.UnlockFileEx(
            handle, 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(overlapped)
        ):
            self._raise("release Windows onboarding state lock")

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def current_user_sid(self) -> str:
        if self._user_sid is not None:
            return self._user_sid
        get_process = self.kernel32.GetCurrentProcess
        get_process.argtypes = []
        get_process.restype = wintypes.HANDLE
        open_token = self.advapi32.OpenProcessToken
        open_token.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        ]
        open_token.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        if not open_token(get_process(), TOKEN_QUERY, ctypes.byref(token)):
            self._raise("identify the current Windows profile owner")
        try:
            get_token = self.advapi32.GetTokenInformation
            get_token.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            get_token.restype = wintypes.BOOL
            required = wintypes.DWORD()
            get_token(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(required))
            if not required.value:
                self._raise("identify the current Windows profile owner")
            buffer = ctypes.create_string_buffer(required.value)
            if not get_token(
                token, TOKEN_USER_CLASS, buffer, required.value,
                ctypes.byref(required),
            ):
                self._raise("identify the current Windows profile owner")
            token_user = _TOKEN_USER.from_buffer(buffer)
            self._user_sid = self._sid_to_string(token_user.User.Sid)
        finally:
            self.kernel32.CloseHandle(token)
        if not self._user_sid:
            raise WindowsStateError("Unable to identify the current Windows profile owner.")
        return self._user_sid

    def _sid_to_string(self, sid: ctypes.c_void_p) -> str:
        convert_sid = self.advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        convert_sid.restype = wintypes.BOOL
        sid_text = wintypes.LPWSTR()
        if not convert_sid(sid, ctypes.byref(sid_text)):
            self._raise("identify the Windows onboarding state owner")
        try:
            value = sid_text.value
            if not value:
                raise WindowsStateError(
                    "Unable to identify the Windows onboarding state owner."
                )
            return value
        finally:
            self.kernel32.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))

    def _private_descriptor(self, directory: bool = False):
        descriptor = ctypes.c_void_p()
        size = wintypes.ULONG()
        convert = self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.ULONG),
        ]
        convert.restype = wintypes.BOOL
        sid = self.current_user_sid()
        inheritance = "OICI" if directory else ""
        sddl = (
            f"O:{sid}D:P(A;{inheritance};FA;;;SY)"
            f"(A;{inheritance};FA;;;{sid})"
        )
        if not convert(sddl, SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)):
            self._raise("build private Windows onboarding ACL")
        return descriptor

    def apply_private_acl(self, handle: int) -> None:
        attributes = self.file_attributes(handle)
        descriptor = self._private_descriptor(
            bool(attributes & FILE_ATTRIBUTE_DIRECTORY)
        )
        dacl_present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        owner_defaulted = wintypes.BOOL()
        owner = ctypes.c_void_p()
        get_dacl = self.advapi32.GetSecurityDescriptorDacl
        get_dacl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL),
        ]
        get_dacl.restype = wintypes.BOOL
        get_owner = self.advapi32.GetSecurityDescriptorOwner
        get_owner.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        get_owner.restype = wintypes.BOOL
        local_free = self.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        try:
            if not get_dacl(
                descriptor, ctypes.byref(dacl_present), ctypes.byref(dacl),
                ctypes.byref(dacl_defaulted),
            ) or not dacl_present.value:
                self._raise("build private Windows onboarding ACL")
            if not get_owner(
                descriptor, ctypes.byref(owner), ctypes.byref(owner_defaulted)
            ) or not owner:
                self._raise("build private Windows onboarding owner")
            set_security = self.advapi32.SetSecurityInfo
            set_security.argtypes = [
                wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ]
            set_security.restype = wintypes.DWORD
            result = set_security(
                handle, SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
                | PROTECTED_DACL_SECURITY_INFORMATION,
                owner, None, dacl, None,
            )
            if result:
                raise WindowsCallError("apply private Windows onboarding ACL", int(result))
        finally:
            local_free(descriptor)

    def verify_private_acl(self, handle: int) -> None:
        descriptor = ctypes.c_void_p()
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        get_security = self.advapi32.GetSecurityInfo
        get_security.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_security.restype = wintypes.DWORD
        result = get_security(
            handle, SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner), None,
            ctypes.byref(dacl), None, ctypes.byref(descriptor),
        )
        if result:
            raise WindowsCallError("verify private Windows onboarding ACL", int(result))
        text = wintypes.LPWSTR()
        length = wintypes.ULONG()
        convert = self.advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG),
        ]
        convert.restype = wintypes.BOOL
        local_free = self.kernel32.LocalFree
        try:
            if not convert(
                descriptor, SDDL_REVISION_1, DACL_SECURITY_INFORMATION,
                ctypes.byref(text), ctypes.byref(length),
            ):
                self._raise("verify private Windows onboarding ACL")
            sid = self.current_user_sid()
            directory = bool(self.file_attributes(handle) & FILE_ATTRIBUTE_DIRECTORY)
            _validate_private_acl(
                self._sid_to_string(owner), text.value or "", sid,
                directory=directory,
            )
        finally:
            if text:
                local_free(ctypes.cast(text, ctypes.c_void_p))
            if descriptor:
                local_free(descriptor)


class Win32Adapter:
    def __init__(self, api=None) -> None:
        self.api = api or Kernel32API()

    def open_verified(
        self,
        path: Path,
        *,
        directory: bool,
        creation: int,
        access: int,
        private: bool,
    ) -> WinHandle:
        flags = FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= FILE_FLAG_BACKUP_SEMANTICS
        else:
            flags |= FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH
        raw = self.api.create_file(path, access, SECURE_SHARE_MODE, creation, flags)
        try:
            attributes = self.api.file_attributes(raw)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise WindowsStateError(
                    "Refusing a symbolic link or reparse point in the onboarding state path."
                )
            if bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory:
                raise WindowsStateError("The onboarding state path has an unsafe object type.")
            self._verify_standard(raw, directory)
            identity = self.api.file_identity(raw)
            if private:
                self.api.apply_private_acl(raw)
                self.api.verify_private_acl(raw)
            return WinHandle(raw, Path(path), identity, directory)
        except Exception:
            self.api.close_handle(raw)
            raise

    def open_child(
        self,
        parent: WinHandle,
        name: str,
        *,
        directory: bool,
        disposition: int,
        access: int,
        private: bool,
    ) -> WinHandle:
        self.verify_handle(parent)
        raw = self.api.create_relative(
            parent.raw,
            name,
            access=access,
            share=SECURE_SHARE_MODE,
            disposition=disposition,
            directory=directory,
            private=private,
        )
        path = parent.path / name
        try:
            attributes = self.api.file_attributes(raw)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise WindowsStateError(
                    "Refusing a symbolic link or reparse point in the onboarding state path."
                )
            if bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory:
                raise WindowsStateError("The onboarding state path has an unsafe object type.")
            self._verify_standard(raw, directory)
            identity = self.api.file_identity(raw)
            if private:
                self.api.apply_private_acl(raw)
                self.api.verify_private_acl(raw)
            return WinHandle(raw, path, identity, directory)
        except Exception:
            self.api.close_handle(raw)
            raise

    def _verify_standard(self, raw: int, directory: bool) -> None:
        inspect = getattr(self.api, "file_standard", None)
        if not callable(inspect):
            return
        links, delete_pending, reported_directory = inspect(raw)
        if delete_pending or reported_directory != directory:
            raise WindowsStateError("Onboarding state changed during the operation.")
        if not directory and links != 1:
            raise WindowsStateError(
                "Refusing a hard-linked onboarding state file."
            )

    def verify_handle(self, handle: WinHandle) -> None:
        if handle.closed or self.api.file_identity(handle.raw) != handle.identity:
            raise WindowsStateError("Onboarding state changed during the operation.")
        attributes = self.api.file_attributes(handle.raw)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise WindowsStateError("Refusing a reparse point in onboarding state.")
        if bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != handle.directory:
            raise WindowsStateError("Onboarding state changed during the operation.")
        self._verify_standard(handle.raw, handle.directory)

    def close(self, handle: WinHandle) -> None:
        if not handle.closed:
            self.api.close_handle(handle.raw)
            handle.closed = True

    def acquire_lock(
        self, handle: int, *, timeout: float = _LOCK_TIMEOUT_SECONDS,
        retry: float = _LOCK_RETRY_SECONDS,
    ) -> None:
        deadline = self.api.monotonic() + timeout
        while not self.api.try_lock(handle):
            remaining = deadline - self.api.monotonic()
            if remaining <= 0:
                raise WindowsStateError("Onboarding state is busy; retry shortly.")
            self.api.sleep(min(retry, remaining))

    def release_lock(self, handle: int) -> None:
        self.api.unlock(handle)

    def flush_directory(self, directory: WinHandle) -> None:
        self.verify_handle(directory)
        self.api.flush(directory.raw, directory=True)


@dataclass
class _Profile:
    home: Path
    onboarding: WinHandle
    root: WinHandle
    history: WinHandle
    held: list[WinHandle]


def _translate(error: Exception, message: str) -> WindowsStateError:
    if isinstance(error, WindowsStateError):
        return error
    return WindowsStateError(message)


def _current_partial_effect_error() -> WindowsStateError:
    return WindowsStateError(
        "The current.json replacement may have committed, but durability or "
        "verification is uncertain; inspect current.json before retrying."
    )


def _history_partial_effect_error(
    history_name: str, temporary_name: str | None = None
) -> WindowsStateError:
    cleanup = ""
    if temporary_name:
        cleanup = (
            f" A leftover temporary remains at history/{temporary_name}; inspect "
            "and remove it before retrying."
        )
    return WindowsStateError(
        f"A partial archive may exist at history/{history_name}, and current.json "
        f"remains active; inspect both artifacts before retrying.{cleanup}"
    )


def _windows_home(path: os.PathLike[str] | str) -> Path:
    text = os.fspath(path)
    if not isinstance(text, str) or not text or "\x00" in text:
        raise WindowsStateError("The active Co-Worker profile home is not available.")
    pure = PureWindowsPath(text)
    if not pure.is_absolute() or not pure.drive or str(pure).startswith("\\\\"):
        raise WindowsStateError("The active profile must use a local absolute Windows path.")
    for part in pure.parts[1:]:
        if (
            part in {".", ".."}
            or part.rstrip(" .") != part
            or ":" in part
            or _WINDOWS_DEVICE_NAME.fullmatch(part)
        ):
            raise WindowsStateError("The active profile Windows path is unsafe.")
    return Path(str(pure))


def _open_directory_chain(
    home: os.PathLike[str] | str, adapter: Win32Adapter
) -> _Profile:
    home_path = _windows_home(home)
    pure = PureWindowsPath(str(home_path))
    held: list[WinHandle] = []
    current = Path(pure.anchor)
    try:
        root_drive = adapter.open_verified(
            current, directory=True, creation=OPEN_EXISTING,
            access=ANCESTOR_DIRECTORY_ACCESS,
            private=False,
        )
        held.append(root_drive)
        validate_volume = getattr(adapter.api, "validate_local_volume", None)
        if callable(validate_volume):
            validate_volume(current)
        for part in pure.parts[1:]:
            opened = adapter.open_child(
                held[-1], part, directory=True, disposition=FILE_OPEN_IF,
                access=ANCESTOR_DIRECTORY_ACCESS,
                private=False,
            )
            held.append(opened)

        state_handles: list[WinHandle] = []
        for name in ("onboarding", "ericsson", "history"):
            opened = adapter.open_child(
                held[-1], name, directory=True, disposition=FILE_OPEN_IF,
                access=PRIVATE_DIRECTORY_ACCESS,
                private=True,
            )
            held.append(opened)
            state_handles.append(opened)
            adapter.flush_directory(held[-2])
        return _Profile(home_path, state_handles[0], state_handles[1], state_handles[2], held)
    except Exception as error:
        for handle in reversed(held):
            try:
                adapter.close(handle)
            except Exception:
                pass
        raise _translate(error, "Unable to prepare onboarding state securely.") from error


def _verify_profile(profile: _Profile, adapter: Win32Adapter) -> None:
    for handle in profile.held:
        adapter.verify_handle(handle)


def _close_profile(profile: _Profile, adapter: Win32Adapter) -> None:
    for handle in reversed(profile.held):
        try:
            adapter.close(handle)
        except Exception:
            pass


@contextmanager
def _locked_profile(
    home: os.PathLike[str] | str,
    *,
    adapter: Win32Adapter,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Iterator[_Profile]:
    profile = _open_directory_chain(home, adapter)
    lock: WinHandle | None = None
    locked = False
    primary_failed = False
    try:
        try:
            lock = adapter.open_child(
                profile.root, ".state.lock", directory=False,
                disposition=FILE_OPEN_IF,
                access=(GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES
                        | FILE_WRITE_ATTRIBUTES | READ_CONTROL | WRITE_DAC | WRITE_OWNER
                        | SYNCHRONIZE),
                private=True,
            )
            adapter.acquire_lock(lock.raw, timeout=lock_timeout)
        except WindowsStateError:
            raise
        except Exception as error:
            raise WindowsStateError(
                "Unable to lock onboarding state safely."
            ) from error
        locked = True
        _verify_profile(profile, adapter)
        yield profile
        _verify_profile(profile, adapter)
    except BaseException:
        primary_failed = True
        raise
    finally:
        release_error: Exception | None = None
        if lock is not None:
            if locked:
                try:
                    adapter.release_lock(lock.raw)
                except Exception as error:
                    release_error = error
            try:
                adapter.close(lock)
            except Exception as error:
                release_error = release_error or error
        _close_profile(profile, adapter)
        if release_error is not None and not primary_failed:
            raise WindowsStateError(
                "Unable to release the onboarding state lock safely."
            ) from release_error


@contextmanager
def native_locked_profile(
    home: os.PathLike[str] | str,
    *,
    adapter: Win32Adapter | None = None,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    active = adapter or Win32Adapter()
    with _locked_profile(home, adapter=active, lock_timeout=lock_timeout):
        yield


def _open_file(
    adapter: Win32Adapter,
    directory: WinHandle,
    name: str,
    *,
    disposition: int,
    write: bool = False,
    delete: bool = False,
    private: bool = True,
) -> WinHandle:
    access = (
        GENERIC_READ | FILE_READ_ATTRIBUTES | READ_CONTROL | WRITE_DAC
        | WRITE_OWNER | SYNCHRONIZE
    )
    if write:
        access |= GENERIC_WRITE | FILE_WRITE_ATTRIBUTES
    if delete:
        access |= DELETE
    return adapter.open_child(
        directory, name, directory=False, disposition=disposition,
        access=access, private=private
    )


def _open_optional_current(profile: _Profile, adapter: Win32Adapter, *, delete=False):
    try:
        return _open_file(
            adapter, profile.root, "current.json", disposition=FILE_OPEN,
            delete=delete,
        )
    except WindowsCallError as error:
        if error.winerror in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}:
            return None
        raise WindowsStateError(
            "Unable to open current onboarding state safely."
        ) from error


def _read_record(adapter: Win32Adapter, handle: WinHandle) -> bytes:
    adapter.verify_handle(handle)
    content = adapter.api.read_all(handle.raw)
    adapter.verify_handle(handle)
    return content


def _temporary_path(directory: WinHandle, prefix: str) -> Path:
    return directory.path / f".{prefix}.{uuid.uuid4().hex}.tmp"


def _best_effort_delete(
    adapter: Win32Adapter,
    directory: WinHandle,
    name: str,
    identity: FileIdentity | None = None,
) -> bool:
    handle: WinHandle | None = None
    try:
        handle = _open_file(
            adapter, directory, name, disposition=FILE_OPEN, delete=True,
            private=False,
        )
        if identity is not None and handle.identity != identity:
            return False
        adapter.api.delete_handle(handle.raw)
        return True
    except WindowsCallError as error:
        return error.winerror in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}
    except Exception:
        return False
    finally:
        if handle is not None:
            try:
                adapter.close(handle)
            except Exception:
                pass


def _write_temp(
    adapter: Win32Adapter, directory: WinHandle, prefix: str, content: bytes
) -> WinHandle:
    name = _temporary_path(directory, prefix).name
    handle: WinHandle | None = None
    try:
        handle = _open_file(
            adapter, directory, name, disposition=FILE_CREATE, write=True,
            delete=True,
        )
        adapter.api.write_all(handle.raw, content)
        adapter.api.flush(handle.raw)
        adapter.verify_handle(handle)
        result = handle
        handle = None
        return result
    except Exception as error:
        leftover = ""
        identity = None if handle is None else handle.identity
        if handle is not None:
            try:
                adapter.close(handle)
            except Exception:
                pass
            handle = None
        if not _best_effort_delete(adapter, directory, name, identity):
            leftover = (
                f" A leftover temporary file named {name} remains; inspect and "
                "remove it before retrying."
            )
        raise WindowsStateError(
            f"Unable to write onboarding state safely.{leftover}"
        ) from error
    finally:
        if handle is not None:
            try:
                adapter.close(handle)
            except Exception:
                pass


def _open_and_match(
    adapter: Win32Adapter, directory: WinHandle, name: str, identity: FileIdentity
) -> WinHandle:
    handle = _open_file(adapter, directory, name, disposition=FILE_OPEN)
    if handle.identity != identity:
        adapter.close(handle)
        raise WindowsStateError("Onboarding state changed during the operation.")
    return handle


def save_current_bytes(
    home: os.PathLike[str] | str,
    content: bytes,
    *,
    adapter: Win32Adapter | None = None,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Path:
    if not isinstance(content, bytes) or len(content) > _MAX_PAYLOAD_BYTES:
        raise WindowsStateError("Onboarding state exceeds the safe size limit.")
    active = adapter or Win32Adapter()
    with _locked_profile(home, adapter=active, lock_timeout=lock_timeout) as profile:
        temporary = _write_temp(active, profile.root, "current.json", content)
        committed = False
        try:
            destination = profile.root.path / "current.json"
            active.api.rename_handle(
                temporary.raw, profile.root.raw, "current.json", replace=True
            )
            committed = True
            temporary.path = destination
            current = _open_and_match(
                active, profile.root, "current.json", temporary.identity
            )
            try:
                active.api.verify_private_acl(current.raw)
                active.api.flush(current.raw)
            finally:
                active.close(current)
            _verify_profile(profile, active)
            active.flush_directory(profile.root)
            return destination
        except Exception as error:
            cleanup = ""
            temp_name = temporary.path.name
            temp_identity = temporary.identity
            active.close(temporary)
            if not committed and not _best_effort_delete(
                active, profile.root, temp_name, temp_identity
            ):
                cleanup = (
                    f" A leftover temporary file named {temp_name} remains; "
                    "inspect and remove it before retrying."
                )
            if committed:
                raise _current_partial_effect_error() from error
            raise WindowsStateError(
                f"Unable to write onboarding state safely.{cleanup}"
            ) from error
        finally:
            if not temporary.closed:
                active.close(temporary)


def load_current_bytes(
    home: os.PathLike[str] | str,
    *,
    adapter: Win32Adapter | None = None,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> bytes | None:
    active = adapter or Win32Adapter()
    with _locked_profile(home, adapter=active, lock_timeout=lock_timeout) as profile:
        try:
            current = _open_optional_current(profile, active)
        except Exception as error:
            raise _translate(error, "Unable to read saved onboarding state safely.") from error
        if current is None:
            return None
        try:
            return _read_record(active, current)
        except Exception as error:
            raise _translate(error, "Unable to read saved onboarding state safely.") from error
        finally:
            active.close(current)


def complete_current_bytes(
    home: os.PathLike[str] | str,
    history_name: str,
    validator: Callable[[bytes], object],
    *,
    adapter: Win32Adapter | None = None,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Path:
    if not history_name or "/" in history_name or "\\" in history_name:
        raise WindowsStateError("The onboarding history name is invalid.")
    active = adapter or Win32Adapter()
    with _locked_profile(home, adapter=active, lock_timeout=lock_timeout) as profile:
        current = _open_optional_current(profile, active, delete=True)
        if current is None:
            raise WindowsStateError("There is no active onboarding state to complete.")
        try:
            content = _read_record(active, current)
            validator(content)
            temporary = _write_temp(active, profile.history, history_name, content)
            history = profile.history.path / history_name
            committed = False
            try:
                active.api.rename_handle(
                    temporary.raw, profile.history.raw, history_name, replace=False
                )
                committed = True
                temporary.path = history
                archived = _open_and_match(
                    active, profile.history, history_name, temporary.identity
                )
                try:
                    active.api.verify_private_acl(archived.raw)
                    active.api.flush(archived.raw)
                finally:
                    active.close(archived)
                active.flush_directory(profile.history)
            except WindowsCallError as error:
                cleanup = ""
                temp_name = temporary.path.name
                temp_identity = temporary.identity
                active.close(temporary)
                if not committed and not _best_effort_delete(
                    active, profile.history, temp_name, temp_identity
                ):
                    cleanup = f" Leftover temporary: history/{temp_name}."
                if error.winerror in {ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS}:
                    raise WindowsStateError(
                        f"An onboarding history entry already exists.{cleanup}"
                    ) from error
                if committed:
                    leftover = temp_name if cleanup else None
                    raise _history_partial_effect_error(
                        history_name, leftover
                    ) from error
                raise WindowsStateError(
                    f"Unable to archive onboarding state safely.{cleanup}"
                ) from error
            except Exception as error:
                cleanup = ""
                temp_name = temporary.path.name
                temp_identity = temporary.identity
                active.close(temporary)
                if not committed and not _best_effort_delete(
                    active, profile.history, temp_name, temp_identity
                ):
                    cleanup = f" Leftover temporary: history/{temp_name}."
                if committed:
                    leftover = temp_name if cleanup else None
                    raise _history_partial_effect_error(
                        history_name, leftover
                    ) from error
                raise WindowsStateError(
                    f"Unable to archive onboarding state safely.{cleanup}"
                ) from error
            finally:
                if not temporary.closed:
                    active.close(temporary)

            try:
                active.verify_handle(current)
                active.api.delete_handle(current.raw)
                active.close(current)
                current = None
                active.flush_directory(profile.root)
            except Exception as error:
                raise WindowsStateError(
                    f"Completion archived history at history/{history_name}, but "
                    "current.json remains active or its removal durability is unknown; "
                    "inspect both before retrying."
                ) from error
            return history
        finally:
            if current is not None:
                active.close(current)


def clear_current_bytes(
    home: os.PathLike[str] | str,
    validator: Callable[[bytes], object],
    *,
    adapter: Win32Adapter | None = None,
    lock_timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> bool:
    active = adapter or Win32Adapter()
    with _locked_profile(home, adapter=active, lock_timeout=lock_timeout) as profile:
        current = _open_optional_current(profile, active, delete=True)
        if current is None:
            return False
        removed = False
        try:
            validator(_read_record(active, current))
            active.verify_handle(current)
            active.api.delete_handle(current.raw)
            active.close(current)
            current = None
            removed = True
            active.flush_directory(profile.root)
            return True
        except Exception as error:
            if removed:
                raise WindowsStateError(
                    "current.json was removed, but removal durability is unconfirmed; "
                    "inspect current onboarding state before retrying."
                ) from error
            raise _translate(error, "Unable to clear onboarding state safely.") from error
        finally:
            if current is not None:
                active.close(current)


def native_acl_is_private(path: os.PathLike[str] | str) -> bool:
    adapter = Win32Adapter()
    target = Path(path)
    directory = target.is_dir()
    handle = adapter.open_verified(
        target, directory=directory, creation=OPEN_EXISTING,
        access=FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE,
        private=False,
    )
    try:
        adapter.api.verify_private_acl(handle.raw)
        return True
    except Exception:
        return False
    finally:
        adapter.close(handle)
