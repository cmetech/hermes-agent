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
from plugins.workflow.sanitize import (
    public_artifact_projection,
    public_attempt_projection,
    public_event_projection,
    public_run_projection,
)
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


@pytest.mark.parametrize(
    "event_type",
    (
        "interaction_approved",
        "interaction_rejected",
        "loop_input_provided",
        "loop_signal_confirmation_required",
        "loop_signal_accepted",
        "loop_feedback_provided",
    ),
)
def test_interaction_event_variants_retain_only_bounded_actor_and_channel(
    event_type: str,
) -> None:
    public_event = public_event_projection({
        "sequence": 7,
        "timestamp": "2026-08-08T20:00:00+00:00",
        "run_id": "run-1",
        "event_type": event_type,
        "node_id": "review",
        "payload": {
            "actor": "operator-1",
            "channel": "desktop",
            "comment": _CANARY,
            "message": _CANARY,
            "prompt": _CANARY,
            "feedback": _CANARY,
            "provider_payload": {"response": _CANARY},
        },
    })

    item = EvidenceReader._interaction_event_item(public_event)

    assert item == {
        "item_type": "interaction",
        "sequence": 7,
        "event_type": event_type,
        "node_id": "review",
        "actor": "operator-1",
        "channel": "desktop",
    }
    assert _CANARY not in json.dumps(item, sort_keys=True)


@pytest.mark.parametrize(
    ("error_code", "expected_public_code"),
    (
        ("execution_integrity", "execution_integrity"),
        ("package_mcp_unavailable", "package_mcp_unavailable"),
        ("provider_timeout", None),
        (_CANARY, None),
        ({"provider_payload": _CANARY}, None),
    ),
)
def test_attempt_failure_code_projection_is_an_explicit_closed_allowlist(
    error_code: object,
    expected_public_code: str | None,
) -> None:
    projected = public_attempt_projection(
        "work",
        {
            "attempt_id": "attempt-1",
            "state": "failed",
            "error_code": error_code,
            "error_message": f"private failure {_CANARY}",
            "audit": {"provider_payload": _CANARY},
        },
    )

    assert projected["error"] == _SAFE_ERROR
    assert projected.get("error_code") == expected_public_code
    assert "error_message" not in projected
    assert "audit" not in projected
    assert _CANARY not in json.dumps(projected, sort_keys=True)


def test_event_truncation_projection_preserves_only_the_boolean_marker() -> None:
    projected = public_event_projection({
        "sequence": 7,
        "timestamp": "2026-08-08T20:00:00+00:00",
        "run_id": "run-1",
        "event_type": "diagnostic",
        "payload_truncated": True,
        "payload": {"provider_payload": _CANARY},
    })

    assert projected == {
        "item_type": "timeline_event",
        "sequence": 7,
        "timestamp": "2026-08-08T20:00:00+00:00",
        "run_id": "run-1",
        "event_type": "diagnostic",
        "payload_truncated": True,
    }
    assert "payload" not in projected


def test_artifact_projection_distinguishes_typed_publications_from_legacy_rows() -> None:
    typed = public_artifact_projection({
        "typed_publication_version": 2,
        "publication_id": "c" * 32,
        "node_id": "produce",
        "sha256": "a" * 64,
        "relative_path": f"private/{_CANARY}",
        "content": _CANARY,
    })
    legacy = public_artifact_projection({
        "node_id": "produce",
        "sha256": "b" * 64,
        "relative_path": f"private/{_CANARY}",
        "content": _CANARY,
    })

    assert typed == {
        "item_type": "artifact",
        "publication_id": "c" * 32,
        "node_id": "produce",
        "sha256": "a" * 64,
        "integrity_status": "verified",
        "recovery_status": "verified",
    }
    assert legacy == {
        "item_type": "artifact",
        "node_id": "produce",
        "sha256": "b" * 64,
        "integrity_status": "legacy_unverified",
        "recovery_status": "projection_recovered",
    }
    assert _CANARY not in json.dumps({"typed": typed, "legacy": legacy}, sort_keys=True)


def test_rest_projection_models_accept_only_the_closed_failure_truncation_and_artifact_shapes() -> None:
    module = _api_module()
    attempt = public_attempt_projection(
        "work",
        {
            "attempt_id": "attempt-1",
            "state": "failed",
            "error_code": "execution_integrity",
            "error_message": _CANARY,
        },
    )
    event = public_event_projection({
        "sequence": 7,
        "timestamp": "2026-08-08T20:00:00+00:00",
        "run_id": "run-1",
        "event_type": "diagnostic",
        "payload_truncated": True,
        "payload": {"provider_payload": _CANARY},
    })
    legacy_artifact = public_artifact_projection({
        "relative_path": f"private/{_CANARY}",
        "sha256": "a" * 64,
    })

    assert module.WorkflowAttemptProjection.model_validate(attempt).error_code == "execution_integrity"
    assert module.WorkflowTimelineEventProjection.model_validate(event).payload_truncated is True
    legacy_model = module.WorkflowArtifactProjection.model_validate(legacy_artifact)
    assert legacy_model.publication_id is None
    assert "publication_id" not in legacy_model.model_dump(mode="json")
    for model, value in (
        (module.WorkflowAttemptProjection, {**attempt, "provider_payload": _CANARY}),
        (module.WorkflowTimelineEventProjection, {**event, "payload": {"prompt": _CANARY}}),
        (module.WorkflowArtifactProjection, {**legacy_artifact, "relative_path": _CANARY}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(value)
    with pytest.raises(ValidationError):
        module.WorkflowArtifactProjection.model_validate({
            **legacy_artifact,
            "publication_id": "c" * 32,
        })


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
    store.append_event(
        run_id,
        "interaction_approved",
        {
            "actor": "operator-1",
            "channel": "desktop",
            "comment": _CANARY,
            "provider_payload": _CANARY,
        },
        node_id="work",
    )
    module = _api_module()
    client = TestClient(_app(module.router))

    detail = client.get(f"/api/plugins/workflow/runs/{run_id}")
    events = client.get(f"/api/plugins/workflow/runs/{run_id}/events")
    evidence = client.get(
        f"/api/plugins/workflow/runs/{run_id}/evidence",
        params={"kind": "timeline"},
    )
    interactions = client.get(
        f"/api/plugins/workflow/runs/{run_id}/evidence",
        params={"kind": "interactions"},
    )

    assert (
        detail.status_code
        == events.status_code
        == evidence.status_code
        == interactions.status_code
        == 200
    )
    rendered = detail.text + events.text + evidence.text + interactions.text
    assert _CANARY not in rendered
    for response in (events, evidence, interactions):
        assert '"actor":"operator-1"' in response.text
        assert '"channel":"desktop"' in response.text
    module.WorkflowRunProjection.model_validate(detail.json())
    module.WorkflowEventPageProjection.model_validate(events.json())
    module.WorkflowEvidencePageProjection.model_validate(evidence.json())
    module.WorkflowEvidencePageProjection.model_validate(interactions.json())
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
