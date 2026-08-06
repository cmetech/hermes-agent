"""Immutable public contracts for portable workflow packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import math
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ExecutionFence:
    """Exact durable coordinator ownership required for background execution."""

    owner_id: str
    owner_epoch: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner_id, str)
            or not self.owner_id
            or len(self.owner_id) > 256
        ):
            raise ValueError("owner_id must be bounded non-empty text")
        if (
            isinstance(self.owner_epoch, bool)
            or not isinstance(self.owner_epoch, int)
            or self.owner_epoch <= 0
        ):
            raise ValueError("owner_epoch must be a positive integer")


@dataclass(frozen=True, slots=True)
class TerminalJournalReserve:
    """Durable capacity held for one attempt's terminal/recovery evidence."""

    projection_limit_bytes: int
    terminal_reserve_bytes: int

    @classmethod
    def for_projection(cls, projection_bytes: int) -> "TerminalJournalReserve":
        if (
            isinstance(projection_bytes, bool)
            or not isinstance(projection_bytes, int)
            or projection_bytes <= 0
        ):
            raise ValueError("projection_bytes must be a positive integer")
        # Ordinary progress may grow the materialized projection, but only
        # within this attempt-owned bound. Three full recovery frames cover
        # node terminal evidence, run terminal/retry evidence, and one repair
        # frame; duplicated bounded payload data is included conservatively.
        projection_limit = projection_bytes + max(8 * 1024, projection_bytes)
        terminal_reserve = 3 * (2 * projection_limit + 8 * 1024)
        return cls(projection_limit, terminal_reserve)

    def contains_projection(self, projection_bytes: int) -> bool:
        return 0 < projection_bytes <= self.projection_limit_bytes


def freeze_value(value: Any) -> Any:
    """Recursively freeze parsed YAML without changing scalar values."""
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): freeze_value(item) for key, item in value.items()
        })
    if isinstance(value, list | tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    severity: str = "error"
    blocking: bool = True
    source_line: int | None = None


class WorkflowValidationError(ValueError):
    """Raised when a portable package cannot be normalized safely."""

    def __init__(
        self,
        issues: ValidationIssue | tuple[ValidationIssue, ...] | list[ValidationIssue],
    ):
        if isinstance(issues, ValidationIssue):
            normalized = (issues,)
        else:
            normalized = tuple(issues)
        self.issues = normalized
        super().__init__("; ".join(issue.message for issue in normalized))


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    node_type: str
    value: Any
    depends_on: tuple[str, ...]
    source_index: int
    source_line: int | None
    options: Mapping[str, Any]
    origin: WorkflowNodeOrigin | None = None


@dataclass(frozen=True, slots=True)
class ValidatedWorkflowResourceBodies:
    """Authenticated resource templates normalized for later binding."""

    command_bodies: Mapping[str, str]
    named_script_bodies: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_bodies",
            MappingProxyType(dict(self.command_bodies)),
        )
        object.__setattr__(
            self,
            "named_script_bodies",
            MappingProxyType(dict(self.named_script_bodies)),
        )


_WORKFLOW_SOURCE_METADATA_MAX_CHARS = 4096
_WORKFLOW_SOURCE_NAME_MAX_CHARS = 128
_WORKFLOW_EXPANDED_NODE_ID_MAX_CHARS = (4 * _WORKFLOW_SOURCE_NAME_MAX_CHARS) + 6


def _bounded_source_text(value: object, field_name: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    return value


def _logical_source_location(value: object, field_name: str) -> str:
    location = _bounded_source_text(
        value,
        field_name,
        limit=_WORKFLOW_SOURCE_METADATA_MAX_CHARS,
    )
    candidate = PurePosixPath(location)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or location.startswith("~")
        or "\\" in location
    ):
        raise ValueError(f"{field_name} must be a contained logical location")
    return candidate.as_posix()


