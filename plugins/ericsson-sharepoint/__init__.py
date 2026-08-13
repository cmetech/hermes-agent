"""Ericsson SharePoint standalone connector registration."""

from __future__ import annotations

import json
from pathlib import Path

from . import audit, auth, client, operations, tools  # noqa: F401
from .models import (
    SharePointAmbiguousWriteError,
    SharePointConfigurationError,
    SharePointFileBoundaryError,
    SharePointResolutionError,
    SharePointWriteError,
)


_WRITE_TOOLS = frozenset(
    {
        "sharepoint_upload",
        "sharepoint_create_folder",
        "sharepoint_move_item",
        "sharepoint_copy_item",
        "sharepoint_recycle_item",
    }
)

_PLUGIN_SKILLS = (
    (
        "sharepoint-navigation",
        "Resolve SharePoint URLs and perform bounded file, folder, and site discovery.",
    ),
    (
        "sharepoint-file-operations",
        "Download files and guide explicitly approved SharePoint mutations.",
    ),
    (
        "sharepoint-permission-audit",
        "Collect bounded permission evidence through an enrolled browser.",
    ),
)


def _has_write_admission(admission, tool_name: str) -> bool:
    return bool(
        getattr(admission, "approved", None) is True
        and getattr(admission, "policy", None) == "plugin_approve"
        and getattr(admission, "tool_name", None) == tool_name
    )


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def register(ctx) -> None:
    """Register user-invoked setup actions; tools are added by later slices."""
    ctx.register_setup_action(
        "authenticate", auth.authenticate, readiness=auth.graph_ready
    )
    ctx.register_setup_action("test_connection", auth.test_connection)
    ctx.register_setup_action("enroll_browser", auth.enroll_browser)
    ctx.register_setup_action("clear_session", auth.clear_session)

    def require_write_approval(tool_name, _arguments, **_kwargs):
        if tool_name not in _WRITE_TOOLS:
            return None
        return {
            "action": "approve",
            "message": "Approve Ericsson SharePoint mutation",
            "rule_key": tool_name,
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

    def available() -> bool:
        try:
            return auth.graph_ready(ctx.configuration())
        except Exception:
            return False

    def handler(name):
        async def invoke(arguments: dict, **_kwargs) -> str:
            if name in _WRITE_TOOLS and not _has_write_admission(
                _kwargs.get("tool_admission"), name
            ):
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "approval_required",
                            "message": "SharePoint mutation requires approval.",
                        },
                    }
                )
            try:
                configuration = ctx.configuration()
                try:
                    from tools.interrupt import is_interrupted
                except ImportError:

                    def is_interrupted():
                        return False

                result = await tools.invoke(
                    name,
                    arguments or {},
                    configuration,
                    file_authorization=_kwargs.get("file_authorization"),
                    unattended=_kwargs.get("unattended") is True,
                    cancel_check=is_interrupted,
                )
                return _json({"success": True, "result": result})
            except SharePointConfigurationError:
                category = "configuration_required"
            except SharePointFileBoundaryError:
                category = "permission_denied"
            except SharePointAmbiguousWriteError:
                category = "ambiguous_write"
            except SharePointWriteError:
                category = "invalid_input"
            except (SharePointResolutionError, ValueError, TypeError, KeyError):
                category = "invalid_input"
            except Exception:
                category = "remote_unavailable"
            return _json(
                {
                    "success": False,
                    "error": {
                        "category": category,
                        "message": (
                            "SharePoint write outcome is uncertain; inspect the remote "
                            "destination before any retry."
                            if category == "ambiguous_write"
                            else "SharePoint operation could not be completed."
                        ),
                    },
                }
            )

        return invoke

    for name, schema in tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-sharepoint",
            schema=schema,
            handler=handler(name),
            check_fn=(lambda: operations.audit_ready(ctx.configuration()))
            if name == "sharepoint_audit_permissions"
            else available,
            is_async=True,
            emoji="📁",
        )

    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        skill_root = Path(__file__).parent / "skills"
        for name, description in _PLUGIN_SKILLS:
            register_skill(name, skill_root / name / "SKILL.md", description)
