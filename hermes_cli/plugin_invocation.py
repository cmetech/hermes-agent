"""Authenticated invocation and delivery contracts for generic plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


PluginBoundary = Literal["cli", "tui", "desktop", "gateway", "api"]
InvocationAssurance = Literal["verified_adapter", "local_admin_claim"]
DeliveryStatus = Literal[
    "delivered",
    "retryable_failure",
    "permanent_failure",
    "outcome_uncertain",
    "unauthorized",
]


@dataclass(frozen=True, slots=True)
class PluginInvocationContext:
    """Immutable identity facts established by the invoking host boundary."""

    boundary: PluginBoundary
    principal: str
    operator_scope: str
    assurance: InvocationAssurance
    return_route_capability: str | None

    def __post_init__(self) -> None:
        if self.boundary not in {"cli", "tui", "desktop", "gateway", "api"}:
            raise ValueError("unknown plugin invocation boundary")
        if self.assurance not in {"verified_adapter", "local_admin_claim"}:
            raise ValueError("unknown plugin invocation assurance")
        for name in ("principal", "operator_scope"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
            if len(value.encode("utf-8")) > 4096:
                raise ValueError(f"{name} is too large")
        capability = self.return_route_capability
        if capability is not None and (
            not isinstance(capability, str)
            or not capability
            or len(capability.encode("utf-8")) > 4096
        ):
            raise ValueError("return_route_capability must be bounded text")

    @classmethod
    def local_admin(cls, *, boundary: PluginBoundary) -> "PluginInvocationContext":
        """Create a local-admin context without any verified delivery route."""
        if boundary == "gateway":
            raise ValueError("Gateway authority must come from a verified adapter")
        return cls(
            boundary=boundary,
            principal="local-admin",
            operator_scope="local-admin",
            assurance="local_admin_claim",
            return_route_capability=None,
        )


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Bounded result returned by a host-owned delivery port."""

    status: DeliveryStatus
    transport_id: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {
            "delivered",
            "retryable_failure",
            "permanent_failure",
            "outcome_uncertain",
            "unauthorized",
        }:
            raise ValueError("unknown delivery receipt status")
        for name in ("transport_id", "detail"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or len(value.encode("utf-8")) > 4096
            ):
                raise ValueError(f"{name} must be bounded text")


@runtime_checkable
class PluginDeliveryPort(Protocol):
    """Host-owned bounded delivery capability exposed to plugin services."""

    def deliver(
        self, capability: str, text: str, idempotency_key: str
    ) -> DeliveryReceipt:
        """Resolve an opaque capability and attempt one idempotent delivery."""


__all__ = [
    "DeliveryReceipt",
    "PluginDeliveryPort",
    "PluginInvocationContext",
]
