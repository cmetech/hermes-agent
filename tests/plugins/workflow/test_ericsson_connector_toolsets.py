from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.schema import parse_workflow_source_bytes
from tests.ericsson_connector_source import resolve_ericsson_connector_source


def _real_jira_to_gitlab_compilation(tmp_path):
    source = resolve_ericsson_connector_source()
    source_path = source.workflow
    sidecar_path = source.workflow_sidecar
    workflow_root = tmp_path / "real-source" / "workflows"
    workflow_root.mkdir(parents=True)
    copied = workflow_root / source_path.name
    copied.write_bytes(source_path.read_bytes())
    copied_sidecar = workflow_root / sidecar_path.name
    copied_sidecar.write_bytes(sidecar_path.read_bytes())
    source = parse_workflow_source_bytes(
        copied,
        workflow_bytes=copied.read_bytes(),
        sidecar_bytes=copied_sidecar.read_bytes(),
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _real_jira_showcase_compilation(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "capabilities" / "workflows" / "jira-single-ticket-showcase.yml"
    sidecar_path = source_path.with_name("jira-single-ticket-showcase.hermes.yaml")
    workflow_root = tmp_path / "real-jira-source" / "workflows"
    workflow_root.mkdir(parents=True)
    copied = workflow_root / source_path.name
    copied.write_bytes(source_path.read_bytes())
    copied_sidecar = workflow_root / sidecar_path.name
    copied_sidecar.write_bytes(sidecar_path.read_bytes())
    source = parse_workflow_source_bytes(
        copied,
        workflow_bytes=copied.read_bytes(),
        sidecar_bytes=copied_sidecar.read_bytes(),
        source="ericsson",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _real_sharepoint_compilation(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "capabilities" / "workflows" / "sharepoint-document-intake.yml"
    )
    sidecar_path = source_path.with_name("sharepoint-document-intake.hermes.yaml")
    workflow_root = tmp_path / "real-sharepoint-source" / "workflows"
    workflow_root.mkdir(parents=True)
    copied = workflow_root / source_path.name
    copied.write_bytes(source_path.read_bytes())
    copied_sidecar = workflow_root / sidecar_path.name
    copied_sidecar.write_bytes(sidecar_path.read_bytes())
    source = parse_workflow_source_bytes(
        copied,
        workflow_bytes=copied.read_bytes(),
        sidecar_bytes=copied_sidecar.read_bytes(),
        source="ericsson",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def test_real_sharepoint_intake_has_exact_tools_and_ready_unready_admission(tmp_path):
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _real_sharepoint_compilation(tmp_path)
    package = compilation.package
    exact_tools = frozenset(
        tool
        for node in package.definition.nodes
        for tool in node.options.get("allowed_tools", ())
    )
    assert package.definition.options["requires"] == ("ericsson-sharepoint",)
    assert exact_tools == {
        "sharepoint_resolve_url",
        "sharepoint_list_items",
        "sharepoint_download",
    }

    blocked = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset(),
        available_tools=exact_tools,
    )
    admitted = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset({"ericsson-sharepoint"}),
        available_tools=exact_tools,
    )
    missing_tool = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset({"ericsson-sharepoint"}),
        available_tools=exact_tools - {"sharepoint_download"},
    )
    assert blocked.compatibility.runnable is False
    assert admitted.compatibility.runnable is True
    assert missing_tool.compatibility.runnable is False


def test_real_jira_showcase_has_exact_tools_and_ready_unready_admission(tmp_path):
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _real_jira_showcase_compilation(tmp_path)
    package = compilation.package
    exact_tools = frozenset(
        tool
        for node in package.definition.nodes
        for tool in node.options.get("allowed_tools", ())
    )
    assert package.definition.options["requires"] == ("ericsson-jira",)
    assert exact_tools == {"jira_get_issue", "jira_add_comment"}

    blocked = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset(),
        available_tools=frozenset(),
    )
    admitted = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset({"ericsson-jira"}),
        available_tools=exact_tools,
    )
    assert blocked.compatibility.runnable is False
    assert admitted.compatibility.runnable is True


