"""Side-effect-free operator CLI for portable workflow packages."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import AbstractSet, Iterable, Mapping

import yaml

from hermes_constants import get_hermes_home
from plugins.workflow.compat import (
    ARCHON_TOOL_ALIASES,
    CompatibilityReport,
    assess_compatibility,
)
from plugins.workflow.discovery import discover_workflows
from plugins.workflow.models import (
    ValidationIssue,
    WorkflowPackage,
    WorkflowValidationError,
)
from plugins.workflow.schema import load_workflow, validate_package
from plugins.workflow.topology import project_topology
from plugins.workflow.trust import (
    WorkflowTrustError,
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _compat_dict(report: CompatibilityReport) -> dict[str, object]:
    return {
        "level": report.level.value,
        "blocking_count": len(report.blocking_findings),
    }


def _catalog_summary(
    package: WorkflowPackage, report: CompatibilityReport
) -> dict[str, object]:
    return {
        "name": package.definition.name,
        "description": package.definition.description,
        "source": package.source,
        "precedence": package.precedence,
        "compatibility": _compat_dict(report),
        "runnable": report.runnable,
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


def show_package(
    package: WorkflowPackage,
    *,
    available_tools: AbstractSet[str] | None = None,
    available_services: AbstractSet[str] | None = None,
    provider_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    isolated_workdir: bool = False,
    mcp_available: bool = False,
    cron_jobs: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    report = assess_compatibility(
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
        "argument_hints": _argument_hints(package),
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
    return result


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output")


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--workdir", default=os.getcwd(), help=argparse.SUPPRESS)
    subparser.add_argument(
        "--hermes-home", default=str(get_hermes_home()), help=argparse.SUPPRESS
    )
    actions = subparser.add_subparsers(dest="workflow_action")

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
    _json_flag(doctor_parser)

    trust_parser = actions.add_parser("trust", help="Trust the current package digest")
    trust_parser.add_argument("name")
    trust_parser.add_argument("--digest", required=True)
    _json_flag(trust_parser)

    untrust_parser = actions.add_parser("untrust", help="Revoke package trust")
    untrust_parser.add_argument("name")
    _json_flag(untrust_parser)

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
    raise WorkflowValidationError(
        ValidationIssue(
            path="name",
            code="workflow_not_found",
            message=f"workflow not found: {name}",
        )
    )


def _cron_jobs() -> Iterable[Mapping[str, object]]:
    try:
        from cron.jobs import list_jobs

        return list_jobs(include_disabled=True)
    except Exception:
        return ()


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(payload)


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
        print(
            "--topology cannot be combined with --json; JSON show always includes both projections",
            file=sys.stderr,
        )
        return 2
    detail = show_package(_resolve(args, args.name), cron_jobs=_cron_jobs())
    if args.json:
        _emit(detail, as_json=True)
        return 0
    print(f"Workflow: {detail['name']}")
    print(f"Description: {detail['description']}")
    print(f"Source: {detail['source']} (precedence {detail['precedence']})")
    print(f"Compatibility: {detail['compatibility']['level']}")
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


def _cmd_validate(args: argparse.Namespace) -> int:
    package = _resolve(args, args.name)
    issues = validate_package(package)
    payload = {
        "name": package.definition.name,
        "valid": not any(issue.blocking for issue in issues),
        "issues": [
            {
                "path": issue.path,
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity,
                "blocking": issue.blocking,
                "source_line": issue.source_line,
            }
            for issue in issues
        ],
    }
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(
            f"{package.definition.name}: {'valid' if payload['valid'] else 'invalid'}"
        )
        for issue in payload["issues"]:
            print(f"- {issue['severity']}: {issue['path']}: {issue['message']}")
    return 0 if payload["valid"] else 1


def _doctor_payload(
    package: WorkflowPackage, *, compat_report: bool
) -> dict[str, object]:
    compatibility = assess_compatibility(package)
    risk = build_risk_summary(package, compatibility)
    payload = risk.to_dict()
    payload.update({
        "name": package.definition.name,
        "compatibility": compatibility.level.value,
        "runnable": compatibility.runnable,
        "remediation": (
            "Resolve blocking compatibility findings, then rerun doctor and trust the current digest."
            if compatibility.blocking_findings
            else "Review this risk summary, then trust the exact package digest before local execution."
        ),
    })
    if compat_report:
        payload["compatibility_findings"] = [
            {
                "path": finding.path,
                "level": finding.level.value,
                "message": finding.message,
                "blocking": finding.blocking,
            }
            for finding in compatibility.findings
        ]
    return payload


def _cmd_doctor(args: argparse.Namespace) -> int:
    payload = _doctor_payload(
        _resolve(args, args.name), compat_report=args.compat_report
    )
    if args.json:
        _emit(payload, as_json=True)
    else:
        print(f"Workflow: {payload['name']}")
        print(f"Package digest: {payload['package_digest']}")
        print(f"Risk digest: {payload['risk_digest']}")
        print(f"Compatibility: {payload['compatibility']}")
        print(f"Execution environment: {payload['execution_environment']}")
        print(f"Remediation: {payload['remediation']}")
    return 0


def _cmd_trust(args: argparse.Namespace) -> int:
    package = _resolve(args, args.name)
    before = compute_package_digest(package)
    if args.digest != before.sha256:
        print(
            "supplied digest does not match the current package digest", file=sys.stderr
        )
        return 1
    compatibility = assess_compatibility(package)
    risk = build_risk_summary(package, compatibility)
    fresh_package = load_workflow(
        package.workflow_path,
        source=package.source,
        precedence=package.precedence,
    )
    fresh_risk = build_risk_summary(fresh_package, assess_compatibility(fresh_package))
    after = compute_package_digest(fresh_package)
    if (
        before != after
        or risk.package_digest != after.sha256
        or risk.risk_digest != fresh_risk.risk_digest
    ):
        print(
            "package changed while trust was being recorded; rerun doctor",
            file=sys.stderr,
        )
        return 1
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
    package = _resolve(args, args.name)
    digest = compute_package_digest(package)
    revoked = WorkflowTrustStore(args.hermes_home).revoke(digest.sha256)
    payload = {
        "name": package.definition.name,
        "package_digest": digest.sha256,
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


def workflow_command(args: argparse.Namespace) -> int:
    action = getattr(args, "workflow_action", None)
    if not action:
        print(
            "Usage: hermes workflow {list|show|validate|doctor|trust|untrust}",
            file=sys.stderr,
        )
        return 2
    try:
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
        print(f"Unknown workflow action: {action}", file=sys.stderr)
        return 2
    except (OSError, WorkflowTrustError, WorkflowValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
