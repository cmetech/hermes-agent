param(
    [string]$UnitTestAdapterPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:HarnessPath = Join-Path $script:RepoRoot "scripts\ci\windows_standard_user_secret_gate.py"
$script:PythonPath = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"

function New-GatePassword {
    $bytes = New-Object byte[] 30
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $plain = [Convert]::ToBase64String($bytes) + "aA1!"
    try {
        return ConvertTo-SecureString $plain -AsPlainText -Force
    }
    finally {
        $plain = $null
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function ConvertTo-GateSidString($user) {
    if ($user.SID -is [System.Security.Principal.SecurityIdentifier]) {
        return $user.SID.Value
    }
    return [string]$user.SID
}

function New-NativeGateAdapter {
    return [pscustomobject]@{
        NewUser = {
            param($name, $password)
            New-LocalUser `
                -Name $name `
                -Password $password `
                -AccountNeverExpires `
                -PasswordNeverExpires `
                -UserMayNotChangePassword
        }
        IsAdministrator = {
            param($user)
            $sid = ConvertTo-GateSidString $user
            $administrators = Get-LocalGroup -SID (
                [System.Security.Principal.SecurityIdentifier]"S-1-5-32-544"
            )
            $members = @(Get-LocalGroupMember -Group $administrators)
            return [bool]($members | Where-Object { $_.SID.Value -eq $sid })
        }
        CreateWorkspace = {
            param($user)
            $workspace = Join-Path $env:RUNNER_TEMP (
                "hermes-standard-user-secret-" + [Guid]::NewGuid().ToString("N")
            )
            New-Item -ItemType Directory -Path $workspace | Out-Null
            return $workspace
        }
        GrantCheckout = {
            param($path, $user)
            $sid = ConvertTo-GateSidString $user
            & icacls.exe $path /grant "*${sid}:(OI)(CI)(RX)" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "checkout bootstrap ACL failed"
            }
        }
        GrantWorkspace = {
            param($path, $user)
            $sid = ConvertTo-GateSidString $user
            & icacls.exe $path `
                /inheritance:r `
                /grant:r `
                "*S-1-5-18:(OI)(CI)(F)" `
                "*S-1-5-32-544:(OI)(CI)(F)" `
                "*${sid}:(OI)(CI)(M)" `
                /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "workspace bootstrap ACL failed"
            }
        }
        Launch = {
            param($user, $password, $workspace, $stdout, $stderr)
            if (-not (Test-Path -LiteralPath $script:PythonPath -PathType Leaf)) {
                throw "locked Python environment is unavailable"
            }
            if (-not (Test-Path -LiteralPath $script:HarnessPath -PathType Leaf)) {
                throw "checked-in Python harness is unavailable"
            }
            $credential = [System.Management.Automation.PSCredential]::new(
                "$env:COMPUTERNAME\$($user.Name)",
                $password
            )
            $process = Start-Process `
                -FilePath $script:PythonPath `
                -ArgumentList @(
                    "`"$script:HarnessPath`""
                    "--workspace"
                    "`"$workspace`""
                ) `
                -Credential $credential `
                -LoadUserProfile `
                -WorkingDirectory $script:RepoRoot `
                -RedirectStandardOutput $stdout `
                -RedirectStandardError $stderr `
                -Wait `
                -PassThru
            return [int]$process.ExitCode
        }
        RevokeCheckout = {
            param($path, $user)
            $sid = ConvertTo-GateSidString $user
            & icacls.exe $path /remove:g "*$sid" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "checkout bootstrap ACL cleanup failed"
            }
        }
        RemoveProfile = {
            param($user)
            $sid = ConvertTo-GateSidString $user
            $profiles = @(
                Get-CimInstance -ClassName Win32_UserProfile |
                    Where-Object { $_.SID -eq $sid }
            )
            foreach ($profile in $profiles) {
                Remove-CimInstance -InputObject $profile
            }
        }
        RemoveUser = {
            param($user)
            $sid = [System.Security.Principal.SecurityIdentifier](
                ConvertTo-GateSidString $user
            )
            Remove-LocalUser -SID $sid
        }
        RemoveWorkspace = {
            param($path)
            if ($path -and (Test-Path -LiteralPath $path)) {
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
}

function Read-RedactedChildOutput([string]$path) {
    $expected = @(
        "platform-preflight PASS"
        "fresh-profile PASS"
        "arm-disabled-auto-keyring PASS"
        "file-tier-acl-repair PASS"
        "plain-doctor-read-only PASS"
        "explicit-write-probe PASS"
        "teams-cache-round-trip PASS"
        "reparse-rejection PASS"
        "cleanup PASS"
    )
    $lines = @()
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return [pscustomobject]@{
            Lines = @("child-output FAIL")
            Valid = $false
        }
    }
    $observed = @(Get-Content -LiteralPath $path)
    foreach ($line in $observed) {
        if ($line -match '^(platform-preflight|fresh-profile|arm-disabled-auto-keyring|file-tier-acl-repair|plain-doctor-read-only|explicit-write-probe|teams-cache-round-trip|reparse-rejection|cleanup) (PASS|FAIL)$') {
            $lines += $line
        }
        elseif ($line -match '^cleanup FAIL path=.+$') {
            $lines += "cleanup FAIL"
        }
    }
    $valid = $observed.Count -eq $expected.Count
    if ($valid) {
        for ($index = 0; $index -lt $expected.Count; $index++) {
            if ($observed[$index] -cne $expected[$index]) {
                $valid = $false
                break
            }
        }
    }
    if (-not $valid) {
        $lines += "child-output FAIL"
    }
    return [pscustomobject]@{
        Lines = $lines
        Valid = $valid
    }
}

$gateFailed = $false
$cleanupFailed = $false
$user = $null
$workspace = $null
$checkoutGrantAttempted = $false
$oldWorkspace = $env:HERMES_WINDOWS_GATE_WORKSPACE

if ($env:CI -ne "true") {
    Write-Output "launcher-ci-only FAIL"
    exit 1
}

try {
    if ($UnitTestAdapterPath) {
        if ($env:HERMES_WINDOWS_GATE_UNIT_TEST -ne "1") {
            throw "unit-test adapter refused"
        }
        $adapter = . (Resolve-Path $UnitTestAdapterPath).Path
    }
    else {
        if ($env:OS -ne "Windows_NT") {
            throw "native Windows launcher required"
        }
        $adapter = New-NativeGateAdapter
    }

    $userName = "hsg-" + [Guid]::NewGuid().ToString("N").Substring(0, 12)
    $password = New-GatePassword
    $user = & $adapter.NewUser $userName $password
    if ($null -eq $user) {
        throw "standard-user creation failed"
    }
    if (& $adapter.IsAdministrator $user) {
        throw "created identity is an administrator"
    }

    $workspace = & $adapter.CreateWorkspace $user
    if (-not $workspace) {
        throw "private workspace creation failed"
    }
    $checkoutGrantAttempted = $true
    & $adapter.GrantCheckout $script:RepoRoot $user
    & $adapter.GrantWorkspace $workspace $user

    $stdout = Join-Path $workspace "gate.stdout"
    $stderr = Join-Path $workspace "gate.stderr"
    $env:HERMES_WINDOWS_GATE_WORKSPACE = $workspace
    $childExit = & $adapter.Launch $user $password $workspace $stdout $stderr
    $childOutput = Read-RedactedChildOutput $stdout
    foreach ($line in $childOutput.Lines) {
        Write-Output $line
    }
    if (-not $childOutput.Valid) {
        $gateFailed = $true
    }
    if ((Test-Path -LiteralPath $stderr -PathType Leaf) -and (
        (Get-Item -LiteralPath $stderr).Length -gt 0
    )) {
        Write-Output "child-stderr FAIL"
        $gateFailed = $true
    }
    if ([int]$childExit -ne 0) {
        Write-Output "child-exit FAIL"
        $gateFailed = $true
    }
}
catch {
    Write-Output "launcher-execution FAIL"
    $gateFailed = $true
}
finally {
    if ($null -eq $oldWorkspace) {
        Remove-Item Env:HERMES_WINDOWS_GATE_WORKSPACE -ErrorAction SilentlyContinue
    }
    else {
        $env:HERMES_WINDOWS_GATE_WORKSPACE = $oldWorkspace
    }
    if ($null -ne $user -and $checkoutGrantAttempted) {
        try {
            & $adapter.RevokeCheckout $script:RepoRoot $user
        }
        catch {
            $cleanupFailed = $true
        }
    }
    if ($null -ne $user) {
        try {
            & $adapter.RemoveProfile $user
        }
        catch {
            $cleanupFailed = $true
        }
        try {
            & $adapter.RemoveUser $user
        }
        catch {
            $cleanupFailed = $true
        }
    }
    if ($workspace) {
        try {
            & $adapter.RemoveWorkspace $workspace
        }
        catch {
            $cleanupFailed = $true
        }
    }
    $password = $null
}

if ($cleanupFailed) {
    Write-Output "launcher-cleanup FAIL"
    $gateFailed = $true
}
if ($gateFailed) {
    exit 1
}
Write-Output "launcher-cleanup PASS"
exit 0
