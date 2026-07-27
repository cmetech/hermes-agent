"""Authenticated, bounded Desktop REST adapter for workflow evidence and state."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import re
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, Mapping

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from hermes_constants import get_hermes_home
from plugins.workflow.actions import MUTATION_ACTIONS, mutation_is_valid
from plugins.workflow.compat import (
    WORKFLOW_COMPATIBILITY_FINDINGS_MAX,
    WORKFLOW_COMPATIBILITY_PAYLOAD_MAX_BYTES,
    derive_compatibility_report_state,
)
from plugins.workflow.evidence import EVIDENCE_KINDS, EvidenceReader
from plugins.workflow.language import WorkflowLanguageCompatibilityError
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowApiRuntime,
    WorkflowRetentionPolicy,
)
from plugins.workflow.sanitize import public_run_projection, sanitize_projection
from plugins.workflow.store import JournalRecoveryError, RunStore


_CURSOR_SECRET = secrets.token_bytes(32)
_RUNTIME: WorkflowApiRuntime | None = None
_RUNTIME_LOCK = threading.Lock()
_WORKFLOW_RESPONSE_TEXT_MAX = 16_384
WORKFLOW_COMPATIBILITY_UNKNOWN_PATH = "<unknown-path>"
_WORKFLOW_COMPATIBILITY_SENTINEL_MESSAGE = re.compile(
    r"^Compatibility findings truncated: ([1-9][0-9]{0,8}) omitted; "
    r"aggregate level (portable|mapped|unsupported)$"
)


def _runtime() -> WorkflowApiRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        with _RUNTIME_LOCK:
            if _RUNTIME is None:
                _RUNTIME = WorkflowApiRuntime(
                    WorkflowApiLimits.from_profile(get_hermes_home())
                )
    return _RUNTIME


def _close_runtime() -> None:
    global _RUNTIME
    if _RUNTIME is not None:
        _RUNTIME.close()
        _RUNTIME = None


@asynccontextmanager
async def _router_lifespan(_app):
    try:
        yield
    finally:
        _close_runtime()


router = APIRouter(lifespan=_router_lifespan)


def _schedule_now_utc() -> datetime:
    return datetime.now(timezone.utc)


_ALL_WORKFLOW_CAPABILITIES = frozenset({"read", "write", "delivery", "admin"})


@dataclass(frozen=True, slots=True)
class WorkflowAuthority:
    principal: str
    scope: str | None
    unrestricted: bool
    capabilities: frozenset[str]
    delivery_destination: str | None = "desktop"
    source_instance: str = "api:local-admin"
    channel: str = "api:local-admin"
    assurance: Literal["verified_adapter", "local_admin_claim"] = "local_admin_claim"
    trigger_source: Literal["desktop", "api"] = "api"
    return_route: str | None = None

    @property
    def cursor_scope(self) -> str:
        return self.scope or self.principal

    @property
    def authority_binding(self) -> str:
        return json.dumps(
            {
                "principal": self.principal,
                "scope": self.scope,
                "unrestricted": self.unrestricted,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def idempotency_namespace(self) -> str:
        return f"api:{self.principal}"

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise HTTPException(
                status_code=403,
                detail={"code": f"workflow_{capability}_required"},
            )

    def require_delivery_destination(self, destination: str) -> None:
        self.require("delivery")
        if not self.delivery_destination or destination != self.delivery_destination:
            raise HTTPException(
                status_code=403,
                detail={"code": "workflow_delivery_scope_mismatch"},
            )


def _token_capabilities(scopes: tuple[str, ...]) -> frozenset[str]:
    if "workflow:admin" in scopes:
        return _ALL_WORKFLOW_CAPABILITIES
    capabilities: set[str] = set()
    if "workflow:read" in scopes:
        capabilities.add("read")
    if "workflow:write" in scopes:
        capabilities.update({"read", "write"})
    if "workflow:delivery" in scopes:
        capabilities.update({"read", "delivery"})
    return frozenset(capabilities)


def _verified_operator(
    request: Request, requested_scope: str | None
) -> WorkflowAuthority:
    session = getattr(request.state, "session", None)
    token = getattr(request.state, "token_principal", None)
    if session is not None:
        provider = getattr(session, "provider", "unknown")
        org = getattr(session, "org_id", "") or "personal"
        maximum = f"dashboard:{provider}:{org}:{getattr(session, 'user_id', 'unknown')}"
        if requested_scope and not (
            requested_scope == maximum or requested_scope.startswith(maximum + ":")
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "operator_scope_not_authorized"},
            )
        return WorkflowAuthority(
            principal=maximum,
            scope=requested_scope or maximum,
            unrestricted=False,
            capabilities=frozenset({"read", "write", "delivery"}),
            source_instance=f"api:session:{provider}",
            channel=f"api:{provider}",
            assurance="verified_adapter",
            trigger_source="desktop",
        )
    if token is not None and getattr(request.state, "token_authenticated", False):
        provider = getattr(token, "provider", "unknown")
        maximum = f"service:{provider}:{getattr(token, 'principal', 'unknown')}"
        scopes = tuple(getattr(token, "scopes", ()) or ())
        if requested_scope and not (
            requested_scope == maximum or requested_scope.startswith(maximum + ":")
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "operator_scope_not_authorized"},
            )
        return WorkflowAuthority(
            principal=maximum,
            scope=requested_scope or maximum,
            unrestricted=False,
            capabilities=_token_capabilities(scopes),
            source_instance=f"api:token:{provider}",
            channel=f"api:{provider}",
            assurance="verified_adapter",
        )
    if getattr(request.state, "local_admin_authenticated", False):
        return WorkflowAuthority(
            principal="profile-local-dashboard",
            scope=requested_scope,
            unrestricted=requested_scope is None,
            capabilities=_ALL_WORKFLOW_CAPABILITIES,
            source_instance="api:local-admin",
            channel="api:local-admin",
            assurance="local_admin_claim",
            trigger_source="desktop",
        )
    raise HTTPException(status_code=401, detail={"code": "authentication_required"})


class WorkflowCatalogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(..., min_length=1, max_length=64)
    required: bool
    max_bytes: int | None = Field(None, gt=0, exclude_if=lambda value: value is None)


class WorkflowCatalogInputSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool
    reason: Literal[
        "parameterless",
        "flat_inputs",
        "unsupported_input_type",
        "unsupported_input_shape",
    ]


class WorkflowCatalogRunSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool
    reason: Literal[
        "supported",
        "unsupported_inputs",
        "schedule_required",
        "showcase_cli_required",
    ]


WorkflowLanguageProfile = Literal["hermes-legacy", "archon-2026-07"]
WorkflowCompatibilityLevel = Literal["portable", "mapped", "unsupported"]


class WorkflowCatalogLanguageStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_profile: WorkflowLanguageProfile
    legacy: StrictBool

    @model_validator(mode="after")
    def require_consistent_legacy_status(self):
        if self.legacy != (self.effective_profile == "hermes-legacy"):
            raise ValueError("legacy must match effective_profile")
        return self


class WorkflowDetailLanguageStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared_profile: WorkflowLanguageProfile | None
    effective_profile: WorkflowLanguageProfile
    legacy: StrictBool
    normalizer_version: StrictInt = Field(..., ge=1, le=1)
    normalized_definition_digest: str = Field(
        ..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_consistent_profile_status(self):
        if self.legacy != (self.effective_profile == "hermes-legacy"):
            raise ValueError("legacy must match effective_profile")
        if self.declared_profile is None:
            if self.effective_profile != "hermes-legacy":
                raise ValueError("an undeclared profile must resolve to hermes-legacy")
        elif self.declared_profile != self.effective_profile:
            raise ValueError("declared_profile must match effective_profile")
        return self


class WorkflowCompatibilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: WorkflowCompatibilityLevel
    runnable: StrictBool


class WorkflowCompatibilityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=_WORKFLOW_RESPONSE_TEXT_MAX)
    level: WorkflowCompatibilityLevel
    message: str = Field(..., min_length=1, max_length=_WORKFLOW_RESPONSE_TEXT_MAX)
    blocking: StrictBool
    code: str = Field(..., min_length=1, max_length=_WORKFLOW_RESPONSE_TEXT_MAX)


class WorkflowCompatibilityFull(WorkflowCompatibilitySummary):
    model_config = ConfigDict(extra="forbid")

    findings_truncated: StrictBool
    finding_count: StrictInt = Field(..., ge=0, le=1_000_000_510)
    findings: list[WorkflowCompatibilityFinding] = Field(
        ..., max_length=WORKFLOW_COMPATIBILITY_FINDINGS_MAX
    )

    @model_validator(mode="before")
    @classmethod
    def accept_complete_legacy_projection(cls, value):
        if not isinstance(value, Mapping):
            return value
        if "findings_truncated" in value or "finding_count" in value:
            return value
        findings = value.get("findings")
        if not isinstance(findings, list):
            return value
        if any(
            isinstance(finding, Mapping)
            and finding.get("code") == "compatibility_findings_truncated"
            and finding.get("path") == "compatibility.findings"
            for finding in findings
        ):
            return value
        projected = dict(value)
        projected["findings_truncated"] = False
        projected["finding_count"] = len(findings)
        return projected

    @model_validator(mode="after")
    def require_authoritative_report_state(self):
        sentinel_indexes = [
            index
            for index, finding in enumerate(self.findings)
            if (
                finding.code == "compatibility_findings_truncated"
                and finding.path == "compatibility.findings"
            )
        ]
        expected_truncated = sentinel_indexes == [len(self.findings) - 1]
        if self.findings_truncated != expected_truncated:
            raise ValueError(
                "findings_truncated must match one final truncation sentinel"
            )
        if self.findings_truncated:
            if len(self.findings) != WORKFLOW_COMPATIBILITY_FINDINGS_MAX:
                raise ValueError(
                    "truncated compatibility must retain exactly the fixed bound"
                )
            sentinel = self.findings[-1]
            match = _WORKFLOW_COMPATIBILITY_SENTINEL_MESSAGE.fullmatch(sentinel.message)
            if (
                match is None
                or match.group(2) != sentinel.level
                or sentinel.level == "portable"
                or (sentinel.blocking and sentinel.level != "unsupported")
            ):
                raise ValueError(
                    "truncation sentinel must carry bounded aggregate state"
                )
            omitted = int(match.group(1))
            if self.finding_count != len(self.findings) - 1 + omitted:
                raise ValueError("finding_count must include exactly omitted findings")
        elif self.finding_count != len(self.findings):
            raise ValueError("finding_count must match complete findings")
        expected_level, expected_runnable = derive_compatibility_report_state(
            self.findings
        )
        if self.level != expected_level or self.runnable != expected_runnable:
            raise ValueError("compatibility level and runnable must match findings")
        serialized = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) >= WORKFLOW_COMPATIBILITY_PAYLOAD_MAX_BYTES:
            raise ValueError("compatibility payload must remain below 1 MiB")
        return self


def _sanitize_compatibility_finding_projection(
    finding: Mapping[str, object],
) -> dict[str, object]:
    """Sanitize one closed finding while preserving its nonempty API path."""
    raw_path = finding.get("path")
    candidate = dict(finding)
    if isinstance(raw_path, str):
        sanitized_path = sanitize_projection(raw_path, key="path")
        if sanitized_path == "":
            raw_message = candidate.get("message")
            if isinstance(raw_message, str):
                if raw_path:
                    candidate["message"] = raw_message.replace(
                        raw_path, WORKFLOW_COMPATIBILITY_UNKNOWN_PATH
                    )
                elif raw_message.endswith(": "):
                    candidate["message"] = (
                        raw_message + WORKFLOW_COMPATIBILITY_UNKNOWN_PATH
                    )
            candidate["path"] = WORKFLOW_COMPATIBILITY_UNKNOWN_PATH
    projected = sanitize_projection(candidate)
    assert isinstance(projected, dict)
    return projected


def _sanitize_full_compatibility_projection(
    compatibility: Mapping[str, object],
) -> dict[str, object]:
    """Preserve one complete producer-bounded report across generic clipping."""
    findings = compatibility.get("findings")
    if not isinstance(findings, list):
        raise TypeError("full compatibility findings must be a list")
    projected_findings: list[dict[str, object]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise TypeError("full compatibility findings must contain mappings")
        projected_findings.append(_sanitize_compatibility_finding_projection(finding))
    return {
        "level": compatibility["level"],
        "runnable": compatibility["runnable"],
        "findings_truncated": compatibility["findings_truncated"],
        "finding_count": compatibility["finding_count"],
        "findings": projected_findings,
    }


def _sanitize_catalog_item_projection(
    item: Mapping[str, object],
) -> dict[str, object]:
    compatibility = item.get("compatibility")
    full_compatibility = None
    if isinstance(compatibility, Mapping) and isinstance(
        compatibility.get("findings"), list
    ):
        full_compatibility = _sanitize_full_compatibility_projection(compatibility)
    projected = sanitize_projection(item)
    assert isinstance(projected, dict)
    if full_compatibility is not None:
        projected["compatibility"] = full_compatibility
    return projected


class WorkflowCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(..., min_length=1, max_length=32)
    description: str = Field(..., max_length=16_400)
    requires_ai: bool
    source: Literal["project", "profile", "showcase"]
    precedence: Literal[1, 2, 3]
    trust_state: Literal["trusted", "untrusted", "verified_bundled"]
    inputs: list[WorkflowCatalogInput] = Field(..., max_length=64)
    supported_inputs: WorkflowCatalogInputSupport
    run_support: WorkflowCatalogRunSupport
    language: WorkflowCatalogLanguageStatus
    compatibility: WorkflowCompatibilitySummary | WorkflowCompatibilityFull | None = (
        None
    )

    @model_validator(mode="after")
    def require_source_compatibility_projection(self):
        if self.compatibility is None:
            return self
        if self.source == "showcase":
            if not isinstance(self.compatibility, WorkflowCompatibilityFull):
                raise ValueError("showcase compatibility must include findings")
        elif isinstance(self.compatibility, WorkflowCompatibilityFull):
            raise ValueError("project and profile compatibility must be summary-only")
        return self


class WorkflowCatalogErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    error: Literal["invalid_definition", "catalog_capacity"]


class WorkflowCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkflowCatalogEntry | WorkflowCatalogErrorEntry] = Field(
        ..., max_length=500
    )
    truncated: bool


class WorkflowTopologyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., max_length=16_384)
    mermaid: str | None = Field(None, max_length=65_536)
    warnings: list[str] = Field(..., max_length=8)
    omitted: str | None = Field(None, max_length=256)


class WorkflowCoordinatorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    healthy: bool
    status: str = Field(..., max_length=64)
    reason: str = Field(..., max_length=128)


class WorkflowDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(..., min_length=1, max_length=32)
    description: str = Field(..., max_length=16_384)
    requires_ai: bool
    source: str = Field(..., min_length=1, max_length=64)
    precedence: int
    trust_state: Literal["trusted", "untrusted", "verified_bundled"]
    inputs: list[WorkflowCatalogInput] = Field(..., max_length=64)
    supported_inputs: WorkflowCatalogInputSupport
    run_support: WorkflowCatalogRunSupport
    language: WorkflowDetailLanguageStatus
    risk_summary: dict[str, object]
    compatibility: WorkflowCompatibilityFull
    coordinator: WorkflowCoordinatorResponse
    topology: WorkflowTopologyResponse
    definition: dict[str, object]


@router.get(
    "/workflows",
    response_model=WorkflowCatalogResponse,
    response_model_exclude_none=True,
)
def list_workflows(
    request: Request,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    from plugins.workflow.catalog_api import (
        WorkflowCatalogCapacityError,
        WorkflowCatalogTrustUnavailableError,
        WorkflowCatalogUnavailableError,
        build_workflow_catalog,
    )

    try:
        items, truncated = build_workflow_catalog(
            hermes_home=get_hermes_home(), workdir=Path.cwd()
        )
    except WorkflowCatalogCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_catalog_capacity", "retryable": True},
        ) from exc
    except WorkflowCatalogUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_catalog_unavailable", "retryable": True},
        ) from exc
    except WorkflowCatalogTrustUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_trust_unavailable", "retryable": True},
        ) from exc
    return {
        "items": [_sanitize_catalog_item_projection(item) for item in items],
        "truncated": truncated,
    }


@router.get("/workflows/{name}", response_model=WorkflowDetailResponse)
def workflow_detail(
    name: str,
    request: Request,
    catalog_source: Literal["project", "profile", "showcase"] | None = Query(None),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    from plugins.workflow.catalog_api import (
        WorkflowCatalogCapacityError,
        WorkflowCatalogInvalidDefinitionError,
        WorkflowCatalogTrustUnavailableError,
        WorkflowCatalogUnavailableError,
        WorkflowDetailNotFoundError,
        WorkflowShowcaseVerificationError,
        build_workflow_detail,
    )

    try:
        detail = build_workflow_detail(
            name,
            hermes_home=get_hermes_home(),
            workdir=Path.cwd(),
            catalog_source=catalog_source,
        )
    except WorkflowDetailNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "workflow_not_found"}
        ) from exc
    except WorkflowCatalogCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_catalog_capacity", "retryable": True},
        ) from exc
    except WorkflowCatalogInvalidDefinitionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "workflow_invalid_definition",
                "retryable": False,
            },
        ) from exc
    except WorkflowCatalogUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_catalog_unavailable", "retryable": True},
        ) from exc
    except WorkflowCatalogTrustUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_trust_unavailable", "retryable": True},
        ) from exc
    except WorkflowShowcaseVerificationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workflow_showcase_verification_failed",
                "retryable": False,
            },
        ) from exc
    compatibility = detail["compatibility"]
    assert isinstance(compatibility, Mapping)
    full_compatibility = _sanitize_full_compatibility_projection(compatibility)
    sanitized = sanitize_projection(detail)
    assert isinstance(sanitized, dict)
    # build_workflow_detail already supplies the shared semantically redacted,
    # byte-bounded definition; generic list limits must not silently clip it.
    sanitized["definition"] = detail["definition"]
    # Preserve the complete producer report instead of applying the generic
    # sanitizer's unrelated 200-item list limit. Sanitize each closed finding
    # independently so path/text redaction remains in force.
    sanitized["compatibility"] = full_compatibility
    return sanitized


@contextmanager
def _store_lease() -> Iterator[RunStore]:
    try:
        with _runtime().stores.lease(get_hermes_home()) as store:
            yield store
    except StoreRegistryCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "workflow_store_capacity", "retryable": True},
        ) from exc


def _scope_key(scope: str) -> str:
    return hashlib.sha256(scope.encode()).hexdigest()


def _encode_cursor(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_CURSOR_SECRET, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def _decode_cursor(value: str, *, kind: str, scope: str) -> dict[str, object]:
    try:
        combined = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        raw, signature = combined[:-32], combined[-32:]
        expected = hmac.new(_CURSOR_SECRET, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if (
            payload.get("v") != 1
            or payload.get("kind") != kind
            or payload.get("scope") != _scope_key(scope)
        ):
            raise ValueError
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=410,
            detail={"code": "cursor_expired", "cursor_reset": True},
        ) from exc


def _authorized_runs(
    store: RunStore,
    operator: WorkflowAuthority,
    *,
    view: str = "all",
    limit: int = 200,
    after: tuple[str, str] | None = None,
    now: datetime | None = None,
):
    retention = WorkflowRetentionPolicy.from_profile(get_hermes_home())
    return store.list_runs(
        operator_scope=None if operator.unrestricted else operator.scope,
        limit=limit,
        view=view,
        after=after,
        now=now,
        terminal_board_days=retention.terminal_board_days,
    )


def _load_authorized(
    store: RunStore,
    run_id: str,
    operator: WorkflowAuthority,
    *,
    now: datetime | None = None,
):
    try:
        return store.get_run_status(
            run_id,
            operator_scope=None if operator.unrestricted else operator.scope,
            **({"now": now} if now is not None else {}),
        )
    except JournalRecoveryError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "run_evidence_uncorroborated"},
        ) from exc
    except (KeyError, OSError) as exc:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc


def _cleanup_duration(value: str):
    from datetime import timedelta

    match = re.fullmatch(r"(\d+)([dhm])", value)
    if not match:
        raise HTTPException(
            status_code=422, detail={"code": "cleanup_duration_invalid"}
        )
    amount = int(match.group(1))
    return {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[match.group(2)]


def _notification_destination(store: RunStore, notification_id: str) -> str:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT destination FROM workflow_notification_outbox "
            "WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "notification_not_found"}
        )
    return str(row["destination"])


def _public_cleanup_preview(payload: Mapping[str, object]) -> dict[str, object]:
    candidates = []
    for raw in payload.get("candidates", []):
        if not isinstance(raw, Mapping):
            continue
        candidates.append(
            {
                key: raw.get(key)
                for key in (
                    "run_id",
                    "status",
                    "updated_at",
                    "state_version",
                    "event_sequence",
                    "projection_sha256",
                    "journal_sha256",
                    "files",
                    "bytes",
                    "evidence_types",
                    "blocked_reasons",
                )
            }
        )
    return {
        "execute": False,
        "run_ids": payload.get("run_ids", []),
        "candidates": sanitize_projection(candidates),
        "files": payload.get("files", 0),
        "bytes": payload.get("bytes", 0),
        "index_integrity": payload.get("index_integrity"),
        "blocked_reasons": payload.get("blocked_reasons", []),
        "notification_dependencies": sanitize_projection(
            payload.get("notification_dependencies", {})
        ),
        # This one-time capability is intentionally returned only across the
        # authenticated high-trust boundary. Generic sanitization treats all
        # token-named fields as secrets and would make execution impossible.
        "confirmation_token": payload.get("confirmation_token"),
        "confirmation_expires_at": payload.get("confirmation_expires_at"),
    }


@router.get("/cleanup/preview")
def cleanup_preview(
    request: Request,
    older_than: str = Query("7d"),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("admin")
    with _store_lease() as store:
        return _public_cleanup_preview(
            store.cleanup_runs(
                older_than=_cleanup_duration(older_than),
                operator_scope=None if operator.unrestricted else operator.scope,
                authority_binding=operator.authority_binding,
            )
        )


class CleanupExecutionRequest(BaseModel):
    older_than: str = "7d"
    confirmation_token: str = Field(..., min_length=1, max_length=256)


@router.post("/cleanup/execute")
def cleanup_execute(
    request_context: Request,
    request: CleanupExecutionRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    operator.require("admin")
    _cleanup_duration(request.older_than)
    try:
        with _store_lease() as store:
            result = store.cleanup_runs(
                execute=True,
                confirmation_token=request.confirmation_token,
                authority_binding=operator.authority_binding,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "cleanup_confirmation_invalid"}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "cleanup_preview_changed"}
        ) from exc
    return {
        "execute": True,
        "run_ids": result["run_ids"],
        "files": result["files"],
        "bytes": result["bytes"],
    }


@router.get("/cleanup/history")
def cleanup_history(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("admin")
    with _store_lease() as store:
        rows = store.list_cleanup_history(limit=limit)
    return {
        "schema_version": 1,
        "items": sanitize_projection(
            [
                {
                    "sequence": row["sequence"],
                    "timestamp": row["timestamp"],
                    "run_id": row["run_id"],
                    "files": row["files"],
                    "bytes": row["bytes"],
                    "outcome": row["outcome"],
                    "payload": row["payload"],
                }
                for row in rows
            ]
        ),
    }


@router.get("/notifications/lease")
def lease_notifications(
    request: Request,
    client_id: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(20, ge=1, le=100),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require_delivery_destination("desktop")
    with _store_lease() as store:
        items = NotificationOutbox(store).lease(
            destination="desktop",
            owner_id=client_id,
            lease_seconds=30,
            limit=limit,
        )
    return {"schema_version": 1, "items": sanitize_projection(items)}


class NotificationReceiptRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=256)
    error: str = Field("", max_length=512)


class NotificationPruneRequest(BaseModel):
    older_than: str = "30d"
    limit: int = Field(200, ge=1, le=200)


@router.post("/notifications/prune")
def prune_notifications(
    request_context: Request,
    request: NotificationPruneRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    operator.require("admin")
    with _store_lease() as store:
        pruned = NotificationOutbox(store).prune_deliveries(
            older_than=_cleanup_duration(request.older_than),
            authority_scope=operator.cursor_scope,
            limit=request.limit,
        )
    return {"schema_version": 1, "pruned": pruned}


@router.post("/notifications/{notification_id}/retry")
def retry_dead_notification(
    request_context: Request,
    notification_id: str,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    operator.require("admin")
    with _store_lease() as store:
        _notification_destination(store, notification_id)
        applied = NotificationOutbox(store).retry_dead(
            notification_id,
            operator.cursor_scope,
        )
    if not applied:
        raise HTTPException(
            status_code=409, detail={"code": "notification_not_dead"}
        )
    return {"schema_version": 1, "outcome": "requeued"}


@router.post("/notifications/{notification_id}/ack")
def acknowledge_notification(
    request_context: Request,
    notification_id: str,
    request: NotificationReceiptRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    with _store_lease() as store:
        operator.require_delivery_destination(
            _notification_destination(store, notification_id)
        )
        applied = NotificationOutbox(store).ack(
            notification_id, owner_id=request.client_id
        )
    if not applied:
        raise HTTPException(
            status_code=409, detail={"code": "notification_lease_not_owned"}
        )
    return {"schema_version": 1, "outcome": "delivered"}


@router.post("/notifications/{notification_id}/fail")
def fail_notification(
    request_context: Request,
    notification_id: str,
    request: NotificationReceiptRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    with _store_lease() as store:
        operator.require_delivery_destination(
            _notification_destination(store, notification_id)
        )
        applied = NotificationOutbox(store).fail(
            notification_id,
            owner_id=request.client_id,
            error=request.error or "projection_failed",
        )
    if not applied:
        raise HTTPException(
            status_code=409, detail={"code": "notification_lease_not_owned"}
        )
    return {"schema_version": 1, "outcome": "retry_scheduled"}


@router.post("/notifications/{notification_id}/dismiss")
def dismiss_notification_projection(
    request_context: Request,
    notification_id: str,
    request: NotificationReceiptRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    with _store_lease() as store:
        operator.require_delivery_destination(
            _notification_destination(store, notification_id)
        )
        applied = NotificationOutbox(store).dismiss(
            notification_id, owner_id=request.client_id
        )
    if not applied:
        raise HTTPException(
            status_code=409, detail={"code": "notification_lease_not_owned"}
        )
    return {"schema_version": 1, "outcome": "presentation_dismissed"}


@router.get("/runs")
def list_runs(
    request: Request,
    limit: int = Query(100, ge=1, le=100),
    cursor: str | None = None,
    view: str = Query("board", pattern="^(board|history|archive|all)$"),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    retention = WorkflowRetentionPolicy.from_profile(get_hermes_home())
    cursor_scope = (
        f"{operator.cursor_scope}:{view}:{retention.terminal_board_days}"
    )
    after = None
    if cursor:
        payload = _decode_cursor(cursor, kind="runs", scope=cursor_scope)
        keyset = payload.get("keyset")
        observed_value = payload.get("observed_at")
        try:
            if (
                not isinstance(keyset, list)
                or len(keyset) != 2
                or not all(isinstance(value, str) and value for value in keyset)
                or not isinstance(observed_value, str)
            ):
                raise ValueError
            observed_at = datetime.fromisoformat(observed_value)
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError
            observed_at = observed_at.astimezone(timezone.utc)
            after = (keyset[0], keyset[1])
        except ValueError as exc:
            raise HTTPException(
                status_code=410,
                detail={"code": "cursor_expired", "cursor_reset": True},
            ) from exc
    else:
        observed_at = _schedule_now_utc()
    with _store_lease() as store:
        runs = list(
            _authorized_runs(
                store,
                operator,
                view=view,
                limit=limit + 1,
                after=after,
                now=observed_at,
            )
        )
    page = runs[:limit]
    next_cursor = None
    if len(runs) > limit:
        last = page[-1]
        next_cursor = _encode_cursor({
            "v": 1,
            "kind": "runs",
            "scope": _scope_key(cursor_scope),
            "observed_at": observed_at.isoformat(),
            "filter": {
                "view": view,
                "terminal_board_days": retention.terminal_board_days,
            },
            "keyset": [last["updated_at"], last["run_id"]],
        })
    return {
        "schema_version": 1,
        "runs": [public_run_projection(item, now=observed_at) for item in page],
        "next_cursor": next_cursor,
    }


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str = Field(..., min_length=1, max_length=256)
    catalog_source: Literal["project", "profile", "showcase"] | None = None
    values: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=1, max_length=512)
    concurrency_policy: Literal["queue", "allow", "forbid"] = "queue"
    schedule_at: object | None = Field(
        None,
        json_schema_extra={
            "anyOf": [
                {"type": "string", "format": "date-time"},
                {"type": "null"},
            ]
        },
    )

    @model_validator(mode="before")
    @classmethod
    def redact_invalid_values_for_validation(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "values" not in data:
            return data
        values = data.get("values")
        values_are_valid = isinstance(values, dict)
        if values_are_valid:
            for name, value in values.items():
                if not isinstance(name, str) or not isinstance(value, str):
                    values_are_valid = False
                    break
                try:
                    name.encode("utf-8")
                    value.encode("utf-8")
                except UnicodeEncodeError:
                    values_are_valid = False
                    break

        if values_are_valid:
            from plugins.workflow.catalog_api import (
                desktop_input_name_is_representable,
            )
            from plugins.workflow.sanitize import workflow_input_names_are_portable

            values_are_valid = (
                len(values) <= 64
                and workflow_input_names_are_portable(values)
                and all(desktop_input_name_is_representable(name) for name in values)
            )
        if values_are_valid:
            return data

        # A model-level error includes the whole request. Let Pydantic reject
        # one safe field sentinel instead of retaining any raw input values.
        safe_data = dict(data)
        safe_data["values"] = None
        return safe_data

    @field_validator("workflow")
    @classmethod
    def validate_workflow_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow must not be blank")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency_key must not be blank")
        return value


@router.post("/runs", status_code=202)
def post_runs(
    request_context: Request,
    request: StartRunRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    operator.require("write")
    from plugins.workflow.api_admission import (
        ApiAdmissionAuthority,
        ApiAdmissionError,
        normalize_api_schedule_at,
        start_api_run,
        validate_api_value_bounds,
    )

    authority = ApiAdmissionAuthority(
        principal=operator.principal,
        namespace=operator.idempotency_namespace,
        operator_scope=None if operator.unrestricted else operator.scope,
        source_instance=operator.source_instance,
        assurance=operator.assurance,
        trigger_source=operator.trigger_source,
        return_route=operator.return_route,
    )
    try:
        schedule_now_utc = _schedule_now_utc()
        schedule_at = normalize_api_schedule_at(
            request.schedule_at,
            now_utc=schedule_now_utc,
        )
        values = validate_api_value_bounds(request.values)
        with _store_lease() as store:
            result = start_api_run(
                store,
                hermes_home=get_hermes_home(),
                workdir=Path.cwd(),
                user_home=Path.home(),
                workflow_name=request.workflow,
                catalog_source=request.catalog_source,
                values=values,
                idempotency_key=request.idempotency_key,
                concurrency_policy=request.concurrency_policy,
                authority=authority,
                schedule_at=schedule_at,
                schedule_now_utc=schedule_now_utc,
            )
    except ApiAdmissionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "retryable": exc.retryable},
        ) from exc
    return {
        "schema_version": 1,
        "ok": True,
        "result": sanitize_projection(result),
        "error": None,
    }


@router.get("/runs/{run_id}")
def get_run(
    request: Request,
    run_id: str,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    observed_at = _schedule_now_utc()
    with _store_lease() as store:
        return public_run_projection(
            _load_authorized(store, run_id, operator, now=observed_at),
            now=observed_at,
        )


def _attention_origin(run: Mapping[str, object]) -> str:
    provenance = run.get("provenance")
    if isinstance(provenance, Mapping) and provenance.get("source"):
        return str(provenance["source"])
    return str(run.get("trigger") or "workflow")


def _run_attention_items(run: Mapping[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    origin = _attention_origin(run)
    nodes = run.get("nodes", {})
    node_items = nodes.items() if isinstance(nodes, Mapping) else ()
    for node_id, node in node_items:
        pending = node.get("pending_interaction") if isinstance(node, Mapping) else None
        kind = pending.get("type") if isinstance(pending, Mapping) else pending
        if kind not in {
            "approval",
            "workflow_approval",
            "loop_input",
            "capability",
            "reconcile",
        }:
            continue
        item_kind = str(kind)
        interaction = (
            dict(pending) if isinstance(pending, Mapping) else {"type": item_kind}
        )
        items.append(
            {
                "run_id": run["run_id"],
                "workflow": run["workflow"],
                "node_id": str(node_id),
                "kind": item_kind,
                "origin": origin,
                "cause": str(
                    interaction.get("message")
                    or interaction.get("gate_message")
                    or interaction.get("prompt")
                    or item_kind
                ),
                "interaction": interaction,
                "status": run["status"],
                "health": run["health"],
                "next_actions": run["next_actions"],
                "state_version": run.get("state_version"),
                "updated_at": run["updated_at"],
                "_source": "run",
                "_source_position": [
                    str(run["updated_at"]),
                    str(run["run_id"]),
                    str(node_id),
                    item_kind,
                ],
            }
        )
    if run["status"] == "failed":
        last_error = run.get("last_error")
        failure_cause = (
            last_error.get("message") or last_error.get("code")
            if isinstance(last_error, Mapping)
            else None
        )
        items.append(
            {
                "run_id": run["run_id"],
                "workflow": run["workflow"],
                "node_id": None,
                "kind": "failure",
                "origin": origin,
                "cause": str(
                    failure_cause or run.get("blocking_reason") or "workflow_failed"
                ),
                "interaction": None,
                "status": run["status"],
                "health": run["health"],
                "next_actions": run["next_actions"],
                "state_version": run.get("state_version"),
                "updated_at": run["updated_at"],
                "_source": "run",
                "_source_position": [
                    str(run["updated_at"]),
                    str(run["run_id"]),
                    "",
                    "failure",
                ],
            }
        )
    elif run["health"] in {
        "stalled",
        "coordinator_unavailable",
        "storage_degraded",
    }:
        items.append(
            {
                "run_id": run["run_id"],
                "workflow": run["workflow"],
                "node_id": None,
                "kind": "stalled",
                "origin": origin,
                "cause": str(run.get("blocking_reason") or run["health"]),
                "interaction": None,
                "status": run["status"],
                "health": run["health"],
                "next_actions": run["next_actions"],
                "state_version": run.get("state_version"),
                "updated_at": run["updated_at"],
                "_source": "run",
                "_source_position": [
                    str(run["updated_at"]),
                    str(run["run_id"]),
                    "",
                    "stalled",
                ],
            }
        )
    return sorted(
        items,
        key=lambda item: (
            str(item["node_id"] or ""),
            str(item["kind"]),
        ),
        reverse=True,
    )


def _attention_order_key(item: Mapping[str, object]) -> tuple[str, ...]:
    return (
        str(item["updated_at"]),
        str(item["run_id"]),
        str(item["_source"]),
        str(item.get("node_id") or ""),
        str(item["kind"]),
        str(
            item.get("interaction", {}).get("notification_id", "")
            if isinstance(item.get("interaction"), Mapping)
            else ""
        ),
    )


def _attention_cursor_position(
    payload: Mapping[str, object], name: str, length: int
) -> tuple[str, ...] | None:
    value = payload.get(name)
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != length
        or not all(isinstance(part, str) for part in value)
    ):
        raise ValueError
    return tuple(value)


@router.get("/attention")
def attention(
    request: Request,
    limit: int = Query(100, ge=1, le=100),
    cursor: str | None = None,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    cursor_scope = f"{operator.cursor_scope}:attention"
    observed_at = datetime.now(timezone.utc)
    run_position: tuple[str, ...] | None = None
    notification_position: tuple[str, ...] | None = None
    if cursor:
        payload = _decode_cursor(cursor, kind="attention", scope=cursor_scope)
        try:
            observed_value = payload.get("observed_at")
            if not isinstance(observed_value, str):
                raise ValueError
            observed_at = datetime.fromisoformat(observed_value)
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError
            observed_at = observed_at.astimezone(timezone.utc)
            run_position = _attention_cursor_position(payload, "run_position", 4)
            notification_position = _attention_cursor_position(
                payload, "notification_position", 3
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=410,
                detail={"code": "cursor_expired", "cursor_reset": True},
            ) from exc

    items: list[dict[str, object]] = []
    run_candidates: tuple[dict[str, object], ...] = ()
    facts: tuple[dict[str, object], ...] = ()
    with _store_lease() as store:
        if run_position is not None:
            try:
                same_run = _load_authorized(store, run_position[1], operator)
            except HTTPException:
                same_run = None
            if (
                isinstance(same_run, Mapping)
                and str(same_run.get("updated_at")) == run_position[0]
            ):
                remaining_key = (run_position[2], run_position[3])
                items.extend(
                    item
                    for item in _run_attention_items(same_run)
                    if (
                        str(item.get("node_id") or ""),
                        str(item["kind"]),
                    )
                    < remaining_key
                )
        from plugins.workflow.coordinator_store import CoordinatorStore

        coordinator = CoordinatorStore(store.database).health(now=observed_at)
        include_unavailable = (
            coordinator.status != "healthy"
            or store.storage_health()["status"] != "healthy"
        )
        run_candidates = store.attention_candidates(
            operator_scope=None if operator.unrestricted else operator.scope,
            observed_at=observed_at,
            limit=200,
            before=(run_position[0], run_position[1])
            if run_position is not None
            else None,
            include_unavailable=include_unavailable,
        )
        for run in run_candidates[:200]:
            items.extend(_run_attention_items(run))

        facts = NotificationOutbox(store).pending_attention_page(
            limit=200,
            observed_at=observed_at,
            before=notification_position,
            operator_scope=None if operator.unrestricted else operator.scope,
        )
        for fact in facts[:200]:
            try:
                run = _load_authorized(store, str(fact["run_id"]), operator)
            except HTTPException:
                continue
            items.append(
                {
                    "run_id": fact["run_id"],
                    "workflow": run["workflow"],
                    "node_id": None,
                    "kind": "notification",
                    "origin": _attention_origin(run),
                    "cause": str(fact["kind"]),
                    "interaction": {
                        "type": "notification",
                        "kind": fact["kind"],
                        "notification_id": fact["notification_id"],
                    },
                    "status": run["status"],
                    "health": run["health"],
                    "next_actions": run["next_actions"],
                    "state_version": run["state_version"],
                    "updated_at": fact["updated_at"],
                    "_source": "notification",
                    "_source_position": [
                        str(fact["updated_at"]),
                        str(fact["run_id"]),
                        str(fact["notification_id"]),
                    ],
                }
            )

    items.sort(key=_attention_order_key, reverse=True)
    page = items[:limit]
    has_more = len(items) > limit or len(run_candidates) > 200 or len(facts) > 200
    next_cursor = None
    if has_more and page:
        next_run_position = list(run_position) if run_position is not None else None
        next_notification_position = (
            list(notification_position) if notification_position is not None else None
        )
        for item in page:
            if item["_source"] == "run":
                next_run_position = list(item["_source_position"])
            else:
                next_notification_position = list(item["_source_position"])
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "kind": "attention",
                "scope": _scope_key(cursor_scope),
                "observed_at": observed_at.isoformat(),
                "run_position": next_run_position,
                "notification_position": next_notification_position,
            }
        )
    public_page = [
        {
            key: value
            for key, value in item.items()
            if key not in {"_source", "_source_position"}
        }
        for item in page
    ]
    return {
        "schema_version": 1,
        "items": sanitize_projection(public_page),
        "next_cursor": next_cursor,
    }


async def _acquire_event_waiter(runtime: WorkflowApiRuntime) -> None:
    try:
        await asyncio.wait_for(runtime.event_waiters.acquire(), timeout=0.001)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "event_wait_capacity",
                "retryable": True,
                "retry_after_seconds": 1,
            },
        ) from exc


@router.get("/runs/{run_id}/events")
async def events(
    request: Request,
    run_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1),
    wait_seconds: float = Query(0, ge=0, le=30),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    runtime = _runtime()
    await _acquire_event_waiter(runtime)
    try:
        with _store_lease() as store:
            await runtime.run_store_io(_load_authorized, store, run_id, operator)
            deadline = time.monotonic() + wait_seconds
            while True:
                page = await runtime.run_store_io(
                    store.events_after,
                    run_id,
                    after=after,
                    limit=min(limit, 200),
                    operator_scope=None if operator.unrestricted else operator.scope,
                )
                if page["events"] or wait_seconds == 0 or time.monotonic() >= deadline:
                    return sanitize_projection(page)
                await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    finally:
        runtime.event_waiters.release()


@router.get("/runs/{run_id}/evidence")
def evidence(
    request: Request,
    run_id: str,
    kind: str = Query(...),
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    if kind not in EVIDENCE_KINDS:
        raise HTTPException(status_code=400, detail={"code": "evidence_kind_invalid"})
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    with _store_lease() as store:
        _load_authorized(store, run_id, operator)
        return EvidenceReader(store).query(
            run_id,
            kind=kind,
            after=after,
            limit=limit,
            operator_scope=None if operator.unrestricted else operator.scope,
        )


class ActionRequest(BaseModel):
    expected_version: int
    interaction_id: str | None = None
    comment: str = Field("", max_length=65536)
    reason: str = Field("", max_length=65536)
    value: str = Field("", max_length=65536)
    outcome: str | None = None
    node_id: str | None = None


@router.post("/runs/{run_id}/{action}")
def mutate_run(
    request_context: Request,
    run_id: str,
    action: str,
    request: ActionRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request_context, operator_scope)
    operator.require("write")
    scope = None if operator.unrestricted else operator.scope
    observed_at = _schedule_now_utc()
    with _store_lease() as store:
        current = _load_authorized(store, run_id, operator, now=observed_at)
        if action not in MUTATION_ACTIONS:
            raise HTTPException(status_code=404, detail={"code": "action_not_found"})
        if action in {"approve", "reject", "provide-input", "reconcile"} and (
            not isinstance(request.interaction_id, str)
            or not request.interaction_id.strip()
        ):
            raise HTTPException(
                status_code=409, detail={"code": "interaction_id_required"}
            )
        if not mutation_is_valid(
            action,
            status=str(current["status"]),
            pending_interaction=current.get("pending_interaction"),
            health=str(current.get("health") or ""),
            archived=bool(current.get("archived_at")),
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "invalid_transition",
                    "current": public_run_projection(current, now=observed_at),
                },
            )
        if int(current["state_version"]) != request.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_state",
                    "current": public_run_projection(current, now=observed_at),
                },
            )
        try:
            if action == "approve":
                result = store.approve_run(
                    run_id,
                    comment=request.comment,
                    expected_state_version=request.expected_version,
                    interaction_id=request.interaction_id,
                    actor=operator.principal,
                    channel=operator.channel,
                    operator_scope=scope,
                )
                if result.outcome != "applied":
                    raise RuntimeError("stale approval decision")
            elif action == "reject":
                result = store.reject_run(
                    run_id,
                    reason=request.reason,
                    expected_state_version=request.expected_version,
                    interaction_id=request.interaction_id,
                    actor=operator.principal,
                    channel=operator.channel,
                    operator_scope=scope,
                )
                if result.outcome != "applied":
                    raise RuntimeError("stale rejection decision")
            elif action == "provide-input":
                pending = current.get("pending_interaction")
                actual_interaction = (
                    pending.get("interaction_id")
                    if isinstance(pending, Mapping)
                    else None
                )
                if actual_interaction != request.interaction_id:
                    raise ValueError("interaction ID does not match pending input")
                store.provide_loop_input(
                    run_id,
                    request.value,
                    expected_state_version=request.expected_version,
                    interaction_id=request.interaction_id,
                    actor=operator.principal,
                    channel=operator.channel,
                    operator_scope=scope,
                )
            elif action == "resume":
                from hermes_cli.profiles import get_active_profile_name
                from plugins.workflow.cli import _runtime_config, _scheduler

                resume_scheduler = _scheduler(
                    store,
                    _runtime_config(store.hermes_home),
                    profile_name=get_active_profile_name(),
                )
                always_run_nodes = resume_scheduler.verified_always_run_nodes(
                    run_id
                )
                store.resume_run(
                    run_id,
                    always_run_nodes=always_run_nodes,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
            elif action == "retry":
                store.retry_run(
                    run_id,
                    node_id=request.node_id,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
            elif action == "reconcile":
                store.reconcile_run(
                    run_id,
                    request.outcome or "",
                    expected_state_version=request.expected_version,
                    interaction_id=request.interaction_id,
                    operator_scope=scope,
                )
            elif action == "cancel":
                store.cancel_run(
                    run_id,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
            elif action == "abandon":
                store.abandon_run(
                    run_id,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
            elif action == "archive":
                store.archive_run(
                    run_id,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
            elif action == "restore":
                store.restore_run(
                    run_id,
                    expected_state_version=request.expected_version,
                    operator_scope=scope,
                )
        except WorkflowLanguageCompatibilityError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_state",
                    "current": public_run_projection(
                        _load_authorized(
                            store,
                            run_id,
                            operator,
                            now=observed_at,
                        ),
                        now=observed_at,
                    ),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "invalid_transition"}
            ) from exc
        return public_run_projection(
            _load_authorized(store, run_id, operator, now=observed_at),
            now=observed_at,
        )
