from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import pytest

from scripts.check_upstream_customizations import classify_upstream_overlap
from tests.scripts.test_check_upstream_customizations import _git, _manifest, _repo


ROOT = Path(__file__).parents[2]
REHEARSAL = ROOT / "scripts/test_workflow_upstream_merge.sh"


def test_rehearsal_runs_brand_generator_from_the_worktree() -> None:
    source = REHEARSAL.read_text()

    assert '(cd "$worktree" && node scripts/brand/generate.mjs' in source


def test_synthetic_overlap_classes_cover_continue_and_stop_cases(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = __import__("yaml").safe_load(_manifest(repo, baseline).read_text())["upstream_changes"][0]

    (repo / "unrelated.py").write_text("value = 1\n")
    _git(repo, "add", "unrelated.py")
    _git(repo, "commit", "-m", "no overlap")
    assert classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")["classification"] == "none"

    same = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    pass\n# unrelated line\n")
    _git(repo, "commit", "-am", "same file")
    assert classify_upstream_overlap(entry, repo, f"{same}..HEAD")["classification"] == "same_file"

    owned = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    changed = True\n")
    _git(repo, "commit", "-am", "owned symbol")
    assert classify_upstream_overlap(entry, repo, f"{owned}..HEAD")["classification"] == "owned_symbol"

    equivalent = _git(repo, "rev-parse", "HEAD")
    (repo / "replacement.py").write_text("class Owned:\n    pass\n")
    _git(repo, "add", "replacement.py")
    _git(repo, "commit", "-m", "upstream equivalent")
    assert classify_upstream_overlap(entry, repo, f"{equivalent}..HEAD")["classification"] == "possible_upstream_equivalent"


def test_rehearsal_rejects_incomplete_args_without_mutating_refs() -> None:
    before = subprocess.check_output(["git", "show-ref", "--heads"], cwd=ROOT)
    result = subprocess.run([REHEARSAL, "--upstream-ref", "main"], cwd=ROOT, text=True, capture_output=True)
    after = subprocess.check_output(["git", "show-ref", "--heads"], cwd=ROOT)
    assert result.returncode == 2
    assert before == after


def _synthetic_rehearsal_repo(tmp_path: Path, overlap: str) -> Path:
    repo = tmp_path / overlap
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Workflow Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "workflow-test@localhost"], cwd=repo, check=True)
    (repo / "core.py").write_text(
        "class Owned:\n    pass\n\n"
        + "\n".join(f"# stable filler {index}" for index in range(20))
        + "\n"
    )
    (repo / "invariant.txt").write_text("invariant\n")
    (repo / "unmanaged.txt").write_text("common\n")
    (repo / "apps/desktop").mkdir(parents=True)
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"hermes","version":"1"}\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")

    (repo / "scripts").mkdir()
    (repo / "docs/upstream-customizations").mkdir(parents=True)
    (repo / "brands").mkdir()
    shutil.copy2(ROOT / "scripts/check_upstream_customizations.py", repo / "scripts")
    shutil.copy2(ROOT / "scripts/test_workflow_upstream_merge.sh", repo / "scripts")
    shutil.copy2(
        ROOT / "docs/upstream-customizations/merge-evidence.schema.json",
        repo / "docs/upstream-customizations/merge-evidence.schema.json",
    )
    gate = repo / "scripts/test_workflow_merge_gate.sh"
    gate.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "ROOT=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "[[ ! -f $ROOT/FAIL_GATE ]] || exit 9\n"
        "echo TESTED_SHA=$(git rev-parse HEAD)\n"
    )
    gate.chmod(0o755)
    generator = repo / "scripts/brand/generate.mjs"
    generator.parent.mkdir()
    generator.write_text(
        "import fs from 'node:fs';\n"
        "const path = new URL('../../apps/desktop/package.json', import.meta.url);\n"
        "const value = JSON.parse(fs.readFileSync(path, 'utf8'));\n"
        "value.name = process.argv[2];\n"
        "fs.writeFileSync(path, JSON.stringify(value) + '\\n');\n"
    )
    (repo / "brands/otto.json").write_text('{"slug":"otto"}\n')
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "feature: workflow-orchestration\n"
        "upstream_changes:\n"
        "- id: owned-core\n"
        "  change_class: generic\n"
        "  owner: workflow-orchestration\n"
        "  files: [core.py]\n"
        "  owned_symbols: [Owned]\n"
        "  tests: [invariant.txt]\n"
        "  expected_commit_subject: local customization\n"
        "  upstream_candidate: true\n"
        "  merge_guidance: preserve the owned contract\n"
        "  removal_condition: remove after an upstream equivalent\n"
        "  last_verified_upstream: 0000000000000000000000000000000000000000\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "install rehearsal gate")
    verified = _git(repo, "rev-parse", "HEAD")
    manifest.write_text(manifest.read_text().replace("0" * 40, verified))
    _git(repo, "commit", "-am", "record verified upstream")
    common = _git(repo, "rev-parse", "HEAD")

    if overlap == "none":
        (repo / "upstream.py").write_text("upstream = True\n")
    elif overlap == "same-file":
        source = (repo / "core.py").read_text()
        (repo / "core.py").write_text(source.replace("# stable filler 19", "# upstream note"))
    elif overlap == "owned-symbol":
        (repo / "core.py").write_text("class Owned:\n    upstream = True\n")
    elif overlap == "upstream-equivalent":
        (repo / "replacement.py").write_text("class Owned:\n    pass\n")
    elif overlap == "failed-gate":
        (repo / "FAIL_GATE").write_text("fail\n")
    else:  # pragma: no cover - helper misuse
        raise AssertionError(overlap)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"upstream {overlap}")

    _git(repo, "branch", "base", common)
    _git(repo, "checkout", "base")
    source = (repo / "core.py").read_text()
    (repo / "core.py").write_text(source.replace("    pass", "    local = True"))
    _git(repo, "commit", "-am", "local customization")
    _git(repo, "branch", "otto")
    _git(repo, "checkout", "otto")
    (repo / "brand.txt").write_text("otto\n")
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"otto","version":"1"}\n'
    )
    _git(repo, "add", "brand.txt")
    _git(repo, "add", "apps/desktop/package.json")
    _git(repo, "commit", "-m", "brand overlay")
    _git(repo, "checkout", "base")
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"hermes","version":"2"}\n'
    )
    _git(repo, "commit", "-am", "advance neutral desktop package")
    return repo


