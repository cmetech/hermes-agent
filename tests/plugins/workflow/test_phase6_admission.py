from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.admission_service import assess_workflow_admission
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.language import WorkflowLanguageCompatibilityError
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.provider_authority import ProviderAuthorityEnvironment
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowPackageDigest


_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}


def _context():
    config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        },
        "model_aliases": {
            "root": {"provider": "openrouter", "model": "openai/gpt-5.4"},
            "group": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.5",
            },
            "child": {
                "provider": "openrouter",
                "model": "google/gemini-2.5-pro",
            },
            "recovery": {
                "provider": "openrouter",
                "model": "openai/gpt-4.1",
            },
        },
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={"provider": "openrouter", "default": "openai/gpt-5.4"},
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=runtime,
        model_config_snapshot=config,
        provider_authority_environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=True,
        ),
    )


def _body_nodes(*, include_skills: bool = True):
    return [
        {"id": "command", "command": "nested-command"},
        {"id": "bash", "bash": "printf nested", "depends_on": ["command"]},
        {
            "id": "script",
            "script": "nested-script",
            "runtime": "uv",
            "depends_on": ["bash"],
        },
        {
            "id": "approval",
            "approval": {
                "message": "Continue?",
                "on_reject": {"prompt": "Revise", "max_attempts": 2},
            },
            "depends_on": ["script"],
        },
        {
            "id": "ordinary-loop",
            "loop": {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": 2,
            },
            "depends_on": ["approval"],
        },
        {
            "id": "prompt",
            "prompt": "Finish",
            "model": "@child",
            "effort": "high",
            "allowed_tools": ["WebSearch"],
            **({"skills": ["child-skill"]} if include_skills else {}),
            "idle_timeout": 12000,
            "retry": {"max_attempts": 1},
            "maxBudgetUsd": 2.5,
            "output_format": _OUTPUT_SCHEMA,
            "depends_on": ["ordinary-loop"],
        },
    ]


def _compile_v6(
    tmp_path: Path,
    workflow_writer,
    *,
    max_iterations: int = 3,
    body=None,
    include_skills: bool = True,
):
    root = tmp_path / "source"
    (root / "commands").mkdir(parents=True)
    (root / "commands/nested-command.md").write_text(
        "nested command\n", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts/nested-script.py").write_text(
        "print('nested script')\n", encoding="utf-8"
    )
    (root / "mcp").mkdir()
    (root / "mcp/echo.yaml").write_text(
        "command: python\nargs: [-c, 'print(1)']\n", encoding="utf-8"
    )
    path = workflow_writer(
        root / "workflows",
        name="phase6-admission",
        filename="phase6-admission.yaml",
        model="@root",
        provider="openrouter",
        requires=["github"],
        nodes=[{
            "id": "group",
            "loop_group": {
                "until": "DONE",
                "max_iterations": max_iterations,
                "nodes": (
                    body
                    if body is not None
                    else _body_nodes(include_skills=include_skills)
                ),
            },
            "model": "@group",
            "fallbackModel": "@recovery",
            "allowed_tools": ["Read"],
            **({"skills": ["nested-skill"]} if include_skills else {}),
            "mcp": "mcp/echo.yaml",
            "hooks": {
                "PostToolUse": [{"response": {"suppressOutput": True}}]
            },
        }],
    )
    policy = (
        b"language_compatibility: archon-2026-07\n"
        b"required_services: [github]\n"
    )
    path.with_name("phase6-admission.hermes.yaml").write_bytes(policy)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=policy,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=6,
    )


