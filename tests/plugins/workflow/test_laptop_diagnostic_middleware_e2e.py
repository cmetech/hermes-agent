from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
import pytest

from agent.plugin_agent import PluginAgentRunner
from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.entitlement import derive_ai_entitlement
from plugins.workflow.evidence import EVIDENCE_KINDS
from plugins.workflow.scheduler import RunScheduler
import plugins.workflow.showcase as showcase_module
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    build_risk_summary,
)
from tools.managed_process import ProcessIdentity


SYMPTOM_CANARY = "SYMPTOM-CANARY-TASK-2-6-9d3a7f"
FIXTURE_CANARY = "FICTIONAL-LAPTOP-042"
REOPEN_CANARY = "REOPEN-CANARY-TASK-2-6-f18c4b"
@contextmanager
def _bundle_path(root: Path):
    yield root.resolve()


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _coordinator_identity() -> CoordinatorIdentity:
    process = ProcessIdentity.capture(os.getpid())
    return CoordinatorIdentity(
        owner_id="laptop-diagnostic-admission",
        host_kind="web",
        host_instance_id="task-2-6",
        pid=process.pid,
        process_start_time=process.start_time,
    )


def _wait_for_detail(client: TestClient, run_id: str, predicate, *, timeout=30):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/plugins/workflow/runs/{run_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"run did not reach expected state: {latest}")


def _start_service(
    home: Path,
) -> tuple[WorkflowCoordinatorService, threading.Event, threading.Thread]:
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="laptop-diagnostic-task-2-6",
        ),
        hermes_home=home,
        heartbeat_seconds=0.1,
        lease_seconds=3.0,
        web_election_grace_seconds=0.05,
        sweep_backoff_seconds=(0.02, 0.04, 0.08),
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=service.run,
        args=(stop,),
        name="laptop-diagnostic-task-2-6-coordinator",
    )
    thread.start()
    return service, stop, thread


def _stop_service(
    store: RunStore,
    stop: threading.Event | None,
    thread: threading.Thread | None,
) -> None:
    if stop is None or thread is None:
        return
    stop.set()
    CoordinatorStore(store.database).notify_local()
    thread.join(timeout=15)
    assert not thread.is_alive()


@contextmanager
def _production_client(monkeypatch):
    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        yield client


def _exact_start_material(status: dict[str, object]) -> dict[str, object]:
    provenance = status["provenance"]
    assert isinstance(provenance, dict)
    return {
        "workflow": status["workflow"],
        "definition": status["definition_digest"],
        "policy": status["policy_digest"],
        "inputs": status["input_manifest_digest"],
        "trigger": status["trigger"],
        "concurrency": status["concurrency_key"],
        "operator_scope_digest": status["operator_scope_digest"],
        "run_metadata": dict(sorted(status["run_metadata"].items())),
        "provenance": {
            "source": provenance["source"],
            "assurance": provenance["assurance"],
            "idempotency_namespace_digest": status[
                "idempotency_namespace_digest"
            ],
        },
    }


