"""Authenticated exact-cost authority for bounded provider call trees."""

from __future__ import annotations

import base64
import binascii
from collections import deque
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import math
import re
import secrets
import socket
import struct
import threading
import time
from typing import Any, Callable, Mapping


_VERSION = 1
_AUTHKEY_BYTES = 32
_NONCE_BYTES = 32
_FRAME_BYTES = 8192
_IO_TIMEOUT_SECONDS = 0.5
_ACCEPT_TIMEOUT_SECONDS = 0.05
_MAX_CLIENTS = 8
_REPLAY_WINDOW = 256
_MAX_USD = Decimal("1000000000000")
_MAX_SCALE = 9
_ATTEMPT_ID = re.compile(r"[0-9a-f]{32,64}")
_FAILURE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class CostBudgetTerminalError(RuntimeError):
    """A hard cost authority refused further provider transport."""

    failure_kind = "cost_budget_terminal"

    def __init__(self, message: str, *, failure_code: str | None = None) -> None:
        self.failure_kind = failure_code or self.failure_kind
        super().__init__(message)


class CostBudgetExhausted(CostBudgetTerminalError):
    failure_kind = "budget_exhausted"


class CostBudgetPoisoned(CostBudgetTerminalError):
    failure_kind = "authoritative_settlement_ambiguous"


class CostBudgetLeaseBusy(CostBudgetTerminalError):
    failure_kind = "cost_budget_lease_timeout"


