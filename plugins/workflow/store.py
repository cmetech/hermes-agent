"""Durable workflow run admission, journal, and materialized projection."""

from __future__ import annotations

import ctypes
import copy
import errno
import gc
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import AbstractSet, Callable, Iterable, Mapping

import yaml

from plugins.workflow.actions import available_actions, lane_state_for
from plugins.workflow.admission import (
    PreparedRunSnapshot,
    RunAdmissionRequest,
    RunAdmissionResult,
)
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from plugins.workflow.language import make_language_snapshot
from plugins.workflow.input_contract import (
    WorkflowInputContractError,
    workflow_input_declarations,
)
from plugins.workflow.machine_contract import WorkflowConflict, projection_was_truncated
from plugins.workflow.language_schema import (
    ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES,
    DURABLE_METADATA_STRING_MAX_CHARS,
)
from plugins.workflow.lease_clock import (
    LeaseClockSample,
    lease_is_fresh,
    sample_lease_clock,
)
from plugins.workflow.models import (
    ApprovalDecision,
    ExecutionFence,
    RunExecutionLimits,
    TerminalJournalReserve,
    WorkflowLanguageProfile,
    WorkflowPackage,
)
from plugins.workflow.provenance import (
    TriggerProvenance,
    legacy_projection_provenance,
)
from plugins.workflow.projection_limits import WORKFLOW_DEFINITION_MAX_NODES
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    ArchonOutputUnavailableError,
    _RETRYABLE_READ_ERRNOS,
    _read_descriptor_relative,
    _safe_component,
    canonical_output_publication_identity,
    output_publication_identity_sha256,
    write_archon_output_exclusive,
)
from plugins.workflow.schedule_time import (
    ScheduleInstantError,
    normalize_rfc3339_instant,
    rfc3339_instant_is_after,
    run_is_scheduled_wait,
)
from plugins.workflow.sanitize import (
    sanitize_projection,
    workflow_filename_components_are_distinct,
    workflow_input_name_is_portable,
    workflow_input_names_are_portable,
)
from plugins.workflow.sessions import (
    NodeSessionKey,
    PersistentSessionRecoverySelection,
    SessionRegistryUpdateCandidate,
    TypedMirrorIntegrityError,
    TypedMirrorObligation,
    TypedMirrorStore,
)
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    WorkflowResourceReadBudget,
    compute_package_digest,
)
from tools.managed_process import ManagedProcessTree, ProcessIdentity


class InputSnapshotError(ValueError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class StorageQuotaError(RuntimeError):
    pass


class JournalRecoveryError(RuntimeError):
    pass


class PublicationNotFoundError(LookupError):
    """No authorized publication matches the opaque identifier."""


class PublicationIntegrityError(RuntimeError):
    """A requested publication failed descriptor or content verification."""


class PublicationUnavailableError(RuntimeError):
    """A requested publication could not be read due to a retryable host fault."""


class ForegroundExecutionConflict(RuntimeError):
    """A foreground owner transition lost its exact state/epoch comparison."""


class _ScheduledPromotionAuthorization:
    """Store-instance-owned, one-use fire-time verifier."""

    __slots__ = (
        "_consumed",
        "_resource_read_budget",
        "_run_id",
        "_store_identity",
        "_verify",
    )

    def __init__(
        self,
        store_identity: object,
        run_id: str,
        verify: Callable[[Mapping[str, object]], None],
        resource_read_budget: WorkflowResourceReadBudget | None,
    ) -> None:
        self._store_identity = store_identity
        self._run_id = run_id
        self._verify = verify
        self._resource_read_budget = resource_read_budget
        self._consumed = False


class _ClosingConnection(sqlite3.Connection):
    """Preserve sqlite transaction semantics and release the handle on exit."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


@dataclass(frozen=True)
class NodeClaim:
    run_id: str
    node_id: str
    attempt_id: str
    owner_id: str
    lease_expires_at: datetime
    execution_fence: ExecutionFence | None = None


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str


def _session_registry_candidate_payload(
    candidate: SessionRegistryUpdateCandidate,
    *,
    retry_count: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "key": {
            "workflow": candidate.key.workflow,
            "node_id": candidate.key.node_id,
            "scope": candidate.key.scope,
            "provider": candidate.key.provider,
            "profile": candidate.key.profile,
        },
        "expected_generation": candidate.expected_generation,
        "new_session_id": candidate.new_session_id,
        "cache_fingerprint": candidate.cache_fingerprint,
        "winning_run_id": candidate.winning_run_id,
        "winning_node_id": candidate.winning_node_id,
        "winning_attempt_id": candidate.winning_attempt_id,
        "recovery_selected": candidate.recovery_selected,
        "retry_count": retry_count,
    }


def _private_authority_json(authority: Mapping[str, object]) -> str:
    return json.dumps(
        authority,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _session_recovery_selection_authority(
    selection: PersistentSessionRecoverySelection,
    *,
    activation_event_sequence: int,
    activation_event_type: str,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "activation_event_sequence": activation_event_sequence,
        "activation_event_type": activation_event_type,
        "run_id": selection.run_id,
        "attempt_id": selection.attempt_id,
        "key": {
            "workflow": selection.key.workflow,
            "node_id": selection.key.node_id,
            "scope": selection.key.scope,
            "provider": selection.key.provider,
            "profile": selection.key.profile,
        },
        "expected_generation": selection.expected_generation,
        "missing_session_id": selection.missing_session_id,
        "cache_fingerprint": selection.cache_fingerprint,
        "source": selection.source,
        "provider_attempts_before_recovery": 0,
    }


def _session_registry_winner_authority(
    candidate: SessionRegistryUpdateCandidate,
    *,
    activation_event_sequence: int,
    activation_event_type: str,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "activation_event_sequence": activation_event_sequence,
        "activation_event_type": activation_event_type,
        "candidate": _session_registry_candidate_payload(candidate),
    }


def _session_recovery_selection_from_authority(
    value: object,
) -> tuple[PersistentSessionRecoverySelection, int | None, str | None]:
    if not isinstance(value, Mapping):
        raise JournalRecoveryError("session recovery selection authority is malformed")
    schema_version = value.get("schema_version")
    expected_fields = {
        "schema_version",
        "run_id",
        "attempt_id",
        "key",
        "expected_generation",
        "missing_session_id",
        "cache_fingerprint",
        "source",
        "provider_attempts_before_recovery",
    }
    activation: int | None = None
    event_type: str | None = None
    if schema_version == 2:
        expected_fields.update({"activation_event_sequence", "activation_event_type"})
        activation_value = value.get("activation_event_sequence")
        if (
            isinstance(activation_value, bool)
            or not isinstance(activation_value, int)
            or activation_value < 1
        ):
            raise JournalRecoveryError(
                "session recovery selection activation is malformed"
            )
        activation = activation_value
        event_type_value = value.get("activation_event_type")
        if event_type_value != "persistent_session_missing_fresh_start":
            raise JournalRecoveryError(
                "session recovery selection activation event is malformed"
            )
        event_type = event_type_value
    elif schema_version != 1:
        raise JournalRecoveryError("session recovery selection authority is malformed")
    if set(value) != expected_fields:
        raise JournalRecoveryError("session recovery selection authority is malformed")
    key = value.get("key")
    if not isinstance(key, Mapping) or set(key) != {
        "workflow",
        "node_id",
        "scope",
        "provider",
        "profile",
    }:
        raise JournalRecoveryError("session recovery selection key is malformed")
    if value.get("provider_attempts_before_recovery") != 0:
        raise JournalRecoveryError("session recovery selection attempts are malformed")
    try:
        selection = PersistentSessionRecoverySelection(
            key=NodeSessionKey(
                workflow=key["workflow"],
                node_id=key["node_id"],
                scope=key["scope"],
                provider=key["provider"],
                profile=key["profile"],
            ),
            expected_generation=value["expected_generation"],
            missing_session_id=value["missing_session_id"],
            cache_fingerprint=value["cache_fingerprint"],
            run_id=value["run_id"],
            attempt_id=value["attempt_id"],
            source=value["source"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalRecoveryError(
            "session recovery selection identity is malformed"
        ) from exc
    return selection, activation, event_type


def _session_registry_candidate_from_authority(
    value: object,
) -> tuple[SessionRegistryUpdateCandidate, int, int | None, str | None]:
    if isinstance(value, Mapping) and value.get("schema_version") == 2:
        if set(value) != {
            "schema_version",
            "activation_event_sequence",
            "activation_event_type",
            "candidate",
        }:
            raise JournalRecoveryError("session registry winner authority is malformed")
        activation = value.get("activation_event_sequence")
        if (
            isinstance(activation, bool)
            or not isinstance(activation, int)
            or activation < 1
        ):
            raise JournalRecoveryError("session registry winner activation is malformed")
        event_type = value.get("activation_event_type")
        if event_type != "node_succeeded":
            raise JournalRecoveryError(
                "session registry winner activation event is malformed"
            )
        candidate, retry_count = _session_registry_candidate_from_payload(
            value.get("candidate")
        )
        return candidate, retry_count, activation, event_type
    candidate, retry_count = _session_registry_candidate_from_payload(value)
    return candidate, retry_count, None, None


def _session_registry_candidate_from_payload(
    value: object,
) -> tuple[SessionRegistryUpdateCandidate, int]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise JournalRecoveryError("session registry obligation is malformed")
    key = value.get("key")
    if not isinstance(key, Mapping) or set(key) != {
        "workflow",
        "node_id",
        "scope",
        "provider",
        "profile",
    }:
        raise JournalRecoveryError("session registry obligation key is malformed")
    retry_count = value.get("retry_count")
    if (
        isinstance(retry_count, bool)
        or not isinstance(retry_count, int)
        or not 0 <= retry_count <= 5
    ):
        raise JournalRecoveryError("session registry obligation retry is malformed")
    if type(value.get("recovery_selected")) is not bool:
        raise JournalRecoveryError(
            "session registry obligation recovery flag is malformed"
        )
    bounded_text = (
        key.get("workflow"),
        key.get("node_id"),
        key.get("scope"),
        key.get("provider"),
        key.get("profile"),
        value.get("new_session_id"),
        value.get("cache_fingerprint"),
        value.get("winning_run_id"),
        value.get("winning_node_id"),
        value.get("winning_attempt_id"),
    )
    if any(
        not isinstance(item, str) or not item or len(item) > 16_384
        for item in bounded_text
    ):
        raise JournalRecoveryError(
            "session registry obligation identity is malformed"
        )
    try:
        candidate = SessionRegistryUpdateCandidate(
            key=NodeSessionKey(
                workflow=key["workflow"],
                node_id=key["node_id"],
                scope=key["scope"],
                provider=key["provider"],
                profile=key["profile"],
            ),
            expected_generation=value["expected_generation"],
            new_session_id=value["new_session_id"],
            cache_fingerprint=value["cache_fingerprint"],
            winning_run_id=value["winning_run_id"],
            winning_node_id=value["winning_node_id"],
            winning_attempt_id=value["winning_attempt_id"],
            recovery_selected=value["recovery_selected"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalRecoveryError(
            "session registry obligation identity is malformed"
        ) from exc
    if set(value) != {
        "schema_version",
        "key",
        "expected_generation",
        "new_session_id",
        "cache_fingerprint",
        "winning_run_id",
        "winning_node_id",
        "winning_attempt_id",
        "recovery_selected",
        "retry_count",
    }:
        raise JournalRecoveryError("session registry obligation fields are malformed")
    return candidate, retry_count


_MAX_PENDING_SESSION_REGISTRY_UPDATES = WORKFLOW_DEFINITION_MAX_NODES


def _pending_session_registry_payloads(
    projection: Mapping[str, object],
) -> dict[str, object]:
    singular = projection.get("pending_session_registry_update")
    plural = projection.get("pending_session_registry_updates")
    if singular is not None and plural is not None:
        raise JournalRecoveryError(
            "session registry obligations have conflicting representations"
        )
    if singular is not None:
        candidate, _retry_count = _session_registry_candidate_from_payload(singular)
        return {candidate.winning_attempt_id: singular}
    if plural is None:
        return {}
    if (
        not isinstance(plural, Mapping)
        or not 1 <= len(plural) <= _MAX_PENDING_SESSION_REGISTRY_UPDATES
    ):
        raise JournalRecoveryError("session registry obligations are malformed")
    pending: dict[str, object] = {}
    for attempt_id, payload in plural.items():
        candidate, _retry_count = _session_registry_candidate_from_payload(payload)
        if (
            not isinstance(attempt_id, str)
            or attempt_id != candidate.winning_attempt_id
            or attempt_id in pending
        ):
            raise JournalRecoveryError(
                "session registry obligation attempt identity is malformed"
            )
        pending[attempt_id] = payload
    return pending


def _store_pending_session_registry_payloads(
    projection: dict[str, object],
    pending: Mapping[str, object],
) -> None:
    projection.pop("pending_session_registry_update", None)
    projection.pop("pending_session_registry_updates", None)
    if len(pending) == 1:
        projection["pending_session_registry_update"] = next(iter(pending.values()))
    elif pending:
        projection["pending_session_registry_updates"] = {
            attempt_id: pending[attempt_id] for attempt_id in sorted(pending)
        }


def _set_pending_session_registry_update(
    projection: dict[str, object],
    candidate: SessionRegistryUpdateCandidate,
    *,
    retry_count: int = 0,
) -> None:
    pending = _pending_session_registry_payloads(projection)
    if (
        candidate.winning_attempt_id not in pending
        and len(pending) >= _MAX_PENDING_SESSION_REGISTRY_UPDATES
    ):
        raise StorageQuotaError("session registry obligation capacity is exhausted")
    pending[candidate.winning_attempt_id] = _session_registry_candidate_payload(
        candidate,
        retry_count=retry_count,
    )
    _store_pending_session_registry_payloads(projection, pending)


def _remove_pending_session_registry_update(
    projection: dict[str, object],
    candidate: SessionRegistryUpdateCandidate,
) -> None:
    pending = _pending_session_registry_payloads(projection)
    payload = pending.get(candidate.winning_attempt_id)
    if payload is None:
        raise RuntimeError("stale session registry update outcome")
    current, _retry_count = _session_registry_candidate_from_payload(payload)
    if current != candidate:
        raise RuntimeError("stale session registry update outcome")
    pending.pop(candidate.winning_attempt_id)
    _store_pending_session_registry_payloads(projection, pending)


def _session_registry_candidate_is_corroborated(
    projection: Mapping[str, object],
    candidate: SessionRegistryUpdateCandidate,
) -> bool:
    nodes = projection.get("nodes")
    winning_node = (
        nodes.get(candidate.winning_node_id) if isinstance(nodes, Mapping) else None
    )
    if not isinstance(winning_node, Mapping):
        return False
    winning_attempts = [
        attempt
        for attempt in winning_node.get("attempts", ())
        if isinstance(attempt, Mapping)
        and attempt.get("attempt_id") == candidate.winning_attempt_id
        and attempt.get("state") == "succeeded"
    ]
    if len(winning_attempts) != 1:
        return False
    try:
        authority, authority_retry_count = _session_registry_candidate_from_payload(
            winning_attempts[0].get("session_registry_authority")
        )
    except JournalRecoveryError:
        return False
    if authority_retry_count != 0 or authority != candidate:
        return False
    winning_metadata = winning_attempts[0].get("metadata")
    if (
        not isinstance(winning_metadata, Mapping)
        or winning_metadata.get("session_id") != candidate.new_session_id
        or winning_metadata.get("cache_fingerprint")
        != candidate.cache_fingerprint
        or winning_node.get("session_id") != candidate.new_session_id
        or winning_node.get("cache_fingerprint") != candidate.cache_fingerprint
    ):
        return False
    for artifact in projection.get("artifacts", ()):
        if (
            isinstance(artifact, Mapping)
            and artifact.get("node_id") == candidate.winning_node_id
            and artifact.get("attempt_id") == candidate.winning_attempt_id
            and "session_id" in artifact
            and artifact.get("session_id") != candidate.new_session_id
        ):
            return False
    if not candidate.recovery_selected:
        return True
    recoveries = winning_node.get("session_recoveries")
    matches = [
        recovery
        for recovery in recoveries
        if isinstance(recovery, Mapping)
        and recovery.get("attempt_id") == candidate.winning_attempt_id
    ] if isinstance(recoveries, list) else []
    if len(matches) != 1:
        return False
    recovery = matches[0]
    return (
        recovery.get("registry_generation") == candidate.expected_generation
        and recovery.get("cache_fingerprint_sha256")
        == _sha256(candidate.cache_fingerprint.encode("utf-8"))
        and recovery.get("source") == "cross_run_registry"
        and recovery.get("provider") == candidate.key.provider
        and recovery.get("runtime_profile") == candidate.key.profile
        and recovery.get("provider_attempts_before_recovery") == 0
    )


@dataclass(frozen=True, slots=True)
class TypedPublicationCandidate:
    attempt_relative_path: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str
    schema_fingerprint: str | None
    canonicalization_version: int
    session_id: str | None


@dataclass(frozen=True, slots=True)
class TypedPublicationRef:
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str
    metadata_sha256: str
    schema_fingerprint: str | None
    canonicalization_version: int
    produced_at: str
    session_id: str | None


@dataclass(frozen=True, slots=True)
class VerifiedPublication:
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str
    node_id: str
    attempt_id: str
    schema_fingerprint: str | None
    produced_at: str
    session_id: str | None
    content: bytes


@dataclass(frozen=True, slots=True)
class _JournaledTypedPublication:
    publication_id: str
    content_name: str
    output_type: str
    media_type: str
    size_bytes: int
    sha256: str
    metadata_sha256: str
    schema_fingerprint: str | None
    canonicalization_version: int
    produced_at: str
    session_id: str | None
    run_id: str
    node_id: str
    attempt_id: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class _DeclaredTypedOutput:
    output_type: str
    has_structured_schema: bool


@dataclass(frozen=True, slots=True)
class _RequiredTypedPublication:
    output_type: str
    schema_fingerprint: str | None
    canonicalization_version: int


@dataclass(frozen=True)
class ForegroundExecutionLease:
    owner_id: str
    epoch: int
    lease_expires_at: datetime
    boot_id: str | None = None
    heartbeat_monotonic: float | None = None
    lease_seconds: float | None = None


_NONTERMINAL = {
    "queued",
    "running",
    "waiting_retry",
    "recovery_pending",
    "paused",
    "interrupted",
}
_EXECUTING = {"running"}
_RUN_SCOPED_REPAIR_REASONS = frozenset(
    {
        "legacy_effect_policy_uncorroborated",
        "notification_reconciliation_unverified",
        "run_evidence_uncorroborated",
        "typed_mirror_integrity",
        "typed_publication_integrity",
    }
)
_UNATTENDED_REVALIDATION_REASONS = frozenset(
    {
        "legacy_effect_policy_uncorroborated",
        "run_evidence_uncorroborated",
    }
)
_UNATTENDED_REVALIDATION_REASON_ORDER = tuple(
    sorted(_UNATTENDED_REVALIDATION_REASONS)
)
_UNATTENDED_REVALIDATION_REASON_SQL = ",".join(
    f"'{reason}'" for reason in _UNATTENDED_REVALIDATION_REASON_ORDER
)
_RUN_SCOPED_REPAIR_REASON_ORDER = tuple(sorted(_RUN_SCOPED_REPAIR_REASONS))
_RUN_SCOPED_REPAIR_REASON_SQL = ",".join(
    f"'{reason}'" for reason in _RUN_SCOPED_REPAIR_REASON_ORDER
)
_RUN_SCOPED_REPAIR_EXCLUSION_SQL = (
    "NOT EXISTS (SELECT 1 FROM repair_events AS repair "
    "WHERE repair.run_id=runs.run_id "
    f"AND repair.reason_code IN ({_RUN_SCOPED_REPAIR_REASON_SQL}) "
    "AND repair.sequence=(SELECT MAX(latest.sequence) "
    "FROM repair_events AS latest WHERE latest.run_id=repair.run_id "
    "AND latest.reason_code=repair.reason_code) "
    "AND repair.outcome='repair_required')"
)
_STORE_SCHEMA_VERSION = 14
_SCHEDULE_PARITY_UNSET = object()
# Direct RunStore/CLI access is already the profile-local filesystem admin
# boundary. Network adapters must always pass their verified authority binding.
_LOCAL_ADMIN_AUTHORITY_BINDING = "profile-local-runstore-admin"
_PROJECTION_STATUSES = {
    "queued",
    "running",
    "waiting_retry",
    "recovery_pending",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
}
_NODE_STATES = {
    "pending",
    "ready",
    "claimed",
    "running",
    "waiting_retry",
    "waiting_resolution",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
}
_SECRET_DIAGNOSTIC = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_ -]?key|token|password|secret)\s*[:=]\s*)"
    r"[^\s,;]+|\bsk-[A-Za-z0-9_-]{8,}\b"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TYPED_PUBLICATION_METADATA_MAX_BYTES = 65_536
_TYPED_PUBLICATION_DESCRIPTOR_VERSION = 2
_LEGACY_TYPED_PUBLICATION_FIELDS = frozenset(
    {
        "publication_id",
        "content_name",
        "output_type",
        "media_type",
        "size_bytes",
        "sha256",
        "metadata_sha256",
        "node_id",
        "attempt_id",
        "relative_path",
    }
)
_TYPED_PUBLICATION_JSON_MEDIA_TYPE = "application/json"
_TYPED_PUBLICATION_TEXT_MEDIA_TYPE = "text/markdown; charset=utf-8"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _projection_digest(projection: Mapping[str, object]) -> str:
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256(encoded)


def _journal_frame_digest(event: Mapping[str, object]) -> str:
    material = dict(event)
    material.pop("frame_sha256", None)
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256(encoded)


def _encode_journal_frame(event: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
    framed = dict(event)
    framed["schema_version"] = 2
    framed["frame_version"] = 1
    framed["frame_sha256"] = _journal_frame_digest(framed)
    encoded = json.dumps(
        framed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    return framed, encoded


def _recovery_fields(projection: Mapping[str, object]) -> dict[str, object]:
    snapshot = json.loads(json.dumps(projection, sort_keys=True, ensure_ascii=False))
    return {
        "projection": snapshot,
        "projection_sha256": _projection_digest(snapshot),
    }


def _sanitize(value: object, *, key: str = "", depth: int = 0) -> object:
    if depth > 12:
        return "[TRUNCATED_DEPTH]"
    lowered = key.lower()
    if any(
        marker in lowered
        for marker in ("secret", "password", "token", "api_key", "authorization")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child): _sanitize(item, key=str(child), depth=depth + 1)
            for child, item in list(value.items())[:200]
        }
    if isinstance(value, list | tuple):
        return [_sanitize(item, key=key, depth=depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return value[:16_384]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:2000]


def _sanitize_diagnostic(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET_DIAGNOSTIC.sub("[REDACTED]", value)[:2000]


def _redact_private_session_authority(
    value: Mapping[str, object],
    *,
    private_authorities: Mapping[
        str, Mapping[str, Mapping[str, object]]
    ] | None = None,
) -> dict[str, object]:
    """Copy a public run/event projection without exact session authority."""
    projected = copy.deepcopy(dict(value))
    authority_projection = (
        projected["projection"]
        if isinstance(projected.get("projection"), dict)
        else projected
    )
    authority_projection.pop("pending_session_registry_update", None)
    authority_projection.pop("pending_session_registry_updates", None)
    nodes = authority_projection.get("nodes")
    protected_attempts: set[tuple[str, str]] = set()
    if isinstance(nodes, dict) and private_authorities is not None:
        for authority in private_authorities.get(
            "session_registry_winner_authority", {}
        ).values():
            try:
                candidate, retry_count, _activation, _event_type = (
                    _session_registry_candidate_from_authority(
                        authority
                    )
                )
            except JournalRecoveryError as exc:
                raise JournalRecoveryError(
                    "private session winner authority is malformed"
                ) from exc
            if (
                retry_count == 0
                and candidate.winning_run_id == authority_projection.get("run_id")
            ):
                protected_attempts.add(
                    (candidate.winning_node_id, candidate.winning_attempt_id)
                )
    if isinstance(nodes, dict):
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            node_is_protected = any(
                protected_node_id == str(node_id)
                for protected_node_id, _attempt_id in protected_attempts
            )
            for attempt in node.get("attempts", ()):
                if not isinstance(attempt, dict):
                    continue
                attempt_id = attempt.get("attempt_id")
                marker_is_protected = isinstance(
                    attempt.get("session_registry_authority"), Mapping
                )
                anchored_is_protected = (
                    str(node_id), str(attempt_id)
                ) in protected_attempts
                if (
                    not isinstance(attempt_id, str)
                    or not marker_is_protected
                    and not anchored_is_protected
                ):
                    continue
                node_is_protected = True
                protected_attempts.add((str(node_id), attempt_id))
                attempt.pop("session_registry_authority", None)
                metadata = attempt.get("metadata")
                if isinstance(metadata, dict):
                    metadata.pop("session_id", None)
                    metadata.pop("cache_fingerprint", None)
            if node_is_protected:
                node.pop("session_id", None)
                node.pop("cache_fingerprint", None)
    artifacts = authority_projection.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if (
                str(artifact.get("node_id")),
                str(artifact.get("attempt_id")),
            ) in protected_attempts:
                artifact.pop("session_id", None)
    payload = projected.get("payload")
    if isinstance(payload, dict):
        event_identity = (
            str(projected.get("node_id")),
            str(projected.get("attempt_id")),
        )
        if event_identity in protected_attempts:
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                metadata.pop("session_id", None)
                metadata.pop("cache_fingerprint", None)
            payload_artifacts = payload.get("artifacts")
            if isinstance(payload_artifacts, list):
                for artifact in payload_artifacts:
                    if isinstance(artifact, dict):
                        artifact.pop("session_id", None)
    return projected


def _sanitize_v3_condition_diagnostic(value: str) -> str:
    """Redact and truncate one condition diagnostic by valid UTF-8 bytes."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("v3 condition message must be valid UTF-8") from exc
    sanitized = _SECRET_DIAGNOSTIC.sub("[REDACTED]", value)
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES:
        return sanitized
    return encoded[:ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES].decode(
        "utf-8", errors="ignore"
    )


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _publication_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _publication_noreplace_primitive():
    if os.name != "posix":
        return None
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        primitive = getattr(library, "renameatx_np", None)
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        primitive = getattr(library, "renameat2", None)
        flag = 1  # RENAME_NOREPLACE
    else:
        return None
    if primitive is None:
        return None
    primitive.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    primitive.restype = ctypes.c_int
    return primitive, flag


def _require_secure_publication_io() -> None:
    required_dir_fd_functions = {
        os.mkdir,
        os.open,
        os.rename,
        os.rmdir,
        os.stat,
        os.unlink,
    }
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not required_dir_fd_functions <= os.supports_dir_fd
        or os.listdir not in os.supports_fd
        or _publication_noreplace_primitive() is None
    ):
        raise ArchonOutputIntegrityError(
            "secure atomic typed publication is unavailable on this host"
        )


def _fsync_publication_directory(descriptor: int, *, boundary: str) -> None:
    """Strictly flush one publication directory or propagate the failure."""
    os.fsync(descriptor)


def _publication_directory_identity(descriptor: int) -> tuple[int, int]:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode):
        raise ArchonOutputIntegrityError("typed publication directory is unsafe")
    return observed.st_dev, observed.st_ino


def _verify_publication_directory_identity(
    run_descriptor: int,
    publications_descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    current_descriptor: int | None = None
    try:
        current_descriptor = os.open(
            "publications",
            _publication_directory_flags(),
            dir_fd=run_descriptor,
        )
        if (
            _publication_directory_identity(publications_descriptor)
            != expected_identity
            or _publication_directory_identity(current_descriptor)
            != expected_identity
        ):
            raise ArchonOutputIntegrityError(
                "typed publication directory identity changed"
            )
    except ArchonOutputIntegrityError:
        raise
    except OSError as exc:
        raise ArchonOutputIntegrityError(
            "typed publication directory identity changed"
        ) from exc
    finally:
        if current_descriptor is not None:
            os.close(current_descriptor)


def _commit_publication_directory_noreplace(
    run_descriptor: int,
    publications_descriptor: int,
    staging_name: str,
    final_name: str,
    expected_identity: tuple[int, int],
) -> None:
    _verify_publication_directory_identity(
        run_descriptor,
        publications_descriptor,
        expected_identity,
    )
    loaded = _publication_noreplace_primitive()
    if loaded is None:
        raise ArchonOutputIntegrityError(
            "secure atomic typed publication is unavailable on this host"
        )
    primitive, flag = loaded
    result = primitive(
        publications_descriptor,
        os.fsencode(staging_name),
        publications_descriptor,
        os.fsencode(final_name),
        flag,
    )
    if result == 0:
        _verify_publication_directory_identity(
            run_descriptor,
            publications_descriptor,
            expected_identity,
        )
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ArchonOutputIntegrityError(
            "typed publication destination already exists"
        )
    raise ArchonOutputIntegrityError(
        "typed publication atomic commit failed"
    ) from OSError(error, os.strerror(error))


def _write_publication_file(
    directory_descriptor: int,
    name: str,
    data: bytes,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("typed publication write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_publication_staging(
    publications_descriptor: int,
    staging_descriptor: int | None,
    staging_name: str,
    file_names: tuple[str, ...],
) -> None:
    if staging_descriptor is not None:
        for name in file_names:
            try:
                os.unlink(name, dir_fd=staging_descriptor)
            except OSError:
                pass
        os.close(staging_descriptor)
    try:
        os.rmdir(staging_name, dir_fd=publications_descriptor)
    except OSError:
        pass


def _remove_publication_entry_at(parent_descriptor: int, name: str) -> None:
    try:
        observed = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    reparse_marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_reparse = bool(
        reparse_marker
        and getattr(observed, "st_file_attributes", 0) & reparse_marker
    )
    if stat.S_ISDIR(observed.st_mode) and not is_reparse:
        try:
            descriptor = os.open(
                name,
                _publication_directory_flags(),
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise ArchonOutputIntegrityError(
                "typed publication cleanup directory is unsafe"
            ) from exc
        try:
            current = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(current.st_mode)
                or (current.st_dev, current.st_ino)
                != (observed.st_dev, observed.st_ino)
            ):
                raise ArchonOutputIntegrityError(
                    "typed publication cleanup directory identity changed"
                )
            for child in os.listdir(descriptor):
                _remove_publication_entry_at(descriptor, child)
        finally:
            os.close(descriptor)
        os.rmdir(name, dir_fd=parent_descriptor)
    else:
        os.unlink(name, dir_fd=parent_descriptor)


def _discard_publication_entry_at(
    publications_descriptor: int,
    name: str,
) -> None:
    try:
        os.stat(
            name,
            dir_fd=publications_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    discarded = f".discard-{uuid.uuid4().hex}"
    os.rename(
        name,
        discarded,
        src_dir_fd=publications_descriptor,
        dst_dir_fd=publications_descriptor,
    )
    os.fsync(publications_descriptor)
    _remove_publication_entry_at(publications_descriptor, discarded)
    os.fsync(publications_descriptor)


def _replace_with_retry(source: str | Path, target: str | Path) -> None:
    """Rename across the quarantine boundary, tolerating Windows file locking.

    POSIX renames an open file happily. Windows refuses with WinError 32 while
    ANY handle is open, which makes this the fragile step in damaged-index
    quarantine (`_preserve_damaged_index`) -- precisely the path taken when a
    store is already unhealthy, so failing here turns a recoverable corrupt
    index into an unhandled PermissionError.

    Two things keep a handle alive past the point the code "closed" it:

    * A connection still referenced by a live traceback. Any caller holding an
      exception -- pytest.raises, or production code that captured the error to
      report it -- keeps the raising frame, its locals, and therefore the
      sqlite3 connection alive until collected. gc.collect() reaps those.
    * Delayed handle release by the OS or an antivirus scanner mid-scan, which
      is common on Windows CI and on corporate laptops with real-time scanning.
      Only waiting fixes that, and the original ~0.15s total was far too short.

    The retry budget below is ~6.4s, which is cheap relative to losing the
    index, and is never paid on the healthy path.
    """
    attempts = 8
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            if attempt == 0:
                # Cheapest cause first: drop connections that are unreachable
                # except through a retained traceback.
                gc.collect()
            time.sleep(0.05 * (2**attempt))


def _durable_replace(source: str | Path, target: str | Path) -> None:
    source_path = Path(source)
    target_path = Path(target)
    _replace_with_retry(source_path, target_path)
    _fsync_directory(target_path.parent)
    if source_path.parent != target_path.parent:
        _fsync_directory(source_path.parent)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _durable_replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _json_document_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(path, _json_document_bytes(value))


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _durable_replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _typed_publication_fields(reference: TypedPublicationRef) -> dict[str, object]:
    return {
        "typed_publication_version": _TYPED_PUBLICATION_DESCRIPTOR_VERSION,
        "publication_id": reference.publication_id,
        "content_name": reference.content_name,
        "output_type": reference.output_type,
        "media_type": reference.media_type,
        "size_bytes": reference.size_bytes,
        "sha256": reference.sha256,
        "metadata_sha256": reference.metadata_sha256,
        "schema_fingerprint": reference.schema_fingerprint,
        "canonicalization_version": reference.canonicalization_version,
        "produced_at": reference.produced_at,
        "session_id": reference.session_id,
    }


def _typed_publication_metadata_bytes(
    *,
    publication_id: str,
    content_name: str,
    output_type: str,
    media_type: str,
    sha256: str,
    node_id: str,
    attempt_id: str,
    run_id: str,
    schema_fingerprint: str | None,
    size_bytes: int,
    produced_at: str,
    session_id: str | None,
    canonicalization_version: int,
) -> bytes:
    return json.dumps(
        {
            "publication_id": publication_id,
            "content_name": content_name,
            "output_type": output_type,
            "media_type": media_type,
            "sha256": sha256,
            "node_id": node_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "language_profile": "archon-2026-07",
            "schema_fingerprint": schema_fingerprint,
            "size_bytes": size_bytes,
            "produced_at": produced_at,
            "session_id": session_id,
            "canonicalization_version": canonicalization_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"


def _migrate_legacy_typed_publication_descriptors(
    directory: Path,
    projection: Mapping[str, object],
) -> int:
    artifacts = projection.get("artifacts")
    run_id = projection.get("run_id")
    if not isinstance(artifacts, list) or not isinstance(run_id, str):
        raise JournalRecoveryError("typed publication descriptor authority is invalid")
    migrated = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if "typed_publication_version" in artifact:
            continue
        artifact_fields = frozenset(artifact)
        if artifact_fields != _LEGACY_TYPED_PUBLICATION_FIELDS:
            if {
                "publication_id",
                "content_name",
                "metadata_sha256",
            }.intersection(artifact_fields):
                raise JournalRecoveryError(
                    "unversioned typed publication descriptor is invalid"
                )
            continue
        publication_id = artifact.get("publication_id")
        content_name = artifact.get("content_name")
        size_bytes = artifact.get("size_bytes")
        metadata_sha256 = artifact.get("metadata_sha256")
        if (
            not isinstance(publication_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", publication_id) is None
            or content_name not in {"content.json", "content.md"}
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= size_bytes <= 500_000
            or not isinstance(metadata_sha256, str)
            or _SHA256_PATTERN.fullmatch(metadata_sha256) is None
        ):
            raise JournalRecoveryError("legacy typed publication descriptor is invalid")
        metadata_path = directory / "publications" / publication_id / "metadata.json"
        try:
            observed = metadata_path.lstat()
            reparse_marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (
                not stat.S_ISREG(observed.st_mode)
                or stat.S_ISLNK(observed.st_mode)
                or observed.st_size > _TYPED_PUBLICATION_METADATA_MAX_BYTES
                or (
                    reparse_marker
                    and getattr(observed, "st_file_attributes", 0) & reparse_marker
                )
            ):
                raise ArchonOutputIntegrityError(
                    "legacy typed publication metadata is unsafe"
                )
            metadata_bytes = _read_descriptor_relative(
                directory,
                f"publications/{publication_id}/metadata.json",
                size_bytes=observed.st_size,
            )
            content = _read_descriptor_relative(
                directory,
                f"publications/{publication_id}/{content_name}",
                size_bytes=size_bytes,
            )
        except (
            ArchonOutputIntegrityError,
            ArchonOutputUnavailableError,
            OSError,
        ) as exc:
            raise JournalRecoveryError(
                "legacy typed publication bundle is unavailable"
            ) from exc
        if (
            not hmac.compare_digest(_sha256(metadata_bytes), metadata_sha256)
            or len(content) != size_bytes
            or not isinstance(artifact.get("sha256"), str)
            or not hmac.compare_digest(_sha256(content), str(artifact["sha256"]))
        ):
            raise JournalRecoveryError(
                "legacy typed publication bundle identity is invalid"
            )
        try:
            metadata = json.loads(metadata_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JournalRecoveryError(
                "legacy typed publication metadata is malformed"
            ) from exc
        if not isinstance(metadata, Mapping):
            raise JournalRecoveryError(
                "legacy typed publication metadata is malformed"
            )
        produced_at = metadata.get("produced_at")
        schema_fingerprint = metadata.get("schema_fingerprint")
        canonicalization_version = metadata.get("canonicalization_version")
        session_id = metadata.get("session_id")
        try:
            produced = datetime.fromisoformat(produced_at)
        except (TypeError, ValueError) as exc:
            raise JournalRecoveryError(
                "legacy typed publication metadata is malformed"
            ) from exc
        if (
            produced.tzinfo is None
            or produced.utcoffset() is None
            or (
                schema_fingerprint is not None
                and (
                    not isinstance(schema_fingerprint, str)
                    or _SHA256_PATTERN.fullmatch(schema_fingerprint) is None
                )
            )
            or isinstance(canonicalization_version, bool)
            or canonicalization_version != 1
            or (
                session_id is not None
                and (
                    not isinstance(session_id, str)
                    or len(session_id) > DURABLE_METADATA_STRING_MAX_CHARS
                )
            )
        ):
            raise JournalRecoveryError(
                "legacy typed publication metadata is malformed"
            )
        expected_metadata = _typed_publication_metadata_bytes(
            publication_id=publication_id,
            content_name=str(content_name),
            output_type=str(artifact.get("output_type")),
            media_type=str(artifact.get("media_type")),
            sha256=str(artifact.get("sha256")),
            node_id=str(artifact.get("node_id")),
            attempt_id=str(artifact.get("attempt_id")),
            run_id=run_id,
            schema_fingerprint=schema_fingerprint,
            size_bytes=size_bytes,
            produced_at=produced_at,
            session_id=session_id,
            canonicalization_version=canonicalization_version,
        )
        if not hmac.compare_digest(metadata_bytes, expected_metadata):
            raise JournalRecoveryError(
                "legacy typed publication metadata is not corroborated"
            )
        artifact.update({
            "typed_publication_version": _TYPED_PUBLICATION_DESCRIPTOR_VERSION,
            "schema_fingerprint": schema_fingerprint,
            "canonicalization_version": canonicalization_version,
            "produced_at": produced_at,
            "session_id": session_id,
        })
        migrated += 1
    return migrated


def _sealed_typed_output_declarations(
    directory: Path,
    projection: Mapping[str, object],
) -> dict[str, _DeclaredTypedOutput]:
    language = projection.get("language")
    if (
        not isinstance(language, Mapping)
        or language.get("effective_profile") != "archon-2026-07"
    ):
        return {}
    definition = directory / "definition.yaml"
    try:
        observed = definition.lstat()
        reparse_marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_size > 2 * 1024 * 1024
            or (
                reparse_marker
                and getattr(observed, "st_file_attributes", 0) & reparse_marker
            )
        ):
            raise JournalRecoveryError(
                "typed publication sealed definition is unsafe"
            )
        document = yaml.safe_load(
            _read_descriptor_relative(
                directory,
                "definition.yaml",
                size_bytes=observed.st_size,
            )
        )
    except ArchonOutputUnavailableError:
        raise
    except OSError as exc:
        if exc.errno in _RETRYABLE_READ_ERRNOS:
            raise ArchonOutputUnavailableError(
                "typed publication sealed definition is temporarily unavailable"
            ) from exc
        raise JournalRecoveryError(
            "typed publication sealed definition is unavailable"
        ) from exc
    except (ArchonOutputIntegrityError, yaml.YAMLError) as exc:
        raise JournalRecoveryError(
            "typed publication sealed definition is unavailable"
        ) from exc
    if not isinstance(document, Mapping) or not isinstance(
        document.get("nodes"), list
    ):
        raise JournalRecoveryError(
            "typed publication sealed definition is malformed"
        )
    declarations: dict[str, _DeclaredTypedOutput] = {}
    for node in document["nodes"]:
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str):
            raise JournalRecoveryError(
                "typed publication sealed node declaration is malformed"
            )
        if "output_type" not in node:
            continue
        node_id = node["id"]
        output_type = node.get("output_type")
        if (
            not node_id
            or node_id in declarations
            or not isinstance(output_type, str)
            or not output_type.strip()
            or len(output_type) > DURABLE_METADATA_STRING_MAX_CHARS
        ):
            raise JournalRecoveryError(
                "typed publication sealed output declaration is malformed"
            )
        declarations[node_id] = _DeclaredTypedOutput(
            output_type=output_type,
            has_structured_schema=node.get("output_format") is not None,
        )
    return declarations


def _journaled_typed_publications(
    projection: Mapping[str, object],
    declared_outputs: Mapping[str, _DeclaredTypedOutput],
) -> tuple[_JournaledTypedPublication, ...]:
    run_id = projection.get("run_id")
    nodes = projection.get("nodes")
    artifacts = projection.get("artifacts")
    if not isinstance(run_id, str) or not isinstance(nodes, Mapping) or not isinstance(
        artifacts, list
    ):
        raise JournalRecoveryError("typed publication descriptor authority is invalid")
    language = projection.get("language")
    archon = (
        isinstance(language, Mapping)
        and language.get("effective_profile") == "archon-2026-07"
    )
    structured_outputs = language.get("structured_outputs") if archon else None
    if archon and not isinstance(structured_outputs, Mapping):
        raise JournalRecoveryError(
            "typed publication language authority is invalid"
        )
    requirements: dict[str, _RequiredTypedPublication] = {}
    for node_id, declaration in declared_outputs.items():
        node = nodes.get(node_id)
        if not isinstance(node, Mapping):
            raise JournalRecoveryError(
                "typed publication node authority is invalid"
            )
        if not archon or node.get("state") != "succeeded":
            continue
        structured = structured_outputs.get(node_id)
        if declaration.has_structured_schema:
            if not isinstance(structured, Mapping):
                raise JournalRecoveryError(
                    "typed publication schema authority is invalid"
                )
            fingerprint = structured.get("schema_fingerprint")
            version = structured.get("canonicalization_version")
            if (
                not isinstance(fingerprint, str)
                or _SHA256_PATTERN.fullmatch(fingerprint) is None
                or isinstance(version, bool)
                or version != 1
            ):
                raise JournalRecoveryError(
                    "typed publication schema authority is invalid"
                )
        else:
            if structured is not None:
                raise JournalRecoveryError(
                    "typed publication schema authority is invalid"
                )
            fingerprint = None
            version = 1
        requirements[node_id] = _RequiredTypedPublication(
            output_type=declaration.output_type,
            schema_fingerprint=fingerprint,
            canonicalization_version=version,
        )
    descriptors: list[_JournaledTypedPublication] = []
    publication_ids: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        typed_markers = {
            "typed_publication_version",
            "publication_id",
            "content_name",
            "metadata_sha256",
            "produced_at",
        }
        if not typed_markers.intersection(artifact):
            continue
        publication_id = artifact.get("publication_id")
        descriptor_version = artifact.get("typed_publication_version")
        content_name = artifact.get("content_name")
        output_type = artifact.get("output_type")
        media_type = artifact.get("media_type")
        size_bytes = artifact.get("size_bytes")
        content_sha256 = artifact.get("sha256")
        metadata_sha256 = artifact.get("metadata_sha256")
        schema_fingerprint = artifact.get("schema_fingerprint")
        canonicalization_version = artifact.get("canonicalization_version")
        produced_at = artifact.get("produced_at")
        session_id = artifact.get("session_id")
        node_id = artifact.get("node_id")
        attempt_id = artifact.get("attempt_id")
        relative_path = artifact.get("relative_path")
        expected_content = (
            {
                _TYPED_PUBLICATION_JSON_MEDIA_TYPE: "content.json",
                _TYPED_PUBLICATION_TEXT_MEDIA_TYPE: "content.md",
            }.get(media_type)
            if isinstance(media_type, str)
            else None
        )
        if (
            isinstance(descriptor_version, bool)
            or descriptor_version != _TYPED_PUBLICATION_DESCRIPTOR_VERSION
            or not isinstance(publication_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", publication_id) is None
            or publication_id in publication_ids
            or not isinstance(content_name, str)
            or content_name != expected_content
            or not isinstance(output_type, str)
            or not output_type.strip()
            or len(output_type) > DURABLE_METADATA_STRING_MAX_CHARS
            or not isinstance(node_id, str)
            or not isinstance(attempt_id, str)
            or not isinstance(relative_path, str)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= size_bytes <= 500_000
            or not isinstance(content_sha256, str)
            or _SHA256_PATTERN.fullmatch(content_sha256) is None
            or not isinstance(metadata_sha256, str)
            or _SHA256_PATTERN.fullmatch(metadata_sha256) is None
            or (
                schema_fingerprint is not None
                and (
                    not isinstance(schema_fingerprint, str)
                    or _SHA256_PATTERN.fullmatch(schema_fingerprint) is None
                )
            )
            or isinstance(canonicalization_version, bool)
            or canonicalization_version != 1
            or not isinstance(produced_at, str)
            or not produced_at
            or len(produced_at) > DURABLE_METADATA_STRING_MAX_CHARS
            or (
                session_id is not None
                and (
                    not isinstance(session_id, str)
                    or len(session_id) > DURABLE_METADATA_STRING_MAX_CHARS
                )
            )
        ):
            raise JournalRecoveryError("typed publication descriptor is invalid")
        try:
            produced = datetime.fromisoformat(produced_at)
        except ValueError as exc:
            raise JournalRecoveryError(
                "typed publication descriptor production time is invalid"
            ) from exc
        if produced.tzinfo is None:
            raise JournalRecoveryError(
                "typed publication descriptor production time is invalid"
            )
        node = nodes.get(node_id)
        relative = PurePosixPath(relative_path)
        owned_prefixes = {
            ("nodes", node_id, attempt_id),
            (
                "nodes",
                _safe_component("node", node_id),
                _safe_component("attempt", attempt_id),
            ),
        }
        loop_state = node.get("loop_state") if isinstance(node, Mapping) else None
        iteration = (
            loop_state.get("iteration")
            if isinstance(loop_state, Mapping)
            else None
        )
        if isinstance(iteration, int) and not isinstance(iteration, bool):
            nested_attempt = f"{attempt_id}/iteration-{iteration:04d}"
            owned_prefixes.add((
                "nodes",
                _safe_component("node", node_id),
                _safe_component("attempt", nested_attempt),
            ))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) <= 3
            or relative.parts[:3] not in owned_prefixes
        ):
            raise JournalRecoveryError(
                "typed publication winning attempt path is invalid"
            )
        attempts = node.get("attempts") if isinstance(node, Mapping) else None
        winners = (
            [
                attempt
                for attempt in attempts
                if isinstance(attempt, Mapping)
                and attempt.get("attempt_id") == attempt_id
                and attempt.get("state") == "succeeded"
            ]
            if isinstance(attempts, list)
            else []
        )
        if len(winners) != 1:
            raise JournalRecoveryError(
                "typed publication winning attempt is not corroborated"
            )
        if not archon:
            raise JournalRecoveryError(
                "typed publication language authority is invalid"
            )
        publication_ids.add(publication_id)
        descriptors.append(
            _JournaledTypedPublication(
                publication_id=publication_id,
                content_name=content_name,
                output_type=output_type,
                media_type=str(media_type),
                size_bytes=size_bytes,
                sha256=content_sha256,
                metadata_sha256=metadata_sha256,
                schema_fingerprint=schema_fingerprint,
                canonicalization_version=canonicalization_version,
                produced_at=produced_at,
                session_id=session_id,
                run_id=run_id,
                node_id=node_id,
                attempt_id=attempt_id,
                relative_path=relative_path,
            )
        )
    descriptors_by_node: dict[str, list[_JournaledTypedPublication]] = {}
    for descriptor in descriptors:
        descriptors_by_node.setdefault(descriptor.node_id, []).append(descriptor)
    if set(descriptors_by_node) - set(requirements):
        raise JournalRecoveryError(
            "typed publication descriptor has no sealed output authority"
        )
    for node_id, requirement in requirements.items():
        matches = descriptors_by_node.get(node_id, [])
        if len(matches) != 1:
            raise JournalRecoveryError(
                "typed publication requires exactly one winning descriptor"
            )
        descriptor = matches[0]
        if (
            descriptor.output_type != requirement.output_type
            or descriptor.schema_fingerprint != requirement.schema_fingerprint
            or descriptor.canonicalization_version
            != requirement.canonicalization_version
        ):
            raise JournalRecoveryError(
                "typed publication descriptor conflicts with sealed output authority"
            )
    return tuple(descriptors)


def _validate_typed_publication_metadata(
    directory: Path,
    projection: Mapping[str, object],
    *,
    migrate_legacy: bool,
) -> int:
    """Validate descriptor authority without opening versioned publication bodies."""
    declared_outputs = _sealed_typed_output_declarations(
        directory,
        projection,
    )
    migrated = (
        _migrate_legacy_typed_publication_descriptors(
            directory,
            projection,
        )
        if migrate_legacy
        else 0
    )
    _journaled_typed_publications(
        projection,
        declared_outputs,
    )
    return migrated


def _write_or_reuse_typed_approval_output(
    run_directory: Path,
    *,
    node_id: str,
    attempt_id: str,
    data: bytes,
) -> PurePosixPath:
    relative = PurePosixPath(
        "nodes",
        _safe_component("node", node_id),
        _safe_component("attempt", attempt_id),
        "output.md",
    )
    try:
        output_path = write_archon_output_exclusive(
            run_directory,
            node_id=node_id,
            attempt_id=attempt_id,
            filename="output.md",
            data=data,
        )
    except ArchonOutputIntegrityError:
        existing = _read_descriptor_relative(
            run_directory,
            relative.as_posix(),
            size_bytes=len(data),
        )
        if not hmac.compare_digest(_sha256(existing), _sha256(data)):
            raise ArchonOutputIntegrityError(
                "typed approval output source identity changed"
            )
        return relative
    return PurePosixPath(output_path.relative_to(run_directory).as_posix())


def _canonical_typed_publication_artifact(
    artifacts: tuple[ArtifactRef, ...],
    candidate: TypedPublicationCandidate,
) -> ArtifactRef:
    same_path = [
        artifact
        for artifact in artifacts
        if artifact.relative_path == candidate.attempt_relative_path
    ]
    matching = [
        artifact
        for artifact in same_path
        if artifact.media_type == candidate.media_type
        and artifact.size_bytes == candidate.size_bytes
        and artifact.sha256 == candidate.sha256
    ]
    if len(matching) != 1:
        raise ArchonOutputIntegrityError(
            "typed publication candidate does not match one executor artifact"
        )
    if len(same_path) != 1:
        raise ArchonOutputIntegrityError(
            "typed publication artifacts contain a conflicting same-path descriptor"
        )
    return matching[0]


def _file_ends_with_newline(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) == b"\n"
    except OSError:
        return False


class RunStore:
    """Profile-scoped workflow state and sole run-creation authority."""

    def __init__(
        self,
        hermes_home: str | Path,
        *,
        max_input_bytes: int = 64 * 1024 * 1024,
        max_executing_runs: int = 4,
        max_queued_runs: int = 100,
        max_paused_runs: int = 100,
        max_nonterminal_runs: int = 200,
        max_start_requests_per_minute: int = 60,
        max_total_workers: int = 4,
        max_run_bytes: int = 512 * 1024 * 1024,
        max_profile_bytes: int = 2 * 1024 * 1024 * 1024,
        max_journal_bytes: int | None = None,
        lease_clock: Callable[[], LeaseClockSample] = sample_lease_clock,
    ) -> None:
        self.hermes_home = Path(hermes_home).resolve()
        self.root = self.hermes_home / "workflows"
        self.runs_root = self.root / "runs"
        self.staging_root = self.root / ".staging"
        self.quarantine_root = self.root / ".quarantine"
        self.locks_root = self.root / ".locks"
        self.database = self.root / "admission.sqlite3"
        self.authority_marker = self.root / ".admission-authority.json"
        self.repair_marker = self.root / ".repair-required.json"
        self.admission_lock = self.root / ".admission.lock"
        self.max_input_bytes = max_input_bytes
        self.max_run_bytes = max_run_bytes
        self.max_profile_bytes = max_profile_bytes
        self._lease_clock = lease_clock
        self.max_journal_bytes = (
            max_journal_bytes
            if max_journal_bytes is not None
            else max(1, max_run_bytes // 2)
        )
        self.limits = {
            "executing": max_executing_runs,
            "queued": max_queued_runs,
            "paused": max_paused_runs,
            "nonterminal": max_nonterminal_runs,
            "rate": max_start_requests_per_minute,
            "workers": max_total_workers,
        }
        self._init_lock = threading.Lock()
        self._admission_gate = threading.RLock()
        self._notification_repair_timeout_lock = threading.Lock()
        self._notification_repair_timeout_run_id: str | None = None
        self._notification_repair_timeout_count = 0
        self._scheduled_authorization_identity = object()
        self._admission_open = True
        self._initialized = False
        self._initialize()

    def _initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            self.runs_root.mkdir(parents=True, exist_ok=True)
            self.staging_root.mkdir(parents=True, exist_ok=True)
            self.quarantine_root.mkdir(parents=True, exist_ok=True)
            self.locks_root.mkdir(parents=True, exist_ok=True)
            with workflow_lock(self.admission_lock):
                database_existed = self.database.exists()
                database_was_empty = (
                    database_existed and self.database.stat().st_size == 0
                )
                evidence_existed = any(
                    path.is_dir()
                    for workflow in self.runs_root.iterdir()
                    if workflow.is_dir()
                    for path in workflow.iterdir()
                )
                index_damage = None
                try:
                    self._create_or_migrate_schema()
                except sqlite3.DatabaseError:
                    self._preserve_damaged_index()
                    self._create_or_migrate_schema()
                    index_damage = "index_corrupt"
                generation_damage = self._establish_index_generation(
                    database_existed=database_existed,
                    database_was_empty=database_was_empty,
                    evidence_existed=evidence_existed,
                )
                if index_damage or generation_damage:
                    self._mark_repair_required(index_damage or generation_damage)
                    with self._connect() as connection:
                        self._record_repair_event(
                            connection,
                            reason_code=(
                                index_damage or generation_damage or "index_unknown"
                            ),
                            outcome="index_recreated",
                        )
                self._reconcile_admission()
                self._reconcile_worker_claims()
            self._initialized = True

    def _create_or_migrate_schema(self) -> None:
        with self._connect() as connection:
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if schema_version > _STORE_SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    "admission index schema is newer than this runtime"
                )
            connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        workflow_name TEXT NOT NULL,
                        trigger_source TEXT NOT NULL,
                        idempotency_namespace_digest TEXT NOT NULL,
                        idempotency_digest TEXT NOT NULL,
                        start_digest TEXT NOT NULL,
                        concurrency_key TEXT NOT NULL,
                        concurrency_policy TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        status TEXT NOT NULL,
                        scheduled_at TEXT,
                        queue_position INTEGER,
                        queue_sequence INTEGER,
                        blocked_by_run_id TEXT,
                        pause_lane_policy TEXT NOT NULL DEFAULT 'hold',
                        lane_state TEXT NOT NULL DEFAULT 'released',
                        run_directory TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        archived_at TEXT,
                        restored_to_history INTEGER NOT NULL DEFAULT 0,
                        admission_state TEXT NOT NULL DEFAULT 'published',
                        desired_status TEXT,
                        staging_directory TEXT,
                        operator_scope_digest TEXT,
                        provenance_json TEXT,
                        execution_mode TEXT NOT NULL DEFAULT 'foreground',
                        foreground_owner_id TEXT,
                        foreground_lease_expires_at TEXT,
                        foreground_epoch INTEGER,
                        foreground_boot_id TEXT,
                        foreground_heartbeat_monotonic REAL,
                        foreground_lease_seconds REAL,
                        projection_schema_version INTEGER NOT NULL DEFAULT 1,
                        projection_state_version INTEGER,
                        projection_sha256 TEXT,
                        journal_sequence INTEGER,
                        journal_sha256 TEXT,
                        integrity_verified_at TEXT,
                        UNIQUE(
                            idempotency_namespace_digest,
                            workflow_name,
                            idempotency_digest
                        )
                    );
                    CREATE INDEX IF NOT EXISTS runs_concurrency
                    ON runs(workflow_name, concurrency_key, status);
                    CREATE TABLE IF NOT EXISTS admission_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        run_id TEXT,
                        reason_code TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE TABLE IF NOT EXISTS worker_claims (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        lease_expires_at TEXT NOT NULL,
                        UNIQUE(run_id, node_id)
                    );
                    CREATE INDEX IF NOT EXISTS worker_claims_lease
                    ON worker_claims(lease_expires_at);
                    CREATE TABLE IF NOT EXISTS attempt_journal_reserves (
                        attempt_id TEXT PRIMARY KEY REFERENCES worker_claims(attempt_id)
                            ON DELETE CASCADE,
                        run_id TEXT NOT NULL,
                        terminal_reserve_bytes INTEGER NOT NULL,
                        projection_limit_bytes INTEGER NOT NULL,
                        consumed_bytes INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS attempt_journal_reserves_run
                    ON attempt_journal_reserves(run_id);
                    CREATE TABLE IF NOT EXISTS obligation_journal_reserves (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(run_id)
                            ON DELETE CASCADE,
                        terminal_reserve_bytes INTEGER NOT NULL,
                        projection_limit_bytes INTEGER NOT NULL,
                        consumed_bytes INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS obligation_journal_reserves_run
                    ON obligation_journal_reserves(run_id);
                    CREATE TABLE IF NOT EXISTS session_recovery_selection_authority (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(run_id)
                            ON DELETE CASCADE,
                        authority_json TEXT NOT NULL,
                        authority_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS session_recovery_selection_run
                    ON session_recovery_selection_authority(run_id);
                    CREATE TABLE IF NOT EXISTS session_registry_winner_authority (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(run_id)
                            ON DELETE CASCADE,
                        authority_json TEXT NOT NULL,
                        authority_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS session_registry_winner_run
                    ON session_registry_winner_authority(run_id);
                    CREATE TABLE IF NOT EXISTS store_repair_state (
                        run_id TEXT NOT NULL,
                        attempt_id TEXT NOT NULL,
                        reason_code TEXT NOT NULL,
                        detected_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY(run_id, attempt_id)
                    );
                    CREATE TABLE IF NOT EXISTS store_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS repair_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        detected_at TEXT NOT NULL,
                        reason_code TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        run_id TEXT,
                        source_path TEXT,
                        preserved_path TEXT,
                        projection_sha256 TEXT,
                        journal_sha256 TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS repair_events_run_reason_sequence
                    ON repair_events(run_id, reason_code, sequence DESC);
                    CREATE INDEX IF NOT EXISTS repair_events_revalidation_sequence
                    ON repair_events(sequence, run_id, reason_code)
                    WHERE outcome='repair_required'
                    AND reason_code IN (
                        'legacy_effect_policy_uncorroborated',
                        'run_evidence_uncorroborated'
                    );
                    CREATE TABLE IF NOT EXISTS cleanup_previews (
                        token_digest TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        preview_digest TEXT NOT NULL,
                        candidates_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        authority_binding_digest TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS cleanup_history (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        token_digest TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        quarantine_path TEXT NOT NULL,
                        files INTEGER NOT NULL,
                        bytes INTEGER NOT NULL,
                        outcome TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    """
                )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)")
            }
            migrations = {
                "admission_state": (
                    "ALTER TABLE runs ADD COLUMN admission_state TEXT "
                    "NOT NULL DEFAULT 'published'"
                ),
                "desired_status": ("ALTER TABLE runs ADD COLUMN desired_status TEXT"),
                "scheduled_at": ("ALTER TABLE runs ADD COLUMN scheduled_at TEXT"),
                "staging_directory": (
                    "ALTER TABLE runs ADD COLUMN staging_directory TEXT"
                ),
                "operator_scope_digest": (
                    "ALTER TABLE runs ADD COLUMN operator_scope_digest TEXT"
                ),
                "provenance_json": ("ALTER TABLE runs ADD COLUMN provenance_json TEXT"),
                "projection_schema_version": (
                    "ALTER TABLE runs ADD COLUMN projection_schema_version "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "projection_state_version": (
                    "ALTER TABLE runs ADD COLUMN projection_state_version INTEGER"
                ),
                "projection_sha256": (
                    "ALTER TABLE runs ADD COLUMN projection_sha256 TEXT"
                ),
                "journal_sequence": (
                    "ALTER TABLE runs ADD COLUMN journal_sequence INTEGER"
                ),
                "journal_sha256": ("ALTER TABLE runs ADD COLUMN journal_sha256 TEXT"),
                "integrity_verified_at": (
                    "ALTER TABLE runs ADD COLUMN integrity_verified_at TEXT"
                ),
                "execution_mode": (
                    "ALTER TABLE runs ADD COLUMN execution_mode TEXT "
                    "NOT NULL DEFAULT 'foreground'"
                ),
                "foreground_owner_id": (
                    "ALTER TABLE runs ADD COLUMN foreground_owner_id TEXT"
                ),
                "foreground_lease_expires_at": (
                    "ALTER TABLE runs ADD COLUMN foreground_lease_expires_at TEXT"
                ),
                "foreground_epoch": (
                    "ALTER TABLE runs ADD COLUMN foreground_epoch INTEGER"
                ),
                "foreground_boot_id": (
                    "ALTER TABLE runs ADD COLUMN foreground_boot_id TEXT"
                ),
                "foreground_heartbeat_monotonic": (
                    "ALTER TABLE runs ADD COLUMN foreground_heartbeat_monotonic REAL"
                ),
                "foreground_lease_seconds": (
                    "ALTER TABLE runs ADD COLUMN foreground_lease_seconds REAL"
                ),
                "queue_sequence": (
                    "ALTER TABLE runs ADD COLUMN queue_sequence INTEGER"
                ),
                "pause_lane_policy": (
                    "ALTER TABLE runs ADD COLUMN pause_lane_policy TEXT "
                    "NOT NULL DEFAULT 'hold'"
                ),
                "lane_state": (
                    "ALTER TABLE runs ADD COLUMN lane_state TEXT "
                    "NOT NULL DEFAULT 'released'"
                ),
                "archived_at": ("ALTER TABLE runs ADD COLUMN archived_at TEXT"),
                "restored_to_history": (
                    "ALTER TABLE runs ADD COLUMN restored_to_history INTEGER "
                    "NOT NULL DEFAULT 0"
                ),
            }
            for name, statement in migrations.items():
                if name not in columns:
                    connection.execute(statement)
            self._migrate_runs_idempotency_namespace(connection)
            if schema_version < 14:
                self._migrate_scheduled_at(connection)
            self._migrate_runnable_admission(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_coordinator_scan "
                "ON runs(created_at, run_id) "
                "WHERE admission_state='published' "
                "AND status IN ('queued','running','waiting_retry') "
                "AND execution_mode IN ('background','foreground')"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_view_keyset "
                "ON runs(operator_scope_digest, updated_at DESC, run_id DESC) "
                "WHERE admission_state='published'"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_view_filter "
                "ON runs(archived_at, restored_to_history, status, "
                "updated_at DESC, run_id DESC) "
                "WHERE admission_state='published'"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_scheduled_queue "
                "ON runs(scheduled_at, created_at, run_id) "
                "WHERE admission_state='published' AND status='queued' "
                "AND scheduled_at IS NOT NULL"
            )
            cleanup_preview_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(cleanup_previews)")
            }
            if "authority_binding_digest" not in cleanup_preview_columns:
                connection.execute(
                    "ALTER TABLE cleanup_previews "
                    "ADD COLUMN authority_binding_digest TEXT"
                )
            from plugins.workflow.coordinator_store import install_coordinator_schema
            from plugins.workflow.notifications import install_notification_schema

            install_coordinator_schema(connection)
            install_notification_schema(connection)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)")
            }
            required_columns = {
                "run_id",
                "workflow_name",
                "trigger_source",
                "idempotency_namespace_digest",
                "idempotency_digest",
                "start_digest",
                "concurrency_key",
                "concurrency_policy",
                "disposition",
                "status",
                "scheduled_at",
                "queue_position",
                "queue_sequence",
                "blocked_by_run_id",
                "pause_lane_policy",
                "lane_state",
                "run_directory",
                "created_at",
                "updated_at",
                "archived_at",
                "restored_to_history",
                "admission_state",
                "desired_status",
                "staging_directory",
                "operator_scope_digest",
                "provenance_json",
                "projection_schema_version",
                "projection_state_version",
                "projection_sha256",
                "journal_sequence",
                "journal_sha256",
                "integrity_verified_at",
                "execution_mode",
                "foreground_owner_id",
                "foreground_lease_expires_at",
                "foreground_epoch",
                "foreground_boot_id",
                "foreground_heartbeat_monotonic",
                "foreground_lease_seconds",
            }
            if not required_columns <= columns:
                missing = ", ".join(sorted(required_columns - columns))
                raise sqlite3.DatabaseError(
                    f"admission index schema is incomplete: {missing}"
                )
            connection.execute(f"PRAGMA user_version={_STORE_SCHEMA_VERSION}")

    def _fresh_coordinator_lease(
        self,
        connection: sqlite3.Connection,
        sample: LeaseClockSample | None = None,
    ):
        from plugins.workflow.coordinator_store import CoordinatorStore

        row = connection.execute(
            "SELECT * FROM coordinator_lease WHERE singleton=1"
        ).fetchone()
        if row is None:
            return None
        lease = CoordinatorStore._lease(row)
        observed = sample or self._lease_clock()
        return lease if lease_is_fresh(lease, observed) else None

    @staticmethod
    def _foreground_lease(row: Mapping[str, object]) -> ForegroundExecutionLease | None:
        def value(name: str) -> object:
            try:
                return row[name]
            except (IndexError, KeyError):
                return None

        owner_id = value("foreground_owner_id")
        epoch = value("foreground_epoch")
        expires_at = value("foreground_lease_expires_at")
        if (
            not isinstance(owner_id, str)
            or not owner_id
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or not isinstance(expires_at, str)
        ):
            return None
        return ForegroundExecutionLease(
            owner_id=owner_id,
            epoch=epoch,
            lease_expires_at=datetime.fromisoformat(expires_at),
            boot_id=(
                str(value("foreground_boot_id"))
                if value("foreground_boot_id") is not None
                else None
            ),
            heartbeat_monotonic=(
                float(value("foreground_heartbeat_monotonic"))
                if value("foreground_heartbeat_monotonic") is not None
                else None
            ),
            lease_seconds=(
                float(value("foreground_lease_seconds"))
                if value("foreground_lease_seconds") is not None
                else None
            ),
        )

    def _foreground_sample(self, now: datetime) -> LeaseClockSample:
        observed = self._lease_clock()
        return LeaseClockSample(
            now.astimezone(timezone.utc),
            observed.monotonic_now,
            observed.boot_id,
        )

    def assert_execution_fence(
        self,
        connection: sqlite3.Connection,
        fence: ExecutionFence,
        now: LeaseClockSample | None = None,
    ) -> None:
        """Reject a coordinator mutation unless its exact epoch remains fresh."""
        lease = self._fresh_coordinator_lease(connection, now)
        if (
            lease is None
            or lease.owner_id != fence.owner_id
            or lease.epoch != fence.owner_epoch
        ):
            raise RuntimeError("stale coordinator execution fence")

    def _assert_claim_execution_fence(
        self, claim: NodeClaim, now: LeaseClockSample | None = None
    ) -> None:
        if claim.execution_fence is None:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self.assert_execution_fence(
                    connection, claim.execution_fence, now
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _execution_fence_transaction(
        self,
        fence: ExecutionFence | None,
        now: LeaseClockSample | None = None,
    ):
        if fence is None:
            yield None
            return
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.assert_execution_fence(connection, fence, now)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate_runs_idempotency_namespace(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(runs)")
        ]
        table_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        table_sql = "".join(str(table_sql_row["sql"] or "").lower().split())
        expected_unique = (
            "unique(idempotency_namespace_digest,workflow_name,"
            "idempotency_digest)"
        )
        if "idempotency_namespace_digest" in columns and expected_unique in table_sql:
            return

        target_columns = (
            "run_id",
            "workflow_name",
            "trigger_source",
            "idempotency_namespace_digest",
            "idempotency_digest",
            "start_digest",
            "concurrency_key",
            "concurrency_policy",
            "disposition",
            "status",
            "scheduled_at",
            "queue_position",
            "queue_sequence",
            "blocked_by_run_id",
            "pause_lane_policy",
            "lane_state",
            "run_directory",
            "created_at",
            "updated_at",
            "archived_at",
            "restored_to_history",
            "admission_state",
            "desired_status",
            "staging_directory",
            "operator_scope_digest",
            "provenance_json",
            "execution_mode",
            "foreground_owner_id",
            "foreground_lease_expires_at",
            "foreground_epoch",
            "foreground_boot_id",
            "foreground_heartbeat_monotonic",
            "foreground_lease_seconds",
            "projection_schema_version",
            "projection_state_version",
            "projection_sha256",
            "journal_sequence",
            "journal_sha256",
            "integrity_verified_at",
        )
        source_columns = set(columns)
        placeholders = ", ".join("?" for _ in target_columns)
        column_sql = ", ".join(target_columns)

        def source_generation() -> str | None:
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE key='generation'"
            ).fetchone()
            return str(row["value"]) if row is not None else None

        def source_signature(rows: Iterable[sqlite3.Row]) -> tuple[tuple[object, ...], ...]:
            return tuple(tuple(row[name] for name in columns) for row in rows)

        def evidence_signature(
            directory: Path,
        ) -> tuple[bool, str | None, str | None]:
            try:
                directory.stat()
            except FileNotFoundError:
                return False, None, None
            digests = []
            for name in ("run.json", "events.jsonl"):
                try:
                    digests.append(_sha256((directory / name).read_bytes()))
                except FileNotFoundError:
                    digests.append(None)
            return True, digests[0], digests[1]

        for _attempt in range(3):
            locks = ExitStack()
            transaction_started = False
            try:
                candidate_rows = connection.execute(
                    "SELECT run_id FROM runs ORDER BY run_id"
                ).fetchall()
                candidate_ids = tuple(str(row["run_id"]) for row in candidate_rows)
                for run_id in candidate_ids:
                    locks.enter_context(workflow_lock(self._run_lock_path(run_id)))

                source_rows = connection.execute(
                    "SELECT * FROM runs ORDER BY run_id"
                ).fetchall()
                if tuple(str(row["run_id"]) for row in source_rows) != candidate_ids:
                    continue
                pinned_source = source_signature(source_rows)
                pinned_generation = source_generation()
                pinned_evidence: dict[
                    str,
                    tuple[
                        dict[str, object] | None,
                        Path,
                        bool,
                        str | None,
                        str | None,
                    ],
                ] = {}
                for row in source_rows:
                    run_id = str(row["run_id"])
                    directory = Path(row["run_directory"])
                    try:
                        projection, projection_sha256, journal_sha256 = (
                            self._corroborate_run_evidence_locked(
                                directory, run_id=run_id
                            )
                        )
                        if projection_sha256 is None or journal_sha256 is None:
                            raise JournalRecoveryError(
                                "run evidence digests are unavailable"
                            )
                        RunStore._start_digest_from_projection(projection)
                        self._scheduled_at_from_projection(projection)
                    except (
                        OSError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                        JournalRecoveryError,
                    ) as exc:
                        if row["admission_state"] != "reserved":
                            raise sqlite3.DatabaseError(
                                "runs namespace migration evidence is uncorroborated"
                            ) from exc
                        try:
                            directory.stat()
                        except FileNotFoundError:
                            pass
                        except OSError as directory_error:
                            raise sqlite3.DatabaseError(
                                "runs namespace migration evidence is unreadable"
                            ) from directory_error
                        else:
                            raise sqlite3.DatabaseError(
                                "runs namespace migration evidence is uncorroborated"
                            ) from exc
                        # Initialization holds the admission lock, so no publisher
                        # can promote staging evidence while this genuinely
                        # incomplete reserved row is classified under its run lock.
                        projection = None
                        (
                            directory_present,
                            projection_sha256,
                            journal_sha256,
                        ) = evidence_signature(directory)
                        if directory_present:
                            raise sqlite3.DatabaseError(
                                "runs namespace migration evidence is uncorroborated"
                            ) from exc
                    else:
                        directory_present = True
                    pinned_evidence[run_id] = (
                        projection,
                        directory,
                        directory_present,
                        projection_sha256,
                        journal_sha256,
                    )

                connection.execute("BEGIN IMMEDIATE")
                transaction_started = True
                current_rows = connection.execute(
                    "SELECT * FROM runs ORDER BY run_id"
                ).fetchall()
                evidence_still_pinned = all(
                    evidence_signature(directory)
                    == (
                        directory_present,
                        projection_sha256,
                        journal_sha256,
                    )
                    for (
                        _projection,
                        directory,
                        directory_present,
                        projection_sha256,
                        journal_sha256,
                    ) in pinned_evidence.values()
                )
                if (
                    source_signature(current_rows) != pinned_source
                    or source_generation() != pinned_generation
                    or not evidence_still_pinned
                ):
                    connection.rollback()
                    transaction_started = False
                    continue

                connection.execute("DROP TABLE IF EXISTS runs_namespace_migration")
                connection.execute(
                    """
                    CREATE TABLE runs_namespace_migration (
                        run_id TEXT PRIMARY KEY,
                        workflow_name TEXT NOT NULL,
                        trigger_source TEXT NOT NULL,
                        idempotency_namespace_digest TEXT NOT NULL,
                        idempotency_digest TEXT NOT NULL,
                        start_digest TEXT NOT NULL,
                        concurrency_key TEXT NOT NULL,
                        concurrency_policy TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        status TEXT NOT NULL,
                        scheduled_at TEXT,
                        queue_position INTEGER,
                        queue_sequence INTEGER,
                        blocked_by_run_id TEXT,
                        pause_lane_policy TEXT NOT NULL DEFAULT 'hold',
                        lane_state TEXT NOT NULL DEFAULT 'released',
                        run_directory TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        archived_at TEXT,
                        restored_to_history INTEGER NOT NULL DEFAULT 0,
                        admission_state TEXT NOT NULL DEFAULT 'published',
                        desired_status TEXT,
                        staging_directory TEXT,
                        operator_scope_digest TEXT,
                        provenance_json TEXT,
                        execution_mode TEXT NOT NULL DEFAULT 'foreground',
                        foreground_owner_id TEXT,
                        foreground_lease_expires_at TEXT,
                        foreground_epoch INTEGER,
                        foreground_boot_id TEXT,
                        foreground_heartbeat_monotonic REAL,
                        foreground_lease_seconds REAL,
                        projection_schema_version INTEGER NOT NULL DEFAULT 1,
                        projection_state_version INTEGER,
                        projection_sha256 TEXT,
                        journal_sequence INTEGER,
                        journal_sha256 TEXT,
                        integrity_verified_at TEXT,
                        UNIQUE(
                            idempotency_namespace_digest,
                            workflow_name,
                            idempotency_digest
                        )
                    )
                    """
                )
                for row in source_rows:
                    namespace_digest = (
                        row["idempotency_namespace_digest"]
                        if "idempotency_namespace_digest" in source_columns
                        and row["idempotency_namespace_digest"]
                        else _sha256(
                            f"profile-local:{row['trigger_source']}".encode()
                        )
                    )
                    projection = pinned_evidence[str(row["run_id"])][0]
                    if projection is None:
                        start_digest = row["start_digest"]
                        scheduled_at = None
                    else:
                        start_digest = RunStore._start_digest_from_projection(
                            projection
                        )
                        scheduled_at = self._scheduled_at_from_projection(projection)
                    values = [
                        namespace_digest
                        if name == "idempotency_namespace_digest"
                        else start_digest
                        if name == "start_digest"
                        else scheduled_at
                        if name == "scheduled_at"
                        else row[name]
                        for name in target_columns
                    ]
                    connection.execute(
                        f"INSERT INTO runs_namespace_migration ({column_sql}) "
                        f"VALUES ({placeholders})",
                        values,
                    )
                copied = connection.execute(
                    "SELECT COUNT(*) FROM runs_namespace_migration"
                ).fetchone()[0]
                if copied != len(source_rows):
                    raise sqlite3.DatabaseError("runs namespace migration lost rows")
                connection.execute("DROP TABLE runs")
                connection.execute(
                    "ALTER TABLE runs_namespace_migration RENAME TO runs"
                )
                connection.execute(
                    "CREATE INDEX runs_concurrency "
                    "ON runs(workflow_name, concurrency_key, status)"
                )
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise sqlite3.DatabaseError(
                        "runs namespace migration violated foreign keys"
                    )
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError(
                        "runs namespace migration failed integrity check"
                    )
                connection.commit()
                transaction_started = False
                return
            except BaseException:
                if transaction_started:
                    connection.rollback()
                raise
            finally:
                locks.close()
        raise sqlite3.DatabaseError(
            "runs namespace migration source changed during migration"
        )

    def _migrate_scheduled_at(self, connection: sqlite3.Connection) -> None:
        """Backfill the v14 query column from corroborated durable evidence."""
        rows = connection.execute(
            "SELECT run_id, run_directory, admission_state FROM runs "
            "WHERE admission_state IN ('published','reserved') ORDER BY run_id"
        ).fetchall()
        for row in rows:
            try:
                projection, _, _ = self._corroborate_run_evidence(
                    Path(row["run_directory"]), run_id=str(row["run_id"])
                )
                scheduled_at = self._scheduled_at_from_projection(projection)
            except (
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                JournalRecoveryError,
            ):
                continue
            connection.execute(
                "UPDATE runs SET scheduled_at=? WHERE run_id=?",
                (scheduled_at, row["run_id"]),
            )

    @staticmethod
    def _migrate_runnable_admission(connection: sqlite3.Connection) -> None:
        """Install one durable FIFO counter and conservative legacy lane state."""
        migrated = connection.execute(
            "SELECT value FROM store_metadata WHERE key='queue_sequence'"
        ).fetchone()
        if migrated is not None:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            maximum = int(
                connection.execute(
                    "SELECT COALESCE(MAX(queue_sequence), 0) FROM runs"
                ).fetchone()[0]
            )
            queued = connection.execute(
                "SELECT run_id FROM runs WHERE status='queued' "
                "AND queue_sequence IS NULL ORDER BY created_at, run_id"
            ).fetchall()
            for row in queued:
                maximum += 1
                connection.execute(
                    "UPDATE runs SET queue_sequence=?, queue_position=? "
                    "WHERE run_id=?",
                    (maximum, maximum, row["run_id"]),
                )
            connection.execute(
                "UPDATE runs SET lane_state=CASE "
                "WHEN status='running' THEN 'held' "
                "WHEN status='paused' AND pause_lane_policy='hold' THEN 'held' "
                "WHEN status='interrupted' THEN 'held' "
                "ELSE 'released' END"
            )
            connection.execute(
                "INSERT INTO store_metadata (key, value) VALUES "
                "('queue_sequence', ?)",
                (str(maximum),),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _preserve_damaged_index(self) -> None:
        preserved_root = self.quarantine_root / f"admission-index-{uuid.uuid4().hex}"
        preserved_root.mkdir(parents=True, exist_ok=False)
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.database}{suffix}")
            if source.exists():
                _durable_replace(source, preserved_root / source.name)

    def _establish_index_generation(
        self,
        *,
        database_existed: bool,
        database_was_empty: bool,
        evidence_existed: bool,
    ) -> str | None:
        marker = None
        marker_existed = self.authority_marker.exists()
        marker_unreadable = False
        try:
            candidate = json.loads(self.authority_marker.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and isinstance(
                candidate.get("generation"), str
            ):
                marker = candidate
        except (OSError, json.JSONDecodeError):
            marker_unreadable = marker_existed
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE key='generation'"
            ).fetchone()
            generation_existed = row is not None
            if row is None:
                candidate_generation = uuid.uuid4().hex
                connection.execute(
                    "INSERT OR IGNORE INTO store_metadata (key, value) "
                    "VALUES ('generation', ?)",
                    (candidate_generation,),
                )
                generation = str(
                    connection.execute(
                        "SELECT value FROM store_metadata WHERE key='generation'"
                    ).fetchone()["value"]
                )
            else:
                generation = str(row["value"])
        if marker is None:
            _atomic_json(
                self.authority_marker,
                {"schema_version": 1, "generation": generation},
            )
            if marker_unreadable:
                return "authority_marker_corrupt"
            if generation_existed:
                return "authority_marker_missing"
            if evidence_existed and not database_existed:
                return "index_missing"
            if evidence_existed and database_was_empty:
                return "index_empty"
            return None
        if marker["generation"] != generation:
            return "index_generation_mismatch"
        return None

    def _mark_repair_required(
        self, reason_code: str, *, run_id: str | None = None
    ) -> None:
        reasons: list[dict[str, str | None]] = []
        detected_at = _utc_now()
        try:
            existing = json.loads(self.repair_marker.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                detected_at = str(existing.get("detected_at") or detected_at)
                existing_reasons = existing.get("reasons")
                if isinstance(existing_reasons, list):
                    reasons = [
                        dict(item)
                        for item in existing_reasons
                        if isinstance(item, dict)
                    ]
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        reason = {"reason_code": reason_code, "run_id": run_id}
        if reason not in reasons:
            reasons.append(reason)
        _atomic_json(
            self.repair_marker,
            {
                "schema_version": 1,
                "status": "repair_required",
                "detected_at": detected_at,
                "reasons": reasons,
            },
        )

    @staticmethod
    def _record_repair_event(
        connection: sqlite3.Connection,
        *,
        reason_code: str,
        outcome: str,
        run_id: str | None = None,
        source_path: Path | None = None,
        preserved_path: Path | None = None,
        projection_sha256: str | None = None,
        journal_sha256: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO repair_events ("
            "detected_at, reason_code, outcome, run_id, source_path, "
            "preserved_path, projection_sha256, journal_sha256, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _utc_now(),
                reason_code,
                outcome,
                run_id,
                str(source_path) if source_path else None,
                str(preserved_path) if preserved_path else None,
                projection_sha256,
                journal_sha256,
                json.dumps(_sanitize(dict(payload or {})), sort_keys=True),
            ),
        )

    def _transition_run_repair(
        self,
        reason_code: str,
        *,
        run_id: str,
        outcome: str,
    ) -> bool:
        """Append a changed run-scoped repair state without degrading the store."""
        if reason_code not in _RUN_SCOPED_REPAIR_REASONS:
            raise ValueError("reason_code is not run-scoped")
        if outcome not in {"repair_required", "repair_verified"}:
            raise ValueError("invalid run repair outcome")
        with self._connect() as connection:
            latest = connection.execute(
                "SELECT outcome FROM repair_events WHERE run_id=? AND reason_code=? "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id, reason_code),
            ).fetchone()
            if latest is not None and str(latest["outcome"]) == outcome:
                return False
            if outcome == "repair_verified" and latest is None:
                return False
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                "SELECT outcome FROM repair_events WHERE run_id=? AND reason_code=? "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id, reason_code),
            ).fetchone()
            if latest is not None and str(latest["outcome"]) == outcome:
                return False
            if outcome == "repair_verified" and latest is None:
                return False
            self._record_repair_event(
                connection,
                reason_code=reason_code,
                outcome=outcome,
                run_id=run_id,
            )
        return True

    def _active_run_repair_reasons(self, run_id: str) -> tuple[str, ...]:
        """Return active run-local repair reasons from their latest transitions."""
        placeholders = ",".join("?" for _ in _RUN_SCOPED_REPAIR_REASON_ORDER)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT events.reason_code FROM repair_events AS events "
                f"WHERE events.run_id=? AND events.reason_code IN ({placeholders}) "
                "AND events.sequence=(SELECT MAX(latest.sequence) "
                "FROM repair_events AS latest WHERE latest.run_id=events.run_id "
                "AND latest.reason_code=events.reason_code) "
                "AND events.outcome='repair_required' ORDER BY events.reason_code",
                (run_id, *_RUN_SCOPED_REPAIR_REASON_ORDER),
            ).fetchall()
        return tuple(str(row["reason_code"]) for row in rows)

    def repair_revalidation_candidate(
        self,
        *,
        after: int | None,
    ) -> tuple[dict[str, object] | None, int | None, bool]:
        """Return one latest-active evidence repair after the keyset cursor."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT repair.sequence, repair.run_id, repair.reason_code "
                "FROM repair_events AS repair "
                "INDEXED BY repair_events_revalidation_sequence "
                "JOIN runs ON runs.run_id=repair.run_id "
                "WHERE repair.sequence>? "
                f"AND repair.reason_code IN ({_UNATTENDED_REVALIDATION_REASON_SQL}) "
                "AND repair.outcome='repair_required' "
                "AND repair.sequence=(SELECT MAX(latest.sequence) "
                "FROM repair_events AS latest "
                "WHERE latest.run_id=repair.run_id "
                "AND latest.reason_code=repair.reason_code) "
                "AND runs.admission_state='published' "
                "AND runs.status IN ('queued','running','waiting_retry') "
                "AND runs.execution_mode IN ('background','foreground') "
                "ORDER BY repair.sequence LIMIT 1",
                (after or 0,),
            ).fetchone()
        if row is None:
            return None, after, True
        sequence = int(row["sequence"])
        return (
            {
                "sequence": sequence,
                "run_id": str(row["run_id"]),
                "reason_code": str(row["reason_code"]),
            },
            sequence,
            False,
        )

    @staticmethod
    def _legacy_effect_policy_nodes(
        directory: Path,
        projection: Mapping[str, object],
        *,
        policy_data: bytes | None = None,
    ) -> list[str]:
        policy_bytes = (
            policy_data
            if policy_data is not None
            else (directory / "policy.yaml").read_bytes()
        )
        expected_digest = projection.get("policy_digest")
        if not isinstance(expected_digest, str) or not expected_digest:
            raise JournalRecoveryError("legacy workflow policy digest is missing")
        if not hmac.compare_digest(_sha256(policy_bytes), expected_digest):
            raise JournalRecoveryError("legacy workflow policy digest mismatch")
        document = yaml.safe_load(policy_bytes) or {}
        if not isinstance(document, Mapping):
            raise JournalRecoveryError("legacy workflow policy is malformed")
        outward = document.get("outward_action_nodes", [])
        if not isinstance(outward, list) or any(
            not isinstance(candidate, str) for candidate in outward
        ):
            raise JournalRecoveryError("legacy outward-action policy is malformed")
        return outward

    @staticmethod
    def _read_bounded_repair_evidence(path: Path, *, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise StorageQuotaError("run repair evidence exceeds run quota")
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise StorageQuotaError("run repair evidence exceeds configured quota")
        return data

    def _normalize_repair_journal_snapshot(
        self,
        directory: Path,
        *,
        run_id: str,
        journal_data: bytes,
        max_bytes: int,
    ) -> bytes:
        """Preserve live torn-tail recovery while replaying immutable bytes."""
        if not journal_data or journal_data.endswith(b"\n"):
            return journal_data
        torn_data = None
        try:
            self._rebuild_projection(
                directory,
                run_id=run_id,
                journal_data=journal_data,
            )
        except JournalRecoveryError as original_error:
            tail_offset = journal_data.rfind(b"\n") + 1
            if tail_offset <= 0:
                raise
            normalized = journal_data[:tail_offset]
            try:
                self._rebuild_projection(
                    directory,
                    run_id=run_id,
                    journal_data=normalized,
                )
            except JournalRecoveryError:
                raise original_error
            torn_data = journal_data[tail_offset:]
        else:
            normalized = journal_data + b"\n"
        if len(normalized) > max_bytes:
            raise StorageQuotaError("normalized journal exceeds configured quota")
        journal_path = directory / "events.jsonl"
        current = self._read_bounded_repair_evidence(
            journal_path,
            max_bytes=len(journal_data),
        )
        if current != journal_data:
            raise JournalRecoveryError("run journal changed during revalidation")
        if torn_data is not None:
            preserved = directory / f"events.jsonl.torn-{uuid.uuid4().hex}"
            _atomic_bytes(preserved, torn_data)
        _atomic_bytes(journal_path, normalized)
        return normalized

    def _adopt_rebuilt_repair_projection(
        self,
        directory: Path,
        *,
        run_id: str,
        projection_data: bytes,
        journal_data: bytes,
        policy_data: bytes | None,
    ) -> tuple[dict[str, object], str]:
        rebuilt = self._rebuild_projection(
            directory,
            run_id=run_id,
            journal_data=journal_data,
        )
        rebuilt_data = (
            json.dumps(
                rebuilt,
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        if (
            len(rebuilt_data) + len(journal_data) + len(policy_data or b"")
            > self.max_run_bytes
        ):
            raise StorageQuotaError("rebuilt projection exceeds run quota")
        projection_path = directory / "run.json"
        journal_path = directory / "events.jsonl"
        if (
            self._read_bounded_repair_evidence(
                projection_path,
                max_bytes=len(projection_data),
            )
            != projection_data
        ):
            raise JournalRecoveryError("run projection changed during revalidation")
        if (
            self._read_bounded_repair_evidence(
                journal_path,
                max_bytes=len(journal_data),
            )
            != journal_data
        ):
            raise JournalRecoveryError("run journal changed during revalidation")
        _atomic_bytes(projection_path, rebuilt_data)
        return rebuilt, _sha256(journal_data)

    def revalidate_run_repair(
        self,
        run_id: str,
        reason_code: str,
        *,
        lock_timeout_seconds: float = 0.05,
    ) -> bool:
        """Revalidate one repair within the coordinator's unattended budget."""
        if reason_code not in _UNATTENDED_REVALIDATION_REASONS:
            raise ValueError("repair reason is not eligible for unattended revalidation")
        if lock_timeout_seconds <= 0:
            raise ValueError("revalidation lock budget must be positive")
        try:
            directory = self.run_directory(run_id)
            with workflow_lock(
                self._run_lock_path(run_id),
                timeout_seconds=lock_timeout_seconds,
            ):
                if reason_code not in self._active_run_repair_reasons(run_id):
                    return False
                projection_path = directory / "run.json"
                journal_path = directory / "events.jsonl"
                remaining_bytes = self.max_run_bytes
                projection_data = self._read_bounded_repair_evidence(
                    projection_path,
                    max_bytes=remaining_bytes,
                )
                remaining_bytes -= len(projection_data)
                journal_data = self._read_bounded_repair_evidence(
                    journal_path,
                    max_bytes=min(self.max_journal_bytes, remaining_bytes),
                )
                journal_data = self._normalize_repair_journal_snapshot(
                    directory,
                    run_id=run_id,
                    journal_data=journal_data,
                    max_bytes=min(self.max_journal_bytes, remaining_bytes),
                )
                remaining_bytes -= len(journal_data)
                policy_data = None
                if reason_code == "legacy_effect_policy_uncorroborated":
                    policy_data = self._read_bounded_repair_evidence(
                        directory / "policy.yaml",
                        max_bytes=remaining_bytes,
                    )
                try:
                    projection, _, journal_sha256 = (
                        self._corroborate_run_evidence_locked(
                            directory,
                            run_id=run_id,
                            projection_data=projection_data,
                            journal_data=journal_data,
                        )
                    )
                except (
                    JournalRecoveryError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ):
                    projection, journal_sha256 = self._adopt_rebuilt_repair_projection(
                        directory,
                        run_id=run_id,
                        projection_data=projection_data,
                        journal_data=journal_data,
                        policy_data=policy_data,
                    )
                if journal_sha256 is None:
                    raise JournalRecoveryError("run journal digest is missing")
                scheduled_at = self._scheduled_at_from_projection(projection)
                with self._connect() as connection:
                    indexed = connection.execute(
                        "SELECT scheduled_at FROM runs WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    if indexed is None:
                        return False
                    if indexed["scheduled_at"] != scheduled_at:
                        connection.execute(
                            "UPDATE runs SET scheduled_at=? WHERE run_id=?",
                            (scheduled_at, run_id),
                        )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=journal_sha256,
                    )
                if reason_code == "legacy_effect_policy_uncorroborated":
                    self._legacy_effect_policy_nodes(
                        directory,
                        projection,
                        policy_data=policy_data,
                    )
                if "run_evidence_uncorroborated" in self._active_run_repair_reasons(
                    run_id
                ):
                    self._transition_run_repair(
                        "run_evidence_uncorroborated",
                        run_id=run_id,
                        outcome="repair_verified",
                    )
                self._transition_run_repair(
                    reason_code,
                    run_id=run_id,
                    outcome="repair_verified",
                )
                return True
        except (
            JournalRecoveryError,
            KeyError,
            OSError,
            StorageQuotaError,
            ValueError,
            WorkflowLockTimeout,
            json.JSONDecodeError,
            sqlite3.Error,
            yaml.YAMLError,
        ):
            return False

    def _note_notification_repair_timeout(self, run_id: str) -> int:
        """Count consecutive repair-lock timeouts for one cursor-blocking run."""
        with self._notification_repair_timeout_lock:
            if self._notification_repair_timeout_run_id == run_id:
                self._notification_repair_timeout_count += 1
            else:
                self._notification_repair_timeout_run_id = run_id
                self._notification_repair_timeout_count = 1
            return self._notification_repair_timeout_count

    def _clear_notification_repair_timeout(self, run_id: str) -> None:
        """Clear diagnostics after the blocked run is read successfully."""
        with self._notification_repair_timeout_lock:
            if self._notification_repair_timeout_run_id == run_id:
                self._notification_repair_timeout_run_id = None
                self._notification_repair_timeout_count = 0

    @staticmethod
    def _active_run_repair_ids(connection: sqlite3.Connection) -> set[str]:
        placeholders = ",".join("?" for _ in _RUN_SCOPED_REPAIR_REASON_ORDER)
        rows = connection.execute(
            "SELECT events.run_id FROM repair_events AS events "
            "WHERE events.run_id IS NOT NULL "
            f"AND events.reason_code IN ({placeholders}) "
            "AND events.sequence=(SELECT MAX(latest.sequence) "
            "FROM repair_events AS latest WHERE latest.run_id=events.run_id "
            "AND latest.reason_code=events.reason_code) "
            "AND events.outcome='repair_required'",
            _RUN_SCOPED_REPAIR_REASON_ORDER,
        ).fetchall()
        return {str(row["run_id"]) for row in rows}

    @staticmethod
    def _snapshot_owner_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        from gateway.status import _pid_exists

        return _pid_exists(pid)

    @staticmethod
    def _write_snapshot_owner(directory: Path) -> None:
        _atomic_json(
            directory / ".snapshot-owner.json",
            {"pid": os.getpid(), "created_at": _utc_now()},
        )

    @staticmethod
    def _record_admission_event(
        connection: sqlite3.Connection,
        event_type: str,
        *,
        run_id: str | None = None,
        reason_code: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO admission_events "
            "(timestamp, event_type, run_id, reason_code, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _utc_now(),
                event_type,
                run_id,
                reason_code,
                json.dumps(_sanitize(dict(payload or {})), sort_keys=True),
            ),
        )
        connection.execute(
            "DELETE FROM admission_events WHERE sequence IN ("
            "SELECT sequence FROM admission_events ORDER BY sequence DESC "
            "LIMIT -1 OFFSET 1000)"
        )

    def _corroborate_run_evidence(
        self,
        directory: Path,
        *,
        run_id: str,
        migrate_legacy_typed_publications: bool = True,
    ) -> tuple[dict[str, object], str | None, str | None]:
        with workflow_lock(self._run_lock_path(run_id)):
            return self._corroborate_run_evidence_locked(
                directory,
                run_id=run_id,
                migrate_legacy_typed_publications=(
                    migrate_legacy_typed_publications
                ),
            )

    def _corroborate_run_evidence_locked(
        self,
        directory: Path,
        *,
        run_id: str,
        projection_data: bytes | None = None,
        journal_data: bytes | None = None,
        migrate_legacy_typed_publications: bool = True,
    ) -> tuple[dict[str, object], str | None, str | None]:
        if (projection_data is None) != (journal_data is None):
            raise ValueError("projection and journal snapshots must be paired")
        projection_path = directory / "run.json"
        journal_path = directory / "events.jsonl"
        projection_bytes = (
            projection_data
            if projection_data is not None
            else projection_path.read_bytes()
        )
        projection = json.loads(projection_bytes)
        if not self._valid_projection(projection, run_id=run_id):
            raise JournalRecoveryError("run projection is not valid")
        rebuilt = self._rebuild_projection(
            directory,
            run_id=run_id,
            journal_data=journal_data,
            migrate_legacy_typed_publications=(
                migrate_legacy_typed_publications
            ),
        )
        if _projection_digest(projection) != _projection_digest(rebuilt):
            raise JournalRecoveryError("run projection does not match journal head")
        journal_bytes = (
            journal_data if journal_data is not None else journal_path.read_bytes()
        )
        projection_sha256 = _sha256(projection_bytes)
        journal_sha256 = _sha256(journal_bytes)
        return projection, projection_sha256, journal_sha256

    @staticmethod
    def _scheduled_at_from_projection(
        projection: Mapping[str, object],
        *,
        indexed: object = _SCHEDULE_PARITY_UNSET,
    ) -> str | None:
        """Derive and, when supplied, parity-check the schedule query column."""
        metadata = projection.get("run_metadata")
        if metadata is None:
            metadata = {}
        elif not isinstance(metadata, Mapping):
            raise JournalRecoveryError("run projection metadata is malformed")
        value = metadata.get("schedule_at")
        if value is None:
            derived = None
        elif not isinstance(value, str) or not value:
            raise JournalRecoveryError("run projection schedule is malformed")
        else:
            try:
                canonical = normalize_rfc3339_instant(value)
            except ScheduleInstantError as exc:
                raise JournalRecoveryError(
                    "run projection schedule is malformed"
                ) from exc
            if value != canonical:
                raise JournalRecoveryError("run projection schedule is not canonical")
            derived = canonical
        if indexed is not _SCHEDULE_PARITY_UNSET and indexed != derived:
            raise JournalRecoveryError("run schedule index parity mismatch")
        return derived

    @staticmethod
    def _sync_integrity_index(
        connection: sqlite3.Connection,
        *,
        projection: Mapping[str, object],
        journal_sha256: str,
    ) -> None:
        schedule_row = connection.execute(
            "SELECT scheduled_at FROM runs WHERE run_id=?",
            (projection["run_id"],),
        ).fetchone()
        scheduled_at = RunStore._scheduled_at_from_projection(
            projection,
            **(
                {"indexed": schedule_row["scheduled_at"]}
                if schedule_row is not None
                else {}
            ),
        )
        queue_position = projection.get("queue_position")
        queue_sequence = projection.get("queue_sequence")
        if projection.get("status") == "queued" and (
            isinstance(queue_sequence, bool)
            or not isinstance(queue_sequence, int)
        ):
            indexed = connection.execute(
                "SELECT queue_sequence FROM runs WHERE run_id=?",
                (projection["run_id"],),
            ).fetchone()
            indexed_sequence = indexed["queue_sequence"] if indexed else None
            queue_sequence = (
                indexed_sequence
                if isinstance(indexed_sequence, int)
                else RunStore._next_queue_sequence(connection)
            )
            if scheduled_at is None:
                queue_position = queue_sequence
        connection.execute(
            "UPDATE runs SET status=?, desired_status=?, execution_mode=?, "
            "scheduled_at=?, "
            "foreground_owner_id=?, foreground_lease_expires_at=?, "
            "foreground_epoch=?, foreground_boot_id=?, "
            "foreground_heartbeat_monotonic=?, foreground_lease_seconds=?, "
            "queue_position=?, queue_sequence=?, blocked_by_run_id=?, "
            "pause_lane_policy=?, lane_state=?, archived_at=?, "
            "restored_to_history=?, projection_schema_version=?, "
            "projection_state_version=?, projection_sha256=?, "
            "journal_sequence=?, journal_sha256=?, integrity_verified_at=? "
            "WHERE run_id=?",
            (
                projection["status"],
                projection.get("desired_status"),
                projection.get("execution_mode", "foreground"),
                scheduled_at,
                projection.get("foreground_owner_id"),
                projection.get("foreground_lease_expires_at"),
                projection.get("foreground_epoch"),
                projection.get("foreground_boot_id"),
                projection.get("foreground_heartbeat_monotonic"),
                projection.get("foreground_lease_seconds"),
                queue_position,
                queue_sequence,
                projection.get("blocked_by_run_id"),
                projection.get("pause_lane_policy", "hold"),
                RunStore._lane_state(projection),
                projection.get("archived_at"),
                int(bool(projection.get("restored_to_history"))),
                int(projection.get("schema_version", 1)),
                int(projection["state_version"]),
                _projection_digest(projection),
                int(projection["event_sequence"]),
                journal_sha256,
                _utc_now(),
                projection["run_id"],
            ),
        )

    def _sync_loaded_integrity(
        self,
        directory: Path,
        projection: Mapping[str, object],
        *,
        migrate_legacy_typed_publications: bool = True,
    ) -> None:
        journal_sha256 = _sha256((directory / "events.jsonl").read_bytes())
        scheduled_at = self._scheduled_at_from_projection(projection)
        expected = (
            projection["status"],
            projection.get("desired_status"),
            projection.get("execution_mode", "foreground"),
            scheduled_at,
            projection.get("queue_position"),
            projection.get("queue_sequence"),
            projection.get("blocked_by_run_id"),
            projection.get("pause_lane_policy", "hold"),
            self._lane_state(projection),
            projection.get("archived_at"),
            int(bool(projection.get("restored_to_history"))),
            int(projection.get("schema_version", 1)),
            int(projection["state_version"]),
            _projection_digest(projection),
            int(projection["event_sequence"]),
            journal_sha256,
        )
        with self._connect() as connection:
            current = connection.execute(
                "SELECT status, desired_status, execution_mode, scheduled_at, "
                "queue_position, "
                "queue_sequence, blocked_by_run_id, pause_lane_policy, lane_state, "
                "archived_at, restored_to_history, "
                "projection_schema_version, "
                "projection_state_version, "
                "projection_sha256, journal_sequence, journal_sha256 "
                "FROM runs WHERE run_id=?",
                (projection["run_id"],),
            ).fetchone()
        if (
            current is not None
            and projection.get("status") == "queued"
            and not isinstance(projection.get("queue_sequence"), int)
            and isinstance(current["queue_sequence"], int)
        ):
            legacy_expected = list(expected)
            legacy_expected[4] = current["queue_sequence"]
            legacy_expected[5] = current["queue_sequence"]
            expected = tuple(legacy_expected)
        if current is not None and tuple(current) == expected:
            return
        try:
            corroborated, _, journal_sha256 = self._corroborate_run_evidence_locked(
                directory,
                run_id=str(projection["run_id"]),
                migrate_legacy_typed_publications=(
                    migrate_legacy_typed_publications
                ),
            )
        except (JournalRecoveryError, OSError, ValueError, json.JSONDecodeError):
            self._mark_repair_required(
                "run_evidence_uncorroborated", run_id=str(projection["run_id"])
            )
            raise
        corroborated_scheduled_at = self._scheduled_at_from_projection(corroborated)
        with self._connect() as connection:
            try:
                self._scheduled_at_from_projection(
                    corroborated,
                    **(
                        {"indexed": current["scheduled_at"]}
                        if current is not None
                        else {}
                    ),
                )
            except JournalRecoveryError:
                self._mark_repair_required(
                    "index_schedule_inconsistent",
                    run_id=str(projection["run_id"]),
                )
                self._record_repair_event(
                    connection,
                    reason_code="index_schedule_inconsistent",
                    outcome="index_rebuilt",
                    run_id=str(projection["run_id"]),
                    source_path=directory,
                    projection_sha256=_projection_digest(corroborated),
                    journal_sha256=journal_sha256,
                )
                connection.execute(
                    "UPDATE runs SET scheduled_at=? WHERE run_id=?",
                    (corroborated_scheduled_at, projection["run_id"]),
                )
            self._sync_integrity_index(
                connection,
                projection=corroborated,
                journal_sha256=journal_sha256,
            )

    @staticmethod
    def _start_digest_from_projection(projection: Mapping[str, object]) -> str:
        required = (
            "workflow",
            "definition_digest",
            "policy_digest",
            "input_manifest_digest",
            "trigger",
            "concurrency_key",
            "idempotency_key_digest",
        )
        if any(not isinstance(projection.get(field), str) for field in required):
            raise JournalRecoveryError("run projection lacks admission identity")
        metadata = projection.get("run_metadata") or {}
        if not isinstance(metadata, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise JournalRecoveryError("run projection metadata is malformed")
        material = json.dumps(
            {
                "workflow": projection["workflow"],
                "definition": projection["definition_digest"],
                "policy": projection["policy_digest"],
                "inputs": projection["input_manifest_digest"],
                "trigger": projection["trigger"],
                "concurrency": projection["concurrency_key"],
                "operator_scope_digest": projection.get("operator_scope_digest"),
                "run_metadata": dict(sorted(metadata.items())),
                **(
                    {
                        "provenance": {
                            "source": projection["provenance"].get("source"),
                            "assurance": projection["provenance"].get("assurance"),
                            "idempotency_namespace_digest": projection.get(
                                "idempotency_namespace_digest"
                            )
                            or _sha256(
                                f"profile-local:{projection['trigger']}".encode()
                            ),
                        }
                    }
                    if isinstance(projection.get("provenance"), Mapping)
                    and projection["provenance"].get("assurance")
                    != "legacy_unknown"
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(material)

    def _rebuild_admission_row(
        self,
        connection: sqlite3.Connection,
        *,
        directory: Path,
        projection: Mapping[str, object],
    ) -> None:
        scheduled_at = self._scheduled_at_from_projection(projection)
        concurrency_policy = projection.get("concurrency_policy", "queue")
        if concurrency_policy not in {"queue", "allow", "forbid"}:
            raise JournalRecoveryError("run concurrency policy is invalid")
        queue_sequence = projection.get("queue_sequence")
        queue_position = projection.get("queue_position")
        if projection.get("status") == "queued" and (
            isinstance(queue_sequence, bool)
            or not isinstance(queue_sequence, int)
        ):
            queue_sequence = self._next_queue_sequence(connection)
            if scheduled_at is None:
                queue_position = queue_sequence
        connection.execute(
            "INSERT INTO runs ("
            "run_id, workflow_name, trigger_source, "
            "idempotency_namespace_digest, idempotency_digest, "
            "start_digest, concurrency_key, concurrency_policy, disposition, "
            "status, scheduled_at, queue_position, queue_sequence, blocked_by_run_id, "
            "pause_lane_policy, lane_state, run_directory, "
            "created_at, updated_at, archived_at, restored_to_history, "
            "admission_state, desired_status, "
            "staging_directory, operator_scope_digest, provenance_json, "
            "execution_mode, foreground_owner_id, foreground_lease_expires_at, "
            "foreground_epoch, foreground_boot_id, "
            "foreground_heartbeat_monotonic, foreground_lease_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, 'published', NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                projection["run_id"],
                projection["workflow"],
                projection["trigger"],
                projection.get("idempotency_namespace_digest")
                or _sha256(f"profile-local:{projection['trigger']}".encode()),
                projection["idempotency_key_digest"],
                self._start_digest_from_projection(projection),
                projection["concurrency_key"],
                concurrency_policy,
                projection.get("admission_disposition", "created"),
                projection["status"],
                scheduled_at,
                queue_position,
                queue_sequence,
                projection.get("blocked_by_run_id"),
                projection.get("pause_lane_policy", "hold"),
                self._lane_state(projection),
                str(directory),
                projection.get("created_at") or projection["updated_at"],
                projection["updated_at"],
                projection.get("archived_at"),
                int(bool(projection.get("restored_to_history"))),
                projection.get("operator_scope_digest"),
                (
                    json.dumps(projection["provenance"], sort_keys=True)
                    if isinstance(projection.get("provenance"), Mapping)
                    else None
                ),
                projection.get("execution_mode", "foreground"),
                projection.get("foreground_owner_id"),
                projection.get("foreground_lease_expires_at"),
                projection.get("foreground_epoch"),
                projection.get("foreground_boot_id"),
                projection.get("foreground_heartbeat_monotonic"),
                projection.get("foreground_lease_seconds"),
            ),
        )

    def _quarantine_evidence(self, source: Path, *, prefix: str) -> Path:
        preserved = self.quarantine_root / f"{prefix}-{uuid.uuid4().hex}"
        _durable_replace(source, preserved)
        return preserved

    def _reconcile_admission(self) -> None:
        """Converge admission state while treating run evidence as authority."""
        with self._connect() as connection:
            reservations = connection.execute(
                "SELECT * FROM runs WHERE admission_state='reserved' "
                "ORDER BY created_at, run_id"
            ).fetchall()
            reserved_staging = {
                str(Path(row["staging_directory"]).resolve())
                for row in reservations
                if row["staging_directory"]
            }
            for row in reservations:
                run_directory = Path(row["run_directory"])
                try:
                    projection, _projection_hash, journal_hash = (
                        self._corroborate_run_evidence(
                            run_directory, run_id=row["run_id"]
                        )
                    )
                except (OSError, ValueError, json.JSONDecodeError, JournalRecoveryError):
                    projection = None
                if projection is not None and projection["event_sequence"] == 1:
                    desired_status = row["desired_status"] or projection["status"]
                    connection.execute(
                        "UPDATE runs SET admission_state='published', status=?, "
                        "desired_status=NULL, staging_directory=NULL, updated_at=? "
                        "WHERE run_id=? AND admission_state='reserved'",
                        (desired_status, _utc_now(), row["run_id"]),
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=journal_hash,
                    )
                    self._record_admission_event(
                        connection,
                        "admission_reservation_recovered",
                        run_id=row["run_id"],
                    )
                    continue
                staging = (
                    Path(row["staging_directory"]) if row["staging_directory"] else None
                )
                preserved_paths = []
                if run_directory.exists():
                    preserved_paths.append(
                        self._quarantine_evidence(
                            run_directory, prefix=f"incomplete-{row['run_id']}"
                        )
                    )
                if staging is not None and staging.exists():
                    preserved_paths.append(
                        self._quarantine_evidence(
                            staging, prefix=f"staging-{row['run_id']}"
                        )
                    )
                connection.execute("DELETE FROM runs WHERE run_id=?", (row["run_id"],))
                self._record_admission_event(
                    connection,
                    "admission_reservation_released",
                    run_id=row["run_id"],
                    reason_code="incomplete_publication",
                    payload={"preserved_paths": [str(path) for path in preserved_paths]},
                )

            published = connection.execute(
                "SELECT * FROM runs WHERE admission_state='published'"
            ).fetchall()
            active_run_repairs = self._active_run_repair_ids(connection)
            known_directories = {
                str(Path(row["run_directory"]).resolve()) for row in published
            }
            for row in published:
                run_directory = Path(row["run_directory"])
                run_id = str(row["run_id"])
                if run_id in active_run_repairs:
                    continue
                try:
                    projection, projection_hash, journal_hash = (
                        self._corroborate_run_evidence(
                            run_directory, run_id=run_id
                        )
                    )
                except (OSError, ValueError, json.JSONDecodeError, JournalRecoveryError):
                    transitioned = self._transition_run_repair(
                        "run_evidence_uncorroborated",
                        run_id=run_id,
                        outcome="repair_required",
                    )
                    active_run_repairs.add(run_id)
                    if transitioned:
                        self._record_repair_event(
                            connection,
                            reason_code="published_evidence_uncorroborated",
                            outcome="evidence_preserved",
                            run_id=run_id,
                            source_path=run_directory,
                        )
                    continue
                try:
                    scheduled_at = self._scheduled_at_from_projection(projection)
                except JournalRecoveryError:
                    transitioned = self._transition_run_repair(
                        "run_evidence_uncorroborated",
                        run_id=run_id,
                        outcome="repair_required",
                    )
                    active_run_repairs.add(run_id)
                    if transitioned:
                        self._record_repair_event(
                            connection,
                            reason_code="published_evidence_uncorroborated",
                            outcome="evidence_preserved",
                            run_id=run_id,
                            source_path=run_directory,
                        )
                    continue
                if row["status"] != projection["status"]:
                    connection.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (projection["status"], projection["updated_at"], row["run_id"]),
                    )
                    self._mark_repair_required(
                        "index_status_inconsistent", run_id=row["run_id"]
                    )
                    self._record_repair_event(
                        connection,
                        reason_code="index_status_inconsistent",
                        outcome="index_rebuilt",
                        run_id=row["run_id"],
                        source_path=run_directory,
                        projection_sha256=projection_hash,
                        journal_sha256=journal_hash,
                    )
                try:
                    self._scheduled_at_from_projection(
                        projection, indexed=row["scheduled_at"]
                    )
                except JournalRecoveryError:
                    self._mark_repair_required(
                        "index_schedule_inconsistent", run_id=run_id
                    )
                    self._record_repair_event(
                        connection,
                        reason_code="index_schedule_inconsistent",
                        outcome="index_rebuilt",
                        run_id=run_id,
                        source_path=run_directory,
                        projection_sha256=projection_hash,
                        journal_sha256=journal_hash,
                    )
                    connection.execute(
                        "UPDATE runs SET scheduled_at=? WHERE run_id=?",
                        (scheduled_at, run_id),
                    )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=journal_hash,
                )

            for workflow_directory in self.runs_root.iterdir():
                if not workflow_directory.is_dir():
                    continue
                for run_directory in tuple(workflow_directory.iterdir()):
                    if not run_directory.is_dir():
                        continue
                    if str(run_directory.resolve()) in known_directories:
                        continue
                    run_id = run_directory.name
                    try:
                        projection, projection_hash, journal_hash = (
                            self._corroborate_run_evidence(
                                run_directory, run_id=run_id
                            )
                        )
                        self._rebuild_admission_row(
                            connection,
                            directory=run_directory,
                            projection=projection,
                        )
                        self._sync_integrity_index(
                            connection,
                            projection=projection,
                            journal_sha256=journal_hash,
                        )
                    except (
                        OSError,
                        ValueError,
                        json.JSONDecodeError,
                        JournalRecoveryError,
                        sqlite3.IntegrityError,
                    ):
                        projection_hash = (
                            _sha256((run_directory / "run.json").read_bytes())
                            if (run_directory / "run.json").is_file()
                            else None
                        )
                        journal_hash = (
                            _sha256((run_directory / "events.jsonl").read_bytes())
                            if (run_directory / "events.jsonl").is_file()
                            else None
                        )
                        preserved = self._quarantine_evidence(
                            run_directory, prefix=f"orphan-{run_id}"
                        )
                        self._mark_repair_required(
                            "orphan_evidence_uncorroborated", run_id=run_id
                        )
                        self._record_repair_event(
                            connection,
                            reason_code="orphan_evidence_uncorroborated",
                            outcome="evidence_quarantined",
                            run_id=run_id,
                            source_path=run_directory,
                            preserved_path=preserved,
                            projection_sha256=projection_hash,
                            journal_sha256=journal_hash,
                        )
                        continue
                    self._mark_repair_required("index_run_missing", run_id=run_id)
                    self._record_repair_event(
                        connection,
                        reason_code="index_run_missing",
                        outcome="index_rebuilt",
                        run_id=run_id,
                        source_path=run_directory,
                        projection_sha256=projection_hash,
                        journal_sha256=journal_hash,
                    )

            for staging in tuple(self.staging_root.iterdir()):
                if not staging.is_dir() or str(staging.resolve()) in reserved_staging:
                    continue
                marker = staging / ".snapshot-owner.json"
                try:
                    owner = json.loads(marker.read_text(encoding="utf-8"))
                    owner_alive = self._snapshot_owner_alive(int(owner["pid"]))
                    created_at = datetime.fromisoformat(str(owner["created_at"]))
                    snapshot_fresh = (
                        datetime.now(timezone.utc) - created_at
                    ) < timedelta(hours=1)
                except (FileNotFoundError, KeyError, TypeError, ValueError, OSError):
                    owner_alive = False
                    snapshot_fresh = False
                if owner_alive and snapshot_fresh:
                    continue
                preserved = self._quarantine_evidence(
                    staging, prefix="orphan-snapshot"
                )
                self._record_admission_event(
                    connection,
                    "orphan_snapshot_removed",
                    reason_code="snapshot_owner_exited",
                    payload={"preserved_path": str(preserved)},
                )

    def list_admission_events(
        self, *, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, timestamp, event_type, run_id, reason_code, "
                "payload_json FROM admission_events ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "run_id": row["run_id"],
                "reason_code": row["reason_code"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def storage_health(self) -> dict[str, object]:
        sqlite_reasons: list[dict[str, str | None]] = []
        try:
            with self._connect() as connection:
                sqlite_reasons = [
                    {
                        "reason_code": str(row["reason_code"]),
                        "run_id": str(row["run_id"]),
                    }
                    for row in connection.execute(
                        "SELECT DISTINCT reason_code, run_id "
                        "FROM store_repair_state ORDER BY reason_code, run_id"
                    ).fetchall()
                ]
        except sqlite3.DatabaseError:
            sqlite_reasons = [
                {"reason_code": "repair_state_unreadable", "run_id": None}
            ]
        try:
            marker = json.loads(self.repair_marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return (
                {"status": "repair_required", "reasons": sqlite_reasons}
                if sqlite_reasons
                else {"status": "healthy", "reasons": []}
            )
        except (OSError, json.JSONDecodeError):
            return {
                "status": "repair_required",
                "reasons": [{"reason_code": "repair_marker_unreadable", "run_id": None}],
            }
        reasons = marker.get("reasons") if isinstance(marker, dict) else None
        combined = [
            dict(reason)
            for reason in reasons
            if isinstance(reason, Mapping)
        ] if isinstance(reasons, list) else []
        for reason in sqlite_reasons:
            if reason not in combined:
                combined.append(reason)
        return {
            "status": "repair_required",
            "detected_at": marker.get("detected_at")
            if isinstance(marker, dict)
            else None,
            "reasons": combined,
        }

    def list_repair_events(
        self, *, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, detected_at, reason_code, outcome, run_id, "
                "source_path, preserved_path, projection_sha256, journal_sha256, "
                "payload_json FROM repair_events ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "detected_at": row["detected_at"],
                "reason_code": row["reason_code"],
                "outcome": row["outcome"],
                "run_id": row["run_id"],
                "source_path": row["source_path"],
                "preserved_path": row["preserved_path"],
                "projection_sha256": row["projection_sha256"],
                "journal_sha256": row["journal_sha256"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def repair_storage(self) -> dict[str, object]:
        """Acknowledge a fully corroborated index rebuild and reopen admission."""
        with self._admission_gate, workflow_lock(self.admission_lock):
            health = self.storage_health()
            if health["status"] == "healthy":
                return health
            reasons = health.get("reasons")
            unresolved = {
                "orphan_evidence_uncorroborated",
                "published_evidence_uncorroborated",
                "run_evidence_uncorroborated",
                "legacy_effect_policy_uncorroborated",
                "repair_marker_unreadable",
                "terminal_journal_reserve_exhausted",
            }
            if isinstance(reasons, list) and any(
                isinstance(reason, dict)
                and reason.get("reason_code") in unresolved
                for reason in reasons
            ):
                raise JournalRecoveryError(
                    "storage evidence is uncorroborated and requires manual repair"
                )
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_id, run_directory, status FROM runs "
                    "WHERE admission_state='published'"
                ).fetchall()
                indexed_directories = set()
                for row in rows:
                    directory = Path(row["run_directory"])
                    projection, _, journal_sha256 = self._corroborate_run_evidence(
                        directory, run_id=row["run_id"]
                    )
                    if projection["status"] != row["status"]:
                        raise JournalRecoveryError(
                            f"admission status remains inconsistent: {row['run_id']}"
                        )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=journal_sha256,
                    )
                    indexed_directories.add(str(directory.resolve()))
                evidence_directories = {
                    str(run.resolve())
                    for workflow in self.runs_root.iterdir()
                    if workflow.is_dir()
                    for run in workflow.iterdir()
                    if run.is_dir()
                }
                if evidence_directories != indexed_directories:
                    raise JournalRecoveryError(
                        "admission index does not cover every run directory"
                    )
                generation = connection.execute(
                    "SELECT value FROM store_metadata WHERE key='generation'"
                ).fetchone()["value"]
                self._record_repair_event(
                    connection,
                    reason_code="operator_repair",
                    outcome="repair_completed",
                    payload={"run_count": len(rows)},
                )
            _atomic_json(
                self.authority_marker,
                {"schema_version": 1, "generation": generation},
            )
            self.repair_marker.unlink(missing_ok=True)
            return {"status": "healthy", "reasons": []}

    def _reconcile_worker_claims(self) -> None:
        """Converge the capacity ledger with durable run projections."""
        active: dict[str, tuple[str, str, str, str]] = {}
        reserves: dict[str, TerminalJournalReserve] = {}
        obligation_reserves: dict[str, tuple[str, TerminalJournalReserve]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, run_directory FROM runs "
                "WHERE admission_state='published' AND status IN "
                "('running','waiting_retry','paused','interrupted',"
                "'recovery_pending')"
            ).fetchall()
        for row in rows:
            try:
                projection = json.loads(
                    (Path(row["run_directory"]) / "run.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
            try:
                pending_payloads = _pending_session_registry_payloads(projection)
            except JournalRecoveryError:
                pending_payloads = {}
            for pending in pending_payloads.values():
                try:
                    candidate, _retry_count = _session_registry_candidate_from_payload(
                        pending
                    )
                except JournalRecoveryError:
                    continue
                if candidate.winning_run_id == row["run_id"]:
                    projection_bytes = len(
                        json.dumps(
                            projection,
                            sort_keys=True,
                            ensure_ascii=False,
                        ).encode("utf-8")
                    )
                    reserve = TerminalJournalReserve.for_projection(
                        projection_bytes
                    )
                    obligation_reserves[candidate.winning_attempt_id] = (
                        row["run_id"],
                        TerminalJournalReserve(
                            projection_limit_bytes=reserve.projection_limit_bytes,
                            terminal_reserve_bytes=(
                                2 * reserve.terminal_reserve_bytes
                            ),
                        ),
                    )
            for node_id, node in projection.get("nodes", {}).items():
                claim = node.get("claim") if isinstance(node, dict) else None
                if not isinstance(claim, dict) and isinstance(node, dict):
                    recovery = node.get("recovery")
                    if isinstance(recovery, dict) and recovery.get("observation") in {
                        "still_running",
                        "outcome_uncertain",
                    }:
                        claim = recovery
                if not isinstance(claim, dict):
                    continue
                attempt_id = str(claim.get("attempt_id", ""))
                if attempt_id:
                    active[attempt_id] = (
                        row["run_id"],
                        str(node_id),
                        str(claim.get("owner_id", "recovered")),
                        str(
                            claim.get("lease_expires_at")
                            or claim.get("lease_expired_at")
                            or _utc_now()
                        ),
                    )
                    projection_bytes = len(
                        json.dumps(
                            projection, sort_keys=True, ensure_ascii=False
                        ).encode("utf-8")
                    )
                    reserves[attempt_id] = TerminalJournalReserve.for_projection(
                        projection_bytes
                    )
        with self._connect() as connection:
            repair_attempts = {
                str(row["attempt_id"])
                for row in connection.execute(
                    "SELECT attempt_id FROM store_repair_state"
                ).fetchall()
            }
            retained = set(active) | repair_attempts
            if retained:
                placeholders = ",".join("?" for _ in retained)
                connection.execute(
                    f"DELETE FROM worker_claims WHERE attempt_id NOT IN ({placeholders})",
                    tuple(sorted(retained)),
                )
            else:
                connection.execute("DELETE FROM worker_claims")
            for attempt_id, values in active.items():
                connection.execute(
                    "INSERT INTO worker_claims "
                    "(attempt_id, run_id, node_id, owner_id, lease_expires_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(attempt_id) DO UPDATE SET "
                    "run_id=excluded.run_id, node_id=excluded.node_id, "
                    "owner_id=excluded.owner_id, "
                    "lease_expires_at=excluded.lease_expires_at",
                    (attempt_id, *values),
                )
                reserve = reserves[attempt_id]
                connection.execute(
                    "INSERT OR IGNORE INTO attempt_journal_reserves ("
                    "attempt_id, run_id, terminal_reserve_bytes, "
                    "projection_limit_bytes, consumed_bytes, created_at) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (
                        attempt_id,
                        values[0],
                        reserve.terminal_reserve_bytes,
                        reserve.projection_limit_bytes,
                        _utc_now(),
                    ),
                )
            retained_obligations = set(obligation_reserves)
            if retained_obligations:
                placeholders = ",".join("?" for _ in retained_obligations)
                connection.execute(
                    "DELETE FROM obligation_journal_reserves "
                    f"WHERE attempt_id NOT IN ({placeholders})",
                    tuple(sorted(retained_obligations)),
                )
            else:
                connection.execute("DELETE FROM obligation_journal_reserves")
            for attempt_id, (run_id, reserve) in obligation_reserves.items():
                connection.execute(
                    "INSERT OR IGNORE INTO obligation_journal_reserves ("
                    "attempt_id, run_id, terminal_reserve_bytes, "
                    "projection_limit_bytes, consumed_bytes, created_at) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (
                        attempt_id,
                        run_id,
                        reserve.terminal_reserve_bytes,
                        reserve.projection_limit_bytes,
                        _utc_now(),
                    ),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database,
            timeout=5.0,
            isolation_level=None,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _write_private_authority(
        connection: sqlite3.Connection,
        *,
        table: str,
        run_id: str,
        attempt_id: str,
        authority: Mapping[str, object],
    ) -> None:
        if table not in {
            "session_recovery_selection_authority",
            "session_registry_winner_authority",
        }:
            raise ValueError("invalid private authority table")
        authority_json = _private_authority_json(authority)
        authority_sha256 = _sha256(authority_json.encode("utf-8"))
        existing = connection.execute(
            f"SELECT run_id, authority_json, authority_sha256 FROM {table} "
            "WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["run_id"] != run_id
                or not hmac.compare_digest(
                    str(existing["authority_json"]), authority_json
                )
                or not hmac.compare_digest(
                    str(existing["authority_sha256"]), authority_sha256
                )
            ):
                raise JournalRecoveryError("private session authority conflicts")
            return
        connection.execute(
            f"INSERT INTO {table} "
            "(attempt_id, run_id, authority_json, authority_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                attempt_id,
                run_id,
                authority_json,
                authority_sha256,
                _utc_now(),
            ),
        )

    def _read_private_session_authorities(
        self,
        *,
        run_id: str,
    ) -> dict[str, dict[str, Mapping[str, object]]]:
        tables = (
            "session_recovery_selection_authority",
            "session_registry_winner_authority",
        )
        authorities: dict[str, dict[str, Mapping[str, object]]] = {
            table: {} for table in tables
        }
        try:
            with self._connect() as connection:
                rows = {
                    table: connection.execute(
                        f"SELECT attempt_id, authority_json, authority_sha256 "
                        f"FROM {table} WHERE run_id=?",
                        (run_id,),
                    ).fetchall()
                    for table in tables
                }
        except sqlite3.Error as exc:
            raise JournalRecoveryError(
                "private session authority is unavailable"
            ) from exc
        for table, table_rows in rows.items():
            for row in table_rows:
                authority_json = str(row["authority_json"])
                authority_sha256 = str(row["authority_sha256"])
                if not hmac.compare_digest(
                    _sha256(authority_json.encode("utf-8")), authority_sha256
                ):
                    raise JournalRecoveryError(
                        "private session authority digest is invalid"
                    )
                try:
                    authority = json.loads(authority_json)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise JournalRecoveryError(
                        "private session authority JSON is invalid"
                    ) from exc
                if not isinstance(authority, dict) or not hmac.compare_digest(
                    _private_authority_json(authority), authority_json
                ):
                    raise JournalRecoveryError(
                        "private session authority is noncanonical"
                    )
                if table == "session_recovery_selection_authority":
                    _session_recovery_selection_from_authority(authority)
                else:
                    _session_registry_candidate_from_authority(authority)
                authorities[table][str(row["attempt_id"])] = authority
        return authorities

    @staticmethod
    def _bind_private_session_authorities_to_events(
        authorities: Mapping[str, Mapping[str, Mapping[str, object]]],
        events: Iterable[Mapping[str, object]],
        *,
        run_id: str,
    ) -> dict[str, dict[str, Mapping[str, object]]]:
        """Keep schema-v2 precommits only when their exact frame exists."""
        event_by_sequence = {
            int(event["sequence"]): event
            for event in events
            if isinstance(event.get("sequence"), int)
            and not isinstance(event.get("sequence"), bool)
        }
        bound: dict[str, dict[str, Mapping[str, object]]] = {
            "session_recovery_selection_authority": {},
            "session_registry_winner_authority": {},
        }
        for attempt_id, authority in authorities.get(
            "session_recovery_selection_authority", {}
        ).items():
            selection, activation, event_type = (
                _session_recovery_selection_from_authority(authority)
            )
            if activation is not None:
                event = event_by_sequence.get(activation)
                if (
                    event is None
                    or event.get("run_id") != run_id
                    or event.get("event_type") != event_type
                    or event.get("node_id") != selection.key.node_id
                    or event.get("attempt_id") != selection.attempt_id
                ):
                    continue
            bound["session_recovery_selection_authority"][attempt_id] = authority
        for attempt_id, authority in authorities.get(
            "session_registry_winner_authority", {}
        ).items():
            candidate, _retry_count, activation, event_type = (
                _session_registry_candidate_from_authority(authority)
            )
            if activation is not None:
                event = event_by_sequence.get(activation)
                if (
                    event is None
                    or event.get("run_id") != run_id
                    or event.get("event_type") != event_type
                    or event.get("node_id") != candidate.winning_node_id
                    or event.get("attempt_id") != candidate.winning_attempt_id
                ):
                    continue
            bound["session_registry_winner_authority"][attempt_id] = authority
        return bound

    def _read_bound_private_session_authorities(
        self,
        *,
        run_id: str,
        events: Iterable[Mapping[str, object]],
    ) -> dict[str, dict[str, Mapping[str, object]]]:
        return self._bind_private_session_authorities_to_events(
            self._read_private_session_authorities(run_id=run_id),
            events,
            run_id=run_id,
        )

    def _validate_recovery_completion_event_authority(
        self,
        events: Iterable[Mapping[str, object]],
        *,
        run_id: str,
        private_authorities: Mapping[
            str, Mapping[str, Mapping[str, object]]
        ],
    ) -> None:
        """Fail closed before projecting a v3 recovery completion payload."""
        for event in events:
            if event.get("event_type") != "node_succeeded":
                continue
            projection = event.get("projection")
            if not isinstance(projection, Mapping):
                continue
            language = projection.get("language")
            if not isinstance(language, Mapping) or language.get(
                "normalizer_version"
            ) != 3:
                continue
            nodes = projection.get("nodes")
            node = (
                nodes.get(event.get("node_id"))
                if isinstance(nodes, Mapping)
                else None
            )
            recoveries = (
                node.get("session_recoveries")
                if isinstance(node, Mapping)
                else None
            )
            if not isinstance(recoveries, list) or not any(
                isinstance(recovery, Mapping)
                and recovery.get("attempt_id") == event.get("attempt_id")
                for recovery in recoveries
            ):
                continue
            if (
                event.get("projection_sha256") != _projection_digest(projection)
                or not self._private_session_authorities_match_projection(
                    projection,
                    private_authorities=private_authorities,
                )
            ):
                raise JournalRecoveryError(
                    "persistent session completion authority is invalid"
                )

    def _private_session_registry_authority_matches(
        self,
        projection: Mapping[str, object],
        candidate: SessionRegistryUpdateCandidate,
        *,
        private_authorities: Mapping[
            str, Mapping[str, Mapping[str, object]]
        ] | None = None,
    ) -> bool:
        authorities = private_authorities or self._read_private_session_authorities(
            run_id=candidate.winning_run_id
        )
        winner_authority = authorities.get(
            "session_registry_winner_authority", {}
        ).get(candidate.winning_attempt_id)
        try:
            anchored_candidate, retry_count, activation, _event_type = (
                _session_registry_candidate_from_authority(winner_authority)
            )
        except JournalRecoveryError:
            return False
        if (
            anchored_candidate != candidate
            or retry_count != 0
            or activation is not None
            and int(projection.get("event_sequence", 0)) < activation
        ):
            return False
        if not candidate.recovery_selected:
            return True
        selection_authority = authorities.get(
            "session_recovery_selection_authority", {}
        ).get(candidate.winning_attempt_id)
        return self._private_selection_authority_matches(
            projection,
            candidate.winning_attempt_id,
            selection_authority,
            require_active=True,
        )

    @staticmethod
    def _private_selection_authority_matches(
        projection: Mapping[str, object],
        attempt_id: str,
        selection_authority: object,
        *,
        require_active: bool,
    ) -> bool:
        if not isinstance(selection_authority, Mapping):
            return False
        schema_version = selection_authority.get("schema_version")
        expected_fields = {
            "schema_version",
            "run_id",
            "attempt_id",
            "key",
            "expected_generation",
            "missing_session_id",
            "cache_fingerprint",
            "source",
            "provider_attempts_before_recovery",
        }
        if schema_version == 2:
            expected_fields.update(
                {"activation_event_sequence", "activation_event_type"}
            )
        elif schema_version != 1:
            return False
        if set(selection_authority) != expected_fields:
            return False
        key = selection_authority.get("key")
        if not isinstance(key, Mapping) or set(key) != {
            "workflow",
            "node_id",
            "scope",
            "provider",
            "profile",
        }:
            return False
        if (
            selection_authority.get("run_id") != projection.get("run_id")
            or selection_authority.get("attempt_id") != attempt_id
            or key.get("workflow") != projection.get("workflow")
            or key.get("scope")
            != str(projection.get("operator_scope_digest") or "local")
            or any(
                not isinstance(key.get(field), str) or not key.get(field)
                for field in ("node_id", "provider", "profile")
            )
        ):
            return False
        node_id = key.get("node_id")
        nodes = projection.get("nodes")
        node = nodes.get(node_id) if isinstance(nodes, Mapping) else None
        attempts = node.get("attempts") if isinstance(node, Mapping) else None
        matching_attempts = [
            attempt
            for attempt in attempts
            if isinstance(attempt, Mapping)
            and attempt.get("attempt_id") == attempt_id
        ] if isinstance(attempts, list) else []
        attempt = matching_attempts[0] if len(matching_attempts) == 1 else None
        recoveries = node.get("session_recoveries") if isinstance(node, Mapping) else None
        matches = [
            recovery
            for recovery in recoveries
            if isinstance(recovery, Mapping)
            and recovery.get("attempt_id") == attempt_id
        ] if isinstance(recoveries, list) else []
        if schema_version == 2:
            activation = selection_authority.get("activation_event_sequence")
            if (
                isinstance(activation, bool)
                or not isinstance(activation, int)
                or activation < 1
                or selection_authority.get("activation_event_type")
                != "persistent_session_missing_fresh_start"
            ):
                return False
            active = int(projection.get("event_sequence", 0)) >= activation
        else:
            active = bool(matches) or (
                isinstance(attempt, Mapping)
                and attempt.get("state")
                in {"succeeded", "failed", "cancelled", "interrupted"}
            )
        if not active:
            return not require_active
        if attempt is None or len(matches) != 1:
            return False
        recovery = matches[0]
        missing_session_id = selection_authority.get("missing_session_id")
        cache_fingerprint = selection_authority.get("cache_fingerprint")
        return (
            selection_authority.get("run_id") == projection.get("run_id")
            and selection_authority.get("attempt_id") == attempt_id
            and key
            == {
                "workflow": projection.get("workflow"),
                "node_id": node_id,
                "scope": str(projection.get("operator_scope_digest") or "local"),
                "provider": recovery.get("provider"),
                "profile": recovery.get("runtime_profile"),
            }
            and selection_authority.get("expected_generation")
            == recovery.get("registry_generation")
            and isinstance(missing_session_id, str)
            and recovery.get("missing_session_sha256")
            == _sha256(missing_session_id.encode("utf-8"))
            and isinstance(cache_fingerprint, str)
            and recovery.get("cache_fingerprint_sha256")
            == _sha256(cache_fingerprint.encode("utf-8"))
            and selection_authority.get("source") == recovery.get("source")
            and selection_authority.get("provider_attempts_before_recovery") == 0
            and recovery.get("provider_attempts_before_recovery") == 0
        )

    def _private_session_authorities_match_projection(
        self,
        projection: Mapping[str, object],
        *,
        private_authorities: Mapping[
            str, Mapping[str, Mapping[str, object]]
        ],
    ) -> bool:
        nodes = projection.get("nodes")
        if not isinstance(nodes, Mapping):
            return False
        selections = private_authorities.get(
            "session_recovery_selection_authority", {}
        )
        winners = private_authorities.get("session_registry_winner_authority", {})
        for attempt_id, selection_authority in selections.items():
            if not self._private_selection_authority_matches(
                projection,
                attempt_id,
                selection_authority,
                require_active=False,
            ):
                return False
        anchored_winners: dict[str, SessionRegistryUpdateCandidate] = {}
        for attempt_id, authority in winners.items():
            try:
                candidate, retry_count, activation, _event_type = (
                    _session_registry_candidate_from_authority(authority)
                )
            except JournalRecoveryError:
                return False
            if (
                retry_count != 0
                or candidate.winning_attempt_id != attempt_id
                or candidate.winning_run_id != projection.get("run_id")
            ):
                return False
            anchored_winners[attempt_id] = candidate
            if activation is not None:
                if int(projection.get("event_sequence", 0)) < activation:
                    continue
            else:
                node = nodes.get(candidate.winning_node_id)
                attempts = (
                    node.get("attempts") if isinstance(node, Mapping) else None
                )
                if not isinstance(attempts, list) or not any(
                    isinstance(attempt, Mapping)
                    and attempt.get("attempt_id") == attempt_id
                    and attempt.get("state") == "succeeded"
                    for attempt in attempts
                ):
                    continue
            if (
                not _session_registry_candidate_is_corroborated(
                    projection, candidate
                )
                or not self._private_session_registry_authority_matches(
                    projection,
                    candidate,
                    private_authorities=private_authorities,
                )
            ):
                return False
        for node in nodes.values():
            attempts = node.get("attempts") if isinstance(node, Mapping) else None
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, Mapping) or not isinstance(
                    attempt.get("session_registry_authority"), Mapping
                ):
                    continue
                attempt_id = attempt.get("attempt_id")
                candidate = anchored_winners.get(str(attempt_id))
                if (
                    candidate is None
                    or not _session_registry_candidate_is_corroborated(
                        projection, candidate
                    )
                    or not self._private_session_registry_authority_matches(
                        projection,
                        candidate,
                        private_authorities=private_authorities,
                    )
                ):
                    return False
        language = projection.get("language")
        strict_recovery = (
            isinstance(language, Mapping)
            and language.get("normalizer_version") == 3
        )
        if strict_recovery:
            for node in nodes.values():
                recoveries = (
                    node.get("session_recoveries")
                    if isinstance(node, Mapping)
                    else None
                )
                if not isinstance(recoveries, list):
                    continue
                for recovery in recoveries:
                    attempt_id = (
                        recovery.get("attempt_id")
                        if isinstance(recovery, Mapping)
                        else None
                    )
                    if (
                        not isinstance(attempt_id, str)
                        or attempt_id not in selections
                        or not self._private_selection_authority_matches(
                            projection,
                            attempt_id,
                            selections[attempt_id],
                            require_active=True,
                        )
                    ):
                        return False
                    attempts = node.get("attempts")
                    matching_attempt = next(
                        (
                            attempt
                            for attempt in attempts
                            if isinstance(attempt, Mapping)
                            and attempt.get("attempt_id") == attempt_id
                        ),
                        None,
                    ) if isinstance(attempts, list) else None
                    if (
                        isinstance(matching_attempt, Mapping)
                        and matching_attempt.get("state") == "succeeded"
                    ):
                        candidate = anchored_winners.get(attempt_id)
                        if (
                            candidate is None
                            or not _session_registry_candidate_is_corroborated(
                                projection, candidate
                            )
                            or not self._private_session_registry_authority_matches(
                                projection,
                                candidate,
                                private_authorities=private_authorities,
                            )
                        ):
                            return False
        return True

    @staticmethod
    def _record_coordinator_wake(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        reason_code: str,
    ) -> int:
        from plugins.workflow.coordinator_store import record_coordinator_wake

        return record_coordinator_wake(
            connection,
            run_id=run_id,
            reason_code=reason_code,
        )

    def _notify_coordinator(self) -> None:
        from plugins.workflow.coordinator_store import CoordinatorStore

        CoordinatorStore(self.database).notify_local()

    def prepare_empty_snapshot(
        self,
        *,
        definition_digest: str,
        policy_digest: str,
        input_manifest_digest: str,
    ) -> PreparedRunSnapshot:
        self._ensure_free_disk()
        with workflow_lock(self.admission_lock):
            staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(staging)
        return PreparedRunSnapshot(
            staging, definition_digest, policy_digest, input_manifest_digest, 0
        )

    def prepare_run_snapshot(
        self,
        package: WorkflowPackage,
        *,
        inputs: Mapping[str, str | Path] | None = None,
        values: Mapping[str, str] | None = None,
        verified_inputs: Mapping[str, tuple[bytes, str]] | None = None,
        resource_read_budget: WorkflowResourceReadBudget | None = None,
        trusted_package_digest: WorkflowPackageDigest | None = None,
        execution_limits: RunExecutionLimits | None = None,
    ) -> PreparedRunSnapshot:
        self._ensure_free_disk()
        with workflow_lock(self.admission_lock):
            staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(staging)
        try:
            try:
                input_declarations = workflow_input_declarations(package)
            except WorkflowInputContractError as exc:
                raise InputSnapshotError(
                    "workflow input declaration is invalid"
                ) from exc

            def input_byte_bound(name: str, *, channel: str) -> int:
                declaration = input_declarations.get(name)
                if declaration is None:
                    return self.max_input_bytes
                return declaration.byte_bound(
                    channel=channel,
                    store_limit=self.max_input_bytes,
                )

            package_digest = trusted_package_digest or compute_package_digest(
                package, read_budget=resource_read_budget
            )
            language = make_language_snapshot(package, package_digest.sha256).to_dict()
            phase3_execution_semantics = None
            if (
                package.language.effective_profile
                is WorkflowLanguageProfile.ARCHON_2026_07
                and package.language.normalizer_version == 3
            ):
                from plugins.workflow.execution_semantics import (
                    build_phase3_execution_semantics,
                )

                phase3_execution_semantics = build_phase3_execution_semantics(
                    package,
                    execution_limits or RunExecutionLimits(),
                ).to_dict()

            def read_package_file(path: Path) -> bytes:
                if resource_read_budget is None:
                    return path.read_bytes()
                return resource_read_budget.read_cached(path)

            definition_data = read_package_file(package.workflow_path)
            (staging / "definition.yaml").write_bytes(definition_data)
            policy_data = b"{}\n"
            if package.sidecar_path is not None:
                policy_data = read_package_file(package.sidecar_path)
                (staging / "policy.yaml").write_bytes(policy_data)
            package_root = Path(os.path.abspath(package.root))
            workflow_relative = (
                Path(os.path.abspath(package.workflow_path))
                .relative_to(package_root)
                .as_posix()
            )
            sidecar_relative = (
                Path(os.path.abspath(package.sidecar_path))
                .relative_to(package_root)
                .as_posix()
                if package.sidecar_path is not None
                else None
            )
            for relative in package_digest.covered_relative_paths:
                if relative in {workflow_relative, sidecar_relative}:
                    continue
                source = package.root / relative
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read_package_file(source))
            node_skill_digests: dict[str, str] = {}
            node_agent_skill_digests: dict[str, str] = {}
            for node in package.definition.nodes:
                skills = tuple(node.options.get("skills", ()))
                if not skills:
                    continue
                from agent.skill_commands import build_preloaded_skills_prompt

                skill_text, _loaded, missing = build_preloaded_skills_prompt(
                    list(skills), task_id=None
                )
                if missing:
                    raise InputSnapshotError(
                        f"workflow node {node.id} references missing skills: "
                        + ", ".join(missing)
                    )
                target = staging / "node-skills" / f"{node.id}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(skill_text, encoding="utf-8")
                node_skill_digests[node.id] = _sha256(skill_text.encode())

            for node in package.definition.nodes:
                for agent_id, definition in node.options.get("agents", {}).items():
                    skills = tuple(definition.get("skills", ()))
                    if not skills:
                        continue
                    from agent.skill_commands import build_preloaded_skills_prompt

                    skill_text, _loaded, missing = build_preloaded_skills_prompt(
                        list(skills), task_id=None
                    )
                    if missing:
                        raise InputSnapshotError(
                            f"workflow inline agent {agent_id} references missing skills: "
                            + ", ".join(missing)
                        )
                    target = staging / "node-agent-skills" / node.id / f"{agent_id}.md"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(skill_text, encoding="utf-8")
                    node_agent_skill_digests[f"{node.id}/{agent_id}"] = _sha256(
                        skill_text.encode()
                    )
            input_manifest: dict[str, dict[str, object]] = {}
            input_digests: dict[str, str] = {}
            input_root = staging / "inputs"
            if not workflow_input_names_are_portable(
                [
                    *(inputs or {}).keys(),
                    *(verified_inputs or {}).keys(),
                    *(values or {}).keys(),
                ]
            ):
                raise InputSnapshotError("invalid or colliding input name")
            if not workflow_filename_components_are_distinct(
                [
                    *(inputs or {}).keys(),
                    *(verified_inputs or {}).keys(),
                    *(f"{name}.txt" for name in (values or {})),
                ]
            ):
                raise InputSnapshotError("invalid or colliding input name")
            for name, source_value in sorted((inputs or {}).items()):
                if not workflow_input_name_is_portable(name):
                    raise InputSnapshotError(f"invalid input name: {name}")
                source = Path(source_value)
                if source.is_symlink():
                    raise InputSnapshotError(f"input symlink is not allowed: {source}")
                try:
                    before = source.stat()
                except OSError as exc:
                    raise InputSnapshotError(
                        f"input is unreadable: {source}: {exc}"
                    ) from exc
                if not source.is_file():
                    raise InputSnapshotError(f"input is not a file: {source}")
                byte_bound = input_byte_bound(name, channel="local-file")
                if before.st_size > byte_bound:
                    raise InputSnapshotError(
                        f"input {name} exceeds {byte_bound} bytes",
                        code="workflow_input_too_large",
                    )
                try:
                    data = source.read_bytes()
                    after = source.stat()
                except OSError as exc:
                    raise InputSnapshotError(
                        f"input is unreadable: {source}: {exc}"
                    ) from exc
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ) or len(data) != before.st_size:
                    raise InputSnapshotError(f"input changed during copy: {source}")
                input_root.mkdir(exist_ok=True)
                target = input_root / name
                target.write_bytes(data)
                digest = _sha256(data)
                input_digests[name] = digest
                input_manifest[name] = {
                    "relative_path": target.relative_to(staging).as_posix(),
                    "source_path": str(source.resolve()),
                    "size_bytes": len(data),
                    "media_type": mimetypes.guess_type(source.name)[0]
                    or "application/octet-stream",
                    "sha256": digest,
                }
            for name, verified in sorted((verified_inputs or {}).items()):
                if name in input_manifest or not workflow_input_name_is_portable(name):
                    raise InputSnapshotError(f"invalid or duplicate input name: {name}")
                if (
                    not isinstance(verified, tuple)
                    or len(verified) != 2
                    or not isinstance(verified[0], bytes)
                    or not isinstance(verified[1], str)
                ):
                    raise InputSnapshotError(f"invalid verified input: {name}")
                data, expected_digest = verified
                byte_bound = input_byte_bound(name, channel="verified-fixture")
                if len(data) > byte_bound:
                    raise InputSnapshotError(
                        f"input {name} exceeds {byte_bound} bytes",
                        code="workflow_input_too_large",
                    )
                digest = _sha256(data)
                if not hmac.compare_digest(digest, expected_digest):
                    raise InputSnapshotError(f"verified input digest mismatch: {name}")
                input_root.mkdir(exist_ok=True)
                target = input_root / name
                target.write_bytes(data)
                input_digests[name] = digest
                input_manifest[name] = {
                    "relative_path": target.relative_to(staging).as_posix(),
                    "size_bytes": len(data),
                    "media_type": "application/octet-stream",
                    "sha256": digest,
                }
            for name, value in sorted((values or {}).items()):
                if name in input_manifest or not workflow_input_name_is_portable(name):
                    raise InputSnapshotError(f"invalid or duplicate input name: {name}")
                data = value.encode("utf-8")
                byte_bound = input_byte_bound(name, channel="text")
                if len(data) > byte_bound:
                    raise InputSnapshotError(
                        f"input {name} exceeds {byte_bound} bytes",
                        code="workflow_input_too_large",
                    )
                input_root.mkdir(exist_ok=True)
                target = input_root / f"{name}.txt"
                target.write_bytes(data)
                digest = _sha256(data)
                input_digests[name] = digest
                input_manifest[name] = {
                    "relative_path": target.relative_to(staging).as_posix(),
                    "size_bytes": len(data),
                    "media_type": "text/plain",
                    "sha256": digest,
                }
            manifest_data = json.dumps(
                input_manifest, sort_keys=True, separators=(",", ":")
            ).encode()
            (staging / "inputs.json").write_bytes(manifest_data)
            sealed_paths = sorted(
                {
                    path.relative_to(staging).as_posix()
                    for path in staging.rglob("*")
                    if path.is_file()
                    and path.relative_to(staging).as_posix()
                    != ".snapshot-owner.json"
                }
                | {"resources.json"}
            )
            snapshot_resources: dict[str, object] = {
                "inputs_sha256": _sha256(manifest_data),
                "node_skills": node_skill_digests,
                "node_agent_skills": node_agent_skill_digests,
                "language": language,
                "sealed_paths": sealed_paths,
            }
            if phase3_execution_semantics is not None:
                snapshot_resources["phase3_execution_semantics"] = (
                    phase3_execution_semantics
                )
            snapshot_manifest = json.dumps(
                snapshot_resources,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            (staging / "resources.json").write_bytes(snapshot_manifest)
            from plugins.workflow.scheduled_revalidation import (
                sealed_snapshot_digest,
            )

            snapshot_digest = sealed_snapshot_digest(
                staging, relative_paths=sealed_paths
            )
            nodes = tuple(
                {
                    "id": node.id,
                    "type": node.node_type,
                    "depends_on": list(node.depends_on),
                    "state": "pending" if node.depends_on else "ready",
                    "attempts": [],
                }
                for node in package.definition.nodes
            )
            reserved_bytes = sum(
                path.stat().st_size for path in staging.rglob("*") if path.is_file()
            )
            if reserved_bytes > self.max_run_bytes:
                raise StorageQuotaError(
                    f"run_storage_quota exceeded: {reserved_bytes} > {self.max_run_bytes}"
                )
            return PreparedRunSnapshot(
                staging_directory=staging,
                definition_digest=package_digest.sha256,
                policy_digest=_sha256(policy_data),
                input_manifest_digest=_sha256(snapshot_manifest),
                reserved_bytes=reserved_bytes,
                workflow_name=package.definition.name,
                nodes=nodes,
                input_digests=input_digests,
                outward_action_nodes=tuple(
                    str(node_id)
                    for node_id in package.sidecar.get("outward_action_nodes", ())
                ),
                language=language,
                sealed_snapshot_digest=snapshot_digest,
            )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _ensure_free_disk(self, required_bytes: int = 0) -> None:
        usage = shutil.disk_usage(self.root)
        watermark = max(1024**3, min(5 * 1024**3, int(usage.total * 0.05)))
        if usage.free - required_bytes < watermark:
            raise StorageQuotaError(
                "free_disk_watermark not met after reservation: "
                f"{usage.free} - {required_bytes} < {watermark}"
            )

    @staticmethod
    def _directory_bytes(directory: Path) -> int:
        total = 0
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                # Atomic projection writes rename their temporary file while a
                # concurrent capacity scan is walking the run tree.
                continue
        return total

    def _profile_storage_bytes(self) -> int:
        return self._directory_bytes(self.runs_root) + self._directory_bytes(
            self.root / "typed-mirrors"
        )

    def _ensure_mirror_capacity(
        self,
        mirror_bytes: int,
        required_bytes: int,
    ) -> None:
        runs_bytes = self._directory_bytes(self.runs_root)
        if runs_bytes + mirror_bytes + required_bytes > self.max_profile_bytes:
            raise StorageQuotaError(
                "profile_storage_quota exceeded by typed mirror"
            )

    def _ensure_run_capacity(
        self,
        directory: Path,
        projection: Mapping[str, object],
        *,
        journal_reserve_bytes: int = 0,
    ) -> None:
        """Reserve enough durable space before allocating a worker."""
        projection_bytes = len(
            json.dumps(projection, sort_keys=True, ensure_ascii=False).encode("utf-8")
        )
        node_count = len(projection.get("nodes", {}))
        # One attempt can claim/start/complete, update run state, and resolve a
        # full downstream ready layer. Heartbeats are compact deltas.
        event_reserve = (node_count + 8) * (
            projection_bytes + 2048
        ) + journal_reserve_bytes
        journal_bytes = (directory / "events.jsonl").stat().st_size
        if journal_bytes + event_reserve > self.max_journal_bytes:
            raise StorageQuotaError(
                "event_journal_quota would be exceeded before worker allocation"
            )
        run_bytes = self._directory_bytes(directory)
        output_reserve = 1024 * 1024
        required = event_reserve + output_reserve
        if run_bytes + required > self.max_run_bytes:
            raise StorageQuotaError(
                "run_storage_quota would be exceeded before worker allocation"
            )
        profile_bytes = self._profile_storage_bytes()
        if profile_bytes + required > self.max_profile_bytes:
            raise StorageQuotaError(
                "profile_storage_quota would be exceeded before worker allocation"
            )

    def _check_journal_reserve(
        self,
        *,
        run_id: str,
        projection: Mapping[str, object],
        journal_bytes: int,
        frame_bytes: int,
        terminal_attempt_id: str | None,
        connection: sqlite3.Connection | None,
    ) -> None:
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect()
        ) as reserve_connection:
            rows = reserve_connection.execute(
                "SELECT attempt_id, terminal_reserve_bytes, "
                "projection_limit_bytes, consumed_bytes, 'attempt' AS kind "
                "FROM attempt_journal_reserves WHERE run_id=? "
                "UNION ALL SELECT attempt_id, terminal_reserve_bytes, "
                "projection_limit_bytes, consumed_bytes, 'obligation' AS kind "
                "FROM obligation_journal_reserves WHERE run_id=?",
                (run_id, run_id),
            ).fetchall()
        if not rows:
            if journal_bytes + frame_bytes > self.max_journal_bytes:
                raise StorageQuotaError("event_journal_quota exceeded")
            return

        projection_bytes = len(
            json.dumps(projection, sort_keys=True, ensure_ascii=False).encode("utf-8")
        )
        remaining_required = 0
        terminal_found = terminal_attempt_id is None
        regrown: list[tuple[str, int, int]] = []
        for row in rows:
            reserve_bytes = int(row["terminal_reserve_bytes"])
            consumed_bytes = int(row["consumed_bytes"])
            remaining = max(0, reserve_bytes - consumed_bytes)
            if row["attempt_id"] == terminal_attempt_id:
                terminal_found = True
                if frame_bytes > remaining:
                    raise StorageQuotaError(
                        "terminal_journal_reserve exhausted before durable completion"
                    )
                remaining -= frame_bytes
            elif (
                row["kind"] == "attempt"
                and projection_bytes > int(row["projection_limit_bytes"])
            ):
                # A concurrently live sibling reserved against the projection as
                # it stood when that sibling claimed. Ordinary progress -- every
                # other sibling in the same fan-out completing and appending its
                # results -- grows the projection past that snapshot, and the
                # snapshot is never revised. Refusing here would fail a healthy
                # run with executor_crash purely because a sibling started
                # earlier, which is what a five-wide fan-out measured at only
                # ~9% headroom before tipping over on a CI runner.
                #
                # Re-reserve that sibling at the CURRENT projection size instead.
                # It still owes the same guarantee -- durable capacity for its
                # own terminal and recovery frames -- so grow the reserve to
                # match and let the journal-capacity check below decide whether
                # the quota can actually fund it. That way the error we raise
                # means "out of durable space", never "an older sibling's
                # snapshot went stale".
                grown = TerminalJournalReserve.for_projection(projection_bytes)
                if grown.terminal_reserve_bytes > reserve_bytes:
                    regrown.append((
                        str(row["attempt_id"]),
                        grown.projection_limit_bytes,
                        grown.terminal_reserve_bytes,
                    ))
                    remaining = max(0, grown.terminal_reserve_bytes - consumed_bytes)
            remaining_required += remaining
        if not terminal_found:
            raise StorageQuotaError("terminal_journal_reserve is missing")
        if journal_bytes + frame_bytes + remaining_required > self.max_journal_bytes:
            # Honest exhaustion: even after re-reserving, the journal cannot hold
            # every live attempt's terminal evidence.
            raise StorageQuotaError(
                "event_journal_quota protected terminal recovery capacity"
            )
        if regrown:
            # Only persisted once the capacity check above has passed, so a
            # rejected write never leaves a widened reserve behind.
            with (
                nullcontext(connection)
                if connection is not None
                else self._connect()
            ) as regrow_connection:
                regrow_connection.executemany(
                    "UPDATE attempt_journal_reserves "
                    "SET projection_limit_bytes=?, terminal_reserve_bytes=? "
                    "WHERE attempt_id=? AND terminal_reserve_bytes<?",
                    [
                        (limit, reserve, attempt_id, reserve)
                        for attempt_id, limit, reserve in regrown
                    ],
                )

    def _consume_journal_reserve(
        self,
        attempt_id: str,
        frame_bytes: int,
        *,
        connection: sqlite3.Connection | None,
    ) -> None:
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect()
        ) as reserve_connection:
            updated = reserve_connection.execute(
                "UPDATE attempt_journal_reserves SET consumed_bytes=consumed_bytes+? "
                "WHERE attempt_id=? "
                "AND consumed_bytes+?<=terminal_reserve_bytes",
                (frame_bytes, attempt_id, frame_bytes),
            ).rowcount
            if updated != 1:
                updated = reserve_connection.execute(
                    "UPDATE obligation_journal_reserves "
                    "SET consumed_bytes=consumed_bytes+? WHERE attempt_id=? "
                    "AND consumed_bytes+?<=terminal_reserve_bytes",
                    (frame_bytes, attempt_id, frame_bytes),
                ).rowcount
        if updated != 1:
            raise StorageQuotaError(
                "terminal_journal_reserve consumption could not be indexed"
            )

    def clone_prepared_snapshot(
        self, snapshot: PreparedRunSnapshot
    ) -> PreparedRunSnapshot:
        with workflow_lock(self.admission_lock):
            target = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(target)
        shutil.copytree(
            snapshot.staging_directory,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".snapshot-owner.json"),
        )
        return PreparedRunSnapshot(
            target,
            snapshot.definition_digest,
            snapshot.policy_digest,
            snapshot.input_manifest_digest,
            snapshot.reserved_bytes,
            snapshot.workflow_name,
            snapshot.workflow_version,
            snapshot.nodes,
            dict(snapshot.input_digests),
            snapshot.outward_action_nodes,
            dict(snapshot.language) if snapshot.language is not None else None,
            snapshot.sealed_snapshot_digest,
        )

    @staticmethod
    def _scope_digest(operator_scope: str | None) -> str | None:
        if operator_scope is None:
            return None
        if not operator_scope:
            raise ValueError("operator_scope must not be empty")
        return _sha256(operator_scope.encode())

    @staticmethod
    def _pause_lane_policy(snapshot: PreparedRunSnapshot) -> str:
        policy_path = snapshot.staging_directory / "policy.yaml"
        if not policy_path.is_file():
            return "hold"
        policy_bytes = policy_path.read_bytes()
        if not hmac.compare_digest(_sha256(policy_bytes), snapshot.policy_digest):
            raise InputSnapshotError("workflow sidecar digest changed before admission")
        try:
            policy = yaml.safe_load(policy_bytes) or {}
        except yaml.YAMLError as exc:
            raise InputSnapshotError("workflow sidecar is malformed") from exc
        value = policy.get("pause_lane_policy", "hold")
        if value not in {"hold", "release"}:
            raise InputSnapshotError("pause_lane_policy must be hold or release")
        return str(value)

    @staticmethod
    def _next_queue_sequence(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM store_metadata WHERE key='queue_sequence'"
        ).fetchone()
        current = int(row["value"]) if row is not None else 0
        sequence = current + 1
        connection.execute(
            "INSERT INTO store_metadata (key, value) VALUES "
            "('queue_sequence', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(sequence),),
        )
        return sequence

    @staticmethod
    def _has_unresolved_outward_attempt(
        projection: Mapping[str, object],
    ) -> bool:
        outward = {
            str(node_id)
            for node_id in projection.get("outward_action_nodes", ())
            if isinstance(node_id, str)
        }
        nodes = projection.get("nodes")
        if not isinstance(nodes, Mapping):
            return False
        for node_id, node in nodes.items():
            if not isinstance(node, Mapping):
                continue
            pending = node.get("pending_interaction")
            pending_reconcile = pending == "reconcile" or (
                isinstance(pending, Mapping) and pending.get("type") == "reconcile"
            )
            recovery = node.get("recovery")
            if isinstance(recovery, Mapping):
                effect = recovery.get("effect_classification")
                if (str(node_id) in outward or effect == "outward") and (
                    pending_reconcile
                    or recovery.get("observation")
                    in {"still_running", "outcome_uncertain"}
                ):
                    return True
            for attempt in node.get("attempts", ()):
                if not isinstance(attempt, Mapping):
                    continue
                if (
                    attempt.get("effect_classification") == "outward"
                    and attempt.get("state")
                    in {"claimed", "running", "paused", "interrupted"}
                    and not attempt.get("reconciliation")
                ):
                    return True
        return False

    @classmethod
    def _lane_state(cls, projection: Mapping[str, object]) -> str:
        return lane_state_for(
            str(projection.get("status") or ""),
            pause_lane_policy=str(
                projection.get("pause_lane_policy") or "hold"
            ),
            unresolved_outward_attempt=cls._has_unresolved_outward_attempt(
                projection
            ),
        )

    @staticmethod
    def _start_digest(request: RunAdmissionRequest) -> str:
        identity = {
                "workflow": request.workflow_name,
                "definition": request.definition_digest,
                "policy": request.policy_digest,
                "inputs": request.input_manifest_digest,
                "trigger": request.trigger_source,
                "concurrency": request.concurrency_key,
                "operator_scope_digest": RunStore._scope_digest(request.operator_scope),
                "run_metadata": dict(sorted((request.run_metadata or {}).items())),
        }
        if request.provenance is not None:
            identity["provenance"] = request.provenance.semantic_record(
                idempotency_namespace=request.idempotency_namespace
            )
        material = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(material)

    @staticmethod
    def _pre_language_input_manifest_digest(
        snapshot: PreparedRunSnapshot,
    ) -> str | None:
        """Reconstruct the pre-language resources digest for legacy retries.

        ``input_manifest_digest`` historically covered the complete
        ``resources.json`` object.  Language pinning and sealed-path metadata
        extended that object without changing legacy workflow semantics.  A
        retry of a pre-language idempotency key must therefore compare against
        the exact old serialization, while all new admissions continue to use
        the complete current digest.
        """
        try:
            resources = json.loads(
                (snapshot.staging_directory / "resources.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(resources, dict):
            return None
        legacy_keys = ("inputs_sha256", "node_skills", "node_agent_skills")
        if any(key not in resources for key in legacy_keys):
            return None
        legacy_resources = {key: resources[key] for key in legacy_keys}
        material = json.dumps(
            legacy_resources, sort_keys=True, separators=(",", ":")
        ).encode()
        return _sha256(material)

    def close_admission(self) -> None:
        """Atomically prevent this coordinator from publishing another run."""
        with self._admission_gate:
            self._admission_open = False

    def start_run(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult:
        with self._admission_gate:
            if not self._admission_open:
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "admission_closed")
            with workflow_lock(self.admission_lock):
                self._reconcile_admission()
                return self._start_run_locked(
                    request, immutable_snapshot=immutable_snapshot
                )

    def _start_run_locked(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult:
        if self.storage_health()["status"] != "healthy":
            shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
            return RunAdmissionResult(None, "rejected", "storage_repair_required")
        if not request.idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        if (
            not isinstance(request.idempotency_namespace, str)
            or not request.idempotency_namespace.strip()
        ):
            raise ValueError("idempotency_namespace must not be empty")
        metadata = dict(request.run_metadata or {})
        if any(
            not isinstance(key, str)
            or not key
            or len(key) > 64
            or not isinstance(value, str)
            or len(value) > 512
            for key, value in metadata.items()
        ):
            raise ValueError("run_metadata must contain bounded string pairs")
        scheduled_at = self._scheduled_at_from_projection(
            {"run_metadata": metadata}
        )
        provenance = request.provenance or TriggerProvenance.legacy(
            source=request.trigger_source,
            intent_key=request.idempotency_key,
        )
        if provenance.source != request.trigger_source:
            raise ValueError("provenance source must match trigger_source")
        if provenance.intent_key != request.idempotency_key:
            raise ValueError("provenance intent_key must match idempotency_key")
        if (
            request.workflow_name != immutable_snapshot.workflow_name
            and immutable_snapshot.workflow_name
        ):
            raise ValueError("snapshot workflow does not match admission request")
        supplied = (
            request.definition_digest,
            request.policy_digest,
            request.input_manifest_digest,
        )
        actual = (
            immutable_snapshot.definition_digest,
            immutable_snapshot.policy_digest,
            immutable_snapshot.input_manifest_digest,
        )
        if supplied != actual:
            shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
            raise ValueError("snapshot digests do not match admission request")
        pause_lane_policy = self._pause_lane_policy(immutable_snapshot)
        key_digest = _sha256(request.idempotency_key.encode())
        namespace_digest = _sha256(request.idempotency_namespace.encode())
        operator_scope_digest = self._scope_digest(request.operator_scope)
        start_digest = self._start_digest(request)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT run_id, start_digest FROM runs "
                "WHERE idempotency_namespace_digest=? AND workflow_name=? "
                "AND idempotency_digest=?",
                (namespace_digest, request.workflow_name, key_digest),
            ).fetchone()
            if existing:
                connection.commit()
                legacy_inputs = self._pre_language_input_manifest_digest(
                    immutable_snapshot
                )
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                if existing["start_digest"] == start_digest:
                    return RunAdmissionResult(existing["run_id"], "existing")
                if legacy_inputs is not None:
                    legacy_request = replace(
                        request, input_manifest_digest=legacy_inputs
                    )
                    if existing["start_digest"] == self._start_digest(legacy_request):
                        return RunAdmissionResult(existing["run_id"], "existing")
                return RunAdmissionResult(None, "rejected", "idempotency_conflict")
            if request.execution_mode not in {"foreground", "background"}:
                raise ValueError("execution_mode must be foreground or background")
            admission_sample = self._lease_clock()
            admission_now = admission_sample.utc_now.astimezone(timezone.utc)
            fresh_leader = self._fresh_coordinator_lease(
                connection, admission_sample
            )
            if request.execution_mode == "background" and fresh_leader is None:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(
                    None, "rejected", "coordinator_unavailable"
                )
            if request.execution_mode == "foreground" and fresh_leader is not None:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "coordinator_active")
            if (
                isinstance(request.foreground_lease_seconds, bool)
                or not isinstance(request.foreground_lease_seconds, int | float)
                or not math.isfinite(float(request.foreground_lease_seconds))
                or request.foreground_lease_seconds <= 0
            ):
                raise ValueError("foreground_lease_seconds must be positive and finite")
            foreground_owner_id = None
            foreground_lease_expires_at = None
            foreground_epoch = None
            foreground_boot_id = None
            foreground_heartbeat_monotonic = None
            foreground_lease_seconds = None
            if request.execution_mode == "foreground":
                foreground_owner_id = request.foreground_owner_id or (
                    f"foreground-{os.getpid()}-{uuid.uuid4().hex}"
                )
                if len(foreground_owner_id) > 256:
                    raise ValueError("foreground_owner_id must be bounded text")
                foreground_lease_expires_at = (
                    admission_now
                    + timedelta(seconds=float(request.foreground_lease_seconds))
                ).isoformat()
                foreground_epoch = 1
                foreground_boot_id = admission_sample.boot_id
                foreground_heartbeat_monotonic = admission_sample.monotonic_now
                foreground_lease_seconds = float(request.foreground_lease_seconds)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            recent_starts = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE created_at>=?", (cutoff,)
            ).fetchone()[0]
            if recent_starts >= self.limits["rate"]:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "start_rate_capacity")
            counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
                )
            }
            nonterminal = sum(counts.get(state, 0) for state in _NONTERMINAL)
            if nonterminal >= self.limits["nonterminal"]:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "nonterminal_capacity")
            profile_bytes = self._profile_storage_bytes()
            if (
                profile_bytes + immutable_snapshot.reserved_bytes
                > self.max_profile_bytes
            ):
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "profile_storage_quota")
            active = connection.execute(
                "SELECT run_id FROM runs WHERE workflow_name=? AND concurrency_key=? "
                "AND lane_state='held' ORDER BY created_at, run_id LIMIT 1",
                (request.workflow_name, request.concurrency_key),
            ).fetchone()
            older_queued = self._eligible_queued_predecessor(
                connection,
                run_id=None,
                now=admission_now,
            )
            status = "running"
            disposition = "created"
            blocked_by = None
            queue_position = None
            queue_sequence = None
            execution_at_capacity = counts.get("running", 0) >= self.limits["executing"]
            if (
                scheduled_at is None
                and active
                and request.concurrency_policy == "forbid"
            ):
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "overlap_forbidden")
            if scheduled_at is not None:
                if counts.get("queued", 0) >= self.limits["queued"]:
                    connection.rollback()
                    shutil.rmtree(
                        immutable_snapshot.staging_directory, ignore_errors=True
                    )
                    return RunAdmissionResult(None, "rejected", "queued_capacity")
                status = "queued"
                disposition = "queued"
                queue_sequence = self._next_queue_sequence(connection)
            elif (
                (active and request.concurrency_policy == "queue")
                or (older_queued is not None and request.concurrency_policy != "allow")
                or (execution_at_capacity and request.concurrency_policy == "queue")
            ):
                if counts.get("queued", 0) >= self.limits["queued"]:
                    connection.rollback()
                    shutil.rmtree(
                        immutable_snapshot.staging_directory, ignore_errors=True
                    )
                    return RunAdmissionResult(None, "rejected", "queued_capacity")
                status = "queued"
                disposition = "queued"
                blocked_by = active["run_id"] if active is not None else None
                queue_sequence = self._next_queue_sequence(connection)
                queue_position = queue_sequence
            elif execution_at_capacity:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "executing_capacity")
            run_id = uuid.uuid4().hex
            run_directory = self.runs_root / request.workflow_name / run_id
            run_directory.parent.mkdir(parents=True, exist_ok=True)
            now = _utc_now()
            provenance_record = provenance.durable_record(admitted_at=now)
            lane_state = "held" if status == "running" else "released"
            connection.execute(
                "INSERT INTO runs ("
                "run_id, workflow_name, trigger_source, "
                "idempotency_namespace_digest, idempotency_digest, "
                "start_digest, concurrency_key, concurrency_policy, disposition, "
                "status, scheduled_at, queue_position, queue_sequence, "
                "blocked_by_run_id, "
                "pause_lane_policy, lane_state, run_directory, "
                "created_at, updated_at, admission_state, desired_status, "
                "staging_directory, operator_scope_digest, provenance_json, execution_mode, "
                "foreground_owner_id, foreground_lease_expires_at, foreground_epoch, "
                "foreground_boot_id, foreground_heartbeat_monotonic, "
                "foreground_lease_seconds) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    request.workflow_name,
                    request.trigger_source,
                    namespace_digest,
                    key_digest,
                    start_digest,
                    request.concurrency_key,
                    request.concurrency_policy,
                    disposition,
                    "admitting",
                    scheduled_at,
                    queue_position,
                    queue_sequence,
                    blocked_by,
                    pause_lane_policy,
                    lane_state,
                    str(run_directory),
                    now,
                    now,
                    "reserved",
                    status,
                    str(immutable_snapshot.staging_directory),
                    operator_scope_digest,
                    json.dumps(provenance_record, sort_keys=True),
                    request.execution_mode,
                    foreground_owner_id,
                    foreground_lease_expires_at,
                    foreground_epoch,
                    foreground_boot_id,
                    foreground_heartbeat_monotonic,
                    foreground_lease_seconds,
                ),
            )
            connection.commit()
            self._publish_reserved_run(
                run_id=run_id,
                run_directory=run_directory,
                request=request,
                snapshot=immutable_snapshot,
                key_digest=key_digest,
                namespace_digest=namespace_digest,
                operator_scope_digest=operator_scope_digest,
                disposition=disposition,
                status=status,
                queue_position=queue_position,
                queue_sequence=queue_sequence,
                blocked_by=blocked_by,
                pause_lane_policy=pause_lane_policy,
                lane_state=lane_state,
                created_at=now,
                provenance_record=provenance_record,
                foreground_owner_id=foreground_owner_id,
                foreground_lease_expires_at=foreground_lease_expires_at,
                foreground_epoch=foreground_epoch,
                foreground_boot_id=foreground_boot_id,
                foreground_heartbeat_monotonic=foreground_heartbeat_monotonic,
                foreground_lease_seconds=foreground_lease_seconds,
            )
            self._mark_reservation_published(run_id, status=status)
            self.load_run(run_id)
            return RunAdmissionResult(
                run_id, disposition, None, queue_position, blocked_by
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _publish_reserved_run(
        self,
        *,
        run_id: str,
        run_directory: Path,
        request: RunAdmissionRequest,
        snapshot: PreparedRunSnapshot,
        key_digest: str,
        namespace_digest: str,
        operator_scope_digest: str | None,
        disposition: str,
        status: str,
        queue_position: int | None,
        queue_sequence: int | None,
        blocked_by: str | None,
        pause_lane_policy: str,
        lane_state: str,
        created_at: str,
        provenance_record: Mapping[str, object],
        foreground_owner_id: str | None,
        foreground_lease_expires_at: str | None,
        foreground_epoch: int | None,
        foreground_boot_id: str | None,
        foreground_heartbeat_monotonic: float | None,
        foreground_lease_seconds: float | None,
    ) -> None:
        (snapshot.staging_directory / ".snapshot-owner.json").unlink(missing_ok=True)
        _durable_replace(snapshot.staging_directory, run_directory)
        (run_directory / ".lock").touch(exist_ok=True)
        now = created_at
        progress_sample = self._lease_clock()
        projection = {
            "schema_version": 2,
            "run_id": run_id,
            "workflow": request.workflow_name,
            "workflow_version": snapshot.workflow_version,
            "snapshot_format_version": 1,
            "definition_digest": request.definition_digest,
            "policy_digest": request.policy_digest,
            "input_manifest_digest": request.input_manifest_digest,
            "trigger": request.trigger_source,
            "provenance": dict(provenance_record),
            "idempotency_namespace_digest": namespace_digest,
            "idempotency_key_digest": key_digest,
            "operator_scope_digest": operator_scope_digest,
            "run_metadata": dict(sorted((request.run_metadata or {}).items())),
            "concurrency_key": request.concurrency_key,
            "concurrency_policy": request.concurrency_policy,
            "execution_mode": request.execution_mode,
            "foreground_owner_id": foreground_owner_id,
            "foreground_lease_expires_at": foreground_lease_expires_at,
            "foreground_epoch": foreground_epoch,
            "foreground_boot_id": foreground_boot_id,
            "foreground_heartbeat_monotonic": foreground_heartbeat_monotonic,
            "foreground_lease_seconds": foreground_lease_seconds,
            "outward_action_nodes": list(snapshot.outward_action_nodes),
            "language": (
                dict(snapshot.language) if snapshot.language is not None else None
            ),
            "sealed_snapshot_digest": snapshot.sealed_snapshot_digest,
            "admission_disposition": disposition,
            "queue_position": queue_position,
            "queue_sequence": queue_sequence,
            "blocked_by_run_id": blocked_by,
            "pause_lane_policy": pause_lane_policy,
            "lane_state": lane_state,
            "state_version": 1,
            "event_sequence": 1,
            "status": status,
            "started_at": now if status == "running" else None,
            "created_at": now,
            "updated_at": now,
            "last_semantic_progress_at": None,
            "last_semantic_progress_monotonic": None,
            "last_semantic_progress_boot_id": None,
            "last_runnable_progress_at": now,
            "last_runnable_progress_monotonic": progress_sample.monotonic_now,
            "last_runnable_progress_boot_id": progress_sample.boot_id,
            "progress_boot_id": progress_sample.boot_id,
            "nodes": {str(node["id"]): dict(node) for node in snapshot.nodes},
            "artifacts": [],
            "warnings": [],
            "last_error": None,
            "pending_interaction": None,
        }
        event = {
            "sequence": 1,
            "timestamp": now,
            "run_id": run_id,
            "node_id": None,
            "attempt_id": None,
            "event_type": "run_admitted",
            "payload": {"disposition": disposition, "status": status},
            **_recovery_fields(projection),
        }
        _, encoded_event = _encode_journal_frame(event)
        _atomic_json(run_directory / "run.json", projection)
        _atomic_bytes(run_directory / "events.jsonl", encoded_event)

    def _mark_reservation_published(self, run_id: str, *, status: str) -> None:
        with self._connect() as connection:
            reserved = connection.execute(
                "SELECT run_directory, scheduled_at FROM runs "
                "WHERE run_id=? AND admission_state='reserved'",
                (run_id,),
            ).fetchone()
            if reserved is None:
                raise RuntimeError(f"admission reservation is not active: {run_id}")
            projection, _, journal_sha256 = self._corroborate_run_evidence(
                Path(reserved["run_directory"]), run_id=run_id
            )
            if projection["status"] != status:
                raise JournalRecoveryError(
                    "reservation projection status does not match publication"
                )
            self._scheduled_at_from_projection(
                projection, indexed=reserved["scheduled_at"]
            )
            updated = connection.execute(
                "UPDATE runs SET admission_state='published', status=?, "
                "desired_status=NULL, staging_directory=NULL, updated_at=? "
                "WHERE run_id=? AND admission_state='reserved'",
                (status, _utc_now(), run_id),
            ).rowcount
            if updated == 1:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=journal_sha256,
                )
                self._record_coordinator_wake(
                    connection,
                    run_id=run_id,
                    reason_code="run_admitted",
                )
        if updated != 1:
            raise RuntimeError(f"admission reservation is not active: {run_id}")
        self._notify_coordinator()

    def run_directory(self, run_id: str, *, operator_scope: str | None = None) -> Path:
        scope_digest = self._scope_digest(operator_scope)
        scope_clause = (
            " AND operator_scope_digest=?" if operator_scope is not None else ""
        )
        values: tuple[object, ...] = (
            (run_id, scope_digest) if operator_scope is not None else (run_id,)
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_directory FROM runs "
                f"WHERE run_id=? AND admission_state='published'{scope_clause}",
                values,
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Path(row["run_directory"])

    def _run_lock_path(self, run_id: str) -> Path:
        return self.locks_root / f"{run_id}.lock"

    @staticmethod
    def _validate_journal_frame(event: object, *, line_number: int) -> None:
        if not isinstance(event, dict):
            raise JournalRecoveryError(
                f"journal event at line {line_number} is not an object"
            )
        frame_version = event.get("frame_version")
        if frame_version is None:
            if event.get("schema_version") != 1:
                raise JournalRecoveryError(
                    f"unsupported journal schema at line {line_number}"
                )
            return
        if frame_version != 1 or event.get("schema_version") != 2:
            raise JournalRecoveryError(
                f"unsupported journal frame at line {line_number}"
            )
        checksum = event.get("frame_sha256")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise JournalRecoveryError(
                f"journal frame checksum missing at line {line_number}"
            )
        if not hmac.compare_digest(checksum, _journal_frame_digest(event)):
            raise JournalRecoveryError(
                f"journal frame checksum mismatch at line {line_number}"
            )

    def _read_journal_events(
        self,
        directory: Path,
        *,
        recover_torn_tail: bool = True,
        journal_data: bytes | None = None,
    ) -> list[dict[str, object]]:
        journal_path = directory / "events.jsonl"
        if journal_data is None:
            try:
                data = journal_path.read_bytes()
            except OSError as exc:
                raise JournalRecoveryError(f"journal unavailable: {exc}") from exc
        else:
            data = journal_data
        raw_frames = data.splitlines(keepends=True)
        events: list[dict[str, object]] = []
        offset = 0
        for index, raw_frame in enumerate(raw_frames):
            line_number = index + 1
            complete = raw_frame.endswith(b"\n")
            content = raw_frame[:-1] if complete else raw_frame
            if not content.strip():
                offset += len(raw_frame)
                continue
            try:
                event = json.loads(content.decode("utf-8"))
                self._validate_journal_frame(event, line_number=line_number)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                JournalRecoveryError,
            ) as exc:
                is_torn_tail = index == len(raw_frames) - 1 and not complete
                if (
                    recover_torn_tail
                    and journal_data is None
                    and is_torn_tail
                    and events
                ):
                    preserved = directory / (f"events.jsonl.torn-{uuid.uuid4().hex}")
                    _atomic_bytes(preserved, data[offset:])
                    _atomic_bytes(journal_path, data[:offset])
                    return events
                if isinstance(exc, JournalRecoveryError):
                    raise
                raise JournalRecoveryError(
                    f"malformed journal event at line {line_number}"
                ) from exc
            events.append(event)
            offset += len(raw_frame)
        if journal_data is None and events and data and not data.endswith(b"\n"):
            _atomic_bytes(journal_path, data + b"\n")
        return events

    @staticmethod
    def _journal_may_contain_typed_mirror_events(directory: Path) -> bool:
        """Parse event types cheaply before deciding a full mirror replay is unnecessary."""
        try:
            data = (directory / "events.jsonl").read_bytes()
        except OSError as exc:
            raise JournalRecoveryError(f"journal unavailable: {exc}") from exc
        for raw_frame in data.splitlines():
            if not raw_frame.strip():
                continue
            try:
                event = json.loads(raw_frame.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Full replay owns malformed/torn-frame recovery and taxonomy.
                return True
            if isinstance(event, Mapping) and event.get("event_type") in {
                "typed_mirror_required",
                "typed_mirror_completed",
            }:
                return True
        return False

    def _read_journal_tail_event(self, directory: Path) -> dict[str, object]:
        journal_path = directory / "events.jsonl"
        try:
            data = journal_path.read_bytes()
        except OSError as exc:
            raise JournalRecoveryError(f"journal unavailable: {exc}") from exc
        populated: list[tuple[int, int, bytes]] = []
        offset = 0
        for line_number, raw_frame in enumerate(
            data.splitlines(keepends=True), 1
        ):
            if raw_frame.rstrip(b"\r\n").strip():
                populated.append((line_number, offset, raw_frame))
            offset += len(raw_frame)
        if not populated:
            raise JournalRecoveryError("journal contains no events")
        line_number, frame_offset, raw_frame = populated[-1]
        complete = raw_frame.endswith(b"\n")
        content = raw_frame[:-1] if complete else raw_frame
        try:
            event = json.loads(content.decode("utf-8"))
            self._validate_journal_frame(event, line_number=line_number)
        except (UnicodeDecodeError, json.JSONDecodeError, JournalRecoveryError) as exc:
            if not complete and len(populated) > 1:
                preserved = directory / f"events.jsonl.torn-{uuid.uuid4().hex}"
                _atomic_bytes(preserved, data[frame_offset:])
                _atomic_bytes(journal_path, data[:frame_offset])
                return self._read_journal_tail_event(directory)
            if isinstance(exc, JournalRecoveryError):
                raise
            raise JournalRecoveryError(
                f"malformed journal event at line {line_number}"
            ) from exc
        if not complete:
            _atomic_bytes(journal_path, data + b"\n")
        return event

    def load_run(
        self, run_id: str, *, operator_scope: str | None = None
    ) -> dict[str, object]:
        return self._load_run_projection(
            run_id,
            operator_scope=operator_scope,
            recover_typed_publications=True,
        )

    def _load_run_metadata(
        self, run_id: str, *, operator_scope: str | None = None
    ) -> dict[str, object]:
        """Load checked run metadata without opening typed-publication bodies."""
        return self._load_run_projection(
            run_id,
            operator_scope=operator_scope,
            recover_typed_publications=False,
        )

    def _load_run_projection(
        self,
        run_id: str,
        *,
        operator_scope: str | None,
        recover_typed_publications: bool,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        path = directory / "run.json"
        with workflow_lock(self._run_lock_path(run_id)):
            # Cleanup first atomically moves a run out of the published tree,
            # then removes its database row after deleting the quarantine copy.
            # A reader may have resolved the row before that move.  Treat the
            # vanished directory as removal instead of trying to rebuild it.
            if not directory.is_dir():
                raise KeyError(run_id)
            try:
                projection = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                projection = None
            except (json.JSONDecodeError, OSError):
                projection = None
            if self._valid_projection(projection, run_id=run_id):
                try:
                    journal_current = self._journal_matches_projection(
                        directory, projection=projection, run_id=run_id
                    )
                except (JournalRecoveryError, OSError):
                    # This corroborates only the resolved run directory. Store-level
                    # index/generation checks retain their global repair markers.
                    self._transition_run_repair(
                        "run_evidence_uncorroborated",
                        run_id=run_id,
                        outcome="repair_required",
                    )
                    raise
                if journal_current:
                    try:
                        migrated = _validate_typed_publication_metadata(
                            directory,
                            projection,
                            migrate_legacy=recover_typed_publications,
                        )
                    except JournalRecoveryError:
                        self._transition_run_repair(
                            "typed_publication_integrity",
                            run_id=run_id,
                            outcome="repair_required",
                        )
                        raise
                    if not recover_typed_publications:
                        self._sync_loaded_integrity(
                            directory,
                            projection,
                            migrate_legacy_typed_publications=False,
                        )
                        self._transition_run_repair(
                            "run_evidence_uncorroborated",
                            run_id=run_id,
                            outcome="repair_verified",
                        )
                        return projection
                    if migrated:
                        self._append_locked(
                            directory,
                            projection,
                            "typed_publication_migrated",
                            {"descriptor_count": migrated},
                        )
                    verified_content = self._recover_typed_publications_locked(
                        directory, projection
                    )
                    self._recover_typed_mirrors_locked(
                        directory,
                        projection,
                        verified_content,
                    )
                    self._sync_loaded_integrity(directory, projection)
                    self._transition_run_repair(
                        "run_evidence_uncorroborated",
                        run_id=run_id,
                        outcome="repair_verified",
                    )
                    return projection
            if path.exists():
                quarantine = directory / f"run.json.corrupt-{uuid.uuid4().hex}"
                _durable_replace(path, quarantine)
            try:
                rebuilt = self._rebuild_projection(
                    directory,
                    run_id=run_id,
                    migrate_legacy_typed_publications=(
                        recover_typed_publications
                    ),
                )
            except (JournalRecoveryError, OSError, ValueError, json.JSONDecodeError):
                # Projection replay is confined to this run. Cross-run/index
                # reconciliation failures are handled by their global callers.
                self._transition_run_repair(
                    "run_evidence_uncorroborated",
                    run_id=run_id,
                    outcome="repair_required",
                )
                raise
            if recover_typed_publications:
                verified_content = self._recover_typed_publications_locked(
                    directory, rebuilt
                )
                self._recover_typed_mirrors_locked(
                    directory,
                    rebuilt,
                    verified_content,
                )
            scheduled_at = self._scheduled_at_from_projection(rebuilt)
            _atomic_json(path, rebuilt)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=?, scheduled_at=? "
                    "WHERE run_id=?",
                    (
                        rebuilt["status"],
                        rebuilt["updated_at"],
                        scheduled_at,
                        run_id,
                    ),
                )
                self._sync_integrity_index(
                    connection,
                    projection=rebuilt,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
            self._transition_run_repair(
                "run_evidence_uncorroborated",
                run_id=run_id,
                outcome="repair_verified",
            )
            return rebuilt

    def lookup_publication(
        self,
        run_id: str,
        publication_id: str,
        *,
        operator_scope: str | None = None,
    ) -> VerifiedPublication:
        """Authorize and verify one checked publication without sweeping siblings."""
        # Metadata loading performs run/scope authorization before the caller's
        # opaque ID is examined and never opens typed-publication bodies.
        try:
            projection = self._load_run_metadata(
                run_id,
                operator_scope=operator_scope,
            )
        except ArchonOutputUnavailableError as exc:
            raise PublicationUnavailableError(
                "publication metadata is temporarily unavailable"
            ) from exc
        except JournalRecoveryError as exc:
            if "typed_publication_integrity" in (
                self._active_run_repair_reasons(run_id)
            ):
                raise PublicationIntegrityError(
                    "publication descriptor is not corroborated"
                ) from exc
            raise
        if (
            not isinstance(publication_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", publication_id) is None
        ):
            raise PublicationNotFoundError(publication_id)
        directory = self.run_directory(
            run_id,
            operator_scope=operator_scope,
        )
        with workflow_lock(self._run_lock_path(run_id)):
            try:
                current = json.loads(
                    (directory / "run.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise JournalRecoveryError(
                    "run projection is unavailable during publication lookup"
                ) from exc
            if (
                not self._valid_projection(current, run_id=run_id)
                or _projection_digest(current) != _projection_digest(projection)
                or not self._journal_matches_projection(
                    directory,
                    projection=current,
                    run_id=run_id,
                )
            ):
                raise JournalRecoveryError(
                    "run projection changed during publication lookup"
                )
            try:
                descriptor = self._requested_publication_descriptor(
                    directory,
                    current,
                    publication_id,
                )
                content = _read_descriptor_relative(
                    directory,
                    (
                        f"publications/{descriptor.publication_id}/"
                        f"{descriptor.content_name}"
                    ),
                    size_bytes=descriptor.size_bytes,
                )
                if (
                    len(content) != descriptor.size_bytes
                    or not hmac.compare_digest(
                        _sha256(content),
                        descriptor.sha256,
                    )
                ):
                    raise PublicationIntegrityError(
                        "publication content identity changed"
                    )
            except PublicationNotFoundError:
                raise
            except ArchonOutputUnavailableError as exc:
                raise PublicationUnavailableError(
                    "publication content is temporarily unavailable"
                ) from exc
            except (
                ArchonOutputIntegrityError,
                JournalRecoveryError,
                OSError,
                PublicationIntegrityError,
            ) as exc:
                self._transition_run_repair(
                    "typed_publication_integrity",
                    run_id=run_id,
                    outcome="repair_required",
                )
                if isinstance(exc, PublicationIntegrityError):
                    raise
                raise PublicationIntegrityError(
                    "publication descriptor or content is not corroborated"
                ) from exc
        return VerifiedPublication(
            publication_id=descriptor.publication_id,
            content_name=descriptor.content_name,
            output_type=descriptor.output_type,
            media_type=descriptor.media_type,
            size_bytes=descriptor.size_bytes,
            sha256=descriptor.sha256,
            node_id=descriptor.node_id,
            attempt_id=descriptor.attempt_id,
            schema_fingerprint=descriptor.schema_fingerprint,
            produced_at=descriptor.produced_at,
            session_id=descriptor.session_id,
            content=content,
        )

    def _requested_publication_descriptor(
        self,
        directory: Path,
        projection: Mapping[str, object],
        publication_id: str,
    ) -> _JournaledTypedPublication:
        artifacts = projection.get("artifacts")
        if not isinstance(artifacts, list):
            raise JournalRecoveryError(
                "typed publication descriptor authority is invalid"
            )
        matches = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, Mapping)
            and artifact.get("publication_id") == publication_id
        ]
        if not matches:
            raise PublicationNotFoundError(publication_id)
        declarations = _sealed_typed_output_declarations(
            directory,
            projection,
        )
        requested_node_ids = {
            artifact.get("node_id")
            for artifact in matches
            if isinstance(artifact.get("node_id"), str)
        }
        selected_declarations = {
            node_id: declarations[node_id]
            for node_id in requested_node_ids
            if node_id in declarations
        }
        selected_projection = dict(projection)
        selected_projection["artifacts"] = matches
        descriptors = _journaled_typed_publications(
            selected_projection,
            selected_declarations,
        )
        if len(descriptors) != 1:
            raise JournalRecoveryError(
                "requested typed publication descriptor is ambiguous"
            )
        descriptor = descriptors[0]
        expected_metadata = self._expected_publication_metadata(descriptor)
        if not hmac.compare_digest(
            _sha256(expected_metadata),
            descriptor.metadata_sha256,
        ):
            raise JournalRecoveryError(
                "requested typed publication metadata is not corroborated"
            )
        return descriptor

    def _journal_matches_projection(
        self,
        directory: Path,
        *,
        projection: Mapping[str, object],
        run_id: str,
    ) -> bool:
        """Validate the durable journal head without replaying on every read."""
        latest = self._read_journal_tail_event(directory)
        if latest.get("run_id") != run_id:
            raise JournalRecoveryError("journal run identity mismatch")
        if latest["sequence"] < projection["event_sequence"]:
            raise JournalRecoveryError("projection is ahead of its journal")
        if latest["sequence"] > projection["event_sequence"]:
            return False
        if "projection_sha256" in latest:
            snapshot = latest.get("projection")
            if snapshot is not None:
                if not self._valid_projection(snapshot, run_id=run_id):
                    raise JournalRecoveryError("journal head has no valid projection")
                if latest["projection_sha256"] != _projection_digest(snapshot):
                    raise JournalRecoveryError("journal projection digest mismatch")
            return latest.get("projection_sha256") == _projection_digest(projection)
        return True

    def _valid_projection(
        self,
        value: object,
        *,
        run_id: str,
        private_authorities: Mapping[
            str, Mapping[str, Mapping[str, object]]
        ] | None = None,
    ) -> bool:
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            return False
        if value.get("schema_version") not in {1, 2}:
            return False
        if value.get("status") not in _PROJECTION_STATUSES:
            return False
        for field in ("event_sequence", "state_version"):
            if (
                isinstance(value.get(field), bool)
                or not isinstance(value.get(field), int)
                or value[field] < 1
            ):
                return False
        if not isinstance(value.get("artifacts"), list) or not isinstance(
            value.get("warnings"), list
        ):
            return False
        nodes = value.get("nodes")
        if not isinstance(nodes, dict) or not nodes:
            return False
        for node_id, node in nodes.items():
            if (
                not isinstance(node_id, str)
                or not isinstance(node, dict)
                or node.get("id") != node_id
                or node.get("state") not in _NODE_STATES
                or not isinstance(node.get("depends_on"), list)
                or not isinstance(node.get("attempts"), list)
            ):
                return False
            claim = node.get("claim")
            if claim is not None and (
                not isinstance(claim, dict)
                or not isinstance(claim.get("attempt_id"), str)
                or not isinstance(claim.get("lease_expires_at"), str)
            ):
                return False
        next_registry_update_at = value.get("next_registry_update_at")
        try:
            pending = _pending_session_registry_payloads(value)
        except JournalRecoveryError:
            return False
        if private_authorities is None:
            try:
                directory = self.run_directory(run_id)
            except KeyError:
                private_authorities = self._read_private_session_authorities(
                    run_id=run_id
                )
            else:
                private_authorities = self._read_bound_private_session_authorities(
                    run_id=run_id,
                    events=self._read_journal_events(directory),
                )
        if not self._private_session_authorities_match_projection(
            value,
            private_authorities=private_authorities,
        ):
            return False
        if not pending:
            return next_registry_update_at is None
        retry_counts = []
        for payload in pending.values():
            try:
                candidate, retry_count = _session_registry_candidate_from_payload(
                    payload
                )
            except JournalRecoveryError:
                return False
            winning_node = nodes.get(candidate.winning_node_id)
            if (
                candidate.winning_run_id != run_id
                or candidate.key.workflow != value.get("workflow")
                or candidate.key.node_id != candidate.winning_node_id
                or candidate.key.scope
                != str(value.get("operator_scope_digest") or "local")
                or not isinstance(winning_node, dict)
                or winning_node.get("state") != "succeeded"
                or value.get("status")
                in {"succeeded", "failed", "cancelled", "abandoned"}
                or not _session_registry_candidate_is_corroborated(value, candidate)
                or not self._private_session_registry_authority_matches(
                    value,
                    candidate,
                    private_authorities=private_authorities,
                )
            ):
                return False
            retry_counts.append(retry_count)
        retrying = [count for count in retry_counts if count > 0]
        if not retrying:
            if next_registry_update_at is not None:
                return False
        else:
            if len(retrying) != 1:
                return False
            if not isinstance(next_registry_update_at, str):
                return False
            try:
                wake = datetime.fromisoformat(next_registry_update_at)
            except ValueError:
                return False
            if wake.tzinfo is None or wake.utcoffset() is None:
                return False
        if value.get("status") == "recovery_pending" and retrying != [5]:
            return False
        return True

    def _rebuild_projection(
        self,
        directory: Path,
        *,
        run_id: str,
        journal_data: bytes | None = None,
        migrate_legacy_typed_publications: bool = True,
    ) -> dict[str, object]:
        latest = None
        latest_migration_count = 0
        expected_sequence = 1
        events = self._read_journal_events(
            directory,
            journal_data=journal_data,
        )
        private_authorities = self._read_bound_private_session_authorities(
            run_id=run_id,
            events=events,
        )
        for event in events:
            if event.get("sequence") != expected_sequence:
                raise JournalRecoveryError(
                    f"journal sequence gap: expected {expected_sequence}, "
                    f"received {event.get('sequence')}"
                )
            if event.get("run_id") != run_id:
                raise JournalRecoveryError("journal run identity mismatch")
            snapshot = event.get("projection")
            checksum = event.get("projection_sha256")
            if self._valid_projection(
                snapshot,
                run_id=run_id,
                private_authorities=private_authorities,
            ):
                if snapshot["event_sequence"] != expected_sequence:
                    raise JournalRecoveryError("journal projection sequence mismatch")
                if checksum != _projection_digest(snapshot):
                    raise JournalRecoveryError("journal projection digest mismatch")
                try:
                    latest_migration_count = (
                        _validate_typed_publication_metadata(
                            directory,
                            snapshot,
                            migrate_legacy=(
                                migrate_legacy_typed_publications
                            ),
                        )
                    )
                except JournalRecoveryError:
                    self._transition_run_repair(
                        "typed_publication_integrity",
                        run_id=run_id,
                        outcome="repair_required",
                    )
                    raise
                latest = snapshot
            elif event.get("event_type") == "node_heartbeat" and latest is not None:
                node = latest["nodes"].get(event.get("node_id"))
                claim = node.get("claim") if isinstance(node, dict) else None
                if not isinstance(claim, dict) or claim.get("attempt_id") != event.get(
                    "attempt_id"
                ):
                    raise JournalRecoveryError("heartbeat claim identity mismatch")
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    raise JournalRecoveryError("heartbeat payload is malformed")
                required = {
                    "heartbeat_at",
                    "heartbeat_monotonic",
                    "lease_expires_at",
                    "lease_seconds",
                }
                if not required <= payload.keys():
                    raise JournalRecoveryError("heartbeat payload is incomplete")
                claim.update({key: payload[key] for key in required})
                latest["event_sequence"] = expected_sequence
                latest["state_version"] = int(latest["state_version"]) + 1
                latest["updated_at"] = event.get("timestamp")
                if checksum != _projection_digest(latest):
                    raise JournalRecoveryError("journal projection digest mismatch")
            else:
                raise JournalRecoveryError(
                    f"journal event {expected_sequence} has no valid recovery data"
                )
            expected_sequence += 1
        if latest is None:
            raise JournalRecoveryError("journal contains no recoverable projection")
        if latest_migration_count:
            self._append_locked(
                directory,
                latest,
                "typed_publication_migrated",
                {"descriptor_count": latest_migration_count},
            )
        return latest

    def request_runnable(
        self,
        run_id: str,
        reason: str,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        """Request one capacity- and lane-checked runnable transition."""
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise ValueError("runnable reason must be bounded non-empty text")
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            return self._request_runnable_locked(
                directory,
                projection,
                reason=reason,
                expected_version=expected_version,
            )

    @staticmethod
    def _eligible_queued_predecessor(
        connection: sqlite3.Connection,
        *,
        run_id: str | None,
        now: datetime,
        before_sequence: int | None = None,
    ):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        observed = now.astimezone(timezone.utc)
        second = observed.isoformat(timespec="seconds").removesuffix("+00:00")
        fractional_bound = f"{second}.{observed.microsecond:06d}"
        clauses = [
            "candidate.status='queued'",
            "(candidate.scheduled_at IS NULL OR candidate.scheduled_at<? "
            "OR candidate.scheduled_at=? OR candidate.scheduled_at=?)",
            "(candidate.concurrency_policy='allow' OR NOT EXISTS ("
            "SELECT 1 FROM runs AS holder WHERE holder.run_id<>candidate.run_id "
            "AND holder.workflow_name=candidate.workflow_name "
            "AND holder.concurrency_key=candidate.concurrency_key "
            "AND holder.lane_state='held'))",
        ]
        values: list[object] = [
            fractional_bound,
            f"{second}Z",
            f"{fractional_bound}Z",
        ]
        if run_id is not None:
            clauses.insert(0, "candidate.run_id<>?")
            values.insert(0, run_id)
        if before_sequence is not None:
            clauses.append("candidate.queue_sequence<?")
            values.append(before_sequence)
        return connection.execute(
            "SELECT candidate.run_id FROM runs AS candidate WHERE "
            + " AND ".join(clauses)
            + " ORDER BY candidate.queue_sequence, candidate.created_at, "
            "candidate.run_id LIMIT 1",
            tuple(values),
        ).fetchone()

    def _request_runnable_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        *,
        reason: str,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        if expected_version is not None and int(projection["state_version"]) != int(
            expected_version
        ):
            raise RuntimeError("stale runnable request")
        if projection.get("status") in {"running", "queued"}:
            return projection
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT workflow_name, concurrency_key, concurrency_policy "
                "FROM runs WHERE run_id=?",
                (projection["run_id"],),
            ).fetchone()
            if row is None:
                raise KeyError(str(projection["run_id"]))
            active = None
            if row["concurrency_policy"] != "allow":
                active = connection.execute(
                    "SELECT run_id FROM runs WHERE run_id<>? AND workflow_name=? "
                    "AND concurrency_key=? AND lane_state='held' "
                    "ORDER BY created_at, run_id LIMIT 1",
                    (
                        projection["run_id"],
                        row["workflow_name"],
                        row["concurrency_key"],
                    ),
                ).fetchone()
            running = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE status='running'"
                ).fetchone()[0]
            )
            older_queued = self._eligible_queued_predecessor(
                connection,
                run_id=str(projection["run_id"]),
                now=self._lease_clock().utc_now,
            )
            if (
                active is None
                and older_queued is None
                and running < self.limits["executing"]
            ):
                projection["status"] = "running"
                projection["queue_position"] = None
                projection["queue_sequence"] = None
                projection["blocked_by_run_id"] = None
                event_type = "run_runnable"
            else:
                sequence = projection.get("queue_sequence")
                if not isinstance(sequence, int):
                    sequence = self._next_queue_sequence(connection)
                projection["status"] = "queued"
                projection["queue_position"] = sequence
                projection["queue_sequence"] = sequence
                projection["blocked_by_run_id"] = (
                    str(active["run_id"]) if active is not None else None
                )
                event_type = "run_runnable_queued"
            self._append_locked(
                directory,
                projection,
                event_type,
                {"reason_code": reason},
                defer_notification=True,
            )
            self._sync_integrity_index(
                connection,
                projection=projection,
                journal_sha256=_sha256((directory / "events.jsonl").read_bytes()),
            )
            self._record_coordinator_wake(
                connection,
                run_id=str(projection["run_id"]),
                reason_code=reason,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._notify_coordinator()
        return projection

    def _scheduled_promotion_authorization(
        self,
        run_id: str,
        verify: Callable[[Mapping[str, object]], None],
        *,
        resource_read_budget: WorkflowResourceReadBudget | None = None,
    ) -> _ScheduledPromotionAuthorization:
        if not isinstance(run_id, str) or not run_id or not callable(verify):
            raise ValueError("scheduled authorization requires a run and verifier")
        return _ScheduledPromotionAuthorization(
            self._scheduled_authorization_identity,
            run_id,
            verify,
            resource_read_budget,
        )

    def _scheduled_promotion_read_budget(
        self,
        authorization: object,
        run_id: str,
    ) -> WorkflowResourceReadBudget | None:
        verified = self._validate_scheduled_promotion_authorization(
            authorization,
            run_id,
        )
        return verified._resource_read_budget

    def _validate_scheduled_promotion_authorization(
        self,
        authorization: object,
        run_id: str,
    ) -> _ScheduledPromotionAuthorization:
        if (
            type(authorization) is not _ScheduledPromotionAuthorization
            or authorization._store_identity is not self._scheduled_authorization_identity
        ):
            raise RuntimeError("opaque scheduled authorization is required")
        if authorization._run_id != run_id:
            raise RuntimeError("scheduled authorization belongs to a different run")
        if authorization._consumed:
            raise RuntimeError("scheduled authorization was already consumed")
        return authorization

    def _consume_scheduled_promotion_authorization(
        self,
        authorization: object,
        run_id: str,
        projection: Mapping[str, object],
    ) -> None:
        verified = self._invalidate_scheduled_promotion_authorization(
            authorization,
            run_id,
        )
        verified._verify(projection)

    def _invalidate_scheduled_promotion_authorization(
        self,
        authorization: object,
        run_id: str,
    ) -> _ScheduledPromotionAuthorization:
        verified = self._validate_scheduled_promotion_authorization(
            authorization,
            run_id,
        )
        verified._consumed = True
        return verified

    def _fail_scheduled_revalidation_locked(
        self,
        connection: sqlite3.Connection,
        directory: Path,
        projection: dict[str, object],
    ) -> None:
        projection["status"] = "failed"
        projection["queue_position"] = None
        projection["queue_sequence"] = None
        projection["blocked_by_run_id"] = None
        projection["last_error"] = {
            "code": "schedule_revalidation_failed",
            "message": "scheduled run authorization changed before execution",
        }
        nodes = projection.get("nodes")
        if not isinstance(nodes, Mapping):
            raise RuntimeError("scheduled run nodes are missing")
        for node in nodes.values():
            if not isinstance(node, dict):
                raise RuntimeError("scheduled run node is malformed")
            if node["state"] not in {"succeeded", "failed", "skipped"}:
                node.pop("claim", None)
                node["state"] = "cancelled"
        self._append_locked(
            directory,
            projection,
            "run_failed",
            {"reason_code": "schedule_revalidation_failed"},
            defer_notification=True,
        )
        self._sync_integrity_index(
            connection,
            projection=projection,
            journal_sha256=_sha256((directory / "events.jsonl").read_bytes()),
        )
        self._record_coordinator_wake(
            connection,
            run_id=str(projection["run_id"]),
            reason_code="run_failed",
        )

    def fail_scheduled_revalidation(
        self,
        run_id: str,
        *,
        expected_state_version: int,
    ) -> bool:
        """Atomically terminalize a still-queued scheduled run before claim."""
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status, scheduled_at FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                projection = json.loads((directory / "run.json").read_text())
                self._scheduled_at_from_projection(
                    projection,
                    indexed=row["scheduled_at"],
                )
                if (
                    row["status"] != "queued"
                    or int(projection.get("state_version", -1))
                    != expected_state_version
                ):
                    connection.rollback()
                    return False
                self._fail_scheduled_revalidation_locked(
                    connection,
                    directory,
                    projection,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        self._notify_coordinator()
        return True

    def _fail_scheduled_package_preparation(
        self,
        run_id: str,
        authorization: object,
    ) -> bool:
        """Consume server authority and fail a queued run whose package cannot load."""
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._invalidate_scheduled_promotion_authorization(
                    authorization,
                    run_id,
                )
                row = connection.execute(
                    "SELECT status, scheduled_at FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                projection = json.loads((directory / "run.json").read_text())
                scheduled_at = self._scheduled_at_from_projection(
                    projection,
                    indexed=row["scheduled_at"],
                )
                metadata = projection.get("run_metadata")
                if (
                    scheduled_at is None
                    or not isinstance(metadata, Mapping)
                    or not isinstance(metadata.get("execution_identity"), str)
                ):
                    raise RuntimeError(
                        "scheduled package failure requires server admission evidence"
                    )
                if row["status"] != "queued":
                    connection.rollback()
                    return False
                self._fail_scheduled_revalidation_locked(
                    connection,
                    directory,
                    projection,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        self._notify_coordinator()
        return True

    def _fail_package_validation(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        error_code: str,
        error_path: str,
        error_message: str,
        schedule_revalidation: object | None = None,
    ) -> bool:
        """Atomically fail a claim-free run on authenticated package validation."""
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version < 1
        ):
            raise ValueError("expected_state_version must be a positive integer")
        bounded = (
            ("error_code", error_code, 128),
            ("error_path", error_path, 1_024),
            ("error_message", error_message, 2_000),
        )
        if any(
            not isinstance(value, str) or not value or len(value) > maximum
            for _name, value, maximum in bounded
        ):
            raise ValueError("package validation diagnostics must be bounded text")
        safe_message = _sanitize_diagnostic(error_message)
        if safe_message is None:
            raise ValueError("package validation message must be bounded text")

        directory = self.run_directory(run_id)
        changed = False
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status, scheduled_at FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                projection = json.loads((directory / "run.json").read_text())
                scheduled_at = self._scheduled_at_from_projection(
                    projection,
                    indexed=row["scheduled_at"],
                )
                status = projection.get("status")
                if (
                    row["status"] != status
                    or status not in {"queued", "running"}
                    or int(projection.get("state_version", -1))
                    != expected_state_version
                    or projection.get("desired_status") is not None
                ):
                    connection.rollback()
                    return False
                metadata = projection.get("run_metadata")
                if schedule_revalidation is not None:
                    if (
                        scheduled_at is None
                        or status != "queued"
                        or not isinstance(metadata, Mapping)
                        or not isinstance(metadata.get("execution_identity"), str)
                    ):
                        raise RuntimeError(
                            "scheduled package failure requires server admission evidence"
                        )
                elif scheduled_at is not None:
                    connection.rollback()
                    return False

                nodes = projection.get("nodes")
                if not isinstance(nodes, Mapping):
                    raise RuntimeError("workflow run nodes are missing")
                projected_claim = any(
                    not isinstance(node, Mapping)
                    or isinstance(node.get("claim"), Mapping)
                    or node.get("state") in {"claimed", "running"}
                    for node in nodes.values()
                )
                indexed_claim = connection.execute(
                    "SELECT 1 FROM worker_claims WHERE run_id=? LIMIT 1",
                    (run_id,),
                ).fetchone()
                if projected_claim or indexed_claim is not None:
                    connection.rollback()
                    return False

                if schedule_revalidation is not None:
                    from plugins.workflow.scheduled_revalidation import (
                        ScheduledRunRevalidationError,
                    )

                    try:
                        self._consume_scheduled_promotion_authorization(
                            schedule_revalidation,
                            run_id,
                            projection,
                        )
                    except ScheduledRunRevalidationError:
                        self._fail_scheduled_revalidation_locked(
                            connection,
                            directory,
                            projection,
                        )
                        connection.commit()
                        changed = True
                if not changed:
                    projection["status"] = "failed"
                    projection["queue_position"] = None
                    projection["queue_sequence"] = None
                    projection["blocked_by_run_id"] = None
                    projection["last_error"] = {
                        "code": error_code,
                        "path": error_path,
                        "message": safe_message,
                    }
                    for node in nodes.values():
                        if node["state"] not in {"succeeded", "failed", "skipped"}:
                            node["state"] = "cancelled"
                    self._append_locked(
                        directory,
                        projection,
                        "run_failed",
                        {
                            "reason_code": "package_validation_failed",
                            "validation_code": error_code,
                            "validation_path": error_path,
                        },
                        defer_notification=True,
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    self._record_coordinator_wake(
                        connection,
                        run_id=run_id,
                        reason_code="run_failed",
                    )
                    connection.commit()
                    changed = True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        if changed:
            self._notify_coordinator()
        return changed

    def try_promote_run(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        schedule_revalidation=None,
    ) -> bool:
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT workflow_name, concurrency_key, concurrency_policy, "
                    "status, queue_sequence, scheduled_at FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None or row["status"] != "queued":
                    connection.rollback()
                    return bool(row and row["status"] == "running")
                projection = json.loads((directory / "run.json").read_text())
                scheduled_at = self._scheduled_at_from_projection(
                    projection,
                    indexed=row["scheduled_at"],
                )
                if scheduled_at is not None:
                    if now is None:
                        raise ValueError("now is required for scheduled promotion")
                    if rfc3339_instant_is_after(scheduled_at, now):
                        if (
                            projection.get("queue_position") is not None
                            or projection.get("blocked_by_run_id") is not None
                        ):
                            projection["queue_position"] = None
                            projection["blocked_by_run_id"] = None
                            self._append_locked(
                                directory,
                                projection,
                                "run_scheduled_wait",
                                {"reason_code": "clock_before_schedule"},
                            )
                            self._sync_integrity_index(
                                connection,
                                projection=projection,
                                journal_sha256=_sha256(
                                    (directory / "events.jsonl").read_bytes()
                                ),
                            )
                            connection.commit()
                        else:
                            connection.rollback()
                        return False
                    metadata = projection.get("run_metadata")
                    if not isinstance(metadata, Mapping):
                        raise RuntimeError("scheduled revalidation metadata missing")
                    if (
                        isinstance(metadata.get("execution_identity"), str)
                        and schedule_revalidation is None
                    ):
                        connection.rollback()
                        return False
                    if schedule_revalidation is not None:
                        self._validate_scheduled_promotion_authorization(
                            schedule_revalidation,
                            run_id,
                        )
                sequence = row["queue_sequence"]
                if sequence is None:
                    sequence = self._next_queue_sequence(connection)
                    connection.execute(
                        "UPDATE runs SET queue_sequence=?, queue_position=? "
                        "WHERE run_id=?",
                        (sequence, sequence, run_id),
                    )
                older = self._eligible_queued_predecessor(
                    connection,
                    run_id=run_id,
                    now=now or self._lease_clock().utc_now,
                    before_sequence=int(sequence),
                )
                active = None
                if row["concurrency_policy"] != "allow":
                    active = connection.execute(
                        "SELECT run_id FROM runs WHERE run_id<>? AND workflow_name=? "
                        "AND concurrency_key=? AND lane_state='held' LIMIT 1",
                        (run_id, row["workflow_name"], row["concurrency_key"]),
                    ).fetchone()
                running = connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE status='running'"
                ).fetchone()[0]
                if (
                    scheduled_at is not None
                    and row["concurrency_policy"] == "forbid"
                    and active is not None
                ):
                    projection["status"] = "failed"
                    projection["queue_position"] = None
                    projection["queue_sequence"] = None
                    projection["blocked_by_run_id"] = None
                    projection["last_error"] = {
                        "code": "schedule_overlap_forbidden",
                        "message": "scheduled run overlaps active same-key work",
                    }
                    for node in projection["nodes"].values():
                        if node["state"] not in {"succeeded", "failed", "skipped"}:
                            node.pop("claim", None)
                            node["state"] = "cancelled"
                    self._append_locked(
                        directory,
                        projection,
                        "run_failed",
                        {"reason_code": "schedule_overlap_forbidden"},
                        defer_notification=True,
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    self._record_coordinator_wake(
                        connection,
                        run_id=run_id,
                        reason_code="run_failed",
                    )
                    connection.commit()
                    self._notify_coordinator()
                    return False
                if older or active or running >= self.limits["executing"]:
                    blocked_by = (
                        str(active["run_id"])
                        if active is not None and "run_id" in active.keys()
                        else None
                    )
                    if (
                        projection.get("queue_position") != sequence
                        or projection.get("blocked_by_run_id") != blocked_by
                    ):
                        projection["queue_position"] = sequence
                        projection["queue_sequence"] = sequence
                        projection["blocked_by_run_id"] = blocked_by
                        self._append_locked(
                            directory,
                            projection,
                            "run_queued",
                            {"reason_code": "scheduled_lane_wait"},
                        )
                        self._sync_integrity_index(
                            connection,
                            projection=projection,
                            journal_sha256=_sha256(
                                (directory / "events.jsonl").read_bytes()
                            ),
                        )
                        connection.commit()
                    else:
                        connection.rollback()
                    return False
                admission_state_version = int(projection.get("state_version", -1))
                execution_identity = ""
                if scheduled_at is not None and schedule_revalidation is not None:
                    metadata = projection.get("run_metadata")
                    if not isinstance(metadata, Mapping):
                        raise RuntimeError("scheduled revalidation metadata missing")
                    execution_identity = str(
                        metadata.get("execution_identity") or ""
                    )
                    try:
                        self._consume_scheduled_promotion_authorization(
                            schedule_revalidation,
                            run_id,
                            projection,
                        )
                    except Exception as exc:
                        from plugins.workflow.scheduled_revalidation import (
                            ScheduledRunRevalidationError,
                        )

                        if not isinstance(exc, ScheduledRunRevalidationError):
                            raise
                        self._fail_scheduled_revalidation_locked(
                            connection,
                            directory,
                            projection,
                        )
                        connection.commit()
                        self._notify_coordinator()
                        return False
                event_now = _utc_now()
                projection["status"] = "running"
                projection["started_at"] = event_now
                projection["queue_position"] = None
                projection["queue_sequence"] = None
                projection["blocked_by_run_id"] = None
                if scheduled_at is not None and schedule_revalidation is not None:
                    projection["schedule_revalidation"] = {
                        "execution_identity": execution_identity,
                        "admission_state_version": admission_state_version,
                    }
                self._append_locked(
                    directory,
                    projection,
                    "run_promoted",
                    (
                        {"schedule_revalidated": True}
                        if scheduled_at is not None
                        and schedule_revalidation is not None
                        else None
                    ),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._record_coordinator_wake(
                    connection, run_id=run_id, reason_code="run_promoted"
                )
                connection.commit()
                self._notify_coordinator()
                return True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        projection_updates: Mapping[str, object] | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id)
        with workflow_lock(
            self._run_lock_path(run_id), timeout_seconds=lock_timeout_seconds
        ):
            projection = json.loads((directory / "run.json").read_text())
            if event_type == "semantic_progress":
                sample = self._lease_clock()
                projection["last_semantic_progress_at"] = sample.utc_now.isoformat()
                projection["last_semantic_progress_monotonic"] = sample.monotonic_now
                projection["last_semantic_progress_boot_id"] = sample.boot_id
                projection["progress_boot_id"] = sample.boot_id
            projection.update(dict(projection_updates or {}))
            return self._append_locked(
                directory,
                projection,
                event_type,
                payload,
                node_id=node_id,
                attempt_id=attempt_id,
            )

    def coordinator_candidates(
        self,
        *,
        after: tuple[str, str] | None,
        now: datetime,
        limit: int = 100,
    ) -> tuple[tuple[dict[str, object], ...], tuple[str, str] | None, bool]:
        """Return one stable keyset page of ordinary coordinator work."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            now.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("now is out of range") from exc
        if after is not None and (
            not isinstance(after, tuple)
            or len(after) != 2
            or not all(isinstance(value, str) and value for value in after)
        ):
            raise ValueError("after must be a created_at/run_id tuple")

        clauses = [
            "admission_state='published'",
            "status IN ('queued','running','waiting_retry')",
            "execution_mode IN ('background','foreground')",
            "(status<>'queued' OR scheduled_at IS NULL)",
            _RUN_SCOPED_REPAIR_EXCLUSION_SQL,
        ]
        values: list[object] = []
        if after is not None:
            clauses.append("(created_at>? OR (created_at=? AND run_id>?))")
            values.extend((after[0], after[0], after[1]))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, created_at, status, execution_mode, scheduled_at "
                "FROM runs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, run_id LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
        for row in rows:
            if row["status"] == "queued":
                projection = self._load_run_metadata(str(row["run_id"]))
                self._scheduled_at_from_projection(
                    projection,
                    indexed=row["scheduled_at"],
                )
        raw_page = rows[:limit]
        page = []
        for row in raw_page:
            if row["status"] == "running":
                projection = self._load_run_metadata(str(row["run_id"]))
                recovery_due_at = projection.get("next_registry_update_at")
                if isinstance(recovery_due_at, str):
                    try:
                        if datetime.fromisoformat(recovery_due_at) > now:
                            continue
                    except ValueError:
                        # Full projection validation owns malformed durable data.
                        self.load_run(str(row["run_id"]))
                        raise JournalRecoveryError(
                            "session registry obligation wake is invalid"
                        )
            page.append(row)
        exhausted = len(rows) <= limit
        cursor = (
            (str(raw_page[-1]["created_at"]), str(raw_page[-1]["run_id"]))
            if raw_page
            else after
        )
        return tuple(dict(row) for row in page), cursor, exhausted

    def scheduled_coordinator_candidates(
        self,
        *,
        after: tuple[str, str] | None,
        now: datetime,
        not_before: datetime | None = None,
        through_queue_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[tuple[dict[str, object], ...], tuple[str, str] | None, bool]:
        """Return one bounded exact-due page from the scheduled-run index."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            observed = now.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("now is out of range") from exc
        if not_before is not None:
            if not_before.tzinfo is None or not_before.utcoffset() is None:
                raise ValueError("not_before must be timezone-aware")
            try:
                lower_observed = not_before.astimezone(timezone.utc)
            except (OverflowError, ValueError) as exc:
                raise ValueError("not_before is out of range") from exc
            if lower_observed >= observed:
                return (), after, True
        else:
            lower_observed = None
        if after is not None and (
            not isinstance(after, tuple)
            or len(after) != 2
            or not all(isinstance(value, str) and value for value in after)
        ):
            raise ValueError("after must be a created_at/run_id tuple")
        if through_queue_sequence is not None and (
            isinstance(through_queue_sequence, bool)
            or not isinstance(through_queue_sequence, int)
            or through_queue_sequence < 0
        ):
            raise ValueError(
                "through_queue_sequence must be a non-negative integer"
            )

        second = observed.isoformat(timespec="seconds").removesuffix("+00:00")
        fractional_bound = f"{second}.{observed.microsecond:06d}"
        common_clauses = [
            "admission_state='published'",
            "status='queued'",
            "execution_mode IN ('background','foreground')",
            "scheduled_at IS NOT NULL",
            _RUN_SCOPED_REPAIR_EXCLUSION_SQL,
        ]
        common = " AND ".join(common_clauses)
        outer_clauses: list[str] = []
        outer_values: list[object] = []
        if lower_observed is not None:
            lower_second = lower_observed.isoformat(timespec="seconds").removesuffix(
                "+00:00"
            )
            lower_fractional_bound = (
                f"{lower_second}.{lower_observed.microsecond:06d}"
            )
            outer_clauses.append(
                "NOT (scheduled_at<? OR scheduled_at=? OR scheduled_at=?)"
            )
            outer_values.extend(
                (
                    lower_fractional_bound,
                    f"{lower_second}Z",
                    f"{lower_fractional_bound}Z",
                )
            )
        if after is not None:
            outer_clauses.append(
                "(created_at>? OR (created_at=? AND run_id>?))"
            )
            outer_values.extend((after[0], after[0], after[1]))
        if through_queue_sequence is not None:
            outer_clauses.append("queue_sequence<=?")
            outer_values.append(through_queue_sequence)
        columns = "run_id, created_at, status, execution_mode, scheduled_at"
        indexed_columns = f"{columns}, queue_sequence"
        due_query = (
            f"SELECT {indexed_columns} FROM runs INDEXED BY runs_scheduled_queue "
            f"WHERE {common} AND scheduled_at<? "
            "UNION ALL "
            f"SELECT {indexed_columns} FROM runs INDEXED BY runs_scheduled_queue "
            f"WHERE {common} AND scheduled_at=? "
            "UNION ALL "
            f"SELECT {indexed_columns} FROM runs INDEXED BY runs_scheduled_queue "
            f"WHERE {common} AND scheduled_at=?"
        )
        outer_where = (
            " WHERE " + " AND ".join(outer_clauses) if outer_clauses else ""
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM ({due_query})"
                f"{outer_where} ORDER BY created_at, run_id LIMIT ?",
                (
                    fractional_bound,
                    f"{second}Z",
                    f"{fractional_bound}Z",
                    *outer_values,
                    limit + 1,
                ),
            ).fetchall()
        for row in rows:
            projection = self.load_run(str(row["run_id"]))
            canonical = self._scheduled_at_from_projection(
                projection,
                indexed=row["scheduled_at"],
            )
            if canonical is None or rfc3339_instant_is_after(canonical, observed):
                raise JournalRecoveryError("scheduled due index selection mismatch")
        page = rows[:limit]
        cursor = (
            (str(page[-1]["created_at"]), str(page[-1]["run_id"]))
            if page
            else after
        )
        return tuple(dict(row) for row in page), cursor, len(rows) <= limit

    def scheduled_coordinator_generation(
        self,
        *,
        now: datetime,
    ) -> tuple[datetime, int]:
        """Capture one fixed due instant and durable admission sequence fence."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            observed = now.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("now is out of range") from exc
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE key='queue_sequence'"
            ).fetchone()
        try:
            fence = int(row["value"]) if row is not None else 0
        except (TypeError, ValueError) as exc:
            raise JournalRecoveryError(
                "scheduled coordinator queue sequence is invalid"
            ) from exc
        if fence < 0:
            raise JournalRecoveryError(
                "scheduled coordinator queue sequence is invalid"
            )
        return observed, fence

    def tail_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            events = self._read_journal_events(directory)
            private_authorities = self._read_bound_private_session_authorities(
                run_id=run_id,
                events=events,
            )
            self._validate_recovery_completion_event_authority(
                events,
                run_id=run_id,
                private_authorities=private_authorities,
            )
        selected = tuple(
            event for event in events if int(event["sequence"]) > after_sequence
        )[:limit]
        public_events = []
        for event in selected:
            event = _redact_private_session_authority(
                event,
                private_authorities=private_authorities,
            )
            event.pop("projection", None)
            event.pop("projection_sha256", None)
            public_events.append(_sanitize(event))
        return tuple(public_events)

    def events_after(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 200,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Return a bounded monotonic event page for REST/desktop consumers."""
        events = self.tail_events(
            run_id,
            after_sequence=after,
            limit=max(1, min(int(limit), 200)),
            operator_scope=operator_scope,
        )
        return {
            "schema_version": 1,
            "events": events,
            "next_cursor": int(events[-1]["sequence"]) if events else after,
            "cursor_reset": False,
        }

    def latest_events(
        self,
        run_id: str,
        *,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Return the newest bounded event tail in chronological display order."""
        return tuple(
            self.latest_event_page(
                run_id, limit=limit, operator_scope=operator_scope
            )["events"]
        )

    def latest_event_page(
        self,
        run_id: str,
        *,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Return one sanitized newest-event page with explicit truncation."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            all_events = self._read_journal_events(directory)
            selected = all_events[-limit:]
            private_authorities = self._read_bound_private_session_authorities(
                run_id=run_id,
                events=all_events,
            )
            self._validate_recovery_completion_event_authority(
                all_events,
                run_id=run_id,
                private_authorities=private_authorities,
            )
        truncated = (
            len(all_events) > limit
            or projection_was_truncated(selected)
            or any(bool(event.get("payload_truncated")) for event in selected)
        )
        public_events = []
        for event in selected:
            event = _redact_private_session_authority(
                event,
                private_authorities=private_authorities,
            )
            event.pop("projection", None)
            event.pop("projection_sha256", None)
            public_events.append(sanitize_projection(event))
        return {
            "events": tuple(public_events),
            "truncated": truncated,
            "next_cursor": (
                int(public_events[-1]["sequence"]) if public_events else 0
            ),
        }

    def list_runs(
        self,
        *,
        workflow: str | None = None,
        status: str | None = None,
        limit: int = 100,
        operator_scope: str | None = None,
        view: str = "all",
        now: datetime | None = None,
        terminal_board_days: int = 7,
        after: tuple[str, str] | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if view not in {"all", "board", "history", "archive"}:
            raise ValueError("view must be all, board, history, or archive")
        if not 1 <= terminal_board_days <= 3650:
            raise ValueError("terminal_board_days must be between 1 and 3650")
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)
        terminal_cutoff = observed_at - timedelta(days=terminal_board_days)
        if after is not None:
            if (
                not isinstance(after, tuple)
                or len(after) != 2
                or not all(isinstance(value, str) and value for value in after)
            ):
                raise ValueError("after must be an updated_at and run_id tuple")
            try:
                after_updated_at = datetime.fromisoformat(after[0])
            except ValueError as exc:
                raise ValueError("after updated_at must be an ISO timestamp") from exc
            if (
                after_updated_at.tzinfo is None
                or after_updated_at.utcoffset() is None
            ):
                raise ValueError("after updated_at must be timezone-aware")
        storage_degraded = self.storage_health()["status"] != "healthy"
        clauses = ["admission_state='published'"]
        values: list[object] = []
        if workflow:
            clauses.append("workflow_name=?")
            values.append(workflow)
        if status and not storage_degraded:
            clauses.append(
                "(status=? OR EXISTS (SELECT 1 FROM repair_events AS repair "
                "WHERE repair.run_id=runs.run_id "
                "AND repair.reason_code IN "
                f"({_RUN_SCOPED_REPAIR_REASON_SQL}) "
                "AND repair.sequence=(SELECT MAX(latest.sequence) "
                "FROM repair_events AS latest "
                "WHERE latest.run_id=repair.run_id "
                "AND latest.reason_code=repair.reason_code) "
                "AND repair.outcome='repair_required'))"
            )
            values.append(status)
        if operator_scope is not None:
            clauses.append("operator_scope_digest=?")
            values.append(self._scope_digest(operator_scope))
        terminal_statuses = "'succeeded','failed','cancelled','abandoned'"
        cutoff = terminal_cutoff.isoformat()
        if view == "archive":
            clauses.append("archived_at IS NOT NULL")
        elif view == "history":
            clauses.append("archived_at IS NULL")
            clauses.append(f"status IN ({terminal_statuses})")
            clauses.append("(restored_to_history=1 OR updated_at<?)")
            values.append(cutoff)
        elif view == "board":
            clauses.append("archived_at IS NULL")
            clauses.append(
                f"(status NOT IN ({terminal_statuses}) OR "
                "(restored_to_history=0 AND updated_at>=?))"
            )
            values.append(cutoff)
        if after is not None:
            clauses.append("(updated_at<? OR (updated_at=? AND run_id<?))")
            values.extend((after[0], after[0], after[1]))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query_limit = 200 if storage_degraded else limit
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, workflow_name, status, updated_at FROM runs"
                f"{where} ORDER BY updated_at DESC, run_id DESC LIMIT ?",
                (*values, query_limit),
            ).fetchall()
        results = []
        for row in rows:
            run_id = str(row["run_id"])
            try:
                result = self.get_run_status(
                    run_id,
                    operator_scope=operator_scope,
                    now=observed_at,
                    _metadata_only=True,
                )
            except (
                JournalRecoveryError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ):
                self._transition_run_repair(
                    "run_evidence_uncorroborated",
                    run_id=run_id,
                    outcome="repair_required",
                )
                result = {
                    "schema_version": 1,
                    "action": "status",
                    "run_id": run_id,
                    "workflow": row["workflow_name"],
                    "status": row["status"],
                    "status_authoritative": False,
                    "health": "storage_degraded",
                    "updated_at": row["updated_at"],
                    "blocking_reason": "run_evidence_uncorroborated",
                    "next_actions": [],
                    "warnings": ["run_evidence_uncorroborated"],
                }
            if (
                status
                and result["health"] != "storage_degraded"
                and result["status"] != status
            ):
                continue
            if storage_degraded:
                result = {**result, "store_health": "repair_required"}
            results.append(result)
            if len(results) >= limit:
                break
        return tuple(results)

    def attention_candidates(
        self,
        *,
        operator_scope: str | None,
        observed_at: datetime,
        limit: int,
        before: tuple[str, str] | None = None,
        include_unavailable: bool = False,
    ) -> tuple[dict[str, object], ...]:
        """Return a bounded newest-first page of runs that may need attention."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)
        if before is not None and (
            not isinstance(before, tuple)
            or len(before) != 2
            or not all(isinstance(value, str) and value for value in before)
        ):
            raise ValueError("before must be an updated_at and run_id tuple")
        clauses = [
            "runs.admission_state='published'",
            "runs.updated_at<=?",
        ]
        values: list[object] = [observed_at.isoformat()]
        attention_states = [
            "runs.status IN ('failed','paused','recovery_pending')",
            "(runs.status='running' AND ("
            "(runs.execution_mode='foreground' AND "
            "(runs.foreground_lease_expires_at IS NULL OR "
            "runs.foreground_lease_expires_at<=?)) OR "
            "EXISTS (SELECT 1 FROM worker_claims AS claims "
            "WHERE claims.run_id=runs.run_id AND claims.lease_expires_at<=?)"
            "))",
            "EXISTS (SELECT 1 FROM repair_events AS repair "
            "WHERE repair.run_id=runs.run_id "
            "AND repair.reason_code IN "
            f"({_RUN_SCOPED_REPAIR_REASON_SQL}) "
            "AND repair.sequence=(SELECT MAX(latest.sequence) "
            "FROM repair_events AS latest "
            "WHERE latest.run_id=repair.run_id "
            "AND latest.reason_code=repair.reason_code) "
            "AND repair.outcome='repair_required')",
        ]
        values.extend((observed_at.isoformat(), observed_at.isoformat()))
        if include_unavailable:
            attention_states.append(
                "runs.status IN ('queued','running','waiting_retry')"
            )
        clauses.append("(" + " OR ".join(attention_states) + ")")
        if operator_scope is not None:
            clauses.append("runs.operator_scope_digest=?")
            values.append(self._scope_digest(operator_scope))
        if before is not None:
            clauses.append(
                "(runs.updated_at<? OR (runs.updated_at=? AND runs.run_id<?))"
            )
            values.extend((before[0], before[0], before[1]))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT runs.run_id, runs.workflow_name, runs.status, "
                "runs.updated_at FROM runs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY runs.updated_at DESC, runs.run_id DESC LIMIT ?",
                (*values, limit + 1),
            ).fetchall()
        results = []
        for row in rows:
            run_id = str(row["run_id"])
            status_read_succeeded = False
            try:
                result = self.get_run_status(
                    run_id, operator_scope=operator_scope
                )
            except (
                JournalRecoveryError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ):
                self._transition_run_repair(
                    "run_evidence_uncorroborated",
                    run_id=run_id,
                    outcome="repair_required",
                )
                result = {
                    "schema_version": 1,
                    "action": "status",
                    "run_id": run_id,
                    "workflow": row["workflow_name"],
                    "status": row["status"],
                    "status_authoritative": False,
                    "health": "storage_degraded",
                    "updated_at": row["updated_at"],
                    "blocking_reason": "run_evidence_uncorroborated",
                    "next_actions": [],
                    "warnings": ["run_evidence_uncorroborated"],
                }
            else:
                status_read_succeeded = True
            active_reasons = self._active_run_repair_reasons(run_id)
            if status_read_succeeded and active_reasons:
                result = {
                    **result,
                    "status_authoritative": False,
                    "health": "storage_degraded",
                    "blocking_reason": active_reasons[0],
                    "next_actions": [],
                    "warnings": list(active_reasons),
                }
            elif (
                status_read_succeeded
                and not active_reasons
                and result.get("status")
                in {"succeeded", "cancelled", "abandoned"}
            ):
                continue
            results.append(result)
        return tuple(results)

    def archive_run(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Hide a terminal run without changing execution state or evidence."""
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if int(projection["state_version"]) != expected_state_version:
                raise WorkflowConflict("state version changed")
            if projection["status"] not in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                raise ValueError("only terminal runs can be archived")
            if projection.get("archived_at"):
                raise ValueError("run is already archived")
            archived_at = _utc_now()
            projection["archived_at"] = archived_at
            projection["restored_to_history"] = False
            projection["archive_version"] = int(
                projection.get("archive_version") or 0
            ) + 1
            self._append_locked(
                directory,
                projection,
                "run_archived",
                {"archive_version": projection["archive_version"]},
            )
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET updated_at=?, archived_at=?, "
                "restored_to_history=? WHERE run_id=?",
                (
                    projection["updated_at"],
                    projection["archived_at"],
                    int(bool(projection["restored_to_history"])),
                    run_id,
                ),
            )
        return self.get_run_status(run_id, operator_scope=operator_scope)

    def restore_run(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Restore archived evidence to History, never to execution or Board."""
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if int(projection["state_version"]) != expected_state_version:
                raise WorkflowConflict("state version changed")
            if not projection.get("archived_at"):
                raise ValueError("run is not archived")
            projection["archived_at"] = None
            projection["restored_to_history"] = True
            projection["archive_version"] = int(
                projection.get("archive_version") or 0
            ) + 1
            self._append_locked(
                directory,
                projection,
                "run_restored_to_history",
                {"archive_version": projection["archive_version"]},
            )
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET updated_at=?, archived_at=?, "
                "restored_to_history=? WHERE run_id=?",
                (
                    projection["updated_at"],
                    projection["archived_at"],
                    int(bool(projection["restored_to_history"])),
                    run_id,
                ),
            )
        return self.get_run_status(run_id, operator_scope=operator_scope)

    def claim_foreground_execution(
        self,
        run_id: str,
        *,
        owner_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> ForegroundExecutionLease | None:
        """Claim an expired/released safe foreground run under a new epoch."""
        if not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be bounded text")
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int | float)
            or not math.isfinite(float(lease_seconds))
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive and finite")
        instant = now.astimezone(timezone.utc)
        sample = self._foreground_sample(instant)
        expires_at = instant + timedelta(seconds=float(lease_seconds))
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("execution_mode") != "foreground":
                return None
            if projection.get("status") not in _NONTERMINAL:
                return None
            if any(
                isinstance(node, Mapping)
                and (
                    isinstance(node.get("claim"), Mapping)
                    or (
                        isinstance(node.get("recovery"), Mapping)
                        and node["recovery"].get("observation")
                        in {"still_running", "outcome_uncertain"}
                        and not node["recovery"].get("termination_confirmed")
                    )
                )
                for node in projection.get("nodes", {}).values()
            ):
                return None
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                coordinator = self._fresh_coordinator_lease(connection)
                row = connection.execute(
                    "SELECT foreground_owner_id, foreground_lease_expires_at, "
                    "foreground_epoch, foreground_boot_id, "
                    "foreground_heartbeat_monotonic, foreground_lease_seconds "
                    "FROM runs WHERE run_id=? AND execution_mode='foreground' "
                    "AND status IN ('queued','running','waiting_retry','paused','interrupted')",
                    (run_id,),
                ).fetchone()
                current_lease = self._foreground_lease(row) if row is not None else None
                if (
                    coordinator is not None
                    or row is None
                    or (
                        current_lease is not None
                        and lease_is_fresh(current_lease, sample)
                    )
                ):
                    connection.rollback()
                    return None
                epoch = int(row["foreground_epoch"] or 0) + 1
                projection["foreground_owner_id"] = owner_id
                projection["foreground_epoch"] = epoch
                projection["foreground_lease_expires_at"] = expires_at.isoformat()
                projection["foreground_boot_id"] = sample.boot_id
                projection["foreground_heartbeat_monotonic"] = sample.monotonic_now
                projection["foreground_lease_seconds"] = float(lease_seconds)
                self._append_locked(
                    directory,
                    projection,
                    "foreground_execution_claimed",
                    {"owner_id": owner_id, "epoch": epoch},
                )
                connection.execute(
                    "UPDATE runs SET foreground_owner_id=?, foreground_epoch=?, "
                    "foreground_lease_expires_at=?, foreground_boot_id=?, "
                    "foreground_heartbeat_monotonic=?, foreground_lease_seconds=?, "
                    "updated_at=? WHERE run_id=?",
                    (
                        owner_id,
                        epoch,
                        expires_at.isoformat(),
                        sample.boot_id,
                        sample.monotonic_now,
                        float(lease_seconds),
                        projection["updated_at"],
                        run_id,
                    ),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256((directory / "events.jsonl").read_bytes()),
                )
                connection.commit()
                return ForegroundExecutionLease(
                    owner_id,
                    epoch,
                    expires_at,
                    sample.boot_id,
                    sample.monotonic_now,
                    float(lease_seconds),
                )
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def adopt_expired_foreground(
        self,
        run_id: str,
        fence: ExecutionFence,
        now: datetime,
    ) -> dict[str, object]:
        """Convert one expired foreground owner into fenced coordinator ownership."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        sample = self._foreground_sample(instant)
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self.assert_execution_fence(connection, fence)
                row = connection.execute(
                    "SELECT execution_mode, foreground_owner_id, "
                    "foreground_lease_expires_at, foreground_epoch, "
                    "foreground_boot_id, foreground_heartbeat_monotonic, "
                    "foreground_lease_seconds, status, "
                    "projection_state_version "
                    "FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None or row["execution_mode"] != "foreground":
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: run is not foreground-owned"
                    )
                if row["status"] not in {"queued", "running", "waiting_retry"}:
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: run is not coordinator-adoptable"
                    )
                expires_text = row["foreground_lease_expires_at"]
                foreground_lease = self._foreground_lease(row)
                if foreground_lease is None or lease_is_fresh(foreground_lease, sample):
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: owner lease is still active"
                    )
                expected = (
                    row["execution_mode"],
                    row["foreground_owner_id"],
                    expires_text,
                    row["foreground_epoch"],
                    row["foreground_boot_id"],
                    row["foreground_heartbeat_monotonic"],
                    row["foreground_lease_seconds"],
                    row["status"],
                    row["projection_state_version"],
                )
                observed = (
                    projection.get("execution_mode"),
                    projection.get("foreground_owner_id"),
                    projection.get("foreground_lease_expires_at"),
                    projection.get("foreground_epoch"),
                    projection.get("foreground_boot_id"),
                    projection.get("foreground_heartbeat_monotonic"),
                    projection.get("foreground_lease_seconds"),
                    projection.get("status"),
                    projection.get("state_version"),
                )
                if observed != expected:
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: journal and index owner differ"
                    )

                claimed_attempts: list[
                    tuple[str, dict[str, object], dict[str, object], str]
                ] = []
                for node_id, node in projection.get("nodes", {}).items():
                    claim = node.get("claim") if isinstance(node, dict) else None
                    if not isinstance(claim, dict):
                        continue
                    attempt_id = str(claim.get("attempt_id") or "")
                    attempt = next(
                        (
                            candidate
                            for candidate in reversed(node.get("attempts", []))
                            if candidate.get("attempt_id") == attempt_id
                        ),
                        None,
                    )
                    if not isinstance(attempt, dict) or not attempt_id:
                        raise ForegroundExecutionConflict(
                            "foreground owner conflict: active claim lacks attempt evidence"
                        )
                    claimed_attempts.append(
                        (str(node_id), node, attempt, attempt_id)
                    )

                reconciliation_required = False
                releasable_attempts: list[str] = []
                for node_id, node, attempt, attempt_id in claimed_attempts:
                    claim = node["claim"]
                    observation = self._observe_attempt(attempt)
                    effect_classification = str(
                        attempt.get(
                            "effect_classification",
                            claim.get("effect_classification", "replay_safe"),
                        )
                    )
                    recovery = {
                        "attempt_id": attempt_id,
                        "owner_id": attempt.get("owner_id", claim.get("owner_id")),
                        "owner_epoch": attempt.get(
                            "owner_epoch", claim.get("owner_epoch")
                        ),
                        "executor_id": attempt.get(
                            "executor_id", claim.get("executor_id", "unknown")
                        ),
                        "effect_classification": effect_classification,
                        "process_identity": attempt.get("process_identity"),
                        "observation": observation,
                        "foreground_owner_expired_at": instant.isoformat(),
                        "termination_confirmed": observation
                        in {"known_stopped", "not_started"},
                    }
                    node.pop("claim", None)
                    node["recovery"] = recovery
                    requires_reconcile = (
                        effect_classification == "outward"
                        or observation in {"still_running", "outcome_uncertain"}
                    )
                    if requires_reconcile:
                        node["state"] = "paused"
                        node["pending_interaction"] = {
                            "type": "reconcile",
                            "interaction_id": f"reconcile-{attempt_id}",
                            "attempt_id": attempt_id,
                            "reason_code": "foreground_owner_expired_outcome_uncertain",
                        }
                        attempt.update({
                            "state": "paused",
                            "error_code": "reconciliation_required",
                        })
                        reconciliation_required = True
                        event_type = "node_reconciliation_required"
                    else:
                        node["state"] = (
                            "ready"
                            if all(
                                projection["nodes"][dependency]["state"]
                                == "succeeded"
                                for dependency in node["depends_on"]
                            )
                            else "pending"
                        )
                        attempt.update({
                            "state": "interrupted",
                            "error_code": "foreground_owner_expired",
                        })
                        event_type = "node_foreground_owner_expired"
                    self._append_locked(
                        directory,
                        projection,
                        event_type,
                        {
                            "observation": observation,
                            "effect_classification": effect_classification,
                        },
                        node_id=node_id,
                        attempt_id=attempt_id,
                        defer_notification=True,
                        terminal_reserve_attempt_id=attempt_id,
                        reserve_connection=connection,
                    )
                    if not requires_reconcile:
                        releasable_attempts.append(attempt_id)

                projection["execution_mode"] = "background"
                projection["foreground_owner_id"] = None
                projection["foreground_epoch"] = None
                projection["foreground_lease_expires_at"] = None
                projection["foreground_boot_id"] = None
                projection["foreground_heartbeat_monotonic"] = None
                projection["foreground_lease_seconds"] = None
                if reconciliation_required:
                    projection["status"] = "paused"
                    transition = "foreground_execution_reconciliation_required"
                else:
                    transition = "foreground_execution_adopted"
                    projection["execution_handoff"] = {
                        "transition": transition,
                        "execution_mode": "background",
                        "coordinator_epoch": fence.owner_epoch,
                        "occurred_at": instant.isoformat(),
                    }
                self._append_locked(
                    directory,
                    projection,
                    transition,
                    {
                        "coordinator_owner_id": fence.owner_id,
                        "coordinator_epoch": fence.owner_epoch,
                        "previous_foreground_owner_id": expected[1],
                        "previous_foreground_epoch": expected[3],
                    },
                    defer_notification=True,
                    terminal_reserve_attempt_id=(
                        claimed_attempts[0][3] if claimed_attempts else None
                    ),
                    reserve_connection=connection,
                )
                updated = connection.execute(
                    "UPDATE runs SET execution_mode='background', "
                    "foreground_owner_id=NULL, foreground_lease_expires_at=NULL, "
                    "foreground_epoch=NULL, foreground_boot_id=NULL, "
                    "foreground_heartbeat_monotonic=NULL, "
                    "foreground_lease_seconds=NULL, status=?, updated_at=? "
                    "WHERE run_id=? AND execution_mode='foreground' "
                    "AND foreground_owner_id IS ? "
                    "AND foreground_lease_expires_at=? AND foreground_epoch IS ? "
                    "AND foreground_boot_id IS ? "
                    "AND foreground_heartbeat_monotonic IS ? "
                    "AND foreground_lease_seconds IS ? "
                    "AND projection_state_version IS ?",
                    (
                        projection["status"],
                        projection["updated_at"],
                        run_id,
                        expected[1],
                        expected[2],
                        expected[3],
                        expected[4],
                        expected[5],
                        expected[6],
                        expected[8],
                    ),
                ).rowcount
                if updated != 1:
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: adoption comparison failed"
                    )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                for attempt_id in releasable_attempts:
                    self._release_worker_claim(
                        attempt_id, connection=connection
                    )
                self._record_coordinator_wake(
                    connection,
                    run_id=run_id,
                    reason_code=(
                        "foreground_reconciliation_required"
                        if reconciliation_required
                        else "foreground_adopted"
                    ),
                )
                connection.commit()
                return projection
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def release_foreground_execution(
        self,
        run_id: str,
        *,
        owner_id: str,
        epoch: int,
        now: datetime,
    ) -> bool:
        """Release an exact foreground fencing token after local quiescence."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id=? "
                    "AND execution_mode='foreground' AND foreground_owner_id=? "
                    "AND foreground_epoch=?",
                    (run_id, owner_id, epoch),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                projection["foreground_lease_expires_at"] = instant.isoformat()
                projection["foreground_boot_id"] = None
                projection["foreground_heartbeat_monotonic"] = None
                projection["foreground_lease_seconds"] = None
                self._append_locked(
                    directory,
                    projection,
                    "foreground_execution_released",
                    {"owner_id": owner_id, "epoch": epoch},
                )
                connection.execute(
                    "UPDATE runs SET foreground_lease_expires_at=?, "
                    "foreground_boot_id=NULL, "
                    "foreground_heartbeat_monotonic=NULL, "
                    "foreground_lease_seconds=NULL, updated_at=? "
                    "WHERE run_id=? AND foreground_owner_id=? AND foreground_epoch=?",
                    (
                        instant.isoformat(),
                        projection["updated_at"],
                        run_id,
                        owner_id,
                        epoch,
                    ),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256((directory / "events.jsonl").read_bytes()),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def renew_foreground_execution(
        self,
        run_id: str,
        *,
        owner_id: str,
        epoch: int,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        """Renew an unexpired foreground execution lease using its fencing token."""
        if not owner_id or len(owner_id) > 256:
            raise ValueError("owner_id must be bounded text")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("epoch must be a positive integer")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int | float)
            or not math.isfinite(float(lease_seconds))
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive and finite")
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        sample = self._foreground_sample(instant)
        expires_at = (instant + timedelta(seconds=float(lease_seconds))).isoformat()
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT foreground_owner_id, foreground_lease_expires_at, "
                    "foreground_epoch, foreground_boot_id, "
                    "foreground_heartbeat_monotonic, foreground_lease_seconds "
                    "FROM runs WHERE run_id=? AND execution_mode='foreground' "
                    "AND status IN ('queued','running','waiting_retry','paused','interrupted')",
                    (run_id,),
                ).fetchone()
                current_lease = self._foreground_lease(row) if row is not None else None
                if (
                    current_lease is None
                    or current_lease.owner_id != owner_id
                    or current_lease.epoch != epoch
                    or not lease_is_fresh(current_lease, sample)
                ):
                    connection.rollback()
                    return False
                projection["foreground_lease_expires_at"] = expires_at
                projection["foreground_boot_id"] = sample.boot_id
                projection["foreground_heartbeat_monotonic"] = sample.monotonic_now
                projection["foreground_lease_seconds"] = float(lease_seconds)
                self._append_locked(
                    directory,
                    projection,
                    "foreground_execution_renewed",
                    {"owner_id": owner_id, "epoch": epoch},
                )
                updated = connection.execute(
                    "UPDATE runs SET foreground_lease_expires_at=?, "
                    "foreground_boot_id=?, foreground_heartbeat_monotonic=?, "
                    "foreground_lease_seconds=?, updated_at=? "
                    "WHERE run_id=? AND foreground_owner_id=? "
                    "AND foreground_epoch=? AND foreground_lease_expires_at=? "
                    "AND foreground_boot_id IS ? "
                    "AND foreground_heartbeat_monotonic IS ? "
                    "AND foreground_lease_seconds IS ?",
                    (
                        expires_at,
                        sample.boot_id,
                        sample.monotonic_now,
                        float(lease_seconds),
                        projection["updated_at"],
                        run_id,
                        owner_id,
                        epoch,
                        current_lease.lease_expires_at.isoformat(),
                        current_lease.boot_id,
                        current_lease.heartbeat_monotonic,
                        current_lease.lease_seconds,
                    ),
                ).rowcount
                if updated != 1:
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: renewal comparison failed"
                    )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256((directory / "events.jsonl").read_bytes()),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def get_run_status(
        self,
        run_id: str,
        *,
        operator_scope: str | None = None,
        now: datetime | None = None,
        _metadata_only: bool = False,
    ) -> dict[str, object]:
        run = (
            self._load_run_metadata(run_id, operator_scope=operator_scope)
            if _metadata_only
            else self.load_run(run_id, operator_scope=operator_scope)
        )
        run = _redact_private_session_authority(
            run,
            private_authorities=self._read_private_session_authorities(run_id=run_id),
        )
        if not isinstance(run.get("provenance"), Mapping):
            run["provenance"] = legacy_projection_provenance(run)
        observed_sample = self._lease_clock()
        observed_at = now or observed_sample.utc_now
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)
        if now is not None:
            observed_sample = LeaseClockSample(
                observed_at,
                observed_sample.monotonic_now,
                observed_sample.boot_id,
            )
        nodes = run.get("nodes", {})
        node_values = list(nodes.values()) if isinstance(nodes, dict) else []
        completed = sum(
            node.get("state") in {"succeeded", "skipped"}
            for node in node_values
            if isinstance(node, dict)
        )
        status = str(run["status"])
        retry_times = [
            node["next_attempt_at"]
            for node in node_values
            if isinstance(node, dict)
            and node.get("state") == "waiting_retry"
            and isinstance(node.get("next_attempt_at"), str)
        ]
        pending_interaction = next(
            (
                {**pending, "node_id": node.get("id")}
                if isinstance(pending, dict)
                else {"type": pending, "node_id": node.get("id")}
                for node in node_values
                if isinstance(node, dict)
                and (pending := node.get("pending_interaction")) is not None
            ),
            None,
        )
        current_nodes = [
            node["id"]
            for node in node_values
            if isinstance(node, dict)
            and node.get("state") in {"ready", "claimed", "running"}
        ]
        completed_attempts = [
            (str(attempt["completed_at"]), str(node["id"]))
            for node in node_values
            if isinstance(node, dict)
            for attempt in node.get("attempts", [])
            if isinstance(attempt, dict)
            and isinstance(attempt.get("completed_at"), str)
        ]
        execution_mode = str(run.get("execution_mode", "foreground"))
        coordinator_facts: dict[str, object]
        if execution_mode == "background":
            from plugins.workflow.coordinator_store import CoordinatorStore

            observed = CoordinatorStore(self.database).health(now=observed_at)
            lease = observed.lease
            coordinator_facts = {
                "status": observed.status,
                "reason_code": observed.reason_code,
                "owner_id": lease.owner_id if lease else None,
                "host_kind": lease.host_kind if lease else None,
                "epoch": lease.epoch if lease else None,
                "heartbeat_at": (
                    lease.heartbeat_at.isoformat() if lease is not None else None
                ),
                "lease_expires_at": (
                    lease.lease_expires_at.isoformat() if lease is not None else None
                ),
            }
        else:
            observed = None
            coordinator_facts = {
                "status": "not_required",
                "reason_code": "foreground_execution",
                "owner_id": run.get("foreground_owner_id"),
                "epoch": run.get("foreground_epoch"),
                "lease_expires_at": run.get("foreground_lease_expires_at"),
            }
        foreground_lease = self._foreground_lease(run)
        foreground_owner_missing = (
            execution_mode == "foreground"
            and status == "running"
            and (
                foreground_lease is None
                or not lease_is_fresh(foreground_lease, observed_sample)
            )
        )
        stale_claim = any(
            isinstance(node, dict)
            and node.get("state") in {"claimed", "running"}
            and (
                not isinstance(node.get("claim"), Mapping)
                or not isinstance(node["claim"].get("lease_expires_at"), str)
                or datetime.fromisoformat(node["claim"]["lease_expires_at"])
                <= observed_at
            )
            for node in node_values
        )
        blocking_reason = None
        if status in {"succeeded", "failed", "cancelled", "abandoned"}:
            health = "terminal"
        elif status in {"paused", "recovery_pending"}:
            health = "user_wait"
            if status == "recovery_pending":
                blocking_reason = "persistent_session_registry_update_pending"
        elif status == "interrupted":
            health = "interrupted"
        elif status == "queued":
            health = "waiting"
            if run_is_scheduled_wait(run, observed=observed_at):
                blocking_reason = "scheduled_wait"
            else:
                blocking_reason = (
                    "concurrency_lane_busy"
                    if run.get("blocked_by_run_id")
                    else "execution_capacity"
                )
            if (
                blocking_reason != "scheduled_wait"
                and observed is not None
                and observed.status != "healthy"
                and not run.get("blocked_by_run_id")
            ):
                health = "coordinator_unavailable"
                blocking_reason = observed.reason_code
        elif status == "waiting_retry":
            health = "retry_wait"
            blocking_reason = "retry_backoff"
            if (
                observed is not None
                and observed.status != "healthy"
                and retry_times
                and datetime.fromisoformat(min(retry_times)) <= observed_at
            ):
                health = "coordinator_unavailable"
                blocking_reason = observed.reason_code
        elif foreground_owner_missing:
            health = "stalled"
            blocking_reason = "foreground_owner_unavailable"
        elif stale_claim:
            health = "stalled"
            blocking_reason = "node_lease_expired"
        elif isinstance(run.get("stall"), Mapping):
            health = "stalled"
            blocking_reason = str(run["stall"].get("reason_code") or "stalled")
        elif observed is not None and observed.status != "healthy":
            health = "coordinator_unavailable"
            blocking_reason = observed.reason_code
        else:
            health = "healthy"
        return {
            **run,
            "action": "status",
            "health": health,
            "blocking_reason": blocking_reason,
            "coordinator": coordinator_facts,
            "elapsed_ms": None,
            "current_nodes": current_nodes,
            "previous_node": max(completed_attempts)[1] if completed_attempts else None,
            "progress": {
                "kind": "graph",
                "completed_nodes": completed,
                "total_nodes": len(node_values),
            },
            "attempts": sum(
                len(node.get("attempts", []))
                for node in node_values
                if isinstance(node, dict)
            ),
            "next_retry_at": min(retry_times) if retry_times else None,
            "pending_interaction": pending_interaction,
            "next_actions": available_actions(
                status,
                pending_interaction,
                health=health,
                archived=bool(run.get("archived_at")),
                can_resume=blocking_reason != "foreground_owner_unavailable",
            ),
        }

    def record_stall_if_due(
        self,
        run_id: str,
        *,
        fence: ExecutionFence,
        now: LeaseClockSample,
        runnable_stall_seconds: float,
        semantic_stall_seconds: float,
    ) -> bool:
        """Persist one deduplicated threshold-backed stalled transition."""
        for name, value in (
            ("runnable_stall_seconds", runnable_stall_seconds),
            ("semantic_stall_seconds", semantic_stall_seconds),
        ):
            if not isinstance(value, int | float) or value <= 0:
                raise ValueError(f"{name} must be positive")
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("status") != "running" or isinstance(
                projection.get("stall"), Mapping
            ):
                return False
            node_values = list(projection.get("nodes", {}).values())
            active = any(
                isinstance(node, Mapping)
                and node.get("state") in {"claimed", "running"}
                for node in node_values
            )
            runnable = any(
                isinstance(node, Mapping)
                and node.get("state") in {"ready", "claimed", "running"}
                for node in node_values
            )
            reason_code = None
            threshold = 0.0
            prefix = "last_runnable_progress"
            if not runnable:
                reason_code = "runnable_progress_stalled"
                threshold = float(runnable_stall_seconds)
            elif active:
                reason_code = "semantic_progress_stalled"
                threshold = float(semantic_stall_seconds)
                prefix = "last_semantic_progress"
            if reason_code is None:
                return False
            observed_monotonic = projection.get(f"{prefix}_monotonic")
            observed_boot_id = projection.get(f"{prefix}_boot_id")
            if observed_boot_id is None:
                observed_boot_id = projection.get("progress_boot_id")
            observed_utc = projection.get(f"{prefix}_at") or projection.get(
                "last_runnable_progress_at"
            )
            if (
                observed_boot_id == now.boot_id
                and isinstance(observed_monotonic, int | float)
            ):
                elapsed = now.monotonic_now - float(observed_monotonic)
            elif isinstance(observed_utc, str):
                elapsed = (
                    now.utc_now - datetime.fromisoformat(observed_utc)
                ).total_seconds()
            else:
                return False
            if elapsed < threshold:
                return False
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self.assert_execution_fence(connection, fence, now)
                projection["stall"] = {
                    "reason_code": reason_code,
                    "detected_at": now.utc_now.isoformat(),
                    "elapsed_seconds": elapsed,
                }
                self._append_locked(
                    directory,
                    projection,
                    "run_stalled",
                    {"reason_code": reason_code, "elapsed_seconds": elapsed},
                    defer_notification=True,
                    reserve_connection=connection,
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def node_effect_classification(
        self,
        run_id: str,
        node_id: str,
        *,
        projection: Mapping[str, object] | None = None,
    ) -> str:
        """Return digest-corroborated effect policy for new and legacy runs."""
        run = projection or self.load_run(run_id)
        persisted = run.get("outward_action_nodes")
        if isinstance(persisted, list):
            return "outward" if node_id in persisted else "replay_safe"
        directory = self.run_directory(run_id)
        try:
            outward = self._legacy_effect_policy_nodes(directory, run)
        except (OSError, KeyError, yaml.YAMLError, JournalRecoveryError):
            self._transition_run_repair(
                "legacy_effect_policy_uncorroborated",
                run_id=run_id,
                outcome="repair_required",
            )
            raise
        self._transition_run_repair(
            "legacy_effect_policy_uncorroborated",
            run_id=run_id,
            outcome="repair_verified",
        )
        return "outward" if node_id in outward else "replay_safe"

    def claim_node(
        self,
        run_id: str,
        node_id: str,
        owner_id: str,
        *,
        lease_seconds: float = 30.0,
        now: datetime | None = None,
        monotonic_now: float | None = None,
        journal_reserve_bytes: int = 0,
        terminal_journal_reserve_bytes: int = 0,
        executor_id: str = "unknown",
        owner_epoch: str | None = None,
        effect_classification: str = "replay_safe",
        evidence_paths: Iterable[str] | None = None,
        execution_fence: ExecutionFence | None = None,
        foreground_owner_id: str | None = None,
        foreground_owner_epoch: int | None = None,
        require_execution_authority: bool = False,
        max_run_workers: int | None = None,
    ) -> NodeClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if effect_classification not in {"replay_safe", "outward"}:
            raise ValueError("effect_classification must be replay_safe or outward")
        if not executor_id:
            raise ValueError("executor_id must not be empty")
        if (foreground_owner_id is None) != (foreground_owner_epoch is None):
            raise ValueError(
                "foreground owner ID and epoch must be provided together"
            )
        if foreground_owner_epoch is not None and (
            isinstance(foreground_owner_epoch, bool)
            or not isinstance(foreground_owner_epoch, int)
            or foreground_owner_epoch <= 0
        ):
            raise ValueError("foreground owner epoch must be a positive integer")
        if max_run_workers is not None and (
            isinstance(max_run_workers, bool)
            or not isinstance(max_run_workers, int)
            or max_run_workers <= 0
        ):
            raise ValueError("max_run_workers must be a positive integer")
        if (
            isinstance(terminal_journal_reserve_bytes, bool)
            or not isinstance(terminal_journal_reserve_bytes, int)
            or terminal_journal_reserve_bytes < 0
        ):
            raise ValueError(
                "terminal_journal_reserve_bytes must be a non-negative integer"
            )
        self._ensure_free_disk()
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock):
            with workflow_lock(self._run_lock_path(run_id)):
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"].get(node_id)
                if (
                    projection.get("desired_status") is not None
                    or not node
                    or node["state"] != "ready"
                    or (
                        isinstance(node.get("recovery"), Mapping)
                        and node["recovery"].get("observation")
                        in {"still_running", "outcome_uncertain"}
                        and not node["recovery"].get("termination_confirmed")
                    )
                ):
                    return None
                preliminary_reserve = TerminalJournalReserve.for_projection(
                    len(
                        json.dumps(
                            projection, sort_keys=True, ensure_ascii=False
                        ).encode("utf-8")
                    )
                )
                self._ensure_run_capacity(
                    directory,
                    projection,
                    journal_reserve_bytes=(
                        journal_reserve_bytes
                        + preliminary_reserve.terminal_reserve_bytes
                    ),
                )
                connection = self._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    instant = now or datetime.now(timezone.utc)
                    if execution_fence is not None:
                        self.assert_execution_fence(connection, execution_fence)
                    elif require_execution_authority or foreground_owner_id is not None:
                        execution = connection.execute(
                            "SELECT execution_mode, foreground_owner_id, "
                            "foreground_epoch, foreground_lease_expires_at, "
                            "foreground_boot_id, foreground_heartbeat_monotonic, "
                            "foreground_lease_seconds "
                            "FROM runs WHERE run_id=?",
                            (run_id,),
                        ).fetchone()
                        if execution is None:
                            connection.rollback()
                            return None
                        if execution["execution_mode"] == "foreground":
                            foreground_sample = self._foreground_sample(instant)
                            if monotonic_now is not None:
                                foreground_sample = LeaseClockSample(
                                    foreground_sample.utc_now,
                                    float(monotonic_now),
                                    foreground_sample.boot_id,
                                )
                            foreground_lease = self._foreground_lease(execution)
                            if (
                                foreground_owner_id is None
                                or execution["foreground_owner_id"]
                                != foreground_owner_id
                                or execution["foreground_epoch"]
                                != foreground_owner_epoch
                                or foreground_lease is None
                                or not lease_is_fresh(
                                    foreground_lease, foreground_sample
                                )
                            ):
                                connection.rollback()
                                return None
                        else:
                            connection.rollback()
                            return None
                    active_workers = connection.execute(
                        "SELECT COUNT(*) FROM worker_claims"
                    ).fetchone()[0]
                    if active_workers >= self.limits["workers"]:
                        connection.rollback()
                        return None
                    if max_run_workers is not None:
                        active_run_workers = connection.execute(
                            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                        if active_run_workers >= max_run_workers:
                            connection.rollback()
                            return None
                    attempt_id = uuid.uuid4().hex
                    monotonic_instant = (
                        float(monotonic_now)
                        if monotonic_now is not None
                        else time.monotonic()
                    )
                    expires = instant + timedelta(seconds=lease_seconds)
                    resolved_evidence_paths = list(
                        evidence_paths
                        or (
                            f"nodes/{node_id}/{attempt_id}/stdout.txt",
                            f"nodes/{node_id}/{attempt_id}/stderr.txt",
                        )
                    )
                    connection.execute(
                        "INSERT INTO worker_claims "
                        "(attempt_id, run_id, node_id, owner_id, lease_expires_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (attempt_id, run_id, node_id, owner_id, expires.isoformat()),
                    )
                    node["state"] = "claimed"
                    node["claim"] = {
                        "owner_id": owner_id,
                        "attempt_id": attempt_id,
                        "lease_expires_at": expires.isoformat(),
                        "heartbeat_at": instant.isoformat(),
                        "heartbeat_monotonic": monotonic_instant,
                        "lease_seconds": float(lease_seconds),
                        "owner_epoch": owner_epoch or owner_id,
                        "executor_id": executor_id,
                        "effect_classification": effect_classification,
                        "execution_fence": (
                            {
                                "owner_id": execution_fence.owner_id,
                                "owner_epoch": execution_fence.owner_epoch,
                            }
                            if execution_fence is not None
                            else None
                        ),
                    }
                    node["attempts"].append({
                        "attempt_id": attempt_id,
                        "state": "claimed",
                        "claimed_at": instant.isoformat(),
                        "owner_id": owner_id,
                        "owner_epoch": owner_epoch or owner_id,
                        "executor_id": executor_id,
                        "effect_classification": effect_classification,
                        "execution_fence": (
                            {
                                "owner_id": execution_fence.owner_id,
                                "owner_epoch": execution_fence.owner_epoch,
                            }
                            if execution_fence is not None
                            else None
                        ),
                        "evidence_paths": resolved_evidence_paths,
                    })
                    reserve = TerminalJournalReserve.for_projection(
                        len(
                            json.dumps(
                                projection, sort_keys=True, ensure_ascii=False
                            ).encode("utf-8")
                        )
                    )
                    self._ensure_run_capacity(
                        directory,
                        projection,
                        journal_reserve_bytes=(
                            journal_reserve_bytes + reserve.terminal_reserve_bytes
                        ),
                    )
                    connection.execute(
                        "INSERT INTO attempt_journal_reserves ("
                        "attempt_id, run_id, terminal_reserve_bytes, "
                        "projection_limit_bytes, consumed_bytes, created_at) "
                        "VALUES (?, ?, ?, ?, 0, ?)",
                        (
                            attempt_id,
                            run_id,
                            reserve.terminal_reserve_bytes
                            + terminal_journal_reserve_bytes,
                            reserve.projection_limit_bytes,
                            _utc_now(),
                        ),
                    )
                    self._append_locked(
                        directory,
                        projection,
                        "node_claimed",
                        {"owner_id": owner_id},
                        node_id=node_id,
                        attempt_id=attempt_id,
                        reserve_connection=connection,
                    )
                    connection.commit()
                    return NodeClaim(
                        run_id,
                        node_id,
                        attempt_id,
                        owner_id,
                        expires,
                        execution_fence,
                    )
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()

    def _release_worker_claim(
        self,
        attempt_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with (
            nullcontext(connection)
            if connection is not None
            else self._connect()
        ) as connection:
            connection.execute(
                "DELETE FROM worker_claims WHERE attempt_id=?", (attempt_id,)
            )

    def _transfer_obligation_journal_reserve(
        self,
        attempt_id: str,
        run_id: str,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        """Retain pre-provider capacity after the worker claim is released."""
        row = connection.execute(
            "SELECT terminal_reserve_bytes, projection_limit_bytes, "
            "consumed_bytes FROM attempt_journal_reserves WHERE attempt_id=? "
            "AND run_id=?",
            (attempt_id, run_id),
        ).fetchone()
        if row is None:
            raise StorageQuotaError(
                "persistent session obligation journal reserve is missing"
            )
        remaining = int(row["terminal_reserve_bytes"]) - int(
            row["consumed_bytes"]
        )
        if remaining <= 0:
            raise StorageQuotaError(
                "persistent session obligation journal reserve is exhausted"
            )
        connection.execute(
            "INSERT INTO obligation_journal_reserves ("
            "attempt_id, run_id, terminal_reserve_bytes, "
            "projection_limit_bytes, consumed_bytes, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(attempt_id) DO UPDATE SET "
            "run_id=excluded.run_id, "
            "terminal_reserve_bytes=excluded.terminal_reserve_bytes, "
            "projection_limit_bytes=excluded.projection_limit_bytes, "
            "consumed_bytes=0",
            (
                attempt_id,
                run_id,
                remaining,
                int(row["projection_limit_bytes"]),
                _utc_now(),
            ),
        )

    def release_claim_before_execution(self, claim: NodeClaim) -> bool:
        """Durably make a fenced claim retryable only when no executor ran."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                return False
            attempt = node.get("attempts", [])[-1]
            if (
                attempt.get("attempt_id") != claim.attempt_id
                or attempt.get("process_identity") is not None
                or active.get("process_identity") is not None
            ):
                return False
            node.pop("claim", None)
            node["state"] = "ready"
            attempt.update({
                "state": "interrupted",
                "error_code": "coordinator_fence_lost_before_execution",
                "completed_at": _utc_now(),
            })
            self._append_locked(
                directory,
                projection,
                "node_fenced_before_execution",
                {"retry_safe": True},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                terminal_reserve_attempt_id=claim.attempt_id,
            )
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._release_worker_claim(
                    claim.attempt_id, connection=connection
                )
            return True

    def mark_node_started(
        self, claim: NodeClaim, *, now: LeaseClockSample | None = None
    ) -> None:
        directory = self.run_directory(claim.run_id)
        with workflow_lock(
            self._run_lock_path(claim.run_id)
        ), self._execution_fence_transaction(
            claim.execution_fence, now
        ) as fence_connection:
            projection = json.loads((directory / "run.json").read_text())
            if (
                projection["status"] != "running"
                or projection.get("desired_status") is not None
            ):
                raise RuntimeError("stale node start for terminal run")
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale node claim")
            node["state"] = "running"
            node["attempts"][-1]["state"] = "running"
            node["attempts"][-1]["started_at"] = _utc_now()
            self._append_locked(
                directory,
                projection,
                "node_started",
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
            )

    def record_persistent_session_recovery_selection(
        self,
        claim: NodeClaim,
        selection: PersistentSessionRecoverySelection,
        *,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Journal a bounded active-claim recovery choice before provider use."""
        if (
            selection.run_id != claim.run_id
            or selection.attempt_id != claim.attempt_id
            or selection.key.node_id != claim.node_id
        ):
            raise ValueError("persistent session recovery selection is misbound")
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if (
                    projection.get("status") != "running"
                    or active.get("attempt_id") != claim.attempt_id
                    or selection.key.workflow != projection.get("workflow")
                    or selection.key.scope
                    != str(projection.get("operator_scope_digest") or "local")
                ):
                    return False
                recoveries = node.setdefault("session_recoveries", [])
                if not isinstance(recoveries, list) or len(recoveries) > 6:
                    raise JournalRecoveryError(
                        "persistent session recovery projection is malformed"
                    )
                existing = next(
                    (
                        item
                        for item in recoveries
                        if isinstance(item, Mapping)
                        and item.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if existing is None and len(recoveries) == 6:
                    raise StorageQuotaError(
                        "persistent session recovery evidence is full"
                    )
                selection_authority = _session_recovery_selection_authority(
                    selection,
                    activation_event_sequence=int(projection["event_sequence"]) + 1,
                    activation_event_type=(
                        "persistent_session_missing_fresh_start"
                    ),
                )
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as authority_connection:
                    if fence_connection is None:
                        authority_connection.execute("BEGIN IMMEDIATE")
                    self._write_private_authority(
                        authority_connection,
                        table="session_recovery_selection_authority",
                        run_id=claim.run_id,
                        attempt_id=claim.attempt_id,
                        authority=selection_authority,
                    )
                    if fence_connection is None:
                        authority_connection.commit()
                if existing is not None:
                    return existing.get("outcome") == "fresh_start_selected"
                public_selection = {
                    "attempt_id": claim.attempt_id,
                    "registry_generation": selection.expected_generation,
                    "missing_session_sha256": _sha256(
                        selection.missing_session_id.encode("utf-8")
                    ),
                    "cache_fingerprint_sha256": _sha256(
                        selection.cache_fingerprint.encode("utf-8")
                    ),
                    "source": selection.source,
                    "provider": selection.key.provider,
                    "runtime_profile": selection.key.profile,
                    "provider_attempts_before_recovery": 0,
                    "outcome": "fresh_start_selected",
                }
                recoveries.append(public_selection)
                self._append_locked(
                    directory,
                    projection,
                    "persistent_session_missing_fresh_start",
                    public_selection,
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=claim.attempt_id,
                    reserve_connection=fence_connection,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_persistent_session_recovery_outcome(
        self,
        claim: NodeClaim,
        *,
        outcome: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Journal a bounded recovery result while the winning claim is active."""
        if outcome != "fresh_execution_failed":
            raise ValueError("invalid active persistent session recovery outcome")
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if (
                    projection.get("status") != "running"
                    or active.get("attempt_id") != claim.attempt_id
                ):
                    return False
                recoveries = node.get("session_recoveries")
                if not isinstance(recoveries, list):
                    raise JournalRecoveryError(
                        "persistent session recovery evidence is missing"
                    )
                matches = [
                    item
                    for item in recoveries
                    if isinstance(item, dict)
                    and item.get("attempt_id") == claim.attempt_id
                ]
                if len(matches) != 1:
                    raise JournalRecoveryError(
                        "persistent session recovery evidence is uncorroborated"
                    )
                if matches[0].get("outcome") == outcome:
                    return True
                if matches[0].get("outcome") != "fresh_start_selected":
                    raise JournalRecoveryError(
                        "persistent session recovery outcome conflicts"
                    )
                matches[0]["outcome"] = outcome
                self._append_locked(
                    directory,
                    projection,
                    "persistent_session_recovery_outcome",
                    {"outcome": outcome},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=claim.attempt_id,
                    reserve_connection=fence_connection,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    @staticmethod
    def _executor_nonce(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("executor_nonce must be bounded non-empty text")
        return value

    def record_spawn_intent(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Persist an attempt-bound intent before any process may be created."""
        nonce = self._executor_nonce(executor_nonce)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                existing = attempt.get("spawn")
                if isinstance(existing, Mapping):
                    return (
                        existing.get("executor_nonce") == nonce
                        and existing.get("state") == "intent"
                    )
                effect_classification = str(
                    attempt.get(
                        "effect_classification",
                        active.get("effect_classification", "replay_safe"),
                    )
                )
                spawn = {
                    "state": "intent",
                    "executor_nonce": nonce,
                    "effect_classification": effect_classification,
                    "recorded_at": _utc_now(),
                }
                attempt["spawn"] = spawn
                active["spawn"] = dict(spawn)
                self._append_locked(
                    directory,
                    projection,
                    "spawn_intent",
                    {
                        "executor_nonce": nonce,
                        "effect_classification": effect_classification,
                    },
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_spawn_failed(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        error_code: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Prove that one persisted spawn intent did not create a process."""
        nonce = self._executor_nonce(executor_nonce)
        safe_error = _sanitize_diagnostic(error_code) or "spawn_failed"
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                spawn = attempt.get("spawn") if isinstance(attempt, dict) else None
                if (
                    not isinstance(spawn, dict)
                    or spawn.get("executor_nonce") != nonce
                    or spawn.get("state") != "intent"
                    or attempt.get("process_identity") is not None
                ):
                    return False
                spawn.update({
                    "state": "failed",
                    "failed_at": _utc_now(),
                    "error_code": safe_error,
                })
                active["spawn"] = dict(spawn)
                self._append_locked(
                    directory,
                    projection,
                    "spawn_failed",
                    {"executor_nonce": nonce, "error_code": safe_error},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_process_started(
        self,
        claim: NodeClaim,
        identity: ProcessIdentity,
        *,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Durably bind an owned process identity to its active node claim."""
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if (
                    projection["status"] != "running"
                    or projection.get("desired_status") is not None
                    or active.get("attempt_id") != claim.attempt_id
                ):
                    return False
                serialized = {
                    "pid": identity.pid,
                    "start_time": identity.start_time,
                    "group_id": identity.group_id,
                    "job_name": identity.job_name,
                }
                active["process_identity"] = serialized
                node["attempts"][-1]["process_identity"] = serialized
                node["attempts"][-1]["process_started_at"] = _utc_now()
                spawn = node["attempts"][-1].get("spawn")
                if isinstance(spawn, dict):
                    spawn["state"] = "started"
                    spawn["process_started_at"] = node["attempts"][-1][
                        "process_started_at"
                    ]
                    active["spawn"] = dict(spawn)
                self._append_locked(
                    directory,
                    projection,
                    "process_started",
                    {"process_identity": serialized},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_process_stopped(
        self,
        claim: NodeClaim,
        identity: ProcessIdentity,
        *,
        cleaned: bool,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Record cleanup against the immutable attempt, including after expiry."""
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                attempt = next(
                    (
                        candidate
                        for candidate in node.get("attempts", [])
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                serialized = attempt.get("process_identity")
                if (
                    not isinstance(serialized, dict)
                    or serialized.get("pid") != identity.pid
                    or serialized.get("start_time") != identity.start_time
                    or serialized.get("job_name") != identity.job_name
                ):
                    return False
                event_type = "process_reaped" if cleaned else "cleanup_failed"
                active_attempt = active.get("attempt_id") == claim.attempt_id
                if active_attempt and cleaned:
                    active.pop("process_identity", None)
                attempt["process_stop"] = {
                    "recorded_at": _utc_now(),
                    "cleaned": cleaned,
                    "identity_matched": True,
                }
                recovery = node.get("recovery")
                if (
                    isinstance(recovery, dict)
                    and recovery.get("attempt_id") == claim.attempt_id
                ):
                    recovery["termination_confirmed"] = cleaned
                    recovery["observation"] = (
                        "known_stopped" if cleaned else "outcome_uncertain"
                    )
                self._append_locked(
                    directory,
                    projection,
                    event_type,
                    {"pid": identity.pid, "cleanup_complete": cleaned},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=(
                        claim.attempt_id if cleaned and not active_attempt else None
                    ),
                    reserve_connection=fence_connection,
                )
                if cleaned and not active_attempt:
                    with (
                        nullcontext(fence_connection)
                        if fence_connection is not None
                        else self._connect()
                    ) as connection:
                        self._sync_integrity_index(
                            connection,
                            projection=projection,
                            journal_sha256=_sha256(
                                (directory / "events.jsonl").read_bytes()
                            ),
                        )
                        self._release_worker_claim(
                            claim.attempt_id, connection=connection
                        )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_provider_dispatch(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Authorize one registered worker to cross the provider boundary."""
        nonce = self._executor_nonce(executor_nonce)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                if (
                    projection.get("status") != "running"
                    or projection.get("desired_status") is not None
                ):
                    return False
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                existing = attempt.get("provider_dispatch")
                if isinstance(existing, Mapping):
                    return (
                        existing.get("executor_nonce") == nonce
                        and existing.get("state")
                        in {
                            "authorized",
                            "delivered",
                            "execute_received",
                            "released",
                        }
                    )
                spawn = attempt.get("spawn")
                if (
                    not isinstance(spawn, Mapping)
                    or spawn.get("executor_nonce") != nonce
                    or spawn.get("state") != "started"
                    or not isinstance(attempt.get("process_identity"), Mapping)
                ):
                    return False
                dispatch = {
                    "state": "authorized",
                    "executor_nonce": nonce,
                    "recorded_at": _utc_now(),
                }
                attempt["provider_dispatch"] = dispatch
                active["provider_dispatch"] = dict(dispatch)
                self._append_locked(
                    directory,
                    projection,
                    "provider_dispatch_authorized",
                    {"executor_nonce": nonce},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_provider_start_delivered(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Record the child's provider-start receipt before execution release."""
        nonce = self._executor_nonce(executor_nonce)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                if (
                    projection.get("status") != "running"
                    or projection.get("desired_status") is not None
                ):
                    return False
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                dispatch = attempt.get("provider_dispatch")
                if not isinstance(dispatch, Mapping) or dispatch.get(
                    "executor_nonce"
                ) != nonce:
                    return False
                if dispatch.get("state") in {
                    "delivered",
                    "execute_received",
                    "released",
                }:
                    return True
                if dispatch.get("state") != "authorized":
                    return False
                delivered = {
                    "state": "delivered",
                    "executor_nonce": nonce,
                    "authorized_at": dispatch.get("recorded_at"),
                    "recorded_at": _utc_now(),
                }
                attempt["provider_dispatch"] = delivered
                active["provider_dispatch"] = dict(delivered)
                self._append_locked(
                    directory,
                    projection,
                    "provider_start_delivered",
                    {"executor_nonce": nonce},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_provider_execute_received(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Record execute intent receipt while the child remains blocked."""
        nonce = self._executor_nonce(executor_nonce)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                if (
                    projection.get("status") != "running"
                    or projection.get("desired_status") is not None
                ):
                    return False
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                dispatch = attempt.get("provider_dispatch")
                if not isinstance(dispatch, Mapping) or dispatch.get(
                    "executor_nonce"
                ) != nonce:
                    return False
                if dispatch.get("state") in {"execute_received", "released"}:
                    return True
                if dispatch.get("state") != "delivered":
                    return False
                received = {
                    "state": "execute_received",
                    "executor_nonce": nonce,
                    "authorized_at": dispatch.get("authorized_at"),
                    "start_received_at": dispatch.get("recorded_at"),
                    "recorded_at": _utc_now(),
                }
                attempt["provider_dispatch"] = received
                active["provider_dispatch"] = dict(received)
                self._append_locked(
                    directory,
                    projection,
                    "provider_execute_received",
                    {"executor_nonce": nonce},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def record_provider_execute_released(
        self,
        claim: NodeClaim,
        *,
        executor_nonce: str,
        now: LeaseClockSample | None = None,
    ) -> bool:
        """Atomically linearize provider execution release against cancellation."""
        nonce = self._executor_nonce(executor_nonce)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                if (
                    projection.get("status") != "running"
                    or projection.get("desired_status") is not None
                ):
                    return False
                node = projection["nodes"][claim.node_id]
                active = node.get("claim", {})
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    return False
                dispatch = attempt.get("provider_dispatch")
                if not isinstance(dispatch, Mapping) or dispatch.get(
                    "executor_nonce"
                ) != nonce:
                    return False
                if dispatch.get("state") == "released":
                    return True
                if dispatch.get("state") != "execute_received":
                    return False
                released = {
                    **dict(dispatch),
                    "state": "released",
                    "recorded_at": _utc_now(),
                }
                attempt["provider_dispatch"] = released
                active["provider_dispatch"] = dict(released)
                self._append_locked(
                    directory,
                    projection,
                    "provider_execute_released",
                    {"executor_nonce": nonce},
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    defer_notification=fence_connection is not None,
                )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    @contextmanager
    def _terminal_completion_guard(self, claim: NodeClaim):
        try:
            yield
        except StorageQuotaError as exc:
            reason_code = "terminal_journal_reserve_exhausted"
            self._mark_repair_required(reason_code, run_id=claim.run_id)
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO store_repair_state ("
                    "run_id, attempt_id, reason_code, detected_at, payload_json) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, attempt_id) DO UPDATE SET "
                    "reason_code=excluded.reason_code, "
                    "detected_at=excluded.detected_at, "
                    "payload_json=excluded.payload_json",
                    (
                        claim.run_id,
                        claim.attempt_id,
                        reason_code,
                        _utc_now(),
                        json.dumps(
                            {"error": _sanitize_diagnostic(str(exc))},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                self._record_repair_event(
                    connection,
                    reason_code=reason_code,
                    outcome="repair_required",
                    run_id=claim.run_id,
                    payload={"attempt_id": claim.attempt_id},
                )
            raise

    def _publish_typed_bundle_locked(
        self,
        directory: Path,
        projection: Mapping[str, object],
        *,
        run_id: str,
        node_id: str,
        attempt_id: str,
        artifact: ArtifactRef,
        candidate: TypedPublicationCandidate,
    ) -> TypedPublicationRef:
        if (
            not isinstance(candidate.media_type, str)
            or candidate.media_type
            not in {
                _TYPED_PUBLICATION_JSON_MEDIA_TYPE,
                _TYPED_PUBLICATION_TEXT_MEDIA_TYPE,
            }
        ):
            raise ArchonOutputIntegrityError(
                "typed publication media type is invalid"
            )
        if (
            not isinstance(candidate.attempt_relative_path, str)
            or not candidate.attempt_relative_path
            or not isinstance(candidate.output_type, str)
            or not candidate.output_type.strip()
            or len(candidate.output_type) > DURABLE_METADATA_STRING_MAX_CHARS
            or isinstance(candidate.size_bytes, bool)
            or not isinstance(candidate.size_bytes, int)
            or not 0 <= candidate.size_bytes <= 500_000
            or not isinstance(candidate.sha256, str)
            or _SHA256_PATTERN.fullmatch(candidate.sha256) is None
            or (
                candidate.schema_fingerprint is not None
                and (
                    not isinstance(candidate.schema_fingerprint, str)
                    or _SHA256_PATTERN.fullmatch(candidate.schema_fingerprint) is None
                )
            )
            or isinstance(candidate.canonicalization_version, bool)
            or candidate.canonicalization_version != 1
            or (
                candidate.session_id is not None
                and (
                    not isinstance(candidate.session_id, str)
                    or len(candidate.session_id)
                    > DURABLE_METADATA_STRING_MAX_CHARS
                )
            )
        ):
            raise ArchonOutputIntegrityError(
                "typed publication candidate is invalid"
            )
        relative = PurePosixPath(candidate.attempt_relative_path)
        owned_prefixes = {
            ("nodes", node_id, attempt_id),
            (
                "nodes",
                _safe_component("node", node_id),
                _safe_component("attempt", attempt_id),
            ),
        }
        node_state = projection.get("nodes", {}).get(node_id, {})
        loop_state = (
            node_state.get("loop_state")
            if isinstance(node_state, Mapping)
            else None
        )
        iteration = (
            loop_state.get("iteration")
            if isinstance(loop_state, Mapping)
            else None
        )
        if isinstance(iteration, int) and not isinstance(iteration, bool):
            nested_attempt = f"{attempt_id}/iteration-{iteration:04d}"
            owned_prefixes.add((
                "nodes",
                _safe_component("node", node_id),
                _safe_component("attempt", nested_attempt),
            ))
        if len(relative.parts) <= 3 or relative.parts[:3] not in owned_prefixes:
            raise ArchonOutputIntegrityError(
                "typed publication content is not owned by the active attempt"
            )
        language = projection.get("language")
        language_profile = (
            language.get("effective_profile")
            if isinstance(language, Mapping)
            else None
        )
        if language_profile != "archon-2026-07":
            raise ArchonOutputIntegrityError(
                "typed publication requires the Archon language profile"
            )
        if (
            artifact.relative_path != candidate.attempt_relative_path
            or artifact.media_type != candidate.media_type
            or artifact.size_bytes != candidate.size_bytes
            or artifact.sha256 != candidate.sha256
        ):
            raise ArchonOutputIntegrityError(
                "typed publication candidate does not match one executor artifact"
            )
        try:
            content = _read_descriptor_relative(
                directory,
                candidate.attempt_relative_path,
                size_bytes=candidate.size_bytes,
            )
        except ArchonOutputUnavailableError as exc:
            raise ArchonOutputIntegrityError(
                "typed publication content is unavailable"
            ) from exc
        if (
            len(content) != candidate.size_bytes
            or _sha256(content) != candidate.sha256
        ):
            raise ArchonOutputIntegrityError(
                "typed publication content digest does not match"
            )
        if candidate.media_type == _TYPED_PUBLICATION_TEXT_MEDIA_TYPE:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ArchonOutputIntegrityError(
                    "typed publication Markdown content is not valid UTF-8"
                ) from exc

        publication_id = uuid.uuid4().hex
        content_name = (
            "content.json"
            if candidate.media_type == _TYPED_PUBLICATION_JSON_MEDIA_TYPE
            else "content.md"
        )
        produced_at = _utc_now()
        metadata_bytes = _typed_publication_metadata_bytes(
            publication_id=publication_id,
            content_name=content_name,
            output_type=candidate.output_type,
            media_type=candidate.media_type,
            sha256=candidate.sha256,
            node_id=node_id,
            attempt_id=attempt_id,
            run_id=run_id,
            schema_fingerprint=candidate.schema_fingerprint,
            size_bytes=candidate.size_bytes,
            produced_at=produced_at,
            session_id=candidate.session_id,
            canonicalization_version=candidate.canonicalization_version,
        )
        if len(metadata_bytes) > _TYPED_PUBLICATION_METADATA_MAX_BYTES:
            raise ArchonOutputIntegrityError(
                "typed publication metadata exceeds its byte ceiling"
            )

        self._ensure_free_disk()
        required_bytes = len(content) + len(metadata_bytes)
        if self._directory_bytes(directory) + required_bytes > self.max_run_bytes:
            raise StorageQuotaError("run_storage_quota exceeded by typed publication")
        if self._profile_storage_bytes() + required_bytes > self.max_profile_bytes:
            raise StorageQuotaError("profile_storage_quota exceeded by typed publication")

        _require_secure_publication_io()
        run_descriptor: int | None = None
        publications_descriptor: int | None = None
        staging_descriptor: int | None = None
        staging_name = f".staging-{uuid.uuid4().hex}"
        file_names = (content_name, "metadata.json")
        staging_created = False
        committed = False
        try:
            run_descriptor = os.open(directory, _publication_directory_flags())
            run_identity = _publication_directory_identity(run_descriptor)
            publications_created = False
            try:
                os.mkdir(
                    "publications",
                    mode=0o700,
                    dir_fd=run_descriptor,
                )
                publications_created = True
            except FileExistsError:
                pass
            try:
                publications_descriptor = os.open(
                    "publications",
                    _publication_directory_flags(),
                    dir_fd=run_descriptor,
                )
            except OSError as exc:
                raise ArchonOutputIntegrityError(
                    "typed publication directory is unsafe"
                ) from exc
            publications_identity = _publication_directory_identity(
                publications_descriptor
            )
            if publications_identity[0] != run_identity[0]:
                raise ArchonOutputIntegrityError(
                    "typed publication directory is not on the run filesystem"
                )
            if publications_created:
                os.fchmod(publications_descriptor, 0o700)
                _fsync_publication_directory(
                    run_descriptor,
                    boundary="run root",
                )

            os.mkdir(
                staging_name,
                mode=0o700,
                dir_fd=publications_descriptor,
            )
            staging_created = True
            staging_descriptor = os.open(
                staging_name,
                _publication_directory_flags(),
                dir_fd=publications_descriptor,
            )
            os.fchmod(staging_descriptor, 0o700)
            for name, data in (
                (content_name, content),
                ("metadata.json", metadata_bytes),
            ):
                _write_publication_file(staging_descriptor, name, data)
            _fsync_publication_directory(
                staging_descriptor,
                boundary="staging",
            )
            _commit_publication_directory_noreplace(
                run_descriptor,
                publications_descriptor,
                staging_name,
                publication_id,
                publications_identity,
            )
            committed = True
            _fsync_publication_directory(
                publications_descriptor,
                boundary="publication parent",
            )
        except BaseException:
            if (
                staging_created
                and not committed
                and publications_descriptor is not None
            ):
                _cleanup_publication_staging(
                    publications_descriptor,
                    staging_descriptor,
                    staging_name,
                    file_names,
                )
                staging_descriptor = None
            raise
        finally:
            if staging_descriptor is not None:
                os.close(staging_descriptor)
            if publications_descriptor is not None:
                os.close(publications_descriptor)
            if run_descriptor is not None:
                os.close(run_descriptor)
        return TypedPublicationRef(
            publication_id=publication_id,
            content_name=content_name,
            output_type=candidate.output_type,
            media_type=candidate.media_type,
            size_bytes=candidate.size_bytes,
            sha256=candidate.sha256,
            metadata_sha256=_sha256(metadata_bytes),
            schema_fingerprint=candidate.schema_fingerprint,
            canonicalization_version=candidate.canonicalization_version,
            produced_at=produced_at,
            session_id=candidate.session_id,
        )

    def _commit_recovered_publication_bundle(
        self,
        directory: Path,
        descriptor: _JournaledTypedPublication,
        content: bytes,
        metadata_bytes: bytes,
    ) -> None:
        self._ensure_free_disk()
        required_bytes = len(content) + len(metadata_bytes)
        if self._directory_bytes(directory) + required_bytes > self.max_run_bytes:
            raise StorageQuotaError(
                "run_storage_quota exceeded during typed publication recovery"
            )
        if self._profile_storage_bytes() + required_bytes > self.max_profile_bytes:
            raise StorageQuotaError(
                "profile_storage_quota exceeded during typed publication recovery"
            )
        _require_secure_publication_io()
        run_descriptor: int | None = None
        publications_descriptor: int | None = None
        staging_descriptor: int | None = None
        staging_name = f".staging-{uuid.uuid4().hex}"
        file_names = (descriptor.content_name, "metadata.json")
        staging_created = False
        committed = False
        try:
            run_descriptor = os.open(directory, _publication_directory_flags())
            run_identity = _publication_directory_identity(run_descriptor)
            try:
                os.mkdir("publications", mode=0o700, dir_fd=run_descriptor)
                _fsync_publication_directory(run_descriptor, boundary="run root")
            except FileExistsError:
                pass
            publications_descriptor = os.open(
                "publications",
                _publication_directory_flags(),
                dir_fd=run_descriptor,
            )
            publications_identity = _publication_directory_identity(
                publications_descriptor
            )
            if publications_identity[0] != run_identity[0]:
                raise JournalRecoveryError(
                    "typed publication directory is not on the run filesystem"
                )
            os.mkdir(staging_name, mode=0o700, dir_fd=publications_descriptor)
            staging_created = True
            staging_descriptor = os.open(
                staging_name,
                _publication_directory_flags(),
                dir_fd=publications_descriptor,
            )
            os.fchmod(staging_descriptor, 0o700)
            _write_publication_file(staging_descriptor, descriptor.content_name, content)
            _write_publication_file(staging_descriptor, "metadata.json", metadata_bytes)
            _fsync_publication_directory(staging_descriptor, boundary="staging")
            _commit_publication_directory_noreplace(
                run_descriptor,
                publications_descriptor,
                staging_name,
                descriptor.publication_id,
                publications_identity,
            )
            committed = True
            _fsync_publication_directory(
                publications_descriptor,
                boundary="publication parent",
            )
        except BaseException:
            if staging_created and not committed and publications_descriptor is not None:
                _cleanup_publication_staging(
                    publications_descriptor,
                    staging_descriptor,
                    staging_name,
                    file_names,
                )
                staging_descriptor = None
            raise
        finally:
            if staging_descriptor is not None:
                os.close(staging_descriptor)
            if publications_descriptor is not None:
                os.close(publications_descriptor)
            if run_descriptor is not None:
                os.close(run_descriptor)

    @staticmethod
    def _expected_publication_metadata(
        descriptor: _JournaledTypedPublication,
    ) -> bytes:
        return _typed_publication_metadata_bytes(
            publication_id=descriptor.publication_id,
            content_name=descriptor.content_name,
            output_type=descriptor.output_type,
            media_type=descriptor.media_type,
            sha256=descriptor.sha256,
            node_id=descriptor.node_id,
            attempt_id=descriptor.attempt_id,
            run_id=descriptor.run_id,
            schema_fingerprint=descriptor.schema_fingerprint,
            size_bytes=descriptor.size_bytes,
            produced_at=descriptor.produced_at,
            session_id=descriptor.session_id,
            canonicalization_version=descriptor.canonicalization_version,
        )

    def _verified_publication_content(
        self,
        directory: Path,
        descriptor: _JournaledTypedPublication,
    ) -> bytes | None:
        bundle = directory / "publications" / descriptor.publication_id
        try:
            observed = bundle.lstat()
        except FileNotFoundError:
            return None
        reparse_marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (
            reparse_marker
            and getattr(observed, "st_file_attributes", 0) & reparse_marker
        ):
            raise JournalRecoveryError(
                "typed publication integrity: bundle is a reparse point"
            )
        if (
            not stat.S_ISDIR(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
        ):
            return None
        try:
            names = {entry.name for entry in os.scandir(bundle)}
        except OSError:
            return None
        if names != {descriptor.content_name, "metadata.json"}:
            return None
        metadata_path = bundle / "metadata.json"
        try:
            metadata_size = metadata_path.lstat().st_size
            if metadata_size > _TYPED_PUBLICATION_METADATA_MAX_BYTES:
                return None
            content = _read_descriptor_relative(
                directory,
                f"publications/{descriptor.publication_id}/{descriptor.content_name}",
                size_bytes=descriptor.size_bytes,
            )
            metadata = _read_descriptor_relative(
                directory,
                f"publications/{descriptor.publication_id}/metadata.json",
                size_bytes=metadata_size,
            )
        except (ArchonOutputIntegrityError, ArchonOutputUnavailableError, OSError):
            return None
        expected_metadata = self._expected_publication_metadata(descriptor)
        if (
            len(content) != descriptor.size_bytes
            or not hmac.compare_digest(_sha256(content), descriptor.sha256)
            or not hmac.compare_digest(metadata, expected_metadata)
            or not hmac.compare_digest(_sha256(metadata), descriptor.metadata_sha256)
        ):
            return None
        return content

    def _recover_typed_publications_locked(
        self,
        directory: Path,
        projection: Mapping[str, object],
    ) -> dict[str, bytes]:
        run_id = str(projection["run_id"])
        run_descriptor: int | None = None
        publications_descriptor: int | None = None
        try:
            declared_outputs = _sealed_typed_output_declarations(
                directory,
                projection,
            )
            descriptors = _journaled_typed_publications(
                projection,
                declared_outputs,
            )
            expected = {descriptor.publication_id: descriptor for descriptor in descriptors}
            publications = directory / "publications"
            if not descriptors:
                try:
                    publications.lstat()
                except FileNotFoundError:
                    self._transition_run_repair(
                        "typed_publication_integrity",
                        run_id=run_id,
                        outcome="repair_verified",
                    )
                    return {}
            _require_secure_publication_io()
            run_descriptor = os.open(directory, _publication_directory_flags())
            run_identity = _publication_directory_identity(run_descriptor)
            try:
                publications_descriptor = os.open(
                    "publications",
                    _publication_directory_flags(),
                    dir_fd=run_descriptor,
                )
            except FileNotFoundError:
                publications_descriptor = None
            except OSError as exc:
                raise JournalRecoveryError(
                    "typed publication integrity: publication root is unsafe"
                ) from exc
            if publications_descriptor is not None:
                publications_identity = _publication_directory_identity(
                    publications_descriptor
                )
                observed = publications.lstat()
                reparse_marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if (
                    not stat.S_ISDIR(observed.st_mode)
                    or stat.S_ISLNK(observed.st_mode)
                    or (
                        reparse_marker
                        and getattr(observed, "st_file_attributes", 0)
                        & reparse_marker
                    )
                    or (observed.st_dev, observed.st_ino)
                    != publications_identity
                    or publications_identity[0] != run_identity[0]
                ):
                    raise JournalRecoveryError(
                        "typed publication integrity: publication root is unsafe"
                    )
                _verify_publication_directory_identity(
                    run_descriptor,
                    publications_descriptor,
                    publications_identity,
                )
                for name in tuple(os.listdir(publications_descriptor)):
                    if (
                        name.startswith(".staging-")
                        or name.startswith(".discard-")
                        or name not in expected
                    ):
                        _discard_publication_entry_at(
                            publications_descriptor,
                            name,
                        )
            verified: dict[str, bytes] = {}
            for descriptor in descriptors:
                content = self._verified_publication_content(directory, descriptor)
                if content is not None:
                    verified[descriptor.publication_id] = content
                    continue
                if publications_descriptor is not None:
                    _discard_publication_entry_at(
                        publications_descriptor,
                        descriptor.publication_id,
                    )
                try:
                    source_observed = (directory / descriptor.relative_path).lstat()
                    reparse_marker = getattr(
                        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
                    )
                    if (
                        not stat.S_ISREG(source_observed.st_mode)
                        or stat.S_ISLNK(source_observed.st_mode)
                        or (
                            reparse_marker
                            and getattr(source_observed, "st_file_attributes", 0)
                            & reparse_marker
                        )
                    ):
                        raise ArchonOutputUnavailableError(
                            "winning attempt source is not a regular file"
                        )
                    content = _read_descriptor_relative(
                        directory,
                        descriptor.relative_path,
                        size_bytes=descriptor.size_bytes,
                    )
                except (
                    ArchonOutputIntegrityError,
                    ArchonOutputUnavailableError,
                    OSError,
                ) as exc:
                    raise JournalRecoveryError(
                        "typed publication integrity: winning attempt is unavailable"
                    ) from exc
                if (
                    len(content) != descriptor.size_bytes
                    or not hmac.compare_digest(_sha256(content), descriptor.sha256)
                ):
                    raise JournalRecoveryError(
                        "typed publication integrity: winning attempt digest mismatch"
                    )
                metadata_bytes = self._expected_publication_metadata(descriptor)
                if not hmac.compare_digest(
                    _sha256(metadata_bytes), descriptor.metadata_sha256
                ):
                    raise JournalRecoveryError(
                        "typed publication integrity: metadata descriptor mismatch"
                    )
                self._commit_recovered_publication_bundle(
                    directory,
                    descriptor,
                    content,
                    metadata_bytes,
                )
                verified[descriptor.publication_id] = content
        except ArchonOutputIntegrityError as exc:
            self._transition_run_repair(
                "typed_publication_integrity",
                run_id=run_id,
                outcome="repair_required",
            )
            raise JournalRecoveryError(
                "typed publication integrity: publication directory identity changed"
            ) from exc
        except (
            JournalRecoveryError,
            OSError,
            StorageQuotaError,
            ValueError,
        ):
            self._transition_run_repair(
                "typed_publication_integrity",
                run_id=run_id,
                outcome="repair_required",
            )
            raise
        finally:
            if publications_descriptor is not None:
                os.close(publications_descriptor)
            if run_descriptor is not None:
                os.close(run_descriptor)
        self._transition_run_repair(
            "typed_publication_integrity",
            run_id=run_id,
            outcome="repair_verified",
        )
        return verified

    @staticmethod
    def _typed_mirror_payload(
        obligation: TypedMirrorObligation,
    ) -> dict[str, object]:
        return {
            "mirror_id": obligation.mirror_id,
            "workflow": obligation.workflow,
            "node_id": obligation.node_id,
            "operator_scope": obligation.operator_scope,
            "run_id": obligation.run_id,
            "attempt_id": obligation.attempt_id,
            "publication_id": obligation.publication_id,
            "content_name": obligation.content_name,
            "output_type": obligation.output_type,
            "media_type": obligation.media_type,
            "size_bytes": obligation.size_bytes,
            "sha256": obligation.sha256,
        }

    @classmethod
    def _typed_mirror_obligation(
        cls,
        projection: Mapping[str, object],
        descriptor: _JournaledTypedPublication,
    ) -> TypedMirrorObligation:
        operator_scope = str(projection.get("operator_scope_digest") or "local")
        identity = {
            "workflow": str(projection["workflow"]),
            "node_id": descriptor.node_id,
            "operator_scope": operator_scope,
            "run_id": descriptor.run_id,
            "attempt_id": descriptor.attempt_id,
            "publication_id": descriptor.publication_id,
            "content_name": descriptor.content_name,
            "output_type": descriptor.output_type,
            "media_type": descriptor.media_type,
            "size_bytes": descriptor.size_bytes,
            "sha256": descriptor.sha256,
        }
        mirror_id = _sha256(
            json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return TypedMirrorObligation(mirror_id=mirror_id, **identity)

    @staticmethod
    def _effective_persistent_publication(
        directory: Path,
        projection: Mapping[str, object],
        descriptor: _JournaledTypedPublication,
    ) -> bool:
        if descriptor.session_id is None:
            return False
        metadata = projection.get("run_metadata")
        if isinstance(metadata, Mapping) and metadata.get("ai_entitlement") == (
            "deterministic"
        ):
            return False
        node_state = projection.get("nodes", {}).get(descriptor.node_id)
        if (
            not isinstance(node_state, Mapping)
            or node_state.get("session_id") != descriptor.session_id
            or not isinstance(node_state.get("cache_fingerprint"), str)
            or not node_state.get("cache_fingerprint")
        ):
            return False
        definition = directory / "definition.yaml"
        try:
            observed = definition.lstat()
            if not stat.S_ISREG(observed.st_mode) or observed.st_size > 2 * 1024 * 1024:
                raise JournalRecoveryError(
                    "typed mirror workflow definition is unsafe"
                )
            document = yaml.safe_load(
                _read_descriptor_relative(
                    directory,
                    "definition.yaml",
                    size_bytes=observed.st_size,
                )
            )
        except (
            ArchonOutputIntegrityError,
            ArchonOutputUnavailableError,
            OSError,
            yaml.YAMLError,
        ) as exc:
            raise JournalRecoveryError(
                "typed mirror workflow definition is unavailable"
            ) from exc
        if not isinstance(document, Mapping) or not isinstance(
            document.get("nodes"), list
        ):
            raise JournalRecoveryError("typed mirror workflow definition is malformed")
        matching = [
            node
            for node in document["nodes"]
            if isinstance(node, Mapping) and node.get("id") == descriptor.node_id
        ]
        if len(matching) != 1:
            raise JournalRecoveryError("typed mirror node definition is ambiguous")
        node = matching[0]
        if not ("command" in node or "prompt" in node):
            return False
        context = node.get("context")
        if context in {"fresh", "shared"}:
            return False
        persist = node.get("persist_session", document.get("persist_sessions", False))
        return persist is True

    @classmethod
    def _typed_mirror_from_payload(
        cls,
        payload: object,
    ) -> TypedMirrorObligation:
        if not isinstance(payload, Mapping):
            raise JournalRecoveryError("typed mirror obligation is malformed")
        value = payload.get("mirror")
        if not isinstance(value, Mapping):
            raise JournalRecoveryError("typed mirror obligation is malformed")
        try:
            obligation = TypedMirrorObligation(
                mirror_id=value["mirror_id"],
                workflow=value["workflow"],
                node_id=value["node_id"],
                operator_scope=value["operator_scope"],
                run_id=value["run_id"],
                attempt_id=value["attempt_id"],
                publication_id=value["publication_id"],
                content_name=value["content_name"],
                output_type=value["output_type"],
                media_type=value["media_type"],
                size_bytes=value["size_bytes"],
                sha256=value["sha256"],
            )
            TypedMirrorStore._validate_obligation(obligation)
        except (KeyError, TypeError, TypedMirrorIntegrityError) as exc:
            raise JournalRecoveryError("typed mirror obligation is malformed") from exc
        expected_identity = dict(cls._typed_mirror_payload(obligation))
        expected_identity.pop("mirror_id")
        expected_mirror_id = _sha256(
            json.dumps(
                expected_identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        if not hmac.compare_digest(obligation.mirror_id, expected_mirror_id):
            raise JournalRecoveryError("typed mirror obligation identity is invalid")
        return obligation

    def _recover_typed_mirrors_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        verified_content: Mapping[str, bytes],
    ) -> None:
        run_id = str(projection["run_id"])
        try:
            self._recover_typed_mirrors_checked_locked(
                directory,
                projection,
                verified_content,
            )
        except (JournalRecoveryError, OSError, StorageQuotaError):
            self._transition_run_repair(
                "typed_mirror_integrity",
                run_id=run_id,
                outcome="repair_required",
            )
            raise
        except (TypedMirrorIntegrityError, ValueError) as exc:
            self._transition_run_repair(
                "typed_mirror_integrity",
                run_id=run_id,
                outcome="repair_required",
            )
            raise JournalRecoveryError("typed mirror integrity failure") from exc
        self._transition_run_repair(
            "typed_mirror_integrity",
            run_id=run_id,
            outcome="repair_verified",
        )

    def _recover_typed_mirrors_checked_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        verified_content: Mapping[str, bytes],
    ) -> None:
        descriptors = {
            descriptor.publication_id: descriptor
            for descriptor in _journaled_typed_publications(
                projection,
                _sealed_typed_output_declarations(directory, projection),
            )
        }
        if (
            not descriptors
            and not self._journal_may_contain_typed_mirror_events(directory)
        ):
            return
        expected = {
            obligation.mirror_id: obligation
            for descriptor in descriptors.values()
            if self._effective_persistent_publication(
                directory,
                projection,
                descriptor,
            )
            for obligation in (self._typed_mirror_obligation(projection, descriptor),)
        }
        events = self._read_journal_events(directory)
        required: dict[str, TypedMirrorObligation] = {}
        completed: dict[str, str] = {}
        for event in events:
            event_type = event.get("event_type")
            if event_type == "typed_mirror_required":
                obligation = self._typed_mirror_from_payload(event.get("payload"))
                if (
                    event.get("node_id") != obligation.node_id
                    or event.get("attempt_id") != obligation.attempt_id
                    or obligation.run_id != projection.get("run_id")
                ):
                    raise JournalRecoveryError(
                        "typed mirror obligation is not corroborated"
                    )
                if obligation.mirror_id in required and required[
                    obligation.mirror_id
                ] != obligation:
                    raise JournalRecoveryError(
                        "typed mirror obligation identity conflicts"
                    )
                required[obligation.mirror_id] = obligation
            elif event_type == "typed_mirror_completed":
                payload = event.get("payload")
                mirror_id = payload.get("mirror_id") if isinstance(payload, Mapping) else None
                entry_id = payload.get("entry_id") if isinstance(payload, Mapping) else None
                if (
                    not isinstance(mirror_id, str)
                    or _SHA256_PATTERN.fullmatch(mirror_id) is None
                    or not isinstance(entry_id, str)
                    or _SHA256_PATTERN.fullmatch(entry_id) is None
                    or mirror_id not in required
                    or event.get("node_id") != required[mirror_id].node_id
                    or event.get("attempt_id") != required[mirror_id].attempt_id
                ):
                    raise JournalRecoveryError(
                        "typed mirror completion is not corroborated"
                    )
                expected_entry_id = TypedMirrorStore._entry_document(
                    required[mirror_id]
                )["entry_id"]
                if not hmac.compare_digest(entry_id, str(expected_entry_id)):
                    raise JournalRecoveryError(
                        "typed mirror completion identity is invalid"
                    )
                previous = completed.get(mirror_id)
                if previous is not None and previous != entry_id:
                    raise JournalRecoveryError(
                        "typed mirror completion identity conflicts"
                    )
                completed[mirror_id] = entry_id
        unbacked = (set(required) | set(completed)) - set(expected)
        if not expected and not unbacked:
            return
        mirror_store = TypedMirrorStore(
            self.hermes_home,
            capacity_check=self._ensure_mirror_capacity,
            free_disk_check=self._ensure_free_disk,
        )
        if unbacked:
            for mirror_id in sorted(unbacked):
                mirror_store.invalidate(required[mirror_id])
            raise JournalRecoveryError(
                "typed mirror journal obligation is not backed by a publication"
            )
        for mirror_id, obligation in expected.items():
            existing = required.get(mirror_id)
            if existing is not None and existing != obligation:
                raise JournalRecoveryError(
                    "typed mirror obligation conflicts with journal"
                )
            descriptor = descriptors.get(obligation.publication_id)
            content = verified_content.get(obligation.publication_id)
            if descriptor is None or content is None:
                raise JournalRecoveryError(
                    "typed mirror requires a verified run publication"
                )
            new_requirement = existing is None
            was_completed = mirror_id in completed
            transaction_bytes = self._typed_mirror_transaction_bytes(
                projection,
                obligation,
                requirement_pending=new_requirement,
                completion_pending=not was_completed,
            )
            with mirror_store.capacity_reservation(
                obligation,
                content,
                transaction_bytes=transaction_bytes,
            ):
                if new_requirement:
                    self._append_locked(
                        directory,
                        projection,
                        "typed_mirror_required",
                        {"mirror": self._typed_mirror_payload(obligation)},
                        node_id=obligation.node_id,
                        attempt_id=obligation.attempt_id,
                    )
                    required[mirror_id] = obligation
                try:
                    record = mirror_store.stage(
                        obligation,
                        content,
                    )
                except TypedMirrorIntegrityError as exc:
                    raise JournalRecoveryError(
                        "typed mirror integrity failure"
                    ) from exc
                try:
                    pointed = mirror_store.point(
                        record,
                        replace_current=not was_completed,
                    )
                except TypedMirrorIntegrityError as exc:
                    raise JournalRecoveryError(
                        "typed mirror integrity failure"
                    ) from exc
                if not was_completed and not pointed:
                    continue
                if not was_completed:
                    self._append_locked(
                        directory,
                        projection,
                        "typed_mirror_completed",
                        {"mirror_id": mirror_id, "entry_id": record.entry_id},
                        node_id=obligation.node_id,
                        attempt_id=obligation.attempt_id,
                    )
                    completed[mirror_id] = record.entry_id
                try:
                    mirror_store.verify(record)
                except TypedMirrorIntegrityError as exc:
                    raise JournalRecoveryError(
                        "typed mirror integrity failure"
                    ) from exc

    def complete_node(
        self,
        claim: NodeClaim,
        *,
        status: str,
        artifacts: Iterable[ArtifactRef] = (),
        typed_publication: TypedPublicationCandidate | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
        session_registry_update: SessionRegistryUpdateCandidate | None = None,
        session_registry_authority: SessionRegistryUpdateCandidate | None = None,
        now: LeaseClockSample | None = None,
    ) -> None:
        if status not in {
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "paused",
        }:
            raise ValueError(f"invalid node completion state: {status}")
        artifacts = tuple(artifacts)
        directory = self.run_directory(claim.run_id)
        with (
            self._terminal_completion_guard(claim),
            workflow_lock(self.admission_lock),
            workflow_lock(self._run_lock_path(claim.run_id)),
            self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection,
        ):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                stale_attempt = next(
                    (
                        candidate
                        for candidate in node.get("attempts", [])
                        if candidate.get("attempt_id") == claim.attempt_id
                    ),
                    None,
                )
                if isinstance(stale_attempt, dict):
                    stale_attempt.setdefault("stale_completions", []).append({
                        "observed_at": _utc_now(),
                        "status": status,
                        "error_code": error_code,
                        "error_message": _sanitize_diagnostic(error_message),
                    })
                    self._append_locked(
                        directory,
                        projection,
                        "stale_node_completion",
                        {"status": status, "error_code": error_code},
                        node_id=claim.node_id,
                        attempt_id=claim.attempt_id,
                        defer_notification=fence_connection is not None,
                    )
                raise RuntimeError("stale node completion")
            if (
                projection["status"] in {"cancelled", "abandoned"}
                or projection.get("desired_status") == "cancelled"
            ) and status != "cancelled" and not (
                status == "succeeded" and session_registry_update is not None
            ):
                raise RuntimeError("stale completion for terminal run")
            if (session_registry_update is None) != (
                session_registry_authority is None
            ):
                raise ValueError(
                    "session registry update authority is incomplete"
                )
            if session_registry_update is not None:
                completion_metadata = metadata or {}
                if (
                    status != "succeeded"
                    or session_registry_authority != session_registry_update
                    or completion_metadata.get("session_id")
                    != session_registry_update.new_session_id
                    or completion_metadata.get("cache_fingerprint")
                    != session_registry_update.cache_fingerprint
                    or session_registry_update.winning_run_id != claim.run_id
                    or session_registry_update.winning_node_id != claim.node_id
                    or session_registry_update.winning_attempt_id
                    != claim.attempt_id
                    or session_registry_update.key.workflow
                    != projection.get("workflow")
                    or session_registry_update.key.scope
                    != str(projection.get("operator_scope_digest") or "local")
                ):
                    raise ValueError(
                        "session registry update does not match winning completion"
                    )
                if (
                    session_registry_update.winning_attempt_id
                    in _pending_session_registry_payloads(projection)
                ):
                    raise JournalRecoveryError(
                        "attempt already has a pending session registry update"
                    )
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as authority_connection:
                    if fence_connection is None:
                        authority_connection.execute("BEGIN IMMEDIATE")
                    self._write_private_authority(
                        authority_connection,
                        table="session_registry_winner_authority",
                        run_id=claim.run_id,
                        attempt_id=claim.attempt_id,
                        authority=_session_registry_winner_authority(
                            session_registry_update,
                            activation_event_sequence=(
                                int(projection["event_sequence"]) + 1
                            ),
                            activation_event_type="node_succeeded",
                        ),
                    )
                    if fence_connection is None:
                        authority_connection.commit()
            if status == "paused":
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as connection:
                    paused = connection.execute(
                        "SELECT COUNT(*) FROM runs WHERE status='paused' AND run_id<>?",
                        (claim.run_id,),
                    ).fetchone()[0]
                if paused >= self.limits["paused"]:
                    status = "failed"
                    error_code = "paused_capacity"
                    error_message = "profile paused-run capacity is exhausted"
                    metadata = {
                        key: value
                        for key, value in dict(metadata or {}).items()
                        if key != "pending_interaction"
                    }
            publication_ref = None
            publication_artifact = None
            if status == "succeeded" and typed_publication is not None:
                publication_artifact = _canonical_typed_publication_artifact(
                    artifacts,
                    typed_publication,
                )
                projected_matches = [
                    entry
                    for entry in projection["artifacts"]
                    if isinstance(entry, dict)
                    and entry.get("node_id") == claim.node_id
                    and entry.get("attempt_id") == claim.attempt_id
                    and entry.get("relative_path")
                    == publication_artifact.relative_path
                ]
                if len(projected_matches) > 1 or any(
                    entry.get("media_type") != publication_artifact.media_type
                    or entry.get("size_bytes") != publication_artifact.size_bytes
                    or entry.get("sha256") != publication_artifact.sha256
                    for entry in projected_matches
                ):
                    raise ArchonOutputIntegrityError(
                        "projected artifact conflicts with typed publication"
                    )
                publication_ref = self._publish_typed_bundle_locked(
                    directory,
                    projection,
                    run_id=claim.run_id,
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    artifact=publication_artifact,
                    candidate=typed_publication,
                )
            node["state"] = status
            node.pop("claim", None)
            safe_error_message = _sanitize_diagnostic(error_message)
            node["attempts"][-1].update({
                "state": status,
                "error_code": error_code,
                "error_message": safe_error_message,
                "completed_at": _utc_now(),
            })
            safe_metadata = dict(_sanitize(dict(metadata or {})))
            safe_metadata.pop("output", None)
            node["attempts"][-1]["metadata"] = safe_metadata
            if session_registry_update is not None:
                node["attempts"][-1]["session_registry_authority"] = (
                    _session_registry_candidate_payload(
                        session_registry_authority
                    )
                )
            if status == "cancelled":
                for other_id, other in projection["nodes"].items():
                    if other_id == claim.node_id or other["state"] in {
                        "succeeded",
                        "failed",
                        "skipped",
                        "cancelled",
                    }:
                        continue
                    other_claim = other.pop("claim", None)
                    other["state"] = "cancelled"
                    if other_claim and other.get("attempts"):
                        other["attempts"][-1].update({
                            "state": "cancelled",
                            "error_code": "cancelled",
                        })
            for field in (
                "session_id",
                "cache_fingerprint",
                "provider",
                "model",
                "usage",
                "pending_interaction",
                "retry_consumed",
                "loop_state",
                "approval_generation",
                "approval_rework_attempts",
                "approval_rework",
            ):
                if field in safe_metadata:
                    node[field] = safe_metadata[field]
            if status == "paused" and "approval_generation" in safe_metadata:
                node.pop("approval_rework", None)
            for warning in safe_metadata.get("warnings", []):
                if isinstance(warning, str) and warning not in projection["warnings"]:
                    projection["warnings"].append(warning)
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                if (
                    publication_ref is not None
                    and artifact == publication_artifact
                ):
                    entry.update(_typed_publication_fields(publication_ref))
                refs.append(entry)
                existing_indices = [
                    index
                    for index, existing in enumerate(projection["artifacts"])
                    if isinstance(existing, dict)
                    and existing.get("attempt_id") == claim.attempt_id
                    and existing.get("relative_path") == artifact.relative_path
                ]
                if artifact == publication_artifact and existing_indices:
                    projection["artifacts"][existing_indices[0]] = entry
                elif not existing_indices:
                    projection["artifacts"].append(entry)
            if session_registry_update is not None:
                if not _session_registry_candidate_is_corroborated(
                    projection,
                    session_registry_update,
                ):
                    raise JournalRecoveryError(
                        "session registry update authority is uncorroborated"
                    )
                _set_pending_session_registry_update(
                    projection,
                    session_registry_update,
                )
            self._append_locked(
                directory,
                projection,
                f"node_{status}",
                {
                    "artifacts": refs,
                    "error_code": error_code,
                    "metadata": {
                        key: value
                        for key, value in safe_metadata.items()
                        if session_registry_update is None
                        or key not in {"session_id", "cache_fingerprint"}
                    },
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
                terminal_reserve_attempt_id=claim.attempt_id,
                reserve_connection=fence_connection,
            )
            if publication_ref is not None:
                verified_content = self._recover_typed_publications_locked(
                    directory,
                    projection,
                )
                self._recover_typed_mirrors_locked(
                    directory,
                    projection,
                    verified_content,
                )
            states = {candidate["state"] for candidate in projection["nodes"].values()}
            terminal = None
            if status == "failed":
                projection["last_error"] = {
                    "code": error_code or "node_failed",
                    "message": safe_error_message or "node execution failed",
                    "node_id": claim.node_id,
                }
            if status in {"cancelled", "interrupted", "paused"}:
                terminal = status
            elif states and states <= {
                "succeeded",
                "failed",
                "skipped",
                "cancelled",
                "interrupted",
            }:
                terminal = "failed" if "failed" in states else "succeeded"
            if _pending_session_registry_payloads(projection):
                terminal = None
            if terminal:
                projection["status"] = terminal
                self._append_locked(
                    directory,
                    projection,
                    f"run_{terminal}",
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=claim.attempt_id,
                    reserve_connection=fence_connection,
                )
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as connection:
                    connection.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (terminal, projection["updated_at"], claim.run_id),
                    )
                    self._record_coordinator_wake(
                        connection,
                        run_id=claim.run_id,
                        reason_code=f"run_{terminal}",
                    )
                    if terminal == "cancelled":
                        connection.execute(
                            "DELETE FROM worker_claims WHERE run_id=?", (claim.run_id,)
                        )
            else:
                if "waiting_retry" in states and not states & {
                    "ready",
                    "claimed",
                    "running",
                }:
                    projection["status"] = "waiting_retry"
                    self._append_locked(
                        directory,
                        projection,
                        "run_retry_waiting",
                        defer_notification=fence_connection is not None,
                        terminal_reserve_attempt_id=claim.attempt_id,
                        reserve_connection=fence_connection,
                    )
                    with (
                        nullcontext(fence_connection)
                        if fence_connection is not None
                        else self._connect()
                    ) as connection:
                        connection.execute(
                            "UPDATE runs SET status='waiting_retry', updated_at=? "
                            "WHERE run_id=?",
                            (projection["updated_at"], claim.run_id),
                        )
                        self._record_coordinator_wake(
                            connection,
                            run_id=claim.run_id,
                            reason_code="retry_waiting",
                        )
                _atomic_json(directory / "run.json", projection)
            with (
                nullcontext(fence_connection)
                if fence_connection is not None
                else self._connect()
            ) as final_connection:
                if fence_connection is None:
                    final_connection.execute("BEGIN IMMEDIATE")
                self._sync_integrity_index(
                    final_connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                if session_registry_update is not None:
                    self._transfer_obligation_journal_reserve(
                        claim.attempt_id,
                        claim.run_id,
                        connection=final_connection,
                    )
                self._release_worker_claim(
                    claim.attempt_id, connection=final_connection
                )
                if fence_connection is None:
                    final_connection.commit()
            if session_registry_update is not None:
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as pending_connection:
                    self._record_coordinator_wake(
                        pending_connection,
                        run_id=claim.run_id,
                        reason_code="pending_session_registry_update",
                    )
            if (
                terminal
                or projection["status"] == "waiting_retry"
                or session_registry_update is not None
            ):
                self._notify_coordinator()

    @staticmethod
    def _set_session_recovery_outcome(
        projection: dict[str, object],
        candidate: SessionRegistryUpdateCandidate,
        outcome: str,
    ) -> None:
        if not candidate.recovery_selected:
            return
        node = projection.get("nodes", {}).get(candidate.winning_node_id)
        recoveries = node.get("session_recoveries") if isinstance(node, dict) else None
        if not isinstance(recoveries, list):
            raise JournalRecoveryError(
                "persistent session recovery evidence is missing"
            )
        matches = [
            item
            for item in recoveries
            if isinstance(item, dict)
            and item.get("attempt_id") == candidate.winning_attempt_id
        ]
        if len(matches) != 1:
            raise JournalRecoveryError(
                "persistent session recovery evidence is uncorroborated"
            )
        matches[0]["outcome"] = outcome

    def pending_session_registry_update(
        self,
        run_id: str,
    ) -> tuple[SessionRegistryUpdateCandidate, int, str | None] | None:
        projection = self.load_run(run_id)
        pending = _pending_session_registry_payloads(projection)
        if not pending:
            return None
        decoded = [
            _session_registry_candidate_from_payload(payload)
            for payload in pending.values()
        ]
        retrying = [item for item in decoded if item[1] > 0]
        candidate, retry_count = (
            retrying[0]
            if retrying
            else min(decoded, key=lambda item: item[0].winning_attempt_id)
        )
        if candidate.winning_run_id != run_id:
            raise JournalRecoveryError("session registry obligation run is invalid")
        next_at = projection.get("next_registry_update_at")
        if next_at is not None and not isinstance(next_at, str):
            raise JournalRecoveryError("session registry obligation wake is invalid")
        return candidate, retry_count, next_at

    def defer_session_registry_update(
        self,
        run_id: str,
        candidate: SessionRegistryUpdateCandidate,
        *,
        now: datetime,
    ) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            pending = _pending_session_registry_payloads(projection)
            payload = pending.get(candidate.winning_attempt_id)
            if payload is None:
                raise RuntimeError("stale session registry obligation deferral")
            current, retry_count = _session_registry_candidate_from_payload(payload)
            if current != candidate or retry_count >= 5:
                raise RuntimeError("stale session registry obligation deferral")
            attempt = retry_count + 1
            delay_seconds = (1, 2, 4, 8, 16)[retry_count]
            _set_pending_session_registry_update(
                projection,
                candidate,
                retry_count=attempt,
            )
            projection["next_registry_update_at"] = (
                now.astimezone(timezone.utc) + timedelta(seconds=delay_seconds)
            ).isoformat()
            self._set_session_recovery_outcome(
                projection,
                candidate,
                "registry_update_deferred",
            )
            if attempt == 5:
                projection["status"] = "recovery_pending"
                projection["last_error"] = {
                    "code": "persistent_session_registry_update_pending",
                    "message": "persistent session registry update remains pending",
                    "node_id": candidate.winning_node_id,
                }
            self._append_locked(
                directory,
                projection,
                "persistent_session_registry_update_deferred",
                {
                    "attempt_id": candidate.winning_attempt_id,
                    "registry_generation": candidate.expected_generation,
                    "outcome": "registry_update_deferred",
                    "registry_update_attempt": attempt,
                    "next_registry_update_at": projection[
                        "next_registry_update_at"
                    ],
                },
                node_id=candidate.winning_node_id,
                attempt_id=candidate.winning_attempt_id,
                terminal_reserve_attempt_id=candidate.winning_attempt_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], run_id),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
            self._notify_coordinator()
            return attempt

    def resolve_session_registry_update(
        self,
        run_id: str,
        candidate: SessionRegistryUpdateCandidate,
        *,
        outcome: str,
    ) -> str:
        if outcome not in {
            "stale_entry_replaced",
            "stale_entry_replaced_already_applied",
            "newer_entry_retained",
        }:
            raise ValueError("invalid session registry update outcome")
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            self._set_session_recovery_outcome(projection, candidate, outcome)
            _remove_pending_session_registry_update(projection, candidate)
            projection.pop("next_registry_update_at", None)
            has_pending = bool(_pending_session_registry_payloads(projection))
            if (
                isinstance(projection.get("last_error"), Mapping)
                and projection["last_error"].get("code")
                == "persistent_session_registry_update_pending"
            ):
                projection["last_error"] = None
            if outcome == "newer_entry_retained":
                warning = "newer persistent session retained"
                if warning not in projection["warnings"]:
                    projection["warnings"].append(warning)
            terminal = None
            if has_pending:
                terminal = None
            elif projection.get("desired_status") == "cancelled":
                projection["desired_status"] = None
                for node in projection["nodes"].values():
                    if node["state"] not in {"succeeded", "failed", "skipped"}:
                        node.pop("claim", None)
                        node["state"] = "cancelled"
                terminal = "cancelled"
            else:
                states = {
                    node["state"] for node in projection["nodes"].values()
                }
                if states and states <= {
                    "succeeded",
                    "failed",
                    "skipped",
                    "cancelled",
                    "interrupted",
                }:
                    terminal = "failed" if "failed" in states else "succeeded"
            projection["status"] = terminal or "running"
            event_type = (
                f"run_{terminal}"
                if terminal is not None
                else "persistent_session_registry_update_resolved"
            )
            self._append_locked(
                directory,
                projection,
                event_type,
                {
                    "attempt_id": candidate.winning_attempt_id,
                    "registry_generation": candidate.expected_generation,
                    "outcome": outcome,
                },
                node_id=candidate.winning_node_id,
                attempt_id=candidate.winning_attempt_id,
                terminal_reserve_attempt_id=candidate.winning_attempt_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM obligation_journal_reserves WHERE attempt_id=?",
                    (candidate.winning_attempt_id,),
                )
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], run_id),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._record_coordinator_wake(
                    connection,
                    run_id=run_id,
                    reason_code=event_type,
                )
            self._notify_coordinator()
            return outcome

    def record_loop_iteration(
        self,
        claim: NodeClaim,
        *,
        artifacts: Iterable[ArtifactRef],
        loop_state: Mapping[str, object],
        now: LeaseClockSample | None = None,
    ) -> None:
        """Persist one completed loop iteration before evaluating continuation."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(
            self._run_lock_path(claim.run_id)
        ), self._execution_fence_transaction(
            claim.execution_fence, now
        ) as fence_connection:
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale loop iteration")
            safe_state = dict(_sanitize(dict(loop_state)))
            node["loop_state"] = safe_state
            existing = {
                (entry.get("attempt_id"), entry.get("relative_path"))
                for entry in projection["artifacts"]
                if isinstance(entry, dict)
            }
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                if (claim.attempt_id, artifact.relative_path) not in existing:
                    projection["artifacts"].append(entry)
            self._append_locked(
                directory,
                projection,
                "loop_iteration_completed",
                {
                    "iteration": safe_state.get("iteration"),
                    "artifacts": refs,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
            )

    def block_cleanup_failed(
        self,
        claim: NodeClaim,
        *,
        artifacts: Iterable[ArtifactRef] = (),
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
        consumed_attempts: int | None = None,
        now: LeaseClockSample | None = None,
    ) -> None:
        """Keep ownership blocked when an executor cannot prove tree cleanup."""
        if consumed_attempts is not None and (
            isinstance(consumed_attempts, bool)
            or not isinstance(consumed_attempts, int)
            or consumed_attempts < 0
        ):
            raise ValueError("consumed attempts must be a non-negative integer")
        directory = self.run_directory(claim.run_id)
        with workflow_lock(
            self._run_lock_path(claim.run_id)
        ), self._execution_fence_transaction(
            claim.execution_fence, now
        ) as fence_connection:
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale cleanup failure")
            safe_metadata = None
            if metadata is not None:
                safe_metadata = dict(_sanitize(dict(metadata)))
                safe_metadata.pop("output", None)
                node["attempts"][-1]["metadata"] = safe_metadata
            if consumed_attempts is not None:
                node["retry_consumed"] = consumed_attempts
            projection["desired_status"] = "cleanup_failed"
            projection["last_error"] = {
                "code": "cleanup_failed",
                "message": _sanitize_diagnostic(error_message)
                or "owned process cleanup did not complete",
                "node_id": claim.node_id,
            }
            existing = {
                (entry.get("attempt_id"), entry.get("relative_path"))
                for entry in projection["artifacts"]
                if isinstance(entry, dict)
            }
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                if (claim.attempt_id, artifact.relative_path) not in existing:
                    projection["artifacts"].append(entry)
            self._append_locked(
                directory,
                projection,
                "cleanup_failed",
                {
                    "artifacts": refs,
                    "cleanup_complete": False,
                    **(
                        {"retry_consumed": consumed_attempts}
                        if consumed_attempts is not None
                        else {}
                    ),
                    **({"metadata": safe_metadata} if safe_metadata is not None else {}),
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
            )
            with (
                nullcontext(fence_connection)
                if fence_connection is not None
                else self._connect()
            ) as connection:
                connection.execute(
                    "UPDATE runs SET desired_status='cleanup_failed', updated_at=? "
                    "WHERE run_id=?",
                    (projection["updated_at"], claim.run_id),
                )
                self._record_admission_event(
                    connection,
                    "cleanup_failed",
                    run_id=claim.run_id,
                    reason_code="uninterruptible_process",
                )

    def _prepare_journal_frame(
        self,
        projection: dict[str, object],
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        compact_recovery: bool = False,
        sample: LeaseClockSample | None = None,
        timestamp: str | None = None,
    ) -> tuple[dict[str, object], bytes]:
        projection.setdefault("pause_lane_policy", "hold")
        projection.setdefault("queue_sequence", None)
        projection["lane_state"] = self._lane_state(projection)
        if event_type not in {
            "node_heartbeat",
            "run_stalled",
            "evidence_annotation",
        }:
            observed = sample or self._lease_clock()
            projection["last_runnable_progress_at"] = observed.utc_now.isoformat()
            projection["last_runnable_progress_monotonic"] = observed.monotonic_now
            projection["last_runnable_progress_boot_id"] = observed.boot_id
            projection["progress_boot_id"] = observed.boot_id
        sequence = int(projection["event_sequence"]) + 1
        now = timestamp or _utc_now()
        projection["event_sequence"] = sequence
        projection["state_version"] = int(projection["state_version"]) + 1
        projection["updated_at"] = now
        recovery = {"projection_sha256": _projection_digest(projection)}
        if not compact_recovery:
            recovery["projection"] = json.loads(
                json.dumps(projection, sort_keys=True, ensure_ascii=False)
            )
        raw_payload = dict(payload or {})
        event = {
            "sequence": sequence,
            "timestamp": now,
            "run_id": projection["run_id"],
            "node_id": node_id,
            "attempt_id": attempt_id,
            "event_type": event_type,
            "payload": _sanitize(raw_payload),
            **recovery,
        }
        if projection_was_truncated(raw_payload):
            event["payload_truncated"] = True
        return _encode_journal_frame(event)

    def _typed_mirror_transaction_bytes(
        self,
        projection: Mapping[str, object],
        obligation: TypedMirrorObligation,
        *,
        requirement_pending: bool,
        completion_pending: bool,
    ) -> int:
        """Bound journal frames and projection temp files for one mirror."""
        simulated = json.loads(
            json.dumps(projection, sort_keys=True, ensure_ascii=False)
        )
        pessimistic_sample = LeaseClockSample(
            datetime.max.replace(tzinfo=timezone.utc),
            1.7976931348623157e308,
            "f" * 256,
        )
        timestamp = pessimistic_sample.utc_now.isoformat()
        transitions: list[tuple[str, Mapping[str, object]]] = []
        if requirement_pending:
            transitions.append((
                "typed_mirror_required",
                {"mirror": self._typed_mirror_payload(obligation)},
            ))
        if completion_pending:
            entry_id = str(
                TypedMirrorStore._entry_document(obligation)["entry_id"]
            )
            transitions.append((
                "typed_mirror_completed",
                {"mirror_id": obligation.mirror_id, "entry_id": entry_id},
            ))
        required = 0
        for event_type, payload in transitions:
            _event, encoded = self._prepare_journal_frame(
                simulated,
                event_type,
                payload,
                node_id=obligation.node_id,
                attempt_id=obligation.attempt_id,
                sample=pessimistic_sample,
                timestamp=timestamp,
            )
            # The frame is a durable addition. Atomic run.json replacement
            # temporarily retains the current projection beside the full new
            # document; summing both transition peaks is deliberately
            # pessimistic and leaves room for platform JSON-number variance.
            required += len(encoded) + len(_json_document_bytes(simulated)) + 2_048
        return required

    def _append_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        compact_recovery: bool = False,
        defer_notification: bool = False,
        terminal_reserve_attempt_id: str | None = None,
        reserve_connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        event, encoded = self._prepare_journal_frame(
            projection,
            event_type,
            payload,
            node_id=node_id,
            attempt_id=attempt_id,
            compact_recovery=compact_recovery,
        )
        journal_path = directory / "events.jsonl"
        if journal_path.stat().st_size and not _file_ends_with_newline(journal_path):
            self._read_journal_events(directory)
        self._check_journal_reserve(
            run_id=str(projection["run_id"]),
            projection=projection,
            journal_bytes=journal_path.stat().st_size,
            frame_bytes=len(encoded),
            terminal_attempt_id=terminal_reserve_attempt_id,
            connection=reserve_connection,
        )
        with journal_path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if terminal_reserve_attempt_id is not None:
            self._consume_journal_reserve(
                terminal_reserve_attempt_id,
                len(encoded),
                connection=reserve_connection,
            )
        _atomic_json(directory / "run.json", projection)
        if defer_notification:
            return event
        # SQLite and the filesystem journal cannot share one transaction. The
        # journal is the transition authority; this idempotent outbox write is
        # reconciled by the coordinator after a crash between the two writes.
        try:
            from plugins.workflow.notifications import (
                NotificationOutbox,
                notification_kind,
            )

            kind = notification_kind(event_type, projection)
            if kind is not None:
                pending = next(
                    (
                        node.get("pending_interaction")
                        for node in projection.get("nodes", {}).values()
                        if isinstance(node, Mapping)
                        and isinstance(node.get("pending_interaction"), Mapping)
                    ),
                    None,
                )
                NotificationOutbox(self).record(
                    run_id=str(projection["run_id"]),
                    kind=kind,
                    destination="desktop",
                    transition_version=int(projection["state_version"]),
                    payload={
                        "workflow": projection.get("workflow"),
                        "status": projection.get("status"),
                        "event_type": event_type,
                        "node_id": node_id,
                        "interaction": pending,
                        "last_error": projection.get("last_error"),
                    },
                    delivery_state=(
                        "pending"
                        if projection.get("execution_mode") == "background"
                        else "suppressed"
                    ),
                    now=datetime.fromisoformat(str(event["timestamp"])),
                )
        except sqlite3.Error:
            pass
        return event

    def transition_pending_nodes(
        self,
        run_id: str,
        transitions: Mapping[str, tuple[str, str | None]],
    ) -> tuple[str, ...]:
        """Compare-and-set dependency-resolved nodes to ready or skipped."""
        directory = self.run_directory(run_id)
        changed = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return ()
            for node_id, (state, warning) in transitions.items():
                if state not in {"ready", "skipped"}:
                    raise ValueError(f"invalid dependency transition: {state}")
                node = projection["nodes"].get(node_id)
                if not node or node["state"] != "pending":
                    continue
                node["state"] = state
                if warning:
                    node["skip_reason"] = warning
                    if warning not in projection["warnings"]:
                        projection["warnings"].append(warning)
                self._append_locked(
                    directory,
                    projection,
                    f"node_{state}",
                    {"reason": warning} if warning else None,
                    node_id=node_id,
                )
                changed.append(node_id)
        return tuple(changed)

    def transition_v3_condition_node(
        self,
        run_id: str,
        node_id: str,
        *,
        state: str,
        code: str,
        message: str,
    ) -> bool:
        """CAS one Archon v3 condition outcome before any executor claim."""
        if state not in {"skipped", "failed"}:
            raise ValueError("v3 condition state must be skipped or failed")
        if state == "skipped" and code != "condition_false":
            raise ValueError("v3 skipped condition must use condition_false")
        try:
            code_size = len(code.encode("utf-8")) if isinstance(code, str) else 0
        except UnicodeEncodeError as exc:
            raise ValueError("v3 condition code must be valid UTF-8") from exc
        if not isinstance(code, str) or not code or code_size > 128:
            raise ValueError("v3 condition diagnostics must be bounded text")
        if not isinstance(message, str) or not message:
            raise ValueError("v3 condition message must be bounded text")
        safe_message = _sanitize_v3_condition_diagnostic(message)

        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            language = projection.get("language")
            if (
                not isinstance(language, Mapping)
                or language.get("effective_profile") != "archon-2026-07"
                or language.get("normalizer_version") != 3
            ):
                raise ValueError("v3 condition transition requires an Archon v3 run")
            if projection.get("status") != "running":
                return False
            node = projection.get("nodes", {}).get(node_id)
            if not isinstance(node, dict) or node.get("state") != "pending":
                return False
            if node.get("attempts") or int(node.get("retry_consumed", 0)) != 0:
                raise RuntimeError("pending condition node already consumed an attempt")

            node["state"] = state
            node["retry_consumed"] = 0
            payload: dict[str, object]
            if state == "skipped":
                node["skip_reason"] = code
                payload = {"reason": code}
                if code not in projection["warnings"]:
                    projection["warnings"].append(code)
            else:
                projection["last_error"] = {
                    "code": code,
                    "message": safe_message,
                    "node_id": node_id,
                }
                payload = {"error_code": code, "error_message": safe_message}
            self._append_locked(
                directory,
                projection,
                f"node_{state}",
                payload,
                node_id=node_id,
            )
            return True

    def transition_v3_reference_node(
        self,
        run_id: str,
        node_id: str,
        *,
        code: str,
        message: str,
    ) -> bool:
        """CAS one non-transient v3 reference failure before executor claim."""
        if (
            not isinstance(code, str)
            or not code.startswith("output_reference_")
            or code == "output_reference_temporarily_unavailable"
            or len(code.encode("utf-8")) > 128
        ):
            raise ValueError("v3 reference failure code is invalid")
        if not isinstance(message, str) or not message:
            raise ValueError("v3 reference failure message is invalid")
        safe_message = _sanitize_v3_condition_diagnostic(message)
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            language = projection.get("language")
            if (
                not isinstance(language, Mapping)
                or language.get("effective_profile") != "archon-2026-07"
                or language.get("normalizer_version") != 3
            ):
                raise ValueError("v3 reference transition requires an Archon v3 run")
            if projection.get("status") != "running":
                return False
            node = projection.get("nodes", {}).get(node_id)
            if not isinstance(node, dict) or node.get("state") not in {
                "pending",
                "ready",
            }:
                return False
            if node.get("attempts") or int(node.get("retry_consumed", 0)) != 0:
                raise RuntimeError("reference failure already consumed an attempt")
            self._clear_output_resolution_fields(node)
            node["state"] = "failed"
            node["retry_consumed"] = 0
            projection["last_error"] = {
                "code": code,
                "message": safe_message,
                "node_id": node_id,
            }
            self._append_locked(
                directory,
                projection,
                "node_failed",
                {"error_code": code, "error_message": safe_message},
                node_id=node_id,
            )
            return True

    @staticmethod
    def _clear_output_resolution_fields(node: dict[str, object]) -> None:
        for field in (
            "next_resolution_at",
            "resolution_producer_identity",
            "resolution_read_count",
            "resolution_resume_state",
        ):
            node.pop(field, None)

    def _fail_output_resolution_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        node_id: str,
        node: dict[str, object],
        *,
        code: str,
        message: str,
        read_count: int,
    ) -> None:
        node["state"] = "failed"
        node["resolution_read_count"] = read_count
        node.pop("next_resolution_at", None)
        node.pop("resolution_resume_state", None)
        node["retry_consumed"] = 0
        projection["last_error"] = {
            "code": code,
            "message": message,
            "node_id": node_id,
        }
        self._append_locked(
            directory,
            projection,
            "node_failed",
            {"error_code": code, "error_message": message},
            node_id=node_id,
        )

    def defer_output_resolution(
        self,
        run_id: str,
        node_id: str,
        *,
        producer_identity: Mapping[str, object],
        now: datetime | None = None,
    ) -> bool:
        """CAS one transient v3 read into the bounded durable wait protocol."""
        identity = canonical_output_publication_identity(producer_identity)
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("resolution observation time must be timezone-aware")
        instant = instant.astimezone(timezone.utc)
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            language = projection.get("language")
            if (
                not isinstance(language, Mapping)
                or language.get("effective_profile") != "archon-2026-07"
                or language.get("normalizer_version") != 3
            ):
                raise ValueError("output resolution waits require an Archon v3 run")
            if projection.get("status") != "running":
                return False
            node = projection.get("nodes", {}).get(node_id)
            if not isinstance(node, dict) or node.get("state") not in {
                "pending",
                "ready",
            }:
                return False
            if node.get("attempts") or int(node.get("retry_consumed", 0)) != 0:
                raise RuntimeError("output resolution wait consumed an executor attempt")

            retained_identity = node.get("resolution_producer_identity")
            read_count = int(node.get("resolution_read_count", 0)) + 1
            if retained_identity is not None and retained_identity != identity:
                self._fail_output_resolution_locked(
                    directory,
                    projection,
                    node_id,
                    node,
                    code="output_reference_integrity",
                    message="output reference producer identity changed during resolution",
                    read_count=read_count,
                )
                return True

            node["resolution_producer_identity"] = identity
            if read_count >= 6:
                self._fail_output_resolution_locked(
                    directory,
                    projection,
                    node_id,
                    node,
                    code="output_reference_unavailable",
                    message="output reference remained unavailable after 6 reads",
                    read_count=read_count,
                )
                return True

            delay_seconds = (0.25, 0.5, 1.0, 2.0, 4.0)[read_count - 1]
            next_resolution_at = instant + timedelta(seconds=delay_seconds)
            node["resolution_read_count"] = read_count
            node["resolution_resume_state"] = str(node["state"])
            node["state"] = "waiting_resolution"
            node["next_resolution_at"] = next_resolution_at.isoformat()
            node["retry_consumed"] = 0
            self._append_locked(
                directory,
                projection,
                "output_resolution_deferred",
                {
                    "error_code": "output_reference_temporarily_unavailable",
                    "next_resolution_at": next_resolution_at.isoformat(),
                    "producer_identity_sha256": output_publication_identity_sha256(
                        identity
                    ),
                    "resolution_read_count": read_count,
                },
                node_id=node_id,
            )
            return True

    def wake_due_output_resolutions(
        self, run_id: str, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        """Wake each due resolution waiter exactly once under the run CAS lock."""
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("resolution wake time must be timezone-aware")
        instant = instant.astimezone(timezone.utc)
        directory = self.run_directory(run_id)
        ready: list[str] = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("status") != "running":
                return ()
            for candidate_id, node in projection.get("nodes", {}).items():
                if (
                    not isinstance(node, dict)
                    or node.get("state") != "waiting_resolution"
                    or not isinstance(node.get("next_resolution_at"), str)
                    or datetime.fromisoformat(node["next_resolution_at"]) > instant
                ):
                    continue
                resume_state = node.pop("resolution_resume_state", None)
                if resume_state not in {"pending", "ready"}:
                    raise RuntimeError("resolution wait resume state is invalid")
                node["state"] = resume_state
                node.pop("next_resolution_at", None)
                self._append_locked(
                    directory,
                    projection,
                    "output_resolution_ready",
                    {"resolution_read_count": node["resolution_read_count"]},
                    node_id=str(candidate_id),
                )
                ready.append(str(candidate_id))
        return tuple(ready)

    def clear_output_resolution(
        self,
        run_id: str,
        node_id: str,
        *,
        producer_identity: Mapping[str, object],
    ) -> bool:
        """Clear one matching durable wait after a successful strict read."""
        identity = canonical_output_publication_identity(producer_identity)
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("status") != "running":
                return False
            node = projection.get("nodes", {}).get(node_id)
            if (
                not isinstance(node, dict)
                or node.get("state") not in {"pending", "ready"}
                or "resolution_read_count" not in node
            ):
                return False
            if node.get("resolution_producer_identity") != identity:
                self._fail_output_resolution_locked(
                    directory,
                    projection,
                    node_id,
                    node,
                    code="output_reference_integrity",
                    message="output reference producer identity changed during resolution",
                    read_count=int(node.get("resolution_read_count", 0)) + 1,
                )
                return True
            read_count = int(node["resolution_read_count"])
            self._clear_output_resolution_fields(node)
            self._append_locked(
                directory,
                projection,
                "output_resolution_cleared",
                {"resolution_read_count": read_count},
                node_id=node_id,
            )
            return True

    def finalize_if_complete(self, run_id: str) -> bool:
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return False
            if _pending_session_registry_payloads(projection):
                return False
            states = {node["state"] for node in projection["nodes"].values()}
            if states - {
                "succeeded",
                "failed",
                "skipped",
                "cancelled",
                "interrupted",
            }:
                return False
            target = "failed" if "failed" in states else "succeeded"
            projection["status"] = target
            self._append_locked(directory, projection, f"run_{target}")
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._record_coordinator_wake(
                    connection, run_id=run_id, reason_code=f"run_{target}"
                )
            self._notify_coordinator()
            return True

    def schedule_retry(
        self,
        claim: NodeClaim,
        *,
        next_attempt_at: datetime,
        artifacts: Iterable[ArtifactRef] = (),
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
        consumed_attempts: int = 1,
        now: LeaseClockSample | None = None,
    ) -> None:
        if next_attempt_at.tzinfo is None:
            raise ValueError("next_attempt_at must be timezone-aware")
        directory = self.run_directory(claim.run_id)
        with (
            workflow_lock(self.admission_lock),
            workflow_lock(self._run_lock_path(claim.run_id)),
            self._execution_fence_transaction(
                claim.execution_fence, now
            ) as fence_connection,
        ):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            if node.get("claim", {}).get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale node completion")
            if projection["status"] in {"cancelled", "abandoned", "interrupted"}:
                raise RuntimeError("stale completion for terminal run")
            node.pop("claim", None)
            node["state"] = "waiting_retry"
            node["next_attempt_at"] = next_attempt_at.isoformat()
            node["retry_consumed"] = consumed_attempts
            safe_error_message = _sanitize_diagnostic(error_message)
            node["attempts"][-1].update({
                "state": "failed",
                "error_code": error_code,
                "error_message": safe_error_message,
                "metadata": _sanitize(dict(metadata or {})),
            })
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                projection["artifacts"].append(entry)
            active_states = {
                candidate["state"] for candidate in projection["nodes"].values()
            }
            projection["status"] = (
                "running"
                if active_states & {"ready", "claimed", "running"}
                else "waiting_retry"
            )
            self._append_locked(
                directory,
                projection,
                "node_retry_scheduled",
                {
                    "next_attempt_at": next_attempt_at.isoformat(),
                    "error_code": error_code,
                    "artifacts": refs,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
                terminal_reserve_attempt_id=claim.attempt_id,
                reserve_connection=fence_connection,
            )
            with (
                nullcontext(fence_connection)
                if fence_connection is not None
                else self._connect()
            ) as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], claim.run_id),
                )
                self._record_coordinator_wake(
                    connection,
                    run_id=claim.run_id,
                    reason_code="retry_scheduled",
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._release_worker_claim(
                    claim.attempt_id, connection=connection
                )
            self._notify_coordinator()

    def wake_due_retries(
        self, run_id: str, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        instant = now or datetime.now(timezone.utc)
        directory = self.run_directory(run_id)
        ready = []
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] not in {"waiting_retry", "running"}:
                return ()
            due_nodes = [
                node
                for node in projection["nodes"].values()
                if node["state"] == "waiting_retry"
                and datetime.fromisoformat(node["next_attempt_at"]) <= instant
            ]
            if not due_nodes:
                return ()
            if projection["status"] == "waiting_retry":
                projection = self._request_runnable_locked(
                    directory,
                    projection,
                    reason="retry_due",
                )
                if projection["status"] == "queued":
                    return ()
            for node_id, node in projection["nodes"].items():
                if node["state"] != "waiting_retry":
                    continue
                due = datetime.fromisoformat(node["next_attempt_at"])
                if due > instant:
                    continue
                node["state"] = "ready"
                node.pop("next_attempt_at", None)
                self._append_locked(
                    directory,
                    projection,
                    "node_retry_ready",
                    node_id=node_id,
                )
                ready.append(node_id)
            if ready:
                self._append_locked(directory, projection, "run_retry_resumed")
                with self._connect() as connection:
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    self._record_coordinator_wake(
                        connection, run_id=run_id, reason_code="retry_due"
                    )
                self._notify_coordinator()
        return tuple(ready)

    def renew_claim(
        self,
        claim: NodeClaim,
        *,
        now: datetime | None = None,
        monotonic_now: float | None = None,
        lease_seconds: float = 30.0,
        heartbeat_interval_seconds: float = 5.0,
        fence_now: LeaseClockSample | None = None,
    ) -> bool:
        if lease_seconds <= 0 or heartbeat_interval_seconds <= 0:
            raise ValueError("lease and heartbeat intervals must be positive")
        try:
            self._assert_claim_execution_fence(claim, fence_now)
        except RuntimeError:
            return False
        instant = now or datetime.now(timezone.utc)
        directory = self.run_directory(claim.run_id)
        try:
            with workflow_lock(
                self._run_lock_path(claim.run_id)
            ), self._execution_fence_transaction(
                claim.execution_fence, fence_now
            ) as fence_connection:
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"].get(claim.node_id)
                active = node.get("claim", {}) if node else {}
                if active.get("attempt_id") != claim.attempt_id:
                    return False
                heartbeat_at = datetime.fromisoformat(active["heartbeat_at"])
                utc_elapsed = (instant - heartbeat_at).total_seconds()
                monotonic_instant = (
                    float(monotonic_now)
                    if monotonic_now is not None
                    else time.monotonic()
                )
                previous_monotonic = active.get("heartbeat_monotonic")
                monotonic_elapsed = (
                    monotonic_instant - float(previous_monotonic)
                    if isinstance(previous_monotonic, int | float)
                    else utc_elapsed
                )
                active_lease_seconds = float(
                    active.get("lease_seconds", lease_seconds)
                )
                if (
                    utc_elapsed < 0
                    or monotonic_elapsed < 0
                    or abs(utc_elapsed - monotonic_elapsed) > active_lease_seconds
                    or monotonic_elapsed >= active_lease_seconds
                    or datetime.fromisoformat(active["lease_expires_at"]) <= instant
                ):
                    return False
                if utc_elapsed < heartbeat_interval_seconds:
                    return True
                active["heartbeat_at"] = instant.isoformat()
                active["heartbeat_monotonic"] = monotonic_instant
                active["lease_seconds"] = float(lease_seconds)
                active["lease_expires_at"] = (
                    instant + timedelta(seconds=lease_seconds)
                ).isoformat()
                self._append_locked(
                    directory,
                    projection,
                    "node_heartbeat",
                    {
                        "heartbeat_at": active["heartbeat_at"],
                        "heartbeat_monotonic": active["heartbeat_monotonic"],
                        "lease_expires_at": active["lease_expires_at"],
                        "lease_seconds": active["lease_seconds"],
                    },
                    node_id=claim.node_id,
                    attempt_id=claim.attempt_id,
                    compact_recovery=True,
                    defer_notification=fence_connection is not None,
                )
                if fence_connection is not None:
                    fence_connection.execute(
                        "UPDATE worker_claims SET lease_expires_at=? "
                        "WHERE attempt_id=?",
                        (active["lease_expires_at"], claim.attempt_id),
                    )
                else:
                    with self._connect() as connection:
                        connection.execute(
                            "UPDATE worker_claims SET lease_expires_at=? "
                            "WHERE attempt_id=?",
                            (active["lease_expires_at"], claim.attempt_id),
                        )
                return True
        except RuntimeError as exc:
            if "execution fence" in str(exc):
                return False
            raise

    def expire_stale_claims(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        monotonic_now: float | None = None,
        current_owner_epoch: str | None = None,
    ) -> tuple[str, ...]:
        instant = now or datetime.now(timezone.utc)
        monotonic_instant = (
            float(monotonic_now) if monotonic_now is not None else time.monotonic()
        )
        directory = self.run_directory(run_id)
        expired = []
        releasable_attempts: list[str] = []
        reconciliation_required = False
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("desired_status") == "cleanup_failed":
                return ()
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                return ()
            for node_id, node in projection["nodes"].items():
                claim = node.get("claim")
                if not claim:
                    continue
                lease_seconds = float(claim.get("lease_seconds", 30.0))
                heartbeat_utc = datetime.fromisoformat(claim["heartbeat_at"])
                utc_elapsed = (instant - heartbeat_utc).total_seconds()
                heartbeat_monotonic = claim.get("heartbeat_monotonic")
                monotonic_elapsed = (
                    monotonic_instant - float(heartbeat_monotonic)
                    if isinstance(heartbeat_monotonic, int | float)
                    else utc_elapsed
                )
                clock_gap = abs(utc_elapsed - monotonic_elapsed) > lease_seconds
                if (
                    datetime.fromisoformat(claim["lease_expires_at"]) > instant
                    and monotonic_elapsed < lease_seconds
                    and not clock_gap
                ):
                    continue
                attempt_id = claim["attempt_id"]
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict):
                    attempt = {"attempt_id": attempt_id, "state": "claimed"}
                    node.setdefault("attempts", []).append(attempt)
                effect_classification = str(
                    attempt.get(
                        "effect_classification",
                        claim.get("effect_classification", "replay_safe"),
                    )
                )
                serialized = attempt.get("process_identity")
                observation = self._observe_attempt(attempt)
                if self._reclaim_still_running_claim(
                    directory,
                    projection,
                    node_id=node_id,
                    node=node,
                    claim=claim,
                    attempt=attempt,
                    observation=observation,
                    current_owner_epoch=current_owner_epoch,
                    instant=instant,
                    monotonic_instant=monotonic_instant,
                    lease_seconds=lease_seconds,
                ):
                    continue
                node.pop("claim", None)
                recovery = {
                    "attempt_id": attempt_id,
                    "owner_id": attempt.get("owner_id", claim.get("owner_id")),
                    "owner_epoch": attempt.get(
                        "owner_epoch", claim.get("owner_epoch")
                    ),
                    "executor_id": attempt.get(
                        "executor_id", claim.get("executor_id", "unknown")
                    ),
                    "effect_classification": effect_classification,
                    "process_identity": serialized,
                    "observation": observation,
                    "lease_expired_at": instant.isoformat(),
                    "termination_confirmed": observation in {
                        "known_stopped",
                        "not_started",
                    },
                }
                node["recovery"] = recovery
                requires_reconcile = effect_classification == "outward" or (
                    observation == "outcome_uncertain"
                )
                if requires_reconcile:
                    interaction_id = f"reconcile-{attempt_id}"
                    node["state"] = "paused"
                    node["pending_interaction"] = {
                        "type": "reconcile",
                        "interaction_id": interaction_id,
                        "attempt_id": attempt_id,
                        "reason_code": "lease_expired_outcome_uncertain",
                    }
                    attempt.update({
                        "state": "paused",
                        "error_code": "reconciliation_required",
                    })
                    reconciliation_required = True
                    event_type = "node_reconciliation_required"
                else:
                    node["state"] = "interrupted"
                    attempt.update({
                        "state": "interrupted",
                        "error_code": (
                            "executor_still_running"
                            if observation == "still_running"
                            else "lease_expired"
                        ),
                    })
                    event_type = "node_interrupted"
                self._append_locked(
                    directory,
                    projection,
                    event_type,
                    {
                        "reason": "lease_expired",
                        "observation": observation,
                        "effect_classification": effect_classification,
                    },
                    node_id=node_id,
                    attempt_id=attempt_id,
                    terminal_reserve_attempt_id=attempt_id,
                )
                if observation in {"known_stopped", "not_started"}:
                    releasable_attempts.append(attempt_id)
                expired.append(node_id)
            if expired:
                projection["status"] = (
                    "paused" if reconciliation_required else "interrupted"
                )
                self._append_locked(
                    directory,
                    projection,
                    (
                        "run_reconciliation_required"
                        if reconciliation_required
                        else "run_interrupted"
                    ),
                    terminal_reserve_attempt_id=(
                        releasable_attempts[0]
                        if releasable_attempts
                        else str(
                            projection["nodes"][expired[0]]["recovery"]["attempt_id"]
                        )
                    ),
                )
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (projection["status"], projection["updated_at"], run_id),
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    for attempt_id in releasable_attempts:
                        self._release_worker_claim(
                            attempt_id, connection=connection
                        )
        return tuple(expired)

    def _reclaim_still_running_claim(
        self,
        directory: Path,
        projection: dict[str, object],
        *,
        node_id: str,
        node: dict[str, object],
        claim: dict[str, object],
        attempt: dict[str, object],
        observation: str,
        current_owner_epoch: str | None,
        instant: datetime,
        monotonic_instant: float,
        lease_seconds: float,
    ) -> bool:
        """Re-adopt one live attempt without changing its immutable identity."""
        if (
            observation != "still_running"
            or not current_owner_epoch
            or claim.get("owner_epoch") != current_owner_epoch
            or attempt.get("owner_epoch") != current_owner_epoch
            or not node.get("attempts")
            or node["attempts"][-1] is not attempt
            or attempt.get("state") not in {"claimed", "running"}
        ):
            return False
        serialized = attempt.get("process_identity")
        if not isinstance(serialized, Mapping) or serialized.get("start_time") is None:
            return False
        prefix = "coordinator:"
        if not current_owner_epoch.startswith(prefix):
            return False
        try:
            coordinator_owner, epoch_text = current_owner_epoch[len(prefix) :].rsplit(
                ":", 1
            )
            coordinator_epoch = int(epoch_text)
        except (TypeError, ValueError):
            return False
        attempt_id = str(claim.get("attempt_id") or "")
        if not attempt_id:
            return False
        expires_at = (instant + timedelta(seconds=lease_seconds)).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            leader = self._fresh_coordinator_lease(connection)
            worker = connection.execute(
                "SELECT 1 FROM worker_claims WHERE attempt_id=? AND run_id=? "
                "AND node_id=? AND owner_id=?",
                (attempt_id, projection["run_id"], node_id, claim.get("owner_id")),
            ).fetchone()
            if (
                leader is None
                or leader.owner_id != coordinator_owner
                or leader.epoch != coordinator_epoch
                or worker is None
            ):
                connection.rollback()
                return False
            claim.update({
                "heartbeat_at": instant.isoformat(),
                "heartbeat_monotonic": monotonic_instant,
                "lease_expires_at": expires_at,
            })
            attempt["reclaimed_at"] = instant.isoformat()
            attempt["reclaim_count"] = int(attempt.get("reclaim_count", 0)) + 1
            self._append_locked(
                directory,
                projection,
                "node_reclaimed",
                {
                    "owner_epoch": current_owner_epoch,
                    "lease_expires_at": expires_at,
                },
                node_id=node_id,
                attempt_id=attempt_id,
            )
            connection.execute(
                "UPDATE worker_claims SET lease_expires_at=? WHERE attempt_id=?",
                (expires_at, attempt_id),
            )
            connection.commit()
            return True
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _observe_process_identity(serialized: object) -> str:
        if not isinstance(serialized, Mapping):
            return "not_started"
        try:
            identity = ProcessIdentity(
                pid=int(serialized["pid"]),
                start_time=(
                    int(serialized["start_time"])
                    if serialized.get("start_time") is not None
                    else None
                ),
                group_id=(
                    int(serialized["group_id"])
                    if serialized.get("group_id") is not None
                    else None
                ),
                job_name=(
                    str(serialized["job_name"])
                    if serialized.get("job_name")
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return "outcome_uncertain"
        if identity.job_name:
            active = ManagedProcessTree.existing_tree_active(identity)
            if active is True:
                return "still_running"
            if active is False:
                return "known_stopped"
            return "outcome_uncertain"
        if os.name == "nt":
            return "outcome_uncertain"
        try:
            if identity.is_current():
                return (
                    "still_running"
                    if identity.start_time is not None
                    else "outcome_uncertain"
                )
            from gateway.status import _pid_exists

            return (
                "outcome_uncertain"
                if _pid_exists(identity.pid)
                else "known_stopped"
            )
        except Exception:
            return "outcome_uncertain"

    @classmethod
    def _observe_attempt(cls, attempt: Mapping[str, object]) -> str:
        serialized = attempt.get("process_identity")
        if isinstance(serialized, Mapping):
            # Reaping an AI worker proves only that its process stopped. The
            # provider may already have accepted work whose result was never
            # journaled, so that cut must remain outcome-uncertain.
            provider_worker_started = attempt.get("executor_id") in {
                "agent",
                "command",
                "prompt",
                "loop",
                "approval",
            }
            process_stop = attempt.get("process_stop")
            provider_dispatch = attempt.get("provider_dispatch")
            provider_work_possible = bool(
                provider_worker_started
                and isinstance(provider_dispatch, Mapping)
                and provider_dispatch.get("state") == "released"
            )
            if (
                not provider_work_possible
                and isinstance(process_stop, Mapping)
                and process_stop.get("identity_matched") is True
                and process_stop.get("cleaned") is True
            ):
                return "known_stopped"
            observation = cls._observe_process_identity(serialized)
            if not provider_work_possible:
                return observation
            return (
                "still_running"
                if observation == "still_running"
                else "outcome_uncertain"
            )
        spawn = attempt.get("spawn")
        if not isinstance(spawn, Mapping):
            return "not_started"
        if spawn.get("state") in {"intent", "failed"}:
            if (
                spawn.get("state") == "intent"
                and attempt.get("effect_classification") == "outward"
            ):
                return "outcome_uncertain"
            return "not_started"
        return "outcome_uncertain"

    def interrupt_active_claims(
        self,
        run_id: str,
        *,
        reason: str,
        lock_timeout_seconds: float = 5.0,
        fence: ExecutionFence | None = None,
        now: LeaseClockSample | None = None,
    ) -> tuple[str, ...]:
        if fence is not None:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self.assert_execution_fence(connection, fence, now)
                except RuntimeError:
                    connection.rollback()
                    return ()
                connection.commit()
        directory = self.run_directory(run_id)
        interrupted = []
        releasable_attempts: list[str] = []
        with (
            workflow_lock(self.admission_lock),
            workflow_lock(
                self._run_lock_path(run_id), timeout_seconds=lock_timeout_seconds
            ),
            self._execution_fence_transaction(fence, now) as fence_connection,
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("desired_status") == "cleanup_failed":
                return ()
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
                "paused",
            }:
                return ()
            reconciliation_required = False
            for node_id, node in projection["nodes"].items():
                claim = node.get("claim")
                if not claim:
                    continue
                claim_fence = claim.get("execution_fence")
                if fence is not None and claim_fence != {
                    "owner_id": fence.owner_id,
                    "owner_epoch": fence.owner_epoch,
                }:
                    continue
                node.pop("claim", None)
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == claim["attempt_id"]
                    ),
                    node["attempts"][-1],
                )
                observation = self._observe_attempt(attempt)
                effect_classification = str(
                    attempt.get("effect_classification", "replay_safe")
                )
                process_stop = attempt.get("process_stop")
                termination_confirmed = observation == "not_started" or bool(
                    isinstance(process_stop, Mapping)
                    and process_stop.get("identity_matched") is True
                    and process_stop.get("cleaned") is True
                )
                node["recovery"] = {
                    "attempt_id": claim["attempt_id"],
                    "owner_id": attempt.get("owner_id", claim.get("owner_id")),
                    "owner_epoch": attempt.get(
                        "owner_epoch", claim.get("owner_epoch")
                    ),
                    "executor_id": attempt.get("executor_id", "unknown"),
                    "effect_classification": effect_classification,
                    "process_identity": attempt.get("process_identity"),
                    "observation": observation,
                    "interrupted_at": _utc_now(),
                    "termination_confirmed": termination_confirmed,
                }
                requires_reconcile = effect_classification == "outward" or (
                    observation == "outcome_uncertain"
                )
                if requires_reconcile:
                    node["state"] = "paused"
                    node["pending_interaction"] = {
                        "type": "reconcile",
                        "interaction_id": f"reconcile-{claim['attempt_id']}",
                        "attempt_id": claim["attempt_id"],
                        "reason_code": f"{reason}_outcome_uncertain",
                    }
                    attempt.update({
                        "state": "paused",
                        "error_code": "reconciliation_required",
                    })
                    reconciliation_required = True
                else:
                    node["state"] = "interrupted"
                    attempt.update({
                        "state": "interrupted",
                        "error_code": reason,
                    })
                self._append_locked(
                    directory,
                    projection,
                    (
                        "node_reconciliation_required"
                        if requires_reconcile
                        else "node_interrupted"
                    ),
                    {"reason": reason, "observation": observation},
                    node_id=node_id,
                    attempt_id=claim["attempt_id"],
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=claim["attempt_id"],
                    reserve_connection=fence_connection,
                )
                if observation in {"known_stopped", "not_started"}:
                    releasable_attempts.append(str(claim["attempt_id"]))
                interrupted.append(node_id)
            if interrupted:
                projection["status"] = (
                    "paused" if reconciliation_required else "interrupted"
                )
                self._append_locked(
                    directory,
                    projection,
                    (
                        "run_reconciliation_required"
                        if reconciliation_required
                        else "run_interrupted"
                    ),
                    defer_notification=fence_connection is not None,
                    terminal_reserve_attempt_id=str(
                        projection["nodes"][interrupted[0]]["recovery"]["attempt_id"]
                    ),
                    reserve_connection=fence_connection,
                )
                with (
                    nullcontext(fence_connection)
                    if fence_connection is not None
                    else self._connect()
                ) as connection:
                    connection.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (projection["status"], projection["updated_at"], run_id),
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    for attempt_id in releasable_attempts:
                        self._release_worker_claim(
                            attempt_id, connection=connection
                        )
        return tuple(interrupted)

    def record_cleanup_failed(self, run_id: str, *, reason: str) -> None:
        with self._connect() as connection:
            self._record_admission_event(
                connection,
                "cleanup_failed",
                run_id=run_id,
                reason_code=reason,
            )

    def interrupt_for_host_pressure(self, run_id: str, *, message: str) -> None:
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return
            projection["status"] = "interrupted"
            projection["last_error"] = {
                "code": "host_pressure",
                "message": _sanitize_diagnostic(message),
            }
            self._append_locked(directory, projection, "host_pressure_refused")
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._record_coordinator_wake(
                    connection,
                    run_id=run_id,
                    reason_code="host_pressure_refused",
                )
            self._notify_coordinator()

    def release_or_expire_claim(self, claim: NodeClaim) -> bool:
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(claim.run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"].get(claim.node_id)
            if not node or node.get("claim", {}).get("attempt_id") != claim.attempt_id:
                return False
            active = node["claim"]
            attempt = next(
                (
                    candidate
                    for candidate in reversed(node.get("attempts", []))
                    if candidate.get("attempt_id") == claim.attempt_id
                ),
                node["attempts"][-1],
            )
            observation = self._observe_attempt(attempt)
            effect_classification = str(
                attempt.get("effect_classification", "replay_safe")
            )
            process_stop = attempt.get("process_stop")
            termination_confirmed = observation == "not_started" or bool(
                isinstance(process_stop, Mapping)
                and process_stop.get("identity_matched") is True
                and process_stop.get("cleaned") is True
            )
            node.pop("claim", None)
            node["recovery"] = {
                "attempt_id": claim.attempt_id,
                "owner_id": attempt.get("owner_id", active.get("owner_id")),
                "owner_epoch": attempt.get(
                    "owner_epoch", active.get("owner_epoch")
                ),
                "executor_id": attempt.get("executor_id", "unknown"),
                "effect_classification": effect_classification,
                "process_identity": attempt.get("process_identity"),
                "observation": observation,
                "interrupted_at": _utc_now(),
                "termination_confirmed": termination_confirmed,
            }
            requires_reconcile = effect_classification == "outward" or (
                observation == "outcome_uncertain"
            )
            if requires_reconcile:
                node["state"] = "paused"
                node["pending_interaction"] = {
                    "type": "reconcile",
                    "interaction_id": f"reconcile-{claim.attempt_id}",
                    "attempt_id": claim.attempt_id,
                    "reason_code": "claim_released_outcome_uncertain",
                }
                attempt.update({
                    "state": "paused",
                    "error_code": "reconciliation_required",
                })
                projection["status"] = "paused"
                event_type = "node_reconciliation_required"
            else:
                node["state"] = "interrupted"
                attempt.update({
                    "state": "interrupted",
                    "error_code": "claim_released",
                })
                projection["status"] = "interrupted"
                event_type = "node_interrupted"
            self._append_locked(
                directory,
                projection,
                event_type,
                {
                    "reason": "claim_released",
                    "observation": observation,
                    "effect_classification": effect_classification,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                terminal_reserve_attempt_id=claim.attempt_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], claim.run_id),
                )
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                if observation in {"known_stopped", "not_started"}:
                    self._release_worker_claim(
                        claim.attempt_id, connection=connection
                    )
            return True

    def cancel_run(
        self,
        run_id: str,
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        recorded: list[tuple[str, str, ProcessIdentity]] = []
        outward_attempts: set[tuple[str, str]] = set()
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise WorkflowConflict("stale cancellation decision")
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                return {**projection, "cancellation_outcome": "already_terminal"}
            if any(
                node.get("pending_interaction") == "reconcile"
                or (
                    isinstance(node.get("pending_interaction"), dict)
                    and node["pending_interaction"].get("type") == "reconcile"
                )
                for node in projection["nodes"].values()
            ):
                self._append_locked(
                    directory,
                    projection,
                    "cancel_reconciliation_required",
                    {"reason_code": "outcome_unknown"},
                )
                return {
                    **projection,
                    "cancellation_outcome": "reconciliation_required",
                }
            if projection.get("desired_status") != "cancelled":
                projection["desired_status"] = "cancelled"
                self._append_locked(directory, projection, "cancel_requested")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET desired_status='cancelled', updated_at=? "
                        "WHERE run_id=?",
                        (projection["updated_at"], run_id),
                    )
                    self._record_coordinator_wake(
                        connection, run_id=run_id, reason_code="cancel_requested"
                    )
                self._notify_coordinator()
            for node_id, node in projection["nodes"].items():
                claim = node.get("claim")
                recovery = node.get("recovery")
                ownership = claim if isinstance(claim, dict) else recovery
                if not isinstance(ownership, dict):
                    continue
                attempt_id = str(ownership.get("attempt_id") or "")
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == attempt_id
                    ),
                    {},
                )
                serialized = ownership.get("process_identity") or attempt.get(
                    "process_identity"
                )
                if isinstance(attempt, Mapping) and attempt.get(
                    "effect_classification"
                ) == "outward" and (
                    attempt.get("state") == "running"
                    or isinstance(serialized, Mapping)
                ):
                    outward_attempts.add((node_id, attempt_id))
                if not isinstance(serialized, dict):
                    continue
                try:
                    identity = ProcessIdentity(
                        pid=int(serialized["pid"]),
                        start_time=(
                            int(serialized["start_time"])
                            if serialized.get("start_time") is not None
                            else None
                        ),
                        group_id=(
                            int(serialized["group_id"])
                            if serialized.get("group_id") is not None
                            else None
                        ),
                        job_name=(
                            str(serialized["job_name"])
                            if serialized.get("job_name")
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                recorded.append((node_id, attempt_id, identity))

        cleanup: list[tuple[str, str, ProcessIdentity, bool]] = []
        for node_id, attempt_id, identity in recorded:
            terminated = ManagedProcessTree.terminate_existing(
                identity,
                term_grace_seconds=5.0,
                kill_grace_seconds=2.0,
            )
            cleaned = (
                terminated
                if os.name == "nt"
                else terminated or not identity.is_current()
            )
            cleanup.append((node_id, attempt_id, identity, cleaned))

        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                return {**projection, "cancellation_outcome": "already_terminal"}
            failed_cleanup = []
            for node_id, attempt_id, identity, cleaned in cleanup:
                node = projection["nodes"].get(node_id, {})
                claim = node.get("claim", {})
                recovery = node.get("recovery", {})
                ownership = (
                    claim
                    if claim.get("attempt_id") == attempt_id
                    else recovery
                )
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == attempt_id
                    ),
                    {},
                )
                serialized = ownership.get("process_identity") or attempt.get(
                    "process_identity"
                )
                if (
                    ownership.get("attempt_id") != attempt_id
                    or not isinstance(serialized, dict)
                    or serialized.get("pid") != identity.pid
                    or serialized.get("start_time") != identity.start_time
                    or serialized.get("job_name") != identity.job_name
                ):
                    continue
                if cleaned:
                    claim.pop("process_identity", None)
                    if isinstance(recovery, dict):
                        recovery["termination_confirmed"] = True
                        recovery["observation"] = "known_stopped"
                    if isinstance(attempt, dict):
                        attempt["process_stop"] = {
                            "recorded_at": _utc_now(),
                            "cleaned": True,
                            "identity_matched": True,
                        }
                    self._append_locked(
                        directory,
                        projection,
                        "process_reaped",
                        {"pid": identity.pid, "cleanup_complete": True},
                        node_id=node_id,
                        attempt_id=attempt_id,
                    )
                else:
                    if isinstance(attempt, dict):
                        attempt["process_stop"] = {
                            "recorded_at": _utc_now(),
                            "cleaned": False,
                            "identity_matched": True,
                        }
                    failed_cleanup.append((node_id, attempt_id, identity.pid))
            if failed_cleanup:
                projection["last_error"] = {
                    "code": "cleanup_failed",
                    "message": "owned process cleanup did not complete",
                }
                for node_id, attempt_id, pid in failed_cleanup:
                    self._append_locked(
                        directory,
                        projection,
                        "cleanup_failed",
                        {"pid": pid, "cleanup_complete": False},
                        node_id=node_id,
                        attempt_id=attempt_id,
                    )
                with self._connect() as connection:
                    self._record_admission_event(
                        connection,
                        "cleanup_failed",
                        run_id=run_id,
                        reason_code="uninterruptible_process",
                    )

            reconciliation_nodes = []
            releasable_outward_attempts: list[str] = []
            for node_id, attempt_id in sorted(outward_attempts):
                node = projection["nodes"].get(node_id)
                if not isinstance(node, dict):
                    continue
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == attempt_id
                    ),
                    None,
                )
                if not isinstance(attempt, dict) or attempt.get("state") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    continue
                claim = node.get("claim")
                recovery = node.get("recovery")
                ownership = (
                    claim
                    if isinstance(claim, Mapping)
                    and claim.get("attempt_id") == attempt_id
                    else recovery
                )
                if not isinstance(ownership, Mapping) or ownership.get(
                    "attempt_id"
                ) != attempt_id:
                    continue
                process_stop = attempt.get("process_stop")
                termination_confirmed = bool(
                    isinstance(process_stop, Mapping) and process_stop.get("cleaned")
                )
                node.pop("claim", None)
                node["recovery"] = {
                    "attempt_id": attempt_id,
                    "owner_id": attempt.get("owner_id", ownership.get("owner_id")),
                    "owner_epoch": attempt.get(
                        "owner_epoch", ownership.get("owner_epoch")
                    ),
                    "executor_id": attempt.get("executor_id", "unknown"),
                    "effect_classification": "outward",
                    "process_identity": attempt.get("process_identity"),
                    "observation": (
                        "known_stopped"
                        if termination_confirmed
                        else "outcome_uncertain"
                    ),
                    "termination_confirmed": termination_confirmed,
                    "cancel_requested_at": _utc_now(),
                }
                node["state"] = "paused"
                node["pending_interaction"] = {
                    "type": "reconcile",
                    "interaction_id": f"reconcile-{attempt_id}",
                    "attempt_id": attempt_id,
                    "reason_code": "cancelled_outward_outcome_uncertain",
                }
                attempt.update({
                    "state": "paused",
                    "error_code": "reconciliation_required",
                })
                if termination_confirmed:
                    releasable_outward_attempts.append(attempt_id)
                reconciliation_nodes.append(node_id)
            if reconciliation_nodes:
                projection["status"] = "paused"
                self._append_locked(
                    directory,
                    projection,
                    "cancel_reconciliation_required",
                    {
                        "reason_code": "outward_outcome_unknown",
                        "node_ids": reconciliation_nodes,
                    },
                )
                with self._connect() as connection:
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    for attempt_id in releasable_outward_attempts:
                        self._release_worker_claim(
                            attempt_id, connection=connection
                        )
                    self._record_coordinator_wake(
                        connection,
                        run_id=run_id,
                        reason_code="cancel_reconciliation_required",
                    )
                self._notify_coordinator()
                return {
                    **projection,
                    "cancellation_outcome": "reconciliation_required",
                }
            if failed_cleanup:
                return {**projection, "cancellation_outcome": "cleanup_failed"}

            pending_registry_payloads = _pending_session_registry_payloads(projection)
            if pending_registry_payloads:
                if projection.get("status") == "recovery_pending":
                    reset_payloads = {}
                    for pending_payload in pending_registry_payloads.values():
                        candidate, _retry_count = (
                            _session_registry_candidate_from_payload(
                                pending_payload
                            )
                        )
                        reset_payloads[candidate.winning_attempt_id] = (
                            _session_registry_candidate_payload(
                                candidate,
                                retry_count=0,
                            )
                        )
                    _store_pending_session_registry_payloads(
                        projection,
                        reset_payloads,
                    )
                    projection.pop("next_registry_update_at", None)
                    projection["status"] = "running"
                    projection["last_error"] = None
                self._append_locked(
                    directory,
                    projection,
                    "cancel_registry_update_pending",
                    {"reason_code": "pending_session_registry_update"},
                )
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status=?, desired_status='cancelled', "
                        "updated_at=? WHERE run_id=?",
                        (projection["status"], projection["updated_at"], run_id),
                    )
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    self._record_coordinator_wake(
                        connection,
                        run_id=run_id,
                        reason_code="cancel_registry_update_pending",
                    )
                self._notify_coordinator()
                return {
                    **projection,
                    "cancellation_outcome": "registry_update_pending",
                }

            projection["status"] = "cancelled"
            projection["desired_status"] = None
            for node in projection["nodes"].values():
                if node["state"] not in {"succeeded", "failed", "skipped"}:
                    claim = node.pop("claim", None)
                    node.pop("recovery", None)
                    node["state"] = "cancelled"
                    if claim and node.get("attempts"):
                        node["attempts"][-1].update({
                            "state": "cancelled",
                            "error_code": "cancelled",
                        })
            self._append_locked(directory, projection, "run_cancelled")
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                connection.execute(
                    "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                )
                self._record_coordinator_wake(
                    connection, run_id=run_id, reason_code="run_cancelled"
                )
            self._notify_coordinator()
            return {**projection, "cancellation_outcome": "cancelled"}

    def resume_run(
        self,
        run_id: str,
        *,
        always_run_nodes: AbstractSet[str],
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        always_run = frozenset(always_run_nodes)
        with (
            workflow_lock(self.admission_lock),
            workflow_lock(self._run_lock_path(run_id)),
        ):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise WorkflowConflict("stale resume decision")
            if projection["status"] == "recovery_pending":
                pending_registry_payloads = _pending_session_registry_payloads(
                    projection
                )
                reset_payloads = {}
                for pending_payload in pending_registry_payloads.values():
                    candidate, _retry_count = (
                        _session_registry_candidate_from_payload(pending_payload)
                    )
                    reset_payloads[candidate.winning_attempt_id] = (
                        _session_registry_candidate_payload(
                            candidate,
                            retry_count=0,
                        )
                    )
                _store_pending_session_registry_payloads(
                    projection,
                    reset_payloads,
                )
                projection.pop("next_registry_update_at", None)
                projection["last_error"] = None
                self._append_locked(
                    directory,
                    projection,
                    "run_resumed",
                    {"reason_code": "persistent_session_registry_update_retry"},
                )
                return self._request_runnable_locked(
                    directory,
                    projection,
                    reason="persistent_session_registry_update_retry",
                )
            if projection["status"] not in {"failed", "interrupted"}:
                if (
                    projection.get("status") == "running"
                    and projection.get("execution_mode") == "foreground"
                    and (
                        (lease := self._foreground_lease(projection)) is None
                        or not lease_is_fresh(lease, self._lease_clock())
                    )
                ):
                    raise ForegroundExecutionConflict(
                        "foreground owner conflict: expired owner requires adoption"
                    )
                return projection
            for node in projection["nodes"].values():
                recovery = node.get("recovery")
                if (
                    isinstance(recovery, Mapping)
                    and recovery.get("observation")
                    in {"still_running", "outcome_uncertain"}
                    and not recovery.get("termination_confirmed")
                ):
                    raise RuntimeError(
                        "cannot resume while the prior executor is still running "
                        "or its identity is uncertain"
                    )
            for node_id, node in projection["nodes"].items():
                if node["state"] == "succeeded" and node_id not in always_run:
                    continue
                node.pop("claim", None)
                recovery = node.get("recovery")
                if not isinstance(recovery, Mapping) or recovery.get(
                    "termination_confirmed"
                ):
                    node.pop("recovery", None)
                node["state"] = (
                    "ready"
                    if all(
                        projection["nodes"][dependency]["state"] == "succeeded"
                        for dependency in node["depends_on"]
                    )
                    else "pending"
                )
            projection["last_error"] = None
            self._append_locked(directory, projection, "run_resumed")
            return self._request_runnable_locked(
                directory,
                projection,
                reason="run_resumed",
            )

    @staticmethod
    def _interaction_identity(node: Mapping[str, object]) -> str | None:
        pending = node.get("pending_interaction")
        if not isinstance(pending, Mapping):
            return None
        value = pending.get("interaction_id") or pending.get("action_digest")
        return str(value) if isinstance(value, str) and value else None

    def _already_decided(
        self,
        projection: Mapping[str, object],
        interaction_id: str | None,
    ) -> ApprovalDecision | None:
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            return None
        for node_id, raw_node in projection["nodes"].items():
            if not isinstance(raw_node, Mapping):
                continue
            recorded = raw_node.get("approval_last_decision")
            if not isinstance(recorded, Mapping):
                continue
            recorded_id = recorded.get("interaction_id")
            if recorded_id != interaction_id:
                continue
            return ApprovalDecision(
                run_id=str(projection["run_id"]),
                node_id=str(node_id),
                decision=str(recorded["decision"]),
                outcome="already_decided",
                interaction_id=str(recorded_id),
                state_version=int(projection["state_version"]),
            )
        return None

    def approve_run(
        self,
        run_id: str,
        *,
        comment: str = "",
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        operator_scope: str | None = None,
    ) -> ApprovalDecision:
        return self._decide_run(
            run_id,
            decision="approved",
            response=comment,
            expected_state_version=expected_state_version,
            interaction_id=interaction_id,
            actor=actor,
            channel=channel,
            operator_scope=operator_scope,
        )

    def reject_run(
        self,
        run_id: str,
        *,
        reason: str = "",
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        operator_scope: str | None = None,
    ) -> ApprovalDecision:
        return self._decide_run(
            run_id,
            decision="rejected",
            response=reason,
            expected_state_version=expected_state_version,
            interaction_id=interaction_id,
            actor=actor,
            channel=channel,
            operator_scope=operator_scope,
        )

    def _decide_run(
        self,
        run_id: str,
        *,
        decision: str,
        response: str,
        expected_state_version: int | None,
        interaction_id: str | None,
        actor: str | None,
        channel: str | None,
        operator_scope: str | None,
    ) -> ApprovalDecision:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision is invalid")
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise ValueError("interaction ID is required")
        if not isinstance(response, str):
            raise TypeError("approval response must be text")
        if len(response.encode("utf-8")) > min(self.max_input_bytes, 64 * 1024):
            raise InputSnapshotError("approval response exceeds the configured limit")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        from plugins.workflow.schema import load_workflow

        package = load_workflow(directory / "definition.yaml")
        definitions = {node.id: node for node in package.definition.nodes}
        with (
            workflow_lock(self.admission_lock),
            workflow_lock(self._run_lock_path(run_id)),
        ):
            projection = json.loads((directory / "run.json").read_text())
            duplicate = self._already_decided(projection, interaction_id)
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                if duplicate is not None:
                    return duplicate
                raise WorkflowConflict("stale approval decision")
            candidates = [
                (node_id, node)
                for node_id, node in projection["nodes"].items()
                if node.get("state") == "paused"
                and self._interaction_identity(node) is not None
                and self._interaction_identity(node) == interaction_id
            ]
            if len(candidates) != 1:
                if duplicate is not None:
                    return duplicate
                raise ValueError(
                    "run does not have exactly one matching pending interaction"
                )
            node_id, node = candidates[0]
            resolved_id = self._interaction_identity(node)
            assert resolved_id is not None
            pending = node["pending_interaction"]
            pending_type = str(pending.get("type") or pending.get("kind") or "")
            safe_response = (_sanitize_diagnostic(response.strip()) or "")[:64_000]
            record = {
                "decision": decision,
                "interaction_id": resolved_id,
            }
            node["approval_last_decision"] = record
            node.pop("pending_interaction", None)
            event_payload: dict[str, object] = {
                "decision": decision,
                "interaction_id": resolved_id,
            }
            if actor:
                event_payload["actor"] = _sanitize_diagnostic(actor)
            if channel:
                event_payload["channel"] = _sanitize_diagnostic(channel)

            terminal = False
            if decision == "approved":
                if pending_type == "workflow_approval":
                    definition = definitions[node_id]
                    approval = definition.value
                    if bool(approval.get("capture_response")):
                        encoded = safe_response.encode("utf-8")
                        output_type = definition.options.get("output_type")
                        typed_approval = (
                            projection.get("language", {}).get("effective_profile")
                            == WorkflowLanguageProfile.ARCHON_2026_07.value
                            and output_type is not None
                        )
                        attempt_id = (
                            str(node["attempts"][-1]["attempt_id"])
                            if typed_approval
                            else None
                        )
                        if attempt_id is not None:
                            relative = _write_or_reuse_typed_approval_output(
                                directory,
                                node_id=node_id,
                                attempt_id=attempt_id,
                                data=encoded,
                            )
                        else:
                            relative = (
                                Path("nodes")
                                / node_id
                                / "approval"
                                / "output.txt"
                            )
                            _atomic_text(directory / relative, safe_response)
                        artifact = {
                            "node_id": node_id,
                            "attempt_id": attempt_id,
                            "relative_path": relative.as_posix(),
                            "media_type": (
                                _TYPED_PUBLICATION_TEXT_MEDIA_TYPE
                                if typed_approval
                                else "text/plain"
                            ),
                            "size_bytes": len(encoded),
                            "sha256": _sha256(encoded),
                        }
                        if typed_approval:
                            assert attempt_id is not None
                            artifact_ref = ArtifactRef(
                                relative_path=relative.as_posix(),
                                media_type=_TYPED_PUBLICATION_TEXT_MEDIA_TYPE,
                                size_bytes=len(encoded),
                                sha256=_sha256(encoded),
                            )
                            publication_candidate = TypedPublicationCandidate(
                                attempt_relative_path=relative.as_posix(),
                                output_type=str(output_type),
                                media_type=_TYPED_PUBLICATION_TEXT_MEDIA_TYPE,
                                size_bytes=len(encoded),
                                sha256=_sha256(encoded),
                                schema_fingerprint=None,
                                canonicalization_version=1,
                                session_id=None,
                            )
                            canonical_artifact = (
                                _canonical_typed_publication_artifact(
                                    (artifact_ref,),
                                    publication_candidate,
                                )
                            )
                            publication_ref = self._publish_typed_bundle_locked(
                                directory,
                                projection,
                                run_id=run_id,
                                node_id=node_id,
                                attempt_id=attempt_id,
                                artifact=canonical_artifact,
                                candidate=publication_candidate,
                            )
                            artifact.update(
                                _typed_publication_fields(publication_ref)
                            )
                        projection["artifacts"].append(artifact)
                        event_payload["artifact"] = artifact
                    node["state"] = "succeeded"
                    if node.get("attempts"):
                        node["attempts"][-1]["state"] = "succeeded"
                elif pending_type == "approval":
                    node["state"] = "ready"
                    node["action_grant"] = resolved_id
                else:
                    raise ValueError("pending interaction is not approvable")
            else:
                definition = definitions[node_id]
                approval = (
                    definition.value if definition.node_type == "approval" else {}
                )
                on_reject = (
                    approval.get("on_reject") if isinstance(approval, Mapping) else None
                )
                attempts = int(node.get("approval_rework_attempts", 0))
                maximum = (
                    int(on_reject.get("max_attempts", 3))
                    if isinstance(on_reject, Mapping)
                    else 0
                )
                if pending_type == "workflow_approval" and attempts < maximum:
                    node["state"] = "ready"
                    node["approval_rework"] = {"reason": safe_response}
                else:
                    terminal = True
                    projection["status"] = "cancelled"
                    projection["desired_status"] = None
                    for candidate in projection["nodes"].values():
                        if candidate["state"] not in {"succeeded", "failed", "skipped"}:
                            candidate.pop("claim", None)
                            candidate["state"] = "cancelled"

            self._append_locked(
                directory,
                projection,
                f"interaction_{decision}",
                event_payload,
                node_id=node_id,
            )
            if terminal:
                self._append_locked(directory, projection, "run_cancelled")
                with self._connect() as connection:
                    self._sync_integrity_index(
                        connection,
                        projection=projection,
                        journal_sha256=_sha256(
                            (directory / "events.jsonl").read_bytes()
                        ),
                    )
                    connection.execute(
                        "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                    )
                    self._record_coordinator_wake(
                        connection,
                        run_id=run_id,
                        reason_code=f"interaction_{decision}",
                    )
                self._notify_coordinator()
            else:
                projection = self._request_runnable_locked(
                    directory,
                    projection,
                    reason=f"interaction_{decision}",
                )
            return ApprovalDecision(
                run_id=run_id,
                node_id=node_id,
                decision=decision,
                outcome="applied",
                interaction_id=resolved_id,
                state_version=int(projection["state_version"]),
            )

    def consume_action_grant(
        self, claim: NodeClaim, *, now: LeaseClockSample | None = None
    ) -> str | None:
        """Remove one exact worker grant durably before spawning the worker."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(
            self._run_lock_path(claim.run_id)
        ), self._execution_fence_transaction(
            claim.execution_fence, now
        ) as fence_connection:
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale action grant consumer")
            digest = node.pop("action_grant", None)
            if digest is None:
                return None
            self._append_locked(
                directory,
                projection,
                "action_grant_consumed",
                {"grant_consumed": True},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                defer_notification=fence_connection is not None,
            )
            return str(digest)

    def provide_loop_input(
        self,
        run_id: str,
        user_input: str,
        *,
        expected_state_version: int,
        interaction_id: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Compare-and-set one paused interactive loop back to ready."""
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise ValueError("interaction ID is required")
        if not isinstance(user_input, str):
            raise TypeError("loop input must be text")
        encoded = user_input.encode("utf-8")
        if len(encoded) > self.max_input_bytes:
            raise InputSnapshotError("loop input exceeds the configured input limit")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["state_version"] != expected_state_version:
                raise WorkflowConflict("stale loop input decision")
            if projection["status"] != "paused":
                raise ValueError("run is not waiting for loop input")
            candidates = [
                (node_id, node)
                for node_id, node in projection["nodes"].items()
                if node.get("state") == "paused"
                and isinstance(node.get("pending_interaction"), dict)
                and node["pending_interaction"].get("type") == "loop_input"
                and self._interaction_identity(node) == interaction_id
            ]
            if len(candidates) != 1:
                raise ValueError("run does not have exactly one pending loop input")
            node_id, node = candidates[0]
            generation = int(node.get("loop_state", {}).get("iteration", 0))
            relative = (
                Path("nodes")
                / node_id
                / "inputs"
                / f"after-iteration-{generation:04d}.txt"
            )
            path = directory / relative
            _atomic_text(path, user_input)
            artifact = {
                "node_id": node_id,
                "attempt_id": None,
                "relative_path": relative.as_posix(),
                "media_type": "text/plain",
                "size_bytes": len(encoded),
                "sha256": _sha256(encoded),
            }
            projection["artifacts"].append(artifact)
            node["state"] = "ready"
            node.pop("pending_interaction", None)
            node["loop_user_input_artifact"] = relative.as_posix()
            event_payload: dict[str, object] = {
                "artifact": artifact,
                "iteration": generation,
                "interaction_id": interaction_id,
            }
            if actor:
                event_payload["actor"] = _sanitize_diagnostic(actor)
            if channel:
                event_payload["channel"] = _sanitize_diagnostic(channel)
            self._append_locked(
                directory,
                projection,
                "loop_input_provided",
                event_payload,
                node_id=node_id,
            )
            return self._request_runnable_locked(
                directory,
                projection,
                reason="input_provided",
            )

    def retry_run(
        self,
        run_id: str,
        *,
        node_id: str | None = None,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Explicitly retry one failed/interrupted node with compare-and-set safety."""
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise WorkflowConflict("stale retry decision")
            candidates = [
                (candidate_id, node)
                for candidate_id, node in projection["nodes"].items()
                if (node_id is None or candidate_id == node_id)
                and node.get("state") in {"failed", "interrupted"}
                and not isinstance(node.get("pending_interaction"), Mapping)
                and not (
                    isinstance(node.get("recovery"), Mapping)
                    and node["recovery"].get("observation")
                    in {"still_running", "outcome_uncertain"}
                    and not node["recovery"].get("termination_confirmed")
                )
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "retry requires exactly one replay-safe failed or interrupted node"
                )
            selected_id, node = candidates[0]
            node.pop("claim", None)
            node.pop("next_attempt_at", None)
            node["state"] = (
                "ready"
                if all(
                    projection["nodes"][dependency]["state"] == "succeeded"
                    for dependency in node["depends_on"]
                )
                else "pending"
            )
            projection["last_error"] = None
            self._append_locked(
                directory,
                projection,
                "node_retry_requested",
                {"reason_code": "operator_retry"},
                node_id=selected_id,
            )
            return self._request_runnable_locked(
                directory,
                projection,
                reason="operator_retry",
            )

    def reconcile_run(
        self,
        run_id: str,
        outcome: str,
        *,
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Resolve one unknown-side-effect pause without making an inference."""
        if outcome not in {"confirmed-succeeded", "confirmed-failed", "safe-to-retry"}:
            raise ValueError("invalid reconciliation outcome")
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise ValueError("interaction ID is required")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise WorkflowConflict("stale reconciliation decision")
            candidates = []
            for candidate_id, node in projection["nodes"].items():
                pending = node.get("pending_interaction")
                is_reconcile = pending == "reconcile" or (
                    isinstance(pending, Mapping) and pending.get("type") == "reconcile"
                )
                identity = self._interaction_identity(node)
                if is_reconcile and identity == interaction_id:
                    candidates.append((candidate_id, node))
            if len(candidates) != 1:
                raise ValueError("reconcile requires exactly one matching interaction")
            selected_id, node = candidates[0]
            recovery = node.get("recovery")
            if (
                outcome == "safe-to-retry"
                and isinstance(recovery, Mapping)
                and not recovery.get("termination_confirmed")
            ):
                raise RuntimeError(
                    "cannot authorize replay until prior executor termination is proven"
                )
            node.pop("pending_interaction", None)
            if outcome == "confirmed-succeeded":
                node["state"] = "succeeded"
                projection["last_error"] = None
            elif outcome == "confirmed-failed":
                node["state"] = "failed"
                projection["status"] = "failed"
            else:
                node["state"] = "ready"
                projection["last_error"] = None
            if isinstance(recovery, dict):
                recovery["reconciled_outcome"] = outcome
                recovery["reconciled_at"] = _utc_now()
                attempt_id = recovery.get("attempt_id")
                attempt = next(
                    (
                        candidate
                        for candidate in reversed(node.get("attempts", []))
                        if candidate.get("attempt_id") == attempt_id
                    ),
                    None,
                )
                if isinstance(attempt, dict):
                    attempt["reconciliation"] = {
                        "outcome": outcome,
                        "recorded_at": recovery["reconciled_at"],
                    }
                node.pop("recovery", None)
            self._append_locked(
                directory,
                projection,
                "node_reconciled",
                {"outcome": outcome, "interaction_id": interaction_id},
                node_id=selected_id,
            )
            if outcome != "confirmed-failed":
                return self._request_runnable_locked(
                    directory,
                    projection,
                    reason="run_reconciled",
                )
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                self._record_coordinator_wake(
                    connection, run_id=run_id, reason_code="run_reconciled"
                )
            self._notify_coordinator()
            return projection

    def abandon_run(
        self,
        run_id: str,
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        return self._set_terminal(
            run_id,
            "abandoned",
            allowed_from={"interrupted", "failed", "paused"},
            expected_state_version=expected_state_version,
            operator_scope=operator_scope,
        )

    def _set_terminal(
        self,
        run_id: str,
        target: str,
        *,
        allowed_from: set[str],
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self.admission_lock), workflow_lock(
            self._run_lock_path(run_id)
        ):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise WorkflowConflict("stale terminal transition")
            if projection["status"] not in allowed_from and projection[
                "status"
            ] not in {"succeeded", "failed", "cancelled", "abandoned"}:
                raise ValueError(
                    "only interrupted, failed, or paused runs may be abandoned"
                )
            if any(
                isinstance(node.get("recovery"), Mapping)
                and node["recovery"].get("observation")
                in {"still_running", "outcome_uncertain"}
                and not node["recovery"].get("termination_confirmed")
                for node in projection["nodes"].values()
            ):
                raise RuntimeError(
                    "cannot abandon while executor termination is unproven"
                )
            if any(
                isinstance(node.get("claim"), Mapping)
                for node in projection["nodes"].values()
            ):
                raise RuntimeError("cannot abandon while a live executor claim exists")
            if projection["status"] in {"succeeded", "cancelled", "abandoned"}:
                if target == "cancelled":
                    return {**projection, "cancellation_outcome": "already_terminal"}
                return projection
            if target == "cancelled" and any(
                node.get("pending_interaction") == "reconcile"
                or (
                    isinstance(node.get("pending_interaction"), dict)
                    and node["pending_interaction"].get("type") == "reconcile"
                )
                for node in projection["nodes"].values()
            ):
                self._append_locked(
                    directory,
                    projection,
                    "cancel_reconciliation_required",
                    {"reason_code": "outcome_unknown"},
                )
                return {
                    **projection,
                    "cancellation_outcome": "reconciliation_required",
                }
            projection["status"] = target
            for node in projection["nodes"].values():
                if node["state"] not in {"succeeded", "failed", "skipped"}:
                    claim = node.pop("claim", None)
                    node["state"] = target if target == "cancelled" else "interrupted"
                    if claim and node.get("attempts"):
                        node["attempts"][-1].update({
                            "state": node["state"],
                            "error_code": target,
                        })
            self._append_locked(directory, projection, f"run_{target}")
            with self._connect() as connection:
                self._sync_integrity_index(
                    connection,
                    projection=projection,
                    journal_sha256=_sha256(
                        (directory / "events.jsonl").read_bytes()
                    ),
                )
                connection.execute(
                    "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                )
                self._record_coordinator_wake(
                    connection, run_id=run_id, reason_code=f"run_{target}"
                )
            self._notify_coordinator()
            if target == "cancelled":
                return {**projection, "cancellation_outcome": "cancelled"}
            return projection

    def cleanup_runs(
        self,
        *,
        older_than: timedelta = timedelta(days=7),
        execute: bool = False,
        confirmation_token: str | None = None,
        operator_scope: str | None = None,
        required_metadata: Mapping[str, str | None] | None = None,
        authority_binding: str = _LOCAL_ADMIN_AUTHORITY_BINDING,
    ) -> dict[str, object]:
        authority_binding_digest = self._authority_binding_digest(authority_binding)
        if execute:
            if not confirmation_token:
                raise ValueError("cleanup execution requires a confirmation token")
            return self._execute_cleanup(
                confirmation_token,
                authority_binding_digest=authority_binding_digest,
            )
        if confirmation_token is not None:
            raise ValueError("confirmation token is valid only with execute")
        return self._preview_cleanup(
            older_than=older_than,
            operator_scope=operator_scope,
            required_metadata=required_metadata,
            authority_binding_digest=authority_binding_digest,
        )

    @staticmethod
    def _authority_binding_digest(authority_binding: str) -> str:
        if not isinstance(authority_binding, str) or not authority_binding:
            raise ValueError("authority_binding must not be empty")
        if len(authority_binding.encode("utf-8")) > 4096:
            raise ValueError("authority_binding is too large")
        return _sha256(authority_binding.encode("utf-8"))

    def _preview_cleanup(
        self,
        *,
        older_than: timedelta,
        operator_scope: str | None,
        required_metadata: Mapping[str, str | None] | None,
        authority_binding_digest: str,
    ) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - older_than
        scope_clause = (
            " AND operator_scope_digest=?" if operator_scope is not None else ""
        )
        values: tuple[object, ...] = (
            (self._scope_digest(operator_scope),) if operator_scope is not None else ()
        )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, run_directory, status, updated_at FROM runs "
                "WHERE admission_state='published' "
                "AND status IN ('succeeded','failed','cancelled','abandoned') "
                f"AND updated_at<=?{scope_clause} "
                "ORDER BY updated_at, run_id LIMIT 201",
                (cutoff.isoformat(), *values),
            ).fetchall()
        more_candidates = len(rows) > 200
        rows = rows[:200]
        if required_metadata:
            rows = [
                row
                for row in rows
                if self._run_has_metadata(Path(row["run_directory"]), required_metadata)
            ]
        from plugins.workflow.notifications import (
            NotificationOutbox,
            NotificationReconciliationError,
        )

        outbox = NotificationOutbox(self)
        candidates: list[dict[str, object]] = []
        for row in rows:
            reconciliation_failed = False
            try:
                outbox.reconcile_run(str(row["run_id"]))
            except NotificationReconciliationError:
                reconciliation_failed = True
            except WorkflowLockTimeout:
                pass
            try:
                with workflow_lock(
                    self._run_lock_path(row["run_id"]), timeout_seconds=0.05
                ):
                    candidate = self._cleanup_candidate(row)
            except WorkflowLockTimeout:
                candidate = self._blocked_cleanup_candidate(
                    row, "active_reader_or_writer"
                )
            except (JournalRecoveryError, OSError, ValueError, json.JSONDecodeError):
                reconciliation_failed = True
                candidate = self._blocked_cleanup_candidate(
                    row, "notification_reconciliation_unverified"
                )
            if reconciliation_failed and (
                "notification_reconciliation_unverified"
                not in candidate["blocked_reasons"]
            ):
                candidate["blocked_reasons"].append(
                    "notification_reconciliation_unverified"
                )
            candidates.append(candidate)
        health = self.storage_health()
        blocked_reasons = (
            ["storage_repair_required"]
            if health["status"] != "healthy"
            else []
        )
        eligible = bool(candidates) and not blocked_reasons and not any(
            candidate["blocked_reasons"] for candidate in candidates
        )
        token = uuid.uuid4().hex if eligible else None
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        candidates_json = json.dumps(candidates, sort_keys=True, separators=(",", ":"))
        if token is not None:
            token_digest = _sha256(token.encode())
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO cleanup_previews ("
                    "token_digest, created_at, expires_at, preview_digest, "
                    "candidates_json, status, authority_binding_digest) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        token_digest,
                        _utc_now(),
                        expires_at.isoformat(),
                        _sha256(candidates_json.encode()),
                        candidates_json,
                        authority_binding_digest,
                    ),
                )
        return {
            "execute": False,
            "run_ids": [candidate["run_id"] for candidate in candidates],
            "candidates": candidates,
            "files": sum(int(candidate["files"]) for candidate in candidates),
            "bytes": sum(int(candidate["bytes"]) for candidate in candidates),
            "index_integrity": health["status"],
            "blocked_reasons": blocked_reasons,
            "notification_dependencies": {
                "status": "pending"
                if any(
                    int(candidate.get("notification_dependencies", 0))
                    for candidate in candidates
                )
                else "clear",
                "count": sum(
                    int(candidate.get("notification_dependencies", 0))
                    for candidate in candidates
                ),
            },
            "confirmation_token": token,
            "confirmation_expires_at": expires_at.isoformat() if token else None,
            "more_candidates": more_candidates,
        }

    @staticmethod
    def _blocked_cleanup_candidate(
        row: sqlite3.Row, reason_code: str
    ) -> dict[str, object]:
        return {
            "run_id": row["run_id"],
            "run_directory": row["run_directory"],
            "status": row["status"],
            "updated_at": row["updated_at"],
            "state_version": None,
            "event_sequence": None,
            "projection_sha256": None,
            "journal_sha256": None,
            "files": 0,
            "bytes": 0,
            "evidence_types": [],
            "notification_dependencies": 0,
            "blocked_reasons": [reason_code],
        }

    def _cleanup_candidate(self, row: sqlite3.Row) -> dict[str, object]:
        directory = Path(row["run_directory"])
        projection_bytes = (directory / "run.json").read_bytes()
        journal_bytes = (directory / "events.jsonl").read_bytes()
        projection = json.loads(projection_bytes)
        if not self._valid_projection(projection, run_id=row["run_id"]):
            raise JournalRecoveryError("cleanup candidate projection is invalid")
        paths = [path for path in directory.rglob("*") if path.is_file()]
        blocked_reasons = []
        with self._connect() as connection:
            claims = connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
                (row["run_id"],),
            ).fetchone()[0]
            notification_dependencies = connection.execute(
                "SELECT COUNT(*) FROM workflow_notification_outbox "
                "WHERE run_id=? AND state IN ('pending','leased','dead')",
                (row["run_id"],),
            ).fetchone()[0]
        if claims:
            blocked_reasons.append("live_worker_claim")
        if any(
            node.get("pending_interaction") == "reconcile"
            or (
                isinstance(node.get("pending_interaction"), Mapping)
                and node["pending_interaction"].get("type") == "reconcile"
            )
            for node in projection["nodes"].values()
        ):
            blocked_reasons.append("reconciliation_required")
        if notification_dependencies:
            blocked_reasons.append("pending_notification_delivery")
        evidence_types = ["projection", "events"]
        if any("stdout" in path.name for path in paths):
            evidence_types.append("stdout")
        if any("stderr" in path.name for path in paths):
            evidence_types.append("stderr")
        if projection.get("artifacts"):
            evidence_types.append("artifacts")
        if any(node.get("output") is not None for node in projection["nodes"].values()):
            evidence_types.append("outputs")
        return {
            "run_id": row["run_id"],
            "run_directory": str(directory),
            "status": row["status"],
            "updated_at": row["updated_at"],
            "state_version": projection["state_version"],
            "event_sequence": projection["event_sequence"],
            "projection_sha256": _sha256(projection_bytes),
            "journal_sha256": _sha256(journal_bytes),
            "files": len(paths),
            "bytes": sum(path.stat().st_size for path in paths),
            "evidence_types": evidence_types,
            "notification_dependencies": int(notification_dependencies),
            "blocked_reasons": blocked_reasons,
        }

    def cleanup_history(
        self, run_id: str, *, operator_scope: str | None = None
    ) -> tuple[dict[str, object], ...]:
        """Return durable cleanup decisions without exposing filesystem paths."""
        self.run_directory(run_id, operator_scope=operator_scope)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, timestamp, files, bytes, outcome, payload_json "
                "FROM cleanup_history WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "files": row["files"],
                "bytes": row["bytes"],
                "outcome": row["outcome"],
                "details": _sanitize(json.loads(row["payload_json"])),
            }
            for row in rows
        )

    def _execute_cleanup(
        self,
        confirmation_token: str,
        *,
        authority_binding_digest: str,
    ) -> dict[str, object]:
        if self.storage_health()["status"] != "healthy":
            raise RuntimeError("cleanup blocked: storage repair required")
        token_digest = _sha256(confirmation_token.encode())
        with self._admission_gate, workflow_lock(self.admission_lock):
            with self._connect() as connection:
                preview = connection.execute(
                    "SELECT * FROM cleanup_previews WHERE token_digest=?",
                    (token_digest,),
                ).fetchone()
            if preview is None or preview["status"] != "pending":
                raise ValueError("cleanup confirmation token is invalid or already used")
            stored_binding = preview["authority_binding_digest"]
            if not isinstance(stored_binding, str) or not hmac.compare_digest(
                stored_binding, authority_binding_digest
            ):
                raise ValueError("cleanup confirmation token authority does not match")
            if datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc):
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE cleanup_previews SET status='expired' "
                        "WHERE token_digest=?",
                        (token_digest,),
                    )
                raise ValueError("cleanup confirmation token has expired")
            candidates = json.loads(preview["candidates_json"])
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("cleanup confirmation token has no candidates")
            from plugins.workflow.notifications import (
                NotificationOutbox,
                NotificationReconciliationError,
            )

            outbox = NotificationOutbox(self)
            for candidate in candidates:
                try:
                    outbox.reconcile_run(str(candidate["run_id"]))
                except NotificationReconciliationError as exc:
                    self._invalidate_cleanup_preview(token_digest)
                    raise RuntimeError(
                        "cleanup preview changed: notification reconciliation "
                        "could not be verified"
                    ) from exc
            with ExitStack() as locks:
                for candidate in sorted(candidates, key=lambda item: item["run_id"]):
                    locks.enter_context(
                        workflow_lock(self._run_lock_path(candidate["run_id"]))
                    )
                with self._connect() as connection:
                    rows = {
                        row["run_id"]: row
                        for row in connection.execute(
                            "SELECT run_id, run_directory, status, updated_at FROM runs "
                            "WHERE admission_state='published'"
                        ).fetchall()
                    }
                current = []
                for candidate in candidates:
                    row = rows.get(candidate["run_id"])
                    if row is None:
                        self._invalidate_cleanup_preview(token_digest)
                        raise RuntimeError("cleanup preview changed: run is no longer indexed")
                    current.append(self._cleanup_candidate(row))
                current_json = json.dumps(
                    current, sort_keys=True, separators=(",", ":")
                )
                if _sha256(current_json.encode()) != preview["preview_digest"]:
                    self._invalidate_cleanup_preview(token_digest)
                    raise RuntimeError("cleanup preview changed; request a new preview")
                quarantine_root = self.quarantine_root / "cleanup" / token_digest[:16]
                quarantine_root.mkdir(parents=True, exist_ok=False)
                moved: list[tuple[dict[str, object], Path]] = []
                for candidate in candidates:
                    source = Path(candidate["run_directory"])
                    destination = quarantine_root / str(candidate["run_id"])
                    _durable_replace(source, destination)
                    moved.append((candidate, destination))
                connection = self._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for candidate, destination in moved:
                        connection.execute(
                            "INSERT INTO cleanup_history ("
                            "timestamp, token_digest, run_id, source_path, "
                            "quarantine_path, files, bytes, outcome, payload_json"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'quarantined', '{}')",
                            (
                                _utc_now(),
                                token_digest,
                                candidate["run_id"],
                                candidate["run_directory"],
                                str(destination),
                                candidate["files"],
                                candidate["bytes"],
                            ),
                        )
                        connection.execute(
                            "DELETE FROM worker_claims WHERE run_id=?",
                            (candidate["run_id"],),
                        )
                        connection.execute(
                            "DELETE FROM workflow_notification_facts WHERE run_id=?",
                            (candidate["run_id"],),
                        )
                        connection.execute(
                            "DELETE FROM workflow_notification_outbox WHERE run_id=?",
                            (candidate["run_id"],),
                        )
                        connection.execute(
                            "DELETE FROM runs WHERE run_id=?",
                            (candidate["run_id"],),
                        )
                    connection.execute(
                        "UPDATE cleanup_previews SET status='executed' "
                        "WHERE token_digest=? AND status='pending'",
                        (token_digest,),
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
        return {
            "execute": True,
            "run_ids": [candidate["run_id"] for candidate in candidates],
            "files": sum(int(candidate["files"]) for candidate in candidates),
            "bytes": sum(int(candidate["bytes"]) for candidate in candidates),
            "quarantine_paths": [str(path) for _, path in moved],
        }

    def _invalidate_cleanup_preview(self, token_digest: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE cleanup_previews SET status='invalidated' WHERE token_digest=?",
                (token_digest,),
            )

    def list_cleanup_history(
        self, *, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, timestamp, token_digest, run_id, source_path, "
                "quarantine_path, files, bytes, outcome, payload_json "
                "FROM cleanup_history ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "token_digest": row["token_digest"],
                "run_id": row["run_id"],
                "source_path": row["source_path"],
                "quarantine_path": row["quarantine_path"],
                "files": row["files"],
                "bytes": row["bytes"],
                "outcome": row["outcome"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    @staticmethod
    def _run_has_metadata(
        directory: Path, expected: Mapping[str, str | None]
    ) -> bool:
        try:
            projection = json.loads((directory / "run.json").read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        metadata = projection.get("run_metadata")
        if not isinstance(metadata, Mapping):
            return False
        return all(
            key in metadata and (value is None or metadata.get(key) == value)
            for key, value in expected.items()
        )


__all__ = [
    "ArtifactRef",
    "ForegroundExecutionConflict",
    "InputSnapshotError",
    "NodeClaim",
    "PublicationIntegrityError",
    "PublicationNotFoundError",
    "PublicationUnavailableError",
    "RunStore",
    "StorageQuotaError",
    "TypedPublicationCandidate",
    "TypedPublicationRef",
    "VerifiedPublication",
]
