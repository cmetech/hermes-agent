from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from fastapi.testclient import TestClient
import pytest
import yaml

from agent.plugin_agent import PluginAgentRunResult, PluginAgentRunner
from agent.plugin_agent_worker import _finalize_authenticated_mcp_config
from hermes_cli.plugin_services import BackgroundServiceContext
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.entitlement import DeterministicAgentRunner
from plugins.workflow.models import ExecutionFence
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    WorkflowRunnerBinding,
    assess_package_execution,
    background_execution_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
import plugins.workflow.api_admission as api_admission_module
import plugins.workflow.catalog_api as catalog_api_module
import plugins.workflow.runner_binding as runner_binding_module
import plugins.workflow.showcase as showcase_module
from tools.mcp_tool import _interpolate_env_vars
from plugins.workflow.trust import (
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    compute_package_digest,
)


EXPECTED_MCP_SERVERS = {
    "echo": {
        "command": "python",
        "args": ["mcp/echo-server.py"],
        "env": {},
    }
}


def _assert_authenticated_mcp_servers(
    servers,
    *,
    require_live_authority: bool = True,
) -> Path:
    assert set(servers) == {"echo"}
    server = servers["echo"]
    assert server["command"] == "python"
    assert server["env"] == {}
    assert server["args"] == ["mcp/echo-server.py"]
    authority = server["__hermes_authenticated_local_mcp"]
    assert authority["version"] == 2
    assert authority["file_count"] > 0
    assert authority["total_bytes"] > 0
    assert "entry" not in authority
    assert "files" not in authority
    authority_root = Path(authority["root"])
    payload_root = authority_root / authority["payload"]
    assert authority_root.is_absolute()
    assert "hermes-workflow-authority-" in authority_root.as_posix()
    if not require_live_authority:
        return authority_root
    assert authority_root.is_dir()

    # Match the real worker boundary exactly: JSON-carried config first resolves
    # environment placeholders, then validates and finalizes the private launch.
    interpolated = {
        name: _interpolate_env_vars(dict(config)) for name, config in servers.items()
    }
    finalized = _finalize_authenticated_mcp_config(interpolated)["echo"]
    assert finalized["args"][0:2] == ["-I", "-c"]
    assert "runpy.run_path" in finalized["args"][2]
    assert finalized["args"][3:5] == [
        str(payload_root),
        "mcp/echo-server.py",
    ]
    assert finalized["__hermes_private_mcp_cwd"] == str(payload_root)
    return authority_root


class CapabilityDeclaringRecordingRunner:
    """Record sealed requests without claiming to start their MCP servers."""

    def __init__(self) -> None:
        self.requests = []

    def run(self, request, *, is_cancelled=None) -> PluginAgentRunResult:
        assert is_cancelled is None or not is_cancelled()
        _assert_authenticated_mcp_servers(request.mcp_servers)
        assert request.allowed_tools is None
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=json.dumps({
                "summary": f"recorded request {len(self.requests)}",
                "simulated": True,
            }),
            session_id="task-3-4-recorded-session",
            provider="task-3-4-recording-runner",
            model="offline-recording-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 0, "recording_runner": True},
        )


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