def test_real_jira_showcase_blocks_before_run_creation_when_unready(
    tmp_path, monkeypatch
):
    import plugins.workflow.cli as cli_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.compat import WorkflowCompatibilityBlockedError
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _real_jira_showcase_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(cli_module, "_resolve_compilation", lambda *_a: compilation)
    monkeypatch.setattr(cli_module, "_runtime_config", lambda *_a, **_k: object())
    monkeypatch.setattr(cli_module, "connector_capability_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate, _context(), **availability
        ),
    )
    monkeypatch.setattr(
        cli_module.WorkflowTrustStore,
        "check",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("trust/store path ran before Jira connector admission")
        ),
    )

    with pytest.raises(WorkflowCompatibilityBlockedError):
        cli_module._cmd_run(
            argparse.Namespace(
                name="jira-single-ticket-showcase",
                hermes_home=str(tmp_path / "home"),
            )
        )


def _connector_compilation(tmp_path):
    root = tmp_path / "source" / "workflows"
    root.mkdir(parents=True)
    workflow_path = root / "connector-workflow.yaml"
    workflow_bytes = b"""name: connector-workflow
description: Inspect a configured connector project.
requires:
  - ericsson-gitlab
nodes:
  - id: inspect
    prompt: Inspect the configured GitLab project.
    allowed_tools:
      - gitlab_read_file
"""
    sidecar_bytes = b"language_compatibility: archon-2026-07\n"
    workflow_path.write_bytes(workflow_bytes)
    sidecar_path = root / "connector-workflow.hermes.yaml"
    sidecar_path.write_bytes(sidecar_bytes)
    source = parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _empty_allowed_tools_compilation(tmp_path):
    compilation = _connector_compilation(tmp_path)
    workflow_path = compilation.package.workflow_path
    workflow_bytes = workflow_path.read_bytes().replace(
        b"allowed_tools:\n      - gitlab_read_file",
        b"allowed_tools: []",
    )
    workflow_path.write_bytes(workflow_bytes)
    sidecar_path = workflow_path.with_name("connector-workflow.hermes.yaml")
    sidecar_bytes = sidecar_path.read_bytes()
    source = parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _pre_extension_compilation(tmp_path):
    compilation = _connector_compilation(tmp_path)
    workflow_path = compilation.package.workflow_path
    workflow_bytes = workflow_path.read_bytes().replace(
        b"requires:\n  - ericsson-gitlab\n",
        b"",
    )
    workflow_path.write_bytes(workflow_bytes)
    sidecar_path = workflow_path.with_name("connector-workflow.hermes.yaml")
    sidecar_bytes = sidecar_path.read_bytes()
    source = parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _two_node_connector_compilation(tmp_path):
    compilation = _connector_compilation(tmp_path)
    workflow_path = compilation.package.workflow_path
    workflow_bytes = (
        workflow_path.read_bytes()
        + b"""  - id: summarize
    depends_on:
      - inspect
    prompt: Summarize the configured GitLab project.
    allowed_tools:
      - gitlab_read_file
"""
    )
    workflow_path.write_bytes(workflow_bytes)
    sidecar_path = workflow_path.with_name("connector-workflow.hermes.yaml")
    sidecar_bytes = sidecar_path.read_bytes()
    source = parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def test_flat_connector_contract_compiles_before_availability_is_assessed(tmp_path):
    compilation = _connector_compilation(tmp_path)

    assert compilation.package.definition.options["requires"] == ("ericsson-gitlab",)
    assert compilation.package.definition.nodes[0].options["allowed_tools"] == (
        "gitlab_read_file",
    )


def test_real_archon_package_blocks_unready_connector_and_preserves_empty_tool_nodes(
    tmp_path,
):
    """Task 12: real source bytes use Task 7's backend-authored admission facts."""
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _real_jira_to_gitlab_compilation(tmp_path)
    all_declared_tools = frozenset(
        tool
        for node in compilation.package.definition.nodes
        for tool in node.options.get("allowed_tools", ())
    )

    blocked = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset({"ericsson-jira"}),
        available_tools=all_declared_tools,
    )
    admitted = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset({"ericsson-jira", "ericsson-gitlab"}),
        available_tools=all_declared_tools,
    )

    assert any(
        finding.code == "required_service"
        and finding.path == "requires[0]"
        for finding in blocked.compatibility.blocking_findings
    )
    assert not any(
        finding.code in {"required_service", "unavailable_tool"}
        for finding in admitted.compatibility.blocking_findings
    )
    reason = next(
        node
        for node in compilation.package.definition.nodes
        if node.id == "reason-about-fix"
    )
    assert reason.options["allowed_tools"] == ()


