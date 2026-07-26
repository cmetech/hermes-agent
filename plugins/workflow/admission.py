"""Immutable contracts for idempotent workflow run admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from plugins.workflow.provenance import TriggerProvenance, TriggerSource


@dataclass(frozen=True)
class RunAdmissionRequest:
    workflow_name: str
    definition_digest: str
    policy_digest: str
    input_manifest_digest: str
    trigger_source: TriggerSource
    idempotency_key: str
    concurrency_key: str
    idempotency_namespace: str = "profile-local:cli"
    concurrency_policy: Literal["queue", "allow", "forbid"] = "queue"
    execution_mode: Literal["foreground", "background"] = "foreground"
    foreground_owner_id: str | None = None
    foreground_lease_seconds: float = 30.0
    operator_scope: str | None = None
    run_metadata: Mapping[str, str] | None = None
    provenance: TriggerProvenance | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.idempotency_namespace, str)
            or not self.idempotency_namespace.strip()
            or len(self.idempotency_namespace) > 512
        ):
            raise ValueError("idempotency_namespace must be bounded non-empty text")


@dataclass(frozen=True)
class RunAdmissionResult:
    run_id: str | None
    disposition: Literal["created", "existing", "queued", "rejected"]
    reason_code: str | None = None
    queue_position: int | None = None
    blocked_by_run_id: str | None = None


@dataclass(frozen=True)
class PreparedRunSnapshot:
    staging_directory: Path
    definition_digest: str
    policy_digest: str
    input_manifest_digest: str
    reserved_bytes: int
    workflow_name: str | None = None
    workflow_version: str = "1"
    nodes: tuple[Mapping[str, object], ...] = ()
    input_digests: Mapping[str, str] = None  # type: ignore[assignment]
    outward_action_nodes: tuple[str, ...] = ()
    language: Mapping[str, object] | None = None
    sealed_snapshot_digest: str | None = None

    def __post_init__(self) -> None:
        if self.input_digests is None:
            object.__setattr__(self, "input_digests", {})


class RunAdmissionController:
    """Public admission facade; RunStore remains the sole creation path."""

    def __init__(self, store) -> None:
        self._store = store

    def start(
        self, request: RunAdmissionRequest, *, immutable_snapshot: PreparedRunSnapshot
    ) -> RunAdmissionResult:
        return self._store.start_run(request, immutable_snapshot=immutable_snapshot)
