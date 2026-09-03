"""Profile-scoped Desktop operations for durable agent handoffs."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re

from .method_ctx import HandlerRegistry


_registry = HandlerRegistry()
method = _registry.method
_bound_server = None

LONG_HANDLERS = frozenset({"agent_handoff.create", "agent_handoff.command"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_COMMAND_ARGUMENTS = {
    "acknowledge": frozenset(),
    "cancel": frozenset(),
    "reconcile": frozenset(),
    "respond": frozenset({"request_id", "choice"}),
    "message": frozenset({"text", "correlation_id"}),
}


class _RpcFailure(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _error(rid, exc: Exception) -> dict:
    from hermes_cli.handoff.service import HandoffServiceError
    from hermes_cli.handoff.store import (
        HandoffConflict,
        HandoffNotFound,
        HandoffStateConflict,
        HandoffStoreError,
    )

    if isinstance(exc, _RpcFailure):
        code = exc.code
    elif isinstance(exc, HandoffNotFound):
        code = "handoff_not_found"
    elif isinstance(exc, HandoffConflict | HandoffStateConflict):
        code = "handoff_conflict"
    elif isinstance(exc, ValueError) and (
        "does not advertise" in str(exc)
        or "approval response" in str(exc)
        or "not accepting guidance" in str(exc)
    ):
        code = "capability_mismatch"
    elif isinstance(exc, ValueError | LookupError):
        code = "invalid_argument"
    elif isinstance(exc, HandoffStoreError | HandoffServiceError):
        code = "handoff_operation_failed"
    else:
        code = "handoff_internal_error"
    status = {
        "invalid_argument": 4400,
        "profile_unavailable": 4401,
        "handoff_not_found": 4404,
        "handoff_conflict": 4409,
        "capability_mismatch": 4412,
        "handoff_operation_failed": 4500,
        "handoff_internal_error": 4501,
    }[code]
    return _bound_server._err(
        rid,
        status,
        code.replace("_", " "),
        data={"code": code},
    )


def _require_params(
    params: dict,
    required: set[str] | frozenset[str],
    optional: set[str] | frozenset[str] = frozenset({"profile"}),
) -> None:
    if (
        not isinstance(params, dict)
        or not required <= set(params) <= required | optional
    ):
        raise _RpcFailure("invalid_argument")


def _selected_profile(params: dict) -> tuple[str, Path]:
    from hermes_cli.profiles import (
        normalize_profile_name,
        profile_exists,
        validate_profile_name,
    )

    raw = params.get("profile")
    if raw is not None and not isinstance(raw, str):
        raise _RpcFailure("profile_unavailable")
    raw = str(raw or "").strip()
    profile = raw or str(_bound_server._current_profile_name() or "default")
    try:
        profile = normalize_profile_name(profile)
        validate_profile_name(profile)
        if not profile_exists(profile):
            raise ValueError
    except (TypeError, ValueError):
        raise _RpcFailure("profile_unavailable") from None

    current = str(_bound_server._current_profile_name() or "default")
    if profile == current:
        return profile, Path(_bound_server._hermes_home).expanduser().resolve()
    home = _bound_server._profile_home(profile)
    if home is None:
        raise _RpcFailure("profile_unavailable")
    return profile, Path(home).expanduser().resolve()


@contextmanager
def _service(params: dict):
    from hermes_cli.handoff.service import AgentHandoffService
    from hermes_cli.handoff.store import HandoffStore

    profile, home = _selected_profile(params)
    service = AgentHandoffService(store=HandoffStore(home / "handoffs.db"))
    try:
        yield profile, home, service
    finally:
        service.store.close()


def _snapshot(service, snapshot) -> dict:
    from hermes_cli.handoff.projection import snapshot_summary

    payload = snapshot_summary(snapshot)
    needs_attention = service.store.has_attention(snapshot.handoff_id)
    payload["needs_attention"] = needs_attention
    checkpoint = snapshot.checkpoint or {}
    capabilities = set((snapshot.binding or {}).get("capabilities") or ())
    actions = []
    if snapshot.phase == "needs_input" and checkpoint.get("approval_request_id"):
        payload["approval"] = {
            "request_id": checkpoint["approval_request_id"],
            "choices": list(checkpoint.get("approval_choices") or ()),
        }
        if snapshot.spec.mode == "conversation" and "approval" in capabilities:
            actions.append("respond")
    if (
        snapshot.spec.mode == "conversation"
        and snapshot.phase == "active"
        and "follow_up" in capabilities
    ):
        actions.append("message")
    if snapshot.phase == "indeterminate":
        actions.append("reconcile")
    if snapshot.phase not in {"succeeded", "failed", "cancelled", "cancelling"}:
        actions.append("cancel")
    if needs_attention:
        actions.append("acknowledge")
    payload["actions"] = actions
    return payload


def _directory(rid, params: dict) -> dict:
    try:
        _require_params(params, frozenset())
        with _service(params) as (_profile, home, _handoff_service):
            from hermes_cli.handoff.directory import load_agent_directory

            agents = [
                {
                    "name": entry.name,
                    "default": entry.default.canonical,
                    "endpoints": [endpoint.canonical for endpoint in entry.endpoints],
                }
                for entry in load_agent_directory(home)
            ]
        return _bound_server._ok(rid, {"agents": agents})
    except Exception as exc:
        return _error(rid, exc)


def _create(rid, params: dict) -> dict:
    try:
        _require_params(params, {"target", "message", "request_id"})
        target = params["target"]
        message = params["message"]
        request_id = params["request_id"]
        if (
            not isinstance(target, str)
            or not target.strip()
            or len(target) > 512
            or not isinstance(message, str)
            or not message.strip()
            or len(message) > 16_000
            or not isinstance(request_id, str)
            or not _SAFE_ID.fullmatch(request_id)
        ):
            raise _RpcFailure("invalid_argument")

        with _service(params) as (profile, home, service):
            from hermes_cli.handoff.directory import resolve_agent_target
            from hermes_cli.handoff.models import HandoffSpec

            resolved = resolve_agent_target(target, initiating_home=home)
            if (
                resolved.source not in {"explicit", "directory"}
                or resolved.endpoint is None
            ):
                raise _RpcFailure("invalid_argument")
            inbox_id = (
                "desktop-"
                + sha256(f"{profile}\0{request_id}".encode("utf-8")).hexdigest()
            )
            snapshot = service.create(
                HandoffSpec(
                    mode="conversation",
                    endpoint=resolved.endpoint,
                    prompt=f"Message from the operator: {message}",
                    output_schema=None,
                    deadline_at=None,
                    attribution={"profile": profile, "source": "desktop"},
                    required_capabilities=resolved.required_capabilities,
                    return_route={
                        "kind": "operator",
                        "profile": profile,
                        "inbox_id": inbox_id,
                    },
                ),
                f"operator/{profile}",
                handoff_key=request_id,
            )
            snapshot = service.advance(snapshot.handoff_id, budget_seconds=2.0).snapshot
            payload = _snapshot(service, snapshot)
        return _bound_server._ok(rid, payload)
    except Exception as exc:
        return _error(rid, exc)


def _get(rid, params: dict) -> dict:
    try:
        _require_params(params, {"handoff_id"})
        with _service(params) as (_profile, _home, service):
            payload = _snapshot(service, service.get(params["handoff_id"]))
        return _bound_server._ok(rid, payload)
    except Exception as exc:
        return _error(rid, exc)


def _list(rid, params: dict) -> dict:
    try:
        _require_params(
            params,
            frozenset(),
            {"profile", "phase", "limit", "before"},
        )
        query = {"phase": params["phase"]} if params.get("phase") is not None else None
        with _service(params) as (_profile, _home, service):
            snapshots = service.list(
                query,
                limit=params.get("limit", 50),
                before=params.get("before"),
            )
            payload = {"handoffs": [_snapshot(service, item) for item in snapshots]}
        return _bound_server._ok(rid, payload)
    except Exception as exc:
        return _error(rid, exc)


def _evidence(rid, params: dict) -> dict:
    try:
        _require_params(
            params,
            {"handoff_id"},
            {"profile", "after_sequence", "limit"},
        )
        with _service(params) as (_profile, _home, service):
            from hermes_cli.handoff.projection import evidence_payload

            snapshot = service.get(params["handoff_id"])
            page = service.evidence(
                snapshot.handoff_id,
                after_sequence=params.get("after_sequence", 0),
                limit=params.get("limit", 100),
            )
            payload = {**_snapshot(service, snapshot), **evidence_payload(page)}
        return _bound_server._ok(rid, payload)
    except Exception as exc:
        return _error(rid, exc)


def _command(rid, params: dict) -> dict:
    try:
        kind = params.get("kind")
        if not isinstance(kind, str):
            raise _RpcFailure("invalid_argument")
        expected = _COMMAND_ARGUMENTS.get(kind)
        if expected is None:
            raise _RpcFailure("invalid_argument")
        required = {"handoff_id", "kind", "command_id"} | set(expected)
        _require_params(params, required)
        if not all(
            isinstance(params[name], str) and params[name] for name in required
        ) or not _SAFE_ID.fullmatch(params["command_id"]):
            raise _RpcFailure("invalid_argument")

        with _service(params) as (_profile, _home, service):
            values = {
                name: params.get(name)
                for name in ("request_id", "choice", "text", "correlation_id")
            }
            snapshot = service.command(
                params["handoff_id"],
                kind,
                command_id=params["command_id"],
                actor="operator",
                **values,
            )
            payload = _snapshot(service, snapshot)
        return _bound_server._ok(rid, payload)
    except Exception as exc:
        return _error(rid, exc)


@method("agent_handoff.create")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _create

    return _create(rid, params)


@method("agent_handoff.get")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _get

    return _get(rid, params)


@method("agent_handoff.list")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _list

    return _list(rid, params)


@method("agent_handoff.evidence")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _evidence

    return _evidence(rid, params)


@method("agent_handoff.command")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _command

    return _command(rid, params)


@method("agent_handoff.directory")
def _(rid, params: dict) -> dict:
    from tui_gateway.methods_agent_handoff import _directory

    return _directory(rid, params)


def register(server) -> None:
    global _bound_server

    _bound_server = server
    _registry.install(server)
    server._LONG_HANDLERS = server._LONG_HANDLERS | LONG_HANDLERS


__all__ = ["register"]