def test_real_gitlab_write_receives_only_host_minted_current_invocation_admission(
    tmp_path, monkeypatch
):
    """Workflow-authored args cannot mint or replace the backend admission fact."""
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugin_configuration import PluginConfigurationService
    from hermes_cli.plugins import PluginManager
    from model_tools import handle_function_call
    from tools.registry import registry

    source = resolve_ericsson_connector_source()
    home = tmp_path / "profile"
    plugin_root = home / "plugins" / "ericsson-gitlab"
    plugin_root.parent.mkdir(parents=True)
    shutil.copytree(source.plugin, plugin_root)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["ericsson-gitlab"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)
    PluginConfigurationService(manager).update(
        "ericsson-gitlab",
        settings={"origin": "https://gitlab.example.test"},
        secrets={"pat": "profile-token"},
    )
    module = manager._plugins["ericsson-gitlab"].module
    assert module is not None
    captured = {}
    monkeypatch.setattr(
        module.gitlab_tools,
        "invoke",
        lambda name, args, configuration, **kwargs: captured.update(
            name=name, args=args, configuration=configuration, kwargs=kwargs
        )
        or {"status": "created", "branch_name": "fix/ERIC-123-safe"},
    )
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True, "message": None},
    )

    schema = registry._tools["gitlab_create_branch"].schema
    assert "approved" not in schema["parameters"].get("properties", {})
    result = json.loads(
        handle_function_call(
            "gitlab_create_branch",
            {
                "project": "group/project",
                "source_branch": "main",
                "prefix": "fix",
                "ticket_key": "ERIC-123",
                "summary": "Safe change",
                "dry_run": False,
            },
            enabled_toolsets=["ericsson-gitlab"],
            tool_call_id="workflow-write-1",
            turn_id="workflow-turn-1",
        )
    )

    assert result["success"] is True
    assert captured["name"] == "gitlab_create_branch"
    assert "tool_admission" not in captured["args"]
    assert "approved" not in captured["args"]
    # The plugin consumed the host-minted admission before invoking its backend.
    assert captured["kwargs"].keys() == {"cancel_check"}

    for name in tuple(registry.get_all_tool_names()):
        if name.startswith("gitlab_"):
            registry.deregister(name)


def test_cli_admission_uses_the_active_connector_capability_snapshot(
    tmp_path, monkeypatch
):
    import plugins.workflow.cli as cli_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _connector_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(
        cli_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate,
            _context(),
            **availability,
        ),
    )

    assessment = cli_module._phase5_admission_assessment(compilation)

    assert assessment is not None
    assert assessment.compatibility.runnable is False
    assert {
        (finding.path, finding.code)
        for finding in assessment.compatibility.blocking_findings
    } == {
        ("requires[0]", "required_service"),
        ("nodes[0].allowed_tools[0]", "unavailable_tool"),
    }
    assert assessment.next_actions == ("doctor",)


def test_public_cli_list_uses_one_connector_capability_snapshot(
    tmp_path, monkeypatch, capsys
):
    import plugins.workflow.cli as cli_module

    compilation = _connector_compilation(tmp_path)
    snapshots = []

    def missing_capabilities():
        snapshot = SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="0" * 64,
        )
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(cli_module, "_discover", lambda _args: (compilation.package,))
    monkeypatch.setattr(
        cli_module, "connector_capability_snapshot", missing_capabilities
    )
    args = argparse.Namespace(workflow_action="list", json=True)

    assert cli_module.workflow_command(args) == 0

    envelope = json.loads(capsys.readouterr().out)
    assert len(snapshots) == 1
    assert envelope["result"][0]["runnable"] is False
    assert envelope["result"][0]["compatibility"]["blocking_count"] >= 2


