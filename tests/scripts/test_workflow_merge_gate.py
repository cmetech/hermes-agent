from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess


ROOT = Path(__file__).parents[2]
GATE = ROOT / "scripts/test_workflow_merge_gate.sh"
CI = ROOT / ".github/workflows/ci.yml"
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
        "tests/plugins/workflow/test_node_mcp.py",
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
        "tests/plugins/workflow/test_showcase_ai_e2e.py",
        "tests/plugins/workflow/test_showcase_evidence.py",
        "tests/plugins/workflow/test_showcase_offline_e2e.py",
        "tests/plugins/workflow/test_showcase_schedule_e2e.py",
        "tests/plugins/workflow/test_store.py",
        "tests/plugins/workflow/test_topology.py",
    )
}


def test_merge_gate_references_only_existing_invariant_tests() -> None:
    referenced = set(
        re.findall(r"tests/[A-Za-z0-9_./-]+\.(?:py|ts|tsx)", GATE.read_text())
    )

    assert referenced
    assert not [path for path in sorted(referenced) if not (ROOT / path).is_file()]


def test_merge_gate_enforces_async_reload_and_delivery_regressions() -> None:
    source = GATE.read_text()

    for required_test in (
        "tests/gateway/test_plugin_background_services.py",
        "tests/gateway/test_plugin_delivery.py",
        "tests/hermes_cli/test_plugin_provider_hot_reload.py",
        "tests/scripts/test_workflow_merge_gate.py",
    ):
        assert required_test in source


def test_merge_gate_pins_workflow_catalog_api_contract() -> None:
    source = GATE.read_text()

    assert "tests/plugins/workflow/test_catalog_api.py" in source


def test_merge_gate_pins_workflow_detail_api_contract() -> None:
    source = GATE.read_text()

    assert "tests/plugins/workflow/test_workflow_detail_api.py" in source


def test_native_workflow_matrix_covers_every_release_gate() -> None:
    source = CI.read_text()

    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in source
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
        assert required_test in source


def test_every_workflow_test_is_selected_or_explicitly_opted_out() -> None:
    inventory = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/plugins/workflow").glob("test_*.py")
    }
    selected = set(
        re.findall(
            r"tests/plugins/workflow/test_[A-Za-z0-9_]+\.py",
            GATE.read_text() + CI.read_text(),
        )
    )
    opted_out = set(WORKFLOW_GATE_OPTOUTS)

    assert all(reason.strip() for reason in WORKFLOW_GATE_OPTOUTS.values())
    assert not any("*" in path for path in opted_out)
    assert not (opted_out - inventory)
    assert not (opted_out & selected)
    assert not (inventory - selected - opted_out)


def test_merge_gate_shares_workspace_root_dependencies_with_temp_worktrees() -> None:
    source = GATE.read_text()

    assert 'ln -s "$SHARED_ROOT/node_modules" node_modules' in source
    assert '[[ -d node_modules ]]' in source


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
    result = subprocess.run([GATE], cwd=ROOT, text=True, capture_output=True, env=env)
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
