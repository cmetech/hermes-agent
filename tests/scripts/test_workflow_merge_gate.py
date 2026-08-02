from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[2]
GATE = ROOT / "scripts/test_workflow_merge_gate.sh"
CI = ROOT / ".github/workflows/ci.yml"
MANIFEST = ROOT / "docs/upstream-customizations/workflow-orchestration.yaml"
CUSTOMIZATION_CHECKER = ROOT / "scripts/check_upstream_customizations.py"
PHASE_1_LANGUAGE_BACKEND_SUITES = (
    "tests/plugins/workflow/test_language.py",
    "tests/plugins/workflow/test_language_snapshot.py",
    "tests/plugins/workflow/test_language_schema.py",
    "tests/plugins/workflow/test_workflow_language_desktop_e2e.py",
)
PHASE_1_LANGUAGE_DESKTOP_SUITES = (
    "src/app/workflows/index.test.tsx",
    "src/app/workflows/view-workflow-dialog.test.tsx",
)
PHASE_1_LANGUAGE_CUSTOMIZATION_IDS = {
    "workflow-language-contracts",
    "workflow-language-profile-normalization",
    "workflow-language-admission-pinning",
    "workflow-language-schema-cli",
    "workflow-language-desktop-status",
    "workflow-language-authoring-reference",
}
WORKFLOW_GATE_OPTOUTS = {
    path: "covered by the standard Python suite; outside the focused release gates"
    for path in (
        "tests/plugins/workflow/test_ai_e2e.py",
        "tests/plugins/workflow/test_ai_executor.py",
        "tests/plugins/workflow/test_api_runtime.py",
        "tests/plugins/workflow/test_approval.py",
        "tests/plugins/workflow/test_approval_races.py",
        "tests/plugins/workflow/test_bash_e2e.py",
        "tests/plugins/workflow/test_cancel_node.py",
        "tests/plugins/workflow/test_catalog_cli.py",
        "tests/plugins/workflow/test_cli.py",
        "tests/plugins/workflow/test_compat_matrix.py",
        "tests/plugins/workflow/test_crash_recovery.py",
        "tests/plugins/workflow/test_deadlines.py",
        "tests/plugins/workflow/test_discovery.py",
        "tests/plugins/workflow/test_doctor.py",
        "tests/plugins/workflow/test_loop_executor.py",
        "tests/plugins/workflow/test_node_agents.py",
        "tests/plugins/workflow/test_node_hooks.py",
        "tests/plugins/workflow/test_node_skills.py",
        "tests/plugins/workflow/test_node_tool_policy.py",
        "tests/plugins/workflow/test_operator_e2e.py",
        "tests/plugins/workflow/test_operator_scope.py",
        "tests/plugins/workflow/test_parallel_scheduler.py",
        "tests/plugins/workflow/test_performance_bounds.py",
        "tests/plugins/workflow/test_persisted_sessions.py",
        "tests/plugins/workflow/test_provenance.py",
        "tests/plugins/workflow/test_provider_compat.py",
        "tests/plugins/workflow/test_provider_failures.py",
        "tests/plugins/workflow/test_resources.py",
        "tests/plugins/workflow/test_retry.py",
        "tests/plugins/workflow/test_run_queries.py",
        "tests/plugins/workflow/test_scheduler.py",
        "tests/plugins/workflow/test_schema.py",
        "tests/plugins/workflow/test_script_executor.py",
        "tests/plugins/workflow/test_showcase_offline_e2e.py",
        "tests/plugins/workflow/test_store.py",
        "tests/plugins/workflow/test_topology.py",
    )
}

PARSER_VERSIONS = {
    "typescript": "6.0.3",
    "unified": "11.0.5",
    "remark-parse": "11.0.0",
    "micromark": "4.0.2",
}


def _parser_package_lock(name: str = "gate-fixture") -> dict[str, object]:
    packages: dict[str, object] = {"": {"name": name}}
    packages.update(
        {
            f"node_modules/{package}": {"version": version}
            for package, version in PARSER_VERSIONS.items()
        }
    )
    return {
        "name": name,
        "lockfileVersion": 3,
        "packages": packages,
    }


def _write_parser_dependencies(root: Path) -> None:
    for package, version in PARSER_VERSIONS.items():
        package_dir = root / "node_modules" / package
        package_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"name": package, "version": version}
        if package == "typescript":
            manifest["main"] = "index.js"
            entrypoint = "module.exports = {};\n"
        else:
            manifest.update({"type": "module", "exports": "./index.js"})
            entrypoint = "export {};\n"
        (package_dir / "package.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (package_dir / "index.js").write_text(entrypoint, encoding="utf-8")


def _dependency_checker_source() -> str:
    return (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        f"expected = {PARSER_VERSIONS!r}\n"
        "for name, version in expected.items():\n"
        "    manifest = Path('node_modules') / name / 'package.json'\n"
        "    assert json.loads(manifest.read_text(encoding='utf-8'))['version'] == version\n"
        "marker = os.environ.get('CHECKER_MARKER')\n"
        "if marker:\n"
        "    Path(marker).write_text('checked\\n', encoding='utf-8')\n"
        "offline = os.environ.get('HERMES_OFFLINE') == '1'\n"
        "credentials_empty = all(not os.environ.get(name) for name in "
        "('OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'NOUS_API_KEY'))\n"
        "raise SystemExit(9 if Path('FAIL_CHECK').exists() else "
        "(0 if offline and credentials_empty else 8))\n"
    )

def test_live_customization_ledger_has_one_rehearsable_upstream_baseline() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CUSTOMIZATION_CHECKER),
            "--manifest",
            str(MANIFEST),
            "--print-verified-upstream",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"[0-9a-f]{40}\n", result.stdout)


