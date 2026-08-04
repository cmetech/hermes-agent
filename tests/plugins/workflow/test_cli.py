from __future__ import annotations

import argparse
import ast
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from plugins import workflow as workflow_plugin
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _runtime_config, register_cli
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow import machine_contract
from plugins.workflow.models import ExecutionFence, ValidationIssue
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import RunStore
from plugins.workflow.trust import build_risk_summary, compute_package_digest
from plugins.workflow.trust import WorkflowPackageDigest, WorkflowTrustStore
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def _json_result(capsys):
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is True
    assert envelope["error"] is None
    return envelope["result"]


def _json_envelope(capsys):
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out)


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    if not root.exists():
        return ()
    entries = []
    for path in sorted(root.rglob("*")):
        kind = "directory" if path.is_dir() else "file"
        entries.append((
            path.relative_to(root).as_posix(),
            kind,
            None if path.is_dir() else path.read_bytes(),
        ))
    return tuple(entries)


def _run_packaged_schema(tmp_path: Path, arguments: list[str]):
    home = tmp_path / "fresh-home"
    hermes_home = tmp_path / "fresh-hermes-home"
    guard_dir = tmp_path / "process-guards"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(
        """
import socket
import sys

class _ForbiddenRuntimeImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"run_agent", "model_tools", "tools.mcp_tool"}:
            raise RuntimeError(f"forbidden runtime import: {fullname}")
        return None

def _forbid_network(*args, **kwargs):
    raise RuntimeError("network access is forbidden during schema introspection")

sys.meta_path.insert(0, _ForbiddenRuntimeImports())
socket.create_connection = _forbid_network
socket.socket.connect = _forbid_network
""".lstrip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_HOME": str(hermes_home),
        "PYTHONPATH": os.pathsep.join([str(guard_dir), str(Path(__file__).parents[3])]),
    }
    before = (_tree_snapshot(home), _tree_snapshot(hermes_home))
    completed = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *arguments],
        cwd=Path(__file__).parents[3],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    after = (_tree_snapshot(home), _tree_snapshot(hermes_home))
    return completed, before, after, home, hermes_home


