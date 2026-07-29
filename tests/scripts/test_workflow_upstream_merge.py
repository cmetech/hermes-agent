from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time

import pytest
from jsonschema import ValidationError, validate
import yaml

import scripts.run_workflow_ledger_invariants as ledger_runner
from scripts.check_upstream_customizations import classify_upstream_overlap
from tests.scripts.test_check_upstream_customizations import _git, _manifest, _repo


ROOT = Path(__file__).parents[2]
REHEARSAL = ROOT / "scripts/test_workflow_upstream_merge.sh"
LEDGER_RUNNER = ROOT / "scripts/run_workflow_ledger_invariants.py"


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
    assert classify_upstream_overlap(entry, repo, f"{equivalent}..HEAD")["classification"] == "none"


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
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "def test_synthetic_invariant():\n"
        "    for name in ('HERMES_PYTHON', 'PYTEST_ADDOPTS', 'PYTHON_BIN', "
        "'WORKFLOW_MERGE_GATE_FAST'):\n"
        "        assert name not in os.environ\n"
        "    assert os.environ['WORKFLOW_LEDGER_PRESERVED_PROBE'] == 'preserved'\n"
        "    assert Path(sys.executable).is_absolute()\n"
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


def _run_synthetic(
    repo: Path,
    report: Path,
    *extra: str,
    discover_python: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_PYTHON"] = "/orchestration-only/hermes-python"
    env["PYTEST_ADDOPTS"] = "--orchestration-only-option"
    if discover_python:
        env.pop("PYTHON_BIN", None)
    else:
        env["PYTHON_BIN"] = sys.executable
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    env["WORKFLOW_LEDGER_PRESERVED_PROBE"] = "preserved"
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
    assert evidence["entries"][0]["overlap_policy"] == "owned_symbol"
    assert evidence["entries"][0]["decision_required"] is False
    assert evidence["entries"][0]["decision"] == "not-required"
    invariant = evidence["entries"][0]["tests"][0]
    assert invariant["kind"] == "executed"
    assert invariant["path"] == "tests/test_invariant.py"
    assert invariant["result"] == "passed"
    assert invariant["duration_ms"] >= 0
    assert invariant["flaky_on_first_attempt"] is False
    assert [attempt["result"] for attempt in invariant["attempts"]] == ["passed"]
    assert all(command["result"] == "passed" for command in evidence["commands"])
    assert evidence["brands"][0]["contains_tested_base"] is True
    assert "apps/desktop/package.json" in (report / "otto-merge.log").read_text()


def test_rehearsal_discovers_repository_virtualenv(tmp_path: Path) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    (repo / ".venv").symlink_to(Path(sys.executable).parent.parent)
    report = tmp_path / "report-discovered-venv"

    result = _run_synthetic(repo, report, discover_python=True)

    assert result.returncode == 0, result.stderr
    assert (report / "merge-evidence.json").is_file()


def test_rehearsal_seals_ledger_execution_to_tested_base_sha(tmp_path: Path) -> None:
    """Dropping --base-ref must make the executable rehearsal fixture refuse."""
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    runner = repo / "scripts/run_workflow_ledger_invariants.py"
    runner.write_text(
        "import argparse, hashlib, json, subprocess\n"
        "from pathlib import Path\n\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo', type=Path, required=True)\n"
        "parser.add_argument('--manifest', type=Path, required=True)\n"
        "parser.add_argument('--output', type=Path, required=True)\n"
        "parser.add_argument('--platform', required=True)\n"
        "parser.add_argument('--base-ref', required=True)\n"
        "args = parser.parse_args()\n"
        "resolved = subprocess.check_output(\n"
        "    ['git', 'rev-parse', '--verify', f'{args.base_ref}^{{commit}}'],\n"
        "    cwd=args.repo, text=True,\n"
        ").strip()\n"
        "head = subprocess.check_output(\n"
        "    ['git', 'rev-parse', '--verify', 'HEAD^{commit}'],\n"
        "    cwd=args.repo, text=True,\n"
        ").strip()\n"
        "if resolved != head:\n"
        "    raise SystemExit(23)\n"
        "path = 'tests/test_invariant.py'\n"
        "digest = hashlib.sha256(path.encode()).hexdigest()\n"
        "args.output.write_text(json.dumps([{\n"
        "    'kind': 'executed', 'name': f'ledger invariant {digest}',\n"
        "    'path': path, 'result': 'passed', 'duration_ms': 0,\n"
        "    'platform': args.platform,\n"
        "    'attempts': [{'attempt': 1, 'result': 'passed',\n"
        "                  'duration_ms': 0, 'output_truncated': False}],\n"
        "    'flaky_on_first_attempt': False,\n"
        "}]) + '\\n')\n"
    )
    _git(repo, "add", str(runner.relative_to(repo)))
    _git(repo, "commit", "-m", "require tested revision sealing")
    report = tmp_path / "report-sealed-ledger-runner"

    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    assert (report / "merge-evidence.json").is_file()


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


def test_rehearsal_preserves_existing_file_path_over_safe_text_limit(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    data = __import__("yaml").safe_load(manifest.read_text())
    segments = [f"owned-{index:02d}-{'f' * 70}" for index in range(7)]
    file_path = "/".join(["owned", *segments, "contract.py"])
    owned_file = repo / file_path
    owned_file.parent.mkdir(parents=True)
    owned_file.write_text("class OwnedBoundary:\n    pass\n")
    assert 512 < len(file_path) < 4096
    data["upstream_changes"][0]["files"] = [file_path]
    data["upstream_changes"][0]["owned_symbols"] = ["OwnedBoundary"]
    manifest.write_text(__import__("yaml").safe_dump(data, sort_keys=False))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "install long owned file path")

    report = tmp_path / "report-long-owned-file"
    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    evidence = json.loads((report / "merge-evidence.json").read_text())
    assert evidence["entries"][0]["files"] == [file_path]


def test_evidence_repository_path_accepts_4096_and_rejects_4097() -> None:
    schema = json.loads(
        (ROOT / "docs/upstream-customizations/merge-evidence.schema.json").read_text()
    )
    repository_path = schema["$defs"]["repositoryPath"]

    validate("p" * 4096, repository_path)
    with pytest.raises(ValidationError, match="too long"):
        validate("p" * 4097, repository_path)


def _evidence_entry(
    *,
    overlap_class: str = "owned_symbol",
    overlap_policy: str = "owned_symbol",
    decision_required: bool = True,
    decision: str = "preserve",
    tests: list[dict] | None = None,
) -> dict:
    sha = "a" * 40
    digest = "b" * 64
    return {
        "id": "owned-core",
        "baseline": sha,
        "files": ["core.py"],
        "patch_sha256": digest,
        "overlap_class": overlap_class,
        "overlap_policy": overlap_policy,
        "decision_required": decision_required,
        "decision": decision,
        "conflict_files": [],
        "retained_commit_subjects": [],
        "removed_commit_subjects": [],
        "tests": tests
        or [
            {
                "kind": "reference",
                "name": "ledger reference",
                "path": "test_core.py",
                "reason": "non-executable invariant reference",
            }
        ],
    }


def _evidence_document(entry: dict) -> dict:
    sha = "a" * 40
    digest = "b" * 64
    evidence = {
        "schema_version": 1,
        "prior_upstream_commit": sha,
        "candidate_upstream_commit": sha,
        "pre_base_commit": sha,
        "post_base_commit": sha,
        "tested_base_tree": sha,
        "ledger_baseline": sha,
        "ledger_sha256": digest,
        "patch_sha256": digest,
        "merge_skill": {"path": "external", "sha256": digest, "owner_commit": None},
        "entries": [entry],
        "commands": [
            {"name": "gate", "result": "passed", "duration_ms": 0, "platform": "test"}
        ],
        "platform": "test",
        "brands": [
            {
                "ref": "otto",
                "commit": sha,
                "tree": sha,
                "descriptor_sha256": digest,
                "contains_tested_base": True,
                "generic_runtime_matches_base": True,
            }
        ],
        "final_ancestry": True,
    }
    return evidence


@pytest.mark.parametrize(
    ("overlap_class", "overlap_policy", "decision_required", "decision"),
    [
        ("owned_symbol", "owned_symbol", False, "not-required"),
        ("possible_upstream_equivalent", "owned_symbol", True, "adapt"),
        ("possible_upstream_equivalent", "owned_symbol", False, "not-required"),
        ("same_file", "any_owned_file", False, "not-required"),
        ("same_file", "owned_symbol", True, "preserve"),
        ("none", "any_owned_file", True, "preserve"),
    ],
)
def test_evidence_rejects_self_asserted_or_contradictory_decision_state(
    overlap_class: str,
    overlap_policy: str,
    decision_required: bool,
    decision: str,
) -> None:
    schema = json.loads(
        (ROOT / "docs/upstream-customizations/merge-evidence.schema.json").read_text()
    )
    evidence = _evidence_document(
        _evidence_entry(
            overlap_class=overlap_class,
            overlap_policy=overlap_policy,
            decision_required=decision_required,
            decision=decision,
        )
    )

    with pytest.raises(ValidationError):
        validate(evidence, schema)


@pytest.mark.parametrize(
    ("overlap_class", "overlap_policy", "decision_required", "decision"),
    [
        ("owned_symbol", "owned_symbol", True, "preserve"),
        ("same_file", "any_owned_file", True, "remove-as-upstream-equivalent"),
        ("same_file", "owned_symbol", False, "not-required"),
        ("none", "any_owned_file", False, "not-required"),
    ],
)
def test_evidence_accepts_only_derived_decision_states(
    overlap_class: str,
    overlap_policy: str,
    decision_required: bool,
    decision: str,
) -> None:
    schema = json.loads(
        (ROOT / "docs/upstream-customizations/merge-evidence.schema.json").read_text()
    )
    evidence = _evidence_document(
        _evidence_entry(
            overlap_class=overlap_class,
            overlap_policy=overlap_policy,
            decision_required=decision_required,
            decision=decision,
        )
    )

    validate(evidence, schema)


def _attempt(
    attempt: int,
    result: str,
    *,
    signal_number: int | None = None,
    output_truncated: bool = False,
) -> dict:
    item = {
        "attempt": attempt,
        "result": result,
        "duration_ms": 1,
        "output_truncated": output_truncated,
    }
    if signal_number is not None:
        item["termination_signal"] = signal_number
    return item


def _executed_test(
    result: str,
    attempts: list[dict],
    *,
    flaky: bool = False,
) -> dict:
    return {
        "kind": "executed",
        "name": "ledger invariant",
        "path": "tests/test_invariant.py",
        "result": result,
        "duration_ms": sum(item["duration_ms"] for item in attempts),
        "platform": "test",
        "attempts": attempts,
        "flaky_on_first_attempt": flaky,
    }


@pytest.mark.parametrize(
    ("result", "attempts", "flaky"),
    [
        ("passed", [_attempt(1, "failed")], False),
        ("passed", [_attempt(1, "failed"), _attempt(2, "passed")], False),
        ("failed", [_attempt(1, "failed")], False),
        ("failed", [_attempt(1, "passed")], False),
        ("failed", [_attempt(1, "failed"), _attempt(2, "passed")], False),
        ("passed", [_attempt(2, "passed")], False),
        ("failed", [_attempt(1, "signaled")], False),
        ("failed", [_attempt(1, "timed_out"), _attempt(2, "failed")], False),
    ],
)
def test_evidence_rejects_contradictory_attempt_state_machine(
    result: str,
    attempts: list[dict],
    flaky: bool,
) -> None:
    schema = json.loads(
        (ROOT / "docs/upstream-customizations/merge-evidence.schema.json").read_text()
    )
    test = _executed_test(result, attempts, flaky=flaky)
    evidence = _evidence_document(_evidence_entry(tests=[test]))

    with pytest.raises(ValidationError):
        validate(evidence, schema)


@pytest.mark.parametrize(
    ("result", "attempts", "flaky"),
    [
        ("passed", [_attempt(1, "passed")], False),
        ("passed", [_attempt(1, "failed"), _attempt(2, "passed")], True),
        ("failed", [_attempt(1, "failed"), _attempt(2, "failed")], False),
        ("failed", [_attempt(1, "timed_out")], False),
        ("failed", [_attempt(1, "signaled", signal_number=15)], False),
        ("failed", [_attempt(1, "infrastructure_error")], False),
        (
            "failed",
            [_attempt(1, "failed"), _attempt(2, "signaled", signal_number=2)],
            False,
        ),
    ],
)
def test_evidence_accepts_consistent_attempt_state_machine(
    result: str,
    attempts: list[dict],
    flaky: bool,
) -> None:
    schema = json.loads(
        (ROOT / "docs/upstream-customizations/merge-evidence.schema.json").read_text()
    )
    test = _executed_test(result, attempts, flaky=flaky)
    evidence = _evidence_document(_evidence_entry(tests=[test]))

    validate(evidence, schema)


def test_ledger_runner_retries_once_and_marks_flaky(tmp_path: Path) -> None:
    """A fail-then-pass invariant keeps its failed-attempt diagnostics in logs."""
    repo = tmp_path / "runner-repo"
    repo.mkdir()
    _git(repo, "init")
    test_path = repo / "tests/test_flaky.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "from pathlib import Path\n"
        "import sys\n\n"
        "def test_passes_on_second_file_attempt():\n"
        "    marker = Path('first-attempt.marker')\n"
        "    if not marker.exists():\n"
        "        marker.write_text('failed once\\n')\n"
        "        stdout_diagnostic = 'FIRST_ATTEMPT_' + 'STDOUT_DIAGNOSTIC'\n"
        "        stderr_diagnostic = 'FIRST_ATTEMPT_' + 'STDERR_DIAGNOSTIC'\n"
        "        print(stdout_diagnostic)\n"
        "        print(stderr_diagnostic, file=sys.stderr)\n"
        "        raise AssertionError('synthetic first-attempt failure')\n"
    )
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_flaky.py"]}]},
            sort_keys=False,
        )
    )
    output = repo / "results.json"

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--platform",
            "synthetic",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    record = json.loads(output.read_text())[0]
    assert record["result"] == "passed"
    assert record["flaky_on_first_attempt"] is True
    assert [attempt["result"] for attempt in record["attempts"]] == [
        "failed",
        "passed",
    ]
    assert all(attempt["output_truncated"] is False for attempt in record["attempts"])
    assert result.stderr.count(
        "ledger invariant nonpassing attempt: tests/test_flaky.py "
        "(attempt 1: failed)"
    ) == 1
    assert result.stderr.count("FIRST_ATTEMPT_STDOUT_DIAGNOSTIC") == 1
    assert result.stderr.count("FIRST_ATTEMPT_STDERR_DIAGNOSTIC") == 1
    serialized = output.read_text()
    assert "FIRST_ATTEMPT_STDOUT_DIAGNOSTIC" not in serialized
    assert "FIRST_ATTEMPT_STDERR_DIAGNOSTIC" not in serialized


