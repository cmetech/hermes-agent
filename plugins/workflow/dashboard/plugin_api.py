"""Authenticated, bounded Desktop REST adapter for the workflow RunStore."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Mapping

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from hermes_constants import get_hermes_home
from plugins.workflow.actions import MUTATION_ACTIONS, mutation_is_valid
from plugins.workflow.store import RunStore


router = APIRouter()
_CURSOR_SECRET = secrets.token_bytes(32)
_FORBIDDEN = {
    "prompt", "reasoning", "environment", "env", "arguments",
    "tool_arguments", "operator_scope_digest", "idempotency_key_digest",
}


def _store() -> RunStore:
    return RunStore(get_hermes_home())


def _scope_key(scope: str | None) -> str:
    return hashlib.sha256((scope or "<local-unscoped>").encode()).hexdigest()


def _encode_cursor(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_CURSOR_SECRET, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def _decode_cursor(value: str, *, kind: str, scope: str | None) -> dict[str, object]:
    try:
        combined = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        raw, signature = combined[:-32], combined[-32:]
        expected = hmac.new(_CURSOR_SECRET, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if payload.get("v") != 1 or payload.get("kind") != kind or payload.get("scope") != _scope_key(scope):
            raise ValueError
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=410,
            detail={"code": "cursor_expired", "cursor_reset": True},
        ) from exc


def _sanitize(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _FORBIDDEN
            and not any(token in str(key).lower() for token in ("secret", "password", "token"))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _authorized_runs(store: RunStore, scope: str | None) -> tuple[dict[str, object], ...]:
    if scope is not None:
        return store.list_runs(operator_scope=scope, limit=200)
    return tuple(run for run in store.list_runs(limit=200) if run.get("operator_scope_digest") is None)


def _load_authorized(store: RunStore, run_id: str, scope: str | None) -> dict[str, object]:
    try:
        run = store.get_run_status(run_id, operator_scope=scope) if scope else store.get_run_status(run_id)
    except (KeyError, OSError) as exc:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"}) from exc
    if scope is None and run.get("operator_scope_digest") is not None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    return run


@router.get("/runs")
def list_runs(
    limit: int = Query(100, ge=1, le=100),
    cursor: str | None = None,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    runs = list(_authorized_runs(_store(), operator_scope))
    start = int(_decode_cursor(cursor, kind="runs", scope=operator_scope).get("position", 0)) if cursor else 0
    page = runs[start : start + limit]
    next_cursor = None
    if start + limit < len(runs):
        next_cursor = _encode_cursor({
            "v": 1, "kind": "runs", "scope": _scope_key(operator_scope),
            "position": start + limit,
        })
    return {"schema_version": 1, "runs": _sanitize(page), "next_cursor": next_cursor}


@router.get("/runs/{run_id}")
def get_run(run_id: str, operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope")):
    store = _store()
    _load_authorized(store, run_id, operator_scope)
    return _sanitize(store.get_run_status(run_id, operator_scope=operator_scope))


@router.get("/attention")
def attention(
    limit: int = Query(100, ge=1, le=100),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    items = []
    for run in _authorized_runs(_store(), operator_scope):
        for node_id, node in run.get("nodes", {}).items():
            pending = node.get("pending_interaction") if isinstance(node, Mapping) else None
            if pending is None:
                continue
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
                "run_id": run["run_id"], "workflow": run["workflow"],
                "node_id": node_id, "interaction": pending,
                "state_version": run["state_version"], "updated_at": run["updated_at"],
            })
    items.sort(key=lambda item: (str(item["updated_at"]), str(item["run_id"]), str(item["node_id"])))
    return {"schema_version": 1, "items": _sanitize(items[:limit]), "next_cursor": None}


@router.get("/runs/{run_id}/events")
def events(
    run_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1),
    wait_seconds: float = Query(0, ge=0, le=30),
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    store = _store()
    _load_authorized(store, run_id, operator_scope)
    deadline = time.monotonic() + wait_seconds
    while True:
        page = store.events_after(
            run_id,
            after=after,
            limit=min(limit, 200),
            operator_scope=operator_scope,
        )
        if page["events"] or wait_seconds == 0 or time.monotonic() >= deadline:
            return _sanitize(page)
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


class ActionRequest(BaseModel):
    expected_version: int
    interaction_id: str | None = None
    comment: str = Field("", max_length=65536)
    reason: str = Field("", max_length=65536)
    outcome: str | None = None
    node_id: str | None = None


@router.post("/runs/{run_id}/{action}")
def mutate_run(
    run_id: str,
    action: str,
    request: ActionRequest,
    operator_scope: str | None = Header(None, alias="X-Hermes-Operator-Scope"),
):
    store = _store()
    current = _load_authorized(store, run_id, operator_scope)
    if action not in MUTATION_ACTIONS:
        raise HTTPException(status_code=404, detail={"code": "action_not_found"})
    if not mutation_is_valid(
        action,
        status=str(current["status"]),
        pending_interaction=current.get("pending_interaction"),
        health=str(current.get("health") or ""),
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_transition", "current": _sanitize(current)},
        )
    if int(current["state_version"]) != request.expected_version:
        raise HTTPException(status_code=409, detail={"code": "stale_state", "current": _sanitize(current)})
    try:
        if action == "approve":
            result = store.approve_run(run_id, comment=request.comment, expected_state_version=request.expected_version, interaction_id=request.interaction_id, channel="desktop", operator_scope=operator_scope)
            if result.outcome != "applied":
                raise RuntimeError("stale approval decision")
        elif action == "reject":
            result = store.reject_run(run_id, reason=request.reason, expected_state_version=request.expected_version, interaction_id=request.interaction_id, channel="desktop", operator_scope=operator_scope)
            if result.outcome != "applied":
                raise RuntimeError("stale rejection decision")
        elif action == "resume":
            store.resume_run(run_id, expected_state_version=request.expected_version, operator_scope=operator_scope)
        elif action == "retry":
            store.retry_run(run_id, node_id=request.node_id, expected_state_version=request.expected_version, operator_scope=operator_scope)
        elif action == "reconcile":
            store.reconcile_run(run_id, request.outcome or "", expected_state_version=request.expected_version, interaction_id=request.interaction_id, operator_scope=operator_scope)
        elif action == "cancel":
            store.cancel_run(run_id, expected_state_version=request.expected_version, operator_scope=operator_scope)
        elif action == "abandon":
            store.abandon_run(run_id, expected_state_version=request.expected_version, operator_scope=operator_scope)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "stale_state", "current": _sanitize(_load_authorized(store, run_id, operator_scope))}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_transition"}) from exc
    return _sanitize(store.get_run_status(run_id, operator_scope=operator_scope))
