"""Stable machine-facing workflow CLI envelopes and exit categories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


SCHEMA_VERSION = 1
EXIT_SUCCESS = 0
EXIT_INVOCATION = 2
EXIT_NOT_FOUND = 3
EXIT_AUTHORIZATION = 4
EXIT_CONFLICT = 5
EXIT_COORDINATOR_UNAVAILABLE = 6
EXIT_BLOCKING_FINDING = 7
EXIT_ACTION_FAILED = 8
EXIT_INTERNAL = 70


@dataclass(frozen=True)
class MachineError:
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details or {}),
        }


class WorkflowCommandError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
        result: object = None,
    ) -> None:
        super().__init__(message)
        self.error = MachineError(code, message, retryable, details)
        self.exit_code = exit_code
        self.result = result


def success_envelope(command: str, result: object) -> dict[str, object]:
    next_actions = []
    warnings = []
    if isinstance(result, Mapping):
        raw_actions = result.get("next_actions", [])
        raw_warnings = result.get("warnings", [])
        if isinstance(raw_actions, list | tuple):
            next_actions = list(raw_actions)
        if isinstance(raw_warnings, list | tuple):
            warnings = list(raw_warnings)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "result": result,
        "error": None,
        "warnings": warnings,
        "next_actions": next_actions,
    }


def error_envelope(
    command: str,
    error: MachineError,
    *,
    result: object = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "result": result,
        "error": error.to_dict(),
        "warnings": [],
        "next_actions": [],
    }


def operator_command_contract() -> dict[str, object]:
    """Return exact argv suffixes for workflow-operating skills and agents."""
    return {
        "schema_version": SCHEMA_VERSION,
        "argv_prefix": ["workflow"],
        "identifier_kinds": {
            "workflow_name": "catalog workflow name",
            "showcase_id": "showcase catalog ID",
            "run_id": "durable run ID returned by run",
            "interaction_id": "pending interaction ID returned by status",
        },
        "commands": {
            "run_background": [
                "run",
                "{workflow_name}",
                "--idempotency-key",
                "{intent_key}",
                "--no-wait",
                "--json",
            ],
            "run_foreground": [
                "run",
                "{workflow_name}",
                "--idempotency-key",
                "{intent_key}",
                "--foreground",
                "--json",
            ],
            "status": ["status", "{run_id}", "--json"],
            "events": ["events", "{run_id}", "--tail", "50", "--json"],
            "approve": [
                "approve",
                "{run_id}",
                "--interaction-id",
                "{interaction_id}",
                "--expected-version",
                "{state_version}",
                "--continue",
                "--json",
            ],
            "reject": [
                "reject",
                "{run_id}",
                "--interaction-id",
                "{interaction_id}",
                "--expected-version",
                "{state_version}",
                "--continue",
                "--json",
            ],
            "provide_input": [
                "provide-input",
                "{run_id}",
                "{interaction_id}",
                "{value}",
                "--expected-version",
                "{state_version}",
                "--continue",
                "--json",
            ],
            "retry": [
                "retry",
                "{run_id}",
                "{node_id}",
                "--expected-version",
                "{state_version}",
                "--continue",
                "--json",
            ],
            "reconcile": [
                "reconcile",
                "{run_id}",
                "{reconciliation_outcome}",
                "--interaction-id",
                "{interaction_id}",
                "--expected-version",
                "{state_version}",
                "--continue",
                "--json",
            ],
            "resume": ["resume", "{run_id}", "--json"],
            "cancel": ["cancel", "{run_id}", "--json"],
            "abandon": ["abandon", "{run_id}", "--json"],
            "cleanup_impact": [
                "cleanup",
                "--older-than",
                "{retention_age}",
                "--json",
            ],
            "cleanup_execute": [
                "cleanup",
                "--older-than",
                "{retention_age}",
                "--execute",
                "--confirmation-token",
                "{confirmation_token}",
                "--json",
            ],
        },
    }


__all__ = [
    "EXIT_ACTION_FAILED",
    "EXIT_AUTHORIZATION",
    "EXIT_BLOCKING_FINDING",
    "EXIT_CONFLICT",
    "EXIT_COORDINATOR_UNAVAILABLE",
    "EXIT_INTERNAL",
    "EXIT_INVOCATION",
    "EXIT_NOT_FOUND",
    "EXIT_SUCCESS",
    "MachineError",
    "SCHEMA_VERSION",
    "WorkflowCommandError",
    "error_envelope",
    "operator_command_contract",
    "success_envelope",
]
