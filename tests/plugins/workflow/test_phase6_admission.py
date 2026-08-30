from __future__ import annotations

from dataclasses import replace
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
from plugins.workflow.dependency_manifest import (
    composite_workflow_digest,
    digest_expanded_compilation,
)
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.execution_semantics import build_phase3_execution_semantics
from plugins.workflow.language import (
    make_language_snapshot,
    read_language_snapshot,
    WorkflowLanguageCompatibilityError,
)
from plugins.workflow.models import (
    RunExecutionLimits,
    WorkflowConnectorCapabilities,
    WorkflowValidationError,
    freeze_value,
)
from plugins.workflow.provider_authority import ProviderAuthorityEnvironment
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.resources import iter_output_field_references
from plugins.workflow.schema import (
    parse_workflow_source_bytes,
    validate_v6_storage_capacity,
)
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
        {
            "id": "command",
            "command": "nested-command",
            "output_format": _OUTPUT_SCHEMA,
        },
        {
            "id": "bash",
            "bash": "printf '%s' '$command.output.status'",
            "depends_on": ["command"],
        },
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
    required_services: tuple[str, ...] = ("github",),
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
        **({"requires": list(required_services)} if required_services else {}),
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
    policy = b"language_compatibility: archon-2026-07\n"
    if required_services:
        policy += yaml.safe_dump({
            "required_services": list(required_services)
        }).encode()
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
        "capacity": {
            "provider_routes": 8,
            "provider_obligations": 44,
            "output_attempts": 36,
            "artifact_executions": 6,
            "artifact_bytes": 100_663_296,
            "run_bytes": 141_950_976,
            "journal_reserve_bytes": 3_538_944,
            "process_executions": 6,
            "process_tree_rss_byte_executions": 12_884_901_888,
            "process_tree_cpu_second_executions": 5_400,
            "process_descendant_executions": 192,
        },
    }
    assert compilation.package.language.structured_outputs["group"] == (
        compilation.package.language.structured_outputs["group/prompt"]
    )
    command_output = compilation.package.language.structured_outputs[
        "group/command"
    ]
    assert command_output.schema_fingerprint == (
        compilation.package.language.structured_outputs[
            "group/prompt"
        ].schema_fingerprint
    )
    bash = compilation.package.definition.nodes[0].value["nodes"][1]
    assert tuple(
        iter_output_field_references(
            bash.value,
            normalizer_version=compilation.package.language.normalizer_version,
        )
    ) == (("command", ("status",)),)
    language_snapshot = make_language_snapshot(
        compilation.package, compilation.composite_digest
    ).to_dict()
    assert language_snapshot["structured_outputs"]["group/command"][
        "schema_fingerprint"
    ] == command_output.schema_fingerprint
    assert (
        compilation.package.language.structured_outputs["group"].schema_fingerprint
        == compilation.package.language.structured_outputs[
            "group/prompt"
        ].schema_fingerprint
    )
    assert compilation.dependency_manifest.expanded_definition_digest == (
        digest_expanded_compilation(
            compilation.definition_bytes, compilation.package
        )
    )
    assert compilation.composite_digest == composite_workflow_digest(
        compilation.dependency_manifest
    )
    execution = build_phase3_execution_semantics(
        compilation.package, RunExecutionLimits()
    )
    assert execution.nodes["group/prompt"]["idle_timeout_seconds"] == 12.0
    assert execution.nodes["group/prompt"]["retry"][
        "requested_total_attempts"
    ] == 2
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


@pytest.mark.parametrize("mutation", ["missing", "contradictory"])
def test_v6_language_snapshot_rejects_missing_or_contradictory_capacity(
    tmp_path, workflow_writer, mutation
):
    compilation = _compile_v6(tmp_path, workflow_writer)
    snapshot = make_language_snapshot(compilation.package, "a" * 64).to_dict()
    capacity = snapshot["node_semantics"]["group"]["loop_group"]["capacity"]
    if mutation == "missing":
        capacity.pop("run_bytes")
    else:
        capacity["run_bytes"] += 1

    with pytest.raises(WorkflowLanguageCompatibilityError):
        read_language_snapshot(snapshot)


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