def test_public_cli_doctor_uses_one_connector_capability_snapshot_and_vocabulary(
    tmp_path, monkeypatch, capsys
):
    import plugins.workflow.cli as cli_module

    compilation = _connector_compilation(tmp_path)
    snapshots = []

    def missing_capabilities():
        snapshot = SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="0" * 64,
        )
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(
        cli_module, "_resolve", lambda _args, _name: compilation.package
    )
    monkeypatch.setattr(
        cli_module, "_resolve_compilation", lambda _args, _name: compilation
    )
    monkeypatch.setattr(
        cli_module, "connector_capability_snapshot", missing_capabilities
    )
    args = argparse.Namespace(
        workflow_action="doctor",
        name=compilation.package.definition.name,
        hermes_home=str(tmp_path / "home"),
        compat_report=True,
        mode=None,
        json=True,
    )

    assert cli_module.workflow_command(args) == 7

    envelope = json.loads(capsys.readouterr().out)
    findings = envelope["result"]["findings"]
    assert len(snapshots) == 1
    assert envelope["error"]["code"] == "blocking_doctor_findings"
    assert {
        (finding["path"], finding["code"])
        for finding in findings
        if finding["blocking"]
    } >= {
        ("requires[0]", "required_service"),
        ("nodes[0].allowed_tools[0]", "unavailable_tool"),
    }


def test_cli_admission_accepts_the_exact_ready_service_and_registered_tool(
    tmp_path, monkeypatch
):
    import plugins.workflow.cli as cli_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _connector_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset({"ericsson-gitlab"}),
        available_tools=frozenset({"gitlab_read_file"}),
        fingerprint="1" * 64,
    )
    monkeypatch.setattr(
        cli_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate,
            _context(),
            **availability,
        ),
    )

    assessment = cli_module._phase5_admission_assessment(compilation)

    assert assessment is not None
    assert assessment.compatibility.runnable is True
    assert assessment.next_actions == ("run",)


def test_explicit_empty_allowed_tools_remains_exactly_empty(tmp_path, monkeypatch):
    import plugins.workflow.cli as cli_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _empty_allowed_tools_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset({"ericsson-gitlab"}),
        available_tools=frozenset({"gitlab_read_file"}),
        fingerprint="1" * 64,
    )
    monkeypatch.setattr(
        cli_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate,
            _context(),
            **availability,
        ),
    )

    assessment = cli_module._phase5_admission_assessment(compilation)

    assert compilation.package.definition.nodes[0].options["allowed_tools"] == ()
    assert assessment is not None
    assert assessment.compatibility.runnable is True


def test_cli_run_blocks_connector_omissions_before_trust_or_run_creation(
    tmp_path, monkeypatch
):
    import plugins.workflow.cli as cli_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.compat import WorkflowCompatibilityBlockedError
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _connector_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(cli_module, "_resolve_compilation", lambda *_a: compilation)
    monkeypatch.setattr(cli_module, "_runtime_config", lambda *_a, **_k: object())
    monkeypatch.setattr(
        cli_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate,
            _context(),
            **availability,
        ),
    )
    monkeypatch.setattr(
        cli_module.WorkflowTrustStore,
        "check",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("trust must not run before connector admission")
        ),
    )

    with pytest.raises(WorkflowCompatibilityBlockedError) as error:
        cli_module._cmd_run(
            argparse.Namespace(
                name="connector-workflow",
                hermes_home=str(tmp_path / "home"),
            )
        )

    assert {
        (finding.path, finding.code) for finding in error.value.report.blocking_findings
    } == {
        ("requires[0]", "required_service"),
        ("nodes[0].allowed_tools[0]", "unavailable_tool"),
    }


def test_rest_admission_blocks_connector_omissions_before_trust_or_run_creation(
    tmp_path, monkeypatch
):
    import plugins.workflow.api_admission as api_module
    from plugins.workflow.api_admission import ApiAdmissionAuthority, ApiAdmissionError
    from plugins.workflow.store import RunStore
    from tests.plugins.workflow.test_phase5_admission_parity import _binding

    compilation = _connector_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(
        api_module,
        "_catalog_compilation",
        lambda *_a, **_k: compilation,
    )
    monkeypatch.setattr(
        api_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        api_module.WorkflowTrustStore,
        "snapshot_read_only",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("trust must not run before connector admission")
        ),
    )

    with pytest.raises(ApiAdmissionError) as error:
        api_module.start_api_run(
            RunStore(tmp_path / "home"),
            hermes_home=tmp_path / "home",
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="connector-workflow",
            values={},
            idempotency_key="connector-rest-red",
            concurrency_policy="queue",
            authority=ApiAdmissionAuthority(
                principal="desktop:user",
                namespace="desktop:user",
                operator_scope=None,
                source_instance="desktop:test",
                assurance="local_admin_claim",
                trigger_source="desktop",
            ),
            runner_binding=_binding(),
        )

    assert error.value.code == "workflow_compatibility_blocked"


