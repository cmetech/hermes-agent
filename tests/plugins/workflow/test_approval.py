from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import pytest

from agent.plugin_agent import PluginAgentRunResult
import plugins.workflow.executors.approval as approval_executor_module
import plugins.workflow.store as store_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import register_cli
from plugins.workflow.executors.approval import ApprovalExecutor
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.models import (
    ApprovalDecision,
    DeadlineBudget,
    RunExecutionLimits,
    WorkflowNode,
    WorkflowLanguageProfile,
    freeze_value,
)
from plugins.workflow.provider_authority import (
    WorkflowProviderAuthority,
    WorkflowResolvedProviderRoute,
)
from plugins.workflow.output_resolution import (
    ArchonOutputIntegrityError,
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
)
from plugins.workflow.resources import VariableContext
from plugins.workflow.scheduler import RunScheduler
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowPackageDigest


def _start(store, package, *, key="approval"):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    return parser


def test_v4_approval_decision_loads_recorded_sealed_normalizer_and_definition(
    tmp_path,
    workflow_writer,
) -> None:
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    workflow = workflow_writer(
        tmp_path / "v4-approval" / "workflows",
        name="v4-approval",
        nodes=[
            {"id": "review", "approval": {"message": "Approve?"}},
            {
                "id": "refine",
                "loop": {
                    "prompt": "Refine",
                    "until": "DONE",
                    "max_iterations": 1,
                    "signal_completes": True,
                },
                "depends_on": ["review"],
            },
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    store = RunStore(tmp_path / "v4-home")
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="v4-approval",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="v4-sealed-approval",
            concurrency_key="v4-approval",
        ),
        immutable_snapshot=prepared,
    )
    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    workflow.unlink()

    decision = RunStore(tmp_path / "v4-home").approve_run(
        admitted.run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    assert decision.outcome == "applied"
    current = store.load_run(admitted.run_id)
    assert current["language"]["normalizer_version"] == 4
    assert current["nodes"]["review"]["state"] == "succeeded"


@pytest.mark.parametrize("normalizer_version", [1, 2, 3])
def test_v1_through_v3_approval_decisions_keep_their_recorded_normalizer(
    tmp_path,
    workflow_writer,
    normalizer_version: int,
) -> None:
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    workflow = workflow_writer(
        tmp_path / f"approval-v{normalizer_version}" / "workflows",
        name=f"approval-v{normalizer_version}",
        nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    sidecar_path = workflow.with_name(f"{workflow.stem}.hermes.yaml")
    sidecar_path.write_bytes(sidecar)
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    package = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=normalizer_version,
    ).package
    store = RunStore(tmp_path / f"approval-home-v{normalizer_version}")
    admitted = _start(store, package, key=f"approval-v{normalizer_version}")
    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    workflow.unlink()
    sidecar_path.unlink()

    decision = RunStore(store.hermes_home).approve_run(
        admitted.run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    assert decision.outcome == "applied"
    current = store.load_run(admitted.run_id)
    assert current["language"]["normalizer_version"] == normalizer_version
    assert current["nodes"]["review"]["state"] == "succeeded"


def test_v3_approval_message_rechecks_direct_dependency_before_pause(tmp_path) -> None:
    node = WorkflowNode(
        id="review",
        node_type="approval",
        value=freeze_value({"message": "Approve $producer.output.answer?"}),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({
            "allowed_tools": [],
            "denied_tools": ["Bash"],
        }),
    )
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        variable_context=VariableContext(
            normalizer_version=3,
            node_outputs={
                "producer": ResolvedNodeOutput(
                    canonical_bytes=b'{"answer":"ready"}',
                    value={"answer": "ready"},
                    text='{"answer":"ready"}',
                    media_type="application/json",
                    sha256="1" * 64,
                    node_id="producer",
                    attempt_id="attempt-winner",
                    publication_id="a" * 32,
                    schema_fingerprint="3" * 64,
                    canonicalization_version=1,
                )
            },
        ),
    )

    with pytest.raises(WorkflowOutputReferenceError) as exc:
        ApprovalExecutor().execute(context)

    assert exc.value.code == "output_reference_not_declared_dependency"


def test_approval_survives_restart_captures_trimmed_output_and_continues(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="durable-gate",
        nodes=[
            {
                "id": "review",
                "approval": {
                    "message": "Approve the proposed plan?",
                    "capture_response": True,
                },
                "output_type": "ApprovalDecision",
            },
            {
                "id": "finish",
                "bash": "printf '%s' '$review.output'",
                "depends_on": ["review"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, package)

    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    assert paused["status"] == "paused"
    assert pending["type"] == "workflow_approval"

    restarted = RunStore(home)
    decision = restarted.approve_run(
        admitted.run_id,
        comment="  looks good  ",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
        actor="operator-1",
        channel="cli",
    )
    assert decision == ApprovalDecision(
        run_id=admitted.run_id,
        node_id="review",
        decision="approved",
        outcome="applied",
        interaction_id=pending["interaction_id"],
        state_version=decision.state_version,
    )

    decided = restarted.load_run(admitted.run_id)
    output = next(
        artifact
        for artifact in decided["artifacts"]
        if artifact["node_id"] == "review"
        and artifact.get("publication_id") is not None
    )
    assert output["publication_id"]
    assert output["media_type"] == "text/markdown; charset=utf-8"
    bundle = (
        restarted.run_directory(admitted.run_id)
        / "publications"
        / output["publication_id"]
    )
    assert (bundle / "content.md").read_bytes() == b"looks good"
    decision_event = next(
        event
        for event in restarted.tail_events(admitted.run_id)
        if event["event_type"] == "interaction_approved"
    )
    assert decision_event["payload"]["artifact"] == output

    completed = RunScheduler(RunStore(home)).advance(admitted.run_id)
    assert completed["status"] == "succeeded"
    completed_output = next(
        artifact
        for artifact in completed["artifacts"]
        if artifact["node_id"] == "review"
        and artifact.get("publication_id") is not None
    )
    assert (
        restarted.run_directory(admitted.run_id) / completed_output["relative_path"]
    ).read_text() == "looks good"
    assert completed_output == output

    duplicate = restarted.approve_run(
        admitted.run_id,
        comment="ignored",
        interaction_id=pending["interaction_id"],
    )
    assert duplicate.outcome == "already_decided"
    assert duplicate.decision == "approved"


def test_typed_approval_retries_after_publication_fails_post_source_write(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    workflow = workflow_writer(
        tmp_path / "retry-package",
        name="retry-durable-gate",
        nodes=[
            {
                "id": "review",
                "approval": {
                    "message": "Approve the proposed plan?",
                    "capture_response": True,
                },
                "output_type": "ApprovalDecision",
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "retry-home")
    admitted = _start(store, package, key="approval-publication-retry")
    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    original_fsync = store_module._fsync_publication_directory

    def fail_staging_fsync(descriptor, *, boundary):
        if boundary == "staging":
            raise OSError("staging fsync failed")
        return original_fsync(descriptor, boundary=boundary)

    monkeypatch.setattr(
        store_module,
        "_fsync_publication_directory",
        fail_staging_fsync,
    )

    with pytest.raises(OSError, match="staging fsync failed"):
        store.approve_run(
            admitted.run_id,
            comment="  looks good  ",
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
            actor="operator-1",
            channel="cli",
        )

    still_paused = store.load_run(admitted.run_id)
    assert still_paused["status"] == "paused"
    assert still_paused["nodes"]["review"]["state"] == "paused"
    assert still_paused["nodes"]["review"]["pending_interaction"] == pending
    assert not any(
        artifact.get("publication_id") is not None
        for artifact in still_paused["artifacts"]
    )
    assert not any(
        event["event_type"] == "interaction_approved"
        for event in store.tail_events(admitted.run_id)
    )
    source_paths = list(
        store.run_directory(admitted.run_id).glob("nodes/*/*/output.md")
    )
    assert len(source_paths) == 1

    monkeypatch.setattr(
        store_module,
        "_fsync_publication_directory",
        original_fsync,
    )
    source_paths[0].write_bytes(b"looks evil")
    with pytest.raises(
        ArchonOutputIntegrityError,
        match="typed approval output source identity changed",
    ):
        store.approve_run(
            admitted.run_id,
            comment="  looks good  ",
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
            actor="operator-1",
            channel="cli",
        )
    source_paths[0].write_bytes(b"looks good")

    decision = store.approve_run(
        admitted.run_id,
        comment="  looks good  ",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
        actor="operator-1",
        channel="cli",
    )
    assert decision.outcome == "applied"

    decided = store.load_run(admitted.run_id)
    publications = [
        artifact
        for artifact in decided["artifacts"]
        if artifact.get("publication_id") is not None
    ]
    assert len(publications) == 1
    output = publications[0]
    assert output["node_id"] == "review"
    assert output["attempt_id"] == paused["nodes"]["review"]["attempts"][-1][
        "attempt_id"
    ]
    assert output["media_type"] == "text/markdown; charset=utf-8"
    assert output["size_bytes"] == len(b"looks good")
    assert output["sha256"] == hashlib.sha256(b"looks good").hexdigest()
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / output["publication_id"]
    )
    assert (bundle / "content.md").read_bytes() == b"looks good"
    events = [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "interaction_approved"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["artifact"] == output


class ReworkRunner:
    starts_request_mcp = True

    def __init__(self):
        self.requests = []
        self.launch_kwargs = []

    def run(self, request, **kwargs):
        from agent.plugin_agent import _validate_request

        _validate_request(request)
        self.requests.append(request)
        self.launch_kwargs.append(kwargs)
        return PluginAgentRunResult(
            final_response="revised plan",
            session_id="rework-session",
            provider="fake",
            model="fake",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": 1,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


def test_v5_approval_rework_uses_the_sealed_route_and_shared_attempt_authority(
    tmp_path,
) -> None:
    runner = ReworkRunner()
    route = WorkflowResolvedProviderRoute(
        route_id="review:primary",
        node_id="review",
        role="primary",
        inline_agent_id=None,
        reference_kind="configured_alias",
        requested_reference_sha256="1" * 64,
        provider="sealed-provider",
        model="sealed-model",
        api_mode="chat_completions",
        route_fingerprint="2" * 64,
        endpoint_sha256="d" * 64,
        registration_provenance_digest="3" * 64,
        provider_options={},
        config_scope="profile",
        base_url_trust_class="provider_default",
    )
    fallback_route = WorkflowResolvedProviderRoute(
        route_id="review:fallback",
        node_id="review",
        role="fallback",
        inline_agent_id=None,
        reference_kind="configured_alias",
        requested_reference_sha256="6" * 64,
        provider="sealed-fallback-provider",
        model="sealed-fallback-model",
        api_mode="chat_completions",
        route_fingerprint="7" * 64,
        endpoint_sha256="9" * 64,
        registration_provenance_digest="8" * 64,
        provider_options={},
        config_scope="profile",
        base_url_trust_class="provider_default",
    )
    authority = WorkflowProviderAuthority(
        config_fingerprint="4" * 64,
        routes={
            route.route_id: route,
            fallback_route.route_id: fallback_route,
        },
        obligations=(),
        warnings=(),
        authority_digest="5" * 64,
    )
    node = WorkflowNode(
        id="review",
        node_type="approval",
        value=freeze_value({
            "message": "Approve?",
            "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
        }),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({
            "allowed_tools": [],
            "denied_tools": ["Bash"],
        }),
    )
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        workflow_options=freeze_value({
            "provider": "authored-provider-must-not-run",
            "model": "@mutable-alias",
            "fallbackModel": "@mutable-fallback-must-not-run",
        }),
        variable_context=VariableContext(workflow_id="run-1"),
        node_state=freeze_value({
            "approval_rework": {"reason": "missing evidence"},
        }),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=5,
        sealed_provider_route=route,
        sealed_provider_authority=authority,
        intended_authority_digest="a" * 64,
        spawn_intent=lambda _nonce: True,
        spawn_failed=lambda _nonce, _code: True,
        provider_dispatch=lambda _nonce: True,
        provider_start_delivered=lambda _nonce: True,
        provider_execute_received=lambda _nonce: True,
        provider_execute_release=lambda _nonce: True,
    )

    result = ApprovalExecutor(runner).execute(context)

    assert result.status == "paused"
    request = runner.requests[0]
    assert request.provider == "sealed-provider"
    assert request.model == "sealed-model"
    assert request.intended_authority_digest == "a" * 64
    assert request.expected_runtime_identity == {
        "provider": "sealed-provider",
        "model": "sealed-model",
        "api_mode": "chat_completions",
        "base_url_trust_class": "provider_default",
        "endpoint_sha256": "d" * 64,
        "registration_provenance_digest": "3" * 64,
    }
    assert request.sealed_provider_attempt_grant is True
    assert request.allowed_tools == ()
    assert request.denied_tools == ("terminal",)
    assert request.fallback_model is None
    assert request.sealed_fallback_route == {
        "provider": "sealed-fallback-provider",
        "effective_provider": "sealed-fallback-provider",
        "model": "sealed-fallback-model",
        "context_mode": "fresh",
        "expected_runtime_identity": {
            "provider": "sealed-fallback-provider",
            "model": "sealed-fallback-model",
            "api_mode": "chat_completions",
            "base_url_trust_class": "provider_default",
            "endpoint_sha256": "9" * 64,
            "registration_provenance_digest": "8" * 64,
        },
        "reasoning_config": {},
        "request_overrides": {},
        "structured_output": None,
    }
    assert result.metadata["intended_authority_digest"] == "a" * 64
    assert result.metadata["model_visible_prefix_digest"] == "9" * 64
    assert set(runner.launch_kwargs[0]) == {
        "is_cancelled",
        "spawn_intent",
        "spawn_failed",
        "provider_dispatch",
        "provider_start_delivered",
        "provider_execute_received",
        "provider_execute_release",
    }

    pre_cancelled_runner = ReworkRunner()
    pre_cancelled = ApprovalExecutor(pre_cancelled_runner).execute(
        replace(context, is_cancelled=lambda: True)
    )
    assert pre_cancelled.status == "cancelled"
    assert pre_cancelled_runner.requests == []

    class CancelledRunner:
        def run(self, _request, **_kwargs):
            return PluginAgentRunResult(
                final_response="",
                session_id="cancelled-rework",
                provider="fake",
                model="fake",
                status="cancelled",
                pending_interaction=None,
                usage={},
                audit={},
            )

    cancelled = ApprovalExecutor(CancelledRunner()).execute(context)
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "cancelled"


def test_v3_approval_rejection_prompt_renders_strict_field_before_provider(
    tmp_path,
) -> None:
    runner = ReworkRunner()
    node = WorkflowNode(
        id="review",
        node_type="approval",
        value=freeze_value({
            "message": "Approve?",
            "on_reject": {
                "prompt": "Revise $producer.output.answer: $REJECTION_REASON"
            },
        }),
        depends_on=("producer",),
        source_index=0,
        source_line=1,
        options=freeze_value({
            "allowed_tools": [],
            "denied_tools": ["Bash"],
            "systemPrompt": "legacy prompt must remain ignored",
        }),
    )
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        workflow_options=freeze_value({"provider": "fake", "model": "fake"}),
        variable_context=VariableContext(
            normalizer_version=3,
            node_outputs={
                "producer": ResolvedNodeOutput(
                    canonical_bytes=b'{"answer":"plan"}',
                    value={"answer": "plan"},
                    text='{"answer":"plan"}',
                    media_type="application/json",
                    sha256="1" * 64,
                    node_id="producer",
                    attempt_id="attempt-winner",
                    publication_id="a" * 32,
                    schema_fingerprint="3" * 64,
                    canonicalization_version=1,
                )
            },
        ),
        node_state=freeze_value({
            "approval_rework": {"reason": "missing evidence"},
        }),
    )

    result = ApprovalExecutor(runner).execute(context)

    assert result.status == "paused"
    assert runner.requests[0].prompt == "Revise plan: missing evidence"
    assert runner.requests[0].allowed_tools is None
    assert runner.requests[0].denied_tools == ()
    assert runner.requests[0].ephemeral_system_prompt is None
    assert set(runner.launch_kwargs[0]) == {"is_cancelled"}


def test_approval_rework_request_maps_every_run_execution_limit_exactly(tmp_path):
    runner = ReworkRunner()
    limits = RunExecutionLimits(
        max_parallel_nodes=2,
        max_total_workers=3,
        ai_idle_timeout_seconds=11,
        ai_wall_timeout_seconds=37,
        provider_request_timeout_seconds=7,
        combined_retries=4,
        subprocess_timeout_seconds=19,
        process_tree_rss_bytes=128 * 1024 * 1024,
        process_tree_cpu_seconds=13,
        max_descendants=3,
        cooperative_shutdown_seconds=1.5,
        term_grace_seconds=2.5,
        kill_reap_grace_seconds=3.5,
    )
    node = WorkflowNode(
        id="review",
        node_type="approval",
        value=freeze_value({
            "message": "Approve?",
            "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
        }),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    budget = DeadlineBudget.create(
        now=10,
        wall_seconds=limits.ai_wall_timeout_seconds,
        idle_seconds=limits.ai_idle_timeout_seconds,
        provider_seconds=limits.provider_request_timeout_seconds,
    )
    context = NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        workflow_options=freeze_value({"provider": "fake", "model": "fake"}),
        variable_context=VariableContext(workflow_id="run-1"),
        node_state=freeze_value({
            "approval_rework": {"reason": "missing evidence"},
        }),
        execution_limits=limits,
        deadline_budget=budget,
        monotonic=lambda: 10,
    )

    result = ApprovalExecutor(runner).execute(context)

    assert result.status == "paused"
    request = runner.requests[0]
    assert request.idle_timeout_seconds == limits.ai_idle_timeout_seconds
    assert request.wall_timeout_seconds == limits.ai_wall_timeout_seconds
    assert (
        request.provider_request_timeout_seconds
        == limits.provider_request_timeout_seconds
    )
    assert request.max_api_attempts == limits.combined_retries
    assert request.max_process_tree_rss_bytes == limits.process_tree_rss_bytes
    assert request.max_process_tree_cpu_seconds == limits.process_tree_cpu_seconds
    assert request.max_descendants == limits.max_descendants
    assert (
        request.cooperative_shutdown_seconds
        == limits.cooperative_shutdown_seconds
    )
    assert request.term_grace_seconds == limits.term_grace_seconds
    assert request.kill_reap_grace_seconds == limits.kill_reap_grace_seconds
    assert request.max_iterations == 90


def test_approval_rework_rechecks_wall_after_prompt_preparation(
    tmp_path, monkeypatch
):
    runner = ReworkRunner()
    node = WorkflowNode(
        id="review",
        node_type="approval",
        value=freeze_value({
            "message": "Approve?",
            "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
        }),
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    budget = DeadlineBudget.create(
        now=10.0,
        wall_seconds=1.0,
        idle_seconds=1.0,
        provider_seconds=1.0,
    )
    clock = {"now": 10.0}
    original_renderer = approval_executor_module.substitution_renderer

    def renderer_then_expire(*args, **kwargs):
        renderer = original_renderer(*args, **kwargs)

        class CrossingRenderer:
            def render_prompt(self, value):
                rendered = renderer.render_prompt(value)
                clock["now"] = 11.0
                return rendered

        return CrossingRenderer()

    monkeypatch.setattr(
        approval_executor_module, "substitution_renderer", renderer_then_expire
    )
    result = ApprovalExecutor(runner).execute(
        NodeExecutionContext(
            run_id="run-1",
            run_directory=run_directory,
            node=node,
            attempt_id="attempt-1",
            workflow_options=freeze_value({"provider": "fake", "model": "fake"}),
            variable_context=VariableContext(workflow_id="run-1"),
            node_state=freeze_value({
                "approval_rework": {"reason": "missing evidence"},
            }),
            deadline_budget=budget,
            sealed_attempt_timeout=True,
            monotonic=lambda: clock["now"],
        )
    )

    assert result.status == "failed"
    assert result.error_code == "provider_timeout"
    assert runner.requests == []


def test_rejection_runs_bounded_rework_with_reason_then_cancels(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="rework-gate",
        nodes=[
            {
                "id": "review",
                "approval": {
                    "message": "Approve?",
                    "on_reject": {
                        "prompt": "Revise because: $REJECTION_REASON",
                        "max_attempts": 1,
                    },
                },
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="rework")
    runner = ReworkRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first_pause = scheduler.advance(admitted.run_id)
    assert first_pause["nodes"]["review"].get("retry_consumed", 0) == 0

    rejected = store.reject_run(
        admitted.run_id,
        reason="  missing evidence  ",
        expected_state_version=first_pause["state_version"],
        interaction_id=first_pause["nodes"]["review"]["pending_interaction"][
            "interaction_id"
        ],
    )
    assert rejected.outcome == "applied"
    second_pause = scheduler.advance(admitted.run_id)
    assert second_pause["status"] == "paused"
    assert second_pause["nodes"]["review"].get("retry_consumed", 0) == 0
    assert runner.requests[0].prompt == "Revise because: missing evidence"
    assert second_pause["nodes"]["review"]["approval_rework_attempts"] == 1

    exhausted = store.reject_run(
        admitted.run_id,
        reason="still incomplete",
        expected_state_version=second_pause["state_version"],
        interaction_id=second_pause["nodes"]["review"]["pending_interaction"][
            "interaction_id"
        ],
    )
    assert exhausted.outcome == "applied"
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


def test_scheduler_uses_ai_deadline_for_approval_rework(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "bounded-rework",
        name="bounded-rework",
        nodes=[{
            "id": "review",
            "approval": {
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            },
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits:\n"
        "  ai_idle_timeout_seconds: 11\n"
        "  ai_wall_timeout_seconds: 37\n"
        "  provider_request_timeout_seconds: 7\n"
        "  subprocess_timeout_seconds: 19\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "bounded-rework-home")
    admitted = _start(store, package, key="bounded-rework")
    runner = ReworkRunner()
    now = time.monotonic()
    scheduler = RunScheduler(store, agent_runner=runner, monotonic=lambda: now)
    first_pause = scheduler.advance(admitted.run_id)
    pending = first_pause["nodes"]["review"]["pending_interaction"]
    store.reject_run(
        admitted.run_id,
        reason="missing evidence",
        expected_state_version=first_pause["state_version"],
        interaction_id=pending["interaction_id"],
    )

    scheduler.advance(admitted.run_id)

    request = runner.requests[0]
    assert request.idle_timeout_seconds == 11
    # Seeded from the real clock (line 299), so remaining_wall()'s
    # (now + 37) - now round-trip is inexact on ~13% of freshly booted CI
    # clocks. See the same guard in test_ai_e2e.py. The fixed-clock cases
    # elsewhere in this file stay exact and are deliberately left alone.
    assert request.wall_timeout_seconds == pytest.approx(37, abs=1e-9)
    assert request.provider_request_timeout_seconds == 7


@pytest.mark.parametrize(
    "reported_provider_attempts",
    [None, -1],
    ids=["missing", "negative"],
)
def test_failed_approval_rework_conservatively_accounts_provider_attempts(
    tmp_path, workflow_writer, reported_provider_attempts
) -> None:
    workflow = workflow_writer(
        tmp_path / "failed-rework",
        name="failed-rework",
        nodes=[{
            "id": "review",
            "approval": {
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            },
            "retry": {"max_attempts": 5, "delay_ms": 1000, "on_error": "all"},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 2}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "failed-rework-home")
    admitted = _start(store, package, key="failed-rework")

    class FailedReworkRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            audit = {"failure_kind": "provider_timeout"}
            if reported_provider_attempts is not None:
                audit["provider_attempts"] = reported_provider_attempts
            return PluginAgentRunResult(
                final_response="",
                session_id="failed-rework",
                provider="fake",
                model="fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit=audit,
            )

    runner = FailedReworkRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first_pause = scheduler.advance(admitted.run_id)
    pending = first_pause["nodes"]["review"]["pending_interaction"]
    store.reject_run(
        admitted.run_id,
        reason="missing evidence",
        expected_state_version=first_pause["state_version"],
        interaction_id=pending["interaction_id"],
    )

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "failed"
    assert len(runner.requests) == 1
    assert runner.requests[0].max_api_attempts == 2
    assert result["nodes"]["review"]["retry_consumed"] == 2


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (OSError("provider connection failed"), "network_error"),
        (RuntimeError("approval agent failed"), "agent_execution_failed"),
    ],
)
def test_approval_runner_exception_consumes_grant_without_refresh(
    tmp_path, workflow_writer, failure, expected_code
) -> None:
    workflow = workflow_writer(
        tmp_path / "crashed-rework",
        name="crashed-rework",
        nodes=[{
            "id": "review",
            "approval": {
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            },
            "retry": {"max_attempts": 5, "delay_ms": 1000, "on_error": "all"},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 2}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "crashed-rework-home")
    admitted = _start(store, package, key="crashed-rework")

    class CrashedReworkRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            raise failure

    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    runner = CrashedReworkRunner()
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    first_pause = scheduler.advance(admitted.run_id)
    pending = first_pause["nodes"]["review"]["pending_interaction"]
    store.reject_run(
        admitted.run_id,
        reason="missing evidence",
        expected_state_version=first_pause["state_version"],
        interaction_id=pending["interaction_id"],
    )

    failed = scheduler.advance(admitted.run_id)
    now += timedelta(seconds=1)
    replay = scheduler.advance(admitted.run_id)

    assert failed["status"] == "failed"
    assert replay["status"] == "failed"
    assert [request.max_api_attempts for request in runner.requests] == [2]
    assert failed["last_error"]["code"] == expected_code
    assert failed["nodes"]["review"]["retry_consumed"] == 2
    attempt = failed["nodes"]["review"]["attempts"][-1]
    assert attempt["error_code"] == expected_code
    assert attempt["metadata"]["provider_attempts"] == 1
    assert attempt["metadata"]["retry_consumed"] == 2


def test_approval_permission_error_stays_authorization_without_provider_charge(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "denied-rework",
        name="denied-rework",
        nodes=[{
            "id": "review",
            "approval": {
                "message": "Approve?",
                "on_reject": {"prompt": "Revise: $REJECTION_REASON"},
            },
            "retry": {"max_attempts": 5, "delay_ms": 1000, "on_error": "all"},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 3}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "denied-rework-home")
    admitted = _start(store, package, key="denied-rework")

    class DeniedReworkRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            raise PermissionError("workflow provider override is not authorized")

    runner = DeniedReworkRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first_pause = scheduler.advance(admitted.run_id)
    pending = first_pause["nodes"]["review"]["pending_interaction"]
    store.reject_run(
        admitted.run_id,
        reason="missing evidence",
        expected_state_version=first_pause["state_version"],
        interaction_id=pending["interaction_id"],
    )

    failed = scheduler.advance(admitted.run_id)
    replay = scheduler.advance(admitted.run_id)

    assert failed["status"] == "failed"
    assert replay["status"] == "failed"
    assert [request.max_api_attempts for request in runner.requests] == [3]
    assert failed["last_error"]["code"] == "authorization"
    assert failed["nodes"]["review"]["retry_consumed"] == 1
    attempt = failed["nodes"]["review"]["attempts"][-1]
    assert attempt["error_code"] == "authorization"
    assert "provider_attempts" not in attempt["metadata"]
    assert attempt["metadata"]["retry_consumed"] == 1


class ToolApprovalRunner:
    def __init__(self):
        self.requests = []
        self.digests = iter(("a" * 64, "b" * 64, "c" * 64))

    def run(self, request, **_kwargs):
        self.requests.append(request)
        digest = next(self.digests)
        return PluginAgentRunResult(
            final_response="",
            session_id="tool-session",
            provider="fake",
            model="fake",
            status="paused",
            pending_interaction={"kind": "approval", "action_digest": digest},
            usage={},
            audit={"provider_attempts": 1, "model_calls": 1},
        )


def test_worker_action_grant_pauses_remain_resumable_until_outcome(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="tool-gate",
        nodes=[
            {
                "id": "act",
                "prompt": "perform action",
                "retry": {"max_attempts": 1},
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="tool")
    runner = ToolApprovalRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    first = scheduler.advance(admitted.run_id)
    pending = first["nodes"]["act"]["pending_interaction"]
    assert first["nodes"]["act"].get("retry_consumed", 0) == 0

    store.approve_run(
        admitted.run_id,
        expected_state_version=first["state_version"],
        interaction_id=pending["action_digest"],
    )
    second = scheduler.advance(admitted.run_id)

    assert runner.requests[1].approved_action_digest == "a" * 64
    assert second["status"] == "paused"
    assert second["nodes"]["act"].get("retry_consumed", 0) == 0
    assert second["nodes"]["act"]["pending_interaction"]["action_digest"] == "b" * 64
    store.approve_run(
        admitted.run_id,
        expected_state_version=second["state_version"],
        interaction_id=second["nodes"]["act"]["pending_interaction"][
            "action_digest"
        ],
    )
    third = scheduler.advance(admitted.run_id)

    assert runner.requests[2].approved_action_digest == "b" * 64
    assert third["status"] == "paused"
    assert third["nodes"]["act"].get("retry_consumed", 0) == 0
    assert third["nodes"]["act"]["pending_interaction"]["action_digest"] == "c" * 64
    persisted = (store.run_directory(admitted.run_id) / "events.jsonl").read_text()
    assert "approved_action_digest" not in persisted


def test_cli_approve_and_reject_have_stable_codes_and_continue_is_opt_in(
    tmp_path, workflow_writer, capsys
):
    workdir = tmp_path / "repo"
    package = load_workflow(
        workflow_writer(
            workdir / ".hermes" / "workflows",
            name="cli-gate",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    home = tmp_path / "home"
    store = RunStore(home)
    admitted = _start(store, package, key="cli")
    paused = RunScheduler(store).advance(admitted.run_id)
    pending = paused["nodes"]["review"]["pending_interaction"]
    parser = _parser()

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "approve",
        admitted.run_id,
        "--interaction-id",
        pending["interaction_id"],
        "--expected-version",
        str(paused["state_version"]),
        "--comment",
        "ok",
        "--json",
    ])
    assert args.func(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    payload = envelope["result"]
    assert payload["outcome"] == "applied"
    assert store.load_run(admitted.run_id)["status"] == "running"

    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "reject",
        admitted.run_id,
        "--interaction-id",
        pending["interaction_id"],
        "--expected-version",
        str(store.load_run(admitted.run_id)["state_version"]),
        "--reason",
        "late",
        "--json",
    ])
    assert args.func(args) == 5
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "decision_conflict"
    assert pending["interaction_id"]
