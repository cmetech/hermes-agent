from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from threading import Event

import pytest

import hermes_cli.handoff.store as store_module
from hermes_cli.handoff.models import (
    ChannelObservation,
    HandoffEndpoint,
    HandoffSpec,
)
from hermes_cli.handoff.service import (
    AgentHandoffService,
    ChannelDefinitelyNotAccepted,
    ChannelRetryableFailure,
    EndpointAssessment,
    UnsupportedHandoffCommand,
)
from hermes_cli.handoff.store import HandoffStore


UTC = timezone.utc


def _result(text: str = "accepted") -> dict[str, object]:
    encoded = text.encode()
    return {
        "text": text,
        "sha256": sha256(encoded).hexdigest(),
        "media_type": "text/plain",
        "size_bytes": len(encoded),
    }


def _spec() -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review this change.",
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=UTC),
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities=frozenset(),
    )


class _Crash(BaseException):
    pass


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self.outcomes: dict[str, object] = {}

    def validate_endpoint(self, endpoint, initiator):
        return EndpointAssessment(endpoint=endpoint, available=True, mechanism="runs")

    def _call(self, operation: str, snapshot, budget_seconds: float):
        assert math.isfinite(budget_seconds) and budget_seconds > 0
        self.calls.append((operation, budget_seconds))
        outcome = self.outcomes.get(operation)
        if callable(outcome):
            return outcome(snapshot)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return outcome
        if operation == "bind":
            return ChannelObservation(
                phase="prepared",
                mechanism="runs",
                binding={"profile": "reviewer", "mechanism": "runs"},
            )
        if operation == "submit":
            return ChannelObservation(phase="submitted", checkpoint={"run_id": "run-1"})
        if operation == "reconcile":
            return ChannelObservation(phase="submitted", checkpoint={"run_id": "run-1"})
        if operation == "observe":
            return ChannelObservation(
                phase=snapshot.phase, checkpoint=snapshot.checkpoint or {}
            )
        return ChannelObservation(
            phase="cancelling", checkpoint=snapshot.checkpoint or {}
        )

    def bind(self, snapshot, *, budget_seconds: float):
        return self._call("bind", snapshot, budget_seconds)

    def submit(self, snapshot, *, budget_seconds: float):
        return self._call("submit", snapshot, budget_seconds)

    def reconcile(self, snapshot, *, budget_seconds: float):
        return self._call("reconcile", snapshot, budget_seconds)

    def observe(self, snapshot, *, budget_seconds: float):
        return self._call("observe", snapshot, budget_seconds)

    def cancel(self, snapshot, *, budget_seconds: float):
        return self._call("cancel", snapshot, budget_seconds)