@dataclass(frozen=True, slots=True)
class WorkflowSourceNode:
    """One immutable authored node captured before profile normalization."""

    id: str
    node_type: str
    value: Any
    depends_on: tuple[str, ...]
    source_index: int
    source_line: int | None
    options: Mapping[str, Any]
    field_lines: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    origin: WorkflowNodeOrigin | None = None

    def __post_init__(self) -> None:
        _bounded_source_text(
            self.id, "source node id", limit=_WORKFLOW_EXPANDED_NODE_ID_MAX_CHARS
        )
        _bounded_source_text(
            self.node_type,
            "source node type",
            limit=_WORKFLOW_SOURCE_NAME_MAX_CHARS,
        )
        if (
            isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or self.source_index < 0
        ):
            raise ValueError("source_index must be a non-negative integer")
        if self.source_line is not None and (
            isinstance(self.source_line, bool)
            or not isinstance(self.source_line, int)
            or self.source_line <= 0
        ):
            raise ValueError("source_line must be a positive integer when present")
        object.__setattr__(self, "value", freeze_value(self.value))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        object.__setattr__(self, "options", freeze_value(self.options))
        object.__setattr__(self, "field_lines", freeze_value(self.field_lines))
        if self.origin is not None and not isinstance(
            self.origin,
            WorkflowNodeOrigin,
        ):
            raise ValueError("source node origin must be workflow provenance")


@dataclass(frozen=True, slots=True)
class WorkflowSourceDocument:
    """Bounded authenticated source bytes with no active language authority."""

    name: str
    description: str
    nodes: tuple[WorkflowSourceNode, ...]
    options: Mapping[str, Any]
    root: Path
    workflow_path: Path
    sidecar_path: Path | None
    sidecar: Mapping[str, Any]
    source: str
    precedence: int
    definition_bytes: bytes
    sidecar_bytes: bytes | None
    definition_location: str
    sidecar_location: str | None
    field_lines: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        _bounded_source_text(
            self.name, "workflow source name", limit=_WORKFLOW_SOURCE_NAME_MAX_CHARS
        )
        _bounded_source_text(
            self.description,
            "workflow source description",
            limit=2 * 1024 * 1024,
        )
        _bounded_source_text(
            self.source,
            "workflow catalog source",
            limit=_WORKFLOW_SOURCE_NAME_MAX_CHARS,
        )
        if (
            isinstance(self.precedence, bool)
            or not isinstance(self.precedence, int)
            or not 0 <= self.precedence <= 2**31 - 1
        ):
            raise ValueError("workflow precedence must be a bounded non-negative integer")
        if not isinstance(self.definition_bytes, bytes):
            raise ValueError("definition_bytes must be immutable bytes")
        if self.sidecar_bytes is not None and not isinstance(self.sidecar_bytes, bytes):
            raise ValueError("sidecar_bytes must be immutable bytes when present")
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "options", freeze_value(self.options))
        object.__setattr__(self, "sidecar", freeze_value(self.sidecar))
        object.__setattr__(self, "field_lines", freeze_value(self.field_lines))
        object.__setattr__(
            self,
            "definition_location",
            _logical_source_location(
                self.definition_location, "definition_location"
            ),
        )
        if self.sidecar_location is not None:
            object.__setattr__(
                self,
                "sidecar_location",
                _logical_source_location(self.sidecar_location, "sidecar_location"),
            )


@dataclass(frozen=True, slots=True)
class WorkflowCompilationLimits:
    """Hard bounds applied to one root's complete include closure."""

    max_include_depth: int
    max_dependencies: int
    max_nodes: int
    max_edges: int
    max_source_bytes: int
    max_expanded_bytes: int

    def __post_init__(self) -> None:
        hard_ceilings = {
            "max_include_depth": 3,
            "max_dependencies": 64,
            "max_nodes": 512,
            "max_edges": 4096,
            "max_source_bytes": 2 * 1024 * 1024,
            "max_expanded_bytes": 2 * 1024 * 1024,
        }
        for field_name, hard_ceiling in hard_ceilings.items():
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
            if value > hard_ceiling:
                raise ValueError(
                    f"{field_name} exceeds the hard compilation ceiling "
                    f"of {hard_ceiling}"
                )


