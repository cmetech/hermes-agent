from __future__ import annotations

import time

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from tests.plugins.workflow.test_ai_e2e import RecordingRunner, _admit_phase5_run
from tools.managed_process import ProcessIdentity


class _PausingRunner:
    def __init__(self, *, model_calls: int = 1, on_run=None) -> None:
        self.requests = []
        self.model_calls = model_calls
        self.on_run = on_run

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        if self.on_run is not None:
            self.on_run()
        action_digest = f"{len(self.requests):064x}"
        return PluginAgentRunResult(
            final_response="",
            session_id="phase5-continuation-session",
            provider=request.provider or "fake",
            model=request.model or "fake",
            status="paused",
            pending_interaction={
                "kind": "approval",
                "action_digest": action_digest,
            },
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": self.model_calls,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


class _CancellingAfterProviderRunner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="",
            session_id="phase5-cancelled-session",
            provider=request.provider or "fake",
            model=request.model or "fake",
            status="cancelled",
            pending_interaction=None,
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": 1,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


def test_phase5_paused_provider_call_durably_consumes_exact_authority(
    tmp_path,
    workflow_writer,
) -> None:
    """Dropping the paused-result charge must recreate the sealed grant."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-paused-authority",
        nodes=[{
            "id": "ask",
            "prompt": "perform one approved action",
            "retry": {"max_attempts": 1},
        }],
    )
    runner = _PausingRunner()

    paused = RunScheduler(store, agent_runner=runner).advance(run_id)

    node = paused["nodes"]["ask"]
    attempt = node["attempts"][-1]
    assert paused["status"] == "paused"
    assert node["retry_consumed"] == 1
    assert attempt["metadata"]["retry_consumed"] == 1
    assert attempt["metadata"]["remaining_attempts"] == 1
    assert attempt["metadata"]["provider_attempts_exact"] is True
    assert "intended_authority_digest" not in attempt["metadata"]
    assert "model_visible_prefix_digest" not in attempt["metadata"]
    assert "shared_context_compatibility_digest" not in attempt["metadata"]
    assert "cache_fingerprint" not in attempt["metadata"]


def test_phase5_approval_restart_cannot_exceed_durable_attempt_remainder(
    tmp_path,
    workflow_writer,
) -> None:
    """Resetting retry_consumed on an approval restart must overrun the cap."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-approval-restart-authority",
        nodes=[{
            "id": "ask",
            "prompt": "perform approved actions",
            "retry": {"max_attempts": 1},
        }],
    )
    runner = _PausingRunner()

    first = RunScheduler(store, agent_runner=runner).advance(run_id)
    first_pending = first["nodes"]["ask"]["pending_interaction"]
    restarted = RunStore(store.hermes_home)
    restarted.approve_run(
        run_id,
        expected_state_version=first["state_version"],
        interaction_id=first_pending["action_digest"],
    )
    second = RunScheduler(restarted, agent_runner=runner).advance(run_id)
    second_pending = second["nodes"]["ask"]["pending_interaction"]
    restarted.approve_run(
        run_id,
        expected_state_version=second["state_version"],
        interaction_id=second_pending["action_digest"],
    )
    resumed = restarted.load_run(run_id)
    assert resumed["status"] == "running", resumed
    assert resumed["nodes"]["ask"]["state"] == "ready", resumed

    exhausted = RunScheduler(restarted, agent_runner=runner).advance(run_id, max_nodes=1)

    assert [request.max_api_attempts for request in runner.requests] == [2, 1]
    assert exhausted["nodes"]["ask"]["state"] == "failed", exhausted
    assert exhausted["status"] == "failed", exhausted
    assert exhausted["last_error"]["code"] == "retry_budget_exhausted"
    assert exhausted["nodes"]["ask"]["retry_consumed"] == 2


