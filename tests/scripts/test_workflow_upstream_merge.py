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
    assert "npm install --package-lock-only --ignore-scripts --offline" in source


def _write_workspace_lock(repo: Path, name: str, version: str) -> None:
    (repo / "package-lock.json").write_text(json.dumps({
        "name": "fixture",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "fixture", "workspaces": ["apps/*"]},
            "apps/desktop": {"name": name, "version": version},
            f"node_modules/{name}": {"resolved": "apps/desktop", "link": True},
        },
    }, indent=2) + "\n")


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
    (repo / "tests").mkdir()
    (repo / "tests/test_invariant.py").write_text(
        "def test_synthetic_invariant():\n    assert True\n"
    )
    (repo / "unmanaged.txt").write_text("common\n")
    (repo / "apps/desktop").mkdir(parents=True)
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"hermes","version":"1"}\n'
    )
    (repo / "package.json").write_text(
        '{"name":"fixture","private":true,"workspaces":["apps/*"]}\n'
    )
    _write_workspace_lock(repo, "hermes", "1")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")

    (repo / "scripts").mkdir()
    (repo / "docs/upstream-customizations").mkdir(parents=True)
    (repo / "brands").mkdir()
    shutil.copy2(ROOT / "scripts/check_upstream_customizations.py", repo / "scripts")
    shutil.copy2(ROOT / "scripts/test_workflow_upstream_merge.sh", repo / "scripts")
    shutil.copy2(
        ROOT / "scripts/run_workflow_ledger_invariants.py", repo / "scripts"
    )
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
        "  tests: [tests/test_invariant.py]\n"
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
    _write_workspace_lock(repo, "otto", "1")
    _git(repo, "add", "brand.txt")
    _git(repo, "add", "apps/desktop/package.json")
    _git(repo, "add", "package-lock.json")
    _git(repo, "commit", "-m", "brand overlay")
    _git(repo, "checkout", "base")
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"hermes","version":"2"}\n'
    )
    _write_workspace_lock(repo, "hermes", "2")
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
    invariant = evidence["entries"][0]["tests"][0]
    assert invariant["kind"] == "executed"
    assert invariant["path"] == "tests/test_invariant.py"
    assert invariant["result"] == "passed"
    assert invariant["duration_ms"] >= 0
    assert all(command["result"] == "passed" for command in evidence["commands"])
    assert evidence["brands"][0]["contains_tested_base"] is True
    assert "apps/desktop/package.json" in (report / "otto-merge.log").read_text()


def test_rehearsal_reference_only_invariants_cannot_claim_execution(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    fixture_path = "tests/fixtures/invariant.yaml"
    fixture = repo / fixture_path
    fixture.parent.mkdir(parents=True)
    fixture.write_text("contract: stable\n")
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    data = __import__("yaml").safe_load(manifest.read_text())
    data["upstream_changes"][0]["tests"] = [fixture_path]
    manifest.write_text(__import__("yaml").safe_dump(data, sort_keys=False))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "install reference-only invariant")

    report = tmp_path / "report-reference-invariant"
    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    evidence = json.loads((report / "merge-evidence.json").read_text())
    reference = evidence["entries"][0]["tests"][0]
    assert reference == {
        "kind": "reference",
        "name": reference["name"],
        "path": fixture_path,
        "reason": "non-executable invariant reference",
    }
    assert "result" not in reference
    assert "duration_ms" not in reference


@pytest.mark.parametrize(
    "test_path",
    (
        "tests/test_packaging_build_guard.py",
        "tests/scripts/test_workflow_upstream_merge.py",
    ),
)
def test_rehearsal_cannot_report_an_unexecuted_ledger_test_as_passed(
    tmp_path: Path,
    test_path: str,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    invariant = repo / test_path
    invariant.parent.mkdir(parents=True, exist_ok=True)
    invariant.write_text("def test_execution_probe():\n    assert False\n")
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    data = __import__("yaml").safe_load(manifest.read_text())
    data["upstream_changes"][0]["tests"] = [test_path]
    manifest.write_text(__import__("yaml").safe_dump(data, sort_keys=False))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "install failing execution probe")
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / f"report-unexecuted-{invariant.stem}"

    result = _run_synthetic(repo, report)

    assert result.returncode == 9
    assert test_path in result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    assert not (report / "merge-evidence.json").exists()


def test_rehearsal_emits_single_oversized_invariant_as_bounded_exact_evidence(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    data = __import__("yaml").safe_load(manifest.read_text())
    segments = [f"segment-{index:02d}-{'x' * 70}" for index in range(7)]
    test_path = "/".join(["tests", *segments, "test_invariant.py"])
    invariant = repo / test_path
    invariant.parent.mkdir(parents=True)
    invariant.write_text("def test_deep_invariant():\n    assert True\n")
    entry_id = "entry-" + ("i" * 500)
    data["upstream_changes"][0]["id"] = entry_id
    data["upstream_changes"][0]["tests"] = [test_path]
    manifest.write_text(__import__("yaml").safe_dump(data, sort_keys=False))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "install oversized invariant identity")

    report = tmp_path / "report-oversized-entry-test"
    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    evidence = json.loads((report / "merge-evidence.json").read_text())
    invariant_evidence = evidence["entries"][0]["tests"][0]
    assert evidence["entries"][0]["id"] == entry_id
    assert invariant_evidence["kind"] == "executed"
    assert invariant_evidence["path"] == test_path
    assert invariant_evidence["result"] == "passed"
    assert len(invariant_evidence["name"]) <= 512


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