def _assert_exact_identity_and_limits(
    store: RunStore,
    run_id: str,
    *,
    idempotency_key: str,
    verified,
) -> tuple[dict[str, object], dict[str, object]]:
    status = store.get_run_status(run_id)
    risk = build_risk_summary(
        verified.package,
        assess_compatibility(verified.package),
    )
    expected_metadata = {
        "bundle_digest": verified.bundle_digest,
        "risk_digest": risk.risk_digest,
        "showcase_id": "laptop-diagnostic",
        "showcase_provenance": "verified_bundled",
        "showcase_version": "1",
    }
    assert status["run_metadata"] == expected_metadata
    entitlement = derive_ai_entitlement(
        status["run_metadata"],
        definition_digest=status["definition_digest"],
    )
    assert asdict(entitlement) == {
        "value": "deterministic",
        "error_code": None,
        "error_message": None,
    }
    assert status["trigger"] == "desktop"
    assert status["execution_mode"] == "background"
    assert status["concurrency_key"] == "showcase:laptop-diagnostic"
    assert status["provenance"] == {
        "source": "desktop",
        "assurance": "local_admin_claim",
        "source_instance": "api:local-admin",
        "actor_id": None,
        "claimed_actor": "profile-local-dashboard",
        "intent_key_digest": hashlib.sha256(idempotency_key.encode()).hexdigest(),
        "return_route": None,
        "admitted_at": status["created_at"],
    }

    scheduler = RunScheduler(store)
    try:
        limits = scheduler._run_execution_limits(
            scheduler._load_run_package(run_id)
        )
    finally:
        scheduler.shutdown()
    assert asdict(limits) == {
        "max_parallel_nodes": 4,
        "max_total_workers": 4,
        "ai_idle_timeout_seconds": 300.0,
        "ai_wall_timeout_seconds": 300.0,
        "provider_request_timeout_seconds": 300.0,
        "combined_retries": 5,
        "subprocess_timeout_seconds": 120.0,
        "process_tree_rss_bytes": 2 * 1024 * 1024 * 1024,
        "process_tree_cpu_seconds": 900.0,
        "max_descendants": 8,
        "cooperative_shutdown_seconds": 5.0,
        "term_grace_seconds": 5.0,
        "kill_reap_grace_seconds": 2.0,
    }

    start_material = _exact_start_material(status)
    encoded = json.dumps(
        start_material, sort_keys=True, separators=(",", ":")
    ).encode()
    with sqlite3.connect(store.database) as connection:
        persisted_start_digest = connection.execute(
            "SELECT start_digest FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    assert hashlib.sha256(encoded).hexdigest() == persisted_start_digest
    assert RunStore._start_digest_from_projection(status) == persisted_start_digest
    return status, start_material


def _assert_fixture_snapshot(
    store: RunStore,
    run_id: str,
    authenticated_fixture: bytes,
) -> None:
    run_directory = store.run_directory(run_id)
    manifest = json.loads((run_directory / "inputs.json").read_text())
    assert set(manifest) == {"arguments", "evidence"}
    assert manifest["evidence"] == {
        "relative_path": "inputs/evidence",
        "size_bytes": len(authenticated_fixture),
        "media_type": "application/octet-stream",
        "sha256": hashlib.sha256(authenticated_fixture).hexdigest(),
    }
    assert (run_directory / "inputs/evidence").read_bytes() == authenticated_fixture
    assert (run_directory / "inputs/arguments.txt").read_text() == SYMPTOM_CANARY
    assert SYMPTOM_CANARY not in json.dumps(manifest, sort_keys=True)


def _assert_canaries_absent_from_operator_material(
    client: TestClient,
    store: RunStore,
    run_id: str,
    *,
    status: dict[str, object],
    start_material: dict[str, object],
    caplog,
) -> None:
    responses: list[bytes] = [
        json.dumps(status["run_metadata"], sort_keys=True).encode(),
        json.dumps(status, sort_keys=True).encode(),
        json.dumps(start_material, sort_keys=True).encode(),
        json.dumps(store.list_runs(), sort_keys=True).encode(),
        json.dumps(store.tail_events(run_id), sort_keys=True).encode(),
        client.get("/api/plugins/workflow/runs?view=all").content,
        client.get(f"/api/plugins/workflow/runs/{run_id}").content,
        client.get(f"/api/plugins/workflow/runs/{run_id}/events").content,
        "\n".join(record.getMessage() for record in caplog.records).encode(),
    ]
    for kind in sorted(EVIDENCE_KINDS):
        response = client.get(
            f"/api/plugins/workflow/runs/{run_id}/evidence",
            params={"kind": kind},
        )
        assert response.status_code == 200, (kind, response.text)
        responses.append(response.content)

    stale = client.post(
        f"/api/plugins/workflow/runs/{run_id}/approve",
        json={
            "expected_version": 1,
            "interaction_id": "stale-interaction",
            "comment": "must remain terminal",
        },
    )
    assert stale.status_code == 409
    responses.append(stale.content)

    for canary in (SYMPTOM_CANARY, FIXTURE_CANARY, REOPEN_CANARY):
        encoded = canary.encode()
        assert all(encoded not in response for response in responses), canary


def _run_laptop_branch(
    tmp_path,
    monkeypatch,
    caplog,
    *,
    branch: str,
    client: TestClient,
    stop_service=_stop_service,
) -> tuple[dict[str, object], int]:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _bundle_path(copied),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    caplog.set_level("DEBUG")

    fixture_path = (
        copied
        / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
    )
    budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    verified = showcase_module.load_verified_showcase_package(
        "laptop-diagnostic",
        read_budget=budget,
        force_reverify=True,
    )
    authenticated_fixture = budget.read_cached(fixture_path)
    assert FIXTURE_CANARY.encode() in authenticated_fixture
    authenticated_digest = hashlib.sha256(authenticated_fixture).hexdigest()
    mutated_fixture = json.dumps({"invalid_after_admission": REOPEN_CANARY}).encode()
    source_before = _tree_snapshot(copied)

    observed_verified_inputs: list[dict[str, tuple[bytes, str]]] = []
    original_prepare = RunStore.prepare_run_snapshot

    def observe_before_snapshot(self, package, *args, **kwargs):
        verified_inputs = kwargs.get("verified_inputs")
        assert verified_inputs == {
            "evidence": (authenticated_fixture, authenticated_digest)
        }
        observed_verified_inputs.append(dict(verified_inputs))
        fixture_path.write_bytes(mutated_fixture)
        return original_prepare(self, package, *args, **kwargs)

    monkeypatch.setattr(RunStore, "prepare_run_snapshot", observe_before_snapshot)

    store = RunStore(home)
    trust_store = WorkflowTrustStore(home)
    trust_store.trust("a" * 64, actor="existing-operator", risk_digest="b" * 64)
    trust_before = trust_store.path.read_bytes()
    identity = _coordinator_identity()
    coordinator_store = CoordinatorStore(store.database)
    leadership = coordinator_store.try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert leadership.is_leader
    store_before = store.database.read_bytes()
    runs_before = _tree_snapshot(store.runs_root)
    assert runs_before == {}

    real_runner_calls = 0
    if branch == "reject":

        def forbidden_real_run(*_args, **_kwargs):
            nonlocal real_runner_calls
            real_runner_calls += 1
            raise AssertionError("offline deterministic rework selected a real runner")

        monkeypatch.setattr(PluginAgentRunner, "run", forbidden_real_run)

    stop = None
    thread = None
    idempotency_key = f"laptop-diagnostic-task-2-6-{branch}"
    try:
        admitted = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "laptop-diagnostic",
                "catalog_source": "showcase",
                "values": {"symptom": SYMPTOM_CANARY},
                "idempotency_key": idempotency_key,
                "concurrency_policy": "queue",
            },
        )
        assert admitted.status_code == 202, admitted.text
        assert admitted.json()["result"] == {
            "run_id": admitted.json()["result"]["run_id"],
            "status": "running",
            "admission_disposition": "created",
            "queue_position": None,
            "blocked_by_run_id": None,
        }
        run_id = admitted.json()["result"]["run_id"]
        admitted_run_snapshot = _tree_snapshot(store.run_directory(run_id))
        admitted_store_snapshot = store.database.read_bytes()
        assert admitted_store_snapshot != store_before
        assert _tree_snapshot(store.runs_root) != runs_before
        assert observed_verified_inputs == [
            {"evidence": (authenticated_fixture, authenticated_digest)}
        ]
        assert fixture_path.read_bytes() == mutated_fixture
        _assert_fixture_snapshot(store, run_id, authenticated_fixture)
        _assert_exact_identity_and_limits(
            store,
            run_id,
            idempotency_key=idempotency_key,
            verified=verified,
        )

        assert coordinator_store.release(
            identity,
            epoch=leadership.lease.epoch,
            now=datetime.now(timezone.utc),
        )
        service, stop, thread = _start_service(home)
        paused = _wait_for_detail(
            client,
            run_id,
            lambda run: run["status"] == "paused"
            and run["nodes"]["review-plan"]["state"] == "paused",
        )
        assert service.health().code == "leader"
        pending = paused["pending_interaction"]
        assert pending["node_id"] == "review-plan"
        assert pending["type"] == "workflow_approval"

        if branch == "approve":
            decision = client.post(
                f"/api/plugins/workflow/runs/{run_id}/approve",
                json={
                    "expected_version": paused["state_version"],
                    "interaction_id": pending["interaction_id"],
                    "comment": "approve the fictional plan",
                },
            )
            assert decision.status_code == 200, decision.text
            outcome = _wait_for_detail(
                client,
                run_id,
                lambda run: run["status"] == "succeeded",
            )
            assert outcome["nodes"]["finalize-plan"]["state"] == "succeeded"
        else:
            decision = client.post(
                f"/api/plugins/workflow/runs/{run_id}/reject",
                json={
                    "expected_version": paused["state_version"],
                    "interaction_id": pending["interaction_id"],
                    "reason": "keep every fictional action manual",
                },
            )
            assert decision.status_code == 200, decision.text
            reworked = _wait_for_detail(
                client,
                run_id,
                lambda run: run["status"] == "paused"
                and run["nodes"]["review-plan"].get(
                    "approval_rework_attempts"
                )
                == 1,
            )
            durable_reworked = store.get_run_status(run_id)
            rework_artifact = next(
                artifact
                for artifact in durable_reworked["artifacts"]
                if Path(artifact["relative_path"]).name == "rework-output.txt"
            )
            rework_text = (
                store.run_directory(run_id) / rework_artifact["relative_path"]
            ).read_text()
            assert rework_text.endswith("DETERMINISTIC_COMPLETE")
            pending = reworked["pending_interaction"]
            exhausted = client.post(
                f"/api/plugins/workflow/runs/{run_id}/reject",
                json={
                    "expected_version": reworked["state_version"],
                    "interaction_id": pending["interaction_id"],
                    "reason": "bounded rejection outcome",
                },
            )
            assert exhausted.status_code == 200, exhausted.text
            outcome = _wait_for_detail(
                client,
                run_id,
                lambda run: run["status"] == "cancelled",
            )
            assert outcome["nodes"]["review-plan"]["approval_rework_attempts"] == 1

        active_stop, active_thread = stop, thread
        stop = None
        thread = None
        stop_service(store, active_stop, active_thread)
        final_status, start_material = _assert_exact_identity_and_limits(
            store,
            run_id,
            idempotency_key=idempotency_key,
            verified=verified,
        )
        final_run_snapshot = _tree_snapshot(store.run_directory(run_id))
        final_store_snapshot = store.database.read_bytes()
        assert final_run_snapshot != admitted_run_snapshot
        assert final_store_snapshot != admitted_store_snapshot
        assert trust_store.path.read_bytes() == trust_before
        _assert_fixture_snapshot(store, run_id, authenticated_fixture)
        assert fixture_path.read_bytes() == mutated_fixture
        source_after = _tree_snapshot(copied)
        fixture_relative = fixture_path.relative_to(copied).as_posix()
        assert source_after[fixture_relative] == ("file", mutated_fixture)
        assert {
            path: value
            for path, value in source_after.items()
            if path != fixture_relative
        } == {
            path: value
            for path, value in source_before.items()
            if path != fixture_relative
        }
        _assert_canaries_absent_from_operator_material(
            client,
            store,
            run_id,
            status=final_status,
            start_material=start_material,
            caplog=caplog,
        )
        return final_status, real_runner_calls
    finally:
        if stop is not None or thread is not None:
            active_stop, active_thread = stop, thread
            stop = None
            thread = None
            stop_service(store, active_stop, active_thread)
        showcase_module._clear_verified_showcase_cache_for_tests()