def _write_runtime_config(
    home: Path,
    *,
    api_mode: str,
    base_url: str = "http://127.0.0.1:9/v1",
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "default": "workflow-task-3-4-model",
                    "provider": "workflow-task-3-4",
                },
                "custom_providers": [
                    {
                        "name": "workflow-task-3-4",
                        "base_url": base_url,
                        "api_key": "offline-test-key",
                        "api_mode": api_mode,
                        "model": "workflow-task-3-4-model",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bundled_skill = Path(__file__).parents[3] / "skills/creative/ascii-art"
    shutil.copytree(
        bundled_skill,
        home / "skills/creative/ascii-art",
        dirs_exist_ok=True,
    )


def _coordinator_identity(label: str) -> CoordinatorIdentity:
    return CoordinatorIdentity(
        owner_id=label,
        host_kind="web",
        host_instance_id=label,
        pid=os.getpid(),
        process_start_time=None,
    )


def _healthy_admission_lease(store: RunStore, label: str):
    identity = _coordinator_identity(label)
    acquired = CoordinatorStore(store.database).try_acquire(
        identity,
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader
    return identity, acquired.lease


@contextmanager
def _production_client(monkeypatch):
    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        yield client


def _capable_test_binding(runner) -> WorkflowRunnerBinding:
    return WorkflowRunnerBinding(
        real_runner=runner,
        deterministic_runner=DeterministicAgentRunner(),
        real_capabilities=RunnerCapabilities(starts_request_mcp=True),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
        ),
    )


def _start_service(
    home: Path,
    binding: WorkflowRunnerBinding,
) -> tuple[WorkflowCoordinatorService, threading.Event, threading.Thread]:
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="ai-extensions-task-3-4",
        ),
        hermes_home=home,
        heartbeat_seconds=0.1,
        lease_seconds=3.0,
        web_election_grace_seconds=0.05,
        sweep_backoff_seconds=(0.02, 0.04, 0.08),
        runner_binding=binding,
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=service.run,
        args=(stop,),
        name="ai-extensions-task-3-4-coordinator",
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


def _wait_for_status(
    client: TestClient,
    run_id: str,
    expected: str,
    *,
    timeout: float = 30,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/plugins/workflow/runs/{run_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] == expected:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"run did not reach {expected}: {latest}")


def _persisted_start_digest(store: RunStore, run_id: str) -> str:
    with sqlite3.connect(store.database) as connection:
        return str(
            connection.execute(
                "SELECT start_digest FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        )


def _database_dump(database: Path) -> tuple[str, ...]:
    with sqlite3.connect(database) as connection:
        return tuple(connection.iterdump())


def _verified_showcase(name: str):
    budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    verified = showcase_module.load_verified_showcase_package(
        name,
        read_budget=budget,
        force_reverify=True,
    )
    return verified, budget


def _admit_sealed_showcase_run(
    store: RunStore,
    *,
    showcase_name: str,
    run_metadata: dict[str, str],
    idempotency_key: str,
):
    verified, budget = _verified_showcase(showcase_name)
    package_digest = compute_package_digest(verified.package, read_budget=budget)
    prepared = store.prepare_run_snapshot(
        verified.package,
        resource_read_budget=budget,
        trusted_package_digest=package_digest,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=showcase_name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=idempotency_key,
            idempotency_namespace="profile-local-dashboard",
            concurrency_key=f"showcase:{showcase_name}:{idempotency_key}",
            concurrency_policy="queue",
            execution_mode="background",
            run_metadata=run_metadata,
            provenance=TriggerProvenance.authenticated_api(
                source="desktop",
                assurance="local_admin_claim",
                intent_key=idempotency_key,
                source_instance="api:local-admin",
                principal="profile-local-dashboard",
            ),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


def _scenario_metadata(name: str, binding: WorkflowRunnerBinding) -> dict[str, str]:
    verified, budget = _verified_showcase(name)
    _compatibility, risk = assess_package_execution(
        verified.package,
        background_execution_context(
            binding,
            requires_ai=verified.scenario.requires_ai,
        ),
        read_budget=budget,
    )
    return {
        "ai_entitlement": "real",
        "bundle_digest": verified.bundle_digest,
        "risk_digest": risk.risk_digest,
        "showcase_id": verified.scenario.id,
        "showcase_provenance": "verified_bundled",
        "showcase_version": verified.scenario.package_version,
    }


class _ProviderCallTrap(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        type(self).requests += 1
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


def _start_provider_trap() -> tuple[ThreadingHTTPServer, str]:
    _ProviderCallTrap.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderCallTrap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def _run_ai_extensions_real_middleware_admits_joins_and_coordinator_succeeds(
    tmp_path,
    monkeypatch,
    client: TestClient,
    *,
    stop_service=_stop_service,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    _write_runtime_config(home, api_mode="chat_completions")
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    identity, lease = _healthy_admission_lease(store, "task-3-4-admission")
    trust_store = WorkflowTrustStore(home)
    trust_store.trust("a" * 64, actor="existing-operator", risk_digest="b" * 64)
    store_before_reads = store.database.read_bytes()
    trust_before = trust_store.path.read_bytes()
    bundle_before = _tree_snapshot(
        Path(showcase_module.__file__).with_name("showcases")
    )
    recording_runner = CapabilityDeclaringRecordingRunner()
    stop = None
    thread = None
    request_body = {
        "workflow": "ai-extensions",
        "catalog_source": "showcase",
        "values": {},
        "idempotency_key": "ai-extensions-task-3-4",
        "concurrency_policy": "queue",
    }
    forbidden_public_fields = {
        "runner",
        "runner_binding",
        "runner_capabilities",
        "runtime_capabilities",
        "mcp_available",
        "ai_entitlement",
        "consent",
        "confirmation_token",
    }

    try:
        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200, catalog_response.text
        row = next(
            item
            for item in catalog_response.json()["items"]
            if item.get("source") == "showcase" and item.get("name") == "ai-extensions"
        )
        assert row["run_support"] == {"supported": True, "reason": "supported"}
        assert row["compatibility"]["runnable"] is True
        assert row["requires_ai"] is True

        detail_response = client.get(
            "/api/plugins/workflow/workflows/ai-extensions",
            params={"catalog_source": "showcase"},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["run_support"] == {"supported": True, "reason": "supported"}
        assert detail["compatibility"]["runnable"] is True
        assert detail["requires_ai"] is True
        assert store.database.read_bytes() == store_before_reads
        assert trust_store.path.read_bytes() == trust_before

        assert forbidden_public_fields.isdisjoint(request_body)
        with monkeypatch.context() as admission_guard:
            admission_guard.setattr(
                RunScheduler,
                "advance",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("REST admission executed a workflow node")
                ),
            )
            created_response = client.post(
                "/api/plugins/workflow/runs", json=request_body
            )
            existing_response = client.post(
                "/api/plugins/workflow/runs", json=request_body
            )

        assert created_response.status_code == existing_response.status_code == 202
        created = created_response.json()["result"]
        existing = existing_response.json()["result"]
        assert created["admission_disposition"] == "created"
        assert existing == {**created, "admission_disposition": "existing"}
        run_id = created["run_id"]
        assert len(list(store.runs_root.rglob("run.json"))) == 1

        admitted = store.get_run_status(run_id)
        assert admitted["execution_mode"] == "background"
        assert admitted["trigger"] == "desktop"
        assert admitted["provenance"] == {
            "source": "desktop",
            "assurance": "local_admin_claim",
            "source_instance": "api:local-admin",
            "actor_id": None,
            "claimed_actor": "profile-local-dashboard",
            "intent_key_digest": hashlib.sha256(
                request_body["idempotency_key"].encode()
            ).hexdigest(),
            "return_route": None,
            "admitted_at": admitted["created_at"],
        }
        assert admitted["run_metadata"] == {
            "ai_entitlement": "real",
            "bundle_digest": admitted["run_metadata"]["bundle_digest"],
            "risk_digest": admitted["run_metadata"]["risk_digest"],
            "showcase_id": "ai-extensions",
            "showcase_provenance": "verified_bundled",
            "showcase_version": "1",
        }
        assert len(admitted["run_metadata"]["bundle_digest"]) == 64
        assert len(admitted["run_metadata"]["risk_digest"]) == 64
        assert store.database.read_bytes() != store_before_reads
        assert _persisted_start_digest(store, run_id) == (
            RunStore._start_digest_from_projection(admitted)
        )
        assert trust_store.path.read_bytes() == trust_before
        assert (
            _tree_snapshot(Path(showcase_module.__file__).with_name("showcases"))
            == bundle_before
        )

        assert CoordinatorStore(store.database).release(
            identity,
            epoch=lease.epoch,
            now=datetime.now(timezone.utc),
        )
        _service, stop, thread = _start_service(
            home,
            _capable_test_binding(recording_runner),
        )
        succeeded = _wait_for_status(client, run_id, "succeeded")

        active_stop, active_thread = stop, thread
        stop = None
        thread = None
        stop_service(store, active_stop, active_thread)
        assert succeeded["status"] == "succeeded"
        assert [request.context_mode for request in recording_runner.requests] == [
            "fresh",
            "shared",
        ]
        assert len(recording_runner.requests) == 2
        for request in recording_runner.requests:
            authority_root = _assert_authenticated_mcp_servers(
                request.mcp_servers,
                require_live_authority=False,
            )
            assert not authority_root.exists()
        assert trust_store.path.read_bytes() == trust_before
        assert _persisted_start_digest(store, run_id) == (
            RunStore._start_digest_from_projection(store.get_run_status(run_id))
        )
    finally:
        if stop is not None or thread is not None:
            active_stop, active_thread = stop, thread
            stop = None
            thread = None
            stop_service(store, active_stop, active_thread)
        showcase_module._clear_verified_showcase_cache_for_tests()


def _run_ai_extensions_incapable_runtime_is_typed_and_zero_residue(
    tmp_path,
    monkeypatch,
    client: TestClient,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _start_provider_trap()
    _write_runtime_config(
        home,
        api_mode="codex_app_server",
        base_url=base_url,
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    trust_store = WorkflowTrustStore(home)
    trust_store.trust("a" * 64, actor="existing-operator", risk_digest="b" * 64)
    trust_before = trust_store.path.read_bytes()

    try:
        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200, catalog_response.text
        row = next(
            item
            for item in catalog_response.json()["items"]
            if item.get("source") == "showcase" and item.get("name") == "ai-extensions"
        )
        assert row["run_support"] == {"supported": True, "reason": "supported"}
        assert row["compatibility"]["runnable"] is False
        assert row["requires_ai"] is True

        detail_response = client.get(
            "/api/plugins/workflow/workflows/ai-extensions",
            params={"catalog_source": "showcase"},
        )
        assert detail_response.status_code == 200, detail_response.text
        assert detail_response.json()["compatibility"]["runnable"] is False
        store_before_refusal = _database_dump(store.database)

        refused = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "ai-extensions",
                "catalog_source": "showcase",
                "values": {},
                "idempotency_key": "ai-extensions-incapable-task-3-4",
                "concurrency_policy": "queue",
            },
        )

        assert refused.status_code == 409
        assert refused.json() == {
            "detail": {
                "code": "workflow_compatibility_blocked",
                "retryable": False,
            }
        }
        assert _database_dump(store.database) == store_before_refusal
        assert trust_store.path.read_bytes() == trust_before
        assert list(store.runs_root.rglob("run.json")) == []
        assert list(store.staging_root.iterdir()) == []
        assert _ProviderCallTrap.requests == 0
    finally:
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()


def _run_explicit_real_non_ai_rework_fails_typed_integrity_without_runner(
    tmp_path,
    monkeypatch,
    client: TestClient,
) -> None:
    home = tmp_path / "non-ai"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    _write_runtime_config(home, api_mode="chat_completions")
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    identity, lease = _healthy_admission_lease(store, "task-3-4-integrity-non-ai")
    runner = CapabilityDeclaringRecordingRunner()
    binding = _capable_test_binding(runner)
    _verified_laptop, laptop_budget = _verified_showcase("laptop-diagnostic")
    laptop_fixture = laptop_budget.read_cached(
        Path(showcase_module.__file__).with_name("showcases")
        / "packages/laptop-diagnostic/fixtures/laptop-snapshot.json"
    )
    original_metadata = api_admission_module.verified_showcase_run_metadata

    def explicit_real_non_ai_metadata(**kwargs):
        metadata = original_metadata(**kwargs)
        if kwargs["showcase_id"] == "laptop-diagnostic":
            metadata["ai_entitlement"] = "real"
        return metadata

    monkeypatch.setattr(
        api_admission_module,
        "verified_showcase_run_metadata",
        explicit_real_non_ai_metadata,
    )
    stop = None
    thread = None

    try:
        admitted_response = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "laptop-diagnostic",
                "catalog_source": "showcase",
                "values": {"symptom": "fictional task 3.4 integrity exercise"},
                "idempotency_key": "task-3-4-integrity-non-ai",
                "concurrency_policy": "queue",
            },
        )
        assert admitted_response.status_code == 202, admitted_response.text
        run_id = admitted_response.json()["result"]["run_id"]
        projection = store.get_run_status(run_id)
        assert projection["workflow"] == "laptop-diagnostic"
        assert projection["run_metadata"]["showcase_id"] == "laptop-diagnostic"
        assert projection["run_metadata"]["showcase_provenance"] == "verified_bundled"
        assert projection["run_metadata"]["ai_entitlement"] == "real"
        assert (
            store.run_directory(run_id) / "inputs/evidence"
        ).read_bytes() == laptop_fixture
        assert CoordinatorStore(store.database).release(
            identity,
            epoch=lease.epoch,
            now=datetime.now(timezone.utc),
        )
        _service, stop, thread = _start_service(home, binding)
        paused = _wait_for_status(client, run_id, "paused")
        pending = paused["pending_interaction"]
        assert pending["node_id"] == "review-plan"
        assert pending["type"] == "workflow_approval"
        rejected = client.post(
            f"/api/plugins/workflow/runs/{run_id}/reject",
            json={
                "expected_version": paused["state_version"],
                "interaction_id": pending["interaction_id"],
                "reason": "exercise authenticated non-AI rework integrity",
            },
        )
        assert rejected.status_code == 200, rejected.text
        failed = _wait_for_status(client, run_id, "failed")

        active_stop, active_thread = stop, thread
        stop = None
        thread = None
        _stop_service(store, active_stop, active_thread)
        assert runner.requests == []
        assert failed["nodes"]["review-plan"]["state"] == "failed"
        assert (
            failed["nodes"]["review-plan"]["attempts"][-1]["error_code"]
            == "execution_integrity"
        )
    finally:
        if stop is not None or thread is not None:
            active_stop, active_thread = stop, thread
            stop = None
            thread = None
            _stop_service(store, active_stop, active_thread)
        showcase_module._clear_verified_showcase_cache_for_tests()


def _run_explicit_real_digest_mismatch_fails_before_coordinator_runner(
    tmp_path,
    monkeypatch,
    client: TestClient,
) -> None:
    home = tmp_path / "digest-mismatch"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    _write_runtime_config(home, api_mode="chat_completions")
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    identity, lease = _healthy_admission_lease(
        store, "task-3-4-integrity-digest-mismatch"
    )
    runner = CapabilityDeclaringRecordingRunner()
    binding = _capable_test_binding(runner)
    metadata = _scenario_metadata("ai-extensions", binding)
    metadata["bundle_digest"] = "0" * 64
    run_id = _admit_sealed_showcase_run(
        store,
        showcase_name="ai-extensions",
        run_metadata=metadata,
        idempotency_key="task-3-4-integrity-digest-mismatch",
    )
    assert CoordinatorStore(store.database).release(
        identity,
        epoch=lease.epoch,
        now=datetime.now(timezone.utc),
    )
    stop = None
    thread = None

    try:
        _service, stop, thread = _start_service(home, binding)
        failed = _wait_for_status(client, run_id, "failed")

        active_stop, active_thread = stop, thread
        stop = None
        thread = None
        _stop_service(store, active_stop, active_thread)
        assert runner.requests == []
        assert failed["status"] == "failed"
        assert failed["nodes"]["inspect"]["state"] == "failed"
        assert failed["nodes"]["inspect"]["attempts"][-1]["error_code"] == (
            "execution_integrity"
        )
    finally:
        if stop is not None or thread is not None:
            active_stop, active_thread = stop, thread
            stop = None
            thread = None
            _stop_service(store, active_stop, active_thread)
        showcase_module._clear_verified_showcase_cache_for_tests()


def _run_capable_admission_then_actual_app_server_runtime_fails_before_provider(
    tmp_path,
    monkeypatch,
    client: TestClient,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _start_provider_trap()
    _write_runtime_config(home, api_mode="chat_completions", base_url=base_url)
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    identity, lease = _healthy_admission_lease(store, "task-3-4-runtime-change")
    stop = None
    thread = None

    try:
        admitted_response = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "ai-extensions",
                "catalog_source": "showcase",
                "values": {},
                "idempotency_key": "ai-extensions-runtime-change-task-3-4",
                "concurrency_policy": "queue",
            },
        )
        assert admitted_response.status_code == 202, admitted_response.text
        run_id = admitted_response.json()["result"]["run_id"]
        admitted = store.get_run_status(run_id)
        assert admitted["run_metadata"]["ai_entitlement"] == "real"

        assert CoordinatorStore(store.database).release(
            identity,
            epoch=lease.epoch,
            now=datetime.now(timezone.utc),
        )
        actual_runner = PluginAgentRunner(plugin_id="workflow/task-3-4")
        capable_binding = _capable_test_binding(actual_runner)
        _write_runtime_config(
            home,
            api_mode="codex_app_server",
            base_url=base_url,
        )
        _service, stop, thread = _start_service(home, capable_binding)
        failed = _wait_for_status(client, run_id, "failed")

        active_stop, active_thread = stop, thread
        stop = None
        thread = None
        _stop_service(store, active_stop, active_thread)
        assert failed["status"] == "failed"
        assert failed.get("terminal_code") is None
        assert failed["nodes"]["inspect"]["attempts"][-1]["error_code"] == (
            "package_mcp_unavailable"
        )
        assert _ProviderCallTrap.requests == 0
    finally:
        if stop is not None or thread is not None:
            active_stop, active_thread = stop, thread
            stop = None
            thread = None
            _stop_service(store, active_stop, active_thread)
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()


def test_ai_extensions_real_middleware_admits_joins_and_coordinator_succeeds(
    tmp_path, monkeypatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_ai_extensions_real_middleware_admits_joins_and_coordinator_succeeds(
            tmp_path, monkeypatch, client
        )


def test_ai_service_cleanup_failure_runs_once_and_preserves_lifespan_exit(
    tmp_path, monkeypatch
) -> None:
    cleanup_calls = []

    def stop_then_raise(store, stop, thread) -> None:
        cleanup_calls.append((stop, thread))
        _stop_service(store, stop, thread)
        raise RuntimeError(f"AI service cleanup failure #{len(cleanup_calls)}")

    with pytest.raises(RuntimeError, match="AI service cleanup failure #1"):
        with _production_client(monkeypatch) as client:
            _run_ai_extensions_real_middleware_admits_joins_and_coordinator_succeeds(
                tmp_path,
                monkeypatch,
                client,
                stop_service=stop_then_raise,
            )

    assert len(cleanup_calls) == 1
    assert not any(
        thread.name.startswith("workflow-store-io")
        for thread in threading.enumerate()
    )


def test_ai_extensions_incapable_runtime_is_typed_and_zero_residue(
    tmp_path, monkeypatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_ai_extensions_incapable_runtime_is_typed_and_zero_residue(
            tmp_path, monkeypatch, client
        )


def test_explicit_real_non_ai_rework_fails_typed_integrity_without_runner(
    tmp_path, monkeypatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_explicit_real_non_ai_rework_fails_typed_integrity_without_runner(
            tmp_path, monkeypatch, client
        )


def test_explicit_real_digest_mismatch_fails_before_coordinator_runner(
    tmp_path, monkeypatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_explicit_real_digest_mismatch_fails_before_coordinator_runner(
            tmp_path, monkeypatch, client
        )


def test_capable_admission_then_actual_app_server_runtime_fails_before_provider(
    tmp_path, monkeypatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_capable_admission_then_actual_app_server_runtime_fails_before_provider(
            tmp_path, monkeypatch, client
        )


def test_production_api_and_coordinator_share_binding_declaration_and_no_request_seam(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    _write_runtime_config(home, api_mode="chat_completions")

    assert api_admission_module.production_workflow_runner_binding is (
        runner_binding_module.production_workflow_runner_binding
    )
    assert catalog_api_module.production_workflow_runner_binding is (
        runner_binding_module.production_workflow_runner_binding
    )
    api_binding = api_admission_module.production_workflow_runner_binding()
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="web",
            host_instance_id="task-3-4-production-binding",
        ),
        hermes_home=home,
    )
    scheduler = service._scheduler(
        RunStore(home),
        fence=ExecutionFence("task-3-4-production-binding", 1),
    )
    try:
        coordinator_binding = scheduler.runner_binding
        assert asdict(coordinator_binding.real_capabilities) == asdict(
            api_binding.real_capabilities
        )
        assert asdict(coordinator_binding.deterministic_capabilities) == asdict(
            api_binding.deterministic_capabilities
        )
        assert asdict(coordinator_binding.runtime_capabilities) == asdict(
            api_binding.runtime_capabilities
        )
        assert type(coordinator_binding.real_runner) is type(api_binding.real_runner)
        assert type(coordinator_binding.deterministic_runner) is type(
            api_binding.deterministic_runner
        )
    finally:
        scheduler.shutdown()

    from hermes_cli import web_server

    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        rejected = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "unreachable",
                "values": {},
                "idempotency_key": "task-7-request-contract",
                "concurrency_policy": "queue",
                "runner_binding": "test-only-seam",
            },
        )
    assert rejected.status_code == 422
    assert any(
        issue["loc"] == ["body", "runner_binding"]
        and issue["type"] == "extra_forbidden"
        for issue in rejected.json()["detail"]
    )
