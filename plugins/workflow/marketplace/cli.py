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


def _render_review(review: object, *, reveal_token: bool) -> None:
    value = _review_payload(review)
    operation = str(value.get("operation") or "trust")
    identity = value.get("identity")
    target = "package"
    if isinstance(identity, Mapping):
        identity_mapping = cast(Mapping[str, object], identity)
        target = (
            f"{identity_mapping.get('source_key')}/{identity_mapping.get('package_id')}"
        )
    print(f"Review required: {operation} {target}")
    version = (
        value.get("candidate_version")
        or value.get("current_version")
        or value.get("version")
    )
    if version:
        print(f"Version: {version}")
    changes = value.get("file_changes")
    if isinstance(changes, list):
        print(f"File changes: {len(changes)}")
    workflows = value.get("workflow_reviews") or value.get("workflows")
    if isinstance(workflows, list):
        print(f"Workflows: {len(workflows)}")
    token = value.get("confirmation_token")
    if reveal_token and isinstance(token, str):
        print(f"Confirmation token: {token}")


def _render_human(result: Mapping[str, object]) -> None:
    status = str(result.get("status", "ok"))
    print(status.replace("_", " ").title())
    package = result.get("package")
    if isinstance(package, Mapping):
        package_projection = cast(Mapping[str, object], package)
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


def _emit_result(command: str, result: Mapping[str, object], *, as_json: bool) -> int:
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
            _render_review(result, reveal_token=True)
        else:
            _render_human(result)
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
    _render_review(review, reveal_token=False)
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
    path = Path(value)
    return bool(
        path.is_absolute()
        or value.startswith(("./", "../"))
        or path.suffix.lower() in {".yaml", ".yml"}
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


def _invoke(args: argparse.Namespace, service) -> dict[str, object]:
    action = args.workflow_action
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
        return _emit_result(command, result, as_json=getattr(args, "json", False))
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
