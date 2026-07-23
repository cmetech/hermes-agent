"""Server-derived workflow input declarations and byte bounds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from plugins.workflow.models import WorkflowPackage
from plugins.workflow.sanitize import workflow_input_names_are_portable


API_INPUT_VALUE_MAX_BYTES = 64 * 1024
API_INPUT_AGGREGATE_MAX_BYTES = 256 * 1024
_API_VALUE_KINDS = frozenset({"text", "string", "number", "boolean", "enum"})


class WorkflowInputContractError(ValueError):
    """A sidecar input declaration cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class WorkflowInputDeclaration:
    name: str
    kind: str
    required: bool
    max_bytes: int | None

    @property
    def accepts_api_value(self) -> bool:
        return self.kind in _API_VALUE_KINDS

    def byte_bound(self, *, channel: str, store_limit: int) -> int:
        declared = self.max_bytes if self.max_bytes is not None else store_limit
        bound = min(store_limit, declared)
        if channel == "text":
            bound = min(bound, API_INPUT_VALUE_MAX_BYTES)
        return bound


def workflow_input_declarations(
    package: WorkflowPackage,
) -> dict[str, WorkflowInputDeclaration]:
    """Return validated declarations; an empty mapping selects legacy-flat mode."""
    delivery = package.sidecar.get("delivery_defaults")
    if delivery is None:
        return {}
    if not isinstance(delivery, Mapping):
        raise WorkflowInputContractError("delivery_defaults must be a mapping")
    raw_inputs = delivery.get("inputs")
    if raw_inputs is None or raw_inputs == {}:
        return {}
    if not isinstance(raw_inputs, Mapping) or len(raw_inputs) > 64:
        raise WorkflowInputContractError("input declarations must be a bounded mapping")
    if not workflow_input_names_are_portable(raw_inputs):
        raise WorkflowInputContractError("input declaration names must be portable")

    declarations: dict[str, WorkflowInputDeclaration] = {}
    for name, raw in raw_inputs.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise WorkflowInputContractError(
                "input declarations must be named mappings"
            )
        kind = raw.get("type", raw.get("kind", "text"))
        if "type" in raw and "kind" in raw and raw.get("type") != raw.get("kind"):
            raise WorkflowInputContractError("input type and kind must agree")
        required = raw.get("required", True)
        max_bytes = raw.get("max_bytes")
        if not isinstance(kind, str) or not kind or len(kind) > 64:
            raise WorkflowInputContractError("input kind must be bounded text")
        if not isinstance(required, bool):
            raise WorkflowInputContractError("input required flag must be boolean")
        if max_bytes is not None and (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise WorkflowInputContractError("input max_bytes must be positive")
        declarations[name] = WorkflowInputDeclaration(
            name=name,
            kind=kind,
            required=required,
            max_bytes=max_bytes,
        )
    return declarations


__all__ = [
    "API_INPUT_AGGREGATE_MAX_BYTES",
    "API_INPUT_VALUE_MAX_BYTES",
    "WorkflowInputContractError",
    "WorkflowInputDeclaration",
    "workflow_input_declarations",
]