def _exercise_base_gate(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """Run the real gate while executable fixtures record its child commands."""
    repo = tmp_path / "gate-contract-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs/upstream-customizations").mkdir(parents=True)
    (repo / "apps/desktop/node_modules").mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "base"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Gate Contract"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "gate@localhost"], cwd=repo, check=True)
    (repo / "scripts/check_upstream_customizations.py").write_text(
        _dependency_checker_source()
    )
    (repo / "scripts/test_workflow_upstream_merge.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "scripts/run_tests.sh").write_text(
        "#!/usr/bin/env bash\n"
        "{ printf 'run_tests'; printf '\\t%s' \"$@\"; printf '\\n'; } >>\"$CAPTURE_LOG\"\n"
    )
    (repo / "scripts/run_tests.sh").chmod(0o755)
    (repo / "docs/upstream-customizations/workflow-orchestration.yaml").write_text(
        "schema_version: 1\n"
    )
    (repo / "docs/upstream-customizations/merge-evidence.schema.json").write_text("{}\n")
    fixture_bin = tmp_path / "fixture-bin"
    fixture_bin.mkdir()
    (fixture_bin / "npx").write_text(
        "#!/usr/bin/env bash\n"
        "{ printf 'npx'; printf '\\t%s' \"$@\"; printf '\\n'; } >>\"$CAPTURE_LOG\"\n"
    )
    (fixture_bin / "npx").chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    _write_parser_dependencies(repo)
    capture = tmp_path / "commands.tsv"
    env = os.environ.copy()
    env.pop("WORKFLOW_MERGE_GATE_FAST", None)
    env["CAPTURE_LOG"] = str(capture)
    env["PATH"] = f"{fixture_bin}{os.pathsep}{env['PATH']}"
    env["PYTHON_BIN"] = sys.executable

    result = subprocess.run(
        [GATE, "--repo", repo, "--phase", "base"],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )
    commands = [line.split("\t") for line in capture.read_text().splitlines()]
    return result, commands


def test_base_gate_executes_the_release_contract_through_fixture_commands(
    tmp_path: Path,
) -> None:
    result, commands = _exercise_base_gate(tmp_path)

    assert result.returncode == 0, result.stderr
    run_tests = [command[1:] for command in commands if command[0] == "run_tests"]
    assert len(run_tests) == 2
    selected_python = [path for invocation in run_tests for path in invocation if path.endswith(".py")]
    selected_desktop = [
        path
        for command in commands
        if command[:3] == ["npx", "vitest", "run"]
        for path in command[3:]
    ]
    assert len(selected_python) == len(set(selected_python))
    assert len(selected_desktop) == len(set(selected_desktop))
    for path in (
        "tests/gateway/test_plugin_background_services.py",
        "tests/gateway/test_plugin_delivery.py",
        "tests/hermes_cli/test_plugin_provider_hot_reload.py",
        "tests/scripts/test_workflow_merge_gate.py",
        "tests/plugins/workflow/test_catalog_api.py",
        "tests/plugins/workflow/test_workflow_detail_api.py",
        "tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py",
        "tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py",
        "tests/plugins/workflow/test_node_mcp.py",
        "tests/hermes_cli/test_execution_runtime_capabilities.py",
        "tests/plugins/workflow/test_runner_binding.py",
        "tests/plugins/workflow/test_typed_publication.py",
        "tests/plugins/workflow/test_typed_publication_recovery.py",
        "tests/plugins/workflow/test_structured_output_language.py",
        "tests/plugins/workflow/test_laptop_diagnostic_middleware_e2e.py",
        "tests/plugins/workflow/test_ai_extensions_middleware_e2e.py",
        "tests/plugins/workflow/test_showcase_ai_e2e.py",
        "tests/plugins/workflow/test_showcase_evidence.py",
        "tests/plugins/workflow/test_showcase_schedule_e2e.py",
        "tests/plugins/workflow/test_scheduled_runs.py",
        "tests/plugins/workflow/test_schedule_revalidation.py",
        "tests/plugins/workflow/test_phase3_language.py",
        "tests/plugins/workflow/test_phase3_execution_semantics.py",
        "tests/plugins/workflow/test_phase3_code_catalog.py",
        "tests/plugins/workflow/test_strict_output_references.py",
        "tests/plugins/workflow/test_phase3_conditions.py",
        "tests/plugins/workflow/test_phase3_resolution_waits.py",
        *PHASE_1_LANGUAGE_BACKEND_SUITES,
    ):
        assert selected_python.count(path) == 1
        assert (ROOT / path).is_file()
    for path in (
        "src/app/workflows/catalog-run-policy.test.ts",
        "src/app/workflows/index.test.tsx",
        "src/app/workflows/review-run-dialog.test.tsx",
        "src/app/workflows/view-workflow-dialog.test.tsx",
        "src/components/assistant-ui/embeds/workflow-topology.test.tsx",
    ):
        assert selected_desktop.count(path) == 1
    workflow_inventory = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/plugins/workflow").glob("test_*.py")
    }
    selected_workflow = set(selected_python) | set(_portability_files())
    opted_out = set(WORKFLOW_GATE_OPTOUTS)
    assert all(reason.strip() for reason in WORKFLOW_GATE_OPTOUTS.values())
    assert not any("*" in path for path in opted_out)
    assert not (opted_out - workflow_inventory)
    assert not (opted_out & selected_workflow)
    assert not (workflow_inventory - selected_workflow - opted_out)