@dataclass(frozen=True, slots=True)
class WorkflowIncludeAlias:
    """Resolved entry and sink identity for one include instance."""

    entries: tuple[str, ...]
    sinks: tuple[str, ...]
    first_sink: str

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        sinks = tuple(self.sinks)
        if not entries or not sinks or self.first_sink != sinks[0]:
            raise ValueError("include aliases require ordered entries and sinks")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "sinks", sinks)


@dataclass(frozen=True, slots=True)
class ExpandedWorkflowSource:
    """One bounded flattened raw graph compiled under root authority."""

    nodes: tuple[WorkflowSourceNode, ...]
    include_aliases: Mapping[str, WorkflowIncludeAlias]
    dependencies: tuple[WorkflowSourceDocument, ...]
    source_bytes: int
    canonical_definition_bytes: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.source_bytes, bool)
            or not isinstance(self.source_bytes, int)
            or self.source_bytes < 0
        ):
            raise ValueError("source_bytes must be a non-negative integer")
        if not isinstance(self.canonical_definition_bytes, bytes):
            raise ValueError("canonical_definition_bytes must be immutable bytes")
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(
            self,
            "include_aliases",
            MappingProxyType(dict(self.include_aliases)),
        )
        object.__setattr__(self, "dependencies", tuple(self.dependencies))

    @property
    def expanded_bytes(self) -> int:
        return len(self.canonical_definition_bytes)


@dataclass(frozen=True, slots=True)
class WorkflowNodeOrigin:
    """Bounded logical provenance for one future expanded workflow node."""

    include_instance_path: tuple[str, ...]
    package_key: str
    workflow_name: str
    catalog_source: str
    precedence: int
    definition_location: str
    source_index: int
    source_line: int | None
    expanded_node_id: str

    def __post_init__(self) -> None:
        if len(self.include_instance_path) > 3:
            raise ValueError("include_instance_path exceeds the bounded include depth")
        for item in self.include_instance_path:
            _bounded_source_text(
                item,
                "include instance id",
                limit=_WORKFLOW_SOURCE_NAME_MAX_CHARS,
            )
        for field_name in (
            "package_key",
            "workflow_name",
            "catalog_source",
            "expanded_node_id",
        ):
            _bounded_source_text(
                getattr(self, field_name),
                field_name,
                limit=_WORKFLOW_SOURCE_METADATA_MAX_CHARS,
            )
        if (
            isinstance(self.precedence, bool)
            or not isinstance(self.precedence, int)
            or not 0 <= self.precedence <= 2**31 - 1
        ):
            raise ValueError("origin precedence must be a bounded non-negative integer")
        if (
            isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or self.source_index < 0
        ):
            raise ValueError("origin source_index must be a non-negative integer")
        if self.source_line is not None and (
            isinstance(self.source_line, bool)
            or not isinstance(self.source_line, int)
            or self.source_line <= 0
        ):
            raise ValueError("origin source_line must be positive when present")
        object.__setattr__(
            self,
            "include_instance_path",
            tuple(self.include_instance_path),
        )
        object.__setattr__(
            self,
            "definition_location",
            _logical_source_location(
                self.definition_location, "origin definition_location"
            ),
        )


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    nodes: tuple[WorkflowNode, ...]
    options: Mapping[str, Any]
    source_path: Path


class WorkflowLanguageProfile(StrEnum):
    HERMES_LEGACY = "hermes-legacy"
    ARCHON_2026_07 = "archon-2026-07"


@dataclass(frozen=True)
class WorkflowLanguageSelection:
    declared_profile: WorkflowLanguageProfile | None
    effective_profile: WorkflowLanguageProfile


@dataclass(frozen=True)
class WorkflowLanguageMetadata:
    declared_profile: WorkflowLanguageProfile | None
    effective_profile: WorkflowLanguageProfile
    normalizer_version: int
    normalized_definition_digest: str
    structured_outputs: Mapping[str, "WorkflowStructuredOutput"] = field(
        default_factory=lambda: MappingProxyType({})
    )
    node_semantics: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structured_outputs",
            MappingProxyType(dict(self.structured_outputs)),
        )
        object.__setattr__(
            self,
            "node_semantics",
            MappingProxyType({
                node_id: freeze_value(value)
                for node_id, value in self.node_semantics.items()
            }),
        )


