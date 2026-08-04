"""Immutable Phase 3 requested-to-effective execution semantics."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from plugins.workflow.models import (
    RunExecutionLimits,
    WorkflowLanguageProfile,
    WorkflowPackage,
    freeze_value,
)


EXECUTION_SEMANTICS_MISMATCH_CODE = "workflow_execution_semantics_mismatch"
_LIMIT_FIELDS = frozenset({
    "ai_idle_timeout_seconds",
    "ai_wall_timeout_seconds",
    "provider_request_timeout_seconds",
    "subprocess_timeout_seconds",
    "combined_total_attempts",
})
_NODE_FIELDS = frozenset({
    "requested_attempt_wall_timeout_seconds",
    "attempt_wall_timeout_seconds",
    "requested_idle_timeout_seconds",
    "idle_timeout_seconds",
    "provider_request_timeout_seconds",
    "timeout_source",
    "timeout_capped",
    "retry",
})
_RETRY_FIELDS = frozenset({
    "explicit",
    "requested_retries",
    "requested_total_attempts",
    "effective_total_attempts",
    "delay_ms",
    "on_error",
    "capped",
})


class WorkflowExecutionSemanticsError(ValueError):
    """A sealed effective-execution projection is malformed or inconsistent."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = EXECUTION_SEMANTICS_MISMATCH_CODE


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Phase3ExecutionSemantics:
    limits: Mapping[str, object]
    nodes: Mapping[str, Mapping[str, object]]
    schema_version: int = 1
    normalizer_version: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", freeze_value(self.limits))
        object.__setattr__(
            self,
            "nodes",
            MappingProxyType({
                node_id: freeze_value(value)
                for node_id, value in sorted(self.nodes.items())
            }),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "normalizer_version": self.normalizer_version,
            "limits": _thaw(self.limits),
            "nodes": _thaw(self.nodes),
        }

    def to_run_execution_limits(
        self, *, base: RunExecutionLimits | None = None
    ) -> RunExecutionLimits:
        """Apply only the five v3-sealed fields to otherwise sealed run limits."""
        current = base or RunExecutionLimits()
        values = {
            name: getattr(current, name)
            for name in current.__dataclass_fields__
        }
        values.update({
            "ai_idle_timeout_seconds": self.limits["ai_idle_timeout_seconds"],
            "ai_wall_timeout_seconds": self.limits["ai_wall_timeout_seconds"],
            "provider_request_timeout_seconds": self.limits[
                "provider_request_timeout_seconds"
            ],
            "subprocess_timeout_seconds": self.limits[
                "subprocess_timeout_seconds"
            ],
            "combined_retries": self.limits["combined_total_attempts"],
        })
        return RunExecutionLimits(**values)


def _positive_finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise WorkflowExecutionSemanticsError(
            f"{field} must be a positive finite number"
        )
    return float(value)


def _canonical_seconds(value: object, field: str) -> float:
    if not isinstance(value, float):
        raise WorkflowExecutionSemanticsError(
            f"{field} must use the canonical finite-number representation"
        )
    return _positive_finite(value, field)


def _optional_positive_finite(value: object, field: str) -> None:
    if value is not None:
        _canonical_seconds(value, field)