def _portability_matrix() -> dict:
    return yaml.safe_load(CI.read_text())["jobs"]["workflow-portability"]["strategy"][
        "matrix"
    ]


def _portability_files() -> list[str]:
    """Every test file the native portability job runs, across all slices.

    The job is sliced (os x slice) because it outgrew a single 30-minute
    runner, so the file list now lives in the matrix rather than inline in the
    run step. The contract these tests protect is unchanged: each pinned path
    runs exactly once, on all three operating systems.
    """
    files: list[str] = []
    for entry in _portability_matrix()["slice"]:
        files.extend(entry["files"].split())
    return files


def test_portability_slices_cover_every_pinned_file_exactly_once() -> None:
    files = _portability_files()

    # Two slices running the same file is wasted Windows minutes on the job
    # that already outgrew its budget once.
    assert len(files) == len(set(files)), "a test file is pinned in two slices"
    for path in files:
        assert CI.read_text().count(path) == 1
    # The slice lists are hand-maintained, so a typo would otherwise only
    # surface as a confusing "file or directory not found" inside CI.
    missing = [path for path in files if not (ROOT / path).is_file()]
    assert not missing, f"portability slices name files that do not exist: {missing}"


def test_portability_job_uses_uv_with_the_cross_platform_isolated_runner() -> None:
    job = yaml.safe_load(CI.read_text())["jobs"]["workflow-portability"]
    step = next(
        item
        for item in job["steps"]
        if item.get("name") == "Run portable workflow and installed-showcase gates"
    )

    assert step["shell"] == "bash"
    assert step["run"].strip() == (
        "uv run --no-sync bash scripts/run_tests.sh "
        "${{ matrix.slice.files }} -q"
    )
    assert "python -m pytest" not in step["run"]


def test_phase_1_language_contracts_are_pinned_in_native_matrix() -> None:
    portable_files = _portability_files()

    for path in PHASE_1_LANGUAGE_BACKEND_SUITES:
        assert portable_files.count(path) == 1


def test_phase_1_language_customizations_and_regression_gate_are_tracked() -> None:
    customization_ids = {
        entry["id"] for entry in yaml.safe_load(MANIFEST.read_text())["upstream_changes"]
    }

    assert PHASE_1_LANGUAGE_CUSTOMIZATION_IDS <= customization_ids
    assert "workflow-language-regression-gates" in customization_ids


def test_native_workflow_matrix_covers_every_release_gate() -> None:
    assert _portability_matrix()["os"] == [
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    ]
    portable_files = _portability_files()
    for required_test in (
        "tests/plugins/workflow/test_desktop_api.py",
        "tests/plugins/workflow/test_evidence_api.py",
        "tests/plugins/workflow/test_idempotency_multiprocess.py",
        "tests/plugins/workflow/test_coordinator.py",
        "tests/plugins/workflow/test_coordinator_multiprocess.py",
        "tests/plugins/workflow/test_schema_migrations.py",
        "tests/plugins/workflow/test_notification_delivery.py",
        "tests/plugins/workflow/test_notifications.py",
        "tests/plugins/workflow/test_shutdown_recovery.py",
        "tests/plugins/workflow/test_retention.py",
    ):
        assert portable_files.count(required_test) == 1


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


