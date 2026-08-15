"""Front-end-owned one-shot controls for explicit tool choice."""

from __future__ import annotations

from dataclasses import dataclass, field
import shlex
import threading

from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext


@dataclass
class OneShotToolChoice:
    pending: ToolChoicePolicy | None = None
    otto_v1: bool = False
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def set_required(self, *, otto_v1: bool = False) -> None:
        self.set(ToolChoicePolicy(mode="required"), otto_v1=otto_v1)

    def set_named(self, name: str, *, otto_v1: bool = False) -> None:
        self.set(ToolChoicePolicy(mode="named", name=name), otto_v1=otto_v1)

    def set_none(self, *, otto_v1: bool = False) -> None:
        self.set(ToolChoicePolicy(mode="none"), otto_v1=otto_v1)

    def set(self, policy: ToolChoicePolicy, *, otto_v1: bool = False) -> None:
        with self._lock:
            self.pending = policy
            self.otto_v1 = bool(otto_v1)

    def clear(self) -> None:
        with self._lock:
            self.pending = None
            self.otto_v1 = False

    def consume_context(
        self,
        *,
        operation_id: str | None = None,
    ) -> ToolOperationContext | None:
        with self._lock:
            policy = self.pending
            otto_v1 = self.otto_v1
            self.pending = None
            self.otto_v1 = False
        if policy is None:
            return None
        return ToolOperationContext.create(
            policy,
            operation_id=operation_id,
            call_role="primary",
            otto_contract_version="v1" if otto_v1 else None,
        )

    def describe(self) -> str:
        with self._lock:
            policy = self.pending
            otto_v1 = self.otto_v1
        if policy is None:
            return "Tool choice is automatic for the next turn."
        selection = (
            f"named({policy.name})" if policy.mode == "named" else policy.mode
        )
        contract = " with OTTO v1" if otto_v1 else ""
        return f"Next turn tool choice: {selection}{contract}."


def configure_tool_choice(control: OneShotToolChoice, arguments: str) -> str:
    """Apply `/tool-choice` arguments to a front-end session holder."""

    try:
        tokens = shlex.split(arguments or "")
    except ValueError as exc:
        raise ValueError(f"Invalid /tool-choice arguments: {exc}") from exc
    if not tokens:
        return control.describe()

    otto_v1 = "--otto-v1" in tokens
    tokens = [token for token in tokens if token != "--otto-v1"]
    if not tokens:
        raise ValueError("Usage: /tool-choice <auto|required|named|none|off>")

    mode = tokens[0].lower()
    if mode in {"auto", "off"}:
        if len(tokens) != 1:
            raise ValueError(f"/{mode} does not accept extra arguments")
        control.clear()
        return (
            "Tool choice disabled; the next turn is automatic."
            if mode == "off"
            else "Tool choice is automatic for the next turn."
        )
    if mode == "required":
        if len(tokens) != 1:
            raise ValueError("Usage: /tool-choice required [--otto-v1]")
        control.set_required(otto_v1=otto_v1)
    elif mode == "none":
        if len(tokens) != 1:
            raise ValueError("Usage: /tool-choice none [--otto-v1]")
        control.set_none(otto_v1=otto_v1)
    elif mode == "named":
        if len(tokens) != 2 or not tokens[1].strip():
            raise ValueError("Usage: /tool-choice named <tool> [--otto-v1]")
        control.set_named(tokens[1], otto_v1=otto_v1)
    else:
        raise ValueError("Tool choice must be auto, required, named, none, or off.")
    return control.describe()
