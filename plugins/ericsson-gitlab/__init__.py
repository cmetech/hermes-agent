"""Ericsson GitLab standalone connector registration."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import application  # noqa: E402
from . import tools as gitlab_tools  # noqa: E402
from .models import GitLabError, SAFE_ERROR_MESSAGES  # noqa: E402


_WRITE_TOOLS = frozenset(
    {
        "gitlab_create_branch",
        "gitlab_create_named_branch",
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
_INVALID_APPROVAL_MESSAGE = "GitLab write arguments cannot be safely approved"
# Commit operations cap aggregate UTF-8 content at 512 KiB and permit 100
# bounded 4,096-character paths. Eight MiB covers worst-case JSON escaping of
# that operation-valid request while preventing unbounded pre-schema hashing.
_MAX_APPROVAL_CANONICAL_BYTES = 8 * 1024 * 1024
_MAX_APPROVAL_CANONICAL_DEPTH = 64
_MAX_APPROVAL_CANONICAL_NODES = 12_000
_MAX_APPROVAL_RENDERED_ARGUMENT = 512
_MAX_APPROVAL_PREVIEW_STRING = 256


class _InvalidApprovalArguments(Exception):
    """Internal marker for values that cannot safely bind an approval."""


def _approval_rule_digest(args) -> str | None:
    """Hash exact JSON arguments without building a second large payload."""
    digest = hashlib.sha256()
    used = 0
    nodes = 0
    active: set[int] = set()

    def emit(fragment: str) -> None:
        nonlocal used
        try:
            encoded = fragment.encode("utf-8")
        except UnicodeEncodeError:
            raise _InvalidApprovalArguments from None
        if used + len(encoded) > _MAX_APPROVAL_CANONICAL_BYTES:
            raise _InvalidApprovalArguments
        digest.update(encoded)
        used += len(encoded)

    def emit_string(value: str) -> None:
        emit('"')
        fragments: list[str] = []
        for character in value:
            if character == '"':
                escaped = '\\"'
            elif character == "\\":
                escaped = "\\\\"
            elif character == "\b":
                escaped = "\\b"
            elif character == "\f":
                escaped = "\\f"
            elif character == "\n":
                escaped = "\\n"
            elif character == "\r":
                escaped = "\\r"
            elif character == "\t":
                escaped = "\\t"
            elif ord(character) < 0x20:
                escaped = f"\\u{ord(character):04x}"
            else:
                escaped = character
            fragments.append(escaped)
            if len(fragments) >= 256:
                emit("".join(fragments))
                fragments.clear()
        if fragments:
            emit("".join(fragments))
        emit('"')

    def encode(value, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if (
            nodes > _MAX_APPROVAL_CANONICAL_NODES
            or depth > _MAX_APPROVAL_CANONICAL_DEPTH
        ):
            raise _InvalidApprovalArguments
        if value is None:
            emit("null")
            return
        if type(value) is bool:
            emit("true" if value else "false")
            return
        if type(value) is int:
            emit(json.dumps(value, ensure_ascii=False, allow_nan=False))
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise _InvalidApprovalArguments
            emit(json.dumps(value, ensure_ascii=False, allow_nan=False))
            return
        if type(value) is str:
            emit_string(value)
            return
        if type(value) is dict:
            if len(value) > _MAX_APPROVAL_CANONICAL_NODES - nodes:
                raise _InvalidApprovalArguments
            identity = id(value)
            if identity in active or any(type(key) is not str for key in value):
                raise _InvalidApprovalArguments
            active.add(identity)
            try:
                emit("{")
                for index, key in enumerate(sorted(value)):
                    if index:
                        emit(",")
                    nodes += 1
                    if nodes > _MAX_APPROVAL_CANONICAL_NODES:
                        raise _InvalidApprovalArguments
                    emit_string(key)
                    emit(":")
                    encode(value[key], depth + 1)
                emit("}")
            finally:
                active.remove(identity)
            return
        if type(value) is list:
            if len(value) > _MAX_APPROVAL_CANONICAL_NODES - nodes:
                raise _InvalidApprovalArguments
            identity = id(value)
            if identity in active:
                raise _InvalidApprovalArguments
            active.add(identity)
            try:
                emit("[")
                for index, nested in enumerate(value):
                    if index:
                        emit(",")
                    encode(nested, depth + 1)
                emit("]")
            finally:
                active.remove(identity)
            return
        raise _InvalidApprovalArguments

    try:
        if type(args) is not dict:
            raise _InvalidApprovalArguments
        encode(args, 0)
        return digest.hexdigest()
    except Exception:
        return None


def _render_approval_argument(value) -> str:
    """Render one approval value without invoking caller-controlled methods."""
    budget = 16

    def bounded(item, depth: int = 0):
        nonlocal budget
        budget -= 1
        if budget < 0 or depth > 4:
            return "<truncated>"
        if item is None or type(item) in {bool, int}:
            return item
        if type(item) is float:
            return item if math.isfinite(item) else "<unsupported>"
        if type(item) is str:
            return (
                item
                if len(item) <= _MAX_APPROVAL_PREVIEW_STRING
                else "<truncated>"
            )
        if type(item) is dict:
            normalized = {}
            for index, (key, nested) in enumerate(item.items()):
                if index >= 8:
                    break
                normalized[
                    key
                    if type(key) is str and len(key) <= _MAX_APPROVAL_PREVIEW_STRING
                    else "<invalid-key>"
                ] = bounded(nested, depth + 1)
            return normalized
        if type(item) is list:
            return [bounded(nested, depth + 1) for nested in item[:8]]
        return "<unsupported>"

    try:
        return json.dumps(bounded(value), ensure_ascii=True, allow_nan=False)[
            :_MAX_APPROVAL_RENDERED_ARGUMENT
        ]
    except Exception:
        return '"<unrepresentable>"'


def _arg(args: dict, name: str) -> str:
    """Render one argument for an approval prompt, safely and bounded."""
    value = args.get(name) if type(args) is dict else None
    return _render_approval_argument(value)


def _arg_or_default(args: dict, name: str, default: object) -> str:
    """Render an argument's effective default in an approval prompt."""
    value = args.get(name, default) if type(args) is dict else default
    return _render_approval_argument(value)


