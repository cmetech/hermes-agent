"""Behavioral tests for the shared Windows credential ACL boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest


SID = "S-1-5-21-1-2-3-1001"
PLUGIN_KEY = "HERMES_PLUGIN_0123456789ABCDEF0123456789ABCDEF_TOKEN"


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["powershell"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_file_acl_uses_constant_script_and_filtered_environment(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    hostile = tmp_path / "it's data'; Write-Output PWNED; $x='"
    hostile.write_text("credential", encoding="utf-8")
    monkeypatch.setenv(PLUGIN_KEY, "must-not-leak")
    monkeypatch.setenv("PRESERVED_SETTING", "present")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(permissions.subprocess, "run", run)

    permissions.restrict_file_to_current_user(hostile)

    argv = run.call_args.args[0]
    script = argv[argv.index("-Command") + 1]
    assert str(hostile) not in script
    assert "PWNED" not in script
    assert SID not in script
    assert "$env:HERMES_ACL_PATH" in script
    assert "$env:HERMES_ACL_SID" in script
    assert "SetAccessRuleProtection($true,$false)" in script
    assert "PurgeAccessRules" in script
    assert script.count("AddAccessRule") == 1
    assert "InheritanceFlags]::None" in script
    assert "FileSystemRights]::Read" in script
    assert "FileSystemRights]::Write" in script
    child_env = run.call_args.kwargs["env"]
    assert child_env["HERMES_ACL_PATH"] == str(hostile)
    assert child_env["HERMES_ACL_SID"] == SID
    assert child_env["PRESERVED_SETTING"] == "present"
    assert PLUGIN_KEY not in child_env


def test_acl_apply_sets_validated_current_user_as_owner(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(permissions.subprocess, "run", run)

    permissions.restrict_file_to_current_user(target)

    argv = run.call_args.args[0]
    script = argv[argv.index("-Command") + 1]
    assert "$acl.SetOwner($id)" in script
    assert SID not in script
    assert run.call_args.kwargs["env"]["HERMES_ACL_SID"] == SID


def test_directory_acl_grants_inheritance_and_required_child_rights(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secrets"
    target.mkdir()
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(permissions.subprocess, "run", run)

    permissions.restrict_directory_to_current_user(target)

    argv = run.call_args.args[0]
    script = argv[argv.index("-Command") + 1]
    assert "ContainerInherit" in script
    assert "ObjectInherit" in script
    assert "ReadAndExecute" in script
    assert "CreateFiles" in script
    assert "CreateDirectories" in script
    assert "DeleteSubdirectoriesAndFiles" in script
    assert script.count("AddAccessRule") == 1


@pytest.mark.parametrize(
    "sid", ["", "not-a-sid", "S-2-5-21-1", "S-1----", "S-1-5-"]
)
def test_missing_or_invalid_sid_raises_typed_error(tmp_path, monkeypatch, sid):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: sid)

    with pytest.raises(permissions.WindowsAclError, match="SID"):
        permissions.restrict_file_to_current_user(target)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError("powershell missing"), "PowerShell"),
        (
            subprocess.TimeoutExpired(cmd=["powershell"], timeout=15),
            "timed out",
        ),
    ],
)
def test_powershell_spawn_failures_raise_typed_error(
    tmp_path, monkeypatch, failure, message
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(permissions.subprocess, "run", mock.Mock(side_effect=failure))

    with pytest.raises(permissions.WindowsAclError, match=message):
        permissions.restrict_file_to_current_user(target)


def test_nonzero_powershell_exit_raises_typed_error(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(return_value=_completed(returncode=1, stderr="access denied")),
    )

    with pytest.raises(permissions.WindowsAclError, match="access denied"):
        permissions.restrict_file_to_current_user(target)


@pytest.mark.parametrize(
    ("kind", "function_name"),
    [("file", "inspect_file_acl"), ("directory", "inspect_directory_acl")],
)
def test_acl_inspection_is_read_only_and_returns_structured_result(
    tmp_path, monkeypatch, kind, function_name
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "artifact"
    target.mkdir() if kind == "directory" else target.write_text(
        "credential", encoding="utf-8"
    )
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    run = mock.Mock(
        return_value=_completed(
            stdout=json.dumps(
                {"secure": False, "detail": "unexpected ACE", "owner_sid": SID}
            )
        )
    )
    monkeypatch.setattr(permissions.subprocess, "run", run)

    result = getattr(permissions, function_name)(target)

    assert result == permissions.WindowsAclInspection(
        secure=False, detail="unexpected ACE"
    )
    script = run.call_args.args[0][run.call_args.args[0].index("-Command") + 1]
    assert "Set-Acl" not in script
    assert "Get-Acl" in script
    assert "GetOwner" in script
    assert str(target) not in script
    assert SID not in script
    assert run.call_args.kwargs["env"]["HERMES_ACL_PATH"] == str(target)


def test_acl_inspection_requires_exact_owner_sid(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(
            return_value=_completed(
                stdout=json.dumps(
                    {
                        "secure": True,
                        "detail": None,
                        "owner_sid": "S-1-5-21-1-2-3-2002",
                    }
                )
            )
        ),
    )

    assert permissions.inspect_file_acl(target) == permissions.WindowsAclInspection(
        secure=False,
        detail="ACL owner does not match the current user",
    )


@pytest.mark.parametrize("owner_sid", [None, 7, "", "not-a-sid"])
def test_acl_inspection_rejects_malformed_owner(
    tmp_path, monkeypatch, owner_sid
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(
            return_value=_completed(
                stdout=json.dumps(
                    {"secure": True, "detail": None, "owner_sid": owner_sid}
                )
            )
        ),
    )

    with pytest.raises(permissions.WindowsAclError, match="inspection"):
        permissions.inspect_file_acl(target)


def test_acl_owner_inspection_error_fails_closed(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(return_value=_completed(returncode=1, stderr="owner denied")),
    )

    with pytest.raises(permissions.WindowsAclError, match="owner denied"):
        permissions.inspect_file_acl(target)


def test_invalid_inspection_output_raises_typed_error(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout="not-json")),
    )

    with pytest.raises(permissions.WindowsAclError, match="inspection"):
        permissions.inspect_file_acl(target)


def test_sid_lookup_filters_plugin_secrets_from_its_child(tmp_path, monkeypatch):
    from hermes_cli import windows_permissions as permissions

    monkeypatch.setenv(PLUGIN_KEY, "must-not-leak")
    run = mock.Mock(return_value=_completed(stdout=f'"DOMAIN\\user","{SID}"\n'))
    monkeypatch.setattr(permissions.subprocess, "run", run)

    assert permissions._current_windows_sid() == SID
    assert PLUGIN_KEY not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"] is not os.environ


@pytest.mark.parametrize(
    "stdout",
    [
        f'"DOMAIN\\user","{SID}","unexpected"\n',
        f'"{SID}","DOMAIN\\user"\n',
        f'"DOMAIN\\user","{SID}"\n"OTHER\\user","{SID}"\n',
        f'"DOMAIN\\user","{SID}"\n\n',
        f'"DOMAIN\\user","{SID}\n',
        f'"DOMAIN\\user","{SID}"  \n',
        f'"","{SID}"\n',
        f'"   ","{SID}"\n',
        f'"DOMAIN\\user"," {SID}"\n',
        f'"DOMAIN\\user","{SID} "\n',
        f'DOMAIN"user,"{SID}"\n',
        f'DOMAIN\\user","{SID}"\n',
    ],
    ids=[
        "extra-field",
        "sid-in-wrong-field",
        "extra-row",
        "trailing-blank-row",
        "unterminated-quote",
        "spaces-after-closing-sid-quote",
        "empty-account",
        "blank-account",
        "leading-sid-whitespace",
        "trailing-sid-whitespace",
        "quote-embedded-in-unquoted-account",
        "unterminated-unquoted-account-quote",
    ],
)
def test_whoami_sid_requires_exactly_one_two_field_csv_row(
    tmp_path, monkeypatch, stdout
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    run = mock.Mock(return_value=_completed(stdout=stdout))
    monkeypatch.setattr(permissions.subprocess, "run", run)

    with pytest.raises(permissions.WindowsAclError, match="SID"):
        permissions.restrict_file_to_current_user(target)

    assert run.call_count == 1


def test_whoami_sid_accepts_canonical_escaped_quote_in_account(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    run = mock.Mock(
        side_effect=[
            _completed(stdout=f'"DOMAIN""user","{SID}"\n'),
            _completed(),
        ]
    )
    monkeypatch.setattr(permissions.subprocess, "run", run)

    permissions.restrict_file_to_current_user(target)

    assert run.call_count == 2


@pytest.mark.parametrize(
    "stdout",
    [
        f'{{"secure":true,"detail":null,"owner_sid":"{SID}","unexpected":false}}',
        f'{{"secure":true,"secure":false,"detail":null,"owner_sid":"{SID}"}}',
        f'{{"detail":null,"detail":"changed","secure":true,"owner_sid":"{SID}"}}',
    ],
    ids=["extra-field", "duplicate-secure", "duplicate-detail"],
)
def test_inspection_json_rejects_extra_or_duplicate_fields(
    tmp_path, monkeypatch, stdout
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout=stdout)),
    )

    with pytest.raises(permissions.WindowsAclError, match="inspection"):
        permissions.inspect_file_acl(target)


def test_file_inspection_rejects_full_control_as_broader_than_required_rights(
    tmp_path, monkeypatch
):
    from hermes_cli import windows_permissions as permissions

    target = tmp_path / "secret"
    target.write_text("credential", encoding="utf-8")
    monkeypatch.setattr(permissions, "_current_windows_sid", lambda: SID)

    def emulate_powershell(argv, **_kwargs):
        script = argv[argv.index("-Command") + 1]
        required_rights = 0x00000116
        full_control = 0x001F01FF
        if "($r.FileSystemRights -band $rights) -eq $rights" in script:
            rights_match = full_control & required_rights == required_rights
        elif "$r.FileSystemRights -eq $rights" in script:
            rights_match = full_control == required_rights
        else:
            raise AssertionError("inspection script did not compare ACL rights")
        return _completed(
            stdout=json.dumps(
                {
                    "secure": rights_match,
                    "detail": None if rights_match else "unexpected rights",
                    "owner_sid": SID,
                }
            )
        )

    monkeypatch.setattr(permissions.subprocess, "run", emulate_powershell)

    assert permissions.inspect_file_acl(target) == permissions.WindowsAclInspection(
        secure=False,
        detail="unexpected rights",
    )
