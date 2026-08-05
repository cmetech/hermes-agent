"""The Windows installer must reject a system npm that cannot install this repo.

npm 11.10.0-11.16.x honor ``min-release-age`` but ignore
``min-release-age-exclude``, both of which ``.npmrc`` sets. ``engines.npm``
excludes that band and ``engine-strict=true`` makes the exclusion fatal, so
such an npm fails ``npm install`` outright::

    npm error code EBADENGINE
    npm error notsup Required: {"node":">=22.22.0","npm":"<11.10.0 || >=11.17.0"}
    npm error notsup Actual:   {"node":"v24.15.0","npm":"11.16.0"}

``scripts/install.sh`` guards this with ``npm_supports_npmrc``: a system
toolchain is adopted only when BOTH halves work. ``scripts/install.ps1`` gated
on the Node floor alone, so Windows adopted any Node >=22.22.0 -- including
Node 24, which bundles npm 11.16.0, squarely inside the band. The node-deps
stage is fail-soft, so both the browser-tools and TUI installs failed with
exit 1 while the stage still reported success and browser tools were silently
dead.

These tests encode the invariant on the Windows side, and that its band agrees
with the one ``engines.npm`` actually excludes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_engines_satisfiable import _satisfies_range

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"

# Real npm releases: the three bundled with the Node majors users arrive with
# (22 -> 10.9.8, 24 -> 11.16.0, 26 -> 11.17.0) plus the band's edges.
_NPM_VERSIONS = ["10.9.8", "11.9.0", "11.10.0", "11.12.1", "11.16.0", "11.17.0", "12.0.2"]


def _source() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def _extract_function(name: str) -> str:
    match = re.search(
        rf"^function {name} \{{$.*?^\}}$", _source(), re.MULTILINE | re.DOTALL
    )
    assert match, f"{name}() not found in install.ps1"
    return match.group(0)


def test_band_predicate_is_defined() -> None:
    """install.ps1 needs the Windows counterpart of npm_supports_npmrc."""
    assert "function Test-NpmSupportsNpmrc" in _source(), (
        "install.ps1 must define Test-NpmSupportsNpmrc -- without it Windows "
        "adopts a system npm that cannot install this repo (scripts/install.sh "
        "has guarded this since the engines floor was fixed)."
    )


def test_test_node_gates_a_system_toolchain_on_the_npm_band() -> None:
    """A system Node passing the version floor is not enough on its own.

    The accept-the-system-toolchain branch of Test-Node must consult the band
    predicate; otherwise Node 24 (npm 11.16.0) is adopted and every npm install
    in the checkout dies with EBADENGINE.
    """
    body = _extract_function("Test-Node")
    accept_branch = re.search(
        r"if \(Test-NodeVersionOk \$version\) \{(.*?)\n {8}\}", body, re.DOTALL
    )
    assert accept_branch, "Test-Node's system-Node accept branch not found"
    guarded = re.search(
        r"if \(\$systemNpmUsable\) \{[^}]*?\$script:HasNode = \$true",
        accept_branch.group(1),
        re.DOTALL,
    )
    assert guarded, (
        "Test-Node must require a usable npm before adopting the system "
        "toolchain (HasNode = true), mirroring check_node() in install.sh."
    )
    assert "Test-SystemNpmUsable" in body, (
        "Test-Node must derive $systemNpmUsable from the band predicate."
    )
    assert "Test-NpmSupportsNpmrc" in _extract_function("Test-SystemNpmUsable")


def test_node_deps_stage_prefers_a_usable_npm() -> None:
    """The stage that runs `npm install` must not use an unusable npm.

    Test-Node's managed-Node PATH ordering reaches this stage only through the
    persisted User PATH, which a locked-down machine can refuse to write and
    which a cross-process stage driver reads before this stage runs. The stage
    re-checks at the point of use so it converges on its own.
    """
    body = _extract_function("Install-NodeDeps")
    assert "Select-UsableNpm" in body, (
        "Install-NodeDeps must route its npm through Select-UsableNpm so a "
        "bad-band npm on PATH is replaced by the Hermes-managed npm."
    )
    selector = _extract_function("Select-UsableNpm")
    assert "Test-NpmSupportsNpmrc" in selector
    assert "node" in selector and "npm.cmd" in selector, (
        "Select-UsableNpm must fall back to the Hermes-managed node tree's npm."
    )


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
@pytest.mark.parametrize("npm_version", _NPM_VERSIONS)
def test_band_predicate_agrees_with_engines_npm(npm_version: str, tmp_path: Path) -> None:
    """Behavioural: the guard must accept exactly what engines.npm accepts.

    Asserting the source mentions a version band does not prove the predicate
    answers correctly, and a hand-written band that drifts from the manifest is
    the whole failure mode -- so run it and compare against engines.npm itself.
    """
    import json

    npm_range = json.loads((REPO_ROOT / "package.json").read_text())["engines"]["npm"]
    expected = _satisfies_range(npm_version, npm_range)

    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(
        _extract_function("Test-NpmSupportsNpmrc")
        + f"\nif (Test-NpmSupportsNpmrc '{npm_version}') {{ 'YES' }} else {{ 'NO' }}\n"
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(fn_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    answer = proc.stdout.strip()
    assert answer == ("YES" if expected else "NO"), (
        f"Test-NpmSupportsNpmrc says {answer} for npm {npm_version}, but "
        f"engines.npm ({npm_range!r}) says {'usable' if expected else 'unusable'}. "
        "The installer's guard and the manifest's gate must agree, or the "
        "installer adopts a toolchain that then fails EBADENGINE."
    )


def _write_stub_npm(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{version}"\n')
    path.chmod(0o755)
    return path


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
@pytest.mark.parametrize(
    ("path_npm_version", "expect_managed"),
    [("11.16.0", True), ("10.9.8", False), ("11.17.0", False)],
)
def test_select_usable_npm_swaps_in_the_managed_npm(
    path_npm_version: str, expect_managed: bool, tmp_path: Path
) -> None:
    """Behavioural: a bad-band npm on PATH must be replaced, a good one kept.

    This is the layer that rescues a machine whose User PATH could not be
    rewritten -- exactly the locked-down server the defect was reported on --
    so asserting only that the source names the managed tree proves nothing.
    """
    home = tmp_path / "home"
    path_npm = _write_stub_npm(tmp_path / "system" / "npm", path_npm_version)
    _write_stub_npm(home / "node" / "npm.cmd", "11.17.0")

    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(
        "function Write-Info { param([string]$m) }\n"
        "function Write-Warn { param([string]$m) }\n"
        f"$HermesHome = '{home}'\n"
        + _extract_function("Test-NpmSupportsNpmrc")
        + "\n"
        + _extract_function("Select-UsableNpm")
        + f"\nSelect-UsableNpm '{path_npm}'\n"
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(fn_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    chosen = proc.stdout.strip()
    if expect_managed:
        assert chosen.endswith("npm.cmd"), (
            f"npm {path_npm_version} is in the band that cannot install this "
            f"repo, but Select-UsableNpm kept it ({chosen})."
        )
    else:
        assert chosen == str(path_npm), (
            f"npm {path_npm_version} can install this repo; replacing it "
            f"({chosen}) swaps a working user toolchain for nothing."
        )


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
def test_select_usable_npm_keeps_path_npm_when_no_managed_tree(tmp_path: Path) -> None:
    """With nothing better available, keep going and let npm report the error.

    Silently returning nothing would turn a loud EBADENGINE into a stage that
    skips without saying why.
    """
    home = tmp_path / "home"
    home.mkdir()
    path_npm = _write_stub_npm(tmp_path / "system" / "npm", "11.16.0")

    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(
        "function Write-Info { param([string]$m) }\n"
        "function Write-Warn { param([string]$m) }\n"
        f"$HermesHome = '{home}'\n"
        + _extract_function("Test-NpmSupportsNpmrc")
        + "\n"
        + _extract_function("Select-UsableNpm")
        + f"\nSelect-UsableNpm '{path_npm}'\n"
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(fn_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(path_npm)


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
@pytest.mark.parametrize("bad_version", ["", "not-a-version"])
def test_band_predicate_rejects_unreadable_versions(bad_version: str, tmp_path: Path) -> None:
    """An npm whose version we cannot read is not assumed usable.

    Mirrors npm_supports_npmrc in scripts/install.sh, which returns non-zero
    for a non-numeric version: the cost is provisioning a managed Node we may
    not have needed; the cost of the other default is a dead install.
    """
    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(
        _extract_function("Test-NpmSupportsNpmrc")
        + f"\nif (Test-NpmSupportsNpmrc '{bad_version}') {{ 'YES' }} else {{ 'NO' }}\n"
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(fn_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NO"