def test_v6_seals_nested_admission_authority_resources_and_bounds(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(tmp_path, workflow_writer)
    assessment = assess_workflow_admission(
        compilation,
        _context(),
        available_tools=frozenset({"read_file", "web_search"}),
        available_services=frozenset({"github"}),
    )
    authority = assessment.provider_authority
    assert authority is not None

    assert set(authority.routes) >= {
        "group/command:primary",
        "group/command:fallback",
        "group/approval:primary",
        "group/ordinary-loop:primary",
        "group/prompt:primary",
        "group/prompt:fallback",
    }
    assert authority.routes["group/command:primary"].model == (
        "anthropic/claude-sonnet-4.5"
    )
    assert authority.routes["group/command:fallback"].model == "openai/gpt-4.1"
    assert authority.routes["group/prompt:primary"].model == "google/gemini-2.5-pro"
    assert authority.routes["group/prompt:primary"].provider_options["effort"] == (
        "high"
    )

    bindings = {
        (binding.node_id, binding.resource_kind): binding
        for binding in compilation.dependency_manifest.resources
    }
    assert {
        ("group/command", "command"),
        ("group/script", "named_script"),
        ("group/command", "mcp"),
        ("group/prompt", "mcp"),
    } <= set(bindings)
    assert assessment.risk.shell_or_script_nodes == ("group/bash", "group/script")
    assert assessment.risk.requested_tools == ("read_file", "web_search")
    assert assessment.risk.requested_skills == ("child-skill", "nested-skill")
    assert assessment.risk.local_mcp_servers == ("mcp/echo.yaml",)

    semantics = compilation.package.language.node_semantics
    assert semantics["group/prompt"]["idle_timeout_seconds"] == 12.0
    assert semantics["group/prompt"]["retry"]["requested_total_attempts"] == 2
    assert semantics["group"]["loop_group"] == {
        "primary_sink": "prompt",
        "effective_interactive": False,
        "signal_completes": True,
        "child_executions": 21,
        "child_attempts": 36,
    }
    assert compilation.package.language.structured_outputs["group"] == (
        compilation.package.language.structured_outputs["group/prompt"]
    )
    assert any(
        item.route_id == "group/prompt:primary"
        and item.decision.option == "maxBudgetUsd"
        for item in authority.obligations
    )
    assert any(
        item.route_id == "group/prompt:primary"
        and item.decision.option == "PostToolUse"
        for item in authority.obligations
    )


def test_v6_snapshot_seals_nested_connector_and_skill_inventory(
    tmp_path, workflow_writer, monkeypatch
):
    compilation = _compile_v6(tmp_path, workflow_writer)
    authority = _context().provider_authority(compilation.package)
    assert authority is not None

    def skill_prompt(skills, *, task_id):
        assert task_id is None
        return f"SEALED:{','.join(skills)}", tuple(skills), ()

    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt", skill_prompt
    )
    capabilities = SimpleNamespace(
        ready_services=frozenset({"github"}),
        available_tools=frozenset({"read_file", "web_search"}),
        fingerprint="f" * 64,
        scoped_fingerprint=lambda *_: "e" * 64,
    )
    prepared = RunStore(tmp_path / "home").prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
        connector_capabilities=capabilities,
    )
    resources = json.loads(
        (prepared.staging_directory / "resources.json").read_bytes()
    )

    assert resources["connector_capabilities"]["required_tools"] == [
        "read_file",
        "web_search",
    ]
    assert "group" not in resources["node_skills"]
    assert "group/command" in resources["node_skills"]
    assert "group/prompt" in resources["node_skills"]
    assert (
        prepared.staging_directory / "node-skills/group/command.md"
    ).read_text() == "SEALED:nested-skill"
    assert (
        prepared.staging_directory / "node-skills/group/prompt.md"
    ).read_text() == "SEALED:child-skill"


def _admit_v6(store: RunStore, compilation, authority):
    capabilities = SimpleNamespace(
        ready_services=frozenset({"github"}),
        available_tools=frozenset({"read_file", "web_search"}),
        fingerprint="f" * 64,
        scoped_fingerprint=lambda *_: "e" * 64,
    )
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
        connector_capabilities=capabilities,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="phase6-tamper",
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


@pytest.mark.parametrize(
    "mutation",
    ["script", "provider", "primary_sink", "structured_output", "topology"],
)
def test_v6_nested_snapshot_tampering_fails_closed_before_reload(
    tmp_path, workflow_writer, mutation
):
    compilation = _compile_v6(tmp_path, workflow_writer, include_skills=False)
    authority = _context().provider_authority(compilation.package)
    assert authority is not None
    store = RunStore(tmp_path / "home")
    run_id = _admit_v6(store, compilation, authority)
    run = store.run_directory(run_id)

    if mutation == "script":
        binding = next(
            item
            for item in compilation.dependency_manifest.resources
            if item.node_id == "group/script"
            and item.resource_kind == "named_script"
        )
        (run / binding.snapshot_path).write_bytes(b"print('tampered')\n")
    elif mutation == "provider":
        provider = run / "provider-resolution.json"
        document = json.loads(provider.read_bytes())
        document["routes"][0]["model"] = "tampered/model"
        provider.write_bytes(json.dumps(document).encode())
    elif mutation in {"primary_sink", "structured_output"}:
        resources = run / "resources.json"
        document = json.loads(resources.read_bytes())
        if mutation == "primary_sink":
            document["language"]["node_semantics"]["group"]["loop_group"][
                "primary_sink"
            ] = "command"
        else:
            document["language"]["structured_outputs"]["group/prompt"][
                "schema_fingerprint"
            ] = "0" * 64
        resources.write_bytes(json.dumps(document).encode())
    else:
        definition = run / "definition.yaml"
        document = yaml.safe_load(definition.read_bytes())
        document["nodes"][0]["loop_group"]["nodes"].reverse()
        definition.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        RunScheduler(store)._load_run_package(run_id)
    assert raised.value.code == "workflow_snapshot_integrity_mismatch"


def test_v6_rejects_work_product_before_external_resolution(
    tmp_path, workflow_writer
):
    probes = []

    def admit_v6_fixture():
        compilation = _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=100,
            body=[
                {
                    "id": f"node{index}",
                    "loop": {
                        "prompt": "work",
                        "until": "DONE",
                        "max_iterations": 10,
                    },
                }
                for index in range(5)
            ],
        )
        probes.append("connector")
        probes.append("provider")
        return compilation

    with pytest.raises(WorkflowValidationError, match="loop_group.*work bound"):
        admit_v6_fixture()
    assert probes == []
