from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path

import pytest
import yaml

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    read_language_snapshot,
    supports_phase4_semantics,
    supports_phase5_semantics,
    verify_language_snapshot,
)
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.provider_authority import (
    ProviderAuthorityEnvironment,
    resolve_workflow_provider_authority,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    build_risk_summary,
)


def _compile_version(path: Path, version: int):
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=version,
    )


def _compile_v4(path: Path):
    return _compile_version(path, 4)


def _phase5_authority(package):
    model_config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        }
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return resolve_workflow_provider_authority(
        package,
        model_config=model_config,
        default_runtime=runtime,
        environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=False,
        ),
    )


class _CountedAgentRunner:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=self.responses.pop(0),
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
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


class _ShutdownAgentRunner(_CountedAgentRunner):
    def __init__(self, response: str, *, status: str) -> None:
        super().__init__(response)
        self.status = status
        self.scheduler: RunScheduler | None = None

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        assert self.scheduler is not None
        self.scheduler._shutdown.set()
        return PluginAgentRunResult(
            final_response=self.responses.pop(0),
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status=self.status,
            pending_interaction=None,
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": 1,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


def _admit_compilation(
    tmp_path: Path,
    compilation,
    *,
    key: str,
) -> tuple[RunStore, str]:
    store = RunStore(tmp_path / "home")
    if supports_phase4_semantics(
        WorkflowLanguageProfile(compilation.package.language.effective_profile),
        compilation.package.language.normalizer_version,
    ):
        phase5 = supports_phase5_semantics(
            WorkflowLanguageProfile(compilation.package.language.effective_profile),
            compilation.package.language.normalizer_version,
        )
        prepared = store.prepare_run_snapshot(
            compilation.package,
            compilation=compilation,
            trusted_package_digest=WorkflowPackageDigest(
                compilation.composite_digest,
                compilation.covered_relative_paths,
            ),
            provider_authority=(
                _phase5_authority(compilation.package) if phase5 else None
            ),
        )
    else:
        prepared = store.prepare_run_snapshot(compilation.package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return store, admitted.run_id


def _write_loop(
    tmp_path: Path,
    loop: dict[str, object],
    *,
    workflow_interactive: bool = False,
) -> Path:
    path = tmp_path / "workflows" / "loop.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "name": "loop",
        "description": "Loop contract fixture",
        "nodes": [{"id": "refine", "loop": loop}],
    }
    if workflow_interactive:
        document["interactive"] = True
    path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("version", "explicit_signal", "expected_status"),
    [
        (3, None, "succeeded"),
        (4, None, "paused"),
        (4, True, "succeeded"),
        (5, None, "paused"),
        (5, True, "succeeded"),
    ],
)
def test_counted_provider_signal_outcomes_follow_sealed_loop_semantics(
    tmp_path: Path,
    version: int,
    explicit_signal: bool | None,
    expected_status: str,
) -> None:
    loop: dict[str, object] = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "interactive": True,
        "gate_message": "Accept this result or provide feedback",
    }
    if explicit_signal is not None:
        loop["signal_completes"] = explicit_signal
    workflow = _write_loop(
        tmp_path / f"v{version}-{explicit_signal}",
        loop,
        workflow_interactive=True,
    )
    if version < 4:
        workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
            "language_compatibility: archon-2026-07\n",
            encoding="utf-8",
        )
    compilation = _compile_version(workflow, version)
    store, run_id = _admit_compilation(
        tmp_path / f"admitted-v{version}-{explicit_signal}",
        compilation,
        key=f"signal-v{version}-{explicit_signal}",
    )
    runner = _CountedAgentRunner("draft <promise>DONE</promise>")

    outcome = RunScheduler(store, agent_runner=runner).advance(run_id)

    diagnostic = outcome["nodes"]["refine"]["attempts"][-1]
    detail = (
        diagnostic.get("error_code"),
        diagnostic.get("error_message"),
        diagnostic.get("metadata"),
    )
    assert outcome["status"] == expected_status, detail
    assert len(runner.requests) == 1, detail
    artifact = outcome["artifacts"][-1]
    cleaned = store.run_directory(run_id).joinpath(
        artifact["relative_path"]
    ).read_bytes()
    assert cleaned == b"draft"
    assert artifact["sha256"] == hashlib.sha256(cleaned).hexdigest()
    if expected_status == "paused":
        pending = outcome["nodes"]["refine"]["pending_interaction"]
        assert pending["type"] == "loop_signal_confirmation"
        assert pending["result_artifact"] == artifact["relative_path"]
        assert pending["result_sha256"] == artifact["sha256"]
        store.approve_run(
            run_id,
            expected_state_version=outcome["state_version"],
            interaction_id=pending["interaction_id"],
        )
        assert len(runner.requests) == 1
        assert store.get_run_status(run_id)["status"] == "succeeded"


