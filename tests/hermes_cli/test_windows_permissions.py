"""Behavioral tests for the native Windows credential ACL boundary."""

from __future__ import annotations

import ctypes
import inspect
import os
import stat
from ctypes import wintypes
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


SID = "S-1-5-21-1-2-3-1001"
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
ACCESS_SYSTEM_SECURITY = 0x01000000
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
SACL_SECURITY_INFORMATION = 0x00000008
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
INHERITED_ACE = 0x10
ACCESS_ALLOWED_ACE_TYPE = 0x00
ACCESS_DENIED_ACE_TYPE = 0x01
FILE_PRIVATE_MASK = 0x0012019F
DIRECTORY_PRIVATE_MASK = 0x001201FF
FILE_ADD_FILE = 0x00000002
FILE_ADD_SUBDIRECTORY = 0x00000004
FILE_TRAVERSE = 0x00000020
FILE_READ_ATTRIBUTES = 0x00000080
SYNCHRONIZE = 0x00100000
DELETE = 0x00010000
FILE_WRITE_DATA = 0x00000002


class HandleBackedProbeApi:
    """Portable handle model for the Windows probe's relative filesystem work."""

    def __init__(self, permissions, *, swapped_root: Path | None = None) -> None:
        self.permissions = permissions
        self.swapped_root = swapped_root
        self.expected_root_identity = (
            swapped_root.stat().st_ino if swapped_root is not None else None
        )
        self.opened_root_identity: int | None = None
        self.children: dict[int, tuple[int, str, bool]] = {}
        self.delete_handles: set[int] = set()
        self.closed: list[int] = []
        self.flushed: list[int] = []
        self.root_opens: list[int] = []
        self.relative_opens: list[int] = []
        self.root_creates: list[int] = []
        self.refuse_next_delete = False
        self.component_swap_fired = False

    def open_handle(self, path, *, access, flags):
        self.root_opens.append(access)
        candidate = Path(path)
        return os.open(candidate, os.O_RDONLY | os.O_DIRECTORY)

    def open_relative_directory(self, parent, name, *, access):
        self.relative_opens.append(access)
        try:
            handle = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        except FileNotFoundError:
            raise self.permissions._WindowsCallError(
                "open relative Windows probe object", 0xC0000034
            ) from None
        if (
            self.swapped_root is not None
            and name == self.swapped_root.name
            and not self.component_swap_fired
        ):
            parked = self.swapped_root.with_name(f"{self.swapped_root.name}-parked")
            attacker = self.swapped_root.with_name(f"{self.swapped_root.name}-attacker")
            self.opened_root_identity = os.fstat(handle).st_ino
            self.swapped_root.rename(parked)
            attacker.rename(self.swapped_root)
            self.swapped_root.rename(attacker)
            parked.rename(self.swapped_root)
            self.component_swap_fired = True
        return handle

    def create_relative_directory(self, parent, name, *, access):
        self.root_creates.append(access)
        os.mkdir(name, dir_fd=parent)
        return self.open_relative_directory(parent, name, access=access)

    def create_relative(self, parent, name, *, directory, access):
        if directory:
            os.mkdir(name, dir_fd=parent)
            handle = self.open_relative_directory(parent, name, access=access)
            if self.expected_root_identity is not None:
                self.opened_root_identity = os.fstat(parent).st_ino
        else:
            handle = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
        self.children[handle] = (parent, name, directory)
        return handle

    def handle_metadata(self, handle):
        info = os.fstat(handle)
        return self.permissions._HandleMetadata(
            FILE_ATTRIBUTE_DIRECTORY if stat.S_ISDIR(info.st_mode) else 0,
            self.permissions._FileIdentity(info.st_dev, info.st_ino),
        )

    def current_user(self):
        return self.permissions._CurrentUserSid(SID, 0x51D, None)

    def read_acl(self, handle, current_user, security_information):
        directory = stat.S_ISDIR(os.fstat(handle).st_mode)
        return self.permissions._AclState(
            True,
            True,
            True,
            1,
            ACCESS_ALLOWED_ACE_TYPE,
            OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if directory else 0,
            DIRECTORY_PRIVATE_MASK if directory else FILE_PRIVATE_MASK,
            True,
        )

    def set_dacl(self, *_args):
        pass

    def write_handle(self, handle, data):
        os.write(handle, data)

    def flush_handle(self, handle):
        os.fsync(handle)
        self.flushed.append(handle)

    def delete_on_close(self, handle):
        if self.refuse_next_delete:
            self.refuse_next_delete = False
            raise OSError("synthetic cleanup failure")
        self.delete_handles.add(handle)

    def close_handle(self, handle):
        child = self.children.pop(handle, None)
        os.close(handle)
        self.closed.append(handle)
        if child is not None and handle in self.delete_handles:
            parent, name, directory = child
            (os.rmdir if directory else os.unlink)(name, dir_fd=parent)