def test_ledger_runner_accepts_report_path_and_exact_base_ref(tmp_path: Path) -> None:
    repo = tmp_path / "runner-cli-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    test_path = repo / "tests/test_pass.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_pass():\n"
        "    Path(os.environ['LEDGER_CACHE_OUTPUT']).write_text('cache output')\n"
        "    assert True\n"
    )
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_pass.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "runner fixture")
    report = tmp_path / "outside-report.json"
    cache_output = tmp_path / "outside-cache.txt"
    temp_root = tmp_path / "sealed-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["LEDGER_CACHE_OUTPUT"] = str(cache_output)
    env["TMPDIR"] = str(temp_root)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text())[0]["result"] == "passed"
    assert cache_output.read_text() == "cache output"
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
@pytest.mark.parametrize(
    ("committed_assertion", "dirty_assertion"),
    [("False", "True"), ("True", "False")],
    ids=["committed-fail-dirty-pass", "committed-pass-dirty-fail"],
)
def test_ledger_runner_refuses_dirty_tracked_executable_at_sealed_base(
    tmp_path: Path,
    staged: bool,
    committed_assertion: str,
    dirty_assertion: str,
) -> None:
    repo = tmp_path / "runner-dirty-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    test_path = repo / "tests/test_sealed.py"
    test_path.parent.mkdir()
    committed_source = (
        "def test_committed_contract():\n"
        f"    assert {committed_assertion}\n"
    )
    dirty_source = (
        "def test_committed_contract():\n"
        f"    assert {dirty_assertion}\n"
    )
    test_path.write_text(committed_source)
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_sealed.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "committed invariant")
    test_path.write_text(dirty_source)
    if staged:
        _git(repo, "add", str(test_path.relative_to(repo)))
    status_before = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo,
        text=True,
    )
    report = repo / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "tracked changes" in result.stderr
    assert not report.exists()
    assert test_path.read_text() == dirty_source
    assert subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo,
        text=True,
    ) == status_before


@pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
@pytest.mark.parametrize("target", ["manifest", "script", "test"])
def test_ledger_runner_refuses_dirty_manifest_script_or_test_at_sealed_base(
    tmp_path: Path,
    staged: bool,
    target: str,
) -> None:
    """Every input that can steer discovery must remain committed at the seal."""
    repo = tmp_path / f"runner-dirty-{target}-{staged}"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    authority = repo / "scripts/authority.py"
    authority.parent.mkdir()
    authority.write_text("ALLOW = True\n")
    test_path = repo / "tests/test_sealed.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "from scripts.authority import ALLOW\n\n"
        "def test_committed_contract():\n"
        "    assert ALLOW\n"
    )
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_sealed.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit sealed inputs")
    changed_path = {
        "manifest": manifest,
        "script": authority,
        "test": test_path,
    }[target]
    changed_path.write_text(changed_path.read_text() + "# dirty authority input\n")
    if staged:
        _git(repo, "add", str(changed_path.relative_to(repo)))
    status_before = _git(repo, "status", "--porcelain", "--untracked-files=no")
    report = tmp_path / f"{target}-{staged}-report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "tracked changes" in result.stderr
    assert not report.exists()
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == status_before