def test_catalog_and_detail_project_the_same_connector_diagnostics(
    tmp_path, monkeypatch
):
    import plugins.workflow.catalog_api as catalog_module
    import plugins.workflow.showcase as showcase_module
    from tests.plugins.workflow.test_phase5_admission_parity import _binding

    compilation = _connector_compilation(tmp_path)
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )
    monkeypatch.setattr(
        catalog_module,
        "_discover_catalog_compilations",
        lambda *_a, **_k: ([compilation], False),
    )
    monkeypatch.setattr(
        catalog_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        showcase_module,
        "load_verified_showcase_packages",
        lambda **_k: {},
    )
    monkeypatch.setattr(
        catalog_module.WorkflowTrustStore,
        "snapshot_read_only",
        lambda *_a, **_k: {},
    )

    catalog, truncated = catalog_module.build_workflow_catalog(
        hermes_home=tmp_path / "home",
        workdir=tmp_path,
        runner_binding=_binding(),
    )
    detail = catalog_module.build_workflow_detail(
        "connector-workflow",
        hermes_home=tmp_path / "home",
        workdir=tmp_path,
        runner_binding=_binding(),
    )

    assert truncated is False
    assert catalog[0]["compatibility"] == {
        "level": "unsupported",
        "runnable": False,
    }
    assert {
        (finding["path"], finding["code"])
        for finding in detail["compatibility"]["findings"]
        if finding["blocking"]
    } == {
        ("requires[0]", "required_service"),
        ("nodes[0].allowed_tools[0]", "unavailable_tool"),
    }


def test_showcase_admission_blocks_connector_omissions_before_store_creation(
    tmp_path, monkeypatch
):
    import plugins.workflow.showcase as showcase_module
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.compat import WorkflowCompatibilityBlockedError
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _connector_compilation(tmp_path)
    base_scenario = showcase_module.load_showcase_catalog()["approval-gate"]
    scenario = replace(
        base_scenario,
        id="connector-showcase",
        package_digest=compilation.composite_digest,
    )
    admitted_without_availability = assess_workflow_admission(
        compilation,
        _context(),
    )
    snapshot = SimpleNamespace(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )

    @contextmanager
    def scenario_compilation(_scenario):
        yield compilation

    monkeypatch.setattr(
        showcase_module,
        "load_showcase_catalog",
        lambda: {scenario.id: scenario},
    )
    monkeypatch.setattr(
        showcase_module,
        "preflight_showcase",
        lambda *_a, **_k: {
            "confirmation_token": "token",
            "bundle_digest": "bundle",
        },
    )
    monkeypatch.setattr(showcase_module, "_scenario_compilation", scenario_compilation)
    monkeypatch.setattr(
        showcase_module,
        "_verified_distribution_risk",
        lambda *_a, **_k: admitted_without_availability.risk,
    )
    monkeypatch.setattr(
        showcase_module,
        "connector_capability_snapshot",
        lambda: snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        showcase_module,
        "assess_production_workflow_admission",
        lambda candidate, **availability: assess_workflow_admission(
            candidate,
            _context(),
            **{
                key: value
                for key, value in availability.items()
                if key in {"available_tools", "available_services"}
            },
        ),
    )
    monkeypatch.setattr(
        showcase_module,
        "_store",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("store must not run before connector admission")
        ),
    )

    with pytest.raises(WorkflowCompatibilityBlockedError) as error:
        showcase_module.run_showcase(
            scenario.id,
            hermes_home=tmp_path / "home",
        )

    assert {
        (finding.path, finding.code) for finding in error.value.report.blocking_findings
    } == {
        ("requires[0]", "required_service"),
        ("nodes[0].allowed_tools[0]", "unavailable_tool"),
    }


def _prepare_connector_snapshot(tmp_path):
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.store import RunStore
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _connector_compilation(tmp_path)
    capability_snapshot = SimpleNamespace(
        ready_services=frozenset({"ericsson-gitlab"}),
        available_tools=frozenset({"gitlab_read_file"}),
        fingerprint="2" * 64,
    )
    assessment = assess_workflow_admission(
        compilation,
        _context(),
        available_services=capability_snapshot.ready_services,
        available_tools=capability_snapshot.available_tools,
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=assessment.package_digest,
        provider_authority=assessment.provider_authority,
        connector_capabilities=capability_snapshot,
    )
    return store, compilation, prepared