def test_connector_capabilities_accept_tools_only_but_not_an_empty_scope():
    tools_only = WorkflowConnectorCapabilities(
        required_services=frozenset(),
        required_tools=frozenset({"read_file"}),
        fingerprint="a" * 64,
    )

    assert WorkflowConnectorCapabilities.from_dict(tools_only.to_dict()) == tools_only
    with pytest.raises(ValueError, match="malformed"):
        WorkflowConnectorCapabilities(
            required_services=frozenset(),
            required_tools=frozenset(),
            fingerprint="a" * 64,
        )


def test_v6_snapshot_seals_nested_tools_without_required_services(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(
        tmp_path,
        workflow_writer,
        include_skills=False,
        required_services=(),
    )
    authority = _context().provider_authority(compilation.package)
    capabilities = SimpleNamespace(
        ready_services=frozenset(),
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

    assert WorkflowConnectorCapabilities.from_dict(
        resources["connector_capabilities"]
    ) == WorkflowConnectorCapabilities(
        required_services=frozenset(),
        required_tools=frozenset({"read_file", "web_search"}),
        fingerprint="e" * 64,
    )


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


def _admit_with_live_probes(
    tmp_path, workflow_writer, monkeypatch, probes, **compile_kwargs
):
    from plugins.workflow import provider_authority

    resolver = provider_authority.resolve_workflow_model_reference

    def provider_probe(*args, **kwargs):
        probes.append("provider")
        return resolver(*args, **kwargs)

    def connector_probe():
        probes.append("connector")
        return SimpleNamespace(
            ready_services=frozenset({"github"}),
            available_tools=frozenset({"read_file", "web_search"}),
            fingerprint="f" * 64,
        )

    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )
    monkeypatch.setattr(
        "hermes_cli.plugin_configuration.connector_capability_snapshot",
        connector_probe,
    )
    compilation = _compile_v6(tmp_path, workflow_writer, **compile_kwargs)
    authority = _context().provider_authority(compilation.package)
    RunStore(tmp_path / "home").prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
    )
    return probes


def test_v6_rejects_work_product_before_external_resolution(
    tmp_path, workflow_writer, monkeypatch
):
    probes = []
    with pytest.raises(WorkflowValidationError, match="loop_group.*work bound"):
        _admit_with_live_probes(
            tmp_path,
            workflow_writer,
            monkeypatch,
            probes,
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
    assert probes == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            [{"id": f"prompt{index}", "prompt": "work"} for index in range(257)],
            "loop_group.*provider route.*512",
        ),
        (
            [
                {
                    "id": f"prompt{index}",
                    "prompt": "work",
                    "hooks": {
                        "PostToolUse": [
                            {"response": {"suppressOutput": True}}
                            for _ in range(12)
                        ]
                    },
                }
                for index in range(160)
            ],
            "loop_group.*provider obligation.*4096",
        ),
    ],
    ids=("provider-routes", "provider-obligations"),
)
def test_v6_rejects_provider_products_before_real_resolution(
    tmp_path, workflow_writer, monkeypatch, body, message
):
    observed = []
    from plugins.workflow import provider_authority

    resolver = provider_authority.resolve_workflow_model_reference

    def provider_probe(*args, **kwargs):
        observed.append("provider")
        return resolver(*args, **kwargs)

    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )

    with pytest.raises(WorkflowValidationError, match=message):
        _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=1,
            body=body,
            include_skills=False,
        )
    assert observed == []


def test_v6_rejects_authenticated_resource_product_before_provider_resolution(
    tmp_path, workflow_writer, monkeypatch
):
    observed = []
    from plugins.workflow import dependency_manifest, provider_authority

    read_resource = dependency_manifest._read_source_resource
    resolver = provider_authority.resolve_workflow_model_reference

    def resource_probe(*args, **kwargs):
        observed.append("resource")
        return read_resource(*args, **kwargs)

    def provider_probe(*args, **kwargs):
        observed.append("provider")
        return resolver(*args, **kwargs)

    monkeypatch.setattr(dependency_manifest, "_read_source_resource", resource_probe)
    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )

    with pytest.raises(WorkflowValidationError, match="loop_group.*resource.*512"):
        compilation = _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=1,
            body=[
                {
                    "id": f"approval{index}",
                    "approval": {"message": "Continue?"},
                }
                for index in range(511)
            ],
            include_skills=False,
        )
        _context().provider_authority(compilation.package)
    assert observed == []


