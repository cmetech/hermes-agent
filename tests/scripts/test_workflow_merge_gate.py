from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess


ROOT = Path(__file__).parents[2]
GATE = ROOT / "scripts/test_workflow_merge_gate.sh"


def test_merge_gate_references_only_existing_invariant_tests() -> None:
    referenced = set(
        re.findall(r"tests/[A-Za-z0-9_./-]+\.(?:py|ts|tsx)", GATE.read_text())
    )

    assert referenced
    assert not [path for path in sorted(referenced) if not (ROOT / path).is_file()]


def test_merge_gate_rejects_invalid_phase_and_unknown_brand() -> None:
    invalid = subprocess.run([GATE, "--phase", "invalid"], cwd=ROOT, text=True, capture_output=True)
    assert invalid.returncode == 2
    assert "base or brand" in invalid.stderr

    brand = subprocess.run(
        [GATE, "--phase", "brand", "--brand", "missing"],
        cwd=ROOT, text=True, capture_output=True,
        env={"PATH": "/usr/bin:/bin", "WORKFLOW_MERGE_GATE_FAST": "1"},
    )
    assert brand.returncode == 2
    assert "unknown brand" in brand.stderr


def test_base_gate_is_offline_and_reports_exact_tested_sha(monkeypatch) -> None:
    env = dict(**__import__("os").environ)
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    result = subprocess.run([GATE, "--phase", "base"], cwd=ROOT, text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert f"TESTED_BASE_SHA={subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}" in result.stdout


def test_partial_gate_installation_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs/upstream-customizations").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "scripts/check_upstream_customizations.py").write_text("raise SystemExit(0)\n")
    (repo / "docs/upstream-customizations/workflow-orchestration.yaml").write_text("schema_version: 1\n")
    result = subprocess.run([GATE, "--repo", repo, "--phase", "base"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1
    assert "partial workflow merge gate" in result.stderr


def _brand_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "brand-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs/upstream-customizations").mkdir(parents=True)
    (repo / "brands").mkdir()
    (repo / "plugins/workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "base"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Gate Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "gate@localhost"], cwd=repo, check=True)
    (repo / "scripts/check_upstream_customizations.py").write_text(
        "from pathlib import Path\nraise SystemExit(9 if Path('FAIL_CHECK').exists() else 0)\n"
    )
    (repo / "scripts/test_workflow_upstream_merge.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "docs/upstream-customizations/workflow-orchestration.yaml").write_text("schema_version: 1\n")
    (repo / "docs/upstream-customizations/merge-evidence.schema.json").write_text("{}\n")
    (repo / "brands/otto.json").write_text('{"slug":"otto"}\n')
    (repo / "plugins/workflow/runtime.py").write_text("VALUE = 'base'\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "checkout", "-b", "otto"], cwd=repo, check=True, capture_output=True)
    (repo / "brand.txt").write_text("otto\n")
    subprocess.run(["git", "add", "brand.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "brand"], cwd=repo, check=True, capture_output=True)
    return repo, base


def _brand_gate(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    return subprocess.run(
        [GATE, "--repo", repo, "--phase", "brand", "--brand", "otto", "--tested-base-sha", base],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )


def test_brand_gate_accepts_exact_base_and_rejects_generic_divergence(tmp_path: Path) -> None:
    repo, base = _brand_repo(tmp_path)
    assert _brand_gate(repo, base).returncode == 0

    (repo / "plugins/workflow/runtime.py").write_text("VALUE = 'brand divergence'\n")
    subprocess.run(["git", "commit", "-am", "diverge generic runtime"], cwd=repo, check=True, capture_output=True)
    result = _brand_gate(repo, base)
    assert result.returncode == 1
    assert "brand diverges" in result.stderr


def test_brand_gate_rejects_tested_commit_outside_brand_ancestry(tmp_path: Path) -> None:
    repo, base = _brand_repo(tmp_path)
    subprocess.run(["git", "checkout", "--orphan", "unrelated"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "rm", "-rf", "."], cwd=repo, check=True, capture_output=True)
    (repo / "unrelated.txt").write_text("unrelated\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "unrelated"], cwd=repo, check=True, capture_output=True)
    unrelated = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "checkout", "otto"], cwd=repo, check=True, capture_output=True)

    result = _brand_gate(repo, unrelated)

    assert result.returncode == 1
    assert "does not contain tested base" in result.stderr
    assert base != unrelated


def test_base_gate_propagates_customization_checker_failure(tmp_path: Path) -> None:
    repo, _base = _brand_repo(tmp_path)
    (repo / "FAIL_CHECK").write_text("fail\n")
    env = os.environ.copy()
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"

    result = subprocess.run(
        [GATE, "--repo", repo, "--phase", "base"],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 9
