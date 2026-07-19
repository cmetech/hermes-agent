from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from plugins import workflow as workflow_plugin
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _runtime_config, register_cli
from plugins.workflow import machine_contract
from plugins.workflow.models import ValidationIssue
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


def test_validate_doctor_trust_and_untrust(workflow_writer, tmp_path, capsys):
    workdir = tmp_path / "repo"
    path = _write(workflow_writer, workdir)
    profile = tmp_path / "profile"
    parser = _parser()
    common = ["--workdir", str(workdir), "--hermes-home", str(profile)]

    args = parser.parse_args([*common, "validate", "sample", "--json"])
    assert args.func(args) == 0
    assert _json_result(capsys)["valid"] is True

    args = parser.parse_args([*common, "doctor", "sample", "--compat-report", "--json"])
    assert args.func(args) == 0
    doctor = _json_result(capsys)
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
    assert datetime.fromisoformat(paused["foreground_lease_expires_at"]) <= datetime.now(
        timezone.utc
    )

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

    assert args.func(args) == 4
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

    invalid = parser.parse_args([*common, "events", "missing-run", "--tail", "0", "--json"])
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


def test_runs_json_reports_sanitized_keyset_truncation(
    tmp_path, capsys, workflow_writer
) -> None:
    profile = tmp_path / "profile"
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="runs-page")
    )
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
