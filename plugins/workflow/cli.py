"""Side-effect-free operator CLI for portable workflow packages."""

from __future__ import annotations

import argparse
from contextvars import ContextVar
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MethodType
from typing import TYPE_CHECKING, AbstractSet, Callable, Iterable, Mapping

import yaml

from agent.structured_output import (
    STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE,
    StructuredOutputSchemaInvalid,
    StructuredOutputValidatorUnavailable,
    require_structured_output_validator,
)
from hermes_constants import get_hermes_home
from hermes_cli.plugin_configuration import (
    ConnectorCapabilitySnapshot,
    connector_capability_snapshot,
)
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    language_projection,
    supports_phase4_semantics,
    supports_phase5_semantics,
)
from plugins.workflow.admission_service import (
    WorkflowAdmissionAssessment,
    assess_workflow_admission,
    assess_production_workflow_admission,
)
from plugins.workflow.compat import (
    ARCHON_TOOL_ALIASES,
    CompatibilityFinding,
    CompatibilityLevel,
    CompatibilityReport,
    DoctorReport,
    InputRequirement,
    WorkflowCompatibilityBlockedError,
    assess_compatibility,
    require_runnable,
)
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.discovery import discover_workflows
from plugins.workflow.models import (
    RunExecutionLimits,
    ValidationIssue,
    WorkflowLanguageProfile,
    WorkflowPackage,
    WorkflowRuntimeConfig,
    WorkflowStructuredOutput,
    WorkflowValidationError,
)
from plugins.workflow.machine_contract import (
    CoordinatorUnavailable,
    EXIT_ACTION_FAILED,
    EXIT_AUTHORIZATION,
    EXIT_BLOCKING_FINDING,
    EXIT_INTERNAL,
    EXIT_INVOCATION,
    MachineError,
    WorkflowActionFailed,
    WorkflowAuthorization,
    WorkflowCommandError,
    WorkflowConflict,
    WorkflowNotFound,
    error_envelope,
    operator_command_contract,
    projection_was_truncated,
    success_envelope,
)
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.projection_limits import (
    WORKFLOW_DEFINITION_MAX_BYTES,
    WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS,
    WORKFLOW_DEFINITION_MAX_EDGES,
    WORKFLOW_DEFINITION_MAX_NODES,
)
from plugins.workflow.schema import (
    load_workflow,
    parse_workflow_source_bytes,
    validate_package,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.sanitize import (
    projection_key_is_secret,
    sanitize_projection,
)
from plugins.workflow.schema_cli import configure_schema_parser, emit_schema
from plugins.workflow.sessions import NodeSessionRegistry
from plugins.workflow.store import (
    ForegroundExecutionConflict,
    RunStore,
    StorageQuotaError,
)
from plugins.workflow.topology import project_topology
from plugins.workflow.trust import (
    WorkflowRiskSummary,
    WorkflowTrustError,
    WorkflowTrustStore,
    WorkflowPackageDigest,
    build_risk_summary,
    compute_package_digest,
    preflight_execution,
)
from tools.managed_process import ProcessResourceLimits

if TYPE_CHECKING:
    from plugins.workflow.compilation import WorkflowCompilation
    from plugins.workflow.runner_binding import ExecutionCapabilityContext


_MACHINE_COMMAND: ContextVar[str] = ContextVar(
    "workflow_machine_command", default="workflow"
)
_BENIGN_POLICY_FIELDS = frozenset({"modelReasoningEffort"})
_DOCTOR_TEXT_FINDINGS_MAX = 200
_COMPILATION_TEXT_ITEMS_MAX = 200
_DOCTOR_ABSOLUTE_PATH_START = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|(?<![A-Za-z0-9_:/-])/)"
)


class WorkflowDefinitionProjectionCapacityError(ValueError):
    """A complete safe definition cannot fit within the public contract."""


class _WorkflowArgumentParser(argparse.ArgumentParser):
    """Keep argparse failures inside the JSON machine contract."""

    _machine_mode = False

    def parse_known_args(self, args=None, namespace=None):
        arguments = list(args) if args is not None else sys.argv[1:]
        self._machine_mode = "--json" in arguments
        parsed, extras = argparse.ArgumentParser.parse_known_args(
            self, arguments, namespace
        )
        if self._machine_mode and extras:
            self.error(f"unrecognized arguments: {' '.join(extras)}")
        return parsed, extras

    def error(self, message: str) -> None:
        if not self._machine_mode:
            argparse.ArgumentParser.error(self, message)
        parts = self.prog.split()
        action = (
            " ".join(parts[-2:])
            if len(parts) >= 2 and parts[-2] == "showcase"
            else parts[-1]
        )
        error = MachineError("invalid_request", message)
        print(
            json.dumps(
                error_envelope(f"workflow {action}", error),
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
        )
        self.exit(EXIT_INVOCATION)


def _compat_dict(report: CompatibilityReport) -> dict[str, object]:
    return {
        "level": report.level.value,
        "blocking_count": len(report.blocking_findings),
    }


def workflow_trigger_idempotency_key(
    schedule_id: str, scheduled_utc_fire_instant: datetime
) -> str:
    """Return the stable source key for one logical cron delivery."""
    if not schedule_id:
        raise ValueError("schedule_id must not be empty")
    if scheduled_utc_fire_instant.tzinfo is None:
        raise ValueError("scheduled fire instant must be timezone-aware")
    instant = scheduled_utc_fire_instant.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )
    material = f"{schedule_id}\0{instant}".encode()
    return "cron:" + hashlib.sha256(material).hexdigest()


def _catalog_summary(
    package: WorkflowPackage, report: CompatibilityReport
) -> dict[str, object]:
    topology = project_topology(package.definition)
    return {
        "action": "list",
        "workflow": package.definition.name,
        "name": package.definition.name,
        "description": package.definition.description,
        "source": package.source,
        "precedence": package.precedence,
        "compatibility": _compat_dict(report),
        "runnable": report.runnable,
        "topology_text": topology.text,
        "topology_mermaid": topology.mermaid,
        "topology_warnings": list(topology.warnings),
        "requirements": {},
        "approvals": [],
        "schedules": [],
        "warnings": [],
        "next_actions": ["show", "doctor"] if report.runnable else ["doctor"],
    }


def build_catalog(
    packages: Iterable[WorkflowPackage],
    *,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
) -> tuple[dict[str, object], ...]:
    entries = []
    for package in packages:
        report = assess_compatibility(
            package,
            available_tools=available_tools,
            available_services=available_services,
            provider_capabilities=provider_capabilities,
            isolated_workdir=isolated_workdir,
            mcp_available=mcp_available,
        )
        entries.append(_catalog_summary(package, report))
    return tuple(sorted(entries, key=lambda entry: str(entry["name"])))


def _command_resource(package: WorkflowPackage, name: str) -> Path | None:
    base = package.root / "commands" / name
    for candidate in (base, base.with_suffix(".md")):
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(package.root.resolve(strict=True))
        except (OSError, ValueError):
            continue
        if not candidate.is_symlink():
            return resolved
    return None


def _argument_hints(package: WorkflowPackage) -> dict[str, str]:
    hints: dict[str, str] = {}
    for node in package.definition.nodes:
        if node.node_type != "command":
            continue
        resource = _command_resource(package, str(node.value))
        if resource is None:
            continue
        try:
            text = resource.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not text.startswith("---\n"):
            continue
        before, marker, _ = text.partition("\n---\n")
        if not marker:
            continue
        try:
            frontmatter = yaml.safe_load(before[4:]) or {}
        except yaml.YAMLError:
            continue
        hint = (
            frontmatter.get("argument-hint") if isinstance(frontmatter, dict) else None
        )
        if isinstance(hint, str) and hint.strip():
            hints[node.id] = hint.strip()[:200]
    return dict(sorted(hints.items()))


def _related_cron(
    name: str, cron_jobs: Iterable[Mapping[str, object]]
) -> list[dict[str, object]]:
    pattern = re.compile(
        rf"(?:^|\s)hermes\s+workflow\s+run\s+{re.escape(name)}(?:\s|$)"
    )
    related: list[dict[str, object]] = []
    for job in cron_jobs:
        bodies = (job.get("prompt"), job.get("command"))
        if not any(isinstance(body, str) and pattern.search(body) for body in bodies):
            continue
        related.append({
            "id": job.get("id"),
            "enabled": bool(job.get("enabled", True)),
            "schedule": job.get("schedule_display"),
            "next_run_at": job.get("next_run_at"),
            "state": job.get("state"),
        })
    return sorted(related, key=lambda entry: str(entry["id"]))


