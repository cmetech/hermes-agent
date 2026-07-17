"""Durable workflow run admission, journal, and materialized projection."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from plugins.workflow.admission import (
    PreparedRunSnapshot,
    RunAdmissionRequest,
    RunAdmissionResult,
)
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import WorkflowPackage
from plugins.workflow.trust import compute_package_digest


class InputSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class NodeClaim:
    run_id: str
    node_id: str
    attempt_id: str
    owner_id: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class ArtifactRef:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str


_NONTERMINAL = {"queued", "running", "waiting_retry", "paused", "interrupted"}
_EXECUTING = {"running"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class RunStore:
    """Profile-scoped workflow state and sole run-creation authority."""

    def __init__(
        self,
        hermes_home: str | Path,
        *,
        max_input_bytes: int = 64 * 1024 * 1024,
        max_executing_runs: int = 4,
        max_queued_runs: int = 100,
        max_paused_runs: int = 100,
        max_nonterminal_runs: int = 200,
        max_start_requests_per_minute: int = 60,
        max_total_workers: int = 4,
    ) -> None:
        self.hermes_home = Path(hermes_home).resolve()
        self.root = self.hermes_home / "workflows"
        self.runs_root = self.root / "runs"
        self.staging_root = self.root / ".staging"
        self.quarantine_root = self.root / ".quarantine"
        self.database = self.root / "admission.sqlite3"
        self.max_input_bytes = max_input_bytes
        self.limits = {
            "executing": max_executing_runs,
            "queued": max_queued_runs,
            "paused": max_paused_runs,
            "nonterminal": max_nonterminal_runs,
            "rate": max_start_requests_per_minute,
            "workers": max_total_workers,
        }
        self._init_lock = threading.Lock()
        self._initialized = False
        self._initialize()

    def _initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            self.runs_root.mkdir(parents=True, exist_ok=True)
            self.staging_root.mkdir(parents=True, exist_ok=True)
            self.quarantine_root.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        workflow_name TEXT NOT NULL,
                        trigger_source TEXT NOT NULL,
                        idempotency_digest TEXT NOT NULL,
                        start_digest TEXT NOT NULL,
                        concurrency_key TEXT NOT NULL,
                        concurrency_policy TEXT NOT NULL,
                        disposition TEXT NOT NULL,
                        status TEXT NOT NULL,
                        queue_position INTEGER,
                        blocked_by_run_id TEXT,
                        run_directory TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(trigger_source, workflow_name, idempotency_digest)
                    );
                    CREATE INDEX IF NOT EXISTS runs_concurrency
                    ON runs(workflow_name, concurrency_key, status);
                    """
                )
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def prepare_empty_snapshot(
        self,
        *,
        definition_digest: str,
        policy_digest: str,
        input_manifest_digest: str,
    ) -> PreparedRunSnapshot:
        staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
        return PreparedRunSnapshot(
            staging, definition_digest, policy_digest, input_manifest_digest, 0
        )

    def prepare_run_snapshot(
        self,
        package: WorkflowPackage,
        *,
        inputs: Mapping[str, str | Path] | None = None,
    ) -> PreparedRunSnapshot:
        staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
        try:
            definition_data = package.workflow_path.read_bytes()
            (staging / "definition.yaml").write_bytes(definition_data)
            policy_data = b"{}\n"
            if package.sidecar_path is not None:
                policy_data = package.sidecar_path.read_bytes()
                (staging / "policy.yaml").write_bytes(policy_data)
            input_manifest: dict[str, dict[str, object]] = {}
            input_digests: dict[str, str] = {}
            input_root = staging / "inputs"
            for name, source_value in sorted((inputs or {}).items()):
                if not name or "/" in name or "\\" in name or name in {".", ".."}:
                    raise InputSnapshotError(f"invalid input name: {name}")
                source = Path(source_value)
                if source.is_symlink():
                    raise InputSnapshotError(f"input symlink is not allowed: {source}")
                try:
                    before = source.stat()
                except OSError as exc:
                    raise InputSnapshotError(
                        f"input is unreadable: {source}: {exc}"
                    ) from exc
                if not source.is_file():
                    raise InputSnapshotError(f"input is not a file: {source}")
                if before.st_size > self.max_input_bytes:
                    raise InputSnapshotError(
                        f"input exceeds {self.max_input_bytes} bytes: {source}"
                    )
                data = source.read_bytes()
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ) or len(data) != before.st_size:
                    raise InputSnapshotError(f"input changed during copy: {source}")
                input_root.mkdir(exist_ok=True)
                target = input_root / name
                target.write_bytes(data)
                digest = _sha256(data)
                input_digests[name] = digest
                input_manifest[name] = {
                    "relative_path": target.relative_to(staging).as_posix(),
                    "source_path": str(source.resolve()),
                    "size_bytes": len(data),
                    "media_type": mimetypes.guess_type(source.name)[0]
                    or "application/octet-stream",
                    "sha256": digest,
                }
            manifest_data = json.dumps(
                input_manifest, sort_keys=True, separators=(",", ":")
            ).encode()
            (staging / "inputs.json").write_bytes(manifest_data)
            nodes = tuple(
                {
                    "id": node.id,
                    "type": node.node_type,
                    "depends_on": list(node.depends_on),
                    "state": "pending" if node.depends_on else "ready",
                    "attempts": [],
                }
                for node in package.definition.nodes
            )
            return PreparedRunSnapshot(
                staging_directory=staging,
                definition_digest=compute_package_digest(package).sha256,
                policy_digest=_sha256(policy_data),
                input_manifest_digest=_sha256(manifest_data),
                reserved_bytes=sum(
                    path.stat().st_size for path in staging.rglob("*") if path.is_file()
                ),
                workflow_name=package.definition.name,
                nodes=nodes,
                input_digests=input_digests,
            )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def clone_prepared_snapshot(
        self, snapshot: PreparedRunSnapshot
    ) -> PreparedRunSnapshot:
        target = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
        shutil.rmtree(target)
        shutil.copytree(snapshot.staging_directory, target)
        return PreparedRunSnapshot(
            target,
            snapshot.definition_digest,
            snapshot.policy_digest,
            snapshot.input_manifest_digest,
            snapshot.reserved_bytes,
            snapshot.workflow_name,
            snapshot.workflow_version,
            snapshot.nodes,
            dict(snapshot.input_digests),
        )

    @staticmethod
    def _start_digest(request: RunAdmissionRequest) -> str:
        material = json.dumps(
            {
                "workflow": request.workflow_name,
                "definition": request.definition_digest,
                "policy": request.policy_digest,
                "inputs": request.input_manifest_digest,
                "trigger": request.trigger_source,
                "concurrency": request.concurrency_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(material)

    def start_run(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult:
        if not request.idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        if (
            request.workflow_name != immutable_snapshot.workflow_name
            and immutable_snapshot.workflow_name
        ):
            raise ValueError("snapshot workflow does not match admission request")
        supplied = (
            request.definition_digest,
            request.policy_digest,
            request.input_manifest_digest,
        )
        actual = (
            immutable_snapshot.definition_digest,
            immutable_snapshot.policy_digest,
            immutable_snapshot.input_manifest_digest,
        )
        if supplied != actual:
            shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
            raise ValueError("snapshot digests do not match admission request")
        key_digest = _sha256(request.idempotency_key.encode())
        start_digest = self._start_digest(request)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT run_id, start_digest FROM runs WHERE trigger_source=? AND workflow_name=? AND idempotency_digest=?",
                (request.trigger_source, request.workflow_name, key_digest),
            ).fetchone()
            if existing:
                connection.commit()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                if existing["start_digest"] == start_digest:
                    return RunAdmissionResult(existing["run_id"], "existing")
                return RunAdmissionResult(None, "rejected", "idempotency_conflict")
            counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
                )
            }
            nonterminal = sum(counts.get(state, 0) for state in _NONTERMINAL)
            if nonterminal >= self.limits["nonterminal"]:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "nonterminal_capacity")
            active = connection.execute(
                "SELECT run_id FROM runs WHERE workflow_name=? AND concurrency_key=? AND status IN ('running','waiting_retry','paused','interrupted') ORDER BY created_at, run_id LIMIT 1",
                (request.workflow_name, request.concurrency_key),
            ).fetchone()
            status = "running"
            disposition = "created"
            blocked_by = None
            queue_position = None
            if active and request.concurrency_policy == "forbid":
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "overlap_forbidden")
            if active and request.concurrency_policy == "queue":
                if counts.get("queued", 0) >= self.limits["queued"]:
                    connection.rollback()
                    shutil.rmtree(
                        immutable_snapshot.staging_directory, ignore_errors=True
                    )
                    return RunAdmissionResult(None, "rejected", "queued_capacity")
                status = "queued"
                disposition = "queued"
                blocked_by = active["run_id"]
                queue_position = counts.get("queued", 0) + 1
            elif counts.get("running", 0) >= min(
                self.limits["executing"], self.limits["workers"]
            ):
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "executing_capacity")
            run_id = uuid.uuid4().hex
            run_directory = self.runs_root / request.workflow_name / run_id
            run_directory.parent.mkdir(parents=True, exist_ok=True)
            os.replace(immutable_snapshot.staging_directory, run_directory)
            now = _utc_now()
            projection = {
                "schema_version": 1,
                "run_id": run_id,
                "workflow": request.workflow_name,
                "workflow_version": immutable_snapshot.workflow_version,
                "definition_digest": request.definition_digest,
                "policy_digest": request.policy_digest,
                "input_manifest_digest": request.input_manifest_digest,
                "trigger": request.trigger_source,
                "idempotency_key_digest": key_digest,
                "concurrency_key": request.concurrency_key,
                "admission_disposition": disposition,
                "queue_position": queue_position,
                "blocked_by_run_id": blocked_by,
                "state_version": 1,
                "event_sequence": 1,
                "status": status,
                "started_at": now if status == "running" else None,
                "created_at": now,
                "updated_at": now,
                "last_semantic_progress_at": None,
                "nodes": {
                    str(node["id"]): dict(node) for node in immutable_snapshot.nodes
                },
                "artifacts": [],
                "warnings": [],
                "last_error": None,
                "pending_interaction": None,
            }
            event = {
                "schema_version": 1,
                "sequence": 1,
                "timestamp": now,
                "run_id": run_id,
                "node_id": None,
                "attempt_id": None,
                "event_type": "run_admitted",
                "payload": {"disposition": disposition, "status": status},
            }
            _atomic_json(run_directory / "run.json", projection)
            with (run_directory / "events.jsonl").open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    request.workflow_name,
                    request.trigger_source,
                    key_digest,
                    start_digest,
                    request.concurrency_key,
                    request.concurrency_policy,
                    disposition,
                    status,
                    queue_position,
                    blocked_by,
                    str(run_directory),
                    now,
                    now,
                ),
            )
            connection.commit()
            return RunAdmissionResult(
                run_id, disposition, None, queue_position, blocked_by
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def run_directory(self, run_id: str) -> Path:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_directory FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Path(row["run_directory"])

    def load_run(self, run_id: str) -> dict[str, object]:
        path = self.run_directory(run_id) / "run.json"
        with workflow_lock(path.parent / ".lock"):
            return json.loads(path.read_text(encoding="utf-8"))

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        projection_updates: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id)
        with workflow_lock(directory / ".lock"):
            projection = json.loads((directory / "run.json").read_text())
            sequence = int(projection["event_sequence"]) + 1
            now = _utc_now()
            event = {
                "schema_version": 1,
                "sequence": sequence,
                "timestamp": now,
                "run_id": run_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "event_type": event_type,
                "payload": dict(payload or {}),
            }
            with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            projection["event_sequence"] = sequence
            projection["state_version"] = int(projection["state_version"]) + 1
            projection["updated_at"] = now
            if event_type == "semantic_progress":
                projection["last_semantic_progress_at"] = now
            projection.update(dict(projection_updates or {}))
            _atomic_json(directory / "run.json", projection)
            return event

    def tail_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        directory = self.run_directory(run_id)
        with workflow_lock(directory / ".lock"):
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
        return tuple(
            event for event in events if int(event["sequence"]) > after_sequence
        )[:limit]

    def list_runs(
        self,
        *,
        workflow: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        clauses = []
        values: list[object] = []
        if workflow:
            clauses.append("workflow_name=?")
            values.append(workflow)
        if status:
            clauses.append("status=?")
            values.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT run_id FROM runs{where} ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return tuple(self.get_run_status(row["run_id"]) for row in rows)

    def get_run_status(self, run_id: str) -> dict[str, object]:
        run = self.load_run(run_id)
        nodes = run.get("nodes", {})
        node_values = list(nodes.values()) if isinstance(nodes, dict) else []
        completed = sum(
            node.get("state") in {"succeeded", "skipped"}
            for node in node_values
            if isinstance(node, dict)
        )
        status = str(run["status"])
        health = (
            "terminal"
            if status in {"succeeded", "failed", "cancelled", "abandoned"}
            else "retry_wait"
            if status == "waiting_retry"
            else "user_wait"
            if status == "paused"
            else "healthy"
        )
        return {
            **run,
            "action": "status",
            "health": health,
            "elapsed_ms": None,
            "current_nodes": [
                node["id"]
                for node in node_values
                if isinstance(node, dict)
                and node.get("state") in {"ready", "claimed", "running"}
            ],
            "progress": {
                "kind": "graph",
                "completed_nodes": completed,
                "total_nodes": len(node_values),
            },
            "attempts": sum(
                len(node.get("attempts", []))
                for node in node_values
                if isinstance(node, dict)
            ),
            "next_retry_at": None,
            "next_actions": self._next_actions(status),
        }

    @staticmethod
    def _next_actions(status: str) -> list[str]:
        if status in {"running", "queued", "waiting_retry", "paused"}:
            return ["status", "events", "cancel"]
        if status in {"failed", "interrupted"}:
            return ["status", "events", "resume", "abandon"]
        return ["status", "events", "cleanup"]

    def claim_node(
        self,
        run_id: str,
        node_id: str,
        owner_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> NodeClaim | None:
        directory = self.run_directory(run_id)
        with workflow_lock(directory / ".lock"):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"].get(node_id)
            if not node or node["state"] != "ready":
                return None
            attempt_id = uuid.uuid4().hex
            expires = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            node["state"] = "claimed"
            node["claim"] = {
                "owner_id": owner_id,
                "attempt_id": attempt_id,
                "lease_expires_at": expires.isoformat(),
            }
            node["attempts"].append({"attempt_id": attempt_id, "state": "claimed"})
            sequence = int(projection["event_sequence"]) + 1
            now = _utc_now()
            event = {
                "schema_version": 1,
                "sequence": sequence,
                "timestamp": now,
                "run_id": run_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "event_type": "node_claimed",
                "payload": {"owner_id": owner_id},
            }
            with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            projection["event_sequence"] = sequence
            projection["state_version"] += 1
            projection["updated_at"] = now
            _atomic_json(directory / "run.json", projection)
            return NodeClaim(run_id, node_id, attempt_id, owner_id, expires)


__all__ = [
    "ArtifactRef",
    "InputSnapshotError",
    "NodeClaim",
    "RunStore",
]
