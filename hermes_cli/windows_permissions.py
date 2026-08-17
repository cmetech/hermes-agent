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


class _WindowsCallError(OSError):
    def __init__(self, operation: str, code: int) -> None:
        super().__init__(code, operation)
        self.operation = operation
        self.winerror = code


class _WindowsAclApi:
    """Minimal ctypes adapter for handle-authoritative Windows ACL operations."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsAclError("native Windows ACL support is unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
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

    def close_handle(self, handle: int) -> None:
        if not self.kernel32.CloseHandle(handle):
            self._raise("close Windows ACL object")

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
    except OSError as exc:
        raise WindowsAclError(f"cannot inspect Windows ACL {kind} path") from exc
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
        access = READ_CONTROL | WRITE_DAC if apply else READ_CONTROL
        handle = api.open_handle(
            path, access=access, flags=_open_flags(directory=directory)
        )
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
    "restrict_directory_to_current_user",
    "restrict_file_to_current_user",
]
