"""Ericsson GitLab standalone connector registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import tools as gitlab_tools  # noqa: E402
from .models import GitLabError, SAFE_ERROR_MESSAGES  # noqa: E402


_WRITE_TOOLS = frozenset(
    {
        "gitlab_create_branch",
        "gitlab_commit_changes",
        "gitlab_create_merge_request",
        "gitlab_create_mr_note",
        "gitlab_reply_to_discussion",
        "gitlab_resolve_discussion",
        "gitlab_approve_merge_request",
        "gitlab_merge_merge_request",
        "gitlab_update_merge_request",
        "gitlab_retry_job",
        "gitlab_play_job",
        "gitlab_retry_pipeline",
    }
)


def _arg(args: dict, name: str) -> str:
    """Render one argument for an approval prompt, safely and bounded."""
    value = args.get(name) if isinstance(args, dict) else None
    return json.dumps(value, ensure_ascii=True)[:512]


def _arg_or_default(args: dict, name: str, default: object) -> str:
    """Render an argument's effective default in an approval prompt."""
    value = args.get(name, default) if isinstance(args, dict) else default
    return json.dumps(value, ensure_ascii=True)[:512]


WRITE_APPROVALS = {
    "gitlab_create_branch": lambda a: (
        f"Project: {_arg(a, 'project')}\nTicket: {_arg(a, 'ticket_key')}"
    ),
    "gitlab_commit_changes": lambda a: (
        f"Project: {_arg(a, 'project')}\nBranch: {_arg(a, 'branch')}\n"
        f"Message: {_arg(a, 'commit_message')}"
    ),
    "gitlab_create_merge_request": lambda a: (
        f"Project: {_arg(a, 'project')}\n"
        f"Source: {_arg(a, 'source_branch')} -> {_arg(a, 'target_branch')}"
    ),
    "gitlab_create_mr_note": lambda a: (
        f"Project: {_arg(a, 'project')}\nMR: !{_arg(a, 'iid')}\n"
        f"Note: {_arg(a, 'body')}"
    ),
    "gitlab_reply_to_discussion": lambda a: (
        f"Project: {_arg(a, 'project')}\nMR: !{_arg(a, 'iid')}\n"
        f"Thread: {_arg(a, 'discussion_id')}\nReply: {_arg(a, 'body')}"
    ),
    "gitlab_resolve_discussion": lambda a: (
        f"Project: {_arg(a, 'project')}\nMR: !{_arg(a, 'iid')}\n"
        f"Thread: {_arg(a, 'discussion_id')}\n"
        f"Set resolved: {_arg_or_default(a, 'resolved', True)}"
    ),
    "gitlab_approve_merge_request": lambda a: (
        f"Project: {_arg(a, 'project')}\nApprove MR: !{_arg(a, 'iid')}\n"
        f"Pinned SHA: {_arg(a, 'sha')}"
    ),
    "gitlab_merge_merge_request": lambda a: (
        f"Project: {_arg(a, 'project')}\nMERGE MR: !{_arg(a, 'iid')}\n"
        f"Pinned SHA: {_arg(a, 'sha')}\n"
        f"Squash: {_arg(a, 'squash')}  "
        f"Remove source: {_arg(a, 'remove_source_branch')}\n"
        f"Merge when pipeline succeeds: "
        f"{_arg(a, 'merge_when_pipeline_succeeds')}"
    ),
    "gitlab_update_merge_request": lambda a: (
        f"Project: {_arg(a, 'project')}\nUpdate MR: !{_arg(a, 'iid')}\n"
        f"Title: {_arg(a, 'title')}  Draft: {_arg(a, 'draft')}\n"
        f"Description: {_arg(a, 'description')}\n"
        f"State: {_arg(a, 'state_event')}\n"
        f"+labels: {_arg(a, 'add_labels')}  -labels: {_arg(a, 'remove_labels')}"
    ),
    "gitlab_retry_job": lambda a: (
        f"Project: {_arg(a, 'project')}\nRetry job: {_arg(a, 'job_id')}"
    ),
    "gitlab_play_job": lambda a: (
        f"Project: {_arg(a, 'project')}\nPlay manual job: {_arg(a, 'job_id')}"
    ),
    "gitlab_retry_pipeline": lambda a: (
        f"Project: {_arg(a, 'project')}\n"
        f"Retry pipeline: {_arg(a, 'pipeline_id')}"
    ),
}
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
        summarise = WRITE_APPROVALS.get(tool_name)
        if summarise is None:
            return None
        canonical_args = json.dumps(
            args if isinstance(args, dict) else {},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return {
            "action": "approve",
            "message": (
                f"Approve Ericsson GitLab change: {tool_name}\n"
                f"{summarise(args if isinstance(args, dict) else {})}"
            ),
            # Argument-derived, NOT the bare tool name. See
            # tools/approval.py:3366 -- a tool-name rule_key means one
            # "always" blankets every future call of that tool.
            "rule_key": (
                f"{tool_name}:"
                f"{hashlib.sha256(canonical_args.encode('utf-8')).hexdigest()}"
            ),
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