@pytest.mark.skipif(os.name != "posix", reason="symlink ownership is POSIX-specific")
def test_sealed_ledger_runner_rejects_tracked_symlink_tree_authority(
    tmp_path: Path,
) -> None:
    """An undeclared committed symlink cannot import bytes outside the base tree."""
    repo = tmp_path / "runner-tracked-symlink-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    marker = tmp_path / "outside-authority-ran"
    outside = tmp_path / "outside-authority.py"
    outside.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('outside authority executed')\n"
        "ALLOW = True\n"
    )
    authority = repo / "authority.py"
    authority.symlink_to(outside)
    test_path = repo / "tests/test_import_authority.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "from authority import ALLOW\n\n"
        "def test_authority():\n"
        "    assert ALLOW\n"
    )
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_import_authority.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit external symlink authority")
    report = tmp_path / "symlink-report.json"
    temp_root = tmp_path / "sealed-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["TMPDIR"] = str(temp_root)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "symlink" in result.stderr
    assert not marker.exists()
    assert not report.exists()
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


def test_sealed_ledger_runner_ignores_untracked_pytest_discovery_inputs(
    tmp_path: Path,
) -> None:
    """An untracked conftest cannot turn a committed failure into green evidence."""
    repo = tmp_path / "runner-untracked-discovery-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    test_path = repo / "tests/test_committed_failure.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "def test_committed_failure():\n"
        "    raise AssertionError('committed invariant must fail')\n"
    )
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_committed_failure.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit failing invariant")
    (repo / "conftest.py").write_text(
        "import pytest\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='untracked bypass'))\n"
    )
    report = tmp_path / "sealed-untracked-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["TMPDIR"] = str(temp_root)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    record = json.loads(report.read_text())[0]
    assert record["result"] == "failed"
    assert [attempt["result"] for attempt in record["attempts"]] == [
        "failed",
        "failed",
    ]
    assert (repo / "conftest.py").is_file()
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="FIFO mutation handshake requires POSIX")
def test_sealed_ledger_runner_ignores_tracked_symlink_mutation_during_run(
    tmp_path: Path,
) -> None:
    """Bytes changed after runner startup cannot replace committed authority."""
    repo = tmp_path / "runner-toc-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    test_path = repo / "tests/test_delayed_authority.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_delayed_authority():\n"
        "    handshake = Path(os.environ['LEDGER_MUTATION_HANDSHAKE'])\n"
        "    first_attempt = handshake.with_suffix('.first')\n"
        "    if not first_attempt.exists():\n"
        "        first_attempt.write_text('started\\n')\n"
        "        with handshake.with_suffix('.ready').open('wb', buffering=0) as ready:\n"
        "            ready.write(b'1')\n"
        "        with handshake.with_suffix('.go').open('rb', buffering=0) as go:\n"
        "            assert go.read(1) == b'1'\n"
        "    from authority import ALLOW\n"
        "    assert ALLOW\n"
    )
    authority = repo / "authority.py"
    authority.write_text("ALLOW = False\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_delayed_authority.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit failing delayed authority")
    outside = tmp_path / "outside-authority.py"
    outside.write_text("ALLOW = True\n")
    handshake = tmp_path / "mutation-handshake"
    ready = handshake.with_suffix(".ready")
    go = handshake.with_suffix(".go")
    os.mkfifo(ready)
    os.mkfifo(go)
    report = tmp_path / "sealed-mutation-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    env = os.environ.copy()
    env["LEDGER_MUTATION_HANDSHAKE"] = str(handshake)
    env["TMPDIR"] = str(temp_root)
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")

    process = subprocess.Popen(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
            "--timeout-seconds",
            "5",
        ],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    with ready.open("rb", buffering=0) as stream:
        assert stream.read(1) == b"1"
    authority.unlink()
    authority.symlink_to(outside)
    with go.open("wb", buffering=0) as stream:
        stream.write(b"1")
    stdout, stderr = process.communicate(timeout=20)

    assert process.returncode == 1, (stdout, stderr)
    record = json.loads(report.read_text())[0]
    assert record["result"] == "failed"
    assert [attempt["result"] for attempt in record["attempts"]] == [
        "failed",
        "failed",
    ]
    assert authority.is_symlink()
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="external toolchain links use POSIX paths")
def test_sealed_ledger_runner_uses_external_node_toolchain_without_live_discovery(
    tmp_path: Path,
) -> None:
    """Vitest resolves third-party bytes and committed workspace packages only."""
    repo = tmp_path / "runner-external-node-toolchain"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    desktop = repo / "apps/desktop"
    test_path = desktop / "src/toolchain.test.ts"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("export const sealedToolchain = true\n")
    electron_test_path = desktop / "electron/toolchain.test.ts"
    electron_test_path.parent.mkdir()
    electron_test_path.write_text("export const sealedElectronToolchain = true\n")
    shared = repo / "apps/shared"
    shared.mkdir()
    (shared / "index.js").write_text("export const authority = 'committed';\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "upstream_changes": [
                    {
                        "tests": [
                            "apps/desktop/electron/toolchain.test.ts",
                            "apps/desktop/src/toolchain.test.ts",
                        ]
                    }
                ]
            },
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit sealed desktop invariant")

    external_project = tmp_path / "external-project"
    external_desktop_modules = external_project / "apps/desktop/node_modules"
    external_desktop_modules.mkdir(parents=True)
    external_root_modules = external_project / "node_modules"
    external_root_modules.mkdir()
    alternate_shared = external_project / "apps/shared"
    alternate_shared.mkdir()
    (alternate_shared / "index.js").write_text(
        "export const authority = 'live alternate checkout';\n"
    )

    root_third_party = external_root_modules / "third-party"
    root_third_party.mkdir()
    (root_third_party / "marker").write_text("external third party\n")
    external_root_cache = external_root_modules / ".vite/vitest/cache-id/results.json"
    external_root_cache.parent.mkdir(parents=True)
    external_root_cache.write_text("external root cache remains immutable\n")
    external_root_temporary_cache = external_root_modules / ".vite-temp"
    external_root_temporary_cache.mkdir()
    root_workspace_scope = external_root_modules / "@hermes"
    root_workspace_scope.mkdir()
    (root_workspace_scope / "shared").symlink_to("../../apps/shared")
    root_third_party_scope = external_root_modules / "@scope"
    root_third_party_scope.mkdir()
    (root_third_party_scope / "tool").symlink_to("../third-party")

    desktop_nested = external_desktop_modules / "nested"
    desktop_nested.mkdir()
    (desktop_nested / "workspace").symlink_to("../../../shared")
    (desktop_nested / "workspace-hop").symlink_to("workspace")
    desktop_third_party_scope = external_desktop_modules / "@scope"
    desktop_third_party_scope.mkdir()
    (desktop_third_party_scope / "tool").symlink_to(
        "../../../../node_modules/third-party"
    )
    desktop_vitest = external_desktop_modules / "vitest"
    desktop_vitest.mkdir()
    external_cache = external_desktop_modules / ".vite/vitest/cache-id/results.json"
    external_cache.parent.mkdir(parents=True)
    external_cache.write_text("external cache remains immutable\n")
    external_temporary_cache = external_desktop_modules / ".vite-temp"
    external_temporary_cache.mkdir()
    vitest = desktop_vitest / "cli.py"
    vitest.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "cwd = Path.cwd()\n"
        "sealed_repo = cwd.parent.parent\n"
        "modules = cwd / 'node_modules'\n"
        "root_modules = sealed_repo / 'node_modules'\n"
        "live_repo = Path(os.environ['EXPECTED_LIVE_REPO']).resolve()\n"
        "external_root = Path(os.environ['EXPECTED_EXTERNAL_ROOT_NODE_MODULES']).resolve()\n"
        "if modules.is_symlink() or root_modules.is_symlink():\n"
        "    raise SystemExit(21)\n"
        "if cwd == live_repo / 'apps/desktop' or (cwd / 'live-discovery-sentinel').exists():\n"
        "    raise SystemExit(22)\n"
        "expected_shared = (sealed_repo / 'apps/shared').resolve()\n"
        "for workspace in (root_modules / '@hermes/shared', modules / 'nested/workspace-hop'):\n"
        "    if workspace.resolve() != expected_shared:\n"
        "        raise SystemExit(23)\n"
        "    if (workspace / 'index.js').read_text() != \"export const authority = 'committed';\\n\":\n"
        "        raise SystemExit(24)\n"
        "for third_party in (root_modules / '@scope/tool', modules / '@scope/tool'):\n"
        "    if not third_party.resolve().is_relative_to(external_root):\n"
        "        raise SystemExit(25)\n"
        "    if (third_party / 'marker').read_text() != 'external third party\\n':\n"
        "        raise SystemExit(26)\n"
        "if any(\n"
        "    raw and Path(raw).resolve().is_relative_to(live_repo)\n"
        "    for raw in os.environ.get('PATH', '').split(os.pathsep)\n"
        "):\n"
        "    raise SystemExit(27)\n"
        "if any(os.environ.get(name) for name in ('NODE_PATH', 'PYTHONPATH', 'INIT_CWD')):\n"
        "    raise SystemExit(28)\n"
        "if Path(os.environ['PWD']).resolve() != cwd.resolve():\n"
        "    raise SystemExit(29)\n"
        "if sys.argv[1:] != ['run', 'src/toolchain.test.ts']:\n"
        "    raise SystemExit(30)\n"
        "cache = modules / '.vite/vitest/cache-id/results.json'\n"
        "cache.parent.mkdir(parents=True, exist_ok=True)\n"
        "cache.write_text('sealed cache write\\n')\n"
        "temporary_cache = modules / '.vite-temp/vitest-temporary'\n"
        "temporary_cache.parent.mkdir(parents=True, exist_ok=True)\n"
        "temporary_cache.write_text('sealed temporary cache write\\n')\n"
        "root_cache = root_modules / '.vite/vitest/cache-id/results.json'\n"
        "root_cache.parent.mkdir(parents=True, exist_ok=True)\n"
        "root_cache.write_text('sealed root cache write\\n')\n"
        "root_temporary_cache = root_modules / '.vite-temp/vitest-temporary'\n"
        "root_temporary_cache.parent.mkdir(parents=True, exist_ok=True)\n"
        "root_temporary_cache.write_text('sealed root temporary cache write\\n')\n"
        "Path(os.environ['OBSERVED_NODE_CWD']).write_text(str(cwd))\n"
    )
    vitest.chmod(0o755)
    desktop_tsx = external_desktop_modules / "tsx"
    desktop_tsx.mkdir()
    tsx = desktop_tsx / "cli.py"
    tsx.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "cwd = Path.cwd()\n"
        "cache = cwd / 'node_modules/.vite/vitest/cache-id/results.json'\n"
        "if cache.read_text() != 'sealed cache write\\n':\n"
        "    raise SystemExit(31)\n"
        "temporary_cache = cwd / 'node_modules/.vite-temp/vitest-temporary'\n"
        "if temporary_cache.read_text() != 'sealed temporary cache write\\n':\n"
        "    raise SystemExit(33)\n"
        "sealed_repo = cwd.parent.parent\n"
        "root_cache = sealed_repo / 'node_modules/.vite/vitest/cache-id/results.json'\n"
        "if root_cache.read_text() != 'sealed root cache write\\n':\n"
        "    raise SystemExit(34)\n"
        "root_temporary_cache = sealed_repo / 'node_modules/.vite-temp/vitest-temporary'\n"
        "if root_temporary_cache.read_text() != 'sealed root temporary cache write\\n':\n"
        "    raise SystemExit(35)\n"
        "final_cache = cwd / 'node_modules/.vite/tsx-final-cache'\n"
        "final_cache.write_text('final cache write\\n')\n"
        "final_root_cache = sealed_repo / 'node_modules/.vite/tsx-final-cache'\n"
        "final_root_cache.write_text('final root cache write\\n')\n"
        "if sys.argv[1:] != ['--test', 'electron/toolchain.test.ts']:\n"
        "    raise SystemExit(32)\n"
        "Path(os.environ['OBSERVED_TSX']).write_text('tsx ran after revalidation')\n"
    )
    tsx.chmod(0o755)
    desktop_bin = external_desktop_modules / ".bin"
    desktop_bin.mkdir()
    (desktop_bin / "vitest").symlink_to("../vitest/cli.py")
    (desktop_bin / "tsx").symlink_to("../tsx/cli.py")
    (repo / "node_modules").symlink_to(external_root_modules, target_is_directory=True)
    (desktop / "node_modules").symlink_to(
        external_desktop_modules,
        target_is_directory=True,
    )
    live_sentinel = desktop / "live-discovery-sentinel"
    live_sentinel.write_text("must never enter the sealed test cwd\n")
    external_bin = tmp_path / "external-node-bin"
    external_bin.mkdir()
    observed_cwd = tmp_path / "observed-node-cwd"
    observed_tsx = tmp_path / "observed-tsx"
    node = external_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n")
    node.chmod(0o755)
    npx = external_bin / "npx"
    npx.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "tool = Path.cwd() / 'node_modules/.bin' / sys.argv[1]\n"
        "if not tool.exists():\n"
        "    raise SystemExit(19)\n"
        "os.execv(str(tool), [str(tool), *sys.argv[2:]])\n"
    )
    npx.chmod(0o755)
    report = tmp_path / "sealed-node-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["PATH"] = f"{external_bin}{os.pathsep}{env['PATH']}"
    env["EXPECTED_EXTERNAL_ROOT_NODE_MODULES"] = str(external_root_modules)
    env["EXPECTED_LIVE_REPO"] = str(repo)
    env["OBSERVED_NODE_CWD"] = str(observed_cwd)
    env["OBSERVED_TSX"] = str(observed_tsx)
    env["NODE_PATH"] = str(external_desktop_modules)
    env["PYTHONPATH"] = str(repo)
    env["INIT_CWD"] = str(repo)
    env["TMPDIR"] = str(temp_root)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert [record["result"] for record in json.loads(report.read_text())] == [
        "passed",
        "passed",
    ]
    assert observed_cwd.is_file()
    assert observed_tsx.is_file()
    assert external_cache.read_text() == "external cache remains immutable\n"
    assert list(external_temporary_cache.iterdir()) == []
    assert external_root_cache.read_text() == "external root cache remains immutable\n"
    assert list(external_root_temporary_cache.iterdir()) == []
    assert Path(observed_cwd.read_text()).parent.name == "apps"
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="external toolchain links use POSIX paths")
@pytest.mark.parametrize(
    (
        "mutating_tool",
        "mutation_kind",
        "tool_exit_code",
        "tsx_should_run",
        "expected_invocations",
    ),
    [
        ("vitest", "external-replace", 0, False, ["vitest"]),
        ("tsx", "external-replace", 0, True, ["vitest", "tsx"]),
        ("tsx", "external-add", 0, True, ["vitest", "tsx"]),
        ("tsx", "view-add", 0, True, ["vitest", "tsx"]),
        ("tsx", "view-replace", 0, True, ["vitest", "tsx"]),
        ("node", "external-replace", 0, True, ["vitest", "tsx", "node"]),
        # Drift is infrastructure failure even when the test itself exits 1:
        # detect it before the normal failed-test retry can run.
        ("vitest", "external-replace", 1, False, ["vitest"]),
    ],
)
def test_sealed_ledger_runner_rejects_dependency_mutation_after_every_node_group(
    tmp_path: Path,
    mutating_tool: str,
    mutation_kind: str,
    tool_exit_code: int,
    tsx_should_run: bool,
    expected_invocations: list[str],
) -> None:
    """A first or final successful Node group cannot leave dependency drift green."""
    repo = tmp_path / "runner-mutated-node-dependency"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    desktop = repo / "apps/desktop"
    source_test = desktop / "src/toolchain.test.ts"
    source_test.parent.mkdir(parents=True)
    source_test.write_text("export const sealedToolchain = true\n")
    electron_test = desktop / "electron/toolchain.test.ts"
    electron_test.parent.mkdir()
    electron_test.write_text("export const sealedElectronToolchain = true\n")
    final_node_test = repo / "tests/final-toolchain.test.mjs"
    final_node_test.parent.mkdir()
    final_node_test.write_text("export const sealedFinalToolchain = true\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "upstream_changes": [
                    {
                        "tests": [
                            "apps/desktop/electron/toolchain.test.ts",
                            "apps/desktop/src/toolchain.test.ts",
                            "tests/final-toolchain.test.mjs",
                        ]
                    }
                ]
            },
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit two sealed Node groups")

    external_project = tmp_path / "external-project"
    external_root_modules = external_project / "node_modules"
    external_root_modules.mkdir(parents=True)
    external_desktop_modules = external_project / "apps/desktop/node_modules"
    dependency = external_desktop_modules / "dependency/authority.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("stable dependency\n")
    (repo / "node_modules").symlink_to(
        external_root_modules,
        target_is_directory=True,
    )
    (desktop / "node_modules").symlink_to(
        external_desktop_modules,
        target_is_directory=True,
    )

    tsx_executed = tmp_path / "tsx-executed"
    invocations = tmp_path / "tool-invocations"
    external_bin = tmp_path / "external-node-bin"
    external_bin.mkdir()
    node = external_bin / "node"
    node.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "if os.environ['MUTATING_TOOL'] == 'node':\n"
        "    Path(os.environ['TOOL_INVOCATIONS']).write_text(\n"
        "        Path(os.environ['TOOL_INVOCATIONS']).read_text() + 'node\\n'\n"
        "    )\n"
        "    Path(os.environ['MUTATED_DEPENDENCY']).write_text('mutated dependency\\n')\n"
    )
    node.chmod(0o755)
    npx = external_bin / "npx"
    npx.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "Path(os.environ['TOOL_INVOCATIONS']).write_text(\n"
        "    Path(os.environ['TOOL_INVOCATIONS']).read_text() + sys.argv[1] + '\\n'\n"
        "    if Path(os.environ['TOOL_INVOCATIONS']).exists() else sys.argv[1] + '\\n'\n"
        ")\n"
        "if sys.argv[1] == 'tsx':\n"
        "    Path(os.environ['TSX_EXECUTED']).write_text('tsx executed')\n"
        "if sys.argv[1] == os.environ['MUTATING_TOOL']:\n"
        "    mutation = os.environ['MUTATION_KIND']\n"
        "    if mutation == 'external-replace':\n"
        "        Path(os.environ['MUTATED_DEPENDENCY']).write_text('mutated dependency\\n')\n"
        "    elif mutation == 'external-add':\n"
        "        Path(os.environ['ADDED_DEPENDENCY']).write_text('added dependency\\n')\n"
        "    elif mutation == 'view-add':\n"
        "        (Path.cwd() / 'node_modules/injected-authority').write_text('injected\\n')\n"
        "    else:\n"
        "        view = Path.cwd() / 'node_modules/dependency'\n"
        "        view.unlink()\n"
        "        view.symlink_to(Path.cwd(), target_is_directory=True)\n"
        "    if os.environ['MUTATION_EXIT_CODE'] != '0':\n"
        "        print('DEPENDENCY_DRIFT_' + 'STDOUT_DIAGNOSTIC')\n"
        "        print('DEPENDENCY_DRIFT_' + 'STDERR_DIAGNOSTIC', file=sys.stderr)\n"
        "        raise SystemExit(int(os.environ['MUTATION_EXIT_CODE']))\n"
    )
    npx.chmod(0o755)
    report = tmp_path / "mutated-dependency-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["PATH"] = f"{external_bin}{os.pathsep}{env['PATH']}"
    env["TMPDIR"] = str(temp_root)
    env["MUTATED_DEPENDENCY"] = str(dependency)
    env["ADDED_DEPENDENCY"] = str(dependency.parent / "added-authority.js")
    env["TSX_EXECUTED"] = str(tsx_executed)
    env["MUTATING_TOOL"] = mutating_tool
    env["MUTATION_KIND"] = mutation_kind
    env["MUTATION_EXIT_CODE"] = str(tool_exit_code)
    env["TOOL_INVOCATIONS"] = str(invocations)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "before execution" in result.stderr
    if mutation_kind == "external-replace":
        assert (
            "apps/desktop/node_modules/dependency/authority.js changed before execution"
            in result.stderr
        )
        assert dependency.read_text() == "mutated dependency\n"
    elif mutation_kind == "external-add":
        assert Path(env["ADDED_DEPENDENCY"]).read_text() == "added dependency\n"
    elif mutation_kind == "view-add":
        assert "sealed apps/desktop/node_modules dependency view" in result.stderr
    else:
        assert "sealed apps/desktop/node_modules dependency view" in result.stderr
    assert tsx_executed.exists() is tsx_should_run
    assert invocations.read_text().splitlines() == expected_invocations
    assert not report.exists()
    if tool_exit_code == 1:
        diagnostic_header = (
            "ledger invariant nonpassing attempt: "
            "apps/desktop/src/toolchain.test.ts (attempt 1: failed)"
        )
        assert result.stderr.count(diagnostic_header) == 1
        assert result.stderr.count("DEPENDENCY_DRIFT_STDOUT_DIAGNOSTIC") == 1
        assert result.stderr.count("DEPENDENCY_DRIFT_STDERR_DIAGNOSTIC") == 1
        assert result.stderr.index(diagnostic_header) < result.stderr.index(
            "changed before execution"
        )
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="external toolchain links use POSIX paths")
@pytest.mark.parametrize(
    ("scope", "kind"),
    [
        ("root", "broken"),
        ("desktop", "escape"),
        ("root", "missing-workspace"),
        ("desktop", "missing-workspace"),
    ],
)
def test_sealed_ledger_runner_rejects_unsafe_dependency_symlinks_before_execution(
    tmp_path: Path,
    scope: str,
    kind: str,
) -> None:
    """Broken, escaping, and uncommitted project links are terminal setup errors."""
    repo = tmp_path / f"runner-unsafe-dependency-{scope}-{kind}"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    desktop = repo / "apps/desktop"
    test_path = desktop / "src/toolchain.test.ts"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("export const sealedToolchain = true\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["apps/desktop/src/toolchain.test.ts"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit sealed desktop invariant")

    external_project = tmp_path / "external-project"
    external_root_modules = external_project / "node_modules"
    external_root_modules.mkdir(parents=True)
    external_desktop_modules = external_project / "apps/desktop/node_modules"
    external_desktop_modules.mkdir(parents=True)
    selected_modules = (
        external_desktop_modules if scope == "desktop" else external_root_modules
    )
    nested = selected_modules / "package/nested"
    nested.mkdir(parents=True)
    unsafe = nested / "unsafe"
    if kind == "broken":
        unsafe.symlink_to("missing-target")
    elif kind == "escape":
        outside = tmp_path / "outside-dependency-authority"
        outside.mkdir()
        unsafe.symlink_to(outside, target_is_directory=True)
    else:
        missing = external_project / "packages/missing"
        missing.mkdir(parents=True)
        unsafe.symlink_to(missing, target_is_directory=True)
    (repo / "node_modules").symlink_to(
        external_root_modules,
        target_is_directory=True,
    )
    (desktop / "node_modules").symlink_to(
        external_desktop_modules,
        target_is_directory=True,
    )

    executed = tmp_path / "unsafe-toolchain-executed"
    external_bin = tmp_path / "external-node-bin"
    external_bin.mkdir()
    node = external_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n")
    node.chmod(0o755)
    npx = external_bin / "npx"
    npx.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['UNSAFE_TOOLCHAIN_EXECUTED']).write_text('executed')\n"
    )
    npx.chmod(0o755)
    report = tmp_path / "unsafe-node-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["PATH"] = f"{external_bin}{os.pathsep}{env['PATH']}"
    env["TMPDIR"] = str(temp_root)
    env["UNSAFE_TOOLCHAIN_EXECUTED"] = str(executed)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "node_modules" in result.stderr
    assert not executed.exists()
    assert not report.exists()
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="external toolchain links use POSIX paths")
@pytest.mark.parametrize(
    ("scope", "kind"),
    [
        ("desktop", "regular"),
        ("desktop", "inside-symlink"),
        ("root", "regular"),
        ("root", "inside-symlink"),
    ],
)
def test_sealed_ledger_runner_rejects_nonexternal_node_modules_authority(
    tmp_path: Path,
    scope: str,
    kind: str,
) -> None:
    """Every mounted Node dependency root must be an external directory symlink."""
    repo = tmp_path / f"runner-node-modules-{scope}-{kind}"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    desktop = repo / "apps/desktop"
    test_path = desktop / "src/toolchain.test.ts"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("export const localToolchain = true\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["apps/desktop/src/toolchain.test.ts"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit desktop invariant")

    external_modules = tmp_path / "external-node-modules"
    external_modules.mkdir()
    node_modules = desktop / "node_modules" if scope == "desktop" else repo / "node_modules"
    other_node_modules = repo / "node_modules" if scope == "desktop" else desktop / "node_modules"
    other_node_modules.symlink_to(external_modules, target_is_directory=True)
    if kind == "regular":
        node_modules.mkdir()
    else:
        inside = repo / f"{scope}-toolchain-inside-source"
        inside.mkdir()
        node_modules.symlink_to(inside, target_is_directory=True)
    external_bin = tmp_path / "external-node-bin"
    external_bin.mkdir()
    node = external_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n")
    node.chmod(0o755)
    npx = external_bin / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    report = tmp_path / f"{kind}-node-report.json"
    temp_root = tmp_path / "runner-temp"
    temp_root.mkdir()
    worktrees_before = _git(repo, "worktree", "list", "--porcelain")
    env = os.environ.copy()
    env["PATH"] = f"{external_bin}{os.pathsep}{env['PATH']}"
    env["TMPDIR"] = str(temp_root)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--report-path",
            str(report),
            "--platform",
            "synthetic",
            "--base-ref",
            "HEAD",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "external" in result.stderr
    assert not report.exists()
    assert _git(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert list(temp_root.iterdir()) == []


def _run_ledger_fixture(
    tmp_path: Path,
    sources: dict[str, str],
    *,
    timeout_seconds: float = 0.4,
    output_limit_bytes: int = 2048,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    repo = tmp_path / "ledger-fixture"
    repo.mkdir()
    for path, source in sources.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": sorted(sources)}]},
            sort_keys=False,
        )
    )
    output = repo / "results.json"
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--platform",
            "synthetic",
            "--timeout-seconds",
            str(timeout_seconds),
            "--output-limit-bytes",
            str(output_limit_bytes),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=10,
    )
    result.elapsed_seconds = time.monotonic() - started  # type: ignore[attr-defined]
    records = json.loads(output.read_text()) if output.exists() else []
    return result, records