class FakeNativeApi:
    """Complete double for the private native boundary used by public functions."""

    def __init__(self, permissions, *, directory: bool = False) -> None:
        self.permissions = permissions
        self.directory = directory
        self.handle = 0xA11
        self.opens: list[tuple[Path, int, int]] = []
        self.closed: list[int] = []
        self.reads: list[tuple[int, int]] = []
        self.mutations: list[tuple[int, str, int]] = []
        self.metadata = [
            permissions._HandleMetadata(
                attributes=FILE_ATTRIBUTE_DIRECTORY if directory else 0,
                identity=permissions._FileIdentity(7, 11),
            )
        ]
        self.states = [self.private_state()]
        self.failure: Exception | None = None

    def private_state(self, **changes):
        values = {
            "owner_matches": True,
            "dacl_present": True,
            "protected": True,
            "ace_count": 1,
            "ace_type": ACCESS_ALLOWED_ACE_TYPE,
            "ace_flags": (
                OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if self.directory else 0
            ),
            "ace_mask": (
                DIRECTORY_PRIVATE_MASK if self.directory else FILE_PRIVATE_MASK
            ),
            "ace_sid_matches": True,
        }
        values.update(changes)
        return self.permissions._AclState(**values)

    def open_handle(self, path: Path, *, access: int, flags: int) -> int:
        if self.failure is not None:
            raise self.failure
        self.opens.append((Path(path), access, flags))
        return self.handle

    def open_bound_handle(self, path: Path, *, access: int, flags: int) -> int:
        return self.open_handle(path, access=access, flags=flags)

    def close_handle(self, handle: int) -> None:
        self.closed.append(handle)

    def handle_metadata(self, handle: int):
        assert handle == self.handle
        if len(self.metadata) > 1:
            return self.metadata.pop(0)
        return self.metadata[0]

    def current_user(self):
        return self.permissions._CurrentUserSid(SID, 0x51D, None)

    def read_acl(self, handle: int, current_user, security_information: int):
        assert handle == self.handle
        assert current_user.text == SID
        self.reads.append((handle, security_information))
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def set_dacl(
        self,
        handle: int,
        sddl: str,
        security_information: int,
    ) -> None:
        self.mutations.append((handle, sddl, security_information))


def _install_api(monkeypatch, permissions, *, directory=False) -> FakeNativeApi:
    api = FakeNativeApi(permissions, directory=directory)
    monkeypatch.setattr(permissions, "_native_api", lambda: api)
    return api


def _target(tmp_path: Path, *, directory: bool) -> Path:
    path = tmp_path / "artifact"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"credential")
    return path


def _uninitialized_native_api(permissions, *, kernel32, advapi32, ntdll=None):
    api = object.__new__(permissions._WindowsAclApi)
    api.kernel32 = kernel32
    api.advapi32 = advapi32
    api.ntdll = ntdll if ntdll is not None else SimpleNamespace()
    return api


def test_private_directory_binding_applies_and_inspects_one_held_handle(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=True)
    api = _install_api(monkeypatch, permissions, directory=True)
    api.states = [api.private_state(), api.private_state(), api.private_state()]

    with permissions.open_private_directory(target):
        assert api.closed == []

    assert api.opens == [
        (
            target,
            READ_CONTROL
            | WRITE_DAC
            | FILE_READ_ATTRIBUTES
            | FILE_TRAVERSE
            | FILE_ADD_FILE,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        )
    ]
    assert api.mutations == [
        (
            api.handle,
            f"D:P(A;OICI;0x{DIRECTORY_PRIVATE_MASK:08x};;;{SID})",
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        )
    ]
    assert [handle for handle, _flags in api.reads] == [api.handle] * 2
    assert api.closed == [api.handle]