def _bounded_integer(value: object, field: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise WorkflowExecutionSemanticsError(
            f"{field} must be an integer from {minimum} through {maximum}"
        )


def _validate_node_projection(raw_node: Mapping[str, object]) -> None:
    for field in (
        "requested_attempt_wall_timeout_seconds",
        "attempt_wall_timeout_seconds",
        "requested_idle_timeout_seconds",
        "idle_timeout_seconds",
        "provider_request_timeout_seconds",
    ):
        _optional_positive_finite(raw_node[field], field)
    if raw_node["timeout_source"] not in {
        "authored",
        "archon_default",
        "profile_ceiling",
    }:
        raise WorkflowExecutionSemanticsError("timeout_source is unsupported")
    if not isinstance(raw_node["timeout_capped"], bool):
        raise WorkflowExecutionSemanticsError("timeout_capped must be a boolean")

    raw_retry = raw_node["retry"]
    if not isinstance(raw_retry, Mapping) or set(raw_retry) != _RETRY_FIELDS:
        raise WorkflowExecutionSemanticsError(
            "execution retry must contain the exact supported fields"
        )
    if not isinstance(raw_retry["explicit"], bool) or not isinstance(
        raw_retry["capped"], bool
    ):
        raise WorkflowExecutionSemanticsError(
            "execution retry flags must be booleans"
        )
    _bounded_integer(raw_retry["requested_retries"], "requested_retries", 0, 5)
    _bounded_integer(
        raw_retry["requested_total_attempts"],
        "requested_total_attempts",
        1,
        6,
    )
    _bounded_integer(
        raw_retry["effective_total_attempts"],
        "effective_total_attempts",
        1,
        5,
    )
    if (
        isinstance(raw_retry["delay_ms"], bool)
        or not isinstance(raw_retry["delay_ms"], int)
        or raw_retry["delay_ms"] < 0
    ):
        raise WorkflowExecutionSemanticsError(
            "execution retry delay_ms must be a non-negative integer"
        )
    if raw_retry["on_error"] not in {"transient", "all"}:
        raise WorkflowExecutionSemanticsError(
            "execution retry on_error is unsupported"
        )


def _validated_limits(limits: RunExecutionLimits) -> dict[str, object]:
    combined = limits.combined_retries
    if isinstance(combined, bool) or not isinstance(combined, int) or not 1 <= combined <= 5:
        raise WorkflowExecutionSemanticsError(
            "combined_total_attempts must be an integer from 1 through 5"
        )
    resolved = {
        "ai_idle_timeout_seconds": _positive_finite(
            limits.ai_idle_timeout_seconds, "ai_idle_timeout_seconds"
        ),
        "ai_wall_timeout_seconds": _positive_finite(
            limits.ai_wall_timeout_seconds, "ai_wall_timeout_seconds"
        ),
        "provider_request_timeout_seconds": _positive_finite(
            limits.provider_request_timeout_seconds,
            "provider_request_timeout_seconds",
        ),
        "subprocess_timeout_seconds": _positive_finite(
            limits.subprocess_timeout_seconds, "subprocess_timeout_seconds"
        ),
        "combined_total_attempts": combined,
    }
    if resolved["ai_idle_timeout_seconds"] > resolved["ai_wall_timeout_seconds"]:
        raise WorkflowExecutionSemanticsError(
            "AI idle timeout cannot exceed AI wall timeout"
        )
    if (
        resolved["provider_request_timeout_seconds"]
        > resolved["ai_wall_timeout_seconds"]
    ):
        raise WorkflowExecutionSemanticsError(
            "provider timeout cannot exceed AI wall timeout"
        )
    return resolved


def _retry_projection(
    requested: Mapping[str, object] | None,
    *,
    combined_total_attempts: int,
) -> dict[str, object]:
    requested = requested or MappingProxyType({
        "explicit": False,
        "requested_retries": 0,
        "requested_total_attempts": 1,
        "delay_ms": 3000,
        "on_error": "transient",
    })
    requested_retries = requested["requested_retries"]
    requested_total = requested["requested_total_attempts"]
    effective_total = min(int(requested_total), combined_total_attempts)
    return {
        "explicit": requested["explicit"],
        "requested_retries": requested_retries,
        "requested_total_attempts": requested_total,
        "effective_total_attempts": effective_total,
        "delay_ms": requested["delay_ms"],
        "on_error": requested["on_error"],
        "capped": effective_total < requested_total,
    }


def build_phase3_execution_semantics(
    package: WorkflowPackage,
    limits: RunExecutionLimits,
) -> Phase3ExecutionSemantics:
    """Intersect normalized v3 requests with one resolved admission authority."""
    if (
        package.language.effective_profile
        is not WorkflowLanguageProfile.ARCHON_2026_07
        or package.language.normalizer_version != 3
    ):
        raise WorkflowExecutionSemanticsError(
            "Phase 3 execution semantics require an Archon v3 package"
        )
    effective_limits = _validated_limits(limits)
    node_semantics = package.language.node_semantics
    nodes: dict[str, Mapping[str, object]] = {}
    for node in package.definition.nodes:
        requested = node_semantics.get(node.id, MappingProxyType({}))
        if node.node_type in {"bash", "script"}:
            requested_wall = float(requested["wall_timeout_seconds"])
            attempt_wall = min(
                requested_wall,
                float(effective_limits["subprocess_timeout_seconds"]),
            )
            requested_idle = None
            idle = None
            provider = None
            timeout_source = (
                "authored" if "timeout" in node.options else "archon_default"
            )
            timeout_capped = attempt_wall < requested_wall
        else:
            requested_wall = float(effective_limits["ai_wall_timeout_seconds"])
            attempt_wall = requested_wall
            requested_idle = (
                float(requested["idle_timeout_seconds"])
                if "idle_timeout_seconds" in requested
                else None
            )
            if node.node_type in {"command", "prompt"}:
                idle = min(
                    requested_idle
                    if requested_idle is not None
                    else float(effective_limits["ai_idle_timeout_seconds"]),
                    float(effective_limits["ai_idle_timeout_seconds"]),
                    attempt_wall,
                )
                provider = min(
                    float(effective_limits["provider_request_timeout_seconds"]),
                    attempt_wall,
                )
            else:
                idle = None
                provider = None
            timeout_source = "profile_ceiling"
            timeout_capped = (
                requested_idle is not None and idle is not None and idle < requested_idle
            )
        retry = requested.get("retry")
        nodes[node.id] = {
            "requested_attempt_wall_timeout_seconds": requested_wall,
            "attempt_wall_timeout_seconds": attempt_wall,
            "requested_idle_timeout_seconds": requested_idle,
            "idle_timeout_seconds": idle,
            "provider_request_timeout_seconds": provider,
            "timeout_source": timeout_source,
            "timeout_capped": timeout_capped,
            "retry": _retry_projection(
                retry if isinstance(retry, Mapping) else None,
                combined_total_attempts=int(
                    effective_limits["combined_total_attempts"]
                ),
            ),
        }
    return Phase3ExecutionSemantics(limits=effective_limits, nodes=nodes)


def read_phase3_execution_semantics(
    value: object,
    *,
    package: WorkflowPackage,
) -> Phase3ExecutionSemantics:
    """Parse and verify an untrusted sealed v3 execution projection."""
    try:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "normalizer_version",
            "limits",
            "nodes",
        }:
            raise WorkflowExecutionSemanticsError(
                "execution semantics must contain the exact supported fields"
            )
        if (
            isinstance(value["schema_version"], bool)
            or not isinstance(value["schema_version"], int)
            or value["schema_version"] != 1
            or isinstance(value["normalizer_version"], bool)
            or not isinstance(value["normalizer_version"], int)
            or value["normalizer_version"] != 3
        ):
            raise WorkflowExecutionSemanticsError(
                "execution semantics version is unsupported"
            )
        raw_limits = value["limits"]
        raw_nodes = value["nodes"]
        if not isinstance(raw_limits, Mapping) or set(raw_limits) != _LIMIT_FIELDS:
            raise WorkflowExecutionSemanticsError(
                "execution limits must contain the exact supported fields"
            )
        if not isinstance(raw_nodes, Mapping):
            raise WorkflowExecutionSemanticsError("execution nodes must be a mapping")
        combined = raw_limits["combined_total_attempts"]
        if (
            isinstance(combined, bool)
            or not isinstance(combined, int)
            or not 1 <= combined <= 5
        ):
            raise WorkflowExecutionSemanticsError(
                "combined_total_attempts must be an integer from 1 through 5"
            )
        parsed_limits = RunExecutionLimits(
            ai_idle_timeout_seconds=_canonical_seconds(
                raw_limits["ai_idle_timeout_seconds"], "ai_idle_timeout_seconds"
            ),
            ai_wall_timeout_seconds=_canonical_seconds(
                raw_limits["ai_wall_timeout_seconds"], "ai_wall_timeout_seconds"
            ),
            provider_request_timeout_seconds=_canonical_seconds(
                raw_limits["provider_request_timeout_seconds"],
                "provider_request_timeout_seconds",
            ),
            subprocess_timeout_seconds=_canonical_seconds(
                raw_limits["subprocess_timeout_seconds"],
                "subprocess_timeout_seconds",
            ),
            combined_retries=combined,
        )
        for node_id, raw_node in raw_nodes.items():
            if not isinstance(node_id, str) or not isinstance(raw_node, Mapping):
                raise WorkflowExecutionSemanticsError(
                    "execution node entries must be mappings"
                )
            if set(raw_node) != _NODE_FIELDS:
                raise WorkflowExecutionSemanticsError(
                    "execution node must contain the exact supported fields"
                )
            _validate_node_projection(raw_node)
        expected = build_phase3_execution_semantics(package, parsed_limits)
        if expected.to_dict() != _thaw(value):
            raise WorkflowExecutionSemanticsError(
                "sealed execution semantics do not match normalized requests"
            )
        return expected
    except WorkflowExecutionSemanticsError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise WorkflowExecutionSemanticsError(
            "sealed execution semantics are malformed"
        ) from exc