@pytest.mark.parametrize(
    ("version", "signal_completes", "expected_status", "expected_output"),
    [
        (3, None, "succeeded", b"draft\nDONE"),
        (4, True, "succeeded", b"draft"),
        (4, None, "paused", b"draft"),
        (5, True, "succeeded", b"draft"),
        (5, None, "paused", b"draft"),
    ],
)
def test_plain_signal_cleanup_follows_sealed_phase4_semantics(
    tmp_path: Path,
    version: int,
    signal_completes: bool | None,
    expected_status: str,
    expected_output: bytes,
) -> None:
    loop: dict[str, object] = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "interactive": True,
        "gate_message": "Accept or refine",
    }
    if signal_completes is not None:
        loop["signal_completes"] = signal_completes
    workflow = _write_loop(
        tmp_path / f"plain-v{version}-{signal_completes}",
        loop,
        workflow_interactive=True,
    )
    if version < 4:
        workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
            "language_compatibility: archon-2026-07\n",
            encoding="utf-8",
        )
    compilation = _compile_version(workflow, version)
    store, run_id = _admit_compilation(
        tmp_path / f"admitted-plain-v{version}-{signal_completes}",
        compilation,
        key=f"plain-v{version}-{signal_completes}",
    )

    outcome = RunScheduler(
        store,
        agent_runner=_CountedAgentRunner("draft\nDONE"),
    ).advance(run_id)

    diagnostic = outcome["nodes"]["refine"]["attempts"][-1]
    detail = (
        diagnostic.get("error_code"),
        diagnostic.get("error_message"),
        diagnostic.get("metadata"),
    )
    assert outcome["status"] == expected_status, detail
    assert outcome["artifacts"], detail
    artifact = outcome["artifacts"][-1]
    output = (
        store.run_directory(run_id) / artifact["relative_path"]
    ).read_bytes()
    assert output == expected_output
    assert artifact["size_bytes"] == len(expected_output)
    assert artifact["sha256"] == hashlib.sha256(expected_output).hexdigest()


