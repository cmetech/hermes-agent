"""Durable workflow run admission, journal, and materialized projection."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from plugins.workflow.admission import (
    PreparedRunSnapshot,
    RunAdmissionRequest,
    RunAdmissionResult,
)
from plugins.workflow.locks import workflow_lock
from plugins.workflow.models import ApprovalDecision, WorkflowPackage
from plugins.workflow.trust import compute_package_digest
from tools.managed_process import ManagedProcessTree, ProcessIdentity


class InputSnapshotError(ValueError):
    pass


class StorageQuotaError(RuntimeError):
    pass


class JournalRecoveryError(RuntimeError):
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
_PROJECTION_STATUSES = {
    "queued",
    "running",
    "waiting_retry",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
}
_NODE_STATES = {
    "pending",
    "ready",
    "claimed",
    "running",
    "waiting_retry",
    "paused",
    "interrupted",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
}
_SECRET_DIAGNOSTIC = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_ -]?key|token|password|secret)\s*[:=]\s*)"
    r"[^\s,;]+|\bsk-[A-Za-z0-9_-]{8,}\b"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _projection_digest(projection: Mapping[str, object]) -> str:
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256(encoded)


def _recovery_fields(projection: Mapping[str, object]) -> dict[str, object]:
    snapshot = json.loads(json.dumps(projection, sort_keys=True, ensure_ascii=False))
    return {
        "projection": snapshot,
        "projection_sha256": _projection_digest(snapshot),
    }


def _sanitize(value: object, *, key: str = "") -> object:
    lowered = key.lower()
    if any(
        marker in lowered
        for marker in ("secret", "password", "token", "api_key", "authorization")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child): _sanitize(item, key=str(child)) for child, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:2000]


def _sanitize_diagnostic(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET_DIAGNOSTIC.sub("[REDACTED]", value)[:2000]


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


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
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
        max_run_bytes: int = 512 * 1024 * 1024,
        max_profile_bytes: int = 2 * 1024 * 1024 * 1024,
        max_journal_bytes: int | None = None,
    ) -> None:
        self.hermes_home = Path(hermes_home).resolve()
        self.root = self.hermes_home / "workflows"
        self.runs_root = self.root / "runs"
        self.staging_root = self.root / ".staging"
        self.quarantine_root = self.root / ".quarantine"
        self.locks_root = self.root / ".locks"
        self.database = self.root / "admission.sqlite3"
        self.admission_lock = self.root / ".admission.lock"
        self.max_input_bytes = max_input_bytes
        self.max_run_bytes = max_run_bytes
        self.max_profile_bytes = max_profile_bytes
        self.max_journal_bytes = (
            max_journal_bytes
            if max_journal_bytes is not None
            else max(1, max_run_bytes // 2)
        )
        self.limits = {
            "executing": max_executing_runs,
            "queued": max_queued_runs,
            "paused": max_paused_runs,
            "nonterminal": max_nonterminal_runs,
            "rate": max_start_requests_per_minute,
            "workers": max_total_workers,
        }
        self._init_lock = threading.Lock()
        self._admission_gate = threading.RLock()
        self._admission_open = True
        self._initialized = False
        self._initialize()

    def _initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            self.runs_root.mkdir(parents=True, exist_ok=True)
            self.staging_root.mkdir(parents=True, exist_ok=True)
            self.quarantine_root.mkdir(parents=True, exist_ok=True)
            self.locks_root.mkdir(parents=True, exist_ok=True)
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
                        admission_state TEXT NOT NULL DEFAULT 'published',
                        desired_status TEXT,
                        staging_directory TEXT,
                        operator_scope_digest TEXT,
                        UNIQUE(trigger_source, workflow_name, idempotency_digest)
                    );
                    CREATE INDEX IF NOT EXISTS runs_concurrency
                    ON runs(workflow_name, concurrency_key, status);
                    CREATE TABLE IF NOT EXISTS admission_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        run_id TEXT,
                        reason_code TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE TABLE IF NOT EXISTS worker_claims (
                        attempt_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        lease_expires_at TEXT NOT NULL,
                        UNIQUE(run_id, node_id)
                    );
                    CREATE INDEX IF NOT EXISTS worker_claims_lease
                    ON worker_claims(lease_expires_at);
                    """
                )
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(runs)")
                }
                migrations = {
                    "admission_state": (
                        "ALTER TABLE runs ADD COLUMN admission_state TEXT "
                        "NOT NULL DEFAULT 'published'"
                    ),
                    "desired_status": (
                        "ALTER TABLE runs ADD COLUMN desired_status TEXT"
                    ),
                    "staging_directory": (
                        "ALTER TABLE runs ADD COLUMN staging_directory TEXT"
                    ),
                    "operator_scope_digest": (
                        "ALTER TABLE runs ADD COLUMN operator_scope_digest TEXT"
                    ),
                }
                for name, statement in migrations.items():
                    if name not in columns:
                        connection.execute(statement)
            with workflow_lock(self.admission_lock):
                self._reconcile_admission()
                self._reconcile_worker_claims()
            self._initialized = True

    @staticmethod
    def _snapshot_owner_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        from gateway.status import _pid_exists

        return _pid_exists(pid)

    @staticmethod
    def _write_snapshot_owner(directory: Path) -> None:
        _atomic_json(
            directory / ".snapshot-owner.json",
            {"pid": os.getpid(), "created_at": _utc_now()},
        )

    @staticmethod
    def _record_admission_event(
        connection: sqlite3.Connection,
        event_type: str,
        *,
        run_id: str | None = None,
        reason_code: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO admission_events "
            "(timestamp, event_type, run_id, reason_code, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _utc_now(),
                event_type,
                run_id,
                reason_code,
                json.dumps(_sanitize(dict(payload or {})), sort_keys=True),
            ),
        )
        connection.execute(
            "DELETE FROM admission_events WHERE sequence IN ("
            "SELECT sequence FROM admission_events ORDER BY sequence DESC "
            "LIMIT -1 OFFSET 1000)"
        )

    def _reconcile_admission(self) -> None:
        """Converge interrupted publication without inferring run success."""
        with self._connect() as connection:
            reservations = connection.execute(
                "SELECT * FROM runs WHERE admission_state='reserved' "
                "ORDER BY created_at, run_id"
            ).fetchall()
            reserved_staging = {
                str(Path(row["staging_directory"]).resolve())
                for row in reservations
                if row["staging_directory"]
            }
            for row in reservations:
                run_directory = Path(row["run_directory"])
                projection_path = run_directory / "run.json"
                events_path = run_directory / "events.jsonl"
                projection = None
                initial_event = None
                try:
                    projection = json.loads(projection_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    pass
                try:
                    event_lines = [
                        line
                        for line in events_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    if len(event_lines) == 1:
                        initial_event = json.loads(event_lines[0])
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    pass
                if (
                    isinstance(projection, dict)
                    and projection.get("run_id") == row["run_id"]
                    and projection.get("event_sequence") == 1
                    and isinstance(initial_event, dict)
                    and initial_event.get("run_id") == row["run_id"]
                    and initial_event.get("sequence") == 1
                    and initial_event.get("event_type") == "run_admitted"
                ):
                    desired_status = row["desired_status"] or projection["status"]
                    connection.execute(
                        "UPDATE runs SET admission_state='published', status=?, "
                        "desired_status=NULL, staging_directory=NULL, updated_at=? "
                        "WHERE run_id=? AND admission_state='reserved'",
                        (desired_status, _utc_now(), row["run_id"]),
                    )
                    self._record_admission_event(
                        connection,
                        "admission_reservation_recovered",
                        run_id=row["run_id"],
                    )
                    continue
                staging = (
                    Path(row["staging_directory"]) if row["staging_directory"] else None
                )
                if run_directory.exists():
                    quarantine = self.quarantine_root / (
                        f"incomplete-{row['run_id']}-{uuid.uuid4().hex}"
                    )
                    os.replace(run_directory, quarantine)
                    shutil.rmtree(quarantine, ignore_errors=True)
                if staging is not None:
                    shutil.rmtree(staging, ignore_errors=True)
                connection.execute("DELETE FROM runs WHERE run_id=?", (row["run_id"],))
                self._record_admission_event(
                    connection,
                    "admission_reservation_released",
                    run_id=row["run_id"],
                    reason_code="incomplete_publication",
                )

            known_directories = {
                str(Path(row["run_directory"]).resolve())
                for row in connection.execute(
                    "SELECT run_directory FROM runs WHERE admission_state='published'"
                )
            }
            for workflow_directory in self.runs_root.iterdir():
                if not workflow_directory.is_dir():
                    continue
                for run_directory in workflow_directory.iterdir():
                    if not run_directory.is_dir():
                        continue
                    if str(run_directory.resolve()) in known_directories:
                        continue
                    quarantine = self.quarantine_root / (
                        f"orphan-{run_directory.name}-{uuid.uuid4().hex}"
                    )
                    os.replace(run_directory, quarantine)
                    shutil.rmtree(quarantine, ignore_errors=True)
                    self._record_admission_event(
                        connection,
                        "orphan_run_removed",
                        run_id=run_directory.name,
                        reason_code="missing_admission_reservation",
                    )

            for staging in self.staging_root.iterdir():
                if not staging.is_dir() or str(staging.resolve()) in reserved_staging:
                    continue
                marker = staging / ".snapshot-owner.json"
                try:
                    owner = json.loads(marker.read_text(encoding="utf-8"))
                    owner_alive = self._snapshot_owner_alive(int(owner["pid"]))
                    created_at = datetime.fromisoformat(str(owner["created_at"]))
                    snapshot_fresh = (
                        datetime.now(timezone.utc) - created_at
                    ) < timedelta(hours=1)
                except (FileNotFoundError, KeyError, TypeError, ValueError, OSError):
                    owner_alive = False
                    snapshot_fresh = False
                if owner_alive and snapshot_fresh:
                    continue
                shutil.rmtree(staging, ignore_errors=True)
                self._record_admission_event(
                    connection,
                    "orphan_snapshot_removed",
                    reason_code="snapshot_owner_exited",
                )

    def list_admission_events(
        self, *, limit: int = 100
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, timestamp, event_type, run_id, reason_code, "
                "payload_json FROM admission_events ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "run_id": row["run_id"],
                "reason_code": row["reason_code"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def _reconcile_worker_claims(self) -> None:
        """Converge the capacity ledger with durable run projections."""
        active: dict[str, tuple[str, str, str, str]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, run_directory FROM runs "
                "WHERE admission_state='published' AND status IN "
                "('running','waiting_retry','paused','interrupted')"
            ).fetchall()
        for row in rows:
            try:
                projection = json.loads(
                    (Path(row["run_directory"]) / "run.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
            for node_id, node in projection.get("nodes", {}).items():
                claim = node.get("claim") if isinstance(node, dict) else None
                if not isinstance(claim, dict):
                    continue
                attempt_id = str(claim.get("attempt_id", ""))
                if attempt_id:
                    active[attempt_id] = (
                        row["run_id"],
                        str(node_id),
                        str(claim.get("owner_id", "recovered")),
                        str(claim.get("lease_expires_at", _utc_now())),
                    )
        with self._connect() as connection:
            if active:
                placeholders = ",".join("?" for _ in active)
                connection.execute(
                    f"DELETE FROM worker_claims WHERE attempt_id NOT IN ({placeholders})",
                    tuple(active),
                )
            else:
                connection.execute("DELETE FROM worker_claims")
            for attempt_id, values in active.items():
                connection.execute(
                    "INSERT INTO worker_claims "
                    "(attempt_id, run_id, node_id, owner_id, lease_expires_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(attempt_id) DO UPDATE SET "
                    "run_id=excluded.run_id, node_id=excluded.node_id, "
                    "owner_id=excluded.owner_id, "
                    "lease_expires_at=excluded.lease_expires_at",
                    (attempt_id, *values),
                )

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
        self._ensure_free_disk()
        with workflow_lock(self.admission_lock):
            staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(staging)
        return PreparedRunSnapshot(
            staging, definition_digest, policy_digest, input_manifest_digest, 0
        )

    def prepare_run_snapshot(
        self,
        package: WorkflowPackage,
        *,
        inputs: Mapping[str, str | Path] | None = None,
        values: Mapping[str, str] | None = None,
    ) -> PreparedRunSnapshot:
        self._ensure_free_disk()
        with workflow_lock(self.admission_lock):
            staging = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(staging)
        try:
            package_digest = compute_package_digest(package)
            definition_data = package.workflow_path.read_bytes()
            (staging / "definition.yaml").write_bytes(definition_data)
            policy_data = b"{}\n"
            if package.sidecar_path is not None:
                policy_data = package.sidecar_path.read_bytes()
                (staging / "policy.yaml").write_bytes(policy_data)
            workflow_relative = (
                package.workflow_path
                .resolve()
                .relative_to(package.root.resolve())
                .as_posix()
            )
            sidecar_relative = (
                package.sidecar_path
                .resolve()
                .relative_to(package.root.resolve())
                .as_posix()
                if package.sidecar_path is not None
                else None
            )
            for relative in package_digest.covered_relative_paths:
                if relative in {workflow_relative, sidecar_relative}:
                    continue
                source = package.root / relative
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            node_skill_digests: dict[str, str] = {}
            node_agent_skill_digests: dict[str, str] = {}
            for node in package.definition.nodes:
                skills = tuple(node.options.get("skills", ()))
                if not skills:
                    continue
                from agent.skill_commands import build_preloaded_skills_prompt

                skill_text, _loaded, missing = build_preloaded_skills_prompt(
                    list(skills), task_id=None
                )
                if missing:
                    raise InputSnapshotError(
                        f"workflow node {node.id} references missing skills: "
                        + ", ".join(missing)
                    )
                target = staging / "node-skills" / f"{node.id}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(skill_text, encoding="utf-8")
                node_skill_digests[node.id] = _sha256(skill_text.encode())

            for node in package.definition.nodes:
                for agent_id, definition in node.options.get("agents", {}).items():
                    skills = tuple(definition.get("skills", ()))
                    if not skills:
                        continue
                    from agent.skill_commands import build_preloaded_skills_prompt

                    skill_text, _loaded, missing = build_preloaded_skills_prompt(
                        list(skills), task_id=None
                    )
                    if missing:
                        raise InputSnapshotError(
                            f"workflow inline agent {agent_id} references missing skills: "
                            + ", ".join(missing)
                        )
                    target = staging / "node-agent-skills" / node.id / f"{agent_id}.md"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(skill_text, encoding="utf-8")
                    node_agent_skill_digests[f"{node.id}/{agent_id}"] = _sha256(
                        skill_text.encode()
                    )
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
                try:
                    data = source.read_bytes()
                    after = source.stat()
                except OSError as exc:
                    raise InputSnapshotError(
                        f"input is unreadable: {source}: {exc}"
                    ) from exc
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
            for name, value in sorted((values or {}).items()):
                if name in input_manifest or not name or "/" in name or "\\" in name:
                    raise InputSnapshotError(f"invalid or duplicate input name: {name}")
                data = value.encode("utf-8")
                if len(data) > self.max_input_bytes:
                    raise InputSnapshotError(
                        f"input exceeds {self.max_input_bytes} bytes: {name}"
                    )
                input_root.mkdir(exist_ok=True)
                target = input_root / f"{name}.txt"
                target.write_bytes(data)
                digest = _sha256(data)
                input_digests[name] = digest
                input_manifest[name] = {
                    "relative_path": target.relative_to(staging).as_posix(),
                    "size_bytes": len(data),
                    "media_type": "text/plain",
                    "sha256": digest,
                }
            manifest_data = json.dumps(
                input_manifest, sort_keys=True, separators=(",", ":")
            ).encode()
            (staging / "inputs.json").write_bytes(manifest_data)
            snapshot_manifest = json.dumps(
                {
                    "inputs_sha256": _sha256(manifest_data),
                    "node_skills": node_skill_digests,
                    "node_agent_skills": node_agent_skill_digests,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            (staging / "resources.json").write_bytes(snapshot_manifest)
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
            reserved_bytes = sum(
                path.stat().st_size for path in staging.rglob("*") if path.is_file()
            )
            if reserved_bytes > self.max_run_bytes:
                raise StorageQuotaError(
                    f"run_storage_quota exceeded: {reserved_bytes} > {self.max_run_bytes}"
                )
            return PreparedRunSnapshot(
                staging_directory=staging,
                definition_digest=package_digest.sha256,
                policy_digest=_sha256(policy_data),
                input_manifest_digest=_sha256(snapshot_manifest),
                reserved_bytes=reserved_bytes,
                workflow_name=package.definition.name,
                nodes=nodes,
                input_digests=input_digests,
            )
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _ensure_free_disk(self) -> None:
        usage = shutil.disk_usage(self.root)
        watermark = max(1024**3, min(5 * 1024**3, int(usage.total * 0.05)))
        if usage.free < watermark:
            raise StorageQuotaError(
                f"free_disk_watermark not met: {usage.free} < {watermark}"
            )

    @staticmethod
    def _directory_bytes(directory: Path) -> int:
        total = 0
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                # Atomic projection writes rename their temporary file while a
                # concurrent capacity scan is walking the run tree.
                continue
        return total

    def _ensure_run_capacity(
        self,
        directory: Path,
        projection: Mapping[str, object],
        *,
        journal_reserve_bytes: int = 0,
    ) -> None:
        """Reserve enough durable space before allocating a worker."""
        projection_bytes = len(
            json.dumps(projection, sort_keys=True, ensure_ascii=False).encode("utf-8")
        )
        node_count = len(projection.get("nodes", {}))
        # One attempt can claim/start/complete, update run state, and resolve a
        # full downstream ready layer. Heartbeats are compact deltas.
        event_reserve = (node_count + 8) * (
            projection_bytes + 2048
        ) + journal_reserve_bytes
        journal_bytes = (directory / "events.jsonl").stat().st_size
        if journal_bytes + event_reserve > self.max_journal_bytes:
            raise StorageQuotaError(
                "event_journal_quota would be exceeded before worker allocation"
            )
        run_bytes = self._directory_bytes(directory)
        output_reserve = 1024 * 1024
        required = event_reserve + output_reserve
        if run_bytes + required > self.max_run_bytes:
            raise StorageQuotaError(
                "run_storage_quota would be exceeded before worker allocation"
            )
        profile_bytes = self._directory_bytes(self.runs_root)
        if profile_bytes + required > self.max_profile_bytes:
            raise StorageQuotaError(
                "profile_storage_quota would be exceeded before worker allocation"
            )

    def clone_prepared_snapshot(
        self, snapshot: PreparedRunSnapshot
    ) -> PreparedRunSnapshot:
        with workflow_lock(self.admission_lock):
            target = Path(tempfile.mkdtemp(prefix="run-", dir=self.staging_root))
            self._write_snapshot_owner(target)
        shutil.copytree(
            snapshot.staging_directory,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".snapshot-owner.json"),
        )
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
    def _scope_digest(operator_scope: str | None) -> str | None:
        if operator_scope is None:
            return None
        if not operator_scope:
            raise ValueError("operator_scope must not be empty")
        return _sha256(operator_scope.encode())

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
                "operator_scope_digest": RunStore._scope_digest(request.operator_scope),
                "run_metadata": dict(sorted((request.run_metadata or {}).items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _sha256(material)

    def close_admission(self) -> None:
        """Atomically prevent this coordinator from publishing another run."""
        with self._admission_gate:
            self._admission_open = False

    def start_run(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult:
        with self._admission_gate:
            if not self._admission_open:
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "admission_closed")
            with workflow_lock(self.admission_lock):
                self._reconcile_admission()
                return self._start_run_locked(
                    request, immutable_snapshot=immutable_snapshot
                )

    def _start_run_locked(
        self,
        request: RunAdmissionRequest,
        *,
        immutable_snapshot: PreparedRunSnapshot,
    ) -> RunAdmissionResult:
        if not request.idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        metadata = dict(request.run_metadata or {})
        if any(
            not isinstance(key, str)
            or not key
            or len(key) > 64
            or not isinstance(value, str)
            or len(value) > 512
            for key, value in metadata.items()
        ):
            raise ValueError("run_metadata must contain bounded string pairs")
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
        operator_scope_digest = self._scope_digest(request.operator_scope)
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
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            recent_starts = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE created_at>=?", (cutoff,)
            ).fetchone()[0]
            if recent_starts >= self.limits["rate"]:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "start_rate_capacity")
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
            profile_bytes = sum(
                path.stat().st_size
                for path in self.runs_root.rglob("*")
                if path.is_file()
            )
            if (
                profile_bytes + immutable_snapshot.reserved_bytes
                > self.max_profile_bytes
            ):
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "profile_storage_quota")
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
            elif counts.get("running", 0) >= self.limits["executing"]:
                connection.rollback()
                shutil.rmtree(immutable_snapshot.staging_directory, ignore_errors=True)
                return RunAdmissionResult(None, "rejected", "executing_capacity")
            run_id = uuid.uuid4().hex
            run_directory = self.runs_root / request.workflow_name / run_id
            run_directory.parent.mkdir(parents=True, exist_ok=True)
            now = _utc_now()
            connection.execute(
                "INSERT INTO runs ("
                "run_id, workflow_name, trigger_source, idempotency_digest, "
                "start_digest, concurrency_key, concurrency_policy, disposition, "
                "status, queue_position, blocked_by_run_id, run_directory, "
                "created_at, updated_at, admission_state, desired_status, "
                "staging_directory, operator_scope_digest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    request.workflow_name,
                    request.trigger_source,
                    key_digest,
                    start_digest,
                    request.concurrency_key,
                    request.concurrency_policy,
                    disposition,
                    "admitting",
                    queue_position,
                    blocked_by,
                    str(run_directory),
                    now,
                    now,
                    "reserved",
                    status,
                    str(immutable_snapshot.staging_directory),
                    operator_scope_digest,
                ),
            )
            connection.commit()
            self._publish_reserved_run(
                run_id=run_id,
                run_directory=run_directory,
                request=request,
                snapshot=immutable_snapshot,
                key_digest=key_digest,
                operator_scope_digest=operator_scope_digest,
                disposition=disposition,
                status=status,
                queue_position=queue_position,
                blocked_by=blocked_by,
                created_at=now,
            )
            self._mark_reservation_published(run_id, status=status)
            return RunAdmissionResult(
                run_id, disposition, None, queue_position, blocked_by
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _publish_reserved_run(
        self,
        *,
        run_id: str,
        run_directory: Path,
        request: RunAdmissionRequest,
        snapshot: PreparedRunSnapshot,
        key_digest: str,
        operator_scope_digest: str | None,
        disposition: str,
        status: str,
        queue_position: int | None,
        blocked_by: str | None,
        created_at: str,
    ) -> None:
        (snapshot.staging_directory / ".snapshot-owner.json").unlink(missing_ok=True)
        os.replace(snapshot.staging_directory, run_directory)
        (run_directory / ".lock").touch(exist_ok=True)
        now = created_at
        projection = {
            "schema_version": 1,
            "run_id": run_id,
            "workflow": request.workflow_name,
            "workflow_version": snapshot.workflow_version,
            "definition_digest": request.definition_digest,
            "policy_digest": request.policy_digest,
            "input_manifest_digest": request.input_manifest_digest,
            "trigger": request.trigger_source,
            "idempotency_key_digest": key_digest,
            "operator_scope_digest": operator_scope_digest,
            "run_metadata": dict(sorted((request.run_metadata or {}).items())),
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
            "nodes": {str(node["id"]): dict(node) for node in snapshot.nodes},
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
            **_recovery_fields(projection),
        }
        _atomic_json(run_directory / "run.json", projection)
        with (run_directory / "events.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _mark_reservation_published(self, run_id: str, *, status: str) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE runs SET admission_state='published', status=?, "
                "desired_status=NULL, staging_directory=NULL, updated_at=? "
                "WHERE run_id=? AND admission_state='reserved'",
                (status, _utc_now(), run_id),
            ).rowcount
        if updated != 1:
            raise RuntimeError(f"admission reservation is not active: {run_id}")

    def run_directory(self, run_id: str, *, operator_scope: str | None = None) -> Path:
        scope_digest = self._scope_digest(operator_scope)
        scope_clause = (
            " AND operator_scope_digest=?" if operator_scope is not None else ""
        )
        values: tuple[object, ...] = (
            (run_id, scope_digest) if operator_scope is not None else (run_id,)
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_directory FROM runs "
                f"WHERE run_id=? AND admission_state='published'{scope_clause}",
                values,
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Path(row["run_directory"])

    def _run_lock_path(self, run_id: str) -> Path:
        return self.locks_root / f"{run_id}.lock"

    def load_run(
        self, run_id: str, *, operator_scope: str | None = None
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        path = directory / "run.json"
        with workflow_lock(self._run_lock_path(run_id)):
            # Cleanup first atomically moves a run out of the published tree,
            # then removes its database row after deleting the quarantine copy.
            # A reader may have resolved the row before that move.  Treat the
            # vanished directory as removal instead of trying to rebuild it.
            if not directory.is_dir():
                raise KeyError(run_id)
            try:
                projection = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                projection = None
            except (json.JSONDecodeError, OSError):
                projection = None
            if self._valid_projection(projection, run_id=run_id):
                journal_current = self._journal_matches_projection(
                    directory, projection=projection, run_id=run_id
                )
                if journal_current:
                    return projection
            if path.exists():
                quarantine = directory / f"run.json.corrupt-{uuid.uuid4().hex}"
                os.replace(path, quarantine)
            rebuilt = self._rebuild_projection(directory, run_id=run_id)
            _atomic_json(path, rebuilt)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (rebuilt["status"], rebuilt["updated_at"], run_id),
                )
            return rebuilt

    def _journal_matches_projection(
        self,
        directory: Path,
        *,
        projection: Mapping[str, object],
        run_id: str,
    ) -> bool:
        """Validate the durable journal head without replaying on every read."""
        try:
            lines = (
                (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
            )
        except OSError as exc:
            raise JournalRecoveryError(f"journal unavailable: {exc}") from exc
        populated = [
            (number, line) for number, line in enumerate(lines, 1) if line.strip()
        ]
        if not populated:
            raise JournalRecoveryError("journal contains no events")
        line_number, line = populated[-1]
        try:
            latest = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalRecoveryError(
                f"malformed journal event at line {line_number}"
            ) from exc
        if latest.get("run_id") != run_id:
            raise JournalRecoveryError("journal run identity mismatch")
        if latest["sequence"] < projection["event_sequence"]:
            raise JournalRecoveryError("projection is ahead of its journal")
        if latest["sequence"] > projection["event_sequence"]:
            return False
        if "projection_sha256" in latest:
            snapshot = latest.get("projection")
            if snapshot is not None:
                if not self._valid_projection(snapshot, run_id=run_id):
                    raise JournalRecoveryError("journal head has no valid projection")
                if latest["projection_sha256"] != _projection_digest(snapshot):
                    raise JournalRecoveryError("journal projection digest mismatch")
            return latest.get("projection_sha256") == _projection_digest(projection)
        return True

    @staticmethod
    def _valid_projection(value: object, *, run_id: str) -> bool:
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            return False
        if value.get("status") not in _PROJECTION_STATUSES:
            return False
        for field in ("event_sequence", "state_version"):
            if (
                isinstance(value.get(field), bool)
                or not isinstance(value.get(field), int)
                or value[field] < 1
            ):
                return False
        if not isinstance(value.get("artifacts"), list) or not isinstance(
            value.get("warnings"), list
        ):
            return False
        nodes = value.get("nodes")
        if not isinstance(nodes, dict) or not nodes:
            return False
        for node_id, node in nodes.items():
            if (
                not isinstance(node_id, str)
                or not isinstance(node, dict)
                or node.get("id") != node_id
                or node.get("state") not in _NODE_STATES
                or not isinstance(node.get("depends_on"), list)
                or not isinstance(node.get("attempts"), list)
            ):
                return False
            claim = node.get("claim")
            if claim is not None and (
                not isinstance(claim, dict)
                or not isinstance(claim.get("attempt_id"), str)
                or not isinstance(claim.get("lease_expires_at"), str)
            ):
                return False
        return True

    def _rebuild_projection(self, directory: Path, *, run_id: str) -> dict[str, object]:
        latest = None
        expected_sequence = 1
        try:
            lines = (
                (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
            )
        except OSError as exc:
            raise JournalRecoveryError(f"journal unavailable: {exc}") from exc
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalRecoveryError(
                    f"malformed journal event at line {line_number}"
                ) from exc
            if event.get("sequence") != expected_sequence:
                raise JournalRecoveryError(
                    f"journal sequence gap: expected {expected_sequence}, "
                    f"received {event.get('sequence')}"
                )
            if event.get("run_id") != run_id:
                raise JournalRecoveryError("journal run identity mismatch")
            snapshot = event.get("projection")
            checksum = event.get("projection_sha256")
            if self._valid_projection(snapshot, run_id=run_id):
                if snapshot["event_sequence"] != expected_sequence:
                    raise JournalRecoveryError("journal projection sequence mismatch")
                if checksum != _projection_digest(snapshot):
                    raise JournalRecoveryError("journal projection digest mismatch")
                latest = snapshot
            elif event.get("event_type") == "node_heartbeat" and latest is not None:
                node = latest["nodes"].get(event.get("node_id"))
                claim = node.get("claim") if isinstance(node, dict) else None
                if not isinstance(claim, dict) or claim.get("attempt_id") != event.get(
                    "attempt_id"
                ):
                    raise JournalRecoveryError("heartbeat claim identity mismatch")
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    raise JournalRecoveryError("heartbeat payload is malformed")
                required = {
                    "heartbeat_at",
                    "heartbeat_monotonic",
                    "lease_expires_at",
                    "lease_seconds",
                }
                if not required <= payload.keys():
                    raise JournalRecoveryError("heartbeat payload is incomplete")
                claim.update({key: payload[key] for key in required})
                latest["event_sequence"] = expected_sequence
                latest["state_version"] = int(latest["state_version"]) + 1
                latest["updated_at"] = event.get("timestamp")
                if checksum != _projection_digest(latest):
                    raise JournalRecoveryError("journal projection digest mismatch")
            else:
                raise JournalRecoveryError(
                    f"journal event {expected_sequence} has no valid recovery data"
                )
            expected_sequence += 1
        if latest is None:
            raise JournalRecoveryError("journal contains no recoverable projection")
        return latest

    def try_promote_run(self, run_id: str) -> bool:
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT workflow_name, concurrency_key, status FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None or row["status"] != "queued":
                    connection.rollback()
                    return bool(row and row["status"] == "running")
                active = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id<>? AND workflow_name=? AND concurrency_key=? AND status IN ('running','waiting_retry','paused','interrupted') LIMIT 1",
                    (run_id, row["workflow_name"], row["concurrency_key"]),
                ).fetchone()
                running = connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE status='running'"
                ).fetchone()[0]
                if active or running >= self.limits["executing"]:
                    connection.rollback()
                    return False
                projection = json.loads((directory / "run.json").read_text())
                now = _utc_now()
                projection["status"] = "running"
                projection["started_at"] = now
                projection["queue_position"] = None
                projection["blocked_by_run_id"] = None
                self._append_locked(directory, projection, "run_promoted")
                connection.execute(
                    "UPDATE runs SET status='running', queue_position=NULL, blocked_by_run_id=NULL, updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        projection_updates: Mapping[str, object] | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id)
        with workflow_lock(
            self._run_lock_path(run_id), timeout_seconds=lock_timeout_seconds
        ):
            projection = json.loads((directory / "run.json").read_text())
            if event_type == "semantic_progress":
                projection["last_semantic_progress_at"] = _utc_now()
            projection.update(dict(projection_updates or {}))
            return self._append_locked(
                directory,
                projection,
                event_type,
                payload,
                node_id=node_id,
                attempt_id=attempt_id,
            )

    def tail_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
        selected = tuple(
            event for event in events if int(event["sequence"]) > after_sequence
        )[:limit]
        public_events = []
        for event in selected:
            event = dict(event)
            event.pop("projection", None)
            event.pop("projection_sha256", None)
            public_events.append(_sanitize(event))
        return tuple(public_events)

    def events_after(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 200,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Return a bounded monotonic event page for REST/desktop consumers."""
        events = self.tail_events(
            run_id,
            after_sequence=after,
            limit=max(1, min(int(limit), 200)),
            operator_scope=operator_scope,
        )
        return {
            "schema_version": 1,
            "events": events,
            "next_cursor": int(events[-1]["sequence"]) if events else after,
            "cursor_reset": False,
        }

    def list_runs(
        self,
        *,
        workflow: str | None = None,
        status: str | None = None,
        limit: int = 100,
        operator_scope: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        clauses = ["admission_state='published'"]
        values: list[object] = []
        if workflow:
            clauses.append("workflow_name=?")
            values.append(workflow)
        if status:
            clauses.append("status=?")
            values.append(status)
        if operator_scope is not None:
            clauses.append("operator_scope_digest=?")
            values.append(self._scope_digest(operator_scope))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT run_id FROM runs{where} ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return tuple(
            self.get_run_status(row["run_id"], operator_scope=operator_scope)
            for row in rows
        )

    def get_run_status(
        self, run_id: str, *, operator_scope: str | None = None
    ) -> dict[str, object]:
        run = self.load_run(run_id, operator_scope=operator_scope)
        nodes = run.get("nodes", {})
        node_values = list(nodes.values()) if isinstance(nodes, dict) else []
        completed = sum(
            node.get("state") in {"succeeded", "skipped"}
            for node in node_values
            if isinstance(node, dict)
        )
        status = str(run["status"])
        retry_times = [
            node["next_attempt_at"]
            for node in node_values
            if isinstance(node, dict)
            and node.get("state") == "waiting_retry"
            and isinstance(node.get("next_attempt_at"), str)
        ]
        pending_interaction = next(
            (
                {**pending, "node_id": node.get("id")}
                if isinstance(pending, dict)
                else {"type": pending, "node_id": node.get("id")}
                for node in node_values
                if isinstance(node, dict)
                and (pending := node.get("pending_interaction")) is not None
            ),
            None,
        )
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
            "next_retry_at": min(retry_times) if retry_times else None,
            "pending_interaction": pending_interaction,
            "next_actions": self._next_actions(status, pending_interaction),
        }

    @staticmethod
    def _next_actions(
        status: str, pending_interaction: dict[str, object] | None = None
    ) -> list[str]:
        if status == "paused" and pending_interaction:
            interaction_type = pending_interaction.get("type")
            if interaction_type in {"approval", "workflow_approval"}:
                return ["status", "events", "approve", "reject", "cancel"]
            if interaction_type == "loop_input":
                return ["status", "events", "provide-input", "cancel"]
            if interaction_type == "reconcile":
                return ["status", "events", "reconcile", "cancel"]
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
        now: datetime | None = None,
        monotonic_now: float | None = None,
        journal_reserve_bytes: int = 0,
    ) -> NodeClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._ensure_free_disk()
        directory = self.run_directory(run_id)
        with workflow_lock(self.admission_lock):
            with workflow_lock(self._run_lock_path(run_id)):
                projection = json.loads((directory / "run.json").read_text())
                node = projection["nodes"].get(node_id)
                if (
                    projection.get("desired_status") is not None
                    or not node
                    or node["state"] != "ready"
                ):
                    return None
                self._ensure_run_capacity(
                    directory,
                    projection,
                    journal_reserve_bytes=journal_reserve_bytes,
                )
                connection = self._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    active_workers = connection.execute(
                        "SELECT COUNT(*) FROM worker_claims"
                    ).fetchone()[0]
                    if active_workers >= self.limits["workers"]:
                        connection.rollback()
                        return None
                    attempt_id = uuid.uuid4().hex
                    instant = now or datetime.now(timezone.utc)
                    monotonic_instant = (
                        float(monotonic_now)
                        if monotonic_now is not None
                        else time.monotonic()
                    )
                    expires = instant + timedelta(seconds=lease_seconds)
                    connection.execute(
                        "INSERT INTO worker_claims "
                        "(attempt_id, run_id, node_id, owner_id, lease_expires_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (attempt_id, run_id, node_id, owner_id, expires.isoformat()),
                    )
                    node["state"] = "claimed"
                    node["claim"] = {
                        "owner_id": owner_id,
                        "attempt_id": attempt_id,
                        "lease_expires_at": expires.isoformat(),
                        "heartbeat_at": instant.isoformat(),
                        "heartbeat_monotonic": monotonic_instant,
                        "lease_seconds": float(lease_seconds),
                    }
                    node["attempts"].append({
                        "attempt_id": attempt_id,
                        "state": "claimed",
                    })
                    self._append_locked(
                        directory,
                        projection,
                        "node_claimed",
                        {"owner_id": owner_id},
                        node_id=node_id,
                        attempt_id=attempt_id,
                    )
                    connection.commit()
                    return NodeClaim(run_id, node_id, attempt_id, owner_id, expires)
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()

    def _release_worker_claim(self, attempt_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM worker_claims WHERE attempt_id=?", (attempt_id,)
            )

    def mark_node_started(self, claim: NodeClaim) -> None:
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if (
                projection["status"] != "running"
                or projection.get("desired_status") is not None
            ):
                raise RuntimeError("stale node start for terminal run")
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale node claim")
            node["state"] = "running"
            node["attempts"][-1]["state"] = "running"
            self._append_locked(
                directory,
                projection,
                "node_started",
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )

    def record_process_started(
        self, claim: NodeClaim, identity: ProcessIdentity
    ) -> bool:
        """Durably bind an owned process identity to its active node claim."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if (
                projection["status"] != "running"
                or projection.get("desired_status") is not None
                or active.get("attempt_id") != claim.attempt_id
            ):
                return False
            serialized = {
                "pid": identity.pid,
                "start_time": identity.start_time,
                "group_id": identity.group_id,
            }
            active["process_identity"] = serialized
            node["attempts"][-1]["process_identity"] = serialized
            self._append_locked(
                directory,
                projection,
                "process_started",
                {"process_identity": serialized},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            return True

    def record_process_stopped(
        self,
        claim: NodeClaim,
        identity: ProcessIdentity,
        *,
        cleaned: bool,
    ) -> bool:
        """Record cleanup only while the same claim still owns the identity."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            serialized = active.get("process_identity")
            if (
                active.get("attempt_id") != claim.attempt_id
                or not isinstance(serialized, dict)
                or serialized.get("pid") != identity.pid
            ):
                return False
            event_type = "process_reaped" if cleaned else "cleanup_failed"
            if cleaned:
                active.pop("process_identity", None)
                node["attempts"][-1].pop("process_identity", None)
            self._append_locked(
                directory,
                projection,
                event_type,
                {"pid": identity.pid, "cleanup_complete": cleaned},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            return True

    def complete_node(
        self,
        claim: NodeClaim,
        *,
        status: str,
        artifacts: Iterable[ArtifactRef] = (),
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if status not in {
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "paused",
        }:
            raise ValueError(f"invalid node completion state: {status}")
        directory = self.run_directory(claim.run_id)
        capacity_guard = (
            workflow_lock(self.admission_lock) if status == "paused" else nullcontext()
        )
        with capacity_guard, workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale node completion")
            if (
                projection["status"] in {"cancelled", "abandoned"}
                or projection.get("desired_status") == "cancelled"
            ) and status != "cancelled":
                raise RuntimeError("stale completion for terminal run")
            if status == "paused":
                with self._connect() as connection:
                    paused = connection.execute(
                        "SELECT COUNT(*) FROM runs WHERE status='paused' AND run_id<>?",
                        (claim.run_id,),
                    ).fetchone()[0]
                if paused >= self.limits["paused"]:
                    status = "failed"
                    error_code = "paused_capacity"
                    error_message = "profile paused-run capacity is exhausted"
                    metadata = {
                        key: value
                        for key, value in dict(metadata or {}).items()
                        if key != "pending_interaction"
                    }
            node["state"] = status
            node.pop("claim", None)
            safe_error_message = _sanitize_diagnostic(error_message)
            node["attempts"][-1].update({
                "state": status,
                "error_code": error_code,
                "error_message": safe_error_message,
            })
            safe_metadata = dict(_sanitize(dict(metadata or {})))
            safe_metadata.pop("output", None)
            node["attempts"][-1]["metadata"] = safe_metadata
            if status == "cancelled":
                for other_id, other in projection["nodes"].items():
                    if other_id == claim.node_id or other["state"] in {
                        "succeeded",
                        "failed",
                        "skipped",
                        "cancelled",
                    }:
                        continue
                    other_claim = other.pop("claim", None)
                    other["state"] = "cancelled"
                    if other_claim and other.get("attempts"):
                        other["attempts"][-1].update({
                            "state": "cancelled",
                            "error_code": "cancelled",
                        })
            for field in (
                "session_id",
                "cache_fingerprint",
                "provider",
                "model",
                "usage",
                "pending_interaction",
                "retry_consumed",
                "loop_state",
                "approval_generation",
                "approval_rework_attempts",
                "approval_rework",
            ):
                if field in safe_metadata:
                    node[field] = safe_metadata[field]
            if status == "paused" and "approval_generation" in safe_metadata:
                node.pop("approval_rework", None)
            for warning in safe_metadata.get("warnings", []):
                if isinstance(warning, str) and warning not in projection["warnings"]:
                    projection["warnings"].append(warning)
            refs = []
            existing_artifacts = {
                (entry.get("attempt_id"), entry.get("relative_path"))
                for entry in projection["artifacts"]
                if isinstance(entry, dict)
            }
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                if (claim.attempt_id, artifact.relative_path) not in existing_artifacts:
                    projection["artifacts"].append(entry)
            self._append_locked(
                directory,
                projection,
                f"node_{status}",
                {
                    "artifacts": refs,
                    "error_code": error_code,
                    "metadata": safe_metadata,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            states = {candidate["state"] for candidate in projection["nodes"].values()}
            terminal = None
            if status == "failed":
                projection["last_error"] = {
                    "code": error_code or "node_failed",
                    "message": safe_error_message or "node execution failed",
                    "node_id": claim.node_id,
                }
            if status in {"cancelled", "interrupted", "paused"}:
                terminal = status
            elif states and states <= {
                "succeeded",
                "failed",
                "skipped",
                "cancelled",
                "interrupted",
            }:
                terminal = "failed" if "failed" in states else "succeeded"
            if terminal:
                projection["status"] = terminal
                self._append_locked(directory, projection, f"run_{terminal}")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (terminal, projection["updated_at"], claim.run_id),
                    )
                    if terminal == "cancelled":
                        connection.execute(
                            "DELETE FROM worker_claims WHERE run_id=?", (claim.run_id,)
                        )
            else:
                if "waiting_retry" in states and not states & {
                    "ready",
                    "claimed",
                    "running",
                }:
                    projection["status"] = "waiting_retry"
                    self._append_locked(directory, projection, "run_retry_waiting")
                    with self._connect() as connection:
                        connection.execute(
                            "UPDATE runs SET status='waiting_retry', updated_at=? "
                            "WHERE run_id=?",
                            (projection["updated_at"], claim.run_id),
                        )
                _atomic_json(directory / "run.json", projection)
            self._release_worker_claim(claim.attempt_id)

    def record_loop_iteration(
        self,
        claim: NodeClaim,
        *,
        artifacts: Iterable[ArtifactRef],
        loop_state: Mapping[str, object],
    ) -> None:
        """Persist one completed loop iteration before evaluating continuation."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale loop iteration")
            safe_state = dict(_sanitize(dict(loop_state)))
            node["loop_state"] = safe_state
            existing = {
                (entry.get("attempt_id"), entry.get("relative_path"))
                for entry in projection["artifacts"]
                if isinstance(entry, dict)
            }
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                if (claim.attempt_id, artifact.relative_path) not in existing:
                    projection["artifacts"].append(entry)
            self._append_locked(
                directory,
                projection,
                "loop_iteration_completed",
                {
                    "iteration": safe_state.get("iteration"),
                    "artifacts": refs,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )

    def block_cleanup_failed(
        self,
        claim: NodeClaim,
        *,
        artifacts: Iterable[ArtifactRef] = (),
        error_message: str | None = None,
    ) -> None:
        """Keep ownership blocked when an executor cannot prove tree cleanup."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale cleanup failure")
            projection["desired_status"] = "cleanup_failed"
            projection["last_error"] = {
                "code": "cleanup_failed",
                "message": _sanitize_diagnostic(error_message)
                or "owned process cleanup did not complete",
                "node_id": claim.node_id,
            }
            existing = {
                (entry.get("attempt_id"), entry.get("relative_path"))
                for entry in projection["artifacts"]
                if isinstance(entry, dict)
            }
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                if (claim.attempt_id, artifact.relative_path) not in existing:
                    projection["artifacts"].append(entry)
            self._append_locked(
                directory,
                projection,
                "cleanup_failed",
                {"artifacts": refs, "cleanup_complete": False},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET desired_status='cleanup_failed', updated_at=? "
                    "WHERE run_id=?",
                    (projection["updated_at"], claim.run_id),
                )
                self._record_admission_event(
                    connection,
                    "cleanup_failed",
                    run_id=claim.run_id,
                    reason_code="uninterruptible_process",
                )

    def _append_locked(
        self,
        directory: Path,
        projection: dict[str, object],
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        node_id: str | None = None,
        attempt_id: str | None = None,
        compact_recovery: bool = False,
    ) -> dict[str, object]:
        sequence = int(projection["event_sequence"]) + 1
        now = _utc_now()
        projection["event_sequence"] = sequence
        projection["state_version"] = int(projection["state_version"]) + 1
        projection["updated_at"] = now
        recovery = {"projection_sha256": _projection_digest(projection)}
        if not compact_recovery:
            recovery["projection"] = json.loads(
                json.dumps(projection, sort_keys=True, ensure_ascii=False)
            )
        event = {
            "schema_version": 1,
            "sequence": sequence,
            "timestamp": now,
            "run_id": projection["run_id"],
            "node_id": node_id,
            "attempt_id": attempt_id,
            "event_type": event_type,
            "payload": _sanitize(dict(payload or {})),
            **recovery,
        }
        encoded = json.dumps(event, sort_keys=True) + "\n"
        journal_path = directory / "events.jsonl"
        if (
            journal_path.stat().st_size + len(encoded.encode("utf-8"))
            > self.max_journal_bytes
        ):
            raise StorageQuotaError("event_journal_quota exceeded")
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_json(directory / "run.json", projection)
        return event

    def transition_pending_nodes(
        self,
        run_id: str,
        transitions: Mapping[str, tuple[str, str | None]],
    ) -> tuple[str, ...]:
        """Compare-and-set dependency-resolved nodes to ready or skipped."""
        directory = self.run_directory(run_id)
        changed = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return ()
            for node_id, (state, warning) in transitions.items():
                if state not in {"ready", "skipped"}:
                    raise ValueError(f"invalid dependency transition: {state}")
                node = projection["nodes"].get(node_id)
                if not node or node["state"] != "pending":
                    continue
                node["state"] = state
                if warning:
                    node["skip_reason"] = warning
                    if warning not in projection["warnings"]:
                        projection["warnings"].append(warning)
                self._append_locked(
                    directory,
                    projection,
                    f"node_{state}",
                    {"reason": warning} if warning else None,
                    node_id=node_id,
                )
                changed.append(node_id)
        return tuple(changed)

    def finalize_if_complete(self, run_id: str) -> bool:
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return False
            states = {node["state"] for node in projection["nodes"].values()}
            if states - {
                "succeeded",
                "failed",
                "skipped",
                "cancelled",
                "interrupted",
            }:
                return False
            target = "failed" if "failed" in states else "succeeded"
            projection["status"] = target
            self._append_locked(directory, projection, f"run_{target}")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (target, projection["updated_at"], run_id),
                )
            return True

    def schedule_retry(
        self,
        claim: NodeClaim,
        *,
        next_attempt_at: datetime,
        artifacts: Iterable[ArtifactRef] = (),
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: Mapping[str, object] | None = None,
        consumed_attempts: int = 1,
    ) -> None:
        if next_attempt_at.tzinfo is None:
            raise ValueError("next_attempt_at must be timezone-aware")
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            if node.get("claim", {}).get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale node completion")
            if projection["status"] in {"cancelled", "abandoned", "interrupted"}:
                raise RuntimeError("stale completion for terminal run")
            node.pop("claim", None)
            node["state"] = "waiting_retry"
            node["next_attempt_at"] = next_attempt_at.isoformat()
            node["retry_consumed"] = consumed_attempts
            safe_error_message = _sanitize_diagnostic(error_message)
            node["attempts"][-1].update({
                "state": "failed",
                "error_code": error_code,
                "error_message": safe_error_message,
                "metadata": _sanitize(dict(metadata or {})),
            })
            refs = []
            for artifact in artifacts:
                entry = {
                    "node_id": claim.node_id,
                    "attempt_id": claim.attempt_id,
                    "relative_path": artifact.relative_path,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                refs.append(entry)
                projection["artifacts"].append(entry)
            active_states = {
                candidate["state"] for candidate in projection["nodes"].values()
            }
            projection["status"] = (
                "running"
                if active_states & {"ready", "claimed", "running"}
                else "waiting_retry"
            )
            self._append_locked(
                directory,
                projection,
                "node_retry_scheduled",
                {
                    "next_attempt_at": next_attempt_at.isoformat(),
                    "error_code": error_code,
                    "artifacts": refs,
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], claim.run_id),
                )
            self._release_worker_claim(claim.attempt_id)

    def wake_due_retries(
        self, run_id: str, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        instant = now or datetime.now(timezone.utc)
        directory = self.run_directory(run_id)
        ready = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] not in {"waiting_retry", "running"}:
                return ()
            for node_id, node in projection["nodes"].items():
                if node["state"] != "waiting_retry":
                    continue
                due = datetime.fromisoformat(node["next_attempt_at"])
                if due > instant:
                    continue
                node["state"] = "ready"
                node.pop("next_attempt_at", None)
                self._append_locked(
                    directory,
                    projection,
                    "node_retry_ready",
                    node_id=node_id,
                )
                ready.append(node_id)
            if ready:
                projection["status"] = "running"
                self._append_locked(directory, projection, "run_retry_resumed")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status='running', updated_at=? WHERE run_id=?",
                        (projection["updated_at"], run_id),
                    )
        return tuple(ready)

    def renew_claim(
        self,
        claim: NodeClaim,
        *,
        now: datetime | None = None,
        monotonic_now: float | None = None,
        lease_seconds: float = 30.0,
        heartbeat_interval_seconds: float = 5.0,
    ) -> bool:
        if lease_seconds <= 0 or heartbeat_interval_seconds <= 0:
            raise ValueError("lease and heartbeat intervals must be positive")
        instant = now or datetime.now(timezone.utc)
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"].get(claim.node_id)
            active = node.get("claim", {}) if node else {}
            if active.get("attempt_id") != claim.attempt_id:
                return False
            heartbeat_at = datetime.fromisoformat(active["heartbeat_at"])
            utc_elapsed = (instant - heartbeat_at).total_seconds()
            monotonic_instant = (
                float(monotonic_now) if monotonic_now is not None else time.monotonic()
            )
            previous_monotonic = active.get("heartbeat_monotonic")
            monotonic_elapsed = (
                monotonic_instant - float(previous_monotonic)
                if isinstance(previous_monotonic, int | float)
                else utc_elapsed
            )
            active_lease_seconds = float(active.get("lease_seconds", lease_seconds))
            if (
                utc_elapsed < 0
                or monotonic_elapsed < 0
                or abs(utc_elapsed - monotonic_elapsed) > active_lease_seconds
                or monotonic_elapsed >= active_lease_seconds
                or datetime.fromisoformat(active["lease_expires_at"]) <= instant
            ):
                return False
            if utc_elapsed < heartbeat_interval_seconds:
                return True
            active["heartbeat_at"] = instant.isoformat()
            active["heartbeat_monotonic"] = monotonic_instant
            active["lease_seconds"] = float(lease_seconds)
            active["lease_expires_at"] = (
                instant + timedelta(seconds=lease_seconds)
            ).isoformat()
            self._append_locked(
                directory,
                projection,
                "node_heartbeat",
                {
                    "heartbeat_at": active["heartbeat_at"],
                    "heartbeat_monotonic": active["heartbeat_monotonic"],
                    "lease_expires_at": active["lease_expires_at"],
                    "lease_seconds": active["lease_seconds"],
                },
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
                compact_recovery=True,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE worker_claims SET lease_expires_at=? WHERE attempt_id=?",
                    (active["lease_expires_at"], claim.attempt_id),
                )
            return True

    def expire_stale_claims(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        monotonic_now: float | None = None,
    ) -> tuple[str, ...]:
        instant = now or datetime.now(timezone.utc)
        monotonic_instant = (
            float(monotonic_now) if monotonic_now is not None else time.monotonic()
        )
        directory = self.run_directory(run_id)
        expired = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection.get("desired_status") == "cleanup_failed":
                return ()
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                return ()
            for node_id, node in projection["nodes"].items():
                claim = node.get("claim")
                if not claim:
                    continue
                lease_seconds = float(claim.get("lease_seconds", 30.0))
                heartbeat_utc = datetime.fromisoformat(claim["heartbeat_at"])
                utc_elapsed = (instant - heartbeat_utc).total_seconds()
                heartbeat_monotonic = claim.get("heartbeat_monotonic")
                monotonic_elapsed = (
                    monotonic_instant - float(heartbeat_monotonic)
                    if isinstance(heartbeat_monotonic, int | float)
                    else utc_elapsed
                )
                clock_gap = abs(utc_elapsed - monotonic_elapsed) > lease_seconds
                if (
                    datetime.fromisoformat(claim["lease_expires_at"]) > instant
                    and monotonic_elapsed < lease_seconds
                    and not clock_gap
                ):
                    continue
                attempt_id = claim["attempt_id"]
                node.pop("claim", None)
                node["state"] = "interrupted"
                node["attempts"][-1].update({
                    "state": "interrupted",
                    "error_code": "lease_expired",
                })
                projection["status"] = "interrupted"
                self._append_locked(
                    directory,
                    projection,
                    "node_interrupted",
                    {"reason": "lease_expired"},
                    node_id=node_id,
                    attempt_id=attempt_id,
                )
                self._release_worker_claim(attempt_id)
                expired.append(node_id)
            if expired:
                self._append_locked(directory, projection, "run_interrupted")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status='interrupted', updated_at=? WHERE run_id=?",
                        (projection["updated_at"], run_id),
                    )
        return tuple(expired)

    def interrupt_active_claims(
        self,
        run_id: str,
        *,
        reason: str,
        lock_timeout_seconds: float = 5.0,
    ) -> tuple[str, ...]:
        directory = self.run_directory(run_id)
        interrupted = []
        with workflow_lock(
            self._run_lock_path(run_id), timeout_seconds=lock_timeout_seconds
        ):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
                "paused",
            }:
                return ()
            for node_id, node in projection["nodes"].items():
                claim = node.pop("claim", None)
                if not claim:
                    continue
                node["state"] = "interrupted"
                node["attempts"][-1].update({
                    "state": "interrupted",
                    "error_code": reason,
                })
                self._append_locked(
                    directory,
                    projection,
                    "node_interrupted",
                    {"reason": reason},
                    node_id=node_id,
                    attempt_id=claim["attempt_id"],
                )
                self._release_worker_claim(claim["attempt_id"])
                interrupted.append(node_id)
            if interrupted:
                projection["status"] = "interrupted"
                self._append_locked(directory, projection, "run_interrupted")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET status='interrupted', updated_at=? WHERE run_id=?",
                        (projection["updated_at"], run_id),
                    )
        return tuple(interrupted)

    def record_cleanup_failed(self, run_id: str, *, reason: str) -> None:
        with self._connect() as connection:
            self._record_admission_event(
                connection,
                "cleanup_failed",
                run_id=run_id,
                reason_code=reason,
            )

    def interrupt_for_host_pressure(self, run_id: str, *, message: str) -> None:
        directory = self.run_directory(run_id)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["status"] != "running":
                return
            projection["status"] = "interrupted"
            projection["last_error"] = {
                "code": "host_pressure",
                "message": _sanitize_diagnostic(message),
            }
            self._append_locked(directory, projection, "host_pressure_refused")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status='interrupted', updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )

    def release_or_expire_claim(self, claim: NodeClaim) -> bool:
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"].get(claim.node_id)
            if not node or node.get("claim", {}).get("attempt_id") != claim.attempt_id:
                return False
            node["state"] = "interrupted"
            node.pop("claim", None)
            projection["status"] = "interrupted"
            self._append_locked(
                directory,
                projection,
                "node_interrupted",
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            self._release_worker_claim(claim.attempt_id)
            return True

    def cancel_run(
        self,
        run_id: str,
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        recorded: list[tuple[str, str, ProcessIdentity]] = []
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise RuntimeError("stale cancellation decision")
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                return {**projection, "cancellation_outcome": "already_terminal"}
            if any(
                node.get("pending_interaction") == "reconcile"
                or (
                    isinstance(node.get("pending_interaction"), dict)
                    and node["pending_interaction"].get("type") == "reconcile"
                )
                for node in projection["nodes"].values()
            ):
                self._append_locked(
                    directory,
                    projection,
                    "cancel_reconciliation_required",
                    {"reason_code": "outcome_unknown"},
                )
                return {
                    **projection,
                    "cancellation_outcome": "reconciliation_required",
                }
            if projection.get("desired_status") != "cancelled":
                projection["desired_status"] = "cancelled"
                self._append_locked(directory, projection, "cancel_requested")
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE runs SET desired_status='cancelled', updated_at=? "
                        "WHERE run_id=?",
                        (projection["updated_at"], run_id),
                    )
            for node_id, node in projection["nodes"].items():
                claim = node.get("claim")
                serialized = (
                    claim.get("process_identity") if isinstance(claim, dict) else None
                )
                if not isinstance(serialized, dict):
                    continue
                try:
                    identity = ProcessIdentity(
                        pid=int(serialized["pid"]),
                        start_time=(
                            int(serialized["start_time"])
                            if serialized.get("start_time") is not None
                            else None
                        ),
                        group_id=(
                            int(serialized["group_id"])
                            if serialized.get("group_id") is not None
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                recorded.append((node_id, str(claim["attempt_id"]), identity))

        cleanup: list[tuple[str, str, ProcessIdentity, bool]] = []
        for node_id, attempt_id, identity in recorded:
            terminated = ManagedProcessTree.terminate_existing(
                identity,
                term_grace_seconds=5.0,
                kill_grace_seconds=2.0,
            )
            cleaned = terminated or not identity.is_current()
            cleanup.append((node_id, attempt_id, identity, cleaned))

        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            failed_cleanup = []
            for node_id, attempt_id, identity, cleaned in cleanup:
                node = projection["nodes"].get(node_id, {})
                claim = node.get("claim", {})
                serialized = claim.get("process_identity")
                if (
                    claim.get("attempt_id") != attempt_id
                    or not isinstance(serialized, dict)
                    or serialized.get("pid") != identity.pid
                ):
                    continue
                if cleaned:
                    claim.pop("process_identity", None)
                    if node.get("attempts"):
                        node["attempts"][-1].pop("process_identity", None)
                    self._append_locked(
                        directory,
                        projection,
                        "process_reaped",
                        {"pid": identity.pid, "cleanup_complete": True},
                        node_id=node_id,
                        attempt_id=attempt_id,
                    )
                else:
                    failed_cleanup.append((node_id, attempt_id, identity.pid))
            if failed_cleanup:
                projection["last_error"] = {
                    "code": "cleanup_failed",
                    "message": "owned process cleanup did not complete",
                }
                for node_id, attempt_id, pid in failed_cleanup:
                    self._append_locked(
                        directory,
                        projection,
                        "cleanup_failed",
                        {"pid": pid, "cleanup_complete": False},
                        node_id=node_id,
                        attempt_id=attempt_id,
                    )
                with self._connect() as connection:
                    self._record_admission_event(
                        connection,
                        "cleanup_failed",
                        run_id=run_id,
                        reason_code="uninterruptible_process",
                    )
                return {**projection, "cancellation_outcome": "cleanup_failed"}

            projection["status"] = "cancelled"
            projection["desired_status"] = None
            for node in projection["nodes"].values():
                if node["state"] not in {"succeeded", "failed", "skipped"}:
                    claim = node.pop("claim", None)
                    node["state"] = "cancelled"
                    if claim and node.get("attempts"):
                        node["attempts"][-1].update({
                            "state": "cancelled",
                            "error_code": "cancelled",
                        })
            self._append_locked(directory, projection, "run_cancelled")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status='cancelled', desired_status=NULL, "
                    "updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )
                connection.execute(
                    "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                )
            return {**projection, "cancellation_outcome": "cancelled"}

    def resume_run(
        self,
        run_id: str,
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        from plugins.workflow.schema import load_workflow

        package = load_workflow(directory / "definition.yaml")
        always_run = {
            node.id
            for node in package.definition.nodes
            if node.options.get("always_run")
        }
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise RuntimeError("stale resume decision")
            if projection["status"] not in {"failed", "interrupted"}:
                return projection
            for node_id, node in projection["nodes"].items():
                if node["state"] == "succeeded" and node_id not in always_run:
                    continue
                node.pop("claim", None)
                node["state"] = (
                    "ready"
                    if all(
                        projection["nodes"][dependency]["state"] == "succeeded"
                        for dependency in node["depends_on"]
                    )
                    else "pending"
                )
            projection["status"] = "running"
            projection["last_error"] = None
            self._append_locked(directory, projection, "run_resumed")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status='running', updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )
            return projection

    @staticmethod
    def _interaction_identity(node: Mapping[str, object]) -> str | None:
        pending = node.get("pending_interaction")
        if not isinstance(pending, Mapping):
            return None
        value = pending.get("interaction_id") or pending.get("action_digest")
        return str(value) if isinstance(value, str) and value else None

    def _already_decided(
        self,
        projection: Mapping[str, object],
        interaction_id: str | None,
    ) -> ApprovalDecision | None:
        for node_id, raw_node in projection["nodes"].items():
            if not isinstance(raw_node, Mapping):
                continue
            recorded = raw_node.get("approval_last_decision")
            if not isinstance(recorded, Mapping):
                continue
            recorded_id = recorded.get("interaction_id")
            if interaction_id is not None and recorded_id != interaction_id:
                continue
            return ApprovalDecision(
                run_id=str(projection["run_id"]),
                node_id=str(node_id),
                decision=str(recorded["decision"]),
                outcome="already_decided",
                interaction_id=str(recorded_id),
                state_version=int(projection["state_version"]),
            )
        return None

    def approve_run(
        self,
        run_id: str,
        *,
        comment: str = "",
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        operator_scope: str | None = None,
    ) -> ApprovalDecision:
        return self._decide_run(
            run_id,
            decision="approved",
            response=comment,
            expected_state_version=expected_state_version,
            interaction_id=interaction_id,
            actor=actor,
            channel=channel,
            operator_scope=operator_scope,
        )

    def reject_run(
        self,
        run_id: str,
        *,
        reason: str = "",
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        actor: str | None = None,
        channel: str | None = None,
        operator_scope: str | None = None,
    ) -> ApprovalDecision:
        return self._decide_run(
            run_id,
            decision="rejected",
            response=reason,
            expected_state_version=expected_state_version,
            interaction_id=interaction_id,
            actor=actor,
            channel=channel,
            operator_scope=operator_scope,
        )

    def _decide_run(
        self,
        run_id: str,
        *,
        decision: str,
        response: str,
        expected_state_version: int | None,
        interaction_id: str | None,
        actor: str | None,
        channel: str | None,
        operator_scope: str | None,
    ) -> ApprovalDecision:
        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision is invalid")
        if not isinstance(response, str):
            raise TypeError("approval response must be text")
        if len(response.encode("utf-8")) > min(self.max_input_bytes, 64 * 1024):
            raise InputSnapshotError("approval response exceeds the configured limit")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        from plugins.workflow.schema import load_workflow

        package = load_workflow(directory / "definition.yaml")
        definitions = {node.id: node for node in package.definition.nodes}
        with (
            workflow_lock(self.admission_lock),
            workflow_lock(self._run_lock_path(run_id)),
        ):
            projection = json.loads((directory / "run.json").read_text())
            duplicate = self._already_decided(projection, interaction_id)
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                if duplicate is not None:
                    return duplicate
                raise RuntimeError("stale approval decision")
            candidates = [
                (node_id, node)
                for node_id, node in projection["nodes"].items()
                if node.get("state") == "paused"
                and self._interaction_identity(node) is not None
                and (
                    interaction_id is None
                    or self._interaction_identity(node) == interaction_id
                )
            ]
            if len(candidates) != 1:
                if duplicate is not None:
                    return duplicate
                raise ValueError(
                    "run does not have exactly one matching pending interaction"
                )
            node_id, node = candidates[0]
            resolved_id = self._interaction_identity(node)
            assert resolved_id is not None
            pending = node["pending_interaction"]
            pending_type = str(pending.get("type") or pending.get("kind") or "")
            safe_response = (_sanitize_diagnostic(response.strip()) or "")[:64_000]
            record = {
                "decision": decision,
                "interaction_id": resolved_id,
            }
            node["approval_last_decision"] = record
            node.pop("pending_interaction", None)
            event_payload: dict[str, object] = {
                "decision": decision,
                "interaction_id": resolved_id,
            }
            if actor:
                event_payload["actor"] = _sanitize_diagnostic(actor)
            if channel:
                event_payload["channel"] = _sanitize_diagnostic(channel)

            terminal = False
            if decision == "approved":
                if pending_type == "workflow_approval":
                    definition = definitions[node_id]
                    approval = definition.value
                    if bool(approval.get("capture_response")):
                        relative = Path("nodes") / node_id / "approval" / "output.txt"
                        encoded = safe_response.encode("utf-8")
                        _atomic_text(directory / relative, safe_response)
                        artifact = {
                            "node_id": node_id,
                            "attempt_id": None,
                            "relative_path": relative.as_posix(),
                            "media_type": "text/plain",
                            "size_bytes": len(encoded),
                            "sha256": _sha256(encoded),
                        }
                        projection["artifacts"].append(artifact)
                        event_payload["artifact"] = artifact
                    node["state"] = "succeeded"
                    if node.get("attempts"):
                        node["attempts"][-1]["state"] = "succeeded"
                elif pending_type == "approval":
                    node["state"] = "ready"
                    node["action_grant"] = resolved_id
                else:
                    raise ValueError("pending interaction is not approvable")
                projection["status"] = "running"
            else:
                definition = definitions[node_id]
                approval = (
                    definition.value if definition.node_type == "approval" else {}
                )
                on_reject = (
                    approval.get("on_reject") if isinstance(approval, Mapping) else None
                )
                attempts = int(node.get("approval_rework_attempts", 0))
                maximum = (
                    int(on_reject.get("max_attempts", 3))
                    if isinstance(on_reject, Mapping)
                    else 0
                )
                if pending_type == "workflow_approval" and attempts < maximum:
                    node["state"] = "ready"
                    node["approval_rework"] = {"reason": safe_response}
                    projection["status"] = "running"
                else:
                    terminal = True
                    projection["status"] = "cancelled"
                    projection["desired_status"] = None
                    for candidate in projection["nodes"].values():
                        if candidate["state"] not in {"succeeded", "failed", "skipped"}:
                            candidate.pop("claim", None)
                            candidate["state"] = "cancelled"

            self._append_locked(
                directory,
                projection,
                f"interaction_{decision}",
                event_payload,
                node_id=node_id,
            )
            if terminal:
                self._append_locked(directory, projection, "run_cancelled")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, desired_status=NULL, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], run_id),
                )
                if terminal:
                    connection.execute(
                        "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                    )
            return ApprovalDecision(
                run_id=run_id,
                node_id=node_id,
                decision=decision,
                outcome="applied",
                interaction_id=resolved_id,
                state_version=int(projection["state_version"]),
            )

    def consume_action_grant(self, claim: NodeClaim) -> str | None:
        """Remove one exact worker grant durably before spawning the worker."""
        directory = self.run_directory(claim.run_id)
        with workflow_lock(self._run_lock_path(claim.run_id)):
            projection = json.loads((directory / "run.json").read_text())
            node = projection["nodes"][claim.node_id]
            active = node.get("claim", {})
            if active.get("attempt_id") != claim.attempt_id:
                raise RuntimeError("stale action grant consumer")
            digest = node.pop("action_grant", None)
            if digest is None:
                return None
            self._append_locked(
                directory,
                projection,
                "action_grant_consumed",
                {"grant_consumed": True},
                node_id=claim.node_id,
                attempt_id=claim.attempt_id,
            )
            return str(digest)

    def provide_loop_input(
        self,
        run_id: str,
        user_input: str,
        *,
        expected_state_version: int,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Compare-and-set one paused interactive loop back to ready."""
        if not isinstance(user_input, str):
            raise TypeError("loop input must be text")
        encoded = user_input.encode("utf-8")
        if len(encoded) > self.max_input_bytes:
            raise InputSnapshotError("loop input exceeds the configured input limit")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if projection["state_version"] != expected_state_version:
                raise RuntimeError("stale loop input decision")
            if projection["status"] != "paused":
                raise ValueError("run is not waiting for loop input")
            candidates = [
                (node_id, node)
                for node_id, node in projection["nodes"].items()
                if node.get("state") == "paused"
                and isinstance(node.get("pending_interaction"), dict)
                and node["pending_interaction"].get("type") == "loop_input"
            ]
            if len(candidates) != 1:
                raise ValueError("run does not have exactly one pending loop input")
            node_id, node = candidates[0]
            generation = int(node.get("loop_state", {}).get("iteration", 0))
            relative = (
                Path("nodes")
                / node_id
                / "inputs"
                / f"after-iteration-{generation:04d}.txt"
            )
            path = directory / relative
            _atomic_text(path, user_input)
            artifact = {
                "node_id": node_id,
                "attempt_id": None,
                "relative_path": relative.as_posix(),
                "media_type": "text/plain",
                "size_bytes": len(encoded),
                "sha256": _sha256(encoded),
            }
            projection["artifacts"].append(artifact)
            node["state"] = "ready"
            node.pop("pending_interaction", None)
            node["loop_user_input_artifact"] = relative.as_posix()
            projection["status"] = "running"
            self._append_locked(
                directory,
                projection,
                "loop_input_provided",
                {"artifact": artifact, "iteration": generation},
                node_id=node_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status='running', updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )
            return projection

    def retry_run(
        self,
        run_id: str,
        *,
        node_id: str | None = None,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Explicitly retry one failed/interrupted node with compare-and-set safety."""
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise RuntimeError("stale retry decision")
            candidates = [
                (candidate_id, node)
                for candidate_id, node in projection["nodes"].items()
                if (node_id is None or candidate_id == node_id)
                and node.get("state") in {"failed", "interrupted"}
                and not isinstance(node.get("pending_interaction"), Mapping)
            ]
            if len(candidates) != 1:
                raise ValueError("retry requires exactly one failed or interrupted node")
            selected_id, node = candidates[0]
            node.pop("claim", None)
            node.pop("next_attempt_at", None)
            node["state"] = (
                "ready"
                if all(
                    projection["nodes"][dependency]["state"] == "succeeded"
                    for dependency in node["depends_on"]
                )
                else "pending"
            )
            projection["status"] = "running"
            projection["last_error"] = None
            self._append_locked(
                directory,
                projection,
                "node_retry_requested",
                {"reason_code": "operator_retry"},
                node_id=selected_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status='running', updated_at=? WHERE run_id=?",
                    (projection["updated_at"], run_id),
                )
            return projection

    def reconcile_run(
        self,
        run_id: str,
        outcome: str,
        *,
        expected_state_version: int | None = None,
        interaction_id: str | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        """Resolve one unknown-side-effect pause without making an inference."""
        if outcome not in {"confirmed-succeeded", "confirmed-failed", "safe-to-retry"}:
            raise ValueError("invalid reconciliation outcome")
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise RuntimeError("stale reconciliation decision")
            candidates = []
            for candidate_id, node in projection["nodes"].items():
                pending = node.get("pending_interaction")
                is_reconcile = pending == "reconcile" or (
                    isinstance(pending, Mapping) and pending.get("type") == "reconcile"
                )
                identity = self._interaction_identity(node)
                if is_reconcile and (interaction_id is None or identity == interaction_id):
                    candidates.append((candidate_id, node))
            if len(candidates) != 1:
                raise ValueError("reconcile requires exactly one matching interaction")
            selected_id, node = candidates[0]
            node.pop("pending_interaction", None)
            if outcome == "confirmed-succeeded":
                node["state"] = "succeeded"
                projection["status"] = "running"
                projection["last_error"] = None
            elif outcome == "confirmed-failed":
                node["state"] = "failed"
                projection["status"] = "failed"
            else:
                node["state"] = "ready"
                projection["status"] = "running"
                projection["last_error"] = None
            self._append_locked(
                directory,
                projection,
                "node_reconciled",
                {"outcome": outcome},
                node_id=selected_id,
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (projection["status"], projection["updated_at"], run_id),
                )
            return projection

    def abandon_run(
        self,
        run_id: str,
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        projection = self.load_run(run_id, operator_scope=operator_scope)
        if projection["status"] not in {"interrupted", "failed", "paused"}:
            raise ValueError(
                "only interrupted, failed, or paused runs may be abandoned"
            )
        return self._set_terminal(
            run_id,
            "abandoned",
            {"abandoned"},
            expected_state_version=expected_state_version,
            operator_scope=operator_scope,
        )

    def _set_terminal(
        self,
        run_id: str,
        target: str,
        outcomes: set[str],
        *,
        expected_state_version: int | None = None,
        operator_scope: str | None = None,
    ) -> dict[str, object]:
        del outcomes
        directory = self.run_directory(run_id, operator_scope=operator_scope)
        with workflow_lock(self._run_lock_path(run_id)):
            projection = json.loads((directory / "run.json").read_text())
            if expected_state_version is not None and (
                int(projection["state_version"]) != expected_state_version
            ):
                raise RuntimeError("stale terminal transition")
            if projection["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "abandoned",
            }:
                if target == "cancelled":
                    return {**projection, "cancellation_outcome": "already_terminal"}
                return projection
            if target == "cancelled" and any(
                node.get("pending_interaction") == "reconcile"
                or (
                    isinstance(node.get("pending_interaction"), dict)
                    and node["pending_interaction"].get("type") == "reconcile"
                )
                for node in projection["nodes"].values()
            ):
                self._append_locked(
                    directory,
                    projection,
                    "cancel_reconciliation_required",
                    {"reason_code": "outcome_unknown"},
                )
                return {
                    **projection,
                    "cancellation_outcome": "reconciliation_required",
                }
            projection["status"] = target
            for node in projection["nodes"].values():
                if node["state"] not in {"succeeded", "failed", "skipped"}:
                    claim = node.pop("claim", None)
                    node["state"] = target if target == "cancelled" else "interrupted"
                    if claim and node.get("attempts"):
                        node["attempts"][-1].update({
                            "state": node["state"],
                            "error_code": target,
                        })
            self._append_locked(directory, projection, f"run_{target}")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (target, projection["updated_at"], run_id),
                )
                connection.execute(
                    "DELETE FROM worker_claims WHERE run_id=?", (run_id,)
                )
            if target == "cancelled":
                return {**projection, "cancellation_outcome": "cancelled"}
            return projection

    def cleanup_runs(
        self,
        *,
        older_than: timedelta = timedelta(days=7),
        dry_run: bool = True,
        operator_scope: str | None = None,
        required_metadata: Mapping[str, str | None] | None = None,
    ) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - older_than
        scope_clause = (
            " AND operator_scope_digest=?" if operator_scope is not None else ""
        )
        values: tuple[object, ...] = (
            (self._scope_digest(operator_scope),) if operator_scope is not None else ()
        )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, run_directory, status, updated_at FROM runs "
                f"WHERE admission_state='published'{scope_clause} "
                "ORDER BY updated_at, run_id",
                values,
            ).fetchall()
        candidates = [
            row
            for row in rows
            if row["status"] in {"succeeded", "failed", "cancelled", "abandoned"}
            and datetime.fromisoformat(row["updated_at"]) <= cutoff
        ]
        if required_metadata:
            candidates = [
                row
                for row in candidates
                if self._run_has_metadata(
                    Path(row["run_directory"]), required_metadata
                )
            ]
        files_total = sum(
            1
            for row in candidates
            for path in Path(row["run_directory"]).rglob("*")
            if path.is_file()
        )
        bytes_total = sum(
            path.stat().st_size
            for row in candidates
            for path in Path(row["run_directory"]).rglob("*")
            if path.is_file()
        )
        if not dry_run:
            for row in candidates:
                source = Path(row["run_directory"])
                with workflow_lock(self._run_lock_path(row["run_id"])):
                    if not source.exists():
                        continue
                    quarantine = (
                        self.quarantine_root / f"{row['run_id']}-{uuid.uuid4().hex}"
                    )
                    os.replace(source, quarantine)
                shutil.rmtree(quarantine, ignore_errors=True)
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM runs WHERE run_id=?", (row["run_id"],)
                    )
        return {
            "dry_run": dry_run,
            "run_ids": [row["run_id"] for row in candidates],
            "files": files_total,
            "bytes": bytes_total,
        }

    @staticmethod
    def _run_has_metadata(
        directory: Path, expected: Mapping[str, str | None]
    ) -> bool:
        try:
            projection = json.loads((directory / "run.json").read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        metadata = projection.get("run_metadata")
        if not isinstance(metadata, Mapping):
            return False
        return all(
            key in metadata and (value is None or metadata.get(key) == value)
            for key, value in expected.items()
        )


__all__ = [
    "ArtifactRef",
    "InputSnapshotError",
    "NodeClaim",
    "RunStore",
    "StorageQuotaError",
]
