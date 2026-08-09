from __future__ import annotations

import threading

import pytest

import agent.plugin_agent as plugin_agent
from agent.plugin_agent import (
    PluginAgentRunRequest,
    PluginAgentRunResult,
    PluginAgentRunner,
)
from agent.plugin_agent_worker import _build_inline_agent_handler
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowPackageDigest
from tests.plugins.workflow.test_phase5_provider_authority import _authority


def _start(store: RunStore, compilation):
    authority = _authority(compilation.package)
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
    )
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="shared-inline-approval",
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


def test_resumed_parent_concurrent_inline_actions_consume_one_shared_grant(
    tmp_path,
    workflow_writer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = workflow_writer(
        tmp_path / "source" / "workflows",
        name="shared-inline-approval",
        filename="shared-inline-approval.yaml",
        model="@primary",
        nodes=[
            {
                "id": "act",
                "prompt": "coordinate two reviewers",
                "model": "@primary",
                "retry": {"max_attempts": 5},
                "agents": {
                    "reviewer": {
                        "description": "perform the reviewed action",
                        "prompt": "perform the action",
                        "model": "large",
                    }
                },
            }
        ],
    )
    sidecar = workflow.with_name(f"{workflow.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, compilation)
    digest = "a" * 64
    effects: list[str] = []
    child_results: list[dict] = []
    captured_descriptor: dict[str, object] = {}

    def exchange_once(payload, **_kwargs):
        parent = PluginAgentRunRequest.from_wire(payload["request"])
        descriptor = parent._provider_attempt_authority
        assert descriptor is not None
        captured_descriptor.update(descriptor)
        barrier = threading.Barrier(2)
        pauses: list[dict[str, str]] = []

        class ChildRunner:
            def run(self, child, **_child_kwargs):
                barrier.wait(timeout=5)
                consumed = plugin_agent._consume_shared_approved_action(
                    child._provider_attempt_authority,
                    child.approved_action_digest,
                )
                if consumed:
                    effects.append("outward-effect")
                    return PluginAgentRunResult(
                        final_response="done",
                        session_id="child",
                        provider=child.provider or "",
                        model=child.model or "",
                        status="completed",
                        pending_interaction=None,
                        usage={},
                        audit={},
                    )
                return PluginAgentRunResult(
                    final_response="",
                    session_id="child",
                    provider=child.provider or "",
                    model=child.model or "",
                    status="paused",
                    pending_interaction={"kind": "approval", "action_digest": digest},
                    usage={},
                    audit={},
                )

        handler = _build_inline_agent_handler(
            plugin_id="workflow",
            definitions={
                name: dict(definition)
                for name, definition in parent.inline_agents.items()
            },
            workdir=tmp_path,
            parent_request=parent,
            runner_factory=lambda _plugin_id: ChildRunner(),
            emit_progress=lambda **_payload: None,
            pause=pauses.append,
        )
        threads = [
            threading.Thread(
                target=lambda: child_results.append(
                    handler({"agent_id": "reviewer", "task": "publish"})
                )
            )
            for _index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert len(pauses) == 1
        return {
            "result": PluginAgentRunResult(
                final_response="",
                session_id="parent",
                provider=parent.provider or "",
                model=parent.model or "",
                status="paused",
                pending_interaction=pauses[0],
                usage={},
                audit={
                    "provider_attempts": 1,
                    "provider_attempts_exact": True,
                    "model_calls": 0,
                    "intended_authority_digest": parent.intended_authority_digest,
                    "model_visible_prefix_digest": "c" * 64,
                },
            ).to_wire()
        }

    class ResumedRunner:
        def __init__(self):
            self.requests: list[PluginAgentRunRequest] = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            if len(self.requests) == 1:
                return PluginAgentRunResult(
                    final_response="",
                    session_id="parent",
                    provider=request.provider or "",
                    model=request.model or "",
                    status="paused",
                    pending_interaction={"kind": "approval", "action_digest": digest},
                    usage={},
                    audit={
                        "provider_attempts": 1,
                        "provider_attempts_exact": True,
                        "model_calls": 0,
                        "intended_authority_digest": (
                            request.intended_authority_digest
                        ),
                        "model_visible_prefix_digest": "c" * 64,
                    },
                )
            return PluginAgentRunner("workflow").run(request)

    monkeypatch.setattr(plugin_agent, "_agent_override_allowed", lambda *_a, **_k: True)
    monkeypatch.setattr(plugin_agent, "_exchange_worker_once", exchange_once)
    runner = ResumedRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first = scheduler.advance(admitted.run_id)
    assert first["status"] == "paused", (
        first.get("last_error"),
        first["nodes"]["act"],
    )
    assert first["nodes"]["act"]["retry_consumed"] == 1
    pending = first["nodes"]["act"]["pending_interaction"]
    store.approve_run(
        admitted.run_id,
        expected_state_version=first["state_version"],
        interaction_id=pending["action_digest"],
    )

    second = scheduler.advance(admitted.run_id)

    assert len(runner.requests) == 2, (
        second.get("last_error"),
        second["nodes"]["act"],
    )
    assert runner.requests[1].approved_action_digest == digest
    assert effects == ["outward-effect"]
    assert sorted(result["status"] for result in child_results) == [
        "completed",
        "paused",
    ]
    assert second["status"] == "paused"
    with pytest.raises(RuntimeError, match="authority unavailable"):
        plugin_agent._consume_shared_approved_action(captured_descriptor, digest)