def _service(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    channel = FakeChannel()
    service = AgentHandoffService(store=store, channel=channel)
    snapshot = service.create(_spec(), "workflow/run-1", handoff_key="node/review")
    return service, store, channel, snapshot


def _bind(service, channel, handoff_id):
    channel.calls.clear()
    snapshot = service.advance(handoff_id).snapshot
    assert [name for name, _ in channel.calls] == ["bind"]
    return snapshot


def _seed_phase(store: HandoffStore, snapshot, phase: str):
    if snapshot.mechanism is None:
        snapshot = store.bind(
            snapshot.handoff_id,
            "runs",
            {"profile": "reviewer", "mechanism": "runs"},
            {},
            snapshot.state_version,
        )
    lease = store.claim_advance(
        snapshot.handoff_id,
        "seed-worker",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    if phase == "prepared_attempted":
        store.journal_attempt(lease, "submit")
    elif phase != "prepared":
        store.journal_attempt(lease, "submit")
        for observed in {
            "submitted": ["submitted"],
            "active": ["submitted", "active"],
            "needs_input": ["submitted", "active", "needs_input"],
            "indeterminate": ["indeterminate"],
            "succeeded": ["submitted", "succeeded"],
            "failed": ["failed"],
            "cancelled": ["submitted", "cancelled"],
            "cancelling": ["submitted"],
        }[phase]:
            store.commit_observation(
                lease,
                ChannelObservation(
                    phase=observed,
                    checkpoint=(
                        {"run_id": "run-1"}
                        if observed
                        in {
                            "submitted",
                            "active",
                            "needs_input",
                            "succeeded",
                            "cancelled",
                        }
                        else {}
                    ),
                    terminal_result=_result() if observed == "succeeded" else None,
                    failure_code="remote_failed" if observed == "failed" else None,
                ),
            )
        if phase == "cancelling":
            store.record_command(
                snapshot.handoff_id,
                "cancel-1",
                "cancel",
                {"actor": "workflow"},
            )
    store.release_advance(lease, next_advance_at=None)
    return store.get(snapshot.handoff_id)


def test_validate_create_and_read_facade_are_consumer_neutral(tmp_path):
    service, _store, _channel, snapshot = _service(tmp_path)

    assessment = service.validate_endpoint("hermes://local/reviewer", "workflow/run-1")
    replay = service.create(_spec(), "workflow/run-1", handoff_key="node/review")

    assert assessment == EndpointAssessment(
        endpoint=_spec().endpoint, available=True, mechanism="runs"
    )
    assert replay == snapshot
    assert service.get(snapshot.handoff_id) == snapshot
    assert service.list({"key_scope": "workflow/run-1"}) == (snapshot,)
    assert service.evidence(snapshot.handoff_id).events[0].kind == "created"


def test_default_dispatcher_selects_only_the_endpoint_channel(tmp_path):
    service = AgentHandoffService(store=HandoffStore(tmp_path / "dispatch.db"))
    calls = []

    class _Channel:
        def __init__(self, name):
            self.name = name

        def validate_endpoint(self, endpoint, _initiator):
            calls.append((self.name, endpoint.kind, "validate"))
            return EndpointAssessment(endpoint=endpoint, available=True)

        def bind(self, snapshot, *, budget_seconds):
            calls.append((self.name, snapshot.spec.endpoint.kind, "bind"))
            if self.name == "peer":
                return ChannelObservation(
                    phase="prepared",
                    mechanism="peer_runs",
                    binding={
                        "peer": "spark",
                        "profile": "reviewer",
                        "mechanism": "peer_runs",
                        "capabilities": [
                            "authoritative_status",
                            "cancellation",
                            "durable_admission",
                        ],
                        "origin_sha256": "a" * 64,
                        "auth_scope_sha256": "b" * 64,
                    },
                )
            return ChannelObservation(
                phase="prepared",
                mechanism="runs",
                binding={"profile": "reviewer", "mechanism": "runs"},
            )

    service.channel.local = _Channel("local")
    service.channel.peer = _Channel("peer")
    local = _spec().endpoint
    peer = HandoffEndpoint.parse("hermes://peer/spark/reviewer")

    service.validate_endpoint(local, "workflow/run-1")
    service.validate_endpoint(peer, "workflow/run-1")
    peer_spec = replace(_spec(), endpoint=peer)
    created = service.create(peer_spec, "workflow/run-1", handoff_key="peer/review")
    service.advance(created.handoff_id)

    assert calls == [
        ("local", "local", "validate"),
        ("peer", "peer", "validate"),
        ("peer", "peer", "bind"),
    ]


@pytest.mark.parametrize(
    "endpoint",
    [
        HandoffEndpoint("hermes://local/reviewer?", "reviewer"),
        HandoffEndpoint("hermes://local/reviewer", "other"),
    ],
)
def test_validate_endpoint_rejects_preconstructed_noncanonical_values(
    tmp_path, endpoint: HandoffEndpoint
):
    service, _store, channel, _snapshot = _service(tmp_path)

    with pytest.raises(ValueError, match="handoff endpoint"):
        service.validate_endpoint(endpoint, "workflow/run-1")

    assert channel.calls == []


@pytest.mark.parametrize(
    ("phase", "expected_operation"),
    [
        ("unbound", "bind"),
        ("prepared", "submit"),
        ("prepared_attempted", "reconcile"),
        ("submitted", "observe"),
        ("active", "observe"),
        ("needs_input", "observe"),
        ("indeterminate", "reconcile"),
        ("cancelling", "cancel"),
        ("succeeded", None),
        ("failed", None),
        ("cancelled", None),
    ],
)
def test_advance_selects_at_most_one_operation_from_durable_facts(
    tmp_path, phase: str, expected_operation: str | None
):
    service, store, channel, snapshot = _service(tmp_path)
    if phase != "unbound":
        snapshot = _seed_phase(store, snapshot, phase)
    if phase == "indeterminate":
        channel.outcomes["reconcile"] = ChannelObservation(phase="indeterminate")

    channel.calls.clear()
    result = service.advance(snapshot.handoff_id, budget_seconds=1.5)

    assert result.operation == expected_operation
    assert result.work_done is (expected_operation is not None)
    assert [name for name, _ in channel.calls] == (
        [] if expected_operation is None else [expected_operation]
    )
    assert all(0 < remaining <= 1.5 for _, remaining in channel.calls)


@pytest.mark.parametrize("budget", [True, 0, -1, float("inf"), float("nan")])
def test_advance_rejects_nonpositive_or_nonfinite_budgets(tmp_path, budget):
    service, _store, channel, snapshot = _service(tmp_path)

    with pytest.raises(ValueError, match="finite and positive"):
        service.advance(snapshot.handoff_id, budget_seconds=budget)

    assert channel.calls == []


def test_bind_attempt_is_visible_before_channel_io(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)

    def bind(_snapshot):
        assert (
            store
            .evidence(snapshot.handoff_id, after_sequence=0, limit=10)
            .events[-1]
            .kind
            == "bind_attempted"
        )
        return ChannelObservation(
            phase="prepared",
            mechanism="runs",
            binding={"profile": "reviewer", "mechanism": "runs"},
        )

    channel.outcomes["bind"] = bind

    assert service.advance(snapshot.handoff_id).snapshot.mechanism == "runs"


def test_crash_after_submit_journal_recovers_only_by_reconcile(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = _Crash()

    with pytest.raises(_Crash):
        service.advance(snapshot.handoff_id)

    assert store.get(snapshot.handoff_id).submit_attempted_at is not None
    channel.calls.clear()
    channel.outcomes.pop("submit")

    recovered = service.advance(snapshot.handoff_id)

    assert recovered.snapshot.phase == "submitted"
    assert [name for name, _ in channel.calls] == ["reconcile"]


def test_cancel_after_unconfirmed_submit_reconciles_before_delivery(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = _Crash()
    with pytest.raises(_Crash):
        service.advance(snapshot.handoff_id)
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["reconcile"] = ChannelObservation(
        phase="active", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()

    reconciled = service.advance(snapshot.handoff_id)

    assert reconciled.snapshot.phase == "cancelling"
    assert reconciled.snapshot.checkpoint == {"run_id": "run-1"}
    assert [name for name, _ in channel.calls] == ["reconcile"]

    channel.outcomes["cancel"] = ChannelObservation(
        phase="cancelled", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()
    terminal = service.advance(snapshot.handoff_id)

    assert terminal.snapshot.phase == "cancelled"
    assert [name for name, _ in channel.calls] == ["cancel"]
    assert [
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ].count("submit_attempted") == 1


def test_bind_session_checkpoint_is_not_admission_before_cancel(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    channel.outcomes["bind"] = ChannelObservation(
        phase="prepared",
        mechanism="runs",
        binding={"profile": "reviewer", "mechanism": "runs"},
        checkpoint={"session_id": "session-1"},
    )
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = _Crash()
    with pytest.raises(_Crash):
        service.advance(snapshot.handoff_id)
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["reconcile"] = ChannelObservation(
        phase="active", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()

    reconciled = service.advance(snapshot.handoff_id)

    assert reconciled.snapshot.phase == "cancelling"
    assert reconciled.snapshot.checkpoint == {"run_id": "run-1"}
    assert [name for name, _ in channel.calls] == ["reconcile"]

    channel.outcomes["cancel"] = ChannelObservation(
        phase="cancelled", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()
    terminal = service.advance(snapshot.handoff_id)

    assert terminal.snapshot.phase == "cancelled"
    assert [name for name, _ in channel.calls] == ["cancel"]
    assert [
        event.kind
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ].count("submit_attempted") == 1


def test_admission_selection_never_reads_evidence_history(tmp_path, monkeypatch):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = _Crash()
    with pytest.raises(_Crash):
        service.advance(snapshot.handoff_id)
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["reconcile"] = ChannelObservation(
        phase="active", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()

    def fail_evidence_scan(*_args, **_kwargs):
        raise AssertionError("admission selection scanned evidence history")

    monkeypatch.setattr(store, "evidence", fail_evidence_scan)

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "cancelling"
    assert [name for name, _ in channel.calls] == ["reconcile"]


def test_ambiguous_cancel_reconciles_before_repeating_cancel(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["cancel"] = RuntimeError("cancel receipt lost")
    channel.calls.clear()

    ambiguous = service.advance(snapshot.handoff_id)

    assert ambiguous.snapshot.phase == "indeterminate"
    assert [name for name, _ in channel.calls] == ["cancel"]

    channel.outcomes["reconcile"] = ChannelObservation(
        phase="active", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()
    reconciled = service.advance(snapshot.handoff_id)

    assert reconciled.snapshot.phase == "cancelling"
    assert [name for name, _ in channel.calls] == ["reconcile"]

    channel.outcomes["cancel"] = ChannelObservation(
        phase="succeeded",
        checkpoint={"run_id": "run-1"},
        terminal_result=_result(),
    )
    channel.calls.clear()
    terminal = service.advance(snapshot.handoff_id)

    assert terminal.snapshot.phase == "succeeded"
    assert [name for name, _ in channel.calls] == ["cancel"]


@pytest.mark.parametrize("terminal_phase", ["succeeded", "cancelled"])
def test_crash_after_acceptance_before_checkpoint_folds_terminal_reconciliation(
    tmp_path, terminal_phase: str
):
    service, _store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = _Crash()
    with pytest.raises(_Crash):
        service.advance(snapshot.handoff_id)
    channel.outcomes["reconcile"] = ChannelObservation(
        phase=terminal_phase,
        checkpoint={"run_id": "run-1"},
        terminal_result=_result() if terminal_phase == "succeeded" else None,
    )
    channel.calls.clear()

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == terminal_phase
    assert [name for name, _ in channel.calls] == ["reconcile"]


def test_repeated_terminal_advance_is_an_idempotent_noop(tmp_path):
    service, _store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = ChannelObservation(
        phase="succeeded", terminal_result=_result()
    )
    terminal = service.advance(snapshot.handoff_id).snapshot
    channel.calls.clear()

    repeated = service.advance(snapshot.handoff_id)

    assert repeated.snapshot == terminal
    assert repeated.operation is None
    assert channel.calls == []


def test_concurrent_advances_make_only_one_channel_call(tmp_path):
    service, _store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    entered = Event()
    release = Event()

    def submit(_snapshot):
        entered.set()
        assert release.wait(2)
        return ChannelObservation(phase="submitted", checkpoint={"run_id": "run-1"})

    channel.outcomes["submit"] = submit
    channel.calls.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.advance, snapshot.handoff_id)
        assert entered.wait(2)
        second = pool.submit(service.advance, snapshot.handoff_id)
        second_result = second.result(timeout=2)
        release.set()
        first_result = first.result(timeout=2)

    assert first_result.snapshot.phase == "submitted"
    assert second_result.operation is None
    assert [name for name, _ in channel.calls] == ["submit"]


def test_expired_lease_cannot_fold_channel_observation(tmp_path, monkeypatch):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)

    def expire(_snapshot):
        monkeypatch.setattr(
            store_module,
            "_utc_now",
            lambda: datetime.now(UTC) + timedelta(minutes=1),
        )
        return ChannelObservation(phase="submitted", checkpoint={"run_id": "run-1"})

    channel.outcomes["submit"] = expire

    result = service.advance(snapshot.handoff_id, budget_seconds=1)

    assert result.snapshot.phase == "prepared"
    assert result.snapshot.submit_attempted_at is not None
    assert result.observation_folded is False


def test_cancel_complete_race_accepts_authoritative_terminal_truth(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "submitted")
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["cancel"] = ChannelObservation(
        phase="succeeded",
        checkpoint={"run_id": "run-1"},
        terminal_result=_result(),
    )

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "succeeded"
    assert result.snapshot.cancel_requested_at is not None


def test_cancel_before_submit_is_authoritatively_local_and_calls_no_channel(tmp_path):
    service, _store, channel, snapshot = _service(tmp_path)
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.calls.clear()

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "cancelled"
    assert result.operation is None
    assert result.observation_folded is True
    assert channel.calls == []


def test_nonterminal_observation_cannot_undo_concurrent_cancellation(tmp_path):
    service, _store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = lambda current: (
        service.command(
            current.handoff_id,
            "cancel",
            command_id="cancel-1",
            actor="workflow",
        ),
        ChannelObservation(phase="submitted", checkpoint={"run_id": "run-1"}),
    )[1]

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "cancelling"
    assert result.snapshot.checkpoint == {"run_id": "run-1"}


@pytest.mark.parametrize(
    ("error", "expected_phase", "expected_code"),
    [
        (
            ChannelDefinitelyNotAccepted("submission_rejected"),
            "failed",
            "submission_rejected",
        ),
        (ChannelRetryableFailure(), "indeterminate", "submission_indeterminate"),
        (
            RuntimeError("Bearer raw-provider-secret"),
            "indeterminate",
            "submission_indeterminate",
        ),
    ],
)
def test_submit_exception_classification_is_safe(
    tmp_path, error: Exception, expected_phase: str, expected_code: str
):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    channel.outcomes["submit"] = error

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == expected_phase
    assert result.snapshot.failure_code == expected_code
    encoded = json.dumps([
        dict(event.data)
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ])
    assert "raw-provider-secret" not in encoded


def test_bind_probe_exception_remains_unsubmitted_and_retryable(tmp_path):
    service, _store, channel, snapshot = _service(tmp_path)
    channel.outcomes["bind"] = RuntimeError("Bearer raw-provider-secret")

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "prepared"
    assert result.snapshot.mechanism is None
    assert result.snapshot.submit_attempted_at is None
    assert result.snapshot.failure_code == "endpoint_unavailable"


def test_cancel_delivery_exception_is_indeterminate_even_when_retryable(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "submitted")
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["cancel"] = ChannelRetryableFailure()

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "indeterminate"
    assert result.snapshot.failure_code == "cancellation_indeterminate"


def test_definitively_rejected_cancel_does_not_terminalize_remote_work(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["cancel"] = ChannelDefinitelyNotAccepted("cancellation_rejected")

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "cancelling"
    assert result.snapshot.failure_code == "cancellation_rejected"


def test_retryable_observation_failure_keeps_authoritative_phase(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    channel.outcomes["observe"] = ChannelRetryableFailure()

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "active"
    assert result.snapshot.failure_code == "observation_retryable"


def test_illegal_channel_transition_becomes_protocol_failure(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    channel.outcomes["observe"] = ChannelObservation(phase="submitted")

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "failed"
    assert result.snapshot.failure_code == "protocol_violation"


def test_pre_submit_binding_contradiction_becomes_stable_protocol_failure(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    channel.outcomes["bind"] = ChannelObservation(
        phase="prepared",
        mechanism="runs",
        binding={"profile": "reviewer", "mechanism": "cli"},
    )

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "failed"
    assert result.snapshot.failure_code == "protocol_violation"
    encoded = json.dumps([
        dict(event.data)
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ])
    assert "does not match" not in encoded


def test_post_submit_binding_contradiction_is_indeterminate_and_keeps_durable_facts(
    tmp_path,
):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    durable_binding = snapshot.binding
    durable_checkpoint = snapshot.checkpoint
    channel.outcomes["observe"] = ChannelObservation(
        phase="active",
        mechanism="cli",
        binding={"profile": "reviewer", "mechanism": "cli"},
        checkpoint={"run_id": "contradictory"},
    )

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "indeterminate"
    assert result.snapshot.failure_code == "protocol_violation"
    assert result.snapshot.mechanism == "runs"
    assert result.snapshot.binding == durable_binding
    assert result.snapshot.checkpoint == durable_checkpoint
    encoded = json.dumps([
        dict(event.data)
        for event in store.evidence(
            snapshot.handoff_id, after_sequence=0, limit=100
        ).events
    ])
    assert "contradictory" not in encoded


def test_future_due_time_is_a_durable_noop(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    lease = store.claim_advance(
        snapshot.handoff_id,
        "schedule-worker",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    future = datetime.now(UTC) + timedelta(minutes=5)
    store.release_advance(lease, next_advance_at=future)
    channel.calls.clear()

    result = service.advance(snapshot.handoff_id)

    assert result.operation is None
    assert result.snapshot.next_advance_at == future
    assert channel.calls == []


def test_cancel_request_overrides_a_future_observation_schedule(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "active")
    lease = store.claim_advance(
        snapshot.handoff_id,
        "schedule-worker",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    future = datetime.now(UTC) + timedelta(minutes=5)
    store.release_advance(lease, next_advance_at=future)
    service.command(
        snapshot.handoff_id,
        "cancel",
        command_id="cancel-1",
        actor="workflow",
    )
    channel.outcomes["cancel"] = ChannelObservation(
        phase="cancelled", checkpoint={"run_id": "run-1"}
    )
    channel.calls.clear()

    result = service.advance(snapshot.handoff_id)

    assert result.snapshot.phase == "cancelled"
    assert [name for name, _ in channel.calls] == ["cancel"]


def test_reconcile_command_makes_an_indeterminate_handoff_due_now(tmp_path):
    service, store, channel, snapshot = _service(tmp_path)
    snapshot = _bind(service, channel, snapshot.handoff_id)
    snapshot = _seed_phase(store, snapshot, "indeterminate")
    lease = store.claim_advance(
        snapshot.handoff_id,
        "schedule-worker",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    store.release_advance(
        lease, next_advance_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    service.command(
        snapshot.handoff_id,
        "reconcile",
        command_id="reconcile-1",
        actor="workflow",
    )
    channel.outcomes["reconcile"] = ChannelObservation(phase="indeterminate")
    channel.calls.clear()

    result = service.advance(snapshot.handoff_id)

    assert result.operation == "reconcile"
    assert [name for name, _ in channel.calls] == ["reconcile"]


def test_commands_are_idempotent_and_stage_one_rejects_future_kinds(tmp_path):
    service, _store, _channel, snapshot = _service(tmp_path)

    first = service.command(
        snapshot.handoff_id,
        "reconcile",
        command_id="reconcile-1",
        actor="workflow",
    )
    replay = service.command(
        snapshot.handoff_id,
        "reconcile",
        command_id="reconcile-1",
        actor="workflow",
    )

    assert replay == first
    with pytest.raises(UnsupportedHandoffCommand, match="unsupported"):
        service.command(
            snapshot.handoff_id,
            "message",
            command_id="message-1",
            actor="workflow",
        )