@dataclass(frozen=True)
class WorkflowStructuredOutput:
    """One sealed normalized output contract for a workflow node."""

    canonical_schema: Mapping[str, object]
    schema_fingerprint: str
    canonicalization_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "canonical_schema", freeze_value(self.canonical_schema)
        )


class CompatibilityLevel(StrEnum):
    PORTABLE = "portable"
    MAPPED = "mapped"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CompatibilityFinding:
    path: str
    level: CompatibilityLevel
    message: str
    blocking: bool
    code: str = "compatibility"
    severity: str | None = None
    effective_profile: WorkflowLanguageProfile | None = None
    migration: str | None = None

    def __post_init__(self) -> None:
        if self.severity is None:
            object.__setattr__(self, "severity", "error" if self.blocking else "info")


@dataclass(frozen=True)
class WorkflowPackage:
    source_definition: WorkflowDefinition
    definition: WorkflowDefinition
    root: Path
    workflow_path: Path
    sidecar_path: Path | None
    sidecar: Mapping[str, Any]
    source: str
    precedence: int
    language: WorkflowLanguageMetadata
    compatibility_findings: tuple[CompatibilityFinding, ...]
    validation_issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class ApprovalDecision:
    """Durable result of a compare-and-set workflow interaction decision."""

    run_id: str
    node_id: str
    decision: str
    outcome: str
    interaction_id: str
    state_version: int


def _bounded_seconds(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return result


@dataclass(frozen=True)
class RetryPolicy:
    """One combined workflow/provider retry budget."""

    max_attempts: int = 5
    delay_ms: int = 1000
    on_error: str = "transient"

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if isinstance(self.delay_ms, bool) or not 1000 <= self.delay_ms <= 60_000:
            raise ValueError("delay_ms must be between 1000 and 60000")
        if self.on_error not in {"transient", "all"}:
            raise ValueError("on_error must be transient or all")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        default_max_attempts: int = 5,
    ) -> "RetryPolicy":
        options = value or {}
        return cls(
            max_attempts=int(options.get("max_attempts", default_max_attempts)),
            delay_ms=int(options.get("delay_ms", 1000)),
            on_error=str(options.get("on_error", "transient")),
        )