def _run_packaged_startup_with_recovery_marker(
    tmp_path: Path, arguments: list[str]
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    recovery_root = tmp_path / "recovery-root"
    recovery_root.mkdir()
    marker = recovery_root / ".lazy-refresh-incomplete"
    marker.write_text("pending\n", encoding="utf-8")
    (recovery_root / "pyproject.toml").write_text(
        '[project]\nname = "workflow-schema-startup-test"\nversion = "0"\n',
        encoding="utf-8",
    )

    trace = tmp_path / "early-recovery.trace"
    guard_dir = tmp_path / "recovery-probe"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(
        """
import os
from pathlib import Path

from hermes_cli import _early_recovery as recovery

recovery._project_root = lambda: Path(os.environ["HERMES_TEST_RECOVERY_ROOT"])
recovery._probe_broken_packages = lambda: []
original_recover_if_needed = recovery.recover_if_needed

def record_recovery_call(*args, **kwargs):
    Path(os.environ["HERMES_TEST_RECOVERY_TRACE"]).write_text(
        "called\\n", encoding="utf-8"
    )
    return original_recover_if_needed(*args, **kwargs)

recovery.recover_if_needed = record_recovery_call
""".lstrip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "HERMES_HOME": str(tmp_path / "hermes-home"),
        "HERMES_TEST_RECOVERY_ROOT": str(recovery_root),
        "HERMES_TEST_RECOVERY_TRACE": str(trace),
        "HERMES_TEST_STARTUP_ARGV": json.dumps(arguments),
        "PYTHONPATH": os.pathsep.join([str(guard_dir), str(Path(__file__).parents[3])]),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os, runpy, sys; "
                "sys.argv = ['hermes', *json.loads(os.environ['HERMES_TEST_STARTUP_ARGV'])]; "
                "runpy.run_module('hermes_cli.main', run_name='__main__')"
            ),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, marker, trace


def test_success_envelope_sanitizes_every_machine_payload_and_preserves_cleanup_capability():
    ordinary = machine_contract.success_envelope(
        "workflow list",
        {"api_token": "secret", "label": "\x1b[31mvisible\x1b[0m"},
    )
    assert ordinary["result"] == {"api_token": "[REDACTED]", "label": "visible"}

    cleanup = machine_contract.success_envelope(
        "workflow cleanup",
        {"confirmation_token": "server-minted-capability", "api_token": "secret"},
    )
    assert cleanup["result"] == {
        "confirmation_token": "server-minted-capability",
        "api_token": "[REDACTED]",
    }

    for command in (
        "workflow showcase preflight",
        "workflow showcase run",
        "workflow showcase cleanup",
    ):
        showcase = machine_contract.success_envelope(
            command,
            {
                "confirmation_token": "server-minted-capability",
                "command_contract": machine_contract.operator_command_contract(),
            },
        )
        assert showcase["result"]["confirmation_token"] == "server-minted-capability"
        assert (
            showcase["result"]["command_contract"]
            == machine_contract.operator_command_contract()
        )

    untrusted = machine_contract.success_envelope(
        "workflow list", {"confirmation_token": "unrecognized-secret"}
    )
    assert untrusted["result"]["confirmation_token"] == "[REDACTED]"


def _write(workflow_writer, workdir):
    return workflow_writer(
        workdir / ".hermes" / "workflows",
        name="sample",
        nodes=[{"id": "start", "bash": "printf SECRET_BODY"}],
    )


def _archon_package(workflow_writer, tmp_path, *, field, value):
    """Write one declared Archon package with the requested field."""
    node = (
        {"id": "start", "bash": "true", field: value}
        if field == "timeout"
        else {"id": "start", "prompt": "x", field: value}
    )
    path = workflow_writer(
        tmp_path / ".hermes" / "workflows",
        name=f"archon-{field}",
        filename=f"archon-{field}.yaml",
        nodes=[node],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return path


@pytest.mark.parametrize(
    "field, value, code",
    [
        ("maxBudgetUsd", 1.0, "archon_budget_enforcement_unavailable"),
        ("sandbox", {"enabled": True}, "archon_sandbox_enforcement_unavailable"),
    ],
)
def test_archon_deferred_fields_block_validate_trust_and_run(
    workflow_writer, tmp_path, capsys, field, value, code
):
    path = _archon_package(workflow_writer, tmp_path, field=field, value=value)
    parser = _parser()
    home = tmp_path / "home"
    common = ["--workdir", str(tmp_path), "--hermes-home", str(home)]

    validate = parser.parse_args([*common, "validate", path.stem, "--json"])
    assert validate.func(validate) == machine_contract.EXIT_BLOCKING_FINDING
    validation = _json_envelope(capsys)
    assert validation["error"]["code"] == "validation_failed"
    identities = {
        (issue["code"], issue["path"])
        for issue in validation["result"]["issues"]
    }
    assert (code, f"nodes[0].{field}") in identities
    assert len(validation["result"]["issues"]) == len(identities)

    package = load_workflow(path)
    digest = compute_package_digest(package).sha256
    trust = parser.parse_args(
        [*common, "trust", path.stem, "--digest", digest, "--json"]
    )
    assert trust.func(trust) == machine_contract.EXIT_BLOCKING_FINDING
    assert _json_envelope(capsys)["error"]["code"] == "workflow_compatibility_blocked"
    assert WorkflowTrustStore(home).check(digest) == "untrusted"

    run = parser.parse_args(
        [
            *common,
            "run",
            path.stem,
            "--foreground",
            "--idempotency-key",
            "archon-refusal",
            "--json",
        ]
    )
    assert run.func(run) == machine_contract.EXIT_BLOCKING_FINDING
    assert _json_envelope(capsys)["error"]["code"] == "workflow_compatibility_blocked"
    store = RunStore(home)
    assert list(store.runs_root.rglob("run.json")) == []
    assert list(store.staging_root.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout", 1000),
        ("retry", {"max_attempts": 2}),
    ],
)
def test_archon_phase3_timeout_and_retry_fields_validate(
    workflow_writer, tmp_path, capsys, field, value
):
    path = _archon_package(workflow_writer, tmp_path, field=field, value=value)
    args = _parser().parse_args([
        "--workdir",
        str(tmp_path),
        "validate",
        path.stem,
        "--json",
    ])

    assert args.func(args) == 0
    result = _json_result(capsys)
    assert result["valid"] is True
    assert result["issues"] == []
    assert result["language"]["normalizer_version"] == 3


def test_archon_cli_admission_seals_resolved_profile_execution_authority(
    workflow_writer, tmp_path, capsys
) -> None:
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="archon-sealed-cli-limits",
        filename="archon-sealed-cli-limits.yaml",
        nodes=[{"id": "start", "bash": "true"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {
                "entries": {
                    "workflow": {
                        "runtime": {
                            "ai_idle_timeout_seconds": 120,
                            "ai_wall_timeout_seconds": 240,
                            "provider_request_timeout_seconds": 90,
                            "subprocess_timeout_seconds": 30,
                            "combined_retries": 2,
                        }
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "run",
        path.stem,
        "--foreground",
        "--idempotency-key",
        "archon-sealed-cli-limits",
        "--json",
    ])

    assert args.func(args) == 0
    result = _json_result(capsys)
    resources = json.loads(
        (RunStore(home).run_directory(result["run_id"]) / "resources.json").read_bytes()
    )
    assert resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 120.0,
        "ai_wall_timeout_seconds": 240.0,
        "provider_request_timeout_seconds": 90.0,
        "subprocess_timeout_seconds": 30.0,
        "combined_total_attempts": 2,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_format", {"type": "object"}),
        ("output_type", "report"),
    ],
)
def test_archon_phase_2_output_fields_validate(
    workflow_writer, tmp_path, capsys, field, value
):
    path = _archon_package(workflow_writer, tmp_path, field=field, value=value)
    parser = _parser()
    home = tmp_path / "home"
    validate = parser.parse_args([
        "--workdir",
        str(tmp_path),
        "--hermes-home",
        str(home),
        "validate",
        path.stem,
        "--json",
    ])

    assert validate.func(validate) == 0
    result = _json_result(capsys)
    assert result["valid"] is True
    assert result["issues"] == []
    assert result["language"]["effective_profile"] == "archon-2026-07"


def test_archon_validate_text_reports_field_specific_compatibility_finding(
    workflow_writer, tmp_path, capsys
):
    path = _archon_package(
        workflow_writer, tmp_path, field="maxBudgetUsd", value=1.0
    )
    args = _parser().parse_args([
        "--workdir",
        str(tmp_path),
        "validate",
        path.stem,
    ])

    assert args.func(args) == machine_contract.EXIT_BLOCKING_FINDING
    output = capsys.readouterr()
    assert output.err == ""
    assert "archon-maxBudgetUsd: invalid" in output.out
    assert "nodes[0].maxBudgetUsd" in output.out
    assert "Archon budget enforcement is not available in Phase 1" in output.out


def test_module_entrypoint_propagates_blocking_doctor_exit(
    tmp_path, workflow_writer
):
    """Catch a top-level dispatcher that discards a workflow handler status."""
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="sample",
        nodes=[{"id": "start", "prompt": "work", "maxBudgetUsd": 1.0}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )

    completed, *_ = _run_packaged_schema(
        tmp_path,
        [
            "workflow",
            "--workdir",
            str(workdir),
            "doctor",
            "sample",
            "--mode",
            "foreground",
            "--compat-report",
            "--json",
        ],
    )

    assert completed.returncode == machine_contract.EXIT_BLOCKING_FINDING
    assert json.loads(completed.stdout)["error"]["code"] == (
        "blocking_doctor_findings"
    )


@pytest.mark.parametrize(
    ("sidecar", "definition", "expected_code"),
    [
        (
            "language_compatibility: unsupported-profile\n",
            {},
            "workflow_language_profile_unsupported",
        ),
        (
            "language_compatibility: archon-2026-07\n",
            {"future_archon_option": True},
            "archon_unknown_top_level_field",
        ),
    ],
    ids=["unsupported-profile", "unknown-archon-top-level"],
)
def test_json_load_failures_preserve_typed_workflow_issue_codes(
    workflow_writer, tmp_path, capsys, sidecar, definition, expected_code
):
    """Catch a generic invalid_request envelope that hides load diagnostics."""
    path = workflow_writer(
        tmp_path / ".hermes" / "workflows",
        name="typed-load-error",
        **definition,
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        sidecar, encoding="utf-8"
    )
    args = _parser().parse_args([
        "--workdir",
        str(tmp_path),
        "validate",
        path.stem,
        "--json",
    ])

    assert args.func(args) == machine_contract.EXIT_INVOCATION
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["details"]["issues"][0]["code"] == expected_code


def test_doctor_text_renders_sanitized_finding_code_path_and_migration(
    workflow_writer, tmp_path, capsys
):
    """Catch doctor text that omits actionable safe finding diagnostics."""
    path = _archon_package(
        workflow_writer, tmp_path, field="maxBudgetUsd", value=1.0
    )
    args = _parser().parse_args([
        "--workdir",
        str(tmp_path),
        "--hermes-home",
        str(tmp_path / "profile"),
        "doctor",
        path.stem,
    ])

    assert args.func(args) == machine_contract.EXIT_BLOCKING_FINDING
    output = capsys.readouterr()
    assert output.err == ""
    assert "archon_budget_enforcement_unavailable" in output.out
    assert "nodes[0].maxBudgetUsd" in output.out
    assert "Remove maxBudgetUsd or wait for Phase 5 budget enforcement." in output.out
    assert "SECRET_BODY" not in output.out
    assert str(tmp_path) not in output.out


def _doctor_text_payload(findings):
    return {
        "name": "sample",
        "package_digest": "package-digest",
        "risk_summary": {
            "risk_digest": "risk-digest",
            "execution_environment": "trusted_local",
        },
        "compatibility": "mapped",
        "language": {"effective_profile": "hermes-legacy"},
        "remediation": "Review diagnostics.",
        "findings": findings,
    }


def test_doctor_text_bounds_finding_diagnostics_to_machine_projection_limit(
    capsys, monkeypatch
):
    """Catch unbounded human output from an oversized finding collection."""
    findings = [
        {
            "code": f"attacker-finding-{index}",
            "path": f"nodes[{index}].option",
            "migration": "Remove unsupported option.",
        }
        for index in range(201)
    ]
    monkeypatch.setattr(
        "plugins.workflow.cli._doctor_payload",
        lambda *_args, **_kwargs: _doctor_text_payload(findings),
    )
    monkeypatch.setattr("plugins.workflow.cli._resolve", lambda *_args: object())
    args = _parser().parse_args(["doctor", "sample"])

    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert output.count("attacker-finding-") == 200
    assert "attacker-finding-199" in output
    assert "attacker-finding-200" not in output
    assert "truncated after 200 findings" in output


@pytest.mark.parametrize(
    ("unsafe_path", "leaked_fragments"),
    [
        ("/private/workflows/secret.yaml", ("secret.yaml",)),
        (r"C:\Users\alice\secret.yaml", ("secret.yaml",)),
        (r"\\server\share\secret.yaml", ("secret.yaml",)),
        (
            "/private/workflows/secret file.yaml",
            ("secret file.yaml", "secret", "file.yaml"),
        ),
        (
            r"C:\Users\alice\secret file.yaml",
            ("secret file.yaml", "secret", "file.yaml"),
        ),
        (
            r"\\server\share\secret file.yaml",
            ("secret file.yaml", "secret", "file.yaml"),
        ),
    ],
    ids=[
        "posix",
        "windows-drive",
        "windows-unc",
        "posix-space",
        "windows-drive-space",
        "windows-unc-space",
    ],
)
def test_doctor_text_redacts_cross_platform_absolute_finding_paths(
    unsafe_path, leaked_fragments, capsys, monkeypatch
):
    """Catch OS-specific absolute paths that escape doctor text sanitization."""
    findings = [
        {
            "code": "attacker-controlled-finding",
            "path": f"sidecar.delivery_defaults.inputs.{unsafe_path}",
            "migration": "Use a portable relative input name.",
        }
    ]
    monkeypatch.setattr(
        "plugins.workflow.cli._doctor_payload",
        lambda *_args, **_kwargs: _doctor_text_payload(findings),
    )
    monkeypatch.setattr("plugins.workflow.cli._resolve", lambda *_args: object())
    args = _parser().parse_args(["doctor", "sample"])

    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "attacker-controlled-finding" in output
    assert "Use a portable relative input name." in output
    assert unsafe_path not in output
    assert "[REDACTED_PATH]" in output
    for fragment in leaked_fragments:
        assert fragment not in output


def test_module_entrypoint_treats_none_returning_plugins_handler_as_success(tmp_path):
    """Catch SystemExit(None) or a changed non-workflow handler exit contract."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "plugins",
            "list",
            "--plain",
            "--no-bundled",
        ],
        cwd=Path(__file__).parents[3],
        env={
            **os.environ,
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "declared_profile",
    [None, "hermes-legacy"],
    ids=["unversioned", "explicit-legacy"],
)
def test_legacy_timeout_and_retry_validate_trust_and_run_with_warnings(
    workflow_writer, tmp_path, capsys, declared_profile
):
    path = workflow_writer(
        tmp_path / ".hermes" / "workflows",
        name="legacy-timeout-retry",
        filename="legacy-timeout-retry.yaml",
        nodes=[
            {
                "id": "start",
                "bash": "true",
                "timeout": 1,
                "retry": {"max_attempts": 2},
            }
        ],
    )
    if declared_profile is not None:
        path.with_name(f"{path.stem}.hermes.yaml").write_text(
            f"language_compatibility: {declared_profile}\n", encoding="utf-8"
        )
    home = tmp_path / "home"
    common = ["--workdir", str(tmp_path), "--hermes-home", str(home)]
    parser = _parser()

    validate = parser.parse_args([*common, "validate", path.stem, "--json"])
    assert validate.func(validate) == 0
    validation = _json_result(capsys)
    assert validation["language"]["effective_profile"] == "hermes-legacy"
    assert {
        issue["code"] for issue in validation["issues"]
    } >= {"legacy_timeout_seconds", "legacy_retry_total_attempts"}

    package = load_workflow(path)
    digest = compute_package_digest(package).sha256
    trust = parser.parse_args(
        [*common, "trust", path.stem, "--digest", digest, "--json"]
    )
    assert trust.func(trust) == 0
    assert _json_result(capsys)["status"] == "trusted"

    run = parser.parse_args(
        [
            *common,
            "run",
            path.stem,
            "--foreground",
            "--idempotency-key",
            f"legacy-{declared_profile or 'unversioned'}",
            "--json",
        ]
    )
    assert run.func(run) == 0
    assert _json_result(capsys)["status"] == "succeeded"


def test_plugin_registers_cli_command_and_background_coordinator():
    class Context:
        def __init__(self):
            self.calls = []
            self.service_calls = []

        def register_cli_command(self, **kwargs):
            self.calls.append(kwargs)

        def register_background_service(self, *args, **kwargs):
            self.service_calls.append((args, kwargs))

        def __getattr__(self, name):
            raise AssertionError(f"unexpected plugin registration: {name}")

    ctx = Context()
    workflow_plugin.register(ctx)
    assert len(ctx.calls) == 1
    assert ctx.calls[0]["name"] == "workflow"
    assert len(ctx.service_calls) == 1
    assert ctx.service_calls[0][0][0] == "coordinator"
    assert ctx.service_calls[0][1]["hosts"] == {"web", "gateway"}


def test_runtime_limits_load_from_plugin_entry_without_new_root_config(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  entries:\n"
        "    workflow:\n"
        "      runtime:\n"
        "        max_parallel_nodes: 2\n"
        "        max_total_workers: 3\n"
    )

    config = _runtime_config(profile)

    assert config.max_parallel_nodes == 2
    assert config.max_total_workers == 3


def test_cli_module_has_no_agent_provider_network_or_mcp_runtime_imports():
    source = (Path(__file__).parents[3] / "plugins" / "workflow" / "cli.py").read_text(
        encoding="utf-8"
    )
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imports.isdisjoint({"run_agent", "model_tools", "mcp", "requests", "httpx"})


@pytest.mark.parametrize(
    ("arguments", "expected_profile"),
    [
        (["schema", "--json"], "archon-2026-07"),
        (["schema", "--profile", "hermes-legacy", "--json"], "hermes-legacy"),
    ],
)
def test_schema_json_selects_profile_without_workflow_discovery(
    arguments, expected_profile, tmp_path, capsys, monkeypatch
):
    workdir = tmp_path / "missing-workflow-directory"
    profile = tmp_path / "missing-profile-directory"

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("schema must not discover workflows or call runtimes")

    monkeypatch.setattr("plugins.workflow.cli._discover", unexpected_call)
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(profile),
        *arguments,
    ])

    assert args.func(args, agent_runner=unexpected_call) == 0
    output = capsys.readouterr()
    contract = json.loads(output.out)
    assert output.err == ""
    assert contract["profile"] == expected_profile
    assert not workdir.exists()
    assert not profile.exists()


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_schema_json_emits_the_complete_authoring_envelope_unchanged(
    profile, capsys
):
    args = _parser().parse_args(["schema", "--profile", profile.value, "--json"])

    assert args.func(args) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted == workflow_authoring_contract(profile)
    assert emitted["contract_digest"].startswith("sha256:")
    assert emitted["node_kinds"]
    assert emitted["semantic_rules"]


def test_schema_json_is_compact_and_byte_deterministic(capsys):
    parser = _parser()

    first = parser.parse_args(["schema", "--json"])
    assert first.func(first) == 0
    first_output = capsys.readouterr().out
    second = parser.parse_args(["schema", "--json"])
    assert second.func(second) == 0
    second_output = capsys.readouterr().out

    assert first_output == second_output
    assert first_output.count("\n") == 1
    assert ": " not in first_output


def test_schema_text_is_indented_json(capsys):
    args = _parser().parse_args(["schema", "--profile", "hermes-legacy"])

    assert args.func(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["profile"] == "hermes-legacy"
    assert '\n  "compatibility_codes"' in output.out


@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "text"])
def test_packaged_schema_command_is_read_only_before_normal_startup(
    tmp_path, json_mode
):
    """The exact packaged introspection path must not initialize Hermes."""
    arguments = [
        "workflow",
        "schema",
        "--profile",
        "archon-2026-07",
    ]
    if json_mode:
        arguments.append("--json")

    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, arguments
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["profile"] == "archon-2026-07"
    if json_mode:
        assert completed.stdout.count("\n") == 1
        assert ": " not in completed.stdout
    else:
        assert '\n  "compatibility_codes"' in completed.stdout
    assert before == ((), ())
    assert after == before
    assert not home.exists()
    assert not hermes_home.exists()


@pytest.mark.parametrize(
    ("arguments", "returncode", "early_recovery_called"),
    [
        (["workflow", "schema", "--json"], 0, False),
        (["workflow", "schema", "--help"], 0, False),
        (["workflow", "schema", "--definitely-child"], 2, False),
        (["--version"], 0, True),
        (["update", "--help"], 0, True),
    ],
    ids=["schema-success", "schema-help", "schema-error", "normal", "update"],
)
def test_packaged_schema_alone_skips_early_recovery_marker_probe(
    tmp_path, arguments, returncode, early_recovery_called
):
    completed, marker, trace = _run_packaged_startup_with_recovery_marker(
        tmp_path, arguments
    )

    assert completed.returncode == returncode, completed.stderr
    assert trace.exists() is early_recovery_called
    assert marker.read_text(encoding="utf-8") == "pending\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--definitely-unknown", "workflow", "schema", "--json"],
        ["workflow", "--definitely-unknown", "schema", "--json"],
    ],
    ids=["global", "workflow-root"],
)
def test_packaged_schema_rejects_unknown_precommand_once_without_startup(
    tmp_path, arguments
):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, arguments
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.count("usage: hermes") == 1
    assert completed.stderr.count("error:") == 1
    assert "unrecognized arguments: --definitely-unknown" in completed.stderr
    assert '"schema_version"' not in completed.stderr
    assert before == after == ((), ())
    assert not home.exists()
    assert not hermes_home.exists()


def test_packaged_schema_help_is_read_only_argparse_output(tmp_path):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, ["workflow", "schema", "--help"]
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.count("usage: hermes workflow schema") == 1
    assert "--profile {hermes-legacy,archon-2026-07}" in completed.stdout
    assert "--json" in completed.stdout
    assert before == after == ((), ())
    assert not home.exists()
    assert not hermes_home.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["workflow", "schema", "--profile", "future-profile"],
            "invalid choice: 'future-profile'",
        ),
        (
            ["workflow", "schema", "--profile"],
            "argument --profile: expected one argument",
        ),
        (
            ["workflow", "schema", "--definitely-child", "--json"],
            "unrecognized arguments: --definitely-child",
        ),
    ],
    ids=["invalid-profile", "missing-profile", "unknown-child"],
)
def test_packaged_schema_parse_errors_are_single_and_read_only(
    tmp_path, arguments, message
):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, arguments
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.count("usage: hermes") == 1
    assert completed.stderr.count("error:") == 1
    assert message in completed.stderr
    assert before == after == ((), ())
    assert not home.exists()
    assert not hermes_home.exists()


@pytest.mark.parametrize(
    ("arguments", "expected_profile"),
    [
        (
            [
                "--profile",
                "default",
                "workflow",
                "schema",
                "--profile",
                "hermes-legacy",
                "--json",
            ],
            "hermes-legacy",
        ),
        (
            [
                "workflow",
                "--profile",
                "default",
                "schema",
                "--profile",
                "archon-2026-07",
                "--json",
            ],
            "archon-2026-07",
        ),
        (
            [
                "workflow",
                "schema",
                "--profile",
                "hermes-legacy",
                "--json",
                "-p",
                "default",
            ],
            "hermes-legacy",
        ),
    ],
    ids=["global-before-command", "global-before-action", "global-after-child"],
)
def test_packaged_schema_keeps_global_and_child_profiles_distinct(
    tmp_path, arguments, expected_profile
):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, arguments
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["profile"] == expected_profile
    assert before == after == ((), ())
    assert not home.exists()
    assert not hermes_home.exists()


@pytest.mark.parametrize("version_flag", ["--version", "-V"])
def test_packaged_schema_defers_to_normal_version_precedence(tmp_path, version_flag):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path, [version_flag, "workflow", "schema", "--json"]
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert "Install directory:" in completed.stdout
    assert '"schema_version"' not in completed.stdout
    assert before == ((), ())
    assert after != before
    assert not home.exists()
    assert hermes_home.exists()


@pytest.mark.parametrize("oneshot_flag", ["--oneshot", "-z"])
def test_packaged_schema_defers_to_normal_oneshot_precedence(tmp_path, oneshot_flag):
    completed, before, after, home, hermes_home = _run_packaged_schema(
        tmp_path,
        [oneshot_flag, "precedence probe", "workflow", "schema", "--json"],
    )

    assert completed.returncode == 1
    assert '"schema_version"' not in completed.stdout
    assert '"schema_version"' not in completed.stderr
    assert "forbidden runtime import:" in completed.stderr
    assert before == ((), ())
    assert after != before
    assert not home.exists()
    assert hermes_home.exists()


def test_list_and_show_json_are_stable_and_redacted(workflow_writer, tmp_path, capsys):
    workdir = tmp_path / "repo"
    _write(workflow_writer, workdir)
    parser = _parser()

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "list",
        "--json",
    ])
    assert args.func(args) == 0
    listing = _json_result(capsys)
    assert listing[0]["name"] == "sample"

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "show",
        "sample",
        "--json",
    ])
    assert args.func(args) == 0
    detail = _json_result(capsys)
    assert detail["topology_text"] == "start"
    assert detail["topology_mermaid"].startswith("flowchart LR")
    assert "SECRET_BODY" not in json.dumps(detail)


def test_json_show_rejects_only_explicit_topology_selector(
    workflow_writer, tmp_path, capsys
):
    workdir = tmp_path / "repo"
    _write(workflow_writer, workdir)
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "show",
        "sample",
        "--json",
        "--topology",
        "mermaid",
    ])
    assert args.func(args) == 2
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == "invalid_request"
    assert "--topology cannot be combined with --json" in envelope["error"]["message"]


def test_human_show_defaults_to_text_topology(workflow_writer, tmp_path, capsys):
    workdir = tmp_path / "repo"
    _write(workflow_writer, workdir)
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "show",
        "sample",
    ])
    assert args.topology is None
    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "Topology: start" in output
    assert "flowchart LR" not in output


def test_cleanup_cli_is_preview_only_until_exact_token_is_executed(
    workflow_writer, tmp_path, capsys
) -> None:
    profile = tmp_path / "profile"
    package = load_workflow(workflow_writer(tmp_path / "package", name="cleanup-cli"))
    store = RunStore(profile)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="cleanup-cli",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="cleanup-cli",
            concurrency_key="cleanup-cli",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    parser = _parser()
    common = ["--hermes-home", str(profile), "cleanup", "--older-than", "0d"]

    preview_args = parser.parse_args([*common, "--json"])
    assert preview_args.func(preview_args) == 0
    preview = _json_result(capsys)
    assert preview["execute"] is False
    assert preview["confirmation_token"]
    assert store.run_directory(admitted.run_id).is_dir()

    execute_args = parser.parse_args([
        *common,
        "--execute",
        "--confirmation-token",
        preview["confirmation_token"],
        "--json",
    ])
    assert execute_args.func(execute_args) == 0
    executed = _json_result(capsys)
    assert executed["execute"] is True
    assert store.list_runs() == ()


def test_resume_authenticates_always_run_before_mutating_durable_state(
    workflow_writer, tmp_path, capsys
) -> None:
    home = tmp_path / "profile"
    package = load_workflow(
        workflow_writer(
            tmp_path / "resume-package",
            name="resume-authentication",
            nodes=[
                {"id": "cached", "bash": "true"},
                {
                    "id": "fail",
                    "bash": "false",
                    "depends_on": ["cached"],
                },
            ],
        )
    )
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="resume-authentication",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    scheduler = RunScheduler(store)
    try:
        assert scheduler.advance(admitted.run_id)["status"] == "failed"
    finally:
        scheduler.shutdown(deadline_seconds=2)
    before = store.get_run_status(admitted.run_id)
    definition_path = store.run_directory(admitted.run_id) / "definition.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["nodes"][0]["always_run"] = True
    definition_path.write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )

    args = _parser().parse_args([
        "--hermes-home",
        str(home),
        "resume",
        admitted.run_id,
        "--json",
    ])

    assert args.func(args) == machine_contract.EXIT_INVOCATION
    assert _json_envelope(capsys)["error"]["code"] == (
        "workflow_snapshot_integrity_mismatch"
    )
    assert store.get_run_status(admitted.run_id) == before


def test_validate_doctor_trust_and_untrust(workflow_writer, tmp_path, capsys):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    args = parser.parse_args([*common, "validate", "sample", "--json"])
    assert args.func(args) == 0
    validation = _json_result(capsys)
    assert validation["valid"] is True
    package = load_workflow(path)
    assert validation["language"] == {
        "declared_profile": None,
        "effective_profile": "hermes-legacy",
        "normalizer_version": package.language.normalizer_version,
        "normalized_definition_digest": package.language.normalized_definition_digest,
        "legacy": True,
    }

    args = parser.parse_args([*common, "doctor", "sample", "--compat-report", "--json"])
    assert args.func(args) == 0
    doctor = _json_result(capsys)
    assert doctor["package_digest"]
    assert doctor["language"] == validation["language"]
    legacy = next(
        finding
        for finding in doctor["findings"]
        if finding["code"] == "legacy_language_profile"
    )
    assert legacy["severity"] == "warning"
    assert legacy["effective_profile"] == "hermes-legacy"
    assert legacy["migration"]
    assert doctor["compatibility_findings"] == doctor["findings"]
    assert "SECRET_BODY" not in json.dumps(doctor)

    args = parser.parse_args([
        *common,
        "trust",
        "sample",
        "--digest",
        "0" * 64,
        "--json",
    ])
    assert args.func(args) == 2
    assert _json_envelope(capsys)["error"]["code"] == "digest_mismatch"

    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    assert digest.sha256 == risk.package_digest
    args = parser.parse_args([
        *common,
        "trust",
        "sample",
        "--digest",
        digest.sha256,
        "--json",
    ])
    assert args.func(args) == 0
    assert _json_result(capsys)["status"] == "trusted"

    args = parser.parse_args([*common, "untrust", "sample", "--json"])
    assert args.func(args) == 0
    assert _json_result(capsys)["status"] == "untrusted"


def test_validate_and_doctor_text_include_effective_language_profile(
    workflow_writer, tmp_path, capsys
):
    workdir = tmp_path / "repo"
    _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    validate_args = parser.parse_args([*common, "validate", "sample"])
    assert validate_args.func(validate_args) == 0
    assert "Language: hermes-legacy" in capsys.readouterr().out

    doctor_args = parser.parse_args([*common, "doctor", "sample"])
    assert doctor_args.func(doctor_args) == 0
    assert "Language: hermes-legacy" in capsys.readouterr().out


def test_trust_rejects_package_mutation_during_admission(
    workflow_writer, tmp_path, capsys, monkeypatch
):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    original = compute_package_digest(load_workflow(path))
    mutated = WorkflowPackageDigest("f" * 64, original.covered_relative_paths)
    calls = iter((original, mutated))
    monkeypatch.setattr(
        "plugins.workflow.cli.compute_package_digest", lambda package: next(calls)
    )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(profile),
        "trust",
        "sample",
        "--digest",
        original.sha256,
    ])

    assert args.func(args) == 5
    assert "changed while trust was being recorded" in capsys.readouterr().err
    assert WorkflowTrustStore(profile).check(original.sha256) == "untrusted"


def test_run_status_events_and_runs_sanitize_the_durable_store(
    workflow_writer, tmp_path, capsys
):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    args = parser.parse_args([
        *common,
        "run",
        "sample",
        "--idempotency-key",
        "message-1",
        "--foreground",
        "--json",
    ])
    assert args.func(args) == 0
    run_envelope = _json_envelope(capsys)
    run = run_envelope["result"]
    assert run["status"] == "succeeded"
    assert "operator_scope_digest" not in json.dumps(run_envelope)
    assert "idempotency_key_digest" not in json.dumps(run_envelope)

    RunStore(profile).append_event(
        run["run_id"],
        "diagnostic",
        {
            "items": list(range(250)),
            "operator_scope_digest": "scope-secret",
            "idempotency_key_digest": "intent-secret",
        },
    )

    args = parser.parse_args([*common, "status", run["run_id"], "--json"])
    assert args.func(args) == 0
    status = _json_result(capsys)
    assert status["run_id"] == run["run_id"]
    assert status["truncated"] is False
    assert status["next_cursor"] is None
    assert "operator_scope_digest" not in json.dumps(status)
    assert "idempotency_key_digest" not in json.dumps(status)

    args = parser.parse_args([
        *common,
        "events",
        run["run_id"],
        "--tail",
        "2",
        "--json",
    ])
    assert args.func(args) == 0
    events = _json_result(capsys)
    assert len(events["events"]) == 2
    assert events["truncated"] is True
    assert events["next_cursor"] == events["events"][-1]["sequence"]
    assert events["events"][-1]["payload_truncated"] is True
    assert len(events["events"][-1]["payload"]["items"]) == 200
    assert "operator_scope_digest" not in json.dumps(events)
    assert "idempotency_key_digest" not in json.dumps(events)

    args = parser.parse_args([*common, "runs", "--status", "succeeded", "--json"])
    assert args.func(args) == 0
    runs = _json_result(capsys)
    assert runs["runs"][0]["run_id"] == run["run_id"]
    assert runs["truncated"] is False
    assert runs["next_cursor"] is None
    assert "operator_scope_digest" not in json.dumps(runs)
    assert "idempotency_key_digest" not in json.dumps(runs)

    args = parser.parse_args([
        *common,
        "archive",
        run["run_id"],
        "--expected-version",
        "-1",
        "--json",
    ])
    assert args.func(args) == 5
    conflict = _json_envelope(capsys)
    assert conflict["error"]["code"] == "version_conflict"
    assert conflict["error"]["retryable"] is True


def test_foreground_cli_releases_at_gate_and_claims_new_epoch_for_continue(
    workflow_writer, tmp_path, capsys
) -> None:
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="foreground-gate",
        nodes=[
            {"id": "gate", "approval": {"message": "Continue?"}},
            {"id": "finish", "bash": "true", "depends_on": ["gate"]},
        ],
    )
    profile = tmp_path / "profile"
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    start_args = parser.parse_args([
        *common,
        "run",
        "foreground-gate",
        "--idempotency-key",
        "foreground-gate",
        "--foreground",
        "--json",
    ])
    assert start_args.func(start_args) == 0
    paused = _json_result(capsys)
    assert paused["status"] == "paused"
    assert datetime.fromisoformat(
        paused["foreground_lease_expires_at"]
    ) <= datetime.now(timezone.utc)

    approve_args = parser.parse_args([
        *common,
        "approve",
        paused["run_id"],
        "--interaction-id",
        paused["pending_interaction"]["interaction_id"],
        "--expected-version",
        str(paused["state_version"]),
        "--continue",
        "--json",
    ])
    assert approve_args.func(approve_args) == 0
    completed = _json_result(capsys)
    assert completed["run_status"] == "succeeded"
    projection = RunStore(profile).load_run(paused["run_id"])
    assert projection["foreground_epoch"] == 2


@pytest.mark.parametrize("as_json", [False, True])
def test_foreground_cli_reports_background_coordinator_adoption(
    workflow_writer, tmp_path, capsys, monkeypatch, as_json
) -> None:
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="foreground-adoption-notice",
        nodes=[{"id": "finish", "bash": "true"}],
    )
    profile = tmp_path / ("json-profile" if as_json else "human-profile")
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )

    def adopting_scheduler(store, _runtime, **_kwargs):
        class AdoptOnAdvance:
            def advance(self, run_id):
                current = store.load_run(run_id)
                released_at = datetime.now(timezone.utc)
                assert store.release_foreground_execution(
                    run_id,
                    owner_id=current["foreground_owner_id"],
                    epoch=current["foreground_epoch"],
                    now=released_at,
                )
                expired_at = released_at + timedelta(microseconds=1)
                identity = CoordinatorIdentity(
                    owner_id="cli-adoption-test",
                    host_kind="web",
                    host_instance_id="cli-adoption-test",
                    pid=1,
                    process_start_time=None,
                )
                acquired = CoordinatorStore(store.database).try_acquire(
                    identity,
                    now=expired_at,
                    lease_seconds=60,
                )
                assert acquired.is_leader
                return store.adopt_expired_foreground(
                    run_id,
                    ExecutionFence(identity.owner_id, acquired.lease.epoch),
                    expired_at,
                )

        return AdoptOnAdvance()

    monkeypatch.setattr("plugins.workflow.cli._scheduler", adopting_scheduler)
    argv = [
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(profile),
        "run",
        "foreground-adoption-notice",
        "--idempotency-key",
        "foreground-adoption-notice",
        "--foreground",
    ]
    if as_json:
        argv.append("--json")
    args = _parser().parse_args(argv)

    assert args.func(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if as_json:
        envelope = json.loads(output.out)
        assert envelope["result"]["execution_mode"] == "background"
        assert envelope["result"]["execution_handoff"]["transition"] == (
            "foreground_execution_adopted"
        )
    else:
        run_id = RunStore(profile).list_runs()[0]["run_id"]
        assert "adopted by the background coordinator and continues" in output.out
        assert f"workflow status {run_id}" in output.out


def test_run_refuses_trusted_package_that_requires_an_isolated_backend(
    workflow_writer, tmp_path, capsys
):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "execution_environment: isolated_backend_required\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(profile),
        "run",
        "sample",
        "--json",
    ])

    assert args.func(args) == machine_contract.EXIT_AUTHORIZATION
    assert _json_envelope(capsys)["error"]["code"] == "trust_required"
    assert RunStore(profile).list_runs() == ()


def test_reset_sessions_requires_confirmation_for_cross_scope_reset(tmp_path, capsys):
    profile = tmp_path / "profile"
    registry = NodeSessionRegistry(profile)
    key = NodeSessionKey("sample", "analyze", "scope-a", "provider", "default")
    registry.compare_and_set(key, 0, "session", "fingerprint")
    parser = _parser()
    common = ["--hermes-home", str(profile), "reset-sessions", "sample"]

    args = parser.parse_args([*common, "--json"])
    assert args.func(args) == 2
    assert _json_envelope(capsys)["error"]["code"] == "confirmation_required"

    args = parser.parse_args([*common, "--scope", "scope-a", "--json"])
    assert args.func(args) == 0
    assert _json_result(capsys)["removed"] == 1


def test_json_failures_use_stable_envelopes_and_exit_categories(
    tmp_path, capsys, monkeypatch
) -> None:
    parser = _parser()
    common = ["--hermes-home", str(tmp_path / "profile")]

    missing = parser.parse_args([*common, "status", "missing-run", "--json"])
    assert missing.func(missing) == 3
    envelope = _json_envelope(capsys)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is False
    assert envelope["command"] == "workflow status"
    assert envelope["error"]["code"] == "not_found"

    invalid = parser.parse_args([
        *common,
        "events",
        "missing-run",
        "--tail",
        "0",
        "--json",
    ])
    assert invalid.func(invalid) == 2
    assert _json_envelope(capsys)["error"]["code"] == "invalid_request"

    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(
            machine_contract.WorkflowConflict("stale state version")
        ),
    )
    conflict = parser.parse_args([*common, "status", "run", "--json"])
    assert conflict.func(conflict) == 5
    conflict_envelope = _json_envelope(capsys)
    assert conflict_envelope["error"]["code"] == "version_conflict"
    assert conflict_envelope["error"]["retryable"] is True

    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(TypeError("secret internal detail")),
    )
    internal = parser.parse_args([*common, "status", "run", "--json"])
    assert internal.func(internal) == 70
    internal_envelope = _json_envelope(capsys)
    assert internal_envelope["error"]["code"] == "internal_error"
    assert "secret internal detail" not in json.dumps(internal_envelope)


def test_json_argparse_failure_uses_one_stdout_envelope(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        _parser().parse_args(["run", "sample", "--json", "--bogus"])

    assert exited.value.code == 2
    envelope = _json_envelope(capsys)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is False
    assert envelope["command"] == "workflow run"
    assert envelope["error"]["code"] == "invalid_request"


def test_top_level_json_parse_error_uses_one_stdout_envelope(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        _parser().parse_args(["bogus", "--json"])

    assert exited.value.code == 2
    output = capsys.readouterr()
    assert output.err == ""
    envelope = json.loads(output.out)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"


def test_os_error_is_sanitized(tmp_path, capsys, monkeypatch) -> None:
    private_path = "/private/profile/workflows/admission.sqlite3"
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(OSError(private_path)),
    )
    args = _parser().parse_args([
        "--hermes-home",
        str(tmp_path / "profile"),
        "status",
        "run",
        "--json",
    ])

    assert args.func(args) == machine_contract.EXIT_ACTION_FAILED
    output = capsys.readouterr()
    assert output.err == ""
    assert private_path not in output.out
    envelope = json.loads(output.out)
    assert envelope["error"] == {
        "code": "action_failed",
        "message": "workflow storage operation failed",
        "retryable": False,
        "details": {"exception_type": "OSError"},
    }


def test_runs_json_reports_sanitized_keyset_truncation(
    tmp_path, capsys, workflow_writer
) -> None:
    profile = tmp_path / "profile"
    package = load_workflow(workflow_writer(tmp_path / "package", name="runs-page"))
    store = RunStore(
        profile,
        max_executing_runs=10,
        max_nonterminal_runs=10,
        max_start_requests_per_minute=10,
    )
    for index in range(3):
        prepared = store.prepare_run_snapshot(package)
        store.start_run(
            RunAdmissionRequest(
                workflow_name="runs-page",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key=f"runs-page-{index}",
                concurrency_key=f"runs-page-{index}",
                concurrency_policy="allow",
            ),
            immutable_snapshot=prepared,
        )
    args = _parser().parse_args([
        "--hermes-home",
        str(profile),
        "runs",
        "--limit",
        "2",
        "--json",
    ])

    assert args.func(args) == 0
    page = _json_result(capsys)
    assert len(page["runs"]) == 2
    assert page["truncated"] is True
    assert page["next_cursor"] == [
        page["runs"][-1]["updated_at"],
        page["runs"][-1]["run_id"],
    ]
    assert "operator_scope_digest" not in json.dumps(page)
    assert "idempotency_key_digest" not in json.dumps(page)


@pytest.mark.parametrize(
    ("exception_name", "expected_code", "expected_exit", "retryable"),
    [
        ("WorkflowNotFound", "not_found", 3, False),
        ("WorkflowAuthorization", "authorization_required", 4, False),
        ("WorkflowConflict", "version_conflict", 5, True),
        ("CoordinatorUnavailable", "coordinator_unavailable", 6, True),
        ("WorkflowActionFailed", "action_failed", 8, False),
    ],
)
def test_typed_domain_failures_map_without_message_classification(
    tmp_path,
    capsys,
    monkeypatch,
    exception_name,
    expected_code,
    expected_exit,
    retryable,
) -> None:
    exception_type = getattr(machine_contract, exception_name)
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(exception_type("domain detail")),
    )
    args = _parser().parse_args([
        "--hermes-home",
        str(tmp_path / "profile"),
        "status",
        "run",
        "--json",
    ])

    assert args.func(args) == expected_exit
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is retryable


@pytest.mark.parametrize("error", [KeyError(), KeyError("internal-lookup")])
def test_internal_key_errors_use_typed_internal_failure_not_not_found(
    tmp_path, capsys, monkeypatch, error
) -> None:
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(error),
    )
    args = _parser().parse_args([
        "--hermes-home",
        str(tmp_path / "profile"),
        "status",
        "run",
        "--json",
    ])

    assert args.func(args) == 70
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == "internal_error"
    assert "internal-lookup" not in json.dumps(envelope)


def test_untyped_runtime_error_is_internal_even_when_message_says_conflict(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "plugins.workflow.cli._cmd_status",
        lambda _args: (_ for _ in ()).throw(
            RuntimeError("diagnostic conflict text is not a typed CAS conflict")
        ),
    )
    args = _parser().parse_args([
        "--hermes-home",
        str(tmp_path / "profile"),
        "status",
        "run",
        "--json",
    ])

    assert args.func(args) == 70
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == "internal_error"
    assert envelope["error"]["retryable"] is False
    assert "diagnostic conflict" not in json.dumps(envelope)


def test_invalid_workflow_validation_uses_documented_blocking_exit(
    workflow_writer, tmp_path, capsys, monkeypatch
) -> None:
    workdir = tmp_path / "repo"
    _write(workflow_writer, workdir)
    monkeypatch.setattr(
        "plugins.workflow.cli.validate_package",
        lambda _package: (
            ValidationIssue("nodes", "invalid", "invalid workflow", blocking=True),
            ValidationIssue("nodes", "invalid", "invalid workflow", blocking=True),
        ),
    )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "validate",
        "sample",
        "--json",
    ])

    assert args.func(args) == 7
    envelope = _json_envelope(capsys)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "validation_failed"
    identities = {
        (issue["code"], issue["path"])
        for issue in envelope["result"]["issues"]
    }
    assert len(envelope["result"]["issues"]) == len(identities)


def test_machine_start_requires_stable_key_and_background_owner(
    workflow_writer, tmp_path, capsys
) -> None:
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    package = load_workflow(path)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        digest.sha256, actor="test", risk_digest=risk.risk_digest
    )
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    keyless = parser.parse_args([
        *common,
        "run",
        "sample",
        "--foreground",
        "--json",
    ])
    assert keyless.func(keyless) == 2
    assert _json_envelope(capsys)["error"]["code"] == "idempotency_key_required"

    background = parser.parse_args([
        *common,
        "run",
        "sample",
        "--idempotency-key",
        "stable-background-key",
        "--no-wait",
        "--json",
    ])
    assert background.func(background) == 6
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == "coordinator_unavailable"
    assert list(RunStore(profile).runs_root.glob("*/*")) == []


def test_not_found_candidates_are_bounded_safe_and_deterministic(
    workflow_writer, tmp_path, capsys
) -> None:
    workdir = tmp_path / "repo"
    for index in reversed(range(12)):
        workflow_writer(
            workdir / ".hermes" / "workflows" / f"package-{index:02d}",
            name=f"workflow-{index:02d}",
        )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "show",
        "missing",
        "--json",
    ])

    assert args.func(args) == 3
    envelope = _json_envelope(capsys)
    candidates = envelope["error"]["details"]["candidates"]
    assert len(candidates) == 10
    assert candidates == sorted(candidates, key=lambda item: item["id"])
    assert set(candidates[0]) == {"id", "kind", "label"}
    assert {candidate["kind"] for candidate in candidates} == {"workflow"}


def test_doctor_json_is_nonzero_when_findings_block_local_execution(
    workflow_writer, tmp_path, capsys
) -> None:
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "execution_environment: isolated_backend_required\n",
        encoding="utf-8",
    )
    args = _parser().parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(tmp_path / "profile"),
        "doctor",
        "sample",
        "--mode",
        "foreground",
        "--compat-report",
        "--json",
    ])

    assert args.func(args) == 7
    envelope = _json_envelope(capsys)
    assert envelope["error"]["code"] == "blocking_doctor_findings"
    assert envelope["result"]["runnable"] is False
    assert envelope["result"]["coordinator"]["status"] == "unavailable"
