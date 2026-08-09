from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent.plugin_agent import PluginAgentRunResult
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hermes_cli.plugin_invocation import PluginInvocationContext
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.gateway_command import workflow_gateway_command
from plugins.workflow.sanitize import public_run_projection
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow


_CANARY = "PROMPT_COMMAND_PROVIDER_PAYLOAD_FEEDBACK_PATH_CANARY_20260808"
_NOW = datetime(2026, 8, 8, 20, tzinfo=timezone.utc)
_SAFE_ERROR = {"code": "workflow_operation_failed", "message": "Workflow operation failed."}


def _api_module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    name = "workflow_public_run_evidence_contract_api"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _app(router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.local_admin_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _start(store: RunStore, package, key: str) -> str:
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


class _OSErrorRunner:
    def run(self, _request, **_kwargs):
        raise OSError(f"provider transport {_CANARY} /private/tmp/{_CANARY}")


class _AuditFailureRunner:
    def run(self, request, **_kwargs):
        return PluginAgentRunResult(
            final_response="",
            session_id="",
            provider=request.provider or "test-provider",
            model=request.model or "test-model",
            status="failed",
            pending_interaction=None,
            usage={},
            audit={
                "failure_kind": "provider_timeout",
                "provider_attempts": 1,
                "error": _CANARY,
                "provider_payload": {"prompt": _CANARY},
                "temporary_path": f"/private/tmp/{_CANARY}",
            },
        )


@pytest.mark.parametrize("runner", (_OSErrorRunner(), _AuditFailureRunner()))
def test_real_agent_failure_scheduler_persistence_is_private_but_public_surfaces_are_closed(
    tmp_path,
    workflow_writer,
    runner,
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"failure-{type(runner).__name__}",
            nodes=[{"id": "work", "prompt": f"private prompt {_CANARY}"}],
        )
    )
    store = RunStore(tmp_path / "home")
    run_id = _start(store, package, f"failure-{type(runner).__name__}")

    RunScheduler(store, agent_runner=runner).advance(run_id)

    private = store.load_run(run_id)
    assert _CANARY in json.dumps(private, sort_keys=True)
    status = public_run_projection(store.get_run_status(run_id), now=_NOW)
    attempts = EvidenceReader(store).query(run_id, kind="attempts")
    timeline = EvidenceReader(store).query(run_id, kind="timeline")
    rendered = json.dumps(
        {"status": status, "attempts": attempts, "timeline": timeline},
        sort_keys=True,
        allow_nan=False,
    )

    assert _CANARY not in rendered
    assert "/private/tmp" not in rendered
    assert set(status).issubset({
        "schema_version",
        "action",
        "run_id",
        "workflow",
        "workflow_version",
        "status",
        "status_authoritative",
        "health",
        "blocking_reason",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "state_version",
        "event_sequence",
        "execution_mode",
        "trigger",
        "provenance",
        "definition_digest",
        "provider_resolution_sha256",
        "nodes",
        "artifacts",
        "warnings",
        "coordinator",
        "progress",
        "attempts",
        "current_nodes",
        "previous_node",
        "next_retry_at",
        "pending_interaction",
        "next_actions",
        "queue_position",
        "blocked_by_run_id",
        "schedule_at",
        "presentation_state",
        "archived_at",
        "archive_version",
        "restored_to_history",
        "admission_disposition",
        "last_semantic_progress_at",
        "last_error",
    })
    assert attempts["items"]
    assert attempts["items"][-1]["error"] == _SAFE_ERROR
    assert all("payload" not in item for item in timeline["items"])


