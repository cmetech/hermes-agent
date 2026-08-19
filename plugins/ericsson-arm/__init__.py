"""Ericsson Artifactory standalone connector registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import types


def _owns_standalone_namespace(name: str, root: str) -> bool:
    """Return whether a complete synthetic namespace belongs to this plugin."""
    package = sys.modules.get(name)
    if getattr(package, "_ericsson_arm_root", None) != root:
        return False
    prefix = f"{name}."
    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith(prefix):
            continue
        module_path = getattr(module, "__file__", None)
        try:
            if not isinstance(module_path, str) or not Path(module_path).resolve().is_relative_to(root):
                return False
        except (OSError, ValueError):
            return False
    return True


if not __package__ or not hasattr(sys.modules.get(__package__), "__path__"):
    # Tests and narrow host probes can execute this file by path, which leaves
    # __package__ empty. Give ARM's direct relative imports one unique package
    # namespace instead of falling back to generic top-level tools/models/
    # _common modules that another standalone connector may already own.
    _standalone_root = str(Path(__file__).parent.resolve())
    _standalone_base = (
        "_ericsson_arm_standalone_"
        f"{hashlib.sha256(_standalone_root.encode()).hexdigest()[:16]}"
    )
    _STANDALONE_PACKAGE = ""
    for _suffix in range(64):
        _candidate = (
            _standalone_base if _suffix == 0 else f"{_standalone_base}_{_suffix}"
        )
        _candidate_children = any(
            module_name.startswith(f"{_candidate}.") for module_name in sys.modules
        )
        _standalone_package = sys.modules.get(_candidate)
        if _standalone_package is None and not _candidate_children:
            _STANDALONE_PACKAGE = _candidate
            _standalone_package = types.ModuleType(_STANDALONE_PACKAGE)
            _standalone_package.__path__ = [_standalone_root]
            _standalone_package.__package__ = _STANDALONE_PACKAGE
            _standalone_package._ericsson_arm_root = _standalone_root
            sys.modules[_STANDALONE_PACKAGE] = _standalone_package
            break
        if _owns_standalone_namespace(_candidate, _standalone_root):
            _STANDALONE_PACKAGE = _candidate
            break
    else:
        raise ImportError("unable to allocate an isolated ARM plugin namespace")
    # A file-path loader may give this module a non-package spec parent. The
    # explicit package namespace above is authoritative for relative imports.
    __spec__ = None
    __package__ = _STANDALONE_PACKAGE

from . import application  # noqa: E402
from . import tools as arm_tools  # noqa: E402
from .models import ArmError, SAFE_ERROR_MESSAGES, safe_remediation  # noqa: E402

_WRITE_TOOLS = frozenset({"arm_deploy", "arm_delete"})
_APPROVAL_STRING_LIMITS = {"repo": 128, "path": 1024, "source_file": 4096}
_APPROVAL_REQUIRED_STRINGS = {
    "arm_deploy": frozenset({"repo", "path", "source_file"}),
    "arm_delete": frozenset({"repo", "path"}),
}


def _approval_rule_digest(tool_name: object, args: object) -> str | None:
    """Bind write approval to one bounded, schema-shaped tool request."""
    required_strings = _APPROVAL_REQUIRED_STRINGS.get(tool_name)
    if type(args) is not dict or required_strings is None:
        return None
    # Generic write previews may carry the other write schema's source_file.
    # It is still bounded, validated, and included in the exact rule key; the
    # tool schema rejects it for arm_delete before any write is attempted.
    allowed = set(_APPROVAL_STRING_LIMITS) | {"dry_run", "confirm"}
    if (
        not required_strings.issubset(args)
        or not set(args).issubset(allowed)
        or any(
            type(args[name]) is not str
            or not args[name]
            or len(args[name]) > _APPROVAL_STRING_LIMITS[name]
            for name in set(args) & set(_APPROVAL_STRING_LIMITS)
        )
        or any(
            type(args[name]) is not bool
            for name in ("dry_run", "confirm")
            if name in args
        )
    ):
        return None
    try:
        canonical = json.dumps(
            args, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _arg(args: object, name: str) -> str:
    """Render one already-bounded string argument for approval."""
    value = args.get(name) if type(args) is dict else None
    maximum = _APPROVAL_STRING_LIMITS.get(name, 0)
    if type(value) is not str or not value or len(value) > maximum:
        return '"<invalid>"'
    return json.dumps(value, ensure_ascii=True)


WRITE_APPROVALS = {
    "arm_deploy": lambda a: (
        f"Upload file: {_arg(a, 'source_file')}\n"
        f"To repository: {_arg(a, 'repo')}\n"
        f"At path: {_arg(a, 'path')}"
    ),
    "arm_delete": lambda a: (
        f"Delete from repository: {_arg(a, 'repo')}\n"
        f"Path: {_arg(a, 'path')}\n"
        f"A folder path removes everything beneath it, and Artifactory "
        f"deletion is not recoverable unless trash is enabled."
    ),
}

_PLUGIN_SKILLS = (
    ("artifact-research", "Trace a release artefact back to the build that made it."),
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _interrupt_authority():
    try:
        from tools.interrupt import is_interrupted
    except ImportError:
        return lambda: False
    return is_interrupted


def _has_write_admission(admission, tool_name: str) -> bool:
    try:
        from hermes_cli.plugins import PluginToolAdmission

        return (
            type(admission) is PluginToolAdmission
            and getattr(admission, "approved", None) is True
            and getattr(admission, "policy", None) == "plugin_approve"
            and getattr(admission, "tool_name", None) == tool_name
        )
    except Exception:
        return False


def register(ctx: object) -> None:
    """Register bounded ARM operations and approve writes by exact arguments."""

    def require_write_approval(tool_name: object, args: object, **_kwargs: object):
        summarise = WRITE_APPROVALS.get(tool_name)
        if summarise is None:
            return None
        argument_digest = _approval_rule_digest(tool_name, args)
        if argument_digest is None:
            return {
                "action": "block",
                "message": "ARM write arguments cannot be safely approved",
            }
        return {
            "action": "approve",
            "message": f"Approve Ericsson Artifactory write: {tool_name}\n{summarise(args)}",
            "rule_key": f"{tool_name}:{argument_digest}",
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

    if getattr(ctx, "register_tool", None) is None:
        return

    def available() -> bool:
        try:
            return arm_tools.check_available(ctx.configuration())
        except Exception:
            return False

    def handler(name):
        def invoke(args: dict, **_kwargs) -> str:
            if name in _WRITE_TOOLS and not _has_write_admission(
                _kwargs.get("tool_admission"), name
            ):
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "permission",
                            "message": SAFE_ERROR_MESSAGES["permission"],
                        },
                    }
                )
            try:
                configuration = ctx.configuration()
            except Exception:
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "invalid_configuration",
                            "message": SAFE_ERROR_MESSAGES["invalid_configuration"],
                        },
                    }
                )
            return _json(
                application.execute(
                    name,
                    args or {},
                    configuration,
                    cancel_check=_interrupt_authority(),
                )
            )

        return invoke

    def local_command_handler(invocation):
        permission_error = {
            "success": False,
            "error": {
                "category": "permission",
                "message": SAFE_ERROR_MESSAGES["permission"],
            },
        }
        invalid_input = {
            "success": False,
            "error": {
                "category": "invalid_input",
                "message": SAFE_ERROR_MESSAGES["invalid_input"],
            },
        }
        try:
            from hermes_cli.plugin_application_commands import (
                PluginApplicationCommandInvocation,
                PluginApplicationCommandMode,
            )

            if (
                type(invocation) is not PluginApplicationCommandInvocation
                or not invocation.active
                or invocation.provider_id != "ericsson-arm"
                or invocation.caller_id != "ericsson-connector-cli"
                or invocation.operation not in arm_tools.SCHEMAS
            ):
                return permission_error
            name = invocation.operation
            arguments = dict(invocation.arguments)
            mode = invocation.mode
            if {"dry_run", "confirm"}.intersection(arguments):
                return invalid_input
            if name in _WRITE_TOOLS:
                if mode is PluginApplicationCommandMode.DRY_RUN:
                    arguments["dry_run"] = True
                elif mode is PluginApplicationCommandMode.CONFIRM:
                    arguments["confirm"] = True
                else:
                    return permission_error
            elif mode is not PluginApplicationCommandMode.READ:
                return permission_error
        except Exception:
            return permission_error

        try:
            configuration = ctx.configuration()
        except Exception:
            return {
                "success": False,
                "error": {
                    "category": "invalid_configuration",
                    "message": SAFE_ERROR_MESSAGES["invalid_configuration"],
                },
            }
        return application.execute(
            name,
            arguments,
            configuration,
            cancel_check=_interrupt_authority(),
        )

    if hasattr(ctx, "register_application_commands"):
        ctx.register_application_commands(
            operations={
                name: "write" if name in _WRITE_TOOLS else "read"
                for name in arm_tools.SCHEMAS
            },
            allowed_callers={"ericsson-connector-cli"},
            handler=local_command_handler,
        )

    for name, schema in arm_tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-arm",
            schema=schema,
            handler=handler(name),
            check_fn=available,
            emoji="📦",
        )

    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        skill_root = Path(__file__).parent / "skills"
        for name, description in _PLUGIN_SKILLS:
            register_skill(name, skill_root / name / "SKILL.md", description)