def canonical_usd(value: object) -> str:
    """Return a bounded nonnegative base-10 amount without binary rounding."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TypeError("USD amount must be decimal text or a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("USD amount must be finite")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("USD amount is invalid") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("USD amount must be finite and nonnegative")
    if amount >= _MAX_USD:
        raise ValueError("USD amount exceeds the authority bound")
    normalized = amount.normalize() if amount else Decimal(0)
    scale = max(0, -normalized.as_tuple().exponent)
    if scale > _MAX_SCALE:
        raise ValueError("USD amount precision exceeds the authority bound")
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{label} is invalid")
    return value


def _validated_attempt_id(value: object) -> str:
    if not isinstance(value, str) or _ATTEMPT_ID.fullmatch(value) is None:
        raise ValueError("cost settlement attempt identity is invalid")
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _mac(authkey: bytes, value: Mapping[str, Any]) -> str:
    return hmac.new(authkey, _canonical(value), hashlib.sha256).hexdigest()


def _send_frame(connection: socket.socket, payload: Mapping[str, Any]) -> None:
    body = _canonical(payload)
    if not body or len(body) > _FRAME_BYTES:
        raise ValueError("cost authority frame is invalid")
    connection.sendall(struct.pack("!I", len(body)) + body)


def _recv_frame(connection: socket.socket) -> bytes:
    header = bytearray()
    while len(header) < 4:
        chunk = connection.recv(4 - len(header))
        if not chunk:
            raise EOFError("cost authority frame ended before its header")
        header.extend(chunk)
    (size,) = struct.unpack("!I", bytes(header))
    if not 1 <= size <= _FRAME_BYTES:
        raise ValueError("cost authority frame size is invalid")
    body = bytearray()
    while len(body) < size:
        chunk = connection.recv(size - len(body))
        if not chunk:
            raise EOFError("cost authority frame ended before its body")
        body.extend(chunk)
    return bytes(body)


def _validated_descriptor(value: object) -> tuple[tuple[str, int], bytes]:
    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "host",
        "port",
        "authkey",
    }:
        raise ValueError("cost budget authority descriptor is invalid")
    if value.get("version") != _VERSION or value.get("host") != "127.0.0.1":
        raise ValueError("cost budget authority descriptor is invalid")
    port = value.get("port")
    token = value.get("authkey")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("cost budget authority descriptor is invalid")
    if not isinstance(token, str):
        raise ValueError("cost budget authority descriptor is invalid")
    try:
        authkey = base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("cost budget authority descriptor is invalid") from exc
    if len(authkey) != _AUTHKEY_BYTES:
        raise ValueError("cost budget authority descriptor is invalid")
    return ("127.0.0.1", port), authkey


def validate_cost_budget_authority_descriptor(value: object) -> None:
    """Validate a credential-bearing descriptor without contacting its broker."""

    _validated_descriptor(value)


def _request(
    descriptor: Mapping[str, Any],
    operation: str,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    address, authkey = _validated_descriptor(descriptor)
    nonce = base64.b64encode(secrets.token_bytes(_NONCE_BYTES)).decode("ascii")
    unsigned = {
        "version": _VERSION,
        "operation": operation,
        "nonce": nonce,
        "payload": dict(payload),
    }
    try:
        with socket.create_connection(address, timeout=_IO_TIMEOUT_SECONDS) as connection:
            connection.settimeout(_IO_TIMEOUT_SECONDS)
            _send_frame(connection, {**unsigned, "mac": _mac(authkey, unsigned)})
            response = json.loads(_recv_frame(connection).decode("ascii"))
    except (
        EOFError,
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise CostBudgetPoisoned(
            "authoritative cost authority is unavailable",
            failure_code="authoritative_cost_authority_unavailable",
        ) from exc
    if not isinstance(response, Mapping):
        raise CostBudgetPoisoned("authoritative cost authority response is invalid")
    response = dict(response)
    response_mac = response.pop("mac", None)
    if (
        response.get("version") != _VERSION
        or response.get("nonce") != nonce
        or not isinstance(response_mac, str)
        or not hmac.compare_digest(response_mac, _mac(authkey, response))
    ):
        raise CostBudgetPoisoned("authoritative cost authority response is invalid")
    response.pop("version", None)
    response.pop("nonce", None)
    return response


def _raise_response_error(response: Mapping[str, Any]) -> None:
    code = response.get("failure_code")
    if not isinstance(code, str) or _FAILURE_CODE.fullmatch(code) is None:
        code = "authoritative_cost_authority_invalid"
    if code == "budget_exhausted":
        raise CostBudgetExhausted("authoritative cost budget is exhausted")
    raise CostBudgetPoisoned(
        "authoritative cost budget is terminal",
        failure_code=code,
    )


def acquire_cost_lease(
    descriptor: Mapping[str, Any],
    *,
    attempt_id: str,
    provider: str,
    model: str,
    deadline: float,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    attempt_id = _validated_attempt_id(attempt_id)
    provider = _bounded_text(provider, "provider", 128)
    model = _bounded_text(model, "model", 256)
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(deadline)
    ):
        raise ValueError("cost lease deadline is invalid")
    while True:
        if is_cancelled is not None and is_cancelled():
            raise CostBudgetLeaseBusy("authoritative cost lease wait cancelled")
        response = _request(
            descriptor,
            "acquire",
            {"attempt_id": attempt_id, "provider": provider, "model": model},
        )
        if response.get("acquired") is True:
            return attempt_id
        if response.get("busy") is not True:
            _raise_response_error(response)
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            raise CostBudgetLeaseBusy("authoritative cost lease deadline expired")
        time.sleep(min(0.01, remaining))


def settle_cost_lease(
    descriptor: Mapping[str, Any],
    *,
    attempt_id: str,
    provider: str,
    model: str,
    strategy: str,
    amount_usd: object,
    authoritative: bool,
) -> dict[str, Any]:
    response = _request(
        descriptor,
        "settle",
        {
            "attempt_id": _validated_attempt_id(attempt_id),
            "provider": _bounded_text(provider, "provider", 128),
            "model": _bounded_text(model, "model", 256),
            "strategy": _bounded_text(strategy, "strategy", 64),
            "amount_usd": canonical_usd(amount_usd),
            "authoritative": authoritative,
        },
    )
    if response.get("settled") is not True:
        _raise_response_error(response)
    return _validated_evidence(response.get("evidence"))


def release_unstarted_cost_lease(
    descriptor: Mapping[str, Any],
    *,
    attempt_id: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    response = _request(
        descriptor,
        "release_unstarted",
        {
            "attempt_id": _validated_attempt_id(attempt_id),
            "provider": _bounded_text(provider, "provider", 128),
            "model": _bounded_text(model, "model", 256),
        },
    )
    if response.get("released") is not True:
        _raise_response_error(response)
    return _validated_evidence(response.get("evidence"))


def poison_cost_lease(
    descriptor: Mapping[str, Any],
    *,
    attempt_id: str,
    failure_code: str,
) -> dict[str, Any]:
    if not isinstance(failure_code, str) or _FAILURE_CODE.fullmatch(failure_code) is None:
        raise ValueError("cost authority failure code is invalid")
    response = _request(
        descriptor,
        "poison",
        {
            "attempt_id": _validated_attempt_id(attempt_id),
            "failure_code": failure_code,
        },
    )
    if response.get("poisoned") is not True:
        _raise_response_error(response)
    return _validated_evidence(response.get("evidence"))


_EVIDENCE_FIELDS = {
    "limit_usd",
    "settled_cost_usd",
    "remaining_usd",
    "overage_usd",
    "settlement_count",
    "authoritative",
    "exhausted",
    "terminal",
    "provider",
    "model",
    "strategy",
    "failure_code",
}


def _validated_evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EVIDENCE_FIELDS:
        raise CostBudgetPoisoned("authoritative cost evidence is invalid")
    evidence = dict(value)
    for field in ("limit_usd", "settled_cost_usd", "remaining_usd", "overage_usd"):
        if evidence.get(field) != canonical_usd(evidence.get(field)):
            raise CostBudgetPoisoned("authoritative cost evidence is invalid")
    if (
        isinstance(evidence.get("settlement_count"), bool)
        or not isinstance(evidence.get("settlement_count"), int)
        or evidence["settlement_count"] < 0
        or any(
            not isinstance(evidence.get(field), bool)
            for field in ("authoritative", "exhausted", "terminal")
        )
    ):
        raise CostBudgetPoisoned("authoritative cost evidence is invalid")
    _bounded_text(evidence.get("provider"), "provider", 128)
    _bounded_text(evidence.get("model"), "model", 256)
    _bounded_text(evidence.get("strategy"), "strategy", 64)
    failure_code = evidence.get("failure_code")
    if failure_code != "" and (
        not isinstance(failure_code, str)
        or _FAILURE_CODE.fullmatch(failure_code) is None
    ):
        raise CostBudgetPoisoned("authoritative cost evidence is invalid")
    return evidence


def snapshot_cost_budget(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    response = _request(descriptor, "snapshot", {})
    if response.get("snapshot") is not True:
        _raise_response_error(response)
    return _validated_evidence(response.get("evidence"))


class AuthoritativeCostBudget:
    """Parent-owned authenticated authority shared by one provider-call tree."""

    def __init__(
        self,
        *,
        limit_usd: object,
        provider: str,
        model: str,
        strategy: str,
    ) -> None:
        self._limit = Decimal(canonical_usd(limit_usd))
        if self._limit <= 0:
            raise ValueError("cost budget limit must be positive")
        self._provider = _bounded_text(provider, "provider", 128)
        self._model = _bounded_text(model, "model", 256)
        self._strategy = _bounded_text(strategy, "strategy", 64)
        self._settled = Decimal(0)
        self._settlements: dict[str, Decimal] = {}
        self._released: set[str] = set()
        self._inflight: str | None = None
        self._terminal = False
        self._failure_code = ""
        self._state_lock = threading.Lock()
        self._authkey = secrets.token_bytes(_AUTHKEY_BYTES)
        self._seen_nonces: set[str] = set()
        self._nonce_order: deque[str] = deque()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(_MAX_CLIENTS * 2)
        self._listener.settimeout(_ACCEPT_TIMEOUT_SECONDS)
        host, port = self._listener.getsockname()
        self.descriptor: Mapping[str, Any] = {
            "version": _VERSION,
            "host": host,
            "port": port,
            "authkey": base64.b64encode(self._authkey).decode("ascii"),
        }
        self._closed = False
        self._close_event = threading.Event()
        self._clients = threading.BoundedSemaphore(_MAX_CLIENTS)
        self._connections_lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._thread = threading.Thread(
            target=self._serve,
            name="authoritative-cost-budget",
            daemon=True,
        )
        self._thread.start()

    def _evidence(self) -> dict[str, Any]:
        remaining = max(Decimal(0), self._limit - self._settled)
        overage = max(Decimal(0), self._settled - self._limit)
        exhausted = self._settled >= self._limit
        return {
            "limit_usd": canonical_usd(self._limit),
            "settled_cost_usd": canonical_usd(self._settled),
            "remaining_usd": canonical_usd(remaining),
            "overage_usd": canonical_usd(overage),
            "settlement_count": len(self._settlements),
            "authoritative": True,
            "exhausted": exhausted,
            "terminal": self._terminal or exhausted,
            "provider": self._provider,
            "model": self._model,
            "strategy": self._strategy,
            "failure_code": self._failure_code,
        }

    def _poison(self, code: str) -> None:
        if not self._terminal:
            self._terminal = True
            self._failure_code = code

    def _error(self) -> dict[str, Any]:
        return {"failure_code": self._failure_code or "cost_budget_terminal"}

    def _operate(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "snapshot":
            if payload:
                return {"failure_code": "authoritative_cost_request_invalid"}
            return {"snapshot": True, "evidence": self._evidence()}
        attempt_id = _validated_attempt_id(payload.get("attempt_id"))
        if operation == "poison":
            if set(payload) != {"attempt_id", "failure_code"}:
                raise ValueError("cost poison request is invalid")
            code = payload.get("failure_code")
            if not isinstance(code, str) or _FAILURE_CODE.fullmatch(code) is None:
                raise ValueError("cost poison request is invalid")
            self._poison(code)
            return {"poisoned": True, "evidence": self._evidence()}

        expected = {"attempt_id", "provider", "model"}
        if operation == "settle":
            expected |= {"strategy", "amount_usd", "authoritative"}
        if set(payload) != expected:
            raise ValueError("cost authority request fields are invalid")
        route_matches = (
            payload.get("provider") == self._provider
            and payload.get("model") == self._model
        )
        if not route_matches:
            self._poison("authoritative_cost_route_mismatch")
            return self._error()

        if operation == "acquire":
            if attempt_id in self._settlements or attempt_id in self._released:
                self._poison("authoritative_cost_replay")
                return self._error()
            if self._terminal or self._settled >= self._limit:
                if not self._failure_code:
                    self._terminal = True
                    self._failure_code = "budget_exhausted"
                return self._error()
            if self._inflight is None:
                self._inflight = attempt_id
                return {"acquired": True}
            if self._inflight == attempt_id:
                return {"acquired": True}
            return {"acquired": False, "busy": True}

        if operation == "release_unstarted":
            if self._terminal:
                return self._error()
            if self._inflight == attempt_id:
                self._inflight = None
                self._released.add(attempt_id)
            elif attempt_id not in self._released:
                self._poison("authoritative_cost_lease_mismatch")
                return self._error()
            return {"released": True, "evidence": self._evidence()}

        if operation != "settle":
            raise ValueError("cost authority operation is invalid")
        amount = Decimal(canonical_usd(payload.get("amount_usd")))
        existing = self._settlements.get(attempt_id)
        if existing is not None:
            if (
                existing == amount
                and payload.get("strategy") == self._strategy
                and payload.get("authoritative") is True
            ):
                return {"settled": True, "evidence": self._evidence()}
            self._poison("authoritative_cost_contradiction")
            return self._error()
        if self._terminal:
            return self._error()
        if (
            self._inflight != attempt_id
            or payload.get("strategy") != self._strategy
            or payload.get("authoritative") is not True
        ):
            self._poison("authoritative_cost_invalid")
            return self._error()
        self._settlements[attempt_id] = amount
        self._settled += amount
        self._inflight = None
        if self._settled >= self._limit:
            self._terminal = True
            self._failure_code = "budget_exhausted"
        return {"settled": True, "evidence": self._evidence()}

    def _serve(self) -> None:
        while not self._close_event.is_set():
            try:
                connection, _address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._close_event.is_set():
                    return
                continue
            if not self._clients.acquire(blocking=False):
                connection.close()
                continue
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="authoritative-cost-budget-client",
                daemon=True,
            )
            with self._connections_lock:
                if self._close_event.is_set():
                    self._clients.release()
                    connection.close()
                    return
                self._connections.add(connection)
                self._workers.add(worker)
            worker.start()

    def _serve_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(_IO_TIMEOUT_SECONDS)
            request = json.loads(_recv_frame(connection).decode("ascii"))
            if not isinstance(request, Mapping) or set(request) != {
                "version",
                "operation",
                "nonce",
                "payload",
                "mac",
            }:
                return
            request = dict(request)
            request_mac = request.pop("mac", None)
            nonce = request.get("nonce")
            operation = request.get("operation")
            payload = request.get("payload")
            if (
                request.get("version") != _VERSION
                or operation not in {
                    "acquire",
                    "settle",
                    "release_unstarted",
                    "poison",
                    "snapshot",
                }
                or not isinstance(nonce, str)
                or not isinstance(payload, Mapping)
                or not isinstance(request_mac, str)
            ):
                return
            try:
                nonce_bytes = base64.b64decode(nonce, validate=True)
            except (binascii.Error, ValueError):
                return
            if (
                len(nonce_bytes) != _NONCE_BYTES
                or not hmac.compare_digest(request_mac, _mac(self._authkey, request))
            ):
                return
            with self._state_lock:
                if nonce in self._seen_nonces:
                    return
                self._seen_nonces.add(nonce)
                self._nonce_order.append(nonce)
                if len(self._nonce_order) > _REPLAY_WINDOW:
                    expired = self._nonce_order.popleft()
                    self._seen_nonces.discard(expired)
                try:
                    result = self._operate(operation, payload)
                except (TypeError, ValueError):
                    self._poison("authoritative_cost_request_invalid")
                    result = self._error()
            signed = {"version": _VERSION, "nonce": nonce, **result}
            _send_frame(connection, {**signed, "mac": _mac(self._authkey, signed)})
        except (
            EOFError,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
        ):
            pass
        finally:
            try:
                connection.close()
            finally:
                with self._connections_lock:
                    self._connections.discard(connection)
                    self._workers.discard(threading.current_thread())
                self._clients.release()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return self._evidence()

    def close(self) -> None:
        with self._connections_lock:
            if self._closed:
                return
            self._closed = True
            self._close_event.set()
            connections = tuple(self._connections)
        try:
            self._listener.close()
        except OSError:
            pass
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._thread.join(timeout=_IO_TIMEOUT_SECONDS + _ACCEPT_TIMEOUT_SECONDS)
        deadline = time.monotonic() + _IO_TIMEOUT_SECONDS
        while True:
            with self._connections_lock:
                workers = tuple(self._workers)
            if not workers or time.monotonic() >= deadline:
                break
            for worker in workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))


__all__ = [
    "AuthoritativeCostBudget",
    "CostBudgetExhausted",
    "CostBudgetLeaseBusy",
    "CostBudgetPoisoned",
    "CostBudgetTerminalError",
    "acquire_cost_lease",
    "canonical_usd",
    "poison_cost_lease",
    "release_unstarted_cost_lease",
    "settle_cost_lease",
    "snapshot_cost_budget",
    "validate_cost_budget_authority_descriptor",
]
