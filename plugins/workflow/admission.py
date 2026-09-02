"""Immutable contracts for idempotent workflow run admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from plugins.workflow.models import (
    ValidationIssue,
    WorkflowPackage,
    WorkflowValidationError,
)
from plugins.workflow.provenance import TriggerProvenance, TriggerSource


def workflow_profile_for_home(hermes_home: str | Path) -> str:
    """Derive the workflow owner from one explicit profile-scoped home."""
    from hermes_constants import named_profile_home
    from hermes_cli.profiles import normalize_profile_name, validate_profile_name

    profile_home = named_profile_home(Path(hermes_home).resolve())
    if profile_home is None:
        return "default"
    profile = normalize_profile_name(profile_home.name)
    validate_profile_name(profile)
    return profile


def validate_assignment_admission(
    package: WorkflowPackage,
    *,
    initiator_profile: str,
    channel=None,
) -> dict[str, str]:
    """Validate local assignment reachability without creating a handoff."""
    from hermes_cli.handoff import HandoffEndpoint
    from hermes_cli.handoff.local import LocalHermesChannel
    from hermes_cli.profiles import normalize_profile_name, profile_exists

    owner = normalize_profile_name(initiator_profile)
    mechanisms: dict[str, str] = {}
    selected_channel = channel if channel is not None else LocalHermesChannel()
    for node_id, assignment in package.sidecar.get("assignments", {}).items():
        endpoint = HandoffEndpoint.parse(assignment["endpoint"])
        path = f"sidecar.assignments.{node_id}.endpoint"
        if endpoint.profile == owner:
            raise WorkflowValidationError(
                ValidationIssue(
                    path=path,
                    code="assignment_self_target",
                    message="assignment target profile must differ from workflow owner",
                )
            )
        if not profile_exists(endpoint.profile):
            raise WorkflowValidationError(
                ValidationIssue(
                    path=path,
                    code="assignment_profile_missing",
                    message=f"assignment target profile does not exist: {endpoint.profile}",
                )
            )
        assessment = selected_channel.validate_endpoint(endpoint, owner)
        if not assessment.available or assessment.mechanism is None:
            failure = assessment.failure_code or "endpoint_unavailable"
            raise WorkflowValidationError(
                ValidationIssue(
                    path=path,
                    code="assignment_mechanism_unavailable",
                    message=f"assignment has no available local mechanism: {failure}",
                )
            )
        mechanisms[str(node_id)] = assessment.mechanism
    return mechanisms


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
    snapshot_format_version: int = 1
    dependency_manifest_digest: str | None = None
    provider_resolution_sha256: str | None = None
    assignments: Mapping[str, Mapping[str, object]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.input_digests is None:
            object.__setattr__(self, "input_digests", {})
        if self.assignments is None:
            object.__setattr__(self, "assignments", {})


class RunAdmissionController:
    """Public admission facade; RunStore remains the sole creation path."""

    def __init__(self, store) -> None:
        self._store = store

    def start(
        self, request: RunAdmissionRequest, *, immutable_snapshot: PreparedRunSnapshot
    ) -> RunAdmissionResult:
        return self._store.start_run(request, immutable_snapshot=immutable_snapshot)