def test_ledger_runner_times_out_hang_without_retry(tmp_path: Path) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_hang.py": "import time\n\ndef test_hang():\n    time.sleep(30)\n"},
    )

    assert result.returncode == 1
    assert result.elapsed_seconds < 5  # type: ignore[attr-defined]
    assert [item["result"] for item in records[0]["attempts"]] == ["timed_out"]
    assert records[0]["result"] == "failed"


def test_ledger_runner_timeout_terminates_spawned_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "ledger-fixture/escaped-child.marker"
    result, records = _run_ledger_fixture(
        tmp_path,
        {
            "tests/test_process_group.py": (
                "import subprocess\nimport sys\nimport time\n\n"
                "def test_process_group():\n"
                "    subprocess.Popen([sys.executable, '-c', "
                "\"import time; from pathlib import Path; time.sleep(1); \""
                "\"Path('escaped-child.marker').write_text('escaped')\"])\n"
                "    time.sleep(30)\n"
            )
        },
    )
    time.sleep(1.2)

    assert result.returncode == 1
    assert records[0]["attempts"][0]["result"] == "timed_out"
    assert not marker.exists()


def test_ledger_runner_caps_oversized_output_and_retries_test_failure(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {
            "tests/test_output.py": (
                "def test_output():\n"
                "    print('X' * 100_000)\n"
                "    raise AssertionError('ordinary failure')\n"
            )
        },
    )

    assert result.returncode == 1
    assert len(result.stderr.encode()) < 10_000
    assert [item["result"] for item in records[0]["attempts"]] == [
        "failed",
        "failed",
    ]
    assert all(item["output_truncated"] for item in records[0]["attempts"])
    assert result.stderr.count(
        "ledger invariant failed: tests/test_output.py"
    ) == 1
    for attempt in (1, 2):
        assert result.stderr.count(
            "ledger invariant nonpassing attempt: tests/test_output.py "
            f"(attempt {attempt}: failed)"
        ) == 1


