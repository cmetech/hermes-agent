from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import shutil
import subprocess
import sys
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
        ("possible_upstream_equivalent", "any_owned_file", True, "adapt"),
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
    """A fail-then-pass executable invariant remains visible but non-terminal."""
    repo = tmp_path / "runner-repo"
    repo.mkdir()
    _git(repo, "init")
    test_path = repo / "tests/test_flaky.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "from pathlib import Path\n\n"
        "def test_passes_on_second_file_attempt():\n"
        "    marker = Path('first-attempt.marker')\n"
        "    if not marker.exists():\n"
        "        marker.write_text('failed once\\n')\n"
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


def test_ledger_runner_accepts_report_path_and_exact_base_ref(tmp_path: Path) -> None:
    repo = tmp_path / "runner-cli-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    test_path = repo / "tests/test_pass.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_pass():\n    assert True\n")
    manifest = repo / "ledger.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"upstream_changes": [{"tests": ["tests/test_pass.py"]}]},
            sort_keys=False,
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "runner fixture")
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

    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text())[0]["result"] == "passed"


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
        ("upstream-equivalent", False),
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
