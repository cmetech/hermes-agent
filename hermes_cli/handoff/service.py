"""Bounded, convergent lifecycle policy for local agent handoffs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from time import monotonic
from typing import Callable
from uuid import uuid4

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot, HandoffSpec
from .store import (
    EvidencePage,
    HandoffConflict,
    HandoffStateConflict,
    HandoffStore,
    StaleAdvanceLease,
)


_TERMINAL_PHASES = frozenset({"succeeded", "failed", "cancelled"})
_OPERATIONS = frozenset({"bind", "submit", "reconcile", "observe", "cancel"})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")


class HandoffServiceError(RuntimeError):
    """Base error for stable service-level failures."""


class UnsupportedHandoffCommand(HandoffServiceError):
    """The command is not implemented by the current handoff stage."""

    def __init__(self, kind: object) -> None:
        self.kind = kind
        super().__init__("handoff command is unsupported")


class ChannelDefinitelyNotAccepted(HandoffServiceError):
    """The channel authoritatively proved external admission did not occur."""

    def __init__(self, failure_code: str = "submission_rejected") -> None:
        if not isinstance(failure_code, str) or not _SAFE_IDENTIFIER.fullmatch(
            failure_code
        ):
            raise ValueError("handoff failure code is invalid")
        self.failure_code = failure_code
        super().__init__(failure_code)


class ChannelRetryableFailure(HandoffServiceError):
    """A read-only channel observation can be attempted again safely."""


class ChannelIndeterminate(HandoffServiceError):
    """The channel cannot prove whether an external operation occurred."""


@dataclass(frozen=True, slots=True)
class EndpointAssessment:
    endpoint: HandoffEndpoint
    available: bool
    mechanism: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, HandoffEndpoint) or not isinstance(
            self.available, bool
        ):
            raise ValueError("handoff endpoint assessment is invalid")
        for value in (self.mechanism, self.failure_code):
            if value is not None and (
                not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value)
            ):
                raise ValueError("handoff endpoint assessment is invalid")


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    snapshot: HandoffSnapshot
    operation: str | None
    observation_folded: bool

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, HandoffSnapshot) or self.operation not in (
            _OPERATIONS | {None}
        ):
            raise ValueError("handoff advance result is invalid")

    @property
    def work_done(self) -> bool:
        return self.operation is not None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _initiator_scope(initiator: object) -> str:
    if not isinstance(initiator, str):
        raise ValueError("handoff initiator is invalid")
    return initiator


class AgentHandoffService:
    """Consumer-neutral facade over one durable store and local channel."""

    def __init__(
        self,
        store: HandoffStore | None = None,
        channel: object | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.store = store or HandoffStore()
        self.channel = channel
        self._clock = clock

    def validate_endpoint(
        self, endpoint: str | HandoffEndpoint, initiator: object
    ) -> EndpointAssessment:
        parsed = HandoffEndpoint.parse(
            endpoint.canonical if isinstance(endpoint, HandoffEndpoint) else endpoint
        )
        if isinstance(endpoint, HandoffEndpoint) and parsed != endpoint:
            raise ValueError("handoff endpoint is invalid")
        scope = _initiator_scope(initiator)
        if self.channel is None:
            return EndpointAssessment(
                endpoint=parsed,
                available=False,
                failure_code="channel_unavailable",
            )
        assessment = self.channel.validate_endpoint(parsed, scope)
        if (
            not isinstance(assessment, EndpointAssessment)
            or assessment.endpoint != parsed
        ):
            raise HandoffServiceError(
                "handoff channel returned an invalid endpoint assessment"
            )
        return assessment

    def create(
        self,
        spec: HandoffSpec,
        initiator: object,
        *,
        handoff_key: str,
    ) -> HandoffSnapshot:
        if not isinstance(spec, HandoffSpec):
            raise ValueError("handoff spec is invalid")
        if HandoffEndpoint.parse(spec.endpoint.canonical) != spec.endpoint:
            raise ValueError("handoff endpoint is invalid")
        return self.store.create_or_get(
            _initiator_scope(initiator), handoff_key, spec, spec.fingerprint
        )

    def advance(self, handoff_id: str, *, budget_seconds: float = 2.0) -> AdvanceResult:
        if (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, int | float)
            or not math.isfinite(budget_seconds)
            or budget_seconds <= 0
        ):
            raise ValueError("handoff advance budget must be finite and positive")

        started = self._clock()
        lease = self.store.claim_advance(
            handoff_id,
            f"service-{uuid4().hex}",
            now=_utc_now(),
            lease_seconds=budget_seconds,
        )
        if lease is None:
            return AdvanceResult(self.store.get(handoff_id), None, False)

        operation: str | None = None
        folded = False
        next_advance_at = None
        try:
            snapshot = self.store.get(handoff_id)
            if snapshot.phase == "cancelling" and snapshot.submit_attempted_at is None:
                self.store.commit_observation(
                    lease, ChannelObservation(phase="cancelled")
                )
                folded = True
            elif (
                snapshot.phase != "cancelling"
                and snapshot.next_advance_at is not None
                and snapshot.next_advance_at > _utc_now()
            ):
                next_advance_at = snapshot.next_advance_at
            else:
                operation = self._select_operation(snapshot)
                if operation is not None:
                    journaled = self.store.journal_attempt(lease, operation)
                    remaining = budget_seconds - (self._clock() - started)
                    if remaining > 0:
                        try:
                            observation = self._call_channel(
                                operation, journaled, remaining
                            )
                        except ChannelDefinitelyNotAccepted as exc:
                            observation = ChannelObservation(
                                phase=(
                                    "cancelling" if operation == "cancel" else "failed"
                                ),
                                checkpoint=journaled.checkpoint or {},
                                failure_code=exc.failure_code,
                            )
                        except ChannelRetryableFailure:
                            observation = self._retryable_observation(
                                operation, journaled
                            )
                        except Exception:
                            observation = self._indeterminate_observation(
                                operation, journaled
                            )

                        _snapshot, folded = self._fold(lease, operation, observation)
                        next_advance_at = observation.next_advance_at
        except (HandoffStateConflict, StaleAdvanceLease):
            folded = False
        finally:
            try:
                self.store.release_advance(lease, next_advance_at=next_advance_at)
            except StaleAdvanceLease:
                pass
        return AdvanceResult(self.store.get(handoff_id), operation, folded)

    def _select_operation(self, snapshot: HandoffSnapshot) -> str | None:
        if snapshot.phase in _TERMINAL_PHASES:
            return None
        if snapshot.phase == "indeterminate":
            return "reconcile"
        if snapshot.cancel_requested_at is not None:
            if (
                snapshot.submit_attempted_at is not None
                and not self._checkpoint_proves_admission(snapshot.checkpoint)
            ):
                return "reconcile"
            return "cancel"
        if snapshot.mechanism is None:
            return "bind"
        if snapshot.phase == "prepared":
            return "reconcile" if snapshot.submit_attempted_at else "submit"
        return "observe"

    @staticmethod
    def _checkpoint_proves_admission(
        checkpoint: Mapping[str, object] | None,
    ) -> bool:
        return checkpoint is not None and (
            "run_id" in checkpoint or "process_pid" in checkpoint
        )

    def _call_channel(
        self, operation: str, snapshot: HandoffSnapshot, budget_seconds: float
    ) -> ChannelObservation:
        if self.channel is None:
            raise ChannelDefinitelyNotAccepted("channel_unavailable")
        observation = getattr(self.channel, operation)(
            snapshot, budget_seconds=budget_seconds
        )
        if not isinstance(observation, ChannelObservation):
            raise ChannelIndeterminate()
        return observation

    @staticmethod
    def _retryable_observation(
        operation: str, snapshot: HandoffSnapshot
    ) -> ChannelObservation:
        if operation in {"submit", "cancel"}:
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=snapshot.checkpoint or {},
                failure_code=(
                    "submission_indeterminate"
                    if operation == "submit"
                    else "cancellation_indeterminate"
                ),
            )
        if operation == "bind":
            return ChannelObservation(
                phase="prepared",
                checkpoint=snapshot.checkpoint or {},
                failure_code="observation_retryable",
            )
        return ChannelObservation(
            phase=snapshot.phase,
            checkpoint=snapshot.checkpoint or {},
            failure_code="observation_retryable",
        )

    @staticmethod
    def _indeterminate_observation(
        operation: str, snapshot: HandoffSnapshot
    ) -> ChannelObservation:
        if operation == "bind":
            return ChannelObservation(
                phase="prepared",
                checkpoint=snapshot.checkpoint or {},
                failure_code="endpoint_unavailable",
            )
        return ChannelObservation(
            phase="indeterminate",
            checkpoint=snapshot.checkpoint or {},
            failure_code={
                "submit": "submission_indeterminate",
                "reconcile": "reconciliation_indeterminate",
                "observe": "observation_indeterminate",
                "cancel": "cancellation_indeterminate",
            }[operation],
        )

    def _fold(
        self,
        lease,
        operation: str,
        observation: ChannelObservation,
    ) -> tuple[HandoffSnapshot, bool]:
        if operation == "bind" and observation.phase == "prepared":
            if observation.mechanism is None or observation.binding is None:
                if observation.failure_code not in {
                    "endpoint_unavailable",
                    "observation_retryable",
                }:
                    observation = ChannelObservation(
                        phase="failed", failure_code="protocol_violation"
                    )
            else:
                try:
                    return (
                        self.store.commit_binding(
                            lease,
                            observation.mechanism,
                            observation.binding,
                            observation.checkpoint,
                        ),
                        True,
                    )
                except (HandoffConflict, ValueError):
                    return (
                        self.store.commit_observation(
                            lease,
                            ChannelObservation(
                                phase="failed", failure_code="protocol_violation"
                            ),
                        ),
                        True,
                    )

        current = self.store.get(lease.handoff_id)
        observation = self._preserve_cancellation(current, observation)
        try:
            return self.store.commit_observation(lease, observation), True
        except (HandoffConflict, ValueError):
            current = self.store.get(lease.handoff_id)
            phase = (
                "indeterminate" if current.submit_attempted_at is not None else "failed"
            )
            return (
                self.store.commit_observation(
                    lease,
                    ChannelObservation(
                        phase=phase,
                        checkpoint=current.checkpoint or {},
                        failure_code="protocol_violation",
                    ),
                ),
                True,
            )
        except HandoffStateConflict:
            current = self.store.get(lease.handoff_id)
            if current.phase in _TERMINAL_PHASES:
                return current, False
            cancellation = self._preserve_cancellation(current, observation)
            if cancellation.phase == "cancelling":
                return self.store.commit_observation(lease, cancellation), True
            return (
                self.store.commit_observation(
                    lease,
                    ChannelObservation(
                        phase="failed", failure_code="protocol_violation"
                    ),
                ),
                True,
            )

    @staticmethod
    def _preserve_cancellation(
        current: HandoffSnapshot, observation: ChannelObservation
    ) -> ChannelObservation:
        if current.cancel_requested_at is None or observation.phase in (
            _TERMINAL_PHASES | {"cancelling", "indeterminate"}
        ):
            return observation
        return ChannelObservation(
            phase="cancelling",
            checkpoint=observation.checkpoint,
            failure_code=observation.failure_code,
            next_advance_at=observation.next_advance_at,
        )

    def command(
        self,
        handoff_id: str,
        kind: str,
        *,
        command_id: str,
        actor: str,
    ) -> HandoffSnapshot:
        if kind not in {"cancel", "reconcile"}:
            raise UnsupportedHandoffCommand(kind)
        self.store.record_command(handoff_id, command_id, kind, {"actor": actor})
        return self.store.get(handoff_id)

    def get(self, handoff_id: str) -> HandoffSnapshot:
        return self.store.get(handoff_id)

    def list(
        self,
        query,
        *,
        limit: int = 50,
        before: str | None = None,
    ) -> tuple[HandoffSnapshot, ...]:
        return self.store.list(query, limit=limit, before=before)

    def evidence(
        self,
        handoff_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> EvidencePage:
        return self.store.evidence(
            handoff_id, after_sequence=after_sequence, limit=limit
        )


__all__ = [
    "AdvanceResult",
    "AgentHandoffService",
    "ChannelDefinitelyNotAccepted",
    "ChannelIndeterminate",
    "ChannelRetryableFailure",
    "EndpointAssessment",
    "HandoffServiceError",
    "UnsupportedHandoffCommand",
]