def _run_synthetic(repo: Path, report: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHON_BIN"] = sys.executable
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    return subprocess.run(
        [
            REHEARSAL,
            "--repo",
            repo,
            "--upstream-ref",
            "main",
            "--base-ref",
            "base",
            "--brand-ref",
            "otto",
            "--report-dir",
            report,
            *extra,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )


@pytest.mark.parametrize("overlap", ["none", "same-file"])
def test_rehearsal_auto_merges_safe_overlap_and_emits_valid_evidence(
    tmp_path: Path, overlap: str
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, overlap)
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / f"report-{overlap}"

    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    evidence = json.loads((report / "merge-evidence.json").read_text())
    assert evidence["entries"][0]["overlap_class"] == overlap.replace("-", "_")
    assert evidence["entries"][0]["decision"] == "not-required"
    assert all(command["result"] == "passed" for command in evidence["commands"])
    assert evidence["brands"][0]["contains_tested_base"] is True
    assert "apps/desktop/package.json" in (report / "otto-merge.log").read_text()


@pytest.mark.parametrize("overlap", ["owned-symbol", "upstream-equivalent"])
def test_rehearsal_requires_explicit_decision_for_owned_or_equivalent_overlap(
    tmp_path: Path, overlap: str
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, overlap)
    refs_before = _git(repo, "show-ref", "--heads")

    result = _run_synthetic(repo, tmp_path / f"report-{overlap}")

    assert result.returncode == 4
    assert "explicit preserve/adapt/remove-as-upstream-equivalent" in result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before


def test_failed_invariant_gate_never_advances_or_emits_verified_evidence(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "failed-gate")
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / "report-failed-gate"

    result = _run_synthetic(repo, report)

    assert result.returncode == 6
    assert "no refs were advanced" in result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    assert not (report / "merge-evidence.json").exists()


def test_rehearsal_rejects_unapproved_brand_conflicts(tmp_path: Path) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    (repo / "unmanaged.txt").write_text("base\n")
    _git(repo, "commit", "-am", "change unmanaged file on base")
    _git(repo, "checkout", "otto")
    (repo / "unmanaged.txt").write_text("brand\n")
    _git(repo, "commit", "-am", "change unmanaged file on brand")
    _git(repo, "checkout", "base")
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / "report-unapproved-brand-conflict"

    result = _run_synthetic(repo, report)

    assert result.returncode == 7
    assert "unapproved conflict unmanaged.txt" in result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    assert not (report / "merge-evidence.json").exists()
