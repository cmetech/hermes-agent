"""Strict, stdlib-only Windows ACL boundaries for credential artifacts."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hermes_cli.plugin_secret_keys import without_plugin_secret_keys


class WindowsAclError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowsAclInspection:
    secure: bool
    detail: str | None


_SID_RE = re.compile(r"^S-1-[0-9]+(?:-[0-9]+)+$")
_TIMEOUT_SECONDS = 15
_POWERSHELL = "powershell"

_SCRIPT_PREFIX = (
    "$ErrorActionPreference='Stop';"
    "$p=$env:HERMES_ACL_PATH;"
    "$id=New-Object System.Security.Principal.SecurityIdentifier("
    "$env:HERMES_ACL_SID);"
    "$acl=Get-Acl -LiteralPath $p;"
)
_PURGE_ACL = (
    "$acl.SetAccessRuleProtection($true,$false);"
    "foreach($r in @($acl.Access)){"
    "[void]$acl.PurgeAccessRules($r.IdentityReference)};"
)
_FILE_RIGHTS = (
    "$rights=[System.Security.AccessControl.FileSystemRights]::Read -bor "
    "[System.Security.AccessControl.FileSystemRights]::Write -bor "
    "[System.Security.AccessControl.FileSystemRights]::Synchronize;"
)
_DIRECTORY_RIGHTS = (
    "$rights=[System.Security.AccessControl.FileSystemRights]::ReadAndExecute -bor "
    "[System.Security.AccessControl.FileSystemRights]::Write -bor "
    "[System.Security.AccessControl.FileSystemRights]::CreateFiles -bor "
    "[System.Security.AccessControl.FileSystemRights]::CreateDirectories -bor "
    "[System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles "
    "-bor [System.Security.AccessControl.FileSystemRights]::Synchronize;"
)
_FILE_INHERITANCE = (
    "$inheritance=[System.Security.AccessControl.InheritanceFlags]::None;"
)
_DIRECTORY_INHERITANCE = (
    "$inheritance="
    "[System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor "
    "[System.Security.AccessControl.InheritanceFlags]::ObjectInherit;"
)
_APPLY_SUFFIX = (
    "$acl.SetOwner($id);"
    "$propagation=[System.Security.AccessControl.PropagationFlags]::None;"
    "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule("
    "$id,$rights,$inheritance,$propagation,"
    "[System.Security.AccessControl.AccessControlType]::Allow);"
    "$acl.AddAccessRule($rule);"
    "Set-Acl -LiteralPath $p -AclObject $acl;"
)
_INSPECT_SUFFIX = (
    "$owner=$acl.GetOwner("
    "[System.Security.Principal.SecurityIdentifier]);"
    "$access=@($acl.Access);"
    "$secure=$owner.Value -eq $id.Value -and "
    "$acl.AreAccessRulesProtected -and $access.Count -eq 1;"
    "$detail=$null;"
    "if($owner.Value -ne $id.Value){$detail='ACL owner does not match the current user'}"
    "elseif(-not $acl.AreAccessRulesProtected){$detail='ACL inheritance is enabled'}"
    "elseif($access.Count -ne 1){$detail='expected exactly one explicit ACE'}"
    "else{"
    "$r=$access[0];"
    "$ruleSid=$r.IdentityReference.Translate("
    "[System.Security.Principal.SecurityIdentifier]);"
    "$secure=$secure -and $ruleSid.Value -eq $id.Value -and "
    "$r.AccessControlType -eq "
    "[System.Security.AccessControl.AccessControlType]::Allow -and "
    "$r.FileSystemRights -eq $rights -and "
    "$r.InheritanceFlags -eq $inheritance -and "
    "$r.PropagationFlags -eq "
    "[System.Security.AccessControl.PropagationFlags]::None;"
    "if(-not $secure){$detail='the explicit ACE does not match the current-user rule'}"
    "};"
    "[pscustomobject]@{secure=$secure;detail=$detail;owner_sid=$owner.Value}"
    "|ConvertTo-Json -Compress;"
)

_APPLY_FILE_SCRIPT = (
    _SCRIPT_PREFIX + _PURGE_ACL + _FILE_RIGHTS + _FILE_INHERITANCE + _APPLY_SUFFIX
)
_APPLY_DIRECTORY_SCRIPT = (
    _SCRIPT_PREFIX
    + _PURGE_ACL
    + _DIRECTORY_RIGHTS
    + _DIRECTORY_INHERITANCE
    + _APPLY_SUFFIX
)
_INSPECT_FILE_SCRIPT = (
    _SCRIPT_PREFIX + _FILE_RIGHTS + _FILE_INHERITANCE + _INSPECT_SUFFIX
)
_INSPECT_DIRECTORY_SCRIPT = (
    _SCRIPT_PREFIX
    + _DIRECTORY_RIGHTS
    + _DIRECTORY_INHERITANCE
    + _INSPECT_SUFFIX
)


def _filtered_environment() -> dict[str, str]:
    return without_plugin_secret_keys(os.environ)


def _current_windows_sid() -> str:
    """Resolve the current account by SID without localized account names."""
    try:
        completed = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=_filtered_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    try:
        rows = list(
            csv.reader(
                io.StringIO(completed.stdout, newline=""),
                strict=True,
            )
        )
    except csv.Error:
        return ""
    if len(rows) != 1 or len(rows[0]) != 2:
        return ""
    canonical = io.StringIO(newline="")
    csv.writer(
        canonical,
        lineterminator="\n",
        quoting=csv.QUOTE_ALL,
    ).writerow(rows[0])
    if completed.stdout != canonical.getvalue():
        return ""
    account, candidate = rows[0]
    if not account.strip():
        return ""
    if len(candidate) > 184 or _SID_RE.fullmatch(candidate) is None:
        return ""
    return candidate


def _validated_sid() -> str:
    sid = _current_windows_sid()
    if not sid:
        raise WindowsAclError("current-user SID is unavailable")
    if len(sid) > 184 or _SID_RE.fullmatch(sid) is None:
        raise WindowsAclError("current-user SID is invalid")
    return sid


def _run_powershell(
    script: str,
    path: Path,
    *,
    sid: str | None = None,
) -> subprocess.CompletedProcess[str]:
    sid = sid or _validated_sid()
    child_env = _filtered_environment()
    child_env["HERMES_ACL_PATH"] = str(Path(path))
    child_env["HERMES_ACL_SID"] = sid
    try:
        completed = subprocess.run(
            [
                _POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=child_env,
        )
    except FileNotFoundError as exc:
        raise WindowsAclError("PowerShell is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise WindowsAclError("PowerShell ACL operation timed out") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsAclError("PowerShell ACL operation could not start") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown failure").strip()
        raise WindowsAclError(f"PowerShell ACL operation failed: {detail}")
    return completed


def restrict_file_to_current_user(path: Path) -> None:
    _run_powershell(_APPLY_FILE_SCRIPT, Path(path))


def restrict_directory_to_current_user(path: Path) -> None:
    _run_powershell(_APPLY_DIRECTORY_SCRIPT, Path(path))


def _inspect(path: Path, script: str) -> WindowsAclInspection:
    sid = _validated_sid()
    completed = _run_powershell(script, Path(path), sid=sid)

    def reject_duplicate_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate ACL inspection field")
            result[key] = value
        return result

    try:
        payload = json.loads(
            completed.stdout,
            object_pairs_hook=reject_duplicate_fields,
        )
    except (TypeError, ValueError) as exc:
        raise WindowsAclError("PowerShell ACL inspection returned invalid data") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"secure", "detail", "owner_sid"}
        or type(payload.get("secure")) is not bool
    ):
        raise WindowsAclError("PowerShell ACL inspection returned invalid data")
    detail = payload.get("detail")
    if detail is not None and not isinstance(detail, str):
        raise WindowsAclError("PowerShell ACL inspection returned invalid data")
    owner_sid = payload.get("owner_sid")
    if (
        not isinstance(owner_sid, str)
        or len(owner_sid) > 184
        or _SID_RE.fullmatch(owner_sid) is None
    ):
        raise WindowsAclError("PowerShell ACL inspection returned invalid data")
    if owner_sid != sid:
        return WindowsAclInspection(
            secure=False,
            detail="ACL owner does not match the current user",
        )
    return WindowsAclInspection(secure=payload["secure"], detail=detail)


def inspect_file_acl(path: Path) -> WindowsAclInspection:
    return _inspect(Path(path), _INSPECT_FILE_SCRIPT)


def inspect_directory_acl(path: Path) -> WindowsAclInspection:
    return _inspect(Path(path), _INSPECT_DIRECTORY_SCRIPT)


__all__ = [
    "WindowsAclError",
    "WindowsAclInspection",
    "inspect_directory_acl",
    "inspect_file_acl",
    "restrict_directory_to_current_user",
    "restrict_file_to_current_user",
]