WRITE_APPROVALS = {
    "gitlab_create_branch": lambda a: (
        f"Project: {_arg(a, 'project')}\nTicket: {_arg(a, 'ticket_key')}"
    ),
    "gitlab_create_named_branch": lambda a: (
        f"Project: {_arg(a, 'project')}\nBranch: {_arg(a, 'branch')}\n"
        f"Ref: {_arg(a, 'ref')}"
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
        from hermes_cli.plugins import PluginToolAdmission

        return (
            type(admission) is PluginToolAdmission
            and getattr(admission, "approved", None) is True
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
            return _json(
                application.execute(
                    name,
                    args or {},
                    configuration,
                    cancel_check=_interrupt_authority(),
                )
            )

        return invoke

    def require_write_approval(tool_name: str, args: dict, **kwargs):
        summarise = WRITE_APPROVALS.get(tool_name)
        if summarise is None:
            return None
        argument_digest = _approval_rule_digest(args)
        if argument_digest is None:
            return {
                "action": "block",
                "message": _INVALID_APPROVAL_MESSAGE,
            }
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
                f"{argument_digest}"
            ),
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

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
                or invocation.provider_id != "ericsson-gitlab"
                or invocation.caller_id != "ericsson-connector-cli"
                or invocation.operation not in gitlab_tools.SCHEMAS
            ):
                return permission_error
            name = invocation.operation
            arguments = dict(invocation.arguments)
            mode = invocation.mode
            if {"dry_run", "confirm"}.intersection(arguments):
                return invalid_input
            if name in _WRITE_TOOLS:
                properties = gitlab_tools.SCHEMAS[name]["parameters"]["properties"]
                if mode is PluginApplicationCommandMode.DRY_RUN:
                    if "dry_run" not in properties:
                        return invalid_input
                    arguments["dry_run"] = True
                elif mode is PluginApplicationCommandMode.CONFIRM:
                    if "confirm" in properties:
                        arguments["confirm"] = True
                    elif "dry_run" in properties:
                        arguments["dry_run"] = False
                    else:
                        return invalid_input
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

    ctx.register_application_commands(
        operations={
            name: "write" if name in _WRITE_TOOLS else "read"
            for name in gitlab_tools.SCHEMAS
        },
        allowed_callers={"ericsson-connector-cli"},
        handler=local_command_handler,
    )

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