@dataclass(frozen=True, slots=True)
class RetryLedgerGrant:
    """One admitted v3 attempt grant derived from durable prior charge."""

    explicit: bool
    requested_retries: int
    requested_total_attempts: int
    effective_total_attempts: int
    delay_ms: int
    on_error: str
    capped: bool
    retry_consumed: int

    def __post_init__(self) -> None:
        if not isinstance(self.explicit, bool) or not isinstance(self.capped, bool):
            raise ValueError("retry ledger flags must be booleans")
        if (
            isinstance(self.requested_retries, bool)
            or not isinstance(self.requested_retries, int)
            or not 0 <= self.requested_retries <= 5
        ):
            raise ValueError("requested_retries must be between 0 and 5")
        if (
            isinstance(self.requested_total_attempts, bool)
            or not isinstance(self.requested_total_attempts, int)
            or self.requested_total_attempts != self.requested_retries + 1
        ):
            raise ValueError("requested_total_attempts must include the initial attempt")
        if (
            isinstance(self.effective_total_attempts, bool)
            or not isinstance(self.effective_total_attempts, int)
            or not 1 <= self.effective_total_attempts <= 5
        ):
            raise ValueError("effective_total_attempts must be between 1 and 5")
        if (
            isinstance(self.retry_consumed, bool)
            or not isinstance(self.retry_consumed, int)
            or not 0 <= self.retry_consumed <= self.effective_total_attempts
        ):
            raise ValueError("retry_consumed exceeds the sealed total")
        if (
            isinstance(self.delay_ms, bool)
            or not isinstance(self.delay_ms, int)
            or not 1000 <= self.delay_ms <= 60_000
        ):
            raise ValueError("retry delay must be between 1000 and 60000 ms")
        if self.on_error not in {"transient", "all"}:
            raise ValueError("retry on_error must be transient or all")
        if self.capped is not (
            self.effective_total_attempts < self.requested_total_attempts
        ):
            raise ValueError("retry capped evidence is inconsistent")

    @classmethod
    def from_projection(
        cls,
        value: Mapping[str, object],
        *,
        retry_consumed: int,
    ) -> "RetryLedgerGrant":
        return cls(
            explicit=value["explicit"],
            requested_retries=value["requested_retries"],
            requested_total_attempts=value["requested_total_attempts"],
            effective_total_attempts=value["effective_total_attempts"],
            delay_ms=value["delay_ms"],
            on_error=value["on_error"],
            capped=value["capped"],
            retry_consumed=retry_consumed,
        )

    @property
    def remaining_attempts(self) -> int:
        return self.effective_total_attempts - self.retry_consumed

    @property
    def policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.effective_total_attempts,
            delay_ms=self.delay_ms,
            on_error=self.on_error,
        )

    def charge(
        self,
        provider_evidence: object,
        *,
        provider_attempts_exact: bool | None = None,
    ) -> "RetryAttemptCharge":
        """Charge one workflow attempt and validated provider retries once."""
        granted = self.remaining_attempts
        if granted <= 0:
            raise ValueError("retry grant is exhausted")
        if (
            provider_attempts_exact is not False
            and isinstance(provider_evidence, int)
            and not isinstance(provider_evidence, bool)
            and 0 <= provider_evidence < granted
        ):
            additional = provider_evidence
            exact = (
                provider_attempts_exact
                if isinstance(provider_attempts_exact, bool)
                else True
            )
        else:
            additional = granted - 1
            exact = False
        charged = 1 + additional
        consumed = min(
            self.effective_total_attempts,
            self.retry_consumed + charged,
        )
        return RetryAttemptCharge(
            additional_provider_attempts=additional,
            charged_attempts=charged,
            retry_consumed=consumed,
            remaining_attempts=self.effective_total_attempts - consumed,
            provider_attempts_exact=exact,
        )

    def evidence(self, charge: "RetryAttemptCharge") -> dict[str, object]:
        return {
            "requested_retries": self.requested_retries,
            "requested_total_attempts": self.requested_total_attempts,
            "effective_total_attempts": self.effective_total_attempts,
            "retry_consumed": charge.retry_consumed,
            "remaining_attempts": charge.remaining_attempts,
            "additional_provider_attempts": (
                charge.additional_provider_attempts
            ),
            "provider_attempts_exact": charge.provider_attempts_exact,
            "capped": self.capped,
        }


@dataclass(frozen=True, slots=True)
class RetryAttemptCharge:
    """One validated durable update to a sealed combined retry ledger."""

    additional_provider_attempts: int
    charged_attempts: int
    retry_consumed: int
    remaining_attempts: int
    provider_attempts_exact: bool