def test_new_connector_snapshot_seals_only_identifiers_and_fingerprint(tmp_path):
    import json

    _store, _compilation, prepared = _prepare_connector_snapshot(tmp_path)

    resources_bytes = (prepared.staging_directory / "resources.json").read_bytes()
    resources = json.loads(resources_bytes)
    assert resources["connector_capabilities"] == {
        "schema_version": 1,
        "required_services": ["ericsson-gitlab"],
        "required_tools": ["gitlab_read_file"],
        "fingerprint": "2" * 64,
    }
    assert b"token" not in resources_bytes.lower()
    assert b"exception" not in resources_bytes.lower()


@pytest.mark.parametrize("entrypoint", ("advance", "advance_all"))
@pytest.mark.parametrize("snapshot_failure", (False, True), ids=("drift", "error"))
def test_scheduler_revalidates_connector_capabilities_before_the_next_claim(
    tmp_path, monkeypatch, entrypoint, snapshot_failure
):
    import plugins.workflow.scheduler as scheduler_module
    from plugins.workflow.admission import RunAdmissionRequest
    from plugins.workflow.scheduler import RunScheduler

    store, compilation, prepared = _prepare_connector_snapshot(tmp_path)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"connector-{entrypoint}",
            concurrency_key="connector-workflow",
        ),
        immutable_snapshot=prepared,
    )

    def live_capabilities(profile=None):
        if snapshot_failure:
            raise FileNotFoundError("active profile is unavailable")
        return SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="3" * 64,
        )

    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        live_capabilities,
        raising=False,
    )

    class RunnerTrap:
        def run(self, *_a, **_k):
            raise AssertionError("provider ran after connector capability drift")

    scheduler = RunScheduler(store, agent_runner=RunnerTrap())
    executor_calls = []
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_a, **_k: executor_calls.append(True),
    )
    try:
        if entrypoint == "advance":
            failed = scheduler.advance(admitted.run_id)
            replay = scheduler.advance(admitted.run_id)
        else:
            failed = scheduler.advance_all([admitted.run_id])[admitted.run_id]
            replay = scheduler.advance_all([admitted.run_id])[admitted.run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert failed["status"] == replay["status"] == "failed"
    assert (
        failed["last_error"]
        == replay["last_error"]
        == {
            "code": "connector_capability_changed",
            "path": "resources.connector_capabilities",
            "message": "connector capability changed after workflow admission",
        }
    )
    assert failed["event_sequence"] == replay["event_sequence"]
    assert executor_calls == []
    assert all(node.get("claim") is None for node in failed["nodes"].values())


def test_advance_all_defers_scheduled_drift_failure_until_active_provider_settles(
    tmp_path, monkeypatch
):
    import plugins.workflow.scheduler as scheduler_module
    from plugins.workflow.admission import RunAdmissionRequest
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.scheduler import RunScheduler
    from plugins.workflow.store import RunStore
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _two_node_connector_compilation(tmp_path)
    admitted_snapshot = SimpleNamespace(
        ready_services=frozenset({"ericsson-gitlab"}),
        available_tools=frozenset({"gitlab_read_file"}),
        fingerprint="2" * 64,
    )
    assessment = assess_workflow_admission(
        compilation,
        _context(),
        available_services=admitted_snapshot.ready_services,
        available_tools=admitted_snapshot.available_tools,
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=assessment.package_digest,
        provider_authority=assessment.provider_authority,
        connector_capabilities=admitted_snapshot,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="scheduled-active-provider-drift",
            concurrency_key="connector-workflow",
            run_metadata={"schedule_at": "2000-01-01T00:00:00Z"},
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.disposition == "queued"
    assert (
        store.try_promote_run(
            admitted.run_id,
            now=datetime.now(timezone.utc),
        )
        is True
    )

    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        lambda profile=None: SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="3" * 64,
        ),
    )
    scheduler = RunScheduler(store, agent_runner=object(), max_parallel_nodes=2)
    executor_calls = []
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_a, **_k: executor_calls.append(True),
    )
    try:
        projection = store.load_run(admitted.run_id)
        loaded = scheduler._prepare_run_package(
            admitted.run_id,
            None,
            expected_state_version=projection["state_version"],
        )
        assert loaded is not None
        inspect = next(
            node for node in loaded.package.definition.nodes if node.id == "inspect"
        )
        assert loaded.execution_semantics is not None
        retry_grant = scheduler._sealed_retry_grant(
            inspect,
            loaded.execution_semantics,
            retry_consumed=0,
        )
        claim = store.claim_node(
            admitted.run_id,
            "inspect",
            "active-provider",
            executor_id="prompt",
            execution_authority={
                "schema_version": 1,
                "retry_consumed_before": 0,
                "remaining_attempts": retry_grant.remaining_attempts,
                "iteration_consumed_before": 0,
                "remaining_iterations": 90,
                "remaining_wall_seconds": 120.0,
            },
        )
        assert claim is not None
        store.mark_node_started(claim)

        while_active = store.load_run(admitted.run_id)
        assert (
            scheduler._revalidate_connector_capabilities(
                admitted.run_id,
                loaded.connector_capabilities,
                while_active,
                None,
            )
            is False
        )
        still_active = store.load_run(admitted.run_id)
        assert still_active["status"] == "running"
        assert still_active["nodes"]["inspect"]["state"] == "running"
        assert still_active["last_error"] is None

        store.complete_node(claim, status="succeeded")
        failed = scheduler.advance_all([admitted.run_id])[admitted.run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert failed["status"] == "failed"
    assert failed["nodes"]["inspect"]["state"] == "succeeded"
    assert failed["nodes"]["summarize"]["state"] == "cancelled"
    assert failed["last_error"] == {
        "code": "connector_capability_changed",
        "path": "resources.connector_capabilities",
        "message": "connector capability changed after workflow admission",
    }
    assert [event["event_type"] for event in store.tail_events(admitted.run_id)].count(
        "run_failed"
    ) == 1
    assert executor_calls == []


def test_advance_all_revalidates_again_immediately_before_claim(tmp_path, monkeypatch):
    import plugins.workflow.scheduler as scheduler_module
    from plugins.workflow.admission import RunAdmissionRequest
    from plugins.workflow.scheduler import RunScheduler

    store, compilation, prepared = _prepare_connector_snapshot(tmp_path)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="parallel-preclaim-drift",
            concurrency_key="connector-workflow",
        ),
        immutable_snapshot=prepared,
    )
    calls = [0]

    def live_capabilities(profile=None):
        calls[0] += 1
        if calls[0] <= 2:
            return SimpleNamespace(
                ready_services=frozenset({"ericsson-gitlab"}),
                available_tools=frozenset({"gitlab_read_file"}),
                fingerprint="2" * 64,
            )
        return SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="3" * 64,
        )

    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        live_capabilities,
    )
    scheduler = RunScheduler(store, agent_runner=object())
    executor_calls = []
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_a, **_k: executor_calls.append(True),
    )
    try:
        failed = scheduler.advance_all([admitted.run_id])[admitted.run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert calls[0] == 3
    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "connector_capability_changed"
    assert executor_calls == []


def test_limited_advance_revalidates_again_immediately_before_claim(
    tmp_path, monkeypatch
):
    import plugins.workflow.scheduler as scheduler_module
    from plugins.workflow.admission import RunAdmissionRequest
    from plugins.workflow.scheduler import RunScheduler

    store, compilation, prepared = _prepare_connector_snapshot(tmp_path)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="limited-preclaim-drift",
            concurrency_key="connector-workflow",
        ),
        immutable_snapshot=prepared,
    )
    calls = [0]

    def live_capabilities(profile=None):
        calls[0] += 1
        if calls[0] <= 2:
            return SimpleNamespace(
                ready_services=frozenset({"ericsson-gitlab"}),
                available_tools=frozenset({"gitlab_read_file"}),
                fingerprint="2" * 64,
            )
        return SimpleNamespace(
            ready_services=frozenset(),
            available_tools=frozenset(),
            fingerprint="3" * 64,
        )

    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        live_capabilities,
    )
    scheduler = RunScheduler(store, agent_runner=object())
    executor_calls = []
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_a, **_k: executor_calls.append(True),
    )
    try:
        failed = scheduler.advance(admitted.run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert calls[0] == 3
    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "connector_capability_changed"
    assert executor_calls == []


def test_scheduler_scope_ignores_unrelated_connector_drift_but_not_required_drift(
    monkeypatch,
):
    import plugins.workflow.scheduler as scheduler_module
    from hermes_cli.plugin_configuration import ConnectorCapabilitySnapshot
    from plugins.workflow.models import WorkflowConnectorCapabilities
    from plugins.workflow.scheduler import RunScheduler

    required_services = frozenset({"service-a"})
    required_tools = frozenset({"tool_a"})
    admitted = ConnectorCapabilitySnapshot(
        ready_services=frozenset({"service-a", "service-b"}),
        available_tools=frozenset({"tool_a", "tool_b"}),
        fingerprint="0" * 64,
        _service_fingerprints=(("service-a", "a" * 64), ("service-b", "b" * 64)),
    )
    sealed = WorkflowConnectorCapabilities(
        required_services=required_services,
        required_tools=required_tools,
        fingerprint=admitted.scoped_fingerprint(required_services, required_tools),
    )
    live = [
        ConnectorCapabilitySnapshot(
            ready_services=admitted.ready_services,
            available_tools=admitted.available_tools,
            fingerprint="1" * 64,
            _service_fingerprints=(
                ("service-a", "a" * 64),
                ("service-b", "c" * 64),
            ),
        )
    ]
    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        lambda: live[0],
    )

    assert RunScheduler._connector_capabilities_match(sealed) is True

    live[0] = ConnectorCapabilitySnapshot(
        ready_services=admitted.ready_services,
        available_tools=admitted.available_tools,
        fingerprint="2" * 64,
        _service_fingerprints=(
            ("service-a", "d" * 64),
            ("service-b", "b" * 64),
        ),
    )
    assert RunScheduler._connector_capabilities_match(sealed) is False

    live[0] = ConnectorCapabilitySnapshot(
        ready_services=admitted.ready_services,
        available_tools=frozenset({"tool_b"}),
        fingerprint="0" * 64,
        _service_fingerprints=admitted._service_fingerprints,
    )
    assert RunScheduler._connector_capabilities_match(sealed) is False


