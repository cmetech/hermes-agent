from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts" / "ci" / "windows_standard_user_secret_gate.py"
LAUNCHER = ROOT / "scripts" / "ci" / "run_windows_standard_user_secret_gate.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

EXPECTED_CASES = [
    "platform-preflight",
    "fresh-profile",
    "arm-disabled-auto-keyring",
    "file-tier-acl-repair",
    "plain-doctor-read-only",
    "explicit-write-probe",
    "teams-cache-round-trip",
    "reparse-rejection",
    "cleanup",
]

COMPLETE_PASS_TRANSCRIPT = """platform-preflight PASS
fresh-profile PASS
arm-disabled-auto-keyring PASS
file-tier-acl-repair PASS
plain-doctor-read-only PASS
explicit-write-probe PASS
teams-cache-round-trip PASS
reparse-rejection PASS
cleanup PASS
"""


def _load_harness():
    assert HARNESS.is_file(), "native gate Python harness is missing"
    spec = importlib.util.spec_from_file_location(
        "windows_standard_user_secret_gate_contract_target", HARNESS
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _ContractAdapter:
    def __init__(self, root: Path, *, fail_case: str | None = None) -> None:
        self.root = root
        self.fail_case = fail_case
        self.calls: list[str] = []
        self.synthetic: list[str | bytes] = []
        self.keyring_entries: set[str] = set()
        self.cache_exists = False
        self.profile: Path | None = None

    def _call(self, case: str, *values: str | bytes) -> None:
        self.calls.append(case)
        self.synthetic.extend(values)
        if case == self.fail_case:
            payload = values[0] if values else "synthetic-adapter-failure"
            raise RuntimeError(payload)

    def verify_standard_user(self) -> None:
        self._call("platform-preflight")

    def create_profile(self) -> Path:
        self._call("fresh-profile")
        self.profile = self.root / "fresh-profile"
        self.profile.mkdir()
        return self.profile

    def exercise_arm_disabled_auto(
        self, profile: Path, first: str, replacement: str
    ) -> None:
        assert profile == self.profile
        self._call("arm-disabled-auto-keyring", first, replacement)
        self.keyring_entries.add("synthetic")

    def exercise_file_tier_acl_repair(
        self, profile: Path, first: str, replacement: str
    ) -> None:
        assert profile == self.profile
        self._call("file-tier-acl-repair", first, replacement)

    def exercise_plain_doctor(self, profile: Path) -> None:
        assert profile == self.profile
        self._call("plain-doctor-read-only")

    def exercise_write_probe(self, profile: Path) -> None:
        assert profile == self.profile
        self._call("explicit-write-probe")

    def exercise_teams_cache(
        self, profile: Path, first: bytes, replacement: bytes
    ) -> None:
        assert profile == self.profile
        self.cache_exists = True
        self._call("teams-cache-round-trip", first, replacement)

    def exercise_reparse_rejection(self, profile: Path, marker: bytes) -> None:
        assert profile == self.profile
        self._call("reparse-rejection", marker)

    def cleanup(self, profile: Path | None) -> None:
        self.calls.append("cleanup")
        self.keyring_entries.clear()
        self.cache_exists = False
        if profile is not None:
            shutil.rmtree(profile)


def test_harness_refuses_non_windows_without_explicit_adapter(monkeypatch) -> None:
    gate = _load_harness()
    monkeypatch.setattr(gate.os, "name", "posix")
    output = io.StringIO()

    assert gate.main(output=output) == 1
    assert output.getvalue().splitlines() == ["platform-preflight FAIL"]


def test_native_adapter_accepts_explicit_workspace_without_inherited_environment(
    tmp_path, monkeypatch
) -> None:
    gate = _load_harness()
    monkeypatch.delenv("HERMES_WINDOWS_GATE_WORKSPACE", raising=False)

    adapter = gate.NativeWindowsAdapter(workspace=tmp_path)
    profile = adapter.create_profile()
    try:
        assert profile.parent == tmp_path
        assert (profile / "config.yaml").is_file()
    finally:
        adapter.cleanup(profile)


def test_harness_cli_routes_explicit_workspace_to_main(tmp_path, monkeypatch) -> None:
    gate = _load_harness()
    workspace = tmp_path / "workspace with spaces"
    observed: list[Path | None] = []

    def fake_main(*, workspace=None, output=sys.stdout, adapter=None):
        observed.append(workspace)
        return 23

    monkeypatch.setattr(gate, "main", fake_main)

    assert gate.run_cli(["--workspace", str(workspace)]) == 23
    assert observed == [workspace]


def test_harness_adapter_contract_covers_native_cases_and_synthetic_cleanup(
    tmp_path,
) -> None:
    gate = _load_harness()
    adapter = _ContractAdapter(tmp_path)
    output = io.StringIO()

    assert gate.main(adapter=adapter, output=output) == 0

    assert adapter.calls == EXPECTED_CASES
    assert len(adapter.synthetic) == 7
    serialized = [
        value.decode("utf-8") if isinstance(value, bytes) else value
        for value in adapter.synthetic
    ]
    assert all(value and len(value) >= 24 for value in serialized)
    assert len(serialized) == len(set(serialized))
    assert adapter.keyring_entries == set()
    assert adapter.cache_exists is False
    assert adapter.profile is not None and not adapter.profile.exists()
    assert output.getvalue().splitlines() == [
        f"{case} PASS" for case in EXPECTED_CASES
    ]
    assert str(tmp_path) not in output.getvalue()
    assert not any(value in output.getvalue() for value in serialized)


def test_harness_scrubs_injected_failure_and_still_runs_finally_cleanup(
    tmp_path,
) -> None:
    gate = _load_harness()
    adapter = _ContractAdapter(tmp_path, fail_case="teams-cache-round-trip")
    output = io.StringIO()

    assert gate.main(adapter=adapter, output=output) == 1

    assert adapter.calls[-1] == "cleanup"
    assert adapter.keyring_entries == set()
    assert adapter.cache_exists is False
    assert adapter.profile is not None and not adapter.profile.exists()
    assert output.getvalue().splitlines()[-2:] == [
        "teams-cache-round-trip FAIL",
        "cleanup PASS",
    ]
    for value in adapter.synthetic:
        rendered = value.decode("utf-8") if isinstance(value, bytes) else value
        assert rendered not in output.getvalue()


def _write_launcher_adapter(
    path: Path,
    *,
    fail_operation: str | None = None,
    child_exit: int = 0,
    child_output: str = COMPLETE_PASS_TRANSCRIPT,
) -> None:
    failure = fail_operation or ""
    path.write_text(
        """
$failure = $env:GATE_FAIL_OPERATION
function Add-GateTrace([string]$value) {
    Add-Content -LiteralPath $env:GATE_TRACE -Value $value
}
[pscustomobject]@{
    PythonPath = $env:GATE_PYTHON_PATH
    HarnessPath = $env:GATE_HARNESS_PATH
    RepoRoot = $env:GATE_REPO_ROOT
    ComputerName = 'portable'
    NewUser = {
        param($name, $password)
        Add-GateTrace "new-user:$name"
        if ($failure -eq 'new-user') { throw 'synthetic new-user failure' }
        [pscustomobject]@{ Name = $name; SID = 'S-1-5-21-1-2-3-1001' }
    }
    IsAdministrator = {
        param($user)
        Add-GateTrace 'check-not-administrator'
        $false
    }
    CreateWorkspace = {
        param($user)
        Add-GateTrace 'create-private-workspace'
        New-Item -ItemType Directory -Path $env:GATE_WORKSPACE -Force | Out-Null
        $env:GATE_WORKSPACE
    }
    GrantCheckout = {
        param($path, $user)
        Add-GateTrace 'grant-checkout:ReadAndExecute'
        if ($failure -eq 'grant-checkout') { throw 'synthetic grant failure' }
    }
    GrantWorkspace = {
        param($path, $user)
        Add-GateTrace 'grant-workspace:Modify'
    }
    StartProcess = {
        param([hashtable]$launchParameters)
        Add-GateTrace 'launch:LoadUserProfile'
        [ordered]@{
            FilePath = [string]$launchParameters.FilePath
            ArgumentList = @($launchParameters.ArgumentList)
            CredentialUserName = [string]$launchParameters.Credential.UserName
            LoadUserProfile = [bool]$launchParameters.LoadUserProfile
            WorkingDirectory = [string]$launchParameters.WorkingDirectory
            RedirectStandardOutput = [string]$launchParameters.RedirectStandardOutput
            RedirectStandardError = [string]$launchParameters.RedirectStandardError
            Wait = [bool]$launchParameters.Wait
            PassThru = [bool]$launchParameters.PassThru
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $env:GATE_LAUNCH_SPEC
        Set-Content -LiteralPath $launchParameters.RedirectStandardOutput -Value $env:GATE_CHILD_OUTPUT -NoNewline
        Set-Content -LiteralPath $launchParameters.RedirectStandardError -Value '' -NoNewline
        if ($failure -eq 'launch') { throw 'synthetic launch failure' }
        [pscustomobject]@{ ExitCode = [int]$env:GATE_CHILD_EXIT }
    }
    RevokeCheckout = {
        param($path, $user)
        Add-GateTrace 'revoke-checkout'
        if ($failure -eq 'revoke-checkout') { throw 'synthetic revoke failure' }
    }
    RemoveProfile = {
        param($user)
        Add-GateTrace 'remove-profile'
        if ($failure -eq 'remove-profile') { throw 'synthetic profile failure' }
    }
    RemoveUser = {
        param($user)
        Add-GateTrace 'remove-user'
        if ($failure -eq 'remove-user') { throw 'synthetic user failure' }
    }
    RemoveWorkspace = {
        param($path)
        Add-GateTrace 'remove-workspace'
        if ($failure -eq 'remove-workspace') { throw 'synthetic workspace failure' }
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _run_launcher(
    tmp_path: Path,
    *,
    fail_operation: str | None = None,
    child_exit: int = 0,
    child_output: str = COMPLETE_PASS_TRANSCRIPT,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert LAUNCHER.is_file(), "standard-user PowerShell launcher is missing"
    tmp_path.mkdir(parents=True, exist_ok=True)
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the portable launcher contract")
    adapter = tmp_path / "launcher-adapter.ps1"
    trace = tmp_path / "trace.txt"
    workspace = tmp_path / "workspace"
    launch_root = tmp_path / "launch paths with spaces"
    python_path = launch_root / "python executable.exe"
    harness_path = launch_root / "gate harness.py"
    repo_root = launch_root / "repo root"
    launch_spec = tmp_path / "launch spec.json"
    repo_root.mkdir(parents=True)
    python_path.touch()
    harness_path.touch()
    _write_launcher_adapter(
        adapter,
        fail_operation=fail_operation,
        child_exit=child_exit,
        child_output=child_output,
    )
    env = os.environ.copy()
    env.update(
        {
            "CI": "true",
            "HERMES_WINDOWS_GATE_UNIT_TEST": "1",
            "GATE_TRACE": str(trace),
            "GATE_WORKSPACE": str(workspace),
            "GATE_FAIL_OPERATION": fail_operation or "",
            "GATE_CHILD_EXIT": str(child_exit),
            "GATE_CHILD_OUTPUT": child_output,
            "GATE_PYTHON_PATH": str(python_path),
            "GATE_HARNESS_PATH": str(harness_path),
            "GATE_REPO_ROOT": str(repo_root),
            "GATE_LAUNCH_SPEC": str(launch_spec),
        }
    )
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(LAUNCHER),
            "-UnitTestAdapterPath",
            str(adapter),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result, lines


def test_native_launcher_builds_credentialed_child_invocation_with_spaced_paths(
    tmp_path,
) -> None:
    root = tmp_path / "portable launcher root with spaces"

    result, trace = _run_launcher(root)

    assert result.returncode == 0, result.stdout + result.stderr
    spec_path = root / "launch spec.json"
    assert spec_path.is_file(), trace
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    credential_user = spec.pop("CredentialUserName")
    assert credential_user.startswith("portable\\hsg-")
    assert len(credential_user) == len("portable\\hsg-") + 12
    assert spec == {
        "FilePath": str(root / "launch paths with spaces" / "python executable.exe"),
        "ArgumentList": [
            f'"{root / "launch paths with spaces" / "gate harness.py"}"',
            "--workspace",
            f'"{root / "workspace"}"',
        ],
        "LoadUserProfile": True,
        "WorkingDirectory": str(root / "launch paths with spaces" / "repo root"),
        "RedirectStandardOutput": str(root / "workspace" / "gate.stdout"),
        "RedirectStandardError": str(root / "workspace" / "gate.stderr"),
        "Wait": True,
        "PassThru": True,
    }


def test_launcher_creates_distinct_non_admin_user_and_limits_bootstrap_access(
    tmp_path,
) -> None:
    first, first_trace = _run_launcher(tmp_path / "first")
    second, second_trace = _run_launcher(tmp_path / "second")

    assert first.returncode == second.returncode == 0
    first_name = first_trace[0].split(":", 1)[1]
    second_name = second_trace[0].split(":", 1)[1]
    assert first_name != second_name
    for trace in (first_trace, second_trace):
        assert trace[1:6] == [
            "check-not-administrator",
            "create-private-workspace",
            "grant-checkout:ReadAndExecute",
            "grant-workspace:Modify",
            "launch:LoadUserProfile",
        ]
        assert trace[-4:] == [
            "revoke-checkout",
            "remove-profile",
            "remove-user",
            "remove-workspace",
        ]
        assert not any(
            operation.startswith(("add-administrator", "grant-privilege", "elevate"))
            for operation in trace
        )


@pytest.mark.parametrize("failure", ["new-user", "launch"])
def test_launcher_fails_instead_of_skipping_and_cleans_created_identity(
    tmp_path, failure
) -> None:
    result, trace = _run_launcher(tmp_path, fail_operation=failure)

    assert result.returncode != 0
    assert "SKIP" not in (result.stdout + result.stderr).upper()
    if failure == "launch":
        assert trace[-4:] == [
            "revoke-checkout",
            "remove-profile",
            "remove-user",
            "remove-workspace",
        ]
    else:
        assert trace == [trace[0]]


def test_launcher_redacts_unexpected_child_output_and_propagates_child_failure(
    tmp_path,
) -> None:
    secret = "synthetic-child-private-output"
    result, trace = _run_launcher(
        tmp_path,
        child_exit=9,
        child_output=f"platform-preflight PASS\n{secret} PASS\n",
    )

    assert result.returncode != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "platform-preflight PASS" in result.stdout
    assert trace[-4:] == [
        "revoke-checkout",
        "remove-profile",
        "remove-user",
        "remove-workspace",
    ]


@pytest.mark.parametrize(
    "child_output",
    [
        pytest.param(
            """platform-preflight PASS
fresh-profile PASS
arm-disabled-auto-keyring PASS
file-tier-acl-repair PASS
plain-doctor-read-only PASS
explicit-write-probe PASS
reparse-rejection PASS
cleanup PASS
""",
            id="incomplete",
        ),
        pytest.param(
            """platform-preflight PASS
fresh-profile PASS
arm-disabled-auto-keyring PASS
file-tier-acl-repair PASS
plain-doctor-read-only PASS
explicit-write-probe PASS
teams-cache-round-trip PASS
teams-cache-round-trip PASS
reparse-rejection PASS
cleanup PASS
""",
            id="duplicate",
        ),
        pytest.param(
            """fresh-profile PASS
platform-preflight PASS
arm-disabled-auto-keyring PASS
file-tier-acl-repair PASS
plain-doctor-read-only PASS
explicit-write-probe PASS
teams-cache-round-trip PASS
cleanup PASS
reparse-rejection PASS
""",
            id="reordered",
        ),
        pytest.param(
            """platform-preflight PASS
fresh-profile PASS
arm-disabled-auto-keyring PASS
file-tier-acl-repair PASS
plain-doctor-read-only FAIL
explicit-write-probe PASS
teams-cache-round-trip PASS
reparse-rejection PASS
cleanup PASS
""",
            id="fail-status",
        ),
    ],
)
def test_launcher_rejects_any_transcript_other_than_nine_ordered_passes(
    tmp_path, child_output
) -> None:
    result, trace = _run_launcher(tmp_path, child_output=child_output)

    assert result.returncode != 0
    assert "launcher-cleanup PASS" not in result.stdout
    assert trace[-4:] == [
        "revoke-checkout",
        "remove-profile",
        "remove-user",
        "remove-workspace",
    ]


@pytest.mark.parametrize(
    ("payload", "private_marker"),
    [
        pytest.param(
            "cleanup FAIL path=C:\\gate\\synthetic-secret-value",
            "synthetic-secret-value",
            id="secret",
        ),
        pytest.param(
            "cleanup FAIL path=RuntimeError: synthetic-exception-private",
            "synthetic-exception-private",
            id="exception",
        ),
        pytest.param(
            "cleanup FAIL path=authority:synthetic-S-1-5-21-999",
            "synthetic-S-1-5-21-999",
            id="authority",
        ),
        pytest.param(
            "cleanup FAIL path=synthetic-unrelated-child-log",
            "synthetic-unrelated-child-log",
            id="unrelated",
        ),
        pytest.param(
            "cleanup FAIL path=..\\..\\synthetic-escape-target",
            "synthetic-escape-target",
            id="escape-path",
        ),
    ],
)
def test_launcher_replaces_cleanup_detail_with_a_fixed_failure_token(
    tmp_path, payload, private_marker
) -> None:
    result, trace = _run_launcher(
        tmp_path,
        child_output=COMPLETE_PASS_TRANSCRIPT + payload + "\n",
    )

    assert result.returncode != 0
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr
    assert "cleanup FAIL" in result.stdout.splitlines()
    assert trace[-4:] == [
        "revoke-checkout",
        "remove-profile",
        "remove-user",
        "remove-workspace",
    ]


def test_launcher_revokes_checkout_after_a_partially_applied_grant_fails(
    tmp_path,
) -> None:
    result, trace = _run_launcher(tmp_path, fail_operation="grant-checkout")

    assert result.returncode != 0
    assert trace == [
        trace[0],
        "check-not-administrator",
        "create-private-workspace",
        "grant-checkout:ReadAndExecute",
        "revoke-checkout",
        "remove-profile",
        "remove-user",
        "remove-workspace",
    ]


@pytest.mark.parametrize(
    "failure",
    ["revoke-checkout", "remove-profile", "remove-user", "remove-workspace"],
)
def test_launcher_cleanup_failures_fail_closed_and_do_not_short_circuit(
    tmp_path, failure
) -> None:
    result, trace = _run_launcher(tmp_path, fail_operation=failure)

    assert result.returncode != 0
    assert "launcher-cleanup FAIL" in result.stdout.splitlines()
    assert "launcher-cleanup PASS" not in result.stdout
    assert trace[-4:] == [
        "revoke-checkout",
        "remove-profile",
        "remove-user",
        "remove-workspace",
    ]


def test_workflow_gates_native_job_by_python_lane_and_aggregates_its_result() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["windows-secret-storage"]

    assert job["needs"] == "detect"
    assert job["if"] == "needs.detect.outputs.python == 'true'"
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] > 0
    assert job["permissions"] == {"contents": "read"}
    checkout = next(step for step in job["steps"] if step["name"] == "Checkout code")
    assert checkout["with"] == {"persist-credentials": False}
    install = next(step for step in job["steps"] if step["name"] == "Install Python and dependencies")
    assert "uv python install 3.11" in install["run"]
    assert (
        "uv sync --locked --python 3.11 --extra all --extra dev" in install["run"]
    )
    gate = next(
        step
        for step in job["steps"]
        if step["name"] == "Run standard-user secret storage gate"
    )
    assert gate == {
        "name": "Run standard-user secret storage gate",
        "shell": "powershell",
        "run": "./scripts/ci/run_windows_standard_user_secret_gate.ps1",
    }
    aggregate = workflow["jobs"]["all-checks-pass"]
    assert aggregate["if"] == "always()"
    assert "windows-secret-storage" in aggregate["needs"]


@pytest.mark.parametrize(
    ("python_lane", "windows_result", "expected_returncode"),
    [
        pytest.param("true", "success", 0, id="affected-success"),
        pytest.param("false", "skipped", 0, id="unaffected-skip"),
        pytest.param("true", "skipped", 1, id="affected-skip"),
        pytest.param("true", "failure", 1, id="affected-failure"),
        pytest.param("true", "cancelled", 1, id="affected-cancellation"),
    ],
)
def test_aggregate_requires_native_success_for_python_relevant_runs(
    tmp_path, python_lane, windows_result, expected_returncode
) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    evaluator = workflow["jobs"]["all-checks-pass"]["steps"][0]["run"]
    needs = {
        "detect": {"result": "success", "outputs": {"python": python_lane}},
        "tests": {"result": "success", "outputs": {}},
        "windows-secret-storage": {"result": windows_result, "outputs": {}},
    }
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", evaluator],
        cwd=ROOT,
        env={
            **os.environ,
            "GITHUB_OUTPUT": str(output),
            "NEEDS": json.dumps(needs),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stdout + result.stderr


def test_launcher_requires_ci_even_with_a_unit_test_adapter(tmp_path) -> None:
    assert LAUNCHER.is_file(), "standard-user PowerShell launcher is missing"
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the portable launcher contract")
    adapter = tmp_path / "launcher-adapter.ps1"
    _write_launcher_adapter(adapter)
    env = os.environ.copy()
    env.pop("CI", None)
    env["HERMES_WINDOWS_GATE_UNIT_TEST"] = "1"

    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(LAUNCHER),
            "-UnitTestAdapterPath",
            str(adapter),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SKIP" not in (result.stdout + result.stderr).upper()
