"""Public local agent-handoff contract."""

from .directory import (
    AgentDirectoryEntry,
    AmbiguousAgentTarget,
    ResolvedAgentTarget,
    load_agent_directory,
    resolve_agent_target,
)
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
from .peer import PeerHermesChannel
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
    "AgentDirectoryEntry",
    "AdvanceLease",
    "AdvanceResult",
    "AgentHandoffService",
    "AmbiguousAgentTarget",
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
    "PeerHermesChannel",
    "ResolvedAgentTarget",
    "StaleAdvanceLease",
    "UnsupportedHandoffCommand",
    "load_agent_directory",
    "resolve_agent_target",
]