def test_base_gate_is_offline_and_reports_exact_tested_sha(tmp_path: Path) -> None:
    repo, _base = _brand_repo(tmp_path)
    env = os.environ.copy()
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    result = subprocess.run(
        [GATE, "--repo", repo], cwd=repo, text=True, capture_output=True, env=env
    )
    assert result.returncode == 0, result.stderr
    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    assert f"TESTED_BASE_SHA={expected}" in result.stdout


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
    (repo / "apps/desktop").mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "base"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Gate Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "gate@localhost"], cwd=repo, check=True)
    (repo / "scripts/check_upstream_customizations.py").write_text(
        _dependency_checker_source()
    )
    (repo / "scripts/test_workflow_upstream_merge.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "docs/upstream-customizations/workflow-orchestration.yaml").write_text("schema_version: 1\n")
    (repo / "docs/upstream-customizations/merge-evidence.schema.json").write_text("{}\n")
    (repo / "brands/otto.json").write_text('{"slug":"otto"}\n')
    (repo / "plugins/workflow/runtime.py").write_text("VALUE = 'base'\n")
    (repo / "package.json").write_text('{"name":"gate-fixture"}\n')
    (repo / "package-lock.json").write_text(
        f"{json.dumps(_parser_package_lock())}\n"
    )
    (repo / "apps/desktop/package.json").write_text(
        '{"name":"gate-desktop-fixture"}\n'
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "checkout", "-b", "otto"], cwd=repo, check=True, capture_output=True)
    (repo / "brand.txt").write_text("otto\n")
    subprocess.run(["git", "add", "brand.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "brand"], cwd=repo, check=True, capture_output=True)
    _write_parser_dependencies(repo)
    return repo, base


def _linked_brand_checkout(tmp_path: Path) -> tuple[Path, Path, str]:
    shared_root, base = _brand_repo(tmp_path)
    linked = tmp_path / "linked-brand"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), "otto"],
        cwd=shared_root,
        check=True,
        capture_output=True,
    )
    return shared_root, linked, base


def _sibling_invocation_checkouts(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str]:
    shared_root, base = _brand_repo(tmp_path)
    invocation = tmp_path / "invocation-worktree"
    detached = tmp_path / "detached-rehearsal"
    for worktree in (invocation, detached):
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), base],
            cwd=shared_root,
            check=True,
            capture_output=True,
        )
    shutil.rmtree(shared_root / "node_modules")
    return shared_root, invocation, detached, base


def _brand_parser_checkouts(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str]:
    shared_root, invocation, brand, base = _sibling_invocation_checkouts(tmp_path)
    _write_parser_dependencies(invocation)
    (brand / "package.json").write_text('{"name":"otto-gate-fixture"}\n')
    (brand / "package-lock.json").write_text(
        f"{json.dumps(_parser_package_lock('otto-gate-fixture'))}\n"
    )
    subprocess.run(
        ["git", "add", "package.json", "package-lock.json"],
        cwd=brand,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "generate brand metadata"],
        cwd=brand,
        check=True,
        capture_output=True,
    )
    return shared_root, invocation, brand, base