def test_phase5_cancelled_result_after_provider_dispatch_charges_exactly_once(
    tmp_path,
    workflow_writer,
) -> None:
    """Treating every cancellation as zero effect must lose provider usage."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-cancelled-provider-authority",
        nodes=[{
            "id": "ask",
            "prompt": "cancel after provider work",
            "retry": {"max_attempts": 2},
        }],
    )
    runner = _CancellingAfterProviderRunner()

    cancelled = RunScheduler(store, agent_runner=runner).advance(run_id)

    node = cancelled["nodes"]["ask"]
    attempt = node["attempts"][-1]
    assert cancelled["status"] == "cancelled"
    assert node["retry_consumed"] == 1
    assert attempt["metadata"]["retry_consumed"] == 1
    assert attempt["metadata"]["remaining_attempts"] == 2
    assert attempt["metadata"]["provider_attempts_exact"] is True
    assert "intended_authority_digest" not in attempt["metadata"]
    assert "model_visible_prefix_digest" not in attempt["metadata"]


def test_phase5_interrupted_result_after_provider_dispatch_charges_exactly_once(
    tmp_path,
    workflow_writer,
) -> None:
    """A shutdown result still consumes provider authority already exercised."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-interrupted-provider-authority",
        nodes=[{
            "id": "ask",
            "prompt": "interrupt after provider work",
            "retry": {"max_attempts": 2},
        }],
    )
    runner = _CancellingAfterProviderRunner()
    scheduler = RunScheduler(store, agent_runner=runner)
    scheduler._cancellation_reason = lambda _run_id: "shutdown"

    interrupted = scheduler.advance(run_id)

    node = interrupted["nodes"]["ask"]
    attempt = node["attempts"][-1]
    assert interrupted["status"] == "interrupted"
    assert node["retry_consumed"] == 1
    assert attempt["metadata"]["retry_consumed"] == 1
    assert attempt["metadata"]["remaining_attempts"] == 2
    assert attempt["metadata"]["provider_attempts_exact"] is True


def test_phase5_released_provider_crash_conservatively_consumes_outstanding_grant(
    tmp_path,
    workflow_writer,
) -> None:
    """A released request with no result must not recreate its whole grant."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-released-crash-authority",
        nodes=[{
            "id": "ask",
            "prompt": "provider may crash after accepting this",
            "retry": {"max_attempts": 1},
        }],
    )
    claim = store.claim_node(
        run_id,
        "ask",
        "crashed-owner",
        executor_id="prompt",
        execution_authority={
            "schema_version": 1,
            "retry_consumed_before": 0,
            "remaining_attempts": 2,
            "iteration_consumed_before": 0,
            "remaining_iterations": 90,
            "remaining_wall_seconds": 120.0,
        },
    )
    assert claim is not None
    store.mark_node_started(claim)
    nonce = "crashed-provider"
    process = ProcessIdentity(pid=999_991, start_time=12345.0, group_id=999_991)
    assert store.record_spawn_intent(claim, executor_nonce=nonce)
    assert store.record_process_started(claim, process)
    assert store.record_provider_dispatch(claim, executor_nonce=nonce)
    assert store.record_provider_start_delivered(claim, executor_nonce=nonce)
    assert store.record_provider_execute_received(claim, executor_nonce=nonce)
    assert store.record_provider_execute_released(claim, executor_nonce=nonce)
    assert store.record_process_stopped(claim, process, cleaned=True)
    assert store.release_or_expire_claim(claim)
    paused = store.load_run(run_id)
    interaction = paused["nodes"]["ask"]["pending_interaction"]

    reconciled = store.reconcile_run(
        run_id,
        "safe-to-retry",
        expected_state_version=paused["state_version"],
        interaction_id=interaction["interaction_id"],
    )
    assert reconciled["nodes"]["ask"]["retry_consumed"] == 2
    attempt = reconciled["nodes"]["ask"]["attempts"][-1]
    assert attempt["metadata"]["remaining_attempts"] == 0
    assert attempt["metadata"]["provider_attempts_exact"] is False

    calls = 0

    class MustNotRun:
        def run(self, _request, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("exhausted authority crossed provider transport")

    exhausted = RunScheduler(store, agent_runner=MustNotRun()).advance(
        run_id, max_nodes=1
    )
    assert calls == 0
    assert exhausted["status"] == "failed"
    assert exhausted["last_error"]["code"] == "retry_budget_exhausted"
    assert exhausted["nodes"]["ask"]["retry_consumed"] == 2

    try:
        store.reconcile_run(
            run_id,
            "safe-to-retry",
            interaction_id=interaction["interaction_id"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate reconciliation unexpectedly succeeded")
    assert store.load_run(run_id)["nodes"]["ask"]["retry_consumed"] == 2


def test_phase5_restart_refuses_transport_after_cumulative_iteration_exhaustion(
    tmp_path,
    workflow_writer,
) -> None:
    """A pause/restart must not recreate the sealed model-iteration cap."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-iteration-continuity",
        nodes=[{
            "id": "ask",
            "prompt": "use the whole model loop, then pause",
            "retry": {"max_attempts": 1},
        }],
    )
    runner = _PausingRunner(model_calls=90)
    paused = RunScheduler(store, agent_runner=runner).advance(run_id)
    interaction = paused["nodes"]["ask"]["pending_interaction"]
    assert runner.requests[0].max_iterations == 90
    assert paused["nodes"]["ask"]["iteration_consumed"] == 90
    assert paused["nodes"]["ask"]["remaining_iterations"] == 0

    restarted = RunStore(store.hermes_home)
    restarted.approve_run(
        run_id,
        expected_state_version=paused["state_version"],
        interaction_id=interaction["action_digest"],
    )
    exhausted = RunScheduler(restarted, agent_runner=runner).advance(
        run_id, max_nodes=1
    )

    assert len(runner.requests) == 1
    assert exhausted["status"] == "failed"
    assert exhausted["last_error"]["code"] == "iteration_budget_exhausted"
    assert exhausted["nodes"]["ask"]["retry_consumed"] == 1


