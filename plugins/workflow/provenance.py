"""Validated, durable workflow trigger provenance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


TriggerSource = Literal[
    "desktop", "chat", "background_agent", "cron", "cli", "api"
]
ProvenanceAssurance = Literal[
    "verified_adapter", "system_schedule", "local_admin_claim", "legacy_unknown"
]

_SOURCES = frozenset({"desktop", "chat", "background_agent", "cron", "cli", "api"})
_ASSURANCES = frozenset(
    {"verified_adapter", "system_schedule", "local_admin_claim", "legacy_unknown"}
)
_ASSURANCE_SOURCES = {
    "verified_adapter": frozenset({"desktop", "chat", "background_agent", "api"}),
    "system_schedule": frozenset({"cron"}),
    "local_admin_claim": frozenset({"cli", "chat", "background_agent", "cron"}),
    "legacy_unknown": _SOURCES,
}


def _bounded(value: str | None, *, name: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name} must be bounded non-empty text")
    return value


@dataclass(frozen=True)
class TriggerProvenance:
    source: TriggerSource
    assurance: ProvenanceAssurance
    intent_key: str
    source_instance: str | None = None
    actor_id: str | None = None
    claimed_actor: str | None = None
    return_route: str | None = None

    def __post_init__(self) -> None:
        if self.source not in _SOURCES:
            raise ValueError("unsupported trigger source")
        if self.assurance not in _ASSURANCES:
            raise ValueError("unsupported provenance assurance")
        if self.source not in _ASSURANCE_SOURCES[self.assurance]:
            raise ValueError(
                f"{self.assurance} cannot be asserted for source {self.source}"
            )
        _bounded(self.intent_key, name="intent_key", required=True)
        _bounded(self.source_instance, name="source_instance")
        _bounded(self.actor_id, name="actor_id")
        _bounded(self.claimed_actor, name="claimed_actor")
        _bounded(self.return_route, name="return_route")
        if self.assurance == "legacy_unknown" and any(
            value is not None
            for value in (
                self.source_instance,
                self.actor_id,
                self.claimed_actor,
                self.return_route,
            )
        ):
            raise ValueError("legacy_unknown cannot carry asserted identity")
        if self.assurance == "local_admin_claim" and self.return_route is not None:
            raise ValueError("local_admin_claim cannot authorize a return route")
        if self.assurance != "local_admin_claim" and self.claimed_actor is not None:
            raise ValueError("claimed_actor is only valid for local_admin_claim")

    @classmethod
    def local_admin_claim(
        cls,
        *,
        source: Literal["cli", "chat", "background_agent", "cron"],
        intent_key: str,
        source_instance: str | None = None,
        claimed_actor: str | None = None,
        actor_id: str | None = None,
        return_route: str | None = None,
    ) -> "TriggerProvenance":
        if actor_id is not None or return_route is not None:
            raise ValueError(
                "local CLI claims are not verified_adapter identity or delivery routes"
            )
        return cls(
            source=source,
            assurance="local_admin_claim",
            intent_key=intent_key,
            source_instance=source_instance,
            claimed_actor=claimed_actor,
        )

    @classmethod
    def legacy(cls, *, source: TriggerSource, intent_key: str) -> "TriggerProvenance":
        return cls(source=source, assurance="legacy_unknown", intent_key=intent_key)

    def durable_record(self, *, admitted_at: str) -> dict[str, str | None]:
        return {
            "source": self.source,
            "assurance": self.assurance,
            "source_instance": self.source_instance,
            "actor_id": self.actor_id,
            "claimed_actor": self.claimed_actor,
            "intent_key_digest": hashlib.sha256(self.intent_key.encode()).hexdigest(),
            "return_route": self.return_route,
            "admitted_at": admitted_at,
        }

    def digest_record(self) -> dict[str, str | None]:
        record = self.durable_record(admitted_at="")
        record.pop("admitted_at")
        return record


def legacy_projection_provenance(projection: dict[str, object]) -> dict[str, str | None]:
    source = str(projection.get("trigger") or "cli")
    return {
        "source": source,
        "assurance": "legacy_unknown",
        "source_instance": None,
        "actor_id": None,
        "claimed_actor": None,
        "intent_key_digest": str(projection.get("idempotency_key_digest") or ""),
        "return_route": None,
        "admitted_at": str(projection.get("created_at") or ""),
    }


__all__ = [
    "ProvenanceAssurance",
    "TriggerProvenance",
    "TriggerSource",
    "legacy_projection_provenance",
]