@dataclass
class DeadlineBudget:
    """Absolute monotonic deadlines shared by a node and its descendants."""

    wall_deadline: float
    idle_seconds: float
    provider_seconds: float
    last_semantic_progress: float
    last_heartbeat: float

    @classmethod
    def create(
        cls,
        *,
        now: float,
        wall_seconds: float,
        idle_seconds: float,
        provider_seconds: float,
    ) -> "DeadlineBudget":
        wall = _bounded_seconds(wall_seconds, "wall_seconds")
        idle = _bounded_seconds(idle_seconds, "idle_seconds")
        provider = _bounded_seconds(provider_seconds, "provider_seconds")
        if not math.isfinite(float(now)):
            raise ValueError("now must be finite")
        return cls(float(now) + wall, idle, provider, float(now), float(now))

    @classmethod
    def from_attempt_semantics(
        cls,
        *,
        now: float,
        attempt_wall_seconds: float,
        idle_seconds: float,
        provider_seconds: float,
    ) -> "DeadlineBudget":
        """Create one attempt budget from already-normalized effective values."""
        wall = _bounded_seconds(attempt_wall_seconds, "attempt_wall_seconds")
        return cls.create(
            now=now,
            wall_seconds=wall,
            idle_seconds=idle_seconds,
            provider_seconds=provider_seconds,
        )

    def child(
        self,
        *,
        now: float,
        requested_wall_seconds: float,
        workflow_cap_seconds: float,
        idle_seconds: float,
        provider_seconds: float,
    ) -> "DeadlineBudget":
        requested = _bounded_seconds(requested_wall_seconds, "requested_wall_seconds")
        cap = _bounded_seconds(workflow_cap_seconds, "workflow_cap_seconds")
        idle = min(self.idle_seconds, _bounded_seconds(idle_seconds, "idle_seconds"))
        provider = min(
            self.provider_seconds,
            _bounded_seconds(provider_seconds, "provider_seconds"),
        )
        wall_deadline = min(
            self.wall_deadline, float(now) + requested, float(now) + cap
        )
        if wall_deadline <= now:
            raise ValueError("parent deadline is exhausted")
        return DeadlineBudget(wall_deadline, idle, provider, float(now), float(now))

    def remaining_wall(self, now: float) -> float:
        return max(0.0, self.wall_deadline - float(now))

    def provider_deadline(self, *, now: float) -> float:
        return min(self.wall_deadline, float(now) + self.provider_seconds)

    def semantic_progress(self, now: float) -> None:
        self.last_semantic_progress = float(now)

    def heartbeat(self, now: float) -> None:
        self.last_heartbeat = float(now)

    def idle_expired(self, now: float) -> bool:
        return float(now) - self.last_semantic_progress >= self.idle_seconds

    def wall_expired(self, now: float) -> bool:
        return float(now) >= self.wall_deadline


@dataclass(frozen=True, slots=True)
class RunExecutionLimits:
    """Immutable limits that may vary between workflow runs."""

    max_parallel_nodes: int = 4
    max_total_workers: int = 4
    ai_idle_timeout_seconds: float = 300.0
    ai_wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0
    combined_retries: int = 5
    subprocess_timeout_seconds: float = 120.0
    process_tree_rss_bytes: int = 2048 * 1024 * 1024
    process_tree_cpu_seconds: float = 900.0
    max_descendants: int = 32
    cooperative_shutdown_seconds: float = 5.0
    term_grace_seconds: float = 5.0
    kill_reap_grace_seconds: float = 2.0

    @classmethod
    def resolve(
        cls,
        profile: "WorkflowRuntimeConfig",
        *,
        sidecar_limits: Mapping[str, Any] | None = None,
        sidecar_resources: Mapping[str, Any] | None = None,
    ) -> "RunExecutionLimits":
        """Tighten a frozen profile with one immutable run sidecar."""
        if not isinstance(profile, WorkflowRuntimeConfig):
            raise TypeError("profile must be WorkflowRuntimeConfig")
        tightened = WorkflowRuntimeConfig.from_mapping(
            asdict(profile),
            sidecar_limits=sidecar_limits,
            sidecar_resources=sidecar_resources,
        )
        return cls(**{
            name: getattr(tightened, name) for name in cls.__dataclass_fields__
        })


