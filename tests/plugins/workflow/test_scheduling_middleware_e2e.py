from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time

from fastapi.testclient import TestClient
import pytest
import yaml

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.plugin_services import BackgroundServiceContext
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.entitlement import DeterministicAgentRunner
from plugins.workflow.lease_clock import LeaseClockSample, current_boot_id
from plugins.workflow.models import ExecutionFence
from plugins.workflow.runner_binding import RunnerCapabilities, WorkflowRunnerBinding
from plugins.workflow.store import RunStore
import plugins.workflow.showcase as showcase_module
from tools.managed_process import ProcessIdentity


UTC = timezone.utc


class _Clocks:
    def __init__(self) -> None:
        self.wall = datetime.now(UTC).replace(microsecond=123456)

    def utcnow(self) -> datetime:
        return self.wall

    def lease_sample(self) -> LeaseClockSample:
        return LeaseClockSample(self.wall, time.monotonic(), current_boot_id())


class _ProviderTrap(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        type(self).requests += 1
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


class _RecordingAIRunner:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def run(self, request, *, is_cancelled=None) -> PluginAgentRunResult:
        assert is_cancelled is None or not is_cancelled()
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=json.dumps({
                "summary": "local scheduled fixture",
                "simulated": True,
            }),
            session_id="task-4-7-local-session",
            provider="task-4-7-local-runner",
            model="offline-recording-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 0, "recording_runner": True},
        )


def _provider_trap() -> tuple[ThreadingHTTPServer, str]:
    _ProviderTrap.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderTrap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def _write_runtime_config(home: Path, base_url: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "default": "workflow-task-4-7-model",
                    "provider": "workflow-task-4-7",
                },
                "custom_providers": [
                    {
                        "name": "workflow-task-4-7",
                        "base_url": base_url,
                        "api_key": "offline-test-key",
                        "api_mode": "chat_completions",
                        "model": "workflow-task-4-7-model",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    shutil.copytree(
        Path(__file__).parents[3] / "skills/creative/ascii-art",
        home / "skills/creative/ascii-art",
        dirs_exist_ok=True,
    )


def _binding(
    runner: _RecordingAIRunner,
    *,
    runner_capable: bool = True,
    api_mode: str = "chat_completions",
) -> WorkflowRunnerBinding:
    return WorkflowRunnerBinding(
        real_runner=runner,
        deterministic_runner=DeterministicAgentRunner(),
        real_capabilities=RunnerCapabilities(starts_request_mcp=runner_capable),
        deterministic_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode=api_mode,
            hermes_managed_tool_loop=api_mode != "codex_app_server",
        ),
    )


def _identity(label: str) -> CoordinatorIdentity:
    process = ProcessIdentity.capture(os.getpid())
    return CoordinatorIdentity(
        owner_id=label,
        host_kind="gateway",
        host_instance_id=label,
        pid=process.pid,
        process_start_time=process.start_time,
    )


def _leader(
    store: RunStore,
    clocks: _Clocks,
    label: str,
) -> tuple[CoordinatorStore, CoordinatorIdentity, int]:
    coordinator = CoordinatorStore(store.database, clock=clocks.lease_sample)
    identity = _identity(label)
    acquired = coordinator.try_acquire(
        identity,
        now=clocks.wall,
        lease_seconds=300,
    )
    assert acquired.is_leader
    return coordinator, identity, acquired.lease.epoch


@contextmanager
def _production_client(monkeypatch: pytest.MonkeyPatch):
    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        yield client


def _schedule_showcase(
    client: TestClient,
    clocks: _Clocks,
    *,
    workflow: str,
    key: str,
) -> tuple[str, str]:
    catalog = client.get("/api/plugins/workflow/workflows")
    assert catalog.status_code == 200, catalog.text
    row = next(
        item
        for item in catalog.json()["items"]
        if item.get("source") == "showcase" and item.get("name") == workflow
    )
    assert row["run_support"] == (
        {"supported": False, "reason": "schedule_required"}
        if workflow == "scheduling"
        else {"supported": True, "reason": "supported"}
    )
    schedule_at = (
        (clocks.wall + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    )
    response = client.post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": workflow,
            "catalog_source": "showcase",
            "values": {},
            "idempotency_key": key,
            "concurrency_policy": "queue",
            "schedule_at": schedule_at,
        },
    )
    assert response.status_code == 202, response.text
    result = response.json()["result"]
    assert result["status"] == "queued"
    run_id = str(result["run_id"])
    detail = client.get(f"/api/plugins/workflow/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "queued"
    assert detail.json()["presentation_state"] == "scheduled_wait"
    assert detail.json()["schedule_at"] == schedule_at
    return run_id, schedule_at


def _service_and_scheduler(
    home: Path,
    store: RunStore,
    clocks: _Clocks,
    identity: CoordinatorIdentity,
    epoch: int,
    binding: WorkflowRunnerBinding,
):
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id=identity.host_instance_id,
        ),
        hermes_home=home,
        heartbeat_seconds=10,
        lease_seconds=300,
        sweep_backoff_seconds=(0.02,),
        utcnow=clocks.utcnow,
        runner_binding=binding,
    )
    fence = ExecutionFence(identity.owner_id, epoch)
    scheduler = service._scheduler(store, fence=fence)
    return service, scheduler


