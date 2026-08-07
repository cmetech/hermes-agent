from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowTrustStore


class _StructuredRunner:
    def __init__(
        self, response: str, *, declaration_source: str, api_mode: str
    ) -> None:
        self.response = response
        self.declaration_source = declaration_source
        self.api_mode = api_mode
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        structured = request.structured_output
        assert structured is not None
        evidence = {
            "provider_attempts": 1,
            "model_calls": 1,
            "strategy": structured.strategy.value,
            "adapter_version": structured.adapter_version,
            "schema_fingerprint": structured.schema.schema_fingerprint,
            "declaration_source": self.declaration_source,
        }
        return PluginAgentRunResult(
            final_response=self.response,
            session_id="phase-2-e2e-session",
            provider=request.provider or "phase-2-provider",
            model=request.model or "phase-2-model",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 3, "output_tokens": 2},
            audit={**evidence, "api_calls": 1, "api_mode": self.api_mode},
            structured_output=evidence,
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


def _workflow_store_io_workers() -> list[str]:
    return sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("workflow-store-io")
    )


def test_testclient_lifespan_closes_workflow_store_workers(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    assert _workflow_store_io_workers() == []
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        response = client.get("/api/plugins/workflow/runs/not-a-real-run/events")
        assert response.status_code == 404
        assert _workflow_store_io_workers()
    assert _workflow_store_io_workers() == []


def test_language_status_crosses_real_desktop_middleware_without_mutation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)

    archon = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="archon-deferred",
        filename="archon-deferred.yaml",
        nodes=[{"id": "start", "bash": "true", "timeout": 5}],
    )
    archon.with_name("archon-deferred.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    workflow_writer(
        home / "workflows",
        name="legacy-preserved",
        filename="legacy-preserved.yaml",
    )
    WorkflowTrustStore(home).trust(
        "a" * 64,
        actor="language-desktop-e2e",
        risk_digest="b" * 64,
    )

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        before_home = _tree_snapshot(home)
        before_project = _tree_snapshot(workdir)
        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200
        rows = {
            (item.get("source"), item.get("name")): item
            for item in catalog_response.json()["items"]
        }
        archon_row = rows[("project", "archon-deferred")]
        legacy_row = rows[("profile", "legacy-preserved")]

        assert archon_row["language"] == {
            "effective_profile": "archon-2026-07",
            "legacy": False,
            "normalizer_version": 5,
        }
        assert archon_row["compatibility"] == {
            "level": "portable",
            "runnable": True,
        }
        assert legacy_row["language"] == {
            "effective_profile": "hermes-legacy",
            "legacy": True,
            "normalizer_version": 2,
        }
        assert legacy_row["compatibility"] == {
            "level": "mapped",
            "runnable": True,
        }
        assert {
            key: archon_row[key]
            for key in (
                "source",
                "precedence",
                "trust_state",
                "inputs",
                "supported_inputs",
                "run_support",
            )
        } == {
            "source": "project",
            "precedence": 1,
            "trust_state": "untrusted",
            "inputs": [],
            "supported_inputs": {"supported": True, "reason": "parameterless"},
            "run_support": {"supported": True, "reason": "supported"},
        }

        detail_response = client.get(
            "/api/plugins/workflow/workflows/archon-deferred",
            params={"catalog_source": "project"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["language"]["declared_profile"] == "archon-2026-07"
        assert detail["language"]["effective_profile"] == "archon-2026-07"
        assert detail["language"]["legacy"] is False
        assert detail["language"]["normalizer_version"] == 5
        assert len(detail["language"]["normalized_definition_digest"]) == 64
        assert set(detail["language"]) == {
            "declared_profile",
            "effective_profile",
            "legacy",
            "normalizer_version",
            "normalized_definition_digest",
        }
        assert detail["compatibility"] == {
            "level": "portable",
            "runnable": True,
            "findings": [],
            "finding_count": 0,
            "findings_truncated": False,
        }
        assert "authoring_schema" not in detail
        assert _tree_snapshot(home) == before_home
        assert _tree_snapshot(workdir) == before_project


def test_archon_canonical_output_crosses_scheduler_recovery_and_desktop_api(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    output_type = "Phase2/CaseSensitive-Result"
    canonical = b'{"answer":"ready","count":2}'
    digest = hashlib.sha256(canonical).hexdigest()
    workflow = workflow_writer(
        tmp_path / "package",
        name="phase-2-structured-path",
        provider="phase-2-provider",
        model="phase-2-model",
        nodes=[
            {
                "id": "producer",
                "prompt": "Return the structured result",
                "output_type": output_type,
                "output_format": {
                    "type": "object",
                    "required": ["answer", "count"],
                    "properties": {
                        "answer": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "id": "consumer",
                "bash": 'printf "%s" "$producer.output.answer"',
                "depends_on": ["producer"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    sidecar = workflow.with_name(f"{workflow.stem}.hermes.yaml")
    package = load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=4,
    )
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="phase-2-provider",
            model="phase-2-model",
        ),
    )
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key="phase-2-structured-path",
            concurrency_key=package.definition.name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    decision = execution_context.structured_output_decisions(package)["producer"]
    runner = _StructuredRunner(
        ' { "count": 2, "answer": "ready" }\n',
        declaration_source=decision.declaration_source,
        api_mode=decision.api_mode,
    )

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "succeeded", result["nodes"]["producer"]
    assert len(runner.requests) == 1
    producer_attempt = result["nodes"]["producer"]["attempts"][-1]
    candidate = producer_attempt["metadata"]["primary_output_candidate"]
    assert candidate["sha256"] == digest
    assert candidate["size_bytes"] == len(canonical)
    assert candidate["schema_fingerprint"] == (
        package.language.structured_outputs["producer"].schema_fingerprint
    )
    producer_output = (
        store.run_directory(admitted.run_id) / candidate["attempt_relative_path"]
    )
    assert producer_output.read_bytes() == canonical
    consumer_artifact = next(
        item
        for item in result["artifacts"]
        if item.get("node_id") == "consumer" and "publication_id" not in item
    )
    assert (
        store.run_directory(admitted.run_id) / consumer_artifact["relative_path"]
    ).read_bytes() == b"ready"

    publication = next(
        item for item in result["artifacts"] if item.get("publication_id")
    )
    assert publication["sha256"] == digest
    assert publication["output_type"] == output_type
    assert publication["attempt_id"] == producer_attempt["attempt_id"]
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / publication["publication_id"]
    )
    assert (bundle / "content.json").read_bytes() == canonical

    expected_projection = store.load_run(admitted.run_id)
    (store.run_directory(admitted.run_id) / "run.json").unlink()
    for path in tuple(bundle.iterdir()):
        path.unlink()
    bundle.rmdir()
    rebuilt = store.load_run(admitted.run_id)
    assert rebuilt == expected_projection
    assert (bundle / "content.json").read_bytes() == canonical

    evidence = EvidenceReader(store).query(admitted.run_id, kind="artifacts")
    typed_items = [
        item for item in evidence["items"] if item.get("publication_id") is not None
    ]
    assert len(typed_items) == 1
    typed = typed_items[0]
    assert typed["publication_id"] == publication["publication_id"]
    assert typed["sha256"] == digest
    assert typed["schema_fingerprint"] == candidate["schema_fingerprint"]
    assert typed["attempt_id"] == producer_attempt["attempt_id"]
    assert typed["output_type"] == output_type
    assert typed["integrity_status"] == "verified"
    assert typed["recovery_status"] == "verified"
    assert canonical not in json.dumps(evidence, sort_keys=True).encode()

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    base = (
        f"/api/plugins/workflow/runs/{admitted.run_id}/artifacts/"
        f"{publication['publication_id']}"
    )
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        evidence_response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/evidence",
            params={"kind": "artifacts"},
        )
        attempt_response = client.get(
            f"/api/plugins/workflow/runs/{admitted.run_id}/evidence",
            params={"kind": "attempts"},
        )
        preview = client.get(f"{base}/preview")
        download = client.get(f"{base}/download")

    assert evidence_response.status_code == 200
    desktop_item = next(
        item
        for item in evidence_response.json()["items"]
        if item.get("publication_id") == publication["publication_id"]
    )
    assert desktop_item == typed
    assert attempt_response.status_code == 200
    desktop_attempt = next(
        item
        for item in attempt_response.json()["items"]
        if item.get("node_id") == "producer"
    )
    assert desktop_attempt == {
        "node_id": "producer",
        "attempt_id": producer_attempt["attempt_id"],
        "state": "succeeded",
        "retry": {
            "requested_retries": 2,
            "requested_total_attempts": 3,
            "effective_total_attempts": 3,
            "retry_consumed": 1,
            "remaining_attempts": 2,
            "additional_provider_attempts": 0,
            "capped": False,
        },
    }
    assert preview.status_code == 200
    assert preview.json() == {
        "publication_id": publication["publication_id"],
        "media_type": "application/json",
        "content": {"answer": "ready", "count": 2},
        "bytes_returned": len(canonical),
        "size_bytes": len(canonical),
        "truncated": False,
    }
    assert download.status_code == 200
    assert download.content == canonical
    assert download.headers["content-length"] == str(len(canonical))
