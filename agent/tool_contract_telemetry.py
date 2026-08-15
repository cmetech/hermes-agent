"""Bounded, content-free diagnostics for request-scoped tool policy."""

from __future__ import annotations

import hashlib
from typing import Any

from agent.error_classifier import TOOL_CONTRACT_ERROR_MESSAGES
from agent.tool_choice_policy import ToolOperationContext


_TRANSPORTS = frozenset(
    {
        "chat_completions",
        "anthropic_messages",
        "codex_responses",
        "gemini",
        "gemini_native",
        "bedrock_converse",
    }
)
_MODEL_SELECTIONS = frozenset({"explicit", "auto"})
_RETRY_DECISIONS = frozenset({"none", "reuse_attempt", "terminal"})
_FALLBACK_DECISIONS = frozenset({"not_considered", "allowed", "blocked"})


def _correlation(value: Any) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def build_tool_contract_event(
    context: ToolOperationContext,
    *,
    requested_model: str | None,
    model_selection: str,
    transport: str | None,
    echo: bool,
    structured_call: bool,
    terminal_code: str | None = None,
    retry_decision: str = "none",
    fallback_decision: str = "not_considered",
) -> dict[str, Any]:
    """Build the only diagnostic shape permitted for this contract."""
    return {
        "event": "tool_contract",
        "operation": _correlation(context.operation_id),
        "call_role": context.call_role,
        "requested_model": _correlation(requested_model),
        "model_selection": (
            model_selection if model_selection in _MODEL_SELECTIONS else "auto"
        ),
        "policy": context.policy.mode,
        "contract": context.otto_contract_version,
        "transport": transport if transport in _TRANSPORTS else "other",
        "echo": bool(echo),
        "structured_call": bool(structured_call),
        "post_tool": context.call_role == "post_tool",
        "terminal_code": (
            terminal_code
            if terminal_code in TOOL_CONTRACT_ERROR_MESSAGES
            else None
        ),
        "retry_decision": (
            retry_decision if retry_decision in _RETRY_DECISIONS else "none"
        ),
        "fallback_decision": (
            fallback_decision
            if fallback_decision in _FALLBACK_DECISIONS
            else "not_considered"
        ),
    }


def emit_tool_contract_event(context: ToolOperationContext | None, **fields: Any) -> None:
    """Emit without ever affecting the model request path."""
    if context is None:
        return
    try:
        from agent.monitoring.emitter import emit

        emit(build_tool_contract_event(context, **fields))
    except Exception:
        return


__all__ = ["build_tool_contract_event", "emit_tool_contract_event"]