def test_phase5_restart_intersects_transport_with_remaining_wall_deadline(
    tmp_path,
    workflow_writer,
) -> None:
    """A pause/restart must preserve elapsed active wall time."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-deadline-continuity",
        nodes=[{
            "id": "ask",
            "prompt": "pause twice while time advances",
            "retry": {"max_attempts": 2},
        }],
    )
    now = [time.monotonic()]

    def monotonic() -> float:
        return now[0]

    def consume_active_time() -> None:
        now[0] += 900.0

    runner = _PausingRunner(on_run=consume_active_time)
    first = RunScheduler(
        store,
        agent_runner=runner,
        monotonic=monotonic,
        heartbeat_seconds=2000,
        lease_seconds=3000,
    ).advance(run_id)
    assert first["nodes"]["ask"]["state"] == "paused", first["nodes"]["ask"]
    first_interaction = first["nodes"]["ask"]["pending_interaction"]
    assert runner.requests[0].wall_timeout_seconds == 1800
    assert first["nodes"]["ask"]["remaining_wall_seconds"] == 900

    restarted = RunStore(store.hermes_home)
    restarted.approve_run(
        run_id,
        expected_state_version=first["state_version"],
        interaction_id=first_interaction["action_digest"],
    )
    now[0] = time.monotonic()
    second = RunScheduler(
        restarted,
        agent_runner=runner,
        monotonic=monotonic,
        heartbeat_seconds=2000,
        lease_seconds=3000,
    ).advance(run_id)
    assert second["nodes"]["ask"]["state"] == "paused", second["nodes"]["ask"]
    second_interaction = second["nodes"]["ask"]["pending_interaction"]
    assert runner.requests[1].wall_timeout_seconds == 900
    assert second["nodes"]["ask"]["remaining_wall_seconds"] == 0

    restarted.approve_run(
        run_id,
        expected_state_version=second["state_version"],
        interaction_id=second_interaction["action_digest"],
    )
    now[0] = time.monotonic()
    exhausted = RunScheduler(
        restarted,
        agent_runner=runner,
        monotonic=monotonic,
        heartbeat_seconds=2000,
        lease_seconds=3000,
    ).advance(run_id, max_nodes=1)

    assert len(runner.requests) == 2
    assert exhausted["status"] == "failed"
    assert exhausted["last_error"]["code"] == "deadline_budget_exhausted"
    assert exhausted["nodes"]["ask"]["retry_consumed"] == 2


def test_phase5_control_nodes_preserve_zero_execution_charge(
    tmp_path,
    workflow_writer,
) -> None:
    """Approval and cancel control transitions allocate no provider authority."""
    approval_store, approval_run = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-zero-approval",
        nodes=[{"id": "gate", "approval": {"message": "Approve?"}}],
    )
    paused = RunScheduler(approval_store).advance(approval_run)
    approval = paused["nodes"]["gate"]
    assert paused["status"] == "paused"
    assert approval.get("retry_consumed", 0) == 0
    assert approval["attempts"][-1]["metadata"]["known_no_effect"] is True

    cancel_store, cancel_run = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-zero-cancel",
        nodes=[{"id": "stop", "cancel": "control decision"}],
    )
    cancelled = RunScheduler(cancel_store).advance(cancel_run)
    control = cancelled["nodes"]["stop"]
    assert cancelled["status"] == "cancelled"
    assert control.get("retry_consumed", 0) == 0
    assert control["attempts"][-1]["metadata"]["known_no_effect"] is True


def test_phase5_predispatch_cancellation_preserves_zero_execution_charge(
    tmp_path,
    workflow_writer,
    monkeypatch,
) -> None:
    """Cancellation after claim but before executor launch must remain free."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-zero-predispatch",
        nodes=[{"id": "ask", "prompt": "must not cross transport"}],
    )
    original = store.mark_node_started

    def cancel_after_mark(claim, **kwargs):
        original(claim, **kwargs)
        store.cancel_run(claim.run_id)

    monkeypatch.setattr(store, "mark_node_started", cancel_after_mark)
    calls = 0

    class MustNotRun:
        def run(self, _request, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("predispatch cancellation crossed transport")

    cancelled = RunScheduler(store, agent_runner=MustNotRun()).advance(run_id)

    node = cancelled["nodes"]["ask"]
    assert calls == 0
    assert cancelled["status"] == "cancelled"
    assert node.get("retry_consumed", 0) == 0
    assert "provider_dispatch" not in node["attempts"][-1]


def test_phase5_sealed_decision_failure_preserves_zero_execution_charge(
    tmp_path,
    workflow_writer,
) -> None:
    """Invalid sealed request metadata must fail before consuming transport."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-zero-sealed-decision",
        nodes=[{
            "id": "ask",
            "prompt": "return structured output",
            "output_format": {"type": "object"},
            "retry": {"max_attempts": 1, "on_error": "all"},
        }],
    )
    runner = RecordingRunner("{}")

    failed = RunScheduler(store, agent_runner=runner).advance(run_id)

    node = failed["nodes"]["ask"]
    attempt = node["attempts"][-1]
    assert runner.requests == []
    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "structured_output_capability_drift"
    assert node.get("retry_consumed", 0) == 0
    assert node.get("iteration_consumed", 0) == 0
    assert attempt["metadata"]["provider_attempts"] == 0
    assert attempt["metadata"]["provider_attempts_exact"] is True
    assert attempt["metadata"]["known_no_effect"] is True


def test_phase5_rebuilt_store_uses_durable_shared_context_identity(
    tmp_path,
    workflow_writer,
) -> None:
    """A downstream shared turn must need only sealed and persisted identity."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-rebuilt-shared-context",
        nodes=[
            {
                "id": "first",
                "prompt": "first user turn",
                "allowed_tools": ["terminal"],
            },
            {
                "id": "second",
                "prompt": "second user turn",
                "depends_on": ["first"],
                "context": "shared",
                "allowed_tools": ["terminal"],
            },
        ],
    )
    runner = RecordingRunner()
    first = RunScheduler(store, agent_runner=runner).advance(run_id, max_nodes=1)
    first_metadata = first["nodes"]["first"]["attempts"][-1]["metadata"]
    assert first["nodes"]["first"]["state"] == "succeeded"

    rebuilt = RunStore(store.hermes_home)
    completed = RunScheduler(rebuilt, agent_runner=runner).advance(
        run_id, max_nodes=1
    )

    assert completed["status"] == "succeeded", completed
    assert len(runner.requests) == 2
    downstream = runner.requests[1]
    assert downstream.context_mode == "shared"
    assert downstream.session_id == "ai-session"
    assert downstream.expected_model_visible_prefix_digest == (
        first_metadata["model_visible_prefix_digest"]
    )
    second_metadata = completed["nodes"]["second"]["attempts"][-1]["metadata"]
    assert second_metadata["shared_context_compatibility_digest"] == (
        first_metadata["shared_context_compatibility_digest"]
    )