@pytest.mark.parametrize("schema_version", (True, 1.0))
def test_connector_capability_extension_requires_an_exact_integer_schema_version(
    schema_version,
):
    from plugins.workflow.models import WorkflowConnectorCapabilities

    with pytest.raises(ValueError, match="malformed"):
        WorkflowConnectorCapabilities.from_dict({
            "schema_version": schema_version,
            "required_services": ["service-a"],
            "required_tools": ["tool_a"],
            "fingerprint": "0" * 64,
        })


def test_pre_extension_normalizer_v5_snapshot_remains_readable_without_reinterpretation(
    tmp_path, monkeypatch
):
    import json

    import plugins.workflow.scheduler as scheduler_module
    from plugins.workflow.admission import RunAdmissionRequest
    from plugins.workflow.admission_service import assess_workflow_admission
    from plugins.workflow.scheduler import RunScheduler
    from plugins.workflow.store import RunStore
    from tests.plugins.workflow.test_phase5_admission_parity import _context

    compilation = _pre_extension_compilation(tmp_path)
    assessment = assess_workflow_admission(
        compilation,
        _context(),
        available_services=frozenset(),
        available_tools=frozenset({"gitlab_read_file"}),
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=assessment.package_digest,
        provider_authority=assessment.provider_authority,
    )
    resources = json.loads((prepared.staging_directory / "resources.json").read_text())
    assert "connector_capabilities" not in resources
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="pre-extension-v5",
            concurrency_key="pre-extension-v5",
        ),
        immutable_snapshot=prepared,
    )
    monkeypatch.setattr(
        scheduler_module,
        "connector_capability_snapshot",
        lambda: (_ for _ in ()).throw(
            AssertionError("old snapshots must not be reinterpreted")
        ),
    )
    scheduler = RunScheduler(store, agent_runner=object())
    try:
        projection = store.load_run(admitted.run_id)
        loaded = scheduler._prepare_run_package(
            admitted.run_id,
            None,
            expected_state_version=projection["state_version"],
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert loaded is not None
    assert loaded.connector_capabilities is None
    assert RunScheduler._connector_capabilities_match(None) is True
