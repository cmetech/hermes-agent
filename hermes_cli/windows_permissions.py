"""Strict, stdlib-only Windows ACL boundaries for credential artifacts."""

from __future__ import annotations

import ctypes
import os
import re
import stat
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class WindowsAclError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowsAclInspection:
    secure: bool
    detail: str | None


@dataclass(frozen=True)
class _WindowsPrivateProbeResult:
    """Outcome of one handle-relative synthetic private-file probe."""

    failure_type: str | None
    cleanup_failed: bool
    failure_stage: str | None = None
    cleanup_stage: str | None = None


OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SE_FILE_OBJECT = 1
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
FILE_PRIVATE_MASK = 0x0012019F
DIRECTORY_PRIVATE_MASK = 0x001201FF

_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_ERROR_INSUFFICIENT_BUFFER = 122
_SDDL_REVISION_1 = 1
_SE_DACL_PROTECTED = 0x1000
_ACL_SIZE_INFORMATION_CLASS = 2
_ACCESS_ALLOWED_ACE_TYPE = 0x00
_OBJECT_INHERIT_ACE = 0x01
_CONTAINER_INHERIT_ACE = 0x02
_SID_RE = re.compile(r"^S-1-[0-9]+(?:-[0-9]+)+$")
_OBJ_CASE_INSENSITIVE = 0x00000040
_OBJ_DONT_REPARSE = 0x00001000
_FILE_OPEN = 0x00000001
_FILE_CREATE = 0x00000002
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_READ_DATA = 0x00000001
_FILE_WRITE_DATA = 0x00000002
_SYNCHRONIZE = 0x00100000
_DELETE = 0x00010000
_FILE_ADD_FILE = 0x00000002
_FILE_ADD_SUBDIRECTORY = 0x00000004
_FILE_TRAVERSE = 0x00000020
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_DISPOSITION_INFO_CLASS = 4
_FILE_RENAME_INFO_CLASS = 3
_STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_PROBE_DIRECTORY_RE = re.compile(r"^\.secret-write-probe-[0-9a-f]{32}$")
_PROBE_FILE_NAME = "sentinel"
_PROBE_CONTENTS = b"hermes-secret-write-probe\n"


@dataclass(frozen=True)
class _FileIdentity:
    volume_serial: int
    file_index: int


@dataclass(frozen=True)
class _HandleMetadata:
    attributes: int
    identity: _FileIdentity


@dataclass(frozen=True)
class _CurrentUserSid:
    text: str
    pointer: int
    storage: object


@dataclass(frozen=True)
class _AclState:
    owner_matches: bool
    dacl_present: bool
    protected: bool
    ace_count: int
    ace_type: int
    ace_flags: int
    ace_mask: int
    ace_sid_matches: bool


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


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    ]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
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
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


class _FILE_RENAME_INFO(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOLEAN),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


class _WindowsCallError(OSError):
    def __init__(self, operation: str, code: int) -> None:
        super().__init__(code, operation)
        self.operation = operation
        self.winerror = code


class _ProbeRootFailure(RuntimeError):
    def __init__(self, stage: str, failure_type: str) -> None:
        super().__init__(stage)
        self.stage = stage
        self.failure_type = failure_type


def _probe_failure_category(exc: Exception) -> str:
    if not isinstance(exc, _WindowsCallError):
        return "other"
    code = int(exc.winerror) & 0xFFFFFFFF
    if code in {5, 0xC0000022}:
        return "access-denied"
    if code in {32, 0xC0000043}:
        return "sharing-violation"
    if code in {87, 0xC000000D}:
        return "invalid-parameter"
    if code in {2, 3, 0xC0000034, 0xC000003A}:
        return "not-found"
    if code == 0xC000050B:
        return "reparse"
    return "other"


def _probe_root_failure(stage: str, exc: Exception) -> _ProbeRootFailure:
    category = _probe_failure_category(exc)
    return _ProbeRootFailure(f"probe-open-root-{stage}-{category}", type(exc).__name__)