def test_v4_loop_executes_only_its_authenticated_command_body(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    workflow = _write_loop(
        source_root,
        {
            "command": "refine",
            "until": "DONE",
            "max_iterations": 1,
        },
    )
    command = source_root / "commands" / "refine.md"
    command.parent.mkdir()
    command.write_text(
        "---\ndescription: Refine safely\n---\nUse the sealed instruction.\n",
        encoding="utf-8",
    )
    compilation = _compile_v4(workflow)
    store, run_id = _admit_compilation(
        tmp_path / "admitted-command",
        compilation,
        key="sealed-command-runtime",
    )
    command.write_text("LIVE MUTATION MUST NOT RUN\n", encoding="utf-8")
    runner = _CountedAgentRunner("<promise>DONE</promise>")

    outcome = RunScheduler(store, agent_runner=runner).advance(run_id)

    assert outcome["status"] == "succeeded"
    assert len(runner.requests) == 1
    assert runner.requests[0].prompt == "Use the sealed instruction.\n"


def test_v4_final_interactive_iteration_fails_without_unusable_input(
    tmp_path: Path,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / "final-iteration",
            {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": 1,
                "interactive": True,
                "gate_message": "No next iteration exists",
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / "admitted-final",
        compilation,
        key="final-hard-limit",
    )
    runner = _CountedAgentRunner("not complete")

    outcome = RunScheduler(store, agent_runner=runner).advance(run_id)

    assert outcome["status"] == "failed", {
        "node": outcome["nodes"]["refine"],
        "last_error": outcome.get("last_error"),
    }
    assert outcome["last_error"]["code"] == "loop_max_iterations"
    assert outcome["nodes"]["refine"].get("pending_interaction") is None
    assert len(runner.requests) == 1


@pytest.mark.parametrize(
    ("first_response", "pending_type"),
    [
        ("draft", "loop_input"),
        ("draft <promise>DONE</promise>", "loop_signal_confirmation"),
    ],
)
def test_v4_feedback_resume_authenticates_prior_output_and_consumes_input_once(
    tmp_path: Path,
    first_response: str,
    pending_type: str,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / pending_type,
            {
                "prompt": (
                    "Previous=<$LOOP_PREV_OUTPUT> "
                    "Feedback=<$LOOP_USER_INPUT>"
                ),
                "until": "DONE",
                "max_iterations": 3,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / f"admitted-{pending_type}",
        compilation,
        key=f"feedback-{pending_type}",
    )
    first_runner = _CountedAgentRunner(first_response)
    paused = RunScheduler(store, agent_runner=first_runner).advance(run_id)
    pending = paused["nodes"]["refine"]["pending_interaction"]
    assert pending["type"] == pending_type

    store.provide_loop_input(
        run_id,
        "tighten the evidence",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    second_runner = _CountedAgentRunner("final <promise>DONE</promise>")
    restarted = RunStore(store.hermes_home)
    confirmed = RunScheduler(restarted, agent_runner=second_runner).advance(run_id)

    assert second_runner.requests[0].prompt == (
        "Previous=<draft> Feedback=<tighten the evidence>"
    )
    assert len(first_runner.requests) == len(second_runner.requests) == 1
    assert confirmed["nodes"]["refine"].get("loop_user_input_artifact") is None
    second_pending = confirmed["nodes"]["refine"]["pending_interaction"]
    assert second_pending["type"] == "loop_signal_confirmation"
    restarted.approve_run(
        run_id,
        expected_state_version=confirmed["state_version"],
        interaction_id=second_pending["interaction_id"],
    )
    assert len(second_runner.requests) == 1


def test_v4_shutdown_after_iteration_record_cannot_poison_a_new_attempt(
    tmp_path: Path,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / "shutdown-boundary",
            {
                "prompt": "Previous=<$LOOP_PREV_OUTPUT>",
                "until": "DONE",
                "max_iterations": 3,
                "signal_completes": True,
            },
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / "admitted-shutdown-boundary",
        compilation,
        key="shutdown-boundary",
    )
    interrupted_runner = _ShutdownAgentRunner("draft", status="completed")
    scheduler = RunScheduler(store, agent_runner=interrupted_runner)
    interrupted_runner.scheduler = scheduler

    interrupted = scheduler.advance(run_id)

    assert interrupted["status"] == "interrupted"
    loop_state = interrupted["nodes"]["refine"]["loop_state"]
    assert loop_state["iteration"] == 1
    assert "_pending_loop_decision" not in loop_state
    assert len(interrupted_runner.requests) == 1


def test_v4_recovery_supersedes_a_decision_bound_to_a_bygone_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / "bygone-decision",
            {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": 3,
                "signal_completes": True,
            },
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / "admitted-bygone-decision",
        compilation,
        key="bygone-decision",
    )
    recorded_claims = []
    original_record = store.record_loop_iteration

    def lose_process_after_record(claim, **kwargs):
        original_record(claim, **kwargs)
        recorded_claims.append(claim)
        raise SystemExit("lost after recording the decision")

    monkeypatch.setattr(store, "record_loop_iteration", lose_process_after_record)
    with pytest.raises(SystemExit, match="lost after recording"):
        RunScheduler(
            store,
            agent_runner=_CountedAgentRunner("draft"),
        ).advance(run_id)

    original_claim = recorded_claims[0]
    store.complete_node(
        original_claim,
        status="interrupted",
        error_code="shutdown",
    )
    store.resume_run(run_id, always_run_nodes=set())
    replacement = store.claim_node(run_id, "refine", "replacement-worker")
    assert replacement is not None
    store.mark_node_started(replacement)

    assert store.recorded_loop_decision(run_id) is None
    projection = store.load_run(run_id)
    loop_state = projection["nodes"]["refine"]["loop_state"]
    assert "_pending_loop_decision" not in loop_state
    replacement_claim = projection["nodes"]["refine"]["claim"]
    expired_at = datetime.fromisoformat(
        replacement_claim["lease_expires_at"]
    ) + timedelta(seconds=1)
    expired_monotonic = (
        float(replacement_claim["heartbeat_monotonic"])
        + float(replacement_claim["lease_seconds"])
        + 1
    )
    assert store.expire_stale_claims(
        run_id,
        now=expired_at,
        monotonic_now=expired_monotonic,
    ) == ("refine",)
    store.resume_run(run_id, always_run_nodes=set())
    resumed_runner = _CountedAgentRunner("final <promise>DONE</promise>")
    monkeypatch.setattr(store, "record_loop_iteration", original_record)

    resumed = RunScheduler(store, agent_runner=resumed_runner).advance(run_id)

    assert resumed["status"] == "succeeded"
    assert len(resumed_runner.requests) == 1
    assert resumed_runner.requests[0].prompt == "Refine"


def test_v4_interrupted_feedback_iteration_preserves_committed_progress(
    tmp_path: Path,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / "feedback-interrupted",
            {
                "prompt": (
                    "Previous=<$LOOP_PREV_OUTPUT> "
                    "Feedback=<$LOOP_USER_INPUT>"
                ),
                "until": "DONE",
                "max_iterations": 5,
                "interactive": True,
                "gate_message": "Accept or refine",
                "signal_completes": True,
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / "admitted-feedback-interrupted",
        compilation,
        key="feedback-interrupted",
    )
    paused = RunScheduler(
        store,
        agent_runner=_CountedAgentRunner("draft"),
    ).advance(run_id)
    pending = paused["nodes"]["refine"]["pending_interaction"]
    store.provide_loop_input(
        run_id,
        "tighten the evidence",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    interrupted_runner = _ShutdownAgentRunner("discarded", status="cancelled")
    scheduler = RunScheduler(store, agent_runner=interrupted_runner)
    interrupted_runner.scheduler = scheduler

    interrupted = scheduler.advance(run_id)

    node = interrupted["nodes"]["refine"]
    assert interrupted["status"] == "interrupted"
    assert node["loop_state"]["iteration"] == 1
    assert node.get("loop_user_input_artifact") is None

    store.resume_run(run_id, always_run_nodes=set())
    resumed_runner = _CountedAgentRunner("final <promise>DONE</promise>")
    resumed = RunScheduler(store, agent_runner=resumed_runner).advance(run_id)

    assert resumed["status"] == "succeeded"
    assert resumed["nodes"]["refine"]["loop_state"]["iteration"] == 2
    assert resumed_runner.requests[0].prompt == "Previous=<draft> Feedback=<>"


@pytest.mark.parametrize(
    ("first_response", "pending_type"),
    [
        ("draft", "loop_input"),
        ("draft <promise>DONE</promise>", "loop_signal_confirmation"),
    ],
)
@pytest.mark.parametrize("mutation", ["tamper", "symlink"])
def test_v4_feedback_artifact_is_authenticated_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch,
    first_response: str,
    pending_type: str,
    mutation: str,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / f"{pending_type}-{mutation}",
            {
                "prompt": "Feedback=<$LOOP_USER_INPUT>",
                "until": "DONE",
                "max_iterations": 3,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / f"admitted-{pending_type}-{mutation}",
        compilation,
        key=f"feedback-auth-{pending_type}-{mutation}",
    )
    paused = RunScheduler(
        store,
        agent_runner=_CountedAgentRunner(first_response),
    ).advance(run_id)
    pending = paused["nodes"]["refine"]["pending_interaction"]
    assert pending["type"] == pending_type
    ready = store.provide_loop_input(
        run_id,
        "trusted feedback",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    relative = ready["nodes"]["refine"]["loop_user_input_artifact"]
    input_path = store.run_directory(run_id) / relative
    runner = _CountedAgentRunner("provider must not run")
    scheduler = RunScheduler(store, agent_runner=runner)
    if mutation == "tamper":
        input_path.write_text("mutated feedback", encoding="utf-8")
    else:
        alias = input_path.with_name("same-content-alias.txt")
        alias.write_bytes(input_path.read_bytes())
        original_variables = scheduler._variables

        def swap_after_snapshot_verification(*args, **kwargs):
            variables = original_variables(*args, **kwargs)
            input_path.unlink()
            input_path.symlink_to(alias)
            return variables

        monkeypatch.setattr(scheduler, "_variables", swap_after_snapshot_verification)

    outcome = scheduler.advance(run_id)

    assert outcome["status"] == "failed"
    assert outcome["last_error"]["code"] == "loop_input_invalid"
    assert runner.requests == []


@pytest.mark.parametrize(
    ("response", "expected_status", "completed_by"),
    [
        ("signal <promise>DONE</promise>", "paused", "signal_pending_confirmation"),
        ("no signal", "succeeded", "until_bash"),
    ],
)
def test_v4_signal_precedes_until_bash_and_plain_success_needs_no_confirmation(
    tmp_path: Path,
    response: str,
    expected_status: str,
    completed_by: str,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / completed_by,
            {
                "prompt": "Refine",
                "until": "DONE",
                "until_bash": "exit 0",
                "max_iterations": 2,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / f"admitted-{completed_by}",
        compilation,
        key=f"until-order-{completed_by}",
    )
    runner = _CountedAgentRunner(response)

    outcome = RunScheduler(store, agent_runner=runner).advance(run_id)

    assert outcome["status"] == expected_status
    node = outcome["nodes"]["refine"]
    assert node["loop_state"]["completed_by"] == completed_by
    if expected_status == "paused":
        assert node["pending_interaction"]["type"] == "loop_signal_confirmation"
    else:
        assert node.get("pending_interaction") is None


def test_v4_loop_evidence_projects_only_bounded_state_machine_facts(
    tmp_path: Path,
) -> None:
    compilation = _compile_v4(
        _write_loop(
            tmp_path / "evidence",
            {
                "prompt": "PROMPT_BODY_MUST_NOT_LEAK",
                "until": "DONE",
                "max_iterations": 3,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
            workflow_interactive=True,
        )
    )
    store, run_id = _admit_compilation(
        tmp_path / "admitted-evidence",
        compilation,
        key="loop-evidence",
    )
    first = RunScheduler(
        store,
        agent_runner=_CountedAgentRunner(
            "RESULT_BODY_MUST_NOT_LEAK <promise>DONE</promise>"
        ),
    ).advance(run_id)
    first_pending = first["nodes"]["refine"]["pending_interaction"]
    store.provide_loop_input(
        run_id,
        "FEEDBACK_BODY_MUST_NOT_LEAK",
        expected_state_version=first["state_version"],
        interaction_id=first_pending["interaction_id"],
        actor="operator-1",
        channel="desktop",
    )
    second = RunScheduler(
        store,
        agent_runner=_CountedAgentRunner("accepted <promise>DONE</promise>"),
    ).advance(run_id)
    second_pending = second["nodes"]["refine"]["pending_interaction"]
    store.approve_run(
        run_id,
        expected_state_version=second["state_version"],
        interaction_id=second_pending["interaction_id"],
        actor="operator-1",
        channel="desktop",
    )

    evidence = EvidenceReader(store).query(run_id, kind="interactions", limit=20)

    event_types = [item.get("event_type") for item in evidence["items"]]
    assert event_types == [
        "loop_signal_confirmation_required",
        "loop_feedback_provided",
        "loop_signal_confirmation_required",
        "loop_signal_accepted",
    ]
    assert "operator-1" in str(evidence)
    assert "desktop" in str(evidence)
    assert "PROMPT_BODY_MUST_NOT_LEAK" not in str(evidence)
    assert "RESULT_BODY_MUST_NOT_LEAK" not in str(evidence)
    assert "FEEDBACK_BODY_MUST_NOT_LEAK" not in str(evidence)
    assert str(tmp_path) not in str(evidence)


def test_v4_inline_loop_projects_noninteractive_default_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflows" / "inline-loop.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "name": "inline-loop",
                "description": "Inline loop semantics",
                "nodes": [
                    {
                        "id": "refine",
                        "loop": {
                            "prompt": "Refine the result",
                            "until": "DONE",
                            "max_iterations": 2,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    compilation = _compile_v4(path)

    assert compilation.package.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "inline",
        "command_binding": None,
        "effective_interactive": False,
        "signal_completes": True,
    }


def test_v4_command_loop_projects_only_its_collision_proof_sealed_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "package" / "workflows" / "command-loop.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "name": "command-loop",
                "description": "Command loop semantics",
                "nodes": [
                    {
                        "id": "refine",
                        "loop": {
                            "command": "refine",
                            "until": "DONE",
                            "max_iterations": 2,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    commands = path.parent.parent / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Refine a result\n---\nRefine the result\n",
        encoding="utf-8",
    )

    compilation = _compile_v4(path)

    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    assert compilation.package.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert "Refine the result" not in str(
        compilation.package.language.node_semantics
    )
    risk = build_risk_summary(
        compilation.package,
        assess_compatibility(compilation.package),
        compilation=compilation,
    )
    assert "Refine the result" not in str(risk.to_dict())


@pytest.mark.parametrize(
    ("workflow_interactive", "loop_interactive", "authored_signal", "expected"),
    [
        (False, False, None, True),
        (True, True, None, False),
        (True, True, True, True),
    ],
)
def test_v4_loop_signal_defaults_follow_effective_interactivity(
    tmp_path: Path,
    workflow_interactive: bool,
    loop_interactive: bool,
    authored_signal: bool | None,
    expected: bool,
) -> None:
    loop: dict[str, object] = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "interactive": loop_interactive,
    }
    if loop_interactive:
        loop["gate_message"] = "Accept or refine"
    if authored_signal is not None:
        loop["signal_completes"] = authored_signal

    compilation = _compile_v4(
        _write_loop(
            tmp_path,
            loop,
            workflow_interactive=workflow_interactive,
        )
    )

    semantics = compilation.package.language.node_semantics["refine"]["loop"]
    assert semantics["effective_interactive"] is (
        workflow_interactive and loop_interactive
    )
    assert semantics["signal_completes"] is expected


def test_v4_rejects_signal_confirmation_without_an_effective_operator_path(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "signal_completes": False,
        },
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_v4(path)

    assert raised.value.issues[0].code == (
        "archon_loop_signal_confirmation_unavailable"
    )


def test_v4_command_loop_snapshot_reloads_only_the_authenticated_binding(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "source"
    path = _write_loop(
        package_root,
        {
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
    )
    commands = package_root / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Refine\n---\nUse only sealed bytes.\n",
        encoding="utf-8",
    )
    compilation = _compile_v4(path)
    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    store = RunStore(tmp_path / "home")
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
            workflow_name="loop",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="sealed-loop-command",
            concurrency_key="loop",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    commands.joinpath("refine.md").write_text(
        "MUTATED SOURCE MUST NOT WIN\n",
        encoding="utf-8",
    )

    reloaded = RunScheduler(RunStore(tmp_path / "home"))._load_run_package(
        admitted.run_id
    )

    assert reloaded.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert reloaded.definition.nodes[0].value["command"] == binding.snapshot_path
    assert (
        reloaded.root.joinpath(binding.snapshot_path).read_text(encoding="utf-8")
        == "---\ndescription: Refine\n---\nUse only sealed bytes.\n"
    )

    sealed_command = store.run_directory(admitted.run_id) / binding.snapshot_path
    sealed_command.write_text("TAMPERED SEALED COMMAND\n", encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        RunScheduler(store)._load_run_package(admitted.run_id)

    assert raised.value.code == "workflow_snapshot_integrity_mismatch"


def test_v4_rejects_an_authenticated_loop_command_with_an_empty_body(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Empty command\n---\n   \n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_v4(path)

    assert raised.value.issues[0].code == "invalid_command_resource"


def test_v4_rejects_missing_and_invalid_loop_command_resources(
    tmp_path: Path,
) -> None:
    missing_path = _write_loop(
        tmp_path / "missing",
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    with pytest.raises(WorkflowValidationError) as missing:
        _compile_v4(missing_path)
    assert missing.value.issues[0].code == "missing_command"

    invalid_path = _write_loop(
        tmp_path / "invalid",
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "invalid" / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: unterminated\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowValidationError) as invalid:
        _compile_v4(invalid_path)
    assert invalid.value.issues[0].code == "invalid_command_resource"


@pytest.mark.parametrize(
    "loop",
    [
        {
            "prompt": "Refine",
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
        {"until": "DONE", "max_iterations": 2},
        {"prompt": " ", "until": "DONE", "max_iterations": 2},
        {"command": " ", "until": "DONE", "max_iterations": 2},
        {"prompt": "Refine", "until": " ", "max_iterations": 2},
        {"prompt": "Refine", "until": "DONE", "max_iterations": 0},
        {"prompt": "Refine", "until": "DONE", "max_iterations": 101},
        {"prompt": "Refine", "until": "DONE", "max_iterations": True},
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": "yes",
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "signal_completes": 1,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "unknown": True,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": True,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": True,
            "gate_message": " ",
        },
    ],
)
def test_v4_loop_schema_rejects_every_invalid_exact_shape(
    tmp_path: Path,
    loop: dict[str, object],
) -> None:
    with pytest.raises(WorkflowValidationError):
        _compile_v4(_write_loop(tmp_path, loop, workflow_interactive=True))


@pytest.mark.parametrize("maximum", [1, 100])
def test_v4_accepts_exact_loop_iteration_bounds_and_inherited_fields(
    tmp_path: Path,
    maximum: int,
) -> None:
    until_bash = "test -s $LOOP_OUTPUT_FILE"
    compilation = _compile_v4(
        _write_loop(
            tmp_path,
            {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": maximum,
                "fresh_context": True,
                "until_bash": until_bash,
            },
        )
    )

    loop = compilation.package.definition.nodes[0].value
    assert loop["max_iterations"] == maximum
    assert loop["fresh_context"] is True
    assert loop["until_bash"] == until_bash


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize(
    "phase4_field",
    [
        {"command": "refine"},
        {"prompt": "Refine", "signal_completes": True},
    ],
)
def test_v1_through_v3_reject_phase4_loop_source_fields(
    tmp_path: Path,
    version: int,
    phase4_field: dict[str, object],
) -> None:
    loop = {"until": "DONE", "max_iterations": 2, **phase4_field}

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_version(_write_loop(tmp_path, loop), version)

    assert raised.value.issues[0].code == "unknown_loop_field"


def test_v3_inline_loop_source_and_snapshot_shape_remain_unchanged(
    tmp_path: Path,
) -> None:
    authored_loop = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "fresh_context": True,
    }
    package = _compile_version(_write_loop(tmp_path, authored_loop), 3).package

    assert dict(package.definition.nodes[0].value) == authored_loop
    assert "refine" not in package.language.node_semantics
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    assert read_language_snapshot(snapshot).to_dict() == snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        lambda loop: loop.update({"extra": True}),
        lambda loop: loop.pop("signal_completes"),
        lambda loop: loop.update({"command_binding": "commands/refine.md"}),
        lambda loop: loop.update({"prompt_source": "inline"}),
        lambda loop: loop.update(
            {"effective_interactive": False, "signal_completes": False}
        ),
    ],
)
def test_v4_language_snapshot_rejects_malformed_loop_semantics(
    tmp_path: Path,
    mutation,
) -> None:
    path = _write_loop(
        tmp_path,
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text("Refine safely.\n", encoding="utf-8")
    compilation = _compile_v4(path)
    snapshot = make_language_snapshot(
        compilation.package, compilation.composite_digest
    ).to_dict()
    corrupted = deepcopy(snapshot)
    mutation(corrupted["node_semantics"]["refine"]["loop"])

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        read_language_snapshot(corrupted)

    assert raised.value.code == "workflow_language_snapshot_invalid"


def test_v4_language_snapshot_rejects_a_different_well_formed_binding(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text("Refine safely.\n", encoding="utf-8")
    compilation = _compile_v4(path)
    snapshot = make_language_snapshot(
        compilation.package, compilation.composite_digest
    ).to_dict()
    snapshot["node_semantics"]["refine"]["loop"]["command_binding"] = (
        "packages/" + "a" * 64 + "/" + "b" * 64 + "/commands/refine.md"
    )
    parsed = read_language_snapshot(snapshot)
    assert parsed is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        verify_language_snapshot(
            compilation.package,
            compilation.composite_digest,
            parsed,
        )

    assert raised.value.code == "workflow_language_snapshot_mismatch"


def test_v4_loop_semantics_cannot_attach_to_the_legacy_profile(
    tmp_path: Path,
) -> None:
    package = _compile_version(
        _write_loop(
            tmp_path,
            {"prompt": "Refine", "until": "DONE", "max_iterations": 2},
        ),
        3,
    ).package
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    snapshot["effective_profile"] = "hermes-legacy"
    snapshot["normalizer_version"] = 4
    snapshot["node_semantics"] = {
        "refine": {
            "loop": {
                "prompt_source": "inline",
                "command_binding": None,
                "effective_interactive": False,
                "signal_completes": True,
            }
        }
    }

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        read_language_snapshot(snapshot)

    assert raised.value.code == "workflow_normalizer_version_unsupported"


def test_included_loop_command_uses_child_origin_and_rewritten_sealed_body(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "root" / "workflows" / "root.yaml"
    root_path.parent.mkdir(parents=True)
    root_path.write_text(
        yaml.safe_dump(
            {
                "name": "root",
                "description": "Root without interactive authority",
                "nodes": [{"id": "child", "include": "child"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    child_path = tmp_path / "child" / "workflows" / "child.yaml"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(
        yaml.safe_dump(
            {
                "name": "child",
                "description": "Child command loop",
                "interactive": True,
                "nodes": [
                    {"id": "prepare", "prompt": "Prepare"},
                    {
                        "id": "refine",
                        "loop": {
                            "command": "refine",
                            "until": "DONE",
                            "max_iterations": 2,
                            "interactive": True,
                            "gate_message": "Accept",
                        },
                        "depends_on": ["prepare"],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    child_commands = child_path.parent.parent / "commands"
    child_commands.mkdir()
    child_commands.joinpath("refine.md").write_text(
        "Refine $prepare.output without exposing this body.\n",
        encoding="utf-8",
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root = parse_workflow_source_bytes(
        root_path,
        workflow_bytes=root_path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    child = parse_workflow_source_bytes(
        child_path,
        workflow_bytes=child_path.read_bytes(),
        sidecar_bytes=None,
        source="profile",
        precedence=2,
    )

    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    semantics = compilation.package.language.node_semantics["child__refine"]["loop"]
    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    assert binding.package_key == "profile:child"
    assert semantics == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert compilation.sealed_files[binding.snapshot_path] == (
        b"Refine $child__prepare.output without exposing this body.\n"
    )
    assert "without exposing this body" not in str(
        compilation.package.language.node_semantics
    )