def test_private_file_write_publish_and_final_inspection_share_one_held_handle(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    class PrivateIoApi(FakeNativeApi):
        def __init__(self):
            super().__init__(permissions, directory=True)
            self.handle = 0xD11
            self.file_handle = 0xF11
            self.file_state = self.private_state(
                ace_flags=0,
                ace_mask=FILE_PRIVATE_MASK,
            )
            self.events = []

        def handle_metadata(self, handle):
            return permissions._HandleMetadata(
                FILE_ATTRIBUTE_DIRECTORY if handle == self.handle else 0,
                permissions._FileIdentity(7, handle),
            )

        def read_acl(self, handle, current_user, security_information):
            self.events.append(("inspect", handle))
            return self.private_state() if handle == self.handle else self.file_state

        def set_dacl(self, handle, sddl, security_information):
            self.events.append(("apply", handle))

        def create_relative(self, parent, name, *, directory, access):
            self.events.append(("create", parent, name, directory, access))
            return self.file_handle

        def set_delete_on_close(self, handle, delete):
            self.events.append(("delete", handle, delete))

        def write_handle(self, handle, data):
            self.events.append(("write", handle, data))

        def flush_handle(self, handle):
            self.events.append(("flush", handle))

        def rename_handle(self, handle, parent, name, *, replace):
            self.events.append(("rename", handle, parent, name, replace))

    target = _target(tmp_path, directory=True)
    api = PrivateIoApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    with permissions.open_private_directory(target) as directory:
        with directory.create_file("temporary") as private_file:
            private_file.write_all(b"synthetic-cache")
            private_file.flush()
            private_file.publish("cache.json")

    assert api.events == [
        ("inspect", api.handle),
        ("apply", api.handle),
        ("inspect", api.handle),
        (
            "create",
            api.handle,
            "temporary",
            False,
            READ_CONTROL
            | WRITE_DAC
            | DELETE
            | SYNCHRONIZE
            | FILE_READ_ATTRIBUTES
            | FILE_WRITE_DATA,
        ),
        ("inspect", api.file_handle),
        ("apply", api.file_handle),
        ("inspect", api.file_handle),
        ("delete", api.file_handle, True),
        ("write", api.file_handle, b"synthetic-cache"),
        ("flush", api.file_handle),
        ("delete", api.file_handle, False),
        ("rename", api.file_handle, api.handle, "cache.json", True),
        ("inspect", api.file_handle),
    ]
    assert api.closed == [api.file_handle, api.handle]


def test_private_file_read_is_bounded_on_the_acl_held_handle(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    class PrivateReadApi(FakeNativeApi):
        def __init__(self):
            super().__init__(permissions, directory=True)
            self.handle = 0xD11
            self.file_handle = 0xF11
            self.events = []
            self.chunks = [b"synthetic-", b"cache", b""]

        def handle_metadata(self, handle):
            return permissions._HandleMetadata(
                FILE_ATTRIBUTE_DIRECTORY if handle == self.handle else 0,
                permissions._FileIdentity(7, handle),
            )

        def read_acl(self, handle, current_user, security_information):
            self.events.append(("inspect", handle))
            state = self.private_state()
            if handle == self.file_handle:
                state = self.private_state(ace_flags=0, ace_mask=FILE_PRIVATE_MASK)
            return state

        def set_dacl(self, handle, sddl, security_information):
            self.events.append(("apply", handle))

        def open_relative_file(self, parent, name, *, access):
            self.events.append(("open", parent, name, access))
            return self.file_handle

        def read_handle(self, handle, size):
            self.events.append(("read", handle, size))
            return self.chunks.pop(0)

    target = _target(tmp_path, directory=True)
    api = PrivateReadApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    with permissions.open_private_directory(target) as directory:
        with directory.open_file("cache.json") as private_file:
            assert private_file.read_all(max_bytes=32) == b"synthetic-cache"

    file_events = [event for event in api.events if api.file_handle in event]
    assert file_events[:3] == [
        ("inspect", api.file_handle),
        ("apply", api.file_handle),
        ("inspect", api.file_handle),
    ]
    assert api.events[3] == (
        "open",
        api.handle,
        "cache.json",
        READ_CONTROL
        | WRITE_DAC
        | SYNCHRONIZE
        | FILE_READ_ATTRIBUTES
        | permissions._FILE_READ_DATA,
    )
    assert [event[0] for event in file_events[3:]] == ["read", "read", "read"]
    assert api.closed == [api.file_handle, api.handle]


def test_private_file_create_preserves_name_collision_for_bounded_retry(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    class CollisionApi(FakeNativeApi):
        def __init__(self):
            super().__init__(permissions, directory=True)

        def create_relative(self, parent, name, *, directory, access):
            raise permissions._WindowsCallError(
                "synthetic private collision",
                0xC0000035,
            )

    target = _target(tmp_path, directory=True)
    api = CollisionApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    with permissions.open_private_directory(target) as directory:
        with pytest.raises(FileExistsError):
            directory.create_file("temporary")


def test_private_file_cleanup_retries_handle_delete_after_publish_failure(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    class CleanupApi(FakeNativeApi):
        def __init__(self):
            super().__init__(permissions, directory=True)
            self.handle = 0xD11
            self.file_handle = 0xF11
            self.delete_events = []

        def handle_metadata(self, handle):
            return permissions._HandleMetadata(
                FILE_ATTRIBUTE_DIRECTORY if handle == self.handle else 0,
                permissions._FileIdentity(7, handle),
            )

        def read_acl(self, handle, current_user, security_information):
            if handle == self.handle:
                return self.private_state()
            return self.private_state(ace_flags=0, ace_mask=FILE_PRIVATE_MASK)

        def create_relative(self, parent, name, *, directory, access):
            return self.file_handle

        def set_delete_on_close(self, handle, delete):
            self.delete_events.append((handle, delete))
            if delete and len(self.delete_events) == 3:
                raise OSError("synthetic first cleanup failure")

        def rename_handle(self, handle, parent, name, *, replace):
            raise OSError("synthetic publication failure")

    target = _target(tmp_path, directory=True)
    api = CleanupApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    with permissions.open_private_directory(target) as directory:
        with pytest.raises(permissions.WindowsAclError):
            with directory.create_file("temporary") as private_file:
                private_file.publish("cache.json")

    assert api.delete_events == [
        (api.file_handle, True),
        (api.file_handle, False),
        (api.file_handle, True),
        (api.file_handle, True),
    ]
    assert api.closed == [api.file_handle, api.handle]


def test_private_write_probe_rejects_non_relative_artifact_names(tmp_path):
    from hermes_cli import windows_permissions as permissions

    with pytest.raises(permissions.WindowsAclError):
        permissions._run_private_acl_write_probe(
            tmp_path,
            directory_name="../outside",
        )


def test_private_write_probe_reports_a_bounded_native_api_failure_stage(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    monkeypatch.setattr(
        permissions,
        "_native_api",
        lambda: (_ for _ in ()).throw(RuntimeError("private native detail")),
    )

    result = permissions._run_private_acl_write_probe(
        tmp_path,
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result.failure_type == "RuntimeError"
    assert result.failure_stage == "probe-native-api"
    assert result.cleanup_stage is None


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (5, "access-denied"),
        (0xC0000022, "access-denied"),
        (32, "sharing-violation"),
        (0xC0000043, "sharing-violation"),
        (87, "invalid-parameter"),
        (0xC000000D, "invalid-parameter"),
        (2, "not-found"),
        (3, "not-found"),
        (0xC0000034, "not-found"),
        (0xC000003A, "not-found"),
        (0xC000050B, "reparse"),
        (1314, "other"),
    ],
)
def test_private_write_probe_maps_native_errors_to_bounded_categories(code, category):
    from hermes_cli import windows_permissions as permissions

    error = permissions._WindowsCallError("private operation", code)

    assert permissions._probe_failure_category(error) == category


def test_private_write_probe_reports_bounded_anchor_open_failure(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    class AnchorFailureApi:
        def open_handle(self, *_args, **_kwargs):
            raise permissions._WindowsCallError("private operation", 5)

    monkeypatch.setattr(permissions, "_native_api", AnchorFailureApi)

    result = permissions._run_private_acl_write_probe(
        tmp_path / "profile",
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result.failure_type == "_WindowsCallError"
    assert result.failure_stage == "probe-open-root-anchor-open-access-denied"
    assert result.cleanup_stage is None


def test_private_write_probe_uses_only_relative_handles_and_deletes_them(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    class ProbeApi:
        def __init__(self):
            self.opens = []
            self.walk = []
            self.relative = []
            self.writes = []
            self.deletes = []
            self.closed = []
            self.flushed = []
            self.acl_events = []
            self.next_handle = 2
            self.directory_handles = {1}

        def open_handle(self, path, *, access, flags):
            self.opens.append((Path(path), access, flags))
            return 1

        def open_relative_directory(self, parent, name, *, access):
            handle = self.next_handle
            self.next_handle += 1
            self.directory_handles.add(handle)
            self.walk.append((parent, name, access, handle))
            return handle

        def create_relative_directory(self, parent, name, *, access):
            raise AssertionError("the test root already exists")

        def create_relative(self, parent, name, *, directory, access):
            self.relative.append((parent, name, directory, access))
            handle = self.next_handle
            self.next_handle += 1
            if directory:
                self.directory_handles.add(handle)
            return handle

        def handle_metadata(self, handle):
            return permissions._HandleMetadata(
                FILE_ATTRIBUTE_DIRECTORY if handle in self.directory_handles else 0,
                permissions._FileIdentity(7, handle),
            )

        def current_user(self):
            return permissions._CurrentUserSid(SID, 0x51D, None)

        def read_acl(self, handle, current_user, security_information):
            self.acl_events.append(("inspect", handle))
            return permissions._AclState(
                True,
                True,
                True,
                1,
                ACCESS_ALLOWED_ACE_TYPE,
                (
                    OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
                    if handle in self.directory_handles
                    else 0
                ),
                (
                    DIRECTORY_PRIVATE_MASK
                    if handle in self.directory_handles
                    else FILE_PRIVATE_MASK
                ),
                True,
            )

        def set_dacl(self, handle, *_args):
            self.acl_events.append(("apply", handle))

        def write_handle(self, handle, data):
            self.writes.append((handle, data))

        def flush_handle(self, handle):
            self.flushed.append(handle)

        def delete_on_close(self, handle):
            self.deletes.append(handle)

        def close_handle(self, handle):
            self.closed.append(handle)

    api = ProbeApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        tmp_path,
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result == permissions._WindowsPrivateProbeResult(None, False)
    assert len(api.opens) == 1
    assert api.opens[0][0] == Path(tmp_path.anchor)
    assert api.opens[0][1] == 0
    assert [item[1] for item in api.walk] == list(tmp_path.parts[1:])
    metadata_access = SYNCHRONIZE | FILE_READ_ATTRIBUTES
    assert [item[2] for item in api.walk[:-1]] == [metadata_access] * (
        len(api.walk) - 1
    )
    assert api.walk[-1][2] == (metadata_access | FILE_ADD_SUBDIRECTORY)
    root_handle = api.walk[-1][3]
    probe_handle = api.relative[0][0] + 1
    file_handle = probe_handle + 1
    assert [item[0:3] for item in api.relative] == [
        (root_handle, f".secret-write-probe-{'a' * 32}", True),
        (probe_handle, "sentinel", False),
    ]
    assert api.relative[0][3] == (
        READ_CONTROL
        | WRITE_DAC
        | DELETE
        | SYNCHRONIZE
        | FILE_READ_ATTRIBUTES
        | FILE_ADD_FILE
    )
    assert api.relative[1][3] == (
        READ_CONTROL
        | WRITE_DAC
        | DELETE
        | SYNCHRONIZE
        | FILE_READ_ATTRIBUTES
        | FILE_WRITE_DATA
    )
    assert api.acl_events == [
        ("inspect", probe_handle),
        ("apply", probe_handle),
        ("inspect", probe_handle),
        ("inspect", file_handle),
        ("apply", file_handle),
        ("inspect", file_handle),
    ]
    assert api.writes == [(file_handle, b"hermes-secret-write-probe\n")]
    assert api.flushed == [file_handle]
    assert api.deletes == [file_handle, probe_handle]
    assert api.closed[:2] == [file_handle, probe_handle]
    assert api.closed[-1] == 1


def test_private_write_probe_closes_artifact_handles_when_cleanup_marking_fails(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    root = tmp_path / "profile"
    root.mkdir()
    api = HandleBackedProbeApi(permissions)
    api.refuse_next_delete = True
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        root,
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    try:
        assert result == permissions._WindowsPrivateProbeResult(
            None,
            True,
            None,
            "probe-cleanup-file-delete",
        )
        assert result.cleanup_stage == "probe-cleanup-file-delete"
        assert api.children == {}
    finally:
        for handle in tuple(api.children):
            try:
                os.close(handle)
            except OSError:
                pass
        sentinel = root / f".secret-write-probe-{'a' * 32}" / "sentinel"
        if sentinel.exists():
            sentinel.unlink()
        if sentinel.parent.exists():
            sentinel.parent.rmdir()


def test_private_write_probe_cannot_be_redirected_by_root_swap(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    root = tmp_path / "profile"
    root.mkdir()
    attacker = tmp_path / "profile-attacker"
    attacker.mkdir()
    api = HandleBackedProbeApi(permissions, swapped_root=root)
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        root,
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result == permissions._WindowsPrivateProbeResult(None, False)
    assert api.component_swap_fired is True
    assert api.opened_root_identity == api.expected_root_identity
    assert list(root.iterdir()) == []
    assert list(attacker.iterdir()) == []


def test_private_write_probe_creates_absent_root_from_held_ancestor(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    root = tmp_path / "missing-parent" / "profile"
    api = HandleBackedProbeApi(permissions)
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        root,
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result == permissions._WindowsPrivateProbeResult(None, False)
    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert api.root_opens == [0]
    assert api.root_creates == [
        SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_ADD_SUBDIRECTORY,
        SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_ADD_SUBDIRECTORY,
    ]
    assert set(api.relative_opens) <= {
        SYNCHRONIZE | FILE_READ_ATTRIBUTES,
        SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_ADD_SUBDIRECTORY,
        READ_CONTROL
        | WRITE_DAC
        | DELETE
        | SYNCHRONIZE
        | FILE_READ_ATTRIBUTES
        | FILE_ADD_FILE,
    }


def test_native_relative_root_open_disables_reparse_and_rename_sharing():
    from hermes_cli import windows_permissions as permissions

    calls = []

    def nt_create_file(*args):
        calls.append(args)
        args[0]._obj.value = 0xA11
        return 0

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(),
        advapi32=SimpleNamespace(),
        ntdll=SimpleNamespace(NtCreateFile=nt_create_file),
    )

    handle = api.open_relative_directory(0x051D, "profile", access=0x00100001)

    assert handle == 0xA11
    assert len(calls) == 1
    call = calls[0]
    attributes = call[2]._obj
    assert attributes.RootDirectory == 0x051D
    assert attributes.Attributes & 0x00001000
    assert call[6] == 0x00000001 | 0x00000002
    assert not call[6] & 0x00000004
    assert call[7] == 1
    assert call[8] & 0x00200000
    assert call[8] & 0x00000001


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_native_probe_artifact_creation_is_exclusive(directory):
    from hermes_cli import windows_permissions as permissions

    calls = []

    def nt_create_file(*args):
        calls.append(args)
        args[0]._obj.value = 0xA11
        return 0

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(),
        advapi32=SimpleNamespace(),
        ntdll=SimpleNamespace(NtCreateFile=nt_create_file),
    )

    handle = api.create_relative(
        0x051D,
        "artifact",
        directory=directory,
        access=READ_CONTROL | WRITE_DAC | DELETE | SYNCHRONIZE,
    )

    assert handle == 0xA11
    assert len(calls) == 1
    assert calls[0][6] == 0
    assert calls[0][7] == 2


def test_native_private_file_open_is_relative_and_rename_exclusive():
    from hermes_cli import windows_permissions as permissions

    calls = []

    def nt_create_file(*args):
        calls.append(args)
        args[0]._obj.value = 0xA11
        return 0

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(),
        advapi32=SimpleNamespace(),
        ntdll=SimpleNamespace(NtCreateFile=nt_create_file),
    )

    handle = api.open_relative_file(
        0x051D,
        "cache.json",
        access=READ_CONTROL | WRITE_DAC | SYNCHRONIZE,
    )

    assert handle == 0xA11
    assert len(calls) == 1
    call = calls[0]
    assert call[2]._obj.RootDirectory == 0x051D
    assert call[6] == 0
    assert call[7] == 1
    assert call[8] & 0x00200000
    assert call[8] & 0x00000040


def test_native_private_file_read_uses_the_held_handle():
    from hermes_cli import windows_permissions as permissions

    calls = []

    def read_file(*args):
        calls.append(args)
        ctypes.memmove(args[1], b"abc", 3)
        args[3]._obj.value = 3
        return True

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(ReadFile=read_file),
        advapi32=SimpleNamespace(),
    )

    assert api.read_handle(0xA11, 7) == b"abc"
    assert calls[0][0] == 0xA11
    assert calls[0][2] == 7


@pytest.mark.parametrize("delete", [True, False])
def test_native_private_file_delete_disposition_is_handle_bound(delete):
    from hermes_cli import windows_permissions as permissions

    values = []

    def set_information(handle, info_class, information, size):
        disposition = ctypes.cast(
            information,
            ctypes.POINTER(permissions._FILE_DISPOSITION_INFO),
        ).contents
        values.append((handle, info_class, bool(disposition.DeleteFile), size))
        return True

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(SetFileInformationByHandle=set_information),
        advapi32=SimpleNamespace(),
    )

    api.set_delete_on_close(0xA11, delete)

    assert values == [
        (
            0xA11,
            4,
            delete,
            ctypes.sizeof(permissions._FILE_DISPOSITION_INFO),
        )
    ]
    assert ctypes.sizeof(permissions._FILE_DISPOSITION_INFO) == 1


def test_native_private_file_publish_renames_the_held_handle_relative_to_parent():
    from hermes_cli import windows_permissions as permissions

    values = []
    buffers = []

    def set_information(handle, info_class, information, size):
        rename = ctypes.cast(
            information,
            ctypes.POINTER(permissions._FILE_RENAME_INFO),
        ).contents
        name_bytes = ctypes.string_at(
            ctypes.addressof(rename) + permissions._FILE_RENAME_INFO.FileName.offset,
            rename.FileNameLength,
        )
        values.append((
            handle,
            info_class,
            bool(rename.ReplaceIfExists),
            rename.RootDirectory,
            name_bytes.decode("utf-16-le"),
            size,
        ))
        buffers.append(ctypes.string_at(information, size))
        return True

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(SetFileInformationByHandle=set_information),
        advapi32=SimpleNamespace(),
    )

    api.rename_handle(0xF11, 0xD11, "cache.json", replace=True)

    assert values == [
        (
            0xF11,
            3,
            True,
            0xD11,
            "cache.json",
            ctypes.sizeof(permissions._FILE_RENAME_INFO)
            + len("cache.json".encode("utf-16-le"))
            + 2,
        )
    ]
    assert permissions._FILE_RENAME_INFO._fields_[0][1] is wintypes.BOOLEAN
    name_end = permissions._FILE_RENAME_INFO.FileName.offset + len(
        "cache.json".encode("utf-16-le")
    )
    assert buffers[0][name_end:] == b"\0" * (len(buffers[0]) - name_end)


@pytest.mark.parametrize(
    "failure_stage",
    ["anchor", "child", "replacement"],
)
def test_private_write_probe_closes_handles_acquired_before_validation_failure(
    tmp_path, monkeypatch, failure_stage
):
    from hermes_cli import windows_permissions as permissions

    class ValidationFailureApi:
        def __init__(self):
            self.next_handle = 1
            self.closed = []
            self.relative_open_count = 0

        def _new_handle(self):
            handle = self.next_handle
            self.next_handle += 1
            return handle

        def open_handle(self, *_args, **_kwargs):
            return self._new_handle()

        def open_relative_directory(self, *_args, **_kwargs):
            self.relative_open_count += 1
            if failure_stage == "replacement" and self.relative_open_count == 1:
                raise permissions._WindowsCallError(
                    "open relative Windows probe object", 0xC0000034
                )
            return self._new_handle()

        def create_relative_directory(self, *_args, **_kwargs):
            return self._new_handle()

        def handle_metadata(self, handle):
            failing_handle = {"anchor": 1, "child": 2, "replacement": 2}[failure_stage]
            if handle == failing_handle:
                raise RuntimeError("private validation detail")
            return permissions._HandleMetadata(
                FILE_ATTRIBUTE_DIRECTORY,
                permissions._FileIdentity(7, handle),
            )

        def close_handle(self, handle):
            self.closed.append(handle)

    api = ValidationFailureApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        tmp_path / "profile",
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result.failure_type == "RuntimeError"
    assert (
        result.failure_stage
        == {
            "anchor": "probe-open-root-anchor-validate-other",
            "child": "probe-open-root-component-validate-other",
            "replacement": "probe-open-root-parent-upgrade-other",
        }[failure_stage]
    )
    assert result.cleanup_failed is False
    assert {"anchor": [1], "child": [2, 1], "replacement": [2, 1]}[
        failure_stage
    ] == api.closed


def test_private_write_probe_reports_close_failure_after_validation_failure(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    class InvalidAnchorApi:
        def __init__(self):
            self.closed = []

        def open_handle(self, *_args, **_kwargs):
            return 0xA11

        def handle_metadata(self, _handle):
            raise RuntimeError("private validation detail")

        def close_handle(self, handle):
            self.closed.append(handle)
            raise OSError("private cleanup detail")

    api = InvalidAnchorApi()
    monkeypatch.setattr(permissions, "_native_api", lambda: api)

    result = permissions._run_private_acl_write_probe(
        tmp_path / "profile",
        directory_name=f".secret-write-probe-{'a' * 32}",
    )

    assert result.failure_type == "RuntimeError"
    assert result.cleanup_failed is True
    assert api.closed == [0xA11]


def test_task4_probe_boundary_is_private_and_fixes_the_sentinel_contract():
    from hermes_cli import windows_permissions as permissions

    assert "run_private_acl_write_probe" not in permissions.__all__
    assert "WindowsPrivateProbeResult" not in permissions.__all__
    assert not hasattr(permissions, "run_private_acl_write_probe")
    assert not hasattr(permissions, "WindowsPrivateProbeResult")
    signature = inspect.signature(permissions._run_private_acl_write_probe)
    assert list(signature.parameters) == ["root", "directory_name"]


def test_native_create_file_call_uses_win32_argument_order(tmp_path):
    from hermes_cli import windows_permissions as permissions

    calls = []

    def create_file(*args):
        calls.append(args)
        return 0xA11

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(CreateFileW=create_file),
        advapi32=SimpleNamespace(),
    )
    target = tmp_path / "credential"

    handle = api.open_handle(
        target,
        access=READ_CONTROL | WRITE_DAC,
        flags=FILE_FLAG_OPEN_REPARSE_POINT,
    )

    assert handle == 0xA11
    assert calls == [
        (
            str(target),
            READ_CONTROL | WRITE_DAC,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    ]


def test_native_private_directory_handle_denies_rename_sharing(tmp_path):
    from hermes_cli import windows_permissions as permissions

    calls = []

    def create_file(*args):
        calls.append(args)
        return 0xA11

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(CreateFileW=create_file),
        advapi32=SimpleNamespace(),
    )
    target = tmp_path / "private-directory"

    handle = api.open_bound_handle(
        target,
        access=READ_CONTROL | WRITE_DAC | FILE_TRAVERSE | FILE_ADD_FILE,
        flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
    )

    assert handle == 0xA11
    assert calls[0][2] == 0x00000001 | 0x00000002


def test_native_get_security_info_uses_owner_dacl_outputs_and_frees_descriptor():
    from hermes_cli import windows_permissions as permissions

    calls = []
    freed = []

    def get_security_info(*args):
        calls.append(args)
        args[3]._obj.value = 0x0A11
        args[7]._obj.value = 0x0D35
        return 0

    def equal_sid(left, right):
        assert left.value == 0x0A11
        assert right.value == 0x051D
        return True

    def local_free(pointer):
        freed.append(pointer.value)
        return None

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(LocalFree=local_free),
        advapi32=SimpleNamespace(
            GetSecurityInfo=get_security_info,
            EqualSid=equal_sid,
        ),
    )
    information = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION

    state = api.read_acl(
        0xA11,
        permissions._CurrentUserSid(SID, 0x051D, None),
        information,
    )

    assert state.owner_matches is True
    assert state.dacl_present is False
    assert len(calls) == 1
    call = calls[0]
    assert call[0:3] == (0xA11, 1, information)
    assert call[3] is not None
    assert call[4] is None
    assert call[5] is not None
    assert call[6] is None
    assert call[7] is not None
    assert freed == [0x0D35]


@pytest.mark.parametrize("set_result", [0, 5], ids=["success", "failure"])
def test_native_set_security_info_mutates_only_protected_dacl_and_frees_descriptor(
    set_result,
):
    from hermes_cli import windows_permissions as permissions

    set_calls = []
    freed = []

    def convert_descriptor(candidate_sddl, revision, descriptor_out, size_out):
        assert candidate_sddl == sddl
        assert revision == 1
        descriptor_out._obj.value = 0x0D35
        size_out._obj.value = 64
        return True

    def get_dacl(descriptor, present_out, dacl_out, defaulted_out):
        assert descriptor.value == 0x0D35
        present_out._obj.value = True
        dacl_out._obj.value = 0x0AC1
        defaulted_out._obj.value = False
        return True

    def set_security_info(*args):
        set_calls.append(args)
        return set_result

    def local_free(pointer):
        freed.append(pointer.value)
        return None

    api = _uninitialized_native_api(
        permissions,
        kernel32=SimpleNamespace(LocalFree=local_free),
        advapi32=SimpleNamespace(
            ConvertStringSecurityDescriptorToSecurityDescriptorW=convert_descriptor,
            GetSecurityDescriptorDacl=get_dacl,
            SetSecurityInfo=set_security_info,
        ),
    )
    mutation_flags = DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
    sddl = f"D:P(A;;0x{FILE_PRIVATE_MASK:08x};;;{SID})"

    if set_result:
        with pytest.raises(OSError) as captured:
            api.set_dacl(0xA11, sddl, mutation_flags)
        assert captured.value.winerror == set_result
    else:
        api.set_dacl(0xA11, sddl, mutation_flags)

    assert len(set_calls) == 1
    call = set_calls[0]
    assert call[0:3] == (0xA11, 1, mutation_flags)
    assert call[3] is None
    assert call[4] is None
    assert isinstance(call[5], ctypes.c_void_p)
    assert call[5].value == 0x0AC1
    assert call[6] is None
    assert not mutation_flags & (
        OWNER_SECURITY_INFORMATION
        | SACL_SECURITY_INFORMATION
        | UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    assert freed == [0x0D35]


@pytest.mark.parametrize(
    ("directory", "function_name", "applying"),
    [
        (False, "restrict_file_to_current_user", True),
        (True, "restrict_directory_to_current_user", True),
        (False, "inspect_file_acl", False),
        (True, "inspect_directory_acl", False),
    ],
)
def test_public_operations_open_the_target_without_traversing_reparse_points(
    tmp_path, monkeypatch, directory, function_name, applying
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=directory)
    api = _install_api(monkeypatch, permissions, directory=directory)

    getattr(permissions, function_name)(target)

    assert len(api.opens) == 1
    opened_path, access, flags = api.opens[0]
    assert opened_path == target
    assert flags & FILE_FLAG_OPEN_REPARSE_POINT
    assert bool(flags & FILE_FLAG_BACKUP_SEMANTICS) is directory
    expected_access = READ_CONTROL | FILE_READ_ATTRIBUTES
    if applying:
        expected_access |= WRITE_DAC
    assert access == expected_access
    assert not access & (ACCESS_SYSTEM_SECURITY | WRITE_OWNER)
    assert api.closed == [api.handle]


@pytest.mark.parametrize(
    ("directory", "function_name", "expected_sddl"),
    [
        (
            False,
            "restrict_file_to_current_user",
            f"D:P(A;;0x{FILE_PRIVATE_MASK:08x};;;{SID})",
        ),
        (
            True,
            "restrict_directory_to_current_user",
            f"D:P(A;OICI;0x{DIRECTORY_PRIVATE_MASK:08x};;;{SID})",
        ),
    ],
)
def test_apply_builds_one_exact_current_user_ace_and_mutates_only_the_dacl(
    tmp_path, monkeypatch, directory, function_name, expected_sddl
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=directory)
    api = _install_api(monkeypatch, permissions, directory=directory)
    api.states = [api.private_state(), api.private_state()]

    getattr(permissions, function_name)(target)

    assert api.mutations == [
        (
            api.handle,
            expected_sddl,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        )
    ]
    mutation_flags = api.mutations[0][2]
    assert not mutation_flags & (
        OWNER_SECURITY_INFORMATION
        | SACL_SECURITY_INFORMATION
        | UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    assert api.reads == [
        (api.handle, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION),
        (api.handle, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION),
    ]


@pytest.mark.parametrize(
    ("directory", "function_name"),
    [
        (False, "inspect_file_acl"),
        (True, "inspect_directory_acl"),
    ],
)
def test_inspection_requests_only_owner_and_dacl_information(
    tmp_path, monkeypatch, directory, function_name
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=directory)
    api = _install_api(monkeypatch, permissions, directory=directory)

    result = getattr(permissions, function_name)(target)

    assert result == permissions.WindowsAclInspection(secure=True, detail=None)
    assert api.reads == [
        (api.handle, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION)
    ]


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"owner_matches": False}, "owner"),
        ({"dacl_present": False}, "DACL"),
        ({"protected": False}, "inheritance"),
        ({"ace_count": 2}, "exactly one"),
        ({"ace_type": ACCESS_DENIED_ACE_TYPE}, "ACE"),
        ({"ace_flags": INHERITED_ACE}, "ACE"),
        ({"ace_mask": FILE_PRIVATE_MASK ^ 0x01}, "ACE"),
        ({"ace_sid_matches": False}, "ACE"),
    ],
)
def test_file_inspection_fails_closed_for_every_non_private_acl(
    tmp_path, monkeypatch, changes, detail
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=False)
    api = _install_api(monkeypatch, permissions)
    api.states = [api.private_state(**changes)]

    result = permissions.inspect_file_acl(target)

    assert result.secure is False
    assert detail in (result.detail or "")


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_matches": False},
        {"dacl_present": False},
        {"protected": False},
        {"ace_count": 2},
        {"ace_type": ACCESS_DENIED_ACE_TYPE},
        {"ace_flags": INHERITED_ACE},
        {"ace_mask": FILE_PRIVATE_MASK ^ 0x01},
        {"ace_sid_matches": False},
    ],
)
def test_apply_rejects_foreign_owner_or_non_exact_postcondition(
    tmp_path, monkeypatch, changes
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=False)
    api = _install_api(monkeypatch, permissions)
    if changes == {"owner_matches": False}:
        api.states = [api.private_state(**changes)]
    else:
        api.states = [api.private_state(), api.private_state(**changes)]

    with pytest.raises(permissions.WindowsAclError):
        permissions.restrict_file_to_current_user(target)

    assert bool(api.mutations) is (changes != {"owner_matches": False})
    assert api.closed == [api.handle]