def _wait_for_terminal(store: RunStore, run_id: str, *, timeout: float = 30):
    deadline = time.monotonic() + timeout
    latest = store.load_run(run_id)
    while latest["status"] not in {"succeeded", "failed", "cancelled"}:
        if time.monotonic() >= deadline:
            raise AssertionError(f"run did not become terminal: {latest}")
        time.sleep(0.02)
        latest = store.load_run(run_id)
    return latest


def _claims(store: RunStore, run_id: str) -> int:
    with store._connect() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
        )


def _run_events(store: RunStore, run_id: str, event_type: str) -> list[dict]:
    return [
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == event_type
    ]


def _run_authenticated_run_later_defers_real_wake_then_executes_checkpoint_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _provider_trap()
    _write_runtime_config(home, base_url)
    showcase_module._clear_verified_showcase_cache_for_tests()
    clocks = _Clocks()
    store = RunStore(home, lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks, "task-4-7-success")
    runner = _RecordingAIRunner()
    binding = _binding(runner)
    service, scheduler = _service_and_scheduler(
        home, store, clocks, identity, epoch, binding
    )

    try:
        from hermes_cli import web_server

        with TestClient(web_server.app) as unauthenticated_client:
            unauthenticated = unauthenticated_client.get(
                "/api/plugins/workflow/workflows"
            )
        assert unauthenticated.status_code == 401
        run_id, schedule_at = _schedule_showcase(
            client,
            clocks,
            workflow="scheduling",
            key="task-4-7-scheduling-success",
        )
        admitted = store.load_run(run_id)
        assert admitted["trigger"] == "desktop"
        assert admitted["execution_mode"] == "background"
        assert admitted["provenance"]["assurance"] == "local_admin_claim"
        assert admitted["run_metadata"]["schedule_at"] == schedule_at

        actionable, cursor, _progress = service._sweep_once(
            store, coordinator, identity, epoch, scheduler
        )

        assert actionable is False
        assert cursor is None
        assert store.load_run(run_id)["status"] == "queued"
        assert _claims(store, run_id) == 0
        assert _run_events(store, run_id, "run_promoted") == []
        assert runner.requests == []
        assert _ProviderTrap.requests == 0
        with store._connect() as connection:
            wakes = connection.execute(
                "SELECT reason_code, outcome, completed_at "
                "FROM coordinator_wakes WHERE run_id=?",
                (run_id,),
            ).fetchall()
        assert len(wakes) == 1
        assert wakes[0]["reason_code"] == "run_admitted"
        assert wakes[0]["outcome"] == "scheduled_not_due"
        assert wakes[0]["completed_at"] is not None
        assert (
            coordinator.pending_wakes(identity, epoch=epoch, now=clocks.wall, limit=100)
            == ()
        )

        clocks.wall = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
        actionable, _cursor, _progress = service._sweep_once(
            store, coordinator, identity, epoch, scheduler
        )
        assert actionable is True
        terminal = _wait_for_terminal(store, run_id)

        # Surface last_error: a bare status comparison told us only
        # "failed != succeeded" on Windows CI, with no way to tell whether
        # the node crashed, the runtime was missing, or a lease expired.
        assert terminal["status"] == "succeeded", terminal.get("last_error")
        assert terminal["schedule_revalidation"] == {
            "execution_identity": terminal["run_metadata"]["execution_identity"],
            "admission_state_version": 1,
        }
        promoted = _run_events(store, run_id, "run_promoted")
        assert len(promoted) == 1
        assert promoted[0]["payload"] == {"schedule_revalidated": True}
        assert len(_run_events(store, run_id, "node_succeeded")) == 1
        artifact = next(
            item
            for item in terminal["artifacts"]
            if str(item["relative_path"]).endswith("scheduled-checkpoint.json")
        )
        checkpoint = json.loads(
            (store.run_directory(run_id) / artifact["relative_path"]).read_text()
        )
        assert checkpoint == {
            "schema_version": 1,
            "checkpoint": "deterministic-one-shot",
            "simulated": True,
        }
        assert runner.requests == []
        assert _ProviderTrap.requests == 0
    finally:
        scheduler.shutdown(deadline_seconds=5)
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()


