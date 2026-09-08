"""Thin workflow marketplace CLI parser, invocation, and rendering adapters."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, cast, Mapping, NoReturn

from pydantic import BaseModel, ValidationError

from hermes_cli.git_source import safe_git_error
from plugins.workflow.machine_contract import (
    EXIT_ACTION_FAILED,
    EXIT_AUTHORIZATION,
    EXIT_BLOCKING_FINDING,
    EXIT_CONFLICT,
    EXIT_INTERNAL,
    EXIT_INVOCATION,
    EXIT_NOT_FOUND,
)

from .models import (
    InstallRequest,
    InstalledPackage,
    InstalledPackageIdentity,
    WorkflowMarketplaceSource,
)
from .package import WorkflowMarketplaceError


MARKETPLACE_ACTIONS = frozenset({
    "source",
    "search",
    "inspect",
    "install",
    "installed",
    "check",
    "update",
    "uninstall",
    "package-state",
    "recover-packages",
})
_CONFIRMABLE_ACTIONS = frozenset({"install", "update", "uninstall", "trust"})
_NOT_FOUND_CODES = frozenset({
    "catalog_package_not_found",
    "installed_package_not_found",
    "installed_workflow_not_found",
    "source_not_found",
})
_AUTHORIZATION_CODES = frozenset({
    "confirmation_token_invalid",
    "source_authentication_failed",
    "trust_confirmation_lock_timeout",
    "trust_confirmation_state_invalid",
})
_CONFLICT_CODES = frozenset({
    "direct_package_path_conflict",
    "install_request_conflict",
    "installed_identity_conflict",
    "installed_package_conflict",
    "package_version_conflict",
    "package_version_regression",
    "source_exists",
    "source_identity_conflict",
    "transaction_conflict",
    "transaction_lock_timeout",
    "trust_review_changed",
})
_INVOCATION_CODES = frozenset({
    "catalog_identifier_invalid",
    "catalog_limit_invalid",
    "catalog_query_invalid",
    "confirmation_required",
    "direct_package_ambiguous",
    "install_request_invalid",
    "profile_invalid",
    "source_credentials_forbidden",
    "source_invalid",
    "source_name_invalid",
})
_BLOCKING_PREFIXES = (
    "catalog_state_",
    "contract_",
    "digest_",
    "manifest_",
    "package_contract_",
    "package_digest_",
    "package_index_",
    "package_manifest_",
    "package_path_",
    "package_resource_",
    "package_review_",
    "package_traversal_",
    "package_workflow_",
    "workflow_contract_",
    "workflow_resource_",
)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_:/-])(?:[A-Za-z]:[\\/]|/)[^\s\"']+")
_CREDENTIAL_VALUE = re.compile(
    r"(?i)\b(token|password|secret|authorization|api[_-]?key)\b"
    r"(\s*(?::|=|\bis\b)?\s+)([^\s,;]+)"
)
_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)


class _MarketplaceCLIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int,
        result: object = None,
    ) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.result = result
        super().__init__(message)


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output")


def _confirmation_flags(parser: argparse.ArgumentParser) -> None:
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare and print an exact review without applying it",
    )
    modes.add_argument(
        "--confirmation-token",
        help="Confirm one previously prepared exact review",
    )
    modes.add_argument(
        "--yes",
        action="store_true",
        help="Prepare, review, and confirm noninteractively",
    )


def configure_marketplace_parsers(actions) -> None:
    """Register marketplace commands below the existing workflow action parser."""

    source = actions.add_parser("source", help="Manage workflow marketplace sources")
    source_actions = source.add_subparsers(dest="source_action")
    source_add = source_actions.add_parser("add", help="Add a Git marketplace source")
    source_add.add_argument("name")
    source_add.add_argument("repository_url", metavar="git-url")
    source_add.add_argument("--ref")
    _json_flag(source_add)
    source_list = source_actions.add_parser("list", help="List marketplace sources")
    _json_flag(source_list)
    source_refresh = source_actions.add_parser(
        "refresh", help="Refresh one or all marketplace sources"
    )
    source_refresh.add_argument("name", nargs="?")
    _json_flag(source_refresh)
    source_remove = source_actions.add_parser(
        "remove", help="Remove a marketplace source definition"
    )
    source_remove.add_argument("name")
    _json_flag(source_remove)

    search = actions.add_parser("search", help="Search verified marketplace catalogs")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--source")
    _json_flag(search)

    inspect = actions.add_parser("inspect", help="Inspect a marketplace package")
    inspect.add_argument("identifier", metavar="source/package")
    _json_flag(inspect)

    install = actions.add_parser("install", help="Install a workflow package")
    install.add_argument("identifier", metavar="source/package-or-git-url")
    install.add_argument("--ref")
    install.add_argument("--path", dest="package_path")
    _confirmation_flags(install)
    _json_flag(install)

    installed = actions.add_parser("installed", help="List installed workflow packages")
    _json_flag(installed)

    state = actions.add_parser(
        "package-state", help="Read verified package state on this backend/profile"
    )
    state.add_argument("identifier", metavar="SOURCE_KEY/PACKAGE_ID")
    _json_flag(state)
    recovery = actions.add_parser(
        "recover-packages",
        help="Recover owned package transactions on this backend/profile",
    )
    recovery.add_argument(
        "--yes", action="store_true", help="Confirm profile-wide recovery"
    )
    _json_flag(recovery)

    check = actions.add_parser("check", help="Check installed packages for updates")
    check.add_argument("identifier", nargs="?", metavar="source/package")
    _json_flag(check)

    update = actions.add_parser("update", help="Update one or all workflow packages")
    update.add_argument("identifier", nargs="?", metavar="source/package")
    update.add_argument("--all", action="store_true")
    _confirmation_flags(update)
    _json_flag(update)

    uninstall = actions.add_parser("uninstall", help="Uninstall a workflow package")
    uninstall.add_argument("identifier", metavar="source/package")
    _confirmation_flags(uninstall)
    _json_flag(uninstall)

    trust = actions.add_parser(
        "trust", help="Trust a loose workflow digest or installed package"
    )
    trust.add_argument("name")
    trust.add_argument("--digest")
    trust.add_argument("--workflow")
    trust.add_argument(
        "--installed-package",
        action="store_true",
        help="Treat the target as an installed marketplace package",
    )
    _confirmation_flags(trust)
    _json_flag(trust)

    untrust = actions.add_parser(
        "untrust", help="Revoke loose workflow or installed-package trust"
    )
    untrust.add_argument("name")
    untrust.add_argument("--workflow")
    untrust.add_argument(
        "--installed-package",
        action="store_true",
        help="Treat the target as an installed marketplace package",
    )
    _json_flag(untrust)


def _service_for_args(args: argparse.Namespace):
    from .service import WorkflowMarketplaceService

    return WorkflowMarketplaceService(
        Path(args.hermes_home),
        profile=getattr(args, "_marketplace_profile", "default"),
    )


def _actor(args: argparse.Namespace) -> str:
    value = getattr(args, "_marketplace_actor", "cli")
    return value if isinstance(value, str) and value else "cli"


def _public(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=False)
    if is_dataclass(value) and not isinstance(value, type):
        return _public(asdict(value))
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): _public(mapping[key])
            for key in sorted(mapping.keys(), key=lambda item: str(item))
        }
    if isinstance(value, tuple | list):
        return [_public(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _success_envelope(command: str, result: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": True,
        "command": command,
        "result": _public(result),
        "error": None,
        "warnings": [],
        "next_actions": [],
    }


def _error_envelope(
    command: str,
    *,
    code: str,
    message: str,
    result: object = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": False,
        "command": command,
        "result": _public(result),
        "error": {
            "code": code,
            "message": message,
            "retryable": _exit_code(code) in {EXIT_CONFLICT, EXIT_ACTION_FAILED},
            "details": {},
        },
        "warnings": [],
        "next_actions": [],
    }


def _command(args: argparse.Namespace) -> str:
    action = args.workflow_action
    if action == "source":
        source_action = getattr(args, "source_action", None)
        return f"workflow source{f' {source_action}' if source_action else ''}"
    return f"workflow {action}"


def _sanitize_message(message: object, args: argparse.Namespace) -> str:
    rendered = safe_git_error(
        subprocess.CompletedProcess((), 1, "", str(message)),
    ).strip()
    for field in ("confirmation_token",):
        secret = getattr(args, field, None)
        if isinstance(secret, str) and secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    rendered = _CREDENTIAL_VALUE.sub(r"\1 [REDACTED]", rendered)
    rendered = _ABSOLUTE_PATH.sub("[REDACTED_PATH]", rendered)
    encoded = rendered.encode("utf-8")[:4096]
    return encoded.decode("utf-8", errors="ignore") or (
        "workflow marketplace operation failed"
    )


def _diagnostic_projection(value: object, args: argparse.Namespace) -> object:
    projection = _public(value)
    if isinstance(projection, dict):
        mapping = cast(dict[str, object], projection)
        message = mapping.get("message")
        if isinstance(message, str):
            mapping["message"] = _sanitize_message(message, args)
    return projection


def _exit_code(code: str) -> int:
    if code in _NOT_FOUND_CODES or code.endswith("_not_found"):
        return EXIT_NOT_FOUND
    if code in _AUTHORIZATION_CODES or "authentication" in code:
        return EXIT_AUTHORIZATION
    if code in _CONFLICT_CODES or code.endswith(("_conflict", "_lock_timeout")):
        return EXIT_CONFLICT
    if code in _INVOCATION_CODES or code.endswith(("_invalid", "_forbidden")):
        return EXIT_INVOCATION
    if code.startswith(_BLOCKING_PREFIXES) or code.endswith((
        "_mismatch",
        "_unsupported",
    )):
        return EXIT_BLOCKING_FINDING
    return EXIT_ACTION_FAILED


def _fail(
    code: str,
    message: str,
    *,
    exit_code: int | None = None,
    result: object = None,
) -> NoReturn:
    raise _MarketplaceCLIError(
        code,
        message,
        exit_code=_exit_code(code) if exit_code is None else exit_code,
        result=result,
    )


def _stdin_is_tty() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _read_confirmation(prompt: str) -> bool:
    try:
        return input(prompt).strip().casefold() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def _has_confirmation_mode(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "prepare_only", False)
        or getattr(args, "confirmation_token", None)
        or getattr(args, "yes", False)
    )


def _require_confirmation_mode(args: argparse.Namespace) -> None:
    if _has_confirmation_mode(args):
        return
    if not getattr(args, "json", False) and _stdin_is_tty():
        return
    _fail(
        "confirmation_required",
        "an explicit confirmation mode is required when stdin is not interactive",
        exit_code=EXIT_AUTHORIZATION,
    )


def _identity(identifier: str) -> InstalledPackageIdentity:
    source_key, separator, package_id = identifier.partition("/")
    if not separator or not source_key or not package_id or "/" in package_id:
        _fail("catalog_identifier_invalid", "installed package must be source/package")
    try:
        return InstalledPackageIdentity(sourceKey=source_key, packageId=package_id)
    except ValidationError:
        _fail("catalog_identifier_invalid", "installed package must be source/package")


def _identifier(identity: InstalledPackageIdentity) -> str:
    return f"{identity.source_key}/{identity.package_id}"


def _review_payload(review: object) -> dict[str, object]:
    value = _public(review)
    if not isinstance(value, dict):
        _fail(
            "internal_error",
            "marketplace review projection is invalid",
            exit_code=EXIT_INTERNAL,
        )
    value = cast(dict[str, object], value)
    value["status"] = "review_required"
    return value


def _review_mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _review_identity(value: object) -> str:
    identity = _review_mapping(value)
    source = identity.get("source_key")
    package = identity.get("package_id")
    return f"{source}/{package}" if source and package else "package"


def _review_text(value: object, args: argparse.Namespace) -> str:
    return _sanitize_message(value, args)


def _render_values(
    label: str,
    values: object,
    args: argparse.Namespace,
    *,
    indent: str = "",
) -> None:
    items = (
        sorted(_review_text(item, args) for item in values)
        if isinstance(values, list)
        else []
    )
    print(f"{indent}{label}: {', '.join(items) if items else 'none'}")


def _render_requirements(
    requirements: object,
    args: argparse.Namespace,
    *,
    title: str = "External requirements",
    indent: str = "",
) -> None:
    fields = _review_mapping(requirements)
    print(f"{indent}{title}")
    for field in ("runtimes", "tools", "providers", "services", "secrets"):
        _render_values(field, fields.get(field), args, indent=f"{indent}  ")


def _render_diagnostics(
    label: str,
    diagnostics: object,
    args: argparse.Namespace,
    *,
    indent: str = "",
) -> None:
    items = (
        [_review_mapping(item) for item in diagnostics if isinstance(item, Mapping)]
        if isinstance(diagnostics, list)
        else []
    )
    items.sort(
        key=lambda item: (str(item.get("code", "")), str(item.get("message", "")))
    )
    print(f"{indent}{label} ({len(items)})")
    for item in items:
        code = _review_text(item.get("code", "diagnostic"), args)
        severity = _review_text(item.get("severity", "finding"), args)
        message = _review_text(item.get("message", "finding"), args)
        print(f"{indent}- {code} [{severity}]: {message}")


def _render_file_changes(changes: object, args: argparse.Namespace) -> None:
    items = (
        [_review_mapping(item) for item in changes if isinstance(item, Mapping)]
        if isinstance(changes, list)
        else []
    )
    items.sort(
        key=lambda item: (
            str(item.get("kind", "")),
            str(item.get("path", "")),
            str(item.get("old_path", "")),
        )
    )
    print(f"File changes ({len(items)})")
    for item in items:
        kind = _review_text(item.get("kind", "change"), args)
        path = _review_text(item.get("path", "resource"), args)
        old_path = item.get("old_path")
        display_path = (
            f"{_review_text(old_path, args)} -> {path}"
            if isinstance(old_path, str)
            else path
        )
        metadata = []
        for key, label in (
            ("old_digest", "old_digest"),
            ("candidate_digest", "candidate_digest"),
        ):
            digest = item.get(key)
            if isinstance(digest, str):
                metadata.append(f"{label}={_review_text(digest, args)}")
        suffix = f"; {'; '.join(metadata)}" if metadata else ""
        print(f"- {kind}: {display_path}{suffix}")


def _render_set_changes(
    title: str,
    changes: object,
    args: argparse.Namespace,
    *,
    fields: tuple[str, ...] | None = None,
) -> None:
    mapping = _review_mapping(changes)
    print(title)
    names = fields or ("",)
    for field in names:
        values = _review_mapping(mapping.get(field)) if field else mapping
        prefix = f"{field} " if field else ""
        for operation, marker in (("added", "+"), ("removed", "-")):
            members = values.get(operation)
            if isinstance(members, list):
                for member in sorted(_review_text(item, args) for item in members):
                    print(f"- {prefix}{marker} {member}")


def _render_structured_changes(
    title: str,
    changes: object,
    args: argparse.Namespace,
    *,
    compatibility: bool,
) -> None:
    mapping = _review_mapping(changes)
    print(title)
    rows = []
    for operation, marker in (("added", "+"), ("removed", "-")):
        values = mapping.get(operation)
        if not isinstance(values, list):
            continue
        for value in values:
            item = _review_mapping(value)
            workflow = _review_text(item.get("workflow_name", "workflow"), args)
            if compatibility:
                detail = (
                    f"code={_review_text(item.get('code', 'finding'), args)} "
                    f"severity={_review_text(item.get('severity', 'finding'), args)}"
                )
            else:
                detail = (
                    f"package={_review_text(item.get('package_digest', ''), args)} "
                    f"risk={_review_text(item.get('risk_digest', ''), args)}"
                )
            rows.append((workflow, marker, detail))
    for workflow, marker, detail in sorted(rows):
        print(f"- {marker} {workflow} {detail}")


def _render_candidate_workflows(workflows: object, args: argparse.Namespace) -> None:
    items = (
        [_review_mapping(item) for item in workflows if isinstance(item, Mapping)]
        if isinstance(workflows, list)
        else []
    )
    items.sort(key=lambda item: str(item.get("workflow_name", "")))
    print(f"Candidate workflow risks ({len(items)})")
    for item in items:
        name = _review_text(item.get("workflow_name", "workflow"), args)
        package = _review_text(item.get("package_digest", ""), args)
        risk = _review_text(item.get("risk_digest", ""), args)
        print(f"- {name} package={package} risk={risk}")


def _render_lifecycle_review(
    value: Mapping[str, object],
    args: argparse.Namespace,
    *,
    operation: str,
    target: str,
) -> None:
    print(
        f"Source: {_review_text(value.get('source_name', target.split('/')[0]), args)}"
    )
    repository = value.get("repository_url")
    if isinstance(repository, str):
        print(f"Repository: {_review_text(repository, args)}")
    configured_ref = value.get("configured_ref")
    print(
        "Configured ref: "
        f"{_review_text(configured_ref, args) if configured_ref else 'default'}"
    )
    print(f"Destination identity: {target}")
    package_path = value.get("package_path")
    if isinstance(package_path, str):
        print(f"Package path: {_review_text(package_path, args)}")
    if operation == "install":
        print(
            f"Candidate version: {_review_text(value.get('candidate_version'), args)}"
        )
        print(f"Candidate commit: {_review_text(value.get('resolved_commit'), args)}")
        print(f"Candidate digest: {_review_text(value.get('candidate_digest'), args)}")
    else:
        for key, label in (
            ("old_version", "Old version"),
            ("candidate_version", "Candidate version"),
            ("old_commit", "Old commit"),
            ("candidate_commit", "Candidate commit"),
            ("old_digest", "Old digest"),
            ("candidate_digest", "Candidate digest"),
        ):
            print(f"{label}: {_review_text(value.get(key), args)}")
    _render_file_changes(value.get("file_changes"), args)
    assessment = _review_mapping(value.get("assessment"))
    if operation == "update":
        _render_set_changes(
            "Workflow membership changes", value.get("workflow_changes"), args
        )
        _render_set_changes(
            "Requirement changes",
            value.get("requirement_changes"),
            args,
            fields=("runtimes", "tools", "providers", "services", "secrets"),
        )
        _render_structured_changes(
            "Risk changes", value.get("risk_changes"), args, compatibility=False
        )
        _render_structured_changes(
            "Compatibility changes",
            value.get("compatibility_changes"),
            args,
            compatibility=True,
        )
    else:
        _render_values("Workflow membership", assessment.get("workflow_names"), args)
    _render_candidate_workflows(value.get("workflow_reviews"), args)
    _render_requirements(assessment.get("external_requirements"), args)
    _render_diagnostics("Blockers", assessment.get("blockers"), args)
    _render_diagnostics("Advisories", assessment.get("advisories"), args)


def _render_remove_review(
    value: Mapping[str, object], args: argparse.Namespace, *, target: str
) -> None:
    print(f"Installed identity: {target}")
    print(f"Current version: {_review_text(value.get('current_version'), args)}")
    print(f"Current commit: {_review_text(value.get('current_commit'), args)}")
    print(
        f"Distribution digest: {_review_text(value.get('distribution_digest'), args)}"
    )
    print(f"Installed provenance removed: {target}")
    print(f"Trust origin removed: marketplace:{target}")
    workflows = value.get("workflow_names")
    items = (
        sorted(_review_text(item, args) for item in workflows)
        if isinstance(workflows, list)
        else []
    )
    print(f"Workflows removed ({len(items)})")
    for item in items:
        print(f"- {item}")


def _render_trust_review(
    value: Mapping[str, object], args: argparse.Namespace, *, target: str
) -> None:
    print(f"Source: {_review_text(value.get('source_name'), args)}")
    print(f"Installed identity: {target}")
    print(f"Version: {_review_text(value.get('version'), args)}")
    print(f"Exact commit: {_review_text(value.get('resolved_commit'), args)}")
    print(
        f"Distribution digest: {_review_text(value.get('distribution_digest'), args)}"
    )
    resources = value.get("package_resources")
    resource_items = (
        sorted(_review_text(item, args) for item in resources)
        if isinstance(resources, list)
        else []
    )
    print(f"Package-owned resources ({len(resource_items)})")
    for resource in resource_items:
        print(f"- {resource}")
    _render_workflow_details(value.get("workflows"), args)


def _render_workflow_details(workflow_values: object, args: argparse.Namespace) -> None:
    workflows = (
        [_review_mapping(item) for item in workflow_values if isinstance(item, Mapping)]
        if isinstance(workflow_values, list)
        else []
    )
    workflows.sort(key=lambda item: str(item.get("workflow_name", "")))
    for workflow in workflows:
        print(f"Workflow: {_review_text(workflow.get('workflow_name'), args)}")
        print(f"  Definition: {_review_text(workflow.get('definition_path'), args)}")
        companion = workflow.get("companion_path")
        print(f"  Companion: {_review_text(companion, args) if companion else 'none'}")
        print(
            "  Effective package digest: "
            f"{_review_text(workflow.get('package_digest'), args)}"
        )
        print(f"  Risk digest: {_review_text(workflow.get('risk_digest'), args)}")
        print(f"  Current trust: {_review_text(workflow.get('trust_state'), args)}")
        print(
            "  Package resource set: "
            f"{_review_text(workflow.get('package_resource_set'), args)} "
            "(see shared table above)"
        )
        for key, label in (
            ("shell_or_script_nodes", "Shell/script nodes"),
            ("command_nodes", "Command nodes"),
            ("approval_nodes", "Approval nodes"),
            ("command_resources", "Command resources"),
            ("script_resources", "Script resources"),
            ("mcp_resources", "MCP resources"),
            ("mcp_resource_files", "MCP resource files"),
            ("local_mcp_servers", "Local MCP servers"),
            ("remote_mcp_servers", "Remote MCP servers"),
            ("requested_tools", "Requested tools"),
            ("requested_skills", "Requested skills"),
            ("providers", "Providers"),
            ("outward_action_nodes", "Outward action nodes"),
            ("required_secrets", "Required secrets"),
        ):
            _render_values(label, workflow.get(key), args, indent="  ")
        _render_requirements(workflow.get("external_requirements"), args, indent="  ")
        _render_diagnostics(
            "Compatibility findings",
            workflow.get("compatibility"),
            args,
            indent="  ",
        )


def _render_inspection(package: Mapping[str, object], args: argparse.Namespace) -> None:
    identity = _review_mapping(package.get("identity"))
    identifier = package.get("identifier") or _review_identity(identity)
    print(f"Package: {_review_text(identifier, args)}")
    for key, label in (
        ("display_name", "Display name"),
        ("source_name", "Source"),
        ("publisher", "Publisher"),
        ("version", "Version"),
        ("description", "Description"),
    ):
        print(f"{label}: {_review_text(package.get(key), args)}")
    _render_values("Tags", package.get("tags"), args)
    print(f"License: {_review_text(package.get('license'), args)}")
    print(f"Repository: {_review_text(package.get('repository_url'), args)}")
    configured_ref = package.get("configured_ref")
    print(
        "Configured ref: "
        f"{_review_text(configured_ref, args) if configured_ref else 'default'}"
    )
    print(f"Verified: {'yes' if package.get('verified') is True else 'no'}")
    for key, label in (
        ("resolved_commit", "Exact commit"),
        ("verified_at", "Verified at"),
        ("source_state", "Source status"),
        ("package_path", "Package path"),
        ("contract_version", "Contract version"),
        ("package_digest", "Distribution digest"),
        ("install_status", "Install status"),
        ("update_status", "Update status"),
    ):
        print(f"{label}: {_review_text(package.get(key), args)}")

    resource_values = package.get("resources")
    resources = (
        [_review_mapping(item) for item in resource_values if isinstance(item, Mapping)]
        if isinstance(resource_values, list)
        else []
    )
    resources.sort(key=lambda item: str(item.get("path", "")))
    print(f"Package resources ({len(resources)})")
    for resource in resources:
        path = _review_text(resource.get("path", "resource"), args)
        types = resource.get("types")
        labels = (
            sorted(_review_text(item, args) for item in types)
            if isinstance(types, list)
            else []
        )
        print(f"- {path} [{', '.join(labels) if labels else 'other'}]")

    workflows = package.get("workflows")
    count = len(workflows) if isinstance(workflows, list) else 0
    print(f"Workflows ({count})")
    _render_workflow_details(workflows, args)
    _render_requirements(package.get("external_requirements"), args)
    _render_diagnostics("Blockers", package.get("blockers"), args)
    _render_diagnostics("Advisories", package.get("advisories"), args)

    installed = package.get("installed")
    if not isinstance(installed, Mapping):
        print("Installed provenance: none")
        return
    provenance = cast(Mapping[str, object], installed)
    print("Installed provenance")
    for key, label in (
        ("source_name", "Installed source"),
        ("repository_url", "Installed repository"),
        ("configured_ref", "Installed configured ref"),
        ("resolved_commit", "Installed commit"),
        ("package_path", "Installed package path"),
        ("version", "Installed version"),
        ("contract_version", "Installed contract version"),
        ("distribution_digest", "Installed digest"),
        ("installed_at", "Installed at"),
        ("actor", "Installed actor"),
        ("orphaned_source", "Orphaned source"),
    ):
        value = provenance.get(key)
        if key == "configured_ref" and value is None:
            value = "default"
        print(f"{label}: {_review_text(value, args)}")
    _render_values("Installed workflows", provenance.get("workflow_paths"), args)


def _render_review(
    review: object,
    *,
    args: argparse.Namespace,
    reveal_token: bool,
) -> None:
    value = _review_payload(review)
    operation = str(value.get("operation") or "trust")
    target = _review_identity(value.get("identity"))
    print(f"Review required: {operation} {target}")
    if operation in {"install", "update"}:
        _render_lifecycle_review(value, args, operation=operation, target=target)
    elif operation == "remove":
        _render_remove_review(value, args, target=target)
    else:
        _render_trust_review(value, args, target=target)
    token = value.get("confirmation_token")
    if reveal_token and isinstance(token, str):
        print(f"Confirmation token: {token}")


def _render_human(result: Mapping[str, object], args: argparse.Namespace) -> None:
    if args.workflow_action == "package-state":
        print(f"Package: {_review_identity(result.get('identity'))}")
        print(f"Profile: {result.get('profile')}")
        print(f"State: {result.get('state')}")
        print(f"Recovery: {result.get('recovery')}; busy: {result.get('busy')}")
        installed = result.get("installed")
        if isinstance(installed, Mapping):
            print(f"Currently installed: {installed.get('version')}")
        return
    if args.workflow_action == "recover-packages":
        print(f"Profile: {result.get('profile')}; recovery: {result.get('status')}")
        for entry in cast(list[dict], result.get("remaining", [])):
            print(
                f"- {_review_identity(entry.get('identity'))}: {entry.get('classification')}"
            )
        return
    status = _review_text(result.get("status", "ok"), args)
    print(status.replace("_", " ").title())
    package = result.get("package")
    if isinstance(package, Mapping):
        package_projection = cast(Mapping[str, object], package)
        if "resources" in package_projection and "workflows" in package_projection:
            _render_inspection(package_projection, args)
            package_projection = {}
        identity = package_projection.get("identity")
        if isinstance(identity, Mapping):
            identity_projection = cast(Mapping[str, object], identity)
            print(
                "Package: "
                f"{identity_projection.get('source_key')}/"
                f"{identity_projection.get('package_id')}"
            )
        if package_projection.get("version"):
            print(f"Version: {package_projection['version']}")
    if result.get("trust_required") is True:
        print("Trust required to run")
    for collection_key in ("sources", "packages", "checks", "refreshes"):
        collection = result.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection[:200]:
            if not isinstance(item, Mapping):
                continue
            identity = item.get("identifier") or item.get("name")
            nested = item.get("identity")
            if identity is None and isinstance(nested, Mapping):
                identity = f"{nested.get('source_key')}/{nested.get('package_id')}"
            suffix = item.get("version") or item.get("state") or item.get("status")
            print(f"- {identity or 'item'}{f' ({suffix})' if suffix else ''}")


def _emit_result(
    command: str,
    result: Mapping[str, object],
    *,
    args: argparse.Namespace,
    as_json: bool,
) -> int:
    if as_json:
        print(
            json.dumps(
                _success_envelope(command, result),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        if result.get("status") == "review_required":
            _render_review(result, args=args, reveal_token=True)
        else:
            _render_human(result, args)
    return 0


def _emit_error(
    command: str,
    error: _MarketplaceCLIError,
    *,
    args: argparse.Namespace,
) -> int:
    message = _sanitize_message(error.message, args)
    code = error.code if _STABLE_CODE.fullmatch(error.code) else "internal_error"
    if getattr(args, "json", False):
        print(
            json.dumps(
                _error_envelope(
                    command,
                    code=code,
                    message=message,
                    result=error.result,
                ),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        if isinstance(error.result, Mapping):
            partial = cast(Mapping[str, object], error.result)
            if args.workflow_action in {"package-state", "recover-packages"}:
                _render_human(partial, args)
            if partial.get("status") == "partial_failure":
                print("Partial failure")
                for key, label in (("succeeded", "Succeeded"), ("failed", "Failed")):
                    items = partial.get(key)
                    if not isinstance(items, list):
                        continue
                    for item in items[:200]:
                        if isinstance(item, Mapping):
                            projected = cast(Mapping[str, object], item)
                            identifier = projected.get("identifier", "item")
                            print(f"- {label}: {identifier}")
        print(f"{code}: {message}", file=sys.stderr)
    return error.exit_code


def _confirmable(
    args: argparse.Namespace,
    *,
    operation: str,
    prepare: Callable[[], object],
    confirm: Callable[[str], object],
) -> tuple[object, bool]:
    if getattr(args, "confirmation_token", None):
        return confirm(args.confirmation_token), True
    review = prepare()
    token = getattr(review, "confirmation_token", None)
    if getattr(args, "prepare_only", False):
        return review, False
    if token is None:
        return review, False
    if getattr(args, "yes", False):
        return confirm(token), True
    _render_review(review, args=args, reveal_token=False)
    if not _read_confirmation(f"Confirm {operation}? [y/N] "):
        return {"status": "cancelled", "operation": operation}, False
    return confirm(token), True


def _confirmed_package_result(
    status: str,
    package: InstalledPackage,
    *,
    service,
    include_trust: bool,
) -> dict[str, object]:
    result: dict[str, object] = {"status": status, "package": _public(package)}
    if include_trust:
        states = dict(sorted(service.workflow_trust(package.identity).items()))
        result["workflow_trust"] = states
        result["trust_required"] = any(state != "trusted" for state in states.values())
    return result


def _invoke_source(args: argparse.Namespace, service) -> dict[str, object]:
    action = getattr(args, "source_action", None)
    if action is None:
        _fail("invalid_request", "source action is required", exit_code=EXIT_INVOCATION)
    if action == "add":
        try:
            source = WorkflowMarketplaceSource(
                name=args.name,
                repositoryUrl=args.repository_url,
                ref=args.ref,
            )
        except ValidationError:
            _fail("source_invalid", "marketplace source is invalid")
        return {"status": "source_added", "source": _public(service.add_source(source))}
    if action == "list":
        sources = sorted(service.list_sources(), key=lambda item: item.name)
        return {"status": "ok", "sources": _public(sources)}
    if action == "remove":
        return {
            "status": "source_removed",
            "source": _public(service.remove_source(args.name)),
        }
    if action == "refresh" and args.name is not None:
        refresh = service.refresh_source(args.name)
        result = {
            "status": refresh.state,
            "refresh": _diagnostic_projection(refresh, args),
        }
        if refresh.diagnostic_code is not None:
            _fail(
                refresh.diagnostic_code,
                refresh.message or "marketplace source refresh failed",
                result=result,
            )
        return result
    if action == "refresh":
        succeeded = []
        failed = []
        for source in sorted(service.list_sources(), key=lambda item: item.name):
            try:
                refresh = service.refresh_source(source.name)
                if refresh.diagnostic_code is not None:
                    failed.append({
                        "identifier": source.name,
                        "error": {
                            "code": refresh.diagnostic_code,
                            "message": _sanitize_message(
                                refresh.message or "marketplace source refresh failed",
                                args,
                            ),
                        },
                    })
                else:
                    succeeded.append(_diagnostic_projection(refresh, args))
            except WorkflowMarketplaceError as error:
                failed.append({
                    "identifier": source.name,
                    "error": {
                        "code": error.code,
                        "message": _sanitize_message(error.message, args),
                    },
                })
        if failed:
            result = {
                "status": "partial_failure",
                "refreshes": succeeded,
                "failed": failed,
            }
            _fail(
                "partial_failure",
                "one or more marketplace sources failed to refresh",
                exit_code=EXIT_ACTION_FAILED,
                result=result,
            )
        return {"status": "ok", "refreshes": succeeded}
    _fail("invalid_request", "unknown source action", exit_code=EXIT_INVOCATION)


def _invoke_install(args: argparse.Namespace, service) -> dict[str, object]:
    try:
        request = InstallRequest.model_validate({
            "identifier": args.identifier,
            "ref": args.ref,
            "packagePath": args.package_path,
        })
    except ValidationError:
        _fail("install_request_invalid", "install request is invalid")
    value, confirmed = _confirmable(
        args,
        operation="install",
        prepare=lambda: service.prepare_install(request, actor=_actor(args)),
        confirm=lambda token: service.confirm_install(token, actor=_actor(args)),
    )
    if not confirmed:
        if isinstance(value, Mapping):
            return cast(dict[str, object], _public(value))
        return _review_payload(value)
    return _confirmed_package_result(
        "installed",
        cast(InstalledPackage, value),
        service=service,
        include_trust=True,
    )


def _invoke_update_one(
    args: argparse.Namespace,
    service,
    identity: InstalledPackageIdentity,
) -> dict[str, object]:
    value, confirmed = _confirmable(
        args,
        operation="update",
        prepare=lambda: service.prepare_update(identity, actor=_actor(args)),
        confirm=lambda token: service.confirm_update(token, actor=_actor(args)),
    )
    if not confirmed:
        if isinstance(value, Mapping):
            return cast(dict[str, object], _public(value))
        if getattr(value, "confirmation_token", None) is None:
            rendered = _public(value)
            if isinstance(rendered, dict):
                rendered = cast(dict[str, object], rendered)
                rendered["status"] = "unchanged"
                return rendered
        return _review_payload(value)
    return _confirmed_package_result(
        "updated",
        cast(InstalledPackage, value),
        service=service,
        include_trust=True,
    )


def _invoke_update_all(args: argparse.Namespace, service) -> dict[str, object]:
    if getattr(args, "confirmation_token", None):
        _fail(
            "invalid_request",
            "--confirmation-token cannot be combined with --all",
            exit_code=EXIT_INVOCATION,
        )
    succeeded = []
    failed = []
    packages = sorted(
        service.installed_packages(),
        key=lambda item: (item.identity.source_key, item.identity.package_id),
    )
    for package in packages:
        identifier = _identifier(package.identity)
        try:
            result = _invoke_update_one(args, service, package.identity)
            succeeded.append({"identifier": identifier, "result": result})
        except WorkflowMarketplaceError as error:
            failed.append({
                "identifier": identifier,
                "error": {
                    "code": error.code,
                    "message": _sanitize_message(error.message, args),
                },
            })
        except _MarketplaceCLIError as error:
            failed.append({
                "identifier": identifier,
                "error": {
                    "code": error.code,
                    "message": _sanitize_message(error.message, args),
                },
            })
    if failed:
        result = {
            "status": "partial_failure",
            "succeeded": succeeded,
            "failed": failed,
        }
        _fail(
            "partial_failure",
            "one or more installed packages failed to update",
            exit_code=EXIT_ACTION_FAILED,
            result=result,
        )
    return {"status": "ok", "succeeded": succeeded, "failed": []}


def _invoke_uninstall(args: argparse.Namespace, service) -> dict[str, object]:
    identity = _identity(args.identifier)
    value, confirmed = _confirmable(
        args,
        operation="uninstall",
        prepare=lambda: service.prepare_remove(identity, actor=_actor(args)),
        confirm=lambda token: service.confirm_remove(token, actor=_actor(args)),
    )
    if not confirmed:
        if isinstance(value, Mapping):
            return cast(dict[str, object], _public(value))
        return _review_payload(value)
    return {"status": "uninstalled", "package": _public(value)}


def _invoke_trust(args: argparse.Namespace, service) -> dict[str, object]:
    identity = _identity(args.name)
    value, confirmed = _confirmable(
        args,
        operation="trust",
        prepare=lambda: service.review_trust(
            identity,
            actor=_actor(args),
            workflow_name=args.workflow,
        ),
        confirm=lambda token: service.grant_trust(token, actor=_actor(args)),
    )
    if not confirmed:
        if isinstance(value, Mapping):
            return cast(dict[str, object], _public(value))
        return _review_payload(value)
    states = cast(Mapping[str, object], value)
    return {
        "status": "trusted",
        "workflows": _public({key: states[key] for key in sorted(states)}),
    }


def _looks_like_loose_workflow_path(value: str) -> bool:
    path = Path(value).expanduser()
    return bool(
        path.is_absolute()
        or value.startswith(("./", "../"))
        or path.suffix.lower() in {".yaml", ".yml"}
        or path.is_file()
    )


def _marketplace_hybrid(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "installed_package", False)
        or ("/" in args.name and not _looks_like_loose_workflow_path(args.name))
    )


def _validate_before_service(args: argparse.Namespace) -> bool:
    action = args.workflow_action
    if action == "trust":
        marketplace = _marketplace_hybrid(args)
        marketplace_flags = bool(
            getattr(args, "workflow", None)
            or getattr(args, "installed_package", False)
            or _has_confirmation_mode(args)
        )
        if args.digest is not None and marketplace_flags:
            _fail(
                "invalid_request",
                "legacy --digest cannot be combined with marketplace trust options",
                exit_code=EXIT_INVOCATION,
            )
        if args.digest is not None:
            return False
        if not marketplace:
            _fail(
                "invalid_request",
                "legacy workflow trust requires --digest",
                exit_code=EXIT_INVOCATION,
            )
        _require_confirmation_mode(args)
        return True
    if action == "untrust":
        marketplace = _marketplace_hybrid(args)
        if not marketplace and getattr(args, "workflow", None):
            _fail(
                "invalid_request",
                "--workflow requires an installed marketplace package",
                exit_code=EXIT_INVOCATION,
            )
        return marketplace
    if action not in MARKETPLACE_ACTIONS:
        return False
    if action == "recover-packages" and not args.yes and not _stdin_is_tty():
        _fail(
            "confirmation_required",
            "interactive confirmation or --yes is required for profile-wide package recovery",
            exit_code=EXIT_AUTHORIZATION,
        )
    if action == "source" and getattr(args, "source_action", None) is None:
        _fail("invalid_request", "source action is required", exit_code=EXIT_INVOCATION)
    if action == "update":
        if bool(args.identifier) == bool(args.all):
            _fail(
                "invalid_request",
                "update requires exactly one source/package or --all",
                exit_code=EXIT_INVOCATION,
            )
    if action in _CONFIRMABLE_ACTIONS:
        _require_confirmation_mode(args)
    return True


def _invoke_package_state(args: argparse.Namespace, service) -> dict[str, object]:
    from .lifecycle_state import read_package_state

    state = read_package_state(service, _identity(args.identifier))
    result = state.model_dump(mode="json", by_alias=False)
    if state.state == "unconfirmed":
        _fail(
            "package_state_unconfirmed",
            "package state cannot be confirmed on this backend/profile",
            result=result,
        )
    return result


def _recovery_result(service, inspection, *, status: str, recovered=(), affected=None):
    return {
        "status": status,
        "profile": service.profile,
        "affected_identities": [
            _public(identity)
            for identity in sorted(
                {entry.identity for entry in (affected or inspection).entries},
                key=lambda identity: (identity.source_key, identity.package_id),
            )
        ],
        "recovered_transaction_ids": list(recovered),
        "remaining": _public(inspection.entries),
        "complete": inspection.complete,
    }


def _require_recovery_idle(service, inspection):
    if not inspection.complete:
        _fail(
            "transaction_recovery_inspection_incomplete",
            "recovery scope could not be inspected completely",
        )
    if inspection.active_writer:
        _fail(
            "transaction_recovery_busy",
            "an active writer lease prevents package recovery on this backend/profile",
            exit_code=EXIT_CONFLICT,
            result=_recovery_result(service, inspection, status="busy"),
        )
    if inspection.has_live_preparations:
        # The lock serializes writers, not time. An unused review could expire
        # after inspection and expand recovery's pruning/staging cleanup set.
        _fail(
            "transaction_recovery_scope_changed",
            "live package reviews can change recovery scope; complete them or let them expire before retrying",
            exit_code=EXIT_CONFLICT,
            result=_recovery_result(service, inspection, status="recovery_required"),
        )


def _invoke_recovery(args: argparse.Namespace, service) -> dict[str, object]:
    store = service.transactions
    before = store.inspect_recovery()
    _require_recovery_idle(service, before)
    if not args.yes:
        print(
            f"Package recovery on this backend, profile {service.profile}:",
            file=sys.stderr,
        )
        for entry in before.entries:
            print(
                f"- {_identifier(entry.identity)}: {entry.kind} ({entry.classification})",
                file=sys.stderr,
            )
        if not before.entries:
            print("No affected package identities.", file=sys.stderr)
        print(
            "Confirm profile-wide package recovery? [y/N] ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        if not _read_confirmation(""):
            result = _recovery_result(service, before, status="cancelled")
            if any(entry.kind == "journal" for entry in before.entries):
                _fail(
                    "transaction_recovery_unfinished",
                    "package recovery was cancelled; journals remain unresolved on this backend/profile",
                    result=result,
                )
            return result
    with store._locked():
        current = store.inspect_recovery()
        _require_recovery_idle(service, current)
        if current != before:
            _fail(
                "transaction_recovery_scope_changed",
                "package recovery scope changed; inspect and confirm again",
                exit_code=EXIT_CONFLICT,
                result=_recovery_result(service, current, status="recovery_required"),
            )
        recovered = store.recover_transactions()
        remaining = store.inspect_recovery()
        result = _recovery_result(
            service,
            remaining,
            status="recovery_required" if remaining.entries else "clear",
            recovered=recovered,
            affected=before,
        )
        if remaining.entries or not remaining.complete:
            _fail(
                "transaction_recovery_ambiguous",
                "package recovery remains unresolved on this backend/profile",
                result=result,
            )
        return result


def _invoke(args: argparse.Namespace, service) -> dict[str, object]:
    action = args.workflow_action
    if action == "package-state":
        return _invoke_package_state(args, service)
    if action == "recover-packages":
        return _invoke_recovery(args, service)
    if action == "source":
        return _invoke_source(args, service)
    if action == "search":
        packages = sorted(
            service.search(args.query, source=args.source, limit=100),
            key=lambda item: item.identifier,
        )
        return {"status": "ok", "packages": _public(packages)}
    if action == "inspect":
        return {"status": "ok", "package": _public(service.inspect(args.identifier))}
    if action == "install":
        return _invoke_install(args, service)
    if action == "installed":
        packages = sorted(
            service.installed_packages(),
            key=lambda item: (item.identity.source_key, item.identity.package_id),
        )
        return {"status": "ok", "packages": _public(packages)}
    if action == "check":
        identity = _identity(args.identifier) if args.identifier else None
        checks = sorted(
            service.check_updates(identity),
            key=lambda item: (item.identity.source_key, item.identity.package_id),
        )
        result: dict[str, object] = {
            "status": "ok",
            "checks": [_diagnostic_projection(item, args) for item in checks],
        }
        failures = [item for item in checks if item.status == "error"]
        if failures:
            result["status"] = "partial_failure"
            _fail(
                "partial_failure",
                "one or more update checks failed",
                exit_code=EXIT_ACTION_FAILED,
                result=result,
            )
        return result
    if action == "update":
        if args.all:
            return _invoke_update_all(args, service)
        return _invoke_update_one(args, service, _identity(args.identifier))
    if action == "uninstall":
        return _invoke_uninstall(args, service)
    if action == "trust":
        return _invoke_trust(args, service)
    if action == "untrust":
        identity = _identity(args.name)
        revoked = service.revoke_trust(identity, workflow_name=args.workflow)
        return {
            "status": "untrusted",
            "identity": _public(identity),
            "workflow": args.workflow,
            "revoked": revoked,
        }
    _fail("invalid_request", "unknown marketplace action", exit_code=EXIT_INVOCATION)


def dispatch_marketplace_command(args: argparse.Namespace) -> int | None:
    """Dispatch one marketplace action, or return ``None`` for legacy actions."""

    command = _command(args)
    try:
        if not _validate_before_service(args):
            return None
        service = _service_for_args(args)
        result = _invoke(args, service)
        return _emit_result(
            command,
            result,
            args=args,
            as_json=getattr(args, "json", False),
        )
    except _MarketplaceCLIError as error:
        return _emit_error(command, error, args=args)
    except WorkflowMarketplaceError as error:
        wrapped = _MarketplaceCLIError(
            error.code,
            error.message,
            exit_code=_exit_code(error.code),
        )
        return _emit_error(command, wrapped, args=args)
    except ValidationError:
        error = _MarketplaceCLIError(
            "invalid_request",
            "marketplace request is invalid",
            exit_code=EXIT_INVOCATION,
        )
        return _emit_error(command, error, args=args)
    except (OSError, ValueError):
        error = _MarketplaceCLIError(
            "action_failed",
            "workflow marketplace operation failed",
            exit_code=EXIT_ACTION_FAILED,
        )
        return _emit_error(command, error, args=args)
    except Exception:
        error = _MarketplaceCLIError(
            "internal_error",
            "workflow marketplace command failed unexpectedly",
            exit_code=EXIT_INTERNAL,
        )
        return _emit_error(command, error, args=args)


__all__ = [
    "MARKETPLACE_ACTIONS",
    "configure_marketplace_parsers",
    "dispatch_marketplace_command",
]