@pytest.mark.parametrize(
    ("directory", "reported_attributes"),
    [
        (False, FILE_ATTRIBUTE_REPARSE_POINT),
        (False, FILE_ATTRIBUTE_DIRECTORY),
        (True, 0),
        (True, FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT),
    ],
)
def test_handle_type_or_reparse_point_fails_closed(
    tmp_path, monkeypatch, directory, reported_attributes
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=directory)
    api = _install_api(monkeypatch, permissions, directory=directory)
    api.metadata = [
        permissions._HandleMetadata(
            attributes=reported_attributes,
            identity=permissions._FileIdentity(7, 11),
        )
    ]

    with pytest.raises(permissions.WindowsAclError):
        (
            permissions.inspect_directory_acl(target)
            if directory
            else permissions.inspect_file_acl(target)
        )

    assert api.reads == []
    assert api.closed == [api.handle]


def test_apply_rejects_identity_change_on_the_same_handle(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=False)
    api = _install_api(monkeypatch, permissions)
    api.states = [api.private_state(), api.private_state()]
    api.metadata = [
        permissions._HandleMetadata(0, permissions._FileIdentity(7, 11)),
        permissions._HandleMetadata(0, permissions._FileIdentity(7, 12)),
    ]

    with pytest.raises(permissions.WindowsAclError, match="changed"):
        permissions.restrict_file_to_current_user(target)

    assert len(api.opens) == 1
    assert api.closed == [api.handle]


