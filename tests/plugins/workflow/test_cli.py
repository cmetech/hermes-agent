from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pytest

from plugins import workflow as workflow_plugin
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _runtime_config, register_cli
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import NodeSessionKey, NodeSessionRegistry
from plugins.workflow.store import RunStore
from plugins.workflow.trust import build_risk_summary, compute_package_digest
from plugins.workflow.trust import WorkflowPackageDigest, WorkflowTrustStore
from plugins.workflow.compat import assess_compatibility


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def _write(workflow_writer, workdir):
    return workflow_writer(
        workdir / ".hermes" / "workflows",
        name="sample",
        nodes=[{"id": "start", "bash": "printf SECRET_BODY"}],
    )


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
    listing = json.loads(capsys.readouterr().out)
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
    detail = json.loads(capsys.readouterr().out)
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
    assert "--topology cannot be combined with --json" in capsys.readouterr().err


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
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cleanup-cli")
    )
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
    preview = json.loads(capsys.readouterr().out)
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
    executed = json.loads(capsys.readouterr().out)
    assert executed["execute"] is True
    assert store.list_runs() == ()


def test_validate_doctor_trust_and_untrust(workflow_writer, tmp_path, capsys):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    args = parser.parse_args([*common, "validate", "sample", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    args = parser.parse_args([*common, "doctor", "sample", "--compat-report", "--json"])
    assert args.func(args) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["package_digest"]
    assert "SECRET_BODY" not in json.dumps(doctor)

    args = parser.parse_args([
        *common,
        "trust",
        "sample",
        "--digest",
        "0" * 64,
        "--json",
    ])
    assert args.func(args) == 1
    assert "digest does not match" in capsys.readouterr().err

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
    assert json.loads(capsys.readouterr().out)["status"] == "trusted"

    args = parser.parse_args([*common, "untrust", "sample", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "untrusted"


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

    assert args.func(args) == 1
    assert "changed while trust was being recorded" in capsys.readouterr().err
    assert WorkflowTrustStore(profile).check(original.sha256) == "untrusted"


def test_run_status_events_and_runs_use_the_durable_store(
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
        "--json",
    ])
    assert args.func(args) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["status"] == "succeeded"

    args = parser.parse_args([*common, "status", run["run_id"], "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == run["run_id"]

    args = parser.parse_args([
        *common,
        "events",
        run["run_id"],
        "--tail",
        "2",
        "--json",
    ])
    assert args.func(args) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2

    args = parser.parse_args([*common, "runs", "--status", "succeeded", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["run_id"] == run["run_id"]


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

    assert args.func(args) == 1
    assert "requires a configured isolated backend" in capsys.readouterr().err
    assert RunStore(profile).list_runs() == ()


def test_reset_sessions_requires_confirmation_for_cross_scope_reset(tmp_path, capsys):
    profile = tmp_path / "profile"
    registry = NodeSessionRegistry(profile)
    key = NodeSessionKey("sample", "analyze", "scope-a", "provider", "default")
    registry.compare_and_set(key, 0, "session", "fingerprint")
    parser = _parser()
    common = ["--hermes-home", str(profile), "reset-sessions", "sample"]

    args = parser.parse_args([*common, "--json"])
    assert args.func(args) == 1
    assert "--yes" in capsys.readouterr().err

    args = parser.parse_args([*common, "--scope", "scope-a", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == 1
