#!/usr/bin/env python3
"""Native Windows acceptance gate for profile-scoped secret persistence."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Protocol, TextIO


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_ARM_PLUGIN_ID = "ericsson-arm"
_ARM_FIELD_ID = "token"
_MODE_ENV = "HERMES_SECRET_KEYSTORE"
_WORKSPACE_ENV = "HERMES_WINDOWS_GATE_WORKSPACE"
_TRANSACTION_CASE_FILES = {
    "authority.json",
    "keystore.enc",
    "keystore.key",
    "keystore.lock",
}
_PROBE_ROOT_FAILURE_STAGES = frozenset({
    "parse",
    "anchor-open",
    "anchor-validate",
    "component-open",
    "parent-upgrade",
    "component-create",
    "component-validate",
    "revalidate",
})
_PROBE_FAILURE_CATEGORIES = frozenset({
    "access-denied",
    "sharing-violation",
    "invalid-parameter",
    "not-found",
    "reparse",
    "other",
})
_PROBE_FAILURE_REASONS = frozenset(
    {
        "probe-native-api",
        "probe-open-root",
        "probe-create-directory",
        "probe-protect-directory",
        "probe-create-file",
        "probe-protect-file",
        "probe-write-file",
        "probe-flush-file",
        "probe-cleanup-file-delete",
        "probe-cleanup-file-close",
        "probe-cleanup-directory-delete",
        "probe-cleanup-directory-close",
        "probe-cleanup-root-close",
        "probe-unknown",
    }
    | {
        f"probe-open-root-{stage}-{category}"
        for stage in _PROBE_ROOT_FAILURE_STAGES
        for category in _PROBE_FAILURE_CATEGORIES
    }
)
_PROBE_FAILURE_REASON_RE = re.compile(
    r"\((probe-[a-z-]+)(?::[A-Za-z_][A-Za-z0-9_]*)?\)"
)
_TEAMS_TRACE_PHASES = frozenset(
    {"first-persist", "first-read", "replacement-persist", "replacement-read"}
)
_TEAMS_TRACE_OPERATIONS = frozenset(
    {
        "outer",
        "open-directory",
        "close-directory",
        "open-file",
        "create-file",
        "close-file",
        "write-file",
        "flush-file",
        "publish-file",
        "publish-disarm-delete",
        "publish-rename-file",
        "publish-verify-metadata",
        "publish-verify-user",
        "publish-verify-acl",
        "read-file",
        "missing",
        "mismatch",
    }
    | {
        f"publish-rename-file-{category}"
        for category in _PROBE_FAILURE_CATEGORIES
    }
)
_TEAMS_FAILURE_REASONS = frozenset(
    {
        f"teams-{phase}-{operation}"
        for phase in _TEAMS_TRACE_PHASES
        for operation in _TEAMS_TRACE_OPERATIONS
    }
    | {
        "teams-first-acl-directory",
        "teams-first-acl-file",
        "teams-replacement-acl-file",
        "teams-cleanup-file",
        "teams-cleanup-directory",
    }
)
_GATE_FAILURE_REASONS = _PROBE_FAILURE_REASONS | _TEAMS_FAILURE_REASONS


class _GateCaseFailure(RuntimeError):
    def __init__(self, reason: str) -> None:
        if reason not in _GATE_FAILURE_REASONS:
            reason = "probe-unknown"
        super().__init__(reason)
        self.reason = reason


class _TracedWindowsAclApi:
    """Record bounded publication substages while delegating native calls."""

    def __init__(self, delegate, mark, classify) -> None:
        self._delegate = delegate
        self._mark = mark
        self._classify = classify
        self._publishing = False

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def begin_publication(self) -> None:
        self._publishing = True

    def end_publication(self) -> None:
        self._publishing = False

    def set_delete_on_close(self, handle: int, delete: bool) -> None:
        if self._publishing and not delete:
            self._mark("publish-disarm-delete")
        self._delegate.set_delete_on_close(handle, delete)

    def rename_handle(
        self,
        handle: int,
        parent: int,
        name: str,
        *,
        replace: bool,
    ) -> None:
        if self._publishing:
            self._mark("publish-rename-file")
        try:
            self._delegate.rename_handle(handle, parent, name, replace=replace)
        except Exception as exc:
            if self._publishing:
                self._mark(f"publish-rename-file-{self._classify(exc)}")
            raise

    def handle_metadata(self, handle: int):
        if self._publishing:
            self._mark("publish-verify-metadata")
        return self._delegate.handle_metadata(handle)

    def current_user(self):
        if self._publishing:
            self._mark("publish-verify-user")
        return self._delegate.current_user()

    def read_acl(self, handle: int, current_user, security_information: int):
        if self._publishing:
            self._mark("publish-verify-acl")
        return self._delegate.read_acl(handle, current_user, security_information)


@dataclass(frozen=True)
class SyntheticInputs:
    auto_first: str
    auto_replacement: str
    file_first: str
    file_replacement: str
    teams_first: bytes
    teams_replacement: bytes
    reparse_marker: bytes

    @classmethod
    def generate(cls) -> SyntheticInputs:
        values = [secrets.token_urlsafe(36) for _ in range(7)]
        return cls(
            auto_first=values[0],
            auto_replacement=values[1],
            file_first=values[2],
            file_replacement=values[3],
            teams_first=values[4].encode("ascii"),
            teams_replacement=values[5].encode("ascii"),
            reparse_marker=values[6].encode("ascii"),
        )


class GateAdapter(Protocol):
    def verify_standard_user(self) -> None: ...

    def create_profile(self) -> Path: ...

    def exercise_arm_disabled_auto(
        self, profile: Path, first: str, replacement: str
    ) -> None: ...

    def exercise_file_tier_acl_repair(
        self, profile: Path, first: str, replacement: str
    ) -> None: ...

    def exercise_plain_doctor(self, profile: Path) -> None: ...

    def exercise_write_probe(self, profile: Path) -> None: ...

    def exercise_teams_cache(
        self, profile: Path, first: bytes, replacement: bytes
    ) -> None: ...

    def exercise_reparse_rejection(self, profile: Path, marker: bytes) -> None: ...

    def cleanup(self, profile: Path | None) -> None: ...


class _TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LuidAndAttributes(ctypes.Structure):
    _fields_ = [("Luid", _Luid), ("Attributes", wintypes.DWORD)]


class _PrivilegeSet(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Control", wintypes.DWORD),
        ("Privilege", _LuidAndAttributes * 1),
    ]


class _ChangedTeamsCache:
    def __init__(self, synthetic: bytes) -> None:
        value = synthetic.decode("ascii")
        self.serialized = json.dumps(
            {
                "AccessToken": {
                    "synthetic": {
                        "credential_type": "AccessToken",
                        "secret": value,
                    }
                }
            },
            separators=(",", ":"),
        )

    @property
    def has_state_changed(self) -> bool:
        return True

    def serialize(self) -> str:
        return self.serialized


class NativeWindowsAdapter:
    def __init__(self, *, workspace: Path | None = None) -> None:
        self._profile: Path | None = None
        self._workspace = workspace
        self._old_home = os.environ.get("HERMES_HOME")
        self._old_mode = os.environ.get(_MODE_ENV)
        self._storage_key: str | None = None
        self._teams_cache_path: Path | None = None
        self._reparse_link: Path | None = None
        self._sensitive: list[str] = []

    @staticmethod
    def _configure_token_api():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
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
        advapi32.LookupPrivilegeValueW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_Luid),
        ]
        advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
        advapi32.PrivilegeCheck.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PrivilegeSet),
            ctypes.POINTER(wintypes.BOOL),
        ]
        advapi32.PrivilegeCheck.restype = wintypes.BOOL
        return kernel32, advapi32

    def verify_standard_user(self) -> None:
        if os.name != "nt":
            raise RuntimeError("native Windows execution required")
        kernel32, advapi32 = self._configure_token_api()
        token = wintypes.HANDLE()
        token_query = 0x0008
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
        ):
            raise RuntimeError("could not inspect process token")
        try:
            elevation = _TokenElevation()
            returned = wintypes.DWORD()
            token_elevation_class = 20
            if not advapi32.GetTokenInformation(
                token,
                token_elevation_class,
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(returned),
            ):
                raise RuntimeError("could not inspect token elevation")
            if elevation.TokenIsElevated:
                raise RuntimeError("elevated token refused")

            security_luid = _Luid()
            if not advapi32.LookupPrivilegeValueW(
                None, "SeSecurityPrivilege", ctypes.byref(security_luid)
            ):
                raise RuntimeError("could not resolve privilege state")
            privileges = _PrivilegeSet(
                PrivilegeCount=1,
                Control=1,
                Privilege=(_LuidAndAttributes * 1)(
                    _LuidAndAttributes(Luid=security_luid, Attributes=0)
                ),
            )
            enabled = wintypes.BOOL()
            if not advapi32.PrivilegeCheck(
                token, ctypes.byref(privileges), ctypes.byref(enabled)
            ):
                raise RuntimeError("could not inspect privilege state")
            if enabled.value:
                raise RuntimeError("SeSecurityPrivilege must not be enabled")
        finally:
            kernel32.CloseHandle(token)

    @staticmethod
    def _require_direct_directory(path: Path) -> None:
        info = path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            (reparse_flag and getattr(info, "st_file_attributes", 0) & reparse_flag)
            or path.is_symlink()
            or not path.is_dir()
        ):
            raise RuntimeError("workspace must be a direct directory")

    def create_profile(self) -> Path:
        workspace = self._workspace
        if workspace is None:
            raw_workspace = os.environ.get(_WORKSPACE_ENV)
            if not raw_workspace:
                raise RuntimeError("private CI workspace is unavailable")
            workspace = Path(raw_workspace)
        self._require_direct_directory(workspace)
        profile = Path(
            tempfile.mkdtemp(prefix="hermes-standard-user-secret-", dir=workspace)
        )
        self._require_direct_directory(profile)
        (profile / "config.yaml").write_text(
            "secret_keystore: auto\n"
            "plugins:\n"
            "  enabled: []\n"
            "  disabled:\n"
            "    - ericsson-arm\n",
            encoding="utf-8",
        )
        os.environ["HERMES_HOME"] = str(profile)
        os.environ[_MODE_ENV] = "auto"
        self._profile = profile
        return profile

    @staticmethod
    def _field(detail: dict, field_id: str) -> dict:
        return next(field for field in detail["fields"] if field["id"] == field_id)

    @staticmethod
    def _validate_store_acls(profile: Path) -> None:
        from hermes_cli import windows_permissions

        root = profile / "secrets"
        inspection = windows_permissions.inspect_directory_acl(root)
        if not inspection.secure or inspection.detail is not None:
            raise RuntimeError("secret root ACL is not exact")
        found: set[str] = set()
        for artifact in root.iterdir():
            if not artifact.is_file() or artifact.is_symlink():
                raise RuntimeError("unexpected secret transaction artifact")
            found.add(artifact.name)
            inspection = windows_permissions.inspect_file_acl(artifact)
            if not inspection.secure or inspection.detail is not None:
                raise RuntimeError("secret artifact ACL is not exact")
        if not found or not found.issubset(_TRANSACTION_CASE_FILES):
            raise RuntimeError("secret transaction artifact inventory is invalid")

    @staticmethod
    def _assert_disabled_arm(manager) -> None:
        loaded = [
            item
            for item in manager.loaded_plugins()
            if (item.manifest.key or item.manifest.name) == _ARM_PLUGIN_ID
        ]
        if len(loaded) != 1 or loaded[0].enabled:
            raise RuntimeError("disabled ARM descriptor was not loaded safely")
        manifest = loaded[0].manifest
        expected = (REPO_ROOT / "plugins" / _ARM_PLUGIN_ID).resolve()
        if manifest.source != "bundled" or Path(manifest.path or "").resolve() != expected:
            raise RuntimeError("ARM descriptor did not come from the vendored plugin")

    def exercise_arm_disabled_auto(
        self, profile: Path, first: str, replacement: str
    ) -> None:
        from hermes_cli import secret_keystore
        from hermes_cli.plugin_configuration import (
            PluginConfigurationService,
            _secret_storage_key,
        )
        from hermes_cli.plugins import PluginManager
        from hermes_cli.secret_authority import SecretAuthority

        self._sensitive.extend((first, replacement))
        os.environ[_MODE_ENV] = "auto"
        secret_keystore.reset_backend_cache()
        manager = PluginManager()
        manager.discover_and_load()
        self._assert_disabled_arm(manager)
        service = PluginConfigurationService(manager)
        storage_key = _secret_storage_key(_ARM_PLUGIN_ID, _ARM_FIELD_ID)
        self._storage_key = storage_key

        first_detail = service.update(
            _ARM_PLUGIN_ID, secrets={_ARM_FIELD_ID: first}
        )
        if first_detail["enabled"] or not self._field(
            first_detail, _ARM_FIELD_ID
        )["is_set"]:
            raise RuntimeError("ARM create projection is invalid")
        if secret_keystore.get_backend().name != "os":
            raise RuntimeError("auto mode did not select the Windows OS keyring")
        if secret_keystore.get_authority(storage_key) is not SecretAuthority.OS:
            raise RuntimeError("auto secret authority is not OS")
        if secret_keystore.get_secret(storage_key) != first:
            raise RuntimeError("auto secret create/read failed")
        self._validate_store_acls(profile)

        replacement_detail = service.update(
            _ARM_PLUGIN_ID, secrets={_ARM_FIELD_ID: replacement}
        )
        if replacement_detail["enabled"] or secret_keystore.get_secret(
            storage_key
        ) != replacement:
            raise RuntimeError("auto secret replacement failed")
        self._validate_store_acls(profile)

        cleared = service.clear_secret(_ARM_PLUGIN_ID, _ARM_FIELD_ID)
        if self._field(cleared, _ARM_FIELD_ID)["is_set"]:
            raise RuntimeError("auto secret clear projection is invalid")
        if secret_keystore.get_secret(storage_key) is not None:
            raise RuntimeError("auto secret clear failed")
        if secret_keystore.get_authority(storage_key) is not SecretAuthority.CLEARED:
            raise RuntimeError("auto clear tombstone is absent")
        rendered = json.dumps((first_detail, replacement_detail, cleared))
        if first in rendered or replacement in rendered:
            raise RuntimeError("plugin projection exposed a secret")
        self._validate_store_acls(profile)

    @staticmethod
    def _inject_extra_current_user_ace(path: Path) -> None:
        import ntsecuritycon
        import win32api
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            sid = win32security.GetTokenInformation(
                token, win32security.TokenUser
            )[0]
        finally:
            token.Close()
        information = win32security.DACL_SECURITY_INFORMATION
        descriptor = win32security.GetFileSecurity(str(path), information)
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            raise RuntimeError("fixture DACL is unavailable")
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            0,
            ntsecuritycon.FILE_GENERIC_READ,
            sid,
        )
        descriptor.SetSecurityDescriptorDacl(True, dacl, False)
        win32security.SetFileSecurity(str(path), information, descriptor)

    def exercise_file_tier_acl_repair(
        self, profile: Path, first: str, replacement: str
    ) -> None:
        from hermes_cli import secret_keystore, windows_permissions
        from hermes_cli.plugin_configuration import PluginConfigurationService
        from hermes_cli.plugins import PluginManager
        from hermes_cli.secret_authority import SecretAuthority

        self._sensitive.extend((first, replacement))
        if self._storage_key is None:
            raise RuntimeError("ARM storage key is unavailable")
        os.environ[_MODE_ENV] = "file"
        secret_keystore.reset_backend_cache()
        manager = PluginManager()
        manager.discover_and_load()
        self._assert_disabled_arm(manager)
        service = PluginConfigurationService(manager)

        detail = service.update(_ARM_PLUGIN_ID, secrets={_ARM_FIELD_ID: first})
        if detail["enabled"] or not self._field(detail, _ARM_FIELD_ID)["is_set"]:
            raise RuntimeError("file secret create projection is invalid")
        if secret_keystore.get_authority(self._storage_key) is not SecretAuthority.FILE:
            raise RuntimeError("forced file authority is invalid")
        if secret_keystore.get_secret(self._storage_key) != first:
            raise RuntimeError("file secret create/read failed")
        self._validate_store_acls(profile)

        ciphertext = profile / "secrets" / "keystore.enc"
        self._inject_extra_current_user_ace(ciphertext)
        drifted = windows_permissions.inspect_file_acl(ciphertext)
        if drifted.secure or drifted.detail is None:
            raise RuntimeError("extra fixture ACE did not create ACL drift")

        replaced = service.update(
            _ARM_PLUGIN_ID, secrets={_ARM_FIELD_ID: replacement}
        )
        if replaced["enabled"] or secret_keystore.get_secret(
            self._storage_key
        ) != replacement:
            raise RuntimeError("file secret replacement failed")
        repaired = windows_permissions.inspect_file_acl(ciphertext)
        if not repaired.secure or repaired.detail is not None:
            raise RuntimeError("file replacement did not repair ACL drift")
        self._validate_store_acls(profile)

        cleared = service.clear_secret(_ARM_PLUGIN_ID, _ARM_FIELD_ID)
        if self._field(cleared, _ARM_FIELD_ID)["is_set"]:
            raise RuntimeError("file secret clear projection is invalid")
        if secret_keystore.get_secret(self._storage_key) is not None:
            raise RuntimeError("file secret clear failed")
        if secret_keystore.get_authority(
            self._storage_key
        ) is not SecretAuthority.CLEARED:
            raise RuntimeError("file clear tombstone is absent")
        self._validate_store_acls(profile)

    @staticmethod
    def _tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
        rows: list[tuple[object, ...]] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if path.is_file() and not path.is_symlink():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                digest = None
            rows.append(
                (
                    relative,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    getattr(info, "st_file_attributes", 0),
                    digest,
                )
            )
        return tuple(rows)

    def _run_doctor(self, *, write_probe: bool) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "hermes_cli.main", "secrets", "doctor"]
        if write_probe:
            command.append("--write-probe")
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        captured = result.stdout + result.stderr
        if any(value in captured for value in self._sensitive):
            raise RuntimeError("doctor exposed a synthetic value")
        return result

    def exercise_plain_doctor(self, profile: Path) -> None:
        before = self._tree_snapshot(profile)
        result = self._run_doctor(write_probe=False)
        after = self._tree_snapshot(profile)
        if result.returncode != 0 or before != after:
            raise RuntimeError("plain doctor was not read-only")
        if "Running explicit synthetic ACL write probe" in result.stdout:
            raise RuntimeError("plain doctor unexpectedly ran the write probe")

    def exercise_write_probe(self, profile: Path) -> None:
        result = self._run_doctor(write_probe=True)
        if result.returncode != 0 or "WRITE_PROBE_OK" not in result.stdout:
            reason = "probe-unknown"
            for line in result.stdout.splitlines():
                if line.startswith(("[WRITE_PROBE_FAILED]", "[WRITE_PROBE_CLEANUP_FAILED]")):
                    match = _PROBE_FAILURE_REASON_RE.search(line)
                    if match:
                        reason = match.group(1)
                        break
            raise _GateCaseFailure(reason)
        if any(profile.glob(".secret-write-probe-*")):
            raise RuntimeError("explicit write probe left an artifact")

    @staticmethod
    def _load_vendored_teams_module():
        plugin_dir = (REPO_ROOT / "plugins" / "ericsson-teams").resolve()
        module_path = (plugin_dir / "graph_auth.py").resolve()
        if module_path.parent != plugin_dir or not module_path.is_file():
            raise RuntimeError("vendored Teams cache module is unavailable")
        module_name = "_hermes_windows_gate_ericsson_teams_graph_auth"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("vendored Teams cache module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        old_path = list(sys.path)
        try:
            sys.path.insert(0, str(plugin_dir))
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = old_path
        return module

    def exercise_teams_cache(
        self, profile: Path, first: bytes, replacement: bytes
    ) -> None:
        from hermes_cli import windows_permissions

        self._sensitive.extend((first.decode("ascii"), replacement.decode("ascii")))
        module = self._load_vendored_teams_module()
        phase = "first-persist"
        last_operation = "outer"

        def mark(operation: str) -> None:
            nonlocal last_operation
            last_operation = operation

        class TracedPrivateFile:
            def __init__(self, private_file) -> None:
                self._private_file = private_file

            def __enter__(self):
                self._private_file.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                if exc_type is None:
                    mark("close-file")
                try:
                    return self._private_file.__exit__(exc_type, exc, traceback)
                except Exception:
                    mark("close-file")
                    raise

            def close(self) -> None:
                mark("close-file")
                self._private_file.close()

            def write_all(self, data: bytes) -> None:
                mark("write-file")
                self._private_file.write_all(data)

            def flush(self) -> None:
                mark("flush-file")
                self._private_file.flush()

            def publish(self, name: str) -> None:
                mark("publish-file")
                api = getattr(self._private_file, "_api", None)
                if isinstance(api, _TracedWindowsAclApi):
                    api.begin_publication()
                try:
                    self._private_file.publish(name)
                finally:
                    if isinstance(api, _TracedWindowsAclApi):
                        api.end_publication()

            def read_all(self, *, max_bytes: int) -> bytes:
                mark("read-file")
                return self._private_file.read_all(max_bytes=max_bytes)

        class TracedPrivateDirectory:
            def __init__(self, directory) -> None:
                self._directory = directory

            def __enter__(self):
                self._directory.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                if exc_type is None:
                    mark("close-directory")
                try:
                    return self._directory.__exit__(exc_type, exc, traceback)
                except Exception:
                    mark("close-directory")
                    raise

            def open_file(self, name: str):
                mark("open-file")
                private_file = self._directory.open_file(name)
                return (
                    None
                    if private_file is None
                    else TracedPrivateFile(private_file)
                )

            def create_file(self, name: str):
                mark("create-file")
                return TracedPrivateFile(self._directory.create_file(name))

        def traced_open_private_directory(path: Path):
            mark("open-directory")
            native_api = windows_permissions._native_api
            windows_permissions._native_api = lambda: _TracedWindowsAclApi(
                native_api(), mark, windows_permissions._probe_failure_category
            )
            try:
                directory = windows_permissions.open_private_directory(path)
            finally:
                windows_permissions._native_api = native_api
            return TracedPrivateDirectory(directory)

        module._windows_acl_api = lambda: module._WindowsAclApi(
            open_private_directory=traced_open_private_directory
        )

        def run_phase(name: str, operation):
            nonlocal phase, last_operation
            phase = name
            last_operation = "outer"
            try:
                return operation()
            except Exception:
                raise _GateCaseFailure(f"teams-{phase}-{last_operation}") from None

        first_cache = _ChangedTeamsCache(first)
        run_phase("first-persist", lambda: module._persist(first_cache))
        cache_path = module.cache_path()
        self._teams_cache_path = cache_path
        first_text = run_phase("first-read", module._read_cache_text)
        if first_text is None:
            raise _GateCaseFailure("teams-first-read-missing")
        if first_text != first_cache.serialized:
            raise _GateCaseFailure("teams-first-read-mismatch")
        try:
            parent_acl = windows_permissions.inspect_directory_acl(cache_path.parent)
        except Exception:
            raise _GateCaseFailure("teams-first-acl-directory") from None
        try:
            file_acl = windows_permissions.inspect_file_acl(cache_path)
        except Exception:
            raise _GateCaseFailure("teams-first-acl-file") from None
        if not parent_acl.secure or parent_acl.detail is not None:
            raise _GateCaseFailure("teams-first-acl-directory")
        if not file_acl.secure or file_acl.detail is not None:
            raise _GateCaseFailure("teams-first-acl-file")

        replacement_cache = _ChangedTeamsCache(replacement)
        run_phase("replacement-persist", lambda: module._persist(replacement_cache))
        replacement_text = run_phase("replacement-read", module._read_cache_text)
        if replacement_text is None:
            raise _GateCaseFailure("teams-replacement-read-missing")
        if replacement_text != replacement_cache.serialized:
            raise _GateCaseFailure("teams-replacement-read-mismatch")
        try:
            file_acl = windows_permissions.inspect_file_acl(cache_path)
        except Exception:
            raise _GateCaseFailure("teams-replacement-acl-file") from None
        if not file_acl.secure or file_acl.detail is not None:
            raise _GateCaseFailure("teams-replacement-acl-file")
        try:
            cache_path.unlink()
        except Exception:
            raise _GateCaseFailure("teams-cleanup-file") from None
        if cache_path.exists():
            raise _GateCaseFailure("teams-cleanup-file")
        try:
            cache_path.parent.rmdir()
        except Exception:
            raise _GateCaseFailure("teams-cleanup-directory") from None

    @staticmethod
    def _snapshot_target(target: Path) -> tuple[tuple[object, ...], ...]:
        return NativeWindowsAdapter._tree_snapshot(target)

    def exercise_reparse_rejection(self, profile: Path, marker: bytes) -> None:
        from hermes_cli import secret_keystore

        self._sensitive.append(marker.decode("ascii"))
        fixture = profile / "reparse-fixture"
        reparse_profile = fixture / "profile"
        target = fixture / "target"
        reparse_profile.mkdir(parents=True)
        target.mkdir()
        (target / "marker.bin").write_bytes(marker)
        before = self._snapshot_target(target)
        link = reparse_profile / "secrets"
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("junction fixture creation failed")
        self._reparse_link = link
        previous_home = os.environ["HERMES_HOME"]
        try:
            os.environ["HERMES_HOME"] = str(reparse_profile)
            os.environ[_MODE_ENV] = "file"
            secret_keystore.reset_backend_cache()
            rejected = False
            try:
                secret_keystore.set_secret(
                    f"HERMES_GATE_REPARSE_{secrets.token_hex(12).upper()}",
                    marker.decode("ascii"),
                )
            except secret_keystore.KeystoreError:
                rejected = True
            if not rejected:
                raise RuntimeError("junction-backed store was accepted")
            if before != self._snapshot_target(target):
                raise RuntimeError("junction rejection altered its target")
        finally:
            os.environ["HERMES_HOME"] = previous_home
            os.environ[_MODE_ENV] = "file"
            secret_keystore.reset_backend_cache()
            if self._reparse_link is not None:
                os.rmdir(self._reparse_link)
                self._reparse_link = None
            shutil.rmtree(fixture)

    def cleanup(self, profile: Path | None) -> None:
        errors: list[Path] = []
        active_profile = profile or self._profile
        try:
            if self._reparse_link is not None:
                try:
                    os.rmdir(self._reparse_link)
                    self._reparse_link = None
                except OSError:
                    errors.append(self._reparse_link)
            if self._teams_cache_path is not None:
                try:
                    self._teams_cache_path.unlink(missing_ok=True)
                except OSError:
                    errors.append(self._teams_cache_path)
            if active_profile is not None and self._storage_key is not None:
                try:
                    from hermes_cli import secret_keystore

                    secret_keystore.OSKeystore(str(active_profile.resolve())).delete(
                        self._storage_key
                    )
                    secret_keystore.reset_backend_cache()
                except Exception:
                    errors.append(active_profile / "secrets")
            if active_profile is not None:
                try:
                    shutil.rmtree(active_profile)
                except OSError:
                    errors.append(active_profile)
                if active_profile.exists():
                    errors.append(active_profile)
        finally:
            if self._old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = self._old_home
            if self._old_mode is None:
                os.environ.pop(_MODE_ENV, None)
            else:
                os.environ[_MODE_ENV] = self._old_mode
        if errors:
            unique = sorted({str(path) for path in errors})
            raise RuntimeError(unique[0])


def _emit(output: TextIO, case: str, passed: bool, detail: str | None = None) -> None:
    suffix = "PASS" if passed else "FAIL"
    line = f"{case} {suffix}"
    if case == "cleanup" and not passed and detail:
        line += f" path={detail}"
    elif (
        case == "explicit-write-probe"
        and not passed
        and detail in _PROBE_FAILURE_REASONS
    ):
        line += f" reason={detail}"
    elif case == "teams-cache-round-trip" and not passed and detail in _TEAMS_FAILURE_REASONS:
        line += f" reason={detail}"
    print(line, file=output, flush=True)


def main(
    adapter: GateAdapter | None = None,
    output: TextIO | None = None,
    *,
    workspace: Path | None = None,
) -> int:
    output = output or sys.stdout
    if adapter is None:
        if os.name != "nt":
            _emit(output, "platform-preflight", False)
            return 1
        adapter = NativeWindowsAdapter(workspace=workspace)

    synthetic = SyntheticInputs.generate()
    profile: Path | None = None
    failed = False
    cases = [
        ("platform-preflight", adapter.verify_standard_user),
        ("fresh-profile", lambda: None),
        (
            "arm-disabled-auto-keyring",
            lambda: adapter.exercise_arm_disabled_auto(
                profile, synthetic.auto_first, synthetic.auto_replacement  # type: ignore[arg-type]
            ),
        ),
        (
            "file-tier-acl-repair",
            lambda: adapter.exercise_file_tier_acl_repair(
                profile, synthetic.file_first, synthetic.file_replacement  # type: ignore[arg-type]
            ),
        ),
        (
            "plain-doctor-read-only",
            lambda: adapter.exercise_plain_doctor(profile),  # type: ignore[arg-type]
        ),
        (
            "explicit-write-probe",
            lambda: adapter.exercise_write_probe(profile),  # type: ignore[arg-type]
        ),
        (
            "teams-cache-round-trip",
            lambda: adapter.exercise_teams_cache(
                profile, synthetic.teams_first, synthetic.teams_replacement  # type: ignore[arg-type]
            ),
        ),
        (
            "reparse-rejection",
            lambda: adapter.exercise_reparse_rejection(
                profile, synthetic.reparse_marker  # type: ignore[arg-type]
            ),
        ),
    ]
    try:
        for case, operation in cases:
            try:
                if case == "fresh-profile":
                    profile = adapter.create_profile()
                else:
                    operation()
            except Exception as error:
                detail = error.reason if isinstance(error, _GateCaseFailure) else None
                _emit(output, case, False, detail)
                failed = True
                break
            _emit(output, case, True)
    finally:
        try:
            adapter.cleanup(profile)
        except Exception as error:
            detail = str(error) if isinstance(adapter, NativeWindowsAdapter) else None
            _emit(output, "cleanup", False, detail)
            failed = True
        else:
            _emit(output, "cleanup", True)
    return 1 if failed else 0


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    arguments = parser.parse_args(argv)
    return main(workspace=arguments.workspace)


if __name__ == "__main__":
    raise SystemExit(run_cli())