@dataclass(frozen=True)
class WorkflowRuntimeConfig:
    """Resolved profile lifecycle limits, optionally tightened by a sidecar."""

    max_parallel_nodes: int = 4
    max_total_workers: int = 4
    max_executing_runs: int = 4
    max_queued_runs: int = 100
    max_paused_runs: int = 100
    max_nonterminal_runs: int = 200
    max_start_requests_per_minute: int = 60
    ai_idle_timeout_seconds: float = 300.0
    ai_wall_timeout_seconds: float = 1800.0
    provider_request_timeout_seconds: float = 300.0
    subprocess_timeout_seconds: float = 120.0
    heartbeat_seconds: float = 5.0
    lease_seconds: float = 30.0
    coordinator_web_election_grace_seconds: float = 3.0
    runnable_stall_seconds: float = 60.0
    semantic_stall_seconds: float = 300.0
    cooperative_shutdown_seconds: float = 5.0
    term_grace_seconds: float = 5.0
    kill_reap_grace_seconds: float = 2.0
    combined_retries: int = 5
    process_tree_rss_bytes: int = 2048 * 1024 * 1024
    process_tree_cpu_seconds: float = 900.0
    max_descendants: int = 32

    def __post_init__(self) -> None:
        integer_bounds = {
            "max_parallel_nodes": (1, 64),
            "max_total_workers": (1, 256),
            "max_executing_runs": (1, 256),
            "max_queued_runs": (1, 100_000),
            "max_paused_runs": (1, 100_000),
            "max_nonterminal_runs": (1, 200_000),
            "max_start_requests_per_minute": (1, 100_000),
            "combined_retries": (1, 5),
            "process_tree_rss_bytes": (16 * 1024 * 1024, 1024**4),
            "max_descendants": (0, 4096),
        }
        for name, (minimum, maximum) in integer_bounds.items():
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        for name in (
            "ai_idle_timeout_seconds",
            "ai_wall_timeout_seconds",
            "provider_request_timeout_seconds",
            "subprocess_timeout_seconds",
            "heartbeat_seconds",
            "lease_seconds",
            "coordinator_web_election_grace_seconds",
            "runnable_stall_seconds",
            "semantic_stall_seconds",
            "cooperative_shutdown_seconds",
            "term_grace_seconds",
            "kill_reap_grace_seconds",
            "process_tree_cpu_seconds",
        ):
            _bounded_seconds(getattr(self, name), name)
        if self.lease_seconds < 3 * self.heartbeat_seconds:
            raise ValueError("lease_seconds must be at least three heartbeats")
        if self.ai_idle_timeout_seconds > self.ai_wall_timeout_seconds:
            raise ValueError("AI idle timeout cannot exceed AI wall timeout")
        if self.provider_request_timeout_seconds > self.ai_wall_timeout_seconds:
            raise ValueError("provider timeout cannot exceed AI wall timeout")

    @classmethod
    def from_mapping(
        cls,
        profile: Mapping[str, Any] | None = None,
        *,
        sidecar_limits: Mapping[str, Any] | None = None,
        sidecar_resources: Mapping[str, Any] | None = None,
    ) -> "WorkflowRuntimeConfig":
        values = dict(profile or {})
        resource_names = {
            "process_tree_rss_bytes",
            "process_tree_cpu_seconds",
            "max_descendants",
        }
        resources = values.pop("resource_limits", {})
        if isinstance(resources, Mapping):
            unknown_resources = set(resources) - resource_names
            if unknown_resources:
                raise ValueError(
                    f"unknown workflow resource limit: {sorted(unknown_resources)[0]}"
                )
            values.update({
                name: resources[name] for name in resource_names if name in resources
            })
        elif resources:
            raise ValueError("resource_limits must contain a mapping")
        known = set(cls.__dataclass_fields__)
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown workflow lifecycle limit: {sorted(unknown)[0]}")
        resolved = cls(**values)
        tightened = dict(values)
        sidecar = dict(sidecar_limits or {})
        sidecar_resources = dict(sidecar_resources or {})
        unknown_sidecar_resources = set(sidecar_resources) - resource_names
        if unknown_sidecar_resources:
            raise ValueError(
                "unknown workflow resource limit: "
                f"{sorted(unknown_sidecar_resources)[0]}"
            )
        sidecar.update({
            name: sidecar_resources[name]
            for name in resource_names
            if name in sidecar_resources
        })
        for name, value in sidecar.items():
            if name not in known:
                raise ValueError(f"unknown workflow lifecycle limit: {name}")
            current = getattr(resolved, name)
            if isinstance(current, int) and not isinstance(current, bool):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{name} must be an integer")
            elif isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
            tightened[name] = min(current, value)
        wall = tightened.get(
            "ai_wall_timeout_seconds", resolved.ai_wall_timeout_seconds
        )
        tightened["ai_idle_timeout_seconds"] = min(
            tightened.get("ai_idle_timeout_seconds", resolved.ai_idle_timeout_seconds),
            wall,
        )
        tightened["provider_request_timeout_seconds"] = min(
            tightened.get(
                "provider_request_timeout_seconds",
                resolved.provider_request_timeout_seconds,
            ),
            wall,
        )
        return cls(**tightened)