def _absolute_projection_path(value: str) -> bool:
    return (
        value.startswith("~")
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _complete_projection(
    value: object,
    *,
    key: str = "",
    depth: int = 0,
) -> object:
    """Sanitize a semantic definition without silently dropping members."""
    if depth > 12:
        raise WorkflowDefinitionProjectionCapacityError(
            "workflow definition nesting limit exceeded"
        )
    if projection_key_is_secret(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        if len(value) > WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS:
            raise WorkflowDefinitionProjectionCapacityError(
                "workflow definition mapping limit exceeded"
            )
        return {
            str(child): _complete_projection(
                item,
                key=str(child),
                depth=depth + 1,
            )
            for child, item in value.items()
            if str(child).lower()
            not in {"operator_scope_digest", "idempotency_key_digest"}
        }
    if isinstance(value, (list, tuple)):
        if len(value) > WORKFLOW_DEFINITION_MAX_CONTAINER_ITEMS:
            raise WorkflowDefinitionProjectionCapacityError(
                "workflow definition sequence limit exceeded"
            )
        return [
            _complete_projection(
                item,
                key=key,
                depth=depth + 1,
            )
            for item in value
        ]
    if isinstance(value, str):
        if _absolute_projection_path(value):
            return "[REDACTED]"
        return sanitize_projection(value, key=key, depth=depth)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _complete_projection(str(value), key=key, depth=depth + 1)


def _hook_projection(hooks: object) -> object:
    if not isinstance(hooks, Mapping):
        return {}
    projected: dict[str, object] = {}
    for event, raw_entries in hooks.items():
        if not isinstance(raw_entries, (list, tuple)):
            continue
        entries: list[object] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, Mapping):
                continue
            entry: dict[str, object] = {}
            for field in ("matcher", "timeout"):
                if field in raw_entry:
                    entry[field] = _complete_projection(raw_entry[field], key=field)
            raw_response = raw_entry.get("response")
            if isinstance(raw_response, Mapping):
                response: dict[str, object] = {}
                for field in ("continue", "suppressOutput", "decision"):
                    if field in raw_response:
                        response[field] = _complete_projection(
                            raw_response[field], key=field
                        )
                for field in ("systemMessage", "stopReason"):
                    if field in raw_response:
                        response[field] = "[REDACTED]"
                specific = raw_response.get("hookSpecificOutput")
                if isinstance(specific, Mapping):
                    safe_specific: dict[str, object] = {}
                    for field, item in specific.items():
                        if field in {
                            "hookEventName",
                            "permissionDecision",
                            "action",
                        }:
                            safe_specific[str(field)] = _complete_projection(
                                item, key=str(field)
                            )
                        else:
                            safe_specific[str(field)] = "[REDACTED]"
                    response["hookSpecificOutput"] = safe_specific
                entry["response"] = response
            entries.append(entry)
        projected[str(event)] = entries
    return projected


def _options_projection(options: Mapping[str, object]) -> dict[str, object]:
    projected: dict[str, object] = {}
    for key, value in options.items():
        if key == "hooks":
            projected[key] = _hook_projection(value)
        elif key == "sandbox":
            projected[key] = _complete_projection(value, key=key)
        elif key in _BENIGN_POLICY_FIELDS:
            projected[key] = _complete_projection(value)
        else:
            projected[key] = _complete_projection(value, key=key)
    return projected


def _definition_projection(package: WorkflowPackage) -> dict[str, object]:
    """Return the bounded, body-free normalized definition used by show APIs."""
    definition = package.definition
    if len(definition.nodes) > WORKFLOW_DEFINITION_MAX_NODES:
        raise WorkflowDefinitionProjectionCapacityError(
            "workflow definition node limit exceeded"
        )
    edge_count = sum(len(node.depends_on) for node in definition.nodes)
    if edge_count > WORKFLOW_DEFINITION_MAX_EDGES:
        raise WorkflowDefinitionProjectionCapacityError(
            "workflow definition edge limit exceeded"
        )
    delivery = package.sidecar.get("delivery_defaults", {})
    raw_inputs = delivery.get("inputs", {}) if isinstance(delivery, Mapping) else {}
    inputs = (
        {
            name: (
                {
                    field: (
                        "[REDACTED]"
                        if str(field).lower() == "default"
                        else _complete_projection(value, key=str(field))
                    )
                    for field, value in specification.items()
                }
                if isinstance(specification, Mapping)
                else _complete_projection(specification, key=str(name))
            )
            for name, specification in raw_inputs.items()
        }
        if isinstance(raw_inputs, Mapping)
        else {}
    )
    delivery_policy = (
        {
            key: _complete_projection(value, key=str(key))
            for key, value in delivery.items()
            if key != "inputs"
        }
        if isinstance(delivery, Mapping)
        else {}
    )
    sensitive_node_types = {
        "prompt",
        "bash",
        "script",
        "loop",
        "approval",
        "cancel",
    }
    projection = {
        "name": definition.name,
        "description": _complete_projection(definition.description, key="description"),
        "nodes": [
            {
                "id": node.id,
                "type": node.node_type,
                "value": (
                    "[REDACTED]"
                    if node.node_type in sensitive_node_types
                    else _complete_projection(node.value, key="value")
                ),
                "depends_on": list(node.depends_on),
                "options": _options_projection(node.options),
            }
            for node in definition.nodes
        ],
        "edges": [
            {"from": dependency, "to": node.id}
            for node in definition.nodes
            for dependency in node.depends_on
        ],
        "inputs": inputs,
        "policies": {
            "workflow": _options_projection(definition.options),
            "sidecar": {
                key: _complete_projection(value, key=str(key))
                for key, value in package.sidecar.items()
                if key != "delivery_defaults"
            },
            "delivery": delivery_policy,
        },
    }
    encoded = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > WORKFLOW_DEFINITION_MAX_BYTES:
        raise WorkflowDefinitionProjectionCapacityError(
            "workflow definition byte limit exceeded"
        )
    return projection


def show_package(
    package: WorkflowPackage,
    *,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
    cron_jobs: Iterable[Mapping[str, object]] = (),
    compatibility_report: CompatibilityReport | None = None,
    include_argument_hints: bool = True,
    compilation: WorkflowCompilation | None = None,
) -> dict[str, object]:
    report = compatibility_report or assess_compatibility(
        package,
        available_tools=available_tools,
        available_services=available_services,
        provider_capabilities=provider_capabilities,
        isolated_workdir=isolated_workdir,
        mcp_available=mcp_available,
    )
    result = _catalog_summary(package, report)
    topology = project_topology(package.definition)
    node_counts = Counter(node.node_type for node in package.definition.nodes)
    requested_tools = {
        ARCHON_TOOL_ALIASES.get(str(tool), str(tool))
        for node in package.definition.nodes
        for field in ("allowed_tools", "denied_tools")
        for tool in node.options.get(field, ())
    }
    requested_skills = {
        str(skill)
        for node in package.definition.nodes
        for skill in node.options.get("skills", ())
    }
    requested_mcp = {
        str(reference)
        for node in package.definition.nodes
        for reference in (
            (node.options.get("mcp"),)
            if isinstance(node.options.get("mcp"), str)
            else node.options.get("mcp", ())
        )
        if isinstance(reference, str)
    }
    providers = {
        str(provider)
        for provider in [
            package.definition.options.get("provider"),
            *(node.options.get("provider") for node in package.definition.nodes),
        ]
        if isinstance(provider, str) and provider
    }
    runtimes = {
        str(node.options["runtime"])
        for node in package.definition.nodes
        if node.node_type == "script" and "runtime" in node.options
    }
    result.update({
        "action": "show",
        "definition": _definition_projection(package),
        "argument_hints": _argument_hints(package) if include_argument_hints else {},
        "topology_text": topology.text,
        "topology_mermaid": topology.mermaid,
        "topology_warnings": list(topology.warnings),
        "node_type_counts": dict(sorted(node_counts.items())),
        "approval_nodes": [
            node.id for node in package.definition.nodes if node.node_type == "approval"
        ],
        "outward_action_nodes": list(package.sidecar.get("outward_action_nodes", ())),
        "required_tools": sorted(requested_tools),
        "required_skills": sorted(requested_skills),
        "required_mcp": sorted(requested_mcp),
        "required_providers": sorted(providers),
        "required_runtimes": sorted(runtimes),
        "related_cron_schedules": _related_cron(package.definition.name, cron_jobs),
        "blocking_findings": [
            {
                "path": finding.path,
                "level": finding.level.value,
                "message": finding.message,
            }
            for finding in report.blocking_findings
        ],
    })
    result["requirements"] = {
        "tools": result["required_tools"],
        "skills": result["required_skills"],
        "mcp": result["required_mcp"],
        "providers": result["required_providers"],
        "runtimes": result["required_runtimes"],
        "arguments": result["argument_hints"],
    }
    result["approvals"] = {
        "nodes": result["approval_nodes"],
        "outward_actions": result["outward_action_nodes"],
    }
    result["schedules"] = result["related_cron_schedules"]
    result["warnings"] = [item["message"] for item in result["blocking_findings"]]
    result["next_actions"] = ["run"] if report.runnable else ["doctor"]
    if compilation is not None and supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        risk = build_risk_summary(
            package,
            report,
            compilation=compilation,
        )
        result["compilation"] = compilation_diagnostics(
            compilation,
            risk_summary=risk,
        )
    return result


def compilation_diagnostics(
    compilation: WorkflowCompilation,
    *,
    risk_summary=None,
) -> dict[str, object]:
    """Return one body-free, bounded projection of an authenticated closure."""
    package = compilation.package
    if not supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        raise ValueError("compilation diagnostics require Phase 4 semantics")
    manifest = compilation.dependency_manifest
    root = manifest.root.to_dict()
    dependencies = [item.to_dict() for item in manifest.dependencies]
    sources = [
        {
            "package_key": item["package_key"],
            "workflow_name": item["workflow_name"],
            "catalog_source": item["catalog_source"],
            "precedence": item["precedence"],
            "definition_location": item["definition_location"],
            "sidecar_status": item["sidecar_status"],
            "ignored_policy_fields": item["ignored_policy_fields"],
        }
        for item in (root, *dependencies)
    ]
    include_edges = [item.to_dict() for item in manifest.include_edges]
    counts = manifest.counts.to_dict()
    counts["expanded_edges"] = sum(
        len(node.depends_on) for node in package.definition.nodes
    )
    risk = risk_summary.to_dict() if risk_summary is not None else {}
    projection: dict[str, object] = {
        "schema_version": 1,
        "effective_profile": package.language.effective_profile.value,
        "normalizer_version": package.language.normalizer_version,
        "snapshot_format_version": 2,
        "dependency_manifest_schema_version": manifest.schema_version,
        "composite_digest": compilation.composite_digest,
        "root": root,
        "dependencies": dependencies,
        "sources": sources,
        "counts": counts,
        "include_depth": max(
            (len(item["include_instance_path"]) for item in include_edges),
            default=0,
        ),
        "include_edges": include_edges,
        "ignored_policies": risk.get("ignored_child_policies", []),
        "origin_risks": risk.get("origin_risks", []),
        "node_origins": [dict(item) for item in manifest.node_origins],
        "resource_origins": [item.to_dict() for item in manifest.resources],
    }
    projection["truncated"] = projection_was_truncated(projection)
    sanitized = sanitize_projection(projection)
    if not isinstance(sanitized, dict):
        raise TypeError("compilation diagnostics must project to a mapping")
    return sanitized


def _compilation_text_items(
    diagnostics: Mapping[str, object], key: str
) -> list[object]:
    value = diagnostics.get(key)
    if not isinstance(value, list | tuple):
        return []
    return list(value[:_COMPILATION_TEXT_ITEMS_MAX])


def _compilation_text_json(value: object) -> str:
    return json.dumps(
        sanitize_projection(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _print_compilation_diagnostics(diagnostics: Mapping[str, object]) -> None:
    """Render the bounded machine projection without source or runtime bodies."""
    print(
        "Compilation: "
        f"profile={diagnostics['effective_profile']} "
        f"normalizer={diagnostics['normalizer_version']} "
        f"snapshot={diagnostics['snapshot_format_version']} "
        f"manifest={diagnostics['dependency_manifest_schema_version']}"
    )
    print(f"Composite digest: {diagnostics['composite_digest']}")
    counts = diagnostics.get("counts")
    if not isinstance(counts, Mapping):
        counts = {}
    print(
        "Expansion: "
        f"dependencies={counts.get('dependency_packages', 0)} "
        f"expanded_nodes={counts.get('expanded_nodes', 0)} "
        f"expanded_edges={counts.get('expanded_edges', 0)} "
        f"include_depth={diagnostics.get('include_depth', 0)}"
    )
    sections = (
        ("Dependencies", "dependencies"),
        ("Sources and precedence", "sources"),
        ("Ignored child policies", "ignored_policies"),
        ("Logical node origins", "node_origins"),
        ("Logical resource origins", "resource_origins"),
        ("Per-origin risks", "origin_risks"),
    )
    for title, key in sections:
        items = _compilation_text_items(diagnostics, key)
        print(f"{title} ({len(items)}):")
        for item in items:
            print(f"- {_compilation_text_json(item)}")
    print(
        "Diagnostics truncated: "
        f"{'true' if diagnostics.get('truncated') is True else 'false'}"
    )


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output")


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subparser.parse_known_args = MethodType(
        _WorkflowArgumentParser.parse_known_args, subparser
    )
    subparser.error = MethodType(_WorkflowArgumentParser.error, subparser)
    subparser.add_argument("--workdir", default=os.getcwd(), help=argparse.SUPPRESS)
    subparser.add_argument(
        "--hermes-home", default=str(get_hermes_home()), help=argparse.SUPPRESS
    )
    actions = subparser.add_subparsers(
        dest="workflow_action", parser_class=_WorkflowArgumentParser
    )

    schema_parser = actions.add_parser(
        "schema", help="Print the workflow authoring contract"
    )
    configure_schema_parser(schema_parser)

    list_parser = actions.add_parser(
        "list", aliases=["ls"], help="List discovered workflows"
    )
    _json_flag(list_parser)

    show_parser = actions.add_parser(
        "show", help="Show a redacted workflow catalog entry"
    )
    show_parser.add_argument("name")
    show_parser.add_argument(
        "--topology", choices=("text", "mermaid", "both"), default=None
    )
    _json_flag(show_parser)

    validate_parser = actions.add_parser("validate", help="Validate a workflow package")
    validate_parser.add_argument("name")
    _json_flag(validate_parser)

    doctor_parser = actions.add_parser(
        "doctor", help="Inspect compatibility and execution risk"
    )
    doctor_parser.add_argument("name")
    doctor_parser.add_argument("--compat-report", action="store_true")
    doctor_parser.add_argument("--mode", choices=("foreground", "background"))
    _json_flag(doctor_parser)

    trust_parser = actions.add_parser("trust", help="Trust the current package digest")
    trust_parser.add_argument("name")
    trust_parser.add_argument("--digest", required=True)
    _json_flag(trust_parser)

    untrust_parser = actions.add_parser("untrust", help="Revoke package trust")
    untrust_parser.add_argument("name")
    _json_flag(untrust_parser)

    run_parser = actions.add_parser("run", help="Start a durable workflow run")
    run_parser.add_argument("name")
    run_parser.add_argument("--arguments", default="")
    run_parser.add_argument("--idempotency-key")
    run_parser.add_argument("--concurrency-key")
    run_parser.add_argument(
        "--trigger-source",
        choices=("cli", "chat", "background_agent", "cron"),
        default="cli",
        help="Local-admin-claimed origin for this shell admission",
    )
    run_parser.add_argument("--source-instance")
    run_parser.add_argument("--claimed-actor")
    execution = run_parser.add_mutually_exclusive_group()
    execution.add_argument("--no-wait", action="store_true")
    execution.add_argument(
        "--foreground",
        action="store_true",
        help="Execute locally when no coordinator is available",
    )
    _json_flag(run_parser)

    runs_parser = actions.add_parser("runs", help="List active and recent runs")
    runs_parser.add_argument("--workflow")
    runs_parser.add_argument("--status")
    runs_parser.add_argument("--limit", type=int, default=100)
    runs_parser.add_argument(
        "--view", choices=("all", "board", "history", "archive"), default="all"
    )
    _json_flag(runs_parser)

    status_parser = actions.add_parser("status", help="Inspect a durable run")
    status_parser.add_argument("run_id", nargs="?")
    _json_flag(status_parser)

    events_parser = actions.add_parser("events", help="Show sanitized run events")
    events_parser.add_argument("run_id")
    events_parser.add_argument("--tail", type=int, default=50)
    _json_flag(events_parser)

    approve_parser = actions.add_parser(
        "approve", help="Approve a paused workflow gate"
    )
    approve_parser.add_argument("run_id")
    approve_parser.add_argument("--comment", default="")
    approve_parser.add_argument("--interaction-id", required=True)
    approve_parser.add_argument("--expected-version", type=int, required=True)
    approve_parser.add_argument("--continue", dest="continue_run", action="store_true")
    _json_flag(approve_parser)

    reject_parser = actions.add_parser("reject", help="Reject a paused workflow gate")
    reject_parser.add_argument("run_id")
    reject_parser.add_argument("--reason", default="")
    reject_parser.add_argument("--interaction-id", required=True)
    reject_parser.add_argument("--expected-version", type=int, required=True)
    reject_parser.add_argument("--continue", dest="continue_run", action="store_true")
    _json_flag(reject_parser)

    input_parser = actions.add_parser(
        "provide-input", help="Provide bounded input to a paused workflow loop"
    )
    input_parser.add_argument("run_id")
    input_parser.add_argument("interaction_id")
    input_parser.add_argument("value")
    input_parser.add_argument("--expected-version", type=int, required=True)
    input_parser.add_argument("--continue", dest="continue_run", action="store_true")
    _json_flag(input_parser)

    retry_parser = actions.add_parser("retry", help="Retry one failed workflow node")
    retry_parser.add_argument("run_id")
    retry_parser.add_argument("node_id", nargs="?")
    retry_parser.add_argument("--expected-version", type=int)
    retry_parser.add_argument("--continue", dest="continue_run", action="store_true")
    _json_flag(retry_parser)

    reconcile_parser = actions.add_parser(
        "reconcile", help="Resolve an unknown outward-action outcome"
    )
    reconcile_parser.add_argument("run_id")
    reconcile_parser.add_argument(
        "outcome",
        choices=("confirmed-succeeded", "confirmed-failed", "safe-to-retry"),
    )
    reconcile_parser.add_argument("--interaction-id", required=True)
    reconcile_parser.add_argument("--expected-version", type=int)
    reconcile_parser.add_argument(
        "--continue", dest="continue_run", action="store_true"
    )
    _json_flag(reconcile_parser)

    for action, help_text in (
        ("resume", "Resume an interrupted workflow run"),
        ("cancel", "Cancel a workflow run"),
        ("abandon", "Abandon a stopped workflow run"),
    ):
        parser = actions.add_parser(action, help=help_text)
        parser.add_argument("run_id")
        _json_flag(parser)

    for action, help_text in (
        ("archive", "Reversibly hide terminal run evidence"),
        ("restore", "Restore archived evidence to history"),
    ):
        parser = actions.add_parser(action, help=help_text)
        parser.add_argument("run_id")
        parser.add_argument("--expected-version", type=int, required=True)
        _json_flag(parser)

    cleanup_parser = actions.add_parser("cleanup", help="Clean retained terminal runs")
    cleanup_parser.add_argument("--older-than", default="7d")
    cleanup_parser.add_argument("--execute", action="store_true")
    cleanup_parser.add_argument("--confirmation-token")
    _json_flag(cleanup_parser)

    reset_parser = actions.add_parser(
        "reset-sessions", help="Reset persistent workflow node sessions"
    )
    reset_parser.add_argument("name")
    reset_parser.add_argument("--scope")
    reset_parser.add_argument("--node")
    reset_parser.add_argument("--yes", action="store_true")
    _json_flag(reset_parser)

    showcase_parser = actions.add_parser(
        "showcase", help="Run digest-verified guided workflow demonstrations"
    )
    showcase_actions = showcase_parser.add_subparsers(dest="showcase_action")
    showcase_list = showcase_actions.add_parser("list", help="List safe showcases")
    _json_flag(showcase_list)
    for action in ("describe", "preflight"):
        parser = showcase_actions.add_parser(action)
        parser.add_argument("showcase_id")
        _json_flag(parser)
    showcase_run = showcase_actions.add_parser("run")
    showcase_run.add_argument("showcase_id")
    showcase_run.add_argument("--symptom")
    showcase_run.add_argument("--confirmation-token")
    showcase_run.add_argument("--schedule-at")
    showcase_run.add_argument("--idempotency-key")
    showcase_run.add_argument(
        "--trigger-source",
        choices=("cli", "chat", "background_agent", "cron"),
        default="cli",
    )
    showcase_run.add_argument("--source-instance")
    showcase_run.add_argument("--claimed-actor")
    showcase_run.add_argument("--no-wait", action="store_true")
    _json_flag(showcase_run)
    showcase_status = showcase_actions.add_parser("status")
    showcase_status.add_argument("run_id")
    _json_flag(showcase_status)
    showcase_report = showcase_actions.add_parser("report")
    showcase_report.add_argument("run_id")
    _json_flag(showcase_report)
    showcase_reset = showcase_actions.add_parser("reset")
    showcase_reset.add_argument("showcase_id")
    _json_flag(showcase_reset)
    showcase_cleanup = showcase_actions.add_parser("cleanup")
    showcase_cleanup.add_argument("--older-than-days", type=int, default=7)
    showcase_cleanup.add_argument("--execute", action="store_true")
    showcase_cleanup.add_argument("--confirmation-token")
    _json_flag(showcase_cleanup)

    subparser.set_defaults(func=workflow_command)


def _discover(args: argparse.Namespace) -> tuple[WorkflowPackage, ...]:
    return discover_workflows(
        Path(args.workdir),
        Path(args.hermes_home),
        Path.home(),
    )


def _resolve(args: argparse.Namespace, name: str) -> WorkflowPackage:
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return load_workflow(candidate)
    packages = _discover(args)
    for package in packages:
        if package.definition.name == name:
            return package
    candidates = [
        {
            "id": package.definition.name,
            "kind": "workflow",
            "label": package.definition.name,
        }
        for package in sorted(packages, key=lambda item: item.definition.name)[:10]
    ]
    raise WorkflowNotFound(
        f"workflow not found: {name}",
        details={"candidates": candidates},
    )


def _resolve_compilation(args: argparse.Namespace, name: str) -> WorkflowCompilation:
    """Resolve one admission target as a single immutable compilation."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    candidate = Path(name).expanduser()
    if candidate.is_file():
        workflow_path = candidate.resolve()
        sidecar_path = workflow_path.with_name(f"{workflow_path.stem}.hermes.yaml")
        sidecar_bytes = sidecar_path.read_bytes() if sidecar_path.is_file() else None
        source = parse_workflow_source_bytes(
            workflow_path,
            workflow_bytes=workflow_path.read_bytes(),
            sidecar_bytes=sidecar_bytes,
            source="explicit",
            precedence=0,
        )
        from plugins.workflow.language import (
            resolve_language_profile,
            select_normalizer_version,
            supports_phase4_semantics,
        )

        language = resolve_language_profile(source.sidecar)
        normalizer_version = select_normalizer_version(language, None)
        if supports_phase4_semantics(
            language.effective_profile,
            normalizer_version,
        ):
            from plugins.workflow.catalog_api import capture_workflow_catalog_snapshot

            catalog = capture_workflow_catalog_snapshot(
                workdir=Path(args.workdir),
                hermes_home=Path(args.hermes_home),
                additional_sources=(source,),
            )
        else:
            catalog = WorkflowCatalogSnapshot.capture((source,))
        return compile_workflow(
            source,
            catalog,
            normalizer_version=normalizer_version,
        )

    from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation

    compilation = resolve_workflow_catalog_compilation(
        name,
        hermes_home=args.hermes_home,
        workdir=args.workdir,
    )
    if compilation is not None:
        return compilation
    packages = _discover(args)
    candidates = [
        {
            "id": package.definition.name,
            "kind": "workflow",
            "label": package.definition.name,
        }
        for package in sorted(packages, key=lambda item: item.definition.name)[:10]
    ]
    raise WorkflowNotFound(
        f"workflow not found: {name}",
        details={"candidates": candidates},
    )


def _admission_compilation(
    compilation: WorkflowCompilation,
) -> WorkflowCompilation | None:
    package = compilation.package
    if supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        return compilation
    return None


def _admission_package_digest(
    compilation: WorkflowCompilation,
) -> WorkflowPackageDigest:
    if _admission_compilation(compilation) is not None:
        return WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        )
    return compute_package_digest(compilation.package)


def _compilation_trust_material(
    compilation: WorkflowCompilation,
) -> tuple[WorkflowPackageDigest, CompatibilityReport, WorkflowRiskSummary]:
    """Return the exact digest, compatibility, and risk for one compilation."""
    package = compilation.package
    assessment = _phase5_admission_assessment(compilation)
    if assessment is not None:
        return (
            assessment.package_digest,
            assessment.compatibility,
            assessment.risk,
        )
    compatibility = assess_compatibility(package)
    risk = build_risk_summary(
        package,
        compatibility,
        compilation=_admission_compilation(compilation),
    )
    digest = _admission_package_digest(compilation)
    return digest, compatibility, risk


def _phase5_admission_assessment(
    compilation: WorkflowCompilation,
    capability_snapshot: ConnectorCapabilitySnapshot | None = None,
) -> WorkflowAdmissionAssessment | None:
    package = compilation.package
    if not supports_phase5_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        return None
    capabilities = capability_snapshot or connector_capability_snapshot()
    return assess_production_workflow_admission(
        compilation,
        available_tools=capabilities.available_tools,
        available_services=capabilities.ready_services,
    )


def _cron_jobs() -> Iterable[Mapping[str, object]]:
    try:
        from cron.jobs import list_jobs

        return list_jobs(include_disabled=True)
    except Exception:
        return ()


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        command = _MACHINE_COMMAND.get()
        print(
            json.dumps(
                success_envelope(command, payload),
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
        )
    elif isinstance(payload, str):
        print(payload)
    else:
        print(payload)


def _cmd_schema(args: argparse.Namespace) -> int:
    return emit_schema(args)


def _cmd_list(args: argparse.Namespace) -> int:
    entries = build_catalog(_discover(args))
    if args.json:
        _emit(entries, as_json=True)
        return 0
    if not entries:
        print("No portable workflows discovered.")
        return 0
    for entry in entries:
        print(
            f"{entry['name']}\t{entry['compatibility']['level']}\t"
            f"{'runnable' if entry['runnable'] else 'blocked'}\t"
            f"{entry['source']}:{entry['precedence']}\t{entry['description']}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if args.json and args.topology is not None:
        raise WorkflowCommandError(
            "invalid_request",
            "--topology cannot be combined with --json; JSON show always includes both projections",
            exit_code=EXIT_INVOCATION,
        )
    package = _resolve(args, args.name)
    compilation = (
        _resolve_compilation(args, args.name)
        if isinstance(package, WorkflowPackage)
        and supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        else None
    )
    assessment = (
        _phase5_admission_assessment(compilation)
        if compilation is not None
        else None
    )
    detail = show_package(
        compilation.package if compilation is not None else package,
        cron_jobs=_cron_jobs(),
        compilation=compilation,
        compatibility_report=(
            assessment.compatibility if assessment is not None else None
        ),
    )
    if args.json:
        _emit(detail, as_json=True)
        return 0
    print(f"Workflow: {detail['name']}")
    print(f"Description: {detail['description']}")
    print(f"Source: {detail['source']} (precedence {detail['precedence']})")
    print(f"Compatibility: {detail['compatibility']['level']}")
    diagnostics = detail.get("compilation")
    if isinstance(diagnostics, Mapping):
        _print_compilation_diagnostics(diagnostics)
    selector = args.topology or "text"
    if selector in {"text", "both"}:
        print(f"Topology: {detail['topology_text']}")
    if selector in {"mermaid", "both"}:
        if detail["topology_mermaid"] is None:
            print(
                "Mermaid topology unavailable: "
                + ", ".join(detail["topology_warnings"])
            )
        else:
            print("Topology (Mermaid):")
            print("```mermaid")
            print(detail["topology_mermaid"])
            print("```")
    return 0


def _language_payload(package: WorkflowPackage) -> dict[str, object]:
    metadata = package.language
    payload = language_projection(metadata)
    payload["legacy"] = (
        metadata.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY
    )
    return payload


def _cmd_validate(args: argparse.Namespace) -> int:
    package = _resolve(args, args.name)
    compilation = (
        _resolve_compilation(args, args.name)
        if supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        else None
    )
    if compilation is not None:
        package = compilation.package
    issues = validate_package(package)
    assessment = (
        _phase5_admission_assessment(compilation)
        if compilation is not None
        else None
    )
    compatibility = (
        assessment.compatibility
        if assessment is not None
        else assess_compatibility(package)
    )
    if any(issue.blocking for issue in issues) and compatibility.runnable:
        compatibility = replace(
            compatibility,
            level=CompatibilityLevel.UNSUPPORTED,
            runnable=False,
        )
    issue_entries = []
    seen_issues = set()
    for issue in issues:
        identity = (issue.code, issue.path)
        if identity in seen_issues:
            continue
        seen_issues.add(identity)
        issue_entries.append({
            "path": issue.path,
            "code": issue.code,
            "message": issue.message,
            "severity": issue.severity,
            "blocking": issue.blocking,
            "source_line": issue.source_line,
        })
    for finding in compatibility.findings:
        identity = (finding.code, finding.path)
        if identity in seen_issues:
            continue
        seen_issues.add(identity)
        issue_entries.append({
            "path": finding.path,
            "code": finding.code,
            "message": finding.message,
            "severity": finding.severity,
            "blocking": finding.blocking,
            "source_line": None,
        })
    payload = {
        "name": package.definition.name,
        "valid": compatibility.runnable,
        "language": _language_payload(package),
        "issues": issue_entries,
    }
    if supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        assert compilation is not None
        payload["compilation"] = compilation_diagnostics(
            compilation,
            risk_summary=(
                assessment.risk
                if assessment is not None
                else build_risk_summary(
                    package,
                    compatibility,
                    compilation=compilation,
                )
            ),
        )
    try:
        require_runnable(compatibility)
    except WorkflowCompatibilityBlockedError as exc:
        if args.json:
            raise WorkflowCommandError(
                "validation_failed",
                "workflow validation found blocking issues",
                exit_code=EXIT_BLOCKING_FINDING,
                result=payload,
            ) from exc
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(
            f"{package.definition.name}: {'valid' if payload['valid'] else 'invalid'}"
        )
        print(f"Language: {payload['language']['effective_profile']}")
        diagnostics = payload.get("compilation")
        if isinstance(diagnostics, Mapping):
            _print_compilation_diagnostics(diagnostics)
        for issue in payload["issues"]:
            print(f"- {issue['severity']}: {issue['path']}: {issue['message']}")
    return 0 if payload["valid"] else EXIT_BLOCKING_FINDING


_MCP_ENV_REFERENCE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)
_INPUT_KINDS = frozenset({"text", "file", "directory", "json"})
_CONCURRENCY_POLICIES = frozenset({"queue", "allow", "forbid"})


def _doctor_finding(
    *,
    code: str,
    path: str,
    message: str,
    blocking: bool = False,
    level: CompatibilityLevel = CompatibilityLevel.MAPPED,
) -> CompatibilityFinding:
    return CompatibilityFinding(
        path=path,
        level=level,
        message=message,
        blocking=blocking,
        code=code,
        severity="error" if blocking else "info",
    )


def _input_requirements(
    package: WorkflowPackage, findings: list[CompatibilityFinding]
) -> tuple[InputRequirement, ...]:
    delivery = package.sidecar.get("delivery_defaults", {})
    if delivery is None:
        return ()
    if not isinstance(delivery, Mapping):
        findings.append(
            _doctor_finding(
                code="invalid_input_requirements",
                path="sidecar.delivery_defaults",
                message="delivery_defaults must contain a mapping",
                blocking=True,
                level=CompatibilityLevel.UNSUPPORTED,
            )
        )
        return ()
    raw_inputs = delivery.get("inputs", {})
    if not isinstance(raw_inputs, Mapping):
        findings.append(
            _doctor_finding(
                code="invalid_input_requirements",
                path="sidecar.delivery_defaults.inputs",
                message="delivery default inputs must contain a mapping",
                blocking=True,
                level=CompatibilityLevel.UNSUPPORTED,
            )
        )
        return ()
    requirements: list[InputRequirement] = []
    for name, raw in sorted(raw_inputs.items(), key=lambda item: str(item[0])):
        path = f"sidecar.delivery_defaults.inputs.{name}"
        if not isinstance(name, str) or not name or not isinstance(raw, Mapping):
            findings.append(
                _doctor_finding(
                    code="invalid_input_requirements",
                    path=path,
                    message="input requirements must be named mappings",
                    blocking=True,
                    level=CompatibilityLevel.UNSUPPORTED,
                )
            )
            continue
        kind = raw.get("kind", "text")
        required = raw.get("required", True)
        max_bytes = raw.get("max_bytes")
        valid = (
            kind in _INPUT_KINDS
            and isinstance(required, bool)
            and (
                max_bytes is None
                or (
                    isinstance(max_bytes, int)
                    and not isinstance(max_bytes, bool)
                    and max_bytes > 0
                )
            )
        )
        if not valid:
            findings.append(
                _doctor_finding(
                    code="invalid_input_requirements",
                    path=path,
                    message="input kind, required flag, or byte ceiling is invalid",
                    blocking=True,
                    level=CompatibilityLevel.UNSUPPORTED,
                )
            )
            continue
        requirements.append(
            InputRequirement(
                name=name,
                kind=kind,
                required=required,
                max_bytes=max_bytes,
            )
        )
        findings.append(
            _doctor_finding(
                code="immutable_input_snapshot",
                path=path,
                message=(
                    f"{kind} input {name} is validated, size-bounded, and copied "
                    "into the immutable run snapshot before admission"
                ),
            )
        )
    return tuple(requirements)


def _mcp_environment_names(
    package: WorkflowPackage,
    *,
    compilation: WorkflowCompilation | None = None,
) -> tuple[str, ...]:
    names: set[str] = set()
    if compilation is not None:
        for binding in compilation.dependency_manifest.resources:
            if binding.resource_kind not in {"mcp", "mcp_resource"}:
                continue
            try:
                text = compilation.sealed_files[binding.snapshot_path].decode("utf-8")
            except (KeyError, UnicodeError):
                continue
            for match in _MCP_ENV_REFERENCE.finditer(text):
                names.add(match.group(1) or match.group(2))
        return tuple(sorted(names))
    digest = compute_package_digest(package)
    for relative in digest.covered_relative_paths:
        if not relative.startswith("mcp/"):
            continue
        path = package.root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in _MCP_ENV_REFERENCE.finditer(text):
            names.add(match.group(1) or match.group(2))
    return tuple(sorted(names))


def _doctor_compatibility_level(
    findings: Iterable[CompatibilityFinding],
) -> CompatibilityLevel:
    materialized = tuple(findings)
    if any(finding.level is CompatibilityLevel.UNSUPPORTED for finding in materialized):
        return CompatibilityLevel.UNSUPPORTED
    if materialized:
        return CompatibilityLevel.MAPPED
    return CompatibilityLevel.PORTABLE


def _profile_config(hermes_home: str | Path) -> Mapping[str, object]:
    from hermes_cli.config import load_config_readonly

    path = Path(hermes_home) / "config.yaml"
    try:
        config = load_config_readonly(path)
    except (OSError, UnicodeError):
        return {}
    return config if isinstance(config, Mapping) else {}


def _provider_override_findings(
    package: WorkflowPackage,
    *,
    hermes_home: str | Path,
) -> tuple[CompatibilityFinding, ...]:
    from agent.plugin_agent import _agent_override_allowed

    config = _profile_config(hermes_home)
    requested: list[tuple[str, str, str]] = []
    for kind in ("provider", "model"):
        workflow_value = package.definition.options.get(kind)
        if isinstance(workflow_value, str) and workflow_value:
            requested.append((kind, kind, workflow_value))
    for index, node in enumerate(package.definition.nodes):
        for kind in ("provider", "model"):
            value = node.options.get(kind)
            if isinstance(value, str) and value:
                requested.append((kind, f"nodes[{index}].{kind}", value))
    return tuple(
        _doctor_finding(
            code=f"{kind}_override_not_authorized",
            path=path,
            message=(f"workflow agent {kind} override is disabled in profile config"),
            blocking=True,
            level=CompatibilityLevel.UNSUPPORTED,
        )
        for kind, path, value in requested
        if not _agent_override_allowed("workflow", kind, value, config=config)
    )


def _structured_output_validator_available(
    structured_outputs: Iterable[WorkflowStructuredOutput],
) -> bool:
    """Probe the exact optional Draft 2020-12 API execution requires."""
    try:
        for structured_output in structured_outputs:
            require_structured_output_validator(structured_output.canonical_schema)
    except StructuredOutputValidatorUnavailable:
        return False
    return True


def doctor_package(
    package: WorkflowPackage,
    *,
    hermes_home: str | Path,
    compilation: WorkflowCompilation | None = None,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    available_skills: AbstractSet[str] | None = None,
    available_runtimes: AbstractSet[str] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
    environment: Mapping[str, str] | None = None,
    runtime_config: WorkflowRuntimeConfig | None = None,
    execution_context: "ExecutionCapabilityContext | None" = None,
) -> DoctorReport:
    """Inspect a package without starting a provider, worker, or MCP server."""
    assessment = None
    if compilation is not None and supports_phase5_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        assessment = (
            assess_workflow_admission(
                compilation,
                execution_context,
                available_tools=available_tools,
                available_services=available_services,
                isolated_workdir=isolated_workdir,
            )
            if execution_context is not None
            else assess_production_workflow_admission(
                compilation,
                available_tools=available_tools,
                available_services=available_services,
                isolated_workdir=isolated_workdir,
            )
        )
        compatibility = assessment.compatibility
    else:
        compatibility = assess_compatibility(
            package,
            available_tools=available_tools,
            available_services=available_services,
            provider_capabilities=provider_capabilities,
            isolated_workdir=isolated_workdir,
            mcp_available=mcp_available,
        )
    findings = list(compatibility.findings)
    if package.language.structured_outputs:
        try:
            validator_available = _structured_output_validator_available(
                package.language.structured_outputs.values()
            )
        except StructuredOutputSchemaInvalid as exc:
            findings.append(
                _doctor_finding(
                    code="structured_output_invalid",
                    path="language.structured_outputs",
                    message=str(exc),
                    blocking=True,
                    level=CompatibilityLevel.UNSUPPORTED,
                )
            )
        else:
            if not validator_available:
                findings.append(
                    _doctor_finding(
                        code="structured_output_unavailable",
                        path="language.structured_outputs",
                        message=STRUCTURED_OUTPUT_VALIDATOR_INSTALL_GUIDANCE,
                        blocking=True,
                        level=CompatibilityLevel.UNSUPPORTED,
                    )
                )
    findings.extend(_provider_override_findings(package, hermes_home=hermes_home))
    phase4_compilation = (
        compilation
        if compilation is not None
        and supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        else None
    )
    risk = (
        assessment.risk
        if assessment is not None
        else build_risk_summary(
            package,
            compatibility,
            compilation=phase4_compilation,
        )
    )
    package_digest = (
        assessment.package_digest
        if assessment is not None
        else (
            _admission_package_digest(compilation)
            if compilation is not None
            else compute_package_digest(package)
        )
    )
    if phase4_compilation is not None:
        bindings = phase4_compilation.dependency_manifest.resources

        def logical_path(binding) -> str:
            return f"{binding.package_key}::{binding.source_relative_path}"

        covered = tuple(sorted({logical_path(binding) for binding in bindings}))
        commands = tuple(sorted({
            logical_path(binding)
            for binding in bindings
            if binding.resource_kind in {"command", "loop_command"}
        }))
        scripts = tuple(sorted({
            logical_path(binding)
            for binding in bindings
            if binding.resource_kind == "named_script"
        }))
        mcp_servers = tuple(sorted({
            logical_path(binding)
            for binding in bindings
            if binding.resource_kind == "mcp"
        }))
    else:
        covered = package_digest.covered_relative_paths
        commands = tuple(path for path in covered if path.startswith("commands/"))
        scripts = tuple(path for path in covered if path.startswith("scripts/"))
        mcp_servers = tuple(path for path in covered if path.startswith("mcp/"))
    skills = tuple(sorted(set(risk.requested_skills)))

    requirements = _input_requirements(package, findings)
    runtimes = (
        frozenset(
            runtime for runtime in ("bun", "uv") if shutil.which(runtime) is not None
        )
        if available_runtimes is None
        else frozenset(available_runtimes)
    )
    for index, node in enumerate(package.definition.nodes):
        if node.node_type == "script":
            runtime = str(node.options.get("runtime", ""))
            if runtime not in runtimes:
                findings.append(
                    _doctor_finding(
                        code="missing_runtime",
                        path=f"nodes[{index}].runtime",
                        message=f"required script runtime is unavailable: {runtime}",
                        blocking=True,
                        level=CompatibilityLevel.UNSUPPORTED,
                    )
                )
        if node.options.get("agents") and not any(
            finding.path == f"nodes[{index}].agents" for finding in findings
        ):
            findings.append(
                _doctor_finding(
                    code="inline_agent_bounds",
                    path=f"nodes[{index}].agents",
                    message="inline agents inherit bounded workflow worker and deadline ceilings",
                )
            )

    if available_skills is not None:
        for skill in skills:
            if skill not in available_skills:
                findings.append(
                    _doctor_finding(
                        code="missing_skill",
                        path="skills",
                        message=f"required skill is unavailable: {skill}",
                        blocking=True,
                        level=CompatibilityLevel.UNSUPPORTED,
                    )
                )

    required_services = package.sidecar.get("required_services", ())
    if not isinstance(required_services, tuple | list) or any(
        not isinstance(service, str) or not service for service in required_services
    ):
        findings.append(
            _doctor_finding(
                code="invalid_required_services",
                path="sidecar.required_services",
                message="required_services must contain configured service names",
                blocking=True,
                level=CompatibilityLevel.UNSUPPORTED,
            )
        )
    else:
        for service in required_services:
            missing = (
                available_services is not None and service not in available_services
            )
            findings.append(
                _doctor_finding(
                    code="missing_service"
                    if missing
                    else "configured_service_preflight",
                    path=f"sidecar.required_services.{service}",
                    message=(
                        f"required configured service is unavailable: {service}"
                        if missing
                        else f"configured service is checked before execution: {service}"
                    ),
                    blocking=missing,
                    level=(
                        CompatibilityLevel.UNSUPPORTED
                        if missing
                        else CompatibilityLevel.MAPPED
                    ),
                )
            )

    visible_environment = os.environ if environment is None else environment
    for name in _mcp_environment_names(
        package,
        compilation=phase4_compilation,
    ):
        if not visible_environment.get(name):
            findings.append(
                _doctor_finding(
                    code="missing_mcp_variable",
                    path=f"mcp.environment.{name}",
                    message=f"required MCP variable is not configured: {name}",
                    blocking=True,
                    level=CompatibilityLevel.UNSUPPORTED,
                )
            )
    for name in risk.required_secret_names:
        if not visible_environment.get(name):
            findings.append(
                _doctor_finding(
                    code="missing_credential",
                    path=f"required_secrets.{name}",
                    message=f"required credential is not configured: {name}",
                    blocking=True,
                    level=CompatibilityLevel.UNSUPPORTED,
                )
            )

    policy = str(package.sidecar.get("overlap_policy") or "queue")
    if policy not in _CONCURRENCY_POLICIES:
        findings.append(
            _doctor_finding(
                code="invalid_overlap_policy",
                path="sidecar.overlap_policy",
                message=f"unsupported overlap policy: {policy}",
                blocking=True,
                level=CompatibilityLevel.UNSUPPORTED,
            )
        )
        policy = "queue"
    findings.append(
        _doctor_finding(
            code=f"overlap_{policy}",
            path="sidecar.overlap_policy",
            message=f"matching concurrent invocations use the {policy} policy",
        )
    )
    if phase4_compilation is not None:
        findings.extend((
            _doctor_finding(
                code="phase4_sealed_closure",
                path="compilation.resources",
                message=(
                    "admitted Phase 4 runs execute only from the sealed root and "
                    "dependency closure"
                ),
            ),
            _doctor_finding(
                code="phase4_future_admission_revalidation",
                path="compilation.composite_digest",
                message=(
                    "later source edits affect future admissions only and require "
                    "a newly validated composite digest"
                ),
            ),
        ))

    base_runtime = runtime_config or _runtime_config(hermes_home)
    limits = package.sidecar.get("limits", {})
    resources = package.sidecar.get("resource_limits", {})
    if not isinstance(limits, Mapping) or not isinstance(resources, Mapping):
        findings.append(
            _doctor_finding(
                code="invalid_resource_ceiling",
                path="sidecar.limits",
                message="workflow limits and resource_limits must be mappings",
                blocking=True,
                level=CompatibilityLevel.UNSUPPORTED,
            )
        )
        effective_runtime = base_runtime
    else:
        effective_runtime = WorkflowRuntimeConfig.from_mapping(
            asdict(base_runtime),
            sidecar_limits=limits,
            sidecar_resources=resources,
        )
    capacity = {
        name: getattr(effective_runtime, name)
        for name in (
            "max_parallel_nodes",
            "max_total_workers",
            "max_executing_runs",
            "max_queued_runs",
            "max_paused_runs",
            "max_nonterminal_runs",
            "max_start_requests_per_minute",
            "process_tree_rss_bytes",
            "process_tree_cpu_seconds",
            "max_descendants",
        )
    }
    findings.append(
        _doctor_finding(
            code="effective_admission_capacity",
            path="runtime.limits",
            message="effective admission and resource ceilings: "
            + json.dumps(capacity, sort_keys=True, separators=(",", ":")),
        )
    )
    findings.append(
        _doctor_finding(
            code="executable_resources_digest_bound",
            path="package.digest",
            message="package digest covers executable resources: " + ", ".join(covered),
        )
    )
    findings.append(
        _doctor_finding(
            code=(
                "isolated_execution_required"
                if risk.execution_environment == "isolated_backend_required"
                else "trusted_local_execution"
            ),
            path="sidecar.execution_environment",
            message=(
                "execution requires an isolated backend with advertised containment"
                if risk.execution_environment == "isolated_backend_required"
                else "trusted package may execute on the configured local backend"
            ),
        )
    )

    findings = [
        replace(
            finding,
            effective_profile=package.language.effective_profile,
        )
        for finding in {
            (finding.code, finding.path): finding for finding in findings
        }.values()
    ]
    trust_state = WorkflowTrustStore(hermes_home).check(
        package_digest.sha256, risk_digest=risk.risk_digest
    )
    return DoctorReport(
        package=str(package.root),
        workflow=package.definition.name,
        runnable=not any(finding.blocking for finding in findings),
        package_digest=package_digest.sha256,
        trust_state=trust_state,
        risk_summary=risk,
        input_requirements=requirements,
        concurrency_policy=policy,
        findings=tuple(findings),
        resolved_commands=commands,
        resolved_scripts=scripts,
        resolved_mcp_servers=mcp_servers,
        resolved_skills=skills,
        provider_capability=(
            None
            if assessment is None or assessment.capability_summary is None
            else dict(assessment.capability_summary)
        ),
    )


def _doctor_payload(
    package: WorkflowPackage,
    *,
    hermes_home: str | Path,
    compat_report: bool,
    mode: str | None = None,
    compilation: WorkflowCompilation | None = None,
) -> dict[str, object]:
    report = doctor_package(
        package,
        hermes_home=hermes_home,
        compilation=compilation,
    )
    payload = report.to_dict()
    payload["package"] = sanitize_projection(str(package.root), key="path")
    payload.update({
        "name": package.definition.name,
        "language": _language_payload(package),
        "compatibility": _doctor_compatibility_level(report.findings).value,
        "remediation": (
            "Resolve blocking compatibility findings, then rerun doctor and trust the current digest."
            if any(finding.blocking for finding in report.findings)
            else "Review this risk summary, then trust the exact package digest before local execution."
        ),
    })
    if compilation is not None and supports_phase4_semantics(
        package.language.effective_profile,
        package.language.normalizer_version,
    ):
        payload["compilation"] = compilation_diagnostics(
            compilation,
            risk_summary=report.risk_summary,
        )
    from plugins.workflow.coordinator_store import CoordinatorStore

    runtime_store = RunStore(hermes_home)
    coordinator = CoordinatorStore(runtime_store.database).health(
        now=datetime.now(timezone.utc)
    )
    payload["machine_contract_schema_version"] = 1
    payload["command_contract"] = operator_command_contract()
    payload["supported_execution_modes"] = ["background", "foreground"]
    payload["coordinator"] = {
        "status": coordinator.status,
        "reason_code": coordinator.reason_code,
        "epoch": coordinator.lease.epoch if coordinator.lease else None,
        "host_kind": coordinator.lease.host_kind if coordinator.lease else None,
        "lease_expires_at": (
            coordinator.lease.lease_expires_at.isoformat()
            if coordinator.lease
            else None
        ),
    }
    mode_findings = []
    effective_profile = package.language.effective_profile.value
    if (
        mode == "foreground"
        and payload["risk_summary"]["execution_environment"]
        == "isolated_backend_required"
    ):
        mode_findings.append({
            "path": "sidecar.execution_environment",
            "level": "unsupported",
            "message": "foreground mode cannot satisfy isolated backend containment",
            "blocking": True,
            "code": "foreground_isolation_unavailable",
            "effective_profile": effective_profile,
        })
    if mode == "background" and coordinator.status != "healthy":
        mode_findings.append({
            "path": "runtime.coordinator",
            "level": "unsupported",
            "message": "background mode requires a healthy workflow coordinator",
            "blocking": True,
            "code": "coordinator_unavailable",
            "effective_profile": effective_profile,
        })
    if mode_findings:
        payload["findings"] = [*payload.get("findings", []), *mode_findings]
        payload["runnable"] = False
        payload["compatibility"] = "unsupported"
        payload["remediation"] = (
            "Resolve blocking findings for the requested mode, then rerun doctor."
        )
    if compat_report:
        payload["compatibility_findings"] = list(payload.get("findings", []))
    return payload


def _doctor_text_value(value: object, *, path: bool = False) -> object:
    """Return one bounded diagnostic value without host-specific path leaks."""
    sanitized = sanitize_projection(value)
    if isinstance(sanitized, str) and path:
        match = _DOCTOR_ABSOLUTE_PATH_START.search(sanitized)
        if match is not None:
            return sanitized[: match.start()] + "[REDACTED_PATH]"
    return sanitized


def _cmd_doctor(args: argparse.Namespace) -> int:
    package = _resolve(args, args.name)
    compilation = (
        _resolve_compilation(args, args.name)
        if isinstance(package, WorkflowPackage)
        and supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        else None
    )
    if compilation is not None:
        package = compilation.package
    payload = _doctor_payload(
        package,
        hermes_home=args.hermes_home,
        compat_report=args.compat_report,
        mode=args.mode,
        compilation=compilation,
    )
    blocking = any(
        isinstance(finding, Mapping) and bool(finding.get("blocking"))
        for finding in payload.get("findings", [])
    )
    if args.json and blocking:
        raise WorkflowCommandError(
            "blocking_doctor_findings",
            "workflow doctor found blocking compatibility or integrity findings",
            exit_code=EXIT_BLOCKING_FINDING,
            result=payload,
        )
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(f"Workflow: {payload['name']}")
        print(f"Package digest: {payload['package_digest']}")
        print(f"Risk digest: {payload['risk_summary']['risk_digest']}")
        print(f"Compatibility: {payload['compatibility']}")
        print(f"Language: {payload['language']['effective_profile']}")
        diagnostics = payload.get("compilation")
        if isinstance(diagnostics, Mapping):
            _print_compilation_diagnostics(diagnostics)
        print(
            f"Execution environment: {payload['risk_summary']['execution_environment']}"
        )
        print(f"Remediation: {payload['remediation']}")
        findings = payload.get("findings", ())
        if not isinstance(findings, list | tuple):
            findings = ()
        for index, finding in enumerate(findings):
            if index >= _DOCTOR_TEXT_FINDINGS_MAX:
                print(
                    "- diagnostics truncated after "
                    f"{_DOCTOR_TEXT_FINDINGS_MAX} findings"
                )
                break
            if not isinstance(finding, Mapping):
                continue
            code = _doctor_text_value(finding.get("code"))
            path = _doctor_text_value(finding.get("path"), path=True)
            migration = _doctor_text_value(finding.get("migration"))
            print(f"- code: {code}")
            print(f"  path: {path}")
            if migration is not None:
                print(f"  migration: {migration}")
    return EXIT_BLOCKING_FINDING if blocking else 0


def _cmd_trust(args: argparse.Namespace) -> int:
    compilation = _resolve_compilation(args, args.name)
    package = compilation.package
    before, compatibility, risk = _compilation_trust_material(compilation)
    if args.digest != before.sha256:
        raise WorkflowCommandError(
            "digest_mismatch",
            "supplied digest does not match the current package digest",
            exit_code=EXIT_INVOCATION,
        )
    require_runnable(compatibility)
    fresh_compilation = _resolve_compilation(args, args.name)
    after, fresh_compatibility, fresh_risk = _compilation_trust_material(
        fresh_compilation
    )
    require_runnable(fresh_compatibility)
    if (
        before != after
        or risk.package_digest != before.sha256
        or fresh_risk.package_digest != after.sha256
        or risk.risk_digest != fresh_risk.risk_digest
    ):
        raise WorkflowConflict(
            "package changed while trust was being recorded; rerun doctor",
            code="package_changed",
        )
    WorkflowTrustStore(args.hermes_home).trust(
        after.sha256,
        actor="local-user",
        risk_digest=fresh_risk.risk_digest,
    )
    payload = {
        "name": package.definition.name,
        "package_digest": after.sha256,
        "status": "trusted",
    }
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(f"Trusted {payload['name']} at digest {payload['package_digest']}")
    return 0


def _cmd_untrust(args: argparse.Namespace) -> int:
    compilation = _resolve_compilation(args, args.name)
    package = compilation.package
    before, _compatibility, risk = _compilation_trust_material(compilation)
    fresh_compilation = _resolve_compilation(args, args.name)
    after, _fresh_compatibility, fresh_risk = _compilation_trust_material(
        fresh_compilation
    )
    if (
        before != after
        or risk.package_digest != before.sha256
        or fresh_risk.package_digest != after.sha256
        or risk.risk_digest != fresh_risk.risk_digest
    ):
        raise WorkflowConflict(
            "package changed while trust was being revoked; rerun doctor",
            code="package_changed",
        )
    revoked = WorkflowTrustStore(args.hermes_home).revoke(after.sha256)
    payload = {
        "name": package.definition.name,
        "package_digest": after.sha256,
        "status": "untrusted",
        "revoked": revoked,
    }
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(
            f"Trust revoked for {payload['name']}"
            if revoked
            else f"{payload['name']} was not trusted"
        )
    return 0


def _runtime_config(
    hermes_home: str | Path,
    *,
    sidecar: Mapping[str, object] | None = None,
) -> WorkflowRuntimeConfig:
    from hermes_cli.config import load_config_readonly

    path = Path(hermes_home) / "config.yaml"
    raw: object = load_config_readonly(path)
    if not isinstance(raw, Mapping):
        raise ValueError("config.yaml must contain a mapping")
    plugins = raw.get("plugins", {})
    if not isinstance(plugins, Mapping):
        raise ValueError("config.yaml plugins section must contain a mapping")
    entries = plugins.get("entries", {})
    if not isinstance(entries, Mapping):
        raise ValueError("config.yaml plugin entries must contain a mapping")
    entry = entries.get("workflow", {})
    if not isinstance(entry, Mapping):
        raise ValueError("workflow plugin entry must contain a mapping")
    workflow = entry.get("runtime", {})
    if not isinstance(workflow, Mapping):
        raise ValueError("workflow plugin runtime config must contain a mapping")
    policy = sidecar or {}
    limits = policy.get("limits", {}) if isinstance(policy, Mapping) else {}
    resources = policy.get("resource_limits", {}) if isinstance(policy, Mapping) else {}
    if not isinstance(limits, Mapping) or not isinstance(resources, Mapping):
        raise ValueError("workflow sidecar limits must contain mappings")
    return WorkflowRuntimeConfig.from_mapping(
        workflow,
        sidecar_limits=limits,
        sidecar_resources=resources,
    )


def _store(
    args: argparse.Namespace, runtime: WorkflowRuntimeConfig | None = None
) -> RunStore:
    config = runtime or _runtime_config(args.hermes_home)
    return RunStore(
        args.hermes_home,
        max_executing_runs=config.max_executing_runs,
        max_queued_runs=config.max_queued_runs,
        max_paused_runs=config.max_paused_runs,
        max_nonterminal_runs=config.max_nonterminal_runs,
        max_start_requests_per_minute=config.max_start_requests_per_minute,
        max_total_workers=config.max_total_workers,
    )


def _scheduler(
    store: RunStore,
    config: WorkflowRuntimeConfig,
    *,
    agent_runner=None,
    runner_binding=None,
    profile_name: str = "default",
    owner_id: str | None = None,
    execution_owner_id: str | None = None,
    execution_owner_epoch: int | None = None,
    utcnow: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> RunScheduler:
    return RunScheduler(
        store,
        owner_id=owner_id,
        execution_owner_id=execution_owner_id,
        execution_owner_epoch=execution_owner_epoch,
        agent_runner=agent_runner,
        runner_binding=runner_binding,
        profile_name=profile_name,
        max_parallel_nodes=config.max_parallel_nodes,
        heartbeat_seconds=config.heartbeat_seconds,
        lease_seconds=config.lease_seconds,
        ai_idle_timeout_seconds=config.ai_idle_timeout_seconds,
        ai_wall_timeout_seconds=config.ai_wall_timeout_seconds,
        provider_request_timeout_seconds=config.provider_request_timeout_seconds,
        subprocess_timeout_seconds=config.subprocess_timeout_seconds,
        default_max_attempts=config.combined_retries,
        cooperative_shutdown_seconds=config.cooperative_shutdown_seconds,
        term_grace_seconds=config.term_grace_seconds,
        kill_reap_grace_seconds=config.kill_reap_grace_seconds,
        utcnow=utcnow,
        monotonic=monotonic,
        resource_limits=ProcessResourceLimits(
            max_rss_bytes=config.process_tree_rss_bytes,
            max_cpu_seconds=config.process_tree_cpu_seconds,
            max_descendants=config.max_descendants,
        ),
    )


def _cmd_run(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    compilation = _resolve_compilation(args, args.name)
    package = compilation.package
    runtime = _runtime_config(args.hermes_home, sidecar=package.sidecar)
    capabilities = connector_capability_snapshot()
    assessment = _phase5_admission_assessment(compilation, capabilities)
    if assessment is None:
        digest = _admission_package_digest(compilation)
        compatibility = assess_compatibility(package)
        risk = build_risk_summary(
            package,
            compatibility,
            compilation=_admission_compilation(compilation),
        )
    else:
        digest = assessment.package_digest
        compatibility = assessment.compatibility
        risk = assessment.risk
    require_runnable(compatibility)
    if (
        WorkflowTrustStore(args.hermes_home).check(
            digest.sha256, risk_digest=risk.risk_digest
        )
        != "trusted"
    ):
        raise WorkflowAuthorization(
            "workflow package is not trusted; run doctor and trust its exact digest",
            code="trust_required",
        )
    preflight_execution(risk, trusted=True)
    store = _store(args, runtime)
    from plugins.workflow.coordinator_store import CoordinatorStore

    coordinator = CoordinatorStore(store.database).health(
        now=datetime.now(timezone.utc)
    )
    execution_mode = "foreground" if args.foreground else "background"
    if execution_mode == "background" and coordinator.status != "healthy":
        raise CoordinatorUnavailable(
            "background execution requires a healthy workflow coordinator; "
            "use --foreground for explicit local execution"
        )
    if (args.json or args.no_wait) and not args.idempotency_key:
        raise WorkflowCommandError(
            "idempotency_key_required",
            "--idempotency-key is required for JSON and background starts",
            exit_code=EXIT_INVOCATION,
        )
    foreground_owner_id = (
        f"foreground-{os.getpid()}-{secrets.token_hex(16)}"
        if execution_mode == "foreground"
        else None
    )
    prepared = store.prepare_run_snapshot(
        package,
        compilation=_admission_compilation(compilation),
        values={"arguments": args.arguments} if args.arguments else None,
        trusted_package_digest=(
            digest if _admission_compilation(compilation) is not None else None
        ),
        execution_limits=RunExecutionLimits.resolve(runtime),
        provider_authority=(
            assessment.provider_authority if assessment is not None else None
        ),
        connector_capabilities=capabilities,
    )
    intent_key = args.idempotency_key or secrets.token_urlsafe(24)
    request = RunAdmissionRequest(
        workflow_name=package.definition.name,
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source=args.trigger_source,
        idempotency_key=intent_key,
        idempotency_namespace=f"profile-local:{args.trigger_source}",
        concurrency_key=args.concurrency_key
        or str(package.sidecar.get("concurrency_key") or package.definition.name),
        concurrency_policy=str(package.sidecar.get("overlap_policy") or "queue"),
        execution_mode=execution_mode,
        foreground_owner_id=foreground_owner_id,
        provenance=TriggerProvenance.local_admin_claim(
            source=args.trigger_source,
            intent_key=intent_key,
            source_instance=args.source_instance or f"cli:pid:{os.getpid()}",
            claimed_actor=args.claimed_actor,
        ),
    )
    admitted = store.start_run(request, immutable_snapshot=prepared)
    if admitted.run_id is None:
        reason = admitted.reason_code or "admission_rejected"
        if reason == "coordinator_unavailable":
            raise CoordinatorUnavailable(f"workflow admission was rejected: {reason}")
        if reason in {"idempotency_conflict", "coordinator_active"}:
            raise WorkflowConflict(
                f"workflow admission was rejected: {reason}",
                code=reason,
                details={"admission_disposition": admitted.disposition},
            )
        raise WorkflowActionFailed(
            f"workflow admission was rejected: {reason}",
            code=reason,
            details={"admission_disposition": admitted.disposition},
        )
    if admitted.disposition == "created" and execution_mode == "foreground":
        try:
            _scheduler(
                store,
                runtime,
                agent_runner=agent_runner,
                profile_name=profile_name,
                owner_id=foreground_owner_id,
                execution_owner_id=foreground_owner_id,
                execution_owner_epoch=1,
            ).advance(admitted.run_id)
        finally:
            store.release_foreground_execution(
                admitted.run_id,
                owner_id=foreground_owner_id,
                epoch=1,
                now=datetime.now(timezone.utc),
            )
    elif (
        admitted.disposition == "created"
        and execution_mode == "background"
        and not args.no_wait
    ):
        while store.get_run_status(admitted.run_id)["status"] == "running":
            time.sleep(0.1)
    payload = store.get_run_status(admitted.run_id)
    payload["action"] = "run"
    payload["admission_disposition"] = admitted.disposition
    payload["truncated"] = projection_was_truncated(payload)
    payload["next_cursor"] = None
    if payload["status"] in {"failed", "cancelled", "abandoned"}:
        raise WorkflowActionFailed(
            f"workflow run entered terminal state: {payload['status']}",
            code="run_failed",
            result=payload,
        )
    handoff = payload.get("execution_handoff")
    if (
        not args.json
        and isinstance(handoff, Mapping)
        and handoff.get("transition") == "foreground_execution_adopted"
    ):
        print(
            "This run was adopted by the background coordinator and continues; "
            f"watch it with workflow status {admitted.run_id}."
        )
    _emit(payload, as_json=args.json)
    return 0


def _require_run(store: RunStore, run_id: str) -> dict[str, object]:
    try:
        return store.get_run_status(run_id)
    except KeyError as exc:
        raise WorkflowNotFound(
            f"workflow run not found: {run_id}",
            details={"run_id": run_id},
        ) from exc


def _run_page(
    store: RunStore,
    *,
    workflow: str | None = None,
    status: str | None = None,
    limit: int = 100,
    view: str = "all",
) -> dict[str, object]:
    runs = store.list_runs(workflow=workflow, status=status, limit=limit, view=view)
    next_cursor = None
    if len(runs) == limit and runs:
        keyset = (str(runs[-1]["updated_at"]), str(runs[-1]["run_id"]))
        remainder = store.list_runs(
            workflow=workflow,
            status=status,
            limit=1,
            view=view,
            after=keyset,
        )
        if remainder:
            next_cursor = list(keyset)
    return {
        "runs": runs,
        "truncated": next_cursor is not None or projection_was_truncated(runs),
        "next_cursor": next_cursor,
    }


def _cmd_runs(args: argparse.Namespace) -> int:
    payload = _run_page(
        _store(args),
        workflow=args.workflow,
        status=args.status,
        limit=args.limit,
        view=args.view,
    )
    _emit(payload, as_json=args.json)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    if args.run_id:
        payload = _require_run(store, args.run_id)
        payload["truncated"] = projection_was_truncated(payload)
        payload["next_cursor"] = None
    else:
        payload = _run_page(store)
    _emit(payload, as_json=args.json)
    return 0


def _cmd_events(args: argparse.Namespace) -> int:
    if not 1 <= args.tail <= 200:
        raise ValueError("limit must be between 1 and 200")
    store = _store(args)
    _require_run(store, args.run_id)
    payload = store.latest_event_page(args.run_id, limit=args.tail)
    _emit(payload, as_json=args.json)
    return 0


def _cmd_approval_decision(
    args: argparse.Namespace,
    *,
    decision: str,
    agent_runner=None,
    profile_name="default",
) -> int:
    runtime = _runtime_config(args.hermes_home)
    store = _store(args, runtime)
    _require_run(store, args.run_id)
    if decision == "approved":
        result = store.approve_run(
            args.run_id,
            comment=args.comment,
            expected_state_version=args.expected_version,
            interaction_id=args.interaction_id,
            channel="cli",
        )
    else:
        result = store.reject_run(
            args.run_id,
            reason=args.reason,
            expected_state_version=args.expected_version,
            interaction_id=args.interaction_id,
            channel="cli",
        )
    if result.outcome == "applied":
        _continue_if_requested(
            args,
            store,
            runtime,
            agent_runner=agent_runner,
            profile_name=profile_name,
        )
    payload = asdict(result)
    payload["action"] = "approve" if decision == "approved" else "reject"
    payload["run_status"] = store.get_run_status(args.run_id)["status"]
    if result.outcome == "already_decided" and result.decision != decision:
        raise WorkflowConflict(
            "the interaction already has a different decision",
            code="decision_conflict",
            details={"interaction_id": result.interaction_id},
        )
    _emit(payload, as_json=args.json)
    return 0


def _cmd_resume(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    runtime = _runtime_config(args.hermes_home)
    store = _store(args, runtime)
    before = _require_run(store, args.run_id)
    foreground_conflict = False
    resume_scheduler = _scheduler(
        store,
        runtime,
        agent_runner=agent_runner,
        profile_name=profile_name,
    )
    always_run_nodes = resume_scheduler.verified_always_run_nodes(args.run_id)
    try:
        store.resume_run(
            args.run_id,
            always_run_nodes=always_run_nodes,
        )
    except ForegroundExecutionConflict:
        foreground_conflict = True
    _continue_foreground_if_owned(
        args.run_id,
        store,
        runtime,
        agent_runner=agent_runner,
        profile_name=profile_name,
    )
    after = store.get_run_status(args.run_id)
    if foreground_conflict and after["state_version"] == before["state_version"]:
        raise WorkflowConflict(
            "foreground owner conflict: resume made no durable transition",
            details={"run_id": args.run_id},
        )
    _emit(after, as_json=args.json)
    return 0


def _continue_if_requested(args, store, runtime, *, agent_runner, profile_name):
    if args.continue_run:
        _continue_foreground_if_owned(
            args.run_id,
            store,
            runtime,
            agent_runner=agent_runner,
            profile_name=profile_name,
        )


def _continue_foreground_if_owned(
    run_id,
    store,
    runtime,
    *,
    agent_runner,
    profile_name,
):
    projection = store.get_run_status(run_id)
    if projection.get("execution_mode") != "foreground":
        return
    from plugins.workflow.coordinator_store import CoordinatorStore

    health = CoordinatorStore(store.database).health(now=datetime.now(timezone.utc))
    if health.status == "healthy":
        return
    owner_id = f"foreground-{os.getpid()}-{secrets.token_hex(16)}"
    lease = store.claim_foreground_execution(
        run_id,
        owner_id=owner_id,
        now=datetime.now(timezone.utc),
        lease_seconds=runtime.lease_seconds,
    )
    if lease is None:
        return
    try:
        _scheduler(
            store,
            runtime,
            agent_runner=agent_runner,
            profile_name=profile_name,
            owner_id=lease.owner_id,
            execution_owner_id=lease.owner_id,
            execution_owner_epoch=lease.epoch,
        ).advance(run_id)
    finally:
        store.release_foreground_execution(
            run_id,
            owner_id=lease.owner_id,
            epoch=lease.epoch,
            now=datetime.now(timezone.utc),
        )


def _cmd_provide_input(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    runtime = _runtime_config(args.hermes_home)
    store = _store(args, runtime)
    current = _require_run(store, args.run_id)
    pending = current.get("pending_interaction")
    if isinstance(pending, Mapping):
        actual = pending.get("interaction_id") or pending.get("action_digest")
        if actual is not None and actual != args.interaction_id:
            raise ValueError("interaction ID does not match the pending input")
    store.provide_loop_input(
        args.run_id,
        args.value,
        expected_state_version=args.expected_version,
        interaction_id=args.interaction_id,
    )
    _continue_if_requested(
        args, store, runtime, agent_runner=agent_runner, profile_name=profile_name
    )
    payload = store.get_run_status(args.run_id)
    payload["action"] = "provide-input"
    _emit(payload, as_json=args.json)
    return 0


def _cmd_retry(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    runtime = _runtime_config(args.hermes_home)
    store = _store(args, runtime)
    _require_run(store, args.run_id)
    store.retry_run(
        args.run_id,
        node_id=args.node_id,
        expected_state_version=args.expected_version,
    )
    _continue_if_requested(
        args, store, runtime, agent_runner=agent_runner, profile_name=profile_name
    )
    payload = store.get_run_status(args.run_id)
    payload["action"] = "retry"
    _emit(payload, as_json=args.json)
    return 0


def _cmd_reconcile(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    runtime = _runtime_config(args.hermes_home)
    store = _store(args, runtime)
    _require_run(store, args.run_id)
    store.reconcile_run(
        args.run_id,
        args.outcome,
        expected_state_version=args.expected_version,
        interaction_id=args.interaction_id,
    )
    _continue_if_requested(
        args, store, runtime, agent_runner=agent_runner, profile_name=profile_name
    )
    payload = store.get_run_status(args.run_id)
    payload["action"] = "reconcile"
    _emit(payload, as_json=args.json)
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    store = _store(args)
    _require_run(store, args.run_id)
    store.cancel_run(args.run_id)
    _emit(store.get_run_status(args.run_id), as_json=args.json)
    return 0


def _cmd_abandon(args: argparse.Namespace) -> int:
    store = _store(args)
    _require_run(store, args.run_id)
    store.abandon_run(args.run_id)
    _emit(store.get_run_status(args.run_id), as_json=args.json)
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    store = _store(args)
    _require_run(store, args.run_id)
    payload = store.archive_run(
        args.run_id, expected_state_version=args.expected_version
    )
    _emit(payload, as_json=args.json)
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    store = _store(args)
    _require_run(store, args.run_id)
    payload = store.restore_run(
        args.run_id, expected_state_version=args.expected_version
    )
    _emit(payload, as_json=args.json)
    return 0


def _duration(value: str):
    from datetime import timedelta

    match = re.fullmatch(r"(\d+)([dhm])", value)
    if not match:
        raise ValueError("--older-than must be a duration such as 7d, 12h, or 30m")
    amount = int(match.group(1))
    return {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[match.group(2)]


def _cmd_cleanup(args: argparse.Namespace) -> int:
    payload = _store(args).cleanup_runs(
        older_than=_duration(args.older_than),
        execute=args.execute,
        confirmation_token=args.confirmation_token,
    )
    _emit(payload, as_json=args.json)
    return 0


def _cmd_reset_sessions(args: argparse.Namespace) -> int:
    if args.scope is None and not args.yes:
        raise WorkflowCommandError(
            "confirmation_required",
            "cross-scope reset requires --yes",
            exit_code=EXIT_INVOCATION,
        )
    removed = NodeSessionRegistry(args.hermes_home).reset(
        args.name, scope=args.scope, node_id=args.node
    )
    _emit(
        {
            "action": "reset-sessions",
            "workflow": args.name,
            "scope": args.scope,
            "node": args.node,
            "removed": removed,
        },
        as_json=args.json,
    )
    return 0


def _cmd_showcase(args: argparse.Namespace) -> int:
    from plugins.workflow.showcase import (
        build_showcase_report,
        cleanup_showcases,
        load_showcase_catalog,
        preflight_showcase,
        report_to_dict,
        reset_showcase,
        run_showcase,
    )

    action = getattr(args, "showcase_action", None)
    catalog = load_showcase_catalog()
    if (
        action in {"describe", "preflight", "run", "reset"}
        and args.showcase_id not in catalog
    ):
        raise WorkflowNotFound(
            f"showcase not found: {args.showcase_id}",
            details={
                "candidates": [
                    {"id": item.id, "kind": "showcase", "label": item.display_name}
                    for item in sorted(catalog.values(), key=lambda item: item.id)[:10]
                ]
            },
        )
    if action == "list":
        payload = [asdict(item) for item in catalog.values()]
    elif action == "describe":
        payload = asdict(catalog[args.showcase_id])
    elif action == "preflight":
        payload = preflight_showcase(args.showcase_id, hermes_home=args.hermes_home)
    elif action == "run":
        if (args.json or args.no_wait) and not args.idempotency_key:
            raise WorkflowCommandError(
                "idempotency_key_required",
                "--idempotency-key is required for JSON and background starts",
                exit_code=EXIT_INVOCATION,
            )
        payload = run_showcase(
            args.showcase_id,
            hermes_home=args.hermes_home,
            symptom=args.symptom,
            confirmation_token=args.confirmation_token,
            schedule_at=args.schedule_at,
            no_wait=args.no_wait,
            idempotency_key=args.idempotency_key,
            trigger_source=args.trigger_source,
            source_instance=args.source_instance,
            claimed_actor=args.claimed_actor,
        )
    elif action == "status":
        payload = _require_run(_store(args), args.run_id)
    elif action == "report":
        _require_run(_store(args), args.run_id)
        payload = report_to_dict(
            build_showcase_report(args.run_id, hermes_home=args.hermes_home)
        )
    elif action == "reset":
        payload = reset_showcase(args.showcase_id, hermes_home=args.hermes_home)
    elif action == "cleanup":
        payload = cleanup_showcases(
            hermes_home=args.hermes_home,
            execute=args.execute,
            confirmation_token=args.confirmation_token,
            older_than_days=args.older_than_days,
        )
    else:
        print(
            "Usage: hermes workflow showcase {list|describe|preflight|run|status|report|reset|cleanup}",
            file=sys.stderr,
        )
        return 2
    if isinstance(payload, Mapping) and payload.get("reason_code"):
        input_required = payload.get("status") == "input_required"
        raise WorkflowCommandError(
            str(payload["reason_code"]),
            str(payload.get("message") or payload["reason_code"]),
            exit_code=EXIT_INVOCATION if input_required else EXIT_ACTION_FAILED,
            result=payload,
        )
    _emit(payload, as_json=args.json)
    return 0


def workflow_command(
    args: argparse.Namespace, *, agent_runner=None, profile_name="default"
) -> int:
    action = getattr(args, "workflow_action", None)
    if not action:
        print(
            "Usage: hermes workflow {schema|list|show|validate|doctor|trust|untrust|run|runs|status|events|approve|reject|provide-input|resume|retry|reconcile|cancel|abandon|archive|restore|cleanup|reset-sessions|showcase}",
            file=sys.stderr,
        )
        return 2
    command = f"workflow {action}"
    if action == "showcase" and getattr(args, "showcase_action", None):
        command += f" {args.showcase_action}"
    _MACHINE_COMMAND.set(command)
    try:
        if action == "schema":
            return _cmd_schema(args)
        if action in {"list", "ls"}:
            return _cmd_list(args)
        if action == "show":
            return _cmd_show(args)
        if action == "validate":
            return _cmd_validate(args)
        if action == "doctor":
            return _cmd_doctor(args)
        if action == "trust":
            return _cmd_trust(args)
        if action == "untrust":
            return _cmd_untrust(args)
        if action == "run":
            return _cmd_run(args, agent_runner=agent_runner, profile_name=profile_name)
        if action == "runs":
            return _cmd_runs(args)
        if action == "status":
            return _cmd_status(args)
        if action == "events":
            return _cmd_events(args)
        if action == "approve":
            return _cmd_approval_decision(
                args,
                decision="approved",
                agent_runner=agent_runner,
                profile_name=profile_name,
            )
        if action == "reject":
            return _cmd_approval_decision(
                args,
                decision="rejected",
                agent_runner=agent_runner,
                profile_name=profile_name,
            )
        if action == "provide-input":
            return _cmd_provide_input(
                args, agent_runner=agent_runner, profile_name=profile_name
            )
        if action == "resume":
            return _cmd_resume(
                args, agent_runner=agent_runner, profile_name=profile_name
            )
        if action == "retry":
            return _cmd_retry(
                args, agent_runner=agent_runner, profile_name=profile_name
            )
        if action == "reconcile":
            return _cmd_reconcile(
                args, agent_runner=agent_runner, profile_name=profile_name
            )
        if action == "cancel":
            return _cmd_cancel(args)
        if action == "abandon":
            return _cmd_abandon(args)
        if action == "archive":
            return _cmd_archive(args)
        if action == "restore":
            return _cmd_restore(args)
        if action == "cleanup":
            return _cmd_cleanup(args)
        if action == "reset-sessions":
            return _cmd_reset_sessions(args)
        if action == "showcase":
            return _cmd_showcase(args)
        print(f"Unknown workflow action: {action}", file=sys.stderr)
        return 2
    except WorkflowCommandError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    error_envelope(command, exc.error, result=exc.result),
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except WorkflowCompatibilityBlockedError as exc:
        error = WorkflowCommandError(
            exc.code,
            str(exc),
            exit_code=EXIT_BLOCKING_FINDING,
        )
        if getattr(args, "json", False):
            print(
                json.dumps(
                    error_envelope(command, error.error),
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(str(error), file=sys.stderr)
        return error.exit_code
    except WorkflowTrustError as exc:
        error = MachineError("trust_required", str(exc))
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_AUTHORIZATION
    except WorkflowLanguageCompatibilityError as exc:
        error = MachineError(exc.code, str(exc))
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_INVOCATION
    except WorkflowValidationError as exc:
        issues = [
            {
                "code": issue.code,
                "path": issue.path,
                "severity": issue.severity,
                "blocking": issue.blocking,
            }
            for issue in exc.issues[:200]
        ]
        primary_code = issues[0]["code"] if issues else "workflow_validation_failed"
        error = MachineError(
            primary_code,
            str(exc),
            details={"issues": issues},
        )
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_INVOCATION
    except ValueError as exc:
        error = MachineError("invalid_request", str(exc))
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_INVOCATION
    except (OSError, StorageQuotaError) as exc:
        error = MachineError(
            "action_failed",
            "workflow storage operation failed",
            details={"exception_type": type(exc).__name__},
        )
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_ACTION_FAILED
    except Exception as exc:
        error = MachineError(
            "internal_error",
            "workflow command failed unexpectedly",
            details={"exception_type": type(exc).__name__},
        )
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(command, error), sort_keys=True, indent=2))
        else:
            print(error.message, file=sys.stderr)
        return EXIT_INTERNAL
