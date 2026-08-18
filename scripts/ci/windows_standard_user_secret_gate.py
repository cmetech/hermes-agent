#!/usr/bin/env python3
"""Native Windows acceptance gate for profile-scoped secret persistence."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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
    def __init__(self) -> None:
        self._profile: Path | None = None
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
            raise RuntimeError("explicit write probe failed")
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
        first_cache = _ChangedTeamsCache(first)
        module._persist(first_cache)
        cache_path = module.cache_path()
        self._teams_cache_path = cache_path
        if module._read_cache_text() != first_cache.serialized:
            raise RuntimeError("Teams cache create/read failed")
        parent_acl = windows_permissions.inspect_directory_acl(cache_path.parent)
        file_acl = windows_permissions.inspect_file_acl(cache_path)
        if (
            not parent_acl.secure
            or parent_acl.detail is not None
            or not file_acl.secure
            or file_acl.detail is not None
        ):
            raise RuntimeError("Teams cache ACL is not exact")

        replacement_cache = _ChangedTeamsCache(replacement)
        module._persist(replacement_cache)
        if module._read_cache_text() != replacement_cache.serialized:
            raise RuntimeError("Teams cache replacement failed")
        file_acl = windows_permissions.inspect_file_acl(cache_path)
        if not file_acl.secure or file_acl.detail is not None:
            raise RuntimeError("Teams replacement ACL is not exact")
        cache_path.unlink()
        if cache_path.exists():
            raise RuntimeError("Teams cache cleanup failed")
        cache_path.parent.rmdir()

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
    print(line, file=output, flush=True)


def main(
    adapter: GateAdapter | None = None,
    output: TextIO | None = None,
) -> int:
    output = output or sys.stdout
    if adapter is None:
        if os.name != "nt":
            _emit(output, "platform-preflight", False)
            return 1
        adapter = NativeWindowsAdapter()

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
            except Exception:
                _emit(output, case, False)
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


if __name__ == "__main__":
    raise SystemExit(main())
