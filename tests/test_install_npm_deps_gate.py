"""Regression tests for the npm-install fingerprint gate in both installers.

The node-deps stage re-runs ``npm install`` at the repo root and in ui-tui on
every install/upgrade, even when nothing Node-related changed. On Windows
servers (AV real-time scanning, small filesystem cache) those two tree walks
are a large fraction of update time. The gate skips ``npm install`` when a
marker written on the last SUCCESSFUL install matches a fingerprint of
(dependency-spec SHA256, Node major version).

Fail-open contract: any doubt -- no marker, no node on PATH, unreadable
marker, changed fingerprint -- must run ``npm install`` exactly as before.
Markers live inside node_modules/ so deleting node_modules auto-invalidates.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"

BASH_HELPERS = ("npm_deps_fingerprint", "npm_deps_current", "write_npm_deps_marker")
PS_HELPERS = ("Get-NpmDepsFingerprint", "Test-NpmDepsCurrent", "Write-NpmDepsMarker")


def _extract_shell_function(text: str, name: str) -> str:
    """Return one top-level ``name() { ... }`` block from a shell script."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{$.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in install.sh"
    return match.group(0)


def _extract_ps_function(text: str, name: str) -> str:
    """Return one top-level ``function Name { ... }`` block from install.ps1."""
    match = re.search(
        rf"^function {re.escape(name)} \{{$.*?^\}}$", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"function {name} not found in install.ps1"
    return match.group(0)


def _write_node_stub(bindir: Path, version_line: str = "v22.17.1") -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    node = bindir / "node"
    node.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    node.chmod(0o755)


def _run_bash_gate(tmp_path: Path, body: str, node_version: str = "v22.17.1",
                   with_node: bool = True) -> subprocess.CompletedProcess:
    bindir = tmp_path / "stub-bin"
    if with_node:
        _write_node_stub(bindir, node_version)
    else:
        bindir.mkdir(parents=True, exist_ok=True)
    text = INSTALL_SH.read_text()
    funcs = "\n".join(_extract_shell_function(text, n) for n in BASH_HELPERS)
    harness = tmp_path / "harness.sh"
    # Restrict PATH to the stub dir plus core utils so the REAL node (if any)
    # cannot leak into the no-node test.
    harness.write_text(
        f'export PATH="{bindir}:/usr/bin:/bin"\n' + funcs + "\n" + body
    )
    return subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, cwd=tmp_path, check=False
    )


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_bash_fingerprint_tracks_spec_content_and_node_major(tmp_path: Path) -> None:
    spec = tmp_path / "package-lock.json"
    spec.write_text('{"deps": 1}')
    body = f'echo "FP1=$(npm_deps_fingerprint "{spec}")"\n'
    fp1 = _run_bash_gate(tmp_path, body).stdout
    fp1 = re.search(r"FP1=(.*)", fp1).group(1)
    assert fp1.startswith("v1 node=22 sha256="), fp1

    # Same content, same node -> identical fingerprint (determinism).
    fp1b = _run_bash_gate(tmp_path, body).stdout
    assert re.search(r"FP1=(.*)", fp1b).group(1) == fp1

    # Changed content -> different fingerprint.
    spec.write_text('{"deps": 2}')
    fp2 = re.search(r"FP1=(.*)", _run_bash_gate(tmp_path, body).stdout).group(1)
    assert fp2 != fp1
    assert fp2.startswith("v1 node=22 sha256=")

    # Node major bump -> different fingerprint even with identical content.
    spec.write_text('{"deps": 1}')
    fp3 = re.search(
        r"FP1=(.*)", _run_bash_gate(tmp_path, body, node_version="v23.1.0").stdout
    ).group(1)
    assert fp3 != fp1
    assert fp3.startswith("v1 node=23 sha256=")


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_bash_fingerprint_fails_open_without_node_or_spec(tmp_path: Path) -> None:
    spec = tmp_path / "package-lock.json"
    spec.write_text("{}")
    body = f'echo "FP=[$(npm_deps_fingerprint "{spec}")]"\n'
    out = _run_bash_gate(tmp_path, body, with_node=False)
    assert out.returncode == 0, out.stderr
    assert "FP=[]" in out.stdout  # no node -> empty fingerprint

    body_missing = f'echo "FP=[$(npm_deps_fingerprint "{tmp_path}/nope.json")]"\n'
    out2 = _run_bash_gate(tmp_path, body_missing)
    assert out2.returncode == 0, out2.stderr
    assert "FP=[]" in out2.stdout  # no spec file -> empty fingerprint


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_bash_gate_round_trip(tmp_path: Path) -> None:
    spec = tmp_path / "package-lock.json"
    spec.write_text('{"deps": 1}')
    nm = tmp_path / "node_modules"
    nm.mkdir()
    marker = nm / ".npm-deps-fingerprint"
    body = f'''
fp="$(npm_deps_fingerprint "{spec}")"
if npm_deps_current "{marker}" "$fp"; then echo "S1=current"; else echo "S1=stale"; fi
write_npm_deps_marker "{marker}" "$fp"
if npm_deps_current "{marker}" "$fp"; then echo "S2=current"; else echo "S2=stale"; fi
# An EMPTY fingerprint must never be "current", even against an empty marker.
if npm_deps_current "{marker}" ""; then echo "S3=current"; else echo "S3=stale"; fi
echo "changed" > "{spec}"
fp2="$(npm_deps_fingerprint "{spec}")"
if npm_deps_current "{marker}" "$fp2"; then echo "S4=current"; else echo "S4=stale"; fi
'''
    out = _run_bash_gate(tmp_path, body)
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    assert "S1=stale" in out.stdout    # no marker yet
    assert "S2=current" in out.stdout  # marker written and matches
    assert "S3=stale" in out.stdout    # empty fingerprint never current
    assert "S4=stale" in out.stdout    # spec changed -> stale


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_install_sh_wires_gate_at_both_npm_sites() -> None:
    text = INSTALL_SH.read_text()
    fn = _extract_shell_function(text, "install_node_deps")

    # Root site: lockfile preferred as the spec, gate consulted, marker
    # written only in the npm-success arm.
    assert 'root_spec="$INSTALL_DIR/package-lock.json"' in fn
    assert 'root_marker="$INSTALL_DIR/node_modules/.npm-deps-fingerprint"' in fn
    assert 'if npm_deps_current "$root_marker" "$root_fingerprint"' in fn
    assert re.search(
        r'if run_with_timeout "\$NODE_DEPS_TIMEOUT" npm install --silent; then\s*\n'
        r'\s*write_npm_deps_marker "\$root_marker" "\$root_fingerprint"',
        fn,
    ), "root marker must be written only when npm install exits 0"

    # TUI site: ui-tui has no lockfile today, so package.json is the spec
    # (with a lockfile upgrade path); its marker also lives in the ROOT
    # node_modules (workspaces hoist there; wiping node_modules must
    # invalidate both gates).
    assert 'tui_spec="$INSTALL_DIR/ui-tui/package.json"' in fn
    assert 'tui_marker="$INSTALL_DIR/node_modules/.npm-deps-fingerprint-tui"' in fn
    assert 'if npm_deps_current "$tui_marker" "$tui_fingerprint"' in fn
    assert re.search(
        r'if run_with_timeout "\$NODE_DEPS_TIMEOUT" npm install --silent; then\s*\n'
        r'\s*write_npm_deps_marker "\$tui_marker" "\$tui_fingerprint"',
        fn,
    ), "tui marker must be written only when npm install exits 0"

    # The Playwright block must NOT be inside the gate's else-branch: a
    # skipped npm install still verifies the browser engine.
    assert fn.index("npm_deps_current \"$root_marker\"") < fn.index("Playwright")


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
def test_ps_gate_round_trip(tmp_path: Path) -> None:
    """Run the real shipped PowerShell helpers end-to-end against a stub node."""
    text = INSTALL_PS1.read_text()
    fns = "\n".join(_extract_ps_function(text, n) for n in PS_HELPERS)
    fn_file = tmp_path / "fns.ps1"
    fn_file.write_text(fns)

    bindir = tmp_path / "stub-bin"
    _write_node_stub(bindir, "v22.17.1")
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"deps": 1}')
    nm = tmp_path / "node_modules"
    nm.mkdir()
    marker = nm / ".npm-deps-fingerprint"

    script = f'''
        $env:PATH = "{bindir}" + [IO.Path]::PathSeparator + $env:PATH
        . "{fn_file}"
        $fp = Get-NpmDepsFingerprint "{lock}"
        if (-not ($fp -match '^v1 node=22 sha256=[0-9A-F]+$')) {{ throw "bad fingerprint: [$fp]" }}
        if (Test-NpmDepsCurrent "{marker}" $fp) {{ throw "current before any write" }}
        Write-NpmDepsMarker "{marker}" $fp
        if (-not (Test-NpmDepsCurrent "{marker}" $fp)) {{ throw "stale after write" }}
        if (Test-NpmDepsCurrent "{marker}" "") {{ throw "empty fingerprint must never be current" }}
        Set-Content -Path "{lock}" -Value '{{"deps": 2}}'
        $fp2 = Get-NpmDepsFingerprint "{lock}"
        if ($fp2 -eq $fp) {{ throw "fingerprint did not change with content" }}
        if (Test-NpmDepsCurrent "{marker}" $fp2) {{ throw "current after content change" }}
        if ((Get-NpmDepsFingerprint "{tmp_path}/missing.json") -ne "") {{ throw "missing spec must yield empty" }}
        Write-Host "RESULT=OK"
    '''
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "RESULT=OK" in proc.stdout