def _run_gate_with_marker(
    repo: Path,
    marker: Path,
    *arguments: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CHECKER_MARKER"] = str(marker)
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    env.update(extra_env or {})
    return subprocess.run(
        [GATE, "--repo", repo, *arguments],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )


def test_base_gate_provisions_root_parser_dependencies_before_checker(
    tmp_path: Path,
) -> None:
    shared_root, linked, _base = _linked_brand_checkout(tmp_path)
    subprocess.run(
        ["git", "checkout", "--detach", "base"],
        cwd=linked,
        check=True,
        capture_output=True,
    )
    marker = tmp_path / "base-checker.marker"

    result = _run_gate_with_marker(linked, marker, "--phase", "base")

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "checked\n"
    assert (linked / "node_modules").resolve() == (shared_root / "node_modules").resolve()


def test_brand_gate_provisions_root_parser_dependencies_before_checker(
    tmp_path: Path,
) -> None:
    shared_root, linked, base = _linked_brand_checkout(tmp_path)
    marker = tmp_path / "brand-checker.marker"

    result = _run_gate_with_marker(
        linked,
        marker,
        "--phase",
        "brand",
        "--brand",
        "otto",
        "--tested-base-sha",
        base,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "checked\n"
    assert (linked / "node_modules").resolve() == (shared_root / "node_modules").resolve()


def test_gate_provisions_parser_dependencies_from_sibling_invocation_worktree(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    marker = tmp_path / "sibling-invocation-checker.marker"

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CHECKER_MARKER": str(marker),
            "WORKFLOW_MERGE_GATE_FAST": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "checked\n"
    assert (detached / "node_modules").resolve() == (
        invocation / "node_modules"
    ).resolve()


def _run_brand_parser_gate(
    brand: Path,
    invocation: Path,
    base: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            GATE,
            "--repo",
            brand,
            "--phase",
            "brand",
            "--brand",
            "otto",
            "--tested-base-sha",
            base,
        ],
        cwd=invocation,
        text=True,
        capture_output=True,
        env={**os.environ, "WORKFLOW_MERGE_GATE_FAST": "1"},
    )


def test_brand_gate_reuses_parser_dependencies_across_brand_metadata(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, brand, base = _brand_parser_checkouts(tmp_path)

    result = _run_brand_parser_gate(brand, invocation, base)

    assert result.returncode == 0, result.stderr
    assert (brand / "node_modules").is_symlink()
    assert (brand / "node_modules").resolve() == (invocation / "node_modules").resolve()


@pytest.mark.parametrize(
    "malformation",
    [
        "version-mismatch",
        "missing-entry",
        "duplicate-entry",
        "malformed-entry",
        "dirty-brand-lock",
        "dirty-source-lock",
    ],
)
def test_brand_gate_rejects_unsealed_or_mismatched_parser_lock_entries(
    tmp_path: Path,
    malformation: str,
) -> None:
    _shared_root, invocation, brand, base = _brand_parser_checkouts(tmp_path)
    target = brand / "package-lock.json"
    payload = _parser_package_lock("otto-gate-fixture")
    packages = payload["packages"]
    assert isinstance(packages, dict)
    if malformation == "version-mismatch":
        packages["node_modules/typescript"] = {"version": "0.0.0"}
    elif malformation == "missing-entry":
        packages.pop("node_modules/typescript")
    elif malformation == "malformed-entry":
        packages["node_modules/typescript"] = {"version": 603}
    elif malformation == "duplicate-entry":
        entry = '"node_modules/typescript":{"version":"6.0.3"}'
        serialized = json.dumps(payload, separators=(",", ":"))
        serialized = serialized.replace(entry, f"{entry},{entry}", 1)
        target.write_text(f"{serialized}\n")
    elif malformation == "dirty-brand-lock":
        payload["name"] = "dirty-brand-lock"
    elif malformation == "dirty-source-lock":
        target = invocation / "package-lock.json"
        target.write_text(f"{json.dumps(_parser_package_lock('dirty-source'))}\n")
    if malformation not in {"duplicate-entry", "dirty-source-lock"}:
        target.write_text(f"{json.dumps(payload)}\n")
    if not malformation.startswith("dirty-"):
        subprocess.run(
            ["git", "add", "package-lock.json"],
            cwd=brand,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"malform lock: {malformation}"],
            cwd=brand,
            check=True,
            capture_output=True,
        )

    result = _run_brand_parser_gate(brand, invocation, base)

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr


def test_brand_gate_rejects_parser_dependencies_from_different_repository(
    tmp_path: Path,
) -> None:
    _shared_root, _invocation, brand, base = _brand_parser_checkouts(
        tmp_path / "target"
    )
    unrelated, _unrelated_base = _brand_repo(tmp_path / "unrelated")

    result = _run_brand_parser_gate(brand, unrelated, base)

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr


def test_brand_gate_rejects_escaping_parser_dependency_symlink(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, brand, base = _brand_parser_checkouts(tmp_path)
    outside = tmp_path / "outside-brand-parser-dependencies"
    _write_parser_dependencies(outside)
    shutil.rmtree(invocation / "node_modules")
    (invocation / "node_modules").symlink_to(
        outside / "node_modules", target_is_directory=True
    )

    result = _run_brand_parser_gate(brand, invocation, base)

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr


def test_gate_rejects_invocation_dependencies_from_different_repository(
    tmp_path: Path,
) -> None:
    _shared_root, _invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    unrelated = tmp_path / "unrelated-repository"
    unrelated.mkdir()
    subprocess.run(["git", "init"], cwd=unrelated, check=True, capture_output=True)
    _write_parser_dependencies(unrelated)
    marker = tmp_path / "unrelated-invocation-checker.marker"

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=unrelated,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CHECKER_MARKER": str(marker),
            "WORKFLOW_MERGE_GATE_FAST": "1",
        },
    )

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_escaping_sibling_invocation_dependency_view(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    outside = tmp_path / "outside-sibling-dependencies"
    _write_parser_dependencies(outside)
    (invocation / "node_modules").symlink_to(
        outside / "node_modules", target_is_directory=True
    )
    marker = tmp_path / "escaping-sibling-invocation-checker.marker"

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "CHECKER_MARKER": str(marker),
            "WORKFLOW_MERGE_GATE_FAST": "1",
        },
    )

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def _install_full_gate_fixtures(repo: Path, tmp_path: Path) -> dict[str, str]:
    run_tests = repo / "scripts/run_tests.sh"
    run_tests.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    run_tests.chmod(0o755)
    (repo / "apps/desktop").mkdir(parents=True, exist_ok=True)
    fixture_bin = tmp_path / f"{repo.name}-fixture-bin"
    fixture_bin.mkdir()
    npx = fixture_bin / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ -n \"${GATE_DESKTOP_OBSERVATION:-}\" ]]; then\n"
        "  [[ -d node_modules && ! -L node_modules ]]\n"
        "  [[ -L node_modules/fixture-package ]]\n"
        "  [[ -d node_modules/.vite && ! -L node_modules/.vite ]]\n"
        "  [[ ! -e node_modules/.vite/source-cache ]]\n"
        "  printf '%s\\n' \"$1\" >>\"$GATE_DESKTOP_OBSERVATION\"\n"
        "fi\n"
        "case \"${GATE_NPX_MODE:-pass}:$1\" in\n"
        "  test-fail:vitest) exit 41 ;;\n"
        "  typecheck-fail:tsc) exit 42 ;;\n"
        "  signal:vitest) kill -TERM \"$PPID\"; exit 143 ;;\n"
        "  handoff-source-missing:tsc) "
        "mv \"$GATE_DESKTOP_SOURCE\" \"$GATE_DESKTOP_SOURCE.moved\" ;;\n"
        "  handoff-target-replaced:tsc) rm -rf node_modules; "
        "ln -s \"$GATE_REPLACEMENT_SOURCE\" node_modules ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    npx.chmod(0o755)
    env = os.environ.copy()
    env.pop("WORKFLOW_MERGE_GATE_FAST", None)
    env["PATH"] = f"{fixture_bin}{os.pathsep}{env['PATH']}"
    env["PYTHON_BIN"] = sys.executable
    return env


