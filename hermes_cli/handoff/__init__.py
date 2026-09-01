"""Public local agent-handoff contract."""

from .models import (
    HANDOFF_PHASES,
    ChannelObservation,
    HandoffEndpoint,
    HandoffSnapshot,
    HandoffSpec,
)
from .service import (
    AdvanceResult,
    AgentHandoffService,
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
    ChannelRetryableFailure,
    EndpointAssessment,
    HandoffServiceError,
    UnsupportedHandoffCommand,
)
from .store import (
    AdvanceLease,
    CommandRecord,
    EvidencePage,
    HandoffConflict,
    HandoffEvent,
    HandoffNotFound,
    HandoffStateConflict,
    HandoffStore,
    HandoffStoreError,
    StaleAdvanceLease,
)

__all__ = [
    "AdvanceLease",
    "AdvanceResult",
    "AgentHandoffService",
    "HANDOFF_PHASES",
    "ChannelObservation",
    "ChannelDefinitelyNotAccepted",
    "ChannelIndeterminate",
    "ChannelRetryableFailure",
    "CommandRecord",
    "EvidencePage",
    "EndpointAssessment",
    "HandoffConflict",
    "HandoffEndpoint",
    "HandoffEvent",
    "HandoffNotFound",
    "HandoffSnapshot",
    "HandoffSpec",
    "HandoffServiceError",
    "HandoffStateConflict",
    "HandoffStore",
    "HandoffStoreError",
    "StaleAdvanceLease",
    "UnsupportedHandoffCommand",
]