def test_v6_attempt_publication_product_rejects_before_resource_or_provider_work(
    tmp_path, workflow_writer, monkeypatch
):
    observed = []
    from plugins.workflow import dependency_manifest, provider_authority

    read_resource = dependency_manifest._read_source_resource
    resolve_provider = provider_authority.resolve_workflow_model_reference

    def resource_probe(*args, **kwargs):
        observed.append("resource")
        return read_resource(*args, **kwargs)

    def provider_probe(*args, **kwargs):
        observed.append("provider")
        return resolve_provider(*args, **kwargs)

    monkeypatch.setattr(dependency_manifest, "_read_source_resource", resource_probe)
    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )

    with pytest.raises(WorkflowValidationError, match="loop_group.*run-byte"):
        _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=1,
            body=[
                {
                    "id": f"approval{index}",
                    "approval": {
                        "message": "Continue?",
                        "on_reject": {"prompt": "Revise", "max_attempts": 2},
                    },
                }
                for index in range(180)
            ],
            include_skills=False,
        )
    assert observed == []


def test_v6_retried_scripts_seal_artifact_and_process_attempt_products(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(
        tmp_path,
        workflow_writer,
        max_iterations=1,
        body=[
            {
                "id": f"script{index}",
                "script": "nested-script",
                "runtime": "uv",
                "retry": {"max_attempts": 1},
            }
            for index in range(2)
        ],
        include_skills=False,
    )

    capacity = compilation.package.language.node_semantics["group"]["loop_group"][
        "capacity"
    ]
    assert capacity["artifact_executions"] == 4
    assert capacity["artifact_bytes"] == 67_108_864
    assert capacity["process_executions"] == 4
    assert capacity["process_tree_rss_byte_executions"] == 8_589_934_592
    assert capacity["process_tree_cpu_second_executions"] == 3_600
    assert capacity["process_descendant_executions"] == 128


def test_v6_artifact_free_scripts_charge_process_but_no_generated_artifact_bytes(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(
        tmp_path,
        workflow_writer,
        max_iterations=2,
        body=[
            {
                "id": "reduce",
                "script": "nested-script",
                "runtime": "uv",
                "artifacts": False,
                "retry": {"max_attempts": 1},
            }
        ],
        include_skills=False,
    )

    capacity = compilation.package.language.node_semantics["group"]["loop_group"][
        "capacity"
    ]
    assert capacity["artifact_executions"] == 0
    assert capacity["artifact_bytes"] == 0
    assert capacity["process_executions"] == 4
    assert capacity["output_attempts"] == 4


def test_v6_artifact_free_capacity_tampering_fails_closed(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(
        tmp_path,
        workflow_writer,
        max_iterations=1,
        body=[
            {
                "id": "reduce",
                "bash": "printf ok",
                "artifacts": False,
            }
        ],
        include_skills=False,
    )
    snapshot = make_language_snapshot(compilation.package, "a" * 64).to_dict()
    capacity = snapshot["node_semantics"]["group"]["loop_group"]["capacity"]
    assert capacity["artifact_executions"] == 0
    assert capacity["artifact_bytes"] == 0
    capacity["artifact_bytes"] = 1

    with pytest.raises(WorkflowLanguageCompatibilityError):
        read_language_snapshot(snapshot)


def test_v6_artifact_free_snapshot_rejects_coherent_extra_process_charge(
    tmp_path, workflow_writer
):
    compilation = _compile_v6(
        tmp_path,
        workflow_writer,
        max_iterations=1,
        body=[
            {
                "id": "reduce",
                "bash": "printf ok",
                "artifacts": False,
            }
        ],
        include_skills=False,
    )
    snapshot = make_language_snapshot(compilation.package, "a" * 64).to_dict()
    loop_group = snapshot["node_semantics"]["group"]["loop_group"]
    capacity = loop_group["capacity"]
    process_executions = loop_group["child_attempts"] + 1
    limits = RunExecutionLimits()
    capacity.update({
        "process_executions": process_executions,
        "process_tree_rss_byte_executions": (
            process_executions * limits.process_tree_rss_bytes
        ),
        "process_tree_cpu_second_executions": int(
            process_executions * limits.process_tree_cpu_seconds
        ),
        "process_descendant_executions": (
            process_executions * limits.max_descendants
        ),
    })

    with pytest.raises(WorkflowLanguageCompatibilityError):
        read_language_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field", "product", "label"),
    [
        ("process_executions", 37, "process execution"),
        (
            "process_tree_rss_byte_executions",
            12_884_901_889,
            "process-tree RSS byte-execution",
        ),
        (
            "process_tree_cpu_second_executions",
            5_401,
            "process-tree CPU second-execution",
        ),
        ("process_descendant_executions", 193, "process descendant-execution"),
    ],
)
def test_v6_storage_validator_rejects_process_products_above_sealed_caps(
    tmp_path, workflow_writer, field, product, label
):
    package = _compile_v6(tmp_path, workflow_writer).package
    semantics = dict(package.language.node_semantics)
    group_semantics = dict(semantics["group"])
    loop_group = dict(group_semantics["loop_group"])
    capacity = dict(loop_group["capacity"])
    capacity[field] = product
    loop_group["capacity"] = capacity
    group_semantics["loop_group"] = loop_group
    semantics["group"] = group_semantics
    tampered = replace(
        package,
        language=replace(
            package.language,
            node_semantics=freeze_value(semantics),
        ),
    )

    with pytest.raises(WorkflowValidationError, match=label):
        validate_v6_storage_capacity(tampered)


def test_v6_rejects_run_byte_product_before_resource_or_provider_work(
    tmp_path, workflow_writer, monkeypatch
):
    observed = []
    from plugins.workflow import dependency_manifest, provider_authority

    read_resource = dependency_manifest._read_source_resource
    resolve_provider = provider_authority.resolve_workflow_model_reference

    def resource_probe(*args, **kwargs):
        observed.append("resource")
        return read_resource(*args, **kwargs)

    def provider_probe(*args, **kwargs):
        observed.append("provider")
        return resolve_provider(*args, **kwargs)

    monkeypatch.setattr(dependency_manifest, "_read_source_resource", resource_probe)
    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )

    with pytest.raises(WorkflowValidationError, match="loop_group.*run-byte"):
        _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=1,
            body=[
                {
                    "id": f"script{index}",
                    "script": "nested-script",
                    "runtime": "uv",
                }
                for index in range(31)
            ],
            include_skills=False,
        )
    assert observed == []


def test_v6_rejects_journal_product_before_resource_or_provider_work(
    tmp_path, workflow_writer, monkeypatch
):
    observed = []
    from plugins.workflow import dependency_manifest, provider_authority

    read_resource = dependency_manifest._read_source_resource
    resolve_provider = provider_authority.resolve_workflow_model_reference

    def resource_probe(*args, **kwargs):
        observed.append("resource")
        return read_resource(*args, **kwargs)

    def provider_probe(*args, **kwargs):
        observed.append("provider")
        return resolve_provider(*args, **kwargs)

    monkeypatch.setattr(dependency_manifest, "_read_source_resource", resource_probe)
    monkeypatch.setattr(
        provider_authority, "resolve_workflow_model_reference", provider_probe
    )

    with pytest.raises(WorkflowValidationError, match="loop_group.*journal-reserve"):
        _compile_v6(
            tmp_path,
            workflow_writer,
            max_iterations=6,
            body=[
                {
                    "id": f"approval{index}",
                    "approval": {"message": "Continue?"},
                }
                for index in range(456)
            ],
            include_skills=False,
        )
    assert observed == []