def test_gate_provisions_desktop_dependencies_from_sibling_invocation_worktree(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    desktop_modules = invocation / "apps/desktop/node_modules"
    (desktop_modules / "fixture-package").mkdir(parents=True)
    (desktop_modules / "fixture-package/package.json").write_text(
        '{"name":"fixture-package"}\n'
    )
    (desktop_modules / ".vite").mkdir()
    (desktop_modules / ".vite/source-cache").write_text("source-only\n")
    env = _install_full_gate_fixtures(detached, tmp_path)
    observation = tmp_path / "desktop-gate-observation.log"
    env["GATE_DESKTOP_OBSERVATION"] = str(observation)

    env["GATE_NPX_MODE"] = "test-fail"
    failed = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )
    assert failed.returncode != 0
    assert not (detached / "apps/desktop/node_modules").exists()

    env.pop("GATE_NPX_MODE")
    retried = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )
    assert retried.returncode == 0, retried.stderr
    detached_modules = detached / "apps/desktop/node_modules"
    assert detached_modules.is_symlink()
    assert detached_modules.resolve() == desktop_modules.resolve()

    assert observation.read_text(encoding="utf-8").splitlines() == [
        "vitest",
        "vitest",
        "tsc",
    ]
    assert (desktop_modules / "fixture-package/package.json").is_file()
    assert (desktop_modules / ".vite/source-cache").read_text() == "source-only\n"


