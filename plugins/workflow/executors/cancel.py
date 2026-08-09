"""Typed durable cancellation for portable ``cancel`` nodes."""

from __future__ import annotations

from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult


class CancelExecutor:
    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        return NodeExecutionResult(
            "cancelled",
            error_code="cancel_node",
            error_message=str(context.node.value),
            metadata={
                "cancel_reason": str(context.node.value),
                "provider_attempts": 0,
                "provider_attempts_exact": True,
                "known_no_effect": True,
            },
        )


__all__ = ["CancelExecutor"]