def _exercise_laptop_branch(
    tmp_path,
    monkeypatch,
    caplog,
    *,
    branch: str,
    after_client_start=None,
    stop_service=_stop_service,
) -> tuple[dict[str, object], int]:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    with _production_client(monkeypatch) as client:
        if after_client_start is not None:
            after_client_start(client)
        return _run_laptop_branch(
            tmp_path,
            monkeypatch,
            caplog,
            branch=branch,
            client=client,
            stop_service=stop_service,
        )


def test_laptop_diagnostic_real_middleware_approves_to_succeeded(
    tmp_path, monkeypatch, caplog
) -> None:
    outcome, real_runner_calls = _exercise_laptop_branch(
        tmp_path,
        monkeypatch,
        caplog,
        branch="approve",
    )

    assert outcome["status"] == "succeeded"
    assert real_runner_calls == 0
    assert {
        Path(artifact["relative_path"]).name for artifact in outcome["artifacts"]
    } >= {
        "diagnostic-report.json",
        "diagnostic-report.md",
        "remediation-plan.md",
    }


def test_laptop_diagnostic_real_middleware_rejects_with_bounded_offline_rework(
    tmp_path, monkeypatch, caplog
) -> None:
    outcome, real_runner_calls = _exercise_laptop_branch(
        tmp_path,
        monkeypatch,
        caplog,
        branch="reject",
    )

    assert outcome["status"] == "cancelled"
    assert outcome["nodes"]["review-plan"]["approval_rework_attempts"] == 1
    assert real_runner_calls == 0