def test_real_approval_rework_and_interaction_journal_omit_rendered_user_text(
    tmp_path,
    workflow_writer,
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        name="private-interactions",
        nodes=[{
            "id": "review",
            "approval": {
                "message": f"rendered approval {_CANARY}",
                "on_reject": {
                    "prompt": f"private rework prompt {_CANARY}",
                    "max_attempts": 2,
                },
            },
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    run_id = _start(store, package, "private-interactions")
    paused = RunScheduler(store).advance(run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    assert _CANARY in pending["message"]

    pending_public = public_run_projection(store.get_run_status(run_id), now=_NOW)
    assert pending_public["pending_interaction"] == {
        "type": "workflow_approval",
        "interaction_id": pending["interaction_id"],
        "node_id": "review",
    }
    store.reject_run(
        run_id,
        reason=f"approval rework reason {_CANARY}",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    store.append_event(
        run_id,
        "loop_feedback_provided",
        {
            "interaction_id": "loop-feedback-1",
            "comment": f"loop comment {_CANARY}",
            "feedback": _CANARY,
            "provider_payload": {"prompt": _CANARY},
        },
        node_id="review",
    )
    private = store.load_run(run_id)
    assert private["nodes"]["review"]["approval_rework"]["reason"].endswith(
        _CANARY
    )

    public = public_run_projection(store.get_run_status(run_id), now=_NOW)
    events = store.events_after(run_id)
    interactions = EvidenceReader(store).query(run_id, kind="interactions")
    rendered = json.dumps(
        {"run": public, "events": events, "interactions": interactions},
        sort_keys=True,
        allow_nan=False,
    )

    assert _CANARY not in rendered
    assert "message" not in json.dumps(public.get("nodes", {}), sort_keys=True)
    assert "approval_rework" not in public["nodes"]["review"]
    assert public["nodes"]["review"]["approval_rework_attempts"] == 0
    assert all("payload" not in event for event in events["events"])
    assert all(item["item_type"] == "interaction" for item in interactions["items"])


def test_direct_sql_and_malformed_legacy_rows_recover_to_bounded_public_projections(
    tmp_path,
    workflow_writer,
) -> None:
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="legacy-public-row")
    )
    store = RunStore(tmp_path / "home")
    run_id = _start(store, package, "legacy-public-row")
    directory = store.run_directory(run_id)
    with store._connect() as connection:
        connection.execute(
            "UPDATE runs SET workflow_name=?, status=?, updated_at=? WHERE run_id=?",
            (_CANARY, _CANARY, _CANARY, run_id),
        )
        connection.execute(
            "INSERT INTO cleanup_history (timestamp, token_digest, run_id, "
            "source_path, quarantine_path, files, bytes, outcome, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _CANARY,
                _CANARY,
                run_id,
                f"/private/source/{_CANARY}",
                f"/private/quarantine/{_CANARY}",
                1,
                2,
                _CANARY,
                json.dumps({"prompt": _CANARY, "path": f"/tmp/{_CANARY}"}),
            ),
        )
    (directory / "run.json").unlink()
    (directory / "events.jsonl").write_text("{malformed legacy journal}\n")

    listed = store.list_runs(view="all", now=_NOW)
    public = public_run_projection(listed[0], now=_NOW)
    cleanup = EvidenceReader(store)._items(
        run_id,
        {"nodes": {}},
        kind="cleanup",
        operator_scope=None,
    )
    rendered = json.dumps({"run": public, "cleanup": cleanup}, allow_nan=False)

    assert _CANARY not in rendered
    assert public["status"] == "recovery_pending"
    assert public["health"] == "storage_degraded"
    assert public["last_error"] == _SAFE_ERROR
    assert cleanup == [{
        "item_type": "cleanup",
        "sequence": cleanup[0]["sequence"],
        "files": 1,
        "bytes": 2,
        "outcome": "cleanup_projection_invalid",
    }]


def test_real_rest_run_event_and_evidence_routes_enforce_closed_models(
    tmp_path,
    monkeypatch,
    workflow_writer,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="rest-closed-public")
    )
    store = RunStore(home)
    run_id = _start(store, package, "rest-closed-public")
    store.append_event(
        run_id,
        "diagnostic",
        {"audit": {"error": _CANARY}, "provider_payload": _CANARY},
    )
    module = _api_module()
    client = TestClient(_app(module.router))

    detail = client.get(f"/api/plugins/workflow/runs/{run_id}")
    events = client.get(f"/api/plugins/workflow/runs/{run_id}/events")
    evidence = client.get(
        f"/api/plugins/workflow/runs/{run_id}/evidence",
        params={"kind": "timeline"},
    )

    assert detail.status_code == events.status_code == evidence.status_code == 200
    assert _CANARY not in detail.text + events.text + evidence.text
    module.WorkflowRunProjection.model_validate(detail.json())
    module.WorkflowEventPageProjection.model_validate(events.json())
    module.WorkflowEvidencePageProjection.model_validate(evidence.json())
    invalid = detail.json()
    invalid["provider_payload"] = _CANARY
    with pytest.raises(ValidationError):
        module.WorkflowRunProjection.model_validate(invalid)


def test_gateway_failure_is_one_fixed_public_error(monkeypatch, tmp_path) -> None:
    class _FailingStore:
        def provide_loop_input(self, *_args, **_kwargs):
            raise OSError(f"private gateway path /tmp/{_CANARY}")

    monkeypatch.setattr("plugins.workflow.cli._runtime_config", lambda *_args: {})
    monkeypatch.setattr("plugins.workflow.cli._store", lambda *_args, **_kwargs: _FailingStore())
    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:test:user",
        operator_scope="gateway:test:chat:user",
        assurance="verified_adapter",
        return_route_capability="opaque-return-route",
    )

    response = workflow_gateway_command(
        "provide-input run-1 --interaction-id interaction-1 "
        "--expected-version 1 --value bounded",
        invocation,
        hermes_home=tmp_path,
    )

    assert json.loads(response) == {
        "error": "workflow_operation_failed",
        "message": "Workflow operation failed.",
        "ok": False,
    }
    assert _CANARY not in response
