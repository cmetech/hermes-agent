"""Durable opaque return-route capabilities for generic Gateway plugins."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import secrets
import sqlite3
from typing import Callable, Mapping

from gateway.session import SessionSource
from hermes_cli.plugin_invocation import DeliveryReceipt, PluginInvocationContext


_DEFAULT_CAPABILITY_TTL = timedelta(days=7)
_MAX_DELIVERY_TEXT_BYTES = 256 * 1024
_MAX_ROUTE_VALUE_BYTES = 4096


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(capability: str) -> str:
    return hashlib.sha256(capability.encode("utf-8")).hexdigest()


class GatewayPluginDeliveryPort:
    """Resolve server-minted routes and durably fence each outward send."""

    def __init__(
        self,
        hermes_home: str | Path,
        *,
        profile: str,
        sender: Callable[[Mapping[str, object], str], DeliveryReceipt],
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("profile must be non-empty text")
        if len(profile.encode("utf-8")) > 256:
            raise ValueError("profile is too large")
        if not callable(sender):
            raise TypeError("sender must be callable")
        self.profile = profile.strip()
        self.sender = sender
        self._now = now
        root = Path(hermes_home).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / "gateway-plugin-delivery.sqlite3"
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plugin_return_routes (
                    capability_digest TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    operator_scope TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plugin_delivery_receipts (
                    capability_digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    transport_id TEXT,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (capability_digest, idempotency_key),
                    FOREIGN KEY (capability_digest)
                        REFERENCES plugin_return_routes(capability_digest)
                );
                CREATE INDEX IF NOT EXISTS idx_plugin_delivery_key
                    ON plugin_delivery_receipts(idempotency_key);
                """
            )
        try:
            self.database.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _now_utc(self) -> datetime:
        observed = self._now()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("delivery clock must be timezone-aware")
        return observed.astimezone(timezone.utc)

    @staticmethod
    def _route(source: SessionSource, *, default_profile: str) -> dict[str, object]:
        route = {
            "platform": source.platform.value,
            "chat_id": str(source.chat_id),
            "user_id": str(source.user_id or ""),
            "thread_id": str(source.thread_id) if source.thread_id else None,
            "profile": str(source.profile or default_profile),
        }
        if any(
            len(str(value).encode("utf-8")) > _MAX_ROUTE_VALUE_BYTES
            for value in route.values()
            if value is not None
        ):
            raise ValueError("Gateway return route contains an oversized value")
        return route

    def mint_return_route(
        self,
        source: SessionSource,
        invocation: PluginInvocationContext,
        *,
        ttl: timedelta = _DEFAULT_CAPABILITY_TTL,
    ) -> str:
        """Mint a route only from a verified inbound Gateway adapter event."""
        if not isinstance(source, SessionSource):
            raise TypeError("source must be a SessionSource")
        if (
            invocation.boundary != "gateway"
            or invocation.assurance != "verified_adapter"
        ):
            raise PermissionError("return routes require a verified Gateway adapter")
        route = self._route(source, default_profile=self.profile)
        route_profile = str(source.profile or self.profile)
        if route_profile != self.profile:
            raise PermissionError("Gateway return route profile does not match port")
        platform = str(route["platform"])
        user_id = str(route["user_id"])
        expected_principal = f"gateway:{platform}:{user_id}"
        if not user_id or invocation.principal != expected_principal:
            raise PermissionError("Gateway return route principal does not match source")
        expected_scope = (
            f"gateway:{self.profile}:{platform}:{route['chat_id']}:{user_id}"
        )
        if invocation.operator_scope != expected_scope:
            if f"gateway:{self.profile}:" not in invocation.operator_scope:
                raise PermissionError("Gateway return route profile does not match scope")
            raise PermissionError("Gateway return route scope does not match source")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("return route ttl must be positive")
        try:
            self.prune_expired()
        except sqlite3.Error:
            logger.warning("Gateway plugin delivery retention pass failed")
        observed = self._now_utc()
        capability = f"gwrt_{secrets.token_urlsafe(32)}"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO plugin_return_routes ("
                "capability_digest, profile, principal, operator_scope, route_json, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _digest(capability),
                    self.profile,
                    invocation.principal,
                    invocation.operator_scope,
                    json.dumps(route, sort_keys=True, separators=(",", ":")),
                    observed.isoformat(),
                    (observed + ttl).isoformat(),
                ),
            )
        return capability

    def prune_expired(
        self,
        *,
        limit: int = 100,
        receipt_retention: timedelta = timedelta(days=30),
    ) -> dict[str, int]:
        """Prune expired routes and old terminal receipts in one transaction."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limit must be between 1 and 1000")
        if not isinstance(
            receipt_retention, timedelta
        ) or receipt_retention <= timedelta(0):
            raise ValueError("receipt_retention must be positive")
        observed = self._now_utc()
        timestamp = observed.isoformat()
        receipt_cutoff = (observed - receipt_retention).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipt_rows = connection.execute(
                "SELECT receipts.capability_digest, receipts.idempotency_key "
                "FROM plugin_delivery_receipts AS receipts "
                "JOIN plugin_return_routes AS routes "
                "ON routes.capability_digest=receipts.capability_digest "
                "WHERE routes.expires_at<=? AND receipts.updated_at<=? "
                "AND receipts.state IN ('delivered','permanent_failure','unauthorized') "
                "ORDER BY receipts.updated_at, receipts.capability_digest, "
                "receipts.idempotency_key LIMIT ?",
                (timestamp, receipt_cutoff, limit),
            ).fetchall()
            receipts_pruned = 0
            for row in receipt_rows:
                receipts_pruned += connection.execute(
                    "DELETE FROM plugin_delivery_receipts "
                    "WHERE capability_digest=? AND idempotency_key=? "
                    "AND updated_at<=? "
                    "AND state IN ('delivered','permanent_failure','unauthorized') "
                    "AND EXISTS (SELECT 1 FROM plugin_return_routes AS routes "
                    "WHERE routes.capability_digest=? AND routes.expires_at<=?)",
                    (
                        row["capability_digest"],
                        row["idempotency_key"],
                        receipt_cutoff,
                        row["capability_digest"],
                        timestamp,
                    ),
                ).rowcount
            remaining = limit - receipts_pruned
            route_rows = connection.execute(
                "SELECT routes.capability_digest FROM plugin_return_routes AS routes "
                "WHERE routes.expires_at<=? AND NOT EXISTS ("
                "SELECT 1 FROM plugin_delivery_receipts AS receipts "
                "WHERE receipts.capability_digest=routes.capability_digest) "
                "ORDER BY routes.expires_at, routes.capability_digest LIMIT ?",
                (timestamp, remaining),
            ).fetchall()
            routes_pruned = 0
            for row in route_rows:
                routes_pruned += connection.execute(
                    "DELETE FROM plugin_return_routes WHERE capability_digest=? "
                    "AND expires_at<=? AND NOT EXISTS ("
                    "SELECT 1 FROM plugin_delivery_receipts "
                    "WHERE capability_digest=?)",
                    (
                        row["capability_digest"],
                        timestamp,
                        row["capability_digest"],
                    ),
                ).rowcount
            connection.commit()
            return {"receipts": receipts_pruned, "routes": routes_pruned}
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> DeliveryReceipt:
        state = str(row["state"])
        status = "outcome_uncertain" if state == "sending" else state
        return DeliveryReceipt(
            status=status,
            transport_id=row["transport_id"],
            detail=str(row["detail"] or ""),
        )

    def _complete_delivery(
        self,
        capability_digest: str,
        idempotency_key: str,
        receipt: DeliveryReceipt,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE plugin_delivery_receipts SET state=?, transport_id=?, "
                "detail=?, updated_at=? WHERE capability_digest=? "
                "AND idempotency_key=? AND state='sending'",
                (
                    receipt.status,
                    receipt.transport_id,
                    receipt.detail,
                    self._now_utc().isoformat(),
                    capability_digest,
                    idempotency_key,
                ),
            )

    def _begin_delivery(
        self,
        capability_digest: str,
        idempotency_key: str,
        observed: datetime,
    ) -> DeliveryReceipt | Mapping[str, object]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            route = connection.execute(
                "SELECT * FROM plugin_return_routes WHERE capability_digest=? "
                "AND profile=?",
                (capability_digest, self.profile),
            ).fetchone()
            if route is None or datetime.fromisoformat(str(route["expires_at"])) <= observed:
                connection.rollback()
                return DeliveryReceipt(status="unauthorized")
            prior = connection.execute(
                "SELECT * FROM plugin_delivery_receipts WHERE capability_digest=? "
                "AND idempotency_key=?",
                (capability_digest, idempotency_key),
            ).fetchone()
            if prior is not None:
                if str(prior["state"]) != "retryable_failure":
                    connection.commit()
                    return self._receipt_from_row(prior)
                connection.execute(
                    "UPDATE plugin_delivery_receipts SET state='sending', "
                    "transport_id=NULL, detail='', updated_at=? WHERE "
                    "capability_digest=? AND idempotency_key=? "
                    "AND state='retryable_failure'",
                    (
                        observed.isoformat(),
                        capability_digest,
                        idempotency_key,
                    ),
                )
            else:
                timestamp = observed.isoformat()
                connection.execute(
                    "INSERT INTO plugin_delivery_receipts (capability_digest, "
                    "idempotency_key, state, created_at, updated_at) "
                    "VALUES (?, ?, 'sending', ?, ?)",
                    (capability_digest, idempotency_key, timestamp, timestamp),
                )
            connection.commit()
            return json.loads(str(route["route_json"]))

    def deliver(
        self, capability: str, text: str, idempotency_key: str
    ) -> DeliveryReceipt:
        """Attempt one bounded send, never replaying an uncertain attempt."""
        if not all(
            isinstance(value, str)
            and value
            and len(value.encode("utf-8")) <= _MAX_ROUTE_VALUE_BYTES
            for value in (capability, idempotency_key)
        ):
            return DeliveryReceipt(status="unauthorized")
        if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_DELIVERY_TEXT_BYTES:
            return DeliveryReceipt(status="permanent_failure", detail="invalid_text")
        capability_digest = _digest(capability)
        observed = self._now_utc()
        try:
            prepared = self._begin_delivery(
                capability_digest, idempotency_key, observed
            )
        except sqlite3.Error:
            return DeliveryReceipt(
                status="retryable_failure", detail="delivery_store_unavailable"
            )
        if isinstance(prepared, DeliveryReceipt):
            return prepared
        normalized_route = prepared
        receipt = self.sender(normalized_route, text)
        if not isinstance(receipt, DeliveryReceipt):
            raise TypeError("Gateway plugin sender must return DeliveryReceipt")
        self._complete_delivery(capability_digest, idempotency_key, receipt)
        return receipt


__all__ = ["GatewayPluginDeliveryPort"]