@pytest.mark.parametrize(("signal_name", "signal_number"), [("SIGTERM", 15), ("SIGINT", 2)])
def test_ledger_runner_does_not_retry_signaled_process(
    tmp_path: Path,
    signal_name: str,
    signal_number: int,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {
            "tests/test_signal.py": (
                "import os\nimport signal\n\n"
                "def test_signal():\n"
                f"    signal.signal(signal.{signal_name}, signal.SIG_DFL)\n"
                f"    os.kill(os.getpid(), signal.{signal_name})\n"
            )
        },
    )

    assert result.returncode == 1
    assert [item["result"] for item in records[0]["attempts"]] == ["signaled"]
    assert records[0]["attempts"][0]["termination_signal"] == signal_number


def test_ledger_runner_retries_ordinary_failure_once_then_fails(tmp_path: Path) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {
            "tests/test_fail.py": (
                "def test_fail():\n    raise AssertionError('ordinary failure')\n"
            )
        },
    )

    assert result.returncode == 1
    assert [item["result"] for item in records[0]["attempts"]] == [
        "failed",
        "failed",
    ]
    assert records[0]["flaky_on_first_attempt"] is False


def test_ledger_runner_does_not_retry_pytest_infrastructure_error(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_invalid.py": "def this_is_not_valid_python(:\n"},
    )

    assert result.returncode == 1
    assert [item["result"] for item in records[0]["attempts"]] == [
        "infrastructure_error"
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor bootstrap policy")
def test_posix_supervisor_spawn_failure_is_terminal_infrastructure(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "missing-executable-repo"
    desktop = repo / "apps/desktop"
    desktop.mkdir(parents=True)
    test_path = desktop / "missing.test.ts"
    test_path.write_text("throw new Error('must never execute')\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["apps/desktop/missing.test.ts"]}]},
            sort_keys=False,
        )
    )
    report = repo / "report.json"
    empty_path = repo / "empty-path"
    empty_path.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(empty_path)

    result = subprocess.run(
        [
            sys.executable,
            str(LEDGER_RUNNER),
            "--repo",
            str(repo),
            "--manifest",
            str(manifest),
            "--output",
            str(report),
            "--platform",
            "synthetic",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    attempts = json.loads(report.read_text())[0]["attempts"]
    assert [attempt["result"] for attempt in attempts] == ["infrastructure_error"]


def test_windows_job_bootstrap_reserves_spawn_failure_as_infrastructure(
    tmp_path: Path,
) -> None:
    """The real bootstrap must not encode a setup failure as retryable exit 1."""
    missing = tmp_path / "definitely-missing-executable"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            ledger_runner._WINDOWS_JOB_BOOTSTRAP,
            str(missing),
        ],
        text=True,
        capture_output=True,
        input="1",
    )

    assert completed.returncode == 125


def test_ledger_runner_never_exceeds_two_concurrent_python_files(tmp_path: Path) -> None:
    source = (
        "from pathlib import Path\n"
        "import time\n\n"
        "def locked_update(delta):\n"
        "    lock = Path('.counter-lock')\n"
        "    while True:\n"
        "        try:\n"
        "            lock.mkdir()\n"
        "            break\n"
        "        except FileExistsError:\n"
        "            time.sleep(0.01)\n"
        "    try:\n"
        "        active_path = Path('.active-count')\n"
        "        active = int(active_path.read_text()) if active_path.exists() else 0\n"
        "        active += delta\n"
        "        active_path.write_text(str(active))\n"
        "        maximum = Path('.maximum-count')\n"
        "        seen = int(maximum.read_text()) if maximum.exists() else 0\n"
        "        maximum.write_text(str(max(seen, active)))\n"
        "        return active\n"
        "    finally:\n"
        "        lock.rmdir()\n\n"
        "def test_concurrency():\n"
        "    active = locked_update(1)\n"
        "    try:\n"
        "        assert active <= 2\n"
        "        time.sleep(0.35)\n"
        "    finally:\n"
        "        locked_update(-1)\n"
    )
    result, records = _run_ledger_fixture(
        tmp_path,
        {f"tests/test_concurrency_{index}.py": source for index in range(3)},
        timeout_seconds=3,
    )

    assert result.returncode == 0, result.stderr
    assert all(record["result"] == "passed" for record in records)
    assert (tmp_path / "ledger-fixture/.maximum-count").read_text() == "2"


def test_ledger_runner_retains_all_worker_diagnostics_when_revalidation_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A sibling drift error cannot discard a completed worker's diagnostics."""
    paths = ["tests/test_a_retry.py", "tests/test_b_drift.py"]
    first_attempts_ready = threading.Barrier(2)
    retry_passed = threading.Event()
    attempt_lock = threading.Lock()
    attempt_counts = {path: 0 for path in paths}
    worker = threading.local()

    def fake_execute_attempt(
        repo: Path,
        path: str,
        kind: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del repo, kind, kwargs
        worker.path = path
        with attempt_lock:
            attempt_counts[path] += 1
            attempt = attempt_counts[path]
        if attempt == 1:
            first_attempts_ready.wait(timeout=5)
            if path == paths[1]:
                assert retry_passed.wait(timeout=5)
            stem = "A_RETRY" if path == paths[0] else "B_DRIFT"
            return {
                "result": "failed",
                "duration_ms": 1,
                "output_truncated": False,
                "_stdout": f"{stem}_STDOUT_DIAGNOSTIC\n",
                "_stderr": f"{stem}_STDERR_DIAGNOSTIC\n",
            }
        assert path == paths[0]
        retry_passed.set()
        return {
            "result": "passed",
            "duration_ms": 1,
            "output_truncated": False,
            "_stdout": "PASSING_RETRY_OUTPUT_MUST_NOT_EMIT\n",
            "_stderr": "",
        }

    def revalidate_before_retry() -> None:
        if worker.path == paths[1]:
            raise ValueError("dependency drift")

    def execute_fixture(
        repo: Path,
        manifest_path: Path,
        *,
        platform: str,
        timeout_seconds: float,
        output_limit_bytes: int,
        source_repo: Path | None = None,
    ) -> list[dict[str, object]]:
        del manifest_path, source_repo
        return ledger_runner._run_group(
            repo,
            paths,
            "python",
            platform,
            2,
            timeout_seconds,
            output_limit_bytes,
            before_retry=revalidate_before_retry,
        )

    monkeypatch.setattr(ledger_runner, "_execute_attempt", fake_execute_attempt)
    monkeypatch.setattr(
        ledger_runner,
        "_execute_manifest_invariants",
        execute_fixture,
    )
    output = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(LEDGER_RUNNER),
            "--repo",
            str(tmp_path),
            "--output",
            str(output),
            "--platform",
            "synthetic",
        ],
    )

    with pytest.raises(SystemExit) as raised:
        ledger_runner.main()

    assert raised.value.code == 2
    assert attempt_counts == {paths[0]: 2, paths[1]: 1}
    assert not output.exists()
    stderr = capsys.readouterr().err
    headers = [
        f"ledger invariant nonpassing attempt: {path} (attempt 1: failed)"
        for path in paths
    ]
    for header in headers:
        assert stderr.count(header) == 1
    for marker in (
        "A_RETRY_STDOUT_DIAGNOSTIC",
        "A_RETRY_STDERR_DIAGNOSTIC",
        "B_DRIFT_STDOUT_DIAGNOSTIC",
        "B_DRIFT_STDERR_DIAGNOSTIC",
    ):
        assert stderr.count(marker) == 1
    assert "PASSING_RETRY_OUTPUT_MUST_NOT_EMIT" not in stderr
    drift_index = stderr.index("dependency drift")
    assert stderr.index(headers[0]) < stderr.index(headers[1]) < drift_index


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group policy")
def test_posix_cleanup_refuses_reused_group_before_term(monkeypatch) -> None:
    class _ExitedProcess:
        pid = 43123

        def wait(self):
            return 0

    signals: list[int] = []
    monkeypatch.setattr(
        ledger_runner,
        "_known_group_member_alive",
        lambda _group, _members: False,
    )
    monkeypatch.setattr(
        ledger_runner,
        "_snapshot_process_group",
        lambda _group: {_ExitedProcess.pid: 2.0},
    )
    monkeypatch.setattr(
        ledger_runner.os,
        "killpg",
        lambda _group, sent_signal: signals.append(sent_signal),
    )

    with pytest.raises(RuntimeError, match="identity"):
        ledger_runner._terminate_process_group(
            _ExitedProcess(),
            _ExitedProcess.pid,
            {_ExitedProcess.pid: 1.0},
        )

    assert signals == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group policy")
def test_posix_cleanup_rechecks_group_identity_before_kill(monkeypatch) -> None:
    class _ExitedProcess:
        pid = 43124
        returncode = 0

        def wait(self):
            return 0

    identity_checks = iter((True, False))
    signals: list[int] = []
    monkeypatch.setattr(
        ledger_runner,
        "_known_group_member_alive",
        lambda _group, _members: next(identity_checks),
    )
    monkeypatch.setattr(ledger_runner, "_TERMINATE_GRACE_SECONDS", 0)
    monkeypatch.setattr(
        ledger_runner,
        "_snapshot_process_group",
        lambda _group: {_ExitedProcess.pid: 2.0},
    )
    monkeypatch.setattr(
        ledger_runner.os,
        "killpg",
        lambda _group, sent_signal: signals.append(sent_signal),
    )

    with pytest.raises(RuntimeError, match="identity"):
        ledger_runner._terminate_process_group(
            _ExitedProcess(),
            _ExitedProcess.pid,
            {_ExitedProcess.pid: 1.0},
        )

    assert signals == [signal.SIGTERM]


def test_windows_job_containment_composes_kill_on_close_lifecycle() -> None:
    calls: list[tuple] = []

    class _FakeKernel32:
        def CreateJobObjectW(self, security, name):
            calls.append(("create", security, name))
            return 73

        def SetInformationJobObject(self, handle, info_class, info, size):
            calls.append(("configure", handle, info_class, size))
            return 1

        def AssignProcessToJobObject(self, handle, process_handle):
            calls.append(("assign", handle, process_handle))
            return 1

        def TerminateJobObject(self, handle, exit_code):
            calls.append(("terminate", handle, exit_code))
            return 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return 1

    containment = ledger_runner._WindowsJobContainment(_FakeKernel32())
    containment.assign(91)
    containment.terminate()
    containment.close()

    assert [call[0] for call in calls] == [
        "create",
        "configure",
        "assign",
        "terminate",
        "close",
    ]
    assert calls[2] == ("assign", 73, 91)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Job Objects")
def test_windows_job_reaps_grandchild_from_fast_intermediate(tmp_path: Path) -> None:
    grandchild = (
        "import time; from pathlib import Path; "
        "time.sleep(2); Path('windows-grandchild.marker').write_text('escaped')"
    )
    intermediate = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])"
    )
    source = (
        "import subprocess\nimport sys\n\n"
        "def test_fast_intermediate():\n"
        f"    subprocess.Popen([sys.executable, '-c', {intermediate!r}]).wait()\n"
    )

    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_windows_job.py": source},
        timeout_seconds=3,
    )
    time.sleep(2.2)

    assert result.returncode == 0, result.stderr
    assert records[0]["result"] == "passed"
    assert not (tmp_path / "ledger-fixture/windows-grandchild.marker").exists()


