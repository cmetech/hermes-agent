from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from agent.plugin_agent import PluginAgentRunResult
import plugins.workflow.showcase as showcase
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    preflight_showcase,
    run_showcase,
)
from plugins.workflow.store import RunStore


class _LegacyResponseRunner:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=self.responses.pop(0),
            session_id=f"legacy-session-{len(self.requests)}",
            provider=request.provider or "legacy-provider",
            model=request.model or "legacy-model",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            audit={},
        )


def test_ai_preflight_has_no_confirmation_gate(tmp_path) -> None:
    preflight = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert preflight["requires_confirmation"] is False
    assert preflight["confirmation_kind"] is None
    assert preflight["confirmation_token"] is None
    serialized = json.dumps(preflight, sort_keys=True)
    assert "consent_required" not in serialized
    assert "ai_confirmation_required" not in serialized


def test_ai_preflight_lists_bounded_extensions_without_connecting(tmp_path) -> None:
    result = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert result["requires_ai"] is True
    assert result["requested_skills"] == ["ascii-art"]
    assert result["local_mcp_servers"] == ["mcp/echo.yaml"]
    assert result["inline_agent_limit"] == 1
    assert result["wall_seconds"] <= 600


@pytest.mark.parametrize("legacy_confirmation_token", [None, "legacy-ai-token"])
def test_cli_run_rejects_ai_showcase_before_execution_side_effects(
    tmp_path, monkeypatch, legacy_confirmation_token
) -> None:
    side_effects: list[str] = []

    def forbidden(name):
        def record(*_args, **_kwargs):
            side_effects.append(name)
            raise AssertionError(f"unexpected {name} side effect")

        return record

    monkeypatch.setattr(showcase, "_store", forbidden("durable store"))
    monkeypatch.setattr(showcase, "_scheduler", forbidden("scheduler"))
    monkeypatch.setattr(showcase, "_advance_until_wait", forbidden("runner"))
    monkeypatch.setattr(showcase, "_stage_fixture", forbidden("staging"))
    monkeypatch.setattr(showcase, "create_job", forbidden("cron"))
    monkeypatch.setattr("subprocess.Popen", forbidden("MCP/provider process"))
    existing_home_entries = set(tmp_path.iterdir())

    with pytest.raises(
        ShowcaseCatalogError,
        match="not runnable under ordinary compatibility policy",
    ):
        run_showcase(
            "ai-extensions",
            hermes_home=tmp_path,
            confirmation_token=legacy_confirmation_token,
        )

    assert side_effects == []
    assert set(tmp_path.iterdir()) == existing_home_entries


def test_legacy_structured_output_keeps_post_hoc_retry_and_output_behavior(
    tmp_path, workflow_writer
) -> None:
    raw_valid = ' { "count": 2, "answer": "ready" }\n'
    workflow = workflow_writer(
        tmp_path / "legacy-package",
        name="legacy-structured-parity",
        provider="legacy-provider",
        model="legacy-model",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return a result",
                "output_type": "Legacy/CaseSensitive-Result",
                "output_format": {
                    "type": "object",
                    "required": ["answer", "count"],
                    "properties": {
                        "answer": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                },
                "retry": {
                    "max_attempts": 2,
                    "delay_ms": 1000,
                    "on_error": "all",
                },
            },
            {
                "id": "consumer",
                "bash": 'printf "%s" "$producer.output.answer"',
                "depends_on": ["producer"],
            },
        ],
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "legacy-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-structured-parity",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    runner = _LegacyResponseRunner("not one JSON value", raw_valid)
    scheduler = RunScheduler(store, agent_runner=runner)

    first = scheduler.advance(admitted.run_id)

    assert first["status"] == "waiting_retry"
    attempts = first["nodes"]["producer"]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["error_code"] == "structured_output_invalid"
    assert runner.requests[0].structured_output is None
    assert store.wake_due_retries(
        admitted.run_id,
        now=datetime.now(timezone.utc) + timedelta(days=1),
    ) == ("producer",)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded", result
    assert len(runner.requests) == 2
    assert all(request.structured_output is None for request in runner.requests)
    attempts = result["nodes"]["producer"]["attempts"]
    assert [attempt["state"] for attempt in attempts] == ["failed", "succeeded"]
    producer_artifact = next(
        item for item in result["artifacts"] if item.get("node_id") == "producer"
    )
    assert producer_artifact["relative_path"].endswith("/output.json")
    assert (
        store.run_directory(admitted.run_id) / producer_artifact["relative_path"]
    ).read_text(encoding="utf-8") == raw_valid
    consumer_artifact = next(
        item for item in result["artifacts"] if item.get("node_id") == "consumer"
    )
    assert (
        store.run_directory(admitted.run_id) / consumer_artifact["relative_path"]
    ).read_bytes() == b"ready"
    assert all("publication_id" not in item for item in result["artifacts"])
