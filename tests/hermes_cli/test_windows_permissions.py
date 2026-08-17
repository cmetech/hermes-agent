"""Behavioral tests for the native Windows credential ACL boundary."""

from __future__ import annotations

import inspect
import stat
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
    assert access == (READ_CONTROL | WRITE_DAC if applying else READ_CONTROL)
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
