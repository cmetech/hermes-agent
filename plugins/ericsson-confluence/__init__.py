"""Ericsson Confluence standalone connector registration."""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
from pathlib import Path
import sys
import types

_WRITE_TOOLS = frozenset({
    "confluence_create_page", "confluence_update_page", "confluence_add_comment",
})

_PLUGIN_SKILLS = (
    ("page-research", "Research bounded Confluence page evidence."),
)


def _own_package_loaded() -> bool:
    """Whether this module is running from its real package namespace."""
    package = sys.modules.get(__package__) if __package__ else None
    paths = getattr(package, "__path__", ())
    root = Path(__file__).resolve().parent
    try:
        return any(Path(path).resolve() == root for path in paths)
    except (OSError, TypeError):
        return False


def _direct_package_name() -> str:
    """Create a collision-isolated package for a direct file import."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    index = 0
    while True:
        package_name = f"_ericsson_confluence_direct_{digest}_{index}"
        package = sys.modules.get(package_name)
        if package is None:
            package = types.ModuleType(package_name)
            package.__file__ = str(root / "__init__.py")
            package.__package__ = package_name
            package.__path__ = [str(root)]
            package.__spec__ = importlib.machinery.ModuleSpec(
                package_name, loader=None, is_package=True
            )
            sys.modules[package_name] = package
            break
        try:
            if any(Path(path).resolve() == root for path in package.__path__):
                break
        except (AttributeError, OSError, TypeError):
            pass
        index += 1
    return package_name


# Hermes normally imports this file as a package, but the plugin loader and
# repository tests also support a direct file import. Give that case its own
# package namespace before using qualified local imports. This keeps generic
# ``tools``/``models`` modules out of the resolution path while leaving the
# registration loop structurally visible to the onboarding contract scanner.
if not _own_package_loaded():
    __package__ = _direct_package_name()
    __spec__ = sys.modules[__package__].__spec__

from . import application
from . import tools as confluence_tools
from .models import ConfluenceError, SAFE_ERROR_MESSAGES


def _arg(args: dict, name: str) -> str:
    """Render one argument for an approval prompt, safely and bounded."""
    value = args.get(name) if isinstance(args, dict) else None
    try:
        return json.dumps(value, ensure_ascii=True)[:512]
    except (TypeError, ValueError):
        return '"<unrepresentable>"'


WRITE_APPROVALS = {
    "confluence_create_page": lambda a: (
        f"Space: {_arg(a, 'space_key')}\nTitle: {_arg(a, 'title')}\n"
        f"Parent: {_arg(a, 'parent_id')}\nBody: {_arg(a, 'markdown')}"
    ),
    "confluence_update_page": lambda a: (
        f"Page: {_arg(a, 'content_id')}\nNew title: {_arg(a, 'title')}\n"
        f"New body: {_arg(a, 'markdown')}"
    ),
    "confluence_add_comment": lambda a: (
        f"Page: {_arg(a, 'content_id')}\nComment: {_arg(a, 'markdown')}"
    ),
}


def _has_write_admission(admission: object, tool_name: str) -> bool:
    """Accept only the host admission minted for this exact write tool."""
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _interrupt_authority():
    try:
        from tools.interrupt import is_interrupted
    except ImportError:
        return lambda: False
    return is_interrupted


def register(ctx: object) -> None:
    """Register bounded Confluence reads and the future write hook."""

    def require_write_approval(tool_name: str, args: dict, **_kwargs: object):
        summarise = WRITE_APPROVALS.get(tool_name)
        if summarise is None:
            return None
        try:
            canonical_args = json.dumps(
                args if isinstance(args, dict) else {},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return {"action": "block", "message": "Confluence write arguments cannot be safely approved"}
        return {
            "action": "approve",
            "message": (
                f"Approve Ericsson Confluence change: {tool_name}\n"
                f"{summarise(args if isinstance(args, dict) else {})}"
            ),
            "rule_key": (
                f"{tool_name}:"
                f"{hashlib.sha256(canonical_args.encode('utf-8')).hexdigest()}"
            ),
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        skill_root = Path(__file__).parent / "skills"
        for name, description in _PLUGIN_SKILLS:
            register_skill(name, skill_root / name / "SKILL.md", description)

    # Task 1's minimal hook-only context intentionally has no tool API.
    # Preserve that loading contract while allowing normal plugin hosts to
    # register the read tools below.
    if not hasattr(ctx, "register_tool"):
        return

    def json_error(category: str) -> str:
        return _json(
            {
                "success": False,
                "error": {
                    "category": category,
                    "message": SAFE_ERROR_MESSAGES[category],
                },
            }
        )

    def available() -> bool:
        try:
            return confluence_tools.check_available(ctx.configuration())
        except Exception:
            return False

    def handler(name: str):
        def invoke(args: dict, **_kwargs) -> str:
            if name in _WRITE_TOOLS and not _has_write_admission(
                _kwargs.get("tool_admission"), name
            ):
                return json_error("permission")
            try:
                configuration = ctx.configuration()
            except Exception:
                return json_error("invalid_configuration")
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
                or invocation.provider_id != "ericsson-confluence"
                or invocation.caller_id != "ericsson-connector-cli"
                or invocation.operation not in confluence_tools.SCHEMAS
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
                for name in confluence_tools.SCHEMAS
            },
            allowed_callers={"ericsson-connector-cli"},
            handler=local_command_handler,
        )

    for name, schema in confluence_tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-confluence",
            schema=schema,
            handler=handler(name),
            check_fn=available,
            emoji="📄",
        )
