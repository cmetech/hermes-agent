from __future__ import annotations

from dataclasses import replace
from importlib.machinery import ModuleSpec
import json
from pathlib import Path
import sys
import types

import yaml

from plugins.workflow.cli import doctor_package
from plugins.workflow.compat import CompatibilityLevel
from plugins.workflow.models import WorkflowRuntimeConfig, WorkflowStructuredOutput
from plugins.workflow.schema import load_workflow
from plugins.workflow.trust import WorkflowTrustStore


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "portable"
    (root / "workflows").mkdir(parents=True)
    (root / "commands").mkdir()
    (root / "scripts").mkdir()
    (root / "mcp").mkdir()
    (root / "commands" / "inspect.md").write_text(
        "---\ndescription: Inspect evidence\nargument-hint: <case>\n---\n"
        "Inspect $ARGUMENTS.\n",
        encoding="utf-8",
    )
    (root / "scripts" / "normalize.py").write_text(
        "print('normalized')\n", encoding="utf-8"
    )
    (root / "mcp" / "local.yaml").write_text(
        yaml.safe_dump({"command": "python", "args": ["${MCP_SCRIPT}"]}),
        encoding="utf-8",
    )
    workflow = root / "workflows" / "diagnostic.yaml"
    workflow.write_text(
        yaml.safe_dump(
            {
                "name": "diagnostic",
                "description": "Inspect supplied evidence",
                "persist_sessions": True,
                "nodes": [
                    {
                        "id": "inspect",
                        "command": "inspect",
                        "context": "fresh",
                        "allowed_tools": ["Read", "UnknownTool"],
                        "skills": ["workflow"],
                        "mcp": "local",
                        "hooks": {"PreToolUse": [{"response": {"continue": True}}]},
                        "agents": {
                            "reviewer": {
                                "description": "review",
                                "prompt": "Review the evidence",
                            }
                        },
                    },
                    {
                        "id": "normalize",
                        "script": "normalize",
                        "runtime": "uv",
                        "depends_on": ["inspect"],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    workflow.with_name("diagnostic.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "overlap_policy": "forbid",
                "required_secrets": ["SERVICE_TOKEN"],
                "required_services": ["outlook"],
                "execution_environment": "isolated_backend_required",
                "delivery_defaults": {
                    "inputs": {
                        "evidence": {
                            "kind": "file",
                            "required": True,
                            "max_bytes": 4096,
                        },
                        "notes": {"kind": "text", "required": False},
                    }
                },
                "limits": {"max_parallel_nodes": 2},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return workflow


def test_doctor_reports_resources_trust_inputs_and_capacity_without_remote_calls(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = _package(tmp_path)
    package = load_workflow(workflow)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("doctor must not start a model, MCP server, or subprocess")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    report = doctor_package(
        package,
        hermes_home=tmp_path / "home",
        available_tools=frozenset({"read_file"}),
        available_services=frozenset(),
        available_skills=frozenset({"workflow"}),
        available_runtimes=frozenset(),
        mcp_available=True,
        environment={},
        runtime_config=WorkflowRuntimeConfig(max_parallel_nodes=4),
    )

    codes = {finding.code for finding in report.findings}
    assert report.workflow == "diagnostic"
    assert report.package == str(package.root)
    assert report.trust_state == "untrusted"
    assert report.concurrency_policy == "forbid"
    assert report.input_requirements[0].name == "evidence"
    assert report.input_requirements[0].kind == "file"
    assert report.input_requirements[0].max_bytes == 4096
    assert report.resolved_commands == ("commands/inspect.md",)
    assert report.resolved_scripts == ("scripts/normalize.py",)
    assert report.resolved_mcp_servers == ("mcp/local.yaml",)
    assert report.resolved_skills == ("workflow",)
    assert {
        "unknown_tool_alias",
        "missing_runtime",
        "missing_mcp_variable",
        "missing_credential",
        "missing_service",
        "immutable_input_snapshot",
        "overlap_forbid",
        "effective_admission_capacity",
        "isolated_execution_required",
        "executable_resources_digest_bound",
        "inline_agent_bounds",
        "persistent_session_fingerprint",
        "hook_mapped",
    } <= codes
    assert report.runnable is False
    assert any(
        finding.level is CompatibilityLevel.UNSUPPORTED and finding.blocking
        for finding in report.findings
    )


def test_doctor_trust_is_bound_to_the_current_risk_digest(tmp_path: Path) -> None:
    package = load_workflow(_package(tmp_path))
    home = tmp_path / "home"
    before = doctor_package(
        package,
        hermes_home=home,
        available_tools=frozenset({"read_file"}),
        available_skills=frozenset({"workflow"}),
        available_runtimes=frozenset({"uv"}),
        mcp_available=True,
        environment={"MCP_SCRIPT": "safe.py", "SERVICE_TOKEN": "redacted"},
    )
    WorkflowTrustStore(home).trust(
        before.package_digest,
        actor="test",
        risk_digest=before.risk_summary.risk_digest,
    )

    after = doctor_package(
        package,
        hermes_home=home,
        available_tools=frozenset({"read_file"}),
        available_skills=frozenset({"workflow"}),
        available_runtimes=frozenset({"uv"}),
        mcp_available=True,
        environment={"MCP_SCRIPT": "safe.py", "SERVICE_TOKEN": "redacted"},
    )

    assert before.package_digest == after.package_digest
    assert after.trust_state == "trusted"


def test_doctor_json_contract_contains_stable_structured_fields(
    tmp_path: Path,
) -> None:
    report = doctor_package(
        load_workflow(_package(tmp_path)),
        hermes_home=tmp_path / "home",
        available_runtimes=frozenset(),
        environment={},
    )
    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["workflow"] == "diagnostic"
    assert payload["risk_summary"]["package_digest"] == payload["package_digest"]
    assert {item["name"] for item in payload["input_requirements"]} == {
        "evidence",
        "notes",
    }
    assert all(
        {"code", "path", "level", "blocking"} <= item.keys()
        for item in payload["findings"]
    )


def test_doctor_matches_runtime_authorization_for_explicit_provider_and_model(
    tmp_path: Path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        nodes=[
            {
                "id": "agent",
                "prompt": "Inspect fictional evidence",
                "provider": "custom",
                "model": "custom-model",
            }
        ],
    )
    package = load_workflow(workflow)
    home = tmp_path / "home"

    blocked = doctor_package(package, hermes_home=home)

    assert blocked.runnable is False
    assert {finding.code for finding in blocked.findings if finding.blocking} == {
        "model_override_not_authorized",
        "provider_override_not_authorized",
    }

    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text(
        "plugins:\n"
        "  entries:\n"
        "    workflow:\n"
        "      agent:\n"
        "        allow_provider_override: true\n"
        "        allow_model_override: true\n",
        encoding="utf-8",
    )

    authorized = doctor_package(package, hermes_home=home)

    assert authorized.runnable is True
    assert not any(
        finding.code.endswith("_override_not_authorized")
        for finding in authorized.findings
    )


def _archon_structured_package(tmp_path: Path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        name="doctor-structured-output",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a report",
                "output_format": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            }
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(path)


def test_doctor_reports_existing_extra_guidance_when_validator_is_missing(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = _archon_structured_package(tmp_path, workflow_writer)
    monkeypatch.setattr(
        "plugins.workflow.cli._structured_output_validator_available",
        lambda _structured_outputs: False,
    )

    report = doctor_package(package, hermes_home=tmp_path / "home")

    finding = next(
        (
            item
            for item in report.findings
            if item.code == "structured_output_unavailable"
        ),
        None,
    )
    assert finding is not None
    assert finding.blocking is True
    assert "install the Hermes mcp or all extra" in finding.message


def test_doctor_rejects_importable_validator_without_callable_draft(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = _archon_structured_package(tmp_path, workflow_writer)
    partial = types.ModuleType("jsonschema")
    partial.__spec__ = ModuleSpec("jsonschema", loader=None)
    partial.Draft202012Validator = object()
    partial.validate = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "jsonschema", partial)

    report = doctor_package(package, hermes_home=tmp_path / "home")

    finding = next(
        (
            item
            for item in report.findings
            if item.code == "structured_output_unavailable"
        ),
        None,
    )
    assert finding is not None
    assert report.runnable is False
    assert finding.blocking is True
    assert (
        finding.message == "jsonschema is required; install the Hermes mcp or all extra"
    )


def test_doctor_rejects_callable_draft_without_iter_errors(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = _archon_structured_package(tmp_path, workflow_writer)

    class EmptySchemaValidator:
        @staticmethod
        def iter_errors(_value):
            return ()

    def draft_validator(schema):
        return EmptySchemaValidator() if schema == {} else object()

    partial = types.ModuleType("jsonschema")
    partial.__spec__ = ModuleSpec("jsonschema", loader=None)
    partial.Draft202012Validator = draft_validator
    partial.validate = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "jsonschema", partial)

    report = doctor_package(package, hermes_home=tmp_path / "home")

    finding = next(
        (
            item
            for item in report.findings
            if item.code == "structured_output_unavailable"
        ),
        None,
    )
    assert finding is not None
    assert report.runnable is False
    assert finding.blocking is True
    assert (
        finding.message == "jsonschema is required; install the Hermes mcp or all extra"
    )


def test_doctor_reports_invalid_canonical_schema_with_stable_taxonomy(
    tmp_path: Path, workflow_writer
) -> None:
    package = _archon_structured_package(tmp_path, workflow_writer)
    package = replace(
        package,
        language=replace(
            package.language,
            structured_outputs={
                "producer": WorkflowStructuredOutput(
                    canonical_schema={"type": 7},
                    schema_fingerprint="f" * 64,
                )
            },
        ),
    )

    report = doctor_package(package, hermes_home=tmp_path / "home")

    finding = next(
        item for item in report.findings if item.code == "structured_output_invalid"
    )
    assert report.runnable is False
    assert finding.blocking is True
    assert finding.message == "structured-output schema is invalid"


def test_doctor_blocks_draft_constructor_import_failure(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = _archon_structured_package(tmp_path, workflow_writer)

    def missing_internal_dependency(_schema):
        raise ModuleNotFoundError("jsonschema internal dependency missing")

    partial = types.ModuleType("jsonschema")
    partial.__spec__ = ModuleSpec("jsonschema", loader=None)
    partial.Draft202012Validator = missing_internal_dependency
    partial.validate = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "jsonschema", partial)

    constructor_exception = None
    report = None
    try:
        report = doctor_package(package, hermes_home=tmp_path / "home")
    except ModuleNotFoundError as exc:
        constructor_exception = exc

    assert constructor_exception is None, (
        f"raw constructor exception escaped Doctor: {constructor_exception}"
    )
    assert report is not None
    finding = next(
        (
            item
            for item in report.findings
            if item.code == "structured_output_unavailable"
        ),
        None,
    )
    assert finding is not None
    assert report.runnable is False
    assert finding.blocking is True
    assert (
        finding.message == "jsonschema is required; install the Hermes mcp or all extra"
    )


def test_doctor_schemaless_workflow_does_not_require_validator(
    tmp_path: Path, workflow_writer, monkeypatch
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path,
            name="doctor-schemaless",
            nodes=[{"id": "agent", "prompt": "Return prose"}],
        )
    )
    monkeypatch.setattr(
        "plugins.workflow.cli._structured_output_validator_available",
        lambda _structured_outputs: False,
    )

    report = doctor_package(package, hermes_home=tmp_path / "home")

    assert not any(
        item.code == "structured_output_unavailable" for item in report.findings
    )