@pytest.mark.skipif(not INSTALL_PS1.exists(), reason="install.ps1 missing")
def test_install_ps1_wires_gate_at_both_npm_sites() -> None:
    text = INSTALL_PS1.read_text()
    fn = _extract_ps_function(text, "Install-NodeDeps")

    # Root site: gate consulted; a skip must still mark browser npm OK so the
    # Playwright verification block runs.
    assert 'Test-NpmDepsCurrent $rootMarker $rootFingerprint' in fn
    assert '\\node_modules\\.npm-deps-fingerprint"' in fn
    assert re.search(
        r'if \(Test-NpmDepsCurrent \$rootMarker \$rootFingerprint\) \{\s*\n'
        r'[^\n]*\n?\s*Write-Info[^\n]*skipping npm install[^\n]*\n'
        r'\s*\$browserNpmOk = \$true',
        fn,
    ), "root skip path must set browserNpmOk so the Playwright check still runs"
    assert re.search(
        r'if \(\$browserNpmOk\) \{\s*\n\s*Write-NpmDepsMarker \$rootMarker \$rootFingerprint',
        fn,
    ), "root marker must be written only on npm success"

    # TUI site: result captured (no [void]) and marker written on success;
    # the marker lives in the ROOT node_modules like the bash side.
    assert '\\node_modules\\.npm-deps-fingerprint-tui"' in fn
    assert 'Test-NpmDepsCurrent $tuiMarker $tuiFingerprint' in fn
    assert '[void](_Run-NpmInstall' not in fn
    assert re.search(
        r'\$tuiNpmOk = _Run-NpmInstall[^\n]*\n\s*if \(\$tuiNpmOk\) \{\s*\n'
        r'\s*Write-NpmDepsMarker \$tuiMarker \$tuiFingerprint',
        fn,
    ), "tui marker must be written only on npm success"