def test_phase5_rebuilt_store_blocks_bad_shared_identity_before_transport(
    tmp_path,
    workflow_writer,
) -> None:
    """Persisted predecessor identity is an input contract, not a hint."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-rebuilt-bad-shared-context",
        nodes=[
            {"id": "first", "prompt": "first", "allowed_tools": ["terminal"]},
            {
                "id": "second",
                "prompt": "second",
                "depends_on": ["first"],
                "context": "shared",
                "allowed_tools": ["terminal"],
            },
        ],
    )
    claim = store.claim_node(run_id, "first", "persisted-winner", executor_id="prompt")
    assert claim is not None
    store.mark_node_started(claim)
    store.complete_node(
        claim,
        status="succeeded",
        metadata={
            "session_id": "persisted-session",
            "cache_fingerprint": "a" * 64,
            "intended_authority_digest": "b" * 64,
            "model_visible_prefix_digest": "c" * 64,
            "shared_context_compatibility_digest": "0" * 64,
        },
    )
    calls = 0

    class MustNotRun:
        def run(self, _request, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("bad shared identity crossed provider transport")

    rebuilt = RunStore(store.hermes_home)
    failed = RunScheduler(rebuilt, agent_runner=MustNotRun()).advance(run_id)

    assert calls == 0
    assert failed["status"] == "failed"
    assert failed["last_error"]["code"] == "context_incompatible"


def test_phase5_duplicate_succeeded_persistence_fails_closed_without_mutation(
    tmp_path,
    workflow_writer,
) -> None:
    """A duplicate winner cannot republish identity or consume authority twice."""
    store, run_id = _admit_phase5_run(
        tmp_path,
        workflow_writer,
        name="phase5-duplicate-success",
        nodes=[{"id": "ask", "prompt": "publish once"}],
    )
    runner = RecordingRunner()
    completed = RunScheduler(store, agent_runner=runner).advance(run_id)
    before = completed["nodes"]["ask"]
    attempt = before["attempts"][-1]
    claim = type("Claim", (), {
        "run_id": run_id,
        "node_id": "ask",
        "attempt_id": attempt["attempt_id"],
        "owner_id": attempt["owner_id"],
        "execution_fence": None,
    })()

    try:
        store.complete_node(
            claim,
            status="succeeded",
            metadata=dict(attempt["metadata"]),
        )
    except RuntimeError as exc:
        assert "stale node completion" in str(exc)
    else:
        raise AssertionError("duplicate succeeded completion unexpectedly won")

    after = store.load_run(run_id)["nodes"]["ask"]
    assert len(after["attempts"]) == 1
    assert after["retry_consumed"] == before["retry_consumed"] == 1
    for field in (
        "intended_authority_digest",
        "model_visible_prefix_digest",
        "shared_context_compatibility_digest",
        "cache_fingerprint",
    ):
        assert after["attempts"][0]["metadata"][field] == (
            before["attempts"][0]["metadata"][field]
        )