def _resistant_descendant_source(*, signal_parent: bool) -> str:
    parent_action = (
        "    time.sleep(0.2)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
        "    os.kill(os.getpid(), signal.SIGTERM)\n"
        if signal_parent
        else "    time.sleep(30)\n"
    )
    return (
        "import os\n"
        "from pathlib import Path\n"
        "import signal\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "def test_resistant_descendant():\n"
        "    child = (\"import os, signal, time; from pathlib import Path; \"\n"
        "        \"signal.signal(signal.SIGTERM, signal.SIG_IGN); \"\n"
        "        \"Path('.resistant-child.ready').write_text(str(os.getpid())); \"\n"
        "        \"time.sleep(3); Path('.resistant-child.marker').write_text('escaped'); \"\n"
        "        \"time.sleep(30)\")\n"
        "    subprocess.Popen([sys.executable, '-c', child])\n"
        "    ready = Path('.resistant-child.ready')\n"
        "    deadline = time.monotonic() + 2\n"
        "    while not ready.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    assert ready.exists()\n"
        + parent_action
    )


def _assert_resistant_descendants_were_reaped(repo: Path, *stems: str) -> None:
    ready_paths = [repo / f".{stem}.ready" for stem in stems]
    marker_paths = [repo / f".{stem}.marker" for stem in stems]
    assert all(path.is_file() for path in ready_paths)
    child_pids = [int(path.read_text()) for path in ready_paths]
    time.sleep(3.2)
    escaped = [path.exists() for path in marker_paths]
    alive = []
    for child_pid in child_pids:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            alive.append(False)
        else:
            alive.append(True)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import os, signal; os.kill({child_pid}, signal.SIGKILL)",
                ],
                capture_output=True,
                check=False,
            )
    assert escaped == [False] * len(stems)
    assert alive == [False] * len(stems)