def _run_authenticated_cancel_before_fire_retains_evidence_and_never_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _provider_trap()
    _write_runtime_config(home, base_url)
    showcase_module._clear_verified_showcase_cache_for_tests()
    clocks = _Clocks()
    store = RunStore(home, lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks, "task-4-7-cancel")
    runner = _RecordingAIRunner()

    try:
        run_id, schedule_at = _schedule_showcase(
            client,
            clocks,
            workflow="scheduling",
            key="task-4-7-scheduling-cancel",
        )
        before = store.load_run(run_id)
        run_directory = store.run_directory(run_id)
        journal_before = (run_directory / "events.jsonl").read_bytes()
        cancelled = client.post(
            f"/api/plugins/workflow/runs/{run_id}/cancel",
            json={"expected_version": before["state_version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["schedule_at"] == schedule_at
        events = client.get(f"/api/plugins/workflow/runs/{run_id}/events")
        assert events.status_code == 200, events.text
        assert any(
            event["event_type"] == "run_cancelled" for event in events.json()["events"]
        )

        restarted = RunStore(home, lease_clock=clocks.lease_sample)
        clocks.wall = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
        service, scheduler = _service_and_scheduler(
            home,
            restarted,
            clocks,
            identity,
            epoch,
            _binding(runner),
        )
        try:
            actionable, _cursor, _progress = service._sweep_once(
                restarted, coordinator, identity, epoch, scheduler
            )
            assert actionable is False
            time.sleep(0.05)
        finally:
            scheduler.shutdown(deadline_seconds=5)

        terminal = restarted.load_run(run_id)
        assert terminal["status"] == "cancelled"
        assert run_directory.is_dir()
        assert (run_directory / "run.json").is_file()
        journal_after = (run_directory / "events.jsonl").read_bytes()
        assert journal_after.startswith(journal_before)
        assert b'"event_type":"run_cancelled"' in journal_after
        assert _claims(restarted, run_id) == 0
        assert _run_events(restarted, run_id, "run_promoted") == []
        assert runner.requests == []
        assert _ProviderTrap.requests == 0
    finally:
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()


def test_authenticated_run_later_defers_real_wake_then_executes_checkpoint_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_authenticated_run_later_defers_real_wake_then_executes_checkpoint_once(
            tmp_path, monkeypatch, client
        )


def test_authenticated_cancel_before_fire_retains_evidence_and_never_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _production_client(monkeypatch) as client:
        _run_authenticated_cancel_before_fire_retains_evidence_and_never_executes(
            tmp_path, monkeypatch, client
        )


def test_restart_and_index_reconstruction_preserve_schedule_and_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_restart_and_index_reconstruction_across_lifespans(tmp_path, monkeypatch)


def _run_restart_and_index_reconstruction_across_lifespans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "lifespan-rebuild"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _provider_trap()
    _write_runtime_config(home, base_url)
    showcase_module._clear_verified_showcase_cache_for_tests()
    clocks = _Clocks()
    store = RunStore(home, lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks, "task-7-lifespan-rebuild")
    runner = _RecordingAIRunner()
    sweep_results = []
    try:
        with _production_client(monkeypatch) as first_client:
            run_id, schedule_at = _schedule_showcase(
                first_client,
                clocks,
                workflow="scheduling",
                key="task-7-lifespan-rebuild",
            )
            worker = first_client.get(
                "/api/plugins/workflow/runs/not-a-real-run/events"
            )
            assert worker.status_code == 404
        assert not any(
            thread.name.startswith("workflow-store-io")
            for thread in threading.enumerate()
        )

        store.database.unlink()
        rebuilt = RunStore(home, lease_clock=clocks.lease_sample)
        assert rebuilt.load_run(run_id)["run_metadata"]["schedule_at"] == schedule_at
        with _production_client(monkeypatch) as second_client:
            detail = second_client.get(f"/api/plugins/workflow/runs/{run_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["schedule_at"] == schedule_at
            restarted, restarted_identity, restarted_epoch = _leader(
                rebuilt, clocks, "task-7-rebuilt-leader"
            )
            clocks.wall = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
            service, scheduler = _service_and_scheduler(
                home,
                rebuilt,
                clocks,
                restarted_identity,
                restarted_epoch,
                _binding(runner),
            )
            try:
                sweep_results.append(
                    service._sweep_once(
                        rebuilt,
                        restarted,
                        restarted_identity,
                        restarted_epoch,
                        scheduler,
                    )
                )
                terminal = _wait_for_terminal(rebuilt, run_id)
                first_response = second_client.get(
                    f"/api/plugins/workflow/runs/{run_id}"
                )
                assert first_response.status_code == 200, first_response.text
                first_projection = first_response.json()
                first_promoted = _run_events(rebuilt, run_id, "run_promoted")
                first_node_succeeded = _run_events(
                    rebuilt, run_id, "node_succeeded"
                )
                first_run_succeeded = _run_events(rebuilt, run_id, "run_succeeded")

                sweep_results.append(
                    service._sweep_once(
                        rebuilt,
                        restarted,
                        restarted_identity,
                        restarted_epoch,
                        scheduler,
                    )
                )
                second_response = second_client.get(
                    f"/api/plugins/workflow/runs/{run_id}"
                )
                assert second_response.status_code == 200, second_response.text
            finally:
                scheduler.shutdown(deadline_seconds=5)
        assert terminal["status"] == "succeeded", terminal.get("last_error")
        assert len(sweep_results) == 2
        assert sweep_results[0][0] is True
        assert sweep_results[1][0] is False
        assert terminal["run_metadata"]["schedule_at"] == schedule_at
        assert second_response.json() == first_projection
        assert second_response.json()["state_version"] == terminal["state_version"]
        assert len(first_promoted) == 1
        assert _run_events(rebuilt, run_id, "run_promoted") == first_promoted
        assert len(first_node_succeeded) == 1
        assert _run_events(rebuilt, run_id, "node_succeeded") == first_node_succeeded
        assert len(first_run_succeeded) == 1
        assert _run_events(rebuilt, run_id, "run_succeeded") == first_run_succeeded
        assert runner.requests == []
        assert _ProviderTrap.requests == 0
    finally:
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()


@pytest.mark.parametrize(
    ("context", "expected_status", "expected_requests"),
    [
        pytest.param("capable", "succeeded", 2, id="capable"),
        pytest.param("incapable-runner", "failed", 0, id="incapable-runner"),
        pytest.param("codex-app-server", "failed", 0, id="codex-app-server"),
    ],
)
def test_scheduled_ai_revalidates_actual_runner_and_runtime_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    expected_status: str,
    expected_requests: int,
) -> None:
    with _production_client(monkeypatch) as client:
        _run_scheduled_ai_revalidates_actual_runner_and_runtime_before_claim(
            tmp_path, monkeypatch, context, expected_status, expected_requests, client
        )


def _run_scheduled_ai_revalidates_actual_runner_and_runtime_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    expected_status: str,
    expected_requests: int,
    client: TestClient,
) -> None:
    home = tmp_path / context
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_OFFLINE", "1")
    provider, base_url = _provider_trap()
    _write_runtime_config(home, base_url)
    showcase_module._clear_verified_showcase_cache_for_tests()
    clocks = _Clocks()
    store = RunStore(home, lease_clock=clocks.lease_sample)
    coordinator, identity, epoch = _leader(store, clocks, f"task-4-7-{context}")
    runner = _RecordingAIRunner()

    try:
        run_id, schedule_at = _schedule_showcase(
            client,
            clocks,
            workflow="ai-extensions",
            key=f"task-4-7-ai-{context}",
        )
        if context == "incapable-runner":
            fire_binding = _binding(runner, runner_capable=False)
        elif context == "codex-app-server":
            fire_binding = _binding(runner, api_mode="codex_app_server")
        else:
            fire_binding = _binding(runner)
        clocks.wall = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
        service, scheduler = _service_and_scheduler(
            home,
            store,
            clocks,
            identity,
            epoch,
            fire_binding,
        )
        try:
            service._sweep_once(store, coordinator, identity, epoch, scheduler)
            terminal = _wait_for_terminal(store, run_id)
        finally:
            scheduler.shutdown(deadline_seconds=5)

        assert terminal["status"] == expected_status
        assert len(runner.requests) == expected_requests
        assert _ProviderTrap.requests == 0
        if expected_status == "failed":
            assert terminal["last_error"]["code"] == "schedule_revalidation_failed"
            assert _claims(store, run_id) == 0
            assert _run_events(store, run_id, "run_promoted") == []
        else:
            assert len(_run_events(store, run_id, "run_promoted")) == 1
    finally:
        provider.shutdown()
        provider.server_close()
        showcase_module._clear_verified_showcase_cache_for_tests()
