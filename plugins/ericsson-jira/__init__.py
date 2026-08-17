"""Ericsson Jira standalone connector registration."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import tools as jira_tools
from .models import JiraError, SAFE_ERROR_MESSAGES, safe_remediation


_WRITE_TOOLS = frozenset(
    {
        "jira_add_comment",
        "jira_transition_issue",
        "jira_assign_issue",
        "jira_update_fields",
        "jira_manage_labels",
        "jira_create_issue",
        "jira_link_issues",
    }
)
_INVALID_APPROVAL_MESSAGE = "Jira write arguments cannot be safely approved"
# A valid 32,000-character comment can contain control characters, each of
# which encodes as six JSON bytes.  256 KiB covers that 192,000-byte worst
# case plus the bounded argument envelope while remaining a firm hook limit.
_MAX_APPROVAL_CANONICAL_BYTES = 262_144
_MAX_APPROVAL_CANONICAL_DEPTH = 64
_MAX_APPROVAL_CANONICAL_NODES = 12_000
_MAX_APPROVAL_RENDERED_ARGUMENT = 256
_MAX_APPROVAL_PREVIEW_STRING = 128


class _InvalidApprovalArguments(Exception):
    """Internal marker for values that cannot safely bind an approval."""


def _approval_rule_digest(args) -> str | None:
    """Hash exact JSON arguments under a running byte budget.

    This hook runs before operation validation. Exact ``dict``/``list`` JSON
    containers are the only shapes that a write may subsequently execute.
    Invalid, cyclic, unencodable, or over-budget arguments return ``None`` so
    they cannot create an approval or reusable rule. Valid values are emitted
    in sorted-key JSON form directly into the digest, so the full canonical
    object is never built.
    """

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


def _arg(args: dict, name: str) -> str:
    """Render one argument for an approval prompt, safely and bounded."""
    value = args.get(name) if type(args) is dict else None
    budget = 8

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


WRITE_APPROVALS = {
    "jira_add_comment": lambda a: (
        f"Issue: {_arg(a, 'key')}\nBody: {_arg(a, 'body')}"
    ),
    "jira_transition_issue": lambda a: (
        f"Issue: {_arg(a, 'key')}\nTransition: {_arg(a, 'transition_id')}"
    ),
    "jira_assign_issue": lambda a: (
        f"Issue: {_arg(a, 'key')}\nAssignee: {_arg(a, 'assignee')}"
    ),
    "jira_update_fields": lambda a: (
        f"Issue: {_arg(a, 'key')}\nFields: {_arg(a, 'fields')}"
    ),
    "jira_manage_labels": lambda a: (
        f"Issue: {_arg(a, 'key')}\n"
        f"{_arg(a, 'operation')} labels: {_arg(a, 'labels')}"
    ),
    "jira_create_issue": lambda a: (
        f"Project: {_arg(a, 'project')}\n"
        f"Type: {_arg(a, 'issue_type')}\n"
        f"Summary: {_arg(a, 'summary')}"
    ),
    "jira_link_issues": lambda a: (
        f"Link: {_arg(a, 'inward')} -> {_arg(a, 'outward')}\n"
        f"Type: {_arg(a, 'link_type')}"
    ),
}


_PLUGIN_SKILLS = (
    ("ticket-research", "Research one bounded Jira ticket."),
    ("defect-triage", "Triage one Jira defect and prepare an approved comment."),
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
    """Register stable Jira tools using fresh opaque profile configuration."""

    def available() -> bool:
        try:
            return jira_tools.check_available(ctx.configuration())
        except Exception:
            return False

    def handler(name):
        def invoke(args: dict, **_kwargs) -> str:
            if name in _WRITE_TOOLS and _approval_rule_digest(args) is None:
                return _json(
                    {
                        "success": False,
                        "error": {
                            "category": "invalid_input",
                            "message": SAFE_ERROR_MESSAGES["invalid_input"],
                        },
                    }
                )
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
            try:
                result = jira_tools.invoke(
                    name,
                    args or {},
                    configuration,
                    cancel_check=_interrupt_authority(),
                )
                return _json({"success": True, "result": result})
            except JiraError as exc:
                error = {
                    "category": exc.category,
                    "message": SAFE_ERROR_MESSAGES[exc.category],
                }
                remediation = safe_remediation(getattr(exc, "remediation", None))
                if remediation:
                    error["remediation"] = remediation
                return _json(
                    {
                        "success": False,
                        "error": error,
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

    def require_write_approval(tool_name: str, args: dict, **_kwargs):
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
                f"Approve Ericsson Jira change: {tool_name}\n"
                f"{summarise(args if isinstance(args, dict) else {})}"
            ),
            "rule_key": (
                f"{tool_name}:"
                f"{argument_digest}"
            ),
        }

    ctx.register_hook("pre_tool_call", require_write_approval)

    for name, schema in jira_tools.SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="ericsson-jira",
            schema=schema,
            handler=handler(name),
            check_fn=available,
            emoji="🎫",
        )

    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is not None:
        skill_root = Path(__file__).parent / "skills"
        for name, description in _PLUGIN_SKILLS:
            register_skill(name, skill_root / name / "SKILL.md", description)