@pytest.mark.parametrize("failure_mode", ["test-fail", "typecheck-fail", "signal"])
def test_gate_cleans_provisioned_desktop_view_on_early_exit(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    desktop_modules = invocation / "apps/desktop/node_modules"
    (desktop_modules / "fixture-package").mkdir(parents=True)
    (desktop_modules / "fixture-package/package.json").write_text(
        '{"name":"fixture-package"}\n'
    )
    (desktop_modules / ".vite").mkdir()
    (desktop_modules / ".vite/source-cache").write_text("source-only\n")
    env = _install_full_gate_fixtures(detached, tmp_path)
    env["GATE_DESKTOP_OBSERVATION"] = str(tmp_path / "observation.log")
    env["GATE_NPX_MODE"] = failure_mode

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert not (detached / "apps/desktop/node_modules").exists()
    assert (desktop_modules / "fixture-package/package.json").is_file()
    assert (desktop_modules / ".vite/source-cache").read_text() == "source-only\n"


def test_gate_fails_closed_when_successful_desktop_handoff_source_disappears(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    desktop_modules = invocation / "apps/desktop/node_modules"
    (desktop_modules / "fixture-package").mkdir(parents=True)
    (desktop_modules / "fixture-package/package.json").write_text(
        '{"name":"fixture-package"}\n'
    )
    (desktop_modules / ".vite").mkdir()
    env = _install_full_gate_fixtures(detached, tmp_path)
    env["GATE_DESKTOP_OBSERVATION"] = str(tmp_path / "observation.log")
    env["GATE_DESKTOP_SOURCE"] = str(desktop_modules)
    env["GATE_NPX_MODE"] = "handoff-source-missing"

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert "desktop dependency cleanup refused" in result.stderr
    assert not (detached / "apps/desktop/node_modules").exists()
    assert Path(f"{desktop_modules}.moved/fixture-package/package.json").is_file()


def test_gate_refuses_to_replace_an_unowned_desktop_handoff_target(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    desktop_modules = invocation / "apps/desktop/node_modules"
    (desktop_modules / "fixture-package").mkdir(parents=True)
    (desktop_modules / "fixture-package/package.json").write_text(
        '{"name":"fixture-package"}\n'
    )
    (desktop_modules / ".vite").mkdir()
    replacement = tmp_path / "unowned-desktop-dependencies"
    replacement.mkdir()
    env = _install_full_gate_fixtures(detached, tmp_path)
    env["GATE_DESKTOP_OBSERVATION"] = str(tmp_path / "observation.log")
    env["GATE_REPLACEMENT_SOURCE"] = str(replacement)
    env["GATE_NPX_MODE"] = "handoff-target-replaced"

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    detached_modules = detached / "apps/desktop/node_modules"
    assert result.returncode != 0
    assert "desktop dependency cleanup refused" in result.stderr
    assert detached_modules.is_symlink()
    assert detached_modules.resolve() == replacement.resolve()
    assert (desktop_modules / "fixture-package/package.json").is_file()


def test_gate_preserves_preexisting_external_desktop_dependency_symlink(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(invocation)
    desktop_modules = invocation / "apps/desktop/node_modules"
    desktop_modules.mkdir(parents=True)
    detached_link = detached / "apps/desktop/node_modules"
    detached_link.symlink_to(desktop_modules, target_is_directory=True)
    env = _install_full_gate_fixtures(detached, tmp_path)

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert detached_link.is_symlink()
    assert os.readlink(detached_link) == str(desktop_modules)
    assert detached_link.resolve() == desktop_modules.resolve()


def test_gate_rejects_desktop_dependencies_from_different_repository(
    tmp_path: Path,
) -> None:
    _shared_root, _invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(detached)
    unrelated = tmp_path / "unrelated-desktop-repository"
    (unrelated / "apps/desktop/node_modules").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=unrelated, check=True, capture_output=True)
    env = _install_full_gate_fixtures(detached, tmp_path)

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=unrelated,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "desktop dependencies are required" in result.stderr


def test_gate_rejects_escaping_sibling_desktop_dependency_view(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(detached)
    outside = tmp_path / "outside-desktop-dependencies"
    (outside / "node_modules").mkdir(parents=True)
    (invocation / "apps/desktop").mkdir(parents=True, exist_ok=True)
    (invocation / "apps/desktop/node_modules").symlink_to(
        outside / "node_modules", target_is_directory=True
    )
    env = _install_full_gate_fixtures(detached, tmp_path)

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "desktop dependencies are required" in result.stderr


@pytest.mark.parametrize(
    "missing_path",
    ["package-lock.json", "apps/desktop/package.json"],
)
def test_gate_rejects_sibling_desktop_view_with_missing_dependency_identity(
    tmp_path: Path,
    missing_path: str,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(detached)
    (invocation / "apps/desktop/node_modules").mkdir(parents=True)
    (invocation / missing_path).unlink()
    env = _install_full_gate_fixtures(detached, tmp_path)

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "desktop dependencies are required" in result.stderr


def test_gate_rejects_sibling_desktop_view_with_mismatched_dependency_identity(
    tmp_path: Path,
) -> None:
    _shared_root, invocation, detached, _base = _sibling_invocation_checkouts(
        tmp_path
    )
    _write_parser_dependencies(detached)
    (invocation / "apps/desktop/node_modules").mkdir(parents=True)
    (invocation / "package-lock.json").write_text(
        '{"name":"different","lockfileVersion":3,"packages":{}}\n'
    )
    subprocess.run(
        ["git", "commit", "-am", "change dependency identity"],
        cwd=invocation,
        check=True,
        capture_output=True,
    )
    env = _install_full_gate_fixtures(detached, tmp_path)

    result = subprocess.run(
        [GATE, "--repo", detached, "--phase", "base"],
        cwd=invocation,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "desktop dependencies are required" in result.stderr


def test_gate_fails_before_checker_when_root_parser_dependencies_are_missing(
    tmp_path: Path,
) -> None:
    shared_root, linked, _base = _linked_brand_checkout(tmp_path)
    (shared_root / "node_modules/micromark/package.json").unlink()
    marker = tmp_path / "missing-checker.marker"

    result = _run_gate_with_marker(linked, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_broken_or_escaping_root_dependency_link(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    outside = tmp_path / "outside"
    _write_parser_dependencies(outside)
    (repo / "node_modules").rename(repo / "shared-node-modules")
    (repo / "node_modules").symlink_to(outside / "node_modules", target_is_directory=True)
    marker = tmp_path / "escaping-checker.marker"

    result = _run_gate_with_marker(repo, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()

    (repo / "node_modules").unlink()
    (repo / "node_modules").symlink_to(tmp_path / "missing-node-modules")
    result = _run_gate_with_marker(repo, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_escaping_parser_package_paths_before_checker(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    outside = tmp_path / "outside"
    _write_parser_dependencies(outside)
    marker = tmp_path / "package-escape-checker.marker"
    package = repo / "node_modules/typescript"

    package.rename(repo / "node_modules/typescript-local")
    package.symlink_to(outside / "node_modules/typescript", target_is_directory=True)
    result = _run_gate_with_marker(repo, marker, "--phase", "base")
    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()

    package.unlink()
    (repo / "node_modules/typescript-local").rename(package)
    manifest = package / "package.json"
    manifest.rename(package / "package.local.json")
    manifest.symlink_to(outside / "node_modules/typescript/package.json")
    result = _run_gate_with_marker(repo, marker, "--phase", "base")
    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()

    manifest.unlink()
    (package / "package.local.json").rename(manifest)
    entrypoint = package / "index.js"
    entrypoint.rename(package / "index.local.js")
    entrypoint.symlink_to(outside / "node_modules/typescript/index.js")
    result = _run_gate_with_marker(repo, marker, "--phase", "base")
    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_nearer_helper_dependency_before_checker(tmp_path: Path) -> None:
    repo, _base = _brand_repo(tmp_path)
    _write_parser_dependencies(repo / "scripts")
    marker = tmp_path / "nearer-helper-dependency.marker"

    result = _run_gate_with_marker(repo, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_esm_exports_entrypoint_escape_before_checker(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    package = repo / "node_modules/unified"
    outside = tmp_path / "outside-entrypoint.js"
    outside.write_text("export {};\n", encoding="utf-8")
    (package / "esm-entrypoint.js").symlink_to(outside)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "unified",
                "version": PARSER_VERSIONS["unified"],
                "type": "module",
                "main": "./index.js",
                "exports": {
                    ".": {
                        "import": "./esm-entrypoint.js",
                        "require": "./index.js",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    node = shutil.which("node")
    assert node is not None
    require_entrypoint = subprocess.check_output(
        [
            node,
            "-e",
            "const {createRequire}=require('node:module'); "
            "process.stdout.write(createRequire(process.argv[1]).resolve('unified'))",
            str(repo / "scripts/extract_non_python_symbols.mjs"),
        ],
        cwd=repo,
        text=True,
    )
    import_entrypoint = subprocess.check_output(
        [
            node,
            "--experimental-import-meta-resolve",
            "--input-type=module",
            "-e",
            "import {fileURLToPath} from 'node:url'; "
            "process.stdout.write(fileURLToPath(import.meta.resolve('unified')))",
        ],
        cwd=repo / "scripts",
        text=True,
    )
    assert Path(require_entrypoint).resolve() == (package / "index.js").resolve()
    assert Path(import_entrypoint).resolve() == outside.resolve()
    marker = tmp_path / "esm-entrypoint-escape.marker"

    result = _run_gate_with_marker(repo, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_uses_node20_compatible_import_meta_resolve_flag(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    actual_node = shutil.which("node")
    assert actual_node is not None
    fixture_bin = tmp_path / "node20-bin"
    fixture_bin.mkdir()
    node_wrapper = fixture_bin / "node"
    node_wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "[[ \"${1:-}\" == \"--experimental-import-meta-resolve\" ]] || exit 97\n"
        "exec \"$REAL_NODE\" \"$@\"\n",
        encoding="utf-8",
    )
    node_wrapper.chmod(0o755)
    marker = tmp_path / "node20-compatibility.marker"

    result = _run_gate_with_marker(
        repo,
        marker,
        "--phase",
        "base",
        extra_env={
            "PATH": f"{fixture_bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_NODE": actual_node,
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "checked\n"


def test_gate_rejects_dangling_local_dependency_link_before_checker(
    tmp_path: Path,
) -> None:
    _shared_root, linked, _base = _linked_brand_checkout(tmp_path)
    (linked / "node_modules").symlink_to(
        tmp_path / "missing-node-modules", target_is_directory=True
    )
    marker = tmp_path / "dangling-checker.marker"

    result = _run_gate_with_marker(linked, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


def test_gate_rejects_wrong_parser_dependency_version_before_checker(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    manifest = repo / "node_modules/micromark/package.json"
    package_data = json.loads(manifest.read_text(encoding="utf-8"))
    package_data["version"] = "4.0.3"
    manifest.write_text(json.dumps(package_data), encoding="utf-8")
    marker = tmp_path / "wrong-version-checker.marker"

    result = _run_gate_with_marker(repo, marker, "--phase", "base")

    assert result.returncode == 1
    assert "root parser dependencies" in result.stderr
    assert not marker.exists()


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
    _write_parser_dependencies(repo)

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


def test_base_gate_refuses_tracked_dirty_tree_before_tested_sha(
    tmp_path: Path,
) -> None:
    """A commit SHA must never be advertised for different tested bytes."""
    repo, _base = _brand_repo(tmp_path)
    (repo / "plugins/workflow/runtime.py").write_text("VALUE = 'dirty tested bytes'\n")
    env = os.environ.copy()
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"

    result = subprocess.run(
        [GATE, "--repo", repo, "--phase", "base"],
        cwd=repo,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert "tracked working tree is dirty" in result.stderr
    assert "TESTED_BASE_SHA=" not in result.stdout


def test_gate_resolves_relative_python_before_switching_repositories(
    tmp_path: Path,
) -> None:
    repo, _base = _brand_repo(tmp_path)
    env = os.environ.copy()
    env["WORKFLOW_MERGE_GATE_FAST"] = "1"
    env["PYTHON_BIN"] = os.path.relpath(sys.executable, ROOT)

    result = subprocess.run(
        [GATE, "--repo", repo, "--phase", "base"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
