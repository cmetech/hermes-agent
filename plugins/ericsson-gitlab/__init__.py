"""Ericsson GitLab standalone connector registration."""

from __future__ import annotations

import json
from pathlib import Path

from . import tools as gitlab_tools  # noqa: E402
from .models import GitLabError, SAFE_ERROR_MESSAGES  # noqa: E402


_WRITE_TOOLS = frozenset(
    {
        "gitlab_create_branch",
        "gitlab_commit_changes",
        "gitlab_create_merge_request",
    }
)
_PLUGIN_SKILLS = (
    (
        "repository-research",
        "Research bounded GitLab repository evidence.",
    ),
    (
        "merge-request-review",
        "Review bounded GitLab merge request evidence.",
    ),
    (
        "ci-investigation",
        "Investigate bounded GitLab CI evidence.",
    ),
    (
        "gitlab-activity-digest",
        "Use for one-time or recurring GitLab commit and merge-request activity digests.",
    ),
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
        return (
            getattr(admission, "approved", None) is True
            and getattr(admission, "policy", None) == "plugin_approve"
            and getattr(admission, "tool_name", None) == tool_name
        )
    except Exception:
        return False


def register(ctx) -> None:
    """Register the connector's bounded read and approval-gated write tools."""

    def available() -> bool:
        try:
            configuration = ctx.configuration()
            gitlab_tools.GitLabAuth.from_configuration(configuration)
            return True
        except Exception:
            return False

    def handler(name):
        def invoke(args: dict, **kwargs) -> str:
            if name in _WRITE_TOOLS:
                admission = kwargs.get("tool_admission")
                if not _has_write_admission(admission, name):
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
                # Resolve the opaque host accessor and its values for this call.
                # Never retain either object across profile generations.
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
            try:
                result = gitlab_tools.invoke(
                    name,
                    args or {},
                    configuration,
                    cancel_check=_interrupt_authority(),
                )
                return _json({"success": True, "result": result})
            except GitLabError as exc:
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": exc.category,
                            "message": SAFE_ERROR_MESSAGES[exc.category],
                        },
                    }
                )
            except (KeyError, TypeError, ValueError):
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "invalid_input",
                            "message": SAFE_ERROR_MESSAGES["invalid_input"],
                        },
                    }
                )
            except Exception:
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "transient",
                            "message": SAFE_ERROR_MESSAGES["transient"],
                        },
                    }
                )

        return invoke

    def require_write_approval(tool_name: str, args: dict, **kwargs):
        if tool_name not in _WRITE_TOOLS:
            return None
        return {
            "action": "approve",
            "message": "Approve Ericsson GitLab mutation",
            "rule_key": tool_name,
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

    for name, schema in gitlab_tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-gitlab",
            schema=schema,
            handler=handler(name),
            check_fn=available,
            emoji="🦊",
        )

    skill_root = Path(__file__).parent / "skills"
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        for name, description in _PLUGIN_SKILLS:
            register_skill(
                name,
                skill_root / name / "SKILL.md",
                description,
            )