def _assert_resistant_descendant_was_reaped(repo: Path) -> None:
    _assert_resistant_descendants_were_reaped(repo, "resistant-child")


def _leader_exit_with_resistant_descendant_source(*, passes: bool) -> str:
    assertion = "    assert True\n" if passes else "    raise AssertionError('retry me')\n"
    return (
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "def test_leader_exit():\n"
        "    counter = Path('.exit-attempt')\n"
        "    attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "    counter.write_text(str(attempt))\n"
        "    stem = f'exit-child-{attempt}'\n"
        "    child = (\"import os, signal, time; from pathlib import Path; \"\n"
        "        \"signal.signal(signal.SIGTERM, signal.SIG_IGN); \"\n"
        "        f\"Path('.{stem}.ready').write_text(str(os.getpid())); \"\n"
        "        f\"time.sleep(3); Path('.{stem}.marker').write_text('escaped'); \"\n"
        "        \"time.sleep(30)\")\n"
        "    subprocess.Popen([sys.executable, '-c', child], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "    ready = Path(f'.{stem}.ready')\n"
        "    deadline = time.monotonic() + 2\n"
        "    while not ready.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    assert ready.exists()\n"
        + assertion
    )


def _leader_signal_after_fast_intermediate_source() -> str:
    grandchild = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path('.fast-grandchild.ready').write_text(str(os.getpid())); "
        "time.sleep(3); Path('.fast-grandchild.marker').write_text('escaped'); "
        "time.sleep(30)"
    )
    intermediate = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)"
    )
    return (
        "from pathlib import Path\n"
        "import os\n"
        "import signal\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "def test_fast_intermediate():\n"
        f"    intermediate = {intermediate!r}\n"
        "    process = subprocess.Popen([sys.executable, '-c', intermediate])\n"
        "    process.wait(timeout=2)\n"
        "    ready = Path('.fast-grandchild.ready')\n"
        "    deadline = time.monotonic() + 2\n"
        "    while not ready.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    assert ready.exists()\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
        "    os.kill(os.getpid(), signal.SIGTERM)\n"
    )


def test_ledger_runner_reaps_group_after_successful_leader_exit(tmp_path: Path) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_exit_zero.py": _leader_exit_with_resistant_descendant_source(passes=True)},
        timeout_seconds=3,
    )

    assert result.returncode == 0, result.stderr
    assert [attempt["result"] for attempt in records[0]["attempts"]] == ["passed"]
    _assert_resistant_descendants_were_reaped(
        tmp_path / "ledger-fixture", "exit-child-1"
    )


