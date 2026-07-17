"""Immutable contracts for idempotent workflow run admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping


@dataclass(frozen=True)
class RunAdmissionRequest:
    workflow_name: str
    definition_digest: str
    policy_digest: str
    input_manifest_digest: str
    trigger_source: Literal["chat", "desktop", "cli", "api", "cron"]
    idempotency_key: str
    concurrency_key: str
    concurrency_policy: Literal["queue", "allow", "forbid"] = "queue"


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
