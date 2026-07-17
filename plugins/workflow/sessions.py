"""Profile-scoped persistent workflow node-session registry."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from plugins.workflow.locks import workflow_lock


@dataclass(frozen=True)
class NodeSessionKey:
    workflow: str
    node_id: str
    scope: str
    provider: str
    profile: str


@dataclass(frozen=True)
class NodeSessionRecord:
    key: NodeSessionKey
    session_id: str
    cache_fingerprint: str
    generation: int
    updated_at: str


class NodeSessionRegistry:
    """Generation-CAS registry; profiles never share its database."""

    def __init__(self, hermes_home: str | Path):
        self.root = Path(hermes_home).resolve() / "workflows"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "node-sessions.sqlite3"
        self.lock_path = self.root / ".node-sessions.lock"
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS node_sessions (
                    workflow TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    cache_fingerprint TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workflow, node_id, scope, provider, profile)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _values(key: NodeSessionKey) -> tuple[str, ...]:
        return (key.workflow, key.node_id, key.scope, key.provider, key.profile)

    def get(self, key: NodeSessionKey) -> NodeSessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM node_sessions WHERE workflow=? AND node_id=? "
                "AND scope=? AND provider=? AND profile=?",
                self._values(key),
            ).fetchone()
        if row is None:
            return None
        return NodeSessionRecord(
            key=key,
            session_id=row["session_id"],
            cache_fingerprint=row["cache_fingerprint"],
            generation=row["generation"],
            updated_at=row["updated_at"],
        )

    def compare_and_set(
        self,
        key: NodeSessionKey,
        expected_generation: int,
        session_id: str,
        cache_fingerprint: str,
    ) -> bool:
        if expected_generation < 0:
            raise ValueError("expected_generation must be non-negative")
        if not session_id or not cache_fingerprint:
            raise ValueError("session_id and cache_fingerprint must be non-empty")
        now = datetime.now(timezone.utc).isoformat()
        with workflow_lock(self.lock_path):
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT generation FROM node_sessions WHERE workflow=? AND "
                    "node_id=? AND scope=? AND provider=? AND profile=?",
                    self._values(key),
                ).fetchone()
                generation = int(row["generation"]) if row is not None else 0
                if generation != expected_generation:
                    connection.rollback()
                    return False
                if row is None:
                    connection.execute(
                        "INSERT INTO node_sessions VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            *self._values(key),
                            session_id,
                            cache_fingerprint,
                            1,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE node_sessions SET session_id=?, cache_fingerprint=?, "
                        "generation=?, updated_at=? WHERE workflow=? AND node_id=? "
                        "AND scope=? AND provider=? AND profile=?",
                        (
                            session_id,
                            cache_fingerprint,
                            generation + 1,
                            now,
                            *self._values(key),
                        ),
                    )
                connection.commit()
                return True

    def reset(
        self, workflow: str, *, scope: str | None = None, node_id: str | None = None
    ) -> int:
        clauses = ["workflow=?"]
        values: list[str] = [workflow]
        if scope is not None:
            clauses.append("scope=?")
            values.append(scope)
        if node_id is not None:
            clauses.append("node_id=?")
            values.append(node_id)
        with workflow_lock(self.lock_path):
            with self._connect() as connection:
                cursor = connection.execute(
                    f"DELETE FROM node_sessions WHERE {' AND '.join(clauses)}", values
                )
                return cursor.rowcount


__all__ = ["NodeSessionKey", "NodeSessionRecord", "NodeSessionRegistry"]