def test_ledger_runner_reaps_each_group_before_retrying_failed_leader(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_exit_one.py": _leader_exit_with_resistant_descendant_source(passes=False)},
        timeout_seconds=3,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == [
        "failed",
        "failed",
    ]
    _assert_resistant_descendants_were_reaped(
        tmp_path / "ledger-fixture", "exit-child-1", "exit-child-2"
    )


def test_ledger_runner_reaps_grandchild_after_fast_intermediate_and_signal(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_fast_intermediate.py": _leader_signal_after_fast_intermediate_source()},
        timeout_seconds=3,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == [
        "signaled"
    ], result.stderr
    _assert_resistant_descendants_were_reaped(
        tmp_path / "ledger-fixture", "fast-grandchild"
    )


def test_ledger_runner_escalates_group_after_timeout_leader_exits(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_resistant_timeout.py": _resistant_descendant_source(signal_parent=False)},
        timeout_seconds=0.5,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == ["timed_out"]
    _assert_resistant_descendant_was_reaped(tmp_path / "ledger-fixture")


def test_ledger_runner_reaps_resistant_group_after_leader_signal(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_resistant_signal.py": _resistant_descendant_source(signal_parent=True)},
        timeout_seconds=3,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == ["signaled"]
    _assert_resistant_descendant_was_reaped(tmp_path / "ledger-fixture")


def _setsid_descendant_source(stem: str, outcome: str) -> str:
    actions = {
        "success": "    assert True\n",
        "failure": "    raise AssertionError('retry me')\n",
        "timeout": "    time.sleep(30)\n",
        "cancellation": "    time.sleep(30)\n",
    }
    return (
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "def test_setsid_descendant():\n"
        f"    counter = Path('.{stem}-attempt')\n"
        "    attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "    counter.write_text(str(attempt))\n"
        f"    child_stem = f'{stem}-{{attempt}}'\n"
        "    child = (\"import os, psutil, signal, time; from pathlib import Path; \"\n"
        "        \"os.setsid(); signal.signal(signal.SIGTERM, signal.SIG_IGN); \"\n"
        "        f\"Path('.{child_stem}.ready').write_text("
        "f'{{os.getpid()}}:{{psutil.Process().create_time()}}'); \"\n"
        "        f\"time.sleep(2); Path('.{child_stem}.marker').write_text('escaped'); \"\n"
        "        \"time.sleep(30)\")\n"
        "    subprocess.Popen([sys.executable, '-c', child], stdin=subprocess.DEVNULL, \n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "    ready = Path(f'.{child_stem}.ready')\n"
        "    deadline = time.monotonic() + 2\n"
        "    while not ready.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    assert ready.exists()\n"
        "    time.sleep(0.15)\n"
        + actions[outcome]
    )


def _assert_setsid_descendants_were_reaped(repo: Path, *stems: str) -> None:
    ready_paths = [repo / f".{stem}.ready" for stem in stems]
    marker_paths = [repo / f".{stem}.marker" for stem in stems]
    assert all(path.is_file() for path in ready_paths)
    child_identities = [
        (int(pid), float(created_at))
        for pid, created_at in (path.read_text().split(":", 1) for path in ready_paths)
    ]
    time.sleep(2.2)
    escaped = [path.exists() for path in marker_paths]
    alive = []
    for child_pid, child_created_at in child_identities:
        try:
            candidate = ledger_runner.psutil.Process(child_pid)
            original_identity_alive = (
                candidate.create_time() == child_created_at
                and candidate.status() != ledger_runner.psutil.STATUS_ZOMBIE
            )
        except (ledger_runner.psutil.NoSuchProcess, ledger_runner.psutil.ZombieProcess):
            original_identity_alive = False
        if not original_identity_alive:
            alive.append(False)
        else:
            alive.append(True)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, psutil, signal, sys; "
                        "candidate = psutil.Process(int(sys.argv[1])); "
                        "candidate.create_time() == float(sys.argv[2]) and "
                        "os.kill(candidate.pid, signal.SIGKILL)"
                    ),
                    str(child_pid),
                    str(child_created_at),
                ],
                capture_output=True,
                check=False,
            )
    assert escaped == [False] * len(stems)
    assert alive == [False] * len(stems)


@pytest.mark.skipif(os.name != "posix", reason="POSIX setsid containment policy")
def test_ledger_runner_reaps_setsid_descendant_after_success(tmp_path: Path) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_setsid_success.py": _setsid_descendant_source("setsid-success", "success")},
        timeout_seconds=3,
    )

    assert result.returncode == 0, result.stderr
    assert [attempt["result"] for attempt in records[0]["attempts"]] == ["passed"]
    _assert_setsid_descendants_were_reaped(
        tmp_path / "ledger-fixture", "setsid-success-1"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX setsid containment policy")
def test_ledger_runner_reaps_setsid_descendants_after_ordinary_failures(
    tmp_path: Path,
) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_setsid_failure.py": _setsid_descendant_source("setsid-failure", "failure")},
        timeout_seconds=3,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == [
        "failed",
        "failed",
    ]
    _assert_setsid_descendants_were_reaped(
        tmp_path / "ledger-fixture", "setsid-failure-1", "setsid-failure-2"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX setsid containment policy")
def test_ledger_runner_reaps_setsid_descendant_after_timeout(tmp_path: Path) -> None:
    result, records = _run_ledger_fixture(
        tmp_path,
        {"tests/test_setsid_timeout.py": _setsid_descendant_source("setsid-timeout", "timeout")},
        timeout_seconds=0.5,
    )

    assert result.returncode == 1
    assert [attempt["result"] for attempt in records[0]["attempts"]] == ["timed_out"]
    _assert_setsid_descendants_were_reaped(
        tmp_path / "ledger-fixture", "setsid-timeout-1"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX setsid containment policy")
@pytest.mark.live_system_guard_bypass
def test_ledger_runner_reaps_setsid_descendant_after_cancellation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cancelled-ledger-fixture"
    test_path = repo / "tests/test_setsid_cancellation.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(_setsid_descendant_source("setsid-cancellation", "cancellation"))
    cancel_event = threading.Event()
    ready = repo / ".setsid-cancellation-1.ready"

    def cancel_when_ready() -> None:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        cancel_event.set()

    canceller = threading.Thread(target=cancel_when_ready)
    canceller.start()
    with pytest.raises(ledger_runner.CancelledError):
        ledger_runner._execute_attempt(
            repo,
            "tests/test_setsid_cancellation.py",
            "python",
            timeout_seconds=5,
            output_limit_bytes=2048,
            cancel_event=cancel_event,
        )
    canceller.join(timeout=3)
    assert not canceller.is_alive()
    _assert_setsid_descendants_were_reaped(repo, "setsid-cancellation-1")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process identity policy")
def test_posix_cleanup_signals_verified_escaped_identity_but_not_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_pid = 43125
    escaped_pid = 43126
    reused_pid = 43127
    created = {leader_pid: 1.0, escaped_pid: 2.0, reused_pid: 99.0}

    class ExitedProcess:
        pid = leader_pid
        returncode = 0

        def wait(self):
            return 0

    class Candidate:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return created[self.pid]

        def status(self) -> str:
            return "running"

        def children(self, recursive: bool = False) -> list:
            return []

    group_signals: list[int] = []
    identity_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(ledger_runner.psutil, "Process", Candidate)
    monkeypatch.setattr(
        ledger_runner,
        "_snapshot_process_group",
        lambda _group: {leader_pid: 1.0},
    )
    monkeypatch.setattr(
        ledger_runner.os,
        "getpgid",
        lambda pid: leader_pid if pid == leader_pid else pid,
    )
    monkeypatch.setattr(
        ledger_runner.os,
        "killpg",
        lambda _group, sent_signal: group_signals.append(sent_signal),
    )
    monkeypatch.setattr(
        ledger_runner.os,
        "kill",
        lambda pid, sent_signal: identity_signals.append((pid, sent_signal)),
    )
    monkeypatch.setattr(ledger_runner, "_TERMINATE_GRACE_SECONDS", 0)

    ledger_runner._terminate_process_group(
        ExitedProcess(),
        leader_pid,
        {leader_pid: 1.0, escaped_pid: 2.0, reused_pid: 3.0},
    )

    assert group_signals == [signal.SIGTERM, signal.SIGKILL]
    assert identity_signals == [
        (escaped_pid, signal.SIGTERM),
        (escaped_pid, signal.SIGKILL),
    ]


def test_rehearsal_records_reconciled_conflict_files(tmp_path: Path) -> None:
    """Structured evidence must retain the generated files reconciled by Git."""
    repo = _synthetic_rehearsal_repo(tmp_path, "none")
    manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["files"].extend(
        ["apps/desktop/package.json", "package-lock.json"]
    )
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    _git(repo, "add", str(manifest.relative_to(repo)))
    _git(repo, "commit", "-m", "track generated reconciliation files")
    report = tmp_path / "report-reconciled-conflicts"

    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    evidence = json.loads((report / "merge-evidence.json").read_text())
    assert evidence["entries"][0]["conflict_files"] == [
        "apps/desktop/package.json",
        "package-lock.json",
    ]


@pytest.mark.parametrize(
    ("overlap", "any_owned_file"),
    [
        ("owned-symbol", False),
        ("same-file", True),
    ],
)
def test_rehearsal_requires_explicit_decision_for_decision_required_overlap(
    tmp_path: Path,
    overlap: str,
    any_owned_file: bool,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, overlap)
    if any_owned_file:
        manifest = repo / "docs/upstream-customizations/workflow-orchestration.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["upstream_changes"][0]["overlap_policy"] = "any_owned_file"
        manifest.write_text(yaml.safe_dump(data, sort_keys=False))
        _git(repo, "add", str(manifest.relative_to(repo)))
        _git(repo, "commit", "-m", "require every owned-file decision")
    refs_before = _git(repo, "show-ref", "--heads")

    result = _run_synthetic(repo, tmp_path / f"report-{overlap}")

    assert result.returncode == 4
    assert "explicit preserve/adapt/remove-as-upstream-equivalent" in result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before


def test_rehearsal_does_not_require_decision_for_unrelated_matching_symbol(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "upstream-equivalent")
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / "report-upstream-equivalent"

    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    evidence = json.loads((report / "merge-evidence.json").read_text())
    assert evidence["entries"][0]["overlap_class"] == "none"
    assert evidence["entries"][0]["decision_required"] is False
    assert evidence["entries"][0]["decision"] == "not-required"


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
