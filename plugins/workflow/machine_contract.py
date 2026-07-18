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
    "success_envelope",
]