def test_laptop_client_lifespan_exits_after_post_enter_setup_failure(
    tmp_path, monkeypatch, caplog
) -> None:
    def fail_after_worker_starts(client: TestClient) -> None:
        response = client.get("/api/plugins/workflow/runs/not-a-real-run/events")
        assert response.status_code == 404
        raise RuntimeError("post-enter setup failure")

    with pytest.raises(RuntimeError, match="post-enter setup failure"):
        _exercise_laptop_branch(
            tmp_path,
            monkeypatch,
            caplog,
            branch="approve",
            after_client_start=fail_after_worker_starts,
        )
    assert not any(
        thread.name.startswith("workflow-store-io")
        for thread in threading.enumerate()
    )


def test_laptop_client_lifespan_exits_when_service_cleanup_raises(
    tmp_path, monkeypatch, caplog
) -> None:
    cleanup_calls = []

    def start_worker(client: TestClient) -> None:
        response = client.get("/api/plugins/workflow/runs/not-a-real-run/events")
        assert response.status_code == 404

    def stop_then_raise(store, stop, thread) -> None:
        cleanup_calls.append((stop, thread))
        _stop_service(store, stop, thread)
        raise RuntimeError("service cleanup failure #1")

    with pytest.raises(RuntimeError, match="service cleanup failure #1"):
        _exercise_laptop_branch(
            tmp_path,
            monkeypatch,
            caplog,
            branch="approve",
            after_client_start=start_worker,
            stop_service=stop_then_raise,
        )
    assert len(cleanup_calls) == 1
    assert not any(
        thread.name.startswith("workflow-store-io")
        for thread in threading.enumerate()
    )