class _WindowsAclApi:
    """Minimal ctypes adapter for handle-authoritative Windows ACL operations."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsAclError("native Windows ACL support is unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        kernel32 = self.kernel32
        advapi32 = self.advapi32

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
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.ULONG),
        ]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        advapi32.GetSecurityDescriptorDacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.GetSecurityInfo.restype = wintypes.DWORD
        advapi32.SetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        advapi32.SetSecurityInfo.restype = wintypes.DWORD
        advapi32.GetSecurityDescriptorControl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.WORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
        advapi32.GetAclInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        ]
        advapi32.GetAclInformation.restype = wintypes.BOOL
        advapi32.GetAce.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.GetAce.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        advapi32.EqualSid.restype = wintypes.BOOL
        self.ntdll.NtCreateFile.argtypes = [
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
            wintypes.ULONG,
        ]
        self.ntdll.NtCreateFile.restype = wintypes.LONG

    @staticmethod
    def _raise(operation: str, code: int | None = None) -> None:
        raise _WindowsCallError(
            operation, ctypes.get_last_error() if code is None else code
        )

    def open_handle(self, path: Path, *, access: int, flags: int) -> int:
        handle = self.kernel32.CreateFileW(
            str(path),
            access,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            self._raise("open Windows ACL object")
        return int(handle)

    def open_bound_handle(self, path: Path, *, access: int, flags: int) -> int:
        handle = self.kernel32.CreateFileW(
            str(path),
            access,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            self._raise("open Windows private directory")
        return int(handle)

    def close_handle(self, handle: int) -> None:
        if not self.kernel32.CloseHandle(handle):
            self._raise("close Windows ACL object")

    def _open_relative(
        self,
        parent: int,
        name: str,
        *,
        directory: bool,
        access: int,
        disposition: int,
        exclusive: bool,
    ) -> int:
        name_buffer = ctypes.create_unicode_buffer(name)
        encoded_length = len(name.encode("utf-16-le"))
        unicode_name = _UNICODE_STRING(
            encoded_length,
            encoded_length + ctypes.sizeof(ctypes.c_wchar),
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            wintypes.HANDLE(parent),
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE | _OBJ_DONT_REPARSE,
            None,
            None,
        )
        handle = wintypes.HANDLE()
        status = _IO_STATUS_BLOCK()
        options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
        options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        share_access = 0 if exclusive else _FILE_SHARE_READ | _FILE_SHARE_WRITE
        result = self.ntdll.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(status),
            None,
            0,
            share_access,
            disposition,
            options,
            None,
            0,
        )
        if result < 0:
            self._raise("open relative Windows probe object", int(result))
        return int(handle.value)

    def open_relative_directory(self, parent: int, name: str, *, access: int) -> int:
        return self._open_relative(
            parent,
            name,
            directory=True,
            access=access,
            disposition=_FILE_OPEN,
            exclusive=False,
        )

    def create_relative_directory(self, parent: int, name: str, *, access: int) -> int:
        return self._open_relative(
            parent,
            name,
            directory=True,
            access=access,
            disposition=_FILE_CREATE,
            exclusive=False,
        )

    def create_relative(
        self, parent: int, name: str, *, directory: bool, access: int
    ) -> int:
        return self._open_relative(
            parent,
            name,
            directory=directory,
            access=access,
            disposition=_FILE_CREATE,
            exclusive=True,
        )

    def open_relative_file(self, parent: int, name: str, *, access: int) -> int:
        return self._open_relative(
            parent,
            name,
            directory=False,
            access=access,
            disposition=_FILE_OPEN,
            exclusive=True,
        )

    def write_handle(self, handle: int, data: bytes) -> None:
        buffer = ctypes.create_string_buffer(data)
        written = wintypes.DWORD()
        if not self.kernel32.WriteFile(
            handle, buffer, len(data), ctypes.byref(written), None
        ) or written.value != len(data):
            self._raise("write Windows probe artifact")

    def read_handle(self, handle: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self.kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            self._raise("read Windows private file")
        return bytes(buffer.raw[: read.value])

    def flush_handle(self, handle: int) -> None:
        if not self.kernel32.FlushFileBuffers(handle):
            self._raise("flush Windows probe artifact")

    def delete_on_close(self, handle: int) -> None:
        self.set_delete_on_close(handle, True)

    def set_delete_on_close(self, handle: int, delete: bool) -> None:
        disposition = _FILE_DISPOSITION_INFO(bool(delete))
        if not self.kernel32.SetFileInformationByHandle(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            self._raise("set Windows private file disposition")

    def rename_handle(
        self,
        handle: int,
        parent: int,
        name: str,
        *,
        replace: bool,
    ) -> None:
        encoded_name = name.encode("utf-16-le")
        size = ctypes.sizeof(_FILE_RENAME_INFO) + len(encoded_name) + 2
        storage = ctypes.create_string_buffer(size)
        information = _FILE_RENAME_INFO.from_buffer(storage)
        information.ReplaceIfExists = bool(replace)
        information.RootDirectory = parent
        information.FileNameLength = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(storage) + _FILE_RENAME_INFO.FileName.offset,
            encoded_name,
            len(encoded_name),
        )
        if not self.kernel32.SetFileInformationByHandle(
            handle,
            _FILE_RENAME_INFO_CLASS,
            storage,
            size,
        ):
            self._raise("publish Windows private file")

    def handle_metadata(self, handle: int) -> _HandleMetadata:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not self.kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            self._raise("inspect Windows ACL object")
        file_index = (int(information.nFileIndexHigh) << 32) | int(
            information.nFileIndexLow
        )
        return _HandleMetadata(
            attributes=int(information.dwFileAttributes),
            identity=_FileIdentity(int(information.dwVolumeSerialNumber), file_index),
        )

    def _sid_text(self, sid: int) -> str:
        converted = wintypes.LPWSTR()
        if not self.advapi32.ConvertSidToStringSidW(
            ctypes.c_void_p(sid), ctypes.byref(converted)
        ):
            self._raise("identify current Windows user")
        try:
            value = converted.value or ""
        finally:
            if converted:
                self.kernel32.LocalFree(ctypes.cast(converted, ctypes.c_void_p))
        if len(value) > 184 or _SID_RE.fullmatch(value) is None:
            raise WindowsAclError("current-user SID is unavailable")
        return value

    def current_user(self) -> _CurrentUserSid:
        token = wintypes.HANDLE()
        if not self.advapi32.OpenProcessToken(
            self.kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            self._raise("open current Windows user token")
        try:
            required = wintypes.DWORD()
            first_result = self.advapi32.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                None,
                0,
                ctypes.byref(required),
            )
            first_error = ctypes.get_last_error()
            if first_result or (
                first_error != _ERROR_INSUFFICIENT_BUFFER or not required.value
            ):
                self._raise("size current Windows user token", first_error)
            storage = ctypes.create_string_buffer(required.value)
            if not self.advapi32.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                storage,
                required.value,
                ctypes.byref(required),
            ):
                self._raise("read current Windows user token")
            sid = _TOKEN_USER.from_buffer(storage).User.Sid
            if not sid:
                raise WindowsAclError("current-user SID is unavailable")
            pointer = int(sid)
            return _CurrentUserSid(self._sid_text(pointer), pointer, storage)
        finally:
            if token.value:
                self.kernel32.CloseHandle(token)

    def read_acl(
        self,
        handle: int,
        current_user: _CurrentUserSid,
        security_information: int,
    ) -> _AclState:
        descriptor = ctypes.c_void_p()
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        result = self.advapi32.GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            security_information,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            self._raise("inspect Windows ACL", int(result))
        try:
            owner_matches = bool(
                owner.value
                and self.advapi32.EqualSid(owner, ctypes.c_void_p(current_user.pointer))
            )
            if not dacl.value:
                return _AclState(owner_matches, False, False, 0, -1, -1, -1, False)

            control = wintypes.WORD()
            revision = wintypes.DWORD()
            if not self.advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                self._raise("inspect Windows DACL control")
            protected = bool(control.value & _SE_DACL_PROTECTED)

            information = _ACL_SIZE_INFORMATION()
            if not self.advapi32.GetAclInformation(
                dacl,
                ctypes.byref(information),
                ctypes.sizeof(information),
                _ACL_SIZE_INFORMATION_CLASS,
            ):
                self._raise("inspect Windows DACL")
            ace_count = int(information.AceCount)
            if ace_count != 1:
                return _AclState(
                    owner_matches,
                    True,
                    protected,
                    ace_count,
                    -1,
                    -1,
                    -1,
                    False,
                )

            ace_pointer = ctypes.c_void_p()
            if not self.advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
                self._raise("inspect Windows DACL entry")
            if not ace_pointer.value:
                self._raise("inspect Windows DACL entry", 0)
            header = ctypes.cast(ace_pointer, ctypes.POINTER(_ACE_HEADER)).contents
            ace_type = int(header.AceType)
            ace_flags = int(header.AceFlags)
            ace_mask = -1
            ace_sid_matches = False
            if ace_type == _ACCESS_ALLOWED_ACE_TYPE:
                allowed = ctypes.cast(
                    ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)
                ).contents
                ace_mask = int(allowed.Mask)
                sid_pointer = (
                    int(ace_pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
                )
                ace_sid_matches = bool(
                    self.advapi32.EqualSid(
                        ctypes.c_void_p(sid_pointer),
                        ctypes.c_void_p(current_user.pointer),
                    )
                )
            return _AclState(
                owner_matches,
                True,
                protected,
                ace_count,
                ace_type,
                ace_flags,
                ace_mask,
                ace_sid_matches,
            )
        finally:
            if descriptor.value:
                self.kernel32.LocalFree(descriptor)

    def set_dacl(
        self,
        handle: int,
        sddl: str,
        security_information: int,
    ) -> None:
        descriptor = ctypes.c_void_p()
        size = wintypes.ULONG()
        if not self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(size),
        ):
            self._raise("build private Windows DACL")
        try:
            dacl_present = wintypes.BOOL()
            dacl_defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not self.advapi32.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(dacl_present),
                ctypes.byref(dacl),
                ctypes.byref(dacl_defaulted),
            ):
                self._raise("read private Windows DACL")
            if not dacl_present.value or not dacl.value:
                self._raise("read private Windows DACL", 0)
            result = self.advapi32.SetSecurityInfo(
                handle,
                SE_FILE_OBJECT,
                security_information,
                None,
                None,
                dacl,
                None,
            )
            if result:
                self._raise("apply private Windows DACL", int(result))
        finally:
            if descriptor.value:
                self.kernel32.LocalFree(descriptor)


def _native_api() -> _WindowsAclApi:
    return _WindowsAclApi()


def _is_reparse_point(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and getattr(info, "st_file_attributes", 0) & flag)


def _validated_direct_path(path: Path, *, directory: bool) -> tuple[int, int]:
    kind = "directory" if directory else "file"
    try:
        info = Path(path).lstat()
    except (OSError, TypeError, ValueError):
        raise WindowsAclError("cannot inspect Windows ACL path") from None
    if _is_reparse_point(info):
        raise WindowsAclError(f"Windows ACL {kind} path is a reparse point")
    if stat.S_ISLNK(info.st_mode):
        raise WindowsAclError(f"Windows ACL {kind} path is a symbolic link")
    type_matches = (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    )
    if not type_matches:
        raise WindowsAclError(f"Windows ACL {kind} path has the wrong type")
    return (info.st_dev, info.st_ino)


def _verify_metadata(metadata: _HandleMetadata, *, directory: bool) -> None:
    if metadata.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsAclError("Windows ACL handle is a reparse point")
    if bool(metadata.attributes & _FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise WindowsAclError("Windows ACL handle has the wrong type")


def _inspection_for_state(state: _AclState, *, directory: bool) -> WindowsAclInspection:
    if not state.owner_matches:
        return WindowsAclInspection(False, "ACL owner does not match the current user")
    if not state.dacl_present:
        return WindowsAclInspection(False, "ACL DACL is null")
    if not state.protected:
        return WindowsAclInspection(False, "ACL inheritance is enabled")
    if state.ace_count != 1:
        return WindowsAclInspection(False, "expected exactly one explicit ACE")
    expected_flags = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE if directory else 0
    expected_mask = DIRECTORY_PRIVATE_MASK if directory else FILE_PRIVATE_MASK
    if (
        state.ace_type != _ACCESS_ALLOWED_ACE_TYPE
        or state.ace_flags != expected_flags
        or state.ace_mask != expected_mask
        or not state.ace_sid_matches
    ):
        return WindowsAclInspection(
            False, "the explicit ACE does not match the current-user rule"
        )
    return WindowsAclInspection(True, None)


def _open_flags(*, directory: bool) -> int:
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    return flags


def _operate_handle(
    api: _WindowsAclApi, handle: int, *, directory: bool, apply: bool
) -> WindowsAclInspection | None:
    """Apply or inspect the private ACL on one already-held native handle."""
    initial = api.handle_metadata(handle)
    _verify_metadata(initial, directory=directory)
    current_user = api.current_user()
    security_information = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    before = api.read_acl(handle, current_user, security_information)

    if apply:
        if not before.owner_matches:
            raise WindowsAclError("ACL owner does not match the current user")
        inheritance = "OICI" if directory else ""
        mask = DIRECTORY_PRIVATE_MASK if directory else FILE_PRIVATE_MASK
        sddl = f"D:P(A;{inheritance};0x{mask:08x};;;{current_user.text})"
        api.set_dacl(
            handle,
            sddl,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        )
        after = api.read_acl(handle, current_user, security_information)
        inspection = _inspection_for_state(after, directory=directory)
        if not inspection.secure:
            raise WindowsAclError(
                inspection.detail or "Windows ACL verification failed"
            )
    else:
        inspection = _inspection_for_state(before, directory=directory)

    final = api.handle_metadata(handle)
    _verify_metadata(final, directory=directory)
    if final.identity != initial.identity:
        raise WindowsAclError("Windows ACL object changed during operation")
    return None if apply else inspection


class _WindowsPrivateDirectory:
    """Held private directory used for handle-relative secret-file operations."""

    def __init__(self, api: _WindowsAclApi, handle: int) -> None:
        self._api = api
        self._handle: int | None = handle

    def __enter__(self) -> _WindowsPrivateDirectory:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except WindowsAclError:
            if exc_type is None:
                raise

    def _require_handle(self) -> int:
        if self._handle is None:
            raise WindowsAclError("Windows private directory is closed")
        return self._handle

    def create_file(self, name: str) -> _WindowsPrivateFile:
        name = _relative_private_name(name)
        handle: int | None = None
        try:
            handle = self._api.create_relative(
                self._require_handle(),
                name,
                directory=False,
                access=(
                    READ_CONTROL
                    | WRITE_DAC
                    | _DELETE
                    | _SYNCHRONIZE
                    | _FILE_READ_ATTRIBUTES
                    | _FILE_WRITE_DATA
                ),
            )
            _operate_handle(self._api, handle, directory=False, apply=True)
            self._api.set_delete_on_close(handle, True)
            private_file = _WindowsPrivateFile(
                self._api,
                handle,
                parent_handle=self._require_handle(),
                delete_armed=True,
            )
            handle = None
            return private_file
        except WindowsAclError:
            raise
        except Exception as exc:
            if _relative_name_collision(exc):
                raise FileExistsError("Windows private file name is reserved") from None
            raise WindowsAclError("Windows private file creation failed") from None
        finally:
            if handle is not None:
                try:
                    self._api.set_delete_on_close(handle, True)
                except Exception:
                    pass
                try:
                    self._api.close_handle(handle)
                except Exception:
                    pass

    def open_file(self, name: str) -> _WindowsPrivateFile | None:
        name = _relative_private_name(name)
        handle: int | None = None
        try:
            handle = self._api.open_relative_file(
                self._require_handle(),
                name,
                access=(
                    READ_CONTROL
                    | WRITE_DAC
                    | _SYNCHRONIZE
                    | _FILE_READ_ATTRIBUTES
                    | _FILE_READ_DATA
                ),
            )
            _operate_handle(self._api, handle, directory=False, apply=True)
            private_file = _WindowsPrivateFile(
                self._api,
                handle,
                parent_handle=self._require_handle(),
                delete_armed=False,
            )
            handle = None
            return private_file
        except Exception as exc:
            if _missing_relative_object(exc):
                return None
            if isinstance(exc, WindowsAclError):
                raise
            raise WindowsAclError("Windows private file open failed") from None
        finally:
            if handle is not None:
                try:
                    self._api.close_handle(handle)
                except Exception:
                    pass

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            self._api.close_handle(handle)
        except Exception:
            raise WindowsAclError("Windows ACL operation failed") from None


class _WindowsPrivateFile:
    """Private native file held from ACL application through publication."""

    def __init__(
        self,
        api: _WindowsAclApi,
        handle: int,
        *,
        parent_handle: int,
        delete_armed: bool,
    ) -> None:
        self._api = api
        self._handle: int | None = handle
        self._parent_handle = parent_handle
        self._delete_armed = delete_armed
        self._published = not delete_armed

    def __enter__(self) -> _WindowsPrivateFile:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _require_handle(self) -> int:
        if self._handle is None:
            raise WindowsAclError("Windows private file is closed")
        return self._handle

    def _inspect(self) -> WindowsAclInspection:
        result = _operate_handle(
            self._api,
            self._require_handle(),
            directory=False,
            apply=False,
        )
        assert isinstance(result, WindowsAclInspection)
        return result

    def write_all(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise WindowsAclError("Windows private file payload is invalid")
        try:
            self._api.write_handle(self._require_handle(), data)
        except WindowsAclError:
            raise
        except Exception:
            raise WindowsAclError("Windows private file write failed") from None

    def read_all(self, *, max_bytes: int) -> bytes:
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        ):
            raise WindowsAclError("Windows private file size bound is invalid")
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = self._api.read_handle(self._require_handle(), 64 * 1024)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > max_bytes:
                    raise WindowsAclError("Windows private file exceeds its size bound")
                chunks.append(chunk)
        except WindowsAclError:
            raise
        except Exception:
            raise WindowsAclError("Windows private file read failed") from None

    def flush(self) -> None:
        try:
            self._api.flush_handle(self._require_handle())
        except WindowsAclError:
            raise
        except Exception:
            raise WindowsAclError("Windows private file flush failed") from None

    def publish(self, name: str) -> None:
        name = _relative_private_name(name)
        handle = self._require_handle()
        try:
            self._api.set_delete_on_close(handle, False)
            self._delete_armed = False
            # A simple name with a null root renames within the held file's directory.
            self._api.rename_handle(
                handle,
                0,
                name,
                replace=True,
            )
            inspection = self._inspect()
            if not inspection.secure:
                raise WindowsAclError("Windows private file ACL is not private")
            self._published = True
        except Exception:
            try:
                self._api.set_delete_on_close(handle, True)
                self._delete_armed = True
            except Exception:
                pass
            raise WindowsAclError("Windows private file publication failed") from None

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        cleanup_failed = False
        if not self._published and not self._delete_armed:
            try:
                self._api.set_delete_on_close(handle, True)
                self._delete_armed = True
            except Exception:
                cleanup_failed = True
        try:
            self._api.close_handle(handle)
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            raise WindowsAclError("Windows private file cleanup failed") from None


def _relative_private_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 128:
        raise WindowsAclError("Windows private file name is invalid")
    if (
        name in {".", ".."}
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        or ":" in name
    ):
        raise WindowsAclError("Windows private file name is not relative")
    return name


def open_private_directory(path: Path) -> _WindowsPrivateDirectory:
    """Protect, verify, and hold one directory for relative secret-file work."""
    path = Path(path)
    _validated_direct_path(path, directory=True)
    try:
        api = _native_api()
    except WindowsAclError:
        raise
    except Exception:
        raise WindowsAclError("Windows ACL operation failed") from None
    handle: int | None = None
    try:
        handle = api.open_bound_handle(
            path,
            access=(
                READ_CONTROL
                | WRITE_DAC
                | _FILE_READ_ATTRIBUTES
                | _FILE_TRAVERSE
                | _FILE_ADD_FILE
            ),
            flags=_open_flags(directory=True),
        )
        _operate_handle(api, handle, directory=True, apply=True)
        binding = _WindowsPrivateDirectory(api, handle)
        handle = None
        return binding
    except WindowsAclError:
        raise
    except Exception:
        raise WindowsAclError("Windows ACL operation failed") from None
    finally:
        if handle is not None:
            try:
                api.close_handle(handle)
            except Exception:
                pass


def _operate(
    path: Path, *, directory: bool, apply: bool
) -> WindowsAclInspection | None:
    path = Path(path)
    _validated_direct_path(path, directory=directory)
    try:
        api = _native_api()
    except WindowsAclError:
        raise
    except Exception:
        raise WindowsAclError("Windows ACL operation failed") from None
    handle: int | None = None
    primary_failed = False
    try:
        access = READ_CONTROL | _FILE_READ_ATTRIBUTES
        if apply:
            access |= WRITE_DAC
        handle = api.open_handle(
            path, access=access, flags=_open_flags(directory=directory)
        )
        return _operate_handle(api, handle, directory=directory, apply=apply)
    except WindowsAclError:
        primary_failed = True
        raise
    except Exception:
        primary_failed = True
        raise WindowsAclError("Windows ACL operation failed") from None
    finally:
        if handle is not None:
            try:
                api.close_handle(handle)
            except Exception:
                if not primary_failed:
                    raise WindowsAclError("Windows ACL operation failed") from None


def _relative_probe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 128:
        raise WindowsAclError("Windows probe artifact name is invalid")
    if (
        name in {".", ".."}
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        or ":" in name
    ):
        raise WindowsAclError("Windows probe artifact name is not relative")
    return name


def _probe_root_parts(root: Path) -> tuple[Path, tuple[str, ...]]:
    try:
        if not root.is_absolute() or not root.anchor:
            raise WindowsAclError("Windows probe root is not absolute")
        anchor = Path(root.anchor)
        components = tuple(_relative_probe_name(part) for part in root.parts[1:])
    except WindowsAclError:
        raise
    except Exception:
        raise WindowsAclError("Windows probe root is invalid") from None
    if not components:
        raise WindowsAclError("Windows probe root cannot be a filesystem anchor")
    return anchor, components


def _missing_relative_object(exc: Exception) -> bool:
    return (
        isinstance(exc, _WindowsCallError)
        and (int(exc.winerror) & 0xFFFFFFFF) == _STATUS_OBJECT_NAME_NOT_FOUND
    )


def _relative_name_collision(exc: Exception) -> bool:
    return (
        isinstance(exc, _WindowsCallError)
        and (int(exc.winerror) & 0xFFFFFFFF) == _STATUS_OBJECT_NAME_COLLISION
    )


def _open_probe_root(
    api: _WindowsAclApi,
    root: Path,
    held: list[tuple[int, _FileIdentity | None]],
) -> int:
    """Walk or create one absolute directory from an immutable anchor handle."""
    try:
        anchor, components = _probe_root_parts(root)
    except Exception as exc:
        raise _probe_root_failure("parse", exc) from None
    base_access = _SYNCHRONIZE | _FILE_READ_ATTRIBUTES
    path_handles: list[tuple[int, _FileIdentity]] = []
    component_names: list[str | None] = [None]
    can_create_child: list[bool] = [False]

    def track(handle: int, stage: str) -> _HandleMetadata:
        index = len(held)
        held.append((handle, None))
        try:
            metadata = api.handle_metadata(handle)
            _verify_metadata(metadata, directory=True)
        except Exception as exc:
            raise _probe_root_failure(stage, exc) from None
        held[index] = (handle, metadata.identity)
        return metadata

    try:
        anchor_handle = api.open_handle(
            anchor,
            access=0,
            flags=_open_flags(directory=True),
        )
    except Exception as exc:
        raise _probe_root_failure("anchor-open", exc) from None
    anchor_metadata = track(anchor_handle, "anchor-validate")
    path_handles.append((anchor_handle, anchor_metadata.identity))

    for index, component in enumerate(components):
        final = index == len(components) - 1
        access = base_access | (_FILE_ADD_SUBDIRECTORY if final else 0)
        parent_handle = path_handles[-1][0]
        try:
            handle = api.open_relative_directory(
                parent_handle, component, access=access
            )
        except Exception as exc:
            if not _missing_relative_object(exc):
                raise _probe_root_failure("component-open", exc) from None
            if not can_create_child[-1]:
                parent_identity = path_handles[-1][1]
                try:
                    if len(path_handles) == 1:
                        replacement = api.open_handle(
                            anchor,
                            access=base_access | _FILE_ADD_SUBDIRECTORY,
                            flags=_open_flags(directory=True),
                        )
                    else:
                        parent_name = component_names[-1]
                        assert parent_name is not None
                        replacement = api.open_relative_directory(
                            path_handles[-2][0],
                            parent_name,
                            access=base_access | _FILE_ADD_SUBDIRECTORY,
                        )
                except Exception as replacement_exc:
                    raise _probe_root_failure(
                        "parent-upgrade", replacement_exc
                    ) from None
                replacement_metadata = track(replacement, "parent-upgrade")
                if replacement_metadata.identity != parent_identity:
                    raise _probe_root_failure(
                        "parent-upgrade",
                        WindowsAclError(
                            "Windows probe root component changed while opening"
                        ),
                    ) from None
                path_handles[-1] = (replacement, replacement_metadata.identity)
                can_create_child[-1] = True
                parent_handle = replacement
            try:
                handle = api.create_relative_directory(
                    parent_handle,
                    component,
                    access=base_access | _FILE_ADD_SUBDIRECTORY,
                )
            except Exception as create_exc:
                raise _probe_root_failure("component-create", create_exc) from None
            access |= _FILE_ADD_SUBDIRECTORY

        metadata = track(handle, "component-validate")
        path_handles.append((handle, metadata.identity))
        component_names.append(component)
        can_create_child.append(bool(access & _FILE_ADD_SUBDIRECTORY))

    for handle, identity in held:
        if identity is None:
            continue
        try:
            metadata = api.handle_metadata(handle)
            _verify_metadata(metadata, directory=True)
        except Exception as exc:
            raise _probe_root_failure("revalidate", exc) from None
        if metadata.identity != identity:
            raise _probe_root_failure(
                "revalidate",
                WindowsAclError(
                    "Windows probe root component changed during traversal"
                ),
            ) from None
    return path_handles[-1][0]


def _run_private_acl_write_probe(
    root: Path, *, directory_name: str
) -> _WindowsPrivateProbeResult:
    """Create, ACL-inspect, and delete synthetic artifacts relative to one root handle."""
    if not isinstance(directory_name, str) or not _PROBE_DIRECTORY_RE.fullmatch(
        directory_name
    ):
        raise WindowsAclError("Windows probe directory name is invalid")
    root = Path(root)
    api: _WindowsAclApi | None = None
    root_handles: list[tuple[int, _FileIdentity | None]] = []
    directory_handle: int | None = None
    file_handle: int | None = None
    failure_type: str | None = None
    failure_stage: str | None = None
    cleanup_failed = False
    cleanup_stage: str | None = None
    stage = "probe-native-api"
    try:
        api = _native_api()
        stage = "probe-open-root"
        root_handle = _open_probe_root(api, root, root_handles)
        stage = "probe-create-directory"
        directory_handle = api.create_relative(
            root_handle,
            directory_name,
            directory=True,
            access=(
                READ_CONTROL
                | WRITE_DAC
                | _DELETE
                | _SYNCHRONIZE
                | _FILE_READ_ATTRIBUTES
                | _FILE_ADD_FILE
            ),
        )
        stage = "probe-protect-directory"
        _operate_handle(api, directory_handle, directory=True, apply=True)
        stage = "probe-create-file"
        file_handle = api.create_relative(
            directory_handle,
            _PROBE_FILE_NAME,
            directory=False,
            access=(
                READ_CONTROL
                | WRITE_DAC
                | _DELETE
                | _SYNCHRONIZE
                | _FILE_READ_ATTRIBUTES
                | _FILE_WRITE_DATA
            ),
        )
        stage = "probe-protect-file"
        _operate_handle(api, file_handle, directory=False, apply=True)
        stage = "probe-write-file"
        api.write_handle(file_handle, _PROBE_CONTENTS)
        stage = "probe-flush-file"
        api.flush_handle(file_handle)
    except Exception as exc:
        if isinstance(exc, _ProbeRootFailure):
            failure_type = exc.failure_type
            failure_stage = exc.stage
        else:
            failure_type = type(exc).__name__
            failure_stage = stage
    finally:
        if api is not None and file_handle is not None:
            try:
                api.delete_on_close(file_handle)
            except Exception:
                cleanup_failed = True
                cleanup_stage = cleanup_stage or "probe-cleanup-file-delete"
            try:
                api.close_handle(file_handle)
            except Exception:
                cleanup_failed = True
                cleanup_stage = cleanup_stage or "probe-cleanup-file-close"
        if api is not None and directory_handle is not None:
            try:
                api.delete_on_close(directory_handle)
            except Exception:
                cleanup_failed = True
                cleanup_stage = cleanup_stage or "probe-cleanup-directory-delete"
            try:
                api.close_handle(directory_handle)
            except Exception:
                cleanup_failed = True
                cleanup_stage = cleanup_stage or "probe-cleanup-directory-close"
        if api is not None:
            for handle, _identity in reversed(root_handles):
                try:
                    api.close_handle(handle)
                except Exception:
                    cleanup_failed = True
                    cleanup_stage = cleanup_stage or "probe-cleanup-root-close"
    return _WindowsPrivateProbeResult(
        failure_type,
        cleanup_failed,
        failure_stage,
        cleanup_stage,
    )


def restrict_file_to_current_user(path: Path) -> None:
    _operate(Path(path), directory=False, apply=True)


def restrict_directory_to_current_user(path: Path) -> None:
    _operate(Path(path), directory=True, apply=True)


def inspect_file_acl(path: Path) -> WindowsAclInspection:
    result = _operate(Path(path), directory=False, apply=False)
    assert isinstance(result, WindowsAclInspection)
    return result


def inspect_directory_acl(path: Path) -> WindowsAclInspection:
    result = _operate(Path(path), directory=True, apply=False)
    assert isinstance(result, WindowsAclInspection)
    return result


__all__ = [
    "WindowsAclError",
    "WindowsAclInspection",
    "inspect_directory_acl",
    "inspect_file_acl",
    "open_private_directory",
    "restrict_directory_to_current_user",
    "restrict_file_to_current_user",
]