@pytest.mark.parametrize(
    ("directory", "function_name"),
    [
        (False, "restrict_file_to_current_user"),
        (True, "restrict_directory_to_current_user"),
        (False, "inspect_file_acl"),
        (True, "inspect_directory_acl"),
    ],
)
def test_direct_path_reparse_points_are_rejected_before_native_open(
    tmp_path, monkeypatch, directory, function_name
):
    from hermes_cli import windows_permissions as permissions

    target = _target(tmp_path, directory=directory)
    info = target.lstat()
    reparse_info = SimpleNamespace(
        st_mode=info.st_mode,
        st_dev=info.st_dev,
        st_ino=info.st_ino,
        st_file_attributes=FILE_ATTRIBUTE_REPARSE_POINT,
    )
    real_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda candidate: (
            reparse_info if candidate == target else real_lstat(candidate)
        ),
    )
    factory = mock.Mock()
    monkeypatch.setattr(permissions, "_native_api", factory, raising=False)

    with pytest.raises(permissions.WindowsAclError, match="reparse"):
        getattr(permissions, function_name)(target)

    factory.assert_not_called()


def test_native_failures_are_typed_and_do_not_disclose_payloads(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    secret = "super-secret-environment-value"
    target = tmp_path / f"credential-{secret}"
    target.write_bytes(b"credential")
    monkeypatch.setenv("SECRET_MATERIAL", secret)
    api = _install_api(monkeypatch, permissions)
    api.failure = OSError(f"access denied for {target}; env={secret}")

    with pytest.raises(permissions.WindowsAclError) as captured:
        permissions.restrict_file_to_current_user(target)

    message = str(captured.value)
    assert str(target) not in message
    assert secret not in message
    assert "Windows ACL" in message


@pytest.mark.parametrize("failure_type", [OSError, ValueError, TypeError])
def test_pre_open_path_failures_are_sanitized_without_exception_chaining(
    tmp_path, monkeypatch, failure_type
):
    from hermes_cli import windows_permissions as permissions

    secret = "credential-bearing-path-fragment"
    target = tmp_path / secret
    target.write_bytes(b"credential")

    def fail_lstat(_path):
        raise failure_type(f"cannot inspect {target}; secret={secret}")

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    factory = mock.Mock()
    monkeypatch.setattr(permissions, "_native_api", factory)

    with pytest.raises(permissions.WindowsAclError) as captured:
        permissions.restrict_file_to_current_user(target)

    error = captured.value
    assert str(error) == "cannot inspect Windows ACL path"
    assert secret not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    factory.assert_not_called()


def test_public_api_signatures_and_inspection_shape_are_stable():
    from hermes_cli import windows_permissions as permissions

    for name in (
        "restrict_file_to_current_user",
        "restrict_directory_to_current_user",
        "inspect_file_acl",
        "inspect_directory_acl",
    ):
        signature = inspect.signature(getattr(permissions, name))
        assert list(signature.parameters) == ["path"]
        assert signature.parameters["path"].annotation in {Path, "Path"}

    assert [field.name for field in fields(permissions.WindowsAclInspection)] == [
        "secure",
        "detail",
    ]
    assert permissions.WindowsAclInspection.__dataclass_params__.frozen is True
    assert "open_private_directory" in permissions.__all__
    assert "WindowsPrivateDirectory" not in permissions.__all__
    assert "WindowsPrivateFile" not in permissions.__all__
    signature = inspect.signature(permissions.open_private_directory)
    assert list(signature.parameters) == ["path"]
