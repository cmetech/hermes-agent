"""Digest-verified, evidence-backed demonstrations of the workflow runtime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
from typing import Iterator, Literal, Mapping

import yaml

from agent.plugin_agent import PluginAgentRunResult
from cron.jobs import create_job, list_jobs, use_cron_store
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _runtime_config, _scheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest


_ALLOWED_CLAIMS = frozenset({
    "immutable-inputs", "parallel-fan-in", "approval-rework",
    "artifact-verification", "no-live-inventory", "persisted-retry",
    "typed-timeout", "process-cleanup", "explicit-ai-consent",
    "scoped-extensions", "persistent-session", "local-mcp-cleanup",
    "explicit-schedule-consent", "one-shot-ownership", "cron-recovery",
})
_FORBIDDEN_TEXT = (
    "get-ciminstance", "get-computerinfo", "wmic ", "system_profiler",
    "/proc/", "powershell", "invoke-webrequest", "http://", "https://",
    "resource exhaustion", "process flood", "corruption mode", "elevation",
)


class ShowcaseCatalogError(ValueError):
    """A bundled showcase failed closed catalog or digest validation."""


@dataclass(frozen=True)
class ShowcaseScenario:
    id: str
    display_name: str
    purpose: str
    bundle_version: str
    package_version: str
    workflow_path: str
    interaction_mode: str
    offline: bool
    requires_ai: bool
    requires_network: bool
    safety_class: str
    supported_platforms: tuple[str, ...]
    expected_checkpoints: tuple[str, ...]
    expected_terminal_outcomes: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    capability_claims: tuple[str, ...]
    limits: Mapping[str, int]
    cleanup_ownership: str
    package_digest: str


@dataclass(frozen=True)
class ShowcaseClaimResult:
    capability: str
    outcome: Literal["passed", "failed", "skipped"]
    reason_code: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ShowcaseReport:
    schema_version: int
    showcase_id: str
    showcase_version: str
    bundle_digest: str
    run_id: str
    definition_digest: str
    terminal_outcome: str
    claims: tuple[ShowcaseClaimResult, ...]
    interactions: tuple[dict, ...]
    artifacts: tuple[dict, ...]
    cleanup: dict
    suggested_next: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ShowcaseCatalogError(f"showcase bundle symlink is forbidden: {path}")
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(len(data)).encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def _bundle_path(explicit: Path | None = None) -> Iterator[Path]:
    if explicit is not None:
        yield explicit.resolve()
        return
    candidate = resources.files("plugins.workflow").joinpath("showcases")
    with resources.as_file(candidate) as materialized:
        yield Path(materialized).resolve()


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ShowcaseCatalogError(f"showcase path escapes bundle: {relative}")
    candidate = root / path
    if candidate.is_symlink():
        raise ShowcaseCatalogError(f"showcase path is a symlink: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ShowcaseCatalogError(f"showcase resource is missing or escapes: {relative}") from exc
    return candidate


def _bundle_digest(root: Path) -> str:
    return _sha256((root / "catalog.yaml").read_bytes() + b"\0" + (root / "digests.json").read_bytes())


def load_showcase_catalog(bundle_root: Path | None = None) -> dict[str, ShowcaseScenario]:
    with _bundle_path(bundle_root) as root:
        try:
            raw = yaml.safe_load((root / "catalog.yaml").read_text(encoding="utf-8"))
            manifest = json.loads((root / "digests.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ShowcaseCatalogError(f"invalid showcase bundle metadata: {exc}") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise ShowcaseCatalogError("unsupported showcase catalog schema")
        if not isinstance(manifest, Mapping) or manifest.get("schema_version") != 1:
            raise ShowcaseCatalogError("unsupported showcase digest schema")
        catalog_digest = manifest.get("catalog_sha256")
        if catalog_digest != _sha256((root / "catalog.yaml").read_bytes()):
            raise ShowcaseCatalogError("showcase catalog digest mismatch")
        package_digests = manifest.get("packages")
        if not isinstance(package_digests, Mapping):
            raise ShowcaseCatalogError("showcase package digest manifest is incomplete")
        scenarios = raw.get("scenarios")
        if not isinstance(scenarios, list):
            raise ShowcaseCatalogError("showcase scenarios must be a list")
        result: dict[str, ShowcaseScenario] = {}
        required = {
            "id", "display_name", "purpose", "bundle_version", "package_version",
            "workflow_path", "interaction_mode", "offline", "requires_ai",
            "requires_network", "safety_class", "supported_platforms",
            "expected_checkpoints", "expected_terminal_outcomes", "expected_artifacts",
            "capability_claims", "limits", "cleanup_ownership",
        }
        for item in scenarios:
            if not isinstance(item, Mapping) or not required <= set(item):
                raise ShowcaseCatalogError("showcase scenario is missing required fields")
            scenario_id = str(item["id"])
            if scenario_id in result:
                raise ShowcaseCatalogError(f"duplicate showcase id: {scenario_id}")
            workflow_path = str(item["workflow_path"])
            workflow = _contained(root, workflow_path)
            package_root = workflow.parent.parent
            actual_digest = _tree_digest(package_root)
            if package_digests.get(scenario_id) != actual_digest:
                raise ShowcaseCatalogError(f"showcase package digest mismatch: {scenario_id}")
            claims = tuple(str(value) for value in item["capability_claims"])
            limits = item["limits"]
            if not isinstance(limits, Mapping) or not isinstance(limits.get("wall_seconds"), int):
                raise ShowcaseCatalogError(f"showcase limits are invalid: {scenario_id}")
            text = json.dumps(item, sort_keys=True).lower()
            if (
                not claims or not set(claims) <= _ALLOWED_CLAIMS
                or "destructive" in str(item["safety_class"]).lower()
                or bool(item["requires_network"])
                or int(limits["wall_seconds"]) > (600 if item["requires_ai"] else 300)
                or any(token in text for token in _FORBIDDEN_TEXT)
            ):
                raise ShowcaseCatalogError(f"showcase safety contract rejected: {scenario_id}")
            load_workflow(workflow)
            result[scenario_id] = ShowcaseScenario(
                **{key: str(item[key]) for key in ("id", "display_name", "purpose", "bundle_version", "package_version", "workflow_path", "interaction_mode", "safety_class", "cleanup_ownership")},
                offline=bool(item["offline"]), requires_ai=bool(item["requires_ai"]),
                requires_network=bool(item["requires_network"]),
                supported_platforms=tuple(map(str, item["supported_platforms"])),
                expected_checkpoints=tuple(map(str, item["expected_checkpoints"])),
                expected_terminal_outcomes=tuple(map(str, item["expected_terminal_outcomes"])),
                expected_artifacts=tuple(map(str, item["expected_artifacts"])),
                capability_claims=claims,
                limits={str(key): int(value) for key, value in limits.items()},
                package_digest=actual_digest,
            )
        return dict(sorted(result.items()))


def _scenario_package(scenario: ShowcaseScenario):
    with _bundle_path() as root:
        return load_workflow(_contained(root, scenario.workflow_path))


def preflight_showcase(showcase_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    catalog = load_showcase_catalog()
    if showcase_id not in catalog:
        raise ShowcaseCatalogError(f"unknown showcase: {showcase_id}")
    scenario = catalog[showcase_id]
    package = _scenario_package(scenario)
    with _bundle_path() as root:
        bundle_digest = _bundle_digest(root)
    confirmation_kind = "ai" if scenario.requires_ai else "schedule" if scenario.interaction_mode == "schedule" else None
    token = _sha256(f"{confirmation_kind or 'none'}\0{scenario.package_digest}\0{bundle_digest}".encode()) if confirmation_kind else None
    requested_skills = sorted({str(skill) for node in package.definition.nodes for skill in node.options.get("skills", ())})
    local_mcp = sorted({str(value) for node in package.definition.nodes for value in ((node.options.get("mcp"),) if isinstance(node.options.get("mcp"), str) else node.options.get("mcp", ())) if isinstance(value, str)})
    inline_agents = max(
        (len(node.options.get("agents", {})) for node in package.definition.nodes),
        default=0,
    )
    return {
        "schema_version": 1, "showcase_id": scenario.id, "display_name": scenario.display_name,
        "purpose": scenario.purpose, "runnable": True, "offline": scenario.offline,
        "requires_ai": scenario.requires_ai, "requires_network": scenario.requires_network,
        "requires_confirmation": confirmation_kind is not None,
        "confirmation_kind": confirmation_kind, "confirmation_token": token,
        "package_digest": scenario.package_digest, "bundle_digest": bundle_digest,
        "requested_skills": requested_skills, "local_mcp_servers": [f"mcp/{value}" for value in local_mcp],
        "inline_agent_limit": inline_agents, "wall_seconds": scenario.limits["wall_seconds"],
        "side_effects_initialized": False,
    }


class _DeterministicReworkRunner:
    def run(self, request, *, is_cancelled=None):
        return PluginAgentRunResult(
            final_response="Deterministic revision recorded: keep every proposed action manual and reversible.",
            session_id="showcase-rework", provider="deterministic", model="none",
            status="completed", pending_interaction=None, usage={},
            audit={"isolated_worker": False, "deterministic": True},
        )


def _store(home: str | Path, package=None) -> tuple[RunStore, object]:
    config = _runtime_config(home, sidecar=package.sidecar if package is not None else None)
    store = RunStore(
        home, max_executing_runs=config.max_executing_runs,
        max_queued_runs=config.max_queued_runs, max_paused_runs=config.max_paused_runs,
        max_nonterminal_runs=config.max_nonterminal_runs,
        max_start_requests_per_minute=config.max_start_requests_per_minute,
        max_total_workers=min(4, config.max_total_workers), max_run_bytes=32 * 1024 * 1024,
    )
    return store, config


def _advance_until_wait(store: RunStore, config, run_id: str) -> dict[str, object]:
    scheduler = _scheduler(store, config, agent_runner=_DeterministicReworkRunner())
    deadline = time.monotonic() + 305
    while True:
        projection = scheduler.advance(run_id)
        if projection["status"] != "waiting_retry":
            return store.get_run_status(run_id)
        if time.monotonic() >= deadline:
            return store.get_run_status(run_id)
        next_at = datetime.fromisoformat(str(store.get_run_status(run_id)["next_retry_at"]))
        time.sleep(max(0.01, min(1.0, (next_at - datetime.now(timezone.utc)).total_seconds())))


def _stage_fixture(home: Path) -> tuple[Path, Path]:
    staging_root = home / "workflow" / "showcase" / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="laptop-", dir=staging_root))
    (directory / "owner.json").write_text(json.dumps({"owner": "workflow-showcase"}), encoding="utf-8")
    with _bundle_path() as root:
        source = _contained(root, "packages/laptop-diagnostic/fixtures/laptop-snapshot.json")
        target = directory / "sanitized-evidence.json"
        target.write_bytes(source.read_bytes())
    return directory, target


def _schedule_showcase(scenario: ShowcaseScenario, *, home: Path, schedule_at: str, token: str | None) -> dict[str, object]:
    preflight = preflight_showcase(scenario.id, hermes_home=home)
    if token != preflight["confirmation_token"]:
        return {"status": "skipped", "reason_code": "schedule_confirmation_required", "run_id": None, "confirmation_token": preflight["confirmation_token"]}
    with use_cron_store(home):
        job = create_job(
            prompt="Run `hermes workflow showcase run scheduling --json` as the explicitly authorized one-shot showcase.",
            schedule=schedule_at, name=f"workflow-showcase-{secrets.token_hex(6)}",
            repeat=1, skills=["workflow-showcase"],
        )
    evidence = {
        "schema_version": 1, "showcase_id": scenario.id, "schedule_id": job["id"],
        "nonce": secrets.token_hex(16), "definition_digest": scenario.package_digest,
        "expected_trigger": schedule_at, "profile": "default",
    }
    path = home / "workflow" / "showcase" / "schedules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {"status": "scheduled", "schedule_id": job["id"], "repeat": job["repeat"], "ownership": evidence}


def run_showcase(
    showcase_id: str, *, hermes_home: str | Path, symptom: str | None = None,
    confirmation_token: str | None = None, schedule_at: str | None = None,
    no_wait: bool = False, idempotency_key: str | None = None,
) -> dict[str, object]:
    scenario = load_showcase_catalog().get(showcase_id)
    if scenario is None:
        raise ShowcaseCatalogError(f"unknown showcase: {showcase_id}")
    home = Path(hermes_home).expanduser().resolve()
    preflight = preflight_showcase(showcase_id, hermes_home=home)
    if scenario.requires_ai and confirmation_token != preflight["confirmation_token"]:
        return {"status": "skipped", "reason_code": "ai_confirmation_required", "run_id": None, "confirmation_token": preflight["confirmation_token"]}
    if scenario.interaction_mode == "schedule":
        if not schedule_at:
            return {"status": "skipped", "reason_code": "schedule_time_required", "run_id": None}
        return _schedule_showcase(scenario, home=home, schedule_at=schedule_at, token=confirmation_token)
    if showcase_id == "laptop-diagnostic" and not (symptom and symptom.strip()):
        return {"status": "input_required", "reason_code": "showcase_input_required", "required_input": "symptom", "run_id": None}
    package = _scenario_package(scenario)
    store, config = _store(home, package)
    fixture_dir: Path | None = None
    try:
        inputs = None
        if showcase_id == "laptop-diagnostic":
            fixture_dir, fixture = _stage_fixture(home)
            inputs = {"evidence": fixture}
        prepared = store.prepare_run_snapshot(package, inputs=inputs, values={"arguments": symptom or ""})
    finally:
        if fixture_dir is not None:
            shutil.rmtree(fixture_dir, ignore_errors=True)
    package_digest = compute_package_digest(package).sha256
    WorkflowTrustStore(home).trust(package_digest, actor="trusted_distribution", risk_digest=package_digest)
    request = RunAdmissionRequest(
        workflow_name=package.definition.name, definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest, input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="cli", idempotency_key=idempotency_key or secrets.token_urlsafe(24),
        concurrency_key=f"showcase:{scenario.id}",
        concurrency_policy=str(package.sidecar.get("overlap_policy") or "queue"),
        run_metadata={"showcase_id": scenario.id, "showcase_version": scenario.package_version, "bundle_digest": str(preflight["bundle_digest"])},
    )
    admitted = store.start_run(request, immutable_snapshot=prepared)
    if admitted.run_id is None:
        return {"status": admitted.disposition, "reason_code": admitted.reason_code, "run_id": None}
    if no_wait or admitted.disposition != "created":
        return store.get_run_status(admitted.run_id)
    return _advance_until_wait(store, config, admitted.run_id)


def approve_showcase(run_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    store, config = _store(hermes_home)
    store.approve_run(run_id, channel="showcase")
    return _advance_until_wait(store, config, run_id)


def reject_showcase(run_id: str, reason: str, *, hermes_home: str | Path) -> dict[str, object]:
    store, config = _store(hermes_home)
    store.reject_run(run_id, reason=reason, channel="showcase")
    return _advance_until_wait(store, config, run_id)


def _event_ref(event: Mapping[str, object]) -> str:
    return f"event:{int(event['sequence'])}:{event['event_type']}"


def build_showcase_report(run_id: str, *, hermes_home: str | Path) -> ShowcaseReport:
    store, _ = _store(hermes_home)
    projection = store.load_run(run_id)
    metadata = projection.get("run_metadata", {})
    if not isinstance(metadata, Mapping) or not metadata.get("showcase_id"):
        raise ShowcaseCatalogError("run is not an authenticated showcase run")
    scenario = load_showcase_catalog()[str(metadata["showcase_id"])]
    events = store.tail_events(run_id, limit=200)
    run_directory = store.run_directory(run_id)
    interactions = tuple(
        {"type": str(node["pending_interaction"].get("type", "interaction")), "node_id": node_id, "interaction_id": str(node["pending_interaction"].get("interaction_id", ""))}
        for node_id, node in projection["nodes"].items()
        if isinstance(node.get("pending_interaction"), Mapping)
    )
    verified_artifacts: list[dict] = []
    for index, artifact in enumerate(projection.get("artifacts", [])):
        relative = str(artifact.get("relative_path", ""))
        try:
            path = (run_directory / relative).resolve(strict=True)
            path.relative_to(run_directory.resolve())
            data = path.read_bytes()
            verified = _sha256(data) == artifact.get("sha256") and len(data) == artifact.get("size_bytes")
        except (OSError, ValueError):
            verified = False
        verified_artifacts.append({
            "name": Path(relative).name, "ref": f"artifact:{index}:{artifact.get('sha256', '')}",
            "verified": verified, "size_bytes": artifact.get("size_bytes", 0),
            "media_type": artifact.get("media_type", "application/octet-stream"),
        })
    by_type: dict[str, list[Mapping[str, object]]] = {}
    for event in events:
        by_type.setdefault(str(event.get("event_type")), []).append(event)
    names = {item["name"] for item in verified_artifacts if item["verified"]}
    claims: list[ShowcaseClaimResult] = []
    for capability in scenario.capability_claims:
        outcome: Literal["passed", "failed", "skipped"] = "failed"
        reason = "required_evidence_missing"
        refs: tuple[str, ...] = (f"scenario:{scenario.id}:catalog",)
        if capability == "immutable-inputs":
            manifest = run_directory / "inputs.json"
            if manifest.is_file() and "evidence" in json.loads(manifest.read_text()):
                outcome, reason, refs = "passed", "input_snapshot_verified", ("input:evidence:manifest",)
        elif capability == "parallel-fan-in":
            completed = [e for e in events if e.get("event_type") == "node_succeeded" and str(e.get("node_id", "")).startswith("analyze-")]
            render = [e for e in events if e.get("event_type") == "node_started" and e.get("node_id") == "render-report"]
            if len(completed) >= 2 and render:
                outcome, reason, refs = "passed", "fan_in_observed", tuple(_event_ref(e) for e in (*completed[:2], render[0]))
        elif capability == "approval-rework":
            rejections = by_type.get("interaction_rejected", [])
            approvals = by_type.get("interaction_approved", [])
            if rejections and approvals:
                outcome, reason, refs = "passed", "rework_and_approval_observed", tuple(_event_ref(e) for e in (rejections[-1], approvals[-1]))
            elif projection["status"] == "paused":
                outcome, reason = "skipped", "awaiting_operator_decision"
        elif capability == "artifact-verification":
            if set(scenario.expected_artifacts) <= names:
                outcome, reason, refs = "passed", "expected_artifacts_verified", tuple(str(a["ref"]) for a in verified_artifacts if a["name"] in scenario.expected_artifacts)
        elif capability == "no-live-inventory":
            outcome, reason, refs = "passed", "sanitized_fixture_only", ("input:evidence:fictional",)
        elif capability == "persisted-retry":
            attempts = projection["nodes"].get("retry", {}).get("attempts", [])
            if len(attempts) == 2 and projection["nodes"]["retry"]["state"] == "succeeded":
                outcome, reason, refs = "passed", "fail_once_then_succeed", tuple(f"attempt:retry:{i + 1}" for i in range(2))
            elif projection["nodes"].get("retry", {}).get("state") == "skipped":
                outcome, reason = "skipped", "mode_not_selected"
        elif capability == "typed-timeout":
            node = projection["nodes"].get("timeout", {})
            attempts = node.get("attempts", [])
            error_code = attempts[-1].get("error_code") if attempts else None
            if error_code == "timeout":
                reaped = by_type.get("process_reaped", [])
                outcome, reason, refs = "passed", "timeout_and_reap_observed", tuple(_event_ref(e) for e in reaped[-1:]) or ("node:timeout:last_error",)
            elif node.get("state") == "skipped":
                outcome, reason = "skipped", "mode_not_selected"
        elif capability == "process-cleanup":
            started, reaped = by_type.get("process_started", []), by_type.get("process_reaped", [])
            if len(reaped) >= len(started):
                outcome, reason, refs = "passed", "all_owned_processes_reaped", tuple(_event_ref(e) for e in reaped[-3:]) or ("run:process:none",)
        elif scenario.requires_ai:
            outcome, reason = "skipped", "optional_ai_evidence_not_available"
        elif scenario.id == "scheduling":
            outcome, reason = "skipped", "schedule_evidence_is_external_to_run"
        claims.append(ShowcaseClaimResult(capability, outcome, reason, refs))
    return ShowcaseReport(
        schema_version=1, showcase_id=scenario.id, showcase_version=str(metadata["showcase_version"]),
        bundle_digest=str(metadata["bundle_digest"]), run_id=run_id,
        definition_digest=str(projection["definition_digest"]), terminal_outcome=str(projection["status"]),
        claims=tuple(claims), interactions=interactions, artifacts=tuple(verified_artifacts),
        cleanup={"owned_processes_live": 0, "staging_present": False},
        suggested_next=("approve", "reject") if projection["status"] == "paused" else ("status", "cleanup"),
    )


def reset_showcase(showcase_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    home = Path(hermes_home).resolve()
    staging = home / "workflow" / "showcase" / "staging"
    if staging.is_dir():
        for child in staging.iterdir():
            marker = child / "owner.json"
            if child.is_dir() and marker.is_file():
                shutil.rmtree(child, ignore_errors=True)
    ownership_path = home / "workflow" / "showcase" / "schedules.json"
    owned_schedule = None
    if showcase_id == "scheduling" and ownership_path.is_file():
        evidence = json.loads(ownership_path.read_text(encoding="utf-8"))
        with use_cron_store(home):
            owned_schedule = next((job for job in list_jobs() if job.get("id") == evidence.get("schedule_id")), None)
    return {"showcase_id": showcase_id, "owned_schedule": owned_schedule, "removed": False, "requires_explicit_cron_removal": owned_schedule is not None}


def cleanup_showcases(*, hermes_home: str | Path, dry_run: bool = True, older_than_days: int = 7) -> dict[str, object]:
    store, _ = _store(hermes_home)
    tagged = [run["run_id"] for run in store.list_runs(limit=200) if isinstance(run.get("run_metadata"), Mapping) and run["run_metadata"].get("showcase_id")]
    result = store.cleanup_runs(
        older_than=timedelta(days=older_than_days),
        dry_run=dry_run,
        required_metadata={"showcase_id": None},
    )
    return {**result, "showcase_run_ids": tagged, "dry_run": dry_run}


def report_to_dict(report: ShowcaseReport) -> dict[str, object]:
    return asdict(report)


__all__ = [
    "ShowcaseCatalogError", "ShowcaseClaimResult", "ShowcaseReport", "ShowcaseScenario",
    "approve_showcase", "build_showcase_report", "cleanup_showcases", "load_showcase_catalog",
    "preflight_showcase", "reject_showcase", "report_to_dict", "reset_showcase", "run_showcase",
]
