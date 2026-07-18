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
from typing import Iterator, Mapping

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from hermes_constants import get_hermes_home
from plugins.workflow.actions import MUTATION_ACTIONS, mutation_is_valid
from plugins.workflow.evidence import EVIDENCE_KINDS, EvidenceReader
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.runtime import (
    StoreRegistryCapacityError,
    WorkflowApiLimits,
    WorkflowApiRuntime,
    WorkflowRetentionPolicy,
)
from plugins.workflow.sanitize import sanitize_projection
from plugins.workflow.store import RunStore


_CURSOR_SECRET = secrets.token_bytes(32)
_RUNTIME: WorkflowApiRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


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


_ALL_WORKFLOW_CAPABILITIES = frozenset({"read", "write", "delivery", "admin"})


@dataclass(frozen=True, slots=True)
class WorkflowAuthority:
    principal: str
    scope: str | None
    unrestricted: bool
    capabilities: frozenset[str]
    delivery_destination: str | None = "desktop"

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
        org = getattr(session, "org_id", "") or "personal"
        maximum = (
            f"dashboard:{getattr(session, 'provider', 'unknown')}:"
            f"{org}:{getattr(session, 'user_id', 'unknown')}"
        )
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
        )
    if token is not None and getattr(request.state, "token_authenticated", False):
        maximum = (
            f"service:{getattr(token, 'provider', 'unknown')}:"
            f"{getattr(token, 'principal', 'unknown')}"
        )
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
        )
    if getattr(request.state, "local_admin_authenticated", False):
        return WorkflowAuthority(
            principal="profile-local-dashboard",
            scope=requested_scope,
            unrestricted=requested_scope is None,
            capabilities=_ALL_WORKFLOW_CAPABILITIES,
        )
    raise HTTPException(status_code=401, detail={"code": "authentication_required"})


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
    store: RunStore, operator: WorkflowAuthority, *, view: str = "all"
):
    retention = WorkflowRetentionPolicy.from_profile(get_hermes_home())
    return store.list_runs(
        operator_scope=None if operator.unrestricted else operator.scope,
        limit=200,
        view=view,
        terminal_board_days=retention.terminal_board_days,
    )


def _load_authorized(store: RunStore, run_id: str, operator: WorkflowAuthority):
    try:
        return store.get_run_status(
            run_id,
            operator_scope=None if operator.unrestricted else operator.scope,
        )
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
    with _store_lease() as store:
        runs = list(_authorized_runs(store, operator, view=view))
    cursor_scope = f"{operator.cursor_scope}:{view}"
    start = (
        int(
            _decode_cursor(cursor, kind="runs", scope=cursor_scope).get(
                "position", 0
            )
        )
        if cursor
        else 0
    )
    page = runs[start : start + limit]
    next_cursor = None
    if start + limit < len(runs):
        next_cursor = _encode_cursor({
            "v": 1,
            "kind": "runs",
            "scope": _scope_key(cursor_scope),
            "position": start + limit,
        })
    return {
        "schema_version": 1,
        "runs": sanitize_projection(page),
        "next_cursor": next_cursor,
    }


@router.get("/runs/{run_id}")
def get_run(
    request: Request,
    run_id: str,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    with _store_lease() as store:
        return sanitize_projection(_load_authorized(store, run_id, operator))


@router.get("/attention")
def attention(
    request: Request,
    limit: int = Query(100, ge=1, le=100),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    operator = _verified_operator(request, operator_scope)
    operator.require("read")
    items = []
    with _store_lease() as store:
        for run in _authorized_runs(store, operator):
            for node_id, node in run.get("nodes", {}).items():
                pending = (
                    node.get("pending_interaction")
                    if isinstance(node, Mapping)
                    else None
                )
                kind = pending.get("type") if isinstance(pending, Mapping) else pending
                if kind not in {
                    "approval",
                    "workflow_approval",
                    "loop_input",
                    "capability",
                    "reconcile",
                }:
                    continue
                items.append({
                    "run_id": run["run_id"],
                    "workflow": run["workflow"],
                    "node_id": node_id,
                    "interaction": pending,
                    "state_version": run["state_version"],
                    "updated_at": run["updated_at"],
                })
        for fact in NotificationOutbox(store).pending_attention():
            try:
                run = _load_authorized(store, str(fact["run_id"]), operator)
            except HTTPException:
                continue
            items.append(
                {
                    "run_id": fact["run_id"],
                    "workflow": run["workflow"],
                    "node_id": None,
                    "interaction": {
                        "type": "notification",
                        "kind": fact["kind"],
                        "notification_id": fact["notification_id"],
                    },
                    "state_version": run["state_version"],
                    "updated_at": fact["updated_at"],
                }
            )
    items.sort(
        key=lambda item: (
            str(item["updated_at"]),
            str(item["run_id"]),
            str(item["node_id"]),
        )
    )
    return {
        "schema_version": 1,
        "items": sanitize_projection(items[:limit]),
        "next_cursor": None,
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
    with _store_lease() as store:
        current = _load_authorized(store, run_id, operator)
        if action not in MUTATION_ACTIONS:
            raise HTTPException(status_code=404, detail={"code": "action_not_found"})
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
                    "current": sanitize_projection(current),
                },
            )
        if int(current["state_version"]) != request.expected_version:
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_state", "current": sanitize_projection(current)},
            )
        try:
            if action == "approve":
                result = store.approve_run(
                    run_id,
                    comment=request.comment,
                    expected_state_version=request.expected_version,
                    interaction_id=request.interaction_id,
                    channel="desktop",
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
                    channel="desktop",
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
                    operator_scope=scope,
                )
            elif action == "resume":
                store.resume_run(
                    run_id,
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
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_state",
                    "current": sanitize_projection(
                        _load_authorized(store, run_id, operator)
                    ),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "invalid_transition"}
            ) from exc
        return sanitize_projection(_load_authorized(store, run_id, operator))
