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

from . import tools as confluence_tools
from .models import ConfluenceError, SAFE_ERROR_MESSAGES, safe_remediation


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
        return (
            getattr(admission, "approved", None) is True
            and getattr(admission, "policy", None) == "plugin_approve"
            and getattr(admission, "tool_name", None) == tool_name
        )
    except Exception:
        return False


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

    def json_error(category: str, remediation: object = None) -> str:
        error = {"category": category, "message": SAFE_ERROR_MESSAGES[category]}
        safe = safe_remediation(remediation)
        if safe:
            error["remediation"] = safe
        return json.dumps(
            {"success": False, "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
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
            try:
                result = confluence_tools.invoke(name, args or {}, configuration)
                return json.dumps(
                    {"success": True, "result": result},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except ConfluenceError as exc:
                return json_error(exc.category, exc.remediation)
            except (KeyError, TypeError, ValueError):
                return json_error("invalid_input")
            except Exception:
                return json_error("transient")

        return invoke

    for name, schema in confluence_tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-confluence",
            schema=schema,
            handler=handler(name),
            check_fn=available,
            emoji="📄",
        )
