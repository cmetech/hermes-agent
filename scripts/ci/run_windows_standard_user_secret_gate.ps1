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

function Invoke-GateChildProcess {
    param(
        [scriptblock]$StartProcess,
        $User,
        $Password,
        [string]$Workspace,
        [string]$Stdout,
        [string]$Stderr,
        [string]$PythonPath,
        [string]$HarnessPath,
        [string]$RepoRoot,
        [string]$ComputerName
    )
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "locked Python environment is unavailable"
    }
    if (-not (Test-Path -LiteralPath $HarnessPath -PathType Leaf)) {
        throw "checked-in Python harness is unavailable"
    }
    if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
        throw "checked-out repository root is unavailable"
    }
    $credential = [System.Management.Automation.PSCredential]::new(
        "$ComputerName\$($User.Name)",
        $Password
    )
    $launchParameters = @{
        FilePath = $PythonPath
        ArgumentList = @(
            "`"$HarnessPath`""
            "--workspace"
            "`"$Workspace`""
        )
        Credential = $credential
        LoadUserProfile = $true
        WorkingDirectory = $RepoRoot
        RedirectStandardOutput = $Stdout
        RedirectStandardError = $Stderr
        Wait = $true
        PassThru = $true
    }
    $process = & $StartProcess $launchParameters
    if ($null -eq $process) {
        throw "standard-user child launch returned no process"
    }
    return [int]$process.ExitCode
}

function New-NativeGateAdapter {
    return [pscustomobject]@{
        PythonPath = $script:PythonPath
        HarnessPath = $script:HarnessPath
        RepoRoot = $script:RepoRoot
        ComputerName = $env:COMPUTERNAME
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
        StartProcess = {
            param([hashtable]$launchParameters)
            Start-Process @launchParameters
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
        if ($line -match '^explicit-write-probe FAIL reason=probe-(native-api|open-root|open-root-(parse|anchor-open|anchor-validate|component-open|parent-upgrade|component-create|component-validate|revalidate)-(access-denied|sharing-violation|invalid-parameter|not-found|reparse|other)|create-directory|protect-directory|create-file|protect-file|write-file|flush-file|cleanup-file-delete|cleanup-file-close|cleanup-directory-delete|cleanup-directory-close|cleanup-root-close|unknown)$') {
            $lines += $line
        }
        elseif ($line -match '^teams-cache-round-trip FAIL reason=teams-((first-persist|first-read|replacement-persist|replacement-read)-(outer|open-directory|close-directory|open-file|create-file|close-file|write-file|flush-file|publish-file|read-file)|first-acl-directory|first-acl-file|replacement-acl-file|cleanup-file|cleanup-directory)$') {
            $lines += $line
        }
        elseif ($line -match '^(platform-preflight|fresh-profile|arm-disabled-auto-keyring|file-tier-acl-repair|plain-doctor-read-only|explicit-write-probe|teams-cache-round-trip|reparse-rejection|cleanup) (PASS|FAIL)$') {
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
    $childExit = Invoke-GateChildProcess `
        -StartProcess $adapter.StartProcess `
        -User $user `
        -Password $password `
        -Workspace $workspace `
        -Stdout $stdout `
        -Stderr $stderr `
        -PythonPath $adapter.PythonPath `
        -HarnessPath $adapter.HarnessPath `
        -RepoRoot $adapter.RepoRoot `
        -ComputerName $adapter.ComputerName
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
