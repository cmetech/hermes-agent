"""Stable machine-facing workflow CLI envelopes and exit categories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from plugins.workflow.sanitize import sanitize_projection


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
_CONFIRMATION_CAPABILITY_COMMANDS = frozenset(
    {
        "workflow cleanup",
        "workflow showcase cleanup",
        "workflow showcase preflight",
        "workflow showcase run",
    }
)


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


class WorkflowNotFound(WorkflowCommandError):
    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            "not_found", message, exit_code=EXIT_NOT_FOUND, details=details
        )


class WorkflowAuthorization(WorkflowCommandError):
    def __init__(self, message: str, *, code: str = "authorization_required") -> None:
        super().__init__(code, message, exit_code=EXIT_AUTHORIZATION)


class WorkflowConflict(WorkflowCommandError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "version_conflict",
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code=EXIT_CONFLICT,
            retryable=True,
            details=details,
        )


class CoordinatorUnavailable(WorkflowCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "coordinator_unavailable",
            message,
            exit_code=EXIT_COORDINATOR_UNAVAILABLE,
            retryable=True,
        )


class WorkflowActionFailed(WorkflowCommandError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "action_failed",
        details: Mapping[str, object] | None = None,
        result: object = None,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code=EXIT_ACTION_FAILED,
            details=details,
            result=result,
        )


def projection_was_truncated(value: object, *, depth: int = 0) -> bool:
    """Report whether the shared machine sanitizer will bound this value."""
    if depth > 12:
        return True
    if isinstance(value, Mapping):
        if len(value) > 200:
            return True
        return any(
            projection_was_truncated(item, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, list | tuple):
        if len(value) > 200:
            return True
        return any(
            projection_was_truncated(item, depth=depth + 1) for item in value
        )
    return isinstance(value, str) and len(value) > 16_384


def _sanitize_success_result(command: str, result: object) -> object:
    sanitized = sanitize_projection(result)
    if (
        command == "workflow show"
        and isinstance(result, Mapping)
        and isinstance(sanitized, dict)
        and isinstance(result.get("definition"), Mapping)
    ):
        # show_package already emits a semantically redacted, byte-bounded,
        # complete definition; generic projection list limits must not clip it.
        sanitized["definition"] = dict(result["definition"])
    if (
        command in _CONFIRMATION_CAPABILITY_COMMANDS
        and isinstance(result, Mapping)
        and isinstance(sanitized, dict)
        and isinstance(result.get("confirmation_token"), str)
    ):
        # This single-use, authority-bound capability is the intentional output
        # of cleanup preview and must round-trip into cleanup execution.
        sanitized["confirmation_token"] = sanitize_projection(
            result["confirmation_token"]
        )
    if isinstance(result, Mapping) and isinstance(sanitized, dict):
        raw_contract = result.get("command_contract")
        if (
            command in _CONFIRMATION_CAPABILITY_COMMANDS
            and isinstance(raw_contract, Mapping)
            and raw_contract == operator_command_contract()
        ):
            # This is a fixed server-authored argv vocabulary, not workflow
            # command content. Restore only the exact generated contract so an
            # arbitrary caller-supplied command mapping remains redacted.
            sanitized["command_contract"] = operator_command_contract()
    return sanitized


def success_envelope(command: str, result: object) -> dict[str, object]:
    sanitized_result = _sanitize_success_result(command, result)
    next_actions = []
    warnings = []
    if isinstance(sanitized_result, Mapping):
        raw_actions = sanitized_result.get("next_actions", [])
        raw_warnings = sanitized_result.get("warnings", [])
        if isinstance(raw_actions, list | tuple):
            next_actions = list(raw_actions)
        if isinstance(raw_warnings, list | tuple):
            warnings = list(raw_warnings)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "result": sanitized_result,
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
        "result": sanitize_projection(result),
        "error": sanitize_projection(error.to_dict()),
        "warnings": [],
        "next_actions": [],
    }


def operator_command_contract() -> dict[str, object]:
    """Return exact argv suffixes for workflow-operating skills and agents."""
    return {
        "schema_version": SCHEMA_VERSION,
        "argv_prefix": ["workflow"],
        "exit_codes": {
            "success": EXIT_SUCCESS,
            "invocation": EXIT_INVOCATION,
            "not_found": EXIT_NOT_FOUND,
            "authorization": EXIT_AUTHORIZATION,
            "conflict": EXIT_CONFLICT,
            "coordinator_unavailable": EXIT_COORDINATOR_UNAVAILABLE,
            "blocking_finding": EXIT_BLOCKING_FINDING,
            "action_failed": EXIT_ACTION_FAILED,
            "internal": EXIT_INTERNAL,
        },
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
                "--trigger-source",
                "{trigger_source}",
                "--no-wait",
                "--json",
            ],
            "run_foreground": [
                "run",
                "{workflow_name}",
                "--idempotency-key",
                "{intent_key}",
                "--trigger-source",
                "{trigger_source}",
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
            "archive": [
                "archive",
                "{run_id}",
                "--expected-version",
                "{state_version}",
                "--json",
            ],
            "restore": [
                "restore",
                "{run_id}",
                "--expected-version",
                "{state_version}",
                "--json",
            ],
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
    "CoordinatorUnavailable",
    "SCHEMA_VERSION",
    "WorkflowCommandError",
    "WorkflowActionFailed",
    "WorkflowAuthorization",
    "WorkflowConflict",
    "WorkflowNotFound",
    "error_envelope",
    "operator_command_contract",
    "projection_was_truncated",
    "success_envelope",
]
